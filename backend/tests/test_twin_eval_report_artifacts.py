from __future__ import annotations

import base64
import tempfile
import unittest
import zipfile
from pathlib import Path

from backend.app.database import init_db
from backend.app.keyring import (
    CXE1_MAGIC,
    DecryptionError,
    LocalKekProvider,
    UserKeyring,
)
from backend.app.sqlite_runtime import sqlite3
from backend.app.storage import CortexStore
from backend.app.twin_eval import (
    AllPairsStrategy,
    CitedProfileItem,
    ComparisonOutcome,
    DeterministicGenerator,
    EvaluationPrompt,
    HeldOutProfile,
    JudgeDecision,
    PairwiseEvaluationRunner,
    REPORT_ARTIFACT_ENCRYPTION_PURPOSE,
    ReportArtifactEncryptionUnavailable,
    ReportArtifactError,
    TwinEvalRepository,
    WinRateRanker,
)


KEK_B64 = base64.b64encode(bytes(reversed(range(32)))).decode("ascii")
PRIVATE_SENTINEL = "report-private-sentinel-93f02d"


class _SentinelJudge:
    judge_id = "report-encryption-fixture"
    requires_candidate_identity = False

    @staticmethod
    def reproducibility_config():
        return {"fixture": "encrypted-report"}

    @staticmethod
    def judge(prompt, profile, left, right, *, seed):
        return JudgeDecision(
            outcome=ComparisonOutcome.LEFT,
            rationale=f"private rationale {PRIVATE_SENTINEL}",
            cited_memory_ids=("memory-a",),
            confidence=0.91,
            metadata={"private_judge_metadata": PRIVATE_SENTINEL},
        )


class TwinEvalEncryptedReportArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "cortex.db"
        init_db(self.db_path)
        self.keyring = UserKeyring(
            self.root / "keyring.sqlite",
            LocalKekProvider(env={"CORTEX_KEK": KEK_B64}),
        )
        self.repository = TwinEvalRepository(
            self.db_path,
            artifact_cipher=self.keyring,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def _report(seed: int = 17):
        profile = HeldOutProfile(
            "private-profile",
            (
                CitedProfileItem(
                    "memory-a",
                    "A private preference that is not persisted here.",
                ),
            ),
        )
        prompt = EvaluationPrompt(
            f"prompt-{PRIVATE_SENTINEL}",
            f"Prompt {PRIVATE_SENTINEL}",
            metadata={"private_prompt_metadata": PRIVATE_SENTINEL},
        )
        return PairwiseEvaluationRunner(
            (
                DeterministicGenerator(
                    f"a-{PRIVATE_SENTINEL}",
                    lambda prompt, profile, seed: (
                        f"candidate a {PRIVATE_SENTINEL}"
                    ),
                ),
                DeterministicGenerator(
                    f"b-{PRIVATE_SENTINEL}",
                    lambda prompt, profile, seed: (
                        f"candidate b {PRIVATE_SENTINEL}"
                    ),
                ),
            ),
            _SentinelJudge(),
            AllPairsStrategy(shuffle=False),
            WinRateRanker(),
            blind_judge_inputs=False,
        ).run(profile, (prompt,), seed=seed)

    def test_encrypted_roundtrip_has_no_plaintext_duplicates(self) -> None:
        report = self._report()
        self.repository.save_report("user-a", report)

        self.assertEqual(
            self.repository.load_report("user-a", report.run_id),
            report,
        )
        replay = self.repository.replay_bundle("user-a", report.run_id)
        self.assertEqual(replay["artifact_digest"], report.artifact_digest)
        self.assertEqual(replay["report"]["run_id"], report.run_id)

        with sqlite3.connect(self.db_path) as conn:
            artifact = conn.execute(
                """
                SELECT artifact_ciphertext
                FROM twin_eval_report_artifacts
                WHERE user_id = ? AND run_id = ?
                """,
                ("user-a", report.run_id),
            ).fetchone()[0]
            run = conn.execute(
                """
                SELECT spec_json, report_json
                FROM twin_eval_runs
                WHERE user_id = ? AND run_id = ?
                """,
                ("user-a", report.run_id),
            ).fetchone()
            candidates = conn.execute(
                """
                SELECT candidate_json
                FROM twin_eval_candidates
                WHERE user_id = ? AND run_id = ?
                """,
                ("user-a", report.run_id),
            ).fetchall()
            comparisons = conn.execute(
                """
                SELECT comparison_json
                FROM twin_eval_comparisons
                WHERE user_id = ? AND run_id = ?
                """,
                ("user-a", report.run_id),
            ).fetchall()
        self.assertTrue(bytes(artifact).startswith(CXE1_MAGIC))
        for value in (run[0], run[1], *(row[0] for row in candidates), *(
            row[0] for row in comparisons
        )):
            self.assertNotIn(PRIVATE_SENTINEL, str(value))
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(self.db_path) + suffix)
            if path.exists():
                self.assertNotIn(PRIVATE_SENTINEL.encode(), path.read_bytes())

    def test_new_writes_fail_closed_without_cipher(self) -> None:
        report = self._report()
        with self.assertRaises(ReportArtifactEncryptionUnavailable):
            TwinEvalRepository(self.db_path).save_report("user-a", report)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM twin_eval_runs").fetchone()[0],
                0,
            )

    def test_legacy_plaintext_reads_require_explicit_local_mode(self) -> None:
        report = self._report(seed=41)
        local_repository = TwinEvalRepository(
            self.db_path,
            allow_plaintext_reports=True,
        )
        local_repository.save_report("legacy-user", report)
        with self.assertRaises(ReportArtifactEncryptionUnavailable):
            TwinEvalRepository(self.db_path).load_report(
                "legacy-user",
                report.run_id,
            )
        self.assertEqual(
            local_repository.load_report("legacy-user", report.run_id),
            report,
        )

    def test_outer_references_are_unlinkable_across_users(self) -> None:
        report = self._report(seed=42)
        self.repository.save_report("user-a", report)
        self.repository.save_report("user-b", report)
        with sqlite3.connect(self.db_path) as conn:
            refs_a = tuple(
                row[0]
                for row in conn.execute(
                    """
                    SELECT candidate_id FROM twin_eval_candidates
                    WHERE user_id = ? AND run_id = ?
                    ORDER BY candidate_id
                    """,
                    ("user-a", report.run_id),
                )
            )
            refs_b = tuple(
                row[0]
                for row in conn.execute(
                    """
                    SELECT candidate_id FROM twin_eval_candidates
                    WHERE user_id = ? AND run_id = ?
                    ORDER BY candidate_id
                    """,
                    ("user-b", report.run_id),
                )
            )
        self.assertNotEqual(refs_a, refs_b)
        self.assertEqual(
            self.repository.load_report("user-a", report.run_id),
            report,
        )
        self.assertEqual(
            self.repository.load_report("user-b", report.run_id),
            report,
        )

    def test_encrypted_load_requires_cipher_and_rejects_cross_run_swap(
        self,
    ) -> None:
        first = self._report(seed=1)
        second = self._report(seed=2)
        self.repository.save_report("user-a", first)
        self.repository.save_report("user-a", second)

        with self.assertRaises(ReportArtifactEncryptionUnavailable):
            TwinEvalRepository(self.db_path).load_report(
                "user-a",
                first.run_id,
            )

        with sqlite3.connect(self.db_path) as conn:
            first_ciphertext = conn.execute(
                """
                SELECT artifact_ciphertext
                FROM twin_eval_report_artifacts
                WHERE user_id = ? AND run_id = ?
                """,
                ("user-a", first.run_id),
            ).fetchone()[0]
            conn.execute(
                """
                UPDATE twin_eval_report_artifacts
                SET artifact_ciphertext = ?
                WHERE user_id = ? AND run_id = ?
                """,
                (first_ciphertext, "user-a", second.run_id),
            )
        with self.assertRaises(ReportArtifactError):
            self.repository.load_report("user-a", second.run_id)

    def test_missing_artifact_and_child_tampering_fail_replay(self) -> None:
        report = self._report()
        self.repository.save_report("user-a", report)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE twin_eval_candidates
                SET candidate_json = '{}'
                WHERE user_id = ? AND run_id = ?
                """,
                ("user-a", report.run_id),
            )
        with self.assertRaisesRegex(
            ValueError,
            "candidates verification failed",
        ):
            self.repository.replay_bundle("user-a", report.run_id)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                DELETE FROM twin_eval_report_artifacts
                WHERE user_id = ? AND run_id = ?
                """,
                ("user-a", report.run_id),
            )
        with self.assertRaises(ReportArtifactError):
            self.repository.load_report("user-a", report.run_id)

    def test_wrong_purpose_and_ciphertext_tampering_are_rejected(self) -> None:
        wrong_purpose_report = self._report(seed=31)
        tampered_report = self._report(seed=32)
        self.repository.save_report("user-a", wrong_purpose_report)
        self.repository.save_report("user-a", tampered_report)
        wrong_purpose = self.keyring.encrypt_blob(
            "user-a",
            "twin_eval_evidence",
            b"authenticated under the wrong purpose",
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE twin_eval_report_artifacts
                SET artifact_ciphertext = ?
                WHERE user_id = ? AND run_id = ?
                """,
                (wrong_purpose, "user-a", wrong_purpose_report.run_id),
            )
            row = conn.execute(
                """
                SELECT artifact_ciphertext
                FROM twin_eval_report_artifacts
                WHERE user_id = ? AND run_id = ?
                """,
                ("user-a", tampered_report.run_id),
            ).fetchone()
            tampered = bytearray(row[0])
            tampered[-1] ^= 1
            conn.execute(
                """
                UPDATE twin_eval_report_artifacts
                SET artifact_ciphertext = ?
                WHERE user_id = ? AND run_id = ?
                """,
                (bytes(tampered), "user-a", tampered_report.run_id),
            )
        with self.assertRaises(DecryptionError):
            self.repository.load_report(
                "user-a",
                wrong_purpose_report.run_id,
            )
        with self.assertRaises(DecryptionError):
            self.repository.load_report("user-a", tampered_report.run_id)
        ciphertext = self.keyring.encrypt_blob(
            "user-a",
            REPORT_ARTIFACT_ENCRYPTION_PURPOSE,
            b"report",
        )
        self.assertTrue(ciphertext.startswith(CXE1_MAGIC))

    def test_encrypted_outer_summary_corruption_is_detected(self) -> None:
        cases = (
            ("twin_eval_runs", "seed_json", "seed"),
            ("twin_eval_runs", "manifest_json", "manifest"),
            (
                "twin_eval_ranking_manifests",
                "ranking_json",
                "ranking manifest",
            ),
        )
        for index, (table, column, expected_error) in enumerate(cases):
            with self.subTest(column=column):
                report = self._report(seed=60 + index)
                self.repository.save_report("user-a", report)
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        f"""
                        UPDATE {table} SET {column} = '{{}}'
                        WHERE user_id = ? AND run_id = ?
                        """,
                        ("user-a", report.run_id),
                    )
                with self.assertRaisesRegex(
                    ValueError,
                    expected_error,
                ):
                    self.repository.replay_bundle(
                        "user-a",
                        report.run_id,
                    )

    def test_routine_backup_excludes_encrypted_full_report(self) -> None:
        report = self._report()
        self.repository.save_report("user-a", report)
        store = CortexStore(self.db_path, self.root / "vault")
        backup = store.create_backup("user-a")

        self.assertEqual(
            backup["excluded_twin_eval_report_artifacts"],
            1,
        )
        self.assertEqual(backup["excluded_twin_eval_runs"], 1)
        backup_db = self.root / "backup-index.sqlite"
        with zipfile.ZipFile(backup["backup_path"]) as archive:
            backup_db.write_bytes(archive.read("index.sqlite"))
        with sqlite3.connect(backup_db) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM twin_eval_report_artifacts"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM twin_eval_runs"
                ).fetchone()[0],
                0,
            )
        self.assertNotIn(PRIVATE_SENTINEL.encode(), backup_db.read_bytes())


if __name__ == "__main__":
    unittest.main()
