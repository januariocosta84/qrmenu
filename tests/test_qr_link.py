import io

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.core.permissions import Role

from .test_ordering import make_restaurant, staff

SITE = "https://qrmenu.timorstore.com"


@override_settings(PUBLIC_BASE_URL=SITE)
class QrLinkTests(TestCase):
    """Every QR code shows the platform link underneath, and still points to the table."""

    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        self.client.force_login(staff(self.r, "owner", Role.OWNER))
        self.qr_url = reverse("dashboard:table_qr", args=["alpha", self.table.pk])

    def test_png_has_caption_band(self):
        img = Image.open(io.BytesIO(self.client.get(self.qr_url + "?download=1").content))
        w, h = img.size
        self.assertGreater(h, w)  # square code + text band below

    def test_svg_has_link_text(self):
        svg = self.client.get(self.qr_url + "?format=svg").content.decode()
        self.assertIn(f">{SITE}</text>", svg)

    def test_print_cards_and_table_page_show_link(self):
        self.assertContains(self.client.get(reverse("dashboard:tables_print", args=["alpha"])), f'<p class="site">{SITE}</p>')
        page = self.client.get(reverse("dashboard:table_detail", args=["alpha", self.table.pk]))
        self.assertContains(page, f'<p class="qr-site">{SITE}</p>')
        self.assertContains(page, SITE + "/r/alpha/t/")  # the code itself still points at the table

    @override_settings(PUBLIC_BASE_URL="")
    def test_falls_back_to_the_current_host(self):
        svg = self.client.get(self.qr_url + "?format=svg").content.decode()
        self.assertIn(">http://testserver</text>", svg)


class QRBadgeTests(TestCase):
    """Restaurant name (and logo) in the middle of every QR code."""

    def test_badge_in_png_and_svg(self):
        from io import BytesIO

        from PIL import Image

        from apps.restaurants.models import Restaurant
        from apps.restaurants.qr import qr_badge, qr_png, qr_svg

        badge = qr_badge(Restaurant(name="Cafe Atsabe Cristo Rei"))
        self.assertEqual(badge.size, (300, 300))
        url = "https://qrmenu.example/r/alpha/t/12/?k=abc"
        svg = qr_svg(url, caption="https://qrmenu.example", badge=badge)
        self.assertIn('<image ', svg)
        self.assertIn("data:image/png;base64,", svg)
        plain = Image.open(BytesIO(qr_png(url)))
        with_badge = Image.open(BytesIO(qr_png(url, badge=badge)))
        self.assertGreater(with_badge.width, plain.width)  # stronger error correction → more modules
        w = with_badge.width  # no caption: the code is square
        inside_badge_margin = with_badge.convert("RGB").getpixel((int(w / 2 - 0.095 * w), w // 2))
        self.assertEqual(inside_badge_margin, (255, 255, 255))  # white badge in the middle

    def test_table_page_and_print_cards_show_badge(self):
        from apps.core.permissions import Role

        from .test_ordering import make_restaurant, staff
        r, *_rest, table = make_restaurant("badge-place")
        c = Client()
        c.force_login(staff(r, "owner", Role.OWNER))
        page = c.get(reverse("dashboard:table_detail", args=["badge-place", table.pk]))
        self.assertIn("data:image/png;base64,", page.content.decode())
        cards = c.get(reverse("dashboard:tables_print", args=["badge-place"]))
        self.assertIn("data:image/png;base64,", cards.content.decode())
