"""Google: robots.txt, sitemap with dish photos, menu meta tags and schema.org Restaurant/Menu data."""
import json
import re

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.core.permissions import Role
from apps.menu.models import MenuItem

from .test_ordering import make_restaurant, staff


@override_settings(PUBLIC_BASE_URL="https://qrmenu.example", GOOGLE_SITE_VERIFICATION="abc123")
class SeoTests(TestCase):
    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        self.r.description, self.r.address, self.r.phone = "Best fried rice in Dili.", "Colmera, Dili", "+670 7712 3456"
        self.r.save(update_fields=["description", "address", "phone"])
        MenuItem.objects.filter(pk=self.rice.pk).update(image="menu/items/rice.webp")

    def test_robots(self):
        txt = Client().get("/robots.txt").content.decode()
        self.assertIn("Disallow: /dashboard/", txt)
        self.assertIn("Disallow: /r/*/t/", txt)
        self.assertIn("Sitemap: https://qrmenu.example/sitemap.xml", txt)

    def test_sitemap_lists_menus_with_photos(self):
        empty, *_ = make_restaurant("empty-place")
        MenuItem.objects.filter(restaurant=empty).delete()
        hidden, *_ = make_restaurant("hidden-place")
        hidden.show_in_search = False
        hidden.save(update_fields=["show_in_search"])
        xml = Client().get("/sitemap.xml").content.decode()
        self.assertIn("<loc>https://qrmenu.example/r/alpha/</loc>", xml)
        self.assertIn("<image:loc>https://qrmenu.example/media/menu/items/rice.webp</image:loc>", xml)
        self.assertNotIn("/r/empty-place/", xml)  # no dishes yet
        self.assertNotIn("/r/hidden-place/", xml)  # owner switched it off

    def test_menu_page_meta_and_structured_data(self):
        html = Client().get(reverse("storefront:menu", args=["alpha"])).content.decode()
        self.assertIn('<link rel="canonical" href="https://qrmenu.example/r/alpha/">', html)
        self.assertIn('content="Best fried rice in Dili."', html)
        self.assertNotIn("noindex", html)
        self.assertIn('alt="Chicken Fried Rice – Alpha"', html)
        data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1))
        self.assertEqual((data["@type"], data["name"], data["telephone"]), ("Restaurant", "Alpha", "+670 7712 3456"))
        dish = data["hasMenu"]["hasMenuSection"][0]["hasMenuItem"][0]
        self.assertEqual((dish["name"], dish["offers"]["price"], dish["offers"]["priceCurrency"]),
                         ("Chicken Fried Rice", "5.00", "USD"))
        self.assertEqual(dish["image"], "https://qrmenu.example/media/menu/items/rice.webp")

    def test_table_links_and_hidden_restaurants_are_noindex(self):
        c = Client()
        c.get(self.table.active_qr().get_path())
        table_page = c.get(reverse("storefront:table", args=["alpha", "12"])).content.decode()
        self.assertIn('content="noindex, follow"', table_page)
        self.assertIn('<link rel="canonical" href="https://qrmenu.example/r/alpha/">', table_page)
        self.r.show_in_search = False
        self.r.save(update_fields=["show_in_search"])
        self.assertIn("noindex", Client().get(reverse("storefront:menu", args=["alpha"])).content.decode())

    def test_home_page_tags_and_owner_setting(self):
        home = Client().get("/").content.decode()
        self.assertIn('name="google-site-verification" content="abc123"', home)
        self.assertIn('<link rel="canonical" href="https://qrmenu.example/">', home)
        owner = Client()
        owner.force_login(staff(self.r, "owner", Role.OWNER))
        self.assertContains(owner.get(reverse("dashboard:settings", args=["alpha"])), "Show my menu on Google")
