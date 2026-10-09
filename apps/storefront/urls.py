from django.urls import path

from . import api, views

app_name = "storefront"

urlpatterns = [
    path("", views.home, name="home"),
    path("terms/", views.terms, name="terms"),
    path("r/<slug:slug>/", views.restaurant_menu, name="menu"),
    path("r/<slug:slug>/t/<str:number>/", views.table_menu, name="table"),
    path("restaurant/<slug:slug>/table/<str:number>/", views.legacy_table_menu, name="table_long"),
    path("o/<uuid:token>/", views.order_status, name="order_status"),
    # Public JSON API
    path("api/v1/public/r/<slug:slug>/menu/", api.PublicMenuAPI.as_view(), name="api_menu"),
    path("api/v1/public/r/<slug:slug>/orders/", api.PlaceOrderAPI.as_view(), name="api_place_order"),
    path("api/v1/public/r/<slug:slug>/access/", api.AccessAPI.as_view(), name="api_access"),
    path("api/v1/public/orders/<uuid:token>/", api.PublicOrderAPI.as_view(), name="api_order"),
]
