"""Per-device identity: an ECDSA P-256 key and a self-signed certificate.

Peers exchange certificates through signaling and then *pin* them: each TLS context trusts
exactly one certificate - the peer's - and the SHA-256 fingerprint is checked again after
the handshake. The identity is persistent so that a resumed transfer can require the very
same peer (trust-on-first-use), which defeats a malicious signaling server on reconnect.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import secrets
import ssl
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


@dataclass
class Identity:
    cert_path: Path
    key_path: Path
    cert_pem: str
    fingerprint: str
    _key: object = None

    def private_key(self):
        if self._key is None:
            self._key = serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
        return self._key

    def sign(self, data: bytes) -> bytes:
        """ECDSA-SHA256 signature (proves possession of this device's key, e.g. to the presence hub)."""
        return self.private_key().sign(data, ec.ECDSA(hashes.SHA256()))

    def ecdh(self, peer_cert_pem: str) -> bytes:
        """Static ECDH with a contact's device certificate -> shared secret only the two devices know."""
        peer_key = x509.load_pem_x509_certificate(peer_cert_pem.encode()).public_key()
        return self.private_key().exchange(ec.ECDH(), peer_key)

    def make_context(self, server_side: bool, peer_cert_pem: str) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if server_side else ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.load_cert_chain(str(self.cert_path), str(self.key_path))
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(cadata=peer_cert_pem)
        if hasattr(ssl, "VERIFY_X509_PARTIAL_CHAIN"):
            ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
        if server_side:
            ctx.num_tickets = 0     # no session tickets: keeps data streams strictly one-way
        return ctx


def fingerprint_of_pem(pem: str) -> str:
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


def short_auth_string(fp_sender: str, fp_receiver: str) -> str:
    """6-digit code both users can compare out of band to rule out a man in the middle."""
    n = int.from_bytes(hashlib.sha256(f"sas|{fp_sender}|{fp_receiver}".encode()).digest()[:4], "big")
    s = f"{n % 1_000_000:06d}"
    return f"{s[:3]} {s[3:]}"


def load_or_create(directory: Path) -> Identity:
    directory.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    if not (cert_path.exists() and key_path.exists()):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"p2pft-{secrets.token_hex(6)}")])
        now = dt.datetime.now(dt.timezone.utc)
        ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
        cert = (
            x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=False,
                                         content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(ski, critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ski), critical=False)
            .sign(key, hashes.SHA256())
        )
        tmp_key = key_path.with_suffix(".tmp")
        fd = os.open(tmp_key, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
        os.replace(tmp_key, key_path)
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    pem = cert_path.read_text()
    return Identity(cert_path=cert_path, key_path=key_path, cert_pem=pem, fingerprint=fingerprint_of_pem(pem))
