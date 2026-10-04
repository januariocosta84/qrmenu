from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel, TranslatableMixin
from apps.restaurants.models import Restaurant

PRICE_KW = dict(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])


class MenuCategory(TranslatableMixin, TimeStampedModel):
    TRANSLATABLE_FIELDS = ("name",)

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=80)
    position = models.PositiveIntegerField(default=0, help_text="Lower numbers appear first.")
    is_active = models.BooleanField(default=True, help_text="Hidden categories are not shown to customers.")

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
    is_available = models.BooleanField(default=True, help_text="Untick to mark as Sold Out.")
    prep_minutes = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MaxValueValidator(240)],
        help_text="Typical preparation time; leave blank to use the restaurant default.",
    )
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "name"]
        indexes = [models.Index(fields=["restaurant", "is_available"])]

    def __str__(self):
        return self.name

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
