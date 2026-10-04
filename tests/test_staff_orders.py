import json
from decimal import Decimal

from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from apps.core.permissions import Role
from apps.orders.models import Order

from .test_ordering import make_restaurant, staff


class WaiterOrderTests(TestCase):
    """Guests without a smartphone: the waiter enters the order."""

    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        self.waiter_user = staff(self.r, "waiter", Role.WAITER)
        self.waiter = Client()
        self.waiter.force_login(self.waiter_user)
        self.url = reverse("dashboard:api_order_create", args=["alpha"])

    def create(self, client=None, **payload):
        body = {"table": self.table.pk, "items": [{"menu_item": self.rice.id, "quantity": 2, "options": [self.egg.id]}]}
        body.update(payload)
        return (client or self.waiter).post(self.url, data=json.dumps(body), content_type="application/json")

    def test_waiter_creates_order_for_a_table(self):
        resp = self.create(customer_name="Maria", note="no spicy")
        self.assertEqual(resp.status_code, 201, resp.content)
        order = Order.objects.get(pk=resp.json()["id"])
        self.assertEqual((order.source, order.placed_by, order.table_number), ("staff", self.waiter_user, "12"))
        self.assertEqual(order.total, Decimal("11.00"))  # prices computed server-side
        self.assertEqual(order.customer_name, "Maria")
        self.assertTrue(order.customer_ref.startswith("w"))
        self.assertIsNotNone(order.table_session)
        self.assertEqual(resp.json()["placed_by_name"], "waiter")

    def test_adding_to_an_existing_guest_shares_their_bill(self):
        first = self.create().json()
        second = self.create(guest=first["guest"], items=[{"menu_item": self.tea.id, "quantity": 1}]).json()
        self.assertEqual(first["guest"], second["guest"])
        new_guest = self.create().json()
        self.assertNotEqual(new_guest["guest"], first["guest"])
        page = self.waiter.get(reverse("dashboard:table_detail", args=["alpha", self.table.pk]))
        self.assertEqual([len(g["orders"]) for g in page.context["guests"]], [2, 1])

    def test_waiter_can_add_to_a_phone_guests_bill(self):
        phone = Client()
        phone.get(self.table.active_qr().get_path())
        token = phone.post(reverse("storefront:api_place_order", args=["alpha"]),
                           data=json.dumps({"items": [{"menu_item": self.tea.id, "quantity": 1}]}),
                           content_type="application/json").json()["token"]
        ref = Order.objects.get(public_token=token).customer_ref
        resp = self.create(guest=ref)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(Order.objects.filter(customer_ref=ref).count(), 2)

    def test_counter_takeaway_order(self):
        resp = self.create(table=None)
        self.assertEqual(resp.status_code, 201)
        order = Order.objects.get(pk=resp.json()["id"])
        self.assertIsNone(order.table)
        self.assertEqual(order.table_number, "")

    def test_validation(self):
        self.rice.is_available = False
        self.rice.save()
        resp = self.create()
        self.assertEqual((resp.status_code, resp.json()["unavailable"]), (409, [self.rice.id]))
        self.assertEqual(self.create(items=[{"menu_item": self.tea.id, "quantity": 1}], guest="nobody").status_code, 400)
        other_r, other_rice, *_ = make_restaurant("beta")
        from apps.restaurants.models import Table
        self.assertEqual(self.create(table=Table.objects.get(restaurant=other_r).pk).status_code, 400)
        self.assertEqual(self.create(items=[{"menu_item": other_rice.id, "quantity": 1}]).status_code, 409)

    def test_roles_and_isolation(self):
        cook = Client()
        cook.force_login(staff(self.r, "cook", Role.KITCHEN))
        self.assertEqual(self.create(client=cook).status_code, 403)
        self.assertEqual(cook.get(reverse("dashboard:new_order", args=["alpha"])).status_code, 403)
        outsider = Client()
        other_r, *_ = make_restaurant("beta")
        outsider.force_login(staff(other_r, "beta-waiter", Role.WAITER))
        self.assertEqual(self.create(client=outsider).status_code, 404)
        self.assertEqual(Client().post(self.url, {}, content_type="application/json").status_code, 403)

    def test_order_entry_page_renders_with_guests(self):
        first = self.create().json()
        page = self.waiter.get(reverse("dashboard:new_order", args=["alpha"]) + f"?table={self.table.pk}")
        self.assertEqual(page.status_code, 200)
        cfg = page.context["pos_config"]
        self.assertEqual(cfg["table"], self.table.pk)
        self.assertEqual(cfg["guests"][str(self.table.pk)][0]["ref"], first["guest"])
        self.assertEqual(len(cfg["menu"][0]["items"]), 2)

    def test_waiter_orders_do_not_affect_phone_access(self):
        phone = Client()
        phone.get(self.table.active_qr().get_path())
        order = Order.objects.get(pk=self.create().json()["id"])
        self.waiter.post(reverse("dashboard:order_mark_paid", args=["alpha", order.pk]))
        resp = phone.post(reverse("storefront:api_place_order", args=["alpha"]),
                          data=json.dumps({"items": [{"menu_item": self.tea.id, "quantity": 1}]}),
                          content_type="application/json")
        self.assertEqual(resp.status_code, 201)
