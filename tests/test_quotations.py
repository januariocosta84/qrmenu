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

    def test_editor_offers_add_all_menu_items(self):
        html = self.client.get(self.new_url).content.decode()
        self.assertIn('id="add-all"', html)
        self.assertIn('id="clear-lines"', html)
        self.assertIn("const MAX_LINES = 200;", html)
        for dish in (self.rice, self.egg, self.tea):  # every dish is there for "Add all menu items"
            self.assertRegex(html, rf'<option value="{dish.pk}" data-all data-name="[^"]+"')

    def test_add_all_skips_car_wash_packages(self):
        from .test_car_wash import car_wash_menu
        basic, _full = car_wash_menu(self.r)
        html = self.client.get(self.new_url).content.decode()
        self.assertIn(f'<option value="{basic.pk}" data-name="Basic Wash"', html)  # still pickable one by one

    def test_save_all_menu_items_as_lines(self):
        dishes = [self.rice, self.egg, self.tea]
        self.post(line_description=[d.name for d in dishes], line_unit=["pax"] * 3, line_quantity=["40"] * 3,
                  line_price=[str(d.price) for d in dishes], line_menu_item=[str(d.pk) for d in dishes])
        self.assertEqual([i.menu_item_id for i in Quotation.objects.get().items.all()], [d.pk for d in dishes])

    def test_print_adds_menu_pages_for_quoted_dishes(self):
        self.post()  # rice from the menu + a custom delivery line
        q = Quotation.objects.get()
        html = self.client.get(reverse("dashboard:quotation_print", args=["alpha", q.pk])).content.decode()
        self.assertIn('class="sheet menu-pages"', html)
        self.assertEqual(html.count('class="dish"'), 1)  # only the dish from the menu, not the delivery line
        self.assertIn(f"<h4>{self.rice.name}</h4>", html)

    def test_print_without_menu_dishes_has_no_menu_pages(self):
        self.post(line_menu_item=["", ""])
        q = Quotation.objects.get()
        html = self.client.get(reverse("dashboard:quotation_print", args=["alpha", q.pk])).content.decode()
        self.assertNotIn('class="sheet menu-pages"', html)

    def test_company_profile_is_saved_and_printed_as_its_own_page(self):
        url = reverse("dashboard:quotation_profile", args=["alpha"])
        self.assertContains(self.client.get(url), "Start from an example")
        text = "Alpha cooks fresh food.\n\n## Our services\n- Catering\n- Coffee breaks\n\nClients:\nMinistry of Health"
        self.assertRedirects(self.client.post(url, {"company_profile": text}), url)
        self.r.refresh_from_db()
        self.assertEqual(self.r.company_profile, text)
        self.post()
        html = self.client.get(reverse("dashboard:quotation_print", args=["alpha", Quotation.objects.get().pk])).content.decode()
        self.assertIn('class="sheet profile-page"', html)
        self.assertIn("<h3>Our services</h3>", html)
        self.assertIn("<li>Coffee breaks</li>", html)
        self.assertIn("<h3>Clients</h3>", html)
        # company profile first, then the quotation, then the menu pages
        self.assertLess(html.index('class="sheet profile-page"'), html.index('<header class="head">'))
        self.assertLess(html.index('<header class="head">'), html.index('class="sheet menu-pages"'))

    def test_no_company_profile_page_when_empty(self):
        self.post()
        html = self.client.get(reverse("dashboard:quotation_print", args=["alpha", Quotation.objects.get().pk])).content.decode()
        self.assertNotIn('class="sheet profile-page"', html)

    def test_drawn_signature_and_stamp_are_placed_on_signed_quotations(self):
        import base64, io
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile

        def png(color):
            buf = io.BytesIO()
            Image.new("RGBA", (60, 20), color).save(buf, "PNG")
            return buf.getvalue()

        url = reverse("dashboard:quotation_signature", args=["alpha"])
        resp = self.client.post(url, {
            "signatory_name": "Ana Soares", "signatory_title": "Director",
            "signature_data": "data:image/png;base64," + base64.b64encode(png((0, 0, 80, 255))).decode(),
            "stamp_file": SimpleUploadedFile("stamp.png", png((200, 0, 0, 255)), content_type="image/png"),
        })
        self.assertRedirects(resp, reverse("dashboard:quotation_profile", args=["alpha"]))
        self.r.refresh_from_db()
        self.assertTrue(self.r.signature.name.startswith("restaurants/signatures/"))
        self.assertTrue(self.r.company_stamp)

        self.post()  # not signed: empty signature line
        q = Quotation.objects.get()
        print_url = reverse("dashboard:quotation_print", args=["alpha", q.pk])
        self.assertNotIn('class="sig-img"', self.client.get(print_url).content.decode())

        self.post(reverse("dashboard:quotation_edit", args=["alpha", q.pk]), signed="on")
        html = self.client.get(print_url).content.decode()
        self.assertIn(f'class="sig-img" src="{self.r.signature.url}"', html)
        self.assertIn(f'class="stamp-img" src="{self.r.company_stamp.url}"', html)
        self.assertIn("<strong>Ana Soares</strong>,<br>Director<br>", html)

        self.client.post(url, {"remove_signature": "1", "remove_stamp": "1"})
        self.r.refresh_from_db()
        self.assertFalse(self.r.signature)
        self.assertFalse(self.r.company_stamp)
        self.assertNotIn('class="sig-img"', self.client.get(print_url).content.decode())

    def test_bad_drawn_signature_is_rejected(self):
        url = reverse("dashboard:quotation_signature", args=["alpha"])
        self.client.post(url, {"signature_data": "data:image/png;base64,bm90IGFuIGltYWdl"})
        self.r.refresh_from_db()
        self.assertFalse(self.r.signature)

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
