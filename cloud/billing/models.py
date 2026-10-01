from django.conf import settings
from django.db import models


class Plan(models.Model):
    """Subscription plan. ``None`` limits mean unlimited."""
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=40)
    price_month = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="USD")
    max_file_size = models.BigIntegerField(null=True, blank=True)
    monthly_quota = models.BigIntegerField(null=True, blank=True)
    max_users = models.PositiveIntegerField(null=True, blank=True)      # users an admin can add
    max_folders = models.PositiveIntegerField(null=True, blank=True)
    description = models.CharField(max_length=200, blank=True)
    sort = models.PositiveIntegerField(default=0)
    public = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort"]

    def __str__(self):
        return self.name


class Subscription(models.Model):
    ACTIVE, CANCELED, PAST_DUE = "active", "canceled", "past_due"
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscription")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    status = models.CharField(max_length=10, default=ACTIVE,
                              choices=[(ACTIVE, "Active"), (CANCELED, "Canceled"), (PAST_DUE, "Past due")])
    current_period_start = models.DateTimeField()
    current_period_end = models.DateTimeField()
    cancel_at_period_end = models.BooleanField(default=False)
    provider = models.CharField(max_length=20, default="none")
    provider_ref = models.CharField(max_length=120, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user} - {self.plan}"
