"""SQLite transfer metadata (never file contents).

``transfers`` holds one row per transfer and side; ``chunks`` holds the SHA-256 of every
chunk that is durably on the receiver's disk (receiver) or acknowledged (sender). A 1 TB
file at 8 MiB chunks is 131 072 rows (~6 MB) - trivial for SQLite.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS transfers (
    transfer_id       TEXT NOT NULL,
    role              TEXT NOT NULL,
    status            TEXT NOT NULL,
    file_name         TEXT NOT NULL,
    file_path         TEXT,
    part_path         TEXT,
    file_size         INTEGER NOT NULL,
    file_mtime_ns     INTEGER,
    chunk_size        INTEGER NOT NULL,
    chunk_count       INTEGER NOT NULL,
    chunks_completed  INTEGER NOT NULL DEFAULT 0,
    bytes_transferred INTEGER NOT NULL DEFAULT 0,
    file_hash         TEXT,
    sha256            TEXT,
    resume_key        BLOB,
    peer_fingerprint  TEXT,
    server_url        TEXT,
    connection_type   TEXT,
    avg_speed         REAL,
    peak_speed        REAL,
    error             TEXT,
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL,
    PRIMARY KEY (transfer_id, role)
);
CREATE TABLE IF NOT EXISTS chunks (
    transfer_id TEXT NOT NULL,
    role        TEXT NOT NULL,
    idx         INTEGER NOT NULL,
    hash        BLOB NOT NULL,
    PRIMARY KEY (transfer_id, role, idx)
) WITHOUT ROWID;
"""

FIELDS = ("status", "file_name", "file_path", "part_path", "file_size", "file_mtime_ns", "chunk_size",
          "chunk_count", "chunks_completed", "bytes_transferred", "file_hash", "sha256", "resume_key",
          "peer_fingerprint", "server_url", "connection_type", "avg_speed", "peak_speed", "error")

ACTIVE_STATES = ("waiting", "connecting", "active", "reconnecting", "verifying")
TERMINAL_STATES = ("completed", "cancelled")


class TransferDB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10.0, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA busy_timeout=10000")
            self.conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # ----------------------------------------------------------- transfers
    def create(self, transfer_id: str, role: str, **fields) -> None:
        now = time.time()
        cols = {k: v for k, v in fields.items() if k in FIELDS}
        cols.setdefault("status", "waiting")
        names = ["transfer_id", "role", *cols, "created_at", "updated_at"]
        values = [transfer_id, role, *cols.values(), now, now]
        with self._lock:
            self.conn.execute(f"INSERT OR REPLACE INTO transfers ({','.join(names)}) "
                              f"VALUES ({','.join('?' * len(values))})", values)

    def update(self, transfer_id: str, role: str, only_if_not: tuple = (), **fields) -> bool:
        cols = {k: v for k, v in fields.items() if k in FIELDS}
        if not cols:
            return False
        sets = ", ".join(f"{k}=?" for k in cols) + ", updated_at=?"
        sql = f"UPDATE transfers SET {sets} WHERE transfer_id=? AND role=?"
        args = [*cols.values(), time.time(), transfer_id, role]
        if only_if_not:
            sql += f" AND status NOT IN ({','.join('?' * len(only_if_not))})"
            args.extend(only_if_not)
        with self._lock:
            return self.conn.execute(sql, args).rowcount > 0

    def get(self, transfer_id: str, role: str | None = None) -> dict | None:
        with self._lock:
            if role:
                row = self.conn.execute("SELECT * FROM transfers WHERE transfer_id=? AND role=?",
                                        (transfer_id, role)).fetchone()
            else:
                row = self.conn.execute("SELECT * FROM transfers WHERE transfer_id=? ORDER BY updated_at DESC",
                                        (transfer_id,)).fetchone()
        return dict(row) if row else None

    def find(self, prefix: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute("SELECT * FROM transfers WHERE transfer_id LIKE ? ORDER BY updated_at DESC",
                                     (prefix + "%",)).fetchall()
        return [dict(r) for r in rows]

    def list(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self.conn.execute("SELECT * FROM transfers ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def status(self, transfer_id: str, role: str) -> str | None:
        with self._lock:
            row = self.conn.execute("SELECT status FROM transfers WHERE transfer_id=? AND role=?",
                                    (transfer_id, role)).fetchone()
        return row[0] if row else None

    # --------------------------------------------------------------- chunks
    def add_chunks(self, transfer_id: str, role: str, chunks: list[tuple[int, bytes]], nbytes: int) -> None:
        """Record completed chunks and bump the counters in one transaction."""
        if not chunks:
            return
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("BEGIN")
            try:
                before = self.conn.total_changes
                cur.executemany("INSERT OR IGNORE INTO chunks (transfer_id, role, idx, hash) VALUES (?,?,?,?)",
                                [(transfer_id, role, i, h) for i, h in chunks])
                added = self.conn.total_changes - before
                cur.execute("UPDATE transfers SET chunks_completed=chunks_completed+?, "
                            "bytes_transferred=bytes_transferred+?, updated_at=? WHERE transfer_id=? AND role=?",
                            (added, nbytes if added == len(chunks) else 0, time.time(), transfer_id, role))
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
        if added != len(chunks):
            self.recount(transfer_id, role)

    def recount(self, transfer_id: str, role: str) -> None:
        rec = self.get(transfer_id, role)
        if not rec:
            return
        with self._lock:
            idxs = [r[0] for r in self.conn.execute(
                "SELECT idx FROM chunks WHERE transfer_id=? AND role=?", (transfer_id, role))]
        size, cs = rec["file_size"], rec["chunk_size"]
        nbytes = sum(min(cs, size - i * cs) for i in idxs)
        self.update(transfer_id, role, chunks_completed=len(idxs), bytes_transferred=nbytes)

    def chunk_hashes(self, transfer_id: str, role: str) -> dict[int, bytes]:
        with self._lock:
            return {r[0]: bytes(r[1]) for r in self.conn.execute(
                "SELECT idx, hash FROM chunks WHERE transfer_id=? AND role=?", (transfer_id, role))}

    def completed_indices(self, transfer_id: str, role: str) -> list[int]:
        with self._lock:
            return [r[0] for r in self.conn.execute(
                "SELECT idx FROM chunks WHERE transfer_id=? AND role=? ORDER BY idx", (transfer_id, role))]

    def clear_chunks(self, transfer_id: str, role: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM chunks WHERE transfer_id=? AND role=?", (transfer_id, role))
        self.update(transfer_id, role, chunks_completed=0, bytes_transferred=0)

    def delete(self, transfer_id: str, role: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM chunks WHERE transfer_id=? AND role=?", (transfer_id, role))
            self.conn.execute("DELETE FROM transfers WHERE transfer_id=? AND role=?", (transfer_id, role))
