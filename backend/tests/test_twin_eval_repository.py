from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from backend.app.database import init_db
from backend.app.sqlite_runtime import sqlite3
from backend.app.twin_eval import (
    BradleyTerryRanker,
    CitedProfileItem,
    DeterministicGenerator,
    EvaluationArtifactCollision,
    EvaluationPrompt,
    HeldOutProfile,
    OracleJudge,
    PairwiseEvaluationRunner,
    RepeatedSwappedStrategy,
    TwinEvalRepository,
    canonical_json,
)


class TwinEvalRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "cortex.db"
        init_db(self.db_path)
        self.repository = TwinEvalRepository(self.db_path)
        self.user_id = "twin-user"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def _report(seed: int = 17, *, trial_id: str | None = None):
        profile = HeldOutProfile(
            "held-out-1",
            (
                CitedProfileItem(
                    "mem-style",
                    "Use short, direct sentences.",
                    "cortex-eval://style",
                    "style",
                ),
            ),
        )
        prompts = (
            EvaluationPrompt("email", "Write a project update."),
            EvaluationPrompt("meeting", "Decline a meeting."),
        )
        runner = PairwiseEvaluationRunner(
            (
                DeterministicGenerator("twin", lambda prompt, profile, seed: "Short update."),
                DeterministicGenerator("baseline", lambda prompt, profile, seed: "A verbose update."),
            ),
            OracleJudge({prompt.prompt_id: "twin" for prompt in prompts}),
            RepeatedSwappedStrategy(),
            BradleyTerryRanker(),
            blind_judge_inputs=False,
        )
        return runner.run(profile, prompts, seed=seed, trial_id=trial_id)

    def test_fresh_database_has_dedicated_tables(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'twin_eval_%'"
                )
            }
        self.assertEqual(
            names,
            {
                "twin_eval_runs",
                "twin_eval_candidates",
                "twin_eval_comparisons",
                "twin_eval_resolved_comparisons",
                "twin_eval_rankings",
                "twin_eval_ranking_manifests",
            },
        )

    def test_roundtrip_and_replay_bundle(self) -> None:
        report = self._report()
        digest = self.repository.save_report(self.user_id, report)
        self.assertEqual(digest, report.artifact_digest)
        self.assertEqual(self.repository.load_report(self.user_id, report.run_id), report)
        bundle = self.repository.replay_bundle(self.user_id, report.run_id)
        self.assertEqual(bundle["artifact_digest"], report.artifact_digest)
        self.assertEqual(bundle["manifest"]["comparison_count"], len(report.comparisons))
        with sqlite3.connect(self.db_path) as conn:
            event_count = conn.execute(
                "SELECT COUNT(*) FROM memory_events WHERE object_type = 'twin_eval'"
            ).fetchone()[0]
        self.assertEqual(event_count, 0)

    def test_compact_storage_does_not_repeat_candidate_text_per_comparison(self) -> None:
        profile = HeldOutProfile(
            "compact-profile",
            (CitedProfileItem("memory", "Prefer system a."),),
        )
        prompt = EvaluationPrompt("compact-prompt", "Choose an answer.")
        report = PairwiseEvaluationRunner(
            tuple(
                DeterministicGenerator(
                    system_id,
                    lambda prompt, profile, seed, system_id=system_id: (
                        system_id + ("x" * 9_999)
                    ),
                )
                for system_id in ("a", "b", "c")
            ),
            OracleJudge(
                {"compact-prompt": {"a": 3.0, "b": 2.0, "c": 1.0}}
            ),
            RepeatedSwappedStrategy(repetitions=3, shuffle=False),
            BradleyTerryRanker(),
            blind_judge_inputs=False,
        ).run(profile, (prompt,), seed=11)

        self.repository.save_report(self.user_id, report)
        with sqlite3.connect(self.db_path) as conn:
            stored_json = conn.execute(
                """
                SELECT report_json FROM twin_eval_runs
                WHERE user_id = ? AND run_id = ?
                """,
                (self.user_id, report.run_id),
            ).fetchone()[0]
            child_payloads = [
                row[0]
                for row in conn.execute(
                    """
                    SELECT comparison_json FROM twin_eval_comparisons
                    WHERE user_id = ? AND run_id = ?
                    """,
                    (self.user_id, report.run_id),
                )
            ]

        stored = json.loads(stored_json)
        self.assertEqual(
            stored["schema_version"],
            "pairwise-twin-artifact/v2",
        )
        self.assertLess(len(stored_json), len(canonical_json(report)) / 3)
        self.assertTrue(
            all("left_candidate_id" in json.loads(payload) for payload in child_payloads)
        )
        self.assertTrue(
            all("x" * 1_000 not in payload for payload in child_payloads)
        )
        self.assertEqual(
            self.repository.load_report(self.user_id, report.run_id),
            report,
        )

    def test_same_artifact_save_is_idempotent(self) -> None:
        report = self._report()
        first = self.repository.save_report(self.user_id, report)
        second = self.repository.save_report(self.user_id, report)
        self.assertEqual(first, second)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM twin_eval_runs").fetchone()[0], 1)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM twin_eval_comparisons").fetchone()[0],
                len(report.comparisons),
            )

    def test_identical_replicates_with_trial_ids_are_stored_separately(self) -> None:
        first = self._report(trial_id="replicate-1")
        second = self._report(trial_id="replicate-2")
        self.assertEqual(first.metadata["spec_id"], second.metadata["spec_id"])
        self.assertEqual(first.comparisons, second.comparisons)
        self.assertNotEqual(first.run_id, second.run_id)

        self.repository.save_report(self.user_id, first)
        self.repository.save_report(self.user_id, second)

        self.assertEqual(
            self.repository.load_report(self.user_id, first.run_id),
            first,
        )
        self.assertEqual(
            self.repository.load_report(self.user_id, second.run_id),
            second,
        )
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM twin_eval_runs WHERE user_id = ?",
                    (self.user_id,),
                ).fetchone()[0],
                2,
            )

    def test_same_run_id_different_artifact_is_rejected(self) -> None:
        report = self._report()
        self.repository.save_report(self.user_id, report)
        collision = replace(report, metadata={"changed": True})
        with self.assertRaises(EvaluationArtifactCollision):
            self.repository.save_report(self.user_id, collision)
        self.assertEqual(self.repository.load_report(self.user_id, report.run_id), report)

    def test_runs_are_isolated_by_user(self) -> None:
        report = self._report()
        self.repository.save_report("user-a", report)
        with self.assertRaises(KeyError):
            self.repository.load_report("user-b", report.run_id)
        self.repository.save_report("user-b", report)
        self.assertEqual(self.repository.load_report("user-b", report.run_id), report)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM twin_eval_runs").fetchone()[0], 2)

    def test_delete_report_is_exact_atomic_and_user_scoped(self) -> None:
        report = self._report()
        self.repository.save_report("user-a", report)
        self.repository.save_report("user-b", report)
        with self.assertRaises(EvaluationArtifactCollision):
            self.repository.delete_report(
                "user-a",
                report.run_id,
                expected_artifact_digest="artifact_wrong",
            )
        self.assertEqual(self.repository.load_report("user-a", report.run_id), report)

        self.assertTrue(
            self.repository.delete_report(
                "user-a",
                report.run_id,
                expected_artifact_digest=report.artifact_digest,
            )
        )
        self.assertFalse(self.repository.delete_report("user-a", report.run_id))
        with self.assertRaises(KeyError):
            self.repository.load_report("user-a", report.run_id)
        self.assertEqual(self.repository.load_report("user-b", report.run_id), report)
        with sqlite3.connect(self.db_path) as conn:
            for table in (
                "twin_eval_runs",
                "twin_eval_candidates",
                "twin_eval_comparisons",
                "twin_eval_resolved_comparisons",
                "twin_eval_rankings",
                "twin_eval_ranking_manifests",
            ):
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE user_id = 'user-a'"
                ).fetchone()[0]
                self.assertEqual(count, 0, table)

    def test_retention_purge_is_bounded_and_user_scoped(self) -> None:
        old = self._report(seed=1)
        recent = self._report(seed=2)
        self.repository.save_report("user-a", old)
        self.repository.save_report("user-a", recent)
        self.repository.save_report("user-b", old)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE twin_eval_runs SET created_at = '2020-01-01T00:00:00Z'
                WHERE user_id = 'user-a' AND run_id = ?
                """,
                (old.run_id,),
            )

        purged = self.repository.purge_reports_before(
            "user-a",
            "2021-01-01T00:00:00Z",
            limit=1,
        )
        self.assertEqual(purged, (old.run_id,))
        with self.assertRaises(KeyError):
            self.repository.load_report("user-a", old.run_id)
        self.assertEqual(self.repository.load_report("user-a", recent.run_id), recent)
        self.assertEqual(self.repository.load_report("user-b", old.run_id), old)

    def test_replay_rejects_corrupted_spec_or_manifest(self) -> None:
        for column in ("spec_json", "manifest_json"):
            with self.subTest(column=column):
                report = self._report()
                self.repository.save_report(self.user_id, report)
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        f"UPDATE twin_eval_runs SET {column} = '{{}}' WHERE user_id = ? AND run_id = ?",
                        (self.user_id, report.run_id),
                    )
                with self.assertRaisesRegex(ValueError, "verification failed"):
                    self.repository.replay_bundle(self.user_id, report.run_id)
                # Restore the canonical artifact for the next subtest.
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        "DELETE FROM twin_eval_rankings WHERE user_id = ? AND run_id = ?",
                        (self.user_id, report.run_id),
                    )
                    conn.execute(
                        "DELETE FROM twin_eval_resolved_comparisons WHERE user_id = ? AND run_id = ?",
                        (self.user_id, report.run_id),
                    )
                    conn.execute(
                        "DELETE FROM twin_eval_comparisons WHERE user_id = ? AND run_id = ?",
                        (self.user_id, report.run_id),
                    )
                    conn.execute(
                        "DELETE FROM twin_eval_ranking_manifests WHERE user_id = ? AND run_id = ?",
                        (self.user_id, report.run_id),
                    )
                    conn.execute(
                        "DELETE FROM twin_eval_candidates WHERE user_id = ? AND run_id = ?",
                        (self.user_id, report.run_id),
                    )
                    conn.execute(
                        "DELETE FROM twin_eval_runs WHERE user_id = ? AND run_id = ?",
                        (self.user_id, report.run_id),
                    )

    def test_replay_rejects_corrupted_or_missing_child_rows(self) -> None:
        report = self._report()
        self.repository.save_report(self.user_id, report)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE twin_eval_candidates SET candidate_json = '{}'
                WHERE user_id = ? AND run_id = ? AND candidate_id = (
                  SELECT candidate_id FROM twin_eval_candidates
                  WHERE user_id = ? AND run_id = ? LIMIT 1
                )
                """,
                (self.user_id, report.run_id, self.user_id, report.run_id),
            )
        with self.assertRaisesRegex(ValueError, "candidates verification failed"):
            self.repository.replay_bundle(self.user_id, report.run_id)

        # A second isolated database verifies that deletion is detected too.
        missing_path = Path(self._tmp.name) / "missing-child.db"
        init_db(missing_path)
        missing_repo = TwinEvalRepository(missing_path)
        missing_repo.save_report(self.user_id, report)
        with sqlite3.connect(missing_path) as conn:
            conn.execute(
                """
                DELETE FROM twin_eval_comparisons
                WHERE user_id = ? AND run_id = ? AND comparison_id = (
                  SELECT comparison_id FROM twin_eval_comparisons
                  WHERE user_id = ? AND run_id = ? LIMIT 1
                )
                """,
                (self.user_id, report.run_id, self.user_id, report.run_id),
            )
        with self.assertRaisesRegex(ValueError, "comparisons verification failed"):
            missing_repo.replay_bundle(self.user_id, report.run_id)

    def test_init_db_migrates_existing_database_idempotently(self) -> None:
        migrated_path = Path(self._tmp.name) / "legacy.db"
        with sqlite3.connect(migrated_path) as conn:
            conn.execute("CREATE TABLE legacy_data (id TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO legacy_data VALUES ('preserved')")
        init_db(migrated_path)
        init_db(migrated_path)
        with sqlite3.connect(migrated_path) as conn:
            self.assertEqual(conn.execute("SELECT id FROM legacy_data").fetchone()[0], "preserved")
            self.assertIsNotNone(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'twin_eval_runs'"
                ).fetchone()
            )


if __name__ == "__main__":
    unittest.main()
