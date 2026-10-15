import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.console.models import DemoRequest
from apps.core.permissions import Role
from apps.storefront.demo import DEMO_USERNAME, SESSION_KEY

from .test_ordering import make_pro, make_restaurant, staff

FORM = {"name": "Maria Soares", "job_title": "Restaurant owner", "address": "Rua de Comoro, Dili",
        "email": "Maria@Example.com"}


@override_settings(DEMO_RESTAURANT="alpha")
class DemoTests(TestCase):
    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        make_pro(self.r)
        self.url = reverse("storefront:demo")

    def request_link(self):
        resp = self.client.post(self.url, FORM)
        self.assertRedirects(resp, self.url + "?status=sent")
        return re.search(r"https?://\S+/demo/\S+/", mail.outbox[0].body).group(0)

    def open_demo(self):
        link = self.request_link()
        resp = self.client.get(link)
        self.assertRedirects(resp, reverse("dashboard:overview", args=["alpha"]))
        return link

    def test_form_needs_every_field_and_a_valid_email(self):
        self.assertContains(self.client.get(self.url), "Email me the demo link")
        resp = self.client.post(self.url, {**FORM, "email": "not-an-email", "job_title": ""})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(DemoRequest.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_bots_filling_the_hidden_field_are_ignored(self):
        self.client.post(self.url, {**FORM, "website": "spam"})
        self.assertFalse(DemoRequest.objects.exists())

    def test_request_is_saved_and_the_link_is_emailed(self):
        link = self.request_link()
        d = DemoRequest.objects.get()
        self.assertEqual((d.name, d.job_title, d.address, d.email), ("Maria Soares", "Restaurant owner",
                                                                      "Rua de Comoro, Dili", "maria@example.com"))
        self.assertEqual(mail.outbox[0].to, ["maria@example.com"])
        self.assertIn(d.token, link)
        self.assertIn(d.token, mail.outbox[0].alternatives[0][0])  # HTML version with the button
        self.assertGreater(d.expires_at, timezone.now() + timedelta(days=6))

    def test_link_opens_the_demo_dashboard_view_only(self):
        self.open_demo()
        d = DemoRequest.objects.get()
        self.assertEqual(d.opened_count, 1)
        demo_user = get_user_model().objects.get(username=DEMO_USERNAME)
        self.assertFalse(demo_user.has_usable_password())
        self.assertEqual(self.r.staff_role(demo_user), Role.DEMO)

        home = self.client.get(reverse("dashboard:overview", args=["alpha"]))
        self.assertContains(home, "Demo, view only.")
        for name in ("menu", "orders", "kitchen", "quotations", "reports"):
            self.assertEqual(self.client.get(reverse(f"dashboard:{name}", args=["alpha"])).status_code, 200, name)
        for name in ("staff", "billing", "api", "business_profile"):  # owner-only pages stay closed
            self.assertIn(self.client.get(reverse(f"dashboard:{name}", args=["alpha"])).status_code, (403, 404), name)

    def test_demo_visitors_cannot_change_anything(self):
        self.open_demo()
        url = reverse("dashboard:quotation_profile", args=["alpha"])
        resp = self.client.post(url, {"company_profile": "Hacked"}, HTTP_REFERER="http://testserver" + url)
        self.assertRedirects(resp, url)
        self.r.refresh_from_db()
        self.assertEqual(self.r.company_profile, "")
        self.assertContains(self.client.get(url), "changes are not saved")
        # fetch / API calls get a JSON answer instead
        resp = self.client.post(reverse("dashboard:menu", args=["alpha"]), {}, HTTP_ACCEPT="application/json")
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(resp.json()["demo"])
        # logging out still works
        self.client.post(reverse("accounts:logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_expired_link_and_expired_session(self):
        link = self.request_link()
        DemoRequest.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertRedirects(self.client.get(link), self.url + "?status=expired")
        self.assertRedirects(self.client.get("/demo/not-a-token/"), self.url + "?status=expired")

        DemoRequest.objects.update(expires_at=timezone.now() + timedelta(days=1))
        self.client.get(link)
        session = self.client.session
        session[SESSION_KEY] = (timezone.now() - timedelta(seconds=1)).timestamp()
        session.save()
        resp = self.client.get(reverse("dashboard:overview", args=["alpha"]))
        self.assertRedirects(resp, self.url + "?status=expired")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_too_many_requests_from_one_address(self):
        for _ in range(3):
            self.client.post(self.url, FORM)
        self.assertEqual(self.client.post(self.url, FORM).status_code, 429)
        self.assertEqual(DemoRequest.objects.count(), 3)

    def test_demo_user_is_hidden_from_the_owners_staff_page(self):
        self.open_demo()
        owner = staff(self.r, "owner", Role.OWNER)
        self.client.force_login(owner)
        self.assertNotContains(self.client.get(reverse("dashboard:staff", args=["alpha"])), DEMO_USERNAME)

    def test_console_lists_demo_requests(self):
        self.request_link()
        admin = get_user_model().objects.create_superuser("boss", "boss@example.com", "pw-Secret-123")
        self.client.force_login(admin)
        resp = self.client.get(reverse("console:demo_requests"))
        self.assertContains(resp, "Maria Soares")
        self.assertContains(resp, "maria@example.com")

    @override_settings(DEMO_RESTAURANT="")
    def test_no_demo_configured(self):
        self.assertContains(self.client.get(self.url), "The demo is not available right now.")
