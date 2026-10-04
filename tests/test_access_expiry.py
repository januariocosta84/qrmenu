import json
from decimal import Decimal

from django.core.cache import cache
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from apps.core.permissions import Role
from apps.orders.models import Order
from apps.restaurants.models import Table, TableSession

from .test_ordering import make_restaurant, staff


class QRAccessExpiresAfterPaymentTests(TestCase):
    """After paying, a phone can't keep ordering from outside the restaurant; scanning again restores it."""

    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        self.waiter = Client()
        self.waiter.force_login(staff(self.r, "waiter", Role.WAITER))

    def scan(self, phone):
        phone.get(self.table.active_qr().get_path())

    def order(self, phone):
        return phone.post(
            reverse("storefront:api_place_order", args=["alpha"]),
            data=json.dumps({"items": [{"menu_item": self.tea.id, "quantity": 1}]}),
            content_type="application/json",
        )

    def pay(self, token):
        order = Order.objects.get(public_token=token)
        self.waiter.post(reverse("dashboard:order_mark_paid", args=["alpha", order.pk]), {"tendered": "5"})

    def test_paid_customer_must_scan_again(self):
        phone = Client()
        self.scan(phone)
        token = self.order(phone).json()["token"]
        self.pay(token)

        resp = self.order(phone)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["reason"], "paid")
        page = phone.get(reverse("storefront:table", args=["alpha", "12"]))
        self.assertFalse(page.context["can_order"])
        self.assertContains(page, "Your bill is paid")

        # Back at the table: scanning the QR code again allows a new order.
        self.scan(phone)
        self.assertEqual(self.order(phone).status_code, 201)

    def test_another_customer_paying_does_not_affect_me(self):
        payer, stranger = Client(), Client()
        self.scan(payer)
        self.scan(stranger)  # a different customer sharing the table, hasn't ordered yet
        token = self.order(payer).json()["token"]
        bill = Order.objects.get(public_token=token).table_session
        self.pay(token)

        bill.refresh_from_db()
        self.assertEqual(bill.status, TableSession.CLOSED)  # everything on the bill was paid
        self.assertEqual(self.order(payer).status_code, 403)  # the payer is done
        self.assertEqual(self.order(stranger).status_code, 201)  # the stranger can still order

    def test_each_customer_gets_their_own_bill_on_the_table_page(self):
        a, b = Client(), Client()
        self.scan(a)
        self.scan(b)
        a1 = Order.objects.get(public_token=self.order(a).json()["token"])
        self.order(a)
        b1 = Order.objects.get(public_token=self.order(b).json()["token"])
        self.assertTrue(a1.customer_ref and b1.customer_ref and a1.customer_ref != b1.customer_ref)

        page = self.waiter.get(reverse("dashboard:table_detail", args=["alpha", self.table.pk]))
        guests = page.context["guests"]
        self.assertEqual([len(g["orders"]) for g in guests], [2, 1])
        self.assertEqual(guests[0]["unpaid"], Decimal("3.00"))

        # Guest 1 pays their own bill with $5: change $2, guest 2 is untouched.
        url = reverse("dashboard:guest_mark_paid", args=["alpha", self.table.pk, a1.customer_ref])
        resp = self.waiter.post(url, {"tendered": "5"}, follow=True)
        self.assertContains(resp, "GIVE CHANGE: $2.00")
        self.assertEqual(Order.objects.filter(customer_ref=a1.customer_ref, payment_status="paid").count(), 2)
        b1.refresh_from_db()
        self.assertEqual(b1.payment_status, "unpaid")
        self.assertEqual(self.order(a).status_code, 403)
        self.assertEqual(self.order(b).status_code, 201)

    def test_guest_pay_cannot_touch_other_tables(self):
        phone = Client()
        self.scan(phone)
        ref = Order.objects.get(public_token=self.order(phone).json()["token"]).customer_ref
        other = Table.objects.create(restaurant=self.r, number="99")
        other.current_session(create=True)
        self.waiter.post(reverse("dashboard:guest_mark_paid", args=["alpha", other.pk, ref]))
        self.assertFalse(Order.objects.filter(payment_status="paid").exists())

    def test_partial_table_payment_only_blocks_the_phone_that_paid(self):
        a, b = Client(), Client()
        self.scan(a)
        self.scan(b)
        token_a = self.order(a).json()["token"]
        self.order(b)  # B's order stays unpaid
        self.pay(token_a)

        self.assertEqual(self.order(a).status_code, 403)  # A paid and may leave
        self.assertEqual(self.order(b).status_code, 201)  # B is still eating
        self.assertEqual(TableSession.objects.get(status="open").table, self.table)

    def test_staff_freeing_the_table_ends_access(self):
        phone = Client()
        self.scan(phone)
        self.order(phone)
        friend = Client()
        self.scan(friend)
        self.waiter.post(reverse("dashboard:table_session_close", args=["alpha", self.table.pk]))
        self.assertEqual(self.order(phone).status_code, 403)
        self.assertEqual(self.order(friend).status_code, 403)
        self.scan(friend)  # new guests at the freed table scan again
        self.assertEqual(self.order(friend).status_code, 201)

    def test_unpaid_customer_keeps_ordering(self):
        phone = Client()
        self.scan(phone)
        for _ in range(3):
            self.assertEqual(self.order(phone).status_code, 201)

    def test_cancelled_orders_do_not_count_as_unpaid(self):
        phone = Client()
        self.scan(phone)
        keep = self.order(phone).json()["token"]
        cancel = self.order(phone).json()["token"]
        self.waiter.post(
            reverse("dashboard:api_order_status", args=["alpha", Order.objects.get(public_token=cancel).pk]),
            {"status": "cancelled"}, content_type="application/json",
        )
        self.pay(keep)
        self.assertEqual(self.order(phone).status_code, 403)


