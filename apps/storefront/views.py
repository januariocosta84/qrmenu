import secrets

from django.conf import settings

from apps.billing.services import subscription_allows_orders
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from apps.core.i18n import LANGUAGES, ui_strings
from apps.menu import service_types
from apps.menu.queries import public_menu
from apps.menu.serializers import PublicItemSerializer
from apps.orders.models import Order, OrderStatus
from apps.restaurants.models import QRCode, Restaurant, Table

from .access import grant_table_access, grant_walk_in, my_orders, table_access, walk_in_access


def _apply_restaurant_language(request, restaurant):
    """Customers who haven't picked a language see the restaurant's default."""
    if not request.ui_lang_explicit:
        request.ui_lang = restaurant.default_language


class RestaurantUnavailable(Http404):
    """The restaurant exists but isn't public (not approved yet, suspended, or a deactivated branch)."""

    def __init__(self, restaurant):
        super().__init__("Restaurant not available")
        self.restaurant = restaurant


def _visible_restaurant(request, slug):
    """Live restaurants for everyone; not-yet-live ones only for their own staff (preview)."""
    restaurant = get_object_or_404(Restaurant, slug=slug)
    if (not restaurant.is_active or restaurant.is_closed) and not restaurant.staff_role(request.user):
        raise RestaurantUnavailable(restaurant)  # not live yet, or a sub-branch deactivated by its main branch
    return restaurant


def page_not_found(request, exception=None):
    """Friendly 404 (in the visitor's language) instead of Django's plain "Not Found"."""
    if request.path.startswith("/api/"):
        return JsonResponse({"error": {"code": "not_found", "message": "Not found."}}, status=404)
    lang = getattr(request, "ui_lang", "en")
    strings = ui_strings(lang)
    restaurant = getattr(exception, "restaurant", None)
    return render(request, "storefront/not_found.html", {
        "t": strings, "ui_lang": lang, "restaurant": restaurant,
        "body": strings["unavailable_body"] % {"name": restaurant.name} if restaurant else strings["not_found_body"],
        "languages": LANGUAGES,
    }, status=404)


@require_GET
def home(request):
    from .forms import CallbackForm

    return render(request, "storefront/home.html", {
        **_home_context(), "callback_form": CallbackForm(), "callback_status": request.GET.get("callback", ""),
    })


def _home_context():
    from apps.billing.models import BillingSettings, Plan

    billing = BillingSettings.load()
    return {
        "signup_open": settings.SIGNUP_MODE != "closed",
        "platform_name": settings.PLATFORM_NAME,
        "plans": Plan.objects.filter(is_active=True, is_public=True),
        "default_plan_id": billing.default_plan_id,
        "trial_days": billing.trial_days,
    }


def call_me_back(request):
    """Home page form: a restaurant owner asks to be called back (we can set up the menu for them)."""
    import logging

    from django.core.cache import cache
    from django.core.mail import mail_admins

    from apps.core.utils import client_ip

    from .forms import CallbackForm

    if request.method != "POST":
        return redirect("/#call-me")
    key = f"callback:{client_ip(request)}"
    if cache.get(key, 0) >= 5:  # a few per hour per network is plenty
        return redirect("/?callback=limit#call-me")
    form = CallbackForm(request.POST)
    if not form.is_valid():
        return render(request, "storefront/home.html", {**_home_context(), "callback_form": form}, status=400)
    cb = form.save(commit=False)
    cb.language = (getattr(request, "LANGUAGE_CODE", "") or "")[:5]
    cb.save()
    cache.set(key, cache.get(key, 0) + 1, 3600)
    try:
        mail_admins(
            f"Call back: {cb.restaurant_name} ({cb.name})",
            f"{cb.name} from {cb.restaurant_name} asked to be called back.\n\n"
            f"Phone / WhatsApp: {cb.phone}\nWhatsApp: https://wa.me/{cb.whatsapp_number}\n"
            f"Best time: {cb.get_best_time_display()}\n"
            f"Set up the menu for them: {'yes' if cb.setup_help else 'no'}\n\n"
            "See all requests in the platform console → Call requests.",
        )
    except Exception:
        logging.getLogger(__name__).exception("Could not email platform admins about a call-back request")
    return redirect("/?callback=sent#call-me")


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


