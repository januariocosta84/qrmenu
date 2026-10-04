import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from apps.core.permissions import Role
from apps.restaurants.models import Restaurant, RestaurantStaff, Table


class Command(BaseCommand):
    help = "Onboard a restaurant: create it, its owner account and (optionally) numbered tables."

    def add_arguments(self, parser):
        parser.add_argument("name")
        parser.add_argument("--slug", help="URL name (default: from the restaurant name)")
        parser.add_argument("--owner", required=True, help="Owner username (created if it doesn't exist)")
        parser.add_argument("--email", default="")
        parser.add_argument("--tables", type=int, default=0, help="Create tables 1..N")

    @transaction.atomic
    def handle(self, *args, name, slug, owner, email, tables, **options):
        slug = slugify(slug or name)
        if not slug:
            raise CommandError("Could not derive a slug; pass --slug.")
        if Restaurant.objects.filter(slug=slug).exists():
            raise CommandError(f"A restaurant with slug '{slug}' already exists.")

        User = get_user_model()
        user = User.objects.filter(username=owner).first()
        if user is None:
            password = getpass.getpass(f"Password for new owner '{owner}': ")
            user = User(username=owner, email=email)
            try:
                validate_password(password, user)
            except ValidationError as exc:
                raise CommandError(" ".join(exc.messages))
            user.set_password(password)
            user.save()

        restaurant = Restaurant.objects.create(name=name, slug=slug, email=email)
        RestaurantStaff.objects.create(restaurant=restaurant, user=user, role=Role.OWNER)
        Table.objects.bulk_create([Table(restaurant=restaurant, number=str(n)) for n in range(1, tables + 1)])
        self.stdout.write(self.style.SUCCESS(f"Created '{name}' → /r/{slug}/  (dashboard: /dashboard/{slug}/)"))
