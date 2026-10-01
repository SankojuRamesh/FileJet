"""Integrity: per-chunk SHA-256 and a SHA-256 "file hash" over the chunk hash list.

Hashing strategy for huge files (each byte is hashed once per side, never re-read):
  * sender hashes each chunk right after reading it (already in RAM),
  * receiver hashes the received bytes (in RAM) and rejects mismatches (NACK -> resend),
  * the *file hash* is SHA-256(tag | size | chunk_size | h0 | h1 | ...), a one-level hash
    list. Both sides compute it from their own chunk hashes and must match exactly.
A conventional whole-file SHA-256 (``--verify-full``) is optional because it costs one
extra full read of the file on each side.
"""
from __future__ import annotations

import hashlib
import struct
from pathlib import Path

ROOT_TAG = b"p2pft-root-v1"


def chunk_digest(data) -> bytes:
    return hashlib.sha256(data).digest()


def root_hash(file_size: int, chunk_size: int, digests) -> str:
    h = hashlib.sha256(ROOT_TAG + struct.pack("!QQ", file_size, chunk_size))
    for d in digests:
        h.update(d)
    return h.hexdigest()


def file_sha256(path: Path, bufsize: int = 8 << 20, progress=None, stop=None) -> str:
    h = hashlib.sha256()
    buf = bytearray(bufsize)
    view = memoryview(buf)
    done = 0
    with open(path, "rb", buffering=0) as f:
        while True:
            if stop is not None and stop.is_set():
                raise InterruptedError("hashing cancelled")
            n = f.readinto(buf)
            if not n:
                break
            h.update(view[:n])
            done += n
            if progress:
                progress(done)
    return h.hexdigest()


def chunk_digests_of_file(path: Path, file_size: int, chunk_size: int, progress=None, stop=None) -> list[bytes]:
    """Hash every chunk of a file (used by --prehash and by the ``hash`` command)."""
    out = []
    buf = bytearray(chunk_size)
    view = memoryview(buf)
    done = 0
    with open(path, "rb", buffering=0) as f:
        while done < file_size:
            if stop is not None and stop.is_set():
                raise InterruptedError("hashing cancelled")
            want = min(chunk_size, file_size - done)
            pos = 0
            while pos < want:
                r = f.readinto(view[pos:want])
                if not r:
                    raise IOError("file shrank while hashing")
                pos += r
            out.append(chunk_digest(view[:want]))
            done += want
            if progress:
                progress(done)
    return out
