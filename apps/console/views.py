"""
Platform console (/platform/) for the platform owner: restaurants, users,
subscription plans, invoices and billing settings. Superusers only; everyone
else gets 404 so the console isn't advertised.
"""
import secrets
from datetime import timedelta
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordResetForm
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from apps.billing import services as billing
from apps.billing.models import BillingSettings, Invoice, Plan, Subscription
from apps.core.permissions import Role
from apps.core.utils import client_ip
from apps.orders.models import Order, OrderStatus
from apps.restaurants.models import Restaurant

from .forms import BillingSettingsForm, MarkPaidForm, NewInvoiceForm, PlanForm
from .models import AuditLog

User = get_user_model()


def platform_admin(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_active and request.user.is_superuser):
            raise Http404
        return view(request, *args, **kwargs)
    return wrapper


def audit(request, action, target="", details=""):
    AuditLog.objects.create(user=request.user, action=action, target=str(target)[:200], details=str(details)[:500],
                            ip=client_ip(request))


def _mrr():
    total = Decimal("0")
    for sub in Subscription.objects.select_related("plan").filter(comped=False, cancelled_at__isnull=True):
        if sub.state in (Subscription.ACTIVE, Subscription.GRACE) and not sub.plan.is_free:
            total += sub.plan.price_monthly
    return total


# ---------------------------------------------------------------- overview

@platform_admin
def overview(request):
    today = timezone.localdate()
    since = timezone.now() - timedelta(days=30)
    for r in Restaurant.objects.filter(subscription__isnull=True):  # older restaurants: create on first look
        billing.start_subscription(r)
    subs = list(Subscription.objects.select_related("plan", "restaurant"))
    states = {}
    for s in subs:
        states[s.state] = states.get(s.state, 0) + 1
    open_invoices = Invoice.objects.filter(status=Invoice.OPEN)
    return render(request, "console/overview.html", {
        "restaurants": Restaurant.objects.aggregate(
            total=Count("id"), live=Count("id", filter=Q(is_active=True)), hidden=Count("id", filter=Q(is_active=False))
        ),
        "users": User.objects.count(),
        "orders_30d": Order.objects.filter(created_at__gte=since).exclude(status=OrderStatus.CANCELLED).count(),
        "mrr": _mrr(),
        "states": states,
        "open_total": open_invoices.aggregate(s=Sum("amount"))["s"] or 0,
        "open_count": open_invoices.count(),
        "overdue": open_invoices.filter(due_date__lt=today).select_related("restaurant")[:8],
        "requests": Subscription.objects.filter(requested_plan__isnull=False).select_related("restaurant", "plan", "requested_plan"),
        "recent": Restaurant.objects.order_by("-created_at")[:8],
        "paid_30d": Invoice.objects.filter(status=Invoice.PAID, paid_at__gte=since).aggregate(s=Sum("amount"))["s"] or 0,
    })


# ---------------------------------------------------------------- restaurants

@platform_admin
def restaurants(request):
    qs = Restaurant.objects.select_related("subscription__plan").annotate(
        order_count=Count("orders", distinct=True)
    ).order_by("-created_at")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(slug__icontains=q) | Q(email__icontains=q)
                       | Q(staff__user__email__icontains=q)).distinct()
    status = request.GET.get("status", "")
    if status == "live":
        qs = qs.filter(is_active=True)
    elif status == "hidden":
        qs = qs.filter(is_active=False)
    rows = list(qs)
    for r in rows:
        r.sub = billing.get_subscription(r)
        r.owner = r.staff.filter(role=Role.OWNER).select_related("user").first()
    billing_filter = request.GET.get("billing", "")
    if billing_filter:
        rows = [r for r in rows if r.sub and r.sub.state == billing_filter]
    page = Paginator(rows, 30).get_page(request.GET.get("page"))
    return render(request, "console/restaurants.html", {
        "page": page, "q": q, "status": status, "billing_filter": billing_filter,
        "billing_states": Subscription.STATE_LABELS.items(),
    })


RESTAURANT_ACTIONS = {
    "approve", "suspend", "set_plan", "set_paid_until", "extend_trial", "comp", "uncomp", "cancel", "reactivate",
    "create_invoice", "notes", "approve_request", "reject_request",
}


