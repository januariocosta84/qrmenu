"""
Dummy data for testing: three restaurants in different subscription states,
a login for every role, full menus, tables, 30 days of order history (with
cash payments, change, cancellations, waiter orders, several guests per
table), live orders in the kitchen right now, and invoices.

    python manage.py seed_testdata                 # create (or top up) the test data
    python manage.py seed_testdata --remove        # delete ALL test data again
    python manage.py seed_testdata --password X    # choose the shared test password

Everything it creates is marked: restaurant slugs start with "test-" and
usernames with "test-", so --remove deletes exactly that and nothing else.
On a production server (DJANGO_DEBUG=false) it asks for --yes-production.
"""
import random
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.billing.models import Invoice, Plan, Subscription
from apps.billing.services import create_invoice, mark_invoice_paid, start_subscription
from apps.core.images import process_image
from apps.core.permissions import Role
from apps.menu.models import MenuCategory, MenuItem, MenuItemOption
from apps.orders.models import Notification, Order, OrderStatus, OrderStatusHistory, PaymentStatus
from apps.orders.services import place_order
from apps.payments.models import Payment
from apps.restaurants.models import Restaurant, RestaurantStaff, Table, TableSession

from .seed_sample import MENU, food_image

PREFIX = "test-"
DEFAULT_PASSWORD = "Test-pass-2026"

RESTAURANTS = [
    # slug, name, plan, billing state, tables, service charge, description
    ("test-dili-bay", "Dili Bay Grill (TEST)", "Standard", "active", 12, "5",
     "Seafood and grill on the Dili waterfront. Test data."),
    ("test-kafe-atauro", "Kafe Atauro (TEST)", "Pro", "trial", 8, "0",
     "Island coffee and breakfast. Test data."),
    ("test-warung-lospalos", "Warung Lospalos (TEST)", "Standard", "expired", 5, "0",
     "Small family warung. Test data, subscription expired on purpose."),
]
ROLES = [(Role.OWNER, "owner"), (Role.MANAGER, "manager"), (Role.KITCHEN, "kitchen"), (Role.WAITER, "waiter")]
FIRST_NAMES = ["Ana", "Joao", "Maria", "Jose", "Rosa", "Agus", "Lucia", "Paulo", "Ines", "Tomas", "Sara", "Budi"]
NOTES = ["", "", "", "No spicy", "Less sugar", "No onions", "Extra hot", "Takeaway box please"]


