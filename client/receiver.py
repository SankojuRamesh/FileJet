"""Receiver engine: verifies every chunk in RAM, writes it at its offset into ``name.part``,
fsyncs in batches, records durable chunks in SQLite, ACKs them, and finally verifies the
file hash and atomically renames ``name.part`` -> ``name``.
"""
from __future__ import annotations

import logging
import os
import queue
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

from .chunk_manager import Bitmap, BufferPool, ChunkLayout, ChunkWriter, DiskFullError, pool_size
from .integrity import chunk_digest, file_sha256, root_hash
from .protocol import MAX_CHUNK, MIN_CHUNK, ProtocolError
from .session import CTL_IN_TIMEOUT, Connection
from .stats import RateAverager, SpeedMeter
from .transfer_manager import EngineBase, FatalError, SessionCtx, StopRequested
from .util import fmt_bytes, sanitize_filename, unique_path

log = logging.getLogger("p2p.receiver")

TID_RE = re.compile(r"^[0-9a-f]{16,64}$")
DISK_MARGIN = 64 << 20


class ReceiverEngine(EngineBase):
    role = "receiver"

    def __init__(self, cfg, db, identity, transfer_id: str | None = None,
                 accept_cb=None, on_event=None, delete_partial_on_cancel: bool = False,
                 expect: tuple | None = None, **kw):
        super().__init__(cfg, db, identity, on_event=on_event, **kw)
        self.accept_cb = accept_cb
        self.expect = expect                  # (name, size[, transfer_id]): accept only exactly this file
        self.delete_partial_on_cancel = delete_partial_on_cancel
        self.writer: ChunkWriter | None = None
        self.rec: dict | None = None
        self.dest_dir = Path(cfg.dest_dir)
        if transfer_id:
            rec = db.get(transfer_id, "receiver")
            if rec is None:
                raise KeyError(f"unknown receiver transfer {transfer_id}")
            if rec["status"] in ("completed", "cancelled"):
                raise ValueError(f"transfer is {rec['status']}")
            self._load(rec)

    def _load(self, rec: dict) -> None:
        self.rec = rec
        self.transfer_id = rec["transfer_id"]
        self.resume_key = bytes(rec["resume_key"])
        if self.peer_fp and rec["peer_fingerprint"] != self.peer_fp:
            raise ValueError("this transfer belongs to a different device")
        self.peer_fp = rec["peer_fingerprint"]
        self.file_name, self.file_size, self.chunk_size = rec["file_name"], rec["file_size"], rec["chunk_size"]
        self.final_path = rec["file_path"]
        self.confirmed = rec["bytes_transferred"]

    def _release(self) -> None:
        if self.writer:
            self.writer.close()
            self.writer = None

    def _on_stopped(self) -> None:
        if self.delete_partial_on_cancel and self.rec and self.rec.get("part_path"):
            try:
                Path(self.rec["part_path"]).unlink()
            except OSError:
                pass

    # ---------------------------------------------------------------- manifest
    def _reject(self, conn: Connection, code: str, reason: str, status: str = "failed") -> None:
        try:
            conn.ctl_out.send_json({"t": "reject", "code": code, "reason": reason})
        except Exception:
            pass
        raise FatalError(reason, status=status)

    def _handle_manifest(self, m: dict, conn: Connection) -> ChunkLayout:
        try:
            tid, name, size, cs = str(m["transfer_id"]), str(m["name"]), int(m["size"]), int(m["chunk_size"])
            mtime = int(m.get("mtime_ns") or 0)
        except (KeyError, TypeError, ValueError):
            raise ProtocolError("malformed manifest") from None
        if not TID_RE.match(tid) or not 0 <= size < 2 ** 63 or not MIN_CHUNK <= cs <= MAX_CHUNK:
            self._reject(conn, "invalid", "invalid manifest")
        layout = ChunkLayout(size, cs)
        if self.rec is None:
            safe = sanitize_filename(name)
            info = {"transfer_id": tid, "name": safe, "size": size, "size_human": fmt_bytes(size),
                    "sas": conn.sas, "connection": conn.conn_type}
            self.file_name, self.file_size, self.chunk_size = safe, size, cs
            self.emit("offer", **info)
            if self.cfg.max_file_size and size > self.cfg.max_file_size:
                self._reject(conn, "too_large", f"file too large ({fmt_bytes(size)})")
            if self.expect is not None:
                exp_name, exp_size = self.expect[0], self.expect[1]
                exp_tid = self.expect[2] if len(self.expect) > 2 else None
                if (safe != sanitize_filename(exp_name) or size != int(exp_size)
                        or (exp_tid and tid != exp_tid)):
                    self._reject(conn, "unexpected", "offered file does not match the negotiated one")
                accepted = True               # already approved (offer accepted / permission checked)
            else:
                accepted = self.cfg.auto_accept or (self.accept_cb(info) if self.accept_cb else False)
            if not accepted:
                self._reject(conn, "declined", "the receiver declined the file", status="cancelled")
            try:
                self.dest_dir.mkdir(parents=True, exist_ok=True)
                final = unique_path(self.dest_dir, safe)
                part = final.with_name(final.name + ".part")
                free = shutil.disk_usage(self.dest_dir).free
            except OSError as exc:
                self._reject(conn, "io_error", f"cannot use destination {self.dest_dir}: {exc}")
            if free < size + DISK_MARGIN:
                self._reject(conn, "disk_full", f"not enough disk space: need {fmt_bytes(size)}, "
                                                f"free {fmt_bytes(free)}")
            try:
                self.writer = ChunkWriter(part, size, self.cfg.preallocate)
            except DiskFullError as exc:
                self._reject(conn, "disk_full", str(exc))
            except OSError as exc:
                self._reject(conn, "io_error", f"cannot create {part}: {exc.strerror or exc}")
            self.transfer_id = tid
            self.resume_key = secrets.token_bytes(32)
            self.peer_fp = conn.peer_fp
            self.final_path = str(final)
            self.db.create(tid, "receiver", status="active", file_name=safe, file_path=str(final),
                           part_path=str(part), file_size=size, file_mtime_ns=mtime, chunk_size=cs,
                           chunk_count=layout.count, resume_key=self.resume_key, peer_fingerprint=conn.peer_fp,
                           server_url=self.cfg.server_url, connection_type=conn.conn_type)
            self.rec = self.db.get(tid, "receiver")
        else:
            r = self.rec
            if tid != r["transfer_id"]:
                self._reject(conn, "unexpected", "peer offered a different transfer")
            if (size, cs) != (r["file_size"], r["chunk_size"]) or (r["file_mtime_ns"] and mtime != r["file_mtime_ns"]):
                self._reject(conn, "file_changed", "the source file changed since the transfer started")
            part = Path(r["part_path"])
            if not part.exists():
                log.warning("%s is missing - restarting this transfer from zero", part)
                self.db.clear_chunks(tid, "receiver")
                self.confirmed = 0
            if self.writer is None:
                try:
                    self.writer = ChunkWriter(part, size, self.cfg.preallocate)
                except DiskFullError as exc:
                    self._reject(conn, "disk_full", str(exc))
                except OSError as exc:
                    self._reject(conn, "io_error", f"cannot open {part}: {exc.strerror or exc}")
        return layout

    # ---------------------------------------------------------------- session
    def _run_session(self, conn: Connection) -> None:
        cfg = self.cfg
        conn.ctl_in.settimeout(60)
        m = conn.ctl_in.recv_json()
        conn.ctl_in.settimeout(CTL_IN_TIMEOUT)
        if m.get("t") != "manifest":
            raise ProtocolError("expected manifest")
        self.set_state("awaiting_accept")
        layout = self._handle_manifest(m, conn)
        tid = self.transfer_id
        verify_full = bool(m.get("verify_full")) or cfg.verify_full
        prehash_root = m.get("prehash_root")
        have = Bitmap.from_indices(layout.count, self.db.completed_indices(tid, "receiver"))
        self.confirmed = sum(layout.length(i) for i in self.db.completed_indices(tid, "receiver")) \
            if have.num_set() else 0
        conn.ctl_out.send_json({"t": "accept", "have": have.to_b64(), "resume_key": self.resume_key.hex()})
        self.set_state("active", connection_type=conn.conn_type)
        self.meter = meter = SpeedMeter(self.file_size, self.confirmed)

        ctx = SessionCtx(conn)
        n_data = len(conn.data)
        pool = BufferPool(pool_size(self.chunk_size, n_data, cfg.memory_budget, 2 * n_data + 4), self.chunk_size)
        write_q: queue.Queue = queue.Queue()
        lock = threading.Lock()
        all_have = threading.Event()
        if have.complete():
            all_have.set()
        finish: dict = {}
        finish_evt = threading.Event()
        write_rate = RateAverager()
        alive = [n_data]
        counters = {"nacks": 0, "dups": 0}
        writer = self.writer
        full_sha: dict = {}

        def data_loop(ch):
            try:
                while not ctx.halt.is_set():
                    f = ch.recv_frame()
                    if f.type == "json":
                        continue            # keepalive
                    idx, n = f.idx, f.length
                    if not 0 <= idx < layout.count or n != layout.length(idx):
                        raise ProtocolError(f"bad chunk header idx={idx} len={n}")
                    buf = pool.acquire(ctx.halt)       # blocks => TCP back-pressure to the sender
                    if buf is None:
                        return
                    view = memoryview(buf)[:n]
                    ch.recv_into(view)
                    if chunk_digest(view) != f.digest:
                        pool.release(buf)
                        counters["nacks"] += 1
                        log.warning("chunk %d failed verification - requesting resend", idx)
                        conn.ctl_out.send_json({"t": "nack", "idx": idx})
                        continue
                    meter.add(n)
                    write_q.put((idx, buf, n, f.digest))
            except (OSError, ConnectionError, ProtocolError):
                if ctx.halt.is_set():
                    return
                with lock:
                    alive[0] -= 1
                    left = alive[0]
                if left <= 0:
                    raise
                log.warning("data stream %s closed; %d left", ch.idx, left)

        # Written-but-not-yet-durable chunks. The writer only writes; a separate committer thread
        # fsyncs, then records and ACKs everything that was written before that fsync began. So the
        # "recorded => on disk" guarantee holds, but a slow flush never stalls the data path.
        written: list[tuple[int, bytes]] = []
        written_set: set[int] = set()
        wstate = {"bytes": 0}
        commit_now = threading.Event()
        writer_done = threading.Event()

        # Parallel streams deliver chunks slightly out of order. Writing beyond the current end of file
        # makes NTFS zero-fill the gap first (measured: ~68 MB/s instead of ~290 MB/s on the same SSD),
        # so chunks that arrive early wait (bounded) in ``ahead`` and are written in file order.
        ahead: dict[int, tuple] = {}
        max_ahead = max(2, pool.count - n_data - 2)       # leave buffers for the streams: never deadlock
        eof = {"pos": writer.size_on_disk()}

        def write_one(idx, buf, n, digest):
            try:
                t0 = time.perf_counter()
                writer.write_at(layout.offset(idx), memoryview(buf)[:n])
                write_rate.add(n, time.perf_counter() - t0)
                eof["pos"] = max(eof["pos"], layout.offset(idx) + n)
                with lock:
                    written.append((idx, digest))
                    written_set.add(idx)
                    wstate["bytes"] += n
                    big = wstate["bytes"] >= cfg.fsync_bytes
                    last = have.num_set() + len(written) >= layout.count
                if big or last:
                    commit_now.set()
            except DiskFullError as exc:
                try:
                    conn.ctl_out.send_json({"t": "error", "code": "disk_full", "resumable": True,
                                            "message": "receiver disk is full"})
                except Exception:
                    pass
                raise FatalError(f"{exc.strerror or exc} - free space and run "
                                 f"'resume {tid}' on both sides", resumable=True) from None
            except OSError as exc:
                try:
                    conn.ctl_out.send_json({"t": "error", "code": "io_error", "resumable": True,
                                            "message": f"receiver write error: {exc}"})
                except Exception:
                    pass
                raise FatalError(f"write error: {exc}", resumable=True) from None
            finally:
                pool.release(buf)

        def drain_ahead(force_one: bool = False):
            while ahead:
                nxt = min(ahead, key=layout.offset)
                if layout.offset(nxt) > eof["pos"] and not force_one:
                    return
                force_one = False
                write_one(nxt, *ahead.pop(nxt))

        def writer_loop():
            try:
                while True:
                    try:
                        item = write_q.get(timeout=0.25)
                    except queue.Empty:
                        if ahead and (ctx.halt.is_set() or write_q.empty()):
                            drain_ahead(force_one=True)    # the gap chunk is late: do not hold data back
                        if ctx.halt.is_set():
                            while ahead:
                                drain_ahead(force_one=True)
                            return
                        if written:
                            commit_now.set()          # idle: make what we have durable soon
                        continue
                    idx, buf, n, digest = item
                    with lock:
                        dup = have.get(idx) or idx in written_set or idx in ahead
                    if dup:
                        counters["dups"] += 1
                        pool.release(buf)
                        continue
                    if layout.offset(idx) > eof["pos"]:
                        ahead[idx] = (buf, n, digest)
                        if len(ahead) > max_ahead:
                            drain_ahead(force_one=True)
                        continue
                    write_one(idx, buf, n, digest)
                    drain_ahead()
            finally:
                writer_done.set()

        def commit(send_ack: bool) -> None:
            with lock:
                batch = list(written)            # everything written so far ...
                nbytes = sum(layout.length(i) for i, _ in batch)
            if not batch:
                return
            writer.sync()                        # ... is durable after this returns
            self.db.add_chunks(tid, "receiver", batch, nbytes)
            with lock:
                for i, _ in batch:
                    have.set(i)
                    written_set.discard(i)
                del written[:len(batch)]
                wstate["bytes"] -= nbytes
                self.confirmed += nbytes
                complete = have.complete()
            if send_ack:
                conn.ctl_out.send_json({"t": "ack", "chunks": [i for i, _ in batch], "w": write_rate.value})
            if complete:
                all_have.set()

        def committer_loop():
            while True:
                commit_now.wait(cfg.fsync_interval)
                commit_now.clear()
                if ctx.halt.is_set():
                    writer_done.wait(4)          # let the writer drain verified chunks first
                    try:
                        commit(send_ack=False)   # keep them for the next session
                    except Exception as exc:     # e.g. file already closed on shutdown
                        log.debug("final commit skipped: %s", exc)
                    return
                commit(send_ack=True)

        def control_loop():
            while not ctx.halt.is_set():
                msg = conn.ctl_in.recv_json()
                t = msg.get("t")
                if t == "ping":
                    conn.ctl_out.send_json({"t": "pong", "ts": msg.get("ts")})
                elif t == "finish":
                    finish.update(msg)
                    finish_evt.set()
                elif t == "cancel":
                    raise FatalError("transfer cancelled by the sender", status="cancelled")
                elif t == "error":
                    raise FatalError(f"sender error: {msg.get('message')}")

        def full_hash():
            full_sha["value"] = file_sha256(Path(self.rec["part_path"]), stop=ctx.halt)

        for ch in conn.data:
            ctx.spawn(data_loop, f"data-{ch.idx}", ch)
        ctx.spawn(writer_loop, "writer")
        ctx.spawn(committer_loop, "committer")
        ctx.spawn(control_loop, "control")
        hashing_started = False
        last_db = 0.0
        completed = False
        try:
            while True:
                if self._stop.is_set():
                    if self._cancel:
                        try:
                            conn.ctl_out.send_json({"t": "cancel"})
                        except Exception:
                            pass
                    raise StopRequested()
                if ctx.dead.is_set():
                    ctx.raise_error()
                now = time.monotonic()
                meter.tick()
                if now - last_db >= 2.0:
                    last_db = now
                    self.metrics.update(write_bps=write_rate.value, nacks=counters["nacks"],
                                        duplicates=counters["dups"], buffers_free=pool.available())
                    self._common_metrics(conn)
                    self._check_db_cancel()
                if all_have.is_set() and verify_full and not hashing_started:
                    hashing_started = True
                    self.set_state("verifying")
                    ctx.spawn(full_hash, "sha256")
                if finish_evt.is_set() and all_have.is_set() and (not verify_full or "value" in full_sha):
                    self.set_state("verifying")
                    self._verify_and_commit(conn, layout, finish, prehash_root, full_sha.get("value"),
                                            verify_full)
                    completed = True
                    ctx.dead.wait(3.0)       # let the sender read "complete" and close first
                    break
                ctx.dead.wait(0.5)
        finally:
            ctx.shutdown()
            if completed and self.writer:
                self.writer.close()
                self.writer = None
        self.db.update(tid, "receiver", avg_speed=meter.average, peak_speed=meter.peak)

    def _verify_and_commit(self, conn, layout, finish, prehash_root, sha, verify_full) -> None:
        tid = self.transfer_id
        hashes = self.db.chunk_hashes(tid, "receiver")
        if len(hashes) != layout.count:
            raise ProtocolError("finish received before all chunks were stored")
        root = root_hash(self.file_size, self.chunk_size, (hashes[i] for i in range(layout.count)))
        problems = []
        if root != finish.get("root"):
            problems.append("file hash mismatch")
        if prehash_root and root != prehash_root:
            problems.append("file hash differs from the sender's pre-computed hash")
        if verify_full and sha != finish.get("sha256"):
            problems.append("full SHA-256 mismatch")
        part = Path(self.rec["part_path"])
        if part.stat().st_size != self.file_size:
            problems.append("file size mismatch")
        if problems:
            msg = ", ".join(problems)
            conn.ctl_out.send_json({"t": "complete", "ok": False, "message": msg})
            raise FatalError(f"integrity verification failed: {msg} (partial data kept in {part})")
        self.writer.sync()
        self.writer.close()
        self.writer = None
        final = Path(self.rec["file_path"])
        i = 1
        while final.exists():                    # never overwrite something that appeared meanwhile
            stem, ext = os.path.splitext(Path(self.rec["file_path"]).name)
            final = final.with_name(f"{stem} ({i}){ext}")
            i += 1
        for attempt in range(20):                # atomic: the final name only ever holds a verified file
            try:
                os.replace(part, final)
                break
            except PermissionError:              # Windows: antivirus/indexer briefly holding the file
                if attempt == 19:
                    raise
                time.sleep(0.5)
        self.final_path, self.file_hash, self.sha256 = str(final), root, sha
        self.db.update(tid, "receiver", file_path=str(final), file_hash=root, sha256=sha)
        conn.ctl_out.send_json({"t": "complete", "ok": True, "root": root, "sha256": sha, "name": final.name})
        log.info("verified and saved %s (%s)", final, root[:16])
