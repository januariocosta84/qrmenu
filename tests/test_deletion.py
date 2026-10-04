from django.db.models import RestrictedError
from django.test import TestCase

from apps.menu.models import MenuCategory, MenuItem
from apps.orders.services import place_order
from apps.restaurants.models import Restaurant

from .test_ordering import make_restaurant


class DeletionTests(TestCase):
    def test_restaurant_with_menu_and_orders_can_be_deleted(self):
        r, rice, egg, tea, table = make_restaurant()
        place_order(restaurant=r, table=table, lines=[{"menu_item": rice.id, "quantity": 1, "options": [egg.id]}])
        r.delete()
        self.assertFalse(Restaurant.objects.filter(slug="alpha").exists())
        self.assertFalse(MenuItem.objects.exists())

    def test_category_with_dishes_still_cannot_be_deleted(self):
        r, rice, *_ = make_restaurant()
        with self.assertRaises(RestrictedError):
            rice.category.delete()
        self.assertTrue(MenuCategory.objects.filter(pk=rice.category_id).exists())
