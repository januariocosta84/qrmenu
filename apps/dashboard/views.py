import csv
from decimal import Decimal, InvalidOperation
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, ProtectedError, Q, RestrictedError, Sum
from django.db.models.functions import TruncDate
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST

from apps.billing.services import plan_limit, subscription_allows_orders
from apps.core.permissions import Role
from apps.menu.models import MenuCategory, MenuItem
from apps.menu.queries import public_menu
from apps.menu.serializers import PublicItemSerializer
from apps.orders import realtime
from apps.orders.models import Notification, Order, OrderItem, OrderStatus, PaymentMethod, PaymentStatus
from apps.orders.services import OrderError, change_status
from apps.payments.drawer import open_cash_drawer
from apps.payments.receipts import (
    network_printing_available, parse_order_ids, print_receipt_network, receipt_data, receipt_orders,
)
from apps.payments.models import CashDrawerOpening
from apps.payments.services import balance_due, receive_cash, receive_payment
from apps.restaurants.models import RestaurantStaff, Table, TableSession
from apps.restaurants.qr import qr_png, qr_svg

from .decorators import staff_view
from .forms import (
    BulkTableForm, CategoryForm, MenuItemForm, OptionFormSet, OrderFilterForm, PaymentForm,
    RestaurantForm, StaffCreateForm, StaffEditForm, TableForm,
)

User = get_user_model()


def _go(request, name, *args):
    return redirect(f"dashboard:{name}", request.restaurant.slug, *args)


def _local_day_bounds(day):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), tz)
    return start, start + timedelta(days=1)


# ---------------------------------------------------------------- home

@login_required
def home(request):
    memberships = list(request.user.restaurant_memberships())
    if len(memberships) == 1:
        return redirect("dashboard:overview", memberships[0].restaurant.slug)
    restaurants = [m.restaurant for m in memberships]
    if request.user.is_superuser and not restaurants:
        from apps.restaurants.models import Restaurant

        restaurants = list(Restaurant.objects.all())
    return render(request, "dashboard/home.html", {"restaurants": restaurants})


@staff_view("view_dashboard")
def overview(request):
    r = request.restaurant
    start, end = _local_day_bounds(timezone.localdate())
    today = Order.objects.filter(restaurant=r, created_at__gte=start, created_at__lt=end)
    stats = today.aggregate(
        count=Count("id"),
        revenue=Sum("total", filter=Q(status=OrderStatus.COMPLETED)),
        cancelled=Count("id", filter=Q(status=OrderStatus.CANCELLED)),
    )
    active = (
        Order.objects.filter(restaurant=r, status__in=OrderStatus.ACTIVE)
        .values("status").annotate(n=Count("id"))
    )
    return render(request, "dashboard/overview.html", {
        "stats": stats,
        "active": {row["status"]: row["n"] for row in active},
        "recent": Order.objects.filter(restaurant=r).prefetch_related("items").annotate(
            paid_sum=Sum("payments__amount", filter=Q(payments__status="succeeded"))
        ).order_by("-created_at")[:8],
        "open_sessions": TableSession.objects.filter(restaurant=r, status=TableSession.OPEN).count(),
        "sold_out": MenuItem.objects.filter(restaurant=r, is_available=False).count(),
        "setup_steps": _setup_steps(r),
    })


def _setup_steps(r):
    """Getting-started checklist for new restaurants; hidden once everything is done."""
    slug = r.slug
    item_count = MenuItem.objects.filter(restaurant=r).count()
    steps = [
        ("Add your logo, address and opening hours", bool(r.logo or r.address or r.opening_hours),
         reverse("dashboard:settings", args=[slug])),
        ("Create menu categories (e.g. Rice, Drinks)", MenuCategory.objects.filter(restaurant=r).exists(),
         reverse("dashboard:category_create", args=[slug])),
        ("Add your dishes with photos and prices", item_count >= 3, reverse("dashboard:item_create", args=[slug])),
        ("Create your tables", Table.objects.filter(restaurant=r).exists(), reverse("dashboard:tables", args=[slug])),
        ("Print the QR codes and place one on each table", Order.objects.filter(restaurant=r).exists(),
         reverse("dashboard:tables_print", args=[slug])),
        ("Add kitchen and waiter accounts", r.staff.count() > 1, reverse("dashboard:staff", args=[slug])),
    ]
    done = sum(1 for _, ok, _ in steps if ok)
    if done == len(steps):
        return None
    return {
        "steps": [{"label": label, "done": ok, "url": url} for label, ok, url in steps],
        "done": done, "total": len(steps), "pct": int(done * 100 / len(steps)),
    }


