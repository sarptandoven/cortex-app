from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from backend.app.sqlite_runtime import sqlite3


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "pairwise_twin_eval.py"
RETENTION_SCRIPT = ROOT / "scripts" / "pairwise_twin_retention.py"


class PairwiseTwinCliTests(unittest.TestCase):
    def test_persist_and_replay_emit_redacted_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "cortex.sqlite"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--seed",
                    "7",
                    "--db-path",
                    str(db_path),
                    "--user-id",
                    "cli-user",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            result = json.loads(completed.stdout)
            self.assertTrue(result["persisted"])
            self.assertTrue(result["passed"])

            replayed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--db-path",
                    str(db_path),
                    "--user-id",
                    "cli-user",
                    "--replay-run-id",
                    result["run_id"],
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            replay = json.loads(replayed.stdout)
            self.assertEqual(replay["status"], "replayed")
            self.assertEqual(replay["run_id"], result["run_id"])
            self.assertEqual(replay["artifact_digest"], result["artifact_digest"])
            self.assertFalse(replay["content_included"])
            self.assertNotIn("Use short, direct sentences", replayed.stdout)
            self.assertNotIn("tests pass. ship monday.", replayed.stdout)

    def test_replay_requires_database_path(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--replay-run-id", "twin_eval_missing"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("--replay-run-id requires --db-path", completed.stderr)

    def test_retention_requires_matching_preview_before_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "cortex.sqlite"
            created = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--seed",
                    "7",
                    "--db-path",
                    str(db_path),
                    "--user-id",
                    "cli-user",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            run_id = json.loads(created.stdout)["run_id"]
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "UPDATE twin_eval_runs SET created_at = ? WHERE run_id = ?",
                    ("2000-01-01 00:00:00", run_id),
                )
                conn.commit()
            finally:
                conn.close()
            common = [
                "--db-path",
                str(db_path),
                "--user-id",
                "cli-user",
                "--retention-days",
                "90",
                "--as-of",
                "2026-01-01T00:00:00+00:00",
            ]
            previewed = subprocess.run(
                [sys.executable, str(RETENTION_SCRIPT), "preview", *common],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            preview = json.loads(previewed.stdout)
            self.assertEqual(preview["eligible_count"], 1)
            rejected = subprocess.run(
                [
                    sys.executable,
                    str(RETENTION_SCRIPT),
                    "apply",
                    *common,
                    "--expected-preview-digest",
                    "wrong",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            applied = subprocess.run(
                [
                    sys.executable,
                    str(RETENTION_SCRIPT),
                    "apply",
                    *common,
                    "--expected-preview-digest",
                    preview["preview_digest"],
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(json.loads(applied.stdout)["deleted_count"], 1)


if __name__ == "__main__":
    unittest.main()
