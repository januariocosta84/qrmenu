"""
Sales analytics for the restaurant dashboard.

Everything is computed for one date range and compared with the period of the
same length just before it. Revenue counts completed orders (as on Reports);
busy times count every order that wasn't cancelled.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import ExtractHour, ExtractIsoWeekDay, TruncDate
from django.utils import timezone

from apps.menu.models import MenuItem
from apps.orders.models import Order, OrderItem, OrderStatus, PaymentMethod
from apps.payments.models import Payment

ZERO = Decimal("0")
MAX_DAYS = 366
# Each payment method keeps one chart colour (categorical slot), whatever else is shown.
PAYMENT_SLOTS = {
    PaymentMethod.CASH: 1, PaymentMethod.CARD: 2, PaymentMethod.BANK_TRANSFER: 3,
    PaymentMethod.QR_PAYMENT: 4, PaymentMethod.MANUAL: 5,
}


def day_bounds(day: date):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time()), tz)
    return start, start + timedelta(days=1)


def period_range(date_from: date, date_to: date):
    return day_bounds(date_from)[0], day_bounds(date_to)[1]


def _kpis(restaurant, start, end) -> dict:
    agg = Order.objects.filter(restaurant=restaurant, created_at__gte=start, created_at__lt=end).aggregate(
        orders=Count("id"),
        completed=Count("id", filter=Q(status=OrderStatus.COMPLETED)),
        cancelled=Count("id", filter=Q(status=OrderStatus.CANCELLED)),
        revenue=Sum("total", filter=Q(status=OrderStatus.COMPLETED)),
    )
    revenue = agg["revenue"] or ZERO
    return {
        "revenue": revenue,
        "completed": agg["completed"],
        "avg": (revenue / agg["completed"]).quantize(Decimal("0.01")) if agg["completed"] else None,
        "cancel_rate": round(100 * agg["cancelled"] / agg["orders"], 1) if agg["orders"] else None,
        "orders": agg["orders"],
    }


def _delta(now, before):
    """% change vs the previous period; None when there's nothing to compare."""
    if now is None or not before:
        return None
    return round(float((Decimal(now) - Decimal(before)) * 100 / Decimal(before)), 1)


