"""Plans, subscription periods, usage and limit checks.

Payments: ``BILLING_PROVIDER=dummy`` (default) activates plan changes immediately and is meant
for development/self-hosting. A real payment gateway (e.g. Stripe Checkout + webhooks) plugs
in by implementing ``PaymentProvider``; it is NOT implemented here.
"""
from datetime import timedelta

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from .models import Plan, Subscription

GB = 10 ** 9


class BillingError(Exception):
    pass


def default_plan() -> Plan:
    return Plan.objects.filter(code="free").first() or Plan.objects.order_by("sort").first()


def get_subscription(user) -> Subscription:
    sub = Subscription.objects.filter(user=user).select_related("plan").first()
    if sub is None:
        now = timezone.now()
        sub = Subscription.objects.create(user=user, plan=default_plan(), current_period_start=now,
                                          current_period_end=now + timedelta(days=30))
    roll_period(sub)
    return sub


def roll_period(sub: Subscription) -> None:
    """Advance the billing period; apply scheduled cancellations (fall back to Free)."""
    now = timezone.now()
    changed = False
    while sub.current_period_end <= now:
        sub.current_period_start = sub.current_period_end
        sub.current_period_end = sub.current_period_start + timedelta(days=30)
        if sub.cancel_at_period_end:
            sub.plan, sub.cancel_at_period_end, sub.status = default_plan(), False, Subscription.ACTIVE
        changed = True
    if changed:
        sub.save()


def get_plan(user) -> Plan:
    sub = get_subscription(user)
    return sub.plan if sub.status != Subscription.PAST_DUE else default_plan()


def usage_bytes(user, sub: Subscription | None = None) -> int:
    from transfers.models import TransferRecord
    sub = sub or get_subscription(user)
    agg = TransferRecord.objects.filter(initiator=user, status=TransferRecord.COMPLETED,
                                        completed_at__gte=sub.current_period_start).aggregate(n=Sum("file_size"))
    return int(agg["n"] or 0)


def usage_summary(user) -> dict:
    sub = get_subscription(user)
    used = usage_bytes(user, sub)
    quota = sub.plan.monthly_quota
    return {"used_bytes": used, "quota_bytes": quota,
            "remaining_bytes": None if quota is None else max(0, quota - used),
            "period_end": sub.current_period_end}


def check_transfer(user, file_size: int) -> tuple[bool, str]:
    plan = get_plan(user)
    if plan.max_file_size is not None and file_size > plan.max_file_size:
        return False, (f"{plan.name} plan allows files up to {plan.max_file_size / GB:.0f} GB - "
                       f"upgrade your subscription")
    if plan.monthly_quota is not None:
        used = usage_bytes(user)
        if used + file_size > plan.monthly_quota:
            return False, (f"monthly transfer quota of {plan.monthly_quota / GB:.0f} GB would be exceeded "
                           f"({used / GB:.1f} GB used) - upgrade your subscription")
    return True, "ok"


# ------------------------------------------------------------------ payments
class PaymentProvider:
    name = "base"

    def change_plan(self, sub: Subscription, plan: Plan) -> dict:
        raise NotImplementedError


class DummyProvider(PaymentProvider):
    """Development provider: plan changes take effect immediately, nothing is charged."""
    name = "dummy"

    def change_plan(self, sub: Subscription, plan: Plan) -> dict:
        now = timezone.now()
        sub.plan, sub.status, sub.cancel_at_period_end, sub.provider = plan, Subscription.ACTIVE, False, self.name
        sub.current_period_start, sub.current_period_end = now, now + timedelta(days=30)
        sub.save()
        return {"status": "active"}


def provider() -> PaymentProvider:
    if settings.BILLING_PROVIDER == "dummy":
        return DummyProvider()
    raise BillingError(f"billing provider {settings.BILLING_PROVIDER!r} is not configured")


def change_plan(user, code: str) -> dict:
    plan = Plan.objects.filter(code=code, public=True).first()
    if plan is None:
        raise BillingError("unknown plan")
    sub = get_subscription(user)
    if sub.plan_id == plan.id and not sub.cancel_at_period_end:
        return {"status": "unchanged"}
    return provider().change_plan(sub, plan)


def cancel(user) -> None:
    sub = get_subscription(user)
    if sub.plan.code != "free":
        sub.cancel_at_period_end = True
        sub.save()
