"""
Try the demo: a visitor fills in name, address, job title and email on /demo/, and gets an
emailed one-click link (valid DEMO_DAYS days) that logs them into the demo restaurant's
dashboard (settings.DEMO_RESTAURANT, e.g. Kafe Atauro) as the shared demo user.

The demo user holds the view-only Demo role there: every page except staff, branches,
API keys, billing and business profile opens, and DemoReadOnlyMiddleware stops every
change (any POST/PUT/PATCH/DELETE) with a "demo only" message.
"""
import logging
import secrets
from urllib.parse import urlsplit
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives, mail_admins
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.console import geo
from apps.console.models import DemoRequest
from apps.core.permissions import Role
from apps.core.utils import client_ip
from apps.restaurants.models import Restaurant, RestaurantStaff

logger = logging.getLogger(__name__)

DEMO_USERNAME = "demo-visitor"
DEMO_DAYS = 7
SESSION_KEY = "demo_until"
# Unsafe requests a demo visitor may still make.
ALLOWED_WRITES = ("/accounts/logout/", "/i18n/setlang/")


def demo_restaurant():
    return Restaurant.objects.filter(slug=settings.DEMO_RESTAURANT).first() if settings.DEMO_RESTAURANT else None


def demo_user(restaurant):
    """The shared demo account (no password, cannot log in any other way), with the Demo role at `restaurant`."""
    User = get_user_model()
    user, created = User.objects.get_or_create(username=DEMO_USERNAME, defaults={"first_name": "Demo", "last_name": "visitor"})
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    RestaurantStaff.objects.update_or_create(restaurant=restaurant, user=user,
                                             defaults={"role": Role.DEMO, "is_active": True})
    return user


def is_demo(user) -> bool:
    return bool(user and user.is_authenticated and user.username == DEMO_USERNAME)


