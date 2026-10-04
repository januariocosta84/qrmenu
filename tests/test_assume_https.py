from channels.testing import HttpCommunicator
from django.test import SimpleTestCase, TransactionTestCase, override_settings


@override_settings(SECURE_SSL_REDIRECT=True, SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
                   ALLOWED_HOSTS=["testserver", "localhost"])
class AssumeHttpsTests(TransactionTestCase):
    """Apache mode: no X-Forwarded-Proto header, the app is told every request is HTTPS."""

    async def _get(self, app, path="/accounts/login/"):
        comm = HttpCommunicator(app, "GET", path, headers=[(b"host", b"localhost")])
        return await comm.get_response()

    async def test_plain_app_redirects_to_https(self):
        from django.core.asgi import get_asgi_application
        resp = await self._get(get_asgi_application())
        self.assertEqual(resp["status"], 301)
        self.assertIn(b"https://", dict(resp["headers"])[b"Location"])

    async def test_assume_https_serves_page_without_redirect(self):
        from django.core.asgi import get_asgi_application

        from config.asgi import assume_https
        resp = await self._get(assume_https(get_asgi_application()))
        self.assertEqual(resp["status"], 200)


class AssumeHttpsScopeTests(SimpleTestCase):
    async def test_websocket_scheme_becomes_wss_and_https_is_untouched(self):
        from config.asgi import assume_https
        seen = []

        async def inner(scope, receive, send):
            seen.append(scope["scheme"])

        app = assume_https(inner)
        for scope in ({"type": "http", "scheme": "http"}, {"type": "websocket", "scheme": "ws"},
                      {"type": "http", "scheme": "https"}):
            await app(scope, None, None)
        self.assertEqual(seen, ["https", "wss", "https"])
