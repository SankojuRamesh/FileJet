"""Transfer METADATA reported by the desktop apps. No file contents are ever stored."""
from django.conf import settings
from django.db import models


class TransferRecord(models.Model):
    PENDING, ACTIVE, VERIFYING, RECONNECTING = "pending", "active", "verifying", "reconnecting"
    PAUSED, COMPLETED, FAILED, CANCELLED = "paused", "completed", "failed", "cancelled"
    QUEUED = "queued"          # the user sent it while the admin was offline: waiting on the user's PC
    STATUSES = [(s, s.title()) for s in (QUEUED, PENDING, ACTIVE, VERIFYING, RECONNECTING, PAUSED, COMPLETED,
                                         FAILED, CANCELLED)]
    LIVE = (QUEUED, PENDING, ACTIVE, VERIFYING, RECONNECTING)

    transfer_id = models.CharField(max_length=64, unique=True)
    job_id = models.CharField(max_length=64, blank=True, db_index=True)       # groups files of a folder transfer
    file_name = models.CharField(max_length=255)
    relative_path = models.CharField(max_length=500, blank=True)
    file_size = models.BigIntegerField(default=0)
    direction = models.CharField(max_length=12, default="send")               # send/download/upload/code
    share_name = models.CharField(max_length=120, blank=True)

    sender = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="sent_transfers")
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="received_transfers")
    initiator = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    sender_fp = models.CharField(max_length=64, blank=True)
    receiver_fp = models.CharField(max_length=64, blank=True)
    sender_status = models.CharField(max_length=20, blank=True)
    receiver_status = models.CharField(max_length=20, blank=True)

    status = models.CharField(max_length=20, default=PENDING, choices=STATUSES, db_index=True)
    bytes_transferred = models.BigIntegerField(default=0)
    speed = models.FloatField(default=0)
    avg_speed = models.FloatField(default=0)
    peak_speed = models.FloatField(default=0)
    connection_type = models.CharField(max_length=40, blank=True)
    file_hash = models.CharField(max_length=64, blank=True)
    error = models.CharField(max_length=300, blank=True)
    folder_id = models.CharField(max_length=12, blank=True, db_index=True)
    job_total = models.PositiveIntegerField(default=0)          # files in the same batch (for one notification)
    # retries of the same file (same job + path) share a key; only the newest attempt is listed,
    # the older ones are "superseded" and shown as that row's attempt log
    attempt_key = models.CharField(max_length=200, blank=True, db_index=True)
    active_seconds = models.FloatField(default=0)     # data really moving (no waiting / offline time)
    active_last = models.FloatField(default=0)        # sender app's last reported value (resets on resume)
    superseded = models.BooleanField(default=False, db_index=True)

    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["sender", "-started_at"]), models.Index(fields=["receiver", "-started_at"])]

    @property
    def progress(self) -> float:
        if not self.file_size:
            return 100.0 if self.status == self.COMPLETED else 0.0
        return min(100.0, self.bytes_transferred * 100.0 / self.file_size)

    @property
    def duration(self) -> float | None:
        """Real transfer time reported by the apps; old records (before it was reported) use start->end."""
        if self.active_seconds > 0:
            return self.active_seconds
        if self.status in self.LIVE:
            return None
        end = self.completed_at or self.updated_at
        return (end - self.started_at).total_seconds() if end else None

    def make_attempt_key(self) -> str:
        if not self.job_id:
            return self.transfer_id
        return f"{self.sender_id or ''}|{self.job_id}|{self.relative_path or self.file_name}"[:200]

    def __str__(self):
        return f"{self.file_name} ({self.status})"
