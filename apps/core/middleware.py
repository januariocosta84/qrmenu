from .i18n import DEFAULT_LANGUAGE, normalize_language

SESSION_KEY = "ui_lang"


class UILanguageMiddleware:
    """
    Resolves the customer UI language into `request.ui_lang`.

    Priority: ?lang= query param (remembered in the session) > session >
    browser Accept-Language > default. `request.ui_lang_explicit` tells views
    whether the customer chose a language, so a restaurant's own default
    language can be applied otherwise.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        lang = normalize_language(request.GET.get("lang"))
        explicit = False
        if lang:
            request.session[SESSION_KEY] = lang
            explicit = True
        else:
            lang = normalize_language(request.session.get(SESSION_KEY))
            explicit = bool(lang)
        if not lang:
            lang = self._from_header(request.META.get("HTTP_ACCEPT_LANGUAGE", ""))
        request.ui_lang = lang or DEFAULT_LANGUAGE
        request.ui_lang_explicit = explicit
        return self.get_response(request)

    @staticmethod
    def _from_header(header: str) -> str | None:
        for part in header.split(","):
            code = normalize_language(part.split(";")[0])
            if code:
                return code
        return None


STAFF_PATHS = ("/dashboard/", "/accounts/", "/api/v1/r/", "/api/v1/auth/", "/i18n/", "/jsi18n/")


class StaffLanguageMiddleware:
    """
    Activates the language staff picked for the dashboard (the `dash_lang`
    cookie set by Django's set_language view). Customer pages are left alone:
    they use UILanguageMiddleware and their own strings.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        from django.utils import translation

        lang = settings.LANGUAGE_CODE
        if request.path == "/" or request.path.startswith(STAFF_PATHS):  # "/" = landing page for owners
            chosen = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
            if chosen in dict(settings.LANGUAGES):
                lang = chosen
        translation.activate(lang)
        request.LANGUAGE_CODE = lang
        response = self.get_response(request)
        response.headers.setdefault("Content-Language", lang)
        translation.deactivate()
        return response
