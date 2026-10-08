from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.core.permissions import Role
from apps.orders.services import place_order
from apps.payments.models import CashSession

from .test_ordering import make_pro, make_restaurant, staff


class CashRegisterTests(TestCase):
    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        make_pro(self.r)
        self.waiter = staff(self.r, "waiter", Role.WAITER)
        self.client.force_login(self.waiter)
        self.url = reverse("dashboard:cash_register", args=["alpha"])

    def tea_order(self):  # 2 × Iced Tea = $3.00
        return place_order(restaurant=self.r, table=self.table, lines=[{"menu_item": self.tea.id, "quantity": 2}])

    def pay_cash(self, order, tendered):
        self.client.post(reverse("dashboard:order_mark_paid", args=["alpha", order.pk]), {"tendered": tendered})

    def open_with_counts(self):
        # 2 × $5 + 4 × $1 + 4 × 25¢ = $15.00
        return self.client.post(self.url, {"action": "open", "count_5": "2", "count_1": "4", "count_0.25": "4"})

    def test_reminder_until_opened(self):
        reminder = "Before selling starts, enter the change in the cash drawer."
        self.assertContains(self.client.get(reverse("dashboard:overview", args=["alpha"])), reminder)
        self.open_with_counts()
        self.assertNotContains(self.client.get(reverse("dashboard:overview", args=["alpha"])), reminder)

    def test_full_day_reconciles(self):
        self.open_with_counts()
        s = CashSession.objects.get()
        self.assertEqual(s.opening_float, Decimal("15.00"))
        self.assertEqual(s.opening_count, {"5": 2, "1": 4, "0.25": 4})

        self.pay_cash(self.tea_order(), "5")   # $3.00 sale, $2.00 change → drawer +3.00
        self.pay_cash(self.tea_order(), "")    # exact $3.00
        card = self.tea_order()                # card payment: not in the drawer
        self.client.post(reverse("dashboard:order_detail", args=["alpha", card.pk]),
                         {"action": "payment", "method": "card", "amount": "3.00", "reference": "T1"})
        self.client.post(self.url, {"action": "out", "amount": "2", "reason": "bought ice"})
        self.client.post(self.url, {"action": "in", "amount": "1", "reason": "more coins"})

        page = self.client.get(self.url)
        f = page.context["figures"]
        self.assertEqual((f["cash_sales"], f["cash_sales_count"]), (Decimal("6.00"), 2))
        self.assertEqual((f["cash_in"], f["cash_out"]), (Decimal("1"), Decimal("2")))
        self.assertEqual(f["expected"], Decimal("20.00"))  # 15 + 6 + 1 − 2
        self.assertEqual(f["other_total"], Decimal("3.00"))
        self.assertEqual(f["takings"], Decimal("9.00"))

        resp = self.client.post(self.url, {"action": "close", "amount": "19.50", "note": "coin missing"})
        s.refresh_from_db()
        self.assertRedirects(resp, reverse("dashboard:cash_report", args=["alpha", s.pk]))
        self.assertFalse(s.is_open)
        self.assertEqual((s.expected_cash, s.counted_cash, s.difference), (Decimal("20.00"), Decimal("19.50"), Decimal("-0.50")))
        report = self.client.get(resp.url)
        self.assertContains(report, "EXPECTED CASH")
        self.assertContains(report, "SHORT")

    def test_closed_report_does_not_change_with_later_sales(self):
        self.client.post(self.url, {"action": "open", "amount": "10"})
        self.pay_cash(self.tea_order(), "")
        self.client.post(self.url, {"action": "close", "amount": "13"})
        s = CashSession.objects.get()
        self.pay_cash(self.tea_order(), "")  # after closing
        self.assertEqual(self.client.get(reverse("dashboard:cash_report", args=["alpha", s.pk])).context["f"]["expected"],
                         Decimal("13.00"))
        self.assertEqual(s.difference, Decimal("0"))

    def test_only_one_open_session_and_validation(self):
        self.client.post(self.url, {"action": "open", "amount": "10"})
        self.client.post(self.url, {"action": "open", "amount": "20"})
        self.assertEqual(CashSession.objects.count(), 1)
        resp = self.client.post(self.url, {"action": "out", "amount": "5", "reason": ""}, follow=True)
        self.assertEqual(resp.context["figures"]["cash_out"], Decimal("0"))
        resp = self.client.post(self.url, {"action": "close", "amount": "abc"}, follow=True)
        self.assertTrue(CashSession.objects.get().is_open)

    def test_kitchen_role_cannot_use_the_register(self):
        self.client.force_login(staff(self.r, "cook", Role.KITCHEN))
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_other_restaurants_sessions_are_hidden(self):
        other, *_ = make_restaurant("beta")
        s = CashSession.objects.create(restaurant=other, opening_float=5)
        self.assertEqual(self.client.get(reverse("dashboard:cash_report", args=["alpha", s.pk])).status_code, 404)
