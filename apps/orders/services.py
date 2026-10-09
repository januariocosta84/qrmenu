"""
Order business logic. Views and APIs call these functions; they never create
or change orders directly, so validation and real-time events stay consistent.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.billing.services import subscription_allows_orders
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


def price_cart(restaurant: Restaurant, lines: list[dict]) -> dict:
    """
    Validate cart lines [{"menu_item": id, "quantity": n, "options": [ids], "note": str}] against the
    menu and price them from the database. Raises OrderError. Used by place_order and API test orders.
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
    subtotal = Decimal("0")
    for line in lines:
        item = items.get(int(line["menu_item"]))
        if item is None or not item.is_available:
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
        unit_total = item.price + sum((o.price for o in chosen), Decimal("0"))
        line_total = _money(unit_total * qty)
        subtotal += line_total
        prepared.append((item, qty, chosen, (line.get("note") or "").strip()[:200], line_total))

    if unavailable:
        raise OrderError(
            _("Some items are no longer available."), code="unavailable", details={"unavailable": sorted(set(unavailable))}
        )

    subtotal = _money(subtotal)
    service_charge = _money(subtotal * restaurant.service_charge_percent / 100)
    vat = order_vat(restaurant, subtotal + service_charge)
    total = subtotal + service_charge + (0 if vat["vat_inclusive"] else vat["vat_amount"])
    estimate = max((i.prep_minutes or restaurant.default_prep_minutes) for i, *_ in prepared)
    return {"prepared": prepared, "subtotal": subtotal, "service_charge": service_charge, "vat": vat,
            "total": total, "estimate": estimate}


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
    """
    if not restaurant.is_active or not restaurant.is_accepting_orders or not subscription_allows_orders(restaurant):
        raise OrderError(_("This restaurant is not accepting orders right now."), code="not_accepting")
    if table is not None and (table.restaurant_id != restaurant.id or not table.is_active):
        raise OrderError(_("Invalid table."), code="invalid_table")
    priced = price_cart(restaurant, lines)
    prepared, subtotal, service_charge = priced["prepared"], priced["subtotal"], priced["service_charge"]
    vat, total, estimate = priced["vat"], priced["total"], priced["estimate"]
    source = Order.SOURCE_API if api_key else Order.SOURCE_STAFF if placed_by else Order.SOURCE_QR

    with transaction.atomic():
        number = restaurant.allocate_order_number()
        session = _open_table_session(table) if table else None
        order = Order.objects.create(
            restaurant=restaurant,
            table=table,
            table_session=session,
            table_number=table.number if table else "",
            number=number,
            customer_ref=customer_ref[:16],
            customer_name=customer_name.strip()[:80],
            customer_phone=customer_phone.strip()[:30],
            note=note.strip()[:500],
            language=language,
            currency=restaurant.currency,
            subtotal=subtotal,
            service_charge=service_charge,
            **vat,
            total=total,
            payment_method=payment_method,
            estimated_minutes=estimate,
            placed_ip=ip,
            source=source,
            placed_by=placed_by,
            api_key=api_key,
            external_ref=external_ref.strip()[:64],
        )
        for item, qty, chosen, line_note, line_total in prepared:
            order_item = OrderItem.objects.create(
                order=order, menu_item=item, name=item.name, unit_price=item.price,
                quantity=qty, note=line_note, line_total=line_total,
            )
            OrderItemOption.objects.bulk_create(
                [OrderItemOption(order_item=order_item, option=o, name=o.name, price=o.price) for o in chosen]
            )
        OrderStatusHistory.objects.create(order=order, from_status="", to_status=OrderStatus.NEW)
        where = (f"Table {order.table_number}" if order.table_number
                 else f"API · {api_key.name}" if api_key else "Counter")
        Notification.objects.create(
            restaurant=restaurant, order=order, kind=Notification.NEW_ORDER,
            title=f"New order #{order.number} — {where}"[:120],
            body=", ".join(f"{i.name} × {q}" for i, q, *_ in prepared)[:255],
        )
        transaction.on_commit(lambda: realtime.broadcast_order(order, "new_order"))
        transaction.on_commit(lambda: _order_event(order, "order.created"))
    return order


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
