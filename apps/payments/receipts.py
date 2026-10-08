"""
Customer receipts: one data builder, two outputs.

* Browser: `dashboard/receipt.html`, laid out for 80 mm thermal paper (prints
  fine on A4 too) and printed with the browser's print dialog.
* Network: ESC/POS bytes sent straight to the receipt printer on port 9100
  (the same printer the cash drawer plugs into). Only when the deployment
  allows network printing (CASH_DRAWER_NETWORK_ENABLED).
"""
import socket
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.orders.models import Order, OrderItem, OrderStatus, PaymentStatus

from .drawer import CONNECT_TIMEOUT, validate_printer_host
from .models import Payment

MAX_ORDERS_PER_RECEIPT = 50


def parse_order_ids(raw) -> list[int]:
    """'12,13' or [12, 13] → [12, 13] (ints only, de-duplicated, capped)."""
    parts = raw.split(",") if isinstance(raw, str) else (raw or [])
    ids = []
    for p in parts:
        try:
            i = int(p)
        except (TypeError, ValueError):
            continue
        if i > 0 and i not in ids:
            ids.append(i)
    return ids[:MAX_ORDERS_PER_RECEIPT]


def receipt_orders(restaurant, ids):
    return list(
        Order.objects.filter(restaurant=restaurant, pk__in=ids)
        .exclude(status=OrderStatus.CANCELLED)
        .prefetch_related(
            Prefetch("items", queryset=OrderItem.objects.prefetch_related("options")),
            Prefetch("payments", queryset=Payment.objects.filter(status=Payment.SUCCEEDED).select_related("received_by")),
        )
        .order_by("created_at")
    )


def vat_lines(orders) -> list[dict]:
    """VAT per (name, rate, included?) across the orders on one bill, e.g. "VAT 10% (incl.)"."""
    groups = {}
    for o in orders:
        if o.vat_amount:
            key = (o.vat_label or "VAT", o.vat_percent, o.vat_inclusive)
            groups[key] = groups.get(key, Decimal("0")) + o.vat_amount
    return [
        {"label": f"{label} {rate.normalize():f}%" + (" (incl.)" if inclusive else ""), "amount": amount,
         "inclusive": inclusive}
        for (label, rate, inclusive), amount in groups.items()
    ]


def receipt_data(restaurant, orders) -> dict:
    payments = [p for o in orders for p in o.payments.all()]
    vat = vat_lines(orders)
    paid = all(o.payment_status == PaymentStatus.PAID for o in orders)
    tendered = sum((p.cash_tendered for p in payments if p.cash_tendered is not None), Decimal("0"))
    staff = next((p.received_by for p in reversed(payments) if p.received_by), None)
    tables = sorted({o.table_number for o in orders if o.table_number})
    names = [n for n in dict.fromkeys(o.customer_name for o in orders) if n]
    return {
        "restaurant": restaurant,
        "orders": orders,
        "title": "RECEIPT" if paid else "BILL",
        "paid": paid,
        "numbers": ", ".join(f"#{o.number}" for o in orders),
        "tables": ", ".join(tables),
        "customer": ", ".join(names),
        "subtotal": sum((o.subtotal for o in orders), Decimal("0")),
        "service_charge": sum((o.service_charge for o in orders), Decimal("0")),
        "vat_lines": vat,
        "vat_number": restaurant.vat_number if (vat or restaurant.vat_enabled) else "",
        "total": sum((o.total for o in orders), Decimal("0")),
        "amount_paid": sum((p.amount for p in payments), Decimal("0")),
        "methods": ", ".join(dict.fromkeys(p.get_method_display() for p in payments)),
        "tendered": tendered or None,
        "change": sum((p.change_given for p in payments), Decimal("0")),
        "staff": (staff.get_full_name() or staff.username) if staff else "",
        "printed_at": timezone.localtime(),
        "footer": restaurant.receipt_footer,
    }


# ---------------------------------------------------------------- ESC/POS

ESC, GS = b"\x1b", b"\x1d"
INIT = ESC + b"@"
ALIGN_LEFT, ALIGN_CENTER = ESC + b"a\x00", ESC + b"a\x01"
BOLD_ON, BOLD_OFF = ESC + b"E\x01", ESC + b"E\x00"
DOUBLE, NORMAL = GS + b"!\x11", GS + b"!\x00"
FEED_CUT = ESC + b"d\x04" + GS + b"V\x42\x00"  # feed, then partial cut


def _txt(s: str) -> bytes:
    return s.encode("cp437", errors="replace")


