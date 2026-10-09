"""
Anti-abuse rules for branches: business profile verification, platform review of
sub-branch requests, branch slots and add-ons, abuse flags, suspension/conversion.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.billing.models import Invoice, Subscription
from apps.billing.services import branch_slots, create_invoice, get_subscription, mark_invoice_paid
from apps.core.permissions import Role
from apps.expenses.models import RevenueEntry
from apps.restaurants.branches import abuse_flags, create_branch
from apps.restaurants.models import BusinessProfile, Restaurant

from .test_branches import photo, verify_profile
from .test_ordering import make_pro, make_restaurant, staff

User = get_user_model()


@override_settings(SIGNUP_MODE="open", ADMINS=[("Platform", "admin@example.test")])
class BranchVerificationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.main, *_ = make_restaurant("atsabe")
        self.main.name = "Cafe Atsabe"
        self.main.save()
        self.sub = make_pro(self.main)
        self.owner = staff(self.main, "owner", Role.OWNER)
        self.owner.email, self.owner.email_verified = "owner@example.test", True
        self.owner.save()
        self.client.force_login(self.owner)
        self.branches_url = reverse("dashboard:branches", args=["atsabe"])
        self.admin = User.objects.create_superuser("platform", "admin@example.test", "x-Admin-pass-2026")
        self.console = Client()
        self.console.force_login(self.admin)

    def request_data(self, **extra):
        return {"action": "request", "name": "Cafe Atsabe – Dili", "address": "Rua de Colmera, Dili",
                "phone": "+670 7712 3456", "storefront_photo": photo(), "registration_number": "SERVE-123",
                "owner_name": "Ana Owner", "source": "", **extra}

    def branch(self, name, status=Restaurant.BRANCH_PENDING, **kw):
        return create_branch(self.owner, main=self.main, name=name, status=status, **kw)

    # ------------------------------------------------------------ business profile

    def test_profile_prompt_banner_and_gating(self):
        overview = reverse("dashboard:overview", args=["atsabe"])
        page = self.client.get(overview)
        self.assertContains(page, "Complete your business profile")
        self.assertNotContains(page, 'id="profile-prompt"')  # pop-up only right after a login
        self.client = Client()
        self.client.post(reverse("accounts:login"), {"username": "owner", "password": "pw-Secret-123"})
        self.assertContains(self.client.get(overview), 'id="profile-prompt"')
        self.client.post(reverse("dashboard:profile_prompt_later", args=["atsabe"]), {"next": overview})
        page = self.client.get(overview)
        self.assertNotContains(page, 'id="profile-prompt"')
        self.assertContains(page, "Complete your business profile")  # the banner stays
        # Can't request branches yet, but can record revenue.
        self.assertIsNone(self.client.get(self.branches_url).context["form"])
        self.assertEqual(self.client.post(self.branches_url, self.request_data()).status_code, 403)
        self.client.post(reverse("dashboard:revenue", args=["atsabe"]),
                         {"date": "2026-10-09", "amount": "10", "method": "cash", "note": ""})
        self.assertEqual(RevenueEntry.objects.filter(restaurant=self.main).count(), 1)

    def test_submit_profile_then_verify(self):
        url = reverse("dashboard:business_profile", args=["atsabe"])
        self.client.post(url, {"registration_number": "SERVE-123", "owner_name": "Ana Owner",
                               "address": "Colmera, Dili", "phone": "+670 7700 0000"})
        p = BusinessProfile.objects.get(restaurant=self.main)
        self.assertEqual(p.status, BusinessProfile.REVIEW)
        self.assertIn("Business profile to verify", mail.outbox[-1].subject)
        page = self.client.get(reverse("dashboard:overview", args=["atsabe"]))
        self.assertNotContains(page, "Complete your business profile")  # complete: no more banner
        self.assertIsNone(self.client.get(self.branches_url).context["form"])  # still not verified
        self.console.post(reverse("console:verification"), {"kind": "profile", "pk": p.pk, "action": "verify"})
        p.refresh_from_db()
        self.assertEqual(p.status, BusinessProfile.VERIFIED)
        self.assertIsNotNone(self.client.get(self.branches_url).context["form"])
        # Changing a verified profile sends it back for review.
        self.client.post(url, {"registration_number": "SERVE-999", "owner_name": "Ana Owner",
                               "address": "Colmera, Dili", "phone": "+670 7700 0000"})
        p.refresh_from_db()
        self.assertEqual(p.status, BusinessProfile.REVIEW)

    def test_branch_name_must_use_brand(self):
        verify_profile(self.main)
        resp = self.client.post(self.branches_url, self.request_data(name="Warung Baru"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "must use your brand name")
        self.assertFalse(Restaurant.objects.filter(parent=self.main).exists())
        for field in ("address", "phone", "storefront_photo"):  # all required
            data = self.request_data()
            data.pop(field)
            self.client.post(self.branches_url, data)
        self.assertFalse(Restaurant.objects.filter(parent=self.main).exists())

    # ------------------------------------------------------------ review

    def test_pending_branch_cannot_record_or_be_used_by_staff(self):
        b = self.branch("Cafe Atsabe – Baucau")
        cashier = Client()
        cashier.force_login(staff(b, "cashier", Role.WAITER))
        blocked = cashier.get(reverse("dashboard:revenue", args=[b.slug]))
        self.assertEqual(blocked.status_code, 403)
        self.assertContains(blocked, "Waiting for approval", status_code=403)
        url = reverse("dashboard:revenue", args=[b.slug])
        self.assertContains(self.client.get(url), "waiting for approval")
        self.assertEqual(self.client.post(url, {"date": "2026-10-09", "amount": "5", "method": "cash"}).status_code, 403)
        self.assertFalse(RevenueEntry.objects.filter(restaurant=b).exists())
        self.assertEqual(Client().get(reverse("storefront:menu", args=[b.slug])).status_code, 404)

    def test_approve_reject_resubmit(self):
        verify_profile(self.main)
        self.client.post(self.branches_url, self.request_data())
        b = Restaurant.objects.get(parent=self.main)
        self.assertIn("Branch request", mail.outbox[-1].subject)
        page = self.console.get(reverse("console:verification"))
        self.assertContains(page, "Cafe Atsabe – Dili")
        self.console.post(reverse("console:verification"), {"kind": "branch", "pk": b.pk, "action": "reject",
                                                            "note": "Photo doesn't show the brand name."})
        b.refresh_from_db()
        self.assertEqual(b.branch_status, Restaurant.BRANCH_REJECTED)
        self.assertIn("Photo doesn't show the brand name.", mail.outbox[-1].body)
        self.assertEqual(mail.outbox[-1].to, ["owner@example.test"])
        self.client.post(reverse("dashboard:branch_manage", args=["atsabe", b.pk]),
                         {"action": "resubmit", "storefront_photo": photo()})
        b.refresh_from_db()
        self.assertEqual(b.branch_status, Restaurant.BRANCH_PENDING)
        self.console.post(reverse("console:verification"), {"kind": "branch", "pk": b.pk, "action": "approve"})
        b.refresh_from_db()
        self.assertEqual((b.branch_status, b.is_active), (Restaurant.BRANCH_ACTIVE, True))
        self.assertIn("approved", mail.outbox[-1].subject)
        self.client.post(reverse("dashboard:revenue", args=[b.slug]), {"date": "2026-10-09", "amount": "5", "method": "cash"})
        self.assertTrue(RevenueEntry.objects.filter(restaurant=b).exists())

    def test_non_admins_cannot_see_verification(self):
        self.assertEqual(self.client.get(reverse("console:verification")).status_code, 404)

    # ------------------------------------------------------------ slots & add-ons

    def test_slots_and_paid_add_on(self):
        self.branch("Cafe Atsabe – Dili", Restaurant.BRANCH_ACTIVE)
        self.branch("Cafe Atsabe – Baucau", Restaurant.BRANCH_ACTIVE)
        third = self.branch("Cafe Atsabe – Lospalos")
        self.assertEqual(branch_slots(self.main)["free"], 0)  # Pro: main + 2
        self.console.post(reverse("console:verification"), {"kind": "branch", "pk": third.pk, "action": "approve"})
        third.refresh_from_db()
        self.assertEqual(third.branch_status, Restaurant.BRANCH_PENDING)  # no slot
        # The owner buys an extra branch: an invoice; the slot appears once it is paid.
        self.client.post(self.branches_url, {"action": "buy_slot"})
        inv = Invoice.objects.get(restaurant=self.main, branch_slots=1)
        self.assertEqual(inv.amount, Decimal("5.00"))
        self.client.post(self.branches_url, {"action": "buy_slot"})  # no second invoice while one is open
        self.assertEqual(Invoice.objects.filter(branch_slots=1).count(), 1)
        mark_invoice_paid(inv, user=self.admin)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.extra_branches, 1)
        self.console.post(reverse("console:verification"), {"kind": "branch", "pk": third.pk, "action": "approve"})
        third.refresh_from_db()
        self.assertEqual(third.branch_status, Restaurant.BRANCH_ACTIVE)
        # Renewal invoices include the add-on.
        self.assertEqual(create_invoice(self.sub).amount, self.sub.plan.price_monthly + Decimal("5.00"))

    def test_reactivating_needs_a_slot(self):
        a = self.branch("Cafe Atsabe – Dili", Restaurant.BRANCH_ACTIVE)
        url = reverse("dashboard:branch_manage", args=["atsabe", a.pk])
        self.client.post(url, {"action": "deactivate"})
        self.branch("Cafe Atsabe – Baucau", Restaurant.BRANCH_ACTIVE)
        self.branch("Cafe Atsabe – Same", Restaurant.BRANCH_ACTIVE)
        self.client.post(url, {"action": "reactivate"})
        a.refresh_from_db()
        self.assertTrue(a.is_closed)

    def test_sub_branch_billing_goes_to_main(self):
        b = self.branch("Cafe Atsabe – Dili", Restaurant.BRANCH_ACTIVE)
        self.assertRedirects(self.client.get(reverse("dashboard:billing", args=[b.slug])),
                             reverse("dashboard:billing", args=["atsabe"]), fetch_redirect_response=False)

    # ------------------------------------------------------------ abuse

    def test_abuse_flags(self):
        verify_profile(self.main)
        other, *_ = make_restaurant("other")
        other.phone = "7799 1234"
        other.save()
        b = self.branch("Cafe Atsabe – Bali", address="Jalan Kuta, Bali", phone="+62 812 7799 1234",
                        branch_registration_number="NIB-555", branch_owner_name="Someone Else")
        flags = " ".join(abuse_flags(b))
        for expected in ("Different registration number", "Different owner", "isn't recognisable",
                         "another country", "Same phone number as Other"):
            self.assertIn(expected, flags)
        clean = self.branch("Cafe Atsabe – Baucau", address="Vila Antiga, Baucau", phone="7712 0000",
                            branch_registration_number="serve-123", branch_owner_name="ana owner")
        self.assertEqual(abuse_flags(clean), [])
        for n in range(3):
            self.branch(f"Cafe Atsabe – {n}", address="Dili")
        self.assertTrue(any("branch requests in the last 30 days" in f
                            for f in abuse_flags(self.branch("Cafe Atsabe – 4", address="Dili"))))

    # ------------------------------------------------------------ suspend / convert

    def test_suspend_and_convert(self):
        b = self.branch("Cafe Atsabe – Dili", Restaurant.BRANCH_ACTIVE)
        manager = Client()
        manager.force_login(staff(b, "mgr", Role.MANAGER))
        detail = reverse("console:restaurant", args=[b.pk])
        self.console.post(detail, {"action": "suspend_branch", "note": "Different owner"})
        b.refresh_from_db()
        self.assertEqual((b.branch_status, b.is_active), (Restaurant.BRANCH_SUSPENDED, False))
        self.assertEqual(manager.get(reverse("dashboard:overview", args=[b.slug])).status_code, 403)
        self.console.post(detail, {"action": "convert"})
        b.refresh_from_db()
        self.assertIsNone(b.parent)
        self.assertTrue(b.is_main_branch)
        own = Subscription.objects.get(restaurant=b)
        self.assertEqual(get_subscription(b), own)
        self.assertEqual(own.plan.name, "Pro")
        self.assertTrue(Invoice.objects.filter(restaurant=b, status=Invoice.OPEN).exists())


class TermsTests(TestCase):
    def test_terms_page(self):
        resp = self.client.get(reverse("storefront:terms"))
        self.assertContains(resp, "must be owned and operated by the same business")
        self.assertContains(self.client.get("/"), reverse("storefront:terms"))
