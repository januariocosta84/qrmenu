import io

from django.core.cache import cache
from django.test import TestCase, override_settings
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
