from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from apps.orders import realtime
from apps.orders.models import Order, OrderStatus, PaymentMethod, PaymentStatus
from apps.orders.services import OrderError
from apps.restaurants.models import TableSession

from .models import Payment
from .providers import ManualProvider


def amount_paid(order: Order) -> Decimal:
    total = order.payments.filter(status=Payment.SUCCEEDED).aggregate(s=Sum("amount"))["s"]
    return total or Decimal("0")


def balance_due(order: Order) -> Decimal:
    return max(order.total - amount_paid(order), Decimal("0"))


def close_bill_if_settled(table_session, user=None) -> bool:
    """Close the table's bill once every (non-cancelled) order on it is paid."""
    if table_session is None or table_session.status != TableSession.OPEN:
        return False
    orders = table_session.orders.exclude(status=OrderStatus.CANCELLED)
    if not orders.exists() or orders.exclude(payment_status=PaymentStatus.PAID).exists():
        return False
    table_session.close(user=user)
    return True


class CashResult:
    def __init__(self, payments, total_due, tendered, change):
        self.payments = payments
        self.total_due = total_due
        self.tendered = tendered
        self.change = change
        self.orders = [p.order for p in payments]


def receive_cash(orders, *, tendered: Decimal | None = None, user=None, skip_unpayable: bool = False) -> CashResult:
    """
    The customer handed over `tendered` cash for one or more orders (e.g. a
    whole table). Every order's remaining balance is paid; the difference is
    the change to give back. Without `tendered`, the exact amount is assumed.
    Raises OrderError if the cash doesn't cover the total.
    """
    with transaction.atomic():
        locked = list(Order.objects.select_for_update().filter(pk__in=[o.pk for o in orders]).order_by("created_at"))
        payable = []
        for order in locked:
            problem = None
            if order.status == OrderStatus.CANCELLED:
                problem = OrderError("This order was cancelled; there is nothing to pay.", code="cancelled")
            elif order.payment_status == PaymentStatus.PAID:
                problem = OrderError(f"Order #{order.number} is already paid.", code="already_paid")
            if problem:
                if skip_unpayable:
                    continue
                raise problem
            due = balance_due(order)
            if due > 0:
                payable.append((order, due))
        if not payable:
            raise OrderError("There is nothing left to pay.", code="nothing_due")

        total_due = sum((due for _, due in payable), Decimal("0"))
        tendered = total_due if tendered is None else tendered
        if tendered < total_due:
            sym = payable[0][0].restaurant.currency_symbol
            raise OrderError(
                f"Not enough cash: received {sym}{tendered:.2f} but {sym}{total_due:.2f} is due.",
                code="insufficient_cash",
            )
        change = tendered - total_due
        payments = []
        for i, (order, due) in enumerate(payable):
            last = i == len(payable) - 1
            payments.append(ManualProvider().record(
                order, due, PaymentMethod.CASH, user=user,
                cash_tendered=due + change if last else due,
                change_given=change if last else Decimal("0"),
            ))
        for order, _ in payable:
            order.refresh_from_db()
            transaction.on_commit(lambda o=order: realtime.broadcast_order(o, "order_updated"))
        for bill in {o.table_session for o, _ in payable if o.table_session_id}:
            close_bill_if_settled(bill, user)
    return CashResult(payments, total_due, tendered, change)


def receive_payment(
    order: Order, *, user=None, method: str = PaymentMethod.CASH, amount: Decimal | None = None, reference: str = ""
) -> Payment:
    """
    Staff received money for an order. Without `amount`, the full remaining
    balance is recorded, which marks the order Paid. Kitchen screens and the
    customer's status page are updated live.
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.status == OrderStatus.CANCELLED:
            raise OrderError("This order was cancelled; there is nothing to pay.", code="cancelled")
        if order.payment_status == PaymentStatus.PAID:
            raise OrderError(f"Order #{order.number} is already paid.", code="already_paid")
        due = balance_due(order)
        amount = due if amount is None else amount
        if amount <= 0:
            raise OrderError("Amount must be greater than zero.", code="invalid_amount")
        payment = ManualProvider().record(order, amount, method, user=user, reference=reference)
        order.refresh_from_db()
        close_bill_if_settled(order.table_session, user)
        transaction.on_commit(lambda: realtime.broadcast_order(order, "order_updated"))
    return payment
