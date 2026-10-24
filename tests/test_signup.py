import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.accounts import signup as signup_service
from apps.core.permissions import Role
from apps.menu.models import MenuCategory, MenuItem
from apps.payments.drawer import open_cash_drawer
from apps.restaurants.models import Restaurant, RestaurantStaff, Table

from .test_ordering import make_restaurant, staff

User = get_user_model()

SIGNUP = {
    "restaurant_name": "Warung Sederhana",
    "full_name": "Budi Santoso",
    "email": "Budi@Example.com",
    "phone": "+670 7712 3456",
    "password": "a-Strong-pass-2024",
    "accept": "on",
    "website": "",
}


def verify_link_from_outbox():
    body = mail.outbox[-1].body
    return re.search(r"http://testserver(/accounts/verify/[^/\s]+/)", body).group(1)


def code_from_outbox():
    return re.search(r"^\s+(\d{6})\s*$", mail.outbox[-1].body, re.M).group(1)


def legacy_unverified_owner():
    """An owner from before sign-up codes: account exists, email not confirmed yet."""
    return signup_service.register_restaurant(restaurant_name="Warung Lama", full_name="Ana Lama",
                                              email="ana@example.com", password="a-Strong-pass-2024")


class SignupTests(TestCase):
    def setUp(self):
        cache.clear()

    def signup(self, client=None, complete=True, **overrides):
        """Fill in the form and, unless complete=False, enter the emailed code."""
        client = client or self.client
        resp = client.post(reverse("accounts:signup"), {**SIGNUP, **overrides})
        if complete and resp.status_code == 302 and resp["Location"] == reverse("accounts:signup_verify"):
            resp = client.post(reverse("accounts:signup_verify"), {"code": code_from_outbox()})
        return resp

    def test_no_account_until_the_emailed_code_is_entered(self):
        resp = self.signup(complete=False)
        self.assertRedirects(resp, reverse("accounts:signup_verify"))
        self.assertFalse(User.objects.exists())
        self.assertFalse(Restaurant.objects.exists())
        self.assertEqual(mail.outbox[0].to, ["budi@example.com"])
        code = code_from_outbox()
        self.assertIn(code, mail.outbox[0].subject)
        page = self.client.get(reverse("accounts:signup_verify"))
        self.assertContains(page, "budi@example.com")
        self.assertNotContains(page, code)  # the code is only in the email

        resp = self.client.post(reverse("accounts:signup_verify"), {"code": code})
        restaurant = Restaurant.objects.get()
        self.assertRedirects(resp, reverse("dashboard:overview", args=[restaurant.slug]), fetch_redirect_response=False)
        self.assertEqual(restaurant.slug, "warung-sederhana")
        self.assertTrue(restaurant.is_active)  # open sign-up: live once the email is proven
        user = User.objects.get()
        self.assertEqual((user.email, user.username, user.first_name), ("budi@example.com", "budi@example.com", "Budi"))
        self.assertTrue(user.email_verified)
        self.assertTrue(user.check_password(SIGNUP["password"]))
        self.assertEqual(RestaurantStaff.objects.get().role, Role.OWNER)
        page = self.client.get(reverse("dashboard:overview", args=[restaurant.slug]))
        self.assertContains(page, "Get your restaurant ready")
        self.assertNotContains(page, "Confirm your email to go live")
        self.assertEqual(Client().get(reverse("storefront:menu", args=[restaurant.slug])).status_code, 200)

    def test_wrong_codes_then_locked(self):
        self.signup(complete=False)
        url = reverse("accounts:signup_verify")
        for _ in range(4):
            self.assertContains(self.client.post(url, {"code": "000000"}), "not right", status_code=400)
        resp = self.client.post(url, {"code": "000000"})
        self.assertRedirects(resp, reverse("accounts:signup"))
        self.assertFalse(User.objects.exists())
        # the right code no longer works either: the sign-up has to start again
        self.assertRedirects(self.client.post(url, {"code": code_from_outbox()}), reverse("accounts:signup"))
        self.assertFalse(User.objects.exists())

    def test_expired_code(self):
        self.signup(complete=False)
        session = self.client.session
        pending = session[signup_service.PENDING_KEY]
        pending["expires"] = 0
        session[signup_service.PENDING_KEY] = pending
        session.save()
        resp = self.client.post(reverse("accounts:signup_verify"), {"code": code_from_outbox()})
        self.assertContains(resp, "expired", status_code=400)
        self.assertFalse(User.objects.exists())

    def test_resend_waits_a_minute_and_is_limited(self):
        self.signup(complete=False)
        url = reverse("accounts:signup_verify")
        self.assertContains(self.client.post(url, {"action": "resend"}), "Please wait", status_code=400)
        old = code_from_outbox()
        for _ in range(2):
            session = self.client.session
            pending = session[signup_service.PENDING_KEY]
            pending["sent_at"] = 0
            session[signup_service.PENDING_KEY] = pending
            session.save()
            self.assertRedirects(self.client.post(url, {"action": "resend"}), url)
        self.assertEqual(len(mail.outbox), 3)
        self.assertContains(self.client.post(url, {"action": "resend"}), "No more codes", status_code=400)
        new = code_from_outbox()
        if new != old:  # an earlier code stops working once a new one is sent
            self.assertContains(self.client.post(url, {"code": old}), "not right", status_code=400)
        self.client.post(url, {"code": new})
        self.assertTrue(User.objects.get().email_verified)

    def test_code_page_needs_the_form_first(self):
        self.assertRedirects(self.client.get(reverse("accounts:signup_verify")), reverse("accounts:signup"))

    def test_codes_per_email_are_limited(self):
        for _ in range(3):
            self.signup(Client(), complete=False)
        resp = self.signup(Client(), complete=False)
        self.assertContains(resp, "Too many codes")
        self.assertEqual(len(mail.outbox), 3)

    def test_email_taken_while_waiting_for_the_code(self):
        self.signup(complete=False)
        mine = code_from_outbox()
        self.signup(Client())  # same email finished first in another browser
        resp = self.client.post(reverse("accounts:signup_verify"), {"code": mine})
        self.assertRedirects(resp, reverse("accounts:login"), fetch_redirect_response=False)
        self.assertEqual(User.objects.count(), 1)

    @override_settings(SIGNUP_MODE="approval")
    def test_not_public_until_approved_but_owner_can_preview(self):
        self.signup()
        r = Restaurant.objects.get()
        Table.objects.create(restaurant=r, number="1")
        self.assertEqual(Client().get(reverse("storefront:menu", args=[r.slug])).status_code, 404)
        self.assertContains(self.client.get(reverse("storefront:menu", args=[r.slug])), "Preview")

    def test_legacy_verification_link_publishes_restaurant(self):
        user, _r = legacy_unverified_owner()
        link = reverse("accounts:verify_email", args=[signup_service.make_verify_token(user)])
        resp = self.client.get(link, follow=True)
        self.assertContains(resp, "now live")
        self.assertTrue(Restaurant.objects.get().is_active)

    def test_login_with_email_case_insensitive(self):
        self.signup()
        client = Client()
        resp = client.post(reverse("accounts:login"), {"username": "BUDI@example.com", "password": SIGNUP["password"]})
        self.assertEqual(resp.status_code, 302)

    def test_tampered_or_other_token_rejected(self):
        user, _r = legacy_unverified_owner()
        link = reverse("accounts:verify_email", args=[signup_service.make_verify_token(user)])
        self.client.get(link[:-3] + "xx/", follow=True)
        self.assertFalse(Restaurant.objects.get().is_active)

    def test_token_invalid_after_email_change(self):
        user, _r = legacy_unverified_owner()
        link = reverse("accounts:verify_email", args=[signup_service.make_verify_token(user)])
        User.objects.update(email="other@example.com")
        self.client.get(link)
        self.assertFalse(Restaurant.objects.get().is_active)

    def test_duplicate_email_honeypot_and_weak_password(self):
        self.signup()
        other = Client()
        self.assertContains(self.signup(other, email="budi@example.com"), "already exists")
        self.assertContains(self.signup(other, email="x@example.com", website="http://spam"), "Sign-up failed")
        self.assertContains(self.signup(other, email="y@example.com", password="123"), "too short")
        self.assertEqual(Restaurant.objects.count(), 1)

    def test_duplicate_names_get_unique_slugs(self):
        self.signup()
        self.signup(Client(), email="second@example.com")
        self.signup(Client(), email="third@example.com", restaurant_name="Admin")
        self.assertEqual(
            sorted(Restaurant.objects.values_list("slug", flat=True)),
            ["admin-restaurant", "warung-sederhana", "warung-sederhana-2"],
        )

    @override_settings(SIGNUPS_PER_IP_PER_HOUR=2)
    def test_signup_rate_limited_per_ip(self):
        for i in range(3):
            resp = self.signup(Client(), email=f"o{i}@example.com")
        self.assertContains(resp, "Too many sign-ups")
        self.assertEqual(Restaurant.objects.count(), 2)

    @override_settings(SIGNUP_MODE="closed")
    def test_closed_signup(self):
        self.assertEqual(self.client.get(reverse("accounts:signup")).status_code, 404)

    @override_settings(SIGNUP_MODE="approval", ADMINS=[("Ops", "ops@platform.test")])
    def test_approval_mode(self):
        self.signup()
        r = Restaurant.objects.get()
        self.assertFalse(r.is_active)
        self.assertTrue(any("waiting for approval" in m.subject.lower() for m in mail.outbox))
        page = self.client.get(reverse("dashboard:overview", args=[r.slug]))
        self.assertContains(page, "Waiting for approval")
        # Platform admin approves via the admin action.
        admin = User.objects.create_superuser("root", "root@platform.test", "root-Pass-123")
        self.client.force_login(admin)
        self.client.post(reverse("admin:restaurants_restaurant_changelist"),
                         {"action": "approve", "_selected_action": [r.pk]})
        r.refresh_from_db()
        self.assertTrue(r.is_active)

    def test_resend_verification_is_throttled_and_safe_redirect(self):
        user, _r = legacy_unverified_owner()
        self.client.force_login(user)
        url = reverse("accounts:resend_verification")
        resp = self.client.post(url, {"next": "https://evil.example/"})
        self.assertEqual(resp["Location"], reverse("dashboard:home"))
        for _ in range(4):
            self.client.post(url)
        self.assertEqual(len(mail.outbox), 3)  # 3 resends, then throttled

    def test_password_reset_flow(self):
        self.signup()
        mail.outbox.clear()
        client = Client()
        client.post(reverse("accounts:password_reset"), {"email": "budi@example.com"})
        self.assertEqual(len(mail.outbox), 1)
        path = re.search(r"http://testserver(\S+)", mail.outbox[0].body).group(1)
        resp = client.get(path, follow=True)
        form_url = resp.redirect_chain[-1][0]
        client.post(form_url, {"new_password1": "Brand-new-pass-77", "new_password2": "Brand-new-pass-77"})
        self.assertTrue(client.login(username="budi@example.com", password="Brand-new-pass-77"))
        # Unknown emails show the same page and send nothing.
        mail.outbox.clear()
        resp = client.post(reverse("accounts:password_reset"), {"email": "nobody@example.com"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)


class PlatformSafetyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.r, self.rice, *_ = make_restaurant()
        self.owner = staff(self.r, "owner", Role.OWNER)
        self.client.force_login(self.owner)

    def test_per_restaurant_limits(self):
        # Limits now come from the restaurant's subscription plan.
        from apps.billing.services import get_subscription
        plan = get_subscription(self.r).plan
        plan.max_tables, plan.max_menu_items = 3, 2
        plan.save()
        url = reverse("dashboard:tables", args=["alpha"])
        resp = self.client.post(url, {"action": "bulk", "start": "1", "end": "10"}, follow=True)
        self.assertContains(resp, "Limit reached")
        self.assertEqual(Table.objects.filter(restaurant=self.r).count(), 1)  # only the fixture table
        resp = self.client.get(reverse("dashboard:item_create", args=["alpha"]), follow=True)
        self.assertContains(resp, "Limit reached")

    @override_settings(CASH_DRAWER_NETWORK_ENABLED=False)
    def test_drawer_disabled_on_public_deployments(self):
        self.r.cash_drawer_enabled, self.r.printer_host = True, "192.168.1.50"
        self.r.save()
        self.assertFalse(open_cash_drawer(self.r).attempted)
        page = self.client.get(reverse("dashboard:settings", args=["alpha"]))
        self.assertNotContains(page, "printer_host")
        self.assertNotContains(page, reverse("dashboard:drawer_open", args=["alpha"]))  # no "Open drawer" button

    def test_not_live_restaurant_staff_tools_work_but_no_orders(self):
        self.r.is_active = False
        self.r.save()
        api = reverse("dashboard:api_item_availability", args=["alpha", self.rice.pk])
        self.assertEqual(self.client.post(api, {"is_available": False}, content_type="application/json").status_code, 200)
        order_api = reverse("storefront:api_place_order", args=["alpha"])
        resp = Client().post(order_api, {"items": [{"menu_item": self.rice.pk, "quantity": 1}]},
                             content_type="application/json")
        self.assertEqual(resp.status_code, 404)

    def test_staff_email_cannot_reuse_owner_email(self):
        self.owner.email = "owner@example.com"
        self.owner.save()
        resp = self.client.post(reverse("dashboard:staff", args=["alpha"]), {
            "username": "newbie", "email": "OWNER@example.com", "role": Role.WAITER, "password": "a-Strong-pass-2024",
        })
        self.assertContains(resp, "already exists")

    def test_forms_render_their_fields(self):
        """Regression: forms without translation fields used to render no inputs at all."""
        for name, field in [("staff", 'name="username"'), ("tables", 'name="number"')]:
            self.assertContains(self.client.get(reverse(f"dashboard:{name}", args=["alpha"])), field)
        self.assertContains(self.client.get(reverse("accounts:password_change")), 'name="old_password"')
        self.assertContains(Client().get(reverse("accounts:password_reset")), 'name="email"')

    def test_landing_page(self):
        resp = Client().get("/")
        self.assertContains(resp, "Register your restaurant")
        self.assertContains(resp, reverse("accounts:signup"))
