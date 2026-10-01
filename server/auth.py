"""Rate limiting for the signaling server (authentication itself is in presence.verify_signal_token)."""
from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    """Sliding window: at most ``limit`` events per ``window`` seconds per key."""

    def __init__(self, limit: int, window: float = 60.0):
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque] = defaultdict(deque)
        self._last_gc = time.monotonic()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and q[0] <= now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        if now - self._last_gc > self.window:
            self._last_gc = now
            for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]:
                del self._hits[k]
        return True
