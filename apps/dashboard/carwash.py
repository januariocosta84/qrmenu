"""
Counter side of multi-service ordering:

* Car wash screen: today's car wash orders (from tables and walk-ins), new orders by
  plate number, linking a walk-in car to a table's bill, tickets, the walk-in QR code.
  There is deliberately no "washing / ready" workflow: car wash staff don't use the app.
* Print bill dialog: choose services (café / car wash), one combined bill or one per
  service, paper size and copies; optionally take the cash payment at the same time.
  Only unpaid orders are billed; printed bills are kept and reprinted as COPY.
"""
import secrets
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import transaction
from django.db.models import F, Prefetch, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from apps.menu import service_types
from apps.menu.models import MenuItem
from apps.orders.models import Order, OrderItem, OrderStatus, PaymentStatus
from apps.orders.services import OrderError, change_status, normalize_plate, place_order
from apps.payments import bills
from apps.payments.escpos import escpos_from_lines
from apps.payments.models import Payment, Receipt
from apps.payments.receipts import network_printing_available, parse_order_ids, send_escpos
from apps.payments.services import balance_due, payable_group, receive_cash
from apps.payments.tickets import ticket_lines
from apps.restaurants.models import Table
from apps.restaurants.qr import qr_png, qr_svg, site_link

from .decorators import staff_view

CAR_WASH = service_types.CAR_WASH


def _require_car_wash(request):
    if not request.restaurant.car_wash_enabled:
        raise Http404("Car wash is not enabled")


def _orders_qs(restaurant):
    return Order.objects.filter(restaurant=restaurant).prefetch_related(
        Prefetch("items", queryset=OrderItem.objects.prefetch_related("options")),
        Prefetch("payments", queryset=Payment.objects.filter(status=Payment.SUCCEEDED).select_related("received_by")),
    )


def walk_in_url(request, restaurant) -> str:
    if not restaurant.car_wash_qr_token:
        restaurant.car_wash_qr_token = secrets.token_urlsafe(12)
        restaurant.save(update_fields=["car_wash_qr_token"])
    base = site_link(request) or request.build_absolute_uri("/").rstrip("/")
    return f"{base}{reverse('storefront:car_wash', args=[restaurant.slug])}?k={restaurant.car_wash_qr_token}"


# ---------------------------------------------------------------- car wash screen

@staff_view("view_orders")
def car_wash(request):
    _require_car_wash(request)
    r = request.restaurant
    packages = MenuItem.objects.filter(restaurant=r, category__service_type=CAR_WASH, category__is_active=True,
                                       is_available=True).order_by("category__position", "position", "name")
    if request.method == "POST":
        if not request.can["take_orders"]:
            raise Http404
        plate = normalize_plate(request.POST.get("plate"))
        table = Table.objects.filter(restaurant=r, pk=request.POST.get("table") or 0, is_active=True).first()
        try:
            order = place_order(
                restaurant=r, table=table, placed_by=request.user, customer_name=request.POST.get("customer_name", ""),
                note=request.POST.get("note", ""),
                lines=[{"menu_item": int(request.POST.get("package") or 0), "quantity": 1, "plate_number": plate,
                        "vehicle_type": request.POST.get("vehicle_type") or service_types.CAR}],
            )
            where = _("Table %(n)s") % {"n": table.number} if table else _("walk-in")
            messages.success(request, _("Car wash #%(number)s for %(plate)s (%(where)s) added.") % {
                "number": order.number, "plate": plate, "where": where})
        except (OrderError, ValueError) as exc:
            messages.error(request, str(exc) if isinstance(exc, OrderError) else _("Choose a package."))
        return redirect("dashboard:car_wash", r.slug)

    today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    qs = _orders_qs(r).filter(service_type=CAR_WASH)
    show_all = request.GET.get("show") == "all"
    orders = list((qs if show_all else qs.filter(Q(created_at__gte=today) | Q(payment_status=PaymentStatus.UNPAID)))
                  .exclude(status=OrderStatus.CANCELLED if not show_all else "__none__")
                  .order_by("-created_at")[:200])
    # Walk-in cars waiting to pay, grouped by plate: one bill per plate.
    walk_ins = {}
    for o in orders:
        if o.table_id is None and o.payment_status == PaymentStatus.UNPAID and o.status != OrderStatus.CANCELLED:
            g = walk_ins.setdefault(o.plate_number, {"plate": o.plate_number, "orders": [], "total": Decimal("0")})
            for part in payable_group(o):  # + the café ordered together with the car: one bill, one payment
                if part not in g["orders"]:
                    g["orders"].append(part)
                    g["total"] += balance_due(part)
    url = walk_in_url(request, r)
    return render(request, "dashboard/car_wash.html", {
        "orders": orders, "walk_ins": list(walk_ins.values()), "packages": packages, "show_all": show_all,
        "tables": Table.objects.filter(restaurant=r, is_active=True).order_by("number"),
        "vehicle_types": service_types.VEHICLE_TYPES,
        "receipts": Receipt.objects.filter(restaurant=r, orders__service_type=CAR_WASH).distinct()[:15],
        "walk_in_url": url, "walk_in_svg": qr_svg(url),
        "network_tickets": network_printing_available(r),
        "car_wash_config": {"autoPrint": r.car_wash_auto_print and not network_printing_available(r),
                            "ticketUrl": reverse("dashboard:car_wash_ticket", args=[r.slug, 0])},
    })


