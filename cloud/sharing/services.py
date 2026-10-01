"""Business rules for clients, folders, members and invitation e-mails."""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import Device, User
from accounts.services import ServiceError, find_user
from billing.services import get_plan

from .models import (ROLES, ActivityEvent, ClientLink, Folder, FolderMember, Group, OutboundEmail,
                     normalize_folder_id)

log = logging.getLogger("cloud.sharing")


# ------------------------------------------------------------------ clients
def add_client(admin: User, query: str, note: str = "") -> ClientLink:
    user = find_user(query)
    if user is None:
        raise ServiceError("no user with that ID, username or e-mail - they must create an account first", 404)
    if user.id == admin.id:
        raise ServiceError("you cannot add yourself")
    limit = get_plan(admin).max_users
    if limit is not None and ClientLink.objects.filter(admin=admin).count() >= limit and \
            not ClientLink.objects.filter(admin=admin, client=user).exists():
        raise ServiceError(f"your plan allows {limit} users - upgrade to add more", 402)
    link, _ = ClientLink.objects.update_or_create(admin=admin, client=user, defaults={"note": note[:80]})
    return link


@transaction.atomic
def remove_client(admin: User, client: User) -> None:
    FolderMember.objects.filter(folder__owner=admin, user=client).delete()
    ClientLink.objects.filter(admin=admin, client=client).delete()


# ------------------------------------------------------------------ folders
def create_folder(owner: User, name: str, device_fp: str = "", settings_data: dict | None = None) -> Folder:
    limit = get_plan(owner).max_folders
    if limit is not None and Folder.objects.filter(owner=owner).count() >= limit:
        raise ServiceError(f"your plan allows {limit} folders - upgrade to create more", 402)
    device = Device.objects.filter(user=owner, fingerprint=device_fp).first() if device_fp else None
    f = Folder(owner=owner, name=(name or "").strip()[:120] or "Folder", device=device)
    apply_folder_settings(f, settings_data or {})
    f.save()
    return f


def apply_folder_settings(f: Folder, d: dict) -> None:
    """Folder rules (metadata only). Enforced by the admin's app; stored here for the dashboard."""
    if d.get("name"):
        f.name = str(d["name"]).strip()[:120]
    if d.get("kind") in (Folder.SHARE, Folder.SUBMIT):
        f.kind = d["kind"]
    if "description" in d:
        f.description = str(d["description"] or "")[:300]
    if "allowed_extensions" in d:
        exts = [e.strip().lstrip(".").lower() for e in str(d["allowed_extensions"] or "").replace(";", ",").split(",")]
        f.allowed_extensions = ",".join(e for e in exts if e and e.isalnum())[:300]
    if "max_file_size" in d:
        v = d["max_file_size"]
        f.max_file_size = int(v) if v not in (None, "", 0) else None
    if "form_fields" in d:                       # field NAMES only; the values stay on the admin's computer
        fields = []
        for item in (d["form_fields"] or [])[:10]:
            name = str(item.get("name", "")).strip()[:40] if isinstance(item, dict) else ""
            if name:
                fields.append({"name": name, "required": bool(item.get("required"))})
        f.form_fields = fields
    if "notify_owner" in d:
        f.notify_owner = bool(d["notify_owner"])


def owned_folder(owner: User, folder_id: str) -> Folder:
    f = Folder.objects.filter(owner=owner, folder_id=normalize_folder_id(folder_id)).first()
    if f is None:
        raise ServiceError("folder not found", 404)
    return f


def _perms_from(data: dict, role: str) -> tuple:
    if role in ROLES and not any(k in data for k in ("read", "upload", "edit", "delete")):
        return ROLES[role]
    perms = tuple(bool(data.get(k)) for k in ("read", "upload", "edit", "delete"))
    if not any(perms):
        raise ServiceError("give at least one permission")
    return perms


