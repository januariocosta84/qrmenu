import secrets
from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.i18n import LANGUAGES
from apps.core.models import TimeStampedModel, TranslatableMixin
from apps.core.permissions import Role, role_can

table_number_validator = RegexValidator(
    r"^[A-Za-z0-9-]{1,20}$", _("Use letters, numbers and dashes only (max 20), e.g. 12 or A-3.")
)


class Restaurant(TranslatableMixin, TimeStampedModel):
    TRANSLATABLE_FIELDS = ("description",)

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=80, unique=True, help_text="Used in menu and QR code URLs.")
    description = models.TextField(blank=True)
    logo = models.ImageField(upload_to="restaurants/logos/", blank=True)
    cover_image = models.ImageField(upload_to="restaurants/covers/", blank=True)
    address = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    opening_hours = models.TextField(blank=True, help_text=_("e.g. Mon–Sat 08:00–22:00"))

    currency = models.CharField(max_length=3, default="USD")
    currency_symbol = models.CharField(max_length=5, default="$")
    service_charge_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(50)],
    )
    # VAT / sales tax. Off by default; switch on in Restaurant settings if required by law.
    vat_enabled = models.BooleanField(default=False, help_text=_("Charge VAT on orders."))
    vat_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(50)],
    )
    vat_inclusive = models.BooleanField(
        default=False,
        help_text=_("Add on top: the bill total goes up by the VAT. Already included: totals stay the same "
                    "and receipts show the VAT part."),
    )
    vat_label = models.CharField(max_length=20, default="VAT", help_text=_("Name printed on bills, e.g. VAT or IVA."))
    vat_number = models.CharField(max_length=40, blank=True, help_text=_("Tax ID printed on receipts."))
    default_language = models.CharField(max_length=5, choices=LANGUAGES, default="en")
    default_prep_minutes = models.PositiveSmallIntegerField(default=15, validators=[MaxValueValidator(240)])

    is_active = models.BooleanField(default=True, help_text="Inactive restaurants are hidden from the public.")
    is_accepting_orders = models.BooleanField(default=True)

    # Cash drawer, connected to a network (ESC/POS) receipt printer.
    DRAWER_PIN_CHOICES = [(0, _("Pin 2 (most drawers)")), (1, _("Pin 5"))]
    cash_drawer_enabled = models.BooleanField(
        default=False, help_text=_("Open the cash drawer automatically when a cash payment is recorded.")
    )
    printer_host = models.CharField(
        max_length=100, blank=True, help_text=_("IP address of the receipt printer the drawer is plugged into, e.g. 192.168.1.50")
    )
    printer_port = models.PositiveIntegerField(
        default=9100, validators=[MinValueValidator(1), MaxValueValidator(65535)],
        help_text=_("Raw printing port; 9100 for almost all network receipt printers."),
    )
    drawer_pin = models.PositiveSmallIntegerField(choices=DRAWER_PIN_CHOICES, default=0)
    # Cloud-friendly: the drawer is plugged into a receipt printer installed on the cashier's
    # computer, and the printer driver opens it whenever something prints.
    cash_drawer_via_printer = models.BooleanField(
        default=False,
        help_text=_("The cash drawer is plugged into the receipt printer of this computer. A small slip prints to open it."),
    )

    # Receipts
    RECEIPT_ASK, RECEIPT_ALWAYS, RECEIPT_NEVER = "ask", "always", "never"
    RECEIPT_PROMPT_CHOICES = [
        (RECEIPT_ASK, _('Ask "Print receipt?" after each payment')),
        (RECEIPT_ALWAYS, _("Always print after payment")),
        (RECEIPT_NEVER, _("Never print automatically")),
    ]
    RECEIPT_BROWSER, RECEIPT_NETWORK = "browser", "network"
    RECEIPT_PRINTER_CHOICES = [
        (RECEIPT_BROWSER, _("This device's printer (print dialog)")),
        (RECEIPT_NETWORK, _("Network receipt printer (prints directly)")),
    ]
    receipt_prompt = models.CharField(max_length=10, choices=RECEIPT_PROMPT_CHOICES, default=RECEIPT_ASK)
    receipt_printer = models.CharField(max_length=10, choices=RECEIPT_PRINTER_CHOICES, default=RECEIPT_BROWSER)
    receipt_width = models.PositiveSmallIntegerField(
        choices=[(48, _("80 mm paper")), (32, _("58 mm paper"))], default=48,
        help_text=_("Paper width of the network receipt printer."),
    )
    receipt_footer = models.CharField(max_length=200, blank=True, default="Thank you! Obrigadu! Terima kasih!")

    # Per-restaurant sequential order numbers (#1001, #1002, ...)
    next_order_number = models.PositiveIntegerField(default=1001)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("storefront:menu", args=[self.slug])

    def allocate_order_number(self) -> int:
        """Atomically reserve the next order number. Call inside a transaction."""
        locked = Restaurant.objects.select_for_update().only("id", "next_order_number").get(pk=self.pk)
        number = locked.next_order_number
        Restaurant.objects.filter(pk=self.pk).update(next_order_number=number + 1)
        return number

    def staff_role(self, user) -> str | None:
        if not user or not user.is_authenticated:
            return None
        membership = self.staff.filter(user=user, is_active=True).only("role").first()
        if membership:
            return membership.role
        return Role.OWNER if user.is_superuser else None

    def user_can(self, user, capability: str) -> bool:
        return role_can(self.staff_role(user), capability)


