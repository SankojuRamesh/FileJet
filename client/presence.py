"""Presence connection (who of my contacts is online) and encrypted RPC between contacts.

``PresenceClient`` keeps one authenticated WebSocket to the signaling server's /ws/presence,
reconnecting forever with back-off. ``Messenger`` layers end-to-end encrypted request /
response messages on top (used for browsing shared folders, permission-checked operations
and negotiating P2P transfers). File data never goes through here.
"""
from __future__ import annotations

import base64
import json
import logging
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from websockets.sync.client import connect

from .e2e import E2E, E2EError
from .identity import Identity

log = logging.getLogger("p2p.presence")


def presence_url(signaling_url: str) -> str:
    base = signaling_url.rstrip("/")
    if base.endswith("/ws"):
        base = base[:-3]
    return base + "/ws/presence"


class PresenceClient:
    def __init__(self, url: str, identity: Identity, token_provider, contacts_provider, on_event):
        self.url = url
        self.identity = identity
        self.token_provider = token_provider
        self.contacts_provider = contacts_provider
        self.on_event = on_event
        self.online: dict[str, dict] = {}         # uid -> {"name", "fp", "cert"}
        self.connected = False
        self.last_error: str | None = None
        self.replaced = False                   # another app signed in with the same account
        self._ws = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="presence", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _emit(self, *event) -> None:
        try:
            self.on_event(*event)
        except Exception:
            log.exception("presence event handler failed")

    def _send(self, obj: dict) -> bool:
        ws = self._ws
        if ws is None:
            return False
        try:
            with self._send_lock:
                ws.send(json.dumps(obj, separators=(",", ":")))
            return True
        except Exception:
            return False

    def restart(self) -> None:
        """Reconnect after being replaced by another sign-in of the same account."""
        self.replaced = False
        self._stop.clear()
        if self._thread is None or not self._thread.is_alive():
            self.start()

    def nudge(self, uid: str) -> None:
        self._send({"type": "nudge", "to": uid})

    def update_contacts(self, ids) -> None:
        self._send({"type": "contacts", "ids": sorted(ids)})

    def send_msg(self, to: str, data: str, ref: str) -> bool:
        return self._send({"type": "msg", "to": to, "data": data, "ref": ref})

    def _run(self) -> None:
        delay = 1.0
        logged = None
        while not self._stop.is_set():
            try:
                self._session()
                delay = 1.0
            except Exception as exc:
                self.last_error = str(exc)
                if str(exc) != logged and not self._stop.is_set():   # once per distinct problem; quiet on stop
                    logged = str(exc)
                    log.warning("presence: cannot connect to %s: %s", self.url, exc)
            finally:
                self._ws = None
                if self.connected or self.online:
                    self.connected = False
                    gone, self.online = list(self.online), {}
                    for uid in gone:
                        self._emit("presence", uid, None)
                    self._emit("disconnected", self.last_error)
            if self.replaced:
                return                            # do not fight the other app for the account
            self._stop.wait(delay)
            delay = min(delay * 2, 30.0)

    def _session(self) -> None:
        token = self.token_provider()
        with connect(self.url, open_timeout=10, max_size=1 << 20, ping_interval=20, ping_timeout=20) as ws:
            challenge = json.loads(ws.recv(timeout=15))
            if challenge.get("type") != "challenge":
                raise ConnectionError(challenge.get("message", "unexpected presence greeting"))
            nonce = base64.b64decode(challenge["nonce"])
            sig = base64.b64encode(self.identity.sign(b"p2pft-presence|" + nonce)).decode()
            ws.send(json.dumps({"type": "auth", "token": token, "cert": self.identity.cert_pem, "sig": sig,
                                "contacts": sorted(self.contacts_provider())}))
            welcome = json.loads(ws.recv(timeout=15))
            if welcome.get("type") != "welcome":
                raise ConnectionError(welcome.get("message", "presence authentication failed"))
            self._ws = ws
            self.connected, self.last_error = True, None
            self._emit("connected", welcome)
            while not self._stop.is_set():
                try:
                    raw = ws.recv(timeout=1.0)
                except TimeoutError:
                    continue
                m = json.loads(raw)
                t = m.get("type")
                if t == "presence":
                    uid = str(m.get("uid"))
                    if m.get("online"):
                        info = {"name": m.get("name"), "fp": m.get("fp"), "cert": m.get("cert")}
                        self.online[uid] = info
                        self._emit("presence", uid, info)
                    else:
                        self.online.pop(uid, None)
                        self._emit("presence", uid, None)
                elif t == "msg":
                    self._emit("msg", str(m.get("from")), str(m.get("fp")), str(m.get("data")))
                elif t == "undeliverable":
                    self._emit("undeliverable", str(m.get("to")), m.get("ref"))
                elif t == "replaced":
                    self.replaced = True
                    self._emit("replaced", m.get("message"))
                    raise ConnectionError("this account signed in on another device or app")
                elif t == "contact_hint":
                    self._emit("hint")
                elif t == "error":
                    raise ConnectionError(m.get("message", "presence error"))


