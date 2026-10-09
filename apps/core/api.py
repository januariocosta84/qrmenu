from django.shortcuts import get_object_or_404
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import NotFound, PermissionDenied

from apps.restaurants.models import Restaurant


class CsrfEnforcedSessionAuthentication(SessionAuthentication):
    """
    DRF only checks CSRF for *authenticated* session users. Public customer
    endpoints are anonymous but still cookie-based, so enforce CSRF always.
    """

    def authenticate(self, request):
        self.enforce_csrf(request)
        user = getattr(request._request, "user", None)
        if user and user.is_active and user.is_authenticated:
            return (user, None)
        return None


class RestaurantScopedMixin:
    """
    For staff API views under /api/v1/r/<slug>/. The user must hold a role in
    that restaurant that includes `required_capability`. Unknown restaurants
    and restaurants the user doesn't work at both give 404, so the API never
    reveals other restaurants' data or existence.
    """

    required_capability = "view_orders"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        restaurant = Restaurant.objects.filter(slug=kwargs.get("slug")).first()  # staff may set up before going live
        role = restaurant.staff_role(request.user) if restaurant else None
        if not role:
            raise NotFound()
        if not restaurant.user_can(request.user, self.required_capability):
            raise PermissionDenied("Your role does not allow this action.")
        self.restaurant = restaurant
        self.role = role


def get_public_restaurant(slug: str) -> Restaurant:
    return get_object_or_404(Restaurant, slug=slug, is_active=True, branch_closed_at__isnull=True)
