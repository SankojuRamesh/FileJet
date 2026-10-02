"""Engine base class (reconnect / resume loop) and the TransferManager used by the CLI and web UI."""
from __future__ import annotations

import logging
import secrets
import threading
import time

from .config import ClientConfig
from .database import TransferDB
from .identity import Identity, load_or_create
from .resume import resume_auth_key, resume_room
from .session import Connection, establish
from .signaling_client import SignalingClient, SignalingError
from .stats import SpeedMeter, process_usage
from .transport import AuthenticationError

log = logging.getLogger("p2p.engine")

DB_STATUS = {
    "starting": "waiting", "hashing": "waiting", "waiting": "waiting", "connecting": "connecting",
    "awaiting_accept": "connecting", "active": "active", "reconnecting": "reconnecting",
    "verifying": "verifying", "completed": "completed", "failed": "failed",
    "cancelled": "cancelled", "paused": "paused",
}


class FatalError(Exception):
    """Stops the transfer without automatic reconnect."""

    def __init__(self, message: str, status: str = "failed", resumable: bool = False):
        super().__init__(message)
        self.status = status
        self.resumable = resumable


class StopRequested(Exception):
    pass


class SessionCtx:
    """Worker threads of one connected session; the first worker exception kills the session."""

    def __init__(self, conn: Connection):
        self.conn = conn
        self.halt = threading.Event()
        self.dead = threading.Event()
        self.error: BaseException | None = None
        self.threads: list[threading.Thread] = []

    def fail(self, exc: BaseException) -> None:
        if self.error is None:
            self.error = exc
        self.dead.set()
        self.halt.set()

    def spawn(self, fn, name: str, *args) -> None:
        def wrap():
            try:
                fn(*args)
            except BaseException as exc:      # noqa: BLE001 - propagate to the session owner
                if not self.halt.is_set():
                    log.debug("worker %s failed: %r", name, exc)
                    self.fail(exc)
        t = threading.Thread(target=wrap, name=name, daemon=True)
        t.start()
        self.threads.append(t)

    def raise_error(self) -> None:
        """Re-raise the worker error; fatal errors are reported to the peer first."""
        if isinstance(self.error, FatalError) and self.error.status != "cancelled":
            try:
                self.conn.ctl_out.send_json({"t": "error", "code": "fatal", "message": str(self.error),
                                             "resumable": self.error.resumable})
            except Exception:
                pass
        raise self.error

    def shutdown(self, timeout: float = 5.0) -> None:
        self.halt.set()
        self.conn.close()
        deadline = time.monotonic() + timeout
        for t in self.threads:
            t.join(max(0.0, deadline - time.monotonic()))


