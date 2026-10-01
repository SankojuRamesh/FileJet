"""Support desk logic shared by the web pages, the staff console and the API.

Roles:
  platform admin = Django superuser   - everything, incl. employees and subscriptions
  employee       = is_staff, member of the "Support" group - users list (read), all tickets
  user           = everyone else      - only their own tickets
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Ticket, TicketMessage

log = logging.getLogger("filejet.support")
SUPPORT_GROUP = "Support"
MAX_BODY = 5000


class SupportError(Exception):
    pass


def is_platform_admin(user) -> bool:
    return bool(user.is_authenticated and user.is_superuser)


def is_staff_member(user) -> bool:
    return bool(user.is_authenticated and user.is_active and (user.is_staff or user.is_superuser))


def _clean(text: str, limit: int, what: str) -> str:
    text = (text or "").strip()
    if not text:
        raise SupportError(f"Please enter a {what}.")
    return text[:limit]


@transaction.atomic
def open_ticket(user, subject: str, message: str, category: str = "general") -> Ticket:
    if category not in dict(Ticket.CATEGORIES):
        category = "general"
    t = Ticket.objects.create(user=user, subject=_clean(subject, 150, "subject"), category=category, staff_unread=1)
    TicketMessage.objects.create(ticket=t, author=user, body=_clean(message, MAX_BODY, "message"))
    return t


@transaction.atomic
def add_message(ticket: Ticket, author, body: str) -> TicketMessage:
    staff = is_staff_member(author) and author.pk != ticket.user_id
    if not staff and author.pk != ticket.user_id:
        raise SupportError("not your ticket")
    msg = TicketMessage.objects.create(ticket=ticket, author=author, from_staff=staff,
                                       body=_clean(body, MAX_BODY, "message"))
    now = timezone.now()
    if staff:
        Ticket.objects.filter(pk=ticket.pk).update(user_unread=F("user_unread") + 1, status=Ticket.WAITING,
                                                   last_message_at=now,
                                                   assigned_to=ticket.assigned_to or author)
        transaction.on_commit(lambda: notify_user(ticket.pk))
    else:
        Ticket.objects.filter(pk=ticket.pk).update(staff_unread=F("staff_unread") + 1, status=Ticket.OPEN,
                                                   last_message_at=now)
    ticket.refresh_from_db()
    return msg


def mark_read(ticket: Ticket, by_staff: bool) -> None:
    field = "staff_unread" if by_staff else "user_unread"
    if getattr(ticket, field):
        Ticket.objects.filter(pk=ticket.pk).update(**{field: 0})
        setattr(ticket, field, 0)


def notify_user(ticket_id: int) -> None:
    """E-mail the user that support replied (best effort - never breaks the reply)."""
    try:
        t = Ticket.objects.select_related("user").get(pk=ticket_id)
        url = settings.CLOUD_PUBLIC_URL.rstrip("/") + f"/support/{t.pk}/"
        send_mail(f"[{t.number}] Support replied: {t.subject}",
                  f"Hello {t.user.label},\n\nThe FileJet support team replied to your request \"{t.subject}\".\n\n"
                  f"Read and answer it in the FileJet app (Support) or here: {url}\n",
                  settings.DEFAULT_FROM_EMAIL, [t.user.email], fail_silently=True)
    except Exception as exc:                       # noqa: BLE001
        log.warning("support e-mail failed: %s", exc)


def create_employee(username: str, email: str, password: str, display_name: str = ""):
    """Platform admin creates a support employee (staff account, Support group, no admin rights)."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.password_validation import validate_password
    User = get_user_model()
    username, email = (username or "").strip(), (email or "").strip().lower()
    if not username or not email:
        raise SupportError("Username and e-mail are required.")
    if User.objects.filter(username__iexact=username).exists():
        raise SupportError("That username is taken.")
    if User.objects.filter(email__iexact=email).exists():
        raise SupportError("That e-mail is already registered.")
    try:
        validate_password(password)
    except Exception as exc:                       # ValidationError
        raise SupportError(" ".join(getattr(exc, "messages", [str(exc)]))) from None
    u = User.objects.create_user(username=username, email=email, password=password,
                                 display_name=display_name.strip(), is_staff=True)
    group, _ = Group.objects.get_or_create(name=SUPPORT_GROUP)
    u.groups.add(group)
    return u


def ticket_json(t: Ticket, for_staff: bool = False, messages: bool = False) -> dict:
    d = {"id": t.pk, "number": t.number, "subject": t.subject, "category": t.category,
         "category_label": t.get_category_display(), "status": t.status, "status_label": t.get_status_display(),
         "unread": t.staff_unread if for_staff else t.user_unread,
         "created_at": t.created_at, "last_message_at": t.last_message_at}
    if messages:
        d["messages"] = [{"id": m.pk, "body": m.body, "from_staff": m.from_staff, "created_at": m.created_at,
                          "author": ("FileJet Support" if m.from_staff and not for_staff else
                                     (m.author.label if m.author else "—"))}
                         for m in t.messages.select_related("author")]
    return d
