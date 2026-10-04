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
        "drawer_available": settings.CASH_DRAWER_NETWORK_ENABLED,
        "receipt_network": _receipt_network(request),
    }


def _receipt_network(request) -> bool:
    restaurant = getattr(request, "restaurant", None)
    if restaurant is None:
        return False
    from apps.payments.receipts import network_printing_available

    return network_printing_available(restaurant)