class EngineBase:
    role = ""

    def __init__(self, cfg: ClientConfig, db: TransferDB, identity: Identity, on_event=None,
                 rendezvous: tuple | None = None, peer_fp: str | None = None, meta: dict | None = None):
        self.cfg = cfg
        self.rendezvous = rendezvous          # (room, auth_key) for contact transfers
        self.meta = dict(meta or {})          # direction / share / job info (reported to the cloud)
        self.db = db
        self.identity = identity
        self.on_event = on_event or (lambda kind, data: None)
        self.state = "starting"
        self._busy_since: float | None = None     # data is moving since (monotonic)
        self._busy_total = 0.0                    # earlier active periods (all sessions / resumes)
        self.error: str | None = None
        self.transfer_id: str | None = None
        self.file_name: str | None = None
        self.file_size: int | None = None
        self.chunk_size: int | None = None
        self.resume_key: bytes | None = None
        self.peer_fp: str | None = None
        self.conn_type: str | None = None
        self.remote: str | None = None
        self.sas: str | None = None
        self.nat: str | None = None
        self.meter: SpeedMeter | None = None
        self.confirmed = 0
        self.metrics: dict = {}
        self.tuner = None
        self.streams_total = 0
        self.sessions = 0
        self.file_hash: str | None = None
        self.sha256: str | None = None
        self.final_path: str | None = None
        self._stop = threading.Event()
        self._cancel = False
        self.finished = threading.Event()
        if peer_fp:
            self.peer_fp = peer_fp            # pinned: only this device may connect

    # ----------------------------------------------------------------- control
    def emit(self, kind: str, **data) -> None:
        try:
            self.on_event(kind, data)
        except Exception:
            log.exception("event handler failed")

    def request_stop(self, cancel: bool = False) -> None:
        self._cancel = self._cancel or cancel
        self._stop.set()

    BUSY = ("active", "verifying")

    @property
    def transfer_time(self) -> float:
        """Seconds the file was really being transferred: waiting for the other side, connecting,
        reconnecting and pauses are not counted."""
        busy = self._busy_since
        return self._busy_total + (time.monotonic() - busy if busy is not None else 0.0)

    def set_state(self, state: str, **db_fields) -> None:
        now = time.monotonic()
        if state in self.BUSY and self._busy_since is None:
            self._busy_since = now
        elif state not in self.BUSY and self._busy_since is not None:
            self._busy_total += now - self._busy_since
            self._busy_since = None
        self.state = state
        self.emit("state", state=state)
        if self.transfer_id:
            self.db.update(self.transfer_id, self.role, only_if_not=("cancelled", "completed"),
                           status=DB_STATUS.get(state, state), **db_fields)

    def snapshot(self) -> dict:
        m = self.meter
        size = self.file_size or 0
        snap = {
            "role": self.role, "state": self.state, "transfer_id": self.transfer_id,
            "file_name": self.file_name, "file_size": self.file_size, "transferred": self.confirmed,
            "progress": (self.confirmed / size * 100.0) if size else (100.0 if self.state == "completed" else 0.0),
            "speed": m.current if m else 0.0, "average": m.average if m else 0.0, "peak": m.peak if m else 0.0,
            "eta": m.eta(self.confirmed) if m and self.state == "active" else None,
            "elapsed": m.elapsed if m else 0.0, "transfer_time": self.transfer_time,
            "connection": self.conn_type, "remote": self.remote, "sas": self.sas, "nat": self.nat,
            "streams_active": self.tuner.active if self.tuner else self.streams_total, "streams_total": self.streams_total,
            "chunk_size": self.chunk_size, "error": self.error, "sessions": self.sessions,
            "file_hash": self.file_hash, "sha256": self.sha256, "final_path": self.final_path,
            "peer_fp": self.peer_fp, "meta": self.meta,
        }
        snap.update(self.metrics)
        return snap

    # --------------------------------------------------------------- main loop
    def _prepare(self) -> None:
        pass

    def _run_session(self, conn: Connection) -> None:
        raise NotImplementedError

    def _on_stopped(self) -> None:
        """Cancel-specific cleanup."""

    def _release(self) -> None:
        """Release OS resources (open files) when the engine stops for any reason."""

    def run(self) -> None:
        try:
            self._run()
        finally:
            self.finished.set()

    def _run(self) -> None:
        try:
            self._prepare()
        except FatalError as exc:
            return self._fail(exc)
        except Exception as exc:        # e.g. permission denied while hashing
            return self._fail(FatalError(str(exc)))
        attempt, lost_at = 0, None
        while True:
            if self._stop.is_set():
                return self._stopped()
            sig = None
            conn = None
            try:
                self.set_state("reconnecting" if self.sessions else "connecting")
                token = self.cfg.token_provider() if self.cfg.token_provider else self.cfg.access_key
                sig = SignalingClient(self.cfg.server_url, token, self.cfg.signal_ca)
                paired, auth_key = self._pair(sig)
                self.set_state("connecting")
                conn = establish(self.role, sig, paired, self.identity, self.cfg, auth_key,
                                 cancel=self._stop, expected_peer_fp=self.peer_fp)
                sig.close()
                sig = None
                self.conn_type, self.remote, self.sas = conn.conn_type, conn.remote, conn.sas
                self.nat = f"local {conn.local_nat} / peer {conn.peer_nat}"
                self.streams_total = len(conn.data)
                self.sessions += 1
                attempt, lost_at, self.error = 0, None, None
                self.emit("connected", connection=conn.conn_type, remote=conn.remote, sas=conn.sas)
                self._run_session(conn)
                self.set_state("completed")
                self.emit("completed", **self.snapshot())
                return
            except StopRequested:
                return self._stopped()
            except FatalError as exc:
                return self._fail(exc)
            except AuthenticationError as exc:
                return self._fail(FatalError(f"authentication failed: {exc}"))
            except SignalingError as exc:
                if exc.code in ("bad_request",) or not self.resume_key:
                    if self._stop.is_set():
                        return self._stopped()
                    return self._fail(FatalError(str(exc)))
                err = exc
            except Exception as exc:        # network errors, timeouts, protocol errors
                err = exc
            finally:
                if sig is not None:
                    sig.close()
                if conn is not None:
                    conn.close()
            # ---- transient failure: reconnect if we can
            if self._stop.is_set():
                return self._stopped()
            if not self.resume_key:
                return self._fail(FatalError(f"connection failed before the transfer started: {err}"))
            lost_at = lost_at or time.monotonic()
            if time.monotonic() - lost_at > self.cfg.reconnect_timeout:
                return self._fail(FatalError(f"gave up reconnecting: {err}", resumable=True))
            delay = min(30.0, 1.0 * (1.6 ** attempt))
            attempt += 1
            self.error = f"{type(err).__name__}: {err}"
            log.info("connection lost (%s); reconnecting in %.1fs", self.error, delay)
            self.set_state("reconnecting")
            self.emit("reconnecting", error=self.error, delay=delay)
            self._stop.wait(delay)

    def _pair(self, sig: SignalingClient):
        """Meet the peer in a room only the two devices can derive (per transfer, or the resume room)."""
        if self.resume_key:
            self.set_state("waiting")
            sig.rendezvous(resume_room(self.resume_key, self.transfer_id), self.role)
            paired = sig.wait_paired(self.cfg.reconnect_timeout, self._stop)
            return paired, resume_auth_key(self.resume_key, self.transfer_id)
        if not self.rendezvous:
            raise FatalError("no rendezvous for this transfer")
        room, key = self.rendezvous
        self.set_state("waiting")
        sig.rendezvous(room, self.role)
        try:
            paired = sig.wait_paired(180, self._stop)
        except TimeoutError:
            raise FatalError("the other computer did not join the transfer") from None
        return paired, key

    def _fail(self, exc: FatalError) -> None:
        self.error = str(exc)
        self._release()
        log.error("%s failed: %s", self.role, exc)
        self.set_state(exc.status, error=self.error)
        self.emit("failed", error=self.error, resumable=exc.resumable)

    def _stopped(self) -> None:
        self._release()
        if self._cancel:
            self.set_state("cancelled")
            self._on_stopped()
            self.emit("cancelled")
        else:
            self.set_state("paused")
            self.emit("paused", transfer_id=self.transfer_id)

    def _common_metrics(self, conn: Connection) -> None:
        usage = process_usage()
        if usage:
            self.metrics["cpu_percent"] = usage.get("cpu_percent")
            self.metrics["rss"] = usage.get("rss")
        if conn.data:
            info = conn.data[0].tcp_info()
            if info:
                self.metrics["tcp_rtt_ms"] = info["rtt_ms"]
                self.metrics["tcp_retrans"] = sum((ch.tcp_info() or {}).get("retrans", 0) for ch in conn.data)

    def _check_db_cancel(self) -> None:
        if self.transfer_id and self.db.status(self.transfer_id, self.role) == "cancelled":
            self.request_stop(cancel=True)


# ============================================================ TransferManager
class TransferManager:
    """Owns the local transfer database and device identity and runs engines in background threads."""

    def __init__(self, cfg: ClientConfig):
        self.cfg = cfg
        self.db = TransferDB(cfg.db_path)
        self.identity = load_or_create(cfg.identity_dir)
        self.jobs: dict[str, EngineBase] = {}
        self._lock = threading.Lock()

    def _start(self, engine: EngineBase) -> str:
        job_id = secrets.token_hex(6)
        with self._lock:
            self.jobs[job_id] = engine
        threading.Thread(target=engine.run, name=f"engine-{job_id}", daemon=True).start()
        return job_id

    def job(self, job_id: str) -> EngineBase | None:
        return self.jobs.get(job_id)

    def stop_all(self) -> None:
        for e in self.jobs.values():
            e.request_stop()
