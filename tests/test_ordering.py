import io
import json
import shutil
import tempfile
from decimal import Decimal

from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.core.permissions import Role
from apps.menu.models import MenuCategory, MenuItem, MenuItemOption
from apps.orders.models import Notification, Order, OrderStatus
from apps.orders.services import OrderError, change_status, place_order
from apps.restaurants.models import Restaurant, RestaurantStaff, Table, TableSession

User = get_user_model()


def make_restaurant(slug="alpha", service="0"):
    r = Restaurant.objects.create(name=slug.title(), slug=slug, service_charge_percent=Decimal(service))
    cat = MenuCategory.objects.create(restaurant=r, name="Rice", translations={"tet": {"name": "Etu"}})
    rice = MenuItem.objects.create(
        restaurant=r, category=cat, name="Chicken Fried Rice", price=Decimal("5.00"), prep_minutes=12,
        translations={"tet": {"name": "Etu Sona ho Manu"}},
    )
    egg = MenuItemOption.objects.create(menu_item=rice, name="Fried Egg", price=Decimal("0.50"))
    tea = MenuItem.objects.create(restaurant=r, category=cat, name="Iced Tea", price=Decimal("1.50"))
    table = Table.objects.create(restaurant=r, number="12")
    return r, rice, egg, tea, table


def make_pro(restaurant):
    """Put the restaurant on the Pro plan (analytics, cash register, quotations)."""
    from apps.billing.models import Plan
    from apps.billing.services import get_subscription

    sub = get_subscription(restaurant)
    sub.plan = Plan.objects.get(name="Pro")
    sub.save()
    return sub


def staff(restaurant, username, role):
    user = User.objects.create_user(username=username, password="pw-Secret-123")
    RestaurantStaff.objects.create(restaurant=restaurant, user=user, role=role)
    return user


class Base(TestCase):
    def setUp(self):
        cache.clear()  # reset rate-limit counters between tests
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        self.client = Client()

    def scan(self, client=None, table=None):
        client = client or self.client
        table = table or self.table
        return client.get(table.active_qr().get_path())

    def order(self, payload=None, client=None):
        payload = payload or {
            "items": [
                {"menu_item": self.rice.id, "quantity": 2, "options": [self.egg.id], "note": "No spicy"},
                {"menu_item": self.tea.id, "quantity": 2},
            ],
            "customer_name": "Ana",
        }
        return (client or self.client).post(
            reverse("storefront:api_place_order", args=[self.r.slug]),
            data=json.dumps(payload), content_type="application/json",
        )


class QRAccessTests(Base):
    def test_scan_grants_access_and_strips_token(self):
        resp = self.scan()
        self.assertRedirects(resp, reverse("storefront:table", args=["alpha", "12"]), fetch_redirect_response=False)
        page = self.client.get(resp["Location"])
        self.assertContains(page, "Chicken Fried Rice")
        self.assertTrue(page.context["can_order"])
        self.assertEqual(TableSession.objects.filter(table=self.table, status="open").count(), 1)

    def test_plain_url_is_view_only(self):
        page = self.client.get(reverse("storefront:table", args=["alpha", "12"]))
        self.assertEqual(page.status_code, 200)
        self.assertFalse(page.context["can_order"])
        self.assertEqual(self.order().status_code, 403)

    def test_long_url_alias_keeps_token(self):
        qr = self.table.active_qr()
        resp = self.client.get(f"/restaurant/alpha/table/12/?k={qr.token}")
        self.assertEqual(resp.status_code, 302)
        self.assertIn(f"k={qr.token}", resp["Location"])

    def test_regenerated_qr_invalidates_old_code(self):
        old = self.table.active_qr().get_path()
        self.table.regenerate_qr()
        self.client.get(old)
        self.assertEqual(self.order().status_code, 403)
        self.scan()
        self.assertEqual(self.order().status_code, 201)

    def test_language_switch(self):
        self.scan()
        page = self.client.get(reverse("storefront:table", args=["alpha", "12"]) + "?lang=tet")
        self.assertContains(page, "Etu Sona ho Manu")
        self.assertContains(page, "Haruka Pedidu")


