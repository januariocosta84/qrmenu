"""Self-service restaurant registration and email verification."""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.mail import mail_admins, send_mail
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
def register_restaurant(*, restaurant_name, full_name, email, password, phone="") -> tuple:
    first, _, last = full_name.strip().partition(" ")
    user = User.objects.create_user(
        username=email, email=email, password=password, first_name=first[:150], last_name=last[:150], phone=phone,
    )
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
