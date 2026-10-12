"""Home page "call me back" form for restaurant owners, and the console list."""
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.console.models import CallbackRequest

DATA = {"name": "Maria", "restaurant_name": "Warung Maria", "phone": "7712 3456", "best_time": "morning",
        "setup_help": "on"}


@override_settings(ADMINS=[("Platform", "admin@example.test")])
class CallbackTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_form_on_home_page_and_submit(self):
        home = self.client.get("/")
        self.assertContains(home, 'id="call-me"')
        self.assertContains(home, "Call me back")
        resp = self.client.post(reverse("storefront:call_me_back"), DATA)
        self.assertRedirects(resp, "/?callback=sent#call-me", fetch_redirect_response=False)
        cb = CallbackRequest.objects.get()
        self.assertEqual((cb.restaurant_name, cb.status, cb.setup_help, cb.whatsapp_number),
                         ("Warung Maria", "new", True, "67077123456"))
        self.assertIn("Warung Maria", mail.outbox[-1].subject)
        self.assertIn("https://wa.me/67077123456", mail.outbox[-1].body)
        self.assertContains(self.client.get("/?callback=sent"), "We will call you soon")

    def test_validation_honeypot_and_limit(self):
        bad = self.client.post(reverse("storefront:call_me_back"), {**DATA, "phone": "abc"})
        self.assertEqual(bad.status_code, 400)
        self.assertContains(bad, "Enter a phone number", status_code=400)
        bot = self.client.post(reverse("storefront:call_me_back"), {**DATA, "website": "http://spam"})
        self.assertEqual(bot.status_code, 400)
        self.assertFalse(CallbackRequest.objects.exists())
        for _ in range(5):
            self.client.post(reverse("storefront:call_me_back"), DATA)
        limited = self.client.post(reverse("storefront:call_me_back"), DATA)
        self.assertEqual(limited["Location"], "/?callback=limit#call-me")
        self.assertEqual(CallbackRequest.objects.count(), 5)

    def test_console_list_and_update(self):
        self.client.post(reverse("storefront:call_me_back"), {**DATA, "phone": "+62 812 3456 7890"})
        cb = CallbackRequest.objects.get()
        self.assertEqual(cb.whatsapp_number, "6281234567890")  # foreign number kept as is
        admin = get_user_model().objects.create_superuser("platform", "a@example.test", "x-Admin-pass-2026")
        c = Client()
        c.force_login(admin)
        self.assertEqual(c.get(reverse("console:overview")).context["callbacks_new"], 1)
        page = c.get(reverse("console:callbacks"))
        self.assertContains(page, "https://wa.me/6281234567890")
        c.post(reverse("console:callbacks"), {"pk": cb.pk, "status": "registered", "notes": "Signed up Tuesday"})
        cb.refresh_from_db()
        self.assertEqual((cb.status, cb.notes, cb.handled_by), ("registered", "Signed up Tuesday", admin))
        self.assertEqual(Client().get(reverse("console:callbacks")).status_code, 302)

    def test_hidden_for_logged_in_users(self):
        from apps.core.permissions import Role

        from .test_ordering import make_restaurant, staff
        r, *_ = make_restaurant()
        self.client.force_login(staff(r, "owner", Role.OWNER))
        self.assertNotContains(self.client.get("/"), 'id="call-me"')