def build(restaurant, date_from: date, date_to: date) -> dict:
    days = (date_to - date_from).days + 1
    start, end = period_range(date_from, date_to)
    prev_from = date_from - timedelta(days=days)
    prev_start, prev_end = period_range(prev_from, date_from - timedelta(days=1))
    tz = timezone.get_current_timezone()

    orders = Order.objects.filter(restaurant=restaurant, created_at__gte=start, created_at__lt=end)
    completed = orders.filter(status=OrderStatus.COMPLETED)

    # ---- KPIs with change vs the previous period
    now, before = _kpis(restaurant, start, end), _kpis(restaurant, prev_start, prev_end)
    kpis = [
        {"key": "revenue", "value": now["revenue"], "delta": _delta(now["revenue"], before["revenue"]), "up_good": True},
        {"key": "completed", "value": now["completed"], "delta": _delta(now["completed"], before["completed"]), "up_good": True},
        {"key": "avg", "value": now["avg"], "delta": _delta(now["avg"], before["avg"]), "up_good": True},
        {"key": "cancel_rate", "value": now["cancel_rate"],
         "delta_pts": (round(now["cancel_rate"] - before["cancel_rate"], 1)
                       if now["cancel_rate"] is not None and before["cancel_rate"] is not None else None),
         "up_good": False},
    ]

    # ---- Revenue per day, this period vs the previous one (aligned by day number)
    def daily(qs):
        rows = qs.annotate(day=TruncDate("created_at", tzinfo=tz)).values("day").annotate(r=Sum("total"))
        return {r["day"]: r["r"] or ZERO for r in rows}

    cur = daily(completed)
    prev = daily(Order.objects.filter(restaurant=restaurant, status=OrderStatus.COMPLETED,
                                      created_at__gte=prev_start, created_at__lt=prev_end))
    trend = [
        {"day": (date_from + timedelta(days=i)).isoformat(),
         "prev_day": (prev_from + timedelta(days=i)).isoformat(),
         "revenue": float(cur.get(date_from + timedelta(days=i), ZERO)),
         "prev": float(prev.get(prev_from + timedelta(days=i), ZERO))}
        for i in range(days)
    ]

    # ---- Busy times: orders by weekday (Mon=1) × hour, cancelled excluded
    busy = {}
    for row in (orders.exclude(status=OrderStatus.CANCELLED)
                .annotate(wd=ExtractIsoWeekDay("created_at", tzinfo=tz), hr=ExtractHour("created_at", tzinfo=tz))
                .values("wd", "hr").annotate(n=Count("id"))):
        busy[(row["wd"], row["hr"])] = row["n"]
    hours = sorted({h for _, h in busy}) or list(range(8, 22))
    hours = list(range(min(hours), max(hours) + 1))
    heatmap = [[busy.get((wd, h), 0) for h in hours] for wd in range(1, 8)]
    by_hour = [sum(heatmap[wd][i] for wd in range(7)) for i in range(len(hours))]
    by_day = [sum(row) for row in heatmap]

    # ---- What sells: categories and dishes (completed orders)
    items = OrderItem.objects.filter(order__in=completed)
    categories = list(
        items.values("menu_item__category__name")
        .annotate(revenue=Sum("line_total"), qty=Sum("quantity")).order_by("-revenue")
    )
    for c in categories:
        c["name"] = c.pop("menu_item__category__name") or "—"
    top_items = list(items.values("name").annotate(qty=Sum("quantity"), revenue=Sum("line_total")).order_by("-revenue")[:10])
    sold_ids = set(items.exclude(menu_item=None).values_list("menu_item_id", flat=True))
    not_sold = list(MenuItem.objects.filter(restaurant=restaurant, is_available=True).exclude(id__in=sold_ids)
                    .select_related("category").order_by("category__position", "position", "name")[:12])

    # ---- How customers pay (succeeded payments in the period)
    labels = dict(PaymentMethod.CHOICES)
    pay_rows = (Payment.objects.filter(restaurant=restaurant, status=Payment.SUCCEEDED, paid_at__gte=start, paid_at__lt=end)
                .values("method").annotate(total=Sum("amount"), n=Count("id")).order_by("-total"))
    pay_total = sum((r["total"] for r in pay_rows), ZERO)
    payments = sorted(
        [{"method": r["method"], "label": str(labels.get(r["method"], r["method"])), "total": r["total"], "n": r["n"],
          "share": round(float(r["total"] * 100 / pay_total), 1) if pay_total else 0,
          "slot": PAYMENT_SLOTS.get(r["method"], 5)} for r in pay_rows],
        key=lambda p: p["slot"],
    )

    # ---- Where orders come from
    src = orders.exclude(status=OrderStatus.CANCELLED).aggregate(
        qr=Count("id", filter=Q(source=Order.SOURCE_QR)), staff=Count("id", filter=Q(source=Order.SOURCE_STAFF)),
        api=Count("id", filter=Q(source=Order.SOURCE_API)),
        table=Count("id", filter=~Q(table_number="")), counter=Count("id", filter=Q(table_number="")),
    )
    total_src = (src["qr"] + src["staff"] + src["api"]) or 1

    # ---- Kitchen speed: order placed → ready
    times = [(o["ready_at"] - o["created_at"]).total_seconds() / 60 for o in
             orders.exclude(ready_at=None).values("created_at", "ready_at")]
    on_time = [o for o in orders.exclude(ready_at=None).exclude(estimated_minutes=None)
               .values("created_at", "ready_at", "estimated_minutes")]
    kitchen = {
        "n": len(times),
        "avg": round(sum(times) / len(times)) if times else None,
        "median": round(sorted(times)[len(times) // 2]) if times else None,
        "on_time": (round(100 * sum(1 for o in on_time if (o["ready_at"] - o["created_at"]).total_seconds() / 60
                                    <= o["estimated_minutes"]) / len(on_time)) if on_time else None),
    }

    # Bar lengths / heat levels as % of the largest value (for the templates).
    top_cat = max((c["revenue"] or ZERO for c in categories), default=ZERO) or 1
    for c in categories:
        c["pct"] = round(float((c["revenue"] or ZERO) * 100 / top_cat), 1)
    top_item = max((i["revenue"] or ZERO for i in top_items), default=ZERO) or 1
    for i in top_items:
        i["pct"] = round(float((i["revenue"] or ZERO) * 100 / top_item), 1)
    peak = max((n for row in heatmap for n in row), default=0) or 1
    heat_rows = [[{"n": n, "hour": hours[i], "level": round(15 + 85 * n / peak) if n else 0} for i, n in enumerate(row)]
                 for row in heatmap]

    return {
        "days": days, "heat_rows": heat_rows, "heat_peak": peak if any(by_hour) else 0, "prev_from": prev_from, "prev_to": date_from - timedelta(days=1),
        "kpis": kpis, "trend": trend,
        "hours": hours, "heatmap": heatmap, "by_hour": by_hour, "by_day": by_day,
        "busiest_hour": hours[by_hour.index(max(by_hour))] if any(by_hour) else None,
        "busiest_day": by_day.index(max(by_day)) if any(by_day) else None,
        "categories": categories, "top_items": top_items, "not_sold": not_sold,
        "payments": payments, "pay_total": pay_total,
        "source": {**src, "qr_pct": round(100 * src["qr"] / total_src), "staff_pct": round(100 * src["staff"] / total_src),
                   "api_pct": round(100 * src["api"] / total_src),
                   "table_pct": round(100 * src["table"] / total_src), "counter_pct": round(100 * src["counter"] / total_src)},
        "kitchen": kitchen,
    }
