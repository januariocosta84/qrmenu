"""
Bills for tables and walk-ins with several services (café + car wash), from the Print bill dialog.

* `build_bills` makes one snapshot per receipt: a combined bill (a section per service,
  each with its own subtotal, then the grand total) or one bill per service (each with
  its own business name and numbering: C-0001 café, W-0001 car wash, R-0001 combined).
* `create_receipts` numbers and stores them (Receipt), so they can be reprinted as COPY.
* `receipt_lines` lays a snapshot out for thermal paper: 48 characters per line on
  80 mm, 32 on 58 mm, long text wrapped. The browser page and the ESC/POS printer print
  exactly these lines.
"""
import textwrap
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.menu import service_types
from apps.orders.models import OrderStatus, PaymentStatus

from .models import Payment, Receipt, ReceiptSequence
from .receipts import vat_lines

COMBINED, SEPARATE = "combined", "separate"
WIDTHS = (32, 48)  # 58 mm, 80 mm
VEHICLE_LABELS = dict(service_types.VEHICLE_TYPES)


def _m(v) -> str:
    return f"{Decimal(v):.2f}"


def billable(orders) -> list:
    """Orders that still need paying: not cancelled, not paid."""
    return [o for o in orders if o.status != OrderStatus.CANCELLED and o.payment_status != PaymentStatus.PAID]


def by_service(orders) -> dict:
    """{service: [orders]} in the registry's order (café first)."""
    groups = {}
    for code in service_types.SERVICE_TYPES:
        part = [o for o in orders if o.service_type == code]
        if part:
            groups[code] = part
    return groups


def _section(restaurant, code, orders) -> dict:
    s = service_types.get(code)
    name = service_types.display_name(restaurant, code).upper()
    plates = list(dict.fromkeys(p.strip() for o in orders for p in o.plate_number.split(",") if p.strip()))
    title = f"{name} (Plate: {', '.join(plates)})" if s.needs_vehicle and plates else name
    lines = []
    for o in orders:
        for item in o.items.all():
            extra = []
            if item.vehicle_type and item.vehicle_type != service_types.CAR:
                extra.append(str(VEHICLE_LABELS.get(item.vehicle_type, item.vehicle_type)))
            if item.plate_number and len(plates) > 1:
                extra.append(item.plate_number)
            lines.append({
                "qty": item.quantity, "name": item.name + (f" ({', '.join(extra)})" if extra else ""),
                "total": _m(item.line_total), "options": [o2.name for o2 in item.options.all()], "note": item.note,
            })
    return {"service": code, "title": title, "lines": lines,
            "subtotal": _m(sum((o.total for o in orders), Decimal("0")))}


def _bill(restaurant, orders, *, business: str, service: str = "") -> dict:
    payments = [p for o in orders for p in o.payments.all() if p.status == Payment.SUCCEEDED]
    paid = all(o.payment_status == PaymentStatus.PAID for o in orders)
    staff = next((p.received_by for p in reversed(payments) if p.received_by), None)
    groups = by_service(orders)
    vat = vat_lines(orders)
    return {
        "business": business, "address": restaurant.address, "phone": restaurant.phone,
        "vat_number": restaurant.vat_number if (vat or restaurant.vat_enabled) else "",
        "title": "RECEIPT" if paid else "BILL", "paid": paid, "service": service,
        "orders": ", ".join(f"#{o.number}" for o in orders),
        "table": ", ".join(sorted({o.table_number for o in orders if o.table_number})),
        "customer": ", ".join(n for n in dict.fromkeys(o.customer_name for o in orders) if n),
        "date": timezone.localtime().strftime("%d %b %Y %H:%M"),
        "sections": [_section(restaurant, code, part) for code, part in groups.items()],
        "show_sections": len(groups) > 1 or any(service_types.get(c).needs_vehicle for c in groups),
        "subtotal": _m(sum((o.subtotal for o in orders), Decimal("0"))),
        "service_charge": _m(sum((o.service_charge for o in orders), Decimal("0"))),
        "vat_lines": [{"label": v["label"], "amount": _m(v["amount"])} for v in vat],
        "total": _m(sum((o.total for o in orders), Decimal("0"))),
        "amount_paid": _m(sum((p.amount for p in payments), Decimal("0"))),
        "methods": ", ".join(dict.fromkeys(str(p.get_method_display()) for p in payments)),
        "tendered": _m(sum((p.cash_tendered for p in payments if p.cash_tendered is not None), Decimal("0"))),
        "change": _m(sum((p.change_given for p in payments), Decimal("0"))),
        "staff": (staff.get_full_name() or staff.username) if staff else "",
        "footer": restaurant.receipt_footer, "currency": restaurant.currency_symbol,
    }


def build_bills(restaurant, orders, mode: str = COMBINED) -> list[dict]:
    """Snapshots (not yet numbered): one combined bill, or one per service."""
    orders = sorted(orders, key=lambda o: (o.created_at, o.pk))
    groups = by_service(orders)
    if mode == SEPARATE and len(groups) > 1:
        return [_bill(restaurant, part, business=service_types.display_name(restaurant, code) if code != service_types.CAFE
                      else restaurant.name, service=code) for code, part in groups.items()]
    only = next(iter(groups)) if len(groups) == 1 else ""
    business = restaurant.name if only in ("", service_types.CAFE) else service_types.display_name(restaurant, only)
    return [_bill(restaurant, orders, business=business, service=only)]


