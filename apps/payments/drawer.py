"""
Cash drawer control via a network ESC/POS receipt printer.

The drawer plugs into the printer's DK (RJ11) port. Sending the ESC/POS
"generate pulse" command to the printer's raw port (usually 9100) kicks the
drawer open. This works for Epson, Star (ESC/POS mode), Xprinter, Bixolon and
most other receipt printers.

NOTE: the *server* connects to the printer, so the server must be able to
reach the printer's IP (i.e. run on the restaurant's network, or have a VPN
or tunnel to it).
"""
import ipaddress
import logging
import socket
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _, gettext_lazy

from .models import CashDrawerOpening

logger = logging.getLogger(__name__)

PULSE_ON, PULSE_OFF = 0x19, 0xFA  # 50 ms on, 500 ms off (in 2 ms units)
CONNECT_TIMEOUT = 2.5  # seconds; keep the waiter's tap snappy if the printer is off


def kick_command(pin: int) -> bytes:
    """ESC p m t1 t2."""
    return bytes([0x1B, 0x70, pin, PULSE_ON, PULSE_OFF])


def validate_printer_host(host: str) -> str:
    """
    Only allow printers on the local network, so a restaurant account can't
    make the server open connections to arbitrary internet hosts.
    """
    host = (host or "").strip()
    if not host:
        return host
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ValidationError(_("Can't find this printer address. Use the printer's IP, e.g. 192.168.1.50."))
    if not getattr(settings, "CASH_DRAWER_ALLOW_PUBLIC_HOSTS", False):
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_private or ip.is_loopback or ip.is_link_local:
                raise ValidationError(_("The printer must be on the local network (e.g. 192.168.x.x or 10.x.x.x)."))
    return host


@dataclass
class DrawerResult:
    opened: bool
    message: str
    attempted: bool = True

    def as_dict(self):
        return {"opened": self.opened, "attempted": self.attempted, "message": self.message}


NOT_CONFIGURED = DrawerResult(False, gettext_lazy("Cash drawer is not set up."), attempted=False)


def open_cash_drawer(restaurant, *, user=None, reason: str = CashDrawerOpening.PAYMENT, order=None) -> DrawerResult:
    if not settings.CASH_DRAWER_NETWORK_ENABLED or not restaurant.cash_drawer_enabled or not restaurant.printer_host:
        return NOT_CONFIGURED
    target = f"{restaurant.printer_host}:{restaurant.printer_port}"
    error = ""
    try:
        validate_printer_host(restaurant.printer_host)  # re-check: a hostname may resolve differently now
        with socket.create_connection((restaurant.printer_host, restaurant.printer_port), timeout=CONNECT_TIMEOUT) as s:
            s.sendall(kick_command(restaurant.drawer_pin))
    except socket.timeout:
        error = _("Printer at %(target)s did not respond. Is it switched on and connected?") % {"target": target}
    except OSError as exc:
        error = _("Could not reach the printer at %(target)s (%(error)s).") % {"target": target, "error": exc.strerror or exc}
    except ValidationError as exc:
        error = " ".join(exc.messages)
    CashDrawerOpening.objects.create(
        restaurant=restaurant, order=order, user=user, reason=reason, success=not error, error=error[:255]
    )
    if error:
        logger.warning("Cash drawer for %s: %s", restaurant.slug, error)
        return DrawerResult(False, error)
    return DrawerResult(True, _("Cash drawer opened."))
