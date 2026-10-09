"""
Django settings for the QR restaurant ordering system.

All deployment-specific values come from environment variables (optionally
loaded from a `.env` file in the project root). Defaults are safe for local
development only.
"""
import os
import time
from pathlib import Path

import dj_database_url
import django.conf.locale

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, no interpolation."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-insecure-secret-key-change-me"
    else:
        raise RuntimeError("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# Base URL printed inside QR codes, e.g. https://menu.example.com
# Falls back to the URL of the current request when empty.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

INSTALLED_APPS = [
    "daphne",  # ASGI runserver for WebSockets in development
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
    "rest_framework.authtoken",
    "channels",
    "apps.core",
    "apps.accounts",
    "apps.restaurants",
    "apps.menu",
    "apps.orders",
    "apps.payments",
    "apps.storefront",
    "apps.dashboard",
    "apps.billing",
    "apps.console",
    "apps.quotations",
    "apps.expenses",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.UILanguageMiddleware",
    "apps.core.middleware.StaffLanguageMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.ui",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Database: PostgreSQL in production via DATABASE_URL, SQLite fallback for dev.
DATABASES = {
    "default": dj_database_url.parse(
        os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
        conn_max_age=int(os.environ.get("DB_CONN_MAX_AGE", "60")),
    )
}

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en"
# Staff dashboard languages (gettext catalogs in locale/). The customer menu
# has its own language list in apps/core/i18n.py.
LANGUAGES = [
    ("en", "English"),
    ("pt", "Português"),
    ("tet", "Tetun"),
    ("id", "Bahasa Indonesia"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
FORMAT_MODULE_PATH = ["config.formats"]  # Tetun date formats
LANGUAGE_COOKIE_NAME = "dash_lang"
LANGUAGE_COOKIE_AGE = 365 * 24 * 3600
LANGUAGE_COOKIE_SAMESITE = "Lax"
# Django ships no Tetun locale; register it so the language tools know its name.
django.conf.locale.LANG_INFO.setdefault(
    "tet", {"bidi": False, "code": "tet", "name": "Tetum", "name_local": "Tetun"}
)
TIME_ZONE = os.environ.get("TIME_ZONE", "Asia/Dili")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
# Appended to dashboard CSS/JS links (?v=...) so browsers fetch fresh files after each restart.
STATIC_VERSION = os.environ.get("STATIC_VERSION") or str(int(time.time()))
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Uploads: images are re-encoded server-side; reject anything larger than this.
MAX_IMAGE_UPLOAD_BYTES = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024

# How long a QR scan lets a phone place orders for that table.
TABLE_ACCESS_HOURS = int(os.environ.get("TABLE_ACCESS_HOURS", "6"))

# Redis powers the channel layer and cache in production. Without it, an
# in-memory layer is used, which only works with a single server process.
REDIS_URL = os.environ.get("REDIS_URL", "")
if REDIS_URL:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [REDIS_URL]},
        }
    }
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    # Number of trusted reverse proxies (1 behind Nginx) for client-IP detection.
    "NUM_PROXIES": int(os.environ.get("NUM_PROXIES", "0")) or None,
    "DEFAULT_THROTTLE_RATES": {
        "order_create": os.environ.get("THROTTLE_ORDER_CREATE", "10/min"),
        "public_read": os.environ.get("THROTTLE_PUBLIC_READ", "120/min"),
        "staff": "600/min",
        "auth_token": "10/min",
    },
}

# ---- Public platform (self-service restaurant sign-up) ----
PLATFORM_NAME = os.environ.get("PLATFORM_NAME", "QR Menu")
# Subject prefix for emails to platform admins (Django's default is "[Django] ").
EMAIL_SUBJECT_PREFIX = os.environ.get("EMAIL_SUBJECT_PREFIX", f"[{PLATFORM_NAME}] ")
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "")
# WhatsApp contact on the landing page (digits with country code, no +); empty hides it.
SUPPORT_WHATSAPP = "".join(c for c in os.environ.get("SUPPORT_WHATSAPP", "67075946629") if c.isdigit())
# "open": a restaurant goes live as soon as the owner verifies their email.
# "approval": a platform admin must also approve it in /admin/ first.
# "closed": no public sign-up (restaurants are created by the platform admin).
SIGNUP_MODE = os.environ.get("SIGNUP_MODE", "open").lower()
SIGNUPS_PER_IP_PER_HOUR = int(os.environ.get("SIGNUPS_PER_IP_PER_HOUR", "5"))
EMAIL_VERIFICATION_DAYS = 3
# Per-restaurant limits against abuse of a free public service.
MAX_TABLES_PER_RESTAURANT = int(os.environ.get("MAX_TABLES_PER_RESTAURANT", "300"))
MAX_MENU_ITEMS_PER_RESTAURANT = int(os.environ.get("MAX_MENU_ITEMS_PER_RESTAURANT", "500"))
MAX_STAFF_PER_RESTAURANT = int(os.environ.get("MAX_STAFF_PER_RESTAURANT", "50"))
# Path of the platform admin (change it to something non-obvious in production).
ADMIN_URL = os.environ.get("ADMIN_URL", "admin/").strip("/") + "/"
# People emailed about new sign-ups waiting for approval: "Name <a@b.c>,Other <d@e.f>"
def _parse_admin(entry: str) -> tuple[str, str]:
    if "<" in entry:
        name, email = entry.split("<", 1)
        return name.strip(), email.strip().rstrip(">").strip()
    return entry, entry


ADMINS = [_parse_admin(e) for e in env_list("PLATFORM_ADMINS")]
# The server connects to restaurant receipt printers to open cash drawers. Only
# enable this when the server runs *inside* the restaurant network (self-hosted).
# On a public cloud server it can't reach restaurant printers anyway, and it
# would let restaurant accounts make the server open internal connections.
CASH_DRAWER_NETWORK_ENABLED = env_bool("CASH_DRAWER_NETWORK_ENABLED", False)

# ---- Email (verification, password reset) ----
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "QR Menu <no-reply@localhost>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
if os.environ.get("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.environ["EMAIL_HOST"]
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
    # Port 587 → EMAIL_USE_TLS=true (STARTTLS); port 465 → EMAIL_USE_SSL=true. Only one may be on.
    EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", not EMAIL_USE_SSL) and not EMAIL_USE_SSL
    EMAIL_TIMEOUT = 15
else:
    # No SMTP configured: emails are printed to the server console (development only).
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

AUTHENTICATION_BACKENDS = ["apps.accounts.backends.EmailOrUsernameBackend"]

# Login brute-force protection (per IP + username).
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60

# Security hardening
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}
