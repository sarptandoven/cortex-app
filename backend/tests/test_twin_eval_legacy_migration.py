from __future__ import annotations

import base64
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from backend.app.database import connect, init_db
from backend.app.database_maintenance import DatabaseMaintenanceBusy
from backend.app.keyring import LocalKekProvider, UserKeyring
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
    ReportArtifactError,
    TwinEvalRepository,
    WinRateRanker,
)


KEK_B64 = base64.b64encode(bytes(range(32))).decode("ascii")
LEGACY_SENTINEL = b"legacy-private-sentinel-73ac91"


class _LegacyJudge:
    judge_id = "legacy-migration-fixture"
    requires_candidate_identity = False

    @staticmethod
    def reproducibility_config():
        return {"fixture": "legacy-migration"}

    @staticmethod
    def judge(prompt, profile, left, right, *, seed):
        return JudgeDecision(
            outcome=ComparisonOutcome.LEFT,
            rationale=f"rationale {LEGACY_SENTINEL.decode()}",
            cited_memory_ids=("memory",),
            confidence=0.8,
            metadata={"private": LEGACY_SENTINEL.decode()},
        )


class TwinEvalLegacyMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "cortex.db"
        init_db(self.db_path)
        self.keyring = UserKeyring(
            self.root / "keyring.sqlite",
            LocalKekProvider(env={"CORTEX_KEK": KEK_B64}),
        )
        self.plaintext = TwinEvalRepository(
            self.db_path,
            allow_plaintext_reports=True,
        )
        self.encrypted = TwinEvalRepository(
            self.db_path,
            artifact_cipher=self.keyring,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def _report(seed: int):
        sentinel = LEGACY_SENTINEL.decode()
        profile = HeldOutProfile(
            "legacy-profile",
            (CitedProfileItem("memory", "private preference"),),
        )
        prompt = EvaluationPrompt(
            f"prompt-{sentinel}",
            f"prompt body {sentinel}",
            metadata={"private": sentinel},
        )
        return PairwiseEvaluationRunner(
            (
                DeterministicGenerator(
                    f"a-{sentinel}",
                    lambda prompt, profile, seed: (
                        f"candidate a {LEGACY_SENTINEL.decode()}"
                    ),
                ),
                DeterministicGenerator(
                    f"b-{sentinel}",
                    lambda prompt, profile, seed: (
                        f"candidate b {LEGACY_SENTINEL.decode()}"
                    ),
                ),
            ),
            _LegacyJudge(),
            AllPairsStrategy(shuffle=False),
            WinRateRanker(),
            blind_judge_inputs=False,
        ).run(profile, (prompt,), seed=seed)

    def test_mixed_inventory_and_bounded_exact_migration(self) -> None:
        legacy_reports = (self._report(1), self._report(2))
        encrypted_report = self._report(3)
        for report in legacy_reports:
            self.plaintext.save_report("user-a", report)
        other_user_report = self._report(4)
        self.plaintext.save_report("user-b", other_user_report)
        self.encrypted.save_report("user-a", encrypted_report)

        preview = self.encrypted.preview_legacy_report_migration(
            "user-a",
            limit=1,
        )
        self.assertEqual(preview.legacy_count, 2)
        self.assertEqual(preview.encrypted_count, 1)
        self.assertEqual(len(preview.run_ids), 1)
        migrated_report = {
            report.run_id: report for report in legacy_reports
        }[preview.run_ids[0]]
        result = self.encrypted.migrate_legacy_reports(
            "user-a",
            expected_run_ids=preview.run_ids,
            expected_selection_digest=preview.selection_digest,
        )
        self.assertEqual(result.migrated_run_ids, preview.run_ids)
        retry = self.encrypted.migrate_legacy_reports(
            "user-a",
            expected_run_ids=preview.run_ids,
            expected_selection_digest=preview.selection_digest,
        )
        self.assertEqual(retry.migrated_run_ids, ())
        self.assertEqual(
            retry.already_migrated_run_ids,
            preview.run_ids,
        )
        self.assertEqual(
            self.encrypted.load_report(
                "user-a",
                migrated_report.run_id,
            ),
            migrated_report,
        )

        remaining = self.encrypted.preview_legacy_report_migration(
            "user-a"
        )
        self.encrypted.migrate_legacy_reports(
            "user-a",
            expected_run_ids=remaining.run_ids,
            expected_selection_digest=remaining.selection_digest,
        )
        audit = self.encrypted.audit_report_storage(
            "user-a",
            require_clean=True,
        )
        self.assertTrue(audit["clean"])
        self.assertEqual(audit["legacy_count"], 0)
        self.assertEqual(audit["encrypted_count"], 3)
        self.assertEqual(
            self.plaintext.load_report(
                "user-b",
                other_user_report.run_id,
            ),
            other_user_report,
        )

    def test_preview_receipt_does_not_substitute_newly_discovered_run(
        self,
    ) -> None:
        first = self._report(10)
        second = self._report(11)
        self.plaintext.save_report("user-a", first)
        preview = self.encrypted.preview_legacy_report_migration(
            "user-a",
            limit=1,
        )
        self.plaintext.save_report("user-a", second)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE twin_eval_runs
                SET created_at = '1900-01-01T00:00:00Z'
                WHERE user_id = ? AND run_id = ?
                """,
                ("user-a", second.run_id),
            )
        result = self.encrypted.migrate_legacy_reports(
            "user-a",
            expected_run_ids=preview.run_ids,
            expected_selection_digest=preview.selection_digest,
        )
        self.assertEqual(result.migrated_run_ids, preview.run_ids)
        remaining = self.encrypted.preview_legacy_report_migration(
            "user-a"
        )
        self.assertIn(second.run_id, remaining.run_ids)

    def test_malformed_legacy_bundle_blocks_before_writes(self) -> None:
        report = self._report(20)
        self.plaintext.save_report("user-a", report)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                DELETE FROM twin_eval_comparisons
                WHERE user_id = ? AND run_id = ?
                """,
                ("user-a", report.run_id),
            )
        preview = self.encrypted.preview_legacy_report_migration(
            "user-a"
        )
        with self.assertRaises(ValueError):
            self.encrypted.migrate_legacy_reports(
                "user-a",
                expected_run_ids=preview.run_ids,
                expected_selection_digest=preview.selection_digest,
            )
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM twin_eval_report_artifacts"
                ).fetchone()[0],
                0,
            )

    def test_partial_batch_retry_is_idempotent(self) -> None:
        reports = (self._report(25), self._report(26))
        for report in reports:
            self.plaintext.save_report("user-a", report)
        preview = self.encrypted.preview_legacy_report_migration(
            "user-a",
            limit=2,
        )

        class _InterruptingRepository(TwinEvalRepository):
            attempts = 0

            def _migrate_one_legacy_report(
                self,
                user_id,
                run_id,
                *,
                cipher,
            ):
                self.attempts += 1
                if self.attempts == 2:
                    raise RuntimeError("simulated interruption")
                return super()._migrate_one_legacy_report(
                    user_id,
                    run_id,
                    cipher=cipher,
                )

        interrupted = _InterruptingRepository(
            self.db_path,
            artifact_cipher=self.keyring,
        )
        with self.assertRaisesRegex(RuntimeError, "interruption"):
            interrupted.migrate_legacy_reports(
                "user-a",
                expected_run_ids=preview.run_ids,
                expected_selection_digest=preview.selection_digest,
            )

        retried = self.encrypted.migrate_legacy_reports(
            "user-a",
            expected_run_ids=preview.run_ids,
            expected_selection_digest=preview.selection_digest,
        )
        self.assertEqual(len(retried.already_migrated_run_ids), 1)
        self.assertEqual(len(retried.migrated_run_ids), 1)
        self.assertEqual(
            set(retried.already_migrated_run_ids)
            | set(retried.migrated_run_ids),
            set(preview.run_ids),
        )

    def test_new_graph_verification_failure_rolls_back_plaintext(
        self,
    ) -> None:
        report = self._report(27)
        self.plaintext.save_report("user-a", report)
        preview = self.encrypted.preview_legacy_report_migration(
            "user-a"
        )

        class _RejectingRepository(TwinEvalRepository):
            def replay_bundle(self, user_id, run_id, *, _conn=None):
                result = super().replay_bundle(
                    user_id,
                    run_id,
                    _conn=_conn,
                )
                if _conn is not None:
                    stored = _conn.execute(
                        """
                        SELECT report_json FROM twin_eval_runs
                        WHERE user_id = ? AND run_id = ?
                        """,
                        (user_id, run_id),
                    ).fetchone()[0]
                    if "encrypted-report-ref" in stored:
                        raise ValueError(
                            "simulated new-graph verification failure"
                        )
                return result

        rejecting = _RejectingRepository(
            self.db_path,
            artifact_cipher=self.keyring,
        )
        with self.assertRaisesRegex(ValueError, "new-graph"):
            rejecting.migrate_legacy_reports(
                "user-a",
                expected_run_ids=preview.run_ids,
                expected_selection_digest=preview.selection_digest,
            )
        self.assertEqual(
            self.plaintext.load_report("user-a", report.run_id),
            report,
        )
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM twin_eval_report_artifacts"
                ).fetchone()[0],
                0,
            )

    def test_finalize_scrubs_main_database_and_wal_bytes(self) -> None:
        report = self._report(30)
        self.plaintext.save_report("user-a", report)
        preview = self.encrypted.preview_legacy_report_migration(
            "user-a"
        )
        self.encrypted.migrate_legacy_reports(
            "user-a",
            expected_run_ids=preview.run_ids,
            expected_selection_digest=preview.selection_digest,
        )
        with self.assertRaises(ReportArtifactError):
            self.encrypted.finalize_legacy_report_migration()
        result = self.encrypted.finalize_legacy_report_migration(
            exclusive_maintenance=True,
        )
        self.assertEqual(result["integrity_check"], ("ok",))
        self.assertEqual(result["verified_report_count"], 1)
        self.assertTrue(
            result["exclusive_maintenance_fence_acquired"]
        )
        self.assertTrue(result["backup_remediation_required"])
        for suffix in ("", "-wal", "-shm", "-journal"):
            path = Path(str(self.db_path) + suffix)
            if path.exists():
                self.assertNotIn(LEGACY_SENTINEL, path.read_bytes())

    def test_finalize_refuses_active_local_connection(self) -> None:
        self.encrypted.save_report("user-a", self._report(31))
        with connect(self.db_path):
            with self.assertRaisesRegex(
                ReportArtifactError,
                "connections and processes",
            ):
                self.encrypted.finalize_legacy_report_migration(
                    exclusive_maintenance=True,
                )
        result = self.encrypted.finalize_legacy_report_migration(
            exclusive_maintenance=True,
        )
        self.assertTrue(
            result["exclusive_maintenance_fence_acquired"]
        )

    def test_repository_context_releases_fence_and_bypass_is_owned(
        self,
    ) -> None:
        self.encrypted.save_report("user-a", self._report(33))
        with self.assertRaises(DatabaseMaintenanceBusy):
            self.encrypted._connect(maintenance_bypass=True)
        with self.encrypted._connect() as conn:
            row = conn.execute("SELECT 1 AS value").fetchone()
            self.assertEqual(row["value"], 1)
        result = self.encrypted.finalize_legacy_report_migration(
            exclusive_maintenance=True,
        )
        self.assertTrue(
            result["exclusive_maintenance_fence_acquired"]
        )

    def test_finalize_refuses_connection_in_another_process(
        self,
    ) -> None:
        self.encrypted.save_report("user-a", self._report(32))
        child_code = """
import sys
from pathlib import Path
from backend.app.database_maintenance import shared_database_access
with shared_database_access(Path(sys.argv[1])):
    print("locked", flush=True)
    sys.stdin.readline()
"""
        child = subprocess.Popen(
            [sys.executable, "-c", child_code, str(self.db_path)],
            cwd=Path(__file__).parents[2],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(
                child.stdout.readline().strip(),
                "locked",
            )
            with self.assertRaisesRegex(
                ReportArtifactError,
                "connections and processes",
            ):
                self.encrypted.finalize_legacy_report_migration(
                    exclusive_maintenance=True,
                )
        finally:
            child.communicate("\n", timeout=5)
        result = self.encrypted.finalize_legacy_report_migration(
            exclusive_maintenance=True,
        )
        self.assertTrue(
            result["exclusive_maintenance_fence_acquired"]
        )

    def test_routine_backup_physically_omits_legacy_graph(self) -> None:
        report = self._report(40)
        self.plaintext.save_report("user-a", report)
        store = CortexStore(self.db_path, self.root / "vault")
        backup = store.create_backup("user-a")
        backup_db = self.root / "backup.sqlite"
        with zipfile.ZipFile(backup["backup_path"]) as archive:
            backup_db.write_bytes(archive.read("index.sqlite"))
            self.assertIn(
                "backup-security.json",
                archive.namelist(),
            )
        self.assertNotIn(LEGACY_SENTINEL, backup_db.read_bytes())
        with sqlite3.connect(backup_db) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM twin_eval_runs"
                ).fetchone()[0],
                0,
            )
        backup_audit = store.audit_pairwise_backup_storage(
            require_clean=True,
        )
        self.assertTrue(backup_audit["managed_backups_clean"])
        self.assertEqual(backup_audit["verified_safe_count"], 1)

    def test_failed_backup_leaves_no_plaintext_temp_or_partial_zip(
        self,
    ) -> None:
        report = self._report(41)
        self.plaintext.save_report("user-a", report)
        store = CortexStore(self.db_path, self.root / "vault")

        def _fail_zip(
            timestamp,
            sqlite_backup_path,
            **kwargs,
        ):
            self.assertNotIn(
                LEGACY_SENTINEL,
                sqlite_backup_path.read_bytes(),
            )
            raise OSError("simulated ZIP failure")

        store.vault.create_zip_backup = _fail_zip
        with self.assertRaisesRegex(OSError, "ZIP failure"):
            store.create_backup("user-a")
        backup_dir = store.vault.backups_dir
        if backup_dir.exists():
            self.assertEqual(tuple(backup_dir.iterdir()), ())

    def test_checkpoint_failure_does_not_promote_backup(self) -> None:
        self.plaintext.save_report("user-a", self._report(42))
        store = CortexStore(self.db_path, self.root / "vault")
        with mock.patch(
            "backend.app.storage._verified_wal_truncate",
            side_effect=RuntimeError("checkpoint fault"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "checkpoint fault",
            ):
                store.create_backup("user-a")
        self.assertEqual(
            tuple(store.vault.backups_dir.glob("*.zip")),
            (),
        )

    def test_unreceipted_legacy_backup_blocks_managed_gate(
        self,
    ) -> None:
        self.plaintext.save_report("user-a", self._report(43))
        store = CortexStore(self.db_path, self.root / "vault")
        store.vault.backups_dir.mkdir(parents=True, exist_ok=True)
        legacy_backup = (
            store.vault.backups_dir / "cortex-vault-legacy.zip"
        )
        with zipfile.ZipFile(legacy_backup, "w") as archive:
            archive.write(self.db_path, "index.sqlite")
        audit = store.audit_pairwise_backup_storage()
        self.assertFalse(audit["managed_backups_clean"])
        self.assertEqual(audit["verified_safe_count"], 0)
        with self.assertRaisesRegex(
            RuntimeError,
            "require correct identity",
        ):
            store.audit_pairwise_backup_storage(
                require_clean=True,
            )

    def test_backup_gate_rejects_wrong_vault_and_residual_files(
        self,
    ) -> None:
        store = CortexStore(self.db_path, self.root / "vault")
        residual = store.vault.backups_dir / "index-old.sqlite"
        residual.write_bytes(LEGACY_SENTINEL)
        audit = store.audit_pairwise_backup_storage()
        self.assertFalse(audit["managed_backups_clean"])
        self.assertTrue(audit["vault_identity_verified"])
        self.assertEqual(
            audit["unsafe_backups"][0]["name"],
            residual.name,
        )

        wrong_root = self.root / "wrong-vault"
        wrong_root.mkdir()
        wrong = CortexStore(
            self.db_path,
            wrong_root,
            ensure_vault=False,
        )
        wrong_audit = wrong.audit_pairwise_backup_storage()
        self.assertFalse(wrong_audit["managed_backups_clean"])
        self.assertFalse(wrong_audit["vault_identity_verified"])


if __name__ == "__main__":
    unittest.main()
