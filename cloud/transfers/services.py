"""Merge status reports from the sender and receiver apps into one TransferRecord.

Each side reports its own view (role, state, bytes, speed, peer device fingerprint). A user
can only claim *its own* role; the other party is identified from the reported peer
fingerprint (devices are registered in the cloud), and a later claim for an already-taken
role by another user is rejected.
"""
import re

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import Device
from accounts.services import device_user

from .models import TransferRecord

TID_RE = re.compile(r"^[0-9a-f]{16,64}$")

# engine state -> record status
STATE_MAP = {
    "starting": "pending", "hashing": "pending", "waiting": "pending", "connecting": "pending",
    "awaiting_accept": "pending", "queued": "queued", "active": "active", "verifying": "verifying",
    "reconnecting": "reconnecting", "paused": "paused", "completed": "completed", "failed": "failed",
    "cancelled": "cancelled",
}
ORDER = ["completed", "failed", "cancelled", "verifying", "active", "reconnecting", "paused", "pending", "queued"]


class ReportError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def derive_status(sender_status: str, receiver_status: str, current: str) -> str:
    if current == TransferRecord.COMPLETED:
        return current                                        # never regresses
    s, r = STATE_MAP.get(sender_status, ""), STATE_MAP.get(receiver_status, "")
    if "completed" in (s, r):
        return "completed"
    for st in ORDER[1:]:
        if st in (s, r):
            return st
    return current or "pending"


def _int(v, default=0):
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return default


def _float(v):
    try:
        return max(0.0, float(v))
    except (TypeError, ValueError):
        return 0.0