# ---------------------------------------------------------------- kitchen

@staff_view("kitchen")
def kitchen(request):
    return render(request, "dashboard/kitchen.html", {
        "kds_config": {
            "ordersUrl": reverse("dashboard:api_orders", args=[request.restaurant.slug]) + "?active=1",
            "statusUrl": reverse("dashboard:api_order_status", args=[request.restaurant.slug, 0]),
            "canCancel": request.can["cancel_orders"],
            "canPay": request.can["payments"],
            "paidUrl": reverse("dashboard:api_order_payment", args=[request.restaurant.slug, 0]),
            "currencySymbol": request.restaurant.currency_symbol,
        }
    })


# ---------------------------------------------------------------- orders

@staff_view("view_orders")
def orders(request):
    r = request.restaurant
    status = request.GET.get("status", "")
    qs = Order.objects.filter(restaurant=r).prefetch_related("items").annotate(
        paid_sum=Sum("payments__amount", filter=Q(payments__status="succeeded"))
    )
    if status in dict(OrderStatus.CHOICES):
        qs = qs.filter(status=status)
    form = OrderFilterForm(request.GET or None)
    if form.is_valid():
        d = form.cleaned_data
        if d["q"]:
            q = d["q"].lstrip("#")
            qs = qs.filter(Q(number=q) if q.isdigit() else Q(customer_name__icontains=q))
        if d["table"]:
            qs = qs.filter(table_number__iexact=d["table"])
        if d["date_from"]:
            qs = qs.filter(created_at__gte=_local_day_bounds(d["date_from"])[0])
        if d["date_to"]:
            qs = qs.filter(created_at__lt=_local_day_bounds(d["date_to"])[1])
    counts = dict(
        Order.objects.filter(restaurant=r).values_list("status").annotate(n=Count("id")).values_list("status", "n")
    )
    page = Paginator(qs.order_by("-created_at"), 30).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "dashboard/orders.html", {
        "page": page,
        "status": status,
        "tabs": [(c, label, counts.get(c, 0)) for c, label in OrderStatus.CHOICES],
        "form": form,
        "querystring": params.urlencode(),
    })


@staff_view("take_orders")
def new_order(request):
    """Waiter order-entry screen (POS) for guests without a smartphone."""
    r = request.restaurant
    tables = sorted(Table.objects.filter(restaurant=r, is_active=True), key=lambda t: t.sort_key)
    open_sessions = {s.table_id: s for s in TableSession.objects.filter(restaurant=r, status=TableSession.OPEN)}
    guests_by_table = {}
    for t in tables:
        session = open_sessions.get(t.id)
        if session:
            orders = session.orders.order_by("created_at")
            guests_by_table[str(t.id)] = [
                {"ref": g["ref"], "label": g["label"]} for g in _guest_bills(orders) if g["ref"]
            ]
    categories = list(public_menu(r))
    menu = [
        {"id": c.id, "name": c.name,
         "items": [PublicItemSerializer(i, context={"lang": "en"}).data for i in c.items.all()]}
        for c in categories
    ]
    preselect = request.GET.get("table", "")
    return render(request, "dashboard/new_order.html", {
        "tables": tables,
        "pos_config": {
            "menu": menu,
            "tables": [{"id": t.id, "number": t.number, "label": t.label} for t in tables],
            "guests": guests_by_table,
            "table": int(preselect) if preselect.isdigit() else None,
            "guest": request.GET.get("guest", ""),
            "createUrl": reverse("dashboard:api_order_create", args=[r.slug]),
            "tableUrl": reverse("dashboard:table_detail", args=[r.slug, 0]),
            "orderUrl": reverse("dashboard:order_detail", args=[r.slug, 0]),
            "currencySymbol": r.currency_symbol,
            "serviceChargePercent": str(r.service_charge_percent),
            "acceptingOrders": r.is_accepting_orders and r.is_active and subscription_allows_orders(r),
        },
    })


