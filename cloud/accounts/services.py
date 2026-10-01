"""Devices, user lookup and signal tokens (shared by the REST API and the web views)."""
import time

import jwt
from cryptography import x509
from django.conf import settings
from django.utils import timezone

from .models import Device, User, fingerprint_of


class ServiceError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def register_device(user: User, cert_pem: str, name: str, platform: str = "", app: str = "") -> Device:
    try:
        x509.load_pem_x509_certificate(cert_pem.encode())
        fp = fingerprint_of(cert_pem)
    except Exception:
        raise ServiceError("invalid device certificate") from None
    existing = Device.objects.filter(fingerprint=fp).first()
    if existing and existing.user_id != user.id:
        raise ServiceError("this device is registered to another account", 409)
    if existing:
        existing.name, existing.platform, existing.app = name[:80] or existing.name, platform[:40], app[:20]
        existing.revoked = False            # signing in again with the password re-authorises the device
        existing.last_seen_at = timezone.now()
        existing.save()
        return existing
    return Device.objects.create(user=user, cert_pem=cert_pem, fingerprint=fp, name=name[:80] or "Device",
                                 platform=platform[:40], app=app[:20], last_seen_at=timezone.now())


def issue_signal_token(user: User, fingerprint: str) -> dict:
    """Short-lived token that lets this device use the signaling server (connection setup only)."""
    device = Device.objects.filter(user=user, fingerprint=fingerprint, revoked=False).first()
    if device is None:
        raise ServiceError("device not registered (or revoked)", 403)
    device.last_seen_at = timezone.now()
    device.save(update_fields=["last_seen_at"])
    now = int(time.time())
    claims = {"typ": "signal", "sub": str(user.id), "uid": user.public_id, "usr": user.username, "name": user.label,
              "fp": fingerprint, "iat": now, "exp": now + settings.SIGNAL_TOKEN_TTL}
    return {"token": jwt.encode(claims, settings.SIGNAL_JWT_SECRET, algorithm="HS256"),
            "expires_in": settings.SIGNAL_TOKEN_TTL, "signaling_url": settings.P2P_SIGNALING_URL}


def find_user(query: str) -> User | None:
    """By 9-digit ID (spaces/dashes allowed), e-mail or username."""
    q = (query or "").strip()
    if not q:
        return None
    digits = "".join(c for c in q if c.isdigit())
    if len(digits) == 9 and len(q.replace(" ", "").replace("-", "")) == 9:
        return User.objects.filter(public_id=digits, is_active=True).first()
    if "@" in q:
        return User.objects.filter(email__iexact=q, is_active=True).first()
    return User.objects.filter(username__iexact=q, is_active=True).first()


def device_user(fingerprint: str) -> User | None:
    if not fingerprint:
        return None
    d = Device.objects.filter(fingerprint=fingerprint).select_related("user").first()
    return d.user if d else None
