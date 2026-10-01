"""Throughput measurement: current / average / peak speed, ETA, process CPU and RAM."""
from __future__ import annotations

import threading
import time
from collections import deque

try:
    import psutil
    _PROC = psutil.Process()
    _PROC.cpu_percent(None)
except Exception:          # psutil is optional
    _PROC = None


class SpeedMeter:
    """Counts application payload bytes. ``tick()`` is called by a monitor every ~0.5 s."""

    WINDOW = 2.0

    def __init__(self, total: int, already: int = 0):
        self.total = total
        self._lock = threading.Lock()
        self.done = already
        self.session_bytes = 0
        self.t0 = time.monotonic()
        self._samples: deque = deque([(self.t0, 0)])
        self.current = 0.0
        self.peak = 0.0

    def add(self, n: int) -> None:
        with self._lock:
            self.done += n
            self.session_bytes += n

    def set_done(self, done: int) -> None:
        with self._lock:
            self.done = done

    def tick(self) -> None:
        now = time.monotonic()
        with self._lock:
            sb = self.session_bytes
        self._samples.append((now, sb))
        while len(self._samples) > 2 and now - self._samples[0][0] > self.WINDOW:
            self._samples.popleft()
        t_old, b_old = self._samples[0]
        if now - t_old >= 0.4:
            self.current = (sb - b_old) / (now - t_old)
            if now - t_old >= 0.9:
                self.peak = max(self.peak, self.current)

    @property
    def average(self) -> float:
        dt = time.monotonic() - self.t0
        return self.session_bytes / dt if dt > 0 else 0.0

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def eta(self, done: int | None = None) -> float | None:
        done = self.done if done is None else done
        remaining = max(0, self.total - done)
        rate = self.current or self.average
        if remaining == 0:
            return 0.0
        return remaining / rate if rate > 0 else None


class RateAverager:
    """Exponentially weighted rate of an I/O operation (e.g. disk read MB/s)."""

    def __init__(self, alpha: float = 0.2):
        self.alpha = alpha
        self.value = 0.0
        self._lock = threading.Lock()

    def add(self, nbytes: int, seconds: float) -> None:
        if seconds <= 0:
            return
        r = nbytes / seconds
        with self._lock:
            self.value = r if self.value == 0 else self.value + self.alpha * (r - self.value)


def process_usage() -> dict:
    if _PROC is None:
        return {}
    try:
        return {"cpu_percent": _PROC.cpu_percent(None), "rss": _PROC.memory_info().rss}
    except Exception:
        return {}


class TokenBucket:
    """Optional sender bandwidth cap (--limit)."""

    def __init__(self, rate: float):
        self.rate = float(rate)
        self.tokens = 0.0
        self.last = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, n: int, stop: threading.Event) -> None:
        while not stop.is_set():
            with self._lock:
                now = time.monotonic()
                self.tokens = min(self.rate, self.tokens + (now - self.last) * self.rate)
                self.last = now
                if self.tokens >= n or n > self.rate and self.tokens >= self.rate * 0.99:
                    self.tokens -= n
                    return
                wait = (min(n, self.rate) - self.tokens) / self.rate
            stop.wait(min(max(wait, 0.001), 0.25))