@staff_view("view_orders")
def order_detail(request, pk):
    r = request.restaurant
    order = get_object_or_404(
        Order.objects.prefetch_related("items__options", "status_history__changed_by", "payments"),
        restaurant=r, pk=pk,
    )
    due = balance_due(order)
    payment_form = PaymentForm(initial={"amount": due, "method": "cash"})

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "status" and request.can["kitchen"]:
            to_status = request.POST.get("status", "")
            if to_status == OrderStatus.CANCELLED and not request.can["cancel_orders"]:
                messages.error(request, "Your role cannot cancel orders.")
            elif to_status in dict(OrderStatus.CHOICES):
                try:
                    change_status(order, to_status, user=request.user, note=request.POST.get("note", ""))
                    messages.success(request, f"Order #{order.number} → {dict(OrderStatus.CHOICES)[to_status]}.")
                except OrderError as exc:
                    messages.error(request, str(exc))
            return _go(request, "order_detail", order.pk)
        if action == "payment" and request.can["payments"]:
            payment_form = PaymentForm(request.POST)
            if payment_form.is_valid():
                d = payment_form.cleaned_data
                try:
                    receive_payment(order, user=request.user, method=d["method"], amount=d["amount"],
                                    reference=d["reference"])
                    order.refresh_from_db()
                    messages.success(
                        request,
                        f"Payment recorded. Order #{order.number} is now {order.get_payment_status_display()}.",
                    )
                    if d["method"] == PaymentMethod.CASH:
                        _drawer_message(request, open_cash_drawer(r, user=request.user, order=order))
                    if order.payment_status == PaymentStatus.PAID:
                        _receipt_after_payment(request, [order])
                except OrderError as exc:
                    messages.error(request, str(exc))
                return _go(request, "order_detail", order.pk)

    action_labels = {
        OrderStatus.ACCEPTED: "Accept", OrderStatus.PREPARING: "Start preparing", OrderStatus.READY: "Mark ready",
        OrderStatus.COMPLETED: "Complete", OrderStatus.CANCELLED: "Cancel order",
    }
    transitions = [(code, action_labels[code]) for code, _ in OrderStatus.CHOICES if order.can_transition(code)]
    return render(request, "dashboard/order_detail.html", {
        "order": order,
        "transitions": transitions,
        "payment_form": payment_form,
        "is_paid": order.payment_status == PaymentStatus.PAID,
        "balance_due": due,
        "amount_paid": order.total - due,
    })


