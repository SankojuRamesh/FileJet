"""Chunk layout, bitmaps, bounded buffer pools and positional file I/O.

All offsets are Python ints (arbitrary precision) and go through 64-bit syscalls, so there
is no 2 GB / 4 GB limit anywhere. Buffers come from a fixed pool, which bounds RAM use to
``pool_size * chunk_size`` regardless of file size and provides back-pressure.
"""
from __future__ import annotations

import base64
import errno
import os
import queue
import threading
import zlib
from dataclasses import dataclass
from pathlib import Path

MiB = 1 << 20


class FileChangedError(Exception):
    pass


def choose_chunk_size(file_size: int) -> int:
    """Default chunk size. 8 MiB won the loopback benchmark for large files (see README);
    small files use smaller chunks so that several streams still get work."""
    if file_size < 64 * MiB:
        return 1 * MiB
    if file_size < 1024 * MiB:
        return 4 * MiB
    return 8 * MiB


@dataclass(frozen=True)
class ChunkLayout:
    file_size: int
    chunk_size: int

    @property
    def count(self) -> int:
        return (self.file_size + self.chunk_size - 1) // self.chunk_size

    def offset(self, idx: int) -> int:
        return idx * self.chunk_size

    def length(self, idx: int) -> int:
        return max(0, min(self.chunk_size, self.file_size - idx * self.chunk_size))


class Bitmap:
    def __init__(self, count: int, data: bytearray | None = None):
        self.count = count
        self.bits = data if data is not None else bytearray((count + 7) // 8)
        self._n = sum(bin(b).count("1") for b in self.bits) if data is not None else 0

    def get(self, i: int) -> bool:
        return bool(self.bits[i >> 3] & (1 << (i & 7)))

    def set(self, i: int) -> bool:
        """Set bit i; returns True if it was newly set."""
        mask = 1 << (i & 7)
        if self.bits[i >> 3] & mask:
            return False
        self.bits[i >> 3] |= mask
        self._n += 1
        return True

    def num_set(self) -> int:
        return self._n

    def complete(self) -> bool:
        return self._n >= self.count

    def copy(self) -> "Bitmap":
        return Bitmap(self.count, bytearray(self.bits))

    def to_b64(self) -> str:
        return base64.b64encode(zlib.compress(bytes(self.bits), 6)).decode()

    @classmethod
    def from_b64(cls, text: str, count: int) -> "Bitmap":
        raw = zlib.decompress(base64.b64decode(text), bufsize=(count + 7) // 8 + 16)
        if len(raw) != (count + 7) // 8:
            raise ValueError("bitmap size mismatch")
        bm = cls(count, bytearray(raw))
        if count % 8 and bm.bits:        # ignore garbage in padding bits
            bm.bits[-1] &= (1 << (count % 8)) - 1
            bm._n = sum(bin(b).count("1") for b in bm.bits)
        return bm

    @classmethod
    def from_indices(cls, count: int, indices) -> "Bitmap":
        bm = cls(count)
        for i in indices:
            if 0 <= i < count:
                bm.set(i)
        return bm


class BufferPool:
    """Fixed set of reusable chunk buffers; ``acquire`` blocks when all are in use."""

    def __init__(self, count: int, size: int):
        self.size = size
        self.count = count
        self._q: queue.Queue = queue.Queue()
        for _ in range(count):
            self._q.put(bytearray(size))

    def acquire(self, stop: threading.Event, timeout: float = 0.25) -> bytearray | None:
        while not stop.is_set():
            try:
                return self._q.get(timeout=timeout)
            except queue.Empty:
                continue
        return None

    def release(self, buf: bytearray) -> None:
        self._q.put(buf)

    def available(self) -> int:
        return self._q.qsize()


def pool_size(chunk_size: int, streams: int, budget: int, wanted: int) -> int:
    return max(streams + 1, min(wanted, budget // chunk_size))


class ChunkReader:
    """Reads chunks at arbitrary offsets. One instance per thread (Windows has no pread)."""

    def __init__(self, path: Path, expected_size: int, expected_mtime_ns: int):
        self.path = path
        self.expected = (expected_size, expected_mtime_ns)
        self.f = open(path, "rb", buffering=0)
        self._pread = getattr(os, "preadv", None) and hasattr(os, "pread")

    def check_unchanged(self) -> None:
        st = os.stat(self.path)
        if (st.st_size, st.st_mtime_ns) != self.expected:
            raise FileChangedError(f"{self.path.name} was modified during the transfer "
                                   f"(size/mtime changed) - aborting to prevent silent corruption")

    def read_into(self, offset: int, view: memoryview) -> None:
        n, pos = len(view), 0
        if self._pread:
            fd = self.f.fileno()
            while pos < n:
                r = os.preadv(fd, [view[pos:]], offset + pos)
                if r == 0:
                    raise FileChangedError("file is shorter than expected")
                pos += r
        else:
            self.f.seek(offset)
            while pos < n:
                r = self.f.readinto(view[pos:])
                if not r:
                    raise FileChangedError("file is shorter than expected")
                pos += r
        self.check_unchanged()

    def close(self) -> None:
        self.f.close()


class DiskFullError(OSError):
    pass


def _is_disk_full(exc: OSError) -> bool:
    return exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)) or getattr(exc, "winerror", None) in (39, 112)


class ChunkWriter:
    """Writes chunks into the preallocated ``.part`` file."""

    def __init__(self, path: Path, size: int, preallocate: bool = True):
        self.path = path
        self._lock = threading.Lock()
        try:
            self.f = open(path, "r+b" if path.exists() else "w+b", buffering=0)
            current = os.fstat(self.f.fileno()).st_size
            if current > size:
                self.f.truncate(size)
            elif current < size and preallocate and hasattr(os, "posix_fallocate"):
                # Linux/BSD: reserve real blocks (unwritten extents) -> early "disk full", no fragmentation.
                try:
                    os.posix_fallocate(self.f.fileno(), 0, size)
                except OSError as exc:
                    if _is_disk_full(exc):
                        raise
            # Windows: deliberately NOT extended up front. Extending an NTFS file ahead of its valid
            # data length measured 67.9 MB/s vs 246.7 MB/s for the same sequential copy (bench notes in
            # README); the file grows as chunks arrive, free space is checked before the transfer.
        except OSError as exc:
            if _is_disk_full(exc):
                raise DiskFullError(exc.errno or errno.ENOSPC, "not enough disk space for the file") from exc
            raise
        self._pwrite = hasattr(os, "pwritev")

    def size_on_disk(self) -> int:
        return os.fstat(self.f.fileno()).st_size

    def write_at(self, offset: int, view: memoryview) -> None:
        n, pos = len(view), 0
        try:
            if self._pwrite:
                fd = self.f.fileno()
                while pos < n:
                    pos += os.pwritev(fd, [view[pos:]], offset + pos)
            else:
                with self._lock:
                    self.f.seek(offset)
                    while pos < n:
                        pos += self.f.write(view[pos:])
        except OSError as exc:
            if _is_disk_full(exc):
                raise DiskFullError(exc.errno or errno.ENOSPC, "disk full while writing") from exc
            raise

    def read_at(self, offset: int, view: memoryview) -> None:
        with self._lock:
            self.f.seek(offset)
            pos = 0
            while pos < len(view):
                r = self.f.readinto(view[pos:])
                if not r:
                    raise IOError("short read")
                pos += r

    def sync(self) -> None:
        os.fsync(self.f.fileno())

    def close(self) -> None:
        try:
            self.f.close()
        except OSError:
            pass
