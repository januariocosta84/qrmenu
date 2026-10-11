from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel, TranslatableMixin
from apps.restaurants.models import Restaurant

from . import service_types

PRICE_KW = dict(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])


class MenuCategory(TranslatableMixin, TimeStampedModel):
    TRANSLATABLE_FIELDS = ("name",)

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=80)
    position = models.PositiveIntegerField(default=0, help_text=_("Lower numbers appear first."))
    is_active = models.BooleanField(default=True, help_text=_("Hidden categories are not shown to customers."))
    service_type = models.CharField(
        _("Service"), max_length=20, choices=service_types.CHOICES, default=service_types.CAFE, db_index=True,
        help_text=_("Café items go to the kitchen; car wash packages go to the counter."),
    )

    class Meta:
        ordering = ["position", "id"]
        verbose_name_plural = "menu categories"
        constraints = [models.UniqueConstraint(fields=["restaurant", "name"], name="unique_category_name")]

    def __str__(self):
        return self.name


class MenuItem(TranslatableMixin, TimeStampedModel):
    TRANSLATABLE_FIELDS = ("name", "description")

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="menu_items")
    category = models.ForeignKey(MenuCategory, on_delete=models.RESTRICT, related_name="items")
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, max_length=600)
    price = models.DecimalField(**PRICE_KW)
    image = models.ImageField(upload_to="menu/items/", blank=True)
    is_available = models.BooleanField(default=True, help_text=_("Untick to mark as Sold Out."))
    prep_minutes = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MaxValueValidator(240)],
        help_text=_("Typical preparation time; leave blank to use the restaurant default."),
    )
    position = models.PositiveIntegerField(default=0)
    # Car wash packages: price per vehicle type, e.g. {"motorbike": "3.00"}; missing types use `price`.
    vehicle_prices = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["position", "name"]
        indexes = [models.Index(fields=["restaurant", "is_available"])]

    def __str__(self):
        return self.name

    @property
    def service_type(self) -> str:
        return self.category.service_type if self.category_id else service_types.CAFE

    def price_for(self, vehicle_type: str = ""):
        """Price for a vehicle type (car wash); everything else uses the normal price."""
        from decimal import Decimal, InvalidOperation

        raw = (self.vehicle_prices or {}).get(vehicle_type) if vehicle_type else None
        try:
            return Decimal(str(raw)) if raw not in (None, "") else self.price
        except InvalidOperation:
            return self.price

    def clean(self):
        if self.category_id and self.restaurant_id and self.category.restaurant_id != self.restaurant_id:
            raise ValidationError({"category": "Category belongs to a different restaurant."})


class MenuItemOption(TranslatableMixin, models.Model):
    """An optional add-on / extra ingredient, e.g. 'Fried Egg +$0.50'."""

    TRANSLATABLE_FIELDS = ("name",)

    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name="options")
    name = models.CharField(max_length=80)
    price = models.DecimalField(default=0, **PRICE_KW)
    is_available = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.name} (+{self.price})"
