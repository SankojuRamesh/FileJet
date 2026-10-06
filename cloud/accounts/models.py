"""Users (with a public 9-digit ID) and their registered devices (public certificates only)."""
import hashlib
import secrets
import ssl

from django.contrib.auth.models import AbstractUser
from django.db import IntegrityError, models, transaction


def generate_public_id() -> str:
    return str(secrets.randbelow(9 * 10 ** 8) + 10 ** 8)          # 9 digits, no leading zero


def normalize_phone(value: str) -> str:
    """'+91 98765-43210' -> '+919876543210'. Empty is allowed. Raises ValueError for anything else."""
    import re
    v = re.sub(r"[\s\-().]", "", str(value or ""))
    if not v:
        return ""
    if v.startswith("00"):
        v = "+" + v[2:]
    if not re.fullmatch(r"\+[1-9]\d{7,14}", v):
        raise ValueError("enter the mobile number with the country code, e.g. +91 98765 43210")
    return v


class User(AbstractUser):
    email = models.EmailField(unique=True)
    public_id = models.CharField(max_length=9, unique=True, default=generate_public_id, editable=False)
    display_name = models.CharField(max_length=80, blank=True)
    organization = models.CharField(max_length=120, blank=True)      # shown to users and in e-mails
    location = models.CharField(max_length=120, blank=True)          # city / country, asked at sign-up
    phone = models.CharField(max_length=20, blank=True)               # E.164, for SMS / WhatsApp codes

    def save(self, *args, **kwargs):
        for attempt in range(5):                                     # regenerate on an ID collision
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError as exc:
                if "public_id" not in str(exc) or attempt == 4:
                    raise
                self.public_id = generate_public_id()

    @property
    def label(self) -> str:
        return self.display_name or self.username

    @property
    def formatted_id(self) -> str:
        p = self.public_id
        return f"{p[:3]} {p[3:6]} {p[6:]}"


def fingerprint_of(cert_pem: str) -> str:
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert_pem)).hexdigest()


class Device(models.Model):
    """An app installation. Only the PUBLIC certificate is stored (peers pin it for encryption)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="devices")
    name = models.CharField(max_length=80)
    platform = models.CharField(max_length=40, blank=True)
    app = models.CharField(max_length=20, blank=True)              # "admin" / "client"
    fingerprint = models.CharField(max_length=64, unique=True)
    cert_pem = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    revoked = models.BooleanField(default=False)

    class Meta:
        ordering = ["-last_seen_at", "-created_at"]

    def __str__(self):
        return f"{self.user.username}: {self.name}"
