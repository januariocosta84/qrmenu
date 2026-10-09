"""Ordering API rules: who may use it, which branches a key reaches, order JSON, and API revenue."""
from django.utils import timezone

from apps.billing.services import get_subscription
from apps.orders.models import Order, OrderStatus, PaymentMethod, PaymentStatus
from apps.restaurants.models import BusinessProfile, Restaurant

# Statuses as the API names them. "received" covers New and Accepted.
API_STATUSES = ("received", "preparing", "ready", "completed", "cancelled")
TO_API_STATUS = {OrderStatus.NEW: "received", OrderStatus.ACCEPTED: "received", OrderStatus.PREPARING: "preparing",
                 OrderStatus.READY: "ready", OrderStatus.COMPLETED: "completed", OrderStatus.CANCELLED: "cancelled"}
FROM_API_STATUS = {"received": OrderStatus.ACCEPTED, "preparing": OrderStatus.PREPARING, "ready": OrderStatus.READY,
                   "completed": OrderStatus.COMPLETED, "cancelled": OrderStatus.CANCELLED}
# Payment methods an ordering app can send → how the order records them.
API_PAYMENT_METHODS = {
    "pay_at_counter": PaymentMethod.PAY_AT_COUNTER, "cash": PaymentMethod.CASH, "card": PaymentMethod.CARD,
    "bank_transfer": PaymentMethod.BANK_TRANSFER, "online": PaymentMethod.ONLINE_GATEWAY,
    "qr": PaymentMethod.QR_PAYMENT,
}
TO_API_PAYMENT = {v: k for k, v in API_PAYMENT_METHODS.items()}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def access_problem(main: Restaurant) -> ApiError | None:
    """Why this main branch can't use the API right now (None = it can)."""
    sub = get_subscription(main)
    if sub is not None and not sub.plan.has_feature("api"):
        return ApiError(403, "plan_required", "API access requires the Pro plan. This account's plan does not include "
                                              "the Ordering API; upgrade to Pro in the dashboard to enable it.")
    if sub is not None and not sub.allows_orders:
        return ApiError(403, "subscription_inactive", "API access is disabled because the Pro subscription has expired "
                                                      "or was cancelled. Pay the open invoice to enable it again.")
    profile = BusinessProfile.objects.filter(restaurant=main).only("status").first()
    if profile is None or profile.status != BusinessProfile.VERIFIED:
        return ApiError(403, "profile_not_verified", "API access needs a verified business profile.")
    return None


def family(main: Restaurant):
    """The main branch and its sub-branches that are live (approved, not deactivated)."""
    return Restaurant.objects.filter(
        pk__in=[main.pk, *main.sub_branches.filter(branch_status=Restaurant.BRANCH_ACTIVE, branch_closed_at__isnull=True)
                .values_list("pk", flat=True)]
    ).order_by("parent_id", "name")


def allowed_branches(key):
    """Branches this key may use: the live family, limited to the key's chosen branches (if any)."""
    qs = family(key.restaurant)
    chosen = list(key.branches.values_list("pk", flat=True))
    return qs.filter(pk__in=chosen) if chosen else qs


def branch_json(b: Restaurant) -> dict:
    return {"id": b.pk, "name": b.name, "is_main_branch": b.is_main_branch, "address": b.address, "phone": b.phone,
            "accepting_orders": b.is_active and b.is_accepting_orders, "currency": b.currency}


def _items_json(order: Order) -> list:
    return [{"name": i.name, "quantity": i.quantity, "unit_price": str(i.unit_price), "line_total": str(i.line_total),
             "note": i.note, "options": [{"name": o.name, "price": str(o.price)} for o in i.options.all()]}
            for i in order.items.all()]


def order_json(order: Order) -> dict:
    return {
        "id": str(order.public_token), "number": order.number, "sandbox": False,
        "branch": {"id": order.restaurant_id, "name": order.restaurant.name},
        "status": TO_API_STATUS[order.status], "source": order.source,
        "items": _items_json(order),
        "subtotal": str(order.subtotal), "service_charge": str(order.service_charge), "vat": str(order.vat_amount),
        "total": str(order.total), "currency": order.currency,
        "payment_method": TO_API_PAYMENT.get(order.payment_method, order.payment_method),
        "paid": order.payment_status == PaymentStatus.PAID,
        "customer_name": order.customer_name, "customer_phone": order.customer_phone, "note": order.note,
        "external_ref": order.external_ref, "cancel_reason": order.cancel_reason,
        "created_at": order.created_at.isoformat(), "updated_at": order.updated_at.isoformat(),
    }


def sandbox_json(s) -> dict:
    return {"id": str(s.token), "number": s.number, "sandbox": True,
            "branch": {"id": s.branch_id, "name": s.branch.name}, "status": s.status, "source": "api",
            **s.data, "created_at": s.created_at.isoformat(), "updated_at": s.updated_at.isoformat()}


def record_api_revenue(order: Order):
    """A completed API order becomes a revenue entry of its branch, marked "API – <key name>"."""
    from apps.expenses.models import RevenueEntry

    if order.status != OrderStatus.COMPLETED or RevenueEntry.objects.filter(order=order).exists():
        return None
    method = {PaymentMethod.CASH: RevenueEntry.CASH, PaymentMethod.PAY_AT_COUNTER: RevenueEntry.CASH,
              PaymentMethod.BANK_TRANSFER: RevenueEntry.TRANSFER}.get(order.payment_method, RevenueEntry.OTHER)
    key = order.api_key
    return RevenueEntry.objects.create(
        restaurant_id=order.restaurant_id, order=order, amount=order.total, method=method,
        date=timezone.localtime(order.created_at).date(),  # the day it was ordered, like other order revenue
        note=f"Order #{order.number}" + (f" · {order.external_ref}" if order.external_ref else ""),
        source=RevenueEntry.API, api_key=key, api_key_name=key.name if key else "",
    )