@require_GET
@ensure_csrf_cookie
def car_wash_menu(request, slug):
    """Walk-in car wash QR (at the counter / wash bay): /r/<slug>/carwash/?k=<token>. Only the car wash tab; no table."""
    restaurant = _visible_restaurant(request, slug)
    if not restaurant.car_wash_enabled:
        raise Http404
    token = request.GET.get("k")
    if token:
        if restaurant.car_wash_qr_token and secrets.compare_digest(token, restaurant.car_wash_qr_token):
            grant_walk_in(request, restaurant)
            return redirect("storefront:car_wash", slug=slug)
        return _render_menu(request, restaurant, table=None, can_order=False, invalid_qr=True, walk_in=True)
    return _render_menu(request, restaurant, table=None, can_order=walk_in_access(request, restaurant), walk_in=True)


def legacy_table_menu(request, slug, number):
    """Long-form alias: /restaurant/<slug>/table/<number>/ (keeps the ?k= token)."""
    url = reverse("storefront:table", args=[slug, number])
    query = request.META.get("QUERY_STRING")
    return redirect(f"{url}?{query}" if query else url)


def _render_menu(request, restaurant, *, table, can_order, invalid_qr=False, access_ended=None, walk_in=False):
    _apply_restaurant_language(request, restaurant)
    lang = request.ui_lang
    categories = list(public_menu(restaurant, services=[service_types.CAR_WASH] if walk_in else None))
    present = [code for code in service_types.SERVICE_TYPES
               if any(c.service_type == code and c.items.all() for c in categories)]
    strings_ = ui_strings(lang)
    tabs = [(code, strings_["cafe_tab"] if code == service_types.CAFE else service_types.display_name(restaurant, code))
            for code in present] if len(present) > 1 else []
    items = {
        str(item.id): PublicItemSerializer(item, context={"lang": lang}).data
        for category in categories
        for item in category.items.all()
    }
    strings = ui_strings(lang)
    active_orders = list(my_orders(request, restaurant).filter(status__in=OrderStatus.ACTIVE)[:5])
    for o in active_orders:
        o.status_label = (strings["car_wash_ordered"] if not service_types.get(o.service_type).kitchen
                          else strings[f"status_{o.customer_status}"])
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
        "cartKey": f"cart:{restaurant.slug}:{table.number if table else ('carwash' if walk_in else '-')}",
        "lang": lang,
        "walkIn": walk_in,
        "vehicleTypes": [[code, strings[f"vehicle_{code}"]] for code in service_types.VEHICLE_CODES],
        "vehicleServices": [c for c, s in service_types.SERVICE_TYPES.items() if s.needs_vehicle],
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
        "tabs": tabs,
        "walk_in": walk_in,
        "walk_in_name": service_types.display_name(restaurant, service_types.CAR_WASH) if walk_in else "",
    })


@require_GET
def order_status(request, token):
    order = get_object_or_404(
        Order.objects.select_related("restaurant").prefetch_related("items__options"), public_token=token
    )
    restaurant = order.restaurant
    _apply_restaurant_language(request, restaurant)
    tracked = service_types.get(order.service_type).kitchen  # car wash: "ordered ✅", no live tracking
    siblings = (Order.objects.filter(order_group=order.order_group).exclude(pk=order.pk).prefetch_related("items")
                if order.order_group else Order.objects.none())
    steps = [OrderStatus.NEW, OrderStatus.PREPARING, OrderStatus.READY, OrderStatus.COMPLETED]
    current = order.customer_status
    current_index = steps.index(current) if current in steps else -1
    back_url = (
        reverse("storefront:table", args=[restaurant.slug, order.table_number]) if order.table_number
        else reverse("storefront:car_wash", args=[restaurant.slug]) if not tracked
        else reverse("storefront:menu", args=[restaurant.slug])
    )
    return render(request, "storefront/order_status.html", {
        "order": order,
        "restaurant": restaurant,
        "steps": [(s, ui_strings(request.ui_lang)[f"status_{s}"], i <= current_index) for i, s in enumerate(steps)],
        "back_url": back_url,
        "other_orders": my_orders(request, restaurant).exclude(pk=order.pk).exclude(
            pk__in=[o.pk for o in siblings])[:10],
        "tracked": tracked,
        "service_siblings": [o for o in siblings if not service_types.get(o.service_type).kitchen],
        "service_name": service_types.display_name(restaurant, order.service_type),
        "config": {
            "token": str(order.public_token),
            "status": current,
            "paymentStatus": order.payment_status,
            "apiUrl": reverse("storefront:api_order", args=[order.public_token]),
            "wsPath": f"/ws/orders/{order.public_token}/",
        },
    })
