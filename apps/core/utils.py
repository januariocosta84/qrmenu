from django.conf import settings


def client_ip(request) -> str | None:
    """Client IP, trusting X-Forwarded-For only for the configured number of proxies."""
    proxies = settings.REST_FRAMEWORK.get("NUM_PROXIES") or 0
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if proxies and forwarded:
        addrs = [a.strip() for a in forwarded.split(",") if a.strip()]
        if addrs:
            return addrs[-min(proxies, len(addrs))]
    return request.META.get("REMOTE_ADDR")
