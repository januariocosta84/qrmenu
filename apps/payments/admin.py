from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["order", "restaurant", "method", "status", "amount", "paid_at"]
    list_filter = ["restaurant", "method", "status"]
