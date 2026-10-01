from decimal import Decimal

from django.db import migrations

GB = 10 ** 9
PLANS = [
    dict(code="free", name="Free", price_month=Decimal("0"), max_file_size=20 * GB, monthly_quota=200 * GB,
         max_users=5, max_folders=3, sort=0, description="For trying it out with a few users."),
    dict(code="pro", name="Pro", price_month=Decimal("19.00"), max_file_size=None, monthly_quota=5000 * GB,
         max_users=100, max_folders=50, sort=1, description="Studios and teams: unlimited file size."),
    dict(code="business", name="Business", price_month=Decimal("49.00"), max_file_size=None, monthly_quota=None,
         max_users=None, max_folders=None, sort=2, description="Unlimited users, folders and volume."),
]


def create(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    for p in PLANS:
        Plan.objects.update_or_create(code=p["code"], defaults=p)


class Migration(migrations.Migration):
    dependencies = [("billing", "0001_initial")]
    operations = [migrations.RunPython(create, migrations.RunPython.noop)]
