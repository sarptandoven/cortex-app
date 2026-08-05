from __future__ import annotations

import threading
import time
from typing import Callable


class TokenBucketRateLimiter:
    """Thread-safe per-key token-bucket rate limiter.

    Used to bound per-user request rate so a single tenant cannot degrade
    availability for the other 10k users. Buckets are kept in-process, so in a
    multi-instance deployment the effective limit is per instance; that is a
    deliberate, simple starting point (a shared store would be needed for a
    strict global limit). Disabled entirely when `rate_per_minute <= 0`.
    """

    def __init__(
        self,
        rate_per_minute: int,
        *,
        burst: int | None = None,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate_per_minute = max(0, int(rate_per_minute or 0))
        self.rate_per_second = self.rate_per_minute / 60.0
        # Allow a short burst up to one minute's worth of requests by default.
        self.capacity = float(burst if burst is not None else max(1, self.rate_per_minute))
        self.enabled = self.rate_per_minute > 0
        self._time_fn = time_fn
        self._buckets: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, float]:
        """Consume one token for `key`. Returns (allowed, retry_after_seconds)."""
        if not self.enabled:
            return True, 0.0
        normalized = str(key or "").strip() or "anonymous"
        now = self._time_fn()
        with self._lock:
            state = self._buckets.get(normalized)
            if state is None:
                tokens, last = self.capacity, now
            else:
                tokens, last = state
                tokens = min(self.capacity, tokens + (now - last) * self.rate_per_second)
            if tokens >= 1.0:
                self._buckets[normalized] = [tokens - 1.0, now]
                return True, 0.0
            self._buckets[normalized] = [tokens, now]
            if self.rate_per_second <= 0:
                return False, 60.0
            return False, max(0.0, (1.0 - tokens) / self.rate_per_second)

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(str(key or "").strip() or "anonymous", None)
