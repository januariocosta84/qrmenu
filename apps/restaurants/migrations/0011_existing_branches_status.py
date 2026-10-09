from django.db import migrations


def forwards(apps, schema_editor):
    # Branches created before platform review existed: live ones count as approved, hidden ones wait for review.
    Restaurant = apps.get_model("restaurants", "Restaurant")
    Restaurant.objects.filter(parent__isnull=False, is_active=True).update(branch_status="active")
    Restaurant.objects.filter(parent__isnull=False, is_active=False).update(branch_status="pending")


class Migration(migrations.Migration):
    dependencies = [("restaurants", "0010_restaurant_branch_flags_restaurant_branch_owner_name_and_more")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
