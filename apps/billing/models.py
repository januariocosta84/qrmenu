"""
Platform billing: plans, one subscription per restaurant, and invoices.

Payments are recorded manually by the platform owner (bank transfer, cash,
mobile money…); marking an invoice paid extends the subscription. The
structure leaves room for an online payment provider later.
"""
from datetime import date, timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.restaurants.models import Restaurant


class Plan(TimeStampedModel):
    name = models.CharField(max_length=60, unique=True)
    description = models.CharField(max_length=200, blank=True)
    price_monthly = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, default="USD")
    max_tables = models.PositiveIntegerField(default=30, validators=[MinValueValidator(1), MaxValueValidator(10000)])
    max_menu_items = models.PositiveIntegerField(default=200, validators=[MinValueValidator(1), MaxValueValidator(10000)])
    max_staff = models.PositiveIntegerField(default=15, validators=[MinValueValidator(1), MaxValueValidator(1000)])
    is_active = models.BooleanField(default=True, help_text="Inactive plans can't be chosen for new subscriptions.")
    is_public = models.BooleanField(default=True, help_text="Shown to restaurant owners on their Billing page.")
    position = models.PositiveIntegerField(default=0)
    # Extra features included in this plan (see FEATURES).
    feature_analytics = models.BooleanField(default=False, help_text=_("Analytics page (trends, busy times, best sellers)."))
    feature_cash_register = models.BooleanField(default=False, help_text=_("Cash register: opening change and end-of-day reconciliation."))
    feature_quotations = models.BooleanField(default=False, help_text=_("Quotations for catering and procurement bids."))
    feature_expenses = models.BooleanField(default=False, help_text=_("Daily expenses and profit per day, week, month and year."))
    feature_branches = models.BooleanField(default=False, help_text=_("Several branches under one owner, with an all-branches overview."))

    included_branches = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(100)],
        help_text="Sub-branches included in the price (0 = main branch only).",
    )
    extra_branch_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)],
        help_text="Monthly price of each extra branch above the included ones.",
    )

    FEATURES = ("analytics", "cash_register", "quotations", "expenses", "branches")

    class Meta:
        ordering = ["position", "price_monthly", "name"]

    def __str__(self):
        return self.name

    @property
    def is_free(self) -> bool:
        return self.price_monthly == 0

    def has_feature(self, name: str) -> bool:
        return bool(getattr(self, f"feature_{name}", False))


class BillingSettings(models.Model):
    """Singleton (pk=1) with platform-wide billing options, edited in the console."""

    default_plan = models.ForeignKey(Plan, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                     help_text="Plan given to newly registered restaurants.")
    trial_days = models.PositiveSmallIntegerField(default=30, validators=[MaxValueValidator(365)],
                                                  help_text="Free trial for new restaurants (0 = no trial).")
    grace_days = models.PositiveSmallIntegerField(default=7, validators=[MaxValueValidator(60)],
                                                  help_text="Days after expiry before ordering is switched off.")
    invoice_days_before = models.PositiveSmallIntegerField(default=7, validators=[MaxValueValidator(60)],
                                                           help_text="Create the next invoice this many days before the period ends.")
    payment_instructions = models.TextField(
        blank=True, help_text="Shown to restaurant owners on their Billing page and invoices (bank account, mobile money…).",
    )
    next_invoice_number = models.PositiveIntegerField(default=1)
    branch_flag_limit = models.PositiveSmallIntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(50)],
        help_text="Flag an account for review when it requests more branches than this within 30 days.",
    )

    class Meta:
        verbose_name = verbose_name_plural = "billing settings"

    def __str__(self):
        return "Billing settings"

    @classmethod
    def load(cls) -> "BillingSettings":
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj


class Subscription(TimeStampedModel):
    TRIAL, ACTIVE, GRACE, EXPIRED, CANCELLED = "trial", "active", "grace", "expired", "cancelled"
    STATE_LABELS = {TRIAL: _("Trial"), ACTIVE: _("Active"), GRACE: _("Payment due"), EXPIRED: _("Expired"), CANCELLED: _("Cancelled")}

    restaurant = models.OneToOneField(Restaurant, on_delete=models.CASCADE, related_name="subscription")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    trial_ends_on = models.DateField(null=True, blank=True)
    paid_until = models.DateField(null=True, blank=True)
    comped = models.BooleanField(default=False, help_text="Complimentary: never expires and is never invoiced.")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    requested_plan = models.ForeignKey(Plan, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    requested_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    extra_branches = models.PositiveSmallIntegerField(default=0, help_text="Paid extra branch slots (add-ons).")

    def __str__(self):
        return f"{self.restaurant} · {self.plan}"

    @property
    def branch_slots(self) -> int:
        return self.plan.included_branches + self.extra_branches

    @property
    def monthly_price(self):
        return self.plan.price_monthly + self.extra_branches * self.plan.extra_branch_price

    @property
    def access_until(self) -> date | None:
        dates = [d for d in (self.paid_until, self.trial_ends_on) if d]
        return max(dates) if dates else None

    @property
    def state(self) -> str:
        today = timezone.localdate()
        if self.cancelled_at:
            return self.CANCELLED
        if self.comped or self.plan.is_free:
            return self.ACTIVE
        if self.paid_until and self.paid_until >= today:
            return self.ACTIVE
        if self.trial_ends_on and self.trial_ends_on >= today:
            return self.TRIAL
        end = self.access_until
        if end and today <= end + timedelta(days=BillingSettings.load().grace_days):
            return self.GRACE
        return self.EXPIRED

    @property
    def state_label(self) -> str:
        if self.comped and not self.cancelled_at:
            return _("Complimentary")
        if self.plan.is_free and not self.cancelled_at:
            return _("Free plan")
        return self.STATE_LABELS[self.state]

    @property
    def allows_orders(self) -> bool:
        return self.state in (self.TRIAL, self.ACTIVE, self.GRACE)

    @property
    def days_left(self) -> int | None:
        end = self.access_until
        if end is None or self.comped or self.plan.is_free:
            return None
        return (end - timezone.localdate()).days

    @property
    def is_billable(self) -> bool:
        return not (self.comped or self.plan.is_free or self.cancelled_at)


class Invoice(models.Model):
    OPEN, PAID, VOID = "open", "paid", "void"
    STATUS_CHOICES = [(OPEN, _("Open")), (PAID, _("Paid")), (VOID, _("Void"))]
    METHOD_CHOICES = [
        ("bank_transfer", "Bank transfer"), ("cash", "Cash"), ("mobile_money", "Mobile money"),
        ("card", "Card"), ("other", "Other"),
    ]

    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="invoices")
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="invoices")
    number = models.CharField(max_length=20, unique=True)
    plan_name = models.CharField(max_length=60)
    period_start = models.DateField()
    period_end = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=OPEN)
    due_date = models.DateField()
    paid_at = models.DateTimeField(null=True, blank=True)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, blank=True)
    reference = models.CharField(max_length=120, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    notes = models.CharField(max_length=255, blank=True)
    branch_slots = models.PositiveSmallIntegerField(
        default=0, help_text="Extra branch slots this invoice buys (an add-on invoice); added when it is paid.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "due_date"]), models.Index(fields=["restaurant", "status"])]

    def __str__(self):
        return self.number

    @property
    def is_overdue(self) -> bool:
        return self.status == self.OPEN and self.due_date < timezone.localdate()
