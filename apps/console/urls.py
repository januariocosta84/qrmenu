from django.urls import path

from . import views

app_name = "console"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("verification/", views.verification, name="verification"),
    path("restaurants/", views.restaurants, name="restaurants"),
    path("restaurants/<int:pk>/", views.restaurant_detail, name="restaurant"),
    path("users/", views.users, name="users"),
    path("users/<int:pk>/", views.user_detail, name="user"),
    path("plans/", views.plans, name="plans"),
    path("plans/new/", views.plan_form, name="plan_create"),
    path("plans/<int:pk>/", views.plan_form, name="plan_edit"),
    path("invoices/", views.invoices, name="invoices"),
    path("invoices/generate/", views.invoices_generate, name="invoices_generate"),
    path("invoices/<int:pk>/", views.invoice_detail, name="invoice"),
    path("settings/", views.billing_settings, name="settings"),
    path("audit/", views.audit_log, name="audit"),
]
