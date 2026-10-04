import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.core.utils import client_ip

from . import signup as signup_service
from .forms import SignupForm

logger = logging.getLogger(__name__)


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
            messages.error(request, "Too many failed login attempts. Please try again in 15 minutes.")
            return self.get(request, *args, **kwargs)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        _hit(_key(self.request, self.request.POST.get("username", "")), settings.LOGIN_LOCKOUT_SECONDS)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(_key(self.request, self.request.POST.get("username", "")))
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "signup_open": settings.SIGNUP_MODE != "closed"}


def signup(request):
    if settings.SIGNUP_MODE == "closed":
        raise Http404
    if request.user.is_authenticated:
        return redirect("dashboard:home")
    form = SignupForm(request.POST or None)
    if request.method == "POST":
        if _hit(f"signup:{client_ip(request)}", 3600) > settings.SIGNUPS_PER_IP_PER_HOUR:
            messages.error(request, "Too many sign-ups from your network. Please try again in an hour.")
        elif form.is_valid():
            d = form.cleaned_data
            user, restaurant = signup_service.register_restaurant(
                restaurant_name=d["restaurant_name"], full_name=d["full_name"], email=d["email"],
                password=d["password"], phone=d.get("phone", ""),
            )
            login(request, user, backend="apps.accounts.backends.EmailOrUsernameBackend")
            try:
                signup_service.send_verification_email(request, user)
                messages.success(request, f"Welcome! We sent a confirmation link to {user.email}.")
            except Exception:
                logger.exception("Verification email to %s failed", user.email)
                messages.warning(request, "Your account is ready, but we couldn't send the confirmation email. "
                                          "Use “Resend email” below.")
            return redirect("dashboard:overview", restaurant.slug)
    return render(request, "accounts/signup.html", {
        "form": form, "approval_required": settings.SIGNUP_MODE == "approval",
    })


def verify_email(request, token):
    user = signup_service.verify_token(token)
    if user is None:
        messages.error(request, "This confirmation link is invalid or has expired. Log in and request a new one.")
        return redirect("accounts:login")
    published = signup_service.mark_verified(user)
    if published and settings.SIGNUP_MODE == "open":
        messages.success(request, "Email confirmed — your restaurant is now live! Customers can scan your QR codes.")
    elif published:
        messages.success(request, "Email confirmed. Your restaurant is waiting for approval by the platform team.")
    else:
        messages.success(request, "Email confirmed.")
    return redirect("dashboard:home" if request.user.is_authenticated else "accounts:login")


@login_required
@require_POST
def resend_verification(request):
    user = request.user
    if user.email_verified:
        messages.info(request, "Your email is already confirmed.")
    elif _hit(f"resend-verify:{user.pk}", 3600) > 3:
        messages.error(request, "Please wait a while before requesting another email.")
    else:
        try:
            signup_service.send_verification_email(request, user)
            messages.success(request, f"Confirmation email sent to {user.email}.")
        except Exception:
            logger.exception("Verification email to %s failed", user.email)
            messages.error(request, "We couldn't send the email right now. Please try again later.")
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