def _tendered(request):
    """Cash handed over by the customer (optional; blank = exact amount)."""
    raw = (request.POST.get("tendered") or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        value = Decimal(raw).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise OrderError("Enter the cash received as a number, e.g. 20 or 20.00.", code="invalid_amount")
    if value <= 0 or value >= Decimal("100000"):
        raise OrderError("Enter a valid cash amount.", code="invalid_amount")
    return value


def _change_message(request, result):
    sym = request.restaurant.currency_symbol
    if result.change > 0:
        messages.add_message(
            request, messages.SUCCESS,
            f"💵 GIVE CHANGE: {sym}{result.change:.2f}  (received {sym}{result.tendered:.2f}, "
            f"total {sym}{result.total_due:.2f})",
            extra_tags="change",
        )
    else:
        messages.success(request, f"Exact amount received: {sym}{result.total_due:.2f}. No change.")


def _receipt_after_payment(request, orders):
    """
    Depending on the restaurant's setting: print now (network printer, "always"),
    or leave a marker so the page asks "Print receipt?" (or prints via the browser).
    """
    r = request.restaurant
    if not orders or r.receipt_prompt == r.RECEIPT_NEVER:
        return
    if r.receipt_prompt == r.RECEIPT_ALWAYS and network_printing_available(r):
        result = print_receipt_network(r, receipt_orders(r, [o.pk for o in orders]))
        (messages.success if result.printed else messages.warning)(
            request, result.message if result.printed else f"Receipt not printed: {result.message}"
        )
        return
    messages.add_message(request, messages.INFO, ",".join(str(o.pk) for o in orders), extra_tags="receipt")


@staff_view("view_orders")
def receipt(request):
    """Printable receipt / bill for one or more orders (?orders=12,13)."""
    r = request.restaurant
    orders = receipt_orders(r, parse_order_ids(request.GET.get("orders", "")))
    if not orders:
        raise Http404("No orders")
    return render(request, "dashboard/receipt.html", {
        **receipt_data(r, orders), "autoprint": request.GET.get("autoprint") == "1",
    })


@require_POST
@staff_view("view_orders")
def receipt_print(request):
    """Print on the network receipt printer (non-JS fallback for the buttons)."""
    r = request.restaurant
    result = print_receipt_network(r, receipt_orders(r, parse_order_ids(request.POST.get("orders", ""))))
    (messages.success if result.printed else messages.error)(request, result.message)
    return _safe_next(request, reverse("dashboard:orders", args=[r.slug]))


def _drawer_message(request, result):
    if result.opened:
        messages.success(request, "🗄 Cash drawer opened.")
    elif result.attempted:
        messages.warning(request, f"Payment saved, but the cash drawer did not open: {result.message}")


@require_POST
@staff_view("payments")
def drawer_open(request):
    """'No sale' opening, e.g. to give change. Logged with the staff member's name."""
    result = open_cash_drawer(request.restaurant, user=request.user, reason=CashDrawerOpening.NO_SALE)
    (messages.success if result.opened else messages.error)(request, result.message)
    return _safe_next(request, reverse("dashboard:overview", args=[request.restaurant.slug]))


@require_POST
@staff_view("manage_restaurant")
def drawer_test(request):
    result = open_cash_drawer(request.restaurant, user=request.user, reason=CashDrawerOpening.TEST)
    (messages.success if result.opened else messages.error)(request, result.message)
    return _go(request, "settings")


def _safe_next(request, fallback):
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    return redirect(fallback)


@require_POST
@staff_view("payments")
def order_mark_paid(request, pk):
    """One tap: the waiter received cash for the full balance → order is Paid."""
    order = get_object_or_404(Order, restaurant=request.restaurant, pk=pk)
    try:
        tendered = _tendered(request)
        result = receive_cash([order], tendered=tendered, user=request.user)
        messages.success(request, f"Order #{order.number} marked as paid.")
        _change_message(request, result)
        _drawer_message(request, open_cash_drawer(request.restaurant, user=request.user, order=order))
        _receipt_after_payment(request, result.orders)
    except OrderError as exc:
        messages.error(request, str(exc))
    return _safe_next(request, reverse("dashboard:order_detail", args=[request.restaurant.slug, pk]))


# ---------------------------------------------------------------- tables & QR

@staff_view("view_orders")
def tables(request):
    r = request.restaurant
    can_manage = request.can["manage_tables"]
    form = TableForm(restaurant=r)
    bulk = BulkTableForm()
    if request.method == "POST":
        if not can_manage:
            messages.error(request, "Your role cannot manage tables.")
            return _go(request, "tables")
        if request.POST.get("action") == "bulk":
            bulk = BulkTableForm(request.POST)
            if bulk.is_valid():
                existing = set(Table.objects.filter(restaurant=r).values_list("number", flat=True))
                new = [
                    Table(restaurant=r, number=str(n))
                    for n in range(bulk.cleaned_data["start"], bulk.cleaned_data["end"] + 1)
                    if str(n) not in existing
                ]
                max_tables = plan_limit(r, "max_tables")
                room = max_tables - len(existing)
                if len(new) > room:
                    messages.error(request, f"Limit reached: a restaurant can have up to "
                                            f"{max_tables} tables on your plan.")
                    return _go(request, "tables")
                Table.objects.bulk_create(new)
                messages.success(request, f"Created {len(new)} tables.")
                return _go(request, "tables")
        else:
            form = TableForm(request.POST, restaurant=r)
            if Table.objects.filter(restaurant=r).count() >= plan_limit(r, "max_tables"):
                messages.error(request, f"Limit reached: your plan allows {plan_limit(r, 'max_tables')} tables.")
                return _go(request, "tables")
            if form.is_valid():
                table = form.save()
                messages.success(request, f"{table} created.")
                return _go(request, "tables")

    table_list = sorted(
        Table.objects.filter(restaurant=r).annotate(
            active_orders=Count("orders", filter=Q(orders__status__in=OrderStatus.ACTIVE))
        ),
        key=lambda t: t.sort_key,
    )
    open_sessions = set(
        TableSession.objects.filter(restaurant=r, status=TableSession.OPEN).values_list("table_id", flat=True)
    )
    for t in table_list:
        t.has_open_session = t.id in open_sessions
    return render(request, "dashboard/tables.html", {
        "tables": table_list, "form": form, "bulk": bulk, "can_manage": can_manage,
    })


@staff_view("view_orders")
def table_detail(request, pk):
    r = request.restaurant
    table = get_object_or_404(Table, restaurant=r, pk=pk)
    session = table.current_session()
    session_orders = (
        Order.objects.filter(table_session=session).prefetch_related("items__options").order_by("created_at")
        if session else Order.objects.none()
    )
    session_total = sum((o.total for o in session_orders if o.status != OrderStatus.CANCELLED), 0)
    session_unpaid = sum(
        (o.total for o in session_orders
         if o.status != OrderStatus.CANCELLED and o.payment_status == PaymentStatus.UNPAID), 0
    )
    qr = table.active_qr()
    return render(request, "dashboard/table_detail.html", {
        "table": table,
        "session": session,
        "session_orders": session_orders,
        "guests": _guest_bills(session_orders),
        "session_receipt_ids": ",".join(str(o.pk) for o in session_orders if o.status != OrderStatus.CANCELLED),
        "session_total": session_total,
        "session_unpaid": session_unpaid,
        "past_sessions": table.sessions.filter(status=TableSession.CLOSED).annotate(
            n=Count("orders"), total=Sum("orders__total")
        )[:10],
        "qr_url": qr.get_url(request),
        "qr_svg": mark_safe(qr_svg(qr.get_url(request))),  # generated locally from our own URL
    })


def _guest_bills(orders):
    """Split a table's orders into one bill per customer (phone)."""
    guests = {}
    for o in orders:
        key = o.customer_ref or f"order-{o.pk}"  # orders without a phone id stand alone
        g = guests.setdefault(key, {"ref": o.customer_ref, "orders": [], "unpaid": Decimal("0"), "name": ""})
        g["orders"].append(o)
        g["name"] = o.customer_name or g["name"]
        if o.status != OrderStatus.CANCELLED and o.payment_status == PaymentStatus.UNPAID:
            g["unpaid"] += o.total
    result = list(guests.values())
    for i, g in enumerate(result, start=1):
        g["label"] = f"Guest {i}" + (f" · {g['name']}" if g["name"] else "")
        g["total"] = sum((o.total for o in g["orders"] if o.status != OrderStatus.CANCELLED), Decimal("0"))
    return result


@require_POST
@staff_view("payments")
def guest_mark_paid(request, pk, ref):
    """One customer at a shared table pays for all of their own orders."""
    table = get_object_or_404(Table, restaurant=request.restaurant, pk=pk)
    session = table.current_session()
    orders = list(Order.objects.filter(table_session=session, customer_ref=ref)) if session and ref else []
    try:
        result = receive_cash(orders, tendered=_tendered(request), user=request.user, skip_unpayable=True)
        numbers = ", ".join(f"#{o.number}" for o in result.orders)
        messages.success(request, f"Marked as paid: {numbers}.")
        _change_message(request, result)
        _drawer_message(request, open_cash_drawer(request.restaurant, user=request.user))
        _receipt_after_payment(request, result.orders)
    except OrderError as exc:
        messages.error(request, str(exc) if exc.code != "nothing_due" else "This guest has nothing left to pay.")
    return _go(request, "table_detail", table.pk)


@require_POST
@staff_view("payments")
def table_mark_paid(request, pk):
    """The whole table paid at once: mark every unpaid order in the current session as paid."""
    table = get_object_or_404(Table, restaurant=request.restaurant, pk=pk)
    session = table.current_session()
    unpaid = (
        Order.objects.filter(table_session=session, payment_status=PaymentStatus.UNPAID)
        .exclude(status=OrderStatus.CANCELLED) if session else Order.objects.none()
    )
    try:
        result = receive_cash(list(unpaid), tendered=_tendered(request), user=request.user, skip_unpayable=True)
        numbers = ", ".join(f"#{o.number}" for o in result.orders)
        messages.success(request, f"Marked as paid: {numbers}.")
        _change_message(request, result)
        _drawer_message(request, open_cash_drawer(request.restaurant, user=request.user))
        _receipt_after_payment(request, result.orders)
    except OrderError as exc:
        if exc.code != "nothing_due":
            messages.error(request, str(exc))
            return _go(request, "table_detail", table.pk)  # e.g. not enough cash: keep the table open
        messages.info(request, "There were no unpaid orders at this table.")
    if request.POST.get("close_session") and request.can["table_sessions"]:
        table.free(user=request.user)
        messages.success(request, f"{table} is now free.")
    return _go(request, "table_detail", table.pk)


@require_POST
@staff_view("table_sessions")
def table_session_close(request, pk):
    table = get_object_or_404(Table, restaurant=request.restaurant, pk=pk)
    table.free(user=request.user)
    messages.success(request, f"{table} is now free. Customers must scan the QR code again to order.")
    return _go(request, "table_detail", table.pk)


@staff_view("manage_tables")
def table_edit(request, pk):
    table = get_object_or_404(Table, restaurant=request.restaurant, pk=pk)
    form = TableForm(request.POST or None, instance=table, restaurant=request.restaurant)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Table updated. Reprint its QR code if you changed the number.")
        return _go(request, "tables")
    return render(request, "dashboard/simple_form.html", {
        "form": form, "title": f"Edit {table}", "back": reverse("dashboard:tables", args=[request.restaurant.slug]),
    })


@require_POST
@staff_view("manage_tables")
def table_delete(request, pk):
    table = get_object_or_404(Table, restaurant=request.restaurant, pk=pk)
    table.delete()  # orders keep their table_number snapshot
    messages.success(request, f"{table} deleted.")
    return _go(request, "tables")


@require_POST
@staff_view("manage_tables")
def table_regenerate_qr(request, pk):
    table = get_object_or_404(Table, restaurant=request.restaurant, pk=pk)
    table.regenerate_qr()
    messages.success(request, f"New QR code generated for {table}. Old printed codes no longer work — print the new one.")
    return redirect(request.POST.get("next") or reverse("dashboard:table_detail", args=[request.restaurant.slug, pk]))


@staff_view("view_orders")
def table_qr_image(request, pk):
    table = get_object_or_404(Table, restaurant=request.restaurant, pk=pk)
    url = table.active_qr().get_url(request)
    filename = f"{request.restaurant.slug}-table-{table.number}"
    if request.GET.get("format") == "svg":
        resp = HttpResponse(qr_svg(url), content_type="image/svg+xml")
        resp["Content-Disposition"] = f'attachment; filename="{filename}.svg"'
    else:
        resp = HttpResponse(qr_png(url), content_type="image/png")
        if request.GET.get("download"):
            resp["Content-Disposition"] = f'attachment; filename="{filename}.png"'
    resp["Cache-Control"] = "private, no-store"
    return resp


@staff_view("view_orders")
def tables_print(request):
    r = request.restaurant
    qs = Table.objects.filter(restaurant=r, is_active=True)
    ids = request.GET.getlist("id")
    if ids:
        qs = qs.filter(pk__in=[i for i in ids if i.isdigit()])
    cards = []
    for table in sorted(qs, key=lambda t: t.sort_key):
        url = table.active_qr().get_url(request)
        cards.append({"table": table, "url": url, "svg": mark_safe(qr_svg(url))})
    return render(request, "dashboard/tables_print.html", {"cards": cards})


# ---------------------------------------------------------------- menu

@staff_view("toggle_availability")
def menu(request):
    r = request.restaurant
    categories = MenuCategory.objects.filter(restaurant=r).prefetch_related("items__options")
    return render(request, "dashboard/menu.html", {
        "categories": categories,
        "item_count": MenuItem.objects.filter(restaurant=r).count(),
        "availability_url": reverse("dashboard:api_item_availability", args=[r.slug, 0]),
    })


@require_POST
@staff_view("toggle_availability")
def item_toggle(request, pk):
    """Non-JS fallback for the Available / Sold Out switch."""
    item = get_object_or_404(MenuItem, restaurant=request.restaurant, pk=pk)
    item.is_available = not item.is_available
    item.save(update_fields=["is_available", "updated_at"])
    realtime.broadcast_availability(item)
    return _go(request, "menu")


@staff_view("manage_menu")
def category_form(request, pk=None):
    r = request.restaurant
    category = get_object_or_404(MenuCategory, restaurant=r, pk=pk) if pk else MenuCategory(restaurant=r)
    form = CategoryForm(request.POST or None, instance=category, restaurant=r)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Category saved.")
        return _go(request, "menu")
    return render(request, "dashboard/simple_form.html", {
        "form": form,
        "title": f"Edit category: {category.name}" if pk else "New category",
        "back": reverse("dashboard:menu", args=[r.slug]),
        "translation_fields": form.translation_field_names(),
    })


@require_POST
@staff_view("manage_menu")
def category_delete(request, pk):
    category = get_object_or_404(MenuCategory, restaurant=request.restaurant, pk=pk)
    try:
        category.delete()
        messages.success(request, "Category deleted.")
    except (ProtectedError, RestrictedError):
        messages.error(request, "Move or delete the dishes in this category first.")
    return _go(request, "menu")


@staff_view("manage_menu")
def item_form(request, pk=None):
    r = request.restaurant
    item = get_object_or_404(MenuItem, restaurant=r, pk=pk) if pk else MenuItem(restaurant=r)
    if not pk and request.GET.get("category", "").isdigit():
        item.category_id = MenuCategory.objects.filter(restaurant=r, pk=request.GET["category"]).values_list(
            "pk", flat=True).first()
    if not MenuCategory.objects.filter(restaurant=r).exists():
        messages.info(request, "Create a category first.")
        return _go(request, "category_create")

    if not pk and MenuItem.objects.filter(restaurant=r).count() >= plan_limit(r, "max_menu_items"):
        messages.error(request, f"Limit reached: your plan allows {plan_limit(r, 'max_menu_items')} dishes.")
        return _go(request, "menu")
    form = MenuItemForm(request.POST or None, request.FILES or None, instance=item, restaurant=r)
    formset = OptionFormSet(request.POST or None, instance=item, prefix="opt")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            item = form.save()
            formset.instance = item
            formset.save()
        realtime.broadcast_availability(item)
        messages.success(request, f"“{item.name}” saved.")
        return _go(request, "menu")
    return render(request, "dashboard/item_form.html", {
        "form": form, "formset": formset, "item": item,
        "translation_fields": form.translation_field_names(),
    })


@require_POST
@staff_view("manage_menu")
def item_delete(request, pk):
    item = get_object_or_404(MenuItem, restaurant=request.restaurant, pk=pk)
    item.delete()  # past orders keep name/price snapshots
    messages.success(request, f"“{item.name}” deleted.")
    return _go(request, "menu")


# ---------------------------------------------------------------- settings & staff

@staff_view("manage_restaurant")
def restaurant_settings(request):
    r = request.restaurant
    form = RestaurantForm(request.POST or None, request.FILES or None, instance=r)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Restaurant settings saved.")
        return _go(request, "settings")
    return render(request, "dashboard/settings.html", {
        "form": form, "translation_fields": form.translation_field_names(),
        "sections": _settings_sections(form),
        "drawer_log": CashDrawerOpening.objects.filter(restaurant=r).select_related("user", "order")[:15],
        "drawer_available": settings.CASH_DRAWER_NETWORK_ENABLED,
        "public_url": request.build_absolute_uri(r.get_absolute_url()),
    })


SETTINGS_SECTIONS = [
    ("Profile", "store", "How your restaurant appears to customers.",
     ["name", "description", "address", "phone", "email", "opening_hours"]),
    ("Branding", "image", "Shown at the top of your menu. JPEG, PNG or WebP up to 5 MB.",
     ["logo", "cover_image"]),
    ("Ordering & money", "receipt", "Pause ordering at any time; prices are shown in this currency.",
     ["is_accepting_orders", "currency", "currency_symbol", "service_charge_percent", "default_prep_minutes"]),
    ("Language", "globe", "Customers can switch language on the menu; untranslated text falls back to English.",
     ["default_language", "description_tet", "description_id"]),
    ("Receipts", "printer", "What happens after a payment is recorded.",
     ["receipt_prompt", "receipt_printer", "receipt_width", "receipt_footer"]),
    ("Receipt printer & cash drawer", "drawer", "Your network (ESC/POS) receipt printer. The cash drawer plugs into it.",
     ["printer_host", "printer_port", "cash_drawer_enabled", "drawer_pin"]),
]
WIDE_FIELDS = {"name", "description", "address", "opening_hours", "description_tet", "description_id",
               "is_accepting_orders", "cash_drawer_enabled", "receipt_prompt", "receipt_footer"}


def _settings_sections(form):
    sections = []
    for title, icon_name, desc, names in SETTINGS_SECTIONS:
        fields = [{"field": form[n], "wide": n in WIDE_FIELDS} for n in names if n in form.fields]
        if fields:
            sections.append({"title": title, "icon": icon_name, "desc": desc, "fields": fields})
    return sections


@staff_view("manage_staff")
def staff(request):
    r = request.restaurant
    form = StaffCreateForm(request.POST or None)
    if request.method == "POST" and r.staff.count() >= plan_limit(r, "max_staff"):
        messages.error(request, f"Limit reached: your plan allows {plan_limit(r, 'max_staff')} staff accounts.")
    elif request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            user = User.objects.create_user(
                username=d["username"], email=d["email"], password=d["password"], first_name=d["first_name"]
            )
            RestaurantStaff.objects.create(restaurant=r, user=user, role=d["role"])
        messages.success(request, f"Staff account “{user.username}” created.")
        return _go(request, "staff")
    return render(request, "dashboard/staff.html", {
        "members": r.staff.select_related("user").order_by("role", "user__username"),
        "form": form,
    })


@staff_view("manage_staff")
def staff_edit(request, pk):
    member = get_object_or_404(RestaurantStaff, restaurant=request.restaurant, pk=pk)
    form = StaffEditForm(request.POST or None, instance=member)
    if request.method == "POST" and form.is_valid():
        if member.user == request.user and (
            form.cleaned_data["role"] != Role.OWNER or not form.cleaned_data["is_active"]
        ):
            messages.error(request, "You cannot demote or deactivate yourself.")
        else:
            form.save()
            messages.success(request, "Staff member updated.")
            return _go(request, "staff")
    return render(request, "dashboard/simple_form.html", {
        "form": form, "title": f"Edit {member.user.username}",
        "back": reverse("dashboard:staff", args=[request.restaurant.slug]),
    })


# ---------------------------------------------------------------- reports & notifications

@staff_view("reports")
def reports(request):
    r = request.restaurant
    today = timezone.localdate()
    try:
        date_from = datetime.strptime(request.GET.get("from", ""), "%Y-%m-%d").date()
    except ValueError:
        date_from = today - timedelta(days=6)
    try:
        date_to = datetime.strptime(request.GET.get("to", ""), "%Y-%m-%d").date()
    except ValueError:
        date_to = today
    start, end = _local_day_bounds(date_from)[0], _local_day_bounds(date_to)[1]
    qs = Order.objects.filter(restaurant=r, created_at__gte=start, created_at__lt=end)

    if request.GET.get("format") == "csv":
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="orders-{r.slug}-{date_from}-{date_to}.csv"'
        writer = csv.writer(resp)
        writer.writerow(["Order", "Created", "Table", "Customer", "Status", "Payment", "Subtotal", "Service", "Total"])
        for o in qs.order_by("created_at"):
            writer.writerow([
                o.number, timezone.localtime(o.created_at).strftime("%Y-%m-%d %H:%M"), o.table_number,
                o.customer_name, o.status, o.payment_status, o.subtotal, o.service_charge, o.total,
            ])
        return resp

    completed = qs.filter(status=OrderStatus.COMPLETED)
    summary = qs.aggregate(
        orders=Count("id"),
        completed=Count("id", filter=Q(status=OrderStatus.COMPLETED)),
        cancelled=Count("id", filter=Q(status=OrderStatus.CANCELLED)),
        revenue=Sum("total", filter=Q(status=OrderStatus.COMPLETED)),
    )
    summary["avg"] = (summary["revenue"] / summary["completed"]) if summary["completed"] else None
    top_items = (
        OrderItem.objects.filter(order__in=completed)
        .values("name").annotate(qty=Sum("quantity"), revenue=Sum("line_total")).order_by("-qty")[:10]
    )
    daily = (
        completed.annotate(day=TruncDate("created_at", tzinfo=timezone.get_current_timezone()))
        .values("day").annotate(n=Count("id"), revenue=Sum("total")).order_by("day")
    )
    max_rev = max((d["revenue"] for d in daily), default=0) or 1
    return render(request, "dashboard/reports.html", {
        "date_from": date_from, "date_to": date_to, "summary": summary, "top_items": top_items,
        "daily": [{**d, "pct": int(d["revenue"] * 100 / max_rev)} for d in daily],
    })


@staff_view("view_orders")
def notifications(request):
    qs = Notification.objects.filter(restaurant=request.restaurant).select_related("order")
    if request.method == "POST":
        qs.filter(is_read=False).update(is_read=True)
        return _go(request, "notifications")
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "dashboard/notifications.html", {"page": page})