@transaction.atomic
def apply_report(user, data: dict) -> TransferRecord:
    tid = str(data.get("transfer_id", ""))
    role = data.get("role")
    if not TID_RE.match(tid) or role not in ("sender", "receiver"):
        raise ReportError("invalid transfer_id or role")
    device_fp = str(data.get("device_fingerprint", ""))[:64]
    if device_fp and not Device.objects.filter(user=user, fingerprint=device_fp).exists():
        raise ReportError("device not registered to this account", 403)
    peer_fp = str(data.get("peer_fingerprint", ""))[:64]
    state = str(data.get("state", ""))[:20]

    rec, created = TransferRecord.objects.select_for_update().get_or_create(
        transfer_id=tid, defaults={"file_name": str(data.get("file_name") or "?")[:255]})
    own_fps = set(Device.objects.filter(user=user).values_list("fingerprint", flat=True))
    if role == "sender":
        if rec.sender_id and rec.sender_id != user.id:
            raise ReportError("transfer belongs to another sender", 403)
        if rec.sender_fp and rec.sender_fp not in own_fps:
            raise ReportError("receiver reported a different sender", 403)
        rec.sender, rec.sender_fp, rec.sender_status = user, device_fp or rec.sender_fp, state
        if peer_fp and not rec.receiver_id:
            rec.receiver_fp = peer_fp
            rec.receiver = device_user(peer_fp)
        if not rec.receiver_id and data.get("folder_id"):
            # upload queued while the admin is offline: the receiver is the folder's admin, if the
            # sender really has access to that folder
            from sharing.models import FolderMember
            m = FolderMember.objects.filter(folder__folder_id=str(data["folder_id"])[:12], user=user) \
                .select_related("folder__owner").first()
            if m is not None:
                rec.receiver = m.folder.owner
    else:
        if rec.receiver_id and rec.receiver_id != user.id:
            raise ReportError("transfer belongs to another receiver", 403)
        if rec.receiver_fp and rec.receiver_fp not in own_fps:
            raise ReportError("sender reported a different receiver", 403)
        rec.receiver, rec.receiver_fp, rec.receiver_status = user, device_fp or rec.receiver_fp, state
        if peer_fp and not rec.sender_id:
            rec.sender_fp = peer_fp
            rec.sender = device_user(peer_fp)

    if data.get("initiator") and rec.initiator_id is None:
        rec.initiator = user
    # descriptive metadata: first writer wins, sender's values preferred
    for field, maxlen in (("file_name", 255), ("relative_path", 500), ("direction", 12), ("share_name", 120),
                          ("job_id", 64)):
        val = data.get(field)
        if val and (created or role == "sender" or not getattr(rec, field)):
            setattr(rec, field, str(val)[:maxlen])
    if data.get("folder_id") and not rec.folder_id:
        # only the folder's admin or one of its members may attribute a transfer to a folder
        from sharing.models import Folder, FolderMember
        fid = str(data["folder_id"])[:12]
        if Folder.objects.filter(folder_id=fid, owner=user).exists() or                 FolderMember.objects.filter(folder__folder_id=fid, user=user).exists():
            rec.folder_id = fid
    if data.get("job_total"):
        rec.job_total = max(rec.job_total, min(int(data["job_total"]), 100000))
    if data.get("file_size") is not None and (role == "sender" or not rec.file_size):
        rec.file_size = _int(data.get("file_size"))
    # progress: the receiver's durable byte count is authoritative once it reports
    if role == "receiver" or not rec.receiver_status:
        rec.bytes_transferred = min(_int(data.get("bytes_transferred")), rec.file_size or 2 ** 62)
        rec.speed = _float(data.get("speed"))
        rec.avg_speed = _float(data.get("avg_speed")) or rec.avg_speed
        rec.peak_speed = max(rec.peak_speed, _float(data.get("peak_speed")))
    if data.get("transfer_time") is not None and (role == "sender" or not rec.sender_status):
        t = min(_float(data.get("transfer_time")), 10 ** 8)
        # the app counts from 0 again after a resume: add the new run on top of the earlier ones
        rec.active_seconds += t - rec.active_last if t >= rec.active_last else t
        rec.active_last = t
    if data.get("connection_type"):
        rec.connection_type = str(data["connection_type"])[:40]
    if data.get("file_hash"):
        rec.file_hash = str(data["file_hash"])[:64]
    if data.get("error"):
        rec.error = str(data["error"])[:300]
    was_completed = rec.status == TransferRecord.COMPLETED
    rec.status = derive_status(rec.sender_status, rec.receiver_status, rec.status)
    if rec.status == TransferRecord.COMPLETED:
        rec.bytes_transferred = rec.file_size
        rec.speed = 0
        rec.completed_at = rec.completed_at or timezone.now()
        rec.error = ""
    key = rec.make_attempt_key()
    new_key = key != rec.attempt_key
    rec.attempt_key = key
    rec.save()
    if new_key:            # a retry of an earlier attempt: list only this one, keep the old ones as its log
        TransferRecord.objects.filter(attempt_key=key, pk__lt=rec.pk, superseded=False).update(superseded=True)
    if rec.status == TransferRecord.COMPLETED and not was_completed:
        from sharing.services import notify_completed
        transaction.on_commit(lambda: notify_completed(rec))
    return rec


def visible_to(user):
    return TransferRecord.objects.filter(Q(sender=user) | Q(receiver=user)).select_related("sender", "receiver")


FAILED_STATES = (TransferRecord.FAILED, TransferRecord.CANCELLED)


def with_attempts(qs):
    """Newest attempt per file, with how many times it was tried and how many tries failed."""
    from django.db.models import Count, IntegerField, Min, OuterRef, Q, Subquery, Sum
    from django.db.models.functions import Coalesce
    same = TransferRecord.objects.filter(attempt_key=OuterRef("attempt_key")).order_by().values("attempt_key")
    return qs.filter(superseded=False).annotate(
        attempts=Coalesce(Subquery(same.annotate(n=Count("id")).values("n")[:1], output_field=IntegerField()), 1),
        failures=Coalesce(Subquery(same.annotate(n=Count("id", filter=Q(status__in=FAILED_STATES)))
                                   .values("n")[:1], output_field=IntegerField()), 0),
        first_started_at=Subquery(same.annotate(m=Min("started_at")).values("m")[:1]),
        total_active=Subquery(same.annotate(t=Sum("active_seconds")).values("t")[:1]))


def attempt_log(user, transfer_id: str) -> list[TransferRecord]:
    rec = visible_to(user).filter(transfer_id=transfer_id).first()
    if rec is None:
        return []
    return list(visible_to(user).filter(attempt_key=rec.attempt_key or rec.transfer_id).order_by("started_at", "pk"))
