from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.billing.models import Plan
from apps.billing.services import get_subscription
from apps.core.permissions import Role
from apps.payments.models import CashSession
from apps.quotations.models import Quotation

from .test_ordering import make_pro, make_restaurant, staff

PRO_PAGES = [("analytics", "analytics"), ("cash_register", "cash_register"), ("quotations", "quotations"),
             ("quotation_create", "quotations")]


class ProFeatureTests(TestCase):
    def setUp(self):
        cache.clear()
        self.r, *_ = make_restaurant()
        self.client.force_login(staff(self.r, "owner", Role.OWNER))

    def test_pro_plan_has_the_features_and_others_do_not(self):
        self.assertTrue(all(Plan.objects.get(name="Pro").has_feature(f) for f in Plan.FEATURES))
        for name in ("Free", "Standard"):
            self.assertFalse(any(Plan.objects.get(name=name).has_feature(f) for f in Plan.FEATURES))

    def test_standard_plan_sees_upgrade_page(self):
        self.assertEqual(get_subscription(self.r).plan.name, "Standard")
        for name, _feature in PRO_PAGES:
            with self.subTest(page=name):
                resp = self.client.get(reverse(f"dashboard:{name}", args=["alpha"]))
                self.assertEqual(resp.status_code, 403)
                self.assertTemplateUsed(resp, "dashboard/upgrade.html")
                self.assertContains(resp, "Pro", status_code=403)
                self.assertContains(resp, reverse("dashboard:billing", args=["alpha"]), status_code=403)

    def test_posts_are_blocked_too(self):
        self.client.post(reverse("dashboard:cash_register", args=["alpha"]), {"action": "open", "amount": "10"})
        self.assertFalse(CashSession.objects.exists())
        self.client.post(reverse("dashboard:quotation_create", args=["alpha"]), {"client_name": "X"})
        self.assertFalse(Quotation.objects.exists())

    def test_sidebar_marks_pro_features(self):
        resp = self.client.get(reverse("dashboard:overview", args=["alpha"]))
        self.assertContains(resp, "PRO", count=7)  # revenue, cash register, analytics, expenses, quotations, branches, API
        self.assertNotContains(resp, "Before selling starts")  # no cash-register reminder without the feature
        make_pro(self.r)
        resp = self.client.get(reverse("dashboard:overview", args=["alpha"]))
        self.assertNotContains(resp, 'class="pro-badge"')
        self.assertContains(resp, "Before selling starts")

    def test_pro_plan_unlocks_everything(self):
        make_pro(self.r)
        for name, _feature in PRO_PAGES:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(f"dashboard:{name}", args=["alpha"])).status_code, 200)

    def test_feature_can_be_switched_on_for_any_plan(self):
        standard = get_subscription(self.r).plan
        standard.feature_analytics = True
        standard.save()
        self.assertEqual(self.client.get(reverse("dashboard:analytics", args=["alpha"])).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashboard:quotations", args=["alpha"])).status_code, 403)

    def test_waiter_without_billing_rights_is_told_to_ask_the_owner(self):
        self.client.force_login(staff(self.r, "waiter", Role.WAITER))
        resp = self.client.get(reverse("dashboard:cash_register", args=["alpha"]))
        self.assertContains(resp, "Ask the restaurant owner to upgrade the plan.", status_code=403)

    def test_pricing_lists_pro_features(self):
        resp = self.client.get("/")
        self.assertContains(resp, "Quotations for catering & tenders", count=1)


class NoBillingPlatformTests(TestCase):
    """Platforms without billing set up don't restrict features."""

    def test_everything_available_without_billing(self):
        from apps.billing.models import BillingSettings

        cache.clear()
        Plan.objects.all().delete()
        BillingSettings.objects.all().delete()
        r, *_ = make_restaurant()
        self.assertIsNone(get_subscription(r))
        self.client.force_login(staff(r, "owner", Role.OWNER))
        for name, _feature in PRO_PAGES:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(f"dashboard:{name}", args=["alpha"])).status_code, 200)
