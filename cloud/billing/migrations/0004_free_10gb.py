"""Free plan: 10 GB of transfers per month (files up to 10 GB). Editable later in the staff console."""
from django.db import migrations

GB = 10 ** 9


def free_10gb(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.filter(code="free").update(monthly_quota=10 * GB, max_file_size=10 * GB,
                                            description="Try it out: 10 GB of transfers per month.")


class Migration(migrations.Migration):
    dependencies = [("billing", "0003_payments")]
    operations = [migrations.RunPython(free_10gb, migrations.RunPython.noop)]
