"""
Create a realistic sample restaurant (menu with photos-style images, add-ons,
translations, tables) for demos and testing.

    python manage.py seed_sample --owner owner@example.com
"""
import io
import random
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.images import process_image
from apps.core.permissions import Role
from apps.menu.models import MenuCategory, MenuItem, MenuItemOption
from apps.restaurants.models import Restaurant, RestaurantStaff, Table

# Category: (name, tetum, indonesian, colour, [items])
# Item: (name, description, price, prep, emoji, tetum, indonesian, [(option, price)], available)
MENU = [
    ("Breakfast", "Matabixu", "Sarapan", "#f59e0b", [
        ("Banana Pancakes", "Three fluffy pancakes with banana and palm-sugar syrup", "3.50", 10, "🥞",
         "Pankeka Hudi", "Panekuk Pisang", [("Extra syrup", "0.25"), ("Ice cream", "1.00")], True),
        ("Nasi Uduk Breakfast", "Coconut rice with fried egg, tempeh and sambal", "3.00", 8, "🍳",
         "Etu Nuu ho Manutolun", "Nasi Uduk Pagi", [("Extra egg", "0.50")], True),
        ("Toast & Eggs", "Two eggs any style with buttered toast", "2.75", 7, "🍞",
         "Paun ho Manutolun", "Roti Bakar & Telur", [("Cheese", "0.50"), ("Sausage", "1.00")], True),
    ]),
    ("Main Meals", "Hahán Prinsipál", "Hidangan Utama", "#dc2626", [
        ("Nasi Campur Timor", "Rice with grilled chicken, vegetables, egg and chilli sauce", "5.00", 15, "🍛",
         "Etu Kahur Timor", "Nasi Campur Timor", [("Extra chicken", "1.50"), ("No spicy", "0")], True),
        ("Ikan Sabuko", "Grilled Spanish mackerel with lime, rice and salad", "8.50", 20, "🐟",
         "Ikan Sabuko Tunu", "Ikan Tenggiri Bakar", [("Extra rice", "0.75")], True),
        ("Batar Da'an", "Traditional corn, pumpkin and mung-bean stew", "4.00", 12, "🥘",
         "Batar Da'an", "Sup Jagung Labu", [], True),
    ]),
    ("Rice", "Etu", "Nasi", "#16a34a", [
        ("Chicken Fried Rice", "Fried rice with chicken, vegetables and egg", "5.00", 12, "🍚",
         "Etu Sona ho Manu", "Nasi Goreng Ayam", [("Fried Egg", "0.50"), ("Extra chicken", "1.00"), ("No spicy", "0")], True),
        ("Seafood Fried Rice", "Fried rice with prawns, squid and vegetables", "6.50", 14, "🦐",
         "Etu Sona ho Ikan-Tasi", "Nasi Goreng Seafood", [("Fried Egg", "0.50")], True),
        ("Nasi Kuning", "Turmeric rice with shredded chicken and egg", "4.50", 10, "🍱",
         "Etu Kinur", "Nasi Kuning", [("Extra sambal", "0.25")], True),
    ]),
    ("Noodles", "Mi", "Mi", "#ea580c", [
        ("Mie Goreng", "Stir-fried egg noodles with vegetables and chicken", "4.50", 10, "🍜",
         "Mi Sona", "Mie Goreng", [("Fried Egg", "0.50"), ("Extra chicken", "1.00")], True),
        ("Chicken Noodle Soup", "Noodles in chicken broth with greens and fried shallots", "4.50", 10, "🍲",
         "Supa Mi ho Manu", "Mie Kuah Ayam", [("Extra noodles", "0.75")], True),
        ("Bakso", "Beef meatball soup with noodles", "5.00", 10, "🥣",
         "Bakso", "Bakso Sapi", [("Extra meatballs", "1.25")], True),
    ]),
    ("Seafood", "Ikan-Tasi", "Makanan Laut", "#0284c7", [
        ("Grilled Prawns", "Six large prawns with garlic butter and rice", "11.00", 18, "🍤",
         "Boek Tunu", "Udang Bakar", [("Extra rice", "0.75")], True),
        ("Calamari Rings", "Crispy fried squid with tartare sauce", "6.00", 12, "🦑",
         "Lula Sona", "Cumi Goreng Tepung", [], True),
        ("Whole Snapper", "Whole grilled red snapper, serves two", "15.00", 25, "🐠",
         "Ikan Mean Tunu", "Kakap Merah Bakar", [("Sambal matah", "0.50")], False),  # sold out sample
    ]),
    ("Chicken", "Manu", "Ayam", "#ca8a04", [
        ("Ayam Bakar", "Grilled marinated chicken leg with rice and sambal", "5.50", 18, "🍗",
         "Manu Tunu", "Ayam Bakar", [("Extra sambal", "0.25")], True),
        ("Ayam Geprek", "Crispy smashed chicken with hot chilli sambal", "5.00", 15, "🌶️",
         "Manu Geprek", "Ayam Geprek", [("Cheese topping", "0.75"), ("Less spicy", "0")], True),
        ("Chicken Satay", "Eight skewers with peanut sauce and rice cakes", "5.50", 15, "🍢",
         "Sate Manu", "Sate Ayam", [], True),
    ]),
    ("Beef", "Karau-Na'an", "Daging Sapi", "#7c2d12", [
        ("Beef Rendang", "Slow-cooked beef in coconut and spices, with rice", "7.50", 10, "🍖",
         "Rendang Karau", "Rendang Sapi", [("Extra rice", "0.75")], True),
        ("Beef Burger", "Beef patty, cheese, salad and fries", "7.00", 15, "🍔",
         "Burger Karau", "Burger Sapi", [("Extra patty", "2.00"), ("Bacon", "1.00")], True),
    ]),
    ("Snacks", "Lanche", "Camilan", "#9333ea", [
        ("French Fries", "Crispy fries with ketchup", "2.50", 8, "🍟",
         "Batata Frita", "Kentang Goreng", [("Cheese sauce", "0.75")], True),
        ("Spring Rolls", "Four vegetable spring rolls with sweet chilli", "2.50", 8, "🥟",
         "Rolu Primavera", "Lumpia Sayur", [], True),
        ("Fried Tempeh", "Crispy tempeh with sambal", "2.00", 6, "🟫",
         "Tempe Sona", "Tempe Goreng", [], True),
    ]),
    ("Drinks", "Hemu", "Minuman", "#0891b2", [
        ("Timor Coffee", "Single-origin Ermera arabica", "1.50", 3, "☕",
         "Kafé Timor", "Kopi Timor", [("Milk", "0.25"), ("Extra shot", "0.50")], True),
        ("Iced Tea", "Sweet iced black tea", "1.50", 2, "🧋",
         "Xá Malirin", "Es Teh Manis", [("No sugar", "0")], True),
        ("Fresh Coconut", "Whole young coconut", "2.00", 2, "🥥",
         "Nuu Foun", "Kelapa Muda", [], True),
        ("Mango Juice", "Fresh mango blended with ice", "2.50", 4, "🥭",
         "Sumu Haas", "Jus Mangga", [("No sugar", "0")], True),
        ("Mineral Water", "600 ml bottle", "0.75", 1, "💧",
         "Bee Mineral", "Air Mineral", [], True),
        ("Soft Drink", "Coke, Sprite or Fanta (can)", "1.25", 1, "🥤",
         "Refrigerante", "Minuman Bersoda", [], True),
    ]),
    ("Desserts", "Doce", "Pencuci Mulut", "#db2777", [
        ("Pisang Goreng", "Banana fritters with palm sugar", "2.00", 8, "🍌",
         "Hudi Sona", "Pisang Goreng", [("Ice cream", "1.00"), ("Chocolate", "0.50")], True),
        ("Coconut Ice Cream", "Two scoops of homemade coconut ice cream", "2.50", 2, "🍨",
         "Ais-krim Nuu", "Es Krim Kelapa", [], True),
        ("Fruit Plate", "Seasonal tropical fruit", "3.00", 5, "🍉",
         "Ai-fuan Kahur", "Piring Buah", [], True),
    ]),
]

