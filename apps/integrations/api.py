"""
Ordering API v1 (Pro) for external ordering apps: websites, mobile apps, delivery partners.

    Authorization: Bearer <api key>        (or  X-API-Key: <api key>)

    GET   /api/ordering/v1/branches
    GET   /api/ordering/v1/menu?branch=<id>
    POST  /api/ordering/v1/orders
    GET   /api/ordering/v1/orders/<id>
    PATCH /api/ordering/v1/orders/<id>

Every request is HTTPS-only, rate limited per key and logged. A key reaches only
its own main branch and the branches it is limited to. Test (sandbox) keys get
the same answers, but their orders are simulated: no kitchen, no revenue.
"""
import json
import random
import time
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.core.utils import client_ip
from apps.menu.queries import public_menu
from apps.orders.models import MAX_QUANTITY, Order, OrderStatus
from apps.orders.services import OrderError, change_status, place_order, price_cart

from . import webhooks
from .models import ApiKey, ApiRequestLog, SandboxOrder, hash_key
from .services import (
    API_PAYMENT_METHODS, API_STATUSES, FROM_API_STATUS, ApiError, access_problem, allowed_branches, branch_json,
    order_json, sandbox_json,
)


def _error(exc: ApiError) -> JsonResponse:
    resp = JsonResponse({"error": {"code": exc.code, "message": exc.message, **exc.extra}}, status=exc.status)
    if exc.status == 429:
        resp["Retry-After"] = "60"
    return resp


def _authenticate(request) -> ApiKey:
    auth = request.headers.get("Authorization", "")
    raw = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("X-API-Key", "").strip()
    if not raw:
        raise ApiError(401, "missing_api_key", "Send your API key in the Authorization header: 'Bearer <key>'.")
    key = ApiKey.objects.select_related("restaurant").filter(key_hash=hash_key(raw)).first()
    if key is None:
        raise ApiError(401, "invalid_api_key", "This API key is not valid.")
    if not key.is_active:
        raise ApiError(401, "revoked_api_key", "This API key has been revoked.")
    return key


def _rate_limit(key: ApiKey) -> None:
    limit = settings.ORDERING_API_RATE_PER_MINUTE
    bucket = f"ordering-api:{key.pk}:{int(time.time() // 60)}"
    if cache.add(bucket, 1, 70):
        return
    try:
        n = cache.incr(bucket)
    except ValueError:
        cache.set(bucket, 1, 70)
        return
    if n > limit:
        raise ApiError(429, "rate_limited", f"Too many requests: the limit is {limit} per minute for each API key.")


def _log(request, key, resp, started):
    error = ""
    if resp.status_code >= 400:
        try:
            error = json.loads(resp.content)["error"]["code"][:40]
        except Exception:
            error = "error"
    ApiRequestLog.objects.create(
        restaurant=key.restaurant if key else None, api_key=key, key_name=key.name if key else "",
        method=request.method, path=request.get_full_path()[:200], status_code=resp.status_code, error=error,
        ip=client_ip(request), duration_ms=int((time.monotonic() - started) * 1000),
    )
    if key is not None:
        ApiKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
    if random.random() < 0.01:  # keep the log to ORDERING_API_LOG_DAYS
        ApiRequestLog.objects.filter(created_at__lt=timezone.now() - timedelta(days=settings.ORDERING_API_LOG_DAYS)).delete()


