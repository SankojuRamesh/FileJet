"""AppCore - everything the Admin and Client desktop apps do, without any UI.

    cloud (REST)  : login, devices, users (added by ID), folders / members / permissions (metadata),
                    subscription, transfer + activity metadata
    presence (WS) : which connected users are online; encrypted request/response between them
    engines (P2P) : the actual file data, directly between the two computers (never via the cloud)

Admin : creates folders on their own computer/storage (or opens existing ones), adds users by ID,
        shares a folder with one or many users, each with their own permissions
        (View & download / Upload / Edit / Delete). The admin's app enforces every permission and
        keeps a log of every received file (who, when, size, form values) next to its data.
Client: sees the folders shared with them and works in them as permitted. Files they send go into
        a persistent OUTBOX on their own computer and are delivered directly to the admin's
        computer as soon as the admin is online (also after either app restarts).
The cloud only ever receives metadata (names, sizes, times, status) - never file contents.
"""
from __future__ import annotations

import base64
import dataclasses
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .cloud_api import CloudClient, CloudError
from .config import ClientConfig
from .e2e import E2E
from .presence import Messenger, PresenceClient, RpcError, presence_url
from .receiver import ReceiverEngine
from .sender import SenderEngine
from . import thumbs
from .shares import (ShareService, ShareStore, check_upload_rules, clean_rel, free_space, safe_child,
                     write_metadata_sidecar)
from .transfer_manager import TransferManager

log = logging.getLogger("p2p.core")

FINAL = ("completed", "failed", "cancelled", "paused")
FINAL_JOB = ("completed", "failed", "cancelled")


@dataclass
class JobItem:
    rel: str
    size: int
    local: str = ""
    state: str = "queued"
    error: str | None = None
    engine: object = None
    item_id: str = ""
    transfer_id: str = ""
    thumb: bytes | None = None


@dataclass
class Job:
    kind: str                     # "send" | "receive" | "download" | "upload"
    peer_uid: str
    peer_name: str
    title: str
    items: list = field(default_factory=list)
    share_name: str = ""
    job_id: str = field(default_factory=lambda: secrets.token_hex(8))
    state: str = "queued"
    error: str | None = None
    created: float = field(default_factory=time.time)
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    expected_bytes: int = 0          # whole batch, told by the other side (files may not have started yet)
    expected_files: int = 0
    finished_at: float | None = None

    def snapshot(self) -> dict:
        total = max(sum(i.size for i in self.items), self.expected_bytes)
        done = 0
        speed = 0.0
        current = None
        conn = None
        for i in self.items:
            if i.state == "completed":
                done += i.size
            elif i.engine is not None:
                s = i.engine.snapshot()
                done += s["transferred"] or 0
                if s["state"] == "active":
                    speed += s["speed"] or 0
                    current = i.rel
                conn = s["connection"] or conn
        finished = sum(1 for i in self.items if i.state == "completed")
        failed = sum(1 for i in self.items if i.state in ("failed", "cancelled"))
        done = min(done, total)
        if self.state in FINAL_JOB and self.finished_at is None:
            self.finished_at = time.time()
        elapsed = (self.finished_at or time.time()) - self.created
        return {"job_id": self.job_id, "kind": self.kind, "peer_uid": self.peer_uid, "peer_name": self.peer_name,
                "title": self.title, "share_name": self.share_name, "state": self.state, "error": self.error,
                "files": max(len(self.items), self.expected_files), "files_done": finished, "files_failed": failed,
                "total": total, "remaining": total - done, "elapsed": elapsed,
                "eta": (total - done) / speed if speed > 0 and total > done else None,
                "avg_speed": done / elapsed if elapsed > 0 and self.state in FINAL_JOB else None,
                "transferred": done, "progress": (done / total * 100) if total else
                (100.0 if self.state == "completed" else 0.0), "speed": speed, "current": current,
                "connection": conn, "created": self.created,
                "thumb": next((i.thumb for i in self.items if i.thumb), None),
                "is_folder": len(self.items) > 1 or any("/" in i.rel for i in self.items)}


class CloudReporter(threading.Thread):
    """Sends each engine's status to the cloud (metadata only): on state change and every 5 s."""

    def __init__(self, core: "AppCore"):
        super().__init__(name="cloud-reporter", daemon=True)
        self.core = core
        self.sent: dict[str, tuple] = {}
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.wait(2.0):
            try:
                self.tick()
            except Exception as exc:
                log.debug("report tick failed: %s", exc)

    def tick(self, force: bool = False) -> None:
        if not self.core.cloud.logged_in or not self.core.cloud.device_fp:
            return
        for engine in list(self.core.manager.jobs.values()):
            s = engine.snapshot()
            tid = s["transfer_id"]
            if not tid:
                continue
            key = (tid, s["role"])
            last = self.sent.get(key)
            if last and last[0] in FINAL and last[0] == s["state"]:
                continue
            if not force and last and last[0] == s["state"] and time.monotonic() - last[1] < 5.0:
                continue
            meta = s.get("meta") or {}
            record = {
                "transfer_id": tid, "role": s["role"], "state": s["state"], "file_name": s["file_name"],
                "file_size": s["file_size"], "bytes_transferred": s["transferred"], "speed": s["speed"],
                "avg_speed": s["average"], "peak_speed": s["peak"], "connection_type": s["connection"],
                "peer_fingerprint": s.get("peer_fp"), "file_hash": s.get("file_hash"), "error": s["error"],
                "direction": meta.get("direction", "code"), "share_name": meta.get("share_name", ""),
                "relative_path": meta.get("relative_path", ""), "job_id": meta.get("job_id", ""),
                "initiator": bool(meta.get("initiator")), "folder_id": meta.get("folder_id", ""),
                "job_total": meta.get("job_total", 0),
            }
            try:
                self.core.cloud.report(record)
                self.sent[key] = (s["state"], time.monotonic())
            except CloudError as exc:
                log.debug("report %s failed: %s", tid[:8], exc)


