"""Cash drawer through the cashier computer's printer (works on the cloud, no network printing)."""
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.permissions import Role
from apps.orders.services import place_order
from apps.payments.drawer import drawer_mode
from apps.payments.models import CashDrawerOpening

from .test_ordering import make_restaurant, staff


@override_settings(CASH_DRAWER_NETWORK_ENABLED=False)
class PrinterDrawerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        self.r.cash_drawer_via_printer = True
        self.r.save()
        self.client.force_login(staff(self.r, "waiter", Role.WAITER))

    def order(self):
        return place_order(restaurant=self.r, table=self.table, lines=[{"menu_item": self.tea.id, "quantity": 2}])

    def test_mode(self):
        self.assertEqual(drawer_mode(self.r), "printer")
        self.r.cash_drawer_via_printer = False
        self.assertEqual(drawer_mode(self.r), "")

    def test_cash_payment_prints_a_slip_and_logs_it(self):
        o = self.order()
        resp = self.client.post(reverse("dashboard:order_mark_paid", args=["alpha", o.pk]), {"tendered": "5"}, follow=True)
        self.assertContains(resp, f'data-drawer-slip="payment:{o.pk}"')
        log = CashDrawerOpening.objects.get()
        self.assertEqual((log.reason, log.order_id, log.success), ("payment", o.pk, True))

    def test_no_sale_button_and_slip_page(self):
        overview = self.client.get(reverse("dashboard:overview", args=["alpha"]))
        self.assertContains(overview, reverse("dashboard:drawer_open", args=["alpha"]))
        resp = self.client.post(reverse("dashboard:drawer_open", args=["alpha"]), follow=True)
        self.assertContains(resp, 'data-drawer-slip="no_sale:"')
        self.assertEqual(CashDrawerOpening.objects.get().reason, "no_sale")
        o = self.order()
        slip = self.client.get(reverse("dashboard:drawer_slip", args=["alpha"]) + f"?reason=payment&orders={o.pk}&autoprint=1")
        self.assertContains(slip, "CASH DRAWER")
        self.assertContains(slip, f"#{o.number}")
        self.assertEqual(slip.headers.get("X-Frame-Options"), "SAMEORIGIN")

    def test_kitchen_api_payment_asks_for_slip(self):
        o = self.order()
        url = reverse("dashboard:api_order_payment", args=["alpha", o.pk])
        data = self.client.post(url, {"method": "cash", "tendered": "3"}, content_type="application/json").json()
        self.assertTrue(data["drawer"]["print_slip"])

    def test_card_payment_does_not_open_drawer(self):
        o = self.order()
        self.client.post(reverse("dashboard:order_detail", args=["alpha", o.pk]),
                         {"action": "payment", "method": "card", "amount": "3.00", "reference": "x"})
        self.assertFalse(CashDrawerOpening.objects.exists())

    def test_settings_show_switch_guide_and_test(self):
        self.client.force_login(staff(self.r, "owner", Role.OWNER))
        page = self.client.get(reverse("dashboard:settings", args=["alpha"]))
        self.assertContains(page, 'name="cash_drawer_via_printer"')
        self.assertContains(page, "--kiosk-printing")
        resp = self.client.post(reverse("dashboard:drawer_test", args=["alpha"]), follow=True)
        self.assertContains(resp, 'data-drawer-slip="test:"')


@override_settings(CASH_DRAWER_NETWORK_ENABLED=False)
class NoDrawerTests(TestCase):
    def test_no_button_and_no_slip_without_drawer(self):
        cache.clear()
        r, rice, egg, tea, table = make_restaurant()
        self.client.force_login(staff(r, "waiter", Role.WAITER))
        self.assertNotContains(self.client.get(reverse("dashboard:overview", args=["alpha"])),
                               reverse("dashboard:drawer_open", args=["alpha"]))
        o = place_order(restaurant=r, table=table, lines=[{"menu_item": tea.id, "quantity": 1}])
        resp = self.client.post(reverse("dashboard:order_mark_paid", args=["alpha", o.pk]), {"tendered": ""}, follow=True)
        self.assertNotContains(resp, "data-drawer-slip")