# ---------------------------------------------------------------- billing (restaurant owner)

@staff_view("manage_billing")
def billing_page(request):
    from apps.billing.models import BillingSettings, Plan
    from apps.billing.services import request_plan_change

    r = request.restaurant
    sub = request.billing
    if request.method == "POST" and sub is not None:
        plan = Plan.objects.filter(pk=request.POST.get("plan"), is_active=True, is_public=True).first()
        if plan is None or plan == sub.plan:
            messages.error(request, "Choose a different plan.")
        else:
            request_plan_change(sub, plan, request.user)
            messages.success(request, f"Request sent: change to {plan.name}. We'll confirm shortly.")
        return _go(request, "billing")
    return render(request, "dashboard/billing.html", {
        "sub": sub,
        "cfg": BillingSettings.load(),
        "plans": Plan.objects.filter(is_active=True, is_public=True),
        "invoices": r.invoices.exclude(status="void")[:24] if sub else [],
        "usage": [
            ("Tables", Table.objects.filter(restaurant=r).count(), sub.plan.max_tables if sub else None),
            ("Dishes", MenuItem.objects.filter(restaurant=r).count(), sub.plan.max_menu_items if sub else None),
            ("Staff accounts", r.staff.count(), sub.plan.max_staff if sub else None),
        ],
    })


@staff_view("manage_billing")
def billing_invoice(request, pk):
    from apps.billing.models import BillingSettings

    inv = get_object_or_404(request.restaurant.invoices.select_related("restaurant"), pk=pk)
    return render(request, "billing/invoice.html", {"inv": inv, "cfg": BillingSettings.load(), "console": False})
