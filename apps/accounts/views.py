import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.core.utils import client_ip

from . import signup as signup_service
from .forms import SignupForm

logger = logging.getLogger(__name__)
User = get_user_model()


def _key(request, username: str) -> str:
    return f"login-fail:{client_ip(request)}:{(username or '').lower()[:150]}"


def _hit(key: str, window: int) -> int:
    """Increment a rate-limit counter and return the new count."""
    if cache.add(key, 1, window):
        return 1
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        return 1


class RateLimitedLoginView(auth_views.LoginView):
    """Locks a username+IP pair out for a while after repeated failures."""

    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        key = _key(request, request.POST.get("username", ""))
        if cache.get(key, 0) >= settings.LOGIN_MAX_ATTEMPTS:
            messages.error(request, _("Too many failed login attempts. Please try again in 15 minutes."))
            return self.get(request, *args, **kwargs)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        _hit(_key(self.request, self.request.POST.get("username", "")), settings.LOGIN_LOCKOUT_SECONDS)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(_key(self.request, self.request.POST.get("username", "")))
        response = super().form_valid(form)
        self.request.session["profile_prompt"] = True  # business profile pop-up, once per login
        return response

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "signup_open": settings.SIGNUP_MODE != "closed"}


def signup(request):
    """Step 1: the owner's details. Nothing is created yet: a 6-digit code goes to the email first."""
    if settings.SIGNUP_MODE == "closed":
        raise Http404
    if request.user.is_authenticated:
        return redirect("dashboard:home")
    form = SignupForm(request.POST or None)
    if request.method == "POST":
        if _hit(f"signup:{client_ip(request)}", 3600) > settings.SIGNUPS_PER_IP_PER_HOUR:
            messages.error(request, _("Too many sign-ups from your network. Please try again in an hour."))
        elif form.is_valid():
            email = form.cleaned_data["email"]
            if _hit(f"signup-code:{email}", 3600) > 3:  # don't let anyone flood an inbox with codes
                messages.error(request, _("Too many codes were sent to this email. Please try again in an hour."))
            else:
                try:
                    signup_service.start_signup(request, form.cleaned_data)
                    return redirect("accounts:signup_verify")
                except Exception:
                    logger.exception("Sign-up code to %s failed", email)
                    request.session.pop(signup_service.PENDING_KEY, None)
                    form.add_error("email", _("We couldn't send an email to this address. Please check it and try again."))
    return render(request, "accounts/signup.html", {
        "form": form, "approval_required": settings.SIGNUP_MODE == "approval",
    })


def signup_verify(request):
    """Step 2: the code from the email. Only the right code creates the account and the restaurant."""
    if settings.SIGNUP_MODE == "closed":
        raise Http404
    if request.user.is_authenticated:
        return redirect("dashboard:home")
    pending = signup_service.pending_signup(request)
    if not pending:
        messages.info(request, _("Please fill in the registration form first."))
        return redirect("accounts:signup")
    error = ""
    if request.method == "POST" and request.POST.get("action") == "resend":
        wait = signup_service.can_resend(pending)
        if wait < 0:
            error = _("No more codes can be sent for this registration. Please start again.")
        elif wait:
            error = _("Please wait %(s)s seconds before asking for a new code.") % {"s": wait}
        else:
            try:
                signup_service.send_signup_code(request)
                messages.success(request, _("We sent a new code to %(email)s.") % {"email": pending["email"]})
                return redirect("accounts:signup_verify")
            except Exception:
                logger.exception("Sign-up code to %s failed", pending["email"])
                error = _("We couldn't send the email right now. Please try again later.")
    elif request.method == "POST":
        result = signup_service.check_signup_code(request, request.POST.get("code", ""))
        if result == "ok":
            if User.objects.filter(email__iexact=pending["email"]).exists():  # registered meanwhile, e.g. in another tab
                request.session.pop(signup_service.PENDING_KEY, None)
                messages.error(request, _("An account with this email already exists. Try logging in or resetting your password."))
                return redirect("accounts:login")
            user, restaurant, published = signup_service.finish_signup(request)
            login(request, user, backend="apps.accounts.backends.EmailOrUsernameBackend")
            request.session["profile_prompt"] = True
            if settings.SIGNUP_MODE == "open":
                messages.success(request, _("Email confirmed. Welcome! Your restaurant is ready: add your menu and tables."))
            else:
                messages.success(request, _("Email confirmed. Your restaurant is waiting for approval by the platform team."))
            return redirect("dashboard:overview", restaurant.slug)
        if result in ("locked", "missing"):
            request.session.pop(signup_service.PENDING_KEY, None)
            messages.error(request, _("Too many wrong codes. Please fill in the form again to get a new code."))
            return redirect("accounts:signup")
        error = (_("This code has expired. Ask for a new code below.") if result == "expired"
                 else _("That code is not right. Check the email and try again."))
    pending = signup_service.pending_signup(request)
    return render(request, "accounts/signup_verify.html", {
        "email": pending["email"], "error": error, "minutes": signup_service.CODE_TTL // 60,
        "can_resend": signup_service.can_resend(pending) >= 0,
    }, status=400 if error else 200)


def verify_email(request, token):
    user = signup_service.verify_token(token)
    if user is None:
        messages.error(request, _("This confirmation link is invalid or has expired. Log in and request a new one."))
        return redirect("accounts:login")
    published = signup_service.mark_verified(user)
    if published and settings.SIGNUP_MODE == "open":
        messages.success(request, _("Email confirmed — your restaurant is now live! Customers can scan your QR codes."))
    elif published:
        messages.success(request, _("Email confirmed. Your restaurant is waiting for approval by the platform team."))
    else:
        messages.success(request, _("Email confirmed."))
    return redirect("dashboard:home" if request.user.is_authenticated else "accounts:login")


@login_required
@require_POST
def resend_verification(request):
    user = request.user
    if user.email_verified:
        messages.info(request, _("Your email is already confirmed."))
    elif _hit(f"resend-verify:{user.pk}", 3600) > 3:
        messages.error(request, _("Please wait a while before requesting another email."))
    else:
        try:
            signup_service.send_verification_email(request, user)
            messages.success(request, _("Confirmation email sent to %(email)s.") % {"email": user.email})
        except Exception:
            logger.exception("Verification email to %s failed", user.email)
            messages.error(request, _("We couldn't send the email right now. Please try again later."))
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    return redirect("dashboard:home")


class ThrottledPasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = "accounts/email/password_reset.txt"
    subject_template_name = "accounts/email/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")

    def form_valid(self, form):
        # Always show the same "check your email" page; just stop sending when hammered.
        if _hit(f"pwreset:{client_ip(self.request)}", 3600) > 10:
            return redirect(self.success_url)
        return super().form_valid(form)
