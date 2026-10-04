from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.permissions import Role
from apps.orders.services import place_order
from apps.restaurants.models import Restaurant

from .test_cash_drawer import FakePrinter, free_port
from .test_ordering import make_restaurant, staff


class ReceiptBase(TestCase):
    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        self.r.address = "Avenida de Portugal, Dili"
        self.r.save()
        self.waiter = staff(self.r, "waiter", Role.WAITER)
        self.client.force_login(self.waiter)
        self.order = place_order(
            restaurant=self.r, table=self.table, customer_name="Ana",
            lines=[{"menu_item": self.rice.id, "quantity": 2, "options": [self.egg.id], "note": "No spicy"}],
        )

    def pay(self, tendered="20", follow=True):
        return self.client.post(
            reverse("dashboard:order_mark_paid", args=["alpha", self.order.pk]), {"tendered": tendered}, follow=follow
        )

    def markers(self, resp):
        return [str(m) for m in resp.context["messages"] if "receipt" in m.extra_tags]


class ReceiptPromptTests(ReceiptBase):
    def test_ask_mode_marks_page_to_ask_print_receipt(self):
        resp = self.pay()
        self.assertEqual(self.markers(resp), [str(self.order.pk)])
        self.assertContains(resp, f'data-receipt-orders="{self.order.pk}"')
        self.assertContains(resp, 'receiptMode: "ask"')

    def test_never_mode_does_not_ask(self):
        self.r.receipt_prompt = Restaurant.RECEIPT_NEVER
        self.r.save()
        self.assertEqual(self.markers(self.pay()), [])

    def test_whole_table_and_guest_payments_ask_once_for_all_orders(self):
        second = place_order(restaurant=self.r, table=self.table, lines=[{"menu_item": self.tea.id, "quantity": 1}])
        resp = self.client.post(reverse("dashboard:table_mark_paid", args=["alpha", self.table.pk]), follow=True)
        ids = self.markers(resp)[0].split(",")
        self.assertEqual(sorted(ids), sorted([str(self.order.pk), str(second.pk)]))

    def test_other_method_payment_asks_only_when_fully_paid(self):
        url = reverse("dashboard:order_detail", args=["alpha", self.order.pk])
        resp = self.client.post(url, {"action": "payment", "method": "manual", "amount": "1.00"}, follow=True)
        self.assertEqual(self.markers(resp), [])  # partial payment
        resp = self.client.post(url, {"action": "payment", "method": "manual", "amount": "10.00"}, follow=True)
        self.assertEqual(self.markers(resp), [str(self.order.pk)])

    def test_kitchen_api_returns_receipt_instructions(self):
        api = reverse("dashboard:api_order_payment", args=["alpha", self.order.pk])
        data = self.client.post(api, {"tendered": "20"}, content_type="application/json").json()
        self.assertEqual(data["receipt"], {"mode": "ask", "orders": [self.order.pk], "printed": None})


class ReceiptPageTests(ReceiptBase):
    def test_receipt_page_after_cash_payment(self):
        self.pay("20")
        resp = self.client.get(reverse("dashboard:receipt", args=["alpha"]) + f"?orders={self.order.pk}")
        self.assertEqual(resp.status_code, 200)
        for text in ("Alpha", "Avenida de Portugal", "RECEIPT", f"#{self.order.number}", "Table", "12", "Ana",
                     "2 × Chicken Fried Rice", "+ Fried Egg", "No spicy", "$11.00", "Cash received", "$20.00",
                     "Change", "$9.00", "Served by", "Thank you"):
            self.assertContains(resp, text)

    def test_unpaid_prints_as_bill(self):
        resp = self.client.get(reverse("dashboard:receipt", args=["alpha"]) + f"?orders={self.order.pk}")
        self.assertContains(resp, "BILL")
        self.assertContains(resp, "NOT PAID")

    def test_receipts_are_restaurant_scoped(self):
        other, *_ = make_restaurant("beta")
        foreign = staff(other, "beta-waiter", Role.WAITER)
        self.client.force_login(foreign)
        resp = self.client.get(reverse("dashboard:receipt", args=["beta"]) + f"?orders={self.order.pk}")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self.client.get(reverse("dashboard:receipt", args=["alpha"]) + "?orders=1").status_code, 404)

    def test_garbage_ids_are_ignored(self):
        url = reverse("dashboard:receipt", args=["alpha"])
        self.assertEqual(self.client.get(url + "?orders=abc,,-1").status_code, 404)
        self.assertEqual(self.client.get(url + f"?orders=x,{self.order.pk},{self.order.pk}").status_code, 200)


