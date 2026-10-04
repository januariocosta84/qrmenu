"""Starter plans and billing settings. Prices and limits can be changed in the platform console."""
from decimal import Decimal

from django.db import migrations

PLANS = [
    # name, description, price, tables, dishes, staff, position
    ("Free", "For trying it out or very small stalls.", Decimal("0"), 5, 30, 3, 0),
    ("Standard", "Most cafés and restaurants.", Decimal("15.00"), 30, 200, 15, 1),
    ("Pro", "Large restaurants with many tables.", Decimal("35.00"), 300, 500, 50, 2),
]


def create(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    BillingSettings = apps.get_model("billing", "BillingSettings")
    created = {}
    for name, desc, price, tables, dishes, staff, pos in PLANS:
        created[name], _ = Plan.objects.get_or_create(name=name, defaults=dict(
            description=desc, price_monthly=price, max_tables=tables, max_menu_items=dishes, max_staff=staff, position=pos,
        ))
    BillingSettings.objects.get_or_create(pk=1, defaults={"default_plan": created["Standard"], "trial_days": 30})


class Migration(migrations.Migration):
    dependencies = [("billing", "0001_initial")]
    operations = [migrations.RunPython(create, migrations.RunPython.noop)]
