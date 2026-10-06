"""Platform admin: organizations, their users, data transferred per day / week / month, expiring plans.

Data transferred = completed transfers a user started (the same figure the monthly quota counts)."""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict

from django.contrib.auth import get_user_model
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate, TruncHour
from django.utils import timezone

from billing.models import Subscription
from transfers.models import TransferRecord

User = get_user_model()
NO_ORG = "No organization"


def period(kind: str, day: dt.date) -> tuple[dt.datetime, dt.datetime, str, dt.date, dt.date]:
    """(start, end, label, previous anchor, next anchor) of the day / ISO week / calendar month around ``day``."""
    tz = timezone.get_current_timezone()
    if kind == "week":
        first = day - dt.timedelta(days=day.weekday())
        last = first + dt.timedelta(days=7)
        label = f"Week {first:%d %b} – {last - dt.timedelta(days=1):%d %b %Y}"
        prev, nxt = first - dt.timedelta(days=7), last
    elif kind == "month":
        first = day.replace(day=1)
        last = (first + dt.timedelta(days=32)).replace(day=1)
        label = f"{first:%B %Y}"
        prev, nxt = (first - dt.timedelta(days=1)).replace(day=1), last
    else:
        first, last = day, day + dt.timedelta(days=1)
        label = f"{day:%A, %d %B %Y}"
        prev, nxt = day - dt.timedelta(days=1), last
    start = timezone.make_aware(dt.datetime.combine(first, dt.time()), tz)
    end = timezone.make_aware(dt.datetime.combine(last, dt.time()), tz)
    return start, end, label, prev, nxt


def build(kind: str, day: dt.date, q: str = "") -> dict:
    start, end, label, prev, nxt = period(kind, day)
    users = list(User.objects.filter(is_staff=False, is_superuser=False).select_related("subscription__plan")
                 .order_by("organization", "display_name", "username"))
    done = TransferRecord.objects.filter(status=TransferRecord.COMPLETED, superseded=False,
                                         completed_at__gte=start, completed_at__lt=end, initiator__isnull=False)
    per_user = {r["initiator"]: r for r in done.values("initiator").annotate(bytes=Sum("file_size"),
                                                                             files=Count("id"))}
    orgs = defaultdict(lambda: {"users": [], "bytes": 0, "files": 0, "active": 0})
    for u in users:
        name = (u.organization or "").strip() or NO_ORG
        if q and q.lower() not in name.lower() and q.lower() not in (u.label + u.email + (u.location or "")).lower():
            continue
        r = per_user.get(u.pk, {})
        u.t_bytes, u.t_files = int(r.get("bytes") or 0), int(r.get("files") or 0)
        sub = getattr(u, "subscription", None)
        u.plan_name = sub.plan.name if sub else "Free"
        u.expires = sub.current_period_end if sub and sub.plan.price_month else None
        o = orgs[name]
        o["users"].append(u)
        o["bytes"] += u.t_bytes
        o["files"] += u.t_files
        o["active"] += 1 if u.t_bytes else 0
    rows = [{"name": n, **o, "locations": sorted({u.location for u in o["users"] if u.location})}
            for n, o in orgs.items()]
    rows.sort(key=lambda o: (o["name"] == NO_ORG, -o["bytes"], o["name"].lower()))
    for o in rows:
        o["users"].sort(key=lambda u: (-u.t_bytes, u.label.lower()))

    # trend: per hour for a day, per day for a week / month
    bucket = TruncHour("completed_at") if kind == "day" else TruncDate("completed_at")
    trend_raw = {r["b"]: r["n"] for r in done.annotate(b=bucket).values("b").annotate(n=Sum("file_size"))}
    trend = []
    if kind == "day":
        for h in range(24):
            t = start + dt.timedelta(hours=h)
            n = next((v for k, v in trend_raw.items() if k and timezone.localtime(k).hour == h), 0)
            trend.append({"label": f"{h:02d}", "bytes": int(n or 0), "title": f"{t:%H}:00"})
    else:
        d = start.date()
        while d < end.date():
            trend.append({"label": f"{d:%d}", "bytes": int(trend_raw.get(d) or 0), "title": f"{d:%a %d %b}"})
            d += dt.timedelta(days=1)
    peak = max((t["bytes"] for t in trend), default=0) or 1
    for t in trend:
        t["pct"] = round(t["bytes"] * 100 / peak, 1)

    total = sum(o["bytes"] for o in rows)
    return {"orgs": rows, "trend": trend, "total_bytes": total, "total_files": sum(o["files"] for o in rows),
            "label": label, "prev": prev, "next": nxt, "start": start, "end": end,
            "users_n": sum(len(o["users"]) for o in rows), "active_users": sum(o["active"] for o in rows)}


def expiring(days: int = 7) -> dict:
    """Paid plans that end within ``days`` and ones that ended in the last ``days`` (fell back to Free)."""
    now = timezone.now()
    paid = Subscription.objects.exclude(plan__price_month=0).select_related("user", "plan")
    soon = list(paid.filter(current_period_end__gte=now, current_period_end__lte=now + dt.timedelta(days=days))
                .order_by("current_period_end"))
    for s in soon:
        s.days_left = max(0, math.ceil((s.current_period_end - now).total_seconds() / 86400) - (
            1 if timezone.localdate(s.current_period_end) == timezone.localdate(now) else 0))   # 0 = today
    overdue = list(paid.filter(current_period_end__lt=now, current_period_end__gte=now - dt.timedelta(days=days))
                   .order_by("-current_period_end"))
    past_due = list(Subscription.objects.filter(status=Subscription.PAST_DUE).select_related("user", "plan"))
    return {"soon": soon, "overdue": overdue, "past_due": past_due, "count": len(soon) + len(overdue) + len(past_due),
            "days": days}