def _cors(resp):
    """Any website may call the API from the browser: access needs the API key, never cookies."""
    resp["Access-Control-Allow-Origin"] = "*"
    resp["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-API-Key"
    resp["Access-Control-Allow-Methods"] = "GET, POST, PATCH, OPTIONS"
    resp["Access-Control-Max-Age"] = "86400"
    return resp


def endpoint(*methods):
    def decorator(view):
        @csrf_exempt
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method == "OPTIONS":  # browser CORS preflight: no key is sent, nothing to log
                return _cors(HttpResponse(status=204))
            started, key = time.monotonic(), None
            try:
                if settings.ORDERING_API_REQUIRE_HTTPS and not request.is_secure():
                    raise ApiError(403, "https_required", "The API only accepts HTTPS requests.")
                key = _authenticate(request)
                _rate_limit(key)
                if problem := access_problem(key.restaurant):
                    raise problem
                if request.method not in methods:
                    raise ApiError(405, "method_not_allowed", f"Use {' or '.join(methods)} on this endpoint.")
                resp = view(request, key, *args, **kwargs)
            except ApiError as exc:
                resp = _error(exc)
            _log(request, key, resp, started)
            return _cors(resp)

        return wrapper

    return decorator


def _json_body(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        raise ApiError(400, "invalid_json", "The request body must be valid JSON.")
    if not isinstance(data, dict):
        raise ApiError(400, "invalid_json", "The request body must be a JSON object.")
    return data


def _branch(key, branch_id):
    """A branch this key may use; anything else (other restaurants included) is rejected."""
    branches = allowed_branches(key)
    if branch_id in (None, ""):
        if branches.count() == 1:
            return branches.first()
        raise ApiError(400, "branch_required", "Give the branch id (see GET /branches).")
    try:
        branch = branches.filter(pk=int(branch_id)).first()
    except (TypeError, ValueError):
        branch = None
    if branch is None:
        raise ApiError(403, "branch_not_allowed", "This API key can't access that branch.")
    return branch


# ------------------------------------------------------------------ endpoints

@endpoint("GET")
def branches(request, key):
    return JsonResponse({"data": [branch_json(b) for b in allowed_branches(key)]})


@endpoint("GET")
def menu(request, key):
    branch = _branch(key, request.GET.get("branch"))
    categories = []
    for c in public_menu(branch):
        categories.append({"id": c.pk, "name": c.name, "items": [
            {"id": i.pk, "name": i.name, "description": i.description, "price": str(i.price),
             "available": i.is_available, "options": [
                 {"id": o.pk, "name": o.name, "price": str(o.price), "available": o.is_available} for o in i.options.all()]}
            for i in c.items.all()]})
    return JsonResponse({"branch": branch_json(branch), "currency": branch.currency, "categories": categories})


def _lines(data) -> list:
    items = data.get("items")
    if not isinstance(items, list) or not items or len(items) > 50:
        raise ApiError(400, "invalid_items", "'items' must be a list of 1 to 50 lines: {menu_item, quantity, options?, note?}.")
    lines = []
    for n, line in enumerate(items):
        try:
            qty = int(line["quantity"])
            options = [int(o) for o in line.get("options") or []][:20]
            lines.append({"menu_item": int(line["menu_item"]), "quantity": qty, "options": options,
                          "note": str(line.get("note") or "")[:200]})
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ApiError(400, "invalid_items", f"Line {n + 1}: give menu_item (id) and quantity (number).")
        if not 1 <= qty <= MAX_QUANTITY:
            raise ApiError(400, "invalid_quantity", f"Line {n + 1}: quantity must be 1 to {MAX_QUANTITY}.")
    return lines


def _check_total(data, total: Decimal):
    if data.get("total") in (None, ""):
        return
    try:
        sent = Decimal(str(data["total"]))
    except (InvalidOperation, ValueError):
        raise ApiError(400, "invalid_total", "'total' must be a number, e.g. \"12.50\".")
    if sent != total:
        raise ApiError(409, "total_mismatch", f"The total doesn't match the menu prices: it is {total}.",
                       expected_total=str(total))


@endpoint("POST")
def orders(request, key):
    data = _json_body(request)
    branch = _branch(key, data.get("branch"))
    lines = _lines(data)
    method = str(data.get("payment_method") or "pay_at_counter")
    if method not in API_PAYMENT_METHODS:
        raise ApiError(400, "invalid_payment_method", f"payment_method must be one of: {', '.join(API_PAYMENT_METHODS)}.")
    customer = {"customer_name": str(data.get("customer_name") or "")[:80],
                "customer_phone": str(data.get("customer_phone") or "")[:30], "note": str(data.get("note") or "")[:500]}
    external_ref = str(data.get("external_ref") or "")[:64]
    paid = bool(data.get("paid"))
    if key.is_sandbox:
        return _sandbox_order(key, branch, lines, data, method, paid, customer, external_ref)
    if external_ref:  # retries with the same reference return the order already made
        existing = Order.objects.filter(api_key__restaurant=key.restaurant, external_ref=external_ref).first()
        if existing:
            return JsonResponse(order_json(existing), status=200)
    try:
        _check_total(data, price_cart(branch, lines)["total"])
        with transaction.atomic():
            order = place_order(restaurant=branch, table=None, lines=lines, payment_method=API_PAYMENT_METHODS[method],
                                language=str(data.get("language") or "en")[:5], ip=client_ip(request),
                                api_key=key, external_ref=external_ref, **customer)
            if paid:
                from apps.payments.services import receive_payment

                receive_payment(order, method=API_PAYMENT_METHODS[method], reference=f"API {key.name}"[:120])
    except OrderError as exc:
        raise ApiError(409, exc.code, str(exc), **exc.details)
    order = Order.objects.select_related("restaurant").prefetch_related("items__options").get(pk=order.pk)
    return JsonResponse(order_json(order), status=201)


def _sandbox_order(key, branch, lines, data, method, paid, customer, external_ref):
    try:
        priced = price_cart(branch, lines)
    except OrderError as exc:
        raise ApiError(409, exc.code, str(exc), **exc.details)
    _check_total(data, priced["total"])
    items = [{"name": i.name, "quantity": q, "unit_price": str(i.price), "line_total": str(t), "note": note,
              "options": [{"name": o.name, "price": str(o.price)} for o in opts]}
             for i, q, opts, note, t in priced["prepared"]]
    number = SandboxOrder.objects.filter(api_key__restaurant=key.restaurant).count() + 1
    s = SandboxOrder.objects.create(api_key=key, branch=branch, number=number, status="received", data={
        "items": items, "subtotal": str(priced["subtotal"]), "service_charge": str(priced["service_charge"]),
        "vat": f'{priced["vat"]["vat_amount"]:.2f}', "total": str(priced["total"]), "currency": branch.currency,
        "payment_method": method, "paid": paid, "external_ref": external_ref, "cancel_reason": "", **customer,
    })
    webhooks.sandbox_event(s, "order.created")
    return JsonResponse(sandbox_json(s), status=201)


# Allowed status changes, in API terms (sandbox orders follow the same rules as real ones).
_API_TRANSITIONS = {
    "received": {"preparing", "cancelled"}, "preparing": {"ready", "cancelled"},
    "ready": {"completed", "preparing"}, "completed": set(), "cancelled": set(),
}


@endpoint("GET", "PATCH")
def order_detail(request, key, token):
    try:
        token = uuid.UUID(token)
    except ValueError:
        raise ApiError(404, "not_found", "No order with this id.")
    if key.is_sandbox:
        s = SandboxOrder.objects.select_related("branch").filter(api_key__restaurant=key.restaurant, token=token,
                                                                  branch__in=allowed_branches(key)).first()
        if s is None:
            raise ApiError(404, "not_found", "No test order with this id.")
        if request.method == "PATCH":
            to, reason = _status_from(request)
            if to != s.status:
                if to not in _API_TRANSITIONS[s.status]:
                    raise ApiError(409, "invalid_transition", f"Can't change a {s.status} order to {to}.")
                s.status = to
                if to == "cancelled":
                    s.data["cancel_reason"] = reason
                s.save()
                webhooks.sandbox_event(s, "order.status_changed")
        return JsonResponse(sandbox_json(s))
    order = (Order.objects.select_related("restaurant").prefetch_related("items__options")
             .filter(public_token=token, restaurant__in=allowed_branches(key)).first())
    if order is None:
        raise ApiError(404, "not_found", "No order with this id for this API key.")
    if request.method == "PATCH":
        to, reason = _status_from(request)
        target = FROM_API_STATUS[to]
        if to == "received":
            if order.status not in (OrderStatus.NEW, OrderStatus.ACCEPTED):
                raise ApiError(409, "invalid_transition", "Only a new order can be marked received.")
            target = OrderStatus.ACCEPTED
        try:
            if order.status != target:
                change_status(order, target, note=reason)
        except OrderError as exc:
            raise ApiError(409, exc.code, str(exc))
        order = Order.objects.select_related("restaurant").prefetch_related("items__options").get(pk=order.pk)
    return JsonResponse(order_json(order))


def _status_from(request):
    data = _json_body(request)
    to = str(data.get("status") or "").lower()
    if to not in API_STATUSES:
        raise ApiError(400, "invalid_status", f"status must be one of: {', '.join(API_STATUSES)}.")
    return to, str(data.get("reason") or "")[:200]


@csrf_exempt
def not_found(request, *args, **kwargs):
    return _cors(JsonResponse({"error": {"code": "not_found", "message": "Unknown endpoint. See the API documentation."}},
                              status=404))
