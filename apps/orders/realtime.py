"""
Real-time fan-out over Django Channels groups.

Groups:
  kitchen.<restaurant_id>  staff dashboards / KDS of one restaurant (authenticated)
  order.<token hex>        one customer's order status page
  menu.<restaurant_id>     customers viewing a menu (live sold-out updates)
"""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def kitchen_group(restaurant_id) -> str:
    return f"kitchen.{restaurant_id}"


def order_group(token) -> str:
    return f"order.{token.hex if hasattr(token, 'hex') else token}"


def menu_group(restaurant_id) -> str:
    return f"menu.{restaurant_id}"


def customer_group(customer_ref: str) -> str:
    return f"customer.{customer_ref}"


def _send(group: str, event: str, data: dict) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(group, {"type": "push", "event": event, "data": data})
    except Exception:  # never let a broadcast failure break an order
        logger.exception("Realtime broadcast to %s failed", group)


def broadcast_order(order, event: str) -> None:
    from .serializers import PublicOrderSerializer, StaffOrderSerializer

    _send(kitchen_group(order.restaurant_id), event, StaffOrderSerializer(order).data)
    public = PublicOrderSerializer(order).data
    _send(order_group(order.public_token), "order_status", public)
    if order.customer_ref:
        _send(customer_group(order.customer_ref), "order_status", public)


def broadcast_availability(item) -> None:
    _send(menu_group(item.restaurant_id), "availability", {"id": item.id, "is_available": item.is_available})
    _send(kitchen_group(item.restaurant_id), "availability", {"id": item.id, "is_available": item.is_available})
