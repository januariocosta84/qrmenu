"""
Self-service restaurant registration and email verification.

New sign-ups prove their email first: the form's details wait in the session (the password already
hashed) while a 6-digit code goes to the address; only the right code creates the account and the
restaurant, already verified. Fake or mistyped addresses never get an account.
"""
import logging
import secrets
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.contrib.auth.hashers import make_password
from django.core.mail import mail_admins, send_mail
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.translation import gettext as _
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.text import slugify

from apps.billing.services import start_subscription
from apps.core.permissions import Role
from apps.restaurants.models import Restaurant, RestaurantStaff

logger = logging.getLogger(__name__)
User = get_user_model()

VERIFY_SALT = "accounts.verify-email"
RESERVED_SLUGS = {
    "admin", "api", "app", "dashboard", "accounts", "account", "login", "logout", "signup", "register",
    "static", "media", "ws", "www", "help", "support", "about", "terms", "privacy", "demo", "test",
}


def unique_slug(name: str) -> str:
    base = slugify(name)[:60].strip("-") or "restaurant"
    if base in RESERVED_SLUGS:
        base = f"{base}-restaurant"
    slug, n = base, 2
    while Restaurant.objects.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


@transaction.atomic
def register_restaurant(*, restaurant_name, full_name, email, password=None, password_hash=None, phone="") -> tuple:
    first, _sep, last = full_name.strip().partition(" ")
    user = User.objects.create_user(
        username=email, email=email, password=password, first_name=first[:150], last_name=last[:150], phone=phone,
    )
    if password_hash:
        user.password = password_hash
        user.save(update_fields=["password"])
    restaurant = Restaurant.objects.create(
        name=restaurant_name, slug=unique_slug(restaurant_name), email=email, phone=phone,
        is_active=False,  # goes live after email verification (and approval, if enabled)
    )
    RestaurantStaff.objects.create(restaurant=restaurant, user=user, role=Role.OWNER)
    start_subscription(restaurant)
    return user, restaurant


def make_verify_token(user) -> str:
    return signing.dumps({"u": user.pk, "e": user.email}, salt=VERIFY_SALT)


def send_verification_email(request, user) -> None:
    link = request.build_absolute_uri(reverse("accounts:verify_email", args=[make_verify_token(user)]))
    context = {"user": user, "link": link, "platform": settings.PLATFORM_NAME, "days": settings.EMAIL_VERIFICATION_DAYS}
    send_mail(
        subject=f"Confirm your email for {settings.PLATFORM_NAME}",
        message=render_to_string("accounts/email/verify.txt", context),
        from_email=None,
        recipient_list=[user.email],
        fail_silently=False,
    )


def verify_token(token: str):
    """Return the user for a valid, unexpired token, else None."""
    try:
        data = signing.loads(token, salt=VERIFY_SALT, max_age=settings.EMAIL_VERIFICATION_DAYS * 86400)
    except signing.BadSignature:
        return None
    user = User.objects.filter(pk=data.get("u")).first()
    if user is None or user.email.lower() != str(data.get("e", "")).lower():
        return None  # email changed since the link was sent
    return user


def mark_verified(user) -> list:
    """Confirm the email; publish the owner's restaurants when sign-up is open. Returns those restaurants."""
    if user.email_verified:
        return []
    user.email_verified = True
    user.save(update_fields=["email_verified"])
    pending = list(Restaurant.objects.filter(staff__user=user, staff__role=Role.OWNER, is_active=False))
    if settings.SIGNUP_MODE == "open":
        Restaurant.objects.filter(pk__in=[r.pk for r in pending]).update(is_active=True)
    elif pending:
        try:
            mail_admins(
                "Restaurant waiting for approval",
                "\n".join(f"{r.name} (/r/{r.slug}/) — owner {user.email}" for r in pending)
                + "\n\nApprove it in the platform admin → Restaurants.",
            )
        except Exception:
            logger.exception("Could not email platform admins about a new sign-up")
    return pending


# ---------------------------------------------------------------- sign-up code (OTP)

PENDING_KEY = "signup_pending"
CODE_TTL = 10 * 60  # seconds a code is valid
MAX_ATTEMPTS = 5  # wrong codes before the sign-up has to start again
MAX_SENDS = 3  # codes per sign-up (first one + 2 resends)
RESEND_AFTER = 60  # seconds between codes


def _code_hash(email: str, code: str) -> str:
    return salted_hmac("accounts.signup-code", f"{email}:{code}").hexdigest()


def start_signup(request, data: dict) -> None:
    """Keep the form's details in the session and email a fresh code."""
    pending = {
        "restaurant_name": data["restaurant_name"], "full_name": data["full_name"], "email": data["email"],
        "phone": data.get("phone", ""), "password_hash": make_password(data["password"]),
        "attempts": 0, "sends": 0,
    }
    request.session[PENDING_KEY] = pending
    send_signup_code(request)


def pending_signup(request) -> dict | None:
    return request.session.get(PENDING_KEY)


def can_resend(pending) -> int:
    """Seconds to wait before another code may be sent (0 = now); -1 when no more codes are allowed."""
    if pending["sends"] >= MAX_SENDS:
        return -1
    return max(0, int(pending.get("sent_at", 0) + RESEND_AFTER - time.time()))


def send_signup_code(request) -> None:
    pending = request.session[PENDING_KEY]
    code = f"{secrets.randbelow(10 ** 6):06d}"
    pending.update(code_hash=_code_hash(pending["email"], code), expires=time.time() + CODE_TTL,
                   sent_at=time.time(), sends=pending["sends"] + 1, attempts=0)
    request.session[PENDING_KEY] = pending
    context = {"name": pending["full_name"].split(" ")[0], "code": code, "platform": settings.PLATFORM_NAME,
               "minutes": CODE_TTL // 60, "restaurant": pending["restaurant_name"]}
    send_mail(
        subject=_("%(code)s is your %(platform)s verification code") % {"code": code, "platform": settings.PLATFORM_NAME},
        message=render_to_string("accounts/email/signup_code.txt", context),
        from_email=None, recipient_list=[pending["email"]], fail_silently=False,
    )


def check_signup_code(request, code: str) -> str:
    """ok | wrong | expired | locked | missing"""
    pending = request.session.get(PENDING_KEY)
    if not pending:
        return "missing"
    if pending["attempts"] >= MAX_ATTEMPTS:
        return "locked"
    if time.time() > pending.get("expires", 0):
        return "expired"
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) == 6 and constant_time_compare(_code_hash(pending["email"], code), pending["code_hash"]):
        return "ok"
    pending["attempts"] += 1
    request.session[PENDING_KEY] = pending
    return "locked" if pending["attempts"] >= MAX_ATTEMPTS else "wrong"


def finish_signup(request):
    """The code was right: create the verified owner and the restaurant. Returns (user, restaurant, published)."""
    pending = request.session.pop(PENDING_KEY)
    user, restaurant = register_restaurant(
        restaurant_name=pending["restaurant_name"], full_name=pending["full_name"], email=pending["email"],
        password_hash=pending["password_hash"], phone=pending["phone"],
    )
    published = mark_verified(user)
    return user, restaurant, bool(published)
