"""Web pages: the user's Support desk (/support/) and the staff console (/staff/)."""
from __future__ import annotations

import datetime as dt
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from billing.models import Plan, Subscription
from billing.services import get_subscription

from . import services
from .models import Ticket

User = get_user_model()


def staff_required(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not services.is_staff_member(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not services.is_platform_admin(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


# ================================================================== user: support desk
@login_required
def support_home(request):
    if request.method == "POST":
        try:
            t = services.open_ticket(request.user, request.POST.get("subject", ""), request.POST.get("message", ""),
                                     request.POST.get("category", "general"))
            messages.success(request, f"Request {t.number} sent – our team will reply here and by e-mail.")
            return redirect("support_ticket", pk=t.pk)
        except services.SupportError as exc:
            messages.error(request, str(exc))
    return render(request, "support/home.html", {
        "active": "support", "tickets": Ticket.objects.filter(user=request.user),
        "categories": Ticket.CATEGORIES})


@login_required
def support_ticket(request, pk):
    t = get_object_or_404(Ticket, pk=pk, user=request.user)
    if request.method == "POST":
        if request.POST.get("close"):
            t.status = Ticket.CLOSED
            t.save(update_fields=["status", "updated_at"])
            messages.success(request, "Request closed. Reply any time to reopen it.")
        else:
            try:
                services.add_message(t, request.user, request.POST.get("body", ""))
            except services.SupportError as exc:
                messages.error(request, str(exc))
        return redirect("support_ticket", pk=t.pk)
    services.mark_read(t, by_staff=False)
    return render(request, "support/ticket.html", {"active": "support", "t": t,
                                                   "msgs": t.messages.select_related("author")})


# ================================================================== staff console
def _expiry_info(sub):
    if sub is None:
        return None, None
    days = (sub.current_period_end - timezone.now()).days
    return sub.current_period_end, days


@staff_required
def staff_overview(request):
    now = timezone.now()
    soon = now + dt.timedelta(days=7)
    plans = Plan.objects.annotate(n=Count("subscription")).order_by("sort")
    ctx = {
        "active": "staff", "tab": "overview",
        "users_total": User.objects.filter(is_staff=False).count(),
        "users_new_7d": User.objects.filter(is_staff=False, date_joined__gte=now - dt.timedelta(days=7)).count(),
        "plans": plans,
        "expiring": Subscription.objects.filter(current_period_end__lte=soon, current_period_end__gte=now)
                    .exclude(plan__code="free").select_related("user", "plan").order_by("current_period_end")[:20],
        "open": Ticket.objects.exclude(status=Ticket.CLOSED).count(),
        "unassigned": Ticket.objects.filter(assigned_to__isnull=True).exclude(status=Ticket.CLOSED).count(),
        "need_reply": Ticket.objects.filter(status=Ticket.OPEN).count(),
        "recent": Ticket.objects.select_related("user", "assigned_to").exclude(status=Ticket.CLOSED)[:8],
    }
    return render(request, "support/staff_overview.html", ctx)


@staff_required
def staff_users(request):
    q = request.GET.get("q", "").strip()
    plan = request.GET.get("plan", "")
    expiring = request.GET.get("expiring", "")
    qs = User.objects.filter(is_staff=False, is_superuser=False).select_related("subscription__plan") \
        .annotate(devices_n=Count("devices", distinct=True)).order_by("-date_joined")
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(display_name__icontains=q)
                       | Q(public_id__icontains=q.replace(" ", "")) | Q(organization__icontains=q))
    if plan:
        qs = qs.filter(subscription__plan__code=plan)
    if expiring:
        qs = qs.filter(subscription__current_period_end__lte=timezone.now() + dt.timedelta(days=int(expiring)))
    rows = []
    for u in qs[:500]:
        sub = getattr(u, "subscription", None)
        end, days = _expiry_info(sub)
        rows.append({"u": u, "sub": sub, "end": end, "days": days})
    return render(request, "support/staff_users.html", {
        "active": "staff", "tab": "users", "rows": rows, "q": q, "plan": plan, "expiring": expiring,
        "plans": Plan.objects.order_by("sort"), "total": qs.count()})


@staff_required
def staff_user(request, pk):
    u = get_object_or_404(User, pk=pk)
    sub = get_subscription(u)
    if request.method == "POST":
        if not services.is_platform_admin(request.user):
            raise PermissionDenied
        plan = Plan.objects.filter(code=request.POST.get("plan")).first()
        try:
            end = dt.datetime.strptime(request.POST.get("expires", ""), "%Y-%m-%d")
            end = timezone.make_aware(end.replace(hour=23, minute=59))
        except ValueError:
            end = None
        if plan:
            sub.plan = plan
        if end:
            sub.current_period_end = end
        sub.cancel_at_period_end = bool(request.POST.get("end_at_expiry"))
        sub.status = request.POST.get("status") if request.POST.get("status") in dict(Subscription._meta.get_field(
            "status").choices) else sub.status
        sub.provider = sub.provider if sub.provider not in ("", "none") else "manual"
        sub.save()
        messages.success(request, f"Subscription of {u.label} updated.")
        return redirect("staff_user", pk=u.pk)
    end, days = _expiry_info(sub)
    return render(request, "support/staff_user.html", {
        "active": "staff", "tab": "users", "u": u, "sub": sub, "end": end, "days": days,
        "plans": Plan.objects.order_by("sort"), "statuses": Subscription._meta.get_field("status").choices,
        "tickets": Ticket.objects.filter(user=u)[:20], "devices": u.devices.all()[:20],
        "is_admin": services.is_platform_admin(request.user)})


@staff_required
def staff_tickets(request):
    view = request.GET.get("view", "active")
    qs = Ticket.objects.select_related("user", "assigned_to")
    if view == "active":
        qs = qs.exclude(status=Ticket.CLOSED)
    elif view == "mine":
        qs = qs.filter(assigned_to=request.user).exclude(status=Ticket.CLOSED)
    elif view == "unassigned":
        qs = qs.filter(assigned_to__isnull=True).exclude(status=Ticket.CLOSED)
    elif view in (Ticket.OPEN, Ticket.WAITING, Ticket.CLOSED):
        qs = qs.filter(status=view)
    q = request.GET.get("q", "").strip()
    if q:
        cond = Q(subject__icontains=q) | Q(user__username__icontains=q) | Q(user__email__icontains=q)
        num = q.upper().replace("FJ-", "").lstrip("0")
        if num.isdigit():
            cond |= Q(pk=int(num))                         # search by ticket number, e.g. FJ-00012
        qs = qs.filter(cond)
    return render(request, "support/staff_tickets.html", {"active": "staff", "tab": "tickets", "tickets": qs[:300],
                                                          "view": view, "q": q})


@staff_required
def staff_ticket(request, pk):
    t = get_object_or_404(Ticket.objects.select_related("user", "assigned_to"), pk=pk)
    if request.method == "POST":
        action = request.POST.get("action", "reply")
        if action == "reply":
            try:
                services.add_message(t, request.user, request.POST.get("body", ""))
                if request.POST.get("close_after"):
                    Ticket.objects.filter(pk=t.pk).update(status=Ticket.CLOSED)
            except services.SupportError as exc:
                messages.error(request, str(exc))
        elif action == "update":
            status = request.POST.get("status")
            if status in dict(Ticket.STATUSES):
                t.status = status
            if request.POST.get("priority") in dict(Ticket.PRIORITIES):
                t.priority = request.POST["priority"]
            assignee = request.POST.get("assigned_to", "")
            t.assigned_to = User.objects.filter(pk=assignee, is_staff=True).first() if assignee else None
            t.save()
            messages.success(request, "Ticket updated.")
        return redirect("staff_ticket", pk=t.pk)
    services.mark_read(t, by_staff=True)
    sub = get_subscription(t.user)
    return render(request, "support/staff_ticket.html", {
        "active": "staff", "tab": "tickets", "t": t, "msgs": t.messages.select_related("author"), "sub": sub,
        "staff": User.objects.filter(Q(is_staff=True) | Q(is_superuser=True), is_active=True).order_by("username"),
        "statuses": Ticket.STATUSES, "priorities": Ticket.PRIORITIES})


@admin_required
def staff_employees(request):
    if request.method == "POST":
        if request.POST.get("toggle"):
            e = get_object_or_404(User, pk=request.POST["toggle"], is_staff=True, is_superuser=False)
            e.is_active = not e.is_active
            e.save(update_fields=["is_active"])
            messages.success(request, f"{e.label} {'activated' if e.is_active else 'deactivated'}.")
        else:
            try:
                e = services.create_employee(request.POST.get("username", ""), request.POST.get("email", ""),
                                             request.POST.get("password", ""), request.POST.get("display_name", ""))
                messages.success(request, f"Employee {e.label} created – they can sign in and open the Staff console.")
            except services.SupportError as exc:
                messages.error(request, str(exc))
        return redirect("staff_employees")
    staff = User.objects.filter(Q(is_staff=True) | Q(is_superuser=True)).annotate(
        open_n=Count("assigned_tickets", filter=~Q(assigned_tickets__status=Ticket.CLOSED))).order_by("-is_superuser",
                                                                                                    "username")
    return render(request, "support/staff_employees.html", {"active": "staff", "tab": "employees", "staff": staff})
