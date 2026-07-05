from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.config import Settings
from backend.app.database import connect, init_db
from backend.app.sharding import StoreRegistry
from backend.app.storage import CortexStore
from backend.app.worker import discover_worker_user_ids, normalize_worker_user_ids, run_worker_tick


class CortexWorkerRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "cortex-test.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path)
        self.user_id = "worker-user"
        self.store.update_settings(self.user_id, {"allow_pending_in_context": True})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_worker_tick_processes_queued_capture(self) -> None:
        phrase = "Worker runner should process the Meridian capture into searchable memory."
        queued = self.store.enqueue_capture(
            user_id=self.user_id,
            content=phrase,
            source="unit-test-worker",
            source_url="local-file://Worker%20Runner.md#line=4",
            title="Worker runner fixture",
        )

        self.assertEqual(queued["status"], "queued")
        self.assertEqual(self.store.search(self.user_id, "Meridian capture"), [])

        tick = run_worker_tick(
            self.store,
            [self.user_id],
            limit_per_user=20,
            worker_id="test-worker",
        )

        self.assertGreaterEqual(tick["processed"], 1)
        self.assertEqual(tick["failed"], 0)
        self.assertEqual(tick["users"][self.user_id]["failed"], 0)
        self.assertTrue(self.store.search(self.user_id, "Meridian capture"))
        self.assertEqual(tick["failed_jobs"], [])

    def test_worker_tick_reports_failed_jobs(self) -> None:
        with connect(self.db_path) as conn:
            job = self.store._enqueue_job(
                conn,
                user_id=self.user_id,
                job_type="unsupported_fixture",
                object_type="worker-test",
                object_id="unsupported-fixture",
                unique_key="unsupported-fixture-job",
                payload={"fixture": True},
                max_attempts=1,
            )

        tick = run_worker_tick(
            self.store,
            [self.user_id],
            limit_per_user=1,
            worker_id="test-worker",
        )

        self.assertEqual(tick["processed"], 1)
        self.assertEqual(tick["failed"], 1)
        self.assertEqual(tick["failed_jobs"][0]["id"], job["id"])
        self.assertEqual(tick["failed_jobs"][0]["job_type"], "unsupported_fixture")
        self.assertIn("Unsupported memory job type", tick["failed_jobs"][0]["last_error"])
        self.assertEqual(self.store.get_job(self.user_id, job["id"])["status"], "failed")

    def test_worker_user_ids_are_stable_and_deduplicated(self) -> None:
        self.assertEqual(
            normalize_worker_user_ids(["", "alpha", "alpha", " beta "], default_user_id="local"),
            ["local", "alpha", "beta"],
        )

    def _hosted_registry(self, mode: str = "user") -> StoreRegistry:
        root = Path(self.tmp.name) / mode
        settings = Settings(
            vault_path=root / "vault",
            db_path=root / "index.sqlite",
            api_key="test-token",
            public_base_url="http://127.0.0.1:8766",
            default_user_id="hosted-default",
            shard_mode=mode,
            shard_root=root / "shards",
        )
        return StoreRegistry.from_settings(settings)

    def test_discover_worker_user_ids_local_mode_uses_default_user(self) -> None:
        registry = self._hosted_registry(mode="local")
        self.assertEqual(
            discover_worker_user_ids(registry, default_user_id="local", shard_mode="local"),
            ["local"],
        )

    def test_discover_worker_user_ids_hosted_enumerates_active_users(self) -> None:
        registry = self._hosted_registry(mode="user")
        registry.provision_user("alice")
        registry.provision_user("bob")

        discovered = discover_worker_user_ids(registry, default_user_id="hosted-default", shard_mode="user")
        self.assertEqual(set(discovered), {"alice", "bob"})

        # Suspended users are excluded so the worker pauses their jobs.
        registry.suspend_user("bob")
        self.assertEqual(
            discover_worker_user_ids(registry, default_user_id="hosted-default", shard_mode="user"),
            ["alice"],
        )

    def test_discover_worker_user_ids_falls_back_to_default_when_empty(self) -> None:
        registry = self._hosted_registry(mode="user")
        self.assertEqual(
            discover_worker_user_ids(registry, default_user_id="hosted-default", shard_mode="user"),
            ["hosted-default"],
        )

    def test_worker_tick_summarizes_scheduled_source_syncs(self) -> None:
        class FakeStore:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def run_due_jobs(self, user_id: str, *, limit: int, worker_id: str) -> dict:
                self.calls.append(user_id)
                if user_id == "alpha":
                    return {
                        "processed": 1,
                        "pending": 2,
                        "failed": 0,
                        "scheduled_source_syncs": {"scheduled": 2, "jobs": [], "skipped": [{"source": "gmail"}]},
                    }
                return {
                    "processed": 0,
                    "pending": 0,
                    "failed": 1,
                    "scheduled_source_syncs": {"scheduled": 1, "jobs": [], "skipped": []},
                }

            def list_jobs(self, user_id: str, *, status: str, limit: int) -> list:
                return []

        store = FakeStore()
        tick = run_worker_tick(store, ["alpha", "beta"], limit_per_user=10, worker_id="test-worker")

        self.assertEqual(store.calls, ["alpha", "beta"])
        self.assertEqual(tick["processed"], 1)
        self.assertEqual(tick["pending"], 2)
        self.assertEqual(tick["failed"], 1)
        self.assertEqual(tick["source_syncs_scheduled"], 3)
        self.assertEqual(tick["source_syncs_skipped"], 1)
        self.assertEqual(tick["users"]["alpha"]["scheduled_source_syncs"]["scheduled"], 2)


if __name__ == "__main__":
    unittest.main()