@platform_admin
def restaurant_detail(request, pk):
    r = get_object_or_404(Restaurant, pk=pk)
    sub = billing.get_subscription(r)
    invoice_form = NewInvoiceForm()
    if request.method == "POST":
        action = request.POST.get("action")
        if action not in RESTAURANT_ACTIONS:
            raise Http404
        msg = _restaurant_action(request, r, sub, action)
        if msg:
            messages.success(request, msg)
        return redirect("console:restaurant", pk=r.pk)
    from apps.menu.models import MenuItem
    from apps.restaurants.models import Table

    return render(request, "console/restaurant_detail.html", {
        "r": r, "sub": sub, "invoice_form": invoice_form,
        "plans": Plan.objects.filter(is_active=True),
        "staff": r.staff.select_related("user").order_by("role"),
        "invoices": r.invoices.all()[:20],
        "usage": {
            "tables": (Table.objects.filter(restaurant=r).count(), sub.plan.max_tables if sub else None),
            "dishes": (MenuItem.objects.filter(restaurant=r).count(), sub.plan.max_menu_items if sub else None),
            "staff": (r.staff.count(), sub.plan.max_staff if sub else None),
        },
        "orders_total": Order.objects.filter(restaurant=r).count(),
        "orders_30d": Order.objects.filter(restaurant=r, created_at__gte=timezone.now() - timedelta(days=30)).count(),
        "audit": AuditLog.objects.filter(target__startswith=f"restaurant:{r.pk} ")[:10],
    })


def _restaurant_action(request, r, sub, action):
    tag = f"restaurant:{r.pk} {r.name}"
    post = request.POST
    if action == "approve":
        r.is_active = True
        r.save(update_fields=["is_active", "updated_at"])
        audit(request, "restaurant.approve", tag)
        return f"{r.name} is now live."
    if action == "suspend":
        r.is_active = False
        r.save(update_fields=["is_active", "updated_at"])
        audit(request, "restaurant.suspend", tag, post.get("reason", ""))
        return f"{r.name} is suspended and hidden from the public."
    if sub is None:
        messages.error(request, "Create a plan first (Plans).")
        return None
    if action == "set_plan":
        plan = get_object_or_404(Plan, pk=post.get("plan"), is_active=True)
        old = sub.plan.name
        billing.change_plan(sub, plan)
        audit(request, "subscription.plan", tag, f"{old} → {plan.name}")
        return f"Plan changed to {plan.name}."
    if action == "approve_request" and sub.requested_plan:
        plan = sub.requested_plan
        billing.change_plan(sub, plan)
        audit(request, "subscription.request_approved", tag, plan.name)
        return f"Plan change approved: {plan.name}."
    if action == "reject_request":
        sub.requested_plan, sub.requested_at = None, None
        sub.save(update_fields=["requested_plan", "requested_at", "updated_at"])
        audit(request, "subscription.request_rejected", tag)
        return "Plan change request dismissed."
    if action == "set_paid_until":
        d = parse_date(post.get("paid_until", ""))
        if not d:
            messages.error(request, "Enter a valid date.")
            return None
        sub.paid_until = d
        sub.save(update_fields=["paid_until", "updated_at"])
        audit(request, "subscription.paid_until", tag, d.isoformat())
        return f"Paid until set to {d:%d %b %Y}."
    if action == "extend_trial":
        try:
            days = max(1, min(365, int(post.get("days", "14"))))
        except ValueError:
            days = 14
        base = max(sub.trial_ends_on or timezone.localdate(), timezone.localdate())
        sub.trial_ends_on = base + timedelta(days=days)
        sub.save(update_fields=["trial_ends_on", "updated_at"])
        audit(request, "subscription.extend_trial", tag, f"+{days} days")
        return f"Trial extended to {sub.trial_ends_on:%d %b %Y}."
    if action in ("comp", "uncomp"):
        sub.comped = action == "comp"
        sub.save(update_fields=["comped", "updated_at"])
        audit(request, f"subscription.{action}", tag)
        return "Marked as complimentary (free, never expires)." if sub.comped else "No longer complimentary."
    if action == "cancel":
        sub.cancelled_at = timezone.now()
        sub.save(update_fields=["cancelled_at", "updated_at"])
        sub.invoices.filter(status=Invoice.OPEN).update(status=Invoice.VOID)
        audit(request, "subscription.cancel", tag)
        return "Subscription cancelled. Customers can no longer order; open invoices were voided."
    if action == "reactivate":
        sub.cancelled_at = None
        sub.save(update_fields=["cancelled_at", "updated_at"])
        audit(request, "subscription.reactivate", tag)
        return "Subscription reactivated."
    if action == "create_invoice":
        form = NewInvoiceForm(post)
        if not form.is_valid():
            messages.error(request, "Check the invoice details.")
            return None
        d = form.cleaned_data
        inv = billing.create_invoice(sub, months=d["months"], amount=d["amount"], notes=d["notes"])
        audit(request, "invoice.create", tag, f"{inv.number} {inv.amount}")
        return f"Invoice {inv.number} created."
    if action == "notes":
        sub.notes = post.get("notes", "")[:2000]
        sub.save(update_fields=["notes", "updated_at"])
        return "Notes saved."
    return None