def role_for(perms: tuple) -> str:
    for name, p in ROLES.items():
        if tuple(perms) == p:
            return name
    return "custom"


def set_member(owner: User, folder: Folder, query: str, data: dict, request=None) -> tuple[FolderMember, bool]:
    user = find_user(query)
    if user is None or not ClientLink.objects.filter(admin=owner, client=user).exists():
        raise ServiceError("add this user to your Users list first (by their ID)", 400)
    perms = _perms_from(data, str(data.get("role", "")))
    fields = dict(zip(("can_read", "can_upload", "can_edit", "can_delete"), perms), role=role_for(perms),
                  expires_at=_expiry(data), via_group=str(data.get("via_group", ""))[:80])
    member, created = FolderMember.objects.update_or_create(folder=folder, user=user, defaults=fields)
    if created:
        send_invitation(member, request)
        log_event(folder, owner, "access", user.label, f"{member.role} access given to {user.username}")
    return member, created


def _expiry(data: dict):
    v = data.get("expires_at")
    if not v:
        return None
    import datetime as _dt
    from django.utils.dateparse import parse_date, parse_datetime
    dtv = parse_datetime(str(v))
    if dtv is None:
        d = parse_date(str(v))
        if d is None:
            raise ServiceError("expires_at must be a date (YYYY-MM-DD)")
        dtv = _dt.datetime.combine(d, _dt.time(23, 59, 59))
    if timezone.is_naive(dtv):
        dtv = timezone.make_aware(dtv)
    return dtv


def add_group_to_folder(owner: User, folder: Folder, group: Group, data: dict, request=None) -> int:
    n = 0
    for user in group.members.all():
        if ClientLink.objects.filter(admin=owner, client=user).exists():
            set_member(owner, folder, user.public_id, dict(data, via_group=group.name), request)
            n += 1
    return n


def log_event(folder, owner: User, action: str, path: str = "", detail: str = "", actor: User | None = None,
              folder_id: str = "") -> ActivityEvent:
    return ActivityEvent.objects.create(folder=folder, folder_id_text=folder.folder_id if folder else folder_id,
                                        owner=owner, actor=actor, action=action[:20], path=str(path)[:500],
                                        detail=str(detail)[:300])


def update_member(owner: User, member: FolderMember, data: dict) -> FolderMember:
    if member.folder.owner_id != owner.id:
        raise ServiceError("not your folder", 403)
    perms = _perms_from(data, str(data.get("role", "")))
    member.can_read, member.can_upload, member.can_edit, member.can_delete = perms
    member.role = role_for(perms)
    if "expires_at" in data:
        member.expires_at = _expiry(data)
    member.save()
    log_event(member.folder, owner, "access", member.user.label, f"permissions changed to {member.role}")
    return member


def join_folder(user: User, folder_id: str) -> FolderMember:
    fid = normalize_folder_id(folder_id)
    member = FolderMember.objects.filter(folder__folder_id=fid, user=user).select_related("folder__owner").first()
    if member is None:
        raise ServiceError("you have not been given access to this folder (check the folder ID, and that you are "
                           "signed in with the account the admin added)", 404)
    if member.expires_at and member.expires_at < timezone.now():
        raise ServiceError("your access to this folder has expired - ask the admin to extend it", 403)
    if member.status != FolderMember.ACTIVE:
        member.status, member.joined_at = FolderMember.ACTIVE, timezone.now()
        member.save(update_fields=["status", "joined_at", "updated_at"])
        log_event(member.folder, member.folder.owner, "join", "", f"{user.username} opened the folder", actor=user)
    return member


def connections(user: User):
    """People this user may see online / exchange encrypted messages with:
    the admins who added them and the clients they added."""
    ids = set(ClientLink.objects.filter(client=user).values_list("admin_id", flat=True))
    ids |= set(ClientLink.objects.filter(admin=user).values_list("client_id", flat=True))
    return User.objects.filter(id__in=ids, is_active=True)


