from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import connect, init_db
from backend.app.storage import CortexStore, _duration_seconds, _percentile_summary


class PercentileSummaryTests(unittest.TestCase):
    def test_empty_summary_is_zeroed(self) -> None:
        self.assertEqual(
            _percentile_summary([]),
            {"count": 0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0},
        )

    def test_nearest_rank_percentiles(self) -> None:
        summary = _percentile_summary([float(value) for value in range(1, 101)])
        self.assertEqual(summary["count"], 100)
        self.assertEqual(summary["p50"], 50.0)
        self.assertEqual(summary["p95"], 95.0)
        self.assertEqual(summary["p99"], 99.0)
        self.assertEqual(summary["max"], 100.0)

    def test_duration_seconds_handles_missing_and_valid(self) -> None:
        self.assertIsNone(_duration_seconds(None, "2026-01-01T00:00:01Z"))
        self.assertIsNone(_duration_seconds("2026-01-01T00:00:01Z", None))
        self.assertEqual(_duration_seconds("2026-01-01T00:00:00Z", "2026-01-01T00:00:05Z"), 5.0)
        # Negative durations are clamped to 0 (clock skew guard).
        self.assertEqual(_duration_seconds("2026-01-01T00:00:05Z", "2026-01-01T00:00:00Z"), 0.0)


class JobHealthPercentileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path)
        self.user_id = "jobs-user"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _insert_succeeded_job(self, index: int, job_type: str, duration_seconds: int) -> None:
        created = f"2026-01-01T00:00:{index:02d}Z"
        completed = f"2026-01-01T00:00:{index + duration_seconds:02d}Z"
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_jobs
                (id, user_id, job_type, object_type, object_id, status, priority, run_at,
                 attempts, max_attempts, unique_key, payload_json, result_json,
                 created_at, updated_at, completed_at)
                VALUES (?, ?, ?, 'capture', ?, 'succeeded', 100, ?, 1, 3, ?, '{}', '{}', ?, ?, ?)
                """,
                (
                    f"job_{index}",
                    self.user_id,
                    job_type,
                    f"obj_{index}",
                    created,
                    f"unique_{index}",
                    created,
                    completed,
                    completed,
                ),
            )
            conn.commit()

    def _insert_queued_job(self, index: int) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_jobs
                (id, user_id, job_type, object_type, object_id, status, priority, run_at,
                 attempts, max_attempts, unique_key, payload_json, result_json,
                 created_at, updated_at)
                VALUES (?, ?, 'extract_capture', 'capture', ?, 'queued', 100, ?, 0, 3, ?, '{}', '{}', ?, ?)
                """,
                (
                    f"queued_{index}",
                    self.user_id,
                    f"qobj_{index}",
                    "2000-01-01T00:00:00Z",
                    f"queued_unique_{index}",
                    "2000-01-01T00:00:00Z",
                    "2000-01-01T00:00:00Z",
                ),
            )
            conn.commit()

    def test_job_health_reports_latency_and_queue_age_percentiles(self) -> None:
        # Succeeded extract_capture jobs with end-to-end durations 1..4s.
        for i, duration in enumerate((1, 2, 3, 4), start=1):
            self._insert_succeeded_job(i, "extract_capture", duration)
        # A slower embed_memory job to prove per-type grouping.
        self._insert_succeeded_job(30, "embed_memory", 10)
        # Two long-waiting queued jobs.
        self._insert_queued_job(1)
        self._insert_queued_job(2)

        health = self.store.job_health(self.user_id)

        overall = health["latency_seconds"]["overall"]
        self.assertEqual(overall["count"], 5)
        self.assertEqual(overall["max"], 10.0)
        self.assertEqual(overall["p99"], 10.0)

        by_type = health["latency_seconds"]["by_type"]
        self.assertEqual(by_type["extract_capture"]["count"], 4)
        self.assertEqual(by_type["extract_capture"]["max"], 4.0)
        self.assertEqual(by_type["embed_memory"]["count"], 1)
        self.assertEqual(by_type["embed_memory"]["p50"], 10.0)

        queue_age = health["queue_age_seconds"]
        self.assertEqual(queue_age["count"], 2)
        # Queued far in the past, so ages are large and non-zero.
        self.assertGreater(queue_age["p50"], 0.0)

    def test_job_health_percentiles_empty_when_no_jobs(self) -> None:
        health = self.store.job_health(self.user_id)
        self.assertEqual(health["latency_seconds"]["overall"]["count"], 0)
        self.assertEqual(health["latency_seconds"]["by_type"], {})
        self.assertEqual(health["queue_age_seconds"]["count"], 0)


if __name__ == "__main__":
    unittest.main()
