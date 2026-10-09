"""
Webhooks: POST a signed JSON event to the main branch's URL when an order is
created or changes status.

Headers: X-QRMenu-Event, X-QRMenu-Timestamp and X-QRMenu-Signature
("sha256=" + HMAC-SHA256 of "<timestamp>.<body>" with the webhook secret).
Only public https URLs are called (no private/loopback addresses, no redirects).
"""
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

logger = logging.getLogger(__name__)
TIMEOUT = 5
KEEP_DELIVERIES = 50


class UnsafeUrl(ValueError):
    pass


def check_url(url: str) -> None:
    """Only https URLs whose host resolves to public addresses (keeps the server from calling internal services)."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise UnsafeUrl("The webhook URL must start with https://")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        raise UnsafeUrl("The webhook host name can't be found.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise UnsafeUrl("The webhook URL must point to a public internet address.")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def _post(webhook_id: int, event: str, payload: dict, order_ref: str, sandbox: bool) -> None:
    from .models import Webhook, WebhookDelivery

    close_old_connections()
    try:
        wh = Webhook.objects.filter(pk=webhook_id, is_active=True).first()
        if wh is None:
            return
        body = json.dumps(payload, separators=(",", ":")).encode()
        ts = str(int(time.time()))
        started = time.monotonic()
        code, error = None, ""
        try:
            check_url(wh.url)
            req = urllib.request.Request(wh.url, data=body, method="POST", headers={
                "Content-Type": "application/json", "User-Agent": "QRMenu-Webhooks/1",
                "X-QRMenu-Event": event, "X-QRMenu-Timestamp": ts, "X-QRMenu-Signature": sign(wh.secret, ts, body),
            })
            with _opener.open(req, timeout=TIMEOUT) as resp:
                code = resp.status
        except urllib.error.HTTPError as exc:
            code, error = exc.code, f"HTTP {exc.code}"
        except UnsafeUrl as exc:
            error = str(exc)
        except Exception as exc:  # timeouts, refused connections, TLS errors
            error = (str(getattr(exc, "reason", "")) or exc.__class__.__name__)[:200]
        ms = int((time.monotonic() - started) * 1000)
        WebhookDelivery.objects.create(webhook=wh, event=event, order_ref=order_ref, sandbox=sandbox,
                                       status_code=code, error=error[:200], duration_ms=ms)
        Webhook.objects.filter(pk=wh.pk).update(last_sent_at=timezone.now(), last_status_code=code,
                                                last_error=error[:200])
        old = list(wh.deliveries.values_list("pk", flat=True)[KEEP_DELIVERIES:])
        if old:
            WebhookDelivery.objects.filter(pk__in=old).delete()
    except Exception:
        logger.exception("Webhook delivery failed")
    finally:
        if settings.WEBHOOKS_ASYNC:
            close_old_connections()


def _send(webhooks, event: str, payload: dict, order_ref: str, sandbox: bool) -> None:
    for wh in webhooks:
        if settings.WEBHOOKS_ASYNC:
            threading.Thread(target=_post, args=(wh.pk, event, payload, order_ref, sandbox), daemon=True).start()
        else:
            _post(wh.pk, event, payload, order_ref, sandbox)


def _envelope(event: str, data: dict) -> dict:
    return {"event": event, "sent_at": timezone.now().isoformat(), "sandbox": data.get("sandbox", False), "data": data}


def order_event(order, event: str) -> None:
    """A real order was created or changed status: tell the main branch's webhooks."""
    from .models import Webhook
    from .services import order_json

    try:
        main = order.restaurant.main_branch
        hooks = [w for w in Webhook.objects.filter(restaurant=main, is_active=True)
                 if w.all_orders or order.source == "api"]
        if hooks:
            _send(hooks, event, _envelope(event, order_json(order)), str(order.public_token), False)
    except Exception:  # webhooks must never break order handling
        logger.exception("Could not queue webhooks for order %s", order.pk)


def sandbox_event(sandbox_order, event: str) -> None:
    from .models import Webhook
    from .services import sandbox_json

    hooks = list(Webhook.objects.filter(restaurant=sandbox_order.api_key.restaurant, is_active=True))
    if hooks:
        _send(hooks, event, _envelope(event, sandbox_json(sandbox_order)), str(sandbox_order.token), True)


def send_test(webhook) -> None:
    _send([webhook], "ping", {"event": "ping", "sent_at": timezone.now().isoformat(), "sandbox": True,
                              "data": {"message": "Webhook test from QR Menu"}}, "", True)