@require_POST
@staff_view("take_orders")
def car_wash_link(request, pk):
    """A car dropped off first (walk-in), the customer sits down later: move its bill to the table."""
    _require_car_wash(request)
    r = request.restaurant
    order = get_object_or_404(Order, restaurant=r, pk=pk, service_type=CAR_WASH)
    table = get_object_or_404(Table, restaurant=r, pk=request.POST.get("table"), is_active=True)
    if order.payment_status == PaymentStatus.PAID or order.status == OrderStatus.CANCELLED:
        messages.error(request, _("Only unpaid car wash orders can be moved to a table."))
        return redirect("dashboard:car_wash", r.slug)
    with transaction.atomic():
        session = table.current_session(create=True)
        # The same car's other unpaid walk-in orders move along (one bill per plate).
        moved = Order.objects.filter(restaurant=r, service_type=CAR_WASH, table__isnull=True,
                                     payment_status=PaymentStatus.UNPAID, plate_number=order.plate_number
                                     ).exclude(status=OrderStatus.CANCELLED) if order.table_id is None else \
            Order.objects.filter(pk=order.pk)
        n = moved.update(table=table, table_number=table.number, table_session=session, updated_at=timezone.now())
    messages.success(request, _("Car wash for %(plate)s added to the bill of table %(n)s (%(count)s order(s)).") % {
        "plate": order.plate_number, "n": table.number, "count": n})
    return redirect(request.POST.get("next") or reverse("dashboard:car_wash", args=[r.slug]))


@require_POST
@staff_view("cancel_orders")
def car_wash_cancel(request, pk):
    _require_car_wash(request)
    order = get_object_or_404(Order, restaurant=request.restaurant, pk=pk, service_type=CAR_WASH)
    try:
        change_status(order, OrderStatus.CANCELLED, user=request.user, note=request.POST.get("reason", "")[:200])
        messages.success(request, _("Car wash #%(number)s cancelled.") % {"number": order.number})
    except OrderError as exc:
        messages.error(request, str(exc))
    return redirect("dashboard:car_wash", request.restaurant.slug)


@xframe_options_sameorigin
@staff_view("view_orders")
def car_wash_ticket(request, pk):
    """Ticket for the car wash team (printed from this device); ?autoprint=1 prints it right away."""
    r = request.restaurant
    order = get_object_or_404(_orders_qs(r), pk=pk)
    if request.method == "POST":  # send it to the network receipt printer
        result = send_escpos(r, escpos_from_lines(ticket_lines(order, r.receipt_width)))
        return JsonResponse(result.as_dict())
    width = int(request.GET.get("width") or r.receipt_width)
    return render(request, "dashboard/bill_print.html", {
        "pages": [ticket_lines(order, width if width in bills.WIDTHS else 48)], "width": width,
        "title": f"Ticket #{order.number}", "autoprint": request.GET.get("autoprint") == "1",
    })


@staff_view("view_orders")
def car_wash_qr(request):
    _require_car_wash(request)
    r = request.restaurant
    if request.method == "POST" and request.can["manage_tables"]:
        r.car_wash_qr_token = secrets.token_urlsafe(12)
        r.save(update_fields=["car_wash_qr_token"])
        messages.success(request, _("New car wash QR code made. Old printouts no longer work — print the new one."))
        return redirect("dashboard:car_wash", r.slug)
    url = walk_in_url(request, r)
    resp = HttpResponse(qr_png(url, caption=site_link(request)), content_type="image/png")
    resp["Content-Disposition"] = f'attachment; filename="{r.slug}-car-wash-qr.png"'
    return resp


# ---------------------------------------------------------------- Print bill dialog

def _scope_orders(request, params):
    """Orders the dialog is about: a table's open bill (?session=), a walk-in plate (?plate=) or ids (?orders=)."""
    r = request.restaurant
    qs = _orders_qs(r).exclude(status=OrderStatus.CANCELLED)
    if params.get("session"):
        return list(qs.filter(table_session_id=params.get("session"), table_session__status="open"))
    if params.get("plate") is not None and params.get("plate") != "":
        cars = qs.filter(service_type=CAR_WASH, table__isnull=True, plate_number=params.get("plate"))
        groups = [g for g in cars.values_list("order_group", flat=True) if g]
        # The car's orders, plus anything ordered together with them (e.g. coffee at the counter).
        return list(qs.filter(Q(pk__in=cars.values("pk")) | Q(order_group__in=groups)).distinct())
    ids = parse_order_ids(params.get("orders", ""))
    return list(qs.filter(pk__in=ids)) if ids else []