EMOJI_FONT = Path(r"C:\Windows\Fonts\seguiemj.ttf")
for candidate in ("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", "/System/Library/Fonts/Apple Color Emoji.ttc"):
    if not EMOJI_FONT.exists() and Path(candidate).exists():
        EMOJI_FONT = Path(candidate)


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def food_image(emoji: str, colour: str):
    """A 900×600 gradient card with a big emoji, as an uploaded file (or None if no emoji font)."""
    from PIL import Image, ImageDraw, ImageFont

    if not EMOJI_FONT.exists():
        return None
    w, h = 900, 600
    base = _hex(colour)
    light = tuple(min(255, int(v + (255 - v) * 0.75)) for v in base)
    img = Image.new("RGB", (w, h), light)
    draw = ImageDraw.Draw(img)
    for y in range(h):  # soft vertical gradient
        t = y / h
        draw.line([(0, y), (w, y)], fill=tuple(int(light[i] * (1 - t * 0.35) + base[i] * t * 0.35) for i in range(3)))
    rnd = random.Random(emoji)
    for _ in range(14):  # decorative circles
        r = rnd.randint(20, 90)
        x, y = rnd.randint(0, w), rnd.randint(0, h)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=tuple(min(255, v + 18) for v in light))
    try:
        font = ImageFont.truetype(str(EMOJI_FONT), 109 if "Noto" in EMOJI_FONT.name else 300)
        draw.text((w / 2, h / 2), emoji, font=font, anchor="mm", embedded_color=True)
    except OSError:
        return None
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return SimpleUploadedFile("food.png", buf.getvalue(), content_type="image/png")


