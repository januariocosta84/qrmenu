from django.test import TestCase


class ManualDownloadTests(TestCase):
    def test_home_offers_the_manual_in_three_languages(self):
        html = self.client.get("/").content.decode()
        self.assertIn('id="manual"', html)
        for name in ("QR-Menu-User-Manual-EN.pdf", "QR-Menu-Manual-Uzuariu-TET.pdf", "QR-Menu-Panduan-Pengguna-ID.pdf"):
            self.assertIn(f"/static/manual/{name}", html)
        # English first (and highlighted) for an English reader
        self.assertLess(html.index("QR-Menu-User-Manual-EN.pdf"), html.index("QR-Menu-Manual-Uzuariu-TET.pdf"))

    def test_readers_language_comes_first(self):
        self.client.cookies["dash_lang"] = "tet"
        html = self.client.get("/").content.decode()
        self.assertIn("Manuál uzuáriu", html)
        self.assertLess(html.index("QR-Menu-Manual-Uzuariu-TET.pdf"), html.index("QR-Menu-User-Manual-EN.pdf"))
        self.assertRegex(html, r'btn-primary" href="/static/manual/QR-Menu-Manual-Uzuariu-TET\.pdf')


class HardwareNoticeTests(TestCase):
    def test_home_says_we_do_not_sell_hardware_and_offers_a_quote(self):
        html = self.client.get("/").content.decode()
        self.assertIn('id="hardware"', html)
        self.assertIn("We do not sell hardware", html)

    def test_hardware_notice_in_tetun(self):
        self.client.cookies["dash_lang"] = "tet"
        self.assertContains(self.client.get("/"), "Ami la faan ekipamentu")
