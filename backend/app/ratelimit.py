from __future__ import annotations

import threading
import time
from collections import OrderedDict
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
        max_buckets: int = 50_000,
        idle_ttl_seconds: float = 600.0,
    ) -> None:
        self.rate_per_minute = max(0, int(rate_per_minute or 0))
        self.rate_per_second = self.rate_per_minute / 60.0
        # Allow a short burst up to one minute's worth of requests by default.
        self.capacity = float(burst if burst is not None else max(1, self.rate_per_minute))
        self.enabled = self.rate_per_minute > 0
        self._time_fn = time_fn
        self.max_buckets = max(1, int(max_buckets or 1))
        self.idle_ttl_seconds = max(1.0, float(idle_ttl_seconds or 1.0))
        self._buckets: "OrderedDict[str, list[float]]" = OrderedDict()
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
                if not self._evict_for_new_bucket(now):
                    # Never reset an actively blocked key just to admit a new
                    # attacker-controlled identity. A saturated limiter fails
                    # closed until an existing bucket expires.
                    return False, self.idle_ttl_seconds
                tokens, last = self.capacity, now
            else:
                tokens, last = state
                self._buckets.move_to_end(normalized)
                tokens = min(self.capacity, tokens + (now - last) * self.rate_per_second)
            if tokens >= 1.0:
                self._buckets[normalized] = [tokens - 1.0, now]
                return True, 0.0
            self._buckets[normalized] = [tokens, now]
            if self.rate_per_second <= 0:
                return False, 60.0
            return False, max(0.0, (1.0 - tokens) / self.rate_per_second)

    def _evict_for_new_bucket(self, now: float) -> bool:
        # OrderedDict is access-ordered, so expired entries are clustered at the
        # front. A fully refilled bucket carries no meaningful enforcement
        # state and is also safe to reclaim before the longer idle TTL.
        # Saturation cleanup is deliberately bounded: a flood of novel keys
        # must not turn every check into an O(max_buckets) scan under the mutex.
        # Non-reclaimable entries rotate to the back so later calls continue
        # the sweep instead of repeatedly examining the same oldest bucket.
        scan_budget = min(128, len(self._buckets))
        for _ in range(scan_budget):
            bucket_key, (tokens, last) = next(iter(self._buckets.items()))
            elapsed = max(0.0, now - last)
            refilled = min(self.capacity, tokens + elapsed * self.rate_per_second)
            if elapsed > self.idle_ttl_seconds or refilled >= self.capacity:
                self._buckets.pop(bucket_key, None)
            else:
                self._buckets.move_to_end(bucket_key)
        return len(self._buckets) < self.max_buckets

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(str(key or "").strip() or "anonymous", None)
