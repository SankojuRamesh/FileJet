"""Presence hub: who of my contacts is online, plus relay of small end-to-end encrypted messages.

    GET /ws/presence   (WebSocket)

    server -> {"type": "challenge", "nonce": b64}
    client -> {"type": "auth", "token": <cloud signal JWT>, "cert": <device cert PEM>,
               "sig": b64(ECDSA-SHA256("p2pft-presence|" + nonce)), "contacts": [public ids]}
    server -> {"type": "welcome", "uid": ..., "expires_at": ...}
           -> {"type": "presence", "uid", "online", "name", "fp", "cert"}  (for every visible contact)
    client -> {"type": "contacts", "ids": [...]}                   update the contact list
    client -> {"type": "nudge", "to": uid}                       ask an online user to re-check the cloud
    server -> {"type": "contact_hint"}                              (someone added/accepted you: refresh contacts)
    client -> {"type": "msg", "to": uid, "data": <opaque b64>, "ref": n}
    server -> {"type": "msg", "from": uid, "fp": ..., "data": ...}  (to the recipient)
           -> {"type": "undeliverable", "to": uid, "ref": n}       (offline or not a mutual contact)

Privacy rule: A sees B online (and can message B) only if A lists B AND B lists A, both
connected. Contact lists are asserted by the clients (from the cloud); the *mutual* rule means
listing someone does not reveal anything unless they list you too.

Authentication: the cloud token proves the account (and names the device fingerprint); the
signature over a fresh nonce proves possession of that device's private key.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import secrets
import ssl
import time
from dataclasses import dataclass, field

import jwt
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import WebSocket, WebSocketDisconnect

from .auth import RateLimiter
from .config import Settings

log = logging.getLogger("p2p.server.presence")

UID_RE = re.compile(r"^\d{9}$")
MAX_DATA = 96 * 1024


class AuthFailed(Exception):
    pass


def verify_signal_token(secret: str, token: str) -> dict:
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "uid", "fp"]})
    except jwt.PyJWTError as exc:
        raise AuthFailed(f"invalid token: {exc}") from None
    if claims.get("typ") != "signal" or not UID_RE.match(str(claims.get("uid"))):
        raise AuthFailed("wrong token type")
    return claims


def verify_device(cert_pem: str, fingerprint: str, nonce: bytes, sig_b64: str) -> None:
    try:
        der = ssl.PEM_cert_to_DER_cert(cert_pem)
        cert = x509.load_der_x509_certificate(der)
        sig = base64.b64decode(sig_b64)
    except Exception:
        raise AuthFailed("bad certificate") from None
    if hashlib.sha256(der).hexdigest() != fingerprint:
        raise AuthFailed("certificate does not match the token's device")
    key = cert.public_key()
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise AuthFailed("unsupported key type")
    try:
        key.verify(sig, b"p2pft-presence|" + nonce, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        raise AuthFailed("device signature invalid") from None


@dataclass(eq=False)
class Client:
    ws: WebSocket
    uid: str
    name: str
    fp: str
    cert_pem: str
    exp: float
    contacts: set = field(default_factory=set)
    closed: bool = False

    async def send(self, msg: dict) -> None:
        if self.closed:
            return
        try:
            await self.ws.send_text(json.dumps(msg, separators=(",", ":")))
        except Exception:
            self.closed = True


class PresenceHub:
    def __init__(self, settings: Settings):
        self.s = settings
        self.online: dict[str, Client] = {}
        self.lock = asyncio.Lock()

    @staticmethod
    def visible(a: Client, b: Client) -> bool:
        return b.uid in a.contacts and a.uid in b.contacts

    def _presence(self, c: Client, online: bool) -> dict:
        msg = {"type": "presence", "uid": c.uid, "online": online}
        if online:
            msg.update(name=c.name, fp=c.fp, cert=c.cert_pem)
        return msg

    async def _announce(self, c: Client, online: bool, previously_visible: set | None = None) -> None:
        """Tell every contact that can see ``c`` about its state; also those who just lost visibility."""
        targets = [o for o in self.online.values() if o is not c and self.visible(o, c)]
        for o in targets:
            await o.send(self._presence(c, online))
        if previously_visible:
            now = {o.uid for o in targets}
            for uid in previously_visible - now:
                o = self.online.get(uid)
                if o is not None:
                    await o.send(self._presence(c, False))

    async def _hint_new_listers(self, c: Client) -> None:
        """Users that ``c`` lists but who do not list ``c`` yet: they were probably just accepted as
        contacts in the cloud - tell them to refresh (carries no information about ``c``)."""
        for uid in c.contacts:
            o = self.online.get(uid)
            if o is not None and o is not c and c.uid not in o.contacts:
                await o.send({"type": "contact_hint"})

    async def _snapshot(self, c: Client) -> None:
        for uid in sorted(c.contacts):
            o = self.online.get(uid)
            await c.send(self._presence(o, True) if o is not None and self.visible(c, o)
                         else {"type": "presence", "uid": uid, "online": False})

    def _clean_ids(self, ids) -> set:
        return {str(i) for i in (ids or [])[:5000] if UID_RE.match(str(i))}

    async def handle(self, ws: WebSocket) -> None:
        await ws.accept()
        if not self.s.cloud_jwt_secret:
            await ws.send_text(json.dumps({"type": "error", "code": "disabled",
                                           "message": "presence requires P2P_CLOUD_JWT_SECRET"}))
            await ws.close(code=4403)
            return
        nonce = secrets.token_bytes(32)
        await ws.send_text(json.dumps({"type": "challenge", "nonce": base64.b64encode(nonce).decode()}))
        try:
            msg = json.loads(await asyncio.wait_for(ws.receive_text(), 15))
            if msg.get("type") != "auth":
                raise AuthFailed("expected auth")
            claims = verify_signal_token(self.s.cloud_jwt_secret, str(msg.get("token", "")))
            cert_pem = str(msg.get("cert", ""))
            verify_device(cert_pem, str(claims["fp"]), nonce, str(msg.get("sig", "")))
        except (AuthFailed, ValueError, asyncio.TimeoutError, WebSocketDisconnect) as exc:
            try:
                await ws.send_text(json.dumps({"type": "error", "code": "auth", "message": str(exc)}))
                await ws.close(code=4401)
            except Exception:
                pass
            return
        c = Client(ws=ws, uid=str(claims["uid"]), name=str(claims.get("name", ""))[:80], fp=str(claims["fp"]),
                   cert_pem=cert_pem, exp=float(claims["exp"]), contacts=self._clean_ids(msg.get("contacts")))
        async with self.lock:
            old = self.online.get(c.uid)
            self.online[c.uid] = c
        if old is not None:
            await old.send({"type": "replaced", "message": "signed in on another device"})
            old.closed = True
            try:
                await old.ws.close(code=4409)
            except Exception:
                pass
        await c.send({"type": "welcome", "uid": c.uid, "expires_at": c.exp})
        await self._snapshot(c)
        await self._announce(c, True)
        await self._hint_new_listers(c)
        log.info("online uid=%s contacts=%d total=%d", c.uid, len(c.contacts), len(self.online))
        limiter = RateLimiter(self.s.presence_msg_rate_per_min)
        try:
            while True:
                remaining = c.exp - time.time()
                if remaining <= 0:
                    await c.send({"type": "error", "code": "token_expired", "message": "signal token expired"})
                    break
                try:
                    raw = await asyncio.wait_for(ws.receive_text(), min(remaining, 60))
                except asyncio.TimeoutError:
                    continue
                if len(raw) > MAX_DATA + 1024:
                    await c.send({"type": "error", "code": "too_large", "message": "message too large"})
                    continue
                try:
                    m = json.loads(raw)
                    mtype = m.get("type")
                except (ValueError, AttributeError):
                    continue
                if mtype == "ping":
                    await c.send({"type": "pong"})
                elif mtype == "contacts":
                    before = {o.uid for o in self.online.values() if o is not c and self.visible(o, c)}
                    c.contacts = self._clean_ids(m.get("ids"))
                    await self._snapshot(c)
                    await self._announce(c, True, previously_visible=before)
                    await self._hint_new_listers(c)
                elif mtype == "nudge":
                    target = self.online.get(str(m.get("to", "")))
                    if target is not None and target is not c and limiter.allow("m"):
                        await target.send({"type": "contact_hint"})     # no reply: reveals nothing
                elif mtype == "msg":
                    if not limiter.allow("m"):
                        await c.send({"type": "error", "code": "rate_limited", "message": "slow down"})
                        continue
                    to = self.online.get(str(m.get("to", "")))
                    data = m.get("data")
                    if to is None or not self.visible(c, to) or not isinstance(data, str) or len(data) > MAX_DATA:
                        await c.send({"type": "undeliverable", "to": m.get("to"), "ref": m.get("ref")})
                        continue
                    await to.send({"type": "msg", "from": c.uid, "fp": c.fp, "data": data})
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("presence handler error")
        finally:
            c.closed = True
            async with self.lock:
                if self.online.get(c.uid) is c:
                    del self.online[c.uid]
                    gone = True
                else:
                    gone = False
            if gone:
                await self._announce(c, False)
            try:
                await ws.close()
            except Exception:
                pass

    def stats(self) -> dict:
        return {"online": len(self.online)}