class AppCore:
    def __init__(self, cfg: ClientConfig, cloud_url: str, app: str = "admin", on_event=None):
        self.cfg = cfg
        self.app = app
        self.manager = TransferManager(cfg)
        self.db, self.identity = self.manager.db, self.manager.identity
        self.cloud = CloudClient(cloud_url, cfg.data_dir, app=app)
        self.store = ShareStore(self.db)
        self.shares = ShareService(self.store, self)
        self.on_event = on_event or (lambda kind, data=None: None)
        self.me: dict | None = None
        self.contacts: dict[str, dict] = {}
        self.overview: dict = {"owned": [], "member": [], "clients": [], "admins": [], "groups": []}
        self.presence: PresenceClient | None = None
        self.messenger: Messenger | None = None
        self.e2e: E2E | None = None
        self.jobs: dict[str, Job] = {}
        self._outbox_kick = threading.Event()
        self._served_lock = threading.Lock()
        self._chat_lock = threading.Lock()
        self._delivering: set[str] = set()
        self.server_override = False
        self.reporter = CloudReporter(self)
        self._hint = threading.Event()
        self._bg_stop = threading.Event()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ session
    def emit(self, kind: str, data=None) -> None:
        try:
            self.on_event(kind, data)
        except Exception:
            log.exception("event handler failed")

    def restore_session(self) -> bool:
        if not self.cloud.logged_in:
            return False
        try:
            self._after_login()
            return True
        except CloudError as exc:
            if exc.status == 401:
                return False
            raise

    def login(self, username: str, password: str) -> dict:
        self.cloud.login(username, password)
        self._after_login()
        return self.me

    def register(self, username, email, password, display_name="") -> dict:
        self.cloud.register(username, email, password, display_name)
        self._after_login()
        return self.me

    def _after_login(self) -> None:
        self.me = self.cloud.me()
        self.cloud.register_device(self.identity)
        self.cloud.signal_token()
        if self.cloud.signaling_url and not self.server_override:
            self.cfg.server_url = self.cloud.signaling_url
        self.cfg.token_provider = self.cloud.signal_token
        self.e2e = E2E(self.identity, self.me["public_id"])
        self.refresh_contacts()
        self.presence = PresenceClient(presence_url(self.cfg.server_url), self.identity, self.cloud.signal_token,
                                       lambda: list(self.contacts), self._on_presence)
        self.messenger = Messenger(self.presence, self.e2e, self.trusted_cert, self._handle_rpc)
        self.presence.start()
        if not self.reporter.is_alive():
            self.reporter.start()
        threading.Thread(target=self._background, name="cloud-sync", daemon=True).start()
        threading.Thread(target=self._hint_worker, name="hint-refresh", daemon=True).start()
        threading.Thread(target=self._outbox_worker, name="outbox", daemon=True).start()
        self.emit("logged_in", self.me)

    def logout(self) -> None:
        self.shutdown(pause=True)
        self.cloud.logout()
        self.me = None
        self.contacts = {}
        self.emit("logged_out")

    def shutdown(self, pause: bool = True) -> None:
        self._bg_stop.set()
        for job in self.jobs.values():
            job.cancel.set() if not pause else None
        self.manager.stop_all()
        if self.presence:
            self.presence.stop()
        try:
            self.reporter.tick(force=True)
        except Exception:
            pass
        self.reporter.stop.set()

    def _hint_worker(self) -> None:
        """Refresh from the cloud whenever someone signals a change; bursts collapse into one refresh,
        and a signal arriving during a refresh triggers one more (nothing is lost)."""
        while not self._bg_stop.is_set():
            if not self._hint.wait(1.0):
                continue
            self._hint.clear()
            self._refresh_quietly()

    def _background(self) -> None:
        while not self._bg_stop.wait(15):
            try:
                self.refresh_contacts()
            except CloudError as exc:
                log.debug("contact refresh failed: %s", exc)

    # ------------------------------------------------------ connections (users)
    def refresh_contacts(self) -> None:
        """Connections = admins who added me + users I added (from the cloud), plus folder metadata."""
        lst = self.cloud.connections()
        new = {c["public_id"]: {"uid": c["public_id"], "username": c["username"], "email": c.get("email", ""),
                                "name": c["display_name"] or c["username"],
                                "organization": c.get("organization", ""),
                                "devices": {d["fingerprint"]: d["cert_pem"] for d in c["devices"]}} for c in lst}
        changed = set(new) != set(self.contacts)
        self.contacts = new
        self.overview = self.cloud.overview()
        self._sync_members()
        if changed and self.presence is not None:
            self.presence.update_contacts(list(new))
        self.emit("contacts")

    def _sync_members(self) -> None:
        """Member status (invited -> opened) and user details come from the cloud; the local copy
        is what the admin's app enforces."""
        local = {f["folder_id"] for f in self.store.list()}
        for f in self.overview.get("owned", []):
            if f["folder_id"] not in local:
                continue
            cloud_uids = set()
            for m in f.get("members", []):
                self.store.upsert_member(f["folder_id"], m)
                cloud_uids.add(m["user"]["public_id"])
            for m in self.store.members(f["folder_id"]):
                if m["uid"] not in cloud_uids:
                    self.store.remove_member(f["folder_id"], m["uid"])
        self.enforce_permissions()

    def refresh_member_status(self) -> None:
        """Re-read member status from the cloud now (throttled): used when an 'invited' user makes a request."""
        now = time.monotonic()
        if now - getattr(self, "_last_member_refresh", 0.0) < 1.0:
            return
        self._last_member_refresh = now
        try:
            self.overview = self.cloud.overview()
            self._sync_members()
        except CloudError as exc:
            log.debug("member refresh failed: %s", exc)

    def trusted_cert(self, uid: str, fp: str | None) -> str | None:
        c = self.contacts.get(uid)
        return c["devices"].get(fp) if c and fp else None

    def is_online(self, uid: str) -> bool:
        info = self.presence.online.get(uid) if self.presence else None
        return bool(info) and self.trusted_cert(uid, info.get("fp")) is not None

    def _nudge(self, uid: str | None) -> None:
        if uid and self.presence is not None:
            self.presence.nudge(uid)              # their app refreshes from the cloud right away

    # ---- admin: users ----
    def add_user(self, query: str, note: str = "") -> dict:
        u = self.cloud.add_user(query, note)
        self.refresh_contacts()
        self._nudge(u["public_id"])
        return u

    def remove_user(self, uid: str) -> None:
        self.cloud.remove_user(uid)
        self.store.remove_user_everywhere(uid)
        self.enforce_permissions()
        self.refresh_contacts()
        self._nudge(uid)

    def create_group(self, name: str) -> dict:
        g = self.cloud.create_group(name)
        self.refresh_contacts()
        return g

    def update_group(self, group_id: int, add=(), remove=()) -> dict:
        g = self.cloud.update_group(group_id, add, remove)
        self.refresh_contacts()
        return g

    def delete_group(self, group_id: int) -> None:
        self.cloud.delete_group(group_id)
        self.refresh_contacts()

    # ---- client: open a folder by its ID ----
    def join_folder(self, folder_id: str) -> dict:
        f = self.cloud.join_folder(folder_id)
        self.refresh_contacts()
        self._nudge(f["owner"]["public_id"])      # the admin's app learns "opened" immediately
        return f

    def my_folders(self) -> list[dict]:
        return self.overview.get("member", [])

    def _on_presence(self, kind, *args) -> None:
        if kind == "presence":
            uid, info = args
            if info and self.trusted_cert(uid, info.get("fp")) is None and uid in self.contacts:
                threading.Thread(target=self._refresh_quietly, daemon=True).start()   # new device of a contact?
            self.emit("presence", {"uid": uid, "online": self.is_online(uid)})
            if info:
                self._outbox_kick.set()                  # the admin came online: deliver waiting files
                threading.Thread(target=self._flush_chat, args=(uid,), daemon=True).start()   # waiting messages
            elif self.presence is not None and self.presence.connected:
                threading.Thread(target=self._peer_went_offline, args=(uid,), daemon=True).start()
        elif kind == "msg":
            self.messenger.on_msg(*args)
        elif kind == "undeliverable":
            self.messenger.on_undeliverable(*args)
        elif kind == "hint":
            self._hint.set()                       # coalesced by the refresher thread, never dropped
        elif kind in ("connected", "disconnected", "replaced"):
            self.emit("server", {"state": kind, "detail": args[0] if args else None})

    def connection_problem(self) -> str | None:
        """Human readable reason why the app is offline (None when connected)."""
        p = self.presence
        if p is None or p.connected:
            return None
        err = (p.last_error or "").lower()
        url = self.cfg.server_url
        if p.replaced or "another device" in err:
            return ("This account is signed in in another app or on another computer, so this app went offline. "
                    "Use a different account in each app (one for the Admin, one for the Client).")
        if "10061" in err or "refused" in err or "connect call failed" in err or "unreachable" in err:
            return (f"The signaling server is not running or not reachable at {url}. Start it with "
                    "'python -m server.main' (P2P_CLOUD_JWT_SECRET set, see README), or run 'python run_dev.py' "
                    "which starts the cloud and the signaling server together.")
        if "requires p2p_cloud_jwt_secret" in err:
            return ("The signaling server was started without P2P_CLOUD_JWT_SECRET, so it cannot check sign-ins. "
                    "Restart it with the same secret as the cloud (or use 'python run_dev.py').")
        if "invalid token" in err or "signature" in err or "authentication" in err:
            return ("The signaling server rejected this app's sign-in: its P2P_CLOUD_JWT_SECRET differs from the "
                    "cloud's. Use the same secret for both.")
        if not err:
            return "Connecting to the signaling server..."
        return f"Not connected to the signaling server at {url}: {p.last_error}"

    def _refresh_quietly(self) -> None:
        try:
            self.refresh_contacts()
        except CloudError:
            pass

    # ------------------------------------------------------------ engines
    def _job_cfg(self, **overrides) -> ClientConfig:
        return dataclasses.replace(self.cfg, **overrides)

    def _peer(self, uid: str) -> tuple[str, str]:
        info = self.presence.online.get(uid) if self.presence else None
        if not info:
            raise RpcError("contact is offline")
        cert = self.trusted_cert(uid, info.get("fp"))
        if cert is None:
            raise RpcError("contact's device is not trusted")
        return info["fp"], cert

    def _start(self, engine) -> None:
        self.manager._start(engine)

    def _stop_existing(self, transfer_id: str, role: str, timeout: float = 15.0) -> None:
        """Before resuming a transfer, stop an older engine for it that may still be waiting to reconnect
        (otherwise two engines of the same side would compete for the same file and connection)."""
        for e in list(self.manager.jobs.values()):
            if e.transfer_id == transfer_id and e.role == role and not e.finished.is_set():
                e.request_stop(cancel=False)
                e.finished.wait(timeout)

    def call(self, uid: str, method: str, params: dict | None = None, timeout: float = 30.0):
        if self.messenger is None:
            raise RpcError("not signed in")
        return self.messenger.call(uid, method, params, timeout)

    def _handle_rpc(self, uid: str, method: str, p: dict):
        if uid not in self.contacts:
            raise PermissionError("you are not connected to this user")
        if method.startswith(("shares.", "fs.")):
            return self.shares.handle(uid, method, p)
        if method == "chat.msg":
            return self._on_chat(uid, p)
        if method == "chat.read":
            ids = [str(x)[:32] for x in (p.get("ids") or [])][:500]
            self.store.chat_set_state(uid, True, ids, "read")
            self.emit("chat", {"uid": uid})
            return {"ok": True}
        raise ValueError(f"unknown request {method!r}")

    # ------------------------------------------------ chat (end-to-end encrypted, directly between the apps)
    CHAT_MAX = 4000

    def _on_chat(self, uid: str, p: dict) -> dict:
        text = str(p.get("text") or "")[:self.CHAT_MAX]
        mid = str(p.get("id") or "")[:32]
        if not text.strip() or not mid:
            raise ValueError("empty message")
        ts = float(p.get("ts") or time.time())
        ts = min(ts, time.time() + 60)
        if self.store.chat_add(mid, uid, False, text, ts, "new"):
            self.emit("chat", {"uid": uid, "incoming": True, "text": text,
                               "from": self.contacts.get(uid, {}).get("name", uid)})
        return {"ok": True}

    def send_chat(self, uid: str, text: str) -> dict:
        """Send a chat message. If the other person is offline it waits here and is delivered when they are
        online. Messages are never stored in the cloud."""
        if uid not in self.contacts:
            raise PermissionError("you can chat only with your users")
        text = text.strip()[:self.CHAT_MAX]
        if not text:
            raise ValueError("empty message")
        msg = {"msg_id": secrets.token_hex(8), "peer_uid": uid, "outgoing": 1, "text": text, "ts": time.time(),
               "state": "queued"}
        self.store.chat_add(msg["msg_id"], uid, True, text, msg["ts"], "queued")
        self.emit("chat", {"uid": uid})
        threading.Thread(target=self._flush_chat, args=(uid,), daemon=True).start()
        return msg

    def _flush_chat(self, only_uid: str | None = None) -> None:
        with self._chat_lock:
            for m in self.store.chat_queued():
                uid = m["peer_uid"]
                if (only_uid and uid != only_uid) or not self.is_online(uid) or uid not in self.contacts:
                    continue
                try:
                    self.call(uid, "chat.msg", {"id": m["msg_id"], "text": m["text"], "ts": m["ts"]}, timeout=15)
                except (RpcError, OSError, ConnectionError, TimeoutError) as exc:
                    log.debug("chat to %s not delivered yet: %s", uid, exc)
                    continue
                self.store.chat_set_state(uid, True, [m["msg_id"]], "delivered")
                self.emit("chat", {"uid": uid})

    def chat_history(self, uid: str) -> list[dict]:
        return self.store.chat_history(uid)

    def chat_summary(self) -> dict:
        return self.store.chat_summary()

    def mark_chat_read(self, uid: str) -> None:
        """I opened the conversation: mark incoming messages read and tell the sender (read receipts)."""
        ids = [m["msg_id"] for m in self.store.chat_history(uid) if not m["outgoing"] and m["state"] == "new"]
        if not ids:
            return
        self.store.chat_set_state(uid, False, ids, "read")
        self.emit("chat", {"uid": uid})

        def tell():
            try:
                self.call(uid, "chat.read", {"ids": ids}, timeout=10)
            except (RpcError, OSError, ConnectionError, TimeoutError):
                pass
        if self.is_online(uid):
            threading.Thread(target=tell, daemon=True).start()

    # ------------------------------------------------ admin side of folders
    def _when_done(self, engine, fn) -> None:
        def watch():
            engine.finished.wait()
            if engine.state == "completed":
                try:
                    fn(engine)
                except Exception as exc:
                    log.info("post-transfer step failed: %s", exc)
        threading.Thread(target=watch, daemon=True).start()

    def log_activity(self, folder: dict, uid: str, action: str, path="", detail: str = "") -> None:
        """Audit event to the cloud (metadata only), in the background."""
        def send():
            try:
                self.cloud.log_activity(folder["folder_id"], action, str(path), detail, actor=uid)
            except CloudError as exc:
                log.debug("activity log failed: %s", exc)
        threading.Thread(target=send, daemon=True).start()

    def _track_served(self, kind: str, uid: str, folder: dict, rel: str, size: int, engine, p: dict,
                      tid: str | None = None, thumb: bytes | None = None) -> None:
        """Show transfers other users start in my folders on my Transfers page (one job per batch)."""
        total = max(1, int(p.get("job_total") or 1))
        tid = str(tid or engine.transfer_id or secrets.token_hex(8))
        key = f"{kind}-{uid}-{str(p.get('job_id') or tid)[:32]}"
        with self._served_lock:
            job = self.jobs.get(key)
            if job is None:
                title = str(p.get("title") or "")[:120] or rel.split("/")[-1]
                job = Job(kind, uid, self.contacts.get(uid, {}).get("name", uid), title, [],
                          share_name=folder["name"], job_id=key)
                self.jobs[key] = job
            try:
                job.expected_bytes = max(job.expected_bytes, min(int(p.get("job_bytes") or 0), 1 << 50))
            except (TypeError, ValueError):
                pass
            job.expected_files = total
            item = JobItem(rel=rel, size=int(size), engine=engine, state="running", transfer_id=tid, thumb=thumb)
            job.items = [i for i in job.items if i.transfer_id != tid] + [item]
            job.state, job.error, job.finished_at = "running", None, None
            job.done.clear()
        self.emit("job", key)

        def watch():
            engine.finished.wait()
            item.state = engine.state if engine.state in FINAL else "failed"
            item.error = engine.error
            with self._served_lock:
                states = [i.state for i in job.items]
                if any(st in ("failed", "cancelled", "paused") for st in states) and "running" not in states:
                    job.state = "failed" if "failed" in states else "cancelled"
                    job.error = next((i.error for i in job.items if i.error), None)
                    job.done.set()
                elif states.count("completed") >= total:
                    job.state = "completed"
                    job.done.set()
            self.emit("job", key)
        threading.Thread(target=watch, daemon=True).start()

    def serve_download(self, uid, folder, rel, target: Path, resume_tid=None, p: dict | None = None) -> dict:
        fp, cert = self._peer(uid)
        p = p or {}
        meta = {"direction": "download", "share_name": folder["name"], "share_uid": folder["folder_id"],
                "folder_id": folder["folder_id"], "relative_path": rel, "peer_uid": uid, "served": True,
                "need": "read", "job_id": str(p.get("job_id", ""))[:32], "job_total": int(p.get("job_total") or 0)}
        if resume_tid:
            rec = self.db.get(str(resume_tid), "sender")
            ct = self.store.contact_transfer(str(resume_tid), "sender")
            if (not rec or not ct or ct["peer_uid"] != uid or Path(rec["file_path"]).resolve() != target
                    or rec["status"] in ("completed", "cancelled")):
                raise PermissionError("this transfer cannot be resumed")
            self._stop_existing(rec["transfer_id"], "sender")
            engine = SenderEngine(self._job_cfg(), self.db, self.identity, transfer_id=rec["transfer_id"],
                                  peer_fp=fp, meta=meta)
            self._start(engine)
            self._track_served("send", uid, folder, rel, rec["file_size"], engine, p)
            return {"resume": True, "transfer_id": rec["transfer_id"]}
        nonce = secrets.token_hex(16)
        engine = SenderEngine(self._job_cfg(), self.db, self.identity, path=target, peer_fp=fp, meta=meta,
                              rendezvous=self.e2e.rendezvous(uid, cert, nonce))
        self.store.remember_transfer(engine.transfer_id, "sender", uid, "download", folder["folder_id"], rel,
                                     str(target))
        self._start(engine)
        self._track_served("send", uid, folder, rel, target.stat().st_size, engine, p)
        self._when_done(engine, lambda e: self.log_activity(folder, uid, "download", rel))
        return {"nonce": nonce, "name": target.name, "size": target.stat().st_size, "transfer_id": engine.transfer_id}

    def serve_upload(self, uid, folder, rel_dir, dest: Path, name: str, size: int, resume_tid=None,
                     transfer_id: str | None = None, fields: dict | None = None, p: dict | None = None) -> dict:
        fp, cert = self._peer(uid)
        p = p or {}
        rel = f"{rel_dir}/{name}".strip("/")
        thumb = None
        if isinstance(p.get("thumb"), str) and len(p["thumb"]) <= thumbs.MAX_BYTES * 4 // 3 + 8:
            try:
                thumb = base64.b64decode(p["thumb"], validate=True)
            except ValueError:
                thumb = None
            thumb = thumb if thumbs.valid(thumb) else None
        meta = {"direction": "upload", "share_name": folder["name"], "share_uid": folder["folder_id"],
                "folder_id": folder["folder_id"], "relative_path": rel, "peer_uid": uid, "served": True,
                "need": "upload", "job_id": str(p.get("job_id", ""))[:32], "job_total": int(p.get("job_total") or 0)}
        who = self.contacts.get(uid, {})

        def finished(engine):
            if fields:
                write_metadata_sidecar(Path(engine.final_path), fields, who.get("username", uid))
            self.store.received_add(engine.transfer_id, folder["folder_id"], rel, size, uid,
                                    who.get("name", uid), engine.final_path, fields, thumb)
            self.log_activity(folder, uid, "upload", rel, f"{size} bytes")
            self.emit("received", {"folder_id": folder["folder_id"], "name": name, "from": who.get("name", uid)})

        if resume_tid:
            rec = self.db.get(str(resume_tid), "receiver")
            ct = self.store.contact_transfer(str(resume_tid), "receiver")
            if (not rec or not ct or ct["peer_uid"] != uid or Path(rec["part_path"]).resolve().parent != dest
                    or rec["status"] in ("completed", "cancelled")):
                raise PermissionError("this transfer cannot be resumed")
            self._stop_existing(str(resume_tid), "receiver")
            rec = self.db.get(str(resume_tid), "receiver")
            if rec["status"] in ("completed", "cancelled"):
                raise PermissionError("this transfer cannot be resumed")
            engine = ReceiverEngine(self._job_cfg(dest_dir=dest), self.db, self.identity,
                                    transfer_id=rec["transfer_id"], peer_fp=fp, meta=meta)
            self._start(engine)
            self._track_served("receive", uid, folder, rel, size, engine, p, tid=str(resume_tid), thumb=thumb)
            self._when_done(engine, finished)
            return {"resume": True}
        res = self._receive_via_contact(uid, fp, cert, dest, name, size, transfer_id, meta,
                                        remember=("upload", folder["folder_id"], rel_dir))
        self._track_served("receive", uid, folder, rel, size, res["engine"], p, tid=str(transfer_id), thumb=thumb)
        self._when_done(res["engine"], finished)
        return {"nonce": res["nonce"]}

    def _receive_via_contact(self, uid, fp, cert, dest, name, size, transfer_id, meta, remember=None) -> dict:
        if not transfer_id or not all(c in "0123456789abcdef" for c in str(transfer_id)):
            raise ValueError("transfer_id required")
        nonce = secrets.token_hex(16)
        engine = ReceiverEngine(self._job_cfg(dest_dir=dest), self.db, self.identity, peer_fp=fp, meta=meta,
                                rendezvous=self.e2e.rendezvous(uid, cert, nonce),
                                expect=(name, int(size), str(transfer_id)))
        if remember:
            self.store.remember_transfer(str(transfer_id), "receiver", uid, remember[0], remember[1], remember[2],
                                         str(dest))
        self._start(engine)
        return {"nonce": nonce, "engine": engine}

    # ---- admin: folders & permissions ----
    def create_folder(self, name: str, path: Path, create: bool = False, settings: dict | None = None) -> str:
        path = Path(path)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise ValueError(f"not a folder: {path}")
        f = self.cloud.create_folder(name, settings)            # unique folder ID from the cloud
        self.store.create(f["folder_id"], f["name"], path, f)
        self.refresh_contacts()
        return f["folder_id"]

    def update_folder(self, folder_id: str, data: dict) -> dict:
        f = self.cloud.update_folder(folder_id, data)
        self.store.update_settings(folder_id, f)
        self.emit("contacts")
        return f

    def delete_folder(self, folder_id: str) -> None:
        members = [m["uid"] for m in self.store.members(folder_id)]
        try:
            self.cloud.delete_folder(folder_id)
        except CloudError as exc:
            if exc.status != 404:
                raise
        self.store.delete(folder_id)
        self.enforce_permissions()
        self.refresh_contacts()
        for uid in members:
            self._nudge(uid)

    def set_member(self, folder_id: str, uid: str, role: str | None = None, perms: dict | None = None,
                   expires_at: str | None = None) -> dict:
        data = dict(perms or {})
        if role:
            data["role"] = role
        if expires_at is not None:
            data["expires_at"] = expires_at
        m = self.cloud.set_member(folder_id, uid, data)
        self.store.upsert_member(folder_id, m)
        self.enforce_permissions()
        self.emit("contacts")
        self._nudge(uid)
        return m

    def update_member(self, folder_id: str, uid: str, perms: dict, expires_at: str | None = None) -> dict:
        local = self.store.member(folder_id, uid)
        if local is None:
            raise ValueError("unknown member")
        data = dict(perms)
        if expires_at is not None:
            data["expires_at"] = expires_at
        m = self.cloud.update_member(folder_id, local["member_id"], data)
        self.store.upsert_member(folder_id, m)
        self.enforce_permissions()                 # revoked rights stop running transfers now
        self.emit("contacts")
        self._nudge(uid)
        return m

    def remove_member(self, folder_id: str, uid: str) -> None:
        local = self.store.member(folder_id, uid)
        if local:
            self.cloud.remove_member(folder_id, local["member_id"])
        self.store.remove_member(folder_id, uid)
        self.enforce_permissions()
        self.emit("contacts")
        self._nudge(uid)

    def resend_invite(self, folder_id: str, uid: str) -> dict:
        local = self.store.member(folder_id, uid)
        return self.cloud.resend_invite(folder_id, local["member_id"])

    def add_group_to_folder(self, folder_id: str, group_id: int, role: str, expires_at: str | None = None) -> int:
        data = {"role": role}
        if expires_at:
            data["expires_at"] = expires_at
        r = self.cloud.add_group_to_folder(folder_id, group_id, data)
        for m in r["folder"]["members"]:
            self.store.upsert_member(folder_id, m)
            self._nudge(m["user"]["public_id"])
        self.emit("contacts")
        return r["added"]

    def folder_free_space(self, folder_id: str) -> int | None:
        f = self.store.get(folder_id)
        return free_space(f["path"]) if f else None

    def enforce_permissions(self) -> None:
        """Stop served transfers whose permission was revoked / expired / folder removed."""
        for engine in list(self.manager.jobs.values()):
            m = engine.meta
            if not m.get("served") or engine.finished.is_set():
                continue
            if self.store.get(m["share_uid"]) is None or \
                    not self.store.perms(m["share_uid"], m["peer_uid"]).allows(m["need"]):
                log.info("permission revoked: stopping %s", engine.transfer_id)
                engine.request_stop(cancel=True)

    # ------------------------------------------------- remote shares (client)
    def remote_shares(self, uid: str) -> list:
        return self.call(uid, "shares.list")

    def remote_list(self, uid: str, share_uid: str, path: str = "") -> dict:
        entries, offset, page = [], 0, {}
        while True:
            page = self.call(uid, "fs.list", {"share": share_uid, "path": path, "offset": offset})
            entries.extend(page["entries"])
            offset += len(page["entries"])
            if offset >= page["total"] or not page["entries"]:
                break
        return {"entries": entries, "perms": page.get("perms"), "rules": page.get("rules") or {},
                "dropbox": bool(page.get("dropbox"))}

    def remote_mkdir(self, uid, share_uid, path, name) -> None:
        self.call(uid, "fs.mkdir", {"share": share_uid, "path": path, "name": name})

    def remote_delete(self, uid, share_uid, path) -> None:
        self.call(uid, "fs.delete", {"share": share_uid, "path": path})

    def remote_rename(self, uid, share_uid, path, new_name) -> None:
        self.call(uid, "fs.rename", {"share": share_uid, "path": path, "new_name": new_name})

    # ------------------------------------------------------------------ jobs
    def _authorize(self, size: int) -> None:
        try:
            ok, reason = self.cloud.authorize(size)
        except CloudError as exc:            # cloud unreachable: transfers still work (P2P)
            log.warning("could not check plan limits: %s", exc)
            return
        if not ok:
            raise PermissionError(reason)

    def _new_job(self, job: Job) -> Job:
        self.jobs[job.job_id] = job
        self.emit("job", job.job_id)
        return job

    def _run_job(self, job: Job, fn) -> None:
        def run():
            job.state = "running"
            try:
                fn()
                if job.cancel.is_set():
                    job.state = "cancelled"
                elif any(i.state != "completed" for i in job.items):
                    job.state = "failed"
                    job.error = job.error or next((i.error for i in job.items if i.error), "some files failed")
                else:
                    job.state = "completed"
            except Exception as exc:
                job.state, job.error = ("cancelled" if job.cancel.is_set() else "failed"), str(exc)
                log.info("job %s failed: %s", job.title, exc)
            finally:
                job.done.set()
                self.emit("job", job.job_id)
        threading.Thread(target=run, name=f"job-{job.job_id[:6]}", daemon=True).start()

    def _wait(self, job: Job, item: JobItem) -> None:
        engine = item.engine
        while not engine.finished.wait(0.3):
            if job.cancel.is_set():
                engine.request_stop(cancel=True)
                engine.finished.wait(10)
                break
        item.state = engine.state if engine.state in FINAL else "failed"
        item.error = engine.error

    def cancel_job(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job:
            job.cancel.set()
            for i in job.items:
                if i.engine is not None and not i.engine.finished.is_set():
                    i.engine.request_stop(cancel=True)

    @staticmethod
    def _expand_local(paths) -> list[JobItem]:
        items = []
        for p in map(Path, paths):
            if p.is_file():
                items.append(JobItem(rel=p.name, size=p.stat().st_size, local=str(p)))
            elif p.is_dir():
                for dirpath, _dirs, files in os.walk(p):
                    for f in files:
                        full = Path(dirpath) / f
                        items.append(JobItem(rel=(Path(p.name) / full.relative_to(p)).as_posix(),
                                             size=full.stat().st_size, local=str(full)))
        return items

    # ---- download from a contact's shared folder ----
    def download(self, uid: str, share_uid: str, share_name: str, selection: list[tuple[str, bool]],
                 local_dir: Path) -> Job:
        """selection: [(path in share, is_dir)]"""
        local_dir = Path(local_dir)
        title = Path(selection[0][0]).name if len(selection) == 1 else f"{len(selection)} items"
        job = self._new_job(Job("download", uid, self.contacts.get(uid, {}).get("name", uid), title,
                                share_name=share_name))

        def work():
            for path, is_dir in selection:
                path = clean_rel(path)
                if is_dir:
                    w = self.call(uid, "fs.walk", {"share": share_uid, "path": path}, timeout=120)
                    base = Path(path).name
                    for d in w["dirs"]:
                        safe_child(local_dir, f"{base}/{d}").mkdir(parents=True, exist_ok=True)
                    for f in w["files"]:
                        job.items.append(JobItem(rel=f"{path}/{f['rel']}", size=f["size"],
                                                 local=f"{base}/{f['rel']}"))
                else:
                    st = self.call(uid, "fs.stat", {"share": share_uid, "path": path})
                    job.items.append(JobItem(rel=path, size=st["size"], local=Path(path).name))
            self._authorize(sum(i.size for i in job.items))
            for item in job.items:
                if job.cancel.is_set():
                    break
                target_dir = safe_child(local_dir, "/".join(item.local.split("/")[:-1]))
                target_dir.mkdir(parents=True, exist_ok=True)
                fp, cert = self._peer(uid)
                res = self.call(uid, "fs.download", {"share": share_uid, "path": item.rel, "job_id": job.job_id,
                                                     "job_total": len(job.items), "title": job.title,
                                                     "job_bytes": sum(i.size for i in job.items)})
                item.engine = ReceiverEngine(
                    self._job_cfg(dest_dir=target_dir), self.db, self.identity, peer_fp=fp,
                    rendezvous=self.e2e.rendezvous(uid, cert, res["nonce"]),
                    expect=(res["name"], res["size"], res["transfer_id"]),
                    meta={"direction": "download", "share_name": share_name, "relative_path": item.rel,
                          "peer_uid": uid, "initiator": True, "job_id": job.job_id, "folder_id": share_uid,
                          "job_total": len(job.items)})
                self.store.remember_transfer(res["transfer_id"], "receiver", uid, "download", share_uid, item.rel,
                                             str(target_dir))
                item.state = "running"
                self._start(item.engine)
                self._wait(job, item)
        self._run_job(job, work)
        return job

    # ---- upload: outbox, delivered directly to the admin when online ----
    def upload(self, uid: str, share_uid: str, share_name: str, remote_dir: str, paths,
               fields: dict | None = None, rules: dict | None = None, thumbnail=None) -> Job:
        """Send files/folders into a folder. They are queued on THIS computer first and delivered
        directly to the admin's computer as soon as the admin is online (now, or later)."""
        remote_dir = clean_rel(remote_dir)
        items = self._expand_local(paths)
        if not items:
            raise ValueError("nothing to send")
        if rules:
            for item in items:
                check_upload_rules(dict(rules, name=share_name), item.rel.split("/")[-1], item.size, fields)
        self._authorize(sum(i.size for i in items))
        picked = thumbs.from_image(thumbnail) if thumbnail else None
        if thumbnail and picked is None:
            raise ValueError("the thumbnail must be an image file (PNG, JPG, ...)")
        for item in items:                      # a picked thumbnail: for a single file, or the videos of a batch
            if picked and (len(items) == 1 or thumbs.is_video(item.rel)):
                item.thumb = picked
        names = [Path(p).name for p in paths]
        job = Job("upload", uid, self.contacts.get(uid, {}).get("name", uid),
                  names[0] if len(names) == 1 else f"{len(names)} items", items, share_name=share_name)
        job.state = "queued"
        rows = []
        for item in items:
            item.item_id, item.transfer_id = secrets.token_hex(8), secrets.token_hex(8)
            rows.append({"item_id": item.item_id, "job_id": job.job_id, "folder_id": share_uid,
                         "folder_name": share_name, "admin_uid": uid, "local_path": item.local, "rel": item.rel,
                         "remote_dir": remote_dir, "size": item.size, "fields": fields or {},
                         "transfer_id": item.transfer_id, "job_total": len(items), "thumb": item.thumb})
        self.store.outbox_add(rows)
        threading.Thread(target=self._make_thumbs, args=(job,), name="thumbs", daemon=True).start()
        self._new_job(job)
        threading.Thread(target=self._report_queued, args=(rows,), daemon=True).start()
        self._outbox_kick.set()
        return job

    def _make_thumbs(self, job: Job) -> None:
        """Automatic previews for images/videos (in the background; never delays sending)."""
        for item in job.items:
            if item.thumb is None and self.store.outbox_state(item.item_id) is not None:
                item.thumb = thumbs.make_thumb(item.local)
                self.store.outbox_update(item.item_id, thumb=item.thumb, thumb_done=1)
                if item.thumb:
                    self.emit("job", job.job_id)

    def _report_queued(self, rows: list[dict]) -> None:
        """Tell the cloud right away (metadata only) so the admin sees what is waiting for them."""
        for r in rows:
            try:
                self.cloud.report({"transfer_id": r["transfer_id"], "role": "sender", "state": "queued",
                                   "file_name": Path(r["rel"]).name, "file_size": r["size"], "bytes_transferred": 0,
                                   "direction": "upload", "share_name": r.get("folder_name", ""),
                                   "relative_path": f"{r.get('remote_dir', '')}/{r['rel']}".strip("/"),
                                   "folder_id": r["folder_id"], "job_id": r["job_id"],
                                   "job_total": r.get("job_total", 1), "initiator": True})
            except CloudError as exc:
                log.debug("queued report failed: %s", exc)

    def outbox_items(self, folder_id: str | None = None) -> list[dict]:
        """Outbox rows with live progress (for the 'My uploads' panel)."""
        rows = self.store.outbox(folder_id)
        live = {}
        for job in self.jobs.values():
            for it in job.items:
                if it.item_id and it.engine is not None:
                    live[it.item_id] = it.engine.snapshot()
        for r in rows:
            snap = live.get(r["item_id"])
            r["progress"] = 100.0 if r["state"] == "delivered" else (snap["progress"] if snap else 0.0)
            r["speed"] = snap["speed"] if snap and snap["state"] == "active" else 0.0
            r["admin_online"] = self.is_online(r["admin_uid"])
        return rows

    def _job_for(self, job_id: str, rows: list[dict]) -> Job:
        job = self.jobs.get(job_id)
        if job is None:                               # after an app restart: rebuild from the outbox
            first = rows[0]
            items = [JobItem(rel=r["rel"], size=r["size"], local=r["local_path"], item_id=r["item_id"],
                             transfer_id=r["transfer_id"], thumb=r.get("thumb"),
                             state="completed" if r["state"] == "delivered" else "queued") for r in
                     self.store.outbox(first["folder_id"]) if r["job_id"] == job_id]
            job = Job("upload", first["admin_uid"], self.contacts.get(first["admin_uid"], {}).get("name", "admin"),
                      f"{len(items)} item(s)" if len(items) != 1 else Path(items[0].rel).name, items,
                      share_name=first.get("folder_name", ""), job_id=job_id)
            job.state = "queued"
            self.jobs[job_id] = job
            self.emit("job", job_id)
        return job

    def _outbox_worker(self) -> None:
        for r in self.store.outbox(states=("sending",)):          # interrupted by an app exit
            self.store.outbox_update(r["item_id"], state="queued", resume=1)
        while not self._bg_stop.is_set():
            self._outbox_kick.wait(5.0)
            self._outbox_kick.clear()
            if self.messenger is None:
                continue
            if self.store.chat_queued():
                self._flush_chat()
            pending: dict[str, list] = {}
            for r in self.store.outbox(states=("queued",)):
                pending.setdefault(r["job_id"], []).append(r)
            for job_id, rows in pending.items():
                admin = rows[0]["admin_uid"]
                job = self._job_for(job_id, rows)
                if job_id in self._delivering or job.cancel.is_set():
                    continue
                if not self.is_online(admin):
                    job.state = "queued"
                    continue
                self._delivering.add(job_id)
                threading.Thread(target=self._deliver, args=(job, rows), name=f"deliver-{job_id[:6]}",
                                 daemon=True).start()

    PERMANENT = ("permission", "file types", "maximum", "fill in", "not allowed", "not enough free space",
                 "no longer exists", "not found")

    def _deliver(self, job: Job, rows: list[dict]) -> None:
        uid, share_uid, share_name = rows[0]["admin_uid"], rows[0]["folder_id"], rows[0].get("folder_name", "")
        job.state, job.error = "running", None
        self.emit("job", job.job_id)
        made: set[str] = set()
        try:
            for r in rows:
                if job.cancel.is_set():
                    break
                item = next((i for i in job.items if i.item_id == r["item_id"]), None)
                if item is None or self.store.outbox_state(r["item_id"]) != "queued":
                    continue
                outcome = self._deliver_item(job, item, r, uid, share_uid, share_name, made)
                if outcome == "offline":
                    break
        finally:
            states = {i.state for i in job.items}
            if job.cancel.is_set():
                job.state = "cancelled"
            elif states <= {"completed"}:
                job.state = "completed"
            elif "queued" in states:
                job.state = "queued"
                job.error = "waiting for the admin to be online - delivered automatically"
            else:
                job.state = "failed"
                job.error = next((i.error for i in job.items if i.error), "some files failed")
            if job.state != "queued":
                job.done.set()
            self._delivering.discard(job.job_id)
            self.emit("job", job.job_id)

    def _deliver_item(self, job, item, r, uid, share_uid, share_name, made) -> str:
        """Deliver one outbox file. Returns 'ok', 'failed' (permanent) or 'offline' (retry later)."""
        name = r["rel"].split("/")[-1]
        parent = "/".join(r["rel"].split("/")[:-1])
        dest = f"{r['remote_dir']}/{parent}".strip("/")
        if not Path(r["local_path"]).is_file() and not r["resume"]:
            item.state, item.error = "failed", "the local file no longer exists"
            self.store.outbox_update(r["item_id"], state="failed", error=item.error)
            return "failed"
        try:
            if parent and parent not in made:
                self.call(uid, "fs.mkdir", {"share": share_uid, "path": r["remote_dir"], "name": parent})
                made.add(parent)
            fp, cert = self._peer(uid)
            params = {"share": share_uid, "dir": dest, "name": name, "size": r["size"], "fields": r["fields"],
                      "job_id": job.job_id, "job_total": r["job_total"], "title": job.title,
                      "job_bytes": sum(i.size for i in job.items)}
            thumb = r.get("thumb") or item.thumb
            if thumb is None and not r.get("thumb_done"):
                thumb = item.thumb = thumbs.make_thumb(r["local_path"])
                self.store.outbox_update(r["item_id"], thumb=thumb, thumb_done=1)
            if thumbs.valid(thumb):
                params["thumb"] = base64.b64encode(thumb).decode()
            meta = {"direction": "upload", "share_name": share_name, "relative_path": f"{dest}/{name}".strip("/"),
                    "peer_uid": uid, "initiator": True, "job_id": job.job_id, "folder_id": share_uid,
                    "job_total": r["job_total"]}
            cfg = self._job_cfg(reconnect_timeout=120)        # short: fall back to the outbox, resume later
            engine = None
            rec = self.db.get(r["transfer_id"], "sender") if r["resume"] else None
            if rec and rec["resume_key"] and rec["status"] not in ("completed", "cancelled"):
                try:                                          # continue from the last verified chunk
                    self.call(uid, "fs.upload", dict(params, transfer_id=r["transfer_id"],
                                                     resume_tid=r["transfer_id"]))
                    engine = SenderEngine(cfg, self.db, self.identity, transfer_id=r["transfer_id"], peer_fp=fp,
                                          meta=meta)
                except RpcError as exc:
                    if "cannot be resumed" not in str(exc):
                        raise
            if engine is None:                                # fresh start (new id if a resume was refused)
                tid = r["transfer_id"] if not rec else secrets.token_hex(8)
                if tid != r["transfer_id"]:
                    self.store.outbox_update(r["item_id"], transfer_id=tid)
                    r["transfer_id"] = item.transfer_id = tid
                res = self.call(uid, "fs.upload", dict(params, transfer_id=tid))
                engine = SenderEngine(cfg, self.db, self.identity, path=Path(r["local_path"]), new_transfer_id=tid,
                                      peer_fp=fp, rendezvous=self.e2e.rendezvous(uid, cert, res["nonce"]), meta=meta)
            item.engine = engine
            self.store.remember_transfer(r["transfer_id"], "sender", uid, "upload", share_uid, dest, r["local_path"])
            self.store.outbox_update(r["item_id"], state="sending", error=None, resume=1)
            item.state, item.error = "running", None
            self._start(engine)
            self._wait(job, item)
        except (RpcError, PermissionError, ValueError) as exc:
            if any(k in str(exc).lower() for k in self.PERMANENT):
                item.state, item.error = "failed", str(exc)
                self.store.outbox_update(r["item_id"], state="failed", error=str(exc)[:300])
                return "failed"
            item.state, item.error = "queued", str(exc)
            self.store.outbox_update(r["item_id"], state="queued", error=str(exc)[:300])
            return "offline"
        except (OSError, ConnectionError, TimeoutError) as exc:
            item.state, item.error = "queued", str(exc)
            self.store.outbox_update(r["item_id"], state="queued", error=str(exc)[:300])
            return "offline"
        if item.state == "completed":
            self.store.outbox_update(r["item_id"], state="delivered", error=None)
            return "ok"
        if job.cancel.is_set() or item.state == "cancelled" or self.store.outbox_state(r["item_id"]) == "cancelled":
            item.state = "cancelled"
            self.store.outbox_update(r["item_id"], state="cancelled")
            return "failed"
        # connection lost / the admin closed the app: resume from the last chunk next time
        item.state, item.error = "queued", item.error
        self.store.outbox_update(r["item_id"], state="queued", resume=1, error=(item.error or "")[:300])
        return "offline"

    def _peer_went_offline(self, uid: str, grace: float = 4.0) -> None:
        """The other computer vanished (power / network loss): pause my uploads to it right away so they go
        back to the outbox (instead of waiting for the reconnect timeout). They resume from the last verified
        chunk when it is back online."""
        time.sleep(grace)
        if self.is_online(uid) or self._bg_stop.is_set():
            return
        for job in list(self.jobs.values()):
            if job.kind == "upload" and job.peer_uid == uid:
                for it in job.items:
                    if it.engine is not None and not it.engine.finished.is_set():
                        log.info("%s went offline - pausing %s (will resume)", uid, it.rel)
                        it.engine.request_stop(cancel=False)

    def cancel_upload(self, item_id: str) -> None:
        self.store.outbox_update(item_id, state="cancelled")
        for job in self.jobs.values():
            for it in job.items:
                if it.item_id == item_id and it.engine is not None and not it.engine.finished.is_set():
                    it.engine.request_stop(cancel=True)

    def retry_upload(self, item_id: str) -> None:
        self.store.outbox_update(item_id, state="queued", error=None)
        self._outbox_kick.set()

    # ---- admin: files received and waiting ----
    def received_files(self, folder_id: str | None = None) -> list[dict]:
        return self.store.received(folder_id)

    def folder_files_cloud(self, folder_id: str) -> dict:
        """Metadata from the cloud: every file sent to the folder incl. those still waiting (queued)."""
        return self.cloud.request("GET", f"api/folders/{folder_id}/files/")
