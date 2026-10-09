from django.urls import re_path

from . import api

app_name = "ordering_api"

urlpatterns = [  # trailing slash optional
    re_path(r"^branches/?$", api.branches, name="branches"),
    re_path(r"^menu/?$", api.menu, name="menu"),
    re_path(r"^orders/?$", api.orders, name="orders"),
    re_path(r"^orders/(?P<token>[0-9a-fA-F-]{36})/?$", api.order_detail, name="order"),
    re_path(r"^.*$", api.not_found),
]