def _pair(left: str, right: str, width: int) -> str:
    space = width - len(right) - 1
    left = left if len(left) <= space else left[: space - 1] + "~"
    return f"{left:<{space}} {right}"


def escpos_receipt(data: dict, width: int) -> bytes:
    r = data["restaurant"]
    sym = r.currency_symbol
    money = lambda v: f"{sym}{v:.2f}"  # noqa: E731
    line = "-" * width
    out = [INIT, ALIGN_CENTER, DOUBLE, BOLD_ON, _txt(r.name[: width // 2] + "\n"), NORMAL, BOLD_OFF]
    for extra in (r.address, r.phone, f"Tax ID {data['vat_number']}" if data["vat_number"] else ""):
        if extra:
            out.append(_txt(extra[:width] + "\n"))
    out += [_txt("\n"), BOLD_ON, _txt(data["title"] + "\n"), BOLD_OFF, ALIGN_LEFT, _txt(line + "\n")]
    out.append(_txt(_pair("Order", data["numbers"], width) + "\n"))
    if data["tables"]:
        out.append(_txt(_pair("Table", data["tables"], width) + "\n"))
    if data["customer"]:
        out.append(_txt(_pair("Customer", data["customer"], width) + "\n"))
    out.append(_txt(_pair("Date", f"{data['printed_at']:%d %b %Y %H:%M}", width) + "\n"))
    out.append(_txt(line + "\n"))
    for o in data["orders"]:
        for item in o.items.all():
            out.append(_txt(_pair(f"{item.quantity} x {item.name}", money(item.line_total), width) + "\n"))
            for opt in item.options.all():
                out.append(_txt(f"   + {opt.name}"[:width] + "\n"))
            if item.note:
                out.append(_txt(f"   \"{item.note}\""[:width] + "\n"))
    out.append(_txt(line + "\n"))
    out.append(_txt(_pair("Subtotal", money(data["subtotal"]), width) + "\n"))
    if data["service_charge"]:
        out.append(_txt(_pair("Service charge", money(data["service_charge"]), width) + "\n"))
    for v in data["vat_lines"]:
        out.append(_txt(_pair(v["label"], money(v["amount"]), width) + "\n"))
    out += [BOLD_ON, _txt(_pair("TOTAL", money(data["total"]), width) + "\n"), BOLD_OFF]
    if data["paid"]:
        out.append(_txt(_pair(f"Paid ({data['methods']})", money(data["amount_paid"]), width) + "\n"))
        if data["tendered"]:
            out.append(_txt(_pair("Cash received", money(data["tendered"]), width) + "\n"))
            out.append(_txt(_pair("Change", money(data["change"]), width) + "\n"))
    else:
        out.append(_txt(_pair("Status", "NOT PAID", width) + "\n"))
    if data["staff"]:
        out.append(_txt(_pair("Served by", data["staff"], width) + "\n"))
    out += [_txt(line + "\n"), ALIGN_CENTER]
    if data["footer"]:
        out.append(_txt(data["footer"][: width * 3] + "\n"))
    out.append(FEED_CUT)
    return b"".join(out)


@dataclass
class PrintResult:
    printed: bool
    message: str
    attempted: bool = True

    def as_dict(self):
        return {"printed": self.printed, "attempted": self.attempted, "message": self.message}


def network_printing_available(restaurant) -> bool:
    return bool(
        settings.CASH_DRAWER_NETWORK_ENABLED
        and restaurant.receipt_printer == restaurant.RECEIPT_NETWORK
        and restaurant.printer_host
    )


def print_receipt_network(restaurant, orders) -> PrintResult:
    if not network_printing_available(restaurant):
        return PrintResult(False, _("Network receipt printer is not set up."), attempted=False)
    if not orders:
        return PrintResult(False, _("Nothing to print."), attempted=False)
    target = f"{restaurant.printer_host}:{restaurant.printer_port}"
    payload = escpos_receipt(receipt_data(restaurant, orders), restaurant.receipt_width)
    try:
        validate_printer_host(restaurant.printer_host)
        with socket.create_connection((restaurant.printer_host, restaurant.printer_port), timeout=CONNECT_TIMEOUT) as s:
            s.sendall(payload)
    except socket.timeout:
        return PrintResult(False, _("Printer at %(target)s did not respond. Is it switched on?") % {"target": target})
    except OSError as exc:
        return PrintResult(False, _("Could not reach the printer at %(target)s (%(error)s).") % {
            "target": target, "error": exc.strerror or exc})
    except ValidationError as exc:
        return PrintResult(False, " ".join(exc.messages))
    return PrintResult(True, _("Receipt printed."))
