from django.urls import path

from . import consumers

websocket_urlpatterns = [
    path("ws/kitchen/<slug:slug>/", consumers.KitchenConsumer.as_asgi()),
    path("ws/orders/<uuid:token>/", consumers.OrderStatusConsumer.as_asgi()),
    path("ws/menu/<slug:slug>/", consumers.MenuConsumer.as_asgi()),
    path("ws/customer/", consumers.CustomerConsumer.as_asgi()),
]
