"""Resume support: persistent transfer state and rendezvous derivation.

Durability rule (receiver): a chunk is recorded as complete only *after* the data has been
fsync'ed, so after a crash/power loss the recorded set is always a subset of what is on
disk. The receiver therefore always knows exactly which chunks it has.

On reconnect both peers rendezvous in a room derived from a 256-bit resume key that was
exchanged over the first TLS session - the signaling server never learned it and cannot
guess the room or impersonate either side. The peer's certificate fingerprint is pinned too.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import asdict, dataclass

from .database import TransferDB


def resume_room(resume_key: bytes, transfer_id: str) -> str:
    return hmac.new(resume_key, f"room|{transfer_id}".encode(), hashlib.sha256).hexdigest()


def resume_auth_key(resume_key: bytes, transfer_id: str) -> bytes:
    return hmac.new(resume_key, f"auth|{transfer_id}".encode(), hashlib.sha256).digest()


@dataclass
class ResumeState:
    transfer_id: str
    role: str
    status: str
    file_name: str
    file_size: int
    chunk_size: int
    chunk_count: int
    chunks_completed: int
    bytes_transferred: int
    file_hash: str | None
    chunk_hashes: dict
    timestamp: float
    resumable: bool

    def to_dict(self, include_hashes: bool = False) -> dict:
        d = asdict(self)
        d["chunk_hashes"] = ({str(k): v.hex() for k, v in self.chunk_hashes.items()}
                             if include_hashes else len(self.chunk_hashes))
        return d


def load_state(db: TransferDB, transfer_id: str, role: str) -> ResumeState | None:
    rec = db.get(transfer_id, role)
    if rec is None:
        return None
    return ResumeState(
        transfer_id=transfer_id, role=role, status=rec["status"], file_name=rec["file_name"],
        file_size=rec["file_size"], chunk_size=rec["chunk_size"], chunk_count=rec["chunk_count"],
        chunks_completed=rec["chunks_completed"], bytes_transferred=rec["bytes_transferred"],
        file_hash=rec["file_hash"], chunk_hashes=db.chunk_hashes(transfer_id, role),
        timestamp=rec["updated_at"] or time.time(),
        resumable=bool(rec["resume_key"]) and rec["status"] not in ("completed", "cancelled"),
    )
