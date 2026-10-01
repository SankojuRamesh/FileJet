"""Rendezvous for P2P transfers (connection setup only - never file data).

    WS /ws   (Authorization: Bearer <cloud signal token>)
    client -> {"type": "rendezvous", "room": <64 hex>, "role": "sender"|"receiver"}
    server -> {"type": "waiting"} ... {"type": "paired", "session_id", "role", "observed_ip", "reflector", "stun"}
    client -> {"type": "signal", "data": {...}}     forwarded to the other peer (certificates, candidates)
    server -> {"type": "peer_left"}

Room names are HMACs of a secret only the two devices know (derived end-to-end), so the server
cannot guess or join them. Nothing is persisted; rooms disappear when both peers leave.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect

from .auth import RateLimiter
from .config import Settings

log = logging.getLogger("p2p.server.signaling")

ROLES = ("sender", "receiver")
ROOM_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_MESSAGE = 64 * 1024


@dataclass(eq=False)
class Peer:
    ws: WebSocket
    ip: str
    host: str
    uid: str
    role: str | None = None
    room: "Room | None" = None
    closed: bool = False

    async def send(self, msg: dict) -> None:
        if self.closed:
            return
        try:
            await self.ws.send_text(json.dumps(msg, separators=(",", ":")))
        except Exception:
            self.closed = True


@dataclass(eq=False)
class Room:
    room_id: str
    created: float = field(default_factory=time.monotonic)
    peers: dict = field(default_factory=dict)
    session_id: str | None = None

    def other(self, peer: Peer) -> Peer | None:
        return next((p for p in self.peers.values() if p is not peer), None)


class Hub:
    def __init__(self, settings: Settings):
        self.s = settings
        self.rooms: dict[str, Room] = {}
        self.lock = asyncio.Lock()

    def _client_ip(self, ws: WebSocket) -> str:
        if self.s.trust_proxy_headers and ws.headers.get("x-forwarded-for"):
            return ws.headers["x-forwarded-for"].split(",")[0].strip()
        return ws.client.host if ws.client else "unknown"

    async def handle(self, ws: WebSocket) -> None:
        from .presence import AuthFailed, verify_signal_token
        auth = ws.headers.get("authorization", "")
        try:
            claims = verify_signal_token(self.s.cloud_jwt_secret, auth[7:] if auth.lower().startswith("bearer ") else "")
        except AuthFailed:
            await ws.close(code=4401)
            return
        await ws.accept()
        peer = Peer(ws=ws, ip=self._client_ip(ws), host=ws.url.hostname or "", uid=str(claims["uid"]))
        limiter = RateLimiter(self.s.signal_rate_per_min)
        try:
            while True:
                raw = await ws.receive_text()
                if len(raw) > MAX_MESSAGE:
                    continue
                try:
                    msg = json.loads(raw)
                    mtype = msg.get("type")
                except (ValueError, AttributeError):
                    continue
                if mtype == "ping":
                    await peer.send({"type": "pong"})
                elif mtype == "rendezvous":
                    await self._rendezvous(peer, msg)
                elif mtype == "signal":
                    if limiter.allow("s"):
                        await self._signal(peer, msg)
                else:
                    await peer.send({"type": "error", "code": "bad_type", "message": f"unknown type {mtype!r}"})
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("signaling handler error")
        finally:
            peer.closed = True
            await self._leave(peer)

    def _paired(self, room: Room, peer: Peer) -> dict:
        reflector = {"host": self.s.public_host or peer.host, "port": self.s.reflector_port} \
            if self.s.reflector_port else None
        return {"type": "paired", "session_id": room.session_id, "role": peer.role, "observed_ip": peer.ip,
                "reflector": reflector, "stun": self.s.stun_servers}

    async def _rendezvous(self, peer: Peer, msg: dict) -> None:
        role, room_id = msg.get("role"), str(msg.get("room", ""))
        if role not in ROLES or peer.room is not None or not ROOM_RE.match(room_id):
            await peer.send({"type": "error", "code": "bad_request", "message": "invalid rendezvous"})
            return
        async with self.lock:
            room = self.rooms.get(room_id)
            if room is None:
                if len(self.rooms) >= self.s.max_rooms:
                    await peer.send({"type": "error", "code": "busy", "message": "server busy"})
                    return
                room = self.rooms[room_id] = Room(room_id)
            stale = room.peers.get(role)
            if stale is not None:
                stale.room = None
            peer.role, peer.room = role, room
            room.peers[role] = peer
            full = len(room.peers) == 2
            if full:
                room.session_id = secrets.token_hex(16)
        if stale is not None:
            try:
                await stale.ws.close()
            except Exception:
                pass
        if full:
            for p in room.peers.values():
                await p.send(self._paired(room, p))
        else:
            await peer.send({"type": "waiting"})

    async def _signal(self, peer: Peer, msg: dict) -> None:
        room, data = peer.room, msg.get("data")
        if room is None or room.session_id is None or not isinstance(data, dict):
            await peer.send({"type": "error", "code": "not_paired", "message": "not paired yet"})
            return
        other = room.other(peer)
        if other is None:
            await peer.send({"type": "peer_left"})
            return
        await other.send({"type": "signal", "data": data})

    async def _leave(self, peer: Peer) -> None:
        room = peer.room
        if room is None:
            return
        peer.room = None
        async with self.lock:
            if room.peers.get(peer.role) is peer:
                del room.peers[peer.role]
            other = room.other(peer)
            if not room.peers:
                self.rooms.pop(room.room_id, None)
        if other is not None:
            await other.send({"type": "peer_left"})

    async def sweep_forever(self, interval: float = 300.0) -> None:
        while True:
            await asyncio.sleep(interval)
            now = time.monotonic()
            async with self.lock:
                for rid, room in list(self.rooms.items()):
                    if room.session_id is None and now - room.created > 86400:
                        del self.rooms[rid]

    def stats(self) -> dict:
        return {"rooms": len(self.rooms)}
