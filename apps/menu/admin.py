from django.contrib import admin

from .models import MenuCategory, MenuItem, MenuItemOption


class OptionInline(admin.TabularInline):
    model = MenuItemOption
    extra = 0


@admin.register(MenuCategory)
class MenuCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "restaurant", "position", "is_active"]
    list_filter = ["restaurant"]


@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ["name", "restaurant", "category", "price", "is_available"]
    list_filter = ["restaurant", "is_available"]
    search_fields = ["name"]
    inlines = [OptionInline]
