"""WSGI entry point (HTTP only — WebSockets require ASGI, see config/asgi.py)."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_wsgi_application()
