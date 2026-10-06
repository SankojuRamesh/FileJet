"""Admin -> clients -> folders -> per-member permissions (metadata only).

    ClientLink    an admin added a user (by their ID) to their list of clients
    Folder        a folder on the admin's computer, identified by a unique public ID (FD-XXXX-XXXX)
    FolderMember  a client's access to a folder: role + View/Upload/Edit/Delete, invited -> active
    OutboundEmail audit copy of every e-mail the cloud sent (folder invitations)

The folders and their files stay on the admin's computer; the admin's app ENFORCES the
permissions on every request. The cloud stores names, IDs, members, roles and status only.
"""
import secrets

from django.conf import settings
from django.db import IntegrityError, models, transaction

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"        # no 0/O, 1/I/L


def generate_folder_id() -> str:
    s = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"FD-{s[:4]}-{s[4:]}"


def normalize_folder_id(text: str) -> str:
    s = "".join(c for c in str(text).upper() if c.isalnum())
    if s.startswith("FD"):
        s = s[2:]
    return f"FD-{s[:4]}-{s[4:8]}" if len(s) == 8 else ""


ROLES = {                                          # role -> (read, upload, edit, delete)
    "uploader": (False, True, False, False),       # "sender": can only send files into the folder
    "viewer": (True, False, False, False),
    "contributor": (True, True, False, False),     # view & download + upload (no rename / delete)
    "editor": (True, True, True, False),
    "manager": (True, True, True, True),
}


class ClientLink(models.Model):
    admin = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="client_links")
    client = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="admin_links")
    note = models.CharField(max_length=80, blank=True)          # e.g. "Editor - Marketing"
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["admin", "client"], name="unique_client_link")]
        ordering = ["client__display_name", "client__username"]


class Folder(models.Model):
    SHARE, SUBMIT = "share", "submit"
    folder_id = models.CharField(max_length=12, unique=True, default=generate_folder_id)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="owned_folders")
    name = models.CharField(max_length=120)
    # Share = browse / upload / download together; Submit = drop box, uploaders cannot see the contents
    kind = models.CharField(max_length=10, default=SHARE, choices=[(SHARE, "Share"), (SUBMIT, "Submit")])
    description = models.CharField(max_length=300, blank=True)
    allowed_extensions = models.CharField(max_length=300, blank=True)     # "mp4,mov,pdf"; empty = any
    max_file_size = models.BigIntegerField(null=True, blank=True)          # bytes; null = no limit
    form_fields = models.JSONField(default=list, blank=True)               # [{"name": "Project", "required": true}]
    notify_owner = models.BooleanField(default=True)                       # e-mail the admin when uploads finish
    device = models.ForeignKey("accounts.Device", null=True, blank=True, on_delete=models.SET_NULL)
    # a folder of a workspace shared on its own: the workspace and the path inside it (shown nested in the app and
    # on the dashboard); empty for a workspace
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="subshares")
    subpath = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        for attempt in range(5):
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError as exc:
                if "folder_id" not in str(exc) or attempt == 4:
                    raise
                self.folder_id = generate_folder_id()

    def __str__(self):
        return f"{self.folder_id} {self.name}"


class FolderMember(models.Model):
    INVITED, ACTIVE, DECLINED = "invited", "active", "declined"
    folder = models.ForeignKey(Folder, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="folder_memberships")
    role = models.CharField(max_length=20, default="uploader")
    can_read = models.BooleanField(default=False)
    can_upload = models.BooleanField(default=True)
    can_edit = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)
    status = models.CharField(max_length=10, default=INVITED, choices=[(INVITED, "Invited"), (ACTIVE, "Active"),
                                                                          (DECLINED, "Declined")])
    invited_at = models.DateTimeField(auto_now_add=True)
    joined_at = models.DateTimeField(null=True, blank=True)
    email_sent_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)               # access ends automatically
    via_group = models.CharField(max_length=80, blank=True)
    # security chosen by the admin when sharing (enforced by the admin's app for every request)
    APPROVE_NONE, APPROVE_FIRST, APPROVE_EVERY = "none", "first", "every"
    APPROVE_CHOICES = [(APPROVE_NONE, "No approval"), (APPROVE_FIRST, "First upload only"),
                       (APPROVE_EVERY, "Every upload")]
    require_otp = models.BooleanField(default=False, db_default=False)        # one-time code before the first transfer
    OTP_CHANNELS = [("email", "E-mail"), ("sms", "SMS"), ("whatsapp", "WhatsApp")]
    otp_channel = models.CharField(max_length=10, default="email", db_default="email", choices=OTP_CHANNELS)
    otp_verified_at = models.DateTimeField(null=True, blank=True)
    otp_hash = models.CharField(max_length=64, blank=True)
    otp_expires_at = models.DateTimeField(null=True, blank=True)
    otp_sent_at = models.DateTimeField(null=True, blank=True)
    otp_attempts = models.PositiveSmallIntegerField(default=0, db_default=0)
    approve_uploads = models.CharField(max_length=6, default=APPROVE_NONE, db_default=APPROVE_NONE, choices=APPROVE_CHOICES)
    # with Delete permission: only the files this person sent ("own") or any file in the folder ("all")
    DELETE_SCOPES = [("own", "Only files they sent"), ("all", "Any file")]
    delete_scope = models.CharField(max_length=4, default="own", db_default="own", choices=DELETE_SCOPES)
    first_upload_approved = models.BooleanField(default=False, db_default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["folder", "user"], name="unique_folder_member")]
        ordering = ["user__display_name", "user__username"]

    def perms(self) -> dict:
        return {"read": self.can_read, "upload": self.can_upload, "edit": self.can_edit, "delete": self.can_delete}

    def security(self) -> dict:
        return {"require_otp": self.require_otp, "otp_verified": self.otp_verified_at is not None,
                "otp_channel": self.otp_channel,
                "approve_uploads": self.approve_uploads, "first_upload_approved": self.first_upload_approved,
                "delete_scope": self.delete_scope}


class Group(models.Model):
    """A named set of the admin's users, e.g. "Editors" - give a whole group access to a folder at once."""
    admin = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_groups")
    name = models.CharField(max_length=80)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="+", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["admin", "name"], name="unique_group_name")]
        ordering = ["name"]


class ActivityEvent(models.Model):
    """Audit log of folder activity (metadata only): who did what, where, when."""
    folder = models.ForeignKey(Folder, null=True, on_delete=models.SET_NULL, related_name="events")
    folder_id_text = models.CharField(max_length=12, db_index=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=20)            # upload, download, delete, rename, mkdir, join, access
    path = models.CharField(max_length=500, blank=True)
    detail = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class OutboundEmail(models.Model):
    to = models.EmailField()
    subject = models.CharField(max_length=200)
    body = models.TextField()
    kind = models.CharField(max_length=30, default="folder_invite")
    folder_id = models.CharField(max_length=12, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    delivered = models.BooleanField(default=True)
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]
