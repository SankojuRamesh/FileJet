"""Sender engine: reads chunks, hashes them once, and streams them over N parallel TLS streams.

Pipeline (memory bounded by the buffer pool):

    sequential reader --(pool buffer)--> send queue --> data stream workers (SHA-256 + TLS) --> network

One reader keeps disk access strictly sequential so OS read-ahead works on large uncached files
(interleaved readers turn it into a strided pattern); CPU work (hashing, encryption) is parallel.
         ^                                                                     |
         +---------------- buffer released after sendall --------------------+

Back-pressure: when the receiver (or its disk) is slower, TCP windows fill, ``sendall``
blocks, buffers are not released, readers block on ``pool.acquire`` - RAM stays constant.
"""
from __future__ import annotations

import logging
import os
import queue
import secrets
import threading
import time
from collections import deque
from pathlib import Path

from .chunk_manager import (Bitmap, BufferPool, ChunkLayout, ChunkReader, FileChangedError, choose_chunk_size,
                            pool_size)
from .integrity import chunk_digest, chunk_digests_of_file, file_sha256, root_hash
from .session import CTL_IN_TIMEOUT, Connection
from .stats import RateAverager, SpeedMeter, TokenBucket
from .transfer_manager import EngineBase, FatalError, SessionCtx, StopRequested
from .protocol import MAX_CHUNK, MIN_CHUNK
from .tuner import AutoTuner

log = logging.getLogger("p2p.sender")


class Scheduler:
    """Hands out chunk indices in file order (sequential disk reads), retries first."""

    def __init__(self, have: Bitmap):
        self.have = have
        self.count = have.count
        self.pos = 0
        self.retry: deque = deque()
        self._lock = threading.Lock()

    def next(self) -> int | None:
        with self._lock:
            if self.retry:
                return self.retry.popleft()
            while self.pos < self.count and self.have.get(self.pos):
                self.pos += 1
            if self.pos >= self.count:
                return None
            self.pos += 1
            return self.pos - 1

    def requeue(self, idx: int) -> None:
        with self._lock:
            if idx not in self.retry:
                self.retry.append(idx)


