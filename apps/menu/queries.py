from django.db.models import Prefetch

from .models import MenuCategory, MenuItem, MenuItemOption


def public_menu(restaurant):
    """Active categories with their items and options, in 3 queries."""
    return (
        MenuCategory.objects.filter(restaurant=restaurant, is_active=True)
        .prefetch_related(
            Prefetch(
                "items",
                queryset=MenuItem.objects.order_by("position", "name").prefetch_related(
                    Prefetch("options", queryset=MenuItemOption.objects.order_by("position", "id"))
                ),
            )
        )
        .order_by("position", "id")
    )