# ------------------------------------------------------------------ e-mail
PERM_TEXT = {"read": "view and download files", "upload": "upload (send) files", "edit": "rename and create folders",
             "delete": "delete files"}


def send_invitation(member: FolderMember, request=None) -> OutboundEmail:
    f, admin, user = member.folder, member.folder.owner, member.user
    allowed = [txt for key, txt in PERM_TEXT.items() if member.perms()[key]]
    web = request.build_absolute_uri("/") if request is not None else settings.CLOUD_PUBLIC_URL
    org = f" ({admin.organization})" if admin.organization else ""
    subject = f"{admin.label}{org} shared the folder \"{f.name}\" with you"
    body = (
        f"Hello {user.label},\n\n"
        f"{admin.label} ({admin.email}) gave you access to the folder \"{f.name}\".\n\n"
        f"    Folder ID:  {f.folder_id}\n"
        f"    Type:       {'Submit (you send files to ' + admin.label + ')' if f.kind == 'submit' else 'Share'}\n"
        f"    Your role:  {member.role.title()} - you can {', '.join(allowed)}.\n\n"
        f"How to open it:\n"
        f"  1. Open FileJet and sign in as {user.username} ({user.email}).\n"
        f"  2. Click \"Open folder\" and enter the folder ID {f.folder_id}\n"
        f"     (it is also listed under Shared with me).\n\n"
        + (f"    Access until: {member.expires_at:%Y-%m-%d}\n" if member.expires_at else "")
        + "\n"
        f"Web dashboard: {web}\n"
    )
    mail = OutboundEmail(to=user.email, subject=subject, body=body, folder_id=f.folder_id)
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)
    except Exception as exc:            # SMTP down: keep the invitation, report the problem
        log.warning("invitation e-mail to %s failed: %s", user.email, exc)
        mail.delivered, mail.error = False, str(exc)[:300]
    mail.save()
    member.email_sent_at = timezone.now()
    member.save(update_fields=["email_sent_at"])
    return mail


# ------------------------------------------------------------------ serialisation
def user_json(u: User) -> dict:
    return {"public_id": u.public_id, "username": u.username, "display_name": u.display_name, "email": u.email,
            "organization": u.organization}


def member_json(m: FolderMember) -> dict:
    return {"id": m.id, "user": user_json(m.user), "role": m.role, "perms": m.perms(), "status": m.status,
            "invited_at": m.invited_at, "joined_at": m.joined_at, "email_sent_at": m.email_sent_at,
            "expires_at": m.expires_at, "via_group": m.via_group}


def folder_json(f: Folder, members: bool = True) -> dict:
    d = {"folder_id": f.folder_id, "name": f.name, "owner": user_json(f.owner), "created_at": f.created_at,
         "updated_at": f.updated_at, "kind": f.kind, "description": f.description,
         "allowed_extensions": f.allowed_extensions, "max_file_size": f.max_file_size, "form_fields": f.form_fields,
         "notify_owner": f.notify_owner}
    if members:
        d["members"] = [member_json(m) for m in f.members.select_related("user")]
    return d


def folder_stats(folder_ids, sender=None) -> dict:
    """Per folder: files received, bytes, waiting/in progress, last upload, senders (metadata only)."""
    from django.db.models import Count, Max, Q, Sum
    from transfers.models import TransferRecord
    qs = TransferRecord.objects.filter(folder_id__in=list(folder_ids), direction="upload")
    if sender is not None:
        qs = qs.filter(sender=sender)
    rows = qs.values("folder_id").annotate(
        files=Count("id", filter=Q(status="completed")), bytes=Sum("file_size", filter=Q(status="completed")),
        waiting=Count("id", filter=Q(status="queued")),
        in_progress=Count("id", filter=Q(status__in=("pending", "active", "verifying", "reconnecting"))),
        failed=Count("id", filter=Q(status__in=("failed", "cancelled"))),
        last=Max("completed_at"), senders=Count("sender", distinct=True))
    return {r["folder_id"]: {"files": r["files"], "bytes": int(r["bytes"] or 0), "waiting": r["waiting"],
                             "in_progress": r["in_progress"], "failed": r["failed"], "last_upload": r["last"],
                             "senders": r["senders"]} for r in rows}


