from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Every action taken in the platform console, and by whom."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=60)
    target = models.CharField(max_length=200, blank=True)
    details = models.CharField(max_length=500, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.target}"


class PageViewDay(models.Model):
    """
    Daily visits per public page: the home page, the sign-up page, or one restaurant's menu.
    `visitors` = different people (one device + browser per day); `views` = page loads.
    Counted by apps.console.visits (no IP addresses or personal data are stored).
    """

    HOME, SIGNUP, MENU = "home", "signup", "menu"
    PAGE_CHOICES = [(HOME, "Home page"), (SIGNUP, "Sign-up page"), (MENU, "Restaurant menu")]

    date = models.DateField(db_index=True)
    page = models.CharField(max_length=10, choices=PAGE_CHOICES)
    restaurant = models.ForeignKey("restaurants.Restaurant", null=True, blank=True, on_delete=models.CASCADE,
                                   related_name="+")
    views = models.PositiveIntegerField(default=0)
    visitors = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-date"]
        constraints = [models.UniqueConstraint(fields=["date", "page", "restaurant"], name="one_row_per_page_day"),
                       models.UniqueConstraint(fields=["date", "page"], condition=models.Q(restaurant__isnull=True),
                                               name="one_row_per_site_page_day")]

    def __str__(self):
        return f"{self.date} {self.page} {self.visitors}/{self.views}"


class CallbackRequest(models.Model):
    """A restaurant owner asked to be called back (home page form), e.g. to get the menu set up for them."""

    NEW, CONTACTED, REGISTERED, NOT_INTERESTED = "new", "contacted", "registered", "no"
    STATUS_CHOICES = [(NEW, "To call"), (CONTACTED, "Contacted"), (REGISTERED, "Registered"),
                      (NOT_INTERESTED, "Not interested")]
    TIME_CHOICES = [("any", "Any time"), ("morning", "Morning"), ("afternoon", "Afternoon"), ("evening", "Evening")]

    name = models.CharField(max_length=80)
    restaurant_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=30, help_text="WhatsApp or phone number.")
    best_time = models.CharField(max_length=10, choices=TIME_CHOICES, default="any")
    setup_help = models.BooleanField(default=True, help_text="Wants us to set up the menu for them.")
    language = models.CharField(max_length=5, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=NEW, db_index=True)
    notes = models.TextField(blank=True)
    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    handled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} · {self.restaurant_name}"

    @property
    def whatsapp_number(self) -> str:
        """Digits for wa.me; local Timor-Leste numbers (7xxxxxxx / 3xxxxxx) get the 670 country code."""
        digits = "".join(ch for ch in self.phone if ch.isdigit())
        if self.phone.strip().startswith("00"):
            digits = digits[2:]
        if len(digits) in (7, 8) and not digits.startswith("670"):
            digits = "670" + digits
        return digits


class VisitorLog(models.Model):
    """
    One row per new visitor per page per day, for the Visitors page: when, which page,
    country, and a partly hidden IP (e.g. 150.228.169.x). Kept 90 days.
    """

    created_at = models.DateTimeField(db_index=True)
    page = models.CharField(max_length=10, choices=PageViewDay.PAGE_CHOICES)
    restaurant = models.ForeignKey("restaurants.Restaurant", null=True, blank=True, on_delete=models.CASCADE,
                                   related_name="+")
    ip_masked = models.CharField(max_length=40, blank=True)
    country = models.CharField(max_length=2, blank=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class DemoRequest(models.Model):
    """
    Someone asked to try the demo (home page → /demo/). They get an emailed one-click link that
    logs them into the demo restaurant's dashboard in the view-only Demo role until `expires_at`.
    Only a working inbox receives the link, so the email address is confirmed by using it.
    """

    name = models.CharField(max_length=120)
    address = models.CharField(max_length=255)
    job_title = models.CharField(max_length=120)
    email = models.EmailField(db_index=True)
    language = models.CharField(max_length=5, blank=True)
    ip_masked = models.CharField(max_length=40, blank=True)
    token = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    opened_count = models.PositiveIntegerField(default=0)
    first_opened_at = models.DateTimeField(null=True, blank=True)
    last_opened_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} <{self.email}>"
