"""Synchronous WebSocket client for the signaling server (connection setup only)."""
from __future__ import annotations

import json
import logging
import ssl
import threading

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

log = logging.getLogger("p2p.signaling")


class SignalingError(Exception):
    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


class PeerLeft(SignalingError):
    def __init__(self):
        super().__init__("peer disconnected from signaling", "peer_left")


class SignalingClient:
    def __init__(self, url: str, access_key: str | None = None, ca_file: str | None = None,
                 timeout: float = 10.0):
        headers = {"Authorization": f"Bearer {access_key}"} if access_key else None
        ssl_ctx = ssl.create_default_context(cafile=ca_file) if url.startswith("wss://") else None
        try:
            self.ws = connect(url, open_timeout=timeout, additional_headers=headers, ssl=ssl_ctx,
                              max_size=1 << 20, ping_interval=20, ping_timeout=20).__enter__()
        except Exception as exc:
            raise SignalingError(f"cannot reach signaling server {url}: {exc}", "unreachable") from exc
        self._pending: list[dict] = []
        self.paired: dict | None = None

    # ----------------------------------------------------------------- basics
    def _send(self, msg: dict) -> None:
        try:
            self.ws.send(json.dumps(msg, separators=(",", ":")))
        except ConnectionClosed as exc:
            raise SignalingError("signaling connection closed", "closed") from exc

    def _recv(self, timeout: float | None) -> dict:
        try:
            raw = self.ws.recv(timeout=timeout)
        except TimeoutError:
            raise
        except ConnectionClosed as exc:
            raise SignalingError("signaling connection closed", "closed") from exc
        msg = json.loads(raw)
        mtype = msg.get("type")
        if mtype == "error":
            raise SignalingError(msg.get("message", "signaling error"), msg.get("code", "error"))
        if mtype == "peer_left":
            raise PeerLeft()
        return msg

    def _wait_for(self, types: tuple, timeout: float, cancel: threading.Event | None = None) -> dict:
        import time
        deadline = time.monotonic() + timeout
        while True:
            for i, m in enumerate(self._pending):
                if m.get("type") in types:
                    return self._pending.pop(i)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"timed out waiting for {types}")
            if cancel is not None and cancel.is_set():
                raise SignalingError("cancelled", "cancelled")
            try:
                msg = self._recv(min(remaining, 1.0))
            except TimeoutError:
                continue
            if msg.get("type") in types:
                return msg
            if msg.get("type") != "pong":
                self._pending.append(msg)

    # ---------------------------------------------------------------- pairing
    def rendezvous(self, room: str, role: str) -> None:
        self._send({"type": "rendezvous", "room": room, "role": role})

    def wait_paired(self, timeout: float, cancel: threading.Event | None = None) -> dict:
        self.paired = self._wait_for(("paired",), timeout, cancel)
        return self.paired

    # ---------------------------------------------------------------- signals
    def send_signal(self, kind: str, data: dict) -> None:
        self._send({"type": "signal", "data": dict(data, kind=kind)})

    def recv_signal(self, kind: str, timeout: float, cancel: threading.Event | None = None) -> dict:
        import time
        deadline = time.monotonic() + timeout
        while True:
            for i, m in enumerate(self._pending):
                if m.get("type") == "signal" and m["data"].get("kind") == kind:
                    return self._pending.pop(i)["data"]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"timed out waiting for peer signal {kind!r}")
            if cancel is not None and cancel.is_set():
                raise SignalingError("cancelled", "cancelled")
            try:
                msg = self._recv(min(remaining, 1.0))
            except TimeoutError:
                continue
            if msg.get("type") == "signal" and isinstance(msg.get("data"), dict):
                if msg["data"].get("kind") == kind:
                    return msg["data"]
                self._pending.append(msg)

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass
