"""Dashboard pages for the Ordering API: keys, webhooks, request log and documentation (main branch owner)."""
from django.conf import settings
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from apps.dashboard.decorators import plan_feature, staff_view
from apps.restaurants.models import BusinessProfile

from . import webhooks
from .forms import ApiKeyEditForm, ApiKeyForm, WebhookForm
from .models import ApiKey, ApiRequestLog, SandboxOrder, Webhook
from .services import access_problem, family

MAX_KEYS, MAX_WEBHOOKS = 20, 5


def _base_url(request) -> str:
    """The API's address; always https (plain HTTP requests are refused)."""
    url = request.build_absolute_uri("/api/ordering/v1/")
    return "https://" + url.split("://", 1)[1]


def _main_only(request):
    """API settings live on the main branch; sub-branches never see keys."""
    r = request.restaurant
    return None if r.is_main_branch else redirect("dashboard:api", r.main_branch.slug)


@staff_view("manage_staff")
@plan_feature("api")
def api_page(request):
    if (go := _main_only(request)) is not None:
        return go
    r = request.restaurant
    branches = family(r)
    verified = BusinessProfile.objects.filter(restaurant=r, status=BusinessProfile.VERIFIED).exists()
    action = request.POST.get("action", "") if request.method == "POST" else ""
    key_form = ApiKeyForm(request.POST if action == "create_key" else None, family=branches)
    hook_form = WebhookForm(request.POST if action == "add_webhook" else None)

    if action == "create_key" and verified and key_form.is_valid():
        if r.api_keys.filter(revoked_at__isnull=True).count() >= MAX_KEYS:
            messages.error(request, _("You can have up to %(n)s active API keys.") % {"n": MAX_KEYS})
        else:
            d = key_form.cleaned_data
            key, raw = ApiKey.generate(restaurant=r, name=d["name"], is_sandbox=d["is_sandbox"], created_by=request.user)
            key.branches.set(d["branches"])
            request.session["new_api_key"] = {"id": key.pk, "raw": raw}  # shown once, on the next page load
            return redirect("dashboard:api", r.slug)
    elif action in ("rename_key", "revoke_key"):
        key = get_object_or_404(ApiKey, restaurant=r, pk=request.POST.get("key"))
        if action == "revoke_key" and key.is_active:
            key.revoke()
            messages.success(request, _("API key “%(name)s” revoked. Requests with it are now refused.") % {"name": key.name})
        elif action == "rename_key":
            form = ApiKeyEditForm(request.POST, family=branches, prefix=f"k{key.pk}")
            if form.is_valid():
                key.name = form.cleaned_data["name"]
                key.save(update_fields=["name"])
                key.branches.set(form.cleaned_data["branches"])
                messages.success(request, _("API key saved."))
        return redirect("dashboard:api", r.slug)
    elif action == "add_webhook" and verified and hook_form.is_valid():
        if r.webhooks.count() >= MAX_WEBHOOKS:
            messages.error(request, _("You can have up to %(n)s webhooks.") % {"n": MAX_WEBHOOKS})
        else:
            hook = hook_form.save(commit=False)
            hook.restaurant = r
            hook.save()
            messages.success(request, _("Webhook added. Its signing secret is shown below."))
            return redirect("dashboard:api", r.slug)
    elif action in ("delete_webhook", "toggle_webhook", "test_webhook"):
        hook = get_object_or_404(Webhook, restaurant=r, pk=request.POST.get("webhook"))
        if action == "delete_webhook":
            hook.delete()
            messages.success(request, _("Webhook removed."))
        elif action == "toggle_webhook":
            hook.is_active = not hook.is_active
            hook.save(update_fields=["is_active"])
        else:
            webhooks.send_test(hook)
            messages.success(request, _("Test event sent. Refresh in a few seconds to see the result."))
        return redirect("dashboard:api", r.slug)

    logs = ApiRequestLog.objects.filter(restaurant=r).select_related("api_key")
    log_key, log_result = request.GET.get("key", ""), request.GET.get("result", "")
    if log_key.isdigit():
        logs = logs.filter(api_key_id=log_key)
    if log_result == "ok":
        logs = logs.filter(status_code__lt=400)
    elif log_result == "error":
        logs = logs.filter(status_code__gte=400)
    keys = list(r.api_keys.prefetch_related("branches"))
    for k in keys:
        k.edit_form = ApiKeyEditForm(initial={"name": k.name, "branches": list(k.branches.all())}, family=branches,
                                     prefix=f"k{k.pk}")
    return render(request, "dashboard/api.html", {
        "keys": keys, "key_form": key_form, "hook_form": hook_form, "verified": verified,
        "new_key": request.session.pop("new_api_key", None),
        "problem": access_problem(r), "branches": branches,
        "hooks": r.webhooks.prefetch_related("deliveries"),
        "logs": logs[:100], "log_key": log_key, "log_result": log_result,
        "sandbox_orders": SandboxOrder.objects.filter(api_key__restaurant=r).select_related("branch", "api_key")[:10],
        "base_url": _base_url(request),
    })


@staff_view("manage_staff")
@plan_feature("api")
def api_docs(request):
    if not request.restaurant.is_main_branch:
        return redirect("dashboard:api_docs", request.restaurant.main_branch.slug)
    r = request.restaurant
    from apps.menu.models import MenuItem

    item = MenuItem.objects.filter(restaurant=r, is_available=True).order_by("position", "id").first()
    return render(request, "dashboard/api_docs.html", {
        "base_url": _base_url(request),
        "branches": family(r), "example_item": item,
        "rate": settings.ORDERING_API_RATE_PER_MINUTE,
    })
