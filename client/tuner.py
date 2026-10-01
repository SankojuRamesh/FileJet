"""Adaptive tuning of the number of active data streams (hill climbing on measured goodput).

All ``max_streams`` TCP connections are opened up front; the tuner decides how many of
them carry chunks. Every window it compares the measured application throughput:

  * after adding a stream: keep it if goodput rose >= 5 %, otherwise step back and hold;
  * while holding: re-probe upwards periodically, or when goodput drops by > 25 %
    (congestion, path change) probe downwards/upwards again.

This is deliberately conservative - streams are only kept when they measurably help, so a
disk- or receiver-bound transfer settles at few streams instead of oscillating. Chunk size
is fixed per transfer (chunk indices are part of the resume state), but chosen from file
size at start; buffering is bounded by the memory budget.
"""
from __future__ import annotations

import time


class AutoTuner:
    def __init__(self, max_streams: int, initial: int, enabled: bool = True, window: float = 3.0):
        self.max = max(1, max_streams)
        self.active = max(1, min(initial, self.max))
        self.enabled = enabled
        self.window = window
        self._win_start = None
        self._win_bytes = 0
        self._baseline = None
        self._last_action = None
        self._hold = 0
        self.history: list[tuple[float, int, float]] = []

    def tick(self, total_bytes: int, now: float | None = None, limited: bool = False) -> None:
        now = time.monotonic() if now is None else now
        if self._win_start is None:
            self._win_start, self._win_bytes = now, total_bytes
            return
        dt = now - self._win_start
        if dt < self.window:
            return
        rate = (total_bytes - self._win_bytes) / dt
        self._win_start, self._win_bytes = now, total_bytes
        self.history.append((now, self.active, rate))
        if not self.enabled or limited:
            return
        if self._baseline is None:
            self._baseline = rate
            self._try_up()
            return
        if self._last_action == "up":
            if rate >= self._baseline * 1.05:
                self._baseline = rate
                self._try_up()
            else:
                self.active = max(1, self.active - 1)
                self._last_action, self._hold = "revert", 5
            return
        if self._last_action == "revert":
            self._baseline = rate
            self._last_action = "hold"
            return
        # holding
        if rate < self._baseline * 0.75:
            self._baseline = rate
            self._try_up()
            return
        self._baseline = 0.8 * self._baseline + 0.2 * rate
        self._hold -= 1
        if self._hold <= 0:
            self._try_up()

    def _try_up(self) -> None:
        if self.active < self.max:
            self.active += 1
            self._last_action = "up"
        else:
            self._last_action, self._hold = "hold", 10
