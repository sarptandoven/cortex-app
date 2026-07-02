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


if __name__ == "__main__":
    unittest.main()
