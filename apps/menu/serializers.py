from rest_framework import serializers

from . import service_types
from .models import MenuCategory, MenuItem, MenuItemOption


class _Translated(serializers.ModelSerializer):
    """Renders translatable fields in the language given in context['lang']."""

    def to_representation(self, instance):
        data = super().to_representation(instance)
        lang = self.context.get("lang")
        for field in instance.TRANSLATABLE_FIELDS:
            if field in data:
                data[field] = instance.tr(field, lang)
        return data


class PublicOptionSerializer(_Translated):
    class Meta:
        model = MenuItemOption
        fields = ["id", "name", "price", "is_available"]


class PublicItemSerializer(_Translated):
    options = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()
    service_type = serializers.SerializerMethodField()
    vehicle_prices = serializers.SerializerMethodField()

    class Meta:
        model = MenuItem
        fields = ["id", "name", "description", "price", "image", "is_available", "options", "service_type",
                  "vehicle_prices"]

    def get_service_type(self, obj):
        return obj.category.service_type

    def get_vehicle_prices(self, obj):
        """Car wash packages: price per vehicle type (car = the normal price); empty for café items."""
        if not service_types.get(obj.category.service_type).needs_vehicle:
            return {}
        return {code: f"{obj.price_for(code):.2f}" for code in service_types.VEHICLE_CODES}

    def get_options(self, obj):
        return PublicOptionSerializer(obj.options.all(), many=True, context=self.context).data

    def get_image(self, obj):
        return obj.image.url if obj.image else None


class PublicCategorySerializer(_Translated):
    items = serializers.SerializerMethodField()

    class Meta:
        model = MenuCategory
        fields = ["id", "name", "items"]

    def get_items(self, obj):
        return PublicItemSerializer(obj.items.all(), many=True, context=self.context).data


class StaffItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = MenuItem
        fields = ["id", "name", "category", "category_name", "price", "is_available", "prep_minutes"]
        read_only_fields = ["id", "name", "category", "category_name", "price", "prep_minutes"]
