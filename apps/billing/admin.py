from django.contrib import admin

from .models import BillingSettings, Invoice, Plan, Subscription


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ["name", "price_monthly", "currency", "max_tables", "max_menu_items", "max_staff", "is_active"]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ["restaurant", "plan", "trial_ends_on", "paid_until", "comped", "cancelled_at"]
    list_filter = ["plan", "comped"]
    search_fields = ["restaurant__name"]


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ["number", "restaurant", "amount", "currency", "status", "due_date", "paid_at"]
    list_filter = ["status"]
    search_fields = ["number", "restaurant__name"]


admin.site.register(BillingSettings)
