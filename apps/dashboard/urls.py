from django.urls import path

from apps.quotations import views as quotes

from . import api, views

app_name = "dashboard"

R = "<slug:slug>/"

urlpatterns = [
    path("dashboard/", views.home, name="home"),
    path(f"dashboard/{R}", views.overview, name="overview"),
    path(f"dashboard/{R}kitchen/", views.kitchen, name="kitchen"),
    path(f"dashboard/{R}orders/", views.orders, name="orders"),
    path(f"dashboard/{R}orders/new/", views.new_order, name="new_order"),
    path(f"dashboard/{R}receipt/", views.receipt, name="receipt"),
    path(f"dashboard/{R}billing/", views.billing_page, name="billing"),
    path(f"dashboard/{R}billing/invoices/<int:pk>/", views.billing_invoice, name="billing_invoice"),
    path(f"dashboard/{R}receipt/print/", views.receipt_print, name="receipt_print"),
    path(f"dashboard/{R}orders/<int:pk>/", views.order_detail, name="order_detail"),
    path(f"dashboard/{R}orders/<int:pk>/paid/", views.order_mark_paid, name="order_mark_paid"),
    path(f"dashboard/{R}tables/", views.tables, name="tables"),
    path(f"dashboard/{R}tables/print/", views.tables_print, name="tables_print"),
    path(f"dashboard/{R}tables/<int:pk>/", views.table_detail, name="table_detail"),
    path(f"dashboard/{R}tables/<int:pk>/edit/", views.table_edit, name="table_edit"),
    path(f"dashboard/{R}tables/<int:pk>/delete/", views.table_delete, name="table_delete"),
    path(f"dashboard/{R}tables/<int:pk>/qr.png", views.table_qr_image, name="table_qr"),
    path(f"dashboard/{R}tables/<int:pk>/regenerate-qr/", views.table_regenerate_qr, name="table_regenerate_qr"),
    path(f"dashboard/{R}tables/<int:pk>/paid/", views.table_mark_paid, name="table_mark_paid"),
    path(f"dashboard/{R}tables/<int:pk>/guest/<str:ref>/paid/", views.guest_mark_paid, name="guest_mark_paid"),
    path(f"dashboard/{R}tables/<int:pk>/close-session/", views.table_session_close, name="table_session_close"),
    path(f"dashboard/{R}menu/", views.menu, name="menu"),
    path(f"dashboard/{R}menu/categories/new/", views.category_form, name="category_create"),
    path(f"dashboard/{R}menu/categories/<int:pk>/", views.category_form, name="category_edit"),
    path(f"dashboard/{R}menu/categories/<int:pk>/delete/", views.category_delete, name="category_delete"),
    path(f"dashboard/{R}menu/items/new/", views.item_form, name="item_create"),
    path(f"dashboard/{R}menu/items/<int:pk>/", views.item_form, name="item_edit"),
    path(f"dashboard/{R}menu/items/<int:pk>/delete/", views.item_delete, name="item_delete"),
    path(f"dashboard/{R}menu/items/<int:pk>/toggle/", views.item_toggle, name="item_toggle"),
    path(f"dashboard/{R}settings/", views.restaurant_settings, name="settings"),
    path(f"dashboard/{R}cash-register/", views.cash_register, name="cash_register"),
    path(f"dashboard/{R}cash-register/<int:pk>/report/", views.cash_report, name="cash_report"),
    path(f"dashboard/{R}cash-drawer/open/", views.drawer_open, name="drawer_open"),
    path(f"dashboard/{R}cash-drawer/test/", views.drawer_test, name="drawer_test"),
    path(f"dashboard/{R}staff/", views.staff, name="staff"),
    path(f"dashboard/{R}staff/<int:pk>/", views.staff_edit, name="staff_edit"),
    path(f"dashboard/{R}reports/", views.reports, name="reports"),
    path(f"dashboard/{R}analytics/", views.analytics_page, name="analytics"),
    path(f"dashboard/{R}quotations/", quotes.quotation_list, name="quotations"),
    path(f"dashboard/{R}quotations/new/", quotes.quotation_create, name="quotation_create"),
    path(f"dashboard/{R}quotations/<int:pk>/", quotes.quotation_edit, name="quotation_edit"),
    path(f"dashboard/{R}quotations/<int:pk>/print/", quotes.quotation_print, name="quotation_print"),
    path(f"dashboard/{R}quotations/<int:pk>/duplicate/", quotes.quotation_duplicate, name="quotation_duplicate"),
    path(f"dashboard/{R}quotations/<int:pk>/delete/", quotes.quotation_delete, name="quotation_delete"),
    path(f"dashboard/{R}notifications/", views.notifications, name="notifications"),
    # Staff REST API
    path("api/v1/auth/token/", api.ThrottledObtainAuthToken.as_view(), name="api_token"),
    path(f"api/v1/r/{R}orders/", api.OrderListAPI.as_view(), name="api_orders"),
    path(f"api/v1/r/{R}orders/new/", api.StaffCreateOrderAPI.as_view(), name="api_order_create"),
    path(f"api/v1/r/{R}receipts/print/", api.ReceiptPrintAPI.as_view(), name="api_receipt_print"),
    path(f"api/v1/r/{R}orders/<int:pk>/", api.OrderDetailAPI.as_view(), name="api_order"),
    path(f"api/v1/r/{R}orders/<int:pk>/status/", api.OrderStatusAPI.as_view(), name="api_order_status"),
    path(f"api/v1/r/{R}orders/<int:pk>/payments/", api.RecordPaymentAPI.as_view(), name="api_order_payment"),
    path(f"api/v1/r/{R}cash-drawer/open/", api.CashDrawerAPI.as_view(), name="api_drawer_open"),
    path(f"api/v1/r/{R}menu-items/", api.MenuItemListAPI.as_view(), name="api_menu_items"),
    path(
        f"api/v1/r/{R}menu-items/<int:pk>/availability/",
        api.ItemAvailabilityAPI.as_view(),
        name="api_item_availability",
    ),
    path(f"api/v1/r/{R}notifications/", api.NotificationsAPI.as_view(), name="api_notifications"),
]
