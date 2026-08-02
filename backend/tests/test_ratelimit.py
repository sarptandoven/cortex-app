from __future__ import annotations

import unittest

from backend.app.ratelimit import TokenBucketRateLimiter


class TokenBucketRateLimiterTests(unittest.TestCase):
    def test_disabled_limiter_allows_everything(self) -> None:
        limiter = TokenBucketRateLimiter(0)
        self.assertFalse(limiter.enabled)
        for _ in range(1000):
            allowed, retry_after = limiter.check("user")
            self.assertTrue(allowed)
            self.assertEqual(retry_after, 0.0)

    def test_allows_burst_then_blocks_and_refills(self) -> None:
        clock = {"t": 0.0}
        limiter = TokenBucketRateLimiter(60, burst=3, time_fn=lambda: clock["t"])

        # Three burst tokens are available immediately.
        self.assertEqual([limiter.check("user")[0] for _ in range(3)], [True, True, True])

        blocked, retry_after = limiter.check("user")
        self.assertFalse(blocked)
        self.assertGreater(retry_after, 0.0)

        # 60/min == 1 token/sec: after one second exactly one request is allowed.
        clock["t"] = 1.0
        self.assertTrue(limiter.check("user")[0])
        self.assertFalse(limiter.check("user")[0])

    def test_buckets_are_per_key(self) -> None:
        clock = {"t": 0.0}
        limiter = TokenBucketRateLimiter(60, burst=1, time_fn=lambda: clock["t"])
        self.assertTrue(limiter.check("alice")[0])
        self.assertFalse(limiter.check("alice")[0])
        # Bob has an independent bucket and is unaffected by alice's usage.
        self.assertTrue(limiter.check("bob")[0])

    def test_reset_clears_bucket(self) -> None:
        clock = {"t": 0.0}
        limiter = TokenBucketRateLimiter(60, burst=1, time_fn=lambda: clock["t"])
        self.assertTrue(limiter.check("alice")[0])
        self.assertFalse(limiter.check("alice")[0])
        limiter.reset("alice")
        self.assertTrue(limiter.check("alice")[0])

    def test_bucket_map_fails_closed_instead_of_resetting_active_keys(self) -> None:
        limiter = TokenBucketRateLimiter(
            60,
            burst=1,
            max_buckets=3,
            idle_ttl_seconds=100,
            time_fn=lambda: 0.0,
        )
        for key in ("a", "b", "c"):
            self.assertTrue(limiter.check(key)[0])
        self.assertFalse(limiter.check("d")[0])
        self.assertEqual(list(limiter._buckets), ["a", "b", "c"])
        # Flooding novel keys cannot evict and reset a key that has exhausted
        # its bucket.
        self.assertFalse(limiter.check("a")[0])

    def test_idle_buckets_are_evicted_before_active_buckets(self) -> None:
        clock = {"t": 0.0}
        limiter = TokenBucketRateLimiter(
            60,
            burst=1,
            max_buckets=3,
            idle_ttl_seconds=10,
            time_fn=lambda: clock["t"],
        )
        limiter.check("old-a")
        limiter.check("old-b")
        clock["t"] = 11.0
        limiter.check("new")
        self.assertEqual(list(limiter._buckets), ["new"])

    def test_fully_refilled_bucket_is_reclaimable_before_idle_ttl(self) -> None:
        clock = {"t": 0.0}
        limiter = TokenBucketRateLimiter(
            60,
            burst=1,
            max_buckets=1,
            idle_ttl_seconds=600,
            time_fn=lambda: clock["t"],
        )
        self.assertTrue(limiter.check("finished")[0])
        self.assertFalse(limiter.check("new")[0])
        clock["t"] = 1.0
        self.assertTrue(limiter.check("new")[0])
        self.assertEqual(list(limiter._buckets), ["new"])


if __name__ == "__main__":
    unittest.main()
