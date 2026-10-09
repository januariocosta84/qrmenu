import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.restaurants.models import Restaurant, Table, TableSession

MONEY_KW = dict(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
MAX_QUANTITY = 50


class OrderStatus:
    NEW = "new"
    ACCEPTED = "accepted"
    PREPARING = "preparing"
    READY = "ready"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    CHOICES = [
        (NEW, _("New")),
        (ACCEPTED, _("Accepted")),
        (PREPARING, _("Preparing")),
        (READY, _("Ready")),
        (COMPLETED, _("Completed")),
        (CANCELLED, _("Cancelled")),
    ]
    ACTIVE = (NEW, ACCEPTED, PREPARING, READY)
    FINAL = (COMPLETED, CANCELLED)

    # Allowed workflow transitions. The kitchen's "Accept" goes straight from
    # NEW to PREPARING; ACCEPTED exists for restaurants that queue first.
    TRANSITIONS = {
        NEW: {ACCEPTED, PREPARING, CANCELLED},
        ACCEPTED: {PREPARING, CANCELLED},
        PREPARING: {READY, CANCELLED},
        READY: {COMPLETED, PREPARING},
        COMPLETED: set(),
        CANCELLED: set(),
    }


class PaymentMethod:
    PAY_AT_COUNTER = "pay_at_counter"
    CASH = "cash"
    MANUAL = "manual"
    # Reserved for future online integrations:
    BANK_TRANSFER = "bank_transfer"
    CARD = "card"
    ONLINE_GATEWAY = "online_gateway"
    QR_PAYMENT = "qr_payment"

    CHOICES = [
        (PAY_AT_COUNTER, _("Pay at restaurant")),
        (CASH, _("Cash")),
        (MANUAL, _("Manual payment")),
        (BANK_TRANSFER, _("Bank transfer")),
        (CARD, _("Visa / Mastercard")),
        (ONLINE_GATEWAY, _("Online payment gateway")),
        (QR_PAYMENT, _("QR payment")),
    ]
    # Methods customers may choose in v1.
    CUSTOMER_CHOICES = (PAY_AT_COUNTER, CASH)


class PaymentStatus:
    UNPAID = "unpaid"
    PAID = "paid"
    REFUNDED = "refunded"
    CHOICES = [(UNPAID, _("Unpaid")), (PAID, _("Paid")), (REFUNDED, _("Refunded"))]


class Order(TimeStampedModel):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="orders")
    table = models.ForeignKey(Table, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    table_session = models.ForeignKey(
        TableSession, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders"
    )
    # Snapshot so history survives table renames/deletion.
    table_number = models.CharField(max_length=20, blank=True)
    number = models.PositiveIntegerField()
    # Unguessable id the customer uses to track the order. Never expose `id`.
    public_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    status = models.CharField(max_length=20, choices=OrderStatus.CHOICES, default=OrderStatus.NEW)
    # Anonymous id of the phone (browser) that placed the order. Groups each
    # customer's orders at a shared table into their own bill.
    customer_ref = models.CharField(max_length=16, blank=True, db_index=True)
    customer_name = models.CharField(max_length=80, blank=True)
    customer_phone = models.CharField(max_length=30, blank=True)
    note = models.TextField(blank=True, max_length=500)
    language = models.CharField(max_length=5, default="en")

    currency = models.CharField(max_length=3, default="USD")
    subtotal = models.DecimalField(**MONEY_KW)
    service_charge = models.DecimalField(default=0, **MONEY_KW)
    # VAT snapshot at order time (0 when the restaurant doesn't charge VAT).
    vat_label = models.CharField(max_length=20, blank=True)
    vat_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    vat_inclusive = models.BooleanField(default=False, help_text=_("VAT is part of the item prices (not added on top)."))
    vat_amount = models.DecimalField(default=0, **MONEY_KW)
    total = models.DecimalField(**MONEY_KW)

    payment_method = models.CharField(
        max_length=20, choices=PaymentMethod.CHOICES, default=PaymentMethod.PAY_AT_COUNTER
    )
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.CHOICES, default=PaymentStatus.UNPAID)

    estimated_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    preparing_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=200, blank=True)

    placed_ip = models.GenericIPAddressField(null=True, blank=True)
    # "qr" = customer's phone, "staff" = entered by a waiter (guest without a smartphone).
    SOURCE_QR, SOURCE_STAFF, SOURCE_API = "qr", "staff", "api"
    source = models.CharField(max_length=10, choices=[(SOURCE_QR, _("QR code")), (SOURCE_STAFF, _("Staff")),
                                                      (SOURCE_API, _("Ordering API"))], default=SOURCE_QR)
    # Orders from the Ordering API: which key placed it, and the ordering app's own reference.
    api_key = models.ForeignKey("integrations.ApiKey", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="orders")
    external_ref = models.CharField(max_length=64, blank=True)
    placed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["restaurant", "number"], name="unique_order_number_per_restaurant")
        ]
        indexes = [
            models.Index(fields=["restaurant", "status", "created_at"]),
            models.Index(fields=["restaurant", "created_at"]),
            models.Index(fields=["table_session"]),
        ]

    def __str__(self):
        return f"Order #{self.number}"

    @property
    def vat_rate_label(self) -> str:
        """e.g. "VAT 10%" (empty when the order has no VAT)."""
        if not self.vat_amount:
            return ""
        return f"{self.vat_label or 'VAT'} {self.vat_percent.normalize():f}%"

    @property
    def is_active(self) -> bool:
        return self.status in OrderStatus.ACTIVE

    def can_transition(self, to_status: str) -> bool:
        return to_status in OrderStatus.TRANSITIONS.get(self.status, set())

    @property
    def customer_status(self) -> str:
        """Status shown to customers: Received → Preparing → Ready → Completed."""
        return OrderStatus.NEW if self.status == OrderStatus.ACCEPTED else self.status


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    menu_item = models.ForeignKey(
        "menu.MenuItem", null=True, blank=True, on_delete=models.SET_NULL, related_name="order_items"
    )
    # Snapshots: menu prices/names may change after the order is placed.
    name = models.CharField(max_length=120)
    unit_price = models.DecimalField(**MONEY_KW)
    quantity = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(MAX_QUANTITY)])
    note = models.CharField(max_length=200, blank=True)
    line_total = models.DecimalField(**MONEY_KW, help_text="(unit price + add-ons) × quantity")

    def __str__(self):
        return f"{self.name} × {self.quantity}"


class OrderItemOption(models.Model):
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="options")
    option = models.ForeignKey("menu.MenuItemOption", null=True, blank=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=80)
    price = models.DecimalField(**MONEY_KW)

    def __str__(self):
        return self.name


class OrderStatusHistory(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_history")
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, choices=OrderStatus.CHOICES)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name_plural = "order status history"

    def __str__(self):
        return f"#{self.order.number}: {self.from_status or '-'} → {self.to_status}"


class Notification(models.Model):
    NEW_ORDER = "new_order"
    ORDER_CANCELLED = "order_cancelled"
    KIND_CHOICES = [(NEW_ORDER, _("New order")), (ORDER_CANCELLED, _("Order cancelled"))]

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="notifications")
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=30, choices=KIND_CHOICES)
    title = models.CharField(max_length=120)
    body = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["restaurant", "is_read", "created_at"])]

    def __str__(self):
        return self.title
