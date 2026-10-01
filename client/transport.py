"""Encrypted, mutually authenticated peer streams: TLS 1.3 over TCP.

Why TCP+TLS for the data plane (see README "Transport selection"): congestion control runs
in the kernel and AES-GCM runs in OpenSSL (C, AES-NI); blocking socket calls release the
GIL, so several streams in threads saturate 1 Gbit/s from Python. Pure-Python QUIC/SCTP
stacks (aioquic, aiortc) spend CPU per packet in the interpreter and top out far lower.

Each stream is authenticated twice:
  1. TLS with the peer's pinned self-signed certificate (fingerprint from signaling), and
  2. a HELLO exchange carrying HMAC(auth_key, session|role|idx|kind|fingerprints), where
     auth_key derives from the pairing code (first session) or the resume key (later).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import queue
import socket
import ssl
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import NamedTuple

from .identity import Identity
from .protocol import (DIGEST_LEN, HDR, KINDS, KIND_PROBE, MAX_CHUNK, MAX_JSON, PROTO_VERSION, T_CHUNK,
                       T_JSON, ProtocolError)

log = logging.getLogger("p2p.transport")


class AuthenticationError(Exception):
    pass


class Frame(NamedTuple):
    type: str                 # "json" | "chunk"
    obj: dict | None = None
    idx: int = 0
    length: int = 0
    digest: bytes = b""


def tune_socket(sock: socket.socket, bufsize: int | None = None) -> None:
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "TCP_KEEPIDLE"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 15)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 4)
        elif sys.platform == "win32" and hasattr(socket, "SIO_KEEPALIVE_VALS"):
            sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 15000, 5000))
        if bufsize:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, bufsize)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, bufsize)
    except OSError as exc:
        log.debug("socket tuning failed: %s", exc)


class Channel:
    """One authenticated TLS stream carrying frames."""

    def __init__(self, sock: ssl.SSLSocket, idx: int | None, kind: str | None, remote: str = ""):
        self.sock = sock
        self.idx = idx
        self.kind = kind
        self.remote = remote
        self._wlock = threading.Lock()
        self.closed = False

    def settimeout(self, t: float | None) -> None:
        self.sock.settimeout(t)

    def send_json(self, obj: dict) -> None:
        data = json.dumps(obj, separators=(",", ":")).encode()
        if len(data) > MAX_JSON:
            raise ProtocolError("control message too large")
        with self._wlock:
            self.sock.sendall(HDR.pack(T_JSON, 0, len(data)) + data)

    def send_chunk(self, idx: int, digest: bytes, payload) -> None:
        with self._wlock:
            self.sock.sendall(HDR.pack(T_CHUNK, idx, len(payload)) + digest)
            self.sock.sendall(payload)

    def recv_into(self, view: memoryview) -> None:
        pos, n = 0, len(view)
        recv_into = self.sock.recv_into
        while pos < n:
            r = recv_into(view[pos:], n - pos)
            if not r:
                raise ConnectionError("peer closed the stream")
            pos += r

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray(n)
        self.recv_into(memoryview(buf))
        return bytes(buf)

    def recv_frame(self) -> Frame:
        ftype, arg, length = HDR.unpack(self._recv_exact(HDR.size))
        if ftype == T_JSON:
            if length > MAX_JSON:
                raise ProtocolError("control frame too large")
            try:
                obj = json.loads(self._recv_exact(length))
            except ValueError:
                raise ProtocolError("invalid JSON frame") from None
            if not isinstance(obj, dict):
                raise ProtocolError("JSON frame must be an object")
            return Frame("json", obj)
        if ftype == T_CHUNK:
            if length > MAX_CHUNK:
                raise ProtocolError("chunk frame too large")
            return Frame("chunk", None, arg, length, self._recv_exact(DIGEST_LEN))
        raise ProtocolError(f"unknown frame type {ftype}")

    def recv_json(self) -> dict:
        frame = self.recv_frame()
        if frame.type != "json":
            raise ProtocolError("expected a control frame")
        return frame.obj

    def tcp_info(self) -> dict | None:
        """RTT / retransmissions from the kernel (Linux only)."""
        if not sys.platform.startswith("linux"):
            return None
        try:
            raw = self.sock.getsockopt(socket.IPPROTO_TCP, getattr(socket, "TCP_INFO", 11), 104)
            rtt_us, = struct.unpack_from("I", raw, 68)
            cwnd, = struct.unpack_from("I", raw, 80)
            mss, = struct.unpack_from("I", raw, 16)
            total_retrans, = struct.unpack_from("I", raw, 100)
            return {"rtt_ms": rtt_us / 1000.0, "cwnd": cwnd, "mss": mss, "retrans": total_retrans}
        except (OSError, struct.error):
            return None

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            socket.socket.shutdown(self.sock, socket.SHUT_RDWR)   # unblocks threads stuck in recv/send
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


@dataclass
class StreamAuth:
    role: str                   # our role: "sender" | "receiver"
    identity: Identity
    peer_cert_pem: str
    peer_fp: str
    auth_key: bytes
    session_id: str
    _ctx: ssl.SSLContext | None = field(default=None, repr=False)

    @property
    def peer_role(self) -> str:
        return "receiver" if self.role == "sender" else "sender"

    @property
    def fingerprints(self) -> tuple[str, str]:
        if self.role == "sender":
            return self.identity.fingerprint, self.peer_fp
        return self.peer_fp, self.identity.fingerprint

    def mac(self, author_role: str, idx, kind) -> str:
        msg = "|".join(["p2pft", str(PROTO_VERSION), self.session_id, author_role, str(idx), str(kind),
                        *self.fingerprints]).encode()
        return hmac.new(self.auth_key, msg, hashlib.sha256).hexdigest()

    def context(self) -> ssl.SSLContext:
        # TLS roles are fixed by application role, independent of who opened the TCP connection:
        # the sender is always the TLS client, the receiver the TLS server.
        if self._ctx is None:
            self._ctx = self.identity.make_context(server_side=self.role == "receiver",
                                                   peer_cert_pem=self.peer_cert_pem)
        return self._ctx


def secure_stream(raw: socket.socket, auth: StreamAuth, initiator: bool, idx: int | None = None,
                  kind: str | None = None, timeout: float = 15.0, remote: str = "") -> Channel:
    """TLS handshake + HELLO authentication. ``initiator`` sends HELLO first and names the stream."""
    raw.settimeout(timeout)
    try:
        ssock = auth.context().wrap_socket(raw, server_side=auth.role == "receiver")
    except Exception:
        raw.close()
        raise
    ch = Channel(ssock, idx, kind, remote)
    try:
        der = ssock.getpeercert(binary_form=True)
        if not der or hashlib.sha256(der).hexdigest() != auth.peer_fp:
            raise AuthenticationError("peer certificate does not match the pinned fingerprint")
        if initiator:
            ch.send_json({"t": "hello", "v": PROTO_VERSION, "idx": idx, "kind": kind,
                          "mac": auth.mac(auth.role, idx, kind)})
            resp = ch.recv_json()
            if resp.get("t") != "hello_ack" or not hmac.compare_digest(
                    str(resp.get("mac", "")), auth.mac(auth.peer_role, idx, kind)):
                raise AuthenticationError("stream authentication failed")
        else:
            req = ch.recv_json()
            idx, kind = req.get("idx"), req.get("kind")
            if req.get("t") != "hello" or not isinstance(idx, int) or kind not in KINDS:
                raise ProtocolError("bad stream hello")
            if not hmac.compare_digest(str(req.get("mac", "")), auth.mac(auth.peer_role, idx, kind)):
                raise AuthenticationError("stream authentication failed")
            ch.send_json({"t": "hello_ack", "mac": auth.mac(auth.role, idx, kind)})
            ch.idx, ch.kind = idx, kind
        return ch
    except Exception:
        ch.close()
        raise


class Acceptor:
    """Listening socket (dual-stack when possible) that authenticates inbound streams."""

    MAX_PENDING_HANDSHAKES = 32

    def __init__(self, port: int = 0, sock_buf: int | None = None):
        if socket.has_dualstack_ipv6():
            try:
                self.sock = socket.create_server(("::", port), family=socket.AF_INET6,
                                                 dualstack_ipv6=True, backlog=64)
            except OSError:
                self.sock = socket.create_server(("0.0.0.0", port), backlog=64)
        else:
            self.sock = socket.create_server(("0.0.0.0", port), backlog=64)
        self.port = self.sock.getsockname()[1]
        self.sock_buf = sock_buf
        self.auth: StreamAuth | None = None
        self.channels: queue.Queue = queue.Queue()
        self.probes = 0
        self._sem = threading.Semaphore(self.MAX_PENDING_HANDSHAKES)
        self._closed = False
        threading.Thread(target=self._loop, name="acceptor", daemon=True).start()

    def set_auth(self, auth: StreamAuth) -> None:
        self.auth = auth

    def _loop(self) -> None:
        while not self._closed:
            try:
                conn, addr = self.sock.accept()
            except OSError:
                if self._closed:
                    return
                time.sleep(0.05)
                continue
            if self.auth is None or not self._sem.acquire(blocking=False):
                conn.close()
                continue
            threading.Thread(target=self._handle, args=(conn, addr), daemon=True).start()

    def _handle(self, conn: socket.socket, addr) -> None:
        try:
            tune_socket(conn, self.sock_buf)
            ch = secure_stream(conn, self.auth, initiator=False, timeout=10.0, remote=f"{addr[0]}:{addr[1]}")
            if ch.kind == KIND_PROBE:
                self.probes += 1
                ch.close()
            else:
                self.channels.put(ch)
        except Exception as exc:
            log.debug("rejected inbound connection from %s: %s", addr, exc)
        finally:
            self._sem.release()

    def collect(self, total: int, timeout: float) -> list[Channel]:
        got: dict[int, Channel] = {}
        deadline = time.monotonic() + timeout
        while len(got) < total:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                ch = self.channels.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            if ch.idx in got or not 0 <= ch.idx < total:
                ch.close()
            else:
                got[ch.idx] = ch
        return list(got.values())

    def close(self) -> None:
        self._closed = True
        try:
            self.sock.close()
        except OSError:
            pass
        while True:
            try:
                self.channels.get_nowait().close()
            except queue.Empty:
                break


def dial(host: str, port: int, timeout: float, sock_buf: int | None = None) -> socket.socket:
    raw = socket.create_connection((host, port), timeout=timeout)
    tune_socket(raw, sock_buf)
    return raw


def readline_raw(sock: socket.socket, limit: int = 256) -> bytes:
    """Read one line byte by byte (never over-reads into the following TLS stream)."""
    out = bytearray()
    while len(out) < limit:
        b = sock.recv(1)
        if not b:
            raise ConnectionError("connection closed")
        if b == b"\n":
            return bytes(out)
        out += b
    raise ProtocolError("line too long")
