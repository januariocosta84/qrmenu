from datetime import datetime, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.permissions import Role
from apps.dashboard import analytics
from apps.orders.models import Order, OrderStatus
from apps.orders.services import place_order
from apps.payments.models import Payment

from .test_ordering import make_pro, make_restaurant, staff


class AnalyticsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        make_pro(self.r)
        self.today = timezone.localdate()
        self.tz = timezone.get_current_timezone()

    def order(self, when, status=OrderStatus.COMPLETED, items=None, ready_after=None, method=None):
        o = place_order(restaurant=self.r, table=self.table,
                        lines=items or [{"menu_item": self.tea.id, "quantity": 2}])  # $3.00 by default
        fields = {"created_at": when, "status": status}
        if ready_after is not None:
            fields["ready_at"] = when + timedelta(minutes=ready_after)
        Order.objects.filter(pk=o.pk).update(**fields)
        if method:
            Payment.objects.create(restaurant=self.r, order=o, method=method, status=Payment.SUCCEEDED,
                                   amount=o.total, paid_at=when)
        return o

    def at(self, days_ago, hour):
        day = self.today - timedelta(days=days_ago)
        return timezone.make_aware(datetime.combine(day, datetime.min.time()) + timedelta(hours=hour), self.tz)

    def test_kpis_compare_with_previous_period(self):
        self.order(self.at(0, 12))
        self.order(self.at(1, 19))
        self.order(self.at(2, 19), status=OrderStatus.CANCELLED)
        self.order(self.at(8, 12))  # previous 7-day period
        a = analytics.build(self.r, self.today - timedelta(days=6), self.today)
        k = {x["key"]: x for x in a["kpis"]}
        self.assertEqual(k["revenue"]["value"], Decimal("6.00"))
        self.assertEqual(k["revenue"]["delta"], 100.0)  # $6 vs $3
        self.assertEqual(k["completed"]["value"], 2)
        self.assertEqual(k["avg"]["value"], Decimal("3.00"))
        self.assertEqual(k["cancel_rate"]["value"], 33.3)
        self.assertEqual(len(a["trend"]), 7)
        self.assertEqual(a["trend"][-1]["revenue"], 3.0)
        self.assertEqual(sum(d["prev"] for d in a["trend"]), 3.0)

    def test_busy_times_categories_payments_and_kitchen(self):
        self.order(self.at(0, 19), ready_after=10, method="cash")
        self.order(self.at(7, 19), ready_after=30, method="card")  # same weekday, a week earlier
        self.order(self.at(0, 12), items=[{"menu_item": self.rice.id, "quantity": 1}], ready_after=8, method="cash")
        a = analytics.build(self.r, self.today - timedelta(days=13), self.today)
        self.assertEqual(a["busiest_hour"], 19)
        wd = self.today.isoweekday() - 1
        self.assertEqual(a["heatmap"][wd][a["hours"].index(19)], 2)
        self.assertEqual([(c["name"], c["revenue"], c["qty"]) for c in a["categories"]], [("Rice", Decimal("11.00"), 5)])
        shares = {p["method"]: p["share"] for p in a["payments"]}
        self.assertEqual(a["pay_total"], Decimal("11.00"))
        self.assertAlmostEqual(shares["cash"] + shares["card"], 100.0, places=0)
        self.assertEqual({p["method"]: p["slot"] for p in a["payments"]}, {"cash": 1, "card": 2})
        self.assertEqual(a["kitchen"]["n"], 3)
        self.assertEqual(a["kitchen"]["median"], 10)
        self.assertEqual(a["kitchen"]["on_time"], 67)  # 30 min > 12 min estimate on one of three

    def test_not_sold_lists_available_dishes_without_sales(self):
        self.order(self.at(0, 12))
        a = analytics.build(self.r, self.today, self.today)
        self.assertIn(self.rice, a["not_sold"])
        self.assertNotIn(self.tea, a["not_sold"])

    def test_page_presets_and_permissions(self):
        self.order(self.at(0, 12), method="cash")
        url = reverse("dashboard:analytics", args=["alpha"])
        self.client.force_login(staff(self.r, "owner", Role.OWNER))
        for q in ("", "?range=7", "?range=90", "?range=month", "?range=custom&from=2020-01-01&to=2030-01-01", "?range=custom&from=bad"):
            with self.subTest(q=q):
                resp = self.client.get(url + q)
                self.assertEqual(resp.status_code, 200)
                self.assertLessEqual(resp.context["a"]["days"], analytics.MAX_DAYS)
        resp = self.client.get(url)
        self.assertContains(resp, 'id="analytics-data"')
        self.assertContains(resp, "Busy times")
        self.client.force_login(staff(self.r, "waiter", Role.WAITER))
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_empty_restaurant_renders(self):
        self.client.force_login(staff(self.r, "owner", Role.OWNER))
        resp = self.client.get(reverse("dashboard:analytics", args=["alpha"]))
        self.assertContains(resp, "No orders in this period yet.")
