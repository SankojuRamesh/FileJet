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


class BillingSettings(models.Model):
    """One row (pk=1), edited by the platform admin in the staff console: how customers pay."""
    TEST, RAZORPAY = "dummy", "razorpay"
    PROVIDERS = [(TEST, "Test mode – no payment, plans switch instantly"), (RAZORPAY, "Razorpay")]
    provider = models.CharField(max_length=20, choices=PROVIDERS, default=TEST)
    razorpay_key_id = models.CharField(max_length=80, blank=True)
    razorpay_key_secret = models.CharField(max_length=120, blank=True)
    razorpay_webhook_secret = models.CharField(max_length=120, blank=True)
    business_name = models.CharField(max_length=80, default="FileJet")       # shown in the payment window
    period_days = models.PositiveSmallIntegerField(default=30)              # one paid period
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "billing settings"

    @classmethod
    def get(cls) -> "BillingSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def razorpay_ready(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)


class Payment(models.Model):
    """One payment for one period of a plan (Razorpay order -> payment)."""
    CREATED, PAID, FAILED = "created", "paid", "failed"
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payments")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    provider = models.CharField(max_length=20)
    order_id = models.CharField(max_length=80, unique=True)
    payment_id = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=10, default=CREATED,
                              choices=[(CREATED, "Created"), (PAID, "Paid"), (FAILED, "Failed")])
    period_end = models.DateTimeField(null=True, blank=True)                # paid until
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} {self.plan} {self.amount} {self.currency} {self.status}"