@override_settings(CASH_DRAWER_NETWORK_ENABLED=True, CASH_DRAWER_ALLOW_PUBLIC_HOSTS=True)
class NetworkReceiptTests(ReceiptBase):
    def setUp(self):
        super().setUp()
        self.printer = FakePrinter()
        self.r.printer_host, self.r.printer_port = "127.0.0.1", self.printer.port
        self.r.receipt_printer = Restaurant.RECEIPT_NETWORK
        self.r.save()

    def tearDown(self):
        self.printer.close()

    def test_yes_print_sends_escpos_receipt(self):
        self.pay("20")
        resp = self.client.post(
            reverse("dashboard:api_receipt_print", args=["alpha"]), {"orders": [self.order.pk]},
            content_type="application/json",
        )
        self.assertEqual(resp.json()["printed"], True)
        data = self.printer.wait_for(1)[0]
        self.assertTrue(data.startswith(b"\x1b@"))  # ESC/POS init
        self.assertTrue(data.endswith(b"\x1dVB\x00"))  # paper cut
        for text in (b"RECEIPT", b"2 x Chicken Fried Rice", b"+ Fried Egg", b"$11.00", b"Cash received", b"$9.00"):
            self.assertIn(text, data)
        self.assertTrue(all(len(line) <= 48 for line in data.split(b"\n") if b"\x1b" not in line and b"\x1d" not in line))

    def test_always_mode_prints_immediately_without_asking(self):
        self.r.receipt_prompt = Restaurant.RECEIPT_ALWAYS
        self.r.save()
        resp = self.pay()
        self.assertEqual(self.markers(resp), [])
        self.assertContains(resp, "Receipt printed")
        self.assertIn(b"RECEIPT", self.printer.wait_for(1)[0])

    def test_58mm_paper_width(self):
        self.r.receipt_width = 32
        self.r.save()
        self.client.post(reverse("dashboard:api_receipt_print", args=["alpha"]), {"orders": [self.order.pk]},
                         content_type="application/json")
        data = self.printer.wait_for(1)[0]
        self.assertTrue(all(len(line) <= 32 for line in data.split(b"\n") if b"\x1b" not in line and b"\x1d" not in line))

    def test_printer_offline_reports_error_but_payment_is_saved(self):
        self.r.printer_port = free_port()
        self.r.receipt_prompt = Restaurant.RECEIPT_ALWAYS
        self.r.save()
        resp = self.pay()
        self.assertContains(resp, "Receipt not printed")
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")

    def test_kitchen_role_cannot_print_other_restaurants_orders(self):
        other, *_ = make_restaurant("beta")
        self.client.force_login(staff(other, "beta-owner", Role.OWNER))
        resp = self.client.post(reverse("dashboard:api_receipt_print", args=["beta"]), {"orders": [self.order.pk]},
                                content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.printer.received, [])


class ReceiptSettingsTests(ReceiptBase):
    @override_settings(CASH_DRAWER_NETWORK_ENABLED=False)
    def test_network_printer_option_hidden_on_public_deployments(self):
        owner = staff(self.r, "owner", Role.OWNER)
        self.client.force_login(owner)
        page = self.client.get(reverse("dashboard:settings", args=["alpha"]))
        self.assertContains(page, 'name="receipt_prompt"')
        self.assertContains(page, 'name="receipt_footer"')
        self.assertNotContains(page, 'name="receipt_printer"')
