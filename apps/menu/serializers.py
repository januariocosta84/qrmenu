from rest_framework import serializers

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

    class Meta:
        model = MenuItem
        fields = ["id", "name", "description", "price", "image", "is_available", "options"]

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
