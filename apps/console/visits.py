"""
Visitor counter for the platform console: how many people open the home page, the
sign-up page and each restaurant's menu, per day.

A "person" is one device + browser per day, recognised by an anonymous daily hash of
IP address and browser kept in the cache for two days. The database keeps daily totals
and, per visitor, only the country and a partly hidden IP (150.228.169.x), for 90 days.
Bots, logged-in staff and admins are not counted.
"""
import hashlib
import logging
import random
import re
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.core.utils import client_ip

from .models import PageViewDay

logger = logging.getLogger(__name__)
BOT = re.compile(r"bot|spider|crawl|slurp|curl|wget|python|headless|monitor|preview|facebookexternalhit|scanner|"
                 r"go-http|zgrab|httpx|nmap|okhttp|java/|libwww|lighthouse|pingdom|uptime", re.I)
MENU_PATH = re.compile(r"^/r/(?P<slug>[-\w]+)/(?:$|t/[^/]+/$|carwash/$)")


def classify(path: str):
    """(page, restaurant slug) for pages we count, else None."""
    if path == "/":
        return PageViewDay.HOME, None
    if path == "/accounts/signup/":
        return PageViewDay.SIGNUP, None
    m = MENU_PATH.match(path)
    if m:
        return PageViewDay.MENU, m.group("slug")
    return None


def visitor_hash(day, ip: str, user_agent: str) -> str:
    return hashlib.sha256(f"{settings.SECRET_KEY}|{day}|{ip}|{user_agent}".encode()).hexdigest()[:24]


def count(day, page: str, restaurant_id, who: str, views: int = 1, ip: str = "") -> None:
    """Add views; the visitor counts once per page per day (and is listed with country + partly hidden IP)."""
    new_visitor = cache.add(f"visit:{day}:{page}:{restaurant_id or '-'}:{who}", 1, 2 * 86400)
    if new_visitor and ip:
        log_visitor(timezone.now(), page, restaurant_id, ip)
    filters = {"date": day, "page": page, "restaurant_id": restaurant_id}
    updated = PageViewDay.objects.filter(**filters).update(
        views=F("views") + views, visitors=F("visitors") + (1 if new_visitor else 0))
    if not updated:
        try:
            with transaction.atomic():
                PageViewDay.objects.create(**filters, views=views, visitors=1 if new_visitor else 0)
        except IntegrityError:  # created at the same moment by another request
            PageViewDay.objects.filter(**filters).update(
                views=F("views") + views, visitors=F("visitors") + (1 if new_visitor else 0))


def log_visitor(when, page, restaurant_id, ip) -> None:
    from . import geo
    from .models import VisitorLog

    VisitorLog.objects.create(created_at=when, page=page, restaurant_id=restaurant_id,
                              ip_masked=geo.mask(ip), country=geo.country(ip))
    if random.random() < 0.01:  # keep 90 days
        VisitorLog.objects.filter(created_at__lt=timezone.now() - timedelta(days=90)).delete()


def _restaurant_id(slug):
    from apps.restaurants.models import Restaurant

    key = f"visit-slug:{slug}"
    rid = cache.get(key)
    if rid is None:
        rid = Restaurant.objects.filter(slug=slug).values_list("id", flat=True).first() or 0
        cache.set(key, rid, 3600)
    return rid or None


class VisitCounterMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            self._record(request, response)
        except Exception:  # counting must never break a page
            logger.exception("Visit counter failed")
        return response

    def _record(self, request, response):
        if request.method != "GET" or response.status_code != 200:
            return
        if "text/html" not in response.get("Content-Type", ""):
            return
        hit = classify(request.path)
        if hit is None:
            return
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:  # owners, staff and admins checking their own pages
            return
        ua = request.META.get("HTTP_USER_AGENT", "")
        if not ua or BOT.search(ua):
            return
        page, slug = hit
        restaurant_id = _restaurant_id(slug) if slug else None
        if page == PageViewDay.MENU and restaurant_id is None:
            return
        day = timezone.localdate()
        ip = client_ip(request) or ""
        count(day, page, restaurant_id, visitor_hash(day, ip, ua), ip=ip)
