from django.contrib import admin

from .models import Notification, Order, OrderItem, OrderStatusHistory


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ["menu_item", "name", "unit_price", "quantity", "note", "line_total"]


class HistoryInline(admin.TabularInline):
    model = OrderStatusHistory
    extra = 0
    readonly_fields = ["from_status", "to_status", "changed_by", "note", "created_at"]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["number", "restaurant", "table_number", "status", "payment_status", "total", "created_at"]
    list_filter = ["restaurant", "status", "payment_status"]
    search_fields = ["number", "customer_name"]
    readonly_fields = ["public_token", "placed_ip"]
    inlines = [OrderItemInline, HistoryInline]


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["title", "restaurant", "is_read", "created_at"]
    list_filter = ["restaurant", "is_read"]