class RestaurantStaff(TimeStampedModel):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="staff")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.CHOICES, default=Role.WAITER)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "restaurant staff"
        constraints = [models.UniqueConstraint(fields=["restaurant", "user"], name="unique_staff_membership")]
        indexes = [models.Index(fields=["user", "is_active"])]

    def __str__(self):
        return f"{self.user} @ {self.restaurant} ({self.role})"


class Table(TimeStampedModel):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="tables")
    number = models.CharField(max_length=20, validators=[table_number_validator])
    label = models.CharField(max_length=60, blank=True, help_text=_("Optional, e.g. 'Terrace' or 'Window'."))
    seats = models.PositiveSmallIntegerField(default=4, validators=[MaxValueValidator(100)])
    is_active = models.BooleanField(default=True)
    # Bumped each time staff free the table: scans from an earlier "epoch" stop working.
    access_epoch = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["restaurant", "number"], name="unique_table_number")]

    def __str__(self):
        return f"Table {self.number}"

    @property
    def sort_key(self):
        return (0, int(self.number), "") if self.number.isdigit() else (1, 0, self.number.lower())

    def active_qr(self) -> "QRCode":
        qr = self.qr_codes.filter(is_active=True).order_by("-created_at").first()
        return qr or QRCode.objects.create(table=self)

    @transaction.atomic
    def regenerate_qr(self) -> "QRCode":
        """Revoke the current QR code (old printouts stop working) and issue a new one."""
        self.qr_codes.filter(is_active=True).update(is_active=False, revoked_at=timezone.now())
        return QRCode.objects.create(table=self)

    def free(self, user=None) -> None:
        """Staff cleared the table: close its bill and end ordering on every phone that scanned it."""
        session = self.current_session()
        if session:
            session.close(user=user)
        Table.objects.filter(pk=self.pk).update(access_epoch=models.F("access_epoch") + 1)
        self.refresh_from_db(fields=["access_epoch"])

    def current_session(self, create=False) -> "TableSession | None":
        session = self.sessions.filter(status=TableSession.OPEN).first()
        if session is None and create:
            session = TableSession.objects.create(table=self, restaurant_id=self.restaurant_id)
        return session


def _qr_token() -> str:
    return secrets.token_urlsafe(12)


class QRCode(models.Model):
    """
    The secret printed in a table's QR code. Scanning it proves the customer is
    physically at the table, which is what authorizes anonymous ordering.
    """

    table = models.ForeignKey(Table, on_delete=models.CASCADE, related_name="qr_codes")
    token = models.CharField(max_length=32, unique=True, default=_qr_token, editable=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "QR code"
        indexes = [models.Index(fields=["table", "is_active"])]

    def __str__(self):
        return f"QR for {self.table} ({'active' if self.is_active else 'revoked'})"

    def get_path(self) -> str:
        url = reverse("storefront:table", args=[self.table.restaurant.slug, self.table.number])
        return f"{url}?k={self.token}"

    def get_url(self, request=None) -> str:
        base = settings.PUBLIC_BASE_URL
        if base:
            return base + self.get_path()
        if request is not None:
            return request.build_absolute_uri(self.get_path())
        return self.get_path()


class TableSession(models.Model):
    """Groups all orders placed at a table during one sitting."""

    OPEN, CLOSED = "open", "closed"
    STATUS_CHOICES = [(OPEN, "Open"), (CLOSED, "Closed")]

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="table_sessions")
    table = models.ForeignKey(Table, on_delete=models.CASCADE, related_name="sessions")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=OPEN)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-opened_at"]
        constraints = [
            models.UniqueConstraint(fields=["table"], condition=Q(status="open"), name="one_open_session_per_table")
        ]
        indexes = [models.Index(fields=["restaurant", "status"])]

    def __str__(self):
        return f"{self.table} session #{self.pk}"

    def close(self, user=None):
        self.status = self.CLOSED
        self.closed_at = timezone.now()
        self.closed_by = user
        self.save(update_fields=["status", "closed_at", "closed_by"])

    @property
    def is_stale(self) -> bool:
        return self.opened_at < timezone.now() - timedelta(hours=12)