# ---------------------------------------------------------------- users

@platform_admin
def users(request):
    qs = User.objects.annotate(n_restaurants=Count("memberships", distinct=True)).order_by("-date_joined")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(first_name__icontains=q)
                       | Q(last_name__icontains=q))
    kind = request.GET.get("kind", "")
    if kind == "admins":
        qs = qs.filter(is_superuser=True)
    elif kind == "owners":
        qs = qs.filter(memberships__role=Role.OWNER).distinct()
    elif kind == "disabled":
        qs = qs.filter(is_active=False)
    elif kind == "unverified":
        qs = qs.filter(email_verified=False, is_superuser=False)
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    return render(request, "console/users.html", {"page": page, "q": q, "kind": kind})


@platform_admin
def user_detail(request, pk):
    u = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        tag = f"user:{u.pk} {u.username}"
        if u == request.user and action in ("deactivate", "remove_admin"):
            messages.error(request, "You can't do that to your own account.")
        elif action == "activate":
            u.is_active = True
            u.save(update_fields=["is_active"])
            audit(request, "user.activate", tag)
            messages.success(request, "Account enabled.")
        elif action == "deactivate":
            u.is_active = False
            u.save(update_fields=["is_active"])
            audit(request, "user.deactivate", tag)
            messages.success(request, "Account disabled. The user can no longer log in.")
        elif action == "verify_email":
            u.email_verified = True
            u.save(update_fields=["email_verified"])
            audit(request, "user.verify_email", tag)
            messages.success(request, "Email marked as confirmed.")
        elif action == "set_password":
            password = secrets.token_urlsafe(9)
            u.set_password(password)
            u.save(update_fields=["password"])
            audit(request, "user.set_password", tag)
            messages.warning(request, f"New temporary password for {u.username}: {password}  (shown once; ask them to change it)")
        elif action == "send_reset":
            if not u.email:
                messages.error(request, "This user has no email address.")
            else:
                form = PasswordResetForm({"email": u.email})
                if form.is_valid():
                    form.save(request=request, use_https=request.is_secure(),
                              subject_template_name="accounts/email/password_reset_subject.txt",
                              email_template_name="accounts/email/password_reset.txt")
                audit(request, "user.send_reset", tag)
                messages.success(request, f"Password reset email sent to {u.email}.")
        elif action in ("make_admin", "remove_admin"):
            u.is_superuser = u.is_staff = action == "make_admin"
            u.save(update_fields=["is_superuser", "is_staff"])
            audit(request, f"user.{action}", tag)
            messages.success(request, "Platform admin rights granted." if u.is_superuser else "Platform admin rights removed.")
        else:
            raise Http404
        return redirect("console:user", pk=u.pk)
    return render(request, "console/user_detail.html", {
        "u": u,
        "memberships": u.memberships.select_related("restaurant"),
        "audit": AuditLog.objects.filter(Q(user=u) | Q(target__startswith=f"user:{u.pk} "))[:15],
    })


# ---------------------------------------------------------------- plans

