"""
Search engines (Google): robots.txt, sitemap.xml with dish photos, and per-menu
meta tags + schema.org structured data (Restaurant → Menu → sections → dishes with
price and photo), so a restaurant's menu can show up in Google with its pictures.

Only live restaurants whose owner left "Show my menu on Google" on, with at least
one dish, are listed. Table links (/t/…), walk-in car wash links, order pages and
everything behind a login stay out of search engines.
"""
import json
from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Max, Q
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import Truncator
from django.views.decorators.http import require_GET

from apps.menu import service_types
from apps.restaurants.models import Restaurant

SITEMAP_CACHE = "seo:sitemap"


def base_url(request) -> str:
    return (settings.PUBLIC_BASE_URL or request.build_absolute_uri("/")).rstrip("/")


def listed_restaurants():
    """Restaurants search engines may list: live, opted in, with dishes on the menu."""
    return (Restaurant.objects.filter(is_active=True, show_in_search=True, branch_closed_at__isnull=True)
            .filter(Q(parent__isnull=True) | Q(branch_status=Restaurant.BRANCH_ACTIVE))
            .annotate(n_items=Count("menu_items", filter=Q(menu_items__category__is_active=True,
                                                          menu_items__category__service_type__in=service_types.KITCHEN_SERVICES)))
            .filter(n_items__gt=0))


def is_listed(restaurant) -> bool:
    return listed_restaurants().filter(pk=restaurant.pk).exists()


def menu_description(restaurant, categories, lang) -> str:
    """Owner's description, or an automatic one: where, and a few dishes."""
    desc = strip_tags(restaurant.tr("description", lang) or "").strip()
    dishes = [i.tr("name", lang) for c in categories if service_types.get(c.service_type).kitchen
              for i in c.items.all()][:6]
    where = f" in {restaurant.address}" if restaurant.address else ""
    auto = f"Menu and prices of {restaurant.name}{where}: {', '.join(dishes)}. Order from your table with QR Menu."
    return Truncator(desc or auto).chars(160)


def _abs(base, file_field):
    if not file_field:
        return None
    url = file_field.url
    return url if url.startswith("http") else base + url


def menu_jsonld(restaurant, categories, lang, base) -> str:
    """schema.org Restaurant with its Menu: sections, dishes, prices and photos."""
    menu_url = base + reverse("storefront:menu", args=[restaurant.slug])
    sections = []
    for c in categories:
        if not service_types.get(c.service_type).kitchen:
            continue  # car wash packages are a service, not food on the menu
        items = []
        for i in c.items.all():
            item = {"@type": "MenuItem", "name": i.tr("name", lang),
                    "offers": {"@type": "Offer", "price": f"{i.price:.2f}", "priceCurrency": restaurant.currency,
                               "availability": "https://schema.org/InStock" if i.is_available
                               else "https://schema.org/OutOfStock"}}
            if i.tr("description", lang):
                item["description"] = strip_tags(i.tr("description", lang))[:300]
            if i.image:
                item["image"] = _abs(base, i.image)
            items.append(item)
        if items:
            sections.append({"@type": "MenuSection", "name": c.tr("name", lang), "hasMenuItem": items})
    data = {
        "@context": "https://schema.org", "@type": "Restaurant", "@id": menu_url + "#restaurant",
        "name": restaurant.name, "url": menu_url,
        "hasMenu": {"@type": "Menu", "name": f"{restaurant.name} menu", "url": menu_url, "inLanguage": lang,
                    "hasMenuSection": sections},
    }
    images = [u for u in (_abs(base, restaurant.cover_image), _abs(base, restaurant.logo)) if u]
    if images:
        data["image"] = images
    if restaurant.logo:
        data["logo"] = _abs(base, restaurant.logo)
    if restaurant.description:
        data["description"] = strip_tags(restaurant.tr("description", lang))[:500]
    if restaurant.address:
        data["address"] = {"@type": "PostalAddress", "streetAddress": restaurant.address, "addressCountry": "TL"}
    if restaurant.phone:
        data["telephone"] = restaurant.phone
    # "</" must not end the <script> element early.
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def menu_seo(request, restaurant, categories, lang, *, table=None, walk_in=False) -> dict:
    """Everything the menu page's <head> needs for search engines and link previews."""
    base = base_url(request)
    canonical = base + reverse("storefront:menu", args=[restaurant.slug])
    listed = not table and not walk_in and is_listed(restaurant)
    image = _abs(base, restaurant.cover_image) or _abs(base, restaurant.logo) or next(
        (_abs(base, i.image) for c in categories for i in c.items.all() if i.image), None)
    return {
        "title": f"{restaurant.name} – Menu & prices" + (f" · {restaurant.address}" if restaurant.address else ""),
        "description": menu_description(restaurant, categories, lang),
        "canonical": canonical,
        "noindex": not listed,
        "image": image,
        "jsonld": menu_jsonld(restaurant, categories, lang, base) if listed else "",
    }


# ---------------------------------------------------------------- robots.txt & sitemap.xml

@require_GET
def robots_txt(request):
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /dashboard/", "Disallow: /platform/", "Disallow: /accounts/", "Disallow: /api/",
        "Disallow: /o/", "Disallow: /call-me-back/", "Disallow: /r/*/t/", "Disallow: /r/*/carwash/",
        "Disallow: /restaurant/",
        f"Disallow: /{settings.ADMIN_URL}",
        "",
        f"Sitemap: {base_url(request)}{reverse('storefront:sitemap')}",
    ]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")


@require_GET
def sitemap_xml(request):
    xml = cache.get(SITEMAP_CACHE)
    if xml is None:
        xml = _build_sitemap(base_url(request))
        cache.set(SITEMAP_CACHE, xml, 3600)
    return HttpResponse(xml, content_type="application/xml")


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def _build_sitemap(base) -> str:
    from apps.menu.models import MenuItem

    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
           'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">']
    today = timezone.localdate().isoformat()
    for path in ("/", reverse("storefront:terms")):
        out.append(f"<url><loc>{_esc(base + path)}</loc><lastmod>{today}</lastmod></url>")
    for r in listed_restaurants().annotate(menu_changed=Max("menu_items__updated_at")).order_by("name"):
        loc = base + reverse("storefront:menu", args=[r.slug])
        changed = max(d for d in (r.updated_at, r.menu_changed) if d)
        out.append(f"<url><loc>{_esc(loc)}</loc><lastmod>{timezone.localtime(changed).date().isoformat()}</lastmod>")
        photos = [f for f in (r.cover_image, r.logo) if f]
        photos += [i.image for i in MenuItem.objects.filter(
            restaurant=r, category__is_active=True, category__service_type__in=service_types.KITCHEN_SERVICES)
            .exclude(image="").only("image")[:200]]
        for f in photos:
            out.append(f"<image:image><image:loc>{_esc(_abs(base, f))}</image:loc></image:image>")
        out.append("</url>")
    out.append("</urlset>")
    return "\n".join(out)
