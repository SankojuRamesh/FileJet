"""Client configuration. Every field can be set from the CLI; a few also from the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MiB = 1 << 20

DEFAULT_STUN = ["stun.l.google.com:19302", "stun.cloudflare.com:3478"]


def _default_data_dir() -> Path:
    env = os.environ.get("P2P_DATA_DIR")
    if env:
        return Path(env)
    old = Path.home() / ".p2p-transfer"
    return old if old.exists() else Path.home() / ".mediarush"


@dataclass
class ClientConfig:
    server_url: str = field(default_factory=lambda: os.environ.get("P2P_SERVER", "ws://filejet.live:8765/ws"))
    access_key: str | None = field(default_factory=lambda: os.environ.get("P2P_ACCESS_KEY"))
    token_provider: object = None         # callable -> current cloud signal token (desktop apps)
    signal_ca: str | None = None          # CA bundle for a wss:// server with a private CA
    data_dir: Path = field(default_factory=_default_data_dir)
    dest_dir: Path = field(default_factory=Path.cwd)

    # performance
    chunk_size: int | None = None         # None -> automatic (8 MiB for large files)
    streams: int = 4                      # initially active data streams
    max_streams: int = 8                  # data connections opened (tuner activates 1..max)
    auto_tune: bool = True
    readers: int = 1                      # sender disk readers (1 = strictly sequential reads)
    memory_budget: int = 256 * MiB        # upper bound for chunk buffers per side
    rate_limit: int | None = None         # bytes/s cap (sender)
    sock_buf: int | None = None           # SO_SNDBUF/SO_RCVBUF; None = OS autotuning

    # connectivity
    listen_port: int = 0
    public_addr: str | None = None        # "host:port" of a manual port forward
    use_upnp: bool = True
    use_stun: bool = True
    stun_servers: list = field(default_factory=lambda: list(DEFAULT_STUN))
    allow_direct: bool = True
    allow_punch: bool = True
    include_loopback: bool = True
    direct_timeout: float = 4.0
    punch_timeout: float = 8.0
    reconnect_timeout: float = 3600.0     # keep retrying for an hour (sleep, outages, ...)
    accept_timeout: float = 600.0

    # durability / integrity
    fsync_interval: float = 2.0
    fsync_bytes: int = 512 * MiB
    preallocate: bool = True
    prehash: bool = False                 # hash the whole file before sending (strongest change detection)
    verify_full: bool = False             # additionally compare a plain SHA-256 of the whole file

    # receiver policy
    auto_accept: bool = False
    max_file_size: int | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "transfers.db"

    @property
    def identity_dir(self) -> Path:
        return self.data_dir / "identity"
