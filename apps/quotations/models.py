"""
Quotations (price offers) for catering and procurement bids.

Lines can come from the restaurant's menu (name and price copied at the time,
so later menu changes don't alter a sent quotation) or be typed in by hand.
"""
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.restaurants.models import Restaurant

CENT = Decimal("0.01")
QUOTE_LANGUAGES = [("en", "English"), ("pt", "Português"), ("tet", "Tetun"), ("id", "Bahasa Indonesia")]


def _money(v) -> Decimal:
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


class Quotation(models.Model):
    DRAFT, SENT, ACCEPTED, REJECTED = "draft", "sent", "accepted", "rejected"
    STATUS_CHOICES = [(DRAFT, _("Draft")), (SENT, _("Sent")), (ACCEPTED, _("Accepted")), (REJECTED, _("Rejected"))]

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="quotations")
    number = models.CharField(max_length=20)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    language = models.CharField(max_length=5, choices=QUOTE_LANGUAGES, default="en")

    client_name = models.CharField(max_length=120)
    client_organization = models.CharField(max_length=160, blank=True)
    client_address = models.TextField(blank=True, max_length=400)
    client_contact = models.CharField(max_length=160, blank=True)
    reference = models.CharField(max_length=80, blank=True)

    title = models.CharField(max_length=160, blank=True)
    event_date = models.DateField(null=True, blank=True)
    guests = models.PositiveIntegerField(null=True, blank=True, validators=[MaxValueValidator(100000)])

    issue_date = models.DateField(default=timezone.localdate)
    valid_until = models.DateField(null=True, blank=True)

    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    vat_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(50)]
    )
    terms = models.TextField(blank=True, max_length=3000)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-issue_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["restaurant", "number"], name="unique_quotation_number")]

    def __str__(self):
        return self.number

    # ---- totals (from the lines; never stored, so they can't drift)
    @property
    def subtotal(self) -> Decimal:
        return sum((i.amount for i in self.items.all()), Decimal("0"))

    @property
    def taxable(self) -> Decimal:
        return max(self.subtotal - self.discount, Decimal("0"))

    @property
    def vat_amount(self) -> Decimal:
        return _money(self.taxable * self.vat_percent / 100)

    @property
    def total(self) -> Decimal:
        return self.taxable + self.vat_amount

    @staticmethod
    def next_number(restaurant, year: int) -> str:
        """Q-2026-0001, counting per restaurant and year. Call inside a transaction."""
        Restaurant.objects.select_for_update().filter(pk=restaurant.pk).first()
        prefix = f"Q-{year}-"
        last = (Quotation.objects.filter(restaurant=restaurant, number__startswith=prefix)
                .order_by("-number").values_list("number", flat=True).first())
        n = int(last.rsplit("-", 1)[1]) + 1 if last else 1
        return f"{prefix}{n:04d}"

    @classmethod
    def create_for(cls, restaurant, user, **fields) -> "Quotation":
        with transaction.atomic():
            issue = fields.get("issue_date") or timezone.localdate()
            fields.setdefault("valid_until", issue + timedelta(days=30))
            return cls.objects.create(
                restaurant=restaurant, created_by=user, number=cls.next_number(restaurant, issue.year), **fields
            )


class QuotationItem(models.Model):
    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="items")
    position = models.PositiveIntegerField(default=0)
    menu_item = models.ForeignKey("menu.MenuItem", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    description = models.CharField(max_length=300)
    unit = models.CharField(max_length=20, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.description

    @property
    def amount(self) -> Decimal:
        return _money(self.quantity * self.unit_price)
