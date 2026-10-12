"""Visitor counter: home page, sign-up and restaurant menus per day; bots and staff skipped; log import."""
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.console.models import PageViewDay
from apps.core.permissions import Role

from .test_ordering import make_restaurant, staff

PHONE = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/150 Mobile Safari/537.36"
LAPTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/150 Safari/537.36"


class VisitorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.r, *_ = make_restaurant()

    def row(self, page, restaurant=None):
        return PageViewDay.objects.get(date=timezone.localdate(), page=page, restaurant=restaurant)

    def test_counts_people_and_views(self):
        phone = Client(HTTP_USER_AGENT=PHONE)
        phone.get("/")
        phone.get("/")
        Client(HTTP_USER_AGENT=LAPTOP).get("/")
        home = self.row(PageViewDay.HOME)
        self.assertEqual((home.visitors, home.views), (2, 3))
        phone.get(reverse("storefront:menu", args=["alpha"]))
        phone.get(reverse("accounts:signup"))
        self.assertEqual(self.row(PageViewDay.MENU, self.r).views, 1)
        self.assertEqual(self.row(PageViewDay.SIGNUP).visitors, 1)

    def test_skips_bots_staff_and_other_pages(self):
        Client(HTTP_USER_AGENT="Googlebot/2.1 (+http://www.google.com/bot.html)").get("/")
        Client(HTTP_USER_AGENT="curl/8.5").get("/")
        owner = Client(HTTP_USER_AGENT=LAPTOP)
        owner.force_login(staff(self.r, "owner", Role.OWNER))
        owner.get("/")
        Client(HTTP_USER_AGENT=PHONE).get("/terms/")
        Client(HTTP_USER_AGENT=PHONE).get("/r/no-such-place/")  # 404: not counted
        self.assertFalse(PageViewDay.objects.exists())

    def test_console_page(self):
        Client(HTTP_USER_AGENT=PHONE).get("/")
        admin = get_user_model().objects.create_superuser("platform", "a@example.test", "x-Admin-pass-2026")
        c = Client()
        c.force_login(admin)
        page = c.get(reverse("console:visitors") + "?days=7")
        self.assertEqual(page.context["today"]["home"], [1, 1])
        self.assertEqual(len(page.context["table"]), 7)
        self.assertEqual(c.get(reverse("console:overview")).context["visitors_today"], 1)
        self.assertEqual(Client().get(reverse("console:visitors")).status_code, 302)  # login required

    def test_import_from_access_log(self):
        day = timezone.localdate() - timedelta(days=2)
        stamp = day.strftime("%d/%b/%Y")
        lines = [
            f'1.1.1.1 - - [{stamp}:10:00:00 +0900] "GET / HTTP/1.1" 200 5000 "-" "{PHONE}"',
            f'1.1.1.1 - - [{stamp}:10:01:00 +0900] "GET / HTTP/1.1" 200 5000 "-" "{PHONE}"',
            f'2.2.2.2 - - [{stamp}:11:00:00 +0900] "GET /r/alpha/ HTTP/1.1" 200 5000 "-" "{LAPTOP}"',
            f'3.3.3.3 - - [{stamp}:12:00:00 +0900] "GET / HTTP/1.1" 200 5000 "-" "Googlebot/2.1"',
            f'9.9.9.9 - - [{stamp}:12:00:00 +0900] "GET / HTTP/1.1" 200 5000 "-" "{LAPTOP}"',
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write("\n".join(lines) + "\n")
        call_command("import_visits", f.name, "--exclude-ip", "9.9.9.9", stdout=open("/dev/null", "w"))
        home = PageViewDay.objects.get(date=day, page=PageViewDay.HOME)
        self.assertEqual((home.visitors, home.views), (1, 2))
        self.assertEqual(PageViewDay.objects.get(date=day, page=PageViewDay.MENU).restaurant, self.r)
        call_command("import_visits", f.name, stdout=open("/dev/null", "w"))  # again: replaced, not doubled
        self.assertEqual(PageViewDay.objects.get(date=day, page=PageViewDay.HOME).views, 3)  # 9.9.9.9 now included


class CountryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.r, *_ = make_restaurant()
        self.tmp = tempfile.mkdtemp()
        csv_path = f"{self.tmp}/dbip.csv"
        with open(csv_path, "w") as f:
            f.write("1.0.0.0,1.0.0.255,AU\n150.228.0.0,150.228.255.255,TL\n2407:3640::,2407:3641:ffff:ffff:ffff:ffff:ffff:ffff,TL\n")
        self.db = f"{self.tmp}/countries.sqlite3"
        with self.settings(GEOIP_DB=self.db):
            call_command("update_geoip", "--csv", csv_path, stdout=open("/dev/null", "w"))
        from apps.console import geo
        geo._local.conn = None  # reconnect to the new file

    def test_lookup_and_mask(self):
        from apps.console import geo
        with self.settings(GEOIP_DB=self.db):
            geo._local.conn = None
            self.assertEqual(geo.country("150.228.169.105"), "TL")
            self.assertEqual(geo.country("2407:3641:2290:6574::1"), "TL")
            self.assertEqual(geo.country("1.0.0.7"), "AU")
            self.assertEqual(geo.country("8.8.8.8"), "")
            self.assertEqual(geo.country("not an ip"), "")
        self.assertEqual(geo.mask("150.228.169.105"), "150.228.169.x")
        self.assertEqual(geo.mask("2407:3641:2290:6574::1"), "2407:3641:2290:x")
        self.assertEqual(geo.name("TL"), "Timor-Leste")

    def test_visitor_logged_with_country_and_shown(self):
        from apps.console import geo
        from apps.console.models import VisitorLog
        with self.settings(GEOIP_DB=self.db):
            geo._local.conn = None
            Client(HTTP_USER_AGENT=PHONE, REMOTE_ADDR="150.228.169.105").get("/")
            Client(HTTP_USER_AGENT=PHONE, REMOTE_ADDR="150.228.169.105").get("/")  # same person: once
            Client(HTTP_USER_AGENT=LAPTOP, REMOTE_ADDR="1.0.0.9").get(reverse("storefront:menu", args=["alpha"]))
        rows = list(VisitorLog.objects.order_by("created_at").values_list("page", "country", "ip_masked"))
        self.assertEqual(rows, [("home", "TL", "150.228.169.x"), ("menu", "AU", "1.0.0.x")])
        self.assertFalse(VisitorLog.objects.filter(ip_masked__contains="105").exists())  # full IP never stored
        admin = get_user_model().objects.create_superuser("platform", "a@example.test", "x-Admin-pass-2026")
        c = Client()
        c.force_login(admin)
        page = c.get(reverse("console:visitors"))
        self.assertEqual({x["name"]: x["n"] for x in page.context["countries"]}, {"Timor-Leste": 1, "Australia": 1})
        self.assertContains(page, "150.228.169.x")
