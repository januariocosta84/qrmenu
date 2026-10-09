import calendar
import logging
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.mail import mail_admins
from django.db import transaction
from django.utils import timezone

from .models import BillingSettings, Invoice, Plan, Subscription

logger = logging.getLogger(__name__)

LIMIT_FALLBACK = {
    "max_tables": "MAX_TABLES_PER_RESTAURANT",
    "max_menu_items": "MAX_MENU_ITEMS_PER_RESTAURANT",
    "max_staff": "MAX_STAFF_PER_RESTAURANT",
}


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def default_plan() -> Plan | None:
    cfg = BillingSettings.load()
    if cfg.default_plan_id and cfg.default_plan.is_active:
        return cfg.default_plan
    return Plan.objects.filter(is_active=True).order_by("position", "price_monthly").first()


def start_subscription(restaurant, plan: Plan | None = None) -> Subscription | None:
    plan = plan or default_plan()
    if plan is None:
        return None
    cfg = BillingSettings.load()
    trial = timezone.localdate() + timedelta(days=cfg.trial_days) if cfg.trial_days and not plan.is_free else None
    sub, _ = Subscription.objects.get_or_create(restaurant=restaurant, defaults={"plan": plan, "trial_ends_on": trial})
    return sub


def get_subscription(restaurant) -> Subscription | None:
    """
    The restaurant's subscription, created on first use (existing restaurants get a trial).
    Sub-branches are covered by their main branch's subscription (plan, limits, features, billing).
    """
    if restaurant.parent_id:
        restaurant = restaurant.parent
    try:
        return restaurant.subscription
    except Subscription.DoesNotExist:
        return start_subscription(restaurant)


def plan_limit(restaurant, key: str) -> int:
    sub = get_subscription(restaurant)
    if sub is not None:
        return getattr(sub.plan, key)
    return getattr(settings, LIMIT_FALLBACK[key])


def subscription_allows_orders(restaurant) -> bool:
    sub = get_subscription(restaurant)
    return True if sub is None else sub.allows_orders


# ---------------------------------------------------------------- invoices

def _next_number() -> str:
    cfg = BillingSettings.objects.select_for_update().get(pk=BillingSettings.load().pk)
    n = cfg.next_invoice_number
    BillingSettings.objects.filter(pk=cfg.pk).update(next_invoice_number=n + 1)
    return f"INV-{timezone.localdate().year}-{n:05d}"


def next_period(sub: Subscription) -> tuple[date, date]:
    start = (sub.access_until or timezone.localdate() - timedelta(days=1)) + timedelta(days=1)
    start = max(start, timezone.localdate()) if sub.state == Subscription.EXPIRED else start
    return start, add_months(start, 1) - timedelta(days=1)


@transaction.atomic
def create_invoice(sub: Subscription, *, months: int = 1, amount=None, notes: str = "") -> Invoice:
    start, _ = next_period(sub)
    end = add_months(start, months) - timedelta(days=1)
    return Invoice.objects.create(
        subscription=sub, restaurant=sub.restaurant, number=_next_number(), plan_name=sub.plan.name,
        period_start=start, period_end=end,
        amount=sub.monthly_price * months if amount is None else amount,
        currency=sub.plan.currency, due_date=start,
        notes=(notes or (f"Includes {sub.extra_branches} extra branch(es)" if sub.extra_branches else ""))[:255],
    )


# ---------------------------------------------------------------- branch slots

def branch_slots(main) -> dict:
    """
    How many sub-branches the main branch's plan allows: included in the plan + paid add-ons.
    Active sub-branches (approved, not deactivated) use a slot; pending, rejected, suspended and
    deactivated ones don't.
    """
    from apps.restaurants.models import Restaurant

    main = main.main_branch
    sub = get_subscription(main)
    used = main.sub_branches.filter(branch_status=Restaurant.BRANCH_ACTIVE, branch_closed_at__isnull=True).count()
    if sub is None:  # no billing on this platform: no limit
        return {"included": None, "extra": 0, "total": None, "used": used, "free": 1, "price": None, "open_addon": None}
    total = sub.branch_slots
    return {
        "included": sub.plan.included_branches, "extra": sub.extra_branches, "total": total, "used": used,
        "free": max(0, total - used), "price": sub.plan.extra_branch_price,
        "open_addon": sub.invoices.filter(status=Invoice.OPEN, branch_slots__gt=0).first(),
    }


