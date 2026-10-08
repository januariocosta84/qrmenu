"""Public (customer) API. No login; access is scoped by QR-scan session and order tokens."""
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.api import CsrfEnforcedSessionAuthentication, get_public_restaurant
from apps.core.i18n import ui_text
from apps.core.utils import client_ip
from apps.menu.queries import public_menu
from apps.menu.serializers import PublicCategorySerializer
from apps.orders.models import Order
from apps.orders.serializers import PlaceOrderSerializer, PublicOrderSerializer
from apps.orders.services import OrderError, place_order

from .access import PAID, customer_ref, record_scan_order, remember_order, table_access


class PublicAPIView(APIView):
    authentication_classes = [CsrfEnforcedSessionAuthentication]
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_read"


class PublicMenuAPI(PublicAPIView):
    def get(self, request, slug):
        restaurant = get_public_restaurant(slug)
        data = PublicCategorySerializer(public_menu(restaurant), many=True, context={"lang": request.ui_lang}).data
        return Response({
            "restaurant": {
                "name": restaurant.name,
                "currency": restaurant.currency,
                "currency_symbol": restaurant.currency_symbol,
                "service_charge_percent": str(restaurant.service_charge_percent),
                "vat_percent": str(restaurant.vat_percent) if restaurant.vat_enabled else "0",
                "vat_inclusive": restaurant.vat_inclusive,
                "is_accepting_orders": restaurant.is_accepting_orders,
            },
            "categories": data,
        })


class AccessAPI(PublicAPIView):
    """GET → can this phone still order here? Used to end ordering live after payment."""

    def get(self, request, slug):
        restaurant = get_public_restaurant(slug)
        table, reason = table_access(request, restaurant)
        return Response({"can_order": table is not None, "table": table.number if table else None, "reason": reason})


class PlaceOrderAPI(PublicAPIView):
    throttle_scope = "order_create"

    def post(self, request, slug):
        restaurant = get_public_restaurant(slug)
        lang = request.ui_lang
        table, reason = table_access(request, restaurant)
        if table is None:
            key = "paid_scan_again" if reason == PAID else "scan_to_order"
            return Response(
                {"error": "scan_required", "reason": reason, "message": ui_text(key, lang)},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = PlaceOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            order = place_order(
                restaurant=restaurant,
                table=table,
                lines=data["items"],
                customer_ref=customer_ref(request),
                customer_name=data.get("customer_name", ""),
                customer_phone=data.get("customer_phone", ""),
                note=data.get("note", ""),
                payment_method=data["payment_method"],
                language=lang,
                ip=client_ip(request),
            )
        except OrderError as exc:
            message = {
                "unavailable": ui_text("item_unavailable_error", lang),
                "not_accepting": ui_text("not_accepting", lang),
            }.get(exc.code, str(exc))
            return Response({"error": exc.code, "message": message, **exc.details}, status=status.HTTP_409_CONFLICT)
        remember_order(request, order)
        record_scan_order(request, order)
        return Response(
            {
                "token": str(order.public_token),
                "number": order.number,
                "status_url": reverse("storefront:order_status", args=[order.public_token]),
            },
            status=status.HTTP_201_CREATED,
        )


class PublicOrderAPI(PublicAPIView):
    def get(self, request, token):
        order = get_object_or_404(Order.objects.prefetch_related("items__options"), public_token=token)
        return Response(PublicOrderSerializer(order).data)
