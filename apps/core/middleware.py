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