def _next_number(restaurant, series: str) -> str:
    seq, _created = ReceiptSequence.objects.get_or_create(restaurant=restaurant, series=series)
    seq = ReceiptSequence.objects.select_for_update().get(pk=seq.pk)
    seq.last += 1
    seq.save(update_fields=["last"])
    return f"{series}-{seq.last:04d}"


@transaction.atomic
def create_receipts(restaurant, orders, *, mode: str = COMBINED, width: int = 48, user=None) -> list[Receipt]:
    """Number and store the bills for these orders (combined: prefix R, separate: the service's prefix)."""
    width = width if width in WIDTHS else 48
    receipts = []
    for data in build_bills(restaurant, orders, mode):
        series = service_types.get(data["service"]).receipt_prefix if data["service"] else service_types.COMBINED_PREFIX
        number = _next_number(restaurant, series)
        data["number"] = number
        receipt = Receipt.objects.create(restaurant=restaurant, number=number, service_type=data["service"],
                                         data=data, width=width, paid=data["paid"], created_by=user)
        receipt.orders.set([o for o in orders if not data["service"] or o.service_type == data["service"]])
        receipts.append(receipt)
    return receipts


# ---------------------------------------------------------------- layout for thermal paper

CENTER, BOLD, BIG, NORMAL = "center", "bold", "big", "normal"


def _wrap(text: str, width: int) -> list[str]:
    return textwrap.wrap(str(text), width, break_long_words=True, break_on_hyphens=False) or [""]


def _pair(left: str, right: str, width: int, indent: int = 0) -> list[str]:
    """`left …… right` on one line; a long left side wraps onto the next lines (right stays on the first)."""
    right = str(right)
    room = max(4, width - len(right) - 1)
    parts = _wrap(left, room)
    first = parts[0].ljust(room) + " " + right
    rest = []
    for p in parts[1:]:
        rest += [" " * indent + q for q in _wrap(p, width - indent)]
    return [first[:width]] + rest


def receipt_lines(data: dict, width: int, *, copy: bool = False) -> list[tuple[str, str]]:
    """[(text, style)]; no line is longer than `width` (BIG lines: width // 2, as they print double size)."""
    sym = data.get("currency", "$")
    money = lambda v: f"{sym}{Decimal(v):.2f}"  # noqa: E731
    out = []
    add = lambda text, style=NORMAL: out.append((text, style))  # noqa: E731
    rule = "-" * width
    for part in _wrap(data["business"], width // 2):
        add(part.center(width // 2).rstrip(), BIG)
    for extra in (data.get("address"), f"Tel {data['phone']}" if data.get("phone") else "",
                  f"Tax ID {data['vat_number']}" if data.get("vat_number") else ""):
        for part in (_wrap(extra, width) if extra else []):
            add(part.center(width).rstrip(), CENTER)
    add("")
    add(data["title"].center(width).rstrip(), BOLD)
    if copy:
        add("*** COPY ***".center(width).rstrip(), BOLD)
    add(rule)
    if data.get("number"):
        for t in _pair("No.", data["number"], width):
            add(t)
    for label, key in (("Order", "orders"), ("Table", "table"), ("Customer", "customer"), ("Date", "date")):
        if data.get(key):
            for t in _pair(label, data[key], width):
                add(t)
    add(rule)
    for sec in data["sections"]:
        if data.get("show_sections"):
            for part in _wrap(sec["title"], width):
                add(part, BOLD)
        for ln in sec["lines"]:
            for t in _pair(f"{ln['qty']} x {ln['name']}", money(ln["total"]), width, indent=4):
                add(t)
            for opt in ln["options"]:
                for part in _wrap(f"+ {opt}", width - 4):
                    add("    " + part)
            if ln.get("note"):
                for part in _wrap(f'"{ln["note"]}"', width - 4):
                    add("    " + part)
        if data.get("show_sections") and len(data["sections"]) > 1:
            for t in _pair(f"Subtotal {sec['title'].split(' (')[0].title()}", money(sec["subtotal"]), width):
                add(t, BOLD)
            add("")
    add(rule)
    for t in _pair("Subtotal", money(data["subtotal"]), width):
        add(t)
    if Decimal(data["service_charge"]):
        for t in _pair("Service charge", money(data["service_charge"]), width):
            add(t)
    for v in data["vat_lines"]:
        for t in _pair(v["label"], money(v["amount"]), width):
            add(t)
    for t in _pair("TOTAL", money(data["total"]), width):
        add(t, BOLD)
    if data["paid"]:
        for t in _pair(f"Paid ({data['methods']})", money(data["amount_paid"]), width):
            add(t)
        if Decimal(data["tendered"]):
            for t in _pair("Cash received", money(data["tendered"]), width) + _pair("Change", money(data["change"]), width):
                add(t)
    else:
        add("NOT PAID".center(width).rstrip(), BOLD)
    if data.get("staff"):
        for t in _pair("Served by", data["staff"], width):
            add(t)
    add(rule)
    if data.get("footer"):
        for part in _wrap(data["footer"], width):
            add(part.center(width).rstrip(), CENTER)
    return out
