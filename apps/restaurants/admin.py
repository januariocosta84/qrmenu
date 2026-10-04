from django.contrib import admin
from django.db.models import Count

from apps.core.permissions import Role

from .models import QRCode, Restaurant, RestaurantStaff, Table, TableSession


class StaffInline(admin.TabularInline):
    model = RestaurantStaff
    extra = 1
    autocomplete_fields = ["user"]


@admin.register(Restaurant)
class RestaurantAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "owner_email", "owner_verified", "is_active", "order_count", "created_at"]
    list_filter = ["is_active", "is_accepting_orders", "created_at"]
    search_fields = ["name", "slug", "email", "staff__user__email"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["next_order_number", "created_at"]
    inlines = [StaffInline]
    actions = ["approve", "suspend"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_orders=Count("orders", distinct=True))

    def _owner(self, obj):
        m = obj.staff.filter(role=Role.OWNER).select_related("user").first()
        return m.user if m else None

    @admin.display(description="Owner")
    def owner_email(self, obj):
        owner = self._owner(obj)
        return owner.email if owner else "—"

    @admin.display(description="Email confirmed", boolean=True)
    def owner_verified(self, obj):
        owner = self._owner(obj)
        return bool(owner and owner.email_verified)

    @admin.display(description="Orders", ordering="_orders")
    def order_count(self, obj):
        return obj._orders

    @admin.action(description="Approve / publish selected restaurants")
    def approve(self, request, queryset):
        n = queryset.update(is_active=True)
        self.message_user(request, f"{n} restaurant(s) are now live.")

    @admin.action(description="Suspend selected restaurants (hide from the public)")
    def suspend(self, request, queryset):
        n = queryset.update(is_active=False)
        self.message_user(request, f"{n} restaurant(s) suspended.")


@admin.register(Table)
class TableAdmin(admin.ModelAdmin):
    list_display = ["number", "restaurant", "label", "is_active"]
    list_filter = ["restaurant", "is_active"]


@admin.register(QRCode)
class QRCodeAdmin(admin.ModelAdmin):
    list_display = ["table", "is_active", "created_at", "revoked_at"]
    list_filter = ["is_active", "table__restaurant"]
    readonly_fields = ["token"]


@admin.register(TableSession)
class TableSessionAdmin(admin.ModelAdmin):
    list_display = ["id", "table", "restaurant", "status", "opened_at", "closed_at"]
    list_filter = ["status", "restaurant"]
