from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.orders.models import Order, PaymentMethod
from apps.restaurants.models import Restaurant


class Payment(TimeStampedModel):
    """
    One payment attempt/record against an order.

    v1 only records manual payments (cash / pay at counter) entered by staff.
    Online providers (bank transfer, cards, gateways, QR payments) plug in via
    `apps.payments.providers` and store their ids in `provider_reference` and
    raw callback data in `metadata`.
    """

    PENDING, SUCCEEDED, FAILED, REFUNDED = "pending", "succeeded", "failed", "refunded"
    STATUS_CHOICES = [(PENDING, _("Pending")), (SUCCEEDED, _("Succeeded")), (FAILED, _("Failed")), (REFUNDED, _("Refunded"))]

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="payments")
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=20, choices=PaymentMethod.CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, default="USD")
    provider = models.CharField(max_length=40, default="manual")
    provider_reference = models.CharField(max_length=120, blank=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    # Cash only: what the customer handed over, and the change given back.
    # `amount` is always what was applied to the order.
    cash_tendered = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    change_given = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["restaurant", "status", "created_at"])]

    def __str__(self):
        return f"{self.get_method_display()} {self.amount} for #{self.order.number}"


class CashDrawerOpening(models.Model):
    """Audit log: every time the cash drawer is opened (or fails to open), and by whom."""

    PAYMENT, NO_SALE, TEST = "payment", "no_sale", "test"
    REASON_CHOICES = [(PAYMENT, _("Cash payment")), (NO_SALE, _("No sale / change")), (TEST, _("Test"))]

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="drawer_openings")
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    success = models.BooleanField(default=True)
    error = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["restaurant", "created_at"])]

    def __str__(self):
        return f"{self.get_reason_display()} @ {self.created_at:%Y-%m-%d %H:%M}"


class CashSession(models.Model):
    """
    One till session: the change put in the drawer at the start (opening float),
    and the count at the end. Expected cash = float + cash payments + cash in − cash out
    while the session was open; the difference shows whether the drawer is over or short.
    """

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="cash_sessions")
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    opened_at = models.DateTimeField(default=timezone.now)
    opening_float = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    opening_count = models.JSONField(default=dict, blank=True, help_text=_("Notes and coins counted, e.g. {\"20\": 2}."))
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    counted_cash = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    closing_count = models.JSONField(default=dict, blank=True)
    # Snapshots taken at closing, so the report never changes afterwards.
    cash_sales = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    expected_cash = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-opened_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["restaurant"], condition=models.Q(closed_at__isnull=True), name="one_open_cash_session"
            )
        ]

    def __str__(self):
        return f"Cash session {self.restaurant} {self.opened_at:%Y-%m-%d %H:%M}"

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    @property
    def difference(self):
        """Counted − expected: positive = over, negative = short (closed sessions only)."""
        if self.counted_cash is None or self.expected_cash is None:
            return None
        return self.counted_cash - self.expected_cash


class CashMovement(models.Model):
    """Cash put into or taken out of the drawer during a session, e.g. buying ice."""

    IN, OUT = "in", "out"
    KIND_CHOICES = [(IN, _("Cash in")), (OUT, _("Cash out"))]

    session = models.ForeignKey(CashSession, on_delete=models.CASCADE, related_name="movements")
    kind = models.CharField(max_length=3, choices=KIND_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    reason = models.CharField(max_length=120)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.amount}: {self.reason}"
