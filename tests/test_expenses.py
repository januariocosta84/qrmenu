from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.permissions import Role
from apps.expenses import report
from apps.expenses.models import Expense
from apps.orders.models import Order, OrderStatus
from apps.orders.services import place_order

from .test_ordering import make_pro, make_restaurant, staff


class ExpensesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        make_pro(self.r)
        self.manager = staff(self.r, "manager", Role.MANAGER)
        self.client.force_login(self.manager)
        self.url = reverse("dashboard:expenses", args=["alpha"])
        self.today = timezone.localdate()

    def sale(self, day: date, status=OrderStatus.COMPLETED):  # $3.00
        o = place_order(restaurant=self.r, table=self.table, lines=[{"menu_item": self.tea.id, "quantity": 2}])
        when = timezone.make_aware(datetime.combine(day, datetime.min.time()) + timedelta(hours=12))
        Order.objects.filter(pk=o.pk).update(created_at=when, status=status)

    def spend(self, day: date, amount, category=Expense.INGREDIENTS, restaurant=None):
        return Expense.objects.create(restaurant=restaurant or self.r, date=day, amount=Decimal(amount),
                                      category=category, description="x")

    def test_profit_today_week_month_year(self):
        today = date(2026, 6, 17)  # a Wednesday
        self.sale(today)
        self.sale(today)
        self.sale(today, status=OrderStatus.CANCELLED)  # not revenue
        self.spend(today, "2.50")
        self.sale(date(2026, 6, 15))   # Monday: this week
        self.sale(date(2026, 6, 1))    # this month, earlier week
        self.spend(date(2026, 2, 3), "4")  # this year only
        self.sale(date(2025, 12, 31))  # last year: not counted
        g = {x["key"]: x for x in report.at_a_glance(self.r, today)}
        self.assertEqual((g["today"]["revenue"], g["today"]["expenses"], g["today"]["profit"]),
                         (Decimal("6.00"), Decimal("2.50"), Decimal("3.50")))
        self.assertEqual((g["week"]["revenue"], g["week"]["profit"]), (Decimal("9.00"), Decimal("6.50")))
        self.assertEqual((g["month"]["revenue"], g["month"]["profit"]), (Decimal("12.00"), Decimal("9.50")))
        self.assertEqual((g["year"]["revenue"], g["year"]["expenses"], g["year"]["profit"]),
                         (Decimal("12.00"), Decimal("6.50"), Decimal("5.50")))
        self.assertEqual(g["week"]["from"], date(2026, 6, 15))

    def test_loss_and_margin(self):
        self.sale(self.today)
        self.spend(self.today, "10")
        t = report.totals(self.r, self.today, self.today)
        self.assertEqual((t["profit"], t["margin"]), (Decimal("-7.00"), -233.3))
        self.assertContains(self.client.get(self.url + "?period=today"), "Loss")

    def test_breakdown_daily_and_monthly(self):
        d1 = date(2026, 3, 10)
        self.sale(d1)
        self.spend(d1, "1", Expense.GAS)
        self.spend(date(2026, 5, 2), "4", Expense.RENT)
        daily = report.breakdown(self.r, date(2026, 3, 1), date(2026, 3, 31))
        self.assertFalse(daily["monthly"])
        row = next(x for x in daily["rows"] if x["key"] == d1)
        self.assertEqual((row["revenue"], row["expenses"], row["profit"]), (Decimal("3.00"), Decimal("1"), Decimal("2.00")))
        yearly = report.breakdown(self.r, date(2026, 1, 1), date(2026, 12, 31))
        self.assertTrue(yearly["monthly"])
        self.assertEqual(len(yearly["rows"]), 12)
        may = next(x for x in yearly["rows"] if x["key"] == date(2026, 5, 1))
        self.assertEqual(may["expenses"], Decimal("4"))
        self.assertEqual([c["amount"] for c in yearly["categories"]], [Decimal("4"), Decimal("1")])

    def test_add_edit_delete_and_csv(self):
        resp = self.client.post(self.url + "?period=today", {
            "date": self.today.isoformat(), "category": "gas", "description": "Gas refill 12 kg",
            "amount": "18.50", "paid_with": "cash"})
        self.assertEqual(resp.status_code, 302)
        e = Expense.objects.get()
        self.assertEqual((e.amount, e.created_by, e.restaurant), (Decimal("18.50"), self.manager, self.r))
        self.client.post(reverse("dashboard:expense_edit", args=["alpha", e.pk]), {
            "date": self.today.isoformat(), "category": "gas", "description": "Gas refill", "amount": "20",
            "paid_with": "cash"})
        e.refresh_from_db()
        self.assertEqual(e.amount, Decimal("20"))
        csv = self.client.get(self.url + "?period=today&format=csv").content.decode()
        self.assertIn("Gas refill,20.00", csv)
        self.client.post(reverse("dashboard:expense_delete", args=["alpha", e.pk]), {"next": self.url + "?period=week"})
        self.assertFalse(Expense.objects.exists())

    def test_amount_must_be_positive(self):
        resp = self.client.post(self.url, {"date": self.today.isoformat(), "category": "other", "description": "x",
                                           "amount": "0", "paid_with": "cash"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Expense.objects.exists())

    def test_restaurants_and_roles_are_separate(self):
        other, *_ = make_restaurant("beta")
        e = self.spend(self.today, "5", restaurant=other)
        self.assertEqual(report.totals(self.r, self.today, self.today)["expenses"], Decimal("0"))
        self.assertEqual(self.client.get(reverse("dashboard:expense_edit", args=["alpha", e.pk])).status_code, 404)
        self.client.force_login(staff(self.r, "waiter", Role.WAITER))
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_pro_only(self):
        from apps.billing.models import Plan
        from apps.billing.services import get_subscription

        sub = get_subscription(self.r)
        sub.plan = Plan.objects.get(name="Standard")
        sub.save()
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)
        self.assertTemplateUsed(resp, "dashboard/upgrade.html")
        self.assertTrue(Plan.objects.get(name="Pro").feature_expenses)
