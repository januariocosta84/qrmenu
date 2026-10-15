from django.conf import settings

from .i18n import LANGUAGES, ui_strings


def ui(request):
    lang = getattr(request, "ui_lang", "en")
    return {
        "ui_lang": lang,
        "ui": ui_strings(lang),
        "ui_languages": LANGUAGES,
        "platform_name": settings.PLATFORM_NAME,
        "support_email": settings.SUPPORT_EMAIL,
        "support_whatsapp": settings.SUPPORT_WHATSAPP,
        "static_v": settings.STATIC_VERSION,
        "drawer_available": settings.CASH_DRAWER_NETWORK_ENABLED,
        "receipt_network": _receipt_network(request),
        "drawer_mode": _drawer_mode(request),
        "kitchen_services": _kitchen_services(),
    }


def _kitchen_services() -> list[str]:
    """Services the kitchen prepares (café, …): the dashboard's order alarms ring only for these, not car wash."""
    from apps.menu.service_types import KITCHEN_SERVICES

    return KITCHEN_SERVICES


def _drawer_mode(request) -> str:
    restaurant = getattr(request, "restaurant", None)
    if restaurant is None:
        return ""
    from apps.payments.drawer import drawer_mode

    return drawer_mode(restaurant)


def _receipt_network(request) -> bool:
    restaurant = getattr(request, "restaurant", None)
    if restaurant is None:
        return False
    from apps.payments.receipts import network_printing_available

    return network_printing_available(restaurant)