class Command(BaseCommand):
    help = "Create (or --remove) dummy test data: restaurants, staff logins, menus, orders, payments, invoices."

    def add_arguments(self, parser):
        parser.add_argument("--remove", action="store_true", help="Delete all test data (test-* restaurants and users).")
        parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Password for every test login.")
        parser.add_argument("--days", type=int, default=30, help="Days of order history (default 30).")
        parser.add_argument("--no-images", action="store_true", help="Skip generated food pictures (faster).")
        parser.add_argument("--yes-production", action="store_true", help="Allow running with DEBUG off.")

    def handle(self, *args, remove, password, days, no_images, yes_production, **options):
        if not settings.DEBUG and not yes_production:
            raise CommandError("DEBUG is off (production?). Re-run with --yes-production if you really want test data here.")
        if remove:
            return self.remove()
        random.seed(2026)
        self.password = password
        self.no_images = no_images
        self.days = max(1, min(days, 120))
        admin = get_user_model().objects.filter(is_superuser=True).order_by("pk").first()
        created = []
        for spec in RESTAURANTS:
            if Restaurant.objects.filter(slug=spec[0]).exists():
                self.stdout.write(f"  {spec[0]} already exists, skipped (use --remove to start over)")
                continue
            with transaction.atomic():
                created.append(self.build(spec, admin))
        self.summary(created)

    # ------------------------------------------------------------------ build

    def build(self, spec, admin):
        slug, name, plan_name, billing_state, n_tables, service, desc = spec
        r = Restaurant.objects.create(
            name=name, slug=slug, description=desc, is_active=True, address="Dili, Timor-Leste",
            phone="+670 7700 0000", opening_hours="Every day 07:00–22:00", email=f"{slug}@example.test",
            service_charge_percent=Decimal(service), default_prep_minutes=15,
        )
        users = {}
        for role, label in ROLES:
            username = f"{slug}-{label}"
            user = get_user_model().objects.create_user(
                username=username, email=f"{username}@example.test", password=self.password,
                first_name=label.capitalize(), last_name=name.split(" (")[0], email_verified=True,
            )
            RestaurantStaff.objects.create(restaurant=r, user=user, role=role)
            users[role] = user

        items = self.menu(r)
        tables = [Table.objects.create(restaurant=r, number=str(n), seats=random.choice([2, 4, 4, 6]))
                  for n in range(1, n_tables + 1)]
        # Orders first: an expired subscription (set below) blocks new orders.
        n_hist = self.history(r, items, tables, users, light=billing_state == "expired")
        n_live = self.live_orders(r, items, tables, users) if billing_state != "expired" else 0
        self.subscription(r, plan_name, billing_state, admin)
        Notification.objects.filter(restaurant=r, created_at__lt=timezone.now() - timedelta(hours=2)).update(is_read=True)
        return {"r": r, "users": users, "hist": n_hist, "live": n_live, "state": billing_state, "plan": plan_name}

    def menu(self, r):
        items = []
        for cpos, (cat, cat_tet, cat_id, colour, dishes) in enumerate(MENU):
            category = MenuCategory.objects.create(restaurant=r, name=cat, position=cpos,
                                                   translations={"tet": {"name": cat_tet}, "id": {"name": cat_id}})
            for ipos, (iname, desc, price, prep, emoji, tet, ind, opts, available) in enumerate(dishes):
                item = MenuItem(restaurant=r, category=category, name=iname, description=desc, price=Decimal(price),
                                prep_minutes=prep, position=ipos, is_available=available,
                                translations={"tet": {"name": tet}, "id": {"name": ind}})
                upload = None if self.no_images else food_image(emoji, colour)
                if upload:
                    item.image = process_image(upload, "item")
                item.save()
                for opos, (oname, oprice) in enumerate(opts):
                    MenuItemOption.objects.create(menu_item=item, name=oname, price=Decimal(oprice), position=opos)
                items.append(item)
        return [i for i in items if i.is_available]

    def subscription(self, r, plan_name, state, admin):
        plan = Plan.objects.filter(name=plan_name).first() or Plan.objects.filter(is_active=True).first()
        sub = start_subscription(r, plan)
        if sub is None:
            return
        sub.plan = plan  # placing orders may already have created a default subscription
        sub.save(update_fields=["plan", "updated_at"])
        today = timezone.localdate()
        if state == "active":
            sub.trial_ends_on = today - timedelta(days=62)
            sub.save()
            for _ in range(2):  # two paid months
                inv = create_invoice(sub)
                mark_invoice_paid(inv, user=admin, method=random.choice(["bank_transfer", "cash"]),
                                  reference=f"TEST-{random.randint(1000, 9999)}")
                sub.refresh_from_db()
            create_invoice(sub)  # next month: open
        elif state == "trial":
            sub.trial_ends_on = today + timedelta(days=5)  # shows the "trial ends soon" banner
            sub.save()
            create_invoice(sub)
        elif state == "expired":
            sub.trial_ends_on = today - timedelta(days=20)
            sub.save()
            inv = create_invoice(sub)
            Invoice.objects.filter(pk=inv.pk).update(due_date=today - timedelta(days=19))  # overdue

    def _lines(self, items, max_lines=4):
        lines = []
        for item in random.sample(items, k=random.randint(1, min(max_lines, len(items)))):
            opts = list(item.options.filter(is_available=True))
            chosen = [o.id for o in random.sample(opts, k=random.randint(0, min(2, len(opts))))] if opts else []
            lines.append({"menu_item": item.id, "quantity": random.choice([1, 1, 1, 2, 2, 3]),
                          "options": chosen, "note": random.choice(NOTES)})
        return lines

    def _pay(self, order, user, when, method="cash"):
        tendered = order.total
        if method == "cash":
            for note in (Decimal("5"), Decimal("10"), Decimal("20"), Decimal("50")):
                if note >= order.total:
                    tendered = random.choice([order.total, note])
                    break
        Payment.objects.create(
            restaurant=order.restaurant, order=order, method=method, status=Payment.SUCCEEDED, amount=order.total,
            currency=order.currency, cash_tendered=tendered if method == "cash" else None,
            change_given=(tendered - order.total) if method == "cash" else Decimal("0"),
            received_by=user, paid_at=when,
        )
        Order.objects.filter(pk=order.pk).update(payment_status=PaymentStatus.PAID)

    def history(self, r, items, tables, users, light=False):
        """Past visits: each table session has 1–3 guests, each with 1–2 orders; mostly completed and paid."""
        now = timezone.now()
        count = 0
        for day in range(self.days, 0, -1):
            visits = random.randint(1, 3) if light else random.randint(4, 12)
            for _ in range(visits):
                start = (now - timedelta(days=day)).replace(hour=random.randint(7, 20), minute=random.randint(0, 59))
                table = random.choice(tables)
                for g in range(random.randint(1, 3)):
                    guest = f"test{random.getrandbits(40):010x}"
                    name = random.choice(FIRST_NAMES) if random.random() < .4 else ""
                    by_waiter = random.random() < .2
                    for k in range(random.choice([1, 1, 2])):
                        created = start + timedelta(minutes=10 * g + 25 * k)
                        order = place_order(restaurant=r, table=table, lines=self._lines(items), customer_ref=guest,
                                            customer_name=name, placed_by=users[Role.WAITER] if by_waiter else None)
                        self._age(order, created, cancelled=random.random() < .05, users=users)
                        count += 1
                session = table.current_session()
                if session:
                    session.close(user=users[Role.WAITER])
                    TableSession.objects.filter(pk=session.pk).update(opened_at=start, closed_at=start + timedelta(hours=1))
        return count

    def _age(self, order, created, *, cancelled, users):
        """Move a freshly placed order into the past and through the workflow."""
        prep = timedelta(minutes=order.estimated_minutes or 12)
        if cancelled:
            fields = dict(status=OrderStatus.CANCELLED, cancelled_at=created + timedelta(minutes=3),
                          cancel_reason="Customer changed their mind")
            steps = [(OrderStatus.CANCELLED, created + timedelta(minutes=3))]
        else:
            fields = dict(status=OrderStatus.COMPLETED, accepted_at=created + timedelta(minutes=1),
                          preparing_at=created + timedelta(minutes=1), ready_at=created + prep,
                          completed_at=created + prep + timedelta(minutes=5))
            steps = [(OrderStatus.PREPARING, fields["preparing_at"]), (OrderStatus.READY, fields["ready_at"]),
                     (OrderStatus.COMPLETED, fields["completed_at"])]
        Order.objects.filter(pk=order.pk).update(created_at=created, updated_at=created, **fields)
        OrderStatusHistory.objects.filter(order=order).update(created_at=created)
        prev = OrderStatus.NEW
        for status, when in steps:
            h = OrderStatusHistory.objects.create(order=order, from_status=prev, to_status=status,
                                                  changed_by=users[Role.KITCHEN])
            OrderStatusHistory.objects.filter(pk=h.pk).update(created_at=when)
            prev = status
        Notification.objects.filter(order=order).update(created_at=created, is_read=True)
        if not cancelled:
            order.refresh_from_db()
            self._pay(order, users[Role.WAITER], fields["completed_at"],
                      method=random.choice(["cash", "cash", "cash", "bank_transfer"]))

    def live_orders(self, r, items, tables, users):
        """Orders happening right now: new, preparing and ready ones on the kitchen screen; open table bills."""
        now = timezone.now()
        plan = [(OrderStatus.NEW, 2), (OrderStatus.NEW, 6), (OrderStatus.PREPARING, 9), (OrderStatus.PREPARING, 14),
                (OrderStatus.READY, 20), (OrderStatus.COMPLETED, 35)]
        for i, (status, minutes_ago) in enumerate(plan):
            table = tables[i % min(3, len(tables))]
            guest = f"test{random.getrandbits(40):010x}"
            order = place_order(restaurant=r, table=table, lines=self._lines(items, 3), customer_ref=guest,
                                customer_name=random.choice(FIRST_NAMES) if i % 2 else "",
                                placed_by=users[Role.WAITER] if i == 3 else None)
            created = now - timedelta(minutes=minutes_ago)
            fields = {"created_at": created, "status": status}
            if status in (OrderStatus.PREPARING, OrderStatus.READY, OrderStatus.COMPLETED):
                fields.update(accepted_at=created + timedelta(minutes=1), preparing_at=created + timedelta(minutes=1))
            if status in (OrderStatus.READY, OrderStatus.COMPLETED):
                fields["ready_at"] = created + timedelta(minutes=12)
            if status == OrderStatus.COMPLETED:
                fields["completed_at"] = created + timedelta(minutes=18)
            Order.objects.filter(pk=order.pk).update(**fields)
            if i == 4:  # one guest already paid
                order.refresh_from_db()
                self._pay(order, users[Role.WAITER], now - timedelta(minutes=5))
        return len(plan)

    # ------------------------------------------------------------------ remove / summary

    def remove(self):
        restaurants = Restaurant.objects.filter(slug__startswith=PREFIX)
        names = list(restaurants.values_list("name", flat=True))
        users = get_user_model().objects.filter(username__startswith=PREFIX, is_superuser=False)
        n_users = users.count()
        with transaction.atomic():
            for r in restaurants:
                Subscription.objects.filter(restaurant=r).delete()
                r.delete()  # cascades: menu, tables, orders, payments, invoices…
            users.delete()
        self.stdout.write(self.style.SUCCESS(f"Removed {len(names)} test restaurant(s) and {n_users} test user(s)."))
        for n in names:
            self.stdout.write(f"  - {n}")

    def summary(self, created):
        if not created:
            self.stdout.write("Nothing new created.")
            return
        self.stdout.write(self.style.SUCCESS("\nTest data created. Every login uses password: " + self.password))
        for c in created:
            r = c["r"]
            t1 = Table.objects.get(restaurant=r, number="1")
            self.stdout.write(f"\n{r.name}   plan {c['plan']} · subscription {c['state']}")
            self.stdout.write(f"  Menu:       /r/{r.slug}/")
            self.stdout.write(f"  Table 1 QR: {t1.active_qr().get_path()}")
            self.stdout.write(f"  Orders:     {c['hist']} in history, {c['live']} live now")
            for role, label in ROLES:
                self.stdout.write(f"  {label:<8}  {c['users'][role].username}")
        self.stdout.write("\nRemove it all later with:  python manage.py seed_testdata --remove")
