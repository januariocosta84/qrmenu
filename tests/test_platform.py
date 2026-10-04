import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import BillingSettings, Invoice, Plan, Subscription
from apps.billing.services import add_months, generate_due_invoices, get_subscription, mark_invoice_paid
from apps.console.models import AuditLog
from apps.core.permissions import Role
from apps.orders.services import OrderError, place_order
from apps.restaurants.models import Table

from .test_ordering import make_restaurant, staff

User = get_user_model()
TODAY = timezone.localdate


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        self.owner = staff(self.r, "owner", Role.OWNER)
        self.admin = User.objects.create_superuser("boss", "boss@example.com", "pw-Secret-123")
        self.standard = Plan.objects.get(name="Standard")
        self.sub = get_subscription(self.r)

    def order(self):
        return place_order(restaurant=self.r, table=self.table, lines=[{"menu_item": self.tea.id, "quantity": 1}])


class SubscriptionRulesTests(Base):
    def test_existing_restaurants_get_default_plan_with_trial(self):
        self.assertEqual(self.sub.plan, self.standard)
        self.assertEqual(self.sub.trial_ends_on, TODAY() + timedelta(days=30))
        self.assertEqual(self.sub.state, Subscription.TRIAL)

    def test_states_trial_grace_expired_and_ordering(self):
        self.sub.trial_ends_on = TODAY() - timedelta(days=3)
        self.sub.save()
        self.assertEqual(self.sub.state, Subscription.GRACE)
        self.order()  # still allowed during grace
        self.sub.trial_ends_on = TODAY() - timedelta(days=8)
        self.sub.save()
        self.assertEqual(self.sub.state, Subscription.EXPIRED)
        self.r.refresh_from_db()
        with self.assertRaises(OrderError):
            self.order()

    def test_comped_and_free_plans_never_expire(self):
        self.sub.trial_ends_on = TODAY() - timedelta(days=100)
        self.sub.comped = True
        self.sub.save()
        self.assertTrue(self.sub.allows_orders)
        self.sub.comped = False
        self.sub.plan = Plan.objects.get(name="Free")
        self.sub.save()
        self.assertTrue(self.sub.allows_orders)

    def test_plan_limits_replace_global_limits(self):
        self.sub.plan = Plan.objects.get(name="Free")  # 5 tables
        self.sub.save()
        self.client.force_login(self.owner)
        url = reverse("dashboard:tables", args=["alpha"])
        self.client.post(url, {"action": "bulk", "start": 1, "end": 10})
        self.assertEqual(Table.objects.filter(restaurant=self.r).count(), 1)  # refused: would exceed 5
        self.client.post(url, {"action": "bulk", "start": 20, "end": 23})
        self.assertEqual(Table.objects.filter(restaurant=self.r).count(), 5)
        self.client.post(url, {"number": "99", "seats": 4, "is_active": "on"})
        self.assertEqual(Table.objects.filter(restaurant=self.r).count(), 5)

    def test_signup_starts_subscription(self):
        self.client.post(reverse("accounts:signup"), {
            "restaurant_name": "New Place", "full_name": "Ana Silva", "email": "ana@example.com",
            "password": "a-Strong-pass-2024", "accept": "on", "website": "",
        })
        sub = Subscription.objects.get(restaurant__name="New Place")
        self.assertEqual(sub.plan, self.standard)
        self.assertEqual(sub.state, Subscription.TRIAL)

    def test_storefront_shows_not_accepting_when_expired(self):
        self.sub.trial_ends_on = TODAY() - timedelta(days=30)
        self.sub.save()
        self.client.get(self.table.active_qr().get_path())
        page = self.client.get(reverse("storefront:table", args=["alpha", "12"]))
        self.assertFalse(page.context["can_order"])
        self.assertContains(page, page.context["ui"]["not_accepting"])