@transaction.atomic
def create_branch_addon_invoice(sub: Subscription, slots: int = 1) -> Invoice:
    """Invoice for extra branch slots, charged up to the end of the current period; the slots are added once paid."""
    today = timezone.localdate()
    end = sub.access_until if sub.access_until and sub.access_until >= today else add_months(today, 1) - timedelta(days=1)
    days = (end - today).days + 1
    price = sub.plan.extra_branch_price * slots
    amount = price if days >= 28 else (price * days / 30).quantize(Decimal("0.01"))  # pro rata for a part month
    return Invoice.objects.create(
        subscription=sub, restaurant=sub.restaurant, number=_next_number(), plan_name=f"{sub.plan.name} · extra branch",
        period_start=today, period_end=end, amount=amount, currency=sub.plan.currency, due_date=today,
        branch_slots=slots, notes=f"{slots} extra branch slot(s) at {sub.plan.extra_branch_price}/month",
    )


def remove_branch_slot(sub: Subscription) -> bool:
    """Give back one unused paid slot (it stops being invoiced). False when every slot is in use."""
    slots = branch_slots(sub.restaurant)
    if sub.extra_branches < 1 or slots["free"] < 1:
        return False
    sub.extra_branches -= 1
    sub.save(update_fields=["extra_branches", "updated_at"])
    return True


@transaction.atomic
def mark_invoice_paid(invoice: Invoice, *, user=None, method: str = "bank_transfer", reference: str = "") -> Invoice:
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != Invoice.OPEN:
        raise ValueError(f"Invoice {invoice.number} is {invoice.get_status_display().lower()}.")
    invoice.status, invoice.paid_at = Invoice.PAID, timezone.now()
    invoice.method, invoice.reference, invoice.recorded_by = method, reference[:120], user
    invoice.save()
    sub = Subscription.objects.select_for_update().get(pk=invoice.subscription_id)
    if invoice.branch_slots:  # add-on: more branch slots, the subscription period is unchanged
        sub.extra_branches += invoice.branch_slots
        sub.save(update_fields=["extra_branches", "updated_at"])
        return invoice
    sub.paid_until = max(d for d in (sub.paid_until, invoice.period_end) if d)
    sub.cancelled_at = None
    sub.save(update_fields=["paid_until", "cancelled_at", "updated_at"])
    return invoice


def void_invoice(invoice: Invoice) -> Invoice:
    if invoice.status != Invoice.OPEN:
        raise ValueError("Only open invoices can be voided.")
    invoice.status = Invoice.VOID
    invoice.save(update_fields=["status"])
    return invoice


def generate_due_invoices() -> list[Invoice]:
    """Create the next invoice for every billable subscription close to (or past) its end date."""
    cfg = BillingSettings.load()
    horizon = timezone.localdate() + timedelta(days=cfg.invoice_days_before)
    created = []
    subs = Subscription.objects.select_related("plan", "restaurant").filter(
        comped=False, cancelled_at__isnull=True,
        restaurant__parent__isnull=True,  # sub-branches are billed through their main branch
    )
    for sub in subs:
        if not sub.is_billable or sub.invoices.filter(status=Invoice.OPEN, branch_slots=0).exists():
            continue
        end = sub.access_until
        if end is None or end <= horizon:
            created.append(create_invoice(sub))
    return created


# ---------------------------------------------------------------- plan changes

def change_plan(sub: Subscription, plan: Plan) -> Subscription:
    sub.plan, sub.requested_plan, sub.requested_at = plan, None, None
    sub.save(update_fields=["plan", "requested_plan", "requested_at", "updated_at"])
    return sub


def request_plan_change(sub: Subscription, plan: Plan, user) -> Subscription:
    sub.requested_plan, sub.requested_at = plan, timezone.now()
    sub.save(update_fields=["requested_plan", "requested_at", "updated_at"])
    try:
        mail_admins(
            f"Plan change requested: {sub.restaurant.name}",
            f"{user.get_full_name() or user.username} ({user.email}) asked to move {sub.restaurant.name} "
            f"from {sub.plan.name} to {plan.name}.\n\nApprove it in the platform console → Restaurants.",
        )
    except Exception:
        logger.exception("Could not email platform admins about a plan change request")
    return sub
