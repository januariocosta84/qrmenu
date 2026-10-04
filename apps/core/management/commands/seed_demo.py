from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.permissions import Role
from apps.menu.models import MenuCategory, MenuItem, MenuItemOption
from apps.restaurants.models import Restaurant, RestaurantStaff, Table

# (category, tetum, indonesian, [(name, desc, price, tet name, id name, [(option, price)])])
MENU = [
    ("Breakfast", "Matabixu", "Sarapan", [
        ("Banana Pancakes", "Fluffy pancakes with banana and honey", "3.50", "Pankeka Hudi", "Panekuk Pisang", [("Extra honey", "0.25")]),
        ("Omelette & Toast", "Three-egg omelette with toast", "3.00", "Omelete no Paun", "Omelet & Roti", [("Cheese", "0.50")]),
    ]),
    ("Main Meals", "Hahán Prinsipál", "Hidangan Utama", [
        ("Nasi Campur", "Rice with mixed vegetables, egg and sambal", "4.50", "Etu Kahur", "Nasi Campur", []),
    ]),
    ("Rice", "Etu", "Nasi", [
        ("Chicken Fried Rice", "Fried rice with chicken, vegetables and egg", "5.00", "Etu Sona ho Manu", "Nasi Goreng Ayam",
         [("Fried Egg", "0.50"), ("Extra chicken", "1.00"), ("No spicy", "0")]),
        ("Seafood Fried Rice", "Fried rice with prawns and squid", "6.00", "Etu Sona ho Ikan-Tasi", "Nasi Goreng Seafood", [("Fried Egg", "0.50")]),
    ]),
    ("Noodles", "Mi", "Mi", [
        ("Mie Goreng", "Stir-fried noodles with vegetables", "4.00", "Mi Sona", "Mie Goreng", [("Fried Egg", "0.50"), ("Chicken", "1.00")]),
        ("Chicken Noodle Soup", "Noodle soup with chicken and greens", "4.00", "Supa Mi ho Manu", "Mie Kuah Ayam", []),
    ]),
    ("Seafood", "Ikan-Tasi", "Makanan Laut", [
        ("Grilled Fish", "Fresh catch with lime and chilli sauce", "8.00", "Ikan Tunu", "Ikan Bakar", [("Extra rice", "0.75")]),
    ]),
    ("Chicken", "Manu", "Ayam", [
        ("Ayam Bakar", "Grilled marinated chicken with rice", "5.50", "Manu Tunu", "Ayam Bakar", [("Extra sambal", "0.25")]),
    ]),
    ("Beef", "Karau-Na'an", "Daging Sapi", [
        ("Beef Rendang", "Slow-cooked beef in coconut spices", "7.00", "Rendang Karau", "Rendang Sapi", [("Extra rice", "0.75")]),
    ]),
    ("Snacks", "Lanche", "Camilan", [
        ("French Fries", "Crispy fries with ketchup", "2.50", "Batata Frita", "Kentang Goreng", []),
        ("Spring Rolls", "Four vegetable spring rolls", "2.50", "Rolu Primavera", "Lumpia", []),
    ]),
    ("Drinks", "Hemu", "Minuman", [
        ("Iced Tea", "Sweet iced tea", "1.50", "Xá Malirin", "Es Teh", [("No sugar", "0")]),
        ("Timor Coffee", "Local arabica coffee", "1.50", "Kafé Timor", "Kopi Timor", [("Milk", "0.25")]),
        ("Fresh Coconut", "Whole young coconut", "2.00", "Nuu Foun", "Kelapa Muda", []),
        ("Mineral Water", "600 ml bottle", "0.75", "Bee Mineral", "Air Mineral", []),
    ]),
    ("Desserts", "Doce", "Pencuci Mulut", [
        ("Fried Banana", "Banana fritters with palm sugar", "2.00", "Hudi Sona", "Pisang Goreng", [("Ice cream", "1.00")]),
    ]),
]


class Command(BaseCommand):
    help = "Create a demo restaurant with a full menu, 12 tables and demo staff accounts (development only)."

    def add_arguments(self, parser):
        parser.add_argument("--slug", default="demo")
        parser.add_argument("--password", default="demo-pass-2024", help="Password for the demo staff accounts.")

    @transaction.atomic
    def handle(self, *args, slug, password, **options):
        User = get_user_model()
        restaurant, created = Restaurant.objects.get_or_create(
            slug=slug,
            defaults=dict(
                name="Dili Demo Kitchen",
                description="Fresh Timorese and Indonesian food.",
                address="Rua de Colmera, Dili, Timor-Leste",
                phone="+670 7700 0000",
                opening_hours="Mon–Sun 07:00–22:00",
                service_charge_percent=Decimal("0"),
                translations={
                    "tet": {"description": "Hahán Timor no Indonézia ne'ebé foun."},
                    "id": {"description": "Masakan Timor dan Indonesia yang segar."},
                },
            ),
        )
        if not created:
            self.stdout.write(self.style.WARNING(f"Restaurant '{slug}' already exists — leaving it unchanged."))
        else:
            for pos, (cat, cat_tet, cat_id, items) in enumerate(MENU):
                category = MenuCategory.objects.create(
                    restaurant=restaurant, name=cat, position=pos,
                    translations={"tet": {"name": cat_tet}, "id": {"name": cat_id}},
                )
                for ipos, (name, desc, price, name_tet, name_id, options) in enumerate(items):
                    item = MenuItem.objects.create(
                        restaurant=restaurant, category=category, name=name, description=desc,
                        price=Decimal(price), position=ipos,
                        translations={"tet": {"name": name_tet}, "id": {"name": name_id}},
                    )
                    for opos, (oname, oprice) in enumerate(options):
                        MenuItemOption.objects.create(menu_item=item, name=oname, price=Decimal(oprice), position=opos)
            for n in range(1, 13):
                Table.objects.create(restaurant=restaurant, number=str(n))

        for username, role in [(f"{slug}-owner", Role.OWNER), (f"{slug}-kitchen", Role.KITCHEN), (f"{slug}-waiter", Role.WAITER)]:
            user, made = User.objects.get_or_create(username=username)
            if made:
                user.set_password(password)
                user.save()
            RestaurantStaff.objects.get_or_create(restaurant=restaurant, user=user, defaults={"role": role})

        table = Table.objects.get(restaurant=restaurant, number="12")
        self.stdout.write(self.style.SUCCESS(f"Demo restaurant ready: /r/{slug}/"))
        self.stdout.write(f"  Staff logins (password '{password}'): {slug}-owner, {slug}-kitchen, {slug}-waiter")
        self.stdout.write(f"  Dashboard: /dashboard/{slug}/")
        self.stdout.write(f"  Table 12 QR link (open on your phone): {table.active_qr().get_path()}")
