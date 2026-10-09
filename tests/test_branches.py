from datetime import datetime, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.permissions import Role
from apps.expenses.models import Expense
from apps.menu.models import MenuItem
from apps.orders.models import Order, OrderStatus
from apps.orders.services import place_order
from apps.billing.models import Subscription
from apps.billing.services import get_subscription
from apps.restaurants.branches import create_branch, owned_restaurants
from apps.restaurants.models import BusinessProfile, Restaurant, RestaurantStaff

from .test_ordering import make_pro, make_restaurant, staff


def photo(name="front.png"):
    """A small real PNG upload (a storefront photo)."""
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "orange").save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def verify_profile(restaurant, number="SERVE-123", owner="Ana Owner"):
    return BusinessProfile.objects.create(restaurant=restaurant, registration_number=number, owner_name=owner,
                                          address="Colmera, Dili", phone="+670 7700 0000",
                                          status=BusinessProfile.VERIFIED)


@override_settings(SIGNUP_MODE="open")
class BranchTests(TestCase):
    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        self.r.service_charge_percent, self.r.currency_symbol, self.r.address = Decimal("5"), "$", "Colmera"
        self.r.printer_host = "192.168.1.50"
        self.r.save()
        self.owner = staff(self.r, "owner", Role.OWNER)
        self.owner.email_verified = True
        self.owner.save()
        make_pro(self.r)
        self.client.force_login(self.owner)
        self.url = reverse("dashboard:branches", args=["alpha"])

    def test_owner_requests_branch_copying_menu_and_settings(self):
        verify_profile(self.r)
        resp = self.client.post(self.url, {"action": "request", "name": "Alpha Comoro", "address": "Comoro, Dili",
                                           "phone": "+670 7712 3456", "storefront_photo": photo(),
                                           "registration_number": "SERVE-123", "owner_name": "Ana Owner",
                                           "source": self.r.pk, "copy_menu": "on", "copy_settings": "on"})
        b = Restaurant.objects.get(name="Alpha Comoro")
        self.assertRedirects(resp, reverse("dashboard:branch_manage", args=["alpha", b.pk]), fetch_redirect_response=False)
        self.assertEqual(b.parent, self.r)
        self.assertEqual(b.branch_status, Restaurant.BRANCH_PENDING)
        self.assertFalse(b.is_active)  # hidden until the platform approves it
        self.assertTrue(b.storefront_photo.name.endswith(".webp"))
        self.assertEqual(b.branch_flags, [])
        self.assertEqual(b.staff.get().user, self.owner)
        self.assertEqual(b.staff.get().role, Role.OWNER)
        self.assertEqual((b.service_charge_percent, b.address), (Decimal("5"), "Comoro, Dili"))
        self.assertEqual(b.printer_host, "")  # device settings are per branch
        copied = MenuItem.objects.get(restaurant=b, name="Chicken Fried Rice")
        self.assertNotEqual(copied.pk, self.rice.pk)
        self.assertEqual(copied.category.restaurant, b)
        self.assertEqual(copied.options.get().name, "Fried Egg")
        self.assertEqual(copied.translations, self.rice.translations)
        self.assertFalse(b.tables.exists())
        self.assertEqual(get_subscription(b), self.r.subscription)  # covered by the main branch's plan
        self.assertFalse(Subscription.objects.filter(restaurant=b).exists())
        # The original branch is untouched.
        self.assertEqual(MenuItem.objects.filter(restaurant=self.r).count(), 2)

    def test_pending_branch_by_default(self):
        b = create_branch(self.owner, main=self.r, name="Alpha Two", source=None)
        self.assertFalse(MenuItem.objects.filter(restaurant=b).exists())
        self.assertEqual((b.branch_status, b.is_active), (Restaurant.BRANCH_PENDING, False))
        self.assertTrue(create_branch(self.owner, main=self.r, name="Alpha Three", status=Restaurant.BRANCH_ACTIVE).is_active)

    def test_overview_per_branch_and_total(self):
        b = create_branch(self.owner, main=self.r, name="Alpha Comoro", source=self.r, status=Restaurant.BRANCH_ACTIVE)
        table = b.tables.create(number="1")
        for restaurant, t, item in ((self.r, self.table, self.tea), (b, table, MenuItem.objects.get(restaurant=b, name="Iced Tea"))):
            o = place_order(restaurant=restaurant, table=t, lines=[{"menu_item": item.id, "quantity": 2}])
            Order.objects.filter(pk=o.pk).update(status=OrderStatus.COMPLETED)
        resp = self.client.get(self.url)
        rows = {r["branch"].name: r for r in resp.context["rows"]}
        self.assertEqual(rows["Alpha"]["day"]["revenue"], Decimal("3.15"))   # 3.00 + 5% service
        self.assertEqual(rows["Alpha Comoro"]["day"]["revenue"], Decimal("3.15"))
        self.assertEqual(resp.context["total"]["day"]["revenue"], Decimal("6.30"))
        self.assertContains(resp, "All branches")

    def test_only_own_branches_are_listed(self):
        other, *_ = make_restaurant("beta")
        staff(other, "someone-else", Role.OWNER)
        self.assertEqual(list(owned_restaurants(self.owner)), [self.r])
        resp = self.client.get(self.url)
        self.assertNotContains(resp, "Beta")

    def test_staff_from_another_branch(self):
        b = create_branch(self.owner, main=self.r, name="Alpha Comoro", source=self.r)
        waiter = staff(self.r, "waiter", Role.WAITER)
        staff_url = reverse("dashboard:staff", args=[b.slug])
        page = self.client.get(staff_url)
        self.assertContains(page, "From another branch")
        self.client.post(staff_url, {"action": "existing", "user": waiter.pk, "role": Role.WAITER})
        self.assertTrue(RestaurantStaff.objects.filter(restaurant=b, user=waiter, role=Role.WAITER).exists())
        # Someone who doesn't work for this owner can't be added.
        other, *_ = make_restaurant("beta")
        stranger = staff(other, "stranger", Role.WAITER)
        self.client.post(staff_url, {"action": "existing", "user": stranger.pk, "role": Role.WAITER})
        self.assertFalse(RestaurantStaff.objects.filter(restaurant=b, user=stranger).exists())
        # Creating a brand-new account still works.
        self.client.post(staff_url, {"action": "new", "username": "newcook", "first_name": "", "email": "",
                                     "role": Role.KITCHEN, "password": "A-strong-pass-2026"})
        self.assertTrue(RestaurantStaff.objects.filter(restaurant=b, user__username="newcook").exists())

    def test_managers_and_waiters_cannot_open_branches(self):
        for role in (Role.MANAGER, Role.WAITER):
            self.client.force_login(staff(self.r, f"u-{role}", role))
            self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_switch_restaurant_lists_branches(self):
        create_branch(self.owner, main=self.r, name="Alpha Comoro", source=self.r)
        resp = self.client.get(reverse("dashboard:home"))
        self.assertContains(resp, "Alpha Comoro")


class BranchesProOnlyTests(TestCase):
    def test_standard_plan_sees_upgrade_page(self):
        cache.clear()
        r, *_ = make_restaurant()
        owner = staff(r, "owner", Role.OWNER)
        self.client.force_login(owner)
        url = reverse("dashboard:branches", args=["alpha"])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)
        self.assertTemplateUsed(resp, "dashboard/upgrade.html")
        self.client.post(url, {"name": "Sneaky branch"})
        self.assertFalse(Restaurant.objects.filter(name="Sneaky branch").exists())
        self.assertContains(self.client.get(reverse("dashboard:overview", args=["alpha"])), "PRO", count=7)
