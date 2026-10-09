"""
Profit = revenue − expenses, for today / this week / this month / this year or any dates.

Revenue = completed QR/waiter orders (by the day they were placed, as on Reports and Analytics)
+ revenue entries (by their date): sales recorded by hand, and completed Ordering API orders. Expenses count by their own date.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone

from apps.orders.models import Order, OrderStatus

from .models import Expense, RevenueEntry

ZERO = Decimal("0")
DAILY_ROWS_MAX = 62  # longer periods are broken down per month


def preset_range(preset: str, today: date) -> tuple[date, date] | None:
    if preset == "today":
        return today, today
    if preset == "week":  # Monday to today
        return today - timedelta(days=today.weekday()), today
    if preset == "month":
        return today.replace(day=1), today
    if preset == "year":
        return today.replace(month=1, day=1), today
    return None


def _bounds(date_from: date, date_to: date):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(timezone.datetime.combine(date_from, timezone.datetime.min.time()), tz)
    end = timezone.make_aware(timezone.datetime.combine(date_to + timedelta(days=1), timezone.datetime.min.time()), tz)
    return start, end


def _completed(restaurant, date_from, date_to):
    """Completed QR and waiter orders. API orders are counted through their revenue entries instead."""
    start, end = _bounds(date_from, date_to)
    return Order.objects.filter(restaurant=restaurant, status=OrderStatus.COMPLETED, created_at__gte=start,
                                created_at__lt=end).exclude(source=Order.SOURCE_API)


def _entries(restaurant, date_from, date_to):
    return RevenueEntry.objects.filter(restaurant=restaurant, date__gte=date_from, date__lte=date_to)


# Revenue sources for filters: all, orders (QR & waiter), manual entries, API (all keys), or one API key ("key:<id>").
SOURCES = ("orders", "manual", "api")


def totals(restaurant, date_from: date, date_to: date, source: str = "") -> dict:
    entries = _entries(restaurant, date_from, date_to)
    if source in ("orders", "manual", "api") or source.startswith("key:"):
        orders = ZERO
        if source == "orders":
            orders = _completed(restaurant, date_from, date_to).aggregate(s=Sum("total"))["s"] or ZERO
        if source == "manual":
            entries = entries.filter(source=RevenueEntry.MANUAL)
        elif source == "api":
            entries = entries.filter(source=RevenueEntry.API)
        elif source.startswith("key:"):
            entries = entries.filter(source=RevenueEntry.API, api_key_id=source[4:] if source[4:].isdigit() else 0)
        else:
            entries = entries.none()
    else:
        orders = _completed(restaurant, date_from, date_to).aggregate(s=Sum("total"))["s"] or ZERO
    split = {r["source"]: r["s"] for r in entries.values("source").annotate(s=Sum("amount"))}
    recorded = sum(split.values(), ZERO)
    revenue = orders + recorded
    expenses = (Expense.objects.filter(restaurant=restaurant, date__gte=date_from, date__lte=date_to)
                .aggregate(s=Sum("amount"))["s"] or ZERO)
    profit = revenue - expenses
    return {
        "revenue": revenue, "orders_revenue": orders, "recorded_revenue": recorded,
        "manual_revenue": split.get(RevenueEntry.MANUAL, ZERO), "api_revenue": split.get(RevenueEntry.API, ZERO),
        "expenses": expenses, "profit": profit,
        "margin": round(float(profit * 100 / revenue), 1) if revenue else None,
    }


def at_a_glance(restaurant, today: date) -> list[dict]:
    """Today, this week, this month, this year: the question the owner asks most."""
    return [{"key": key, "from": preset_range(key, today)[0], **totals(restaurant, *preset_range(key, today))}
            for key in ("today", "week", "month", "year")]


def breakdown(restaurant, date_from: date, date_to: date) -> dict:
    """Rows per day (short periods) or per month (long periods), plus expenses per category."""
    days = (date_to - date_from).days + 1
    tz = timezone.get_current_timezone()
    monthly = days > DAILY_ROWS_MAX
    expenses = Expense.objects.filter(restaurant=restaurant, date__gte=date_from, date__lte=date_to)
    if monthly:
        rev = {r["k"].date() if hasattr(r["k"], "date") else r["k"]: r["s"] for r in
               _completed(restaurant, date_from, date_to).annotate(k=TruncMonth("created_at", tzinfo=tz))
               .values("k").annotate(s=Sum("total"))}
        exp = {r["k"]: r["s"] for r in expenses.annotate(k=TruncMonth("date")).values("k").annotate(s=Sum("amount"))}
        for r in _entries(restaurant, date_from, date_to).annotate(k=TruncMonth("date")).values("k").annotate(s=Sum("amount")):
            rev[r["k"]] = (rev.get(r["k"]) or ZERO) + r["s"]
        keys, d = [], date_from.replace(day=1)
        while d <= date_to:
            keys.append(d)
            d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    else:
        rev = {r["k"]: r["s"] for r in _completed(restaurant, date_from, date_to)
               .annotate(k=TruncDate("created_at", tzinfo=tz)).values("k").annotate(s=Sum("total"))}
        exp = {r["date"]: r["s"] for r in expenses.values("date").annotate(s=Sum("amount"))}
        for r in _entries(restaurant, date_from, date_to).values("date").annotate(s=Sum("amount")):
            rev[r["date"]] = (rev.get(r["date"]) or ZERO) + r["s"]
        keys = [date_from + timedelta(days=i) for i in range(days)]
    rows = []
    for k in keys:
        r, e = rev.get(k) or ZERO, exp.get(k) or ZERO
        rows.append({"key": k, "revenue": r, "expenses": e, "profit": r - e})
    by_cat = list(expenses.values("category").annotate(s=Sum("amount")).order_by("-s"))
    total_exp = sum((c["s"] for c in by_cat), ZERO)
    labels = dict(Expense.CATEGORY_CHOICES)
    categories = [{"label": labels.get(c["category"], c["category"]), "amount": c["s"],
                   "pct": round(float(c["s"] * 100 / total_exp), 1) if total_exp else 0} for c in by_cat]
    return {"monthly": monthly, "rows": rows, "categories": categories}
