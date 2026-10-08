from datetime import date
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.core.permissions import Role
from apps.quotations.models import Quotation
from apps.quotations.words import amount_in_words

from .test_ordering import make_pro, make_restaurant, staff


class QuotationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        make_pro(self.r)
        self.client.force_login(staff(self.r, "manager", Role.MANAGER))
        self.new_url = reverse("dashboard:quotation_create", args=["alpha"])

    def post(self, url=None, **extra):
        data = {
            "client_name": "Maria Soares", "client_organization": "Ministry of Health", "client_contact": "+670 7700 1234",
            "client_address": "Dili", "reference": "MS/2026/045", "title": "Workshop lunch", "event_date": "2026-11-05",
            "guests": "40", "issue_date": "2026-10-08", "valid_until": "2026-11-07", "language": "pt", "status": "draft",
            "discount": "10", "vat_percent": "2.5", "terms": "50% deposit.",
            # 40 × rice from the menu ($5.00) + a custom delivery line
            "line_description": ["Chicken Fried Rice", "Delivery & setup"], "line_unit": ["pax", "trip"],
            "line_quantity": ["40", "1"], "line_price": ["5.00", "25"], "line_menu_item": [str(self.rice.pk), ""],
        }
        data.update(extra)
        return self.client.post(url or self.new_url, data)

    def test_create_with_menu_and_custom_lines(self):
        resp = self.post()
        q = Quotation.objects.get()
        self.assertRedirects(resp, reverse("dashboard:quotation_edit", args=["alpha", q.pk]))
        self.assertEqual(q.number, "Q-2026-0001")
        self.assertEqual([(i.description, i.quantity, i.amount, i.menu_item_id) for i in q.items.all()],
                         [("Chicken Fried Rice", Decimal("40.00"), Decimal("200.00"), self.rice.pk),
                          ("Delivery & setup", Decimal("1.00"), Decimal("25.00"), None)])
        self.assertEqual(q.subtotal, Decimal("225.00"))
        self.assertEqual(q.vat_amount, Decimal("5.38"))  # (225 − 10) × 2.5%
        self.assertEqual(q.total, Decimal("220.38"))

    def test_numbers_count_up_per_year(self):
        self.post()
        self.post()
        self.post(issue_date="2027-01-03", valid_until="2027-02-01")
        self.assertEqual(sorted(Quotation.objects.values_list("number", flat=True)),
                         ["Q-2026-0001", "Q-2026-0002", "Q-2027-0001"])

    def test_edit_replaces_lines_and_validation_keeps_input(self):
        self.post()
        q = Quotation.objects.get()
        edit = reverse("dashboard:quotation_edit", args=["alpha", q.pk])
        self.post(edit, line_description=["Coffee break"], line_unit=["pax"], line_quantity=["40"],
                  line_price=["1.5"], line_menu_item=[""], status="sent")
        q.refresh_from_db()
        self.assertEqual((q.items.count(), q.subtotal, q.status), (1, Decimal("60.00"), "sent"))
        resp = self.post(edit, line_description=["", "Water"], line_unit=["", ""], line_quantity=["", "abc"],
                         line_price=["", "1"], line_menu_item=["", ""])
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Line 2: enter numbers for quantity and price.")
        self.assertEqual(q.items.count(), 1)  # nothing saved

    def test_needs_at_least_one_line(self):
        resp = self.post(line_description=[""], line_unit=[""], line_quantity=[""], line_price=[""], line_menu_item=[""])
        self.assertContains(resp, "Add at least one line.")
        self.assertFalse(Quotation.objects.exists())

    def test_other_restaurants_menu_items_are_not_linked(self):
        _other, other_rice, *_ = make_restaurant("beta")
        self.post(line_menu_item=[str(other_rice.pk), ""])
        self.assertIsNone(Quotation.objects.get().items.first().menu_item_id)

    def test_print_in_document_language_with_amount_in_words(self):
        self.post()
        q = Quotation.objects.get()
        resp = self.client.get(reverse("dashboard:quotation_print", args=["alpha", q.pk]))
        self.assertContains(resp, '<html lang="pt">')
        self.assertContains(resp, "Q-2026-0001")
        self.assertContains(resp, "Duzentos e vinte dólares americanos e trinta e oito cêntimos")
        self.assertContains(resp, "Ministry of Health")

    def test_new_quotation_copies_last_terms_and_vat(self):
        self.r.vat_enabled, self.r.vat_percent = True, Decimal("5")
        self.r.save()
        self.post(terms="Bank: BNU 123")
        form = self.client.get(self.new_url).context["form"]
        self.assertEqual(form.initial["terms"], "Bank: BNU 123")
        self.assertEqual(form.initial["vat_percent"], Decimal("5"))

    def test_duplicate_and_delete(self):
        self.post()
        q = Quotation.objects.get()
        resp = self.client.post(reverse("dashboard:quotation_duplicate", args=["alpha", q.pk]))
        copy = Quotation.objects.exclude(pk=q.pk).get()
        self.assertRedirects(resp, reverse("dashboard:quotation_edit", args=["alpha", copy.pk]))
        self.assertEqual((copy.items.count(), copy.total, copy.status), (2, q.total, "draft"))
        self.client.post(reverse("dashboard:quotation_delete", args=["alpha", q.pk]))
        self.assertEqual(Quotation.objects.count(), 1)

    def test_list_and_permissions(self):
        self.post()
        self.assertContains(self.client.get(reverse("dashboard:quotations", args=["alpha"])), "Q-2026-0001")
        self.client.force_login(staff(self.r, "waiter", Role.WAITER))
        self.assertEqual(self.client.get(reverse("dashboard:quotations", args=["alpha"])).status_code, 403)
        _other, *_ = make_restaurant("beta")
        q = Quotation.create_for(_other, None, client_name="X", issue_date=date(2026, 1, 1))
        self.client.force_login(staff(self.r, "owner", Role.OWNER))
        self.assertEqual(self.client.get(reverse("dashboard:quotation_print", args=["alpha", q.pk])).status_code, 404)


class AmountInWordsTests(TestCase):
    def test_languages(self):
        self.assertEqual(amount_in_words(Decimal("1250.50"), "en"), "One thousand two hundred fifty US dollars and fifty cents")
        self.assertEqual(amount_in_words(Decimal("2345"), "pt"), "Dois mil trezentos e quarenta e cinco dólares americanos")
        self.assertEqual(amount_in_words(Decimal("1100"), "tet"), "Mil e cem dólares americanos")
        self.assertEqual(amount_in_words(Decimal("1011.15"), "id"), "Seribu sebelas dolar AS dan lima belas sen")
        self.assertEqual(amount_in_words(Decimal("1"), "en"), "One US dollar")
