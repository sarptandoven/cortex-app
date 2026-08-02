from __future__ import annotations

import hashlib
import json
import pickle
import tempfile
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.database import init_db
from backend.app.keyring import LocalKekProvider, UserKeyring
from backend.app.sqlite_runtime import sqlite3
from backend.app.storage import CortexStore
from backend.app.twin_eval import (
    CortexHeldOutProfileBundle,
    CortexProfileManifest,
    EvaluationPrompt,
    EvaluationArtifactInUse,
    HeldOutProfile,
    PairwiseAdmissionPolicy,
    PairwiseConsentGrant,
    PairwiseExecutionConflict,
    PairwiseExecutionError,
    PairwiseExecutionNotFound,
    PairwiseExecutionService,
    PairwiseExecutionUnavailable,
    PromptProfileCoverage,
    PromptScopedCitationPolicy,
    TrustedPairwiseAdapterEndpoint,
    TrustedPairwiseExecutionConfig,
    canonical_hash,
    canonical_json,
)
from backend.app.twin_eval.execution_authority import (
    PairwiseDispatchAuthorityStore,
)
from backend.app.twin_eval.domain import CitedProfileItem
from backend.app.twin_eval.profile_artifacts import (
    parse_cortex_profile_bundle,
)


_SIGNING_KEY = "execution-test-signing-key-is-at-least-32-bytes"
_BINDING_KEYS = {
    "execution-binding-v1": (
        "stable-execution-binding-key-is-at-least-32-bytes"
    ),
    "execution-binding-v2": (
        "rotated-execution-binding-key-is-at-least-32-bytes"
    ),
}
_CONSENT = "remote-processing-consent/v1"
_AS_OF = "2026-07-24T19:00:00Z"
_PROFILE_CONFIG_DIGEST = "profile_config_" + ("1" * 64)


def _spec(prompt: str = "Write a private status update.") -> dict:
    return {
        "as_of": _AS_OF,
        "prompts": [{"prompt_id": "prompt-1", "text": prompt}],
        "system_ids": ["system-a", "system-b"],
        "strategy": {
            "type": "repeated_swapped",
            "repetitions": 1,
            "shuffle": False,
        },
        "seed": 7,
        "budget": {"max_provider_calls": 10},
    }


class _ProfileBuilder:
    def __init__(self, secret: str) -> None:
        self.secret = secret
        self.snapshot_revision = "a"
        self.calls: list[str] = []

    def build(
        self,
        user_id: str,
        prompts,
        *,
        as_of: str,
        sector=None,
    ) -> CortexHeldOutProfileBundle:
        del sector
        self.calls.append(user_id)
        selection_digest = canonical_hash(
            {
                "user_id": user_id,
                "secret": self.secret,
                "prompts": prompts,
                "as_of": as_of,
            },
            prefix="pairwise_profile_selection_",
        )
        prompt_scope_digests = tuple(
            (
                prompt.prompt_id,
                canonical_hash(
                    {"memory_ids": ("memory-1",)},
                    prefix="pairwise_prompt_scope_",
                ),
            )
            for prompt in prompts
        )
        safe_manifest = {
            "schema_version": "cortex-pairwise-profile-manifest/v1",
            "builder_id": "cortex_context_profile_v1",
            "as_of": as_of,
            "config_digest": _PROFILE_CONFIG_DIGEST,
            "selection_digest": selection_digest,
            "prompt_scope_digests": prompt_scope_digests,
        }
        profile = HeldOutProfile(
            profile_id=canonical_hash(
                safe_manifest,
                prefix="cortex_pairwise_profile_",
            ),
            items=(
                CitedProfileItem(
                    memory_id="memory-1",
                    content=self.secret,
                    author_class="user",
                    status="active",
                    trust_score=1.0,
                ),
            ),
            metadata={
                "builder_id": "cortex_context_profile_v1",
                "selection_digest": selection_digest,
                "profile_manifest": safe_manifest,
            },
        )
        manifest = CortexProfileManifest(
            schema_version=safe_manifest["schema_version"],
            builder_id=safe_manifest["builder_id"],
            as_of=as_of,
            snapshot_digest="snapshot-" + (
                self.snapshot_revision * 64
            ),
            config_digest=_PROFILE_CONFIG_DIGEST,
            selection_digest=selection_digest,
            prompt_scope_digests=prompt_scope_digests,
        )
        return CortexHeldOutProfileBundle(
            profile=profile,
            citation_policy=PromptScopedCitationPolicy(
                {
                    prompt.prompt_id: ("memory-1",)
                    for prompt in prompts
                }
            ),
            coverage=tuple(
                PromptProfileCoverage(
                    prompt_id=prompt.prompt_id,
                    status="sufficient",
                    selected_items=1,
                    conflicts_resolved=0,
                    excluded_by_reason=(),
                )
                for prompt in prompts
            ),
            manifest=manifest,
        )


class _ConsentAuthority:
    def __init__(self) -> None:
        self.grant: PairwiseConsentGrant | None = None

    def get_pairwise_consent(
        self, user_id: str
    ) -> PairwiseConsentGrant | None:
        del user_id
        return self.grant


class PairwiseExecutionControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.db_path = root / "cortex.sqlite"
        init_db(self.db_path)
        provider = LocalKekProvider(
            {
                "CORTEX_KEK": (
                    "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
                ),
                "CORTEX_KEK_VERSION": "1",
            }
        )
        self.keyring = UserKeyring(root / "keyring.sqlite", provider)
        self.policy = PairwiseAdmissionPolicy()
        self.adapter_endpoints = {
            "adapter-a@sha256:111": TrustedPairwiseAdapterEndpoint(
                adapter_revision="adapter-a@sha256:111",
                endpoint_id="candidate-endpoint-a",
                model_id="candidate-model-a",
                request_schema_version="candidate-request/v1",
                response_parser_revision=(
                    "openai-responses-candidate/v1"
                ),
                timeout_seconds=4,
                max_input_chars=100_000,
                max_output_chars=20_000,
                max_input_tokens=20_000,
                max_output_tokens=4_000,
                supports_idempotency=True,
                idempotency_field="Idempotency-Key",
            ),
            "adapter-b@sha256:222": TrustedPairwiseAdapterEndpoint(
                adapter_revision="adapter-b@sha256:222",
                endpoint_id="candidate-endpoint-b",
                model_id="candidate-model-b",
                request_schema_version="candidate-request/v1",
                response_parser_revision=(
                    "openai-responses-candidate/v1"
                ),
                timeout_seconds=4,
                max_input_chars=100_000,
                max_output_chars=20_000,
                max_input_tokens=20_000,
                max_output_tokens=4_000,
            ),
            "judge@sha256:333": TrustedPairwiseAdapterEndpoint(
                adapter_revision="judge@sha256:333",
                endpoint_id="judge-endpoint",
                model_id="judge-model",
                request_schema_version="judge-request/v1",
                response_parser_revision=(
                    "openai-responses-judge/v1"
                ),
                timeout_seconds=4,
                max_input_chars=200_000,
                max_output_chars=20_000,
                max_input_tokens=40_000,
                max_output_tokens=4_000,
                supports_idempotency=True,
                idempotency_field="Idempotency-Key",
            ),
        }
        self.config = TrustedPairwiseExecutionConfig(
            system_revisions={
                "system-a": "adapter-a@sha256:111",
                "system-b": "adapter-b@sha256:222",
            },
            judge_revision="judge@sha256:333",
            assumptions={},
            consent_version=_CONSENT,
            request_retention_seconds=60,
            adapter_endpoints=self.adapter_endpoints,
        )
        self.builder = _ProfileBuilder(
            "do-not-store-this-owner-preference"
        )
        self.consent = _ConsentAuthority()
        self.consent.grant = PairwiseConsentGrant(
            user_id="user-a",
            scope="pairwise_remote_evaluation",
            consent_version=_CONSENT,
            config_digest=self.config.digest,
            granted_at="1970-01-01T00:00:00Z",
            expires_at="1970-01-02T00:00:00Z",
        )
        self.service = PairwiseExecutionService(
            self.db_path,
            cipher=self.keyring,
            profile_builder=self.builder,
            consent_authority=self.consent,
            policy=self.policy,
            signing_key=_SIGNING_KEY,
            binding_keys=_BINDING_KEYS,
            active_binding_key_id="execution-binding-v1",
            config=self.config,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _receipt(
        self,
        spec: dict,
        *,
        user_id: str = "user-a",
    ) -> dict:
        response = self.service.prepare(
            user_id=user_id,
            spec=spec,
            receipt_ttl_seconds=600,
            now_unix=1_000,
        )
        return response["admission_receipt"]

    def _submit(
        self,
        spec: dict | None = None,
        *,
        user_id: str = "user-a",
        idempotency_key: str = "idem-1",
        receipt: dict | None = None,
    ):
        value = spec or _spec()
        return self.service.submit(
            user_id=user_id,
            spec=value,
            receipt=receipt or self._receipt(value, user_id=user_id),
            idempotency_key=idempotency_key,
            now_unix=1_001,
        )

    @staticmethod
    def _offset(timestamp: str, seconds: int) -> str:
        parsed = datetime.fromisoformat(
            timestamp[:-1] + "+00:00"
            if timestamp.endswith("Z")
            else timestamp
        ).astimezone(timezone.utc)
        return (
            (parsed + timedelta(seconds=seconds))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

    def _queue_and_claim(self, *, worker_id: str = "worker-a"):
        status = self._submit()
        now = self._offset(status.created_at, 1)
        self.service._repository.queue(
            "user-a", status.evaluation_id, now_utc=now
        )
        lease = self.service._repository.claim_next(
            "user-a",
            worker_id,
            now_utc=now,
            lease_seconds=5,
            execution_deadline_seconds=30,
        )
        self.assertIsNotNone(lease)
        return status, lease, now

    def _dispatch_authority(self, status, now: str):
        self.dispatch_now = datetime.fromisoformat(
            self._offset(now, 1).replace("Z", "+00:00")
        )
        authority = PairwiseDispatchAuthorityStore(
            self.db_path,
            clock=lambda: self.dispatch_now,
        )
        disabled = authority.configure_runtime(
            config_digest=self.config.digest,
            dispatch_enabled=False,
        )
        runtime = authority.configure_runtime(
            config_digest=self.config.digest,
            dispatch_enabled=True,
            expected_epoch=disabled.config_epoch,
        )
        authority.grant_consent(
            user_id="user-a",
            scope="pairwise_remote_evaluation",
            consent_version=_CONSENT,
            config_digest=self.config.digest,
            config_epoch=runtime.config_epoch,
            granted_at=status.created_at,
            expires_at=self._offset(status.created_at, 55),
        )
        return authority

    def _fixture_report(self, lease):
        return self.service._repository._build_fixture_report(lease)

    def _rename_checkpoint_table_as_interrupted(
        self,
        *,
        modern_copy: str | None,
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                DROP TRIGGER
                  enforce_twin_eval_execution_call_transition
                """
            )
            conn.execute(
                """
                DROP TRIGGER enforce_twin_eval_execution_no_open_calls
                """
            )
            conn.execute(
                "DROP INDEX idx_twin_eval_execution_call_state"
            )
            conn.execute(
                """
                ALTER TABLE twin_eval_execution_call_checkpoints
                RENAME TO
                  twin_eval_execution_call_checkpoints_legacy_shape
                """
            )
            if modern_copy is not None:
                where = "" if modern_copy == "populated" else "WHERE 0"
                conn.execute(
                    f"""
                    CREATE TABLE twin_eval_execution_call_checkpoints
                    AS SELECT *
                    FROM
                      twin_eval_execution_call_checkpoints_legacy_shape
                    {where}
                    """
                )

    def test_submit_is_server_built_encrypted_and_publicly_redacted(
        self,
    ) -> None:
        status = self._submit()
        public = status.to_dict()
        secret = self.builder.secret

        self.assertEqual(status.status, "prepared")
        self.assertTrue(status.content_retained)
        self.assertFalse(public["remote_execution_enabled"])
        self.assertNotIn(secret, str(public))
        self.assertNotIn("request_digest", public)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT request_ciphertext, request_binding
                FROM twin_eval_execution_requests
                WHERE user_id = ?
                """,
                ("user-a",),
            ).fetchone()
        self.assertTrue(bytes(row[0]).startswith(b"CXE1"))
        guessed = hashlib.sha256(
            secret.encode("utf-8")
        ).hexdigest()
        self.assertNotEqual(row[1], guessed)
        for path in (
            self.db_path,
            Path(f"{self.db_path}-wal"),
            Path(f"{self.db_path}-shm"),
        ):
            if path.exists():
                self.assertNotIn(
                    secret.encode("utf-8"), path.read_bytes()
                )
        artifact = self.service._repository.load_request(
            "user-a", status.evaluation_id
        )
        self.assertEqual(
            artifact["request"]["profile"]["items"][0]["content"],
            secret,
        )
        self.assertEqual(
            artifact["profile_bundle"]["citation_policy"][
                "allowed_memory_ids"
            ],
            {"prompt-1": ["memory-1"]},
        )
        self.assertEqual(
            artifact["profile_bundle"]["builder_manifest"][
                "snapshot_digest"
            ],
            "snapshot-" + ("a" * 64),
        )

    def test_client_profile_is_rejected_and_builder_is_authoritative(
        self,
    ) -> None:
        forged = _spec()
        forged["profile"] = {
            "metadata": {
                "builder_id": "cortex_context_profile_v1"
            }
        }
        before = len(self.builder.calls)
        with self.assertRaisesRegex(
            PairwiseExecutionError, "unknown fields"
        ):
            self.service.prepare(
                user_id="user-a",
                spec=forged,
                now_unix=1_000,
            )
        self.assertEqual(len(self.builder.calls), before)

    def test_authoritative_consent_is_user_config_time_and_revocation_bound(
        self,
    ) -> None:
        spec = _spec()
        receipt = self._receipt(spec)
        original = self.consent.grant
        cases = (
            None,
            replace(original, user_id="user-b"),
            replace(original, config_digest="other"),
            replace(original, revoked_at="1970-01-01T00:10:00Z"),
            replace(original, expires_at="1970-01-01T00:10:00Z"),
        )
        for index, grant in enumerate(cases):
            self.consent.grant = grant
            with self.assertRaisesRegex(
                PairwiseExecutionUnavailable, "consent"
            ):
                self.service.submit(
                    user_id="user-a",
                    spec=spec,
                    receipt=receipt,
                    idempotency_key=f"consent-{index}",
                    now_unix=1_001,
                )
        self.consent.grant = original
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM twin_eval_execution_requests"
                ).fetchone()[0],
                0,
            )

    def test_exact_receipt_and_idempotency_pair_is_required(self) -> None:
        spec = _spec()
        receipt = self._receipt(spec)
        first = self._submit(spec, receipt=receipt)
        retry = self._submit(spec, receipt=receipt)
        self.assertEqual(first.evaluation_id, retry.evaluation_id)
        with self.assertRaises(PairwiseExecutionConflict):
            self._submit(
                spec,
                receipt=receipt,
                idempotency_key="different-key",
            )
        fresh_receipt = self.service.prepare(
            user_id="user-a",
            spec=spec,
            receipt_ttl_seconds=601,
            now_unix=1_000,
        )["admission_receipt"]
        with self.assertRaises(PairwiseExecutionConflict):
            self._submit(
                spec,
                receipt=fresh_receipt,
                idempotency_key="idem-1",
            )
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM twin_eval_execution_requests"
                ).fetchone()[0],
                1,
            )

    def test_receipt_binds_complete_bundle_and_trusted_config(
        self,
    ) -> None:
        spec = _spec()
        receipt = self._receipt(spec)
        self.builder.snapshot_revision = "b"
        with self.assertRaisesRegex(ValueError, "invalid"):
            self._submit(spec, receipt=receipt)
        self.builder.snapshot_revision = "a"

        rotated = TrustedPairwiseExecutionConfig(
            system_revisions={
                "system-a": "adapter-a@sha256:changed",
                "system-b": "adapter-b@sha256:222",
            },
            judge_revision="judge@sha256:333",
            assumptions={},
            consent_version=_CONSENT,
            request_retention_seconds=60,
        )
        authority = _ConsentAuthority()
        authority.grant = replace(
            self.consent.grant,
            config_digest=rotated.digest,
        )
        rotated_service = PairwiseExecutionService(
            self.db_path,
            cipher=self.keyring,
            profile_builder=self.builder,
            consent_authority=authority,
            policy=self.policy,
            signing_key=_SIGNING_KEY,
            binding_keys=_BINDING_KEYS,
            active_binding_key_id="execution-binding-v1",
            config=rotated,
        )
        with self.assertRaisesRegex(ValueError, "invalid"):
            rotated_service.submit(
                user_id="user-a",
                spec=spec,
                receipt=receipt,
                idempotency_key="rotated",
                now_unix=1_001,
            )

    def test_idempotency_key_reuse_for_different_request_is_rejected(
        self,
    ) -> None:
        self._submit(_spec("first"))
        changed = _spec("second")
        with self.assertRaises(PairwiseExecutionConflict):
            self._submit(
                changed,
                receipt=self._receipt(changed),
                idempotency_key="idem-1",
            )

    def test_concurrent_replay_creates_one_row_and_one_keyring_write(
        self,
    ) -> None:
        spec = _spec()
        receipt = self._receipt(spec)

        def submit(_: int) -> str:
            return self._submit(
                spec, receipt=receipt
            ).evaluation_id

        with ThreadPoolExecutor(max_workers=8) as pool:
            evaluation_ids = tuple(pool.map(submit, range(16)))
        self.assertEqual(len(set(evaluation_ids)), 1)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM twin_eval_execution_requests"
                ).fetchone()[0],
                1,
            )
        with sqlite3.connect(Path(self.tempdir.name) / "keyring.sqlite") as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT write_count FROM user_keys WHERE user_id = ?",
                    ("user-a",),
                ).fetchone()[0],
                1,
            )

    def test_cross_user_access_and_receipt_replay_are_rejected(self) -> None:
        spec = _spec()
        receipt = self._receipt(spec)
        status = self._submit(spec, receipt=receipt)
        self.consent.grant = replace(
            self.consent.grant, user_id="user-b"
        )
        with self.assertRaisesRegex(ValueError, "invalid"):
            self.service.submit(
                user_id="user-b",
                spec=spec,
                receipt=receipt,
                idempotency_key="user-b",
                now_unix=1_001,
            )
        with self.assertRaises(PairwiseExecutionNotFound):
            self.service.get_status(
                "user-b", status.evaluation_id
            )
        with self.assertRaises(PairwiseExecutionNotFound):
            self.service.cancel(
                "user-b", status.evaluation_id
            )

    def test_cancel_delete_and_retention_are_idempotent(self) -> None:
        first = self._submit()
        cancelled = self.service.cancel(
            "user-a", first.evaluation_id
        )
        repeated = self.service.cancel(
            "user-a", first.evaluation_id
        )
        self.assertEqual(cancelled, repeated)
        deleted = self.service.delete_request_content(
            "user-a", first.evaluation_id
        )
        self.assertFalse(deleted.content_retained)
        self.assertEqual(
            self.service.delete_request_content(
                "user-a", first.evaluation_id
            ),
            deleted,
        )
        with self.assertRaises(PairwiseExecutionNotFound):
            self.service._repository.load_request(
                "user-a", first.evaluation_id
            )

        second_spec = _spec("second")
        second = self._submit(
            second_spec,
            receipt=self._receipt(second_spec),
            idempotency_key="second",
        )
        purged = self.service.purge_expired(
            now_utc="2100-01-01T00:00:00Z"
        )
        self.assertEqual(purged, (second.evaluation_id,))
        self.assertFalse(
            self.service.get_status(
                "user-a", second.evaluation_id
            ).content_retained
        )

        third_spec = _spec("stale running")
        third = self._submit(
            third_spec,
            receipt=self._receipt(third_spec),
            idempotency_key="third",
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE twin_eval_execution_requests
                SET status = 'running'
                WHERE user_id = ? AND evaluation_id = ?
                """,
                ("user-a", third.evaluation_id),
            )
        self.assertEqual(
            self.service.purge_expired(
                now_utc="2100-01-01T00:00:00Z"
            ),
            (third.evaluation_id,),
        )
        stale = self.service.get_status(
            "user-a", third.evaluation_id
        )
        self.assertEqual(stale.status, "cancelled")
        self.assertFalse(stale.content_retained)

    def test_ciphertext_and_outer_binding_tampering_are_detected(
        self,
    ) -> None:
        status = self._submit()
        with sqlite3.connect(self.db_path) as conn:
            ciphertext = bytes(
                conn.execute(
                    """
                    SELECT request_ciphertext
                    FROM twin_eval_execution_requests
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    ("user-a", status.evaluation_id),
                ).fetchone()[0]
            )
            conn.execute(
                """
                UPDATE twin_eval_execution_requests
                SET request_ciphertext = ?
                WHERE user_id = ? AND evaluation_id = ?
                """,
                (
                    ciphertext[:-1]
                    + bytes((ciphertext[-1] ^ 1,)),
                    "user-a",
                    status.evaluation_id,
                ),
            )
        with self.assertRaisesRegex(
            PairwiseExecutionError, "authenticated"
        ):
            self.service._repository.load_request(
                "user-a", status.evaluation_id
            )

    def test_backup_omits_requests_and_account_deletion_removes_them(
        self,
    ) -> None:
        status = self._submit()
        now = self._offset(status.created_at, 1)
        self.service._repository.queue(
            "user-a", status.evaluation_id, now_utc=now
        )
        lease = self.service._repository.claim_next(
            "user-a",
            "backup-worker",
            now_utc=now,
            lease_seconds=20,
            execution_deadline_seconds=30,
        )
        self.assertIsNotNone(lease)
        authority = self._dispatch_authority(status, now)
        self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-a",
        )
        with sqlite3.connect(self.db_path) as conn:
            runtime_epoch = conn.execute(
                """
                SELECT config_epoch
                FROM twin_eval_dispatch_runtime
                WHERE singleton = 1
                """
            ).fetchone()[0]
        root = Path(self.tempdir.name)
        store = CortexStore(self.db_path, root / "vault")
        backup = store.create_backup("user-a")
        self.assertEqual(
            backup["excluded_twin_eval_execution_requests"], 1
        )
        self.assertEqual(
            backup[
                "excluded_twin_eval_execution_call_checkpoints"
            ],
            1,
        )
        self.assertEqual(
            backup["excluded_twin_eval_dispatch_consents"], 1
        )
        extracted = root / "backup.sqlite"
        with zipfile.ZipFile(backup["backup_path"]) as archive:
            extracted.write_bytes(archive.read("index.sqlite"))
        with sqlite3.connect(extracted) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM twin_eval_execution_requests"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM twin_eval_execution_call_checkpoints
                    """
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM twin_eval_dispatch_consents"
                ).fetchone()[0],
                0,
            )
            runtime_row = conn.execute(
                """
                SELECT dispatch_enabled, config_epoch
                FROM twin_eval_dispatch_runtime
                WHERE singleton = 1
                """
            ).fetchone()
            self.assertEqual(runtime_row[0], 0)
            self.assertEqual(
                runtime_row[1], runtime_epoch + 1
            )
        deletion = store.delete_user_data("user-a")
        self.assertEqual(
            deletion["sqlite"]["twin_eval_execution_requests"], 1
        )
        self.assertEqual(
            deletion["sqlite"][
                "twin_eval_execution_call_checkpoints"
            ],
            1,
        )
        self.assertEqual(
            deletion["sqlite"]["twin_eval_dispatch_consents"], 1
        )
        with self.assertRaises(PairwiseExecutionNotFound):
            self.service.get_status(
                "user-a", status.evaluation_id
            )

    def test_service_refuses_missing_encryption(self) -> None:
        class NoCipher:
            available = False

        with self.assertRaises(PairwiseExecutionUnavailable):
            PairwiseExecutionService(
                self.db_path,
                cipher=NoCipher(),
                profile_builder=self.builder,
                consent_authority=self.consent,
                policy=self.policy,
                signing_key=_SIGNING_KEY,
                binding_keys=_BINDING_KEYS,
                active_binding_key_id="execution-binding-v1",
                config=self.config,
            )

    def test_admission_signing_key_rotation_preserves_retained_work(
        self,
    ) -> None:
        spec = _spec()
        receipt = self._receipt(spec)
        first = self._submit(spec, receipt=receipt)
        rotated_service = PairwiseExecutionService(
            self.db_path,
            cipher=self.keyring,
            profile_builder=self.builder,
            consent_authority=self.consent,
            policy=self.policy,
            signing_key=(
                "rotated-admission-signing-key-is-at-least-32-bytes"
            ),
            binding_keys=_BINDING_KEYS,
            active_binding_key_id="execution-binding-v2",
            config=self.config,
        )

        retried = rotated_service.submit(
            user_id="user-a",
            spec=spec,
            receipt=receipt,
            idempotency_key="idem-1",
            now_unix=1_001,
        )
        self.assertEqual(retried.evaluation_id, first.evaluation_id)
        artifact = rotated_service._repository.load_request(
            "user-a", first.evaluation_id
        )
        self.assertEqual(
            artifact["binding_key_id"], "execution-binding-v1"
        )
        fresh_receipt = rotated_service.prepare(
            user_id="user-a",
            spec=spec,
            receipt_ttl_seconds=601,
            now_unix=1_000,
        )["admission_receipt"]
        with self.assertRaises(PairwiseExecutionConflict):
            rotated_service.submit(
                user_id="user-a",
                spec=spec,
                receipt=fresh_receipt,
                idempotency_key="idem-1",
                now_unix=1_001,
            )
        changed_spec = _spec("changed after key rotation")
        changed_receipt = rotated_service.prepare(
            user_id="user-a",
            spec=changed_spec,
            receipt_ttl_seconds=602,
            now_unix=1_000,
        )["admission_receipt"]
        with self.assertRaises(PairwiseExecutionConflict):
            rotated_service.submit(
                user_id="user-a",
                spec=changed_spec,
                receipt=changed_receipt,
                idempotency_key="idem-1",
                now_unix=1_001,
            )
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM twin_eval_execution_requests"
                ).fetchone()[0],
                1,
            )

    def test_trusted_config_is_deeply_immutable(self) -> None:
        config = TrustedPairwiseExecutionConfig(
            system_revisions={
                "system-a": "a@1",
                "system-b": "b@1",
            },
            judge_revision="judge@1",
            assumptions={
                "pricing": {"input": 1.0},
                "stops": ["done"],
            },
            consent_version=_CONSENT,
        )
        digest = config.digest
        with self.assertRaises(TypeError):
            config.assumptions["pricing"]["input"] = 0.0
        with self.assertRaises(TypeError):
            config.system_revisions["system-a"] = "a@2"
        self.assertEqual(config.digest, digest)

    def test_candidate_call_checkpoint_is_atomic_encrypted_and_private(
        self,
    ) -> None:
        status, lease, now = self._queue_and_claim()
        authority = self._dispatch_authority(status, now)
        capability = self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-a",
        )
        self.assertEqual(capability.call_ordinal, 0)
        self.assertEqual(capability.lease_generation, lease.generation)
        self.assertTrue(
            capability.provider_idempotency_key.startswith(
                "pairwise_idem_"
            )
        )
        self.assertNotIn(capability.permit, repr(capability))
        self.assertNotIn(self.builder.secret, repr(capability))
        with self.assertRaisesRegex(
            TypeError, "cannot be serialized"
        ):
            pickle.dumps(capability)
        with self.assertRaisesRegex(
            AttributeError, "immutable"
        ):
            capability.call_id = "forged"
        profile_items = capability.adapter_input["profile"]["items"]
        self.assertEqual(len(profile_items), 1)
        self.assertEqual(
            profile_items[0]["content"], self.builder.secret
        )

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT checkpoint_ciphertext, coordinate_binding,
                       payload_binding, permit_digest, state
                FROM twin_eval_execution_call_checkpoints
                WHERE user_id = ? AND evaluation_id = ?
                """,
                ("user-a", status.evaluation_id),
            ).fetchone()
            counter = conn.execute(
                """
                SELECT provider_calls_reserved
                FROM twin_eval_execution_requests
                WHERE user_id = ? AND evaluation_id = ?
                """,
                ("user-a", status.evaluation_id),
            ).fetchone()[0]
        self.assertEqual(row[4], "reserved")
        self.assertEqual(counter, 1)
        self.assertTrue(bytes(row[0]).startswith(b"CXE1"))
        self.assertNotIn("prompt-1", tuple(str(value) for value in row[1:4]))
        plaintext = self.keyring.decrypt_blob(
            "user-a",
            "twin_eval_execution_call",
            bytes(row[0]),
        )
        checkpoint = json.loads(plaintext)
        self.assertEqual(checkpoint["call_id"], capability.call_id)
        self.assertNotIn("permit", checkpoint)
        self.assertEqual(checkpoint["permit_digest"], row[3])
        self.assertEqual(
            checkpoint["authorization"]["authorized_at"],
            capability.authorized_at,
        )
        for path in (
            self.db_path,
            Path(f"{self.db_path}-wal"),
            Path(f"{self.db_path}-shm"),
        ):
            if path.exists():
                raw = path.read_bytes()
                self.assertNotIn(
                    capability.permit.encode("utf-8"), raw
                )

    def test_candidate_call_is_non_replayable_and_coordinate_stable(
        self,
    ) -> None:
        status, lease, now = self._queue_and_claim()
        authority = self._dispatch_authority(status, now)
        first = self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-a",
        )
        with self.assertRaisesRegex(
            PairwiseExecutionConflict, "already reserved"
        ):
            self.service._repository._begin_candidate_call(
                lease,
                prompt_id="prompt-1",
                system_id="system-a",
            )
        second = self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-b",
        )
        self.assertNotEqual(first.call_id, second.call_id)
        self.assertEqual(second.call_ordinal, 1)
        self.assertIsNone(second.provider_idempotency_key)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    """
                    SELECT provider_calls_reserved
                    FROM twin_eval_execution_requests
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    ("user-a", status.evaluation_id),
                ).fetchone()[0],
                2,
            )

    def test_concurrent_candidate_begin_issues_one_permit(self) -> None:
        status, lease, now = self._queue_and_claim()
        authority = self._dispatch_authority(status, now)

        def begin(_index: int):
            try:
                return self.service._repository._begin_candidate_call(
                    lease,
                    prompt_id="prompt-1",
                    system_id="system-a",
                )
            except PairwiseExecutionConflict:
                return None

        with ThreadPoolExecutor(max_workers=32) as pool:
            results = list(pool.map(begin, range(32)))
        capabilities = [value for value in results if value is not None]
        self.assertEqual(len(capabilities), 1)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM twin_eval_execution_call_checkpoints
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    ("user-a", status.evaluation_id),
                ).fetchone()[0],
                1,
            )

    def test_candidate_checkpoint_insert_failure_rolls_back_budget(self) -> None:
        status, lease, now = self._queue_and_claim()
        authority = self._dispatch_authority(status, now)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TRIGGER fail_candidate_checkpoint_insert
                BEFORE INSERT ON twin_eval_execution_call_checkpoints
                BEGIN
                  SELECT RAISE(ABORT, 'injected checkpoint failure');
                END
                """
            )
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository._begin_candidate_call(
                lease,
                prompt_id="prompt-1",
                system_id="system-a",
            )
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    """
                    SELECT provider_calls_reserved
                    FROM twin_eval_execution_requests
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    ("user-a", status.evaluation_id),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM twin_eval_execution_call_checkpoints
                    """
                ).fetchone()[0],
                0,
            )

    def test_candidate_begin_requires_current_authority_and_live_lease(
        self,
    ) -> None:
        status, lease, now = self._queue_and_claim()
        authority = self._dispatch_authority(status, now)
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository._begin_candidate_call(
                replace(lease, token="forged-lease-token"),
                prompt_id="prompt-1",
                system_id="system-a",
            )
        self.assertTrue(
            authority.revoke_consent(user_id="user-a")
        )
        with self.assertRaises(PairwiseExecutionUnavailable):
            self.service._repository._begin_candidate_call(
                lease,
                prompt_id="prompt-1",
                system_id="system-a",
            )
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM twin_eval_execution_call_checkpoints"
                ).fetchone()[0],
                0,
            )

    def test_candidate_begin_and_revocation_serialize(self) -> None:
        status, lease, now = self._queue_and_claim()
        authority = self._dispatch_authority(status, now)
        barrier = threading.Barrier(2)

        def begin():
            barrier.wait()
            try:
                return self.service._repository._begin_candidate_call(
                    lease,
                    prompt_id="prompt-1",
                    system_id="system-a",
                )
            except PairwiseExecutionUnavailable:
                return None

        def revoke() -> bool:
            barrier.wait()
            return authority.revoke_consent(user_id="user-a")

        with ThreadPoolExecutor(max_workers=2) as pool:
            begin_future = pool.submit(begin)
            revoke_future = pool.submit(revoke)
            capability = begin_future.result(timeout=10)
            self.assertTrue(revoke_future.result(timeout=10))
        with sqlite3.connect(self.db_path) as conn:
            count = conn.execute(
                """
                SELECT COUNT(*)
                FROM twin_eval_execution_call_checkpoints
                """
            ).fetchone()[0]
        self.assertEqual(count, 1 if capability is not None else 0)

    def test_candidate_begin_and_cancel_serialize(self) -> None:
        status, lease, now = self._queue_and_claim()
        authority = self._dispatch_authority(status, now)
        barrier = threading.Barrier(2)

        def begin():
            barrier.wait()
            try:
                return self.service._repository._begin_candidate_call(
                    lease,
                    prompt_id="prompt-1",
                    system_id="system-a",
                )
            except PairwiseExecutionConflict:
                return None

        def cancel():
            barrier.wait()
            return self.service.cancel(
                "user-a", status.evaluation_id
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            begin_future = pool.submit(begin)
            cancel_future = pool.submit(cancel)
            capability = begin_future.result(timeout=10)
            cancelled = cancel_future.result(timeout=10)
        with sqlite3.connect(self.db_path) as conn:
            count = conn.execute(
                """
                SELECT COUNT(*)
                FROM twin_eval_execution_call_checkpoints
                """
            ).fetchone()[0]
        self.assertEqual(count, 1 if capability is not None else 0)
        self.assertEqual(cancelled.status, "cancel_requested")

    def test_candidate_begin_denies_cancel_and_exact_expiry(self) -> None:
        status, lease, now = self._queue_and_claim()
        self._dispatch_authority(status, now)
        exact_expiry = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE twin_eval_execution_requests
                SET lease_expires_at = ?
                WHERE user_id = ? AND evaluation_id = ?
                """,
                (exact_expiry, "user-a", status.evaluation_id),
            )
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository._begin_candidate_call(
                replace(lease, lease_expires_at=exact_expiry),
                prompt_id="prompt-1",
                system_id="system-a",
            )

        self.service.cancel("user-a", status.evaluation_id)
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository._begin_candidate_call(
                lease,
                prompt_id="prompt-1",
                system_id="system-a",
            )

    def test_candidate_begin_rejects_worker_security_dependencies(
        self,
    ) -> None:
        status, lease, now = self._queue_and_claim()
        authority = self._dispatch_authority(status, now)
        for prompt_id, system_id in (
            ("not-in-plan", "system-a"),
            ("prompt-1", "not-in-plan"),
        ):
            with self.assertRaises(PairwiseExecutionConflict):
                self.service._repository._begin_candidate_call(
                    lease,
                    prompt_id=prompt_id,
                    system_id=system_id,
                )
        drifted = TrustedPairwiseExecutionConfig(
            system_revisions=self.config.system_revisions,
            judge_revision=self.config.judge_revision,
            assumptions=self.config.assumptions,
            consent_version=self.config.consent_version,
            request_retention_seconds=60,
            adapter_endpoints={},
        )
        with self.assertRaisesRegex(
            TypeError, "unexpected keyword argument 'config'"
        ):
            self.service._repository._begin_candidate_call(
                lease,
                prompt_id="prompt-1",
                system_id="system-a",
                config=drifted,
            )
        with self.assertRaisesRegex(
            TypeError, "unexpected keyword argument 'authority'"
        ):
            self.service._repository._begin_candidate_call(
                lease,
                prompt_id="prompt-1",
                system_id="system-a",
                authority=authority,
            )

    def test_candidate_begin_ignores_temp_shadow_tables(self) -> None:
        status, lease, now = self._queue_and_claim()
        self._dispatch_authority(status, now)
        original_connect = self.service._repository._connect

        def shadowed_connect():
            conn = original_connect()
            conn.execute(
                """
                CREATE TEMP TABLE twin_eval_execution_requests (
                    user_id TEXT,
                    evaluation_id TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TEMP TABLE twin_eval_execution_call_checkpoints (
                    user_id TEXT,
                    evaluation_id TEXT
                )
                """
            )
            return conn

        self.service._repository._connect = shadowed_connect
        try:
            capability = (
                self.service._repository._begin_candidate_call(
                    lease,
                    prompt_id="prompt-1",
                    system_id="system-a",
                )
            )
        finally:
            self.service._repository._connect = original_connect
        self.assertEqual(capability.evaluation_id, status.evaluation_id)

    def test_candidate_capability_consumes_once_and_burns_secrets(
        self,
    ) -> None:
        status, lease, now = self._queue_and_claim()
        self._dispatch_authority(status, now)
        capability = self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-a",
        )
        raw_permit = capability.permit
        provider_idempotency_key = (
            capability.provider_idempotency_key
        )
        consumed = (
            self.service._repository._consume_candidate_capability(
                lease, capability
            )
        )
        self.assertEqual(consumed.call_id, capability.call_id)
        self.assertNotIn(raw_permit, repr(consumed))
        self.assertNotIn(self.builder.secret, repr(consumed))
        with self.assertRaisesRegex(RuntimeError, "burned"):
            _ = capability.permit
        with self.assertRaisesRegex(RuntimeError, "burned"):
            _ = capability.adapter_input
        with self.assertRaisesRegex(
            TypeError, "cannot be serialized"
        ):
            pickle.dumps(consumed)
        transport_input, provider_key = (
            consumed._take_transport_input()
        )
        self.assertEqual(
            transport_input["profile"]["items"][0]["content"],
            self.builder.secret,
        )
        self.assertEqual(
            provider_key, provider_idempotency_key
        )
        with self.assertRaisesRegex(RuntimeError, "already taken"):
            consumed._take_transport_input()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT state, paid_attempt_count, consumed_at,
                       consume_binding, outcome_unknown_at
                FROM twin_eval_execution_call_checkpoints
                WHERE user_id = ? AND evaluation_id = ?
                """,
                ("user-a", status.evaluation_id),
            ).fetchone()
        self.assertEqual(row[0], "dispatching")
        self.assertEqual(row[1], 1)
        self.assertEqual(row[2], consumed.consumed_at)
        self.assertTrue(row[3])
        self.assertIsNone(row[4])
        public = self.service.get_status(
            "user-a", status.evaluation_id
        )
        self.assertEqual(public.provider_calls_reserved, 1)
        self.assertEqual(public.provider_calls_dispatched, 1)
        self.assertFalse(public.remote_outcome_unknown)
        with self.assertRaisesRegex(
            PairwiseExecutionConflict, "capability"
        ):
            self.service._repository._consume_candidate_capability(
                lease, capability
            )

    def test_concurrent_candidate_consumption_has_one_winner(self) -> None:
        status, lease, now = self._queue_and_claim()
        self._dispatch_authority(status, now)
        capability = self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-a",
        )

        def consume(_index: int):
            try:
                return (
                    self.service._repository
                    ._consume_candidate_capability(lease, capability)
                )
            except PairwiseExecutionConflict:
                return None

        with ThreadPoolExecutor(max_workers=32) as pool:
            results = list(pool.map(consume, range(32)))
        self.assertEqual(
            sum(value is not None for value in results), 1
        )
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT state, paid_attempt_count
                FROM twin_eval_execution_call_checkpoints
                WHERE user_id = ? AND evaluation_id = ?
                """,
                ("user-a", status.evaluation_id),
            ).fetchone()
        self.assertEqual(row, ("dispatching", 1))

    def test_candidate_consumption_failure_rolls_back_fence(self) -> None:
        status, lease, now = self._queue_and_claim()
        self._dispatch_authority(status, now)
        capability = self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-a",
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TRIGGER fail_candidate_consume
                AFTER UPDATE OF state
                ON twin_eval_execution_call_checkpoints
                WHEN NEW.state = 'dispatching'
                BEGIN
                  SELECT RAISE(ABORT, 'injected consume failure');
                END
                """
            )
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository._consume_candidate_capability(
                lease, capability
            )
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT state, paid_attempt_count, consumed_at
                FROM twin_eval_execution_call_checkpoints
                WHERE user_id = ? AND evaluation_id = ?
                """,
                ("user-a", status.evaluation_id),
            ).fetchone()
        self.assertEqual(row, ("reserved", 0, None))

    def test_candidate_consumption_rejects_forgery_and_regrant(
        self,
    ) -> None:
        status, lease, now = self._queue_and_claim()
        authority = self._dispatch_authority(status, now)
        capability = self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-a",
        )
        forged = type(capability)(
            user_id=capability.user_id,
            evaluation_id=capability.evaluation_id,
            call_id=capability.call_id,
            call_ordinal=capability.call_ordinal,
            lease_generation=capability.lease_generation,
            adapter_revision=capability.adapter_revision,
            authorized_at=capability.authorized_at,
            call_deadline_at=capability.call_deadline_at,
            permit="forged-permit",
            adapter_input=capability.adapter_input,
            provider_idempotency_key=(
                capability.provider_idempotency_key
            ),
        )
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository._consume_candidate_capability(
                lease, forged
            )
        self.assertTrue(authority.revoke_consent(user_id="user-a"))
        with sqlite3.connect(self.db_path) as conn:
            runtime_epoch = conn.execute(
                """
                SELECT config_epoch
                FROM twin_eval_dispatch_runtime
                WHERE singleton = 1
                """
            ).fetchone()[0]
        authority.grant_consent(
            user_id="user-a",
            scope="pairwise_remote_evaluation",
            consent_version=_CONSENT,
            config_digest=self.config.digest,
            config_epoch=runtime_epoch,
            granted_at=status.created_at,
            expires_at=self._offset(status.created_at, 55),
        )
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository._consume_candidate_capability(
                lease, capability
            )
        with sqlite3.connect(self.db_path) as conn:
            state = conn.execute(
                """
                SELECT state
                FROM twin_eval_execution_call_checkpoints
                WHERE user_id = ? AND evaluation_id = ?
                """,
                ("user-a", status.evaluation_id),
            ).fetchone()[0]
        self.assertEqual(state, "reserved")

    def test_candidate_consumption_rejects_cancel_and_exact_deadline(
        self,
    ) -> None:
        status, lease, now = self._queue_and_claim()
        self._dispatch_authority(status, now)
        capability = self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-a",
        )
        exact_deadline = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                DROP TRIGGER
                  enforce_twin_eval_execution_call_transition
                """
            )
            conn.execute(
                """
                UPDATE twin_eval_execution_call_checkpoints
                SET call_deadline_at = ?
                WHERE user_id = ? AND evaluation_id = ?
                """,
                (exact_deadline, "user-a", status.evaluation_id),
            )
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository._consume_candidate_capability(
                lease, capability
            )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE twin_eval_execution_call_checkpoints
                SET call_deadline_at = ?
                WHERE user_id = ? AND evaluation_id = ?
                """,
                (
                    capability.call_deadline_at,
                    "user-a",
                    status.evaluation_id,
                ),
            )
        self.service.cancel("user-a", status.evaluation_id)
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository._consume_candidate_capability(
                lease, capability
            )

    def test_candidate_consumption_rejects_tampered_ciphertext(
        self,
    ) -> None:
        status, lease, now = self._queue_and_claim()
        self._dispatch_authority(status, now)
        capability = self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-a",
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                DROP TRIGGER
                  enforce_twin_eval_execution_call_transition
                """
            )
            ciphertext = bytes(
                conn.execute(
                    """
                    SELECT checkpoint_ciphertext
                    FROM twin_eval_execution_call_checkpoints
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    ("user-a", status.evaluation_id),
                ).fetchone()[0]
            )
            conn.execute(
                """
                UPDATE twin_eval_execution_call_checkpoints
                SET checkpoint_ciphertext = ?
                WHERE user_id = ? AND evaluation_id = ?
                """,
                (
                    ciphertext[:-1]
                    + bytes((ciphertext[-1] ^ 1,)),
                    "user-a",
                    status.evaluation_id,
                ),
            )
        with self.assertRaisesRegex(
            PairwiseExecutionConflict, "authenticated"
        ):
            self.service._repository._consume_candidate_capability(
                lease, capability
            )

    def test_candidate_consumption_ignores_temp_shadow_tables(
        self,
    ) -> None:
        status, lease, now = self._queue_and_claim()
        self._dispatch_authority(status, now)
        capability = self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-a",
        )
        original_connect = self.service._repository._connect

        def shadowed_connect():
            conn = original_connect()
            conn.execute(
                """
                CREATE TEMP TABLE twin_eval_execution_requests (
                    user_id TEXT,
                    evaluation_id TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TEMP TABLE twin_eval_execution_call_checkpoints (
                    user_id TEXT,
                    evaluation_id TEXT,
                    call_id TEXT
                )
                """
            )
            return conn

        self.service._repository._connect = shadowed_connect
        try:
            consumed = (
                self.service._repository
                ._consume_candidate_capability(lease, capability)
            )
        finally:
            self.service._repository._connect = original_connect
        self.assertEqual(consumed.call_id, capability.call_id)

    def test_reserved_checkpoint_migrates_to_dispatch_state_shape(
        self,
    ) -> None:
        status, lease, now = self._queue_and_claim()
        self._dispatch_authority(status, now)
        capability = self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-a",
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                DROP TRIGGER
                  enforce_twin_eval_execution_call_transition
                """
            )
            conn.execute(
                """
                DROP TRIGGER enforce_twin_eval_execution_no_open_calls
                """
            )
            conn.execute(
                "DROP INDEX idx_twin_eval_execution_call_state"
            )
            conn.execute(
                """
                ALTER TABLE twin_eval_execution_call_checkpoints
                RENAME TO checkpoint_source
                """
            )
            conn.execute(
                """
                CREATE TABLE twin_eval_execution_call_checkpoints (
                  user_id TEXT NOT NULL,
                  evaluation_id TEXT NOT NULL,
                  call_id TEXT NOT NULL,
                  call_kind TEXT NOT NULL,
                  call_ordinal INTEGER NOT NULL,
                  binding_key_id TEXT NOT NULL,
                  coordinate_binding TEXT NOT NULL,
                  payload_binding TEXT NOT NULL,
                  adapter_binding TEXT NOT NULL,
                  checkpoint_binding TEXT NOT NULL,
                  checkpoint_ciphertext BLOB NOT NULL,
                  request_artifact_digest TEXT NOT NULL,
                  config_digest TEXT NOT NULL,
                  consent_config_epoch INTEGER NOT NULL,
                  consent_revision INTEGER NOT NULL,
                  lease_generation INTEGER NOT NULL,
                  lease_token_digest TEXT NOT NULL,
                  permit_digest TEXT NOT NULL,
                  idempotency_supported INTEGER NOT NULL,
                  state TEXT NOT NULL CHECK(state = 'reserved'),
                  paid_attempt_count INTEGER NOT NULL
                    CHECK(paid_attempt_count = 1),
                  reserved_at TEXT NOT NULL,
                  call_deadline_at TEXT NOT NULL,
                  PRIMARY KEY(user_id, evaluation_id, call_id)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO twin_eval_execution_call_checkpoints
                SELECT
                  user_id, evaluation_id, call_id, call_kind,
                  call_ordinal, binding_key_id, coordinate_binding,
                  payload_binding, adapter_binding, checkpoint_binding,
                  checkpoint_ciphertext, request_artifact_digest,
                  config_digest, consent_config_epoch, consent_revision,
                  lease_generation, lease_token_digest, permit_digest,
                  idempotency_supported, 'reserved', 1,
                  reserved_at, call_deadline_at
                FROM checkpoint_source
                """
            )
            conn.execute("DROP TABLE checkpoint_source")
        init_db(self.db_path)
        with sqlite3.connect(self.db_path) as conn:
            columns = {
                row[1]
                for row in conn.execute(
                    """
                    PRAGMA table_info(
                      twin_eval_execution_call_checkpoints
                    )
                    """
                ).fetchall()
            }
            row = conn.execute(
                """
                SELECT call_id, state, paid_attempt_count,
                       consumed_at, consume_binding, outcome_unknown_at
                FROM twin_eval_execution_call_checkpoints
                WHERE user_id = ? AND evaluation_id = ?
                """,
                ("user-a", status.evaluation_id),
            ).fetchone()
        self.assertTrue(
            {
                "consumed_at",
                "consume_binding",
                "outcome_unknown_at",
            }.issubset(columns)
        )
        self.assertEqual(
            row,
            (capability.call_id, "reserved", 0, None, None, None),
        )

    def test_checkpoint_migration_recovers_legacy_only_interruption(
        self,
    ) -> None:
        status, lease, now = self._queue_and_claim()
        self._dispatch_authority(status, now)
        capability = self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-a",
        )
        self._rename_checkpoint_table_as_interrupted(
            modern_copy=None
        )
        init_db(self.db_path)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT call_id, state
                FROM twin_eval_execution_call_checkpoints
                WHERE user_id = ? AND evaluation_id = ?
                """,
                ("user-a", status.evaluation_id),
            ).fetchone()
        self.assertEqual(row, (capability.call_id, "reserved"))

    def test_checkpoint_migration_recovers_empty_modern_interruption(
        self,
    ) -> None:
        status, lease, now = self._queue_and_claim()
        self._dispatch_authority(status, now)
        capability = self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-a",
        )
        self._rename_checkpoint_table_as_interrupted(
            modern_copy="empty"
        )
        init_db(self.db_path)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT call_id, state
                FROM twin_eval_execution_call_checkpoints
                WHERE user_id = ? AND evaluation_id = ?
                """,
                ("user-a", status.evaluation_id),
            ).fetchone()
            legacy_exists = conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table'
                  AND name =
                    'twin_eval_execution_call_checkpoints_legacy_shape'
                """
            ).fetchone()
        self.assertEqual(row, (capability.call_id, "reserved"))
        self.assertIsNone(legacy_exists)

    def test_checkpoint_migration_fails_closed_when_both_have_data(
        self,
    ) -> None:
        status, lease, now = self._queue_and_claim()
        self._dispatch_authority(status, now)
        self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-a",
        )
        self._rename_checkpoint_table_as_interrupted(
            modern_copy="populated"
        )
        with self.assertRaisesRegex(
            sqlite3.OperationalError, "ambiguous interrupted"
        ):
            init_db(self.db_path)
        with sqlite3.connect(self.db_path) as conn:
            current_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM twin_eval_execution_call_checkpoints
                """
            ).fetchone()[0]
            legacy_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM twin_eval_execution_call_checkpoints_legacy_shape
                """
            ).fetchone()[0]
        self.assertEqual((current_count, legacy_count), (1, 1))

    def test_dispatching_call_becomes_unknown_on_lease_expiry(
        self,
    ) -> None:
        status, lease, now = self._queue_and_claim()
        self._dispatch_authority(status, now)
        capability = self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-a",
        )
        self.service._repository._consume_candidate_capability(
            lease, capability
        )
        reaped = self.service._repository.reap_expired_leases(
            "user-a", now_utc=lease.lease_expires_at
        )
        self.assertEqual(reaped, (status.evaluation_id,))
        failed = self.service.get_status(
            "user-a", status.evaluation_id
        )
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_code, "remote_outcome_unknown")
        self.assertEqual(failed.provider_calls_dispatched, 1)
        self.assertTrue(failed.remote_outcome_unknown)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT state, outcome_unknown_at
                FROM twin_eval_execution_call_checkpoints
                WHERE user_id = ? AND evaluation_id = ?
                """,
                ("user-a", status.evaluation_id),
            ).fetchone()
        self.assertEqual(row[0], "outcome_unknown")
        self.assertTrue(row[1])

    def test_dispatching_cancel_surfaces_unknown_tombstone(self) -> None:
        status, lease, now = self._queue_and_claim()
        self._dispatch_authority(status, now)
        capability = self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-a",
        )
        consumed = (
            self.service._repository._consume_candidate_capability(
                lease, capability
            )
        )
        self.service.cancel("user-a", status.evaluation_id)
        cancelled = self.service._repository.acknowledge_cancel(
            lease, now_utc=self._offset(now, 2)
        )
        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(
            cancelled.error_code, "remote_outcome_unknown"
        )
        self.assertTrue(cancelled.remote_outcome_unknown)
        self.assertEqual(cancelled.provider_calls_dispatched, 1)
        transport_input, _provider_key = (
            consumed._take_transport_input()
        )
        self.assertEqual(
            transport_input["profile"]["items"][0]["content"],
            self.builder.secret,
        )

    def test_dispatching_purge_keeps_content_free_unknown_tombstone(
        self,
    ) -> None:
        status, lease, now = self._queue_and_claim()
        self._dispatch_authority(status, now)
        capability = self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-a",
        )
        consumed = (
            self.service._repository._consume_candidate_capability(
                lease, capability
            )
        )
        purged = self.service.purge_expired(
            now_utc=status.request_expires_at
        )
        self.assertEqual(purged, (status.evaluation_id,))
        tombstone = self.service.get_status(
            "user-a", status.evaluation_id
        )
        self.assertEqual(tombstone.status, "cancelled")
        self.assertFalse(tombstone.content_retained)
        self.assertTrue(tombstone.remote_outcome_unknown)
        self.assertEqual(
            tombstone.error_code, "remote_outcome_unknown"
        )
        self.assertEqual(tombstone.provider_calls_reserved, 1)
        self.assertEqual(tombstone.provider_calls_dispatched, 1)
        with sqlite3.connect(self.db_path) as conn:
            checkpoint_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM twin_eval_execution_call_checkpoints
                WHERE user_id = ? AND evaluation_id = ?
                """,
                ("user-a", status.evaluation_id),
            ).fetchone()[0]
        self.assertEqual(checkpoint_count, 0)
        transport_input, _provider_key = (
            consumed._take_transport_input()
        )
        self.assertEqual(
            transport_input["profile"]["items"][0]["content"],
            self.builder.secret,
        )

    def test_dispatching_candidate_blocks_fixture_completion(
        self,
    ) -> None:
        _status, lease, now = self._queue_and_claim()
        self._dispatch_authority(_status, now)
        capability = self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-a",
        )
        self.service._repository._consume_candidate_capability(
            lease, capability
        )
        with self.assertRaisesRegex(
            PairwiseExecutionConflict, "recorded outcomes"
        ):
            self.service._repository.complete_with_report(
                lease,
                self._fixture_report(lease),
                now_utc=self._offset(now, 2),
            )

    def test_reserved_candidate_blocks_fixture_completion_until_outcome(
        self,
    ) -> None:
        status, lease, now = self._queue_and_claim()
        authority = self._dispatch_authority(status, now)
        self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-a",
        )
        with self.assertRaisesRegex(
            PairwiseExecutionConflict, "recorded outcomes"
        ):
            self.service._repository.complete_with_report(
                lease,
                self._fixture_report(lease),
                now_utc=self._offset(now, 2),
            )
        self.assertEqual(
            self.service.get_status(
                "user-a", status.evaluation_id
            ).status,
            "running",
        )

    def test_checkpoint_content_deletes_after_terminal_cancel(self) -> None:
        status, lease, now = self._queue_and_claim()
        authority = self._dispatch_authority(status, now)
        self.service._repository._begin_candidate_call(
            lease,
            prompt_id="prompt-1",
            system_id="system-a",
        )
        self.service.cancel("user-a", status.evaluation_id)
        self.service._repository.acknowledge_cancel(
            lease, now_utc=self._offset(now, 2)
        )
        deleted = self.service.delete_request_content(
            "user-a", status.evaluation_id
        )
        self.assertFalse(deleted.content_retained)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM twin_eval_execution_call_checkpoints"
                ).fetchone()[0],
                0,
            )

    def test_concurrent_claim_is_single_attempt_and_token_is_secret(
        self,
    ) -> None:
        status = self._submit()
        now = self._offset(status.created_at, 1)
        queued = self.service._repository.queue(
            "user-a", status.evaluation_id, now_utc=now
        )
        self.assertEqual(queued.status, "queued")

        def claim(index: int):
            return self.service._repository.claim_next(
                "user-a",
                f"worker-{index}",
                now_utc=now,
                lease_seconds=5,
                execution_deadline_seconds=30,
            )

        with ThreadPoolExecutor(max_workers=16) as pool:
            leases = tuple(pool.map(claim, range(32)))
        winners = tuple(lease for lease in leases if lease is not None)
        self.assertEqual(len(winners), 1)
        lease = winners[0]
        public = self.service.get_status(
            "user-a", status.evaluation_id
        )
        self.assertEqual(public.status, "running")
        self.assertEqual(public.attempt_count, 1)
        self.assertNotIn(lease.token, str(public.to_dict()))
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT lease_owner, lease_token_digest, attempt_count
                FROM twin_eval_execution_requests
                WHERE user_id = ? AND evaluation_id = ?
                """,
                ("user-a", status.evaluation_id),
            ).fetchone()
        self.assertEqual(row[0], lease.worker_id)
        self.assertEqual(len(row[1]), 64)
        self.assertEqual(row[2], 1)
        self.assertNotEqual(row[1], lease.token)
        for path in (
            self.db_path,
            Path(f"{self.db_path}-wal"),
            Path(f"{self.db_path}-shm"),
        ):
            if path.exists():
                self.assertNotIn(
                    lease.token.encode("utf-8"), path.read_bytes()
                )

    def test_renew_is_fenced_and_expiry_boundary_is_exclusive(
        self,
    ) -> None:
        _status, lease, claimed_at = self._queue_and_claim()
        renewed = self.service._repository.renew(
            lease,
            now_utc=self._offset(claimed_at, 4),
            lease_seconds=5,
        )
        self.assertEqual(
            renewed.lease_expires_at,
            self._offset(claimed_at, 9),
        )
        forged = replace(renewed, token="forged-token")
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository.renew(
                forged,
                now_utc=self._offset(claimed_at, 5),
                lease_seconds=5,
            )
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository.renew(
                renewed,
                now_utc=renewed.lease_expires_at,
                lease_seconds=5,
            )

    def test_cancelled_worker_must_acknowledge_with_active_fence(
        self,
    ) -> None:
        status, lease, claimed_at = self._queue_and_claim()
        requested = self.service.cancel(
            "user-a", status.evaluation_id
        )
        self.assertEqual(requested.status, "cancel_requested")
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository.renew(
                lease,
                now_utc=self._offset(claimed_at, 2),
            )
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository.acknowledge_cancel(
                replace(lease, worker_id="other-worker"),
                now_utc=self._offset(claimed_at, 2),
            )
        cancelled = self.service._repository.acknowledge_cancel(
            lease,
            now_utc=self._offset(claimed_at, 2),
        )
        self.assertEqual(cancelled.status, "cancelled")
        self.assertIsNotNone(cancelled.cancel_requested_at)
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository.acknowledge_cancel(
                lease,
                now_utc=self._offset(claimed_at, 3),
            )

    def test_expired_cancellation_is_finalized_only_by_reaper(
        self,
    ) -> None:
        status, lease, claimed_at = self._queue_and_claim()
        self.service.cancel("user-a", status.evaluation_id)
        expired_at = self._offset(claimed_at, 5)
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository.acknowledge_cancel(
                lease, now_utc=expired_at
            )
        self.assertEqual(
            self.service._repository.reap_expired_leases(
                "user-a", now_utc=expired_at
            ),
            (status.evaluation_id,),
        )
        cancelled = self.service.get_status(
            "user-a", status.evaluation_id
        )
        self.assertEqual(cancelled.status, "cancelled")
        self.assertIsNone(cancelled.error_code)

    def test_expired_lease_fails_once_and_is_never_reclaimed(
        self,
    ) -> None:
        status, lease, claimed_at = self._queue_and_claim()
        expired_at = self._offset(claimed_at, 5)
        self.assertEqual(
            self.service._repository.reap_expired_leases(
                "user-a", now_utc=expired_at
            ),
            (status.evaluation_id,),
        )
        failed = self.service.get_status(
            "user-a", status.evaluation_id
        )
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_code, "worker_lease_expired")
        self.assertEqual(failed.attempt_count, 1)
        self.assertIsNone(
            self.service._repository.claim_next(
                "user-a",
                "worker-b",
                now_utc=self._offset(claimed_at, 6),
                lease_seconds=5,
                execution_deadline_seconds=30,
            )
        )
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository.fail(
                lease,
                error_code="internal_error",
                now_utc=self._offset(claimed_at, 4),
            )

    def test_retention_purge_fences_running_worker(
        self,
    ) -> None:
        status, lease, claimed_at = self._queue_and_claim()
        self.assertEqual(
            self.service.purge_expired(
                now_utc="2100-01-01T00:00:00Z"
            ),
            (status.evaluation_id,),
        )
        purged = self.service.get_status(
            "user-a", status.evaluation_id
        )
        self.assertEqual(purged.status, "cancelled")
        self.assertFalse(purged.content_retained)
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository.fail(
                lease,
                error_code="internal_error",
                now_utc=self._offset(claimed_at, 2),
            )

    def test_worker_mutations_are_user_and_error_code_scoped(
        self,
    ) -> None:
        _status, lease, claimed_at = self._queue_and_claim()
        with self.assertRaises(PairwiseExecutionNotFound):
            self.service._repository.fail(
                replace(lease, user_id="user-b"),
                error_code="internal_error",
                now_utc=self._offset(claimed_at, 2),
            )
        with self.assertRaises(PairwiseExecutionError):
            self.service._repository.fail(
                lease,
                error_code="secret provider exception",
                now_utc=self._offset(claimed_at, 2),
            )
        with self.assertRaises(PairwiseExecutionError):
            self.service._repository.fail(
                lease,
                error_code="worker_lease_expired",
                now_utc=self._offset(claimed_at, 2),
            )
        failed = self.service._repository.fail(
            lease,
            error_code="internal_error",
            now_utc=self._offset(claimed_at, 2),
        )
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_code, "internal_error")

    def test_queue_rejects_request_at_absolute_expiry(self) -> None:
        status = self._submit()
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository.queue(
                "user-a",
                status.evaluation_id,
                now_utc=status.request_expires_at,
            )
        second_spec = _spec("queued before expiry")
        second = self._submit(
            second_spec,
            receipt=self._receipt(second_spec),
            idempotency_key="queued-expiry",
        )
        self.service._repository.queue(
            "user-a",
            second.evaluation_id,
            now_utc=self._offset(second.created_at, 1),
        )
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository.queue(
                "user-a",
                second.evaluation_id,
                now_utc=second.request_expires_at,
            )

    def test_atomic_completion_persists_one_encrypted_result_and_retries(
        self,
    ) -> None:
        status, lease, claimed_at = self._queue_and_claim()
        report = self._fixture_report(lease)
        artifact = json.loads(canonical_json(lease.artifact))
        report_repository = (
            self.service._repository._report_repository
        )
        prepared = report_repository._prepare_report_write(
            "user-a",
            report,
            profile_bundle=parse_cortex_profile_bundle(
                artifact["profile_bundle"]
            ),
            evidence_expires_at=artifact["request_expires_at"],
        )
        with sqlite3.connect(self.db_path) as conn:
            with self.assertRaisesRegex(
                ValueError, "active transaction"
            ):
                report_repository._save_report_tx(conn, prepared)
        completed = self.service._repository.complete_with_report(
            lease,
            report,
            now_utc=self._offset(claimed_at, 2),
        )
        self.assertEqual(completed.status, "succeeded")
        self.assertEqual(completed.result_run_id, report.run_id)
        self.assertEqual(completed.attempt_count, 1)

        def retry(_: int):
            return self.service._repository.complete_with_report(
                lease,
                report,
                now_utc=self._offset(claimed_at, 3),
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            retries = tuple(pool.map(retry, range(16)))
        self.assertTrue(
            all(value == completed for value in retries)
        )
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT result_artifact_digest, completion_binding,
                       lease_owner, lease_token_digest, completed_at
                FROM twin_eval_execution_requests
                WHERE user_id = ? AND evaluation_id = ?
                """,
                ("user-a", status.evaluation_id),
            ).fetchone()
            run_count = conn.execute(
                """
                SELECT COUNT(*) FROM twin_eval_runs
                WHERE user_id = ? AND run_id = ?
                """,
                ("user-a", report.run_id),
            ).fetchone()[0]
        self.assertEqual(row[0], report.artifact_digest)
        self.assertEqual(len(row[1]), 64)
        self.assertIsNone(row[2])
        self.assertIsNone(row[3])
        self.assertIsNotNone(row[4])
        self.assertEqual(run_count, 1)
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository.complete_with_report(
                lease,
                replace(report, seed=999),
                now_utc=self._offset(claimed_at, 3),
            )
        self.assertEqual(
            self.service._repository._report_repository.load_report(
                "user-a", report.run_id
            ),
            report,
        )
        with sqlite3.connect(self.db_path) as conn:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "invalid twin evaluation result state",
            ):
                conn.execute(
                    """
                    UPDATE twin_eval_execution_requests
                    SET result_artifact_digest = NULL
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    ("user-a", status.evaluation_id),
                )

    def test_completion_update_failure_rolls_back_every_report_row(
        self,
    ) -> None:
        status, lease, claimed_at = self._queue_and_claim()
        report = self._fixture_report(lease)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TRIGGER reject_pairwise_completion
                BEFORE UPDATE OF status
                ON twin_eval_execution_requests
                WHEN NEW.status = 'succeeded'
                BEGIN
                  SELECT RAISE(ABORT, 'injected completion failure');
                END
                """
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.service._repository.complete_with_report(
                lease,
                report,
                now_utc=self._offset(claimed_at, 2),
            )
        with sqlite3.connect(self.db_path) as conn:
            for table in (
                "twin_eval_runs",
                "twin_eval_profile_artifacts",
                "twin_eval_report_artifacts",
                "twin_eval_candidates",
                "twin_eval_comparisons",
                "twin_eval_resolved_comparisons",
                "twin_eval_rankings",
                "twin_eval_ranking_manifests",
            ):
                self.assertEqual(
                    conn.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0],
                    0,
                    table,
                )
            row = conn.execute(
                """
                SELECT status, result_run_id, completed_at,
                       lease_token_digest
                FROM twin_eval_execution_requests
                WHERE user_id = ? AND evaluation_id = ?
                """,
                ("user-a", status.evaluation_id),
            ).fetchone()
            conn.execute("DROP TRIGGER reject_pairwise_completion")
        self.assertEqual(row[0], "running")
        self.assertIsNone(row[1])
        self.assertIsNone(row[2])
        self.assertIsNotNone(row[3])
        completed = self.service._repository.complete_with_report(
            lease,
            report,
            now_utc=self._offset(claimed_at, 3),
        )
        self.assertEqual(completed.status, "succeeded")

    def test_cancel_or_expiry_before_completion_creates_no_report(
        self,
    ) -> None:
        status, lease, claimed_at = self._queue_and_claim()
        report = self._fixture_report(lease)
        self.service.cancel("user-a", status.evaluation_id)
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository.complete_with_report(
                lease,
                report,
                now_utc=self._offset(claimed_at, 2),
            )
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM twin_eval_runs"
                ).fetchone()[0],
                0,
            )

        second_spec = _spec("expiry completion")
        second = self._submit(
            second_spec,
            receipt=self._receipt(second_spec),
            idempotency_key="expiry-completion",
        )
        second_now = self._offset(second.created_at, 1)
        self.service._repository.queue(
            "user-a", second.evaluation_id, now_utc=second_now
        )
        second_lease = self.service._repository.claim_next(
            "user-a",
            "worker-b",
            now_utc=second_now,
            lease_seconds=5,
            execution_deadline_seconds=30,
        )
        second_report = self._fixture_report(second_lease)
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository.complete_with_report(
                second_lease,
                second_report,
                now_utc=self._offset(second_now, 5),
            )
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM twin_eval_runs"
                ).fetchone()[0],
                0,
            )

    def test_completion_requires_exact_execution_result_binding(
        self,
    ) -> None:
        _status, lease, claimed_at = self._queue_and_claim()
        report = self._fixture_report(lease)
        cases = (
            replace(report, seed=999),
            replace(
                report,
                systems=tuple(reversed(report.systems)),
            ),
            replace(
                report,
                metadata={
                    **dict(report.metadata),
                    "execution_result_manifest": {
                        **dict(
                            report.metadata[
                                "execution_result_manifest"
                            ]
                        ),
                        "execution_config_digest": "forged",
                    },
                },
            ),
        )
        for changed in cases:
            with self.assertRaises(PairwiseExecutionConflict):
                self.service._repository.complete_with_report(
                    lease,
                    changed,
                    now_utc=self._offset(claimed_at, 2),
                )
        forged_artifact = json.loads(canonical_json(lease.artifact))
        forged_artifact["request"]["prompts"][0]["text"] = (
            "forged prompt hidden behind the old outer digest"
        )
        forged_lease = replace(
            lease, artifact=forged_artifact
        )
        forged_report = (
            self.service._repository._build_fixture_report(
                forged_lease
            )
        )
        with self.assertRaises(PairwiseExecutionConflict):
            self.service._repository.complete_with_report(
                forged_lease,
                forged_report,
                now_utc=self._offset(claimed_at, 2),
            )
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM twin_eval_runs"
                ).fetchone()[0],
                0,
            )

    def test_completion_and_cancel_race_has_only_atomic_outcomes(
        self,
    ) -> None:
        status, lease, claimed_at = self._queue_and_claim()
        report = self._fixture_report(lease)
        barrier = threading.Barrier(2)

        def complete():
            barrier.wait()
            try:
                return self.service._repository.complete_with_report(
                    lease,
                    report,
                    now_utc=self._offset(claimed_at, 2),
                ).status
            except PairwiseExecutionConflict:
                return "conflict"

        def cancel():
            barrier.wait()
            return self.service.cancel(
                "user-a", status.evaluation_id
            ).status

        with ThreadPoolExecutor(max_workers=2) as pool:
            complete_future = pool.submit(complete)
            cancel_future = pool.submit(cancel)
            outcomes = (
                complete_future.result(),
                cancel_future.result(),
            )
        final = self.service.get_status(
            "user-a", status.evaluation_id
        )
        with sqlite3.connect(self.db_path) as conn:
            report_count = conn.execute(
                """
                SELECT COUNT(*) FROM twin_eval_runs
                WHERE user_id = ? AND run_id = ?
                """,
                ("user-a", report.run_id),
            ).fetchone()[0]
        if final.status == "succeeded":
            self.assertEqual(report_count, 1)
            self.assertIn("succeeded", outcomes)
        else:
            self.assertEqual(final.status, "cancel_requested")
            self.assertEqual(report_count, 0)
            self.assertIn("conflict", outcomes)

    def test_completion_keeps_private_result_and_token_out_of_storage(
        self,
    ) -> None:
        _status, lease, claimed_at = self._queue_and_claim()
        sentinels = (
            self.builder.secret,
            "Write a private status update.",
        )
        report = self._fixture_report(lease)
        self.service._repository.complete_with_report(
            lease,
            report,
            now_utc=self._offset(claimed_at, 2),
        )
        for path in (
            self.db_path,
            Path(f"{self.db_path}-wal"),
            Path(f"{self.db_path}-shm"),
        ):
            if path.exists():
                content = path.read_bytes()
                for sentinel in sentinels:
                    self.assertNotIn(
                        sentinel.encode("utf-8"), content
                    )
                self.assertNotIn(
                    lease.token.encode("utf-8"), content
                )

    def test_completed_result_deletion_fails_explicitly_and_atomically(
        self,
    ) -> None:
        _status, lease, claimed_at = self._queue_and_claim()
        report = self._fixture_report(lease)
        repository = self.service._repository._report_repository
        self.service._repository.complete_with_report(
            lease,
            report,
            now_utc=self._offset(claimed_at, 2),
        )
        with self.assertRaises(EvaluationArtifactInUse):
            repository.delete_report(
                "user-a",
                report.run_id,
                expected_artifact_digest=report.artifact_digest,
            )

        second_spec = _spec("unreferenced retention report")
        second = self._submit(
            second_spec,
            receipt=self._receipt(second_spec),
            idempotency_key="unreferenced-retention",
        )
        second_now = self._offset(second.created_at, 1)
        self.service._repository.queue(
            "user-a", second.evaluation_id, now_utc=second_now
        )
        second_lease = self.service._repository.claim_next(
            "user-a",
            "retention-worker",
            now_utc=second_now,
            lease_seconds=5,
            execution_deadline_seconds=30,
        )
        second_report = self._fixture_report(second_lease)
        second_artifact = json.loads(
            canonical_json(second_lease.artifact)
        )
        repository.save_report(
            "user-a",
            second_report,
            profile_bundle=parse_cortex_profile_bundle(
                second_artifact["profile_bundle"]
            ),
            evidence_expires_at=second_artifact[
                "request_expires_at"
            ],
        )
        self.assertEqual(
            repository.purge_reports_before(
                "user-a",
                "2100-01-01T00:00:00Z",
                expected_run_ids=(second_report.run_id,),
            ),
            (second_report.run_id,),
        )
        self.assertEqual(
            repository.load_report("user-a", report.run_id),
            report,
        )
        with self.assertRaises(KeyError):
            repository.load_report(
                "user-a", second_report.run_id
            )

    def test_completion_resolves_historical_binding_key_or_fails_closed(
        self,
    ) -> None:
        _status, lease, claimed_at = self._queue_and_claim()
        report = self._fixture_report(lease)
        rotated = PairwiseExecutionService(
            self.db_path,
            cipher=self.keyring,
            profile_builder=self.builder,
            consent_authority=self.consent,
            policy=self.policy,
            signing_key=_SIGNING_KEY,
            binding_keys=_BINDING_KEYS,
            active_binding_key_id="execution-binding-v2",
            config=self.config,
        )
        completed = (
            rotated._repository.complete_with_report(
                lease,
                report,
                now_utc=self._offset(claimed_at, 2),
            )
        )
        self.assertEqual(completed.status, "succeeded")

        without_history = PairwiseExecutionService(
            self.db_path,
            cipher=self.keyring,
            profile_builder=self.builder,
            consent_authority=self.consent,
            policy=self.policy,
            signing_key=_SIGNING_KEY,
            binding_keys={
                "execution-binding-v2": _BINDING_KEYS[
                    "execution-binding-v2"
                ]
            },
            active_binding_key_id="execution-binding-v2",
            config=self.config,
        )
        with self.assertRaises(PairwiseExecutionUnavailable):
            without_history._repository.complete_with_report(
                lease,
                report,
                now_utc=self._offset(claimed_at, 3),
            )

    def test_completion_retry_authenticates_encrypted_report(
        self,
    ) -> None:
        _status, lease, claimed_at = self._queue_and_claim()
        report = self._fixture_report(lease)
        self.service._repository.complete_with_report(
            lease,
            report,
            now_utc=self._offset(claimed_at, 2),
        )
        with sqlite3.connect(self.db_path) as conn:
            ciphertext = bytes(
                conn.execute(
                    """
                    SELECT artifact_ciphertext
                    FROM twin_eval_report_artifacts
                    WHERE user_id = ? AND run_id = ?
                    """,
                    ("user-a", report.run_id),
                ).fetchone()[0]
            )
            conn.execute(
                """
                UPDATE twin_eval_report_artifacts
                SET artifact_ciphertext = ?
                WHERE user_id = ? AND run_id = ?
                """,
                (
                    ciphertext[:-1]
                    + bytes((ciphertext[-1] ^ 1,)),
                    "user-a",
                    report.run_id,
                ),
            )
        with self.assertRaisesRegex(
            PairwiseExecutionConflict, "persisted"
        ):
            self.service._repository.complete_with_report(
                lease,
                report,
                now_utc=self._offset(claimed_at, 3),
            )


class PairwiseExecutionLeaseMigrationTests(unittest.TestCase):
    def test_prototype_execution_table_is_rebuilt_fail_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            db_path = Path(root) / "prototype.sqlite"
            with sqlite3.connect(db_path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE twin_eval_execution_requests (
                      user_id TEXT NOT NULL,
                      evaluation_id TEXT NOT NULL,
                      receipt_id TEXT NOT NULL,
                      idempotency_digest TEXT NOT NULL,
                      request_digest TEXT NOT NULL,
                      config_digest TEXT NOT NULL,
                      artifact_digest TEXT NOT NULL,
                      request_ciphertext BLOB NOT NULL,
                      consent_version TEXT NOT NULL,
                      status TEXT NOT NULL DEFAULT 'prepared',
                      cancel_requested_at TEXT,
                      result_run_id TEXT,
                      error_code TEXT,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL,
                      PRIMARY KEY(user_id, evaluation_id)
                    );
                    INSERT INTO twin_eval_execution_requests
                    VALUES (
                      'user-a', 'legacy-eval', 'legacy-receipt',
                      'legacy-idempotency', 'legacy-request',
                      'legacy-config', 'legacy-artifact', X'43584531',
                      'legacy-consent', 'running', NULL, NULL, NULL,
                      '2026-01-01T00:00:00Z',
                      '2026-01-02T00:00:00Z'
                    );
                    """
                )
            init_db(db_path)
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT * FROM twin_eval_execution_requests
                    WHERE user_id = 'user-a'
                      AND evaluation_id = 'legacy-eval'
                    """
                ).fetchone()
                indexes = {
                    value[1]
                    for value in conn.execute(
                        "PRAGMA index_list(twin_eval_execution_requests)"
                    )
                }
            self.assertEqual(row["status"], "cancelled")
            self.assertIsNone(row["request_ciphertext"])
            self.assertEqual(
                row["content_deleted_at"], "2026-01-02T00:00:00Z"
            )
            self.assertEqual(row["binding_key_id"], "legacy-unavailable")
            self.assertEqual(row["request_binding"], "legacy-request")
            self.assertEqual(row["attempt_count"], 0)
            self.assertIsNotNone(row["completed_at"])
            self.assertIn("idx_twin_eval_execution_status", indexes)
            self.assertIn("idx_twin_eval_execution_claim", indexes)

    def test_prelease_active_rows_fail_closed_and_terminal_is_backfilled(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            db_path = Path(root) / "legacy.sqlite"
            with sqlite3.connect(db_path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE twin_eval_execution_requests (
                      user_id TEXT NOT NULL,
                      evaluation_id TEXT NOT NULL,
                      receipt_id TEXT NOT NULL,
                      binding_key_id TEXT NOT NULL,
                      idempotency_digest TEXT NOT NULL,
                      request_binding TEXT NOT NULL,
                      config_digest TEXT NOT NULL,
                      artifact_digest TEXT NOT NULL,
                      request_ciphertext BLOB,
                      consent_version TEXT NOT NULL,
                      receipt_consumed_at TEXT NOT NULL,
                      request_expires_at TEXT NOT NULL,
                      content_deleted_at TEXT,
                      status TEXT NOT NULL,
                      cancel_requested_at TEXT,
                      result_run_id TEXT,
                      error_code TEXT,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL,
                      PRIMARY KEY(user_id, evaluation_id)
                    );
                    """
                )
                for evaluation_id, status in (
                    ("legacy-terminal", "cancelled"),
                    ("legacy-queued", "queued"),
                    ("legacy-running", "running"),
                ):
                    conn.execute(
                        """
                        INSERT INTO twin_eval_execution_requests
                        (
                          user_id, evaluation_id, receipt_id,
                          binding_key_id, idempotency_digest,
                          request_binding, config_digest, artifact_digest,
                          request_ciphertext, consent_version,
                          receipt_consumed_at, request_expires_at, status,
                          created_at, updated_at
                        )
                        VALUES (
                          'user-a', ?, ?, 'binding-v1', ?, ?, ?, ?,
                          X'43584531', 'consent-v1',
                          '2026-01-01T00:00:00Z',
                          '2099-01-01T00:00:00Z', ?,
                          '2026-01-01T00:00:00Z',
                          '2026-01-02T00:00:00Z'
                        )
                        """,
                        (
                            evaluation_id,
                            f"receipt-{evaluation_id}",
                            f"idem-{evaluation_id}",
                            f"binding-{evaluation_id}",
                            f"config-{evaluation_id}",
                            f"artifact-{evaluation_id}",
                            status,
                        ),
                    )
            init_db(db_path)
            with sqlite3.connect(db_path) as conn:
                columns = {
                    row[1]
                    for row in conn.execute(
                        "PRAGMA table_info(twin_eval_execution_requests)"
                    )
                }
                rows = conn.execute(
                    """
                    SELECT evaluation_id, status, completed_at,
                           lease_owner, lease_token_digest
                    FROM twin_eval_execution_requests
                    ORDER BY evaluation_id
                    """
                ).fetchall()
            self.assertTrue(
                {
                    "attempt_count",
                    "lease_generation",
                    "lease_owner",
                    "lease_token_digest",
                    "lease_expires_at",
                    "execution_deadline_at",
                    "queued_at",
                    "started_at",
                    "last_heartbeat_at",
                    "completed_at",
                    "result_artifact_digest",
                    "completion_binding",
                }.issubset(columns)
            )
            self.assertEqual(
                {row[1] for row in rows}, {"cancelled"}
            )
            self.assertTrue(all(row[2] is not None for row in rows))
            self.assertTrue(
                all(row[3] is None and row[4] is None for row in rows)
            )


if __name__ == "__main__":
    unittest.main()