class PlaceOrderTests(Base):
    def test_order_totals_are_computed_server_side(self):
        self.scan()
        resp = self.order({
            "items": [{"menu_item": self.rice.id, "quantity": 2, "options": [self.egg.id], "price": "0.01"}],
        })
        self.assertEqual(resp.status_code, 201, resp.content)
        order = Order.objects.get(public_token=resp.json()["token"])
        self.assertEqual(order.total, Decimal("11.00"))  # (5.00 + 0.50) × 2
        self.assertEqual(order.number, 1001)
        self.assertEqual(order.table_number, "12")
        self.assertEqual(order.estimated_minutes, 12)
        self.assertEqual(order.items.get().options.get().name, "Fried Egg")
        self.assertTrue(Notification.objects.filter(order=order, kind="new_order").exists())
        self.assertEqual(order.status_history.get().to_status, "new")

    def test_spec_example_total(self):
        self.scan()
        order = Order.objects.get(public_token=self.order().json()["token"])
        self.assertEqual(order.total, Decimal("14.00"))  # 10.00 + 3.00 + 1.00 egg add-ons

    def test_service_charge(self):
        self.r.service_charge_percent = Decimal("10")
        self.r.save()
        self.scan()
        order = Order.objects.get(public_token=self.order().json()["token"])
        self.assertEqual(order.subtotal, Decimal("14.00"))
        self.assertEqual(order.service_charge, Decimal("1.40"))
        self.assertEqual(order.total, Decimal("15.40"))

    def test_multiple_orders_same_table_share_session(self):
        phone_a, phone_b = Client(), Client()
        self.scan(phone_a)
        self.scan(phone_b)
        a = Order.objects.get(public_token=self.order(client=phone_a).json()["token"])
        b = Order.objects.get(public_token=self.order(client=phone_b).json()["token"])
        self.assertEqual((a.number, b.number), (1001, 1002))
        self.assertEqual(a.table_session_id, b.table_session_id)

    def test_sold_out_item_rejected(self):
        self.scan()
        self.tea.is_available = False
        self.tea.save()
        resp = self.order()
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["unavailable"], [self.tea.id])
        self.assertFalse(Order.objects.exists())

    def test_option_from_other_item_rejected(self):
        self.scan()
        resp = self.order({"items": [{"menu_item": self.tea.id, "quantity": 1, "options": [self.egg.id]}]})
        self.assertEqual(resp.status_code, 409)

    def test_item_from_other_restaurant_rejected(self):
        _, other_rice, *_ = make_restaurant("beta")
        self.scan()
        resp = self.order({"items": [{"menu_item": other_rice.id, "quantity": 1}]})
        self.assertEqual(resp.status_code, 409)

    def test_invalid_quantity_rejected(self):
        self.scan()
        self.assertEqual(self.order({"items": [{"menu_item": self.rice.id, "quantity": 0}]}).status_code, 400)
        self.assertEqual(self.order({"items": [{"menu_item": self.rice.id, "quantity": 51}]}).status_code, 400)
        self.assertEqual(self.order({"items": []}).status_code, 400)

    def test_not_accepting_orders(self):
        self.scan()
        self.r.is_accepting_orders = False
        self.r.save()
        self.assertEqual(self.order().status_code, 409)

    def test_csrf_enforced_for_anonymous_customers(self):
        client = Client(enforce_csrf_checks=True)
        self.scan(client)
        self.assertEqual(self.order(client=client).status_code, 403)
        page = client.get(reverse("storefront:table", args=["alpha", "12"]))
        token = page.cookies["csrftoken"].value
        resp = client.post(
            reverse("storefront:api_place_order", args=["alpha"]),
            data=json.dumps({"items": [{"menu_item": self.tea.id, "quantity": 1}]}),
            content_type="application/json", HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(resp.status_code, 201)

    def test_order_rate_limit(self):
        self.scan()
        line = {"items": [{"menu_item": self.tea.id, "quantity": 1}]}
        codes = [self.order(line).status_code for _ in range(11)]
        self.assertEqual(codes[:10], [201] * 10)
        self.assertEqual(codes[10], 429)

    def test_status_page_and_public_api(self):
        self.scan()
        data = self.order({"items": [{"menu_item": self.tea.id, "quantity": 1}], "customer_phone": "+670 123"}).json()
        page = self.client.get(data["status_url"])
        self.assertContains(page, f"#{data['number']}")
        api = self.client.get(reverse("storefront:api_order", args=[data["token"]])).json()
        self.assertEqual(api["status"], "new")
        self.assertNotIn("customer_phone", api)
        self.assertNotIn("id", api)


class WorkflowTests(Base):
    def _order(self):
        return place_order(restaurant=self.r, table=self.table, lines=[{"menu_item": self.tea.id, "quantity": 1}])

    def test_happy_path(self):
        o = self._order()
        for s in [OrderStatus.PREPARING, OrderStatus.READY, OrderStatus.COMPLETED]:
            o = change_status(o, s)
        self.assertIsNotNone(o.accepted_at)
        self.assertIsNotNone(o.completed_at)
        self.assertEqual(o.status_history.count(), 4)

    def test_invalid_transition(self):
        o = self._order()
        with self.assertRaises(OrderError):
            change_status(o, OrderStatus.COMPLETED)
        o = change_status(o, OrderStatus.CANCELLED, note="Customer left")
        with self.assertRaises(OrderError):
            change_status(o, OrderStatus.PREPARING)
        self.assertEqual(o.cancel_reason, "Customer left")


class StaffAccessTests(Base):
    def setUp(self):
        super().setUp()
        self.owner = staff(self.r, "owner", Role.OWNER)
        self.cook = staff(self.r, "cook", Role.KITCHEN)
        self.other_r, *_ = make_restaurant("beta")
        self.other_owner = staff(self.other_r, "beta-owner", Role.OWNER)
        self.o = place_order(restaurant=self.r, table=self.table, lines=[{"menu_item": self.tea.id, "quantity": 1}])

    def test_dashboard_requires_login(self):
        resp = self.client.get(reverse("dashboard:overview", args=["alpha"]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])

    def test_restaurant_isolation(self):
        self.client.force_login(self.other_owner)
        self.assertEqual(self.client.get(reverse("dashboard:overview", args=["alpha"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("dashboard:api_orders", args=["alpha"])).status_code, 404)
        # Using own slug with the other restaurant's order id must not work either.
        url = reverse("dashboard:api_order_status", args=["beta", self.o.pk])
        resp = self.client.post(url, {"status": "preparing"}, content_type="application/json")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(
            self.client.get(reverse("dashboard:order_detail", args=["beta", self.o.pk])).status_code, 404
        )
        self.o.refresh_from_db()
        self.assertEqual(self.o.status, "new")

    def test_kitchen_role(self):
        self.client.force_login(self.cook)
        self.assertEqual(self.client.get(reverse("dashboard:kitchen", args=["alpha"])).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashboard:settings", args=["alpha"])).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard:staff", args=["alpha"])).status_code, 403)
        url = reverse("dashboard:api_order_status", args=["alpha", self.o.pk])
        self.assertEqual(self.client.post(url, {"status": "cancelled"}, content_type="application/json").status_code, 403)
        resp = self.client.post(url, {"status": "preparing"}, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "preparing")

    def test_sold_out_toggle_via_api(self):
        self.client.force_login(self.cook)
        url = reverse("dashboard:api_item_availability", args=["alpha", self.rice.pk])
        resp = self.client.post(url, {"is_available": False}, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.rice.refresh_from_db()
        self.assertFalse(self.rice.is_available)

    def test_owner_pages_render(self):
        self.client.force_login(self.owner)
        for name, args in [
            ("overview", []), ("kitchen", []), ("orders", []), ("order_detail", [self.o.pk]),
            ("tables", []), ("table_detail", [self.table.pk]), ("tables_print", []), ("table_qr", [self.table.pk]),
            ("menu", []), ("item_create", []), ("item_edit", [self.rice.pk]), ("category_create", []),
            ("settings", []), ("staff", []), ("reports", []), ("notifications", []),
        ]:
            resp = self.client.get(reverse(f"dashboard:{name}", args=["alpha", *args]))
            self.assertEqual(resp.status_code, 200, name)
        csv_resp = self.client.get(reverse("dashboard:reports", args=["alpha"]) + "?format=csv")
        self.assertIn("text/csv", csv_resp["Content-Type"])

    def test_record_payment_marks_paid(self):
        self.client.force_login(self.owner)
        url = reverse("dashboard:order_detail", args=["alpha", self.o.pk])
        self.client.post(url, {"action": "payment", "method": "cash", "amount": "1.50", "reference": ""})
        self.o.refresh_from_db()
        self.assertEqual(self.o.payment_status, "paid")

    def test_waiter_one_tap_mark_paid(self):
        waiter = staff(self.r, "waiter", Role.WAITER)
        self.client.force_login(waiter)
        url = reverse("dashboard:order_mark_paid", args=["alpha", self.o.pk])
        resp = self.client.post(url, {"next": reverse("dashboard:orders", args=["alpha"])})
        self.assertRedirects(resp, reverse("dashboard:orders", args=["alpha"]), fetch_redirect_response=False)
        self.o.refresh_from_db()
        self.assertEqual(self.o.payment_status, "paid")
        payment = self.o.payments.get()
        self.assertEqual((payment.amount, payment.method, payment.received_by), (self.o.total, "cash", waiter))
        # Paying twice is refused, and no second payment is recorded.
        self.client.post(url)
        self.assertEqual(self.o.payments.count(), 1)

    def test_mark_paid_ignores_offsite_next(self):
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("dashboard:order_mark_paid", args=["alpha", self.o.pk]), {"next": "https://evil.example/"}
        )
        self.assertEqual(resp["Location"], reverse("dashboard:order_detail", args=["alpha", self.o.pk]))

    def test_kitchen_cannot_take_payments(self):
        self.client.force_login(self.cook)
        self.assertEqual(self.client.post(reverse("dashboard:order_mark_paid", args=["alpha", self.o.pk])).status_code, 403)
        api = reverse("dashboard:api_order_payment", args=["alpha", self.o.pk])
        self.assertEqual(self.client.post(api, {}, content_type="application/json").status_code, 403)
        self.o.refresh_from_db()
        self.assertEqual(self.o.payment_status, "unpaid")

    def test_payment_api_full_balance_and_conflicts(self):
        self.client.force_login(self.owner)
        api = reverse("dashboard:api_order_payment", args=["alpha", self.o.pk])
        resp = self.client.post(api, {}, content_type="application/json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["order"]["payment_status"], "paid")
        self.assertEqual(self.client.post(api, {}, content_type="application/json").status_code, 409)
        cancelled = place_order(restaurant=self.r, table=self.table, lines=[{"menu_item": self.tea.id, "quantity": 1}])
        change_status(cancelled, OrderStatus.CANCELLED)
        api = reverse("dashboard:api_order_payment", args=["alpha", cancelled.pk])
        self.assertEqual(self.client.post(api, {}, content_type="application/json").status_code, 409)

    def test_partial_payment_then_balance(self):
        big = place_order(restaurant=self.r, table=self.table, lines=[{"menu_item": self.rice.id, "quantity": 2}])
        self.client.force_login(self.owner)
        api = reverse("dashboard:api_order_payment", args=["alpha", big.pk])
        self.client.post(api, {"amount": "4.00"}, content_type="application/json")
        big.refresh_from_db()
        self.assertEqual(big.payment_status, "unpaid")
        self.client.post(api, {}, content_type="application/json")  # remaining 6.00
        big.refresh_from_db()
        self.assertEqual(big.payment_status, "paid")
        self.assertEqual(sorted(p.amount for p in big.payments.all()), [Decimal("4.00"), Decimal("6.00")])

    def test_whole_table_paid_and_freed(self):
        second = place_order(restaurant=self.r, table=self.table, lines=[{"menu_item": self.rice.id, "quantity": 1}])
        self.client.force_login(self.owner)
        self.client.post(reverse("dashboard:table_mark_paid", args=["alpha", self.table.pk]), {"close_session": "1"})
        for o in (self.o, second):
            o.refresh_from_db()
            self.assertEqual(o.payment_status, "paid")
        self.assertIsNone(self.table.current_session())

    def test_owner_creates_item_with_options_and_translation(self):
        self.client.force_login(self.owner)
        resp = self.client.post(reverse("dashboard:item_create", args=["alpha"]), {
            "category": self.rice.category_id, "name": "Mie Goreng", "description": "", "price": "4.00",
            "is_available": "on", "position": "0", "prep_minutes": "", "name_tet": "Mi Sona", "name_id": "",
            "description_tet": "", "description_id": "",
            "opt-TOTAL_FORMS": "1", "opt-INITIAL_FORMS": "0", "opt-MIN_NUM_FORMS": "0", "opt-MAX_NUM_FORMS": "30",
            "opt-0-name": "Fried Egg", "opt-0-price": "0.50", "opt-0-is_available": "on", "opt-0-position": "0",
        })
        self.assertEqual(resp.status_code, 302, getattr(resp, "context", None) and resp.context["form"].errors)
        item = MenuItem.objects.get(name="Mie Goreng")
        self.assertEqual(item.tr("name", "tet"), "Mi Sona")
        self.assertEqual(item.options.get().price, Decimal("0.50"))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ImageUploadTests(Base):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def test_images_are_reencoded_and_fakes_rejected(self):
        owner = staff(self.r, "owner", Role.OWNER)
        self.client.force_login(owner)
        buf = io.BytesIO()
        Image.new("RGB", (2000, 1500), "orange").save(buf, "PNG")
        base = {
            "category": self.rice.category_id, "name": "Chicken Fried Rice", "description": "", "price": "5.00",
            "is_available": "on", "position": "0", "prep_minutes": "",
            "opt-TOTAL_FORMS": "0", "opt-INITIAL_FORMS": "0", "opt-MIN_NUM_FORMS": "0", "opt-MAX_NUM_FORMS": "30",
        }
        url = reverse("dashboard:item_edit", args=["alpha", self.rice.pk])
        resp = self.client.post(url, {**base, "image": SimpleUploadedFile("x.png", buf.getvalue(), "image/png")})
        self.assertEqual(resp.status_code, 302)
        self.rice.refresh_from_db()
        self.assertTrue(self.rice.image.name.endswith(".webp"))
        with Image.open(self.rice.image.path) as img:
            self.assertLessEqual(max(img.size), 1000)

        fake = SimpleUploadedFile("evil.png", b"<?php echo 1; ?>", "image/png")
        resp = self.client.post(url, {**base, "image": fake})
        self.assertEqual(resp.status_code, 200)  # form re-rendered with an error


class WebSocketTests(TransactionTestCase):
    def setUp(self):
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        self.cook = staff(self.r, "cook", Role.KITCHEN)

    async def _communicator(self, path, user=None):
        from config.asgi import application
        from channels.db import database_sync_to_async
        from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
        from django.contrib.sessions.backends.db import SessionStore

        headers = [(b"origin", b"http://localhost"), (b"host", b"localhost")]
        if user is not None:
            def make_session():
                s = SessionStore()
                s[SESSION_KEY] = str(user.pk)
                s[BACKEND_SESSION_KEY] = "apps.accounts.backends.EmailOrUsernameBackend"
                s[HASH_SESSION_KEY] = user.get_session_auth_hash()
                s.create()
                return s.session_key
            key = await database_sync_to_async(make_session)()
            headers.append((b"cookie", f"sessionid={key}".encode()))
        return WebsocketCommunicator(application, path, headers=headers)

    async def test_kitchen_receives_new_orders_and_customers_get_status(self):
        from channels.db import database_sync_to_async

        anon = await self._communicator("/ws/kitchen/alpha/")
        connected, _ = await anon.connect()
        self.assertFalse(connected)

        kitchen = await self._communicator("/ws/kitchen/alpha/", user=self.cook)
        connected, _ = await kitchen.connect()
        self.assertTrue(connected)

        order = await database_sync_to_async(place_order)(
            restaurant=self.r, table=self.table, lines=[{"menu_item": self.tea.id, "quantity": 2}]
        )
        msg = await kitchen.receive_json_from(timeout=3)
        self.assertEqual(msg["event"], "new_order")
        self.assertEqual(msg["data"]["number"], order.number)
        self.assertEqual(msg["data"]["table_number"], "12")

        customer = await self._communicator(f"/ws/orders/{order.public_token}/")
        connected, _ = await customer.connect()
        self.assertTrue(connected)
        await database_sync_to_async(change_status)(order, OrderStatus.PREPARING)
        update = await customer.receive_json_from(timeout=3)
        self.assertEqual(update["data"]["status"], "preparing")
        self.assertNotIn("customer_phone", update["data"])
        await kitchen.receive_json_from(timeout=3)  # the kitchen also gets the status change

        from apps.payments.services import receive_payment
        await database_sync_to_async(receive_payment)(order)
        paid = await customer.receive_json_from(timeout=3)
        self.assertEqual(paid["data"]["payment_status"], "paid")
        kitchen_paid = await kitchen.receive_json_from(timeout=3)
        self.assertEqual(kitchen_paid["data"]["payment_status"], "paid")

        for c in (kitchen, customer):
            await c.disconnect()
