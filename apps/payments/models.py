from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

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
    STATUS_CHOICES = [(PENDING, "Pending"), (SUCCEEDED, "Succeeded"), (FAILED, "Failed"), (REFUNDED, "Refunded")]

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
    REASON_CHOICES = [(PAYMENT, "Cash payment"), (NO_SALE, "No sale / change"), (TEST, "Test")]

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