class RpcError(Exception):
    pass


class Messenger:
    """Encrypted request/response between contacts over the presence connection."""

    def __init__(self, presence: PresenceClient, e2e: E2E, trusted_cert, handler, workers: int = 8):
        self.presence = presence
        self.e2e = e2e
        self.trusted_cert = trusted_cert       # (uid, fp) -> cert PEM if that device is a trusted contact device
        self.handler = handler                 # (from_uid, method, params) -> result
        self._pending: dict[str, tuple[threading.Event, dict]] = {}
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rpc")

    def _peer_cert(self, uid: str) -> str:
        info = self.presence.online.get(uid)
        if not info:
            raise RpcError("contact is offline")
        cert = self.trusted_cert(uid, info.get("fp"))
        if cert is None:
            raise RpcError("contact's device is not trusted (not registered in the cloud)")
        return cert

    def call(self, uid: str, method: str, params: dict | None = None, timeout: float = 30.0):
        cert = self._peer_cert(uid)
        rid = secrets.token_hex(8)
        ev, box = threading.Event(), {}
        self._pending[rid] = (ev, box)
        try:
            data = self.e2e.seal(uid, cert, {"k": "req", "id": rid, "m": method, "p": params or {}})
            if not self.presence.send_msg(uid, data, rid):
                raise RpcError("not connected to the server")
            if not ev.wait(timeout):
                raise RpcError(f"{method}: no answer from contact")
        finally:
            self._pending.pop(rid, None)
        if "undeliverable" in box:
            raise RpcError("contact is offline")
        if not box.get("ok"):
            raise RpcError(box.get("e") or "request failed")
        return box.get("r")

    def on_undeliverable(self, to: str, ref) -> None:
        entry = self._pending.get(str(ref))
        if entry:
            entry[1]["undeliverable"] = True
            entry[0].set()

    def on_msg(self, from_uid: str, fp: str, data: str) -> None:
        cert = self.trusted_cert(from_uid, fp)
        if cert is None:
            log.warning("dropped message from untrusted device of %s", from_uid)
            return
        try:
            body = self.e2e.open(from_uid, cert, data)
        except E2EError as exc:
            log.warning("dropped message from %s: %s", from_uid, exc)
            return
        kind = body.get("k")
        if kind == "res":
            entry = self._pending.get(str(body.get("id")))
            if entry:
                entry[1].update(body)
                entry[0].set()
        elif kind == "req":
            self._pool.submit(self._serve, from_uid, cert, body)

    def _serve(self, from_uid: str, cert: str, body: dict) -> None:
        rid = body.get("id")
        try:
            result = self.handler(from_uid, str(body.get("m")), body.get("p") or {})
            resp = {"k": "res", "id": rid, "ok": True, "r": result}
        except PermissionError as exc:
            resp = {"k": "res", "id": rid, "ok": False, "e": f"permission denied: {exc}"}
        except Exception as exc:
            log.info("rpc %s from %s failed: %s", body.get("m"), from_uid, exc)
            resp = {"k": "res", "id": rid, "ok": False, "e": str(exc)}
        try:
            self.presence.send_msg(from_uid, self.e2e.seal(from_uid, cert, resp), f"r{rid}")
        except Exception:
            pass
