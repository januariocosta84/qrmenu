"""
Till sessions for end-of-day cash reconciliation.

Start of day: staff enter the change put in the drawer (opening float).
During the day every cash payment, plus any cash in / cash out, is added up.
End of day: staff count the drawer; the report shows expected vs counted.

    expected cash = opening float + cash payments + cash in − cash out

Cash payments are those recorded while the session is open (by `paid_at`).
A payment's `amount` is what the drawer keeps: the change given back has
already left it.
"""
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.orders.models import PaymentMethod
from apps.orders.services import OrderError

from .models import CashMovement, CashSession, Payment

ZERO = Decimal("0")

# Notes and coins used in Timor-Leste (US dollar notes + centavo coins), largest first.
USD_DENOMINATIONS = ["100", "50", "20", "10", "5", "2", "1", "0.50", "0.25", "0.10", "0.05", "0.01"]


def denominations(restaurant) -> list[str]:
    """Denominations for the counting helper, or [] to just type the total."""
    return USD_DENOMINATIONS if restaurant.currency == "USD" else []


def parse_counts(data, restaurant) -> tuple[dict, Decimal | None]:
    """Read `count_<denomination>` fields → ({"20": 2, ...}, total) ; total None if nothing counted."""
    counts, total = {}, ZERO
    for d in denominations(restaurant):
        raw = (data.get(f"count_{d}") or "").strip()
        if not raw:
            continue
        try:
            n = int(raw)
        except ValueError:
            raise OrderError(_("Enter whole numbers for the notes and coins."), code="invalid_count")
        if n < 0 or n > 100000:
            raise OrderError(_("Enter whole numbers for the notes and coins."), code="invalid_count")
        if n:
            counts[d] = n
            total += Decimal(d) * n
    return counts, (total if counts else None)


def parse_amount(raw) -> Decimal:
    raw = (str(raw or "")).strip().replace(",", ".")
    try:
        value = Decimal(raw).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise OrderError(_("Enter the amount as a number, e.g. 50 or 50.00."), code="invalid_amount")
    if value < 0 or value >= Decimal("1000000"):
        raise OrderError(_("Enter a valid amount."), code="invalid_amount")
    return value


def current_session(restaurant):
    return restaurant.cash_sessions.filter(closed_at__isnull=True).first()


def open_session(restaurant, user, amount: Decimal, counts: dict | None = None) -> CashSession:
    try:
        with transaction.atomic():
            return CashSession.objects.create(
                restaurant=restaurant, opened_by=user, opening_float=amount, opening_count=counts or {}
            )
    except IntegrityError:
        raise OrderError(_("The cash register is already open."), code="already_open")


def add_movement(session, user, kind: str, amount: Decimal, reason: str) -> CashMovement:
    if not session.is_open:
        raise OrderError(_("This cash session is already closed."), code="closed")
    if amount <= 0:
        raise OrderError(_("Enter an amount greater than zero."), code="invalid_amount")
    if not reason.strip():
        raise OrderError(_("Write a short reason, e.g. “bought ice”."), code="reason_required")
    return CashMovement.objects.create(session=session, kind=kind, amount=amount, reason=reason.strip()[:120], user=user)


def summary(session) -> dict:
    """Live (open) or final (closed) figures for a session."""
    until = session.closed_at or timezone.now()
    payments = Payment.objects.filter(
        restaurant=session.restaurant, status=Payment.SUCCEEDED, paid_at__gte=session.opened_at, paid_at__lt=until,
    )
    cash = payments.filter(method=PaymentMethod.CASH).aggregate(
        total=Sum("amount"), n=Count("id"), tendered=Sum("cash_tendered"), change=Sum("change_given"),
    )
    moves = {m["kind"]: m["s"] for m in session.movements.values("kind").annotate(s=Sum("amount"))}
    cash_sales = session.cash_sales if session.cash_sales is not None else (cash["total"] or ZERO)
    cash_in, cash_out = moves.get(CashMovement.IN) or ZERO, moves.get(CashMovement.OUT) or ZERO
    expected = (
        session.expected_cash if session.expected_cash is not None
        else session.opening_float + cash_sales + cash_in - cash_out
    )
    labels = dict(PaymentMethod.CHOICES)
    other = [
        {"method": labels.get(row["method"], row["method"]), "total": row["s"], "n": row["n"]}
        for row in payments.exclude(method=PaymentMethod.CASH).values("method").annotate(s=Sum("amount"), n=Count("id"))
    ]
    other_total = sum((o["total"] for o in other), ZERO)
    return {
        "session": session,
        "opening_float": session.opening_float,
        "cash_sales": cash_sales,
        "cash_sales_count": cash["n"] or 0,
        "cash_received": cash["tendered"] or ZERO,
        "change_given": cash["change"] or ZERO,
        "cash_in": cash_in,
        "cash_out": cash_out,
        "expected": expected,
        "counted": session.counted_cash,
        "difference": session.difference,
        "difference_abs": abs(session.difference) if session.difference is not None else None,
        "other": other,
        "other_total": other_total,
        "takings": cash_sales + other_total,  # everything taken during the session
        "movements": list(session.movements.select_related("user")),
    }


def close_session(session, user, counted: Decimal, counts: dict | None = None, note: str = "") -> CashSession:
    with transaction.atomic():
        session = CashSession.objects.select_for_update().get(pk=session.pk)
        if not session.is_open:
            raise OrderError(_("This cash session is already closed."), code="closed")
        figures = summary(session)
        session.cash_sales = figures["cash_sales"]
        session.expected_cash = figures["expected"]
        session.counted_cash = counted
        session.closing_count = counts or {}
        session.note = note.strip()[:300]
        session.closed_by = user
        session.closed_at = timezone.now()
        session.save()
    return session
