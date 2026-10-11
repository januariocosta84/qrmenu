"""
Order business logic. Views and APIs call these functions; they never create
or change orders directly, so validation and real-time events stay consistent.
"""
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.billing.services import subscription_allows_orders
from apps.menu import service_types
from apps.menu.models import MenuItem, MenuItemOption
from apps.restaurants.models import Restaurant, Table, TableSession

from . import realtime
from .models import (
    MAX_QUANTITY, Notification, Order, OrderItem, OrderItemOption, OrderStatus,
    OrderStatusHistory, PaymentMethod,
)

CENT = Decimal("0.01")


class OrderError(Exception):
    """A business-rule violation that should be shown to the user."""

    def __init__(self, message: str, code: str = "invalid", details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def order_vat(restaurant, base: Decimal) -> dict:
    """
    VAT fields for a new order, where `base` is subtotal + service charge.
    Inclusive: the VAT is the part of `base` that is tax. Exclusive: added on top.
    """
    rate = restaurant.vat_percent if restaurant.vat_enabled else Decimal("0")
    if rate <= 0:
        return {"vat_label": "", "vat_percent": Decimal("0"), "vat_inclusive": False, "vat_amount": Decimal("0")}
    if restaurant.vat_inclusive:
        amount = _money(base * rate / (100 + rate))
    else:
        amount = _money(base * rate / 100)
    return {"vat_label": restaurant.vat_label or "VAT", "vat_percent": rate,
            "vat_inclusive": restaurant.vat_inclusive, "vat_amount": amount}


def _open_table_session(table: Table) -> TableSession:
    session = table.current_session()
    if session and session.is_stale:
        session.close()  # forgotten session from an earlier day
        session = None
    if session is None:
        session = TableSession.objects.create(table=table, restaurant_id=table.restaurant_id)
    return session


class PricedLine(NamedTuple):
    item: MenuItem
    qty: int
    options: list
    note: str
    line_total: Decimal
    unit_price: Decimal
    plate: str
    vehicle: str
    service: str


def normalize_plate(raw) -> str:
    return " ".join(str(raw or "").upper().split())[:20]


def _price_group(restaurant, lines: list[PricedLine]) -> dict:
    """Totals for the lines of one service: they become one order."""
    subtotal = _money(sum((ln.line_total for ln in lines), Decimal("0")))
    service_charge = _money(subtotal * restaurant.service_charge_percent / 100)
    vat = order_vat(restaurant, subtotal + service_charge)
    total = subtotal + service_charge + (0 if vat["vat_inclusive"] else vat["vat_amount"])
    estimate = max((ln.item.prep_minutes or restaurant.default_prep_minutes) for ln in lines)
    return {"prepared": lines, "subtotal": subtotal, "service_charge": service_charge, "vat": vat,
            "total": total, "estimate": estimate}


def price_cart(restaurant: Restaurant, lines: list[dict]) -> dict:
    """
    Validate cart lines [{"menu_item": id, "quantity": n, "options": [ids], "note": str,
    "plate_number": str, "vehicle_type": str}] against the menu and price them from the
    database. Raises OrderError. Lines are grouped per service (café, car wash, …):
    `groups` maps each service to its totals; the cart total is their sum.
    """
    if not lines:
        raise OrderError(_("Your cart is empty."), code="empty")

    item_ids = {int(line["menu_item"]) for line in lines}
    items = {
        i.id: i
        for i in MenuItem.objects.filter(
            restaurant=restaurant, id__in=item_ids, category__is_active=True
        ).select_related("category")
    }
    option_ids = {int(o) for line in lines for o in line.get("options") or []}
    options = {
        o.id: o for o in MenuItemOption.objects.filter(id__in=option_ids, menu_item__restaurant=restaurant)
    }

    unavailable = []
    prepared = []
    for line in lines:
        item = items.get(int(line["menu_item"]))
        if item is None or not item.is_available or not service_types.is_enabled(restaurant, item.service_type):
            unavailable.append(int(line["menu_item"]))
            continue
        qty = int(line["quantity"])
        if not 1 <= qty <= MAX_QUANTITY:
            raise OrderError(_("Invalid quantity."), code="invalid_quantity")
        chosen = []
        for oid in dict.fromkeys(int(o) for o in line.get("options") or []):  # de-duplicate, keep order
            option = options.get(oid)
            if option is None or option.menu_item_id != item.id:
                raise OrderError(_("Invalid add-on selected."), code="invalid_option")
            if not option.is_available:
                unavailable.append(item.id)
                break
            chosen.append(option)
        plate = vehicle = ""
        if service_types.get(item.service_type).needs_vehicle:
            plate = normalize_plate(line.get("plate_number"))
            if not plate:
                raise OrderError(_("Enter the plate number for %(item)s.") % {"item": item.name}, code="plate_required",
                                 details={"menu_item": item.id})
            vehicle = line.get("vehicle_type") or service_types.CAR
            if vehicle not in service_types.VEHICLE_CODES:
                raise OrderError(_("Invalid vehicle type."), code="invalid_vehicle")
        unit_price = item.price_for(vehicle)
        unit_total = unit_price + sum((o.price for o in chosen), Decimal("0"))
        line_total = _money(unit_total * qty)
        prepared.append(PricedLine(item, qty, chosen, (line.get("note") or "").strip()[:200], line_total,
                                   unit_price, plate, vehicle, item.service_type))

    if unavailable:
        raise OrderError(
            _("Some items are no longer available."), code="unavailable", details={"unavailable": sorted(set(unavailable))}
        )

    groups = {}
    for code in service_types.SERVICE_TYPES:  # fixed order: café first
        part = [ln for ln in prepared if ln.service == code]
        if part:
            groups[code] = _price_group(restaurant, part)
    total = sum((g["total"] for g in groups.values()), Decimal("0"))
    return {"prepared": prepared, "groups": groups, "total": total,
            "subtotal": sum((g["subtotal"] for g in groups.values()), Decimal("0")),
            "service_charge": sum((g["service_charge"] for g in groups.values()), Decimal("0"))}


def place_order(
    *,
    restaurant: Restaurant,
    table: Table | None,
    lines: list[dict],
    customer_name: str = "",
    customer_phone: str = "",
    customer_ref: str = "",
    note: str = "",
    payment_method: str = PaymentMethod.PAY_AT_COUNTER,
    language: str = "en",
    ip: str | None = None,
    placed_by=None,
    api_key=None,
    external_ref: str = "",
) -> Order:
    """
    Create an order from cart lines: [{"menu_item": id, "quantity": n,
    "options": [ids], "note": str}]. All prices come from the database.
    With `api_key`, the order comes from the Ordering API (an external ordering app).

    A cart with several services (café + car wash) becomes one order per service,
    sharing `order_group`, the table bill and the customer: café items go to the
    kitchen, car wash items to the counter. Returns the first order (café when
    present); `order.group_orders` lists them all.
    """
    if not restaurant.is_active or not restaurant.is_accepting_orders or not subscription_allows_orders(restaurant):
        raise OrderError(_("This restaurant is not accepting orders right now."), code="not_accepting")
    if table is not None and (table.restaurant_id != restaurant.id or not table.is_active):
        raise OrderError(_("Invalid table."), code="invalid_table")
    priced = price_cart(restaurant, lines)
    source = Order.SOURCE_API if api_key else Order.SOURCE_STAFF if placed_by else Order.SOURCE_QR
    group = uuid.uuid4() if len(priced["groups"]) > 1 else None

    created = []
    with transaction.atomic():
        session = _open_table_session(table) if table else None
        for service, part in priced["groups"].items():
            plates = ", ".join(dict.fromkeys(ln.plate for ln in part["prepared"] if ln.plate))
            order = Order.objects.create(
                restaurant=restaurant,
                table=table,
                table_session=session,
                table_number=table.number if table else "",
                number=restaurant.allocate_order_number(),
                customer_ref=customer_ref[:16],
                customer_name=customer_name.strip()[:80],
                customer_phone=customer_phone.strip()[:30],
                note=note.strip()[:500],
                language=language,
                currency=restaurant.currency,
                subtotal=part["subtotal"],
                service_charge=part["service_charge"],
                **part["vat"],
                total=part["total"],
                payment_method=payment_method,
                estimated_minutes=part["estimate"] if service_types.get(service).kitchen else None,
                placed_ip=ip,
                source=source,
                placed_by=placed_by,
                api_key=api_key,
                external_ref=external_ref.strip()[:64],
                service_type=service,
                order_group=group,
                plate_number=plates[:60],
            )
            for ln in part["prepared"]:
                order_item = OrderItem.objects.create(
                    order=order, menu_item=ln.item, name=ln.item.name, unit_price=ln.unit_price,
                    quantity=ln.qty, note=ln.note, line_total=ln.line_total,
                    plate_number=ln.plate, vehicle_type=ln.vehicle,
                )
                OrderItemOption.objects.bulk_create(
                    [OrderItemOption(order_item=order_item, option=o, name=o.name, price=o.price) for o in ln.options]
                )
            OrderStatusHistory.objects.create(order=order, from_status="", to_status=OrderStatus.NEW)
            where = (f"Table {order.table_number}" if order.table_number
                     else f"API · {api_key.name}" if api_key else "Counter")
            if service_types.get(service).needs_vehicle:
                title = f"{service_types.display_name(restaurant, service)} #{order.number} — {where} · {plates}"
            else:
                title = f"New order #{order.number} — {where}"
            Notification.objects.create(
                restaurant=restaurant, order=order, kind=Notification.NEW_ORDER, title=title[:120],
                body=", ".join(f"{ln.item.name} × {ln.qty}" for ln in part["prepared"])[:255],
            )
            transaction.on_commit(lambda o=order: realtime.broadcast_order(o, "new_order"))
            transaction.on_commit(lambda o=order: _order_event(o, "order.created"))
            if not service_types.get(service).kitchen:
                transaction.on_commit(lambda o=order: _counter_ticket(o))
            created.append(order)
    primary = created[0]
    primary.group_orders = created
    return primary


def _counter_ticket(order):
    """Car wash (non-kitchen) orders: print the counter ticket on the network printer if set up."""
    from apps.payments.tickets import auto_print_ticket

    auto_print_ticket(order)


def _order_event(order, event):
    from apps.integrations.webhooks import order_event

    order_event(order, event)


_TIMESTAMP_FIELD = {
    OrderStatus.ACCEPTED: "accepted_at",
    OrderStatus.PREPARING: "preparing_at",
    OrderStatus.READY: "ready_at",
    OrderStatus.COMPLETED: "completed_at",
    OrderStatus.CANCELLED: "cancelled_at",
}


def change_status(order: Order, to_status: str, *, user=None, note: str = "") -> Order:
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.status == to_status:
            return order
        if not order.can_transition(to_status):
            raise OrderError(
                _("Cannot change order from %(from)s to %(to)s.") % {
                    "from": order.get_status_display(), "to": dict(OrderStatus.CHOICES)[to_status]},
                code="invalid_transition",
            )
        now = timezone.now()
        from_status = order.status
        order.status = to_status
        fields = ["status", "updated_at", _TIMESTAMP_FIELD[to_status]]
        setattr(order, _TIMESTAMP_FIELD[to_status], now)
        if to_status == OrderStatus.PREPARING and not order.accepted_at:
            order.accepted_at = now
            fields.append("accepted_at")
        if to_status == OrderStatus.CANCELLED:
            order.cancel_reason = note[:200]
            fields.append("cancel_reason")
        order.save(update_fields=fields)
        OrderStatusHistory.objects.create(
            order=order, from_status=from_status, to_status=to_status, changed_by=user, note=note[:200]
        )
        if to_status == OrderStatus.COMPLETED and order.source == Order.SOURCE_API:
            from apps.integrations.services import record_api_revenue

            record_api_revenue(order)  # API orders count as revenue once completed, with their source
        transaction.on_commit(lambda: realtime.broadcast_order(order, "order_updated"))
        transaction.on_commit(lambda: _order_event(order, "order.status_changed"))
    return order
