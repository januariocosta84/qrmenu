"""
Anonymous customer access, stored in the Django session (cookie).

Scanning a table's QR code (which carries a secret token) lets *that phone*
order for the table. Several customers can share a table: each phone scans
for itself, gets its own anonymous id (`customer_ref`) and its own bill, and
its permission ends independently when:

  * the orders this phone placed since scanning are all paid (customer settled up),
  * staff freed the table (after the scan), or
  * TABLE_ACCESS_HOURS have passed since the scan.

Another customer paying never affects your access.

After that the customer must scan the QR code on the table again, which
requires being at the restaurant. Typing the plain URL without the token only
shows the menu, so nobody can place prank orders from elsewhere.
"""
import secrets
import time

from django.conf import settings

from apps.orders.models import Order, OrderStatus, PaymentStatus
from apps.restaurants.models import Table, TableSession

ACCESS_KEY = "table_access"
ORDERS_KEY = "my_orders"
CUSTOMER_KEY = "customer_ref"
MAX_REMEMBERED_ORDERS = 30

# Why a phone can no longer order (shown to the customer).
EXPIRED, PAID = "expired", "paid"


def customer_ref(request) -> str:
    """Anonymous, random id for this phone (browser); no personal data."""
    ref = request.session.get(CUSTOMER_KEY)
    if not ref:
        ref = secrets.token_hex(8)
        request.session[CUSTOMER_KEY] = ref
    return ref


def grant_table_access(request, table: Table) -> TableSession:
    session = table.current_session(create=True)  # start or continue the table's bill
    customer_ref(request)
    access = dict(request.session.get(ACCESS_KEY, {}))
    access[str(table.restaurant_id)] = {"table": table.id, "epoch": table.access_epoch, "at": time.time()}
    request.session[ACCESS_KEY] = access
    return session


def revoke_table_access(request, restaurant, reason: str = EXPIRED) -> None:
    access = dict(request.session.get(ACCESS_KEY, {}))
    entry = access.get(str(restaurant.id))
    if entry is not None:
        access[str(restaurant.id)] = {"ended": reason, "table": entry.get("table"), "at": time.time()}
        request.session[ACCESS_KEY] = access


def table_access(request, restaurant) -> tuple[Table | None, str | None]:
    """(table the phone may order for, None) or (None, reason it can't)."""
    entry = request.session.get(ACCESS_KEY, {}).get(str(restaurant.id))
    if not entry:
        return None, None
    if entry.get("ended"):
        # Keep explaining why for a while; afterwards just ask to scan.
        recent = time.time() - entry.get("at", 0) < settings.TABLE_ACCESS_HOURS * 3600
        return None, entry["ended"] if recent else None
    reason = _check(request, restaurant, entry)
    if reason:
        revoke_table_access(request, restaurant, reason)
        return None, reason
    return Table.objects.get(id=entry["table"]), None


def get_table_access(request, restaurant) -> Table | None:
    return table_access(request, restaurant)[0]


def _check(request, restaurant, entry) -> str | None:
    scanned_at = entry.get("at", 0)
    if time.time() - scanned_at > settings.TABLE_ACCESS_HOURS * 3600:
        return EXPIRED
    table = Table.objects.filter(id=entry.get("table"), restaurant=restaurant, is_active=True).first()
    if table is None:
        return EXPIRED
    if entry.get("epoch", -1) != table.access_epoch:
        return EXPIRED  # staff freed the table after this phone scanned
    # Only orders this phone placed under *this* scan count; other customers don't matter.
    mine = Order.objects.filter(public_token__in=entry.get("orders", [])).exclude(status=OrderStatus.CANCELLED)
    if mine.exists() and not mine.exclude(payment_status=PaymentStatus.PAID).exists():
        return PAID  # every order this phone placed since scanning is paid: the customer settled up
    return None


def record_scan_order(request, order: Order) -> None:
    """Remember that this order was placed under the phone's current QR scan."""
    access = dict(request.session.get(ACCESS_KEY, {}))
    entry = access.get(str(order.restaurant_id))
    if entry and not entry.get("ended"):
        entry = {**entry, "orders": [*entry.get("orders", []), str(order.public_token)][-50:]}
        access[str(order.restaurant_id)] = entry
        request.session[ACCESS_KEY] = access


def remember_order(request, order: Order) -> None:
    tokens = [t for t in request.session.get(ORDERS_KEY, []) if t != str(order.public_token)]
    tokens.insert(0, str(order.public_token))
    request.session[ORDERS_KEY] = tokens[:MAX_REMEMBERED_ORDERS]


def my_orders(request, restaurant):
    tokens = request.session.get(ORDERS_KEY, [])
    if not tokens:
        return Order.objects.none()
    return Order.objects.filter(restaurant=restaurant, public_token__in=tokens).order_by("-created_at")
