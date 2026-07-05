from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import connect, init_db
from backend.app.storage import CortexStore, MEMORY_JOB_LEASE_SECONDS, _parse_iso_timestamp


class JobReaperTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path)
        self.user_id = "reaper-user"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _insert_running_job(self, job_id: str, *, attempts: int, max_attempts: int, locked_until: str) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_jobs
                (id, user_id, job_type, object_type, object_id, status, priority, run_at,
                 attempts, max_attempts, locked_by, locked_until, unique_key, payload_json,
                 result_json, created_at, updated_at)
                VALUES (?, ?, 'extract_capture', 'capture', ?, 'running', 100, ?, ?, ?,
                        'dead-worker', ?, ?, '{}', '{}', ?, ?)
                """,
                (
                    job_id,
                    self.user_id,
                    f"obj_{job_id}",
                    "2026-01-01T00:00:00Z",
                    attempts,
                    max_attempts,
                    locked_until,
                    f"key_{job_id}",
                    "2026-01-01T00:00:00Z",
                    locked_until,
                ),
            )
            conn.commit()

    def _status(self, job_id: str) -> str:
        with connect(self.db_path) as conn:
            return conn.execute("SELECT status FROM memory_jobs WHERE id = ?", (job_id,)).fetchone()[0]

    def test_expired_lease_running_job_is_reclaimed(self) -> None:
        # A worker died holding this job; its lease is far in the past and it has attempts left.
        self._insert_running_job("orphan", attempts=1, max_attempts=3, locked_until="2000-01-01T00:00:00Z")
        claimed = self.store._claim_next_job(self.user_id, "fresh-worker")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["id"], "orphan")
        self.assertEqual(claimed["status"], "running")
        # attempts incremented on the reclaiming claim; a fresh future lease is held.
        self.assertEqual(claimed["attempts"], 2)
        lease = _parse_iso_timestamp(claimed["locked_until"])
        now = _parse_iso_timestamp(claimed["updated_at"])
        self.assertIsNotNone(lease)
        self.assertGreater((lease - now).total_seconds(), MEMORY_JOB_LEASE_SECONDS - 5)

    def test_expired_lease_out_of_attempts_fails_terminally(self) -> None:
        # A poison job that already used all attempts must not be reclaimed forever.
        self._insert_running_job("poison", attempts=3, max_attempts=3, locked_until="2000-01-01T00:00:00Z")
        claimed = self.store._claim_next_job(self.user_id, "fresh-worker")
        self.assertIsNone(claimed)
        self.assertEqual(self._status("poison"), "failed")

    def test_live_lease_running_job_is_not_touched(self) -> None:
        # A healthy in-flight job (lease in the future) must never be reclaimed.
        self._insert_running_job("healthy", attempts=1, max_attempts=3, locked_until="2099-01-01T00:00:00Z")
        claimed = self.store._claim_next_job(self.user_id, "fresh-worker")
        self.assertIsNone(claimed)
        self.assertEqual(self._status("healthy"), "running")

    def test_fresh_claim_sets_future_lease(self) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_jobs
                (id, user_id, job_type, object_type, object_id, status, priority, run_at,
                 attempts, max_attempts, unique_key, payload_json, result_json, created_at, updated_at)
                VALUES ('q1', ?, 'extract_capture', 'capture', 'objq', 'queued', 100,
                        '2000-01-01T00:00:00Z', 0, 3, 'keyq', '{}', '{}',
                        '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
                """,
                (self.user_id,),
            )
            conn.commit()
        claimed = self.store._claim_next_job(self.user_id, "worker")
        self.assertEqual(claimed["id"], "q1")
        lease = _parse_iso_timestamp(claimed["locked_until"])
        updated = _parse_iso_timestamp(claimed["updated_at"])
        self.assertGreater((lease - updated).total_seconds(), MEMORY_JOB_LEASE_SECONDS - 5)


class FailJobBackoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path)
        self.user_id = "backoff-user"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_requeued_job_gets_future_run_at(self) -> None:
        # A transient failure with attempts remaining requeues with a backoff, not run_at=now.
        job = {
            "id": "b1",
            "user_id": self.user_id,
            "object_type": "memory",
            "object_id": "m1",
            "job_type": "embed_memory",
            "attempts": 1,
            "max_attempts": 3,
        }
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_jobs
                (id, user_id, job_type, object_type, object_id, status, priority, run_at,
                 attempts, max_attempts, unique_key, payload_json, result_json, created_at, updated_at)
                VALUES ('b1', ?, 'embed_memory', 'memory', 'm1', 'running', 100,
                        '2026-01-01T00:00:00Z', 1, 3, 'keyb1', '{}', '{}',
                        '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
                """,
                (self.user_id,),
            )
            conn.commit()
        result = self.store._fail_job(job, "transient 429")
        self.assertEqual(result["status"], "queued")
        run_at = _parse_iso_timestamp(result["run_at"])
        updated = _parse_iso_timestamp(result["updated_at"])
        self.assertGreater((run_at - updated).total_seconds(), 0)

    def test_final_failure_marks_failed(self) -> None:
        job = {
            "id": "b2",
            "user_id": self.user_id,
            "object_type": "memory",
            "object_id": "m2",
            "job_type": "embed_memory",
            "attempts": 3,
            "max_attempts": 3,
        }
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_jobs
                (id, user_id, job_type, object_type, object_id, status, priority, run_at,
                 attempts, max_attempts, unique_key, payload_json, result_json, created_at, updated_at)
                VALUES ('b2', ?, 'embed_memory', 'memory', 'm2', 'running', 100,
                        '2026-01-01T00:00:00Z', 3, 3, 'keyb2', '{}', '{}',
                        '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
                """,
                (self.user_id,),
            )
            conn.commit()
        result = self.store._fail_job(job, "permanent error")
        self.assertEqual(result["status"], "failed")


class JobRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path)
        self.user_id = "retention-user"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _insert(self, job_id: str, status: str, completed_at: str | None) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_jobs
                (id, user_id, job_type, object_type, object_id, status, priority, run_at,
                 attempts, max_attempts, unique_key, payload_json, result_json, created_at,
                 updated_at, completed_at)
                VALUES (?, ?, 'extract_capture', 'capture', ?, ?, 100, '2000-01-01T00:00:00Z',
                        1, 3, ?, '{}', '{}', ?, ?, ?)
                """,
                (
                    job_id,
                    self.user_id,
                    f"obj_{job_id}",
                    status,
                    f"key_{job_id}",
                    completed_at or "2000-01-01T00:00:00Z",
                    completed_at or "2000-01-01T00:00:00Z",
                    completed_at,
                ),
            )
            conn.commit()

    def _ids(self) -> set[str]:
        with connect(self.db_path) as conn:
            return {row[0] for row in conn.execute("SELECT id FROM memory_jobs WHERE user_id = ?", (self.user_id,))}

    def test_prune_removes_old_terminal_jobs_only(self) -> None:
        from datetime import datetime, timezone

        recent = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self._insert("old_ok", "succeeded", "2000-01-01T00:00:00Z")
        self._insert("old_fail", "failed", "2000-01-01T00:00:00Z")
        self._insert("recent_ok", "succeeded", recent)
        # queued job (never terminal) with an ancient created_at must be kept.
        self._insert("queued_old", "queued", None)
        with connect(self.db_path) as conn:
            conn.execute("UPDATE memory_jobs SET status='queued', completed_at=NULL WHERE id='queued_old'")
            conn.commit()

        removed = self.store._prune_terminal_jobs(self.user_id)
        self.assertEqual(removed, 2)
        remaining = self._ids()
        self.assertNotIn("old_ok", remaining)
        self.assertNotIn("old_fail", remaining)
        self.assertIn("recent_ok", remaining)
        self.assertIn("queued_old", remaining)


if __name__ == "__main__":
    unittest.main()
