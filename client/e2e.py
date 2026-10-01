"""End-to-end encryption between contacts' devices.

Keys: static ECDH between the two device keys (P-256), HKDF-SHA256 -> 256-bit key per device
pair. The peer certificate is only accepted if it is one of that contact's devices registered
in the cloud AND the one the presence hub reports as connected, so neither the signaling
server nor a network attacker can read or forge messages.

Messages: AES-256-GCM, random 96-bit nonce, AAD = "sender>recipient", with a timestamp and a
message id inside the ciphertext for replay protection.

The same pair key also derives the rendezvous room and stream authentication key for each
P2P file transfer between the two contacts (one random nonce per transfer).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import OrderedDict

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .identity import Identity, fingerprint_of_pem

MAX_SKEW = 300.0


class E2EError(Exception):
    pass


class E2E:
    def __init__(self, identity: Identity, my_uid: str):
        self.identity = identity
        self.uid = my_uid
        self._keys: dict[tuple, bytes] = {}
        self._seen: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def pair_key(self, peer_uid: str, peer_cert_pem: str) -> bytes:
        peer_fp = fingerprint_of_pem(peer_cert_pem)
        cache_key = (peer_uid, peer_fp)
        key = self._keys.get(cache_key)
        if key is None:
            a, b = sorted([(self.uid, self.identity.fingerprint), (peer_uid, peer_fp)])
            info = f"p2pft-e2e-v1|{a[0]}|{a[1]}|{b[0]}|{b[1]}".encode()
            key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"p2pft-e2e", info=info).derive(
                self.identity.ecdh(peer_cert_pem))
            self._keys[cache_key] = key
        return key

    def seal(self, peer_uid: str, peer_cert_pem: str, obj: dict) -> str:
        body = json.dumps({"ts": time.time(), "mid": secrets.token_hex(8), "body": obj},
                          separators=(",", ":")).encode()
        nonce = os.urandom(12)
        ct = AESGCM(self.pair_key(peer_uid, peer_cert_pem)).encrypt(nonce, body, f"{self.uid}>{peer_uid}".encode())
        return base64.b64encode(nonce + ct).decode()

    def open(self, peer_uid: str, peer_cert_pem: str, data: str) -> dict:
        try:
            raw = base64.b64decode(data)
            plain = AESGCM(self.pair_key(peer_uid, peer_cert_pem)).decrypt(raw[:12], raw[12:],
                                                                           f"{peer_uid}>{self.uid}".encode())
            env = json.loads(plain)
        except Exception:
            raise E2EError("message could not be decrypted/authenticated") from None
        if abs(time.time() - float(env.get("ts", 0))) > MAX_SKEW:
            raise E2EError("message too old (clock skew or replay)")
        mid = (peer_uid, env.get("mid"))
        with self._lock:
            if mid in self._seen:
                raise E2EError("replayed message")
            self._seen[mid] = True
            while len(self._seen) > 20000:
                self._seen.popitem(last=False)
        body = env.get("body")
        if not isinstance(body, dict):
            raise E2EError("bad message body")
        return body

    def rendezvous(self, peer_uid: str, peer_cert_pem: str, nonce_hex: str) -> tuple[str, bytes]:
        """(room id, stream auth key) for one P2P transfer with this contact."""
        key = self.pair_key(peer_uid, peer_cert_pem)
        room = hmac.new(key, f"room|{nonce_hex}".encode(), hashlib.sha256).hexdigest()
        auth = hmac.new(key, f"auth|{nonce_hex}".encode(), hashlib.sha256).digest()
        return room, auth
