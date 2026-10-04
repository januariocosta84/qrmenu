from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.core.permissions import Role
from apps.orders.services import place_order

from .test_ordering import make_restaurant, staff


class CashChangeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        self.waiter = staff(self.r, "waiter", Role.WAITER)
        self.client.force_login(self.waiter)
        # Spec example: 2 × Chicken Fried Rice + Fried Egg, 2 × Iced Tea = $14.00
        self.order = place_order(restaurant=self.r, table=self.table, lines=[
            {"menu_item": self.rice.id, "quantity": 2, "options": [self.egg.id]},
            {"menu_item": self.tea.id, "quantity": 2},
        ])

    def pay(self, tendered, order=None):
        url = reverse("dashboard:order_mark_paid", args=["alpha", (order or self.order).pk])
        return self.client.post(url, {"tendered": tendered}, follow=True)

    def test_change_is_calculated_and_recorded(self):
        resp = self.pay("20")
        self.assertContains(resp, "GIVE CHANGE: $6.00")
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")
        p = self.order.payments.get()
        self.assertEqual((p.amount, p.cash_tendered, p.change_given), (Decimal("14.00"), Decimal("20.00"), Decimal("6.00")))

    def test_exact_amount_and_blank_mean_no_change(self):
        resp = self.pay("")
        self.assertContains(resp, "No change")
        p = self.order.payments.get()
        self.assertEqual((p.cash_tendered, p.change_given), (Decimal("14.00"), Decimal("0")))

    def test_not_enough_cash_is_refused(self):
        resp = self.pay("10")
        self.assertContains(resp, "Not enough cash")
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "unpaid")
        self.assertFalse(self.order.payments.exists())

    def test_garbage_amount_is_refused(self):
        for bad in ("abc", "-5", "0"):
            self.assertContains(self.pay(bad), "cash amount" if bad != "abc" else "as a number")
        self.assertFalse(self.order.payments.exists())

    def test_comma_decimal_is_accepted(self):
        self.assertContains(self.pay("14,50"), "GIVE CHANGE: $0.50")

    def test_change_on_remaining_balance_after_partial_payment(self):
        api = reverse("dashboard:api_order_payment", args=["alpha", self.order.pk])
        self.client.post(api, {"method": "cash", "amount": "4.00"}, content_type="application/json")
        self.assertContains(self.pay("20"), "GIVE CHANGE: $10.00")  # 10.00 still due

    def test_api_returns_change(self):
        api = reverse("dashboard:api_order_payment", args=["alpha", self.order.pk])
        data = self.client.post(api, {"tendered": "50"}, content_type="application/json").json()
        self.assertEqual((data["change"], data["tendered"]), ("36.00", "50.00"))
        self.assertEqual(data["order"]["payment_status"], "paid")
        short = place_order(restaurant=self.r, table=self.table, lines=[{"menu_item": self.tea.id, "quantity": 1}])
        api = reverse("dashboard:api_order_payment", args=["alpha", short.pk])
        resp = self.client.post(api, {"tendered": "1.00"}, content_type="application/json")
        self.assertEqual((resp.status_code, resp.json()["error"]), (409, "insufficient_cash"))

    def test_whole_table_change(self):
        second = place_order(restaurant=self.r, table=self.table, lines=[{"menu_item": self.tea.id, "quantity": 2}])
        url = reverse("dashboard:table_mark_paid", args=["alpha", self.table.pk])
        resp = self.client.post(url, {"tendered": "20", "close_session": "1"}, follow=True)
        self.assertContains(resp, "GIVE CHANGE: $3.00")  # 14.00 + 3.00 = 17.00 due
        for o in (self.order, second):
            o.refresh_from_db()
            self.assertEqual(o.payment_status, "paid")
        payments = list(self.order.payments.all()) + list(second.payments.all())
        self.assertEqual(sum(p.cash_tendered for p in payments), Decimal("20.00"))
        self.assertEqual(sum(p.change_given for p in payments), Decimal("3.00"))
        self.assertIsNone(self.table.current_session())

    def test_whole_table_short_keeps_table_open(self):
        url = reverse("dashboard:table_mark_paid", args=["alpha", self.table.pk])
        resp = self.client.post(url, {"tendered": "5", "close_session": "1"}, follow=True)
        self.assertContains(resp, "Not enough cash")
        self.assertIsNotNone(self.table.current_session())
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "unpaid")
