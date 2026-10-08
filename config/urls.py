from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.i18n import JavaScriptCatalog

admin.site.site_header = "QR Menu — Platform admin"

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("i18n/", include("django.conf.urls.i18n")),  # set_language: dashboard language switcher
    path("jsi18n/", JavaScriptCatalog.as_view(), name="javascript-catalog"),  # gettext() for dashboard JS
    path("platform/", include("apps.console.urls")),
    path("", include("apps.dashboard.urls")),
    path("", include("apps.storefront.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
