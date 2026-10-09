from decimal import Decimal

from django.db import migrations


def forwards(apps, schema_editor):
    # Plans with Branches include the main branch + 2 sub-branches; each extra branch is a monthly add-on.
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.filter(feature_branches=True).update(included_branches=2, extra_branch_price=Decimal("5.00"))


class Migration(migrations.Migration):
    dependencies = [("billing", "0006_billingsettings_branch_flag_limit_and_more")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
