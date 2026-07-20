"""Regression guard for the session-prefetch stability fix.

The speculative session prefetch (`CortexStore._spawn_session_prefetch`) used to fire an unbounded
number of daemon threads — one burst per session `/v1/context` call, fanout 3 — each running a FULL
max-budget `assemble_context` plus a write. A multi-turn agent firing rapid session context calls
spawned heavy background assembles faster than they finished; they opened their own sqlite-vec
connections while the foreground request was mid-connection on the same DB and wedged the server
(a live repro deadlocked at ~call 8, foreground blocked in `connect()` close while the warm blocked
in `connect()` open). These tests pin the two guarantees that fix it:

  1. The speculative warm is OFF by default (opt-in via CORTEX_SESSION_PREFETCH) — no background
     assemble/CPU on a guess, and no concurrent-connection contention, unless explicitly enabled.
  2. Even when enabled, the warm is bounded: at most one in-flight worker per user (drop, never
     queue) and a small server-wide cap, so it can never pile up again.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from backend.app.database import init_db
from backend.app.storage import CortexStore


class _StoreFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "index.sqlite"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def _live_prefetch_threads() -> int:
        return sum(1 for t in threading.enumerate() if t.name == "cortex-session-prefetch")


class TestPrefetchOffByDefault(_StoreFixture):
    def test_spawn_is_a_noop_when_flag_unset(self) -> None:
        """With CORTEX_SESSION_PREFETCH unset, _spawn_session_prefetch must not start any thread and
        must not warm anything — the speculative background assemble is entirely off."""
        env = {k: v for k, v in os.environ.items() if k != "CORTEX_SESSION_PREFETCH"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(self.store, "_warm_context_candidate") as warm:
                before = self._live_prefetch_threads()
                self.store._spawn_session_prefetch(
                    "user-default",
                    task="atlas launch readiness",
                    sector=None,
                    project=None,
                    centroid={},
                    entity_connections=[{"label": "Atlas"}],
                )
                # Give any (erroneously) spawned thread a chance to appear.
                time.sleep(0.2)
                self.assertEqual(self._live_prefetch_threads(), before, "prefetch spawned a thread while off")
                warm.assert_not_called()

    def test_explicit_off_values_stay_off(self) -> None:
        for value in ("0", "false", "off", "no", ""):
            with mock.patch.dict(os.environ, {"CORTEX_SESSION_PREFETCH": value}):
                with mock.patch.object(self.store, "_warm_context_candidate") as warm:
                    self.store._spawn_session_prefetch(
                        "u", task="t", sector=None, project=None, centroid={}, entity_connections=[]
                    )
                    time.sleep(0.05)
                    warm.assert_not_called()


class TestPrefetchGateBounds(_StoreFixture):
    """The slot gate that bounds the warm even when it is enabled."""

    def test_single_slot_per_user(self) -> None:
        self.assertTrue(self.store._acquire_prefetch_slot("u1"))
        self.assertFalse(self.store._acquire_prefetch_slot("u1"), "same user got a second concurrent slot")
        self.store._release_prefetch_slot("u1")
        self.assertTrue(self.store._acquire_prefetch_slot("u1"), "slot not freed after release")
        self.store._release_prefetch_slot("u1")

    def test_server_wide_cap(self) -> None:
        cap = self.store.SESSION_PREFETCH_MAX_INFLIGHT
        held = [f"user{i}" for i in range(cap * 3) if self.store._acquire_prefetch_slot(f"user{i}")]
        self.assertEqual(len(held), cap, f"server-wide cap breached: {len(held)} in flight (cap {cap})")
        # Releasing one frees a global slot for a brand-new user.
        self.store._release_prefetch_slot(held[0])
        self.assertTrue(self.store._acquire_prefetch_slot("newcomer"), "global slot not freed on release")
        for u in held[1:] + ["newcomer"]:
            self.store._release_prefetch_slot(u)

    def test_stray_release_is_safe(self) -> None:
        # A release with no matching acquire must never over-release the BoundedSemaphore.
        self.store._release_prefetch_slot("never-acquired")
        # And the cap is still intact afterward.
        cap = self.store.SESSION_PREFETCH_MAX_INFLIGHT
        held = [f"u{i}" for i in range(cap + 2) if self.store._acquire_prefetch_slot(f"u{i}")]
        self.assertEqual(len(held), cap)
        for u in held:
            self.store._release_prefetch_slot(u)

    def test_enabled_warm_is_bounded_to_one_per_user(self) -> None:
        """With the flag ON, a rapid burst of spawns for one user starts at most one warm worker at a
        time — the rest are dropped, not queued."""
        started = threading.Event()
        release = threading.Event()
        calls = {"n": 0}
        lock = threading.Lock()

        def _blocking_warm(user_id, task, sector, project):  # noqa: ANN001
            with lock:
                calls["n"] += 1
            started.set()
            release.wait(timeout=5)

        with mock.patch.dict(os.environ, {"CORTEX_SESSION_PREFETCH": "1"}):
            with mock.patch.object(self.store, "_warm_context_candidate", side_effect=_blocking_warm):
                for _ in range(10):
                    self.store._spawn_session_prefetch(
                        "burst-user",
                        task="atlas",
                        sector=None,
                        project=None,
                        centroid={},
                        entity_connections=[],
                    )
                self.assertTrue(started.wait(timeout=5), "no warm worker started")
                time.sleep(0.2)  # let any extra (wrongly-spawned) workers reach the counter
                with lock:
                    self.assertEqual(calls["n"], 1, "more than one concurrent warm ran for the same user")
                release.set()


if __name__ == "__main__":
    unittest.main()