class DemoRequestForm(forms.ModelForm):
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))

    class Meta:
        model = DemoRequest
        fields = ["name", "job_title", "address", "email"]
        labels = {
            "name": gettext_lazy("Your name"), "job_title": gettext_lazy("Job title"),
            "address": gettext_lazy("Address"), "email": gettext_lazy("Email"),
        }
        help_texts = {"email": gettext_lazy("We send the demo link to this address.")}
        widgets = {
            "name": forms.TextInput(attrs={"autocomplete": "name"}),
            "job_title": forms.TextInput(attrs={"autocomplete": "organization-title",
                                                "placeholder": gettext_lazy("e.g. Restaurant owner, Manager")}),
            "address": forms.TextInput(attrs={"autocomplete": "street-address"}),
            "email": forms.EmailInput(attrs={"autocomplete": "email"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("name", "job_title", "address", "email"):
            self.fields[name].required = True

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("website"):  # honeypot: filled in by bots only
            raise ValidationError(_("Something went wrong. Please try again."))
        return cleaned


def demo_page(request):
    """GET: the form. POST: save the request and email the link."""
    restaurant = demo_restaurant()
    ctx = {"platform_name": settings.PLATFORM_NAME, "available": restaurant is not None, "days": DEMO_DAYS,
           "status": request.GET.get("status", "")}
    if request.method != "POST" or restaurant is None:
        return render(request, "storefront/demo.html", {**ctx, "form": DemoRequestForm()})

    ip = client_ip(request) or ""
    ip_key = f"demo:ip:{ip}"
    if cache.get(ip_key, 0) >= 5:  # per network per hour
        return render(request, "storefront/demo.html", {**ctx, "form": DemoRequestForm(request.POST), "status": "limit"},
                      status=429)
    form = DemoRequestForm(request.POST)
    if not form.is_valid():
        return render(request, "storefront/demo.html", {**ctx, "form": form}, status=400)
    email_key = f"demo:email:{form.cleaned_data['email']}"
    if cache.get(email_key, 0) >= 3:  # per address per day
        return render(request, "storefront/demo.html", {**ctx, "form": form, "status": "limit"}, status=429)

    dr = form.save(commit=False)
    dr.language = (getattr(request, "LANGUAGE_CODE", "") or "")[:5]
    dr.ip_masked = geo.mask(ip) if ip else ""
    dr.token = secrets.token_urlsafe(32)
    dr.expires_at = timezone.now() + timedelta(days=DEMO_DAYS)
    dr.save()
    cache.set(ip_key, cache.get(ip_key, 0) + 1, 3600)
    cache.set(email_key, cache.get(email_key, 0) + 1, 86400)

    if not send_demo_email(request, dr, restaurant):
        return render(request, "storefront/demo.html", {**ctx, "form": form, "status": "mail_failed"}, status=502)
    try:
        mail_admins(f"Demo request: {dr.name} ({dr.job_title})",
                    f"{dr.name}, {dr.job_title}\n{dr.address}\n{dr.email}\n\n"
                    "See all requests in the platform console → Demo requests.")
    except Exception:
        logger.exception("Could not email platform admins about a demo request")
    return redirect(f"{reverse('storefront:demo')}?status=sent")


def send_demo_email(request, dr, restaurant) -> bool:
    base = (settings.PUBLIC_BASE_URL or request.build_absolute_uri("/")).rstrip("/")
    ctx = {
        "dr": dr, "restaurant": restaurant, "platform_name": settings.PLATFORM_NAME, "days": DEMO_DAYS,
        "link": base + reverse("storefront:demo_open", args=[dr.token]),
        "menu_link": base + reverse("storefront:menu", args=[restaurant.slug]),
        "signup_link": base + reverse("accounts:signup"),
    }
    with translation.override(dr.language or None):
        subject = _("Your %(platform)s demo") % {"platform": settings.PLATFORM_NAME}
        text = render_to_string("storefront/email/demo.txt", ctx)
        html = render_to_string("storefront/email/demo.html", ctx)
    msg = EmailMultiAlternatives(subject, text, to=[dr.email])
    msg.attach_alternative(html, "text/html")
    try:
        msg.send()
        return True
    except Exception:
        logger.exception("Could not send the demo email to request %s", dr.pk)
        return False


def demo_open(request, token):
    """The emailed link: log in as the demo user and open the demo restaurant's dashboard."""
    dr = DemoRequest.objects.filter(token=token).first()
    restaurant = demo_restaurant()
    if not dr or restaurant is None or dr.expires_at <= timezone.now():
        return redirect(f"{reverse('storefront:demo')}?status=expired")
    now = timezone.now()
    DemoRequest.objects.filter(pk=dr.pk).update(
        opened_count=dr.opened_count + 1, last_opened_at=now, first_opened_at=dr.first_opened_at or now)
    login(request, demo_user(restaurant), backend=settings.AUTHENTICATION_BACKENDS[0])
    request.session[SESSION_KEY] = dr.expires_at.timestamp()
    request.session["demo_request"] = dr.pk
    request.session.set_expiry(max(60, int((dr.expires_at - now).total_seconds())))
    return redirect("dashboard:overview", restaurant.slug)


class DemoReadOnlyMiddleware:
    """Demo visitors can look at everything they may open, but change nothing; their access ends with the link."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if not is_demo(user):
            return self.get_response(request)
        until = request.session.get(SESSION_KEY)
        if not until or until <= timezone.now().timestamp():
            logout(request)
            return redirect(f"{reverse('storefront:demo')}?status=expired")
        if request.method in ("GET", "HEAD", "OPTIONS") or request.path.startswith(ALLOWED_WRITES):
            return self.get_response(request)
        text = _("This is a demo: changes are not saved. Register your restaurant to use it for real.")
        wants_json = (request.path.startswith("/api/") or "application/json" in request.headers.get("Accept", "")
                      or request.headers.get("X-Requested-With") == "XMLHttpRequest"
                      or request.content_type == "application/json")
        if wants_json:
            return JsonResponse({"detail": text, "error": text, "demo": True}, status=403)
        messages.info(request, text)
        back = request.headers.get("Referer", "")
        if url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            parts = urlsplit(back)
            back = parts.path + (f"?{parts.query}" if parts.query else "")
        else:
            back = reverse("dashboard:home")
        return redirect(back or "/")
