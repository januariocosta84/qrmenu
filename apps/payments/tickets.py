"""
Counter tickets for services without a kitchen (car wash): what the car wash team needs.

    CAR WASH – Table 5
    Plate: 12-345 TL
    Package: Full Wash (Pickup / SUV)
    Time: 14:32

Car wash staff don't use the app: they work from this ticket, printed on the network
receipt printer (if set up) or from the dashboard's Car wash screen.
"""
import logging
import threading

from django.utils import timezone

from apps.menu import service_types

from .bills import BIG, BOLD, NORMAL, _wrap
from .escpos import escpos_from_lines
from .receipts import network_printing_available, send_escpos

logger = logging.getLogger(__name__)
VEHICLE_LABELS = dict(service_types.VEHICLE_TYPES)


def ticket_lines(order, width: int) -> list[tuple[str, str]]:
    """One block per car (order line); tickets are cut between orders, not between cars."""
    name = service_types.display_name(order.restaurant, order.service_type).upper()
    where = f"Table {order.table_number}" if order.table_number else "Walk-in"
    out = []
    for i, item in enumerate(order.items.all()):
        if i:
            out.append(("-" * width, NORMAL))
        for part in _wrap(f"{name} - {where}", width // 2):
            out.append((part, BIG))
        package = item.name + (f" x {item.quantity}" if item.quantity > 1 else "")
        if item.vehicle_type:
            package += f" ({VEHICLE_LABELS.get(item.vehicle_type, item.vehicle_type)})"
        for label, value, style in (("Plate", item.plate_number or "-", BOLD), ("Package", package, NORMAL),
                                    ("Time", f"{timezone.localtime(order.created_at):%H:%M}", NORMAL)):
            for part in _wrap(f"{label}: {value}", width):
                out.append((part, style))
        if item.note:
            for part in _wrap(f'Note: {item.note}', width):
                out.append((part, NORMAL))
        if order.customer_name:
            out.append((f"Customer: {order.customer_name}"[:width], NORMAL))
        out.append((f"Order #{order.number}", NORMAL))
    return out


def print_ticket_network(order):
    r = order.restaurant
    return send_escpos(r, escpos_from_lines(ticket_lines(order, r.receipt_width)))


def auto_print_ticket(order) -> None:
    """New car wash order: print its ticket on the network printer, in the background."""
    r = order.restaurant
    if service_types.get(order.service_type).kitchen or not r.car_wash_auto_print or not network_printing_available(r):
        return

    payload = escpos_from_lines(ticket_lines(order, r.receipt_width))  # database work stays in this thread

    def run():
        try:
            result = send_escpos(r, payload)
            if not result.printed:
                logger.warning("Car wash ticket for order %s not printed: %s", order.pk, result.message)
        except Exception:
            logger.exception("Car wash ticket for order %s failed", order.pk)

    threading.Thread(target=run, daemon=True).start()
