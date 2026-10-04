from rest_framework import serializers

from .models import MAX_QUANTITY, Order, OrderItem, OrderStatus, PaymentMethod


class OrderItemSerializer(serializers.ModelSerializer):
    options = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = ["name", "quantity", "unit_price", "line_total", "note", "options"]

    def get_options(self, obj):
        return [{"name": o.name, "price": str(o.price)} for o in obj.options.all()]


class StaffOrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    placed_by_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    payment_method_display = serializers.CharField(source="get_payment_method_display", read_only=True)

    class Meta:
        model = Order
        fields = [
            "id", "number", "status", "status_display", "table_number", "table_session", "source", "placed_by_name",
            "customer_name", "customer_phone", "note", "items",
            "subtotal", "service_charge", "total", "currency",
            "payment_method", "payment_method_display", "payment_status",
            "estimated_minutes", "created_at", "accepted_at", "preparing_at",
            "ready_at", "completed_at", "cancelled_at", "cancel_reason",
        ]
        read_only_fields = fields

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
            "subtotal", "service_charge", "total", "currency",
            "payment_status", "estimated_minutes", "created_at", "ready_at",
        ]
        read_only_fields = fields


# ---- Input -----------------------------------------------------------------

class CartLineSerializer(serializers.Serializer):
    menu_item = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_QUANTITY)
    options = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, max_length=20)
    note = serializers.CharField(required=False, allow_blank=True, max_length=200)


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


class StatusChangeSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[c for c, _ in OrderStatus.CHOICES])
    note = serializers.CharField(required=False, allow_blank=True, max_length=200)
