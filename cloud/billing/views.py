from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import services
from .models import BillingSettings, Payment, Plan


@login_required
def subscription(request):
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "cancel":
                services.cancel(request.user)
                messages.success(request, "Your plan will fall back to Free at the end of the period.")
            elif action == "pay":                     # paid plan (new or renewal): Razorpay checkout
                checkout = services.start_checkout(request.user, request.POST.get("plan", ""))
                return render(request, "cloud/checkout.html", {"c": checkout, "active": "subscription"})
            else:
                result = services.change_plan(request.user, request.POST.get("plan", ""))
                if result.get("status") == "active":
                    messages.success(request, "Subscription updated.")
                elif result.get("status") == "ends_at_period_end":
                    messages.success(request, "You keep your paid plan until the end of the period, then Free.")
        except services.PaymentRequired:
            checkout = services.start_checkout(request.user, request.POST.get("plan", ""))
            return render(request, "cloud/checkout.html", {"c": checkout, "active": "subscription"})
        except services.BillingError as exc:
            messages.error(request, str(exc))
        return redirect("subscription")
    sub = services.get_subscription(request.user)
    usage = services.usage_summary(request.user)
    pct = (usage["used_bytes"] / usage["quota_bytes"] * 100) if usage["quota_bytes"] else 0
    return render(request, "cloud/subscription.html", {
        "sub": sub, "plans": Plan.objects.filter(public=True), "usage": usage, "usage_pct": min(100, pct),
        "active": "subscription", "payments_on": services.payments_enabled(),
        "period_days": services.period_days(), "chosen": request.GET.get("plan", ""),
        "payments": Payment.objects.filter(user=request.user).select_related("plan")[:20]})


@login_required
@require_POST
def checkout_done(request):
    """Razorpay checkout handler posts the payment here; the signature proves it is genuine."""
    try:
        p = services.verify_checkout(request.user, request.POST.get("razorpay_order_id", ""),
                                     request.POST.get("razorpay_payment_id", ""),
                                     request.POST.get("razorpay_signature", ""))
        messages.success(request, f"Payment received - {p.plan.name} is active until "
                                  f"{p.period_end:%Y-%m-%d}. Thank you!")
    except services.BillingError as exc:
        messages.error(request, str(exc))
    return redirect("subscription")


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    """Server-to-server confirmation from Razorpay (set the URL + secret in the Razorpay dashboard)."""
    try:
        result = services.razorpay_webhook(request.body, request.headers.get("X-Razorpay-Signature", ""))
    except services.BillingError:
        return HttpResponseBadRequest("bad signature")
    return HttpResponse(result)


def payments_ready() -> bool:
    cfg = BillingSettings.get()
    return cfg.provider == BillingSettings.RAZORPAY and cfg.razorpay_ready
