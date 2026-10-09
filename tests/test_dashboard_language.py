from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.core.permissions import Role
from apps.orders.services import place_order

from .test_ordering import make_pro, make_restaurant, staff


class DashboardLanguageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        self.waiter = staff(self.r, "waiter", Role.WAITER)
        self.client.force_login(self.waiter)

    def use(self, lang):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = lang

    def test_english_by_default(self):
        resp = self.client.get(reverse("dashboard:orders", args=["alpha"]))
        self.assertContains(resp, '<html lang="en">')
        self.assertContains(resp, "Every order from QR codes and waiters.")

    def test_each_language_translates_the_dashboard(self):
        for lang, text in [
            ("pt", "Todos os pedidos dos códigos QR e dos empregados."),
            ("tet", "Pedidu hotu husi kódigu QR no serbidór sira."),
            ("id", "Semua pesanan dari kode QR dan pelayan."),
        ]:
            with self.subTest(lang=lang):
                self.use(lang)
                resp = self.client.get(reverse("dashboard:orders", args=["alpha"]))
                self.assertContains(resp, f'<html lang="{lang}">')
                self.assertContains(resp, text)

    def test_switcher_sets_the_cookie_and_returns_to_the_page(self):
        page = reverse("dashboard:orders", args=["alpha"])
        resp = self.client.post(reverse("set_language"), {"language": "tet", "next": page})
        self.assertRedirects(resp, page, fetch_redirect_response=False)
        self.assertEqual(resp.cookies[settings.LANGUAGE_COOKIE_NAME].value, "tet")
        self.assertContains(self.client.get(page), "Pedidu foun")

    def test_unknown_language_falls_back_to_english(self):
        self.use("xx")
        self.assertContains(self.client.get(reverse("dashboard:orders", args=["alpha"])), '<html lang="en">')

    def test_messages_and_status_names_are_translated(self):
        order = place_order(restaurant=self.r, table=self.table, lines=[{"menu_item": self.tea.id, "quantity": 2}])
        self.use("pt")
        resp = self.client.post(
            reverse("dashboard:order_mark_paid", args=["alpha", order.pk]), {"tendered": "20"}, follow=True
        )
        self.assertContains(resp, "DAR TROCO")
        # The "give change" pop-up reads plain amounts, whatever the language.
        self.assertContains(resp, 'data-change-amounts="')
        resp = self.client.get(reverse("dashboard:order_detail", args=["alpha", order.pk]))
        self.assertContains(resp, "Pago")

    def test_javascript_catalog_follows_the_language(self):
        self.use("id")
        resp = self.client.get(reverse("javascript-catalog"))
        self.assertContains(resp, "BERI KEMBALIAN")

    def test_customer_menu_keeps_its_own_language(self):
        self.use("pt")  # staff language cookie on the same device
        resp = self.client.get(reverse("storefront:menu", args=["alpha"]))
        self.assertNotContains(resp, 'lang="pt"')


class DashboardPagesRenderInEveryLanguageTests(TestCase):
    def test_every_page_renders(self):
        cache.clear()
        r, rice, egg, tea, table = make_restaurant()
        make_pro(r)
        owner = staff(r, "owner", Role.OWNER)
        order = place_order(restaurant=r, table=table, lines=[{"menu_item": rice.id, "quantity": 1}])
        pages = [reverse("dashboard:home"), reverse("accounts:password_change")] + [
            reverse(f"dashboard:{name}", args=["alpha", *extra]) for name, extra in [
                ("overview", []), ("kitchen", []), ("orders", []), ("new_order", []), ("tables", []),
                ("table_detail", [table.pk]), ("table_edit", [table.pk]), ("order_detail", [order.pk]),
                ("menu", []), ("item_create", []), ("item_edit", [rice.pk]), ("category_create", []),
                ("category_edit", [rice.category_id]), ("settings", []), ("staff", []), ("reports", []),
                ("notifications", []), ("billing", []), ("cash_register", []), ("analytics", []),
                ("quotations", []), ("quotation_create", []), ("expenses", []), ("branches", []), ("revenue", []),
            ]
        ]
        for lang, _name in settings.LANGUAGES:
            self.client.logout()  # also clears cookies
            self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = lang
            with self.subTest(lang=lang, page="login"):
                self.assertContains(self.client.get(reverse("accounts:login")), f'<html lang="{lang}">')
            self.client.force_login(owner)
            for url in pages:
                with self.subTest(lang=lang, page=url):
                    self.assertContains(self.client.get(url, follow=True), f'<html lang="{lang}">')


class LandingPageLanguageTests(TestCase):
    def test_landing_page_has_switcher_after_log_in_and_follows_language(self):
        resp = self.client.get("/")
        html = resp.content.decode()
        self.assertLess(html.index(reverse("accounts:login")), html.index('class="land-lang"'))
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "id"
        resp = self.client.get("/")
        self.assertContains(resp, '<html lang="id">')
        self.assertContains(resp, "Scan. Pesan. Nikmati.")


class LandingPageSignedInTests(TestCase):
    def test_signed_in_user_sees_dashboard_link_not_log_in(self):
        r, *_ = make_restaurant()
        self.client.force_login(staff(r, "owner", Role.OWNER))
        resp = self.client.get("/")
        self.assertContains(resp, reverse("dashboard:home"))
        self.assertContains(resp, "Open dashboard")
        self.assertNotContains(resp, reverse("accounts:login"))


class LandingPagePricingTests(TestCase):
    def test_public_plans_are_listed(self):
        from apps.billing.models import Plan

        Plan.objects.create(name="Starter", price_monthly=0, max_tables=5)
        Plan.objects.create(name="Business", price_monthly="12.50", max_tables=40)
        Plan.objects.create(name="Secret", price_monthly=99, is_public=False)
        resp = self.client.get("/")
        self.assertContains(resp, 'id="pricing"')
        self.assertContains(resp, "Starter")
        self.assertContains(resp, "$12.50")
        self.assertContains(resp, "Up to 40 tables")
        self.assertNotContains(resp, "Secret")


class LandingPageWhatsAppTests(TestCase):
    def test_whatsapp_link_with_greeting(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "pt"
        resp = self.client.get("/")
        self.assertContains(resp, f"https://wa.me/{settings.SUPPORT_WHATSAPP}?text=Ol%C3%A1", count=2)
        self.assertContains(resp, 'class="wa-float"', count=1)


class NumbersInMarkupIgnoreLanguageTests(TestCase):
    """pt/id write decimals with a comma; CSS and data-* attributes must still get a dot."""

    def test_css_and_data_attributes_use_a_dot(self):
        import re

        from apps.payments.models import CashSession

        cache.clear()
        r, rice, egg, tea, table = make_restaurant()
        make_pro(r)
        owner = staff(r, "owner", Role.OWNER)
        o = place_order(restaurant=r, table=table, lines=[{"menu_item": rice.id, "quantity": 1}, {"menu_item": tea.id, "quantity": 1}])
        CashSession.objects.create(restaurant=r, opening_float="10.50")
        self.client.force_login(owner)
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "pt"
        for url in (reverse("dashboard:analytics", args=["alpha"]), reverse("dashboard:cash_register", args=["alpha"]),
                    reverse("dashboard:orders", args=["alpha"]), reverse("dashboard:order_detail", args=["alpha", o.pk])):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertFalse(re.findall(r'(?:width|flex): ?\d+,\d', html))
                self.assertFalse(re.findall(r'data-(?:expected|cash-due|cash-paid|price)="\d+,\d', html))
        self.assertContains(self.client.get(reverse("dashboard:cash_register", args=["alpha"])), 'data-expected="10.50"')
