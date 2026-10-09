"""Daily expenses (ingredients, gas, salaries…), used to show profit = revenue − expenses."""
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.restaurants.models import Restaurant


class Expense(models.Model):
    INGREDIENTS, DRINKS, GAS, UTILITIES, RENT, SALARIES, TRANSPORT, EQUIPMENT, MAINTENANCE, OTHER = (
        "ingredients", "drinks", "gas", "utilities", "rent", "salaries", "transport", "equipment", "maintenance", "other",
    )
    CATEGORY_CHOICES = [
        (INGREDIENTS, _("Ingredients & food supplies")),
        (DRINKS, _("Drinks")),
        (GAS, _("Gas & fuel")),
        (UTILITIES, _("Electricity, water & internet")),
        (RENT, _("Rent")),
        (SALARIES, _("Salaries & wages")),
        (TRANSPORT, _("Transport")),
        (EQUIPMENT, _("Equipment & tableware")),
        (MAINTENANCE, _("Repairs & cleaning")),
        (OTHER, _("Other")),
    ]
    CASH, BANK, OTHER_METHOD = "cash", "bank", "other"
    PAID_WITH_CHOICES = [(CASH, _("Cash")), (BANK, _("Bank transfer")), (OTHER_METHOD, _("Other"))]

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="expenses")
    date = models.DateField(default=timezone.localdate, db_index=True)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default=INGREDIENTS)
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0.01)])
    paid_with = models.CharField(max_length=10, choices=PAID_WITH_CHOICES, default=CASH)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["restaurant", "date"])]

    def __str__(self):
        return f"{self.date} {self.description} {self.amount}"


class RevenueEntry(models.Model):
    """Sales recorded by hand (e.g. walk-in cash sales not entered as orders). Added to order revenue."""

    CASH, TRANSFER, OTHER = "cash", "transfer", "other"
    METHOD_CHOICES = [(CASH, _("Cash")), (TRANSFER, _("Transfer")), (OTHER, _("Other"))]

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="revenue_entries")
    date = models.DateField(default=timezone.localdate, db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0.01)])
    method = models.CharField(max_length=10, choices=METHOD_CHOICES, default=CASH)
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["restaurant", "date"])]

    def __str__(self):
        return f"{self.date} {self.amount} ({self.method})"