class Command(BaseCommand):
    help = "Create a sample restaurant with a full menu, images, translations and tables."

    def add_arguments(self, parser):
        parser.add_argument("--owner", required=True, help="Email or username of an existing user to own it")
        parser.add_argument("--name", default="Sabor Timor Café")
        parser.add_argument("--slug", default="sabor-timor")
        parser.add_argument("--tables", type=int, default=10)
        parser.add_argument("--no-images", action="store_true")

    @transaction.atomic
    def handle(self, *args, owner, name, slug, tables, no_images, **options):
        User = get_user_model()
        user = User.objects.filter(email__iexact=owner).first() or User.objects.filter(username=owner).first()
        if user is None:
            raise CommandError(f"No user '{owner}'.")
        if Restaurant.objects.filter(slug=slug).exists():
            raise CommandError(f"A restaurant with slug '{slug}' already exists.")

        r = Restaurant.objects.create(
            name=name, slug=slug, is_active=True,
            description="Timorese and Indonesian home cooking, fresh seafood and Timor coffee.",
            translations={
                "tet": {"description": "Hahán uma Timor no Indonézia, ikan-tasi foun no kafé Timor."},
                "id": {"description": "Masakan rumahan Timor dan Indonesia, hidangan laut segar, dan kopi Timor."},
            },
            address="Avenida de Portugal, Dili, Timor-Leste",
            phone="+670 7700 1234",
            opening_hours="Mon–Sat 07:00–22:00\nSunday 08:00–21:00",
            service_charge_percent=Decimal("0"),
            default_prep_minutes=15,
        )
        RestaurantStaff.objects.create(restaurant=r, user=user, role=Role.OWNER)

        images = 0
        for cpos, (cat, cat_tet, cat_id, colour, items) in enumerate(MENU):
            category = MenuCategory.objects.create(
                restaurant=r, name=cat, position=cpos, translations={"tet": {"name": cat_tet}, "id": {"name": cat_id}},
            )
            for ipos, (iname, desc, price, prep, emoji, tet, ind, options, available) in enumerate(items):
                item = MenuItem(
                    restaurant=r, category=category, name=iname, description=desc, price=Decimal(price),
                    prep_minutes=prep, position=ipos, is_available=available,
                    translations={"tet": {"name": tet}, "id": {"name": ind}},
                )
                upload = None if no_images else food_image(emoji, colour)
                if upload:
                    item.image = process_image(upload, "item")
                    images += 1
                item.save()
                for opos, (oname, oprice) in enumerate(options):
                    MenuItemOption.objects.create(menu_item=item, name=oname, price=Decimal(oprice), position=opos)

        Table.objects.bulk_create([Table(restaurant=r, number=str(n)) for n in range(1, tables + 1)])
        t1 = Table.objects.get(restaurant=r, number="1")

        n_items = MenuItem.objects.filter(restaurant=r).count()
        self.stdout.write(self.style.SUCCESS(
            f"Created '{name}' (/r/{slug}/): {len(MENU)} categories, {n_items} dishes, {images} images, {tables} tables."
        ))
        self.stdout.write(f"  Owner: {user.email or user.username}")
        self.stdout.write(f"  Table 1 QR link: {t1.active_qr().get_path()}")