@staff_view("view_orders")
def bill_options(request):
    """JSON for the dialog: unpaid total per service, default print mode and paper."""
    r = request.restaurant
    orders = bills.billable(_scope_orders(request, request.GET))
    services = []
    for code, part in bills.by_service(orders).items():
        services.append({
            "code": code, "name": service_types.display_name(r, code) if code != service_types.CAFE else str(_("Café")),
            "total": f"{sum((o.total for o in part), Decimal('0')):.2f}", "orders": [o.pk for o in part],
            "plates": sorted({o.plate_number for o in part if o.plate_number}),
        })
    return JsonResponse({
        "services": services, "mode": r.bill_print_mode, "width": r.receipt_width, "currency": r.currency_symbol,
        "canPay": request.can["payments"],
        "previewUrl": reverse("dashboard:bill_preview", args=[r.slug]),
        "printUrl": reverse("dashboard:bill_print", args=[r.slug]),
    })


def _width(raw, default=48):
    try:
        w = int(raw)
    except (TypeError, ValueError):
        return default
    return w if w in bills.WIDTHS else default


@xframe_options_sameorigin
@staff_view("view_orders")
def bill_preview(request):
    r = request.restaurant
    orders = bills.billable(_scope_orders(request, request.GET))
    if not orders:
        raise Http404("Nothing to bill")
    width = _width(request.GET.get("width"), r.receipt_width)
    snapshots = bills.build_bills(r, orders, request.GET.get("mode") or r.bill_print_mode)
    for snap in snapshots:
        snap["number"] = "PREVIEW"
    return render(request, "dashboard/bill_print.html", {
        "pages": [bills.receipt_lines(s, width) for s in snapshots], "width": width, "title": _("Bill preview"),
        "autoprint": False,
    })


@require_POST
@staff_view("view_orders")
def bill_print(request):
    """Number and store the bills for the chosen unpaid orders; optionally take cash first (pay=1)."""
    import json

    r = request.restaurant
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"message": "Invalid request."}, status=400)
    orders = bills.billable(_scope_orders(request, {"orders": body.get("orders") or []}))
    if not orders:
        return JsonResponse({"message": _("Nothing left to bill: these orders are paid. Reprint from the history.")},
                            status=409)
    mode = body.get("mode") if body.get("mode") in (bills.COMBINED, bills.SEPARATE) else r.bill_print_mode
    width = _width(body.get("width"), r.receipt_width)
    copies = max(1, min(int(body.get("copies") or 1), 5))
    change = None
    if body.get("pay"):
        if not request.can["payments"]:
            return JsonResponse({"message": _("Your role cannot take payments.")}, status=403)
        try:
            tendered = Decimal(str(body["tendered"])) if body.get("tendered") not in (None, "") else None
            result = receive_cash(orders, tendered=tendered, user=request.user, skip_unpayable=True)
        except (OrderError, InvalidOperation) as exc:
            return JsonResponse({"message": str(exc) if isinstance(exc, OrderError) else "Invalid amount."}, status=409)
        change = {"change": f"{result.change:.2f}", "tendered": f"{result.tendered:.2f}", "due": f"{result.total_due:.2f}"}
        orders = list(_orders_qs(r).filter(pk__in=[o.pk for o in orders]))  # fresh: paid, with payments
    receipts = bills.create_receipts(r, orders, mode=mode, width=width, user=request.user)
    ids = ",".join(str(x.pk) for x in receipts)
    printed = None
    if network_printing_available(r):
        payload = b"".join(escpos_from_lines(bills.receipt_lines(x.data, width), copies=copies) for x in receipts)
        printed = send_escpos(r, payload).as_dict()
    return JsonResponse({
        "receipts": [{"id": x.pk, "number": x.number} for x in receipts],
        "printUrl": f"{reverse('dashboard:bill_receipts', args=[r.slug])}?ids={ids}&copies={copies}&autoprint=1",
        "printed": printed, "change": change,
    })


@xframe_options_sameorigin
@staff_view("view_orders")
def bill_receipts(request):
    """Printable page for stored bills (?ids=1,2). ?reprint=1 (reprint from history) marks them COPY."""
    r = request.restaurant
    receipts = list(Receipt.objects.filter(restaurant=r, pk__in=parse_order_ids(request.GET.get("ids", ""))).order_by("pk"))
    if not receipts:
        raise Http404("No receipts")
    reprint = request.GET.get("reprint") == "1"
    if reprint:
        Receipt.objects.filter(pk__in=[x.pk for x in receipts]).update(reprints=F("reprints") + 1)
    copies = max(1, min(int(request.GET.get("copies") or 1), 5))
    pages = []
    for x in receipts:
        width = _width(request.GET.get("width"), x.width)
        pages += [bills.receipt_lines(x.data, width, copy=reprint)] * copies
    width = _width(request.GET.get("width"), receipts[0].width)
    return render(request, "dashboard/bill_print.html", {
        "pages": pages, "width": width, "title": ", ".join(x.number for x in receipts),
        "autoprint": request.GET.get("autoprint") == "1",
    })
