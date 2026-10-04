from django.core.management.base import BaseCommand

from apps.billing.services import generate_due_invoices


class Command(BaseCommand):
    help = "Create renewal invoices for subscriptions that end soon. Run daily (cron / systemd timer)."

    def handle(self, *args, **options):
        created = generate_due_invoices()
        for inv in created:
            self.stdout.write(f"{inv.number}  {inv.restaurant.name}  {inv.amount} {inv.currency}  due {inv.due_date}")
        self.stdout.write(self.style.SUCCESS(f"{len(created)} invoice(s) created."))
