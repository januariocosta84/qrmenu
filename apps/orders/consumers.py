from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from apps.restaurants.models import Restaurant

from . import realtime
from .models import Order


class PushConsumer(AsyncJsonWebsocketConsumer):
    """Server → client only. Subclasses resolve which group to join."""

    group_name: str | None = None

    async def connect(self):
        self.group_name = await self.resolve_group()
        if not self.group_name:
            await self.close(code=4403)
            return
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if self.group_name:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if content.get("type") == "ping":
            await self.send_json({"event": "pong"})

    async def push(self, event):
        await self.send_json({"event": event["event"], "data": event["data"]})

    async def resolve_group(self) -> str | None:
        raise NotImplementedError


class KitchenConsumer(PushConsumer):
    """Staff only, and only for restaurants the user works at."""

    async def resolve_group(self):
        return await self._group(self.scope.get("user"), self.scope["url_route"]["kwargs"]["slug"])

    @database_sync_to_async
    def _group(self, user, slug):
        if not user or not user.is_authenticated:
            return None
        restaurant = Restaurant.objects.filter(slug=slug).first()  # staff may set up before going live
        if restaurant and restaurant.user_can(user, "view_orders"):
            return realtime.kitchen_group(restaurant.id)
        return None


class OrderStatusConsumer(PushConsumer):
    """A customer following their own order; the unguessable token is the credential."""

    async def resolve_group(self):
        token = self.scope["url_route"]["kwargs"]["token"]
        exists = await database_sync_to_async(Order.objects.filter(public_token=token).exists)()
        return realtime.order_group(token) if exists else None


class CustomerConsumer(PushConsumer):
    """A customer's phone: updates about its own orders (e.g. 'paid' → ordering ends)."""

    async def resolve_group(self):
        session = self.scope.get("session")
        if session is None:
            return None
        ref = await database_sync_to_async(session.get)("customer_ref")  # session load hits the DB
        return realtime.customer_group(ref) if ref else None


class MenuConsumer(PushConsumer):
    """Public: pushes sold-out / available changes to customers browsing a menu."""

    async def resolve_group(self):
        slug = self.scope["url_route"]["kwargs"]["slug"]
        rid = await database_sync_to_async(
            lambda: Restaurant.objects.filter(slug=slug, is_active=True).values_list("id", flat=True).first()
        )()
        return realtime.menu_group(rid) if rid else None
