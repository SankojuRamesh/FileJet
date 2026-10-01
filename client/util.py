"""Formatting, parsing and filename helpers.

Units: throughput and sizes shown to users are decimal (1 MB = 10^6 bytes) so that
"125 MB/s" really means 1 Gbit/s. Sizes typed on the command line (``--chunk-size 8M``)
are binary (8M = 8 MiB), which is what chunk sizes conventionally mean.
"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def fmt_bytes(n: float | int | None) -> str:
    if n is None:
        return "-"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} PB"


def fmt_rate(bps: float | None) -> str:
    if not bps:
        return "0.0 MB/s"
    return f"{bps / 1e6:.1f} MB/s"


def fmt_duration(seconds: float | None) -> str:
    if seconds is None or seconds != seconds or seconds == float("inf"):
        return "-"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {sec:02d}s"
    if m:
        return f"{m}m {sec:02d}s"
    return f"{sec}s"


def parse_size(text: str | int) -> int:
    if isinstance(text, int):
        return text
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgt]?)(i?b)?\s*", str(text), re.I)
    if not m:
        raise ValueError(f"invalid size: {text!r}")
    mult = {"": 1, "k": 1 << 10, "m": 1 << 20, "g": 1 << 30, "t": 1 << 40}[m.group(2).lower()]
    return int(float(m.group(1)) * mult)


def sanitize_filename(name: str) -> str:
    """Make a sender-supplied file name safe: no directories, traversal or device names."""
    name = unicodedata.normalize("NFC", str(name))
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch >= " " and ch != "\x7f")
    name = re.sub(r'[<>:"|?*]', "_", name).strip().rstrip(". ")
    if name in ("", ".", ".."):
        name = "received_file"
    stem = name.split(".")[0].upper()
    if stem in _WIN_RESERVED:
        name = "_" + name
    if len(name.encode()) > 200:
        root, ext = os.path.splitext(name)
        name = root.encode()[: 200 - len(ext.encode())].decode(errors="ignore") + ext
    return name


def unique_path(directory: Path, name: str) -> Path:
    """First non-existing path for ``name`` (also avoiding a leftover ``.part``)."""
    root, ext = os.path.splitext(name)
    candidate = directory / name
    i = 1
    while candidate.exists() or candidate.with_name(candidate.name + ".part").exists():
        candidate = directory / f"{root} ({i}){ext}"
        i += 1
    return candidate


def progress_bar(fraction: float, width: int = 30) -> str:
    fraction = min(max(fraction, 0.0), 1.0)
    full = int(fraction * width)
    return "█" * full + "░" * (width - full)
