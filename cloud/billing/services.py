"""Plans, subscription periods, usage and limit checks.

Payments are set up by the platform admin in the staff console (BillingSettings): test mode (plan changes
apply immediately, nothing is charged) or Razorpay checkout with signature verification and webhooks.
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
                                          current_period_end=now + timedelta(days=period_days()))
    roll_period(sub)
    return sub


def roll_period(sub: Subscription) -> None:
    """Advance the billing period; apply scheduled cancellations (fall back to Free)."""
    now = timezone.now()
    changed = False
    while sub.current_period_end <= now:
        sub.current_period_start = sub.current_period_end
        sub.current_period_end = sub.current_period_start + timedelta(days=period_days())
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
# Paid plans are prepaid for one period (BillingSettings.period_days). Paying again extends the period; without a
# new payment the plan falls back to Free at the end of the period. The platform admin chooses the provider in the
# staff console: test mode (no payment) or Razorpay (UPI, cards, net banking, wallets).
class PaymentRequired(BillingError):
    """A paid plan was chosen: the customer has to pay at ``checkout_url`` first."""

    def __init__(self, message: str, checkout_url: str):
        super().__init__(message)
        self.checkout_url = checkout_url


def period_days() -> int:
    from .models import BillingSettings
    return max(1, BillingSettings.get().period_days or 30)


def payments_enabled() -> bool:
    from .models import BillingSettings
    return BillingSettings.get().provider == BillingSettings.RAZORPAY


class PaymentProvider:
    name = "base"

    def change_plan(self, sub: Subscription, plan: Plan) -> dict:
        raise NotImplementedError


class DummyProvider(PaymentProvider):
    """Test mode: plan changes take effect immediately, nothing is charged."""
    name = "dummy"

    def change_plan(self, sub: Subscription, plan: Plan) -> dict:
        now = timezone.now()
        sub.plan, sub.status, sub.cancel_at_period_end, sub.provider = plan, Subscription.ACTIVE, False, self.name
        sub.current_period_start, sub.current_period_end = now, now + timedelta(days=period_days())
        sub.save()
        return {"status": "active"}


def provider() -> PaymentProvider:
    return DummyProvider()


def change_plan(user, code: str) -> dict:
    plan = Plan.objects.filter(code=code, public=True).first()
    if plan is None:
        raise BillingError("unknown plan")
    sub = get_subscription(user)
    if plan.price_month and payments_enabled():
        raise PaymentRequired(f"{plan.name} is a paid plan - pay to activate it",
                              f"{settings.CLOUD_PUBLIC_URL.rstrip('/')}/subscription/?plan={plan.code}")
    if not plan.price_month and payments_enabled() and sub.plan.price_month:
        cancel(user)                                  # paid until the end of the period, then Free
        return {"status": "ends_at_period_end"}
    if sub.plan_id == plan.id and not sub.cancel_at_period_end:
        return {"status": "unchanged"}
    return provider().change_plan(sub, plan)


# ---- Razorpay (orders API, checkout.js, signature verification, webhooks)
RAZORPAY_API = "https://api.razorpay.com/v1"


def _razorpay():
    from .models import BillingSettings
    cfg = BillingSettings.get()
    if cfg.provider != BillingSettings.RAZORPAY or not cfg.razorpay_ready:
        raise BillingError("online payments are not set up yet - please contact support")
    return cfg


def _razorpay_request(cfg, method: str, path: str, body: dict | None = None) -> dict:
    import base64
    import json
    import urllib.error
    import urllib.request
    req = urllib.request.Request(RAZORPAY_API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    token = base64.b64encode(f"{cfg.razorpay_key_id}:{cfg.razorpay_key_secret}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:     # noqa: S310 - fixed https URL
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            msg = json.loads(exc.read()).get("error", {}).get("description", "")
        except ValueError:
            msg = ""
        raise BillingError(f"payment gateway error: {msg or exc.code}") from None
    except OSError as exc:
        raise BillingError(f"payment gateway not reachable: {exc}") from None


def start_checkout(user, code: str) -> dict:
    """Create a Razorpay order for one period of the plan; returns what the checkout page needs."""
    from .models import Payment
    cfg = _razorpay()
    plan = Plan.objects.filter(code=code, public=True).first()
    if plan is None or not plan.price_month:
        raise BillingError("choose a paid plan")
    amount = int((plan.price_month * 100).to_integral_value())    # smallest unit (paise, cents)
    order = _razorpay_request(cfg, "POST", "/orders", {
        "amount": amount, "currency": plan.currency.upper(), "receipt": f"u{user.pk}-{plan.code}"[:40],
        "notes": {"user": user.username, "plan": plan.code}})
    Payment.objects.create(user=user, plan=plan, amount=plan.price_month, currency=plan.currency.upper(),
                           provider="razorpay", order_id=order["id"])
    return {"key_id": cfg.razorpay_key_id, "order_id": order["id"], "amount": amount,
            "currency": plan.currency.upper(), "business_name": cfg.business_name, "plan": plan,
            "days": period_days()}


def _activate(payment, payment_id: str):
    """Mark the payment paid (once) and give the user the plan for one more period."""
    from django.db import transaction

    from .models import Payment
    with transaction.atomic():
        p = Payment.objects.select_for_update().get(pk=payment.pk)
        if p.status == Payment.PAID:
            return p                                   # already counted (e.g. webhook + browser)
        sub = Subscription.objects.select_for_update().get(pk=get_subscription(p.user).pk)
        now = timezone.now()
        days = timedelta(days=period_days())
        if sub.plan_id == p.plan_id and sub.current_period_end > now and sub.provider == "razorpay":
            sub.current_period_end += days             # renewal: add a period
        else:
            sub.current_period_start, sub.current_period_end = now, now + days
        sub.plan, sub.status, sub.provider, sub.provider_ref = p.plan, Subscription.ACTIVE, "razorpay", payment_id
        sub.cancel_at_period_end = True                # prepaid: back to Free unless paid again
        sub.save()
        p.status, p.payment_id, p.paid_at, p.period_end = Payment.PAID, payment_id, now, sub.current_period_end
        p.save()
        return p


def verify_checkout(user, order_id: str, payment_id: str, signature: str):
    """The browser's proof of payment: HMAC-SHA256(order_id|payment_id) with the key secret."""
    import hashlib
    import hmac

    from .models import Payment
    cfg = _razorpay()
    p = Payment.objects.filter(order_id=order_id, user=user).first()
    if p is None:
        raise BillingError("unknown payment")
    expected = hmac.new(cfg.razorpay_key_secret.encode(), f"{order_id}|{payment_id}".encode(),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(signature)):
        Payment.objects.filter(pk=p.pk, status=Payment.CREATED).update(status=Payment.FAILED)
        raise BillingError("payment could not be verified - please contact support")
    return _activate(p, payment_id)


def razorpay_webhook(body: bytes, signature: str) -> str:
    """Razorpay server-to-server events (payment.captured / order.paid): activates even if the browser closed."""
    import hashlib
    import hmac
    import json

    from .models import BillingSettings
    from .models import Payment
    cfg = BillingSettings.get()
    if not cfg.razorpay_webhook_secret:
        return "webhook secret not set"
    expected = hmac.new(cfg.razorpay_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(signature)):
        raise BillingError("bad signature")
    event = json.loads(body or b"{}")
    if event.get("event") not in ("payment.captured", "order.paid"):
        return "ignored"
    entity = event.get("payload", {}).get("payment", {}).get("entity", {})
    p = Payment.objects.filter(order_id=entity.get("order_id", "")).first()
    if p is None:
        return "unknown order"
    _activate(p, entity.get("id", ""))
    return "ok"


def cancel(user) -> None:
    sub = get_subscription(user)
    if sub.plan.code != "free":
        sub.cancel_at_period_end = True
        sub.save()
