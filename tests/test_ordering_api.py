"""Ordering API (Pro): keys, branch scoping, orders → revenue, plan gating, rate limit, log, sandbox, webhooks."""
import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import Plan
from apps.billing.services import get_subscription
from apps.core.permissions import Role
from apps.expenses import report
from apps.expenses.models import RevenueEntry
from apps.integrations import webhooks
from apps.integrations.forms import WebhookForm
from apps.integrations.models import ApiKey, ApiRequestLog, SandboxOrder, Webhook
from apps.orders.models import Notification, Order, PaymentStatus
from apps.restaurants.branches import create_branch
from apps.restaurants.models import Restaurant

from .test_branches import verify_profile
from .test_ordering import make_pro, make_restaurant, staff

BASE = "/api/ordering/v1/"
HTTPS = {}


class HttpsClient(Client):
    """Every request over HTTPS (the API refuses plain HTTP)."""

    def generic(self, *args, **kwargs):
        kwargs["secure"] = True
        return super().generic(*args, **kwargs)


@override_settings(WEBHOOKS_ASYNC=False)
class OrderingApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.main, self.rice, self.egg, self.tea, _t = make_restaurant("atsabe")
        self.main.name = "Cafe Atsabe"
        self.main.save()
        make_pro(self.main)
        verify_profile(self.main)
        self.owner = staff(self.main, "owner", Role.OWNER)
        self.dili = create_branch(self.owner, main=self.main, name="Cafe Atsabe – Dili", source=self.main,
                                  status=Restaurant.BRANCH_ACTIVE)
        self.pending = create_branch(self.owner, main=self.main, name="Cafe Atsabe – Same")
        self.key, self.raw = ApiKey.generate(restaurant=self.main, name="Website")
        self.api = HttpsClient(HTTP_AUTHORIZATION=f"Bearer {self.raw}")

    def get(self, path, client=None):
        return (client or self.api).get(BASE + path)

    def send(self, method, path, data, client=None):
        c = client or self.api
        return getattr(c, method)(BASE + path, data=json.dumps(data), content_type="application/json")

    def order(self, **extra):
        return self.send("post", "orders", {"branch": self.main.pk, "items": [{"menu_item": self.tea.pk, "quantity": 2}],
                                            "payment_method": "cash", "customer_name": "Maria", **extra})

    # ------------------------------------------------------------ access

    def test_authentication_and_https(self):
        self.assertEqual(HttpsClient().get(BASE + "branches").json()["error"]["code"], "missing_api_key")
        bad = HttpsClient(HTTP_AUTHORIZATION="Bearer qrm_live_nope")
        self.assertEqual(bad.get(BASE + "branches").status_code, 401)
        plain = Client(HTTP_AUTHORIZATION=f"Bearer {self.raw}")
        resp = plain.get(BASE + "branches")
        self.assertEqual((resp.status_code, resp.json()["error"]["code"]), (403, "https_required"))
        self.assertEqual(HttpsClient(HTTP_X_API_KEY=self.raw).get(BASE + "branches").status_code, 200)
        self.key.revoke()
        self.assertEqual(self.get("branches").json()["error"]["code"], "revoked_api_key")

    def test_cors_for_browser_apps(self):
        pre = HttpsClient().options(BASE + "orders", HTTP_ORIGIN="https://shop.example",
                                    HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST")
        self.assertEqual(pre.status_code, 204)
        self.assertEqual(pre["Access-Control-Allow-Origin"], "*")
        self.assertIn("Authorization", pre["Access-Control-Allow-Headers"])
        self.assertIn("PATCH", pre["Access-Control-Allow-Methods"])
        self.assertEqual(self.get("branches")["Access-Control-Allow-Origin"], "*")
        self.assertEqual(HttpsClient().get(BASE + "branches")["Access-Control-Allow-Origin"], "*")  # errors too
        self.assertFalse(ApiRequestLog.objects.filter(method="OPTIONS").exists())

    def test_branches_and_scoping(self):
        ids = [b["id"] for b in self.get("branches").json()["data"]]
        self.assertEqual(sorted(ids), sorted([self.main.pk, self.dili.pk]))  # pending branch not listed
        limited, raw = ApiKey.generate(restaurant=self.main, name="Dili app")
        limited.branches.set([self.dili])
        c = HttpsClient(HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual([b["id"] for b in self.get("branches", c).json()["data"]], [self.dili.pk])
        self.assertEqual(self.get(f"menu?branch={self.main.pk}", c).json()["error"]["code"], "branch_not_allowed")
        self.assertEqual(self.get("menu", c).json()["branch"]["id"], self.dili.pk)  # only one branch: implied
        other, *_ = make_restaurant("other")
        self.assertEqual(self.get(f"menu?branch={other.pk}").status_code, 403)
        self.assertEqual(self.get(f"menu?branch={self.pending.pk}").status_code, 403)

    def test_menu(self):
        data = self.get(f"menu?branch={self.main.pk}").json()
        items = {i["name"]: i for c in data["categories"] for i in c["items"]}
        self.assertEqual(items["Chicken Fried Rice"]["price"], str(self.rice.price))
        self.assertEqual(items["Chicken Fried Rice"]["options"][0]["name"], "Fried Egg")

    def test_plan_subscription_and_profile_gating(self):
        sub = get_subscription(self.main)
        sub.plan = Plan.objects.get(name="Standard")
        sub.save()
        resp = self.get("branches")
        self.assertEqual((resp.status_code, resp.json()["error"]["code"]), (403, "plan_required"))
        self.assertIn("Pro", resp.json()["error"]["message"])
        make_pro(self.main)
        sub.refresh_from_db()
        sub.trial_ends_on, sub.paid_until = timezone.localdate() - timedelta(days=60), None
        sub.save()
        self.assertEqual(self.get("branches").json()["error"]["code"], "subscription_inactive")
        sub.paid_until = timezone.localdate() + timedelta(days=30)
        sub.save()
        self.main.business_profile.status = "review"
        self.main.business_profile.save()
        self.assertEqual(self.get("branches").json()["error"]["code"], "profile_not_verified")

    @override_settings(ORDERING_API_RATE_PER_MINUTE=3)
    def test_rate_limit_and_log(self):
        codes = [self.get("branches").status_code for _ in range(4)]
        self.assertEqual(codes, [200, 200, 200, 429])
        logs = ApiRequestLog.objects.filter(api_key=self.key)
        self.assertEqual(logs.count(), 4)
        self.assertEqual(logs.first().error, "rate_limited")
        self.key.refresh_from_db()
        self.assertIsNotNone(self.key.last_used_at)

    # ------------------------------------------------------------ orders

    def test_create_order_appears_in_branch(self):
        resp = self.order(note="Pick-up 12:30", external_ref="WEB-1")
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        o = Order.objects.get(public_token=body["id"])
        self.assertEqual((o.source, o.api_key, o.restaurant, o.external_ref), ("api", self.key, self.main, "WEB-1"))
        self.assertEqual(body["status"], "received")
        self.assertEqual(body["total"], str(o.total))
        self.assertTrue(Notification.objects.filter(order=o, title__contains="API · Website").exists())
        again = self.order(external_ref="WEB-1")  # retry: same order, no duplicate
        self.assertEqual((again.status_code, again.json()["id"]), (200, body["id"]))
        self.assertEqual(Order.objects.filter(source="api").count(), 1)

    def test_total_check_paid_and_errors(self):
        resp = self.order(total="1.00")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"]["code"], "total_mismatch")
        expected = resp.json()["error"]["expected_total"]
        ok = self.order(total=expected, paid=True)
        self.assertEqual(ok.status_code, 201)
        self.assertTrue(ok.json()["paid"])
        self.assertEqual(Order.objects.get(public_token=ok.json()["id"]).payment_status, PaymentStatus.PAID)
        self.tea.is_available = False
        self.tea.save()
        self.assertEqual(self.order().json()["error"]["code"], "unavailable")
        bad = self.send("post", "orders", {"branch": self.main.pk, "items": "x"})
        self.assertEqual(bad.json()["error"]["code"], "invalid_items")

    def test_status_flow_and_revenue(self):
        oid = self.order().json()["id"]
        for status in ("preparing", "ready"):
            self.assertEqual(self.send("patch", f"orders/{oid}", {"status": status}).json()["status"], status)
        self.assertFalse(RevenueEntry.objects.exists())  # not revenue until completed
        done = self.send("patch", f"orders/{oid}", {"status": "completed"}).json()
        self.assertEqual(done["status"], "completed")
        o = Order.objects.get(public_token=oid)
        e = RevenueEntry.objects.get(order=o)
        self.assertEqual((e.source, e.amount, e.restaurant, e.source_label), ("api", o.total, self.main, "API – Website"))
        today = timezone.localdate()
        t = report.totals(self.main, today, today)
        self.assertEqual((t["revenue"], t["api_revenue"], t["orders_revenue"]), (o.total, o.total, Decimal("0")))  # once
        self.assertEqual(report.totals(self.main, today, today, "manual")["revenue"], Decimal("0"))
        self.assertEqual(report.totals(self.main, today, today, f"key:{self.key.pk}")["revenue"], o.total)
        self.assertEqual(self.send("patch", f"orders/{oid}", {"status": "preparing"}).json()["error"]["code"],
                         "invalid_transition")
        # Cancelled orders never count.
        cid = self.order().json()["id"]
        self.assertEqual(self.send("patch", f"orders/{cid}", {"status": "cancelled", "reason": "No show"})
                         .json()["cancel_reason"], "No show")
        self.assertEqual(RevenueEntry.objects.count(), 1)

    def test_other_accounts_orders_are_invisible(self):
        oid = self.order().json()["id"]
        other, *_ = make_restaurant("other")
        make_pro(other)
        verify_profile(other)
        _, raw = ApiKey.generate(restaurant=other, name="Theirs")
        c = HttpsClient(HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(self.get(f"orders/{oid}", c).status_code, 404)
        self.assertEqual(self.send("patch", f"orders/{oid}", {"status": "cancelled"}, c).status_code, 404)
        self.assertEqual(self.get(f"orders/{oid}").json()["status"], "received")

    # ------------------------------------------------------------ sandbox

    def test_sandbox_key(self):
        _, raw = ApiKey.generate(restaurant=self.main, name="Test", is_sandbox=True)
        c = HttpsClient(HTTP_AUTHORIZATION=f"Bearer {raw}")
        resp = self.send("post", "orders", {"branch": self.main.pk, "items": [{"menu_item": self.tea.pk, "quantity": 1}]}, c)
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.json()["sandbox"])
        self.assertFalse(Order.objects.exists())  # nothing for the kitchen
        oid = resp.json()["id"]
        for status in ("preparing", "ready", "completed"):
            self.send("patch", f"orders/{oid}", {"status": status}, c)
        self.assertEqual(self.get(f"orders/{oid}", c).json()["status"], "completed")
        self.assertEqual(SandboxOrder.objects.get().status, "completed")
        self.assertFalse(RevenueEntry.objects.exists())

    # ------------------------------------------------------------ webhooks

    def test_webhooks_signed_and_filtered(self):
        sent = []

        class Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_open(req, timeout):
            sent.append(req)
            return Resp()

        hook = Webhook.objects.create(restaurant=self.main, url="https://hooks.example.com/qr")
        with mock.patch.object(webhooks, "check_url"), mock.patch.object(webhooks._opener, "open", side_effect=fake_open), \
                self.captureOnCommitCallbacks(execute=True):
            oid = self.order().json()["id"]
            self.send("patch", f"orders/{oid}", {"status": "preparing"})
            # A QR order is not sent unless the webhook asks for all orders.
            from apps.orders.services import place_order
            place_order(restaurant=self.main, table=None, lines=[{"menu_item": self.tea.pk, "quantity": 1}])
        self.assertEqual([r.get_header("X-qrmenu-event") for r in sent], ["order.created", "order.status_changed"])
        req = sent[1]
        body = req.data
        self.assertEqual(json.loads(body)["data"]["status"], "preparing")
        ts = req.get_header("X-qrmenu-timestamp")
        self.assertEqual(req.get_header("X-qrmenu-signature"), webhooks.sign(hook.secret, ts, body))
        self.assertEqual(hook.deliveries.count(), 2)
        hook.refresh_from_db()
        self.assertEqual(hook.last_status_code, 200)

    def test_webhook_url_must_be_public_https(self):
        self.assertFalse(WebhookForm({"url": "http://example.com/x"}).is_valid())
        self.assertFalse(WebhookForm({"url": "https://127.0.0.1/x"}).is_valid())
        self.assertFalse(WebhookForm({"url": "https://10.0.0.5/x"}).is_valid())

    # ------------------------------------------------------------ dashboard

    def test_dashboard_keys(self):
        self.client.force_login(self.owner)
        url = reverse("dashboard:api", args=["atsabe"])
        resp = self.client.post(url, {"action": "create_key", "name": "Delivery partner", "branches": [self.dili.pk]},
                                follow=True)
        key = ApiKey.objects.get(name="Delivery partner")
        raw = resp.context["new_key"]["raw"]
        self.assertTrue(raw.startswith("qrm_live_") and raw.endswith(key.last4))
        self.assertContains(resp, raw)
        again = self.client.get(url)
        self.assertNotContains(again, raw)  # shown only once
        self.assertContains(again, key.masked)
        self.assertEqual(list(key.branches.all()), [self.dili])
        self.client.post(url, {"action": "rename_key", "key": key.pk, f"k{key.pk}-name": "Foodpanda"})
        key.refresh_from_db()
        self.assertEqual((key.name, key.branches.count()), ("Foodpanda", 0))
        self.client.post(url, {"action": "revoke_key", "key": key.pk})
        key.refresh_from_db()
        self.assertFalse(key.is_active)
        self.assertEqual(self.client.get(reverse("dashboard:api_docs", args=["atsabe"])).status_code, 200)
        # Sub-branches: owner is sent to the main branch; branch staff can't see keys at all.
        self.assertRedirects(self.client.get(reverse("dashboard:api", args=[self.dili.slug])), url,
                             fetch_redirect_response=False)
        mgr = Client()
        mgr.force_login(staff(self.dili, "mgr", Role.MANAGER))
        self.assertEqual(mgr.get(reverse("dashboard:api", args=[self.dili.slug])).status_code, 403)

    def test_locked_without_pro(self):
        r, *_ = make_restaurant("plain")
        self.client.force_login(staff(r, "plain-owner", Role.OWNER))
        resp = self.client.get(reverse("dashboard:api", args=["plain"]))
        self.assertEqual(resp.status_code, 403)
        self.assertContains(resp, "Upgrade to Pro", status_code=403)

    def test_revenue_page_shows_source_and_protects_api_entries(self):
        oid = self.order().json()["id"]
        for s in ("preparing", "ready", "completed"):
            self.send("patch", f"orders/{oid}", {"status": s})
        e = RevenueEntry.objects.get()
        self.client.force_login(self.owner)
        page = self.client.get(reverse("dashboard:revenue", args=["atsabe"]))
        self.assertContains(page, "API – Website")
        self.assertEqual(self.client.post(reverse("dashboard:revenue_entry_delete", args=["atsabe", e.pk])).status_code, 404)
        branches = self.client.get(reverse("dashboard:branches", args=["atsabe"]) + "?source=manual")
        self.assertEqual(branches.context["total"]["day"]["revenue"], Decimal("0"))
        self.assertEqual(self.client.get(reverse("dashboard:branches", args=["atsabe"]) + "?source=api")
                         .context["total"]["day"]["revenue"], e.amount)
