from __future__ import annotations

import base64
import json
import tempfile
import unittest
import zipfile
from dataclasses import replace
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
    CortexHeldOutProfileBundle,
    CortexProfileManifest,
    DeterministicGenerator,
    EvaluationPrompt,
    HeldOutProfile,
    JudgeDecision,
    OracleJudge,
    PairwiseEvaluationRunner,
    PromptProfileCoverage,
    PromptScopedCitationPolicy,
    REPORT_ARTIFACT_ENCRYPTION_PURPOSE,
    ReportArtifactEncryptionUnavailable,
    TwinEvalRepository,
    WinRateRanker,
    canonical_hash,
    canonical_json,
)
from backend.app.twin_eval.profile_artifacts import (
    PROFILE_ARTIFACT_ENCRYPTION_PURPOSE,
    ProfileArtifactEncryptionUnavailable,
    ProfileArtifactError,
    build_profile_artifact_envelope,
    parse_profile_artifact,
)


KEK_B64 = base64.b64encode(bytes(range(32))).decode("ascii")
FUTURE_EXPIRY = "2099-01-01T00:00:00Z"


class TwinEvalEncryptedProfileArtifactTests(unittest.TestCase):
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
            evidence_cipher=self.keyring,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def _bundle_and_report(secret: str = "owner-private-sentinel"):
        prompt = EvaluationPrompt("p", "Write an update.")
        config_digest = canonical_hash(
            {"token_budget_per_prompt": 2_000},
            prefix="pairwise_profile_config_",
        )
        selection_digest = canonical_hash(
            {"memory_id": "owner-memory"},
            prefix="pairwise_profile_selection_",
        )
        prompt_scope_digest = canonical_hash(
            {"memory_ids": ("owner-memory",)},
            prefix="pairwise_prompt_scope_",
        )
        manifest = CortexProfileManifest(
            schema_version="cortex-pairwise-profile-manifest/v1",
            builder_id="cortex_context_profile_v1",
            as_of="2026-07-24T19:00:00Z",
            snapshot_digest="pairwise_context_build_guard_" + ("a" * 64),
            config_digest=config_digest,
            selection_digest=selection_digest,
            prompt_scope_digests=(("p", prompt_scope_digest),),
        )
        safe_manifest = {
            "schema_version": manifest.schema_version,
            "builder_id": manifest.builder_id,
            "as_of": manifest.as_of,
            "config_digest": config_digest,
            "selection_digest": selection_digest,
            "prompt_scope_digests": manifest.prompt_scope_digests,
        }
        profile = HeldOutProfile(
            "encrypted-profile",
            (
                CitedProfileItem(
                    "owner-memory",
                    f"Use concise updates. {secret}",
                    source_url=None,
                    layer="preference",
                ),
            ),
            metadata={"profile_manifest": safe_manifest},
        )
        policy = PromptScopedCitationPolicy(
            {"p": ("owner-memory",)}
        )
        bundle = CortexHeldOutProfileBundle(
            profile=profile,
            citation_policy=policy,
            coverage=(
                PromptProfileCoverage(
                    prompt_id="p",
                    status="sufficient",
                    selected_items=1,
                    conflicts_resolved=0,
                    excluded_by_reason=(),
                ),
            ),
            manifest=manifest,
        )
        report = PairwiseEvaluationRunner(
            (
                DeterministicGenerator(
                    "a",
                    lambda prompt, profile, seed: "Short update.",
                ),
                DeterministicGenerator(
                    "b",
                    lambda prompt, profile, seed: "Long update.",
                ),
            ),
            OracleJudge({"p": "a"}),
            AllPairsStrategy(shuffle=False),
            WinRateRanker(),
            citation_policy=policy,
            blind_judge_inputs=False,
        ).run(profile, (prompt,), seed=7)
        return bundle, report

    def test_roundtrip_is_cxe1_encrypted_and_contains_no_plaintext(self) -> None:
        secret = "owner-private-sentinel"
        bundle, report = self._bundle_and_report(secret)

        self.repository.save_report(
            "user-a",
            report,
            profile_bundle=bundle,
            evidence_expires_at=FUTURE_EXPIRY,
        )

        self.assertEqual(
            self.repository.load_profile_artifact("user-a", report.run_id),
            bundle,
        )
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT artifact_ciphertext
                FROM twin_eval_profile_artifacts
                WHERE user_id = ? AND run_id = ?
                """,
                ("user-a", report.run_id),
            ).fetchone()
        self.assertTrue(bytes(row[0]).startswith(CXE1_MAGIC))
        self.assertNotIn(secret.encode(), bytes(row[0]))
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(self.db_path) + suffix)
            if path.exists():
                self.assertNotIn(secret.encode(), path.read_bytes())

    def test_verbatim_judge_quotes_are_hashed_before_report_persistence(
        self,
    ) -> None:
        secret = "ｏｗｎｅｒ   private sentinel"
        normalized_secret = "owner private sentinel"
        bundle, _unused_report = self._bundle_and_report(secret)

        class _QuotedJudge:
            judge_id = "quoted-fixture"
            requires_candidate_identity = False

            @staticmethod
            def reproducibility_config():
                return {"fixture": "quoted"}

            @staticmethod
            def judge(prompt, profile, left, right, *, seed):
                return JudgeDecision(
                    outcome=ComparisonOutcome.LEFT,
                    rationale=(
                        "The left candidate follows "
                        f"{normalized_secret} from the cited preference."
                    ),
                    cited_memory_ids=("owner-memory",),
                    confidence=0.9,
                    metadata={
                        "evidence_quotes": {
                            "owner-memory": (secret,),
                        }
                    },
                )

        report = PairwiseEvaluationRunner(
            (
                DeterministicGenerator(
                    "a",
                    lambda prompt, profile, seed: "Short update.",
                ),
                DeterministicGenerator(
                    "b",
                    lambda prompt, profile, seed: "Long update.",
                ),
            ),
            _QuotedJudge(),
            AllPairsStrategy(shuffle=False),
            WinRateRanker(),
            citation_policy=bundle.citation_policy,
        ).run(
            bundle.profile,
            (EvaluationPrompt("p", "Write an update."),),
            seed=7,
        )

        self.assertNotIn(
            "evidence_quotes",
            report.comparisons[0].decision.metadata,
        )
        self.assertIn(
            "evidence_quote_digests",
            report.comparisons[0].decision.metadata,
        )
        self.assertNotIn(
            normalized_secret,
            report.comparisons[0].decision.rationale,
        )
        self.repository.save_report(
            "user-a",
            report,
            profile_bundle=bundle,
            evidence_expires_at=FUTURE_EXPIRY,
        )
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(self.db_path) + suffix)
            if path.exists():
                self.assertNotIn(secret.encode(), path.read_bytes())
                self.assertNotIn(
                    normalized_secret.encode(),
                    path.read_bytes(),
                )

    def test_missing_cipher_or_profile_bundle_fails_before_any_write(self) -> None:
        bundle, report = self._bundle_and_report()
        without_cipher = TwinEvalRepository(self.db_path)

        with self.assertRaises(ProfileArtifactEncryptionUnavailable):
            without_cipher.save_report(
                "user-a",
                report,
                profile_bundle=bundle,
                evidence_expires_at=FUTURE_EXPIRY,
            )
        with self.assertRaises(ProfileArtifactEncryptionUnavailable):
            self.repository.save_report("user-a", report)

        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM twin_eval_runs").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM twin_eval_profile_artifacts"
                ).fetchone()[0],
                0,
            )

    def test_profile_report_mismatch_and_plaintext_cipher_fail_closed(self) -> None:
        bundle, report = self._bundle_and_report()
        changed_profile = replace(
            bundle.profile,
            items=(
                replace(
                    bundle.profile.items[0],
                    content="different evidence",
                ),
            ),
        )
        changed_bundle = replace(bundle, profile=changed_profile)
        with self.assertRaises(ProfileArtifactError):
            self.repository.save_report(
                "user-a",
                report,
                profile_bundle=changed_bundle,
                evidence_expires_at=FUTURE_EXPIRY,
            )

    def test_false_coverage_and_malformed_authenticated_payload_are_rejected(
        self,
    ) -> None:
        bundle, report = self._bundle_and_report()
        bad_coverage = replace(
            bundle.coverage[0],
            selected_items=2,
        )
        with self.assertRaisesRegex(
            ProfileArtifactError,
            "coverage counts",
        ):
            self.repository.save_report(
                "user-a",
                report,
                profile_bundle=replace(
                    bundle,
                    coverage=(bad_coverage,),
                ),
                evidence_expires_at=FUTURE_EXPIRY,
            )

        envelope = build_profile_artifact_envelope(
            user_id="user-a",
            report=report,
            bundle=bundle,
            artifact_id="a" * 32,
            created_at="2026-07-24T19:00:00Z",
            expires_at=FUTURE_EXPIRY,
        )
        payload = json.loads(envelope.plaintext)
        payload["citation_policy"]["allowed_memory_ids"]["p"] = (
            "not-an-array"
        )
        malformed = canonical_json(payload).encode()
        malformed_digest = canonical_hash(
            payload,
            prefix="pairwise_profile_artifact_",
        )
        with self.assertRaises(ProfileArtifactError):
            parse_profile_artifact(
                malformed,
                expected_user_id="user-a",
                expected_run_id=report.run_id,
                expected_artifact_id="a" * 32,
                expected_profile_fingerprint=bundle.profile.fingerprint,
                expected_scope_digest=envelope.scope_digest,
                expected_artifact_digest=malformed_digest,
                expected_created_at="2026-07-24T19:00:00Z",
                expected_expires_at=FUTURE_EXPIRY,
            )

        class _PlaintextCipher:
            available = True

            @staticmethod
            def encrypt_blob(user_id, purpose, plaintext):
                return plaintext

            @staticmethod
            def decrypt_blob(user_id, purpose, ciphertext):
                return ciphertext

            @staticmethod
            def is_encrypted(ciphertext):
                return False

        with self.assertRaises(ReportArtifactEncryptionUnavailable):
            TwinEvalRepository(
                self.db_path,
                evidence_cipher=_PlaintextCipher(),
            ).save_report(
                "user-a",
                report,
                profile_bundle=bundle,
                evidence_expires_at=FUTURE_EXPIRY,
            )

    def test_wrong_user_purpose_and_ciphertext_tampering_are_rejected(self) -> None:
        bundle, report = self._bundle_and_report()
        for user_id in ("user-a", "user-b"):
            self.repository.save_report(
                user_id,
                report,
                profile_bundle=bundle,
                evidence_expires_at=FUTURE_EXPIRY,
            )
        with sqlite3.connect(self.db_path) as conn:
            ciphertext_a = conn.execute(
                """
                SELECT artifact_ciphertext
                FROM twin_eval_profile_artifacts
                WHERE user_id = 'user-a' AND run_id = ?
                """,
                (report.run_id,),
            ).fetchone()[0]
            conn.execute(
                """
                UPDATE twin_eval_profile_artifacts
                SET artifact_ciphertext = ?
                WHERE user_id = 'user-b' AND run_id = ?
                """,
                (ciphertext_a, report.run_id),
            )
        with self.assertRaises(DecryptionError):
            self.repository.load_profile_artifact(
                "user-b",
                report.run_id,
            )
        with self.assertRaises(DecryptionError):
            self.keyring.decrypt_blob(
                "user-a",
                "content",
                bytes(ciphertext_a),
            )

        with sqlite3.connect(self.db_path) as conn:
            corrupted = bytearray(ciphertext_a)
            corrupted[-1] ^= 1
            conn.execute(
                """
                UPDATE twin_eval_profile_artifacts
                SET artifact_ciphertext = ?
                WHERE user_id = 'user-a' AND run_id = ?
                """,
                (bytes(corrupted), report.run_id),
            )
        with self.assertRaises(DecryptionError):
            self.repository.load_profile_artifact(
                "user-a",
                report.run_id,
            )

    def test_idempotence_retention_and_report_deletion(self) -> None:
        bundle, report = self._bundle_and_report()
        first = self.repository.save_report(
            "user-a",
            report,
            profile_bundle=bundle,
            evidence_expires_at=FUTURE_EXPIRY,
        )
        second = self.repository.save_report(
            "user-a",
            report,
            profile_bundle=bundle,
            evidence_expires_at=FUTURE_EXPIRY,
        )
        self.assertEqual(first, second)
        self.assertEqual(
            self.repository.list_expired_profile_artifacts(
                "user-a",
                "2100-01-01T00:00:00Z",
            ),
            (report.run_id,),
        )
        self.assertEqual(
            self.repository.purge_expired_profile_artifacts(
                "user-a",
                "2100-01-01T00:00:00Z",
                expected_run_ids=(report.run_id,),
            ),
            (report.run_id,),
        )
        with self.assertRaises(KeyError):
            self.repository.load_profile_artifact(
                "user-a",
                report.run_id,
            )
        self.assertEqual(
            self.repository.load_report("user-a", report.run_id),
            report,
        )
        self.assertTrue(
            self.repository.delete_report("user-a", report.run_id)
        )

    def test_account_deletion_removes_every_pairwise_row(self) -> None:
        bundle, report = self._bundle_and_report()
        self.repository.save_report(
            "user-a",
            report,
            profile_bundle=bundle,
            evidence_expires_at=FUTURE_EXPIRY,
        )
        store = CortexStore(self.db_path, self.root / "vault")

        result = store.delete_user_data("user-a")

        self.assertEqual(result["sqlite"]["twin_eval_runs"], 1)
        self.assertEqual(
            result["sqlite"]["twin_eval_profile_artifacts"],
            1,
        )
        self.assertEqual(
            result["sqlite"]["twin_eval_report_artifacts"],
            1,
        )
        with sqlite3.connect(self.db_path) as conn:
            names = (
                "twin_eval_profile_artifacts",
                "twin_eval_report_artifacts",
                "twin_eval_ranking_manifests",
                "twin_eval_rankings",
                "twin_eval_resolved_comparisons",
                "twin_eval_comparisons",
                "twin_eval_candidates",
                "twin_eval_runs",
            )
            self.assertTrue(
                all(
                    conn.execute(
                        f"SELECT COUNT(*) FROM {name} WHERE user_id = ?",
                        ("user-a",),
                    ).fetchone()[0]
                    == 0
                    for name in names
                )
            )

    def test_routine_backups_exclude_profile_evidence_ciphertext(self) -> None:
        bundle, report = self._bundle_and_report()
        self.repository.save_report(
            "user-a",
            report,
            profile_bundle=bundle,
            evidence_expires_at=FUTURE_EXPIRY,
        )
        store = CortexStore(self.db_path, self.root / "vault")

        backup = store.create_backup("user-a")

        self.assertEqual(
            backup["excluded_twin_eval_profile_artifacts"],
            1,
        )
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
                    "SELECT COUNT(*) FROM twin_eval_profile_artifacts"
                ).fetchone()[0],
                0,
            )
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

    def test_dedicated_encryption_purpose_is_supported(self) -> None:
        ciphertext = self.keyring.encrypt_blob(
            "user-a",
            PROFILE_ARTIFACT_ENCRYPTION_PURPOSE,
            b"evidence",
        )
        self.assertEqual(
            self.keyring.decrypt_blob(
                "user-a",
                PROFILE_ARTIFACT_ENCRYPTION_PURPOSE,
                ciphertext,
            ),
            b"evidence",
        )
        report_ciphertext = self.keyring.encrypt_blob(
            "user-a",
            REPORT_ARTIFACT_ENCRYPTION_PURPOSE,
            b"report",
        )
        self.assertEqual(
            self.keyring.decrypt_blob(
                "user-a",
                REPORT_ARTIFACT_ENCRYPTION_PURPOSE,
                report_ciphertext,
            ),
            b"report",
        )


if __name__ == "__main__":
    unittest.main()