class InvoiceTests(Base):
    def test_numbering_payment_extends_subscription(self):
        from apps.billing.services import create_invoice
        a = create_invoice(self.sub)
        self.assertEqual(a.number, f"INV-{TODAY().year}-00001")
        self.assertEqual(a.amount, Decimal("15.00"))
        self.assertEqual(a.period_start, self.sub.trial_ends_on + timedelta(days=1))
        mark_invoice_paid(a, user=self.admin, method="cash")
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.paid_until, a.period_end)
        b = create_invoice(self.sub, months=3)
        self.assertEqual(b.number, f"INV-{TODAY().year}-00002")
        self.assertEqual(b.amount, Decimal("45.00"))
        self.assertEqual(b.period_start, a.period_end + timedelta(days=1))
        with self.assertRaises(ValueError):
            mark_invoice_paid(a)

    def test_expired_subscription_reactivates_when_paid(self):
        from apps.billing.services import create_invoice
        self.sub.trial_ends_on = TODAY() - timedelta(days=40)
        self.sub.save()
        inv = create_invoice(self.sub)
        self.assertEqual(inv.period_start, TODAY())  # don't bill for the time it was switched off
        mark_invoice_paid(inv)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state, Subscription.ACTIVE)
        self.order()

    def test_generate_due_invoices(self):
        self.sub.trial_ends_on = TODAY() + timedelta(days=3)
        self.sub.save()
        other, *_ = make_restaurant("beta")
        far = get_subscription(other)  # trial ends in 30 days: not due yet
        self.assertEqual(len(generate_due_invoices()), 1)
        self.assertEqual(len(generate_due_invoices()), 0)  # no duplicates while one is open
        self.assertFalse(far.invoices.exists())

    def test_add_months_handles_month_ends(self):
        from datetime import date
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2026, 12, 15), 1), date(2027, 1, 15))


class ConsoleAccessTests(Base):
    def test_only_superusers(self):
        for url in [reverse("console:overview"), reverse("console:users"), reverse("console:plans")]:
            self.client.logout()
            self.assertEqual(self.client.get(url).status_code, 302)  # login
            self.client.force_login(self.owner)
            self.assertEqual(self.client.get(url).status_code, 404)
            self.client.force_login(self.admin)
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_all_pages_render(self):
        from apps.billing.services import create_invoice
        inv = create_invoice(self.sub)
        self.client.force_login(self.admin)
        for name, args in [("overview", []), ("restaurants", []), ("restaurant", [self.r.pk]), ("users", []),
                           ("user", [self.owner.pk]), ("plans", []), ("plan_create", []),
                           ("plan_edit", [self.standard.pk]), ("invoices", []), ("invoice", [inv.pk]),
                           ("settings", []), ("audit", [])]:
            self.assertEqual(self.client.get(reverse(f"console:{name}", args=args)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("console:restaurants") + "?billing=trial&q=alp").status_code, 200)


class ConsoleActionsTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.url = reverse("console:restaurant", args=[self.r.pk])

    def post(self, **data):
        return self.client.post(self.url, data, follow=True)

    def test_suspend_approve_and_audit(self):
        self.post(action="suspend")
        self.r.refresh_from_db()
        self.assertFalse(self.r.is_active)
        self.post(action="approve")
        self.r.refresh_from_db()
        self.assertTrue(self.r.is_active)
        self.assertEqual(list(AuditLog.objects.values_list("action", flat=True))[:2],
                         ["restaurant.approve", "restaurant.suspend"])

    def test_change_plan_paid_until_trial_comp_cancel(self):
        pro = Plan.objects.get(name="Pro")
        self.post(action="set_plan", plan=pro.pk)
        self.post(action="set_paid_until", paid_until="2030-01-31")
        self.post(action="extend_trial", days="10")
        self.post(action="comp")
        self.sub.refresh_from_db()
        self.assertEqual((self.sub.plan, str(self.sub.paid_until), self.sub.comped), (pro, "2030-01-31", True))
        self.assertEqual(self.sub.trial_ends_on, TODAY() + timedelta(days=40))
        self.post(action="cancel")
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state, Subscription.CANCELLED)
        self.post(action="reactivate")
        self.sub.refresh_from_db()
        self.assertIsNone(self.sub.cancelled_at)

    def test_create_and_mark_invoice_paid_from_console(self):
        self.post(action="create_invoice", months="2", amount="", notes="2 months upfront")
        inv = Invoice.objects.get()
        self.assertEqual(inv.amount, Decimal("30.00"))
        self.client.post(reverse("console:invoice", args=[inv.pk]), {"action": "mark_paid", "method": "bank_transfer",
                                                                    "reference": "BNCTL-123"})
        inv.refresh_from_db()
        self.assertEqual((inv.status, inv.reference, inv.recorded_by), ("paid", "BNCTL-123", self.admin))

    def test_plan_request_flow(self):
        pro = Plan.objects.get(name="Pro")
        self.client.force_login(self.owner)
        self.client.post(reverse("dashboard:billing", args=["alpha"]), {"plan": pro.pk})
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.requested_plan, pro)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("console:overview")), "asked to change")
        self.post(action="approve_request")
        self.sub.refresh_from_db()
        self.assertEqual((self.sub.plan, self.sub.requested_plan), (pro, None))

    def test_unknown_action_is_rejected(self):
        self.assertEqual(self.client.post(self.url, {"action": "delete_everything"}).status_code, 404)


class ConsoleUserTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def act(self, user, action):
        return self.client.post(reverse("console:user", args=[user.pk]), {"action": action}, follow=True)

    def test_disable_enable_verify_and_admin_rights(self):
        self.act(self.owner, "deactivate")
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.is_active)
        self.act(self.owner, "activate")
        self.act(self.owner, "verify_email")
        self.act(self.owner, "make_admin")
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active and self.owner.email_verified and self.owner.is_superuser)

    def test_temporary_password_shown_once_and_works(self):
        resp = self.act(self.owner, "set_password")
        msg = next(str(m) for m in resp.context["messages"] if "temporary password" in str(m))
        password = msg.split(": ")[1].split()[0]
        self.assertTrue(self.client.login(username="owner", password=password))

    def test_send_reset_email(self):
        self.owner.email = "owner@example.com"
        self.owner.save()
        self.act(self.owner, "send_reset")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("password-reset", mail.outbox[0].body)

    def test_cannot_lock_yourself_out(self):
        self.act(self.admin, "deactivate")
        self.act(self.admin, "remove_admin")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active and self.admin.is_superuser)


class OwnerBillingTests(Base):
    def test_owner_sees_billing_others_dont(self):
        from apps.billing.services import create_invoice
        inv = create_invoice(self.sub)
        cfg = BillingSettings.load()
        cfg.payment_instructions = "BNCTL account 123-456"
        cfg.save()
        self.client.force_login(self.owner)
        page = self.client.get(reverse("dashboard:billing", args=["alpha"]))
        self.assertContains(page, "Standard")
        self.assertContains(page, "BNCTL account 123-456")
        self.assertContains(page, inv.number)
        self.assertContains(self.client.get(reverse("dashboard:billing_invoice", args=["alpha", inv.pk])), "INVOICE")
        waiter = staff(self.r, "waiter", Role.WAITER)
        self.client.force_login(waiter)
        self.assertEqual(self.client.get(reverse("dashboard:billing", args=["alpha"])).status_code, 403)
        other, *_ = make_restaurant("beta")
        self.client.force_login(staff(other, "beta-owner", Role.OWNER))
        self.assertEqual(self.client.get(reverse("dashboard:billing_invoice", args=["beta", inv.pk])).status_code, 404)

    def test_trial_ending_banner(self):
        self.sub.trial_ends_on = TODAY() + timedelta(days=3)
        self.sub.save()
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("dashboard:overview", args=["alpha"])), "free trial ends in 3 days")
