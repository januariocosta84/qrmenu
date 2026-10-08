from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.core.permissions import Role
from apps.orders.services import place_order
from apps.payments.receipts import escpos_receipt, receipt_data, receipt_orders

from .test_ordering import make_restaurant, staff


class VatTests(TestCase):
    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()

    def order(self):
        # 2 × Iced Tea = $3.00
        return place_order(restaurant=self.r, table=self.table, lines=[{"menu_item": self.tea.id, "quantity": 2}])

    def enable(self, percent, inclusive):
        self.r.vat_enabled, self.r.vat_percent, self.r.vat_inclusive = True, Decimal(percent), inclusive
        self.r.save()

    def test_off_by_default(self):
        o = self.order()
        self.assertFalse(self.r.vat_enabled)
        self.assertEqual((o.vat_amount, o.total), (Decimal("0"), Decimal("3.00")))
        self.assertEqual(o.vat_rate_label, "")

    def test_vat_added_on_top(self):
        self.enable("10", inclusive=False)
        o = self.order()
        self.assertEqual((o.vat_amount, o.total, o.vat_percent), (Decimal("0.30"), Decimal("3.30"), Decimal("10")))
        self.assertEqual(o.vat_rate_label, "VAT 10%")

    def test_vat_included_in_prices_keeps_total(self):
        self.enable("10", inclusive=True)
        o = self.order()
        self.assertEqual((o.vat_amount, o.total), (Decimal("0.27"), Decimal("3.00")))  # 3.00 × 10/110
        self.assertTrue(o.vat_inclusive)

    def test_vat_is_on_subtotal_plus_service_charge(self):
        self.r.service_charge_percent = Decimal("10")
        self.enable("10", inclusive=False)
        o = self.order()  # 3.00 + 0.30 service = 3.30 → VAT 0.33
        self.assertEqual((o.service_charge, o.vat_amount, o.total), (Decimal("0.30"), Decimal("0.33"), Decimal("3.63")))

    def test_enabled_with_zero_rate_charges_nothing(self):
        self.enable("0", inclusive=False)
        self.assertEqual(self.order().vat_amount, Decimal("0"))

    def test_past_orders_keep_their_vat(self):
        self.enable("10", inclusive=False)
        o = self.order()
        self.r.vat_enabled = False
        self.r.save()
        o.refresh_from_db()
        self.assertEqual(o.total, Decimal("3.30"))

    def test_receipts_show_vat_and_tax_id(self):
        self.r.vat_number = "TL-123456"
        self.r.vat_label = "IVA"
        self.enable("10", inclusive=True)
        o = self.order()
        data = receipt_data(self.r, receipt_orders(self.r, [o.pk]))
        self.assertEqual(data["vat_lines"], [{"label": "IVA 10% (incl.)", "amount": Decimal("0.27"), "inclusive": True}])
        printed = escpos_receipt(data, 48)
        self.assertIn(b"IVA 10% (incl.)", printed)
        self.assertIn(b"Tax ID TL-123456", printed)
        self.client.force_login(staff(self.r, "waiter", Role.WAITER))
        page = self.client.get(reverse("dashboard:receipt", args=["alpha"]) + f"?orders={o.pk}")
        self.assertContains(page, "IVA 10% (incl.)")
        self.assertContains(page, "Tax ID TL-123456")

    def test_settings_require_a_rate_when_enabled(self):
        from apps.dashboard.forms import RestaurantForm

        form = RestaurantForm(instance=self.r)
        data = {k: v for k, v in form.initial.items() if v is not None and not k.endswith(("logo", "cover_image"))}
        data.update({"vat_enabled": "on", "vat_percent": "0", "vat_label": "VAT"})
        bound = RestaurantForm(data, instance=self.r)
        self.assertFalse(bound.is_valid())
        self.assertIn("vat_percent", bound.errors)

    def test_new_restaurants_add_vat_on_top_by_default(self):
        self.assertFalse(self.r.vat_inclusive)
        self.r.vat_enabled, self.r.vat_percent = True, Decimal("10")
        self.r.save()
        self.assertEqual(self.order().total, Decimal("3.30"))

    def test_settings_dropdown_chooses_how_vat_is_charged(self):
        from apps.dashboard.forms import RestaurantForm

        for choice, expected in [("False", False), ("True", True)]:
            data = {k: v for k, v in RestaurantForm(instance=self.r).initial.items()
                    if v is not None and not k.endswith(("logo", "cover_image"))}
            data.update({"vat_enabled": "on", "vat_percent": "10", "vat_inclusive": choice, "vat_label": "VAT"})
            form = RestaurantForm(data, instance=self.r)
            self.assertTrue(form.is_valid(), form.errors)
            self.assertIs(form.save().vat_inclusive, expected)
