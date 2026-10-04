from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Staff/admin account. Customers never need an account."""

    phone = models.CharField(max_length=30, blank=True)
    # Self-registered owners must confirm their email before their restaurant goes live.
    email_verified = models.BooleanField(default=False)

    def restaurant_memberships(self):
        # Includes restaurants that are not live yet, so owners can set them up.
        return self.memberships.filter(is_active=True).select_related("restaurant")
