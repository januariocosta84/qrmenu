from django.db.models import Prefetch

from . import service_types
from .models import MenuCategory, MenuItem, MenuItemOption


def public_menu(restaurant, services=None):
    """Active categories (of the restaurant's enabled services, or only `services`) with items and options, in 3 queries."""
    codes = service_types.enabled_codes(restaurant)
    if services is not None:
        codes = [c for c in codes if c in services]
    return (
        MenuCategory.objects.filter(restaurant=restaurant, is_active=True, service_type__in=codes)
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
