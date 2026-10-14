from django.db import migrations


def forwards(apps, schema_editor):
    # Seeded test restaurants (web address test-…) stay out of Google; owners can switch it on in settings.
    apps.get_model("restaurants", "Restaurant").objects.filter(slug__startswith="test-").update(show_in_search=False)


class Migration(migrations.Migration):
    dependencies = [("restaurants", "0013_show_in_search")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