@platform_admin
def plans(request):
    return render(request, "console/plans.html", {
        "plans": Plan.objects.annotate(n=Count("subscriptions")),
        "default_plan_id": BillingSettings.load().default_plan_id,
    })


@platform_admin
def plan_form(request, pk=None):
    plan = get_object_or_404(Plan, pk=pk) if pk else Plan()
    form = PlanForm(request.POST or None, instance=plan)
    if request.method == "POST" and form.is_valid():
        plan = form.save()
        audit(request, "plan.update" if pk else "plan.create", f"plan:{plan.pk} {plan.name}",
              f"{plan.price_monthly} {plan.currency}")
        messages.success(request, f"Plan “{plan.name}” saved.")
        return redirect("console:plans")
    return render(request, "console/form.html", {
        "form": form, "title": f"Edit plan: {plan.name}" if pk else "New plan", "back": reverse("console:plans"),
        "help": "Limits apply immediately to every restaurant on this plan. Existing data above a lower limit is kept; "
                "only adding more is blocked.",
    })


# ---------------------------------------------------------------- invoices

@platform_admin
def invoices(request):
    qs = Invoice.objects.select_related("restaurant").order_by("-created_at")
    status = request.GET.get("status", "")
    if status == "overdue":
        qs = qs.filter(status=Invoice.OPEN, due_date__lt=timezone.localdate())
    elif status in dict(Invoice.STATUS_CHOICES):
        qs = qs.filter(status=status)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(restaurant__name__icontains=q))
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    return render(request, "console/invoices.html", {
        "page": page, "status": status, "q": q, "paid_form": MarkPaidForm(),
        "totals": {
            "open": Invoice.objects.filter(status=Invoice.OPEN).aggregate(s=Sum("amount"))["s"] or 0,
            "overdue": Invoice.objects.filter(status=Invoice.OPEN, due_date__lt=timezone.localdate()).aggregate(s=Sum("amount"))["s"] or 0,
        },
    })


@platform_admin
def invoice_detail(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("restaurant", "recorded_by"), pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "mark_paid":
                form = MarkPaidForm(request.POST)
                if form.is_valid():
                    billing.mark_invoice_paid(inv, user=request.user, **form.cleaned_data)
                    inv.refresh_from_db()
                    audit(request, "invoice.paid", f"restaurant:{inv.restaurant_id} {inv.restaurant.name}",
                          f"{inv.number} {inv.amount} {inv.method}")
                    messages.success(request, f"{inv.number} marked paid. Subscription extended to "
                                              f"{inv.subscription.paid_until:%d %b %Y}.")
            elif action == "void":
                billing.void_invoice(inv)
                audit(request, "invoice.void", f"restaurant:{inv.restaurant_id} {inv.restaurant.name}", inv.number)
                messages.success(request, f"{inv.number} voided.")
            else:
                raise Http404
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect(request.POST.get("next") or reverse("console:invoice", args=[inv.pk]))
    return render(request, "billing/invoice.html", {
        "inv": inv, "cfg": BillingSettings.load(), "console": True, "paid_form": MarkPaidForm(),
    })


@require_POST
@platform_admin
def invoices_generate(request):
    created = billing.generate_due_invoices()
    audit(request, "invoice.generate", "", f"{len(created)} created")
    messages.success(request, f"{len(created)} invoice(s) created." if created else "No invoices are due right now.")
    return redirect("console:invoices")


# ---------------------------------------------------------------- settings & audit

@platform_admin
def billing_settings(request):
    cfg = BillingSettings.load()
    form = BillingSettingsForm(request.POST or None, instance=cfg)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit(request, "settings.billing", "", "updated")
        messages.success(request, "Billing settings saved.")
        return redirect("console:settings")
    return render(request, "console/form.html", {
        "form": form, "title": "Billing settings", "back": reverse("console:overview"),
        "help": "Run “Generate due invoices” on the Invoices page (or the generate_invoices command daily via cron) "
                "to create renewal invoices automatically.",
    })


@platform_admin
def audit_log(request):
    page = Paginator(AuditLog.objects.select_related("user"), 50).get_page(request.GET.get("page"))
    return render(request, "console/audit.html", {"page": page})
