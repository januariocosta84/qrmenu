"""
Ordering API (Pro): API keys of a main branch, the request log, webhooks and
sandbox orders. Keys are stored hashed; the full key is shown once at creation.
"""
import hashlib
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.restaurants.models import Restaurant

LIVE_PREFIX, TEST_PREFIX = "qrm_live_", "qrm_test_"


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class ApiKey(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="api_keys",
                                   help_text="The main branch that owns this key.")
    name = models.CharField(_("Name"), max_length=60, help_text=_("e.g. Website, Mobile app, Delivery partner"))
    is_sandbox = models.BooleanField(_("Test (sandbox) key"), default=False,
                                     help_text=_("Test orders are not sent to the kitchen and never count as revenue."))
    key_hash = models.CharField(max_length=64, unique=True)
    last4 = models.CharField(max_length=4)
    branches = models.ManyToManyField(Restaurant, blank=True, related_name="+",
                                      help_text="Branches this key may use; none selected = all branches.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["revoked_at", "-created_at"]

    def __str__(self):
        return f"{self.name} ({self.masked})"

    @classmethod
    def generate(cls, *, restaurant, name, is_sandbox=False, created_by=None) -> tuple["ApiKey", str]:
        """Create a key; returns (key, full secret). The secret is not stored and can't be shown again."""
        raw = (TEST_PREFIX if is_sandbox else LIVE_PREFIX) + secrets.token_urlsafe(30)
        key = cls.objects.create(restaurant=restaurant, name=name.strip()[:60], is_sandbox=is_sandbox,
                                 key_hash=hash_key(raw), last4=raw[-4:], created_by=created_by)
        return key, raw

    @property
    def masked(self) -> str:
        return f"{TEST_PREFIX if self.is_sandbox else LIVE_PREFIX}••••{self.last4}"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    @property
    def source_label(self) -> str:
        return f"API – {self.name}"

    def revoke(self):
        self.revoked_at = timezone.now()
        self.save(update_fields=["revoked_at"])


class ApiRequestLog(models.Model):
    """Every Ordering API request: time, key, endpoint, result. Shown to the main branch."""

    restaurant = models.ForeignKey(Restaurant, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    api_key = models.ForeignKey(ApiKey, null=True, blank=True, on_delete=models.SET_NULL, related_name="requests")
    key_name = models.CharField(max_length=60, blank=True)
    method = models.CharField(max_length=8)
    path = models.CharField(max_length=200)
    status_code = models.PositiveSmallIntegerField()
    error = models.CharField(max_length=40, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["restaurant", "created_at"])]

    @property
    def ok(self) -> bool:
        return self.status_code < 400


class Webhook(models.Model):
    """The main branch's URL that is told whenever an order changes status."""

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="webhooks")
    url = models.URLField(_("Webhook URL"), max_length=300, help_text=_("Must start with https://"))
    secret = models.CharField(max_length=64, editable=False)
    all_orders = models.BooleanField(
        _("Also send orders not placed through the API"), default=False,
        help_text=_("Off: only orders created with your API keys. On: every order of your branches (QR, waiter, API)."),
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    last_status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    last_error = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["created_at"]

    def save(self, *args, **kwargs):
        if not self.secret:
            self.secret = "whsec_" + secrets.token_urlsafe(24)
        super().save(*args, **kwargs)


class WebhookDelivery(models.Model):
    webhook = models.ForeignKey(Webhook, on_delete=models.CASCADE, related_name="deliveries")
    event = models.CharField(max_length=40)
    order_ref = models.CharField(max_length=40, blank=True)
    sandbox = models.BooleanField(default=False)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    error = models.CharField(max_length=200, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    @property
    def ok(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code < 300


class SandboxOrder(models.Model):
    """An order made with a test key: priced like a real one, but never sent to the kitchen or counted."""

    api_key = models.ForeignKey(ApiKey, on_delete=models.CASCADE, related_name="sandbox_orders")
    branch = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="+")
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    number = models.PositiveIntegerField()
    status = models.CharField(max_length=20)
    data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
