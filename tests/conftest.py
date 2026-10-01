"""Shared test helpers (the servers are started by the test modules that need them)."""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from client.config import ClientConfig  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_cfg(server_url: str, data_dir: Path, **kw) -> ClientConfig:
    cfg = ClientConfig(server_url=server_url, data_dir=data_dir, dest_dir=data_dir / "downloads",
                       use_upnp=False, use_stun=False, stun_servers=[], auto_accept=True,
                       reconnect_timeout=60, fsync_interval=0.5, direct_timeout=3.0)
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def write_random_file(path: Path, size: int, block: int = 1 << 20) -> None:
    seed = os.urandom(block)
    with open(path, "wb") as f:
        written = 0
        i = 0
        while written < size:
            n = min(block, size - written)
            f.write(i.to_bytes(8, "little") + seed[8:n] if n >= 8 else seed[:n])   # distinct chunk hashes
            written += n
            i += 1
