"""
Staff API under /api/v1/r/<slug>/. Authenticated by session (dashboard) or
token (`Authorization: Token <key>`, for future POS/mobile clients). Every
query is filtered by the restaurant resolved in RestaurantScopedMixin.
"""
import secrets
from decimal import Decimal

from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from rest_framework import serializers, status
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.api import RestaurantScopedMixin
from apps.menu.models import MenuItem
from apps.menu.serializers import StaffItemSerializer
from apps.orders import realtime
from apps.orders.models import Notification, Order, OrderStatus, PaymentMethod
from apps.orders.serializers import StaffOrderSerializer, StatusChangeSerializer
from apps.orders.serializers import CartLineSerializer
from apps.orders.services import OrderError, change_status, place_order
from apps.restaurants.models import Table
from apps.payments.providers import STAFF_RECORDABLE_METHODS
from apps.payments.drawer import NOT_CONFIGURED, open_cash_drawer
from apps.payments.receipts import network_printing_available, parse_order_ids, print_receipt_network, receipt_orders
from apps.payments.models import CashDrawerOpening
from apps.payments.services import receive_cash, receive_payment


class StaffAPIView(RestaurantScopedMixin, APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "staff"

    def orders(self):
        return Order.objects.filter(restaurant=self.restaurant).prefetch_related("items__options")


class OrderListAPI(StaffAPIView):
    """GET ?status=new,preparing  ?active=1  ?table=12  ?session=<id>  ?since=<iso>"""

    def get(self, request, slug):
        qs = self.orders()
        p = request.query_params
        if p.get("active") in {"1", "true"}:
            qs = qs.filter(status__in=OrderStatus.ACTIVE)
        if p.get("status"):
            qs = qs.filter(status__in=p["status"].split(","))
        if p.get("table"):
            qs = qs.filter(table_number=p["table"])
        if p.get("session", "").isdigit():
            qs = qs.filter(table_session_id=p["session"])
        if p.get("since") and (since := parse_datetime(p["since"])):
            qs = qs.filter(created_at__gte=since)
        qs = qs.order_by("created_at")[:200]
        return Response(StaffOrderSerializer(qs, many=True).data)


class StaffOrderInputSerializer(serializers.Serializer):
    table = serializers.IntegerField(required=False, allow_null=True, help_text="Table id; empty = counter/takeaway")
    guest = serializers.CharField(required=False, allow_blank=True, max_length=16,
                                  help_text="Existing guest id at the table; empty = new guest")
    items = CartLineSerializer(many=True, allow_empty=False, max_length=50)
    customer_name = serializers.CharField(required=False, allow_blank=True, max_length=80)
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)


class StaffCreateOrderAPI(StaffAPIView):
    """POST: a waiter enters an order for a guest who has no smartphone."""

    required_capability = "take_orders"

    def post(self, request, slug):
        s = StaffOrderInputSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        table = None
        if d.get("table"):
            table = Table.objects.filter(restaurant=self.restaurant, pk=d["table"], is_active=True).first()
            if table is None:
                return Response({"error": "invalid_table", "message": "Unknown table."}, status=400)
        guest = d.get("guest") or ""
        if guest:
            session = table.current_session() if table else None
            if not session or not session.orders.filter(customer_ref=guest).exists():
                return Response({"error": "invalid_guest", "message": "That guest is not at this table any more."},
                                status=400)
        else:
            guest = "w" + secrets.token_hex(7)  # new guest without a phone
        try:
            order = place_order(
                restaurant=self.restaurant, table=table, lines=d["items"], customer_ref=guest,
                customer_name=d.get("customer_name", ""), note=d.get("note", ""),
                language="en", placed_by=request.user,
            )
        except OrderError as exc:
            return Response({"error": exc.code, "message": str(exc), **exc.details}, status=status.HTTP_409_CONFLICT)
        data = StaffOrderSerializer(get_object_or_404(self.orders(), pk=order.pk)).data
        return Response({**data, "guest": guest}, status=status.HTTP_201_CREATED)


class OrderDetailAPI(StaffAPIView):
    def get(self, request, slug, pk):
        return Response(StaffOrderSerializer(get_object_or_404(self.orders(), pk=pk)).data)


class OrderStatusAPI(StaffAPIView):
    required_capability = "kitchen"

    def post(self, request, slug, pk):
        order = get_object_or_404(Order, restaurant=self.restaurant, pk=pk)
        s = StatusChangeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        to_status = s.validated_data["status"]
        if to_status == OrderStatus.CANCELLED and not self.restaurant.user_can(request.user, "cancel_orders"):
            return Response({"error": "forbidden", "message": "Your role cannot cancel orders."}, status=403)
        try:
            order = change_status(order, to_status, user=request.user, note=s.validated_data.get("note", ""))
        except OrderError as exc:
            return Response({"error": exc.code, "message": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(StaffOrderSerializer(get_object_or_404(self.orders(), pk=order.pk)).data)


class PaymentInputSerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=STAFF_RECORDABLE_METHODS, required=False, default="cash")
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"), required=False)
    reference = serializers.CharField(max_length=120, required=False, allow_blank=True)
    tendered = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0.01"), required=False,
        help_text="Cash handed over by the customer; the response includes the change to give.",
    )


