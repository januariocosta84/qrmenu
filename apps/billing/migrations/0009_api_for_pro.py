from django.db import migrations


def forwards(apps, schema_editor):
    # The Ordering API comes with the plans that have Branches (Pro).
    apps.get_model("billing", "Plan").objects.filter(feature_branches=True).update(feature_api=True)


class Migration(migrations.Migration):
    dependencies = [("billing", "0008_plan_feature_api")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
