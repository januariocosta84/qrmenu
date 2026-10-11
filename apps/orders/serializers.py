from rest_framework import serializers

from apps.menu.service_types import VEHICLE_CODES

from .models import MAX_QUANTITY, Order, OrderItem, OrderStatus, PaymentMethod


class OrderItemSerializer(serializers.ModelSerializer):
    options = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = ["name", "quantity", "unit_price", "line_total", "note", "options", "plate_number", "vehicle_type"]

    def get_options(self, obj):
        return [{"name": o.name, "price": str(o.price)} for o in obj.options.all()]


class StaffOrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    placed_by_name = serializers.SerializerMethodField()
    pay_group = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    payment_method_display = serializers.CharField(source="get_payment_method_display", read_only=True)

    class Meta:
        model = Order
        fields = [
            "id", "number", "status", "status_display", "table_number", "table_session", "source", "placed_by_name",
            "service_type", "plate_number", "order_group",
            "customer_name", "customer_phone", "note", "items",
            "subtotal", "service_charge", "vat_label", "vat_percent", "vat_inclusive", "vat_amount", "total", "currency",
            "payment_method", "payment_method_display", "payment_status",
            "estimated_minutes", "created_at", "accepted_at", "preparing_at",
            "ready_at", "completed_at", "cancelled_at", "cancel_reason", "pay_group",
        ]
        read_only_fields = fields

    def get_pay_group(self, obj):
        """Café + car wash from one order are paid together: the unpaid sum of the whole group."""
        if not obj.order_group or obj.payment_status == "paid":
            return None
        from apps.payments.services import group_due, payable_group

        group = payable_group(obj)
        if len(group) < 2:
            return None
        return {"due": f"{group_due(group):.2f}", "numbers": " + ".join(f"#{o.number}" for o in group),
                "ids": [o.pk for o in group]}

    def get_placed_by_name(self, obj):
        u = obj.placed_by
        return (u.get_full_name() or u.username) if u else ""


class PublicOrderSerializer(serializers.ModelSerializer):
    """What a customer may see about their own order (no phone, no internal ids)."""

    token = serializers.UUIDField(source="public_token", read_only=True)
    status = serializers.CharField(source="customer_status", read_only=True)
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "token", "number", "status", "table_number", "items",
            "subtotal", "service_charge", "vat_label", "vat_percent", "vat_inclusive", "vat_amount", "total", "currency",
            "payment_status", "estimated_minutes", "created_at", "ready_at",
        ]
        read_only_fields = fields


# ---- Input -----------------------------------------------------------------

class CartLineSerializer(serializers.Serializer):
    menu_item = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_QUANTITY)
    options = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, max_length=20)
    note = serializers.CharField(required=False, allow_blank=True, max_length=200)
    # Car wash packages: the vehicle (required by the service; ignored for café items)
    plate_number = serializers.CharField(required=False, allow_blank=True, max_length=20)
    vehicle_type = serializers.ChoiceField(choices=VEHICLE_CODES, required=False, allow_blank=True)


class PlaceOrderSerializer(serializers.Serializer):
    """Customers send ids and quantities only. Prices are always computed server-side."""

    items = CartLineSerializer(many=True, allow_empty=False, max_length=50)
    customer_name = serializers.CharField(required=False, allow_blank=True, max_length=80)
    customer_phone = serializers.RegexField(
        r"^[0-9+\-() ]{0,30}$", required=False, allow_blank=True, max_length=30,
        error_messages={"invalid": "Enter a valid phone number."},
    )
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)
    payment_method = serializers.ChoiceField(
        choices=PaymentMethod.CUSTOMER_CHOICES, required=False, default=PaymentMethod.PAY_AT_COUNTER
    )
    walk_in = serializers.BooleanField(required=False, default=False,
                                       help_text="Ordered from the walk-in car wash QR (no table).")


class StatusChangeSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[c for c, _ in OrderStatus.CHOICES])
    note = serializers.CharField(required=False, allow_blank=True, max_length=200)