EMPTY_STATS = {"files": 0, "bytes": 0, "waiting": 0, "in_progress": 0, "failed": 0, "last_upload": None, "senders": 0}


def overview(user: User) -> dict:
    owned = list(Folder.objects.filter(owner=user).select_related("owner").prefetch_related("members__user"))
    mine = list(FolderMember.objects.filter(user=user).select_related("folder__owner"))
    own_stats = folder_stats([f.folder_id for f in owned])
    my_stats = folder_stats([m.folder.folder_id for m in mine], sender=user)
    return {
        "owned": [dict(folder_json(f), stats=own_stats.get(f.folder_id, EMPTY_STATS)) for f in owned],
        "member": [dict(folder_json(m.folder, members=False), role=m.role, perms=m.perms(), status=m.status,
                        member_id=m.id, expires_at=m.expires_at,
                        stats=my_stats.get(m.folder.folder_id, EMPTY_STATS)) for m in mine],
        "groups": [{"id": g.id, "name": g.name, "members": [user_json(u) for u in g.members.all()]}
                   for g in Group.objects.filter(admin=user).prefetch_related("members")],
        "clients": [dict(user_json(l.client), note=l.note, added_at=l.created_at)
                    for l in ClientLink.objects.filter(admin=user).select_related("client")],
        "admins": [user_json(l.admin) for l in ClientLink.objects.filter(client=user).select_related("admin")],
    }


# ------------------------------------------------------------------ completion notifications
def notify_completed(rec) -> None:
    """One e-mail per batch (job) when every file of it has completed."""
    from transfers.models import TransferRecord
    if rec.status != "completed" or not rec.job_id:
        return
    done = TransferRecord.objects.filter(job_id=rec.job_id, status="completed")
    if done.count() < max(rec.job_total, 1):
        return
    if OutboundEmail.objects.filter(kind="batch_done", folder_id=rec.job_id[:12]).exists():
        return
    size = sum(t.file_size for t in done)
    names = ", ".join(t.relative_path or t.file_name for t in done[:5]) + (" ..." if done.count() > 5 else "")
    notes = []
    if rec.direction == "upload" and rec.folder_id:
        folder = Folder.objects.filter(folder_id=rec.folder_id).first()
        if folder and folder.notify_owner and rec.receiver:
            who = rec.sender.label if rec.sender else "A user"
            notes.append((rec.receiver, f"{who} uploaded {done.count()} file(s) to \"{folder.name}\"",
                          f"{who} uploaded {done.count()} file(s) ({size / 1e6:.1f} MB) to your folder "
                          f"\"{folder.name}\" ({folder.folder_id}).\n\nFiles: {names}\n"))
    elif rec.direction == "send" and rec.sender and rec.receiver:
        notes.append((rec.receiver, f"{rec.sender.label} sent you {done.count()} file(s)",
                      f"{rec.sender.label} sent you {done.count()} file(s) ({size / 1e6:.1f} MB): {names}\n"))
        notes.append((rec.sender, f"Delivered: {done.count()} file(s) to {rec.receiver.label}",
                      f"{rec.receiver.label} received all {done.count()} file(s) ({size / 1e6:.1f} MB): {names}\n"
                      "Integrity verified (SHA-256).\n"))
    for user, subject, body in notes:
        mail = OutboundEmail(to=user.email, subject=subject, body=f"Hello {user.label},\n\n{body}",
                             kind="batch_done", folder_id=rec.job_id[:12])
        try:
            send_mail(subject, mail.body, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)
        except Exception as exc:
            mail.delivered, mail.error = False, str(exc)[:300]
        mail.save()
