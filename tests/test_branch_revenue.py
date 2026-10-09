"""
Multi-branch revenue tracking: main branch → sub-branches, manual revenue entries,
daily dashboard for the main branch, and strict separation between branches.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.billing.services import generate_due_invoices, get_subscription
from apps.core.permissions import Role
from apps.expenses import report
from apps.expenses.models import RevenueEntry
from apps.restaurants.branches import create_branch
from apps.restaurants.models import Restaurant, RestaurantStaff

from .test_ordering import make_pro, make_restaurant, staff


@override_settings(SIGNUP_MODE="open")
class BranchRevenueTests(TestCase):
    def setUp(self):
        cache.clear()
        self.main, *_ = make_restaurant("atsabe")
        self.main.name = "Cafe Atsabe"
        self.main.save()
        make_pro(self.main)
        self.owner = staff(self.main, "owner", Role.OWNER)
        self.owner.email_verified = True
        self.owner.save()
        self.dili = create_branch(self.owner, main=self.main, name="Cafe Atsabe – Dili", source=self.main,
                                  status=Restaurant.BRANCH_ACTIVE)
        self.baucau = create_branch(self.owner, main=self.main, name="Cafe Atsabe – Baucau", source=self.main,
                                    status=Restaurant.BRANCH_ACTIVE)
        self.dili_manager = staff(self.dili, "dili-manager", Role.MANAGER)
        self.baucau_cashier = staff(self.baucau, "baucau-cashier", Role.WAITER)
        self.today = timezone.localdate()

    def entry(self, branch, amount, day=None, method=RevenueEntry.CASH):
        return RevenueEntry.objects.create(restaurant=branch, amount=Decimal(amount), method=method,
                                           date=day or self.today)

    def test_structure(self):
        self.assertTrue(self.main.is_main_branch)
        self.assertEqual((self.dili.parent, self.baucau.parent), (self.main, self.main))
        self.assertEqual(get_subscription(self.dili), self.main.subscription)  # billed through the main branch
        # a branch of a branch still hangs off the main branch (one level)
        nested = create_branch(self.owner, main=self.dili, name="Cafe Atsabe – Lospalos")
        self.assertEqual(nested.parent, self.main)

    def test_branch_records_its_own_revenue(self):
        self.client.force_login(self.dili_manager)
        url = reverse("dashboard:revenue", args=[self.dili.slug])
        for amount, method in (("120.50", "cash"), ("30", "transfer")):  # several entries per day
            self.client.post(url, {"date": self.today.isoformat(), "amount": amount, "method": method, "note": "walk-in"})
        entries = RevenueEntry.objects.filter(restaurant=self.dili)
        self.assertEqual(entries.count(), 2)
        self.assertTrue(all(e.created_by == self.dili_manager for e in entries))
        page = self.client.get(url)
        self.assertEqual(page.context["today"]["revenue"], Decimal("150.50"))
        self.assertEqual(page.context["today"]["recorded_revenue"], Decimal("150.50"))

    def test_branch_never_sees_other_branches(self):
        self.entry(self.baucau, "99")
        self.client.force_login(self.dili_manager)
        page = self.client.get(reverse("dashboard:revenue", args=[self.dili.slug]))
        self.assertEqual(page.context["today"]["revenue"], Decimal("0"))
        self.assertEqual(self.client.get(reverse("dashboard:revenue", args=[self.baucau.slug])).status_code, 404)
        self.assertEqual(self.client.get(reverse("dashboard:branches", args=[self.dili.slug])).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard:branches", args=[self.main.slug])).status_code, 404)
        # can't delete another branch's entry through its own URL either
        other = self.entry(self.baucau, "5")
        self.client.post(reverse("dashboard:revenue_entry_delete", args=[self.dili.slug, other.pk]))
        self.assertTrue(RevenueEntry.objects.filter(pk=other.pk).exists())

    def test_cashier_deletes_only_own_entries(self):
        mine = RevenueEntry.objects.create(restaurant=self.baucau, amount=Decimal("4"), created_by=self.baucau_cashier)
        theirs = self.entry(self.baucau, "6")
        self.client.force_login(self.baucau_cashier)
        self.client.post(reverse("dashboard:revenue_entry_delete", args=[self.baucau.slug, theirs.pk]))
        self.client.post(reverse("dashboard:revenue_entry_delete", args=[self.baucau.slug, mine.pk]))
        self.assertEqual(list(RevenueEntry.objects.filter(restaurant=self.baucau)), [theirs])

    def test_date_filter(self):
        old = self.entry(self.dili, "10", day=self.today - timedelta(days=3))
        self.entry(self.dili, "20")
        self.client.force_login(self.dili_manager)
        url = reverse("dashboard:revenue", args=[self.dili.slug])
        d = (self.today - timedelta(days=3)).isoformat()
        page = self.client.get(f"{url}?from={d}&to={d}")
        self.assertEqual(list(page.context["entries"]), [old])
        self.assertEqual(list(self.client.get(url).context["entries"])[0].amount, Decimal("20"))  # default: today

    def test_main_dashboard_per_branch_and_combined(self):
        self.entry(self.main, "50")
        self.entry(self.dili, "120")
        self.entry(self.baucau, "80")
        self.entry(self.baucau, "15", day=self.today - timedelta(days=1))
        self.client.force_login(self.owner)
        resp = self.client.get(reverse("dashboard:branches", args=[self.main.slug]))
        rows = {r["branch"].name: r["day"]["revenue"] for r in resp.context["rows"]}
        self.assertEqual(rows, {"Cafe Atsabe": Decimal("50"), "Cafe Atsabe – Dili": Decimal("120"),
                                "Cafe Atsabe – Baucau": Decimal("80")})
        self.assertEqual(resp.context["total"]["day"]["revenue"], Decimal("250"))
        yesterday = (self.today - timedelta(days=1)).isoformat()
        past = self.client.get(reverse("dashboard:branches", args=[self.main.slug]) + f"?date={yesterday}")
        self.assertEqual(past.context["total"]["day"]["revenue"], Decimal("15"))
        self.assertGreaterEqual(resp.context["total"]["month"]["revenue"], Decimal("250"))
        # owner opening Branches from a sub-branch goes to the main branch's dashboard
        self.assertRedirects(self.client.get(reverse("dashboard:branches", args=[self.dili.slug])),
                             reverse("dashboard:branches", args=[self.main.slug]), fetch_redirect_response=False)

    def test_entries_count_in_profit_reports(self):
        self.entry(self.dili, "40")
        self.assertEqual(report.totals(self.dili, self.today, self.today)["revenue"], Decimal("40"))

    def test_main_manages_sub_branch_details_and_staff(self):
        self.client.force_login(self.owner)
        url = reverse("dashboard:branch_manage", args=[self.main.slug, self.dili.pk])
        self.client.post(url, {"action": "details", "name": "Cafe Atsabe – Dili Centro", "address": "Colmera", "phone": ""})
        self.dili.refresh_from_db()
        self.assertEqual(self.dili.name, "Cafe Atsabe – Dili Centro")
        page = self.client.get(url)
        roles = [c[0] for c in page.context["staff_form"].fields["role"].choices]
        self.assertNotIn(Role.OWNER, roles)
        self.client.post(url, {"action": "staff", "username": "dili-cashier", "first_name": "", "email": "",
                               "role": Role.WAITER, "password": "A-strong-pass-2026"})
        m = RestaurantStaff.objects.get(restaurant=self.dili, user__username="dili-cashier")
        self.client.post(url, {"action": "staff_toggle", "member": m.pk})
        m.refresh_from_db()
        self.assertFalse(m.is_active)
        # a different main branch's owner can't manage these branches
        other, *_ = make_restaurant("other")
        make_pro(other)
        self.client.force_login(staff(other, "other-owner", Role.OWNER))
        self.assertEqual(self.client.get(reverse("dashboard:branch_manage", args=["other", self.dili.pk])).status_code, 404)

    def test_deactivate_and_reactivate(self):
        self.client.force_login(self.owner)
        url = reverse("dashboard:branch_manage", args=[self.main.slug, self.dili.pk])
        self.client.post(url, {"action": "deactivate"})
        self.dili.refresh_from_db()
        self.assertTrue(self.dili.is_closed)
        # customers: menu gone; branch staff: blocked; owner: can still look at history
        self.assertEqual(Client().get(reverse("storefront:menu", args=[self.dili.slug])).status_code, 404)
        manager = Client()
        manager.force_login(self.dili_manager)
        blocked = manager.get(reverse("dashboard:revenue", args=[self.dili.slug]))
        self.assertEqual(blocked.status_code, 403)
        self.assertContains(blocked, "deactivated", status_code=403)
        self.assertEqual(self.client.get(reverse("dashboard:overview", args=[self.dili.slug])).status_code, 200)
        # sub-branches are never invoiced themselves
        self.assertNotIn(self.dili.pk, [i.restaurant_id for i in generate_due_invoices()])
        self.client.post(url, {"action": "reactivate"})
        self.dili.refresh_from_db()
        self.assertFalse(self.dili.is_closed)
        self.assertEqual(manager.get(reverse("dashboard:revenue", args=[self.dili.slug])).status_code, 200)

    def test_revenue_is_pro_only(self):
        r, *_ = make_restaurant("plain")
        self.client.force_login(staff(r, "plain-owner", Role.OWNER))
        resp = self.client.get(reverse("dashboard:revenue", args=["plain"]))
        self.assertEqual(resp.status_code, 403)
        self.assertTemplateUsed(resp, "dashboard/upgrade.html")
