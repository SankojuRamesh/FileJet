from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from . import services
from .models import Plan


@login_required
def subscription(request):
    if request.method == "POST":
        try:
            if request.POST.get("action") == "cancel":
                services.cancel(request.user)
                messages.success(request, "Your plan will fall back to Free at the end of the period.")
            else:
                result = services.change_plan(request.user, request.POST.get("plan", ""))
                if result.get("status") == "active":
                    messages.success(request, "Subscription updated.")
        except services.BillingError as exc:
            messages.error(request, str(exc))
        return redirect("subscription")
    sub = services.get_subscription(request.user)
    usage = services.usage_summary(request.user)
    pct = (usage["used_bytes"] / usage["quota_bytes"] * 100) if usage["quota_bytes"] else 0
    return render(request, "cloud/subscription.html", {
        "sub": sub, "plans": Plan.objects.filter(public=True), "usage": usage, "usage_pct": min(100, pct),
        "active": "subscription", "billing_provider": settings.BILLING_PROVIDER})