class RecordPaymentAPI(StaffAPIView):
    """POST {} → cash received for the full balance → order becomes Paid."""

    required_capability = "payments"

    def post(self, request, slug, pk):
        order = get_object_or_404(Order, restaurant=self.restaurant, pk=pk)
        s = PaymentInputSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        change = None
        try:
            if d["method"] == PaymentMethod.CASH and not d.get("amount"):
                result = receive_cash([order], tendered=d.get("tendered"), user=request.user)
                payment, change = result.payments[0], result.change
            else:
                payment = receive_payment(
                    order, user=request.user, method=d["method"],
                    amount=d.get("amount"), reference=d.get("reference", ""),
                )
        except OrderError as exc:
            return Response({"error": exc.code, "message": str(exc)}, status=status.HTTP_409_CONFLICT)
        drawer = NOT_CONFIGURED
        if s.validated_data["method"] == PaymentMethod.CASH:
            drawer = open_cash_drawer(self.restaurant, user=request.user, order=order)
        order.refresh_from_db()
        r = self.restaurant
        receipt = {"mode": r.receipt_prompt, "orders": [order.pk], "printed": None}
        if order.payment_status != "paid" or r.receipt_prompt == r.RECEIPT_NEVER:
            receipt["mode"] = r.RECEIPT_NEVER
        elif r.receipt_prompt == r.RECEIPT_ALWAYS and network_printing_available(r):
            receipt["printed"] = print_receipt_network(r, receipt_orders(r, [order.pk])).as_dict()
        return Response(
            {
                "payment_id": payment.id,
                "tendered": str(payment.cash_tendered) if payment.cash_tendered is not None else None,
                "change": str(change) if change is not None else None,
                "order": StaffOrderSerializer(get_object_or_404(self.orders(), pk=pk)).data,
                "drawer": drawer.as_dict(),
                "receipt": receipt,
            },
            status=status.HTTP_201_CREATED,
        )


class ReceiptPrintAPI(StaffAPIView):
    """POST {"orders": [12, 13]} → print a receipt on the network receipt printer."""

    def post(self, request, slug):
        orders = receipt_orders(self.restaurant, parse_order_ids(request.data.get("orders")))
        if not orders:
            return Response({"printed": False, "attempted": False, "message": "No orders to print."}, status=400)
        result = print_receipt_network(self.restaurant, orders)
        return Response(result.as_dict(), status=200 if result.printed else 503)


class CashDrawerAPI(StaffAPIView):
    """POST → open the drawer without a sale (logged)."""

    required_capability = "payments"

    def post(self, request, slug):
        result = open_cash_drawer(self.restaurant, user=request.user, reason=CashDrawerOpening.NO_SALE)
        return Response(result.as_dict(), status=200 if result.opened else 503)


class MenuItemListAPI(StaffAPIView):
    required_capability = "toggle_availability"

    def get(self, request, slug):
        qs = MenuItem.objects.filter(restaurant=self.restaurant).select_related("category")
        return Response(StaffItemSerializer(qs, many=True).data)


class ItemAvailabilityAPI(StaffAPIView):
    """POST {"is_available": false} → Sold Out. Customers' menus update live."""

    required_capability = "toggle_availability"

    def post(self, request, slug, pk):
        item = get_object_or_404(MenuItem, restaurant=self.restaurant, pk=pk)
        value = request.data.get("is_available")
        if not isinstance(value, bool):
            return Response({"error": "invalid", "message": "is_available must be true or false."}, status=400)
        item.is_available = value
        item.save(update_fields=["is_available", "updated_at"])
        realtime.broadcast_availability(item)
        return Response(StaffItemSerializer(item).data)


class NotificationsAPI(StaffAPIView):
    def get(self, request, slug):
        qs = Notification.objects.filter(restaurant=self.restaurant)
        unread = qs.filter(is_read=False).count()
        items = [
            {"id": n.id, "title": n.title, "body": n.body, "is_read": n.is_read,
             "order": n.order_id, "created_at": n.created_at}
            for n in qs[:20]
        ]
        return Response({"unread": unread, "results": items})

    def post(self, request, slug):
        """Mark all (or the given ids) as read."""
        qs = Notification.objects.filter(restaurant=self.restaurant, is_read=False)
        ids = request.data.get("ids")
        if isinstance(ids, list):
            try:
                qs = qs.filter(id__in=[int(i) for i in ids])
            except (TypeError, ValueError):
                return Response({"error": "invalid"}, status=400)
        updated = qs.update(is_read=True)
        return Response({"marked_read": updated})


class ThrottledObtainAuthToken(ObtainAuthToken):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_token"