class LiveExpiryTests(TestCase):
    """The open menu learns about the payment right away."""

    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        self.waiter = Client()
        self.waiter.force_login(staff(self.r, "waiter", Role.WAITER))
        self.phone = Client()
        self.phone.get(self.table.active_qr().get_path())

    def access(self):
        return self.phone.get(reverse("storefront:api_access", args=["alpha"])).json()

    def test_access_endpoint_and_lasting_paid_notice(self):
        self.assertEqual(self.access(), {"can_order": True, "table": "12", "reason": None})
        token = self.phone.post(
            reverse("storefront:api_place_order", args=["alpha"]),
            data=json.dumps({"items": [{"menu_item": self.tea.id, "quantity": 1}]}), content_type="application/json",
        ).json()["token"]
        order = Order.objects.get(public_token=token)
        self.waiter.post(reverse("dashboard:order_mark_paid", args=["alpha", order.pk]))

        self.assertEqual(self.access(), {"can_order": False, "table": None, "reason": "paid"})
        # The notice keeps showing on later visits (not just the first one).
        for _ in range(2):
            page = self.phone.get(reverse("storefront:table", args=["alpha", "12"]))
            self.assertFalse(page.context["can_order"])
            self.assertContains(page, "Your bill is paid")
        status_page = self.phone.get(reverse("storefront:order_status", args=[token]))
        self.assertContains(status_page, "Your bill is paid")
        self.assertNotContains(status_page, ">Order more</a>")
        # Scanning again clears it.
        self.phone.get(self.table.active_qr().get_path())
        self.assertTrue(self.access()["can_order"])


class CustomerSocketTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()

    async def test_phone_is_told_when_its_order_is_paid(self):
        from channels.db import database_sync_to_async
        from channels.testing import WebsocketCommunicator

        from apps.orders.services import place_order
        from apps.payments.services import receive_cash
        from config.asgi import application

        phone = Client()
        await database_sync_to_async(lambda: phone.get(self.table.active_qr().get_path()))()
        session_key = phone.cookies["sessionid"].value
        ref = await database_sync_to_async(lambda: phone.session["customer_ref"])()

        comm = WebsocketCommunicator(application, "/ws/customer/", headers=[
            (b"origin", b"http://localhost"), (b"host", b"localhost"),
            (b"cookie", f"sessionid={session_key}".encode()),
        ])
        connected, _ = await comm.connect()
        self.assertTrue(connected)

        order = await database_sync_to_async(place_order)(
            restaurant=self.r, table=self.table, lines=[{"menu_item": self.tea.id, "quantity": 1}], customer_ref=ref,
        )
        await comm.receive_json_from(timeout=3)  # "new" status
        await database_sync_to_async(receive_cash)([order])
        msg = await comm.receive_json_from(timeout=3)
        self.assertEqual(msg["data"]["payment_status"], "paid")
        await comm.disconnect()

        # A browser without a customer id can't subscribe to anything.
        anon = WebsocketCommunicator(application, "/ws/customer/", headers=[(b"origin", b"http://localhost"), (b"host", b"localhost")])
        connected, _ = await anon.connect()
        self.assertFalse(connected)
