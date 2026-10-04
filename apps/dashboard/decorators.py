from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404

from apps.billing.services import get_subscription
from apps.core.permissions import CAPABILITIES, Role, role_can
from apps.restaurants.models import Restaurant


def go_live_status(restaurant) -> str:
    """live | verify_email | awaiting_approval | suspended"""
    if restaurant.is_active:
        return "live"
    owners_verified = restaurant.staff.filter(role=Role.OWNER, user__email_verified=True).exists()
    if not owners_verified and restaurant.staff.filter(role=Role.OWNER).exists():
        return "verify_email"
    return "awaiting_approval" if settings.SIGNUP_MODE == "approval" else "suspended"


def staff_view(capability: str = "view_dashboard"):
    """
    Resolve the restaurant from the URL slug and require the logged-in user to
    hold a role there with `capability`. Non-members get 404 (not 403) so one
    restaurant's staff cannot even confirm another restaurant's dashboard exists.
    Sets request.restaurant, request.role and request.can (capability → bool).
    """

    def decorator(view):
        @login_required
        @wraps(view)
        def wrapper(request, slug, *args, **kwargs):
            restaurant = Restaurant.objects.filter(slug=slug).first()
            role = restaurant.staff_role(request.user) if restaurant else None
            if not role:
                raise Http404("Restaurant not found")
            if not role_can(role, capability):
                raise PermissionDenied
            request.restaurant = restaurant
            request.role = role
            request.can = {cap: role_can(role, cap) for cap in CAPABILITIES}
            request.go_live = go_live_status(restaurant)
            request.billing = get_subscription(restaurant)
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
