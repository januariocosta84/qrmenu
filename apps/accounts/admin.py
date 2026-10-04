from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    fieldsets = BaseUserAdmin.fieldsets + (("Contact", {"fields": ("phone", "email_verified")}),)
    list_display = ["username", "email", "first_name", "email_verified", "is_staff", "date_joined"]
    list_filter = ["email_verified", "is_staff", "is_superuser", "is_active"]