class SenderEngine(EngineBase):
    role = "sender"

    def __init__(self, cfg, db, identity, path: Path | None = None, transfer_id: str | None = None,
                 on_event=None, new_transfer_id: str | None = None, **kw):
        super().__init__(cfg, db, identity, on_event=on_event, **kw)
        self.prehash_digests: list[bytes] | None = None
        self.prehash_root: str | None = None
        self._resuming = bool(transfer_id)
        if transfer_id:
            rec = db.get(transfer_id, "sender")
            if rec is None:
                raise KeyError(f"unknown sender transfer {transfer_id}")
            if not rec["resume_key"]:
                raise ValueError("this transfer never started on the receiver side - start a new one")
            if rec["status"] in ("completed", "cancelled"):
                raise ValueError(f"transfer is {rec['status']}")
            self.transfer_id = transfer_id
            self.path = Path(rec["file_path"])
            self.resume_key = bytes(rec["resume_key"])
            if self.peer_fp and rec["peer_fingerprint"] != self.peer_fp:
                raise ValueError("this transfer belongs to a different device")
            self.peer_fp = rec["peer_fingerprint"]
            self.chunk_size = rec["chunk_size"]
            self.file_size = rec["file_size"]
            self.mtime_ns = rec["file_mtime_ns"]
            self.prehash_root = rec["file_hash"] if cfg.prehash else None
            self.confirmed = rec["bytes_transferred"]
        else:
            if path is None:
                raise ValueError("path required")
            self.path = Path(path).resolve()
            # the id exists before the transfer starts so peers can agree on it in advance
            self.transfer_id = new_transfer_id or secrets.token_hex(8)
        self.file_name = self.path.name

    # ------------------------------------------------------------------ setup
    def _prepare(self) -> None:
        try:
            st = os.stat(self.path)
        except OSError as exc:
            raise FatalError(f"cannot open {self.path}: {exc.strerror or exc}") from None
        if not self.path.is_file():
            raise FatalError(f"{self.path} is not a regular file")
        if not os.access(self.path, os.R_OK):
            raise FatalError(f"permission denied: {self.path}")
        if self._resuming:          # resume: the file must be the one we started with
            if (st.st_size, st.st_mtime_ns) != (self.file_size, self.mtime_ns):
                raise FatalError("the source file changed since the transfer started "
                                 "(size or modification time differ) - refusing to resume")
        else:
            self.file_size, self.mtime_ns = st.st_size, st.st_mtime_ns
            cs = self.cfg.chunk_size or choose_chunk_size(self.file_size)
            if not MIN_CHUNK <= cs <= MAX_CHUNK:
                raise FatalError(f"chunk size must be between {MIN_CHUNK} and {MAX_CHUNK} bytes")
            self.chunk_size = cs
            layout = ChunkLayout(self.file_size, cs)
            self.db.create(self.transfer_id, "sender", status="waiting", file_name=self.file_name,
                           file_path=str(self.path), file_size=self.file_size, file_mtime_ns=self.mtime_ns,
                           chunk_size=cs, chunk_count=layout.count, server_url=self.cfg.server_url)
        self.layout = ChunkLayout(self.file_size, self.chunk_size)
        self.meter = SpeedMeter(self.file_size, self.confirmed)
        if self.cfg.prehash:
            self.set_state("hashing")
            self.meter = SpeedMeter(self.file_size)
            digests = chunk_digests_of_file(self.path, self.file_size, self.chunk_size,
                                            progress=self.meter.set_done, stop=self._stop)
            root = root_hash(self.file_size, self.chunk_size, digests)
            if self.prehash_root and root != self.prehash_root:
                raise FatalError("source file content changed since the transfer started - refusing to resume")
            self.prehash_digests, self.prehash_root = digests, root
            self.db.update(self.transfer_id, "sender", file_hash=root)
            st2 = os.stat(self.path)
            if (st2.st_size, st2.st_mtime_ns) != (self.file_size, self.mtime_ns):
                raise FatalError("the source file changed while it was being hashed")
        self.emit("prepared", transfer_id=self.transfer_id, file_name=self.file_name, file_size=self.file_size)

    # ---------------------------------------------------------------- session
    def _run_session(self, conn: Connection) -> None:
        cfg, layout, tid = self.cfg, self.layout, self.transfer_id
        self.set_state("awaiting_accept", connection_type=conn.conn_type)
        conn.ctl_out.send_json({
            "t": "manifest", "v": 1, "transfer_id": tid, "name": self.file_name, "size": self.file_size,
            "chunk_size": self.chunk_size, "mtime_ns": self.mtime_ns, "prehash_root": self.prehash_root,
            "verify_full": cfg.verify_full,
        })
        conn.ctl_in.settimeout(cfg.accept_timeout)
        reply = conn.ctl_in.recv_json()
        conn.ctl_in.settimeout(CTL_IN_TIMEOUT)
        if reply.get("t") == "reject":
            raise FatalError(f"receiver declined: {reply.get('reason', 'rejected')}",
                             status="cancelled" if reply.get("code") == "declined" else "failed")
        if reply.get("t") != "accept":
            raise FatalError(f"unexpected reply from receiver: {reply.get('t')}")
        if not self.resume_key:
            key = bytes.fromhex(str(reply.get("resume_key", "")))
            if len(key) != 32:
                raise FatalError("receiver sent an invalid resume key")
            self.resume_key, self.peer_fp = key, conn.peer_fp
            self.db.update(tid, "sender", resume_key=key, peer_fingerprint=conn.peer_fp)
        have = Bitmap.from_b64(reply["have"], layout.count)
        acked = have.copy()
        self.confirmed = sum(layout.length(i) for i in range(layout.count) if have.get(i)) \
            if layout.count < 4_000_000 else have.num_set() * self.chunk_size
        self.meter = meter = SpeedMeter(self.file_size, self.confirmed)

        ctx = SessionCtx(conn)
        sched = Scheduler(have)
        n_data = len(conn.data)
        self.tuner = tuner = AutoTuner(n_data, min(cfg.streams, n_data), cfg.auto_tune)
        pool = BufferPool(pool_size(self.chunk_size, n_data, cfg.memory_budget, 2 * n_data + 4), self.chunk_size)
        send_q: queue.Queue = queue.Queue()
        lock = threading.Lock()
        inflight: dict[int, set] = {ch.idx: set() for ch in conn.data}
        sent_digest: dict[int, bytes] = {}
        all_acked = threading.Event()
        if acked.complete():
            all_acked.set()
        result: dict = {}
        bucket = TokenBucket(cfg.rate_limit) if cfg.rate_limit else None
        read_rate = RateAverager()
        alive = [n_data]
        counters = {"nacks": 0, "resent": 0}

        def reader_loop():
            reader = ChunkReader(self.path, self.file_size, self.mtime_ns)
            try:
                while not ctx.halt.is_set():
                    buf = pool.acquire(ctx.halt)
                    if buf is None:
                        return
                    idx = sched.next()
                    with lock:
                        skip = idx is not None and acked.get(idx)
                    if idx is None or skip:
                        pool.release(buf)
                        if idx is None:
                            ctx.halt.wait(0.05)
                        continue
                    n = layout.length(idx)
                    view = memoryview(buf)[:n]
                    t0 = time.perf_counter()
                    try:
                        reader.read_into(layout.offset(idx), view)
                    except FileChangedError as exc:
                        raise FatalError(str(exc)) from None
                    read_rate.add(n, time.perf_counter() - t0)
                    send_q.put((idx, buf, n))        # hashing happens in the parallel stream threads
            finally:
                reader.close()

        def data_loop(rank: int, ch):
            last = time.monotonic()
            while not ctx.halt.is_set():
                if rank >= tuner.active:
                    if time.monotonic() - last > 15:
                        ch.send_json({"t": "nop"})
                        last = time.monotonic()
                    ctx.halt.wait(0.1)
                    continue
                try:
                    idx, buf, n = send_q.get(timeout=0.2)
                except queue.Empty:
                    if time.monotonic() - last > 15:
                        ch.send_json({"t": "nop"})
                        last = time.monotonic()
                    continue
                digest = chunk_digest(memoryview(buf)[:n])      # GIL released: parallel across streams
                if self.prehash_digests is not None and digest != self.prehash_digests[idx]:
                    pool.release(buf)
                    raise FatalError(f"chunk {idx} of {self.file_name} changed since it was hashed - aborting")
                with lock:
                    if acked.get(idx):
                        pool.release(buf)
                        continue
                    inflight[ch.idx].add(idx)
                    sent_digest[idx] = digest
                try:
                    if bucket:
                        bucket.consume(n, ctx.halt)
                    ch.send_chunk(idx, digest, memoryview(buf)[:n])
                except Exception:
                    pool.release(buf)
                    if ctx.halt.is_set():
                        return
                    with lock:
                        lost = [i for i in inflight[ch.idx] if not acked.get(i)]
                        inflight[ch.idx].clear()
                        alive[0] -= 1
                        remaining = alive[0]
                    for i in lost:
                        sched.requeue(i)
                    log.warning("data stream %s failed; %d chunk(s) requeued, %d stream(s) left",
                                ch.idx, len(lost), remaining)
                    if remaining <= 0:
                        raise ConnectionError("all data streams failed")
                    return
                pool.release(buf)
                meter.add(n)
                last = time.monotonic()

        def control_loop():
            while not ctx.halt.is_set():
                msg = conn.ctl_in.recv_json()
                t = msg.get("t")
                if t == "ack":
                    new, nbytes = [], 0
                    with lock:
                        for i in msg.get("chunks") or []:
                            if isinstance(i, int) and 0 <= i < layout.count and acked.set(i):
                                nbytes += layout.length(i)
                                d = sent_digest.pop(i, None)
                                if d is not None:
                                    new.append((i, d))
                                for s in inflight.values():
                                    s.discard(i)
                        self.confirmed += nbytes
                        complete = acked.complete()
                    self.db.add_chunks(tid, "sender", new, sum(layout.length(i) for i, _ in new))
                    if isinstance(msg.get("w"), (int, float)):
                        self.metrics["receiver_write_bps"] = msg["w"]
                    if complete:
                        all_acked.set()
                elif t == "nack":
                    idx = msg.get("idx")
                    if isinstance(idx, int) and 0 <= idx < layout.count:
                        counters["nacks"] += 1
                        with lock:
                            for s in inflight.values():
                                s.discard(idx)
                        sched.requeue(idx)
                elif t == "pong":
                    ts = msg.get("ts")
                    if isinstance(ts, (int, float)):
                        self.metrics["rtt_ms"] = round((time.monotonic() - ts) * 1000, 2)
                elif t == "complete":
                    result.update(msg)
                    return
                elif t == "error":
                    raise FatalError(f"receiver error: {msg.get('message')}", resumable=bool(msg.get("resumable")))
                elif t == "cancel":
                    raise FatalError("transfer cancelled by the receiver", status="cancelled")

        def finisher():
            hashes = self.db.chunk_hashes(tid, "sender")
            if self.prehash_digests is not None:
                digests = self.prehash_digests
            else:
                missing = [i for i in range(layout.count) if i not in hashes]
                if missing:          # e.g. sender DB was lost/restored: hash those chunks locally
                    reader = ChunkReader(self.path, self.file_size, self.mtime_ns)
                    buf = bytearray(self.chunk_size)
                    try:
                        for i in missing:
                            view = memoryview(buf)[:layout.length(i)]
                            reader.read_into(layout.offset(i), view)
                            hashes[i] = chunk_digest(view)
                    except FileChangedError as exc:
                        raise FatalError(str(exc)) from None
                    finally:
                        reader.close()
                    self.db.add_chunks(tid, "sender", [(i, hashes[i]) for i in missing], 0)
                digests = [hashes[i] for i in range(layout.count)]
            root = root_hash(self.file_size, self.chunk_size, digests)
            sha = file_sha256(self.path, stop=ctx.halt) if cfg.verify_full else None
            st = os.stat(self.path)
            if (st.st_size, st.st_mtime_ns) != (self.file_size, self.mtime_ns):
                raise FatalError("the source file changed during the transfer")
            self.file_hash, self.sha256 = root, sha
            conn.ctl_out.send_json({"t": "finish", "root": root, "sha256": sha})

        for r in range(max(1, cfg.readers)):
            ctx.spawn(reader_loop, f"reader-{r}")
        for rank, ch in enumerate(conn.data):
            ctx.spawn(data_loop, f"data-{ch.idx}", rank, ch)
        ctx.spawn(control_loop, "control")
        self.set_state("active")
        finishing = False
        last_ping = last_db = 0.0
        try:
            while True:
                if self._stop.is_set():
                    if self._cancel:
                        try:
                            conn.ctl_out.send_json({"t": "cancel"})
                        except Exception:
                            pass
                    raise StopRequested()
                if result:
                    break
                if ctx.dead.is_set():
                    ctx.raise_error()
                now = time.monotonic()
                meter.tick()
                tuner.tick(meter.session_bytes, now, limited=bucket is not None)
                if now - last_ping >= 1.0:
                    conn.ctl_out.send_json({"t": "ping", "ts": now})
                    last_ping = now
                if now - last_db >= 2.0:
                    last_db = now
                    self.metrics.update(read_bps=read_rate.value, nacks=counters["nacks"],
                                        buffers_free=pool.available(), send_queue=send_q.qsize())
                    self._common_metrics(conn)
                    self._check_db_cancel()
                if all_acked.is_set() and not finishing:
                    finishing = True
                    self.set_state("verifying")
                    ctx.spawn(finisher, "finisher")
                ctx.dead.wait(0.5)
        finally:
            ctx.shutdown()
        if not result.get("ok"):
            raise FatalError(f"integrity verification failed on the receiver: {result.get('message')}")
        self.db.update(tid, "sender", file_hash=self.file_hash, sha256=self.sha256, bytes_transferred=self.file_size,
                       chunks_completed=layout.count, avg_speed=meter.average, peak_speed=meter.peak)
