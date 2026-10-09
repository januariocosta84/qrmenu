from django.conf import settings

from apps.billing.services import subscription_allows_orders
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from apps.core.i18n import ui_strings
from apps.menu.queries import public_menu
from apps.menu.serializers import PublicItemSerializer
from apps.orders.models import Order, OrderStatus
from apps.restaurants.models import QRCode, Restaurant, Table

from .access import grant_table_access, my_orders, table_access


def _apply_restaurant_language(request, restaurant):
    """Customers who haven't picked a language see the restaurant's default."""
    if not request.ui_lang_explicit:
        request.ui_lang = restaurant.default_language


def _visible_restaurant(request, slug):
    """Live restaurants for everyone; not-yet-live ones only for their own staff (preview)."""
    restaurant = get_object_or_404(Restaurant, slug=slug)
    if (not restaurant.is_active or restaurant.is_closed) and not restaurant.staff_role(request.user):
        raise Http404  # not live yet, or a sub-branch deactivated by its main branch
    return restaurant


@require_GET
def home(request):
    from apps.billing.models import BillingSettings, Plan

    billing = BillingSettings.load()
    return render(request, "storefront/home.html", {
        "signup_open": settings.SIGNUP_MODE != "closed",
        "platform_name": settings.PLATFORM_NAME,
        "plans": Plan.objects.filter(is_active=True, is_public=True),
        "default_plan_id": billing.default_plan_id,
        "trial_days": billing.trial_days,
    })


def terms(request):
    return render(request, "storefront/terms.html", {"platform_name": settings.PLATFORM_NAME})


@require_GET
@ensure_csrf_cookie
def restaurant_menu(request, slug):
    restaurant = _visible_restaurant(request, slug)
    table, reason = table_access(request, restaurant)
    if table is not None:
        return redirect("storefront:table", slug=slug, number=table.number)
    return _render_menu(request, restaurant, table=None, can_order=False, access_ended=reason)


@require_GET
@ensure_csrf_cookie
def table_menu(request, slug, number):
    """Entry point printed in table QR codes: /r/<slug>/t/<number>/?k=<token>."""
    restaurant = _visible_restaurant(request, slug)
    table = get_object_or_404(Table, restaurant=restaurant, number=number, is_active=True)

    token = request.GET.get("k")
    if token:
        if QRCode.objects.filter(table=table, token=token, is_active=True).exists():
            grant_table_access(request, table)  # also starts or continues the table's bill
            # Drop the secret from the address bar so it isn't shared by accident.
            return redirect("storefront:table", slug=slug, number=table.number)
        return _render_menu(request, restaurant, table=table, can_order=False, invalid_qr=True)

    access, reason = table_access(request, restaurant)
    can_order = access is not None and access.id == table.id
    return _render_menu(request, restaurant, table=table, can_order=can_order, access_ended=reason)


def legacy_table_menu(request, slug, number):
    """Long-form alias: /restaurant/<slug>/table/<number>/ (keeps the ?k= token)."""
    url = reverse("storefront:table", args=[slug, number])
    query = request.META.get("QUERY_STRING")
    return redirect(f"{url}?{query}" if query else url)


def _render_menu(request, restaurant, *, table, can_order, invalid_qr=False, access_ended=None):
    _apply_restaurant_language(request, restaurant)
    lang = request.ui_lang
    categories = list(public_menu(restaurant))
    items = {
        str(item.id): PublicItemSerializer(item, context={"lang": lang}).data
        for category in categories
        for item in category.items.all()
    }
    strings = ui_strings(lang)
    active_orders = list(my_orders(request, restaurant).filter(status__in=OrderStatus.ACTIVE)[:5])
    for o in active_orders:
        o.status_label = strings[f"status_{o.customer_status}"]
    config = {
        "slug": restaurant.slug,
        "table": table.number if table else None,
        "canOrder": can_order and restaurant.is_accepting_orders and restaurant.is_active
                    and subscription_allows_orders(restaurant),
        "currencySymbol": restaurant.currency_symbol,
        "serviceChargePercent": str(restaurant.service_charge_percent),
        "vatPercent": str(restaurant.vat_percent) if restaurant.vat_enabled else "0",
        "vatInclusive": restaurant.vat_inclusive,
        "orderUrl": reverse("storefront:api_place_order", args=[restaurant.slug]),
        "accessUrl": reverse("storefront:api_access", args=[restaurant.slug]),
        "menuUrl": reverse("storefront:api_menu", args=[restaurant.slug]),
        "wsPath": f"/ws/menu/{restaurant.slug}/",
        "cartKey": f"cart:{restaurant.slug}:{table.number if table else '-'}",
        "lang": lang,
    }
    return render(request, "storefront/menu.html", {
        "restaurant": restaurant,
        "description": restaurant.tr("description", lang),
        "categories": categories,
        "table": table,
        "can_order": config["canOrder"],
        "accepting": restaurant.is_accepting_orders and subscription_allows_orders(restaurant),
        "invalid_qr": invalid_qr,
        "access_ended": access_ended,
        "preview": not restaurant.is_active,
        "active_orders": active_orders,
        "items_json": items,
        "config": config,
    })


@require_GET
def order_status(request, token):
    order = get_object_or_404(
        Order.objects.select_related("restaurant").prefetch_related("items__options"), public_token=token
    )
    restaurant = order.restaurant
    _apply_restaurant_language(request, restaurant)
    steps = [OrderStatus.NEW, OrderStatus.PREPARING, OrderStatus.READY, OrderStatus.COMPLETED]
    current = order.customer_status
    current_index = steps.index(current) if current in steps else -1
    back_url = (
        reverse("storefront:table", args=[restaurant.slug, order.table_number])
        if order.table_number else reverse("storefront:menu", args=[restaurant.slug])
    )
    return render(request, "storefront/order_status.html", {
        "order": order,
        "restaurant": restaurant,
        "steps": [(s, ui_strings(request.ui_lang)[f"status_{s}"], i <= current_index) for i, s in enumerate(steps)],
        "back_url": back_url,
        "other_orders": my_orders(request, restaurant).exclude(pk=order.pk)[:10],
        "config": {
            "token": str(order.public_token),
            "status": current,
            "paymentStatus": order.payment_status,
            "apiUrl": reverse("storefront:api_order", args=[order.public_token]),
            "wsPath": f"/ws/orders/{order.public_token}/",
        },
    })
