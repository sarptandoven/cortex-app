from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..database_maintenance import (
    DatabaseMaintenanceBusy,
    exclusive_database_maintenance,
    maintenance_locked_connect,
    require_exclusive_database_maintenance,
)
from ..keyring_errors import KeyringError
from ..sqlite_runtime import sqlite3
from .domain import (
    Candidate,
    ComparisonOutcome,
    ComparisonPlan,
    ComparisonRecord,
    EvaluationPrompt,
    EvaluationReport,
    JudgeDecision,
    RankingDiagnostics,
    RankingResult,
    ResolvedComparison,
    SystemRating,
    canonical_hash,
    canonical_json,
)
from .profile_adapter import CortexHeldOutProfileBundle
from .profile_artifacts import (
    PROFILE_ARTIFACT_ENCRYPTION_PURPOSE,
    PROFILE_ARTIFACT_SCHEMA_VERSION,
    ProfileArtifactEncryptionUnavailable,
    ProfileArtifactError,
    ProfileArtifactExpired,
    build_profile_artifact_envelope,
    parse_profile_artifact,
)
from .report_artifacts import (
    REPORT_ARTIFACT_ENCRYPTION_PURPOSE,
    REPORT_ARTIFACT_SCHEMA_VERSION,
    ParsedReportArtifact,
    ReportArtifactEncryptionUnavailable,
    ReportArtifactError,
    build_report_artifact_envelope,
    parse_report_artifact,
)


class EvaluationArtifactCollision(ValueError):
    """A run ID already exists with different immutable artifact bytes."""


class EvaluationArtifactInUse(ValueError):
    """A completed execution still references this immutable report."""


@dataclass(frozen=True)
class LegacyMigrationPreview:
    user_id: str
    run_ids: tuple[str, ...]
    selection_digest: str
    legacy_count: int
    encrypted_count: int
    inconsistent_count: int


@dataclass(frozen=True)
class LegacyMigrationResult:
    user_id: str
    migrated_run_ids: tuple[str, ...]
    already_migrated_run_ids: tuple[str, ...]
    selection_digest: str


@dataclass(frozen=True)
class _PreparedReportWrite:
    user_id: str
    report: EvaluationReport
    report_json: str
    artifact_digest: str
    manifest_json: str
    stored_seed_json: str
    stored_spec_json: str
    stored_report_json: str
    encrypted_storage: bool
    reference_salt: str
    encrypted_report_artifact: tuple[Any, bytes] | None
    profile_bundle: CortexHeldOutProfileBundle | None
    encrypted_profile_artifact: tuple[Any, bytes] | None


ARTIFACT_SCHEMA_VERSION = "pairwise-twin-artifact/v2"
COMPARISON_SCHEMA_VERSION = "pairwise-twin-comparison/v2"
ENCRYPTED_REPORT_REFERENCE_SCHEMA = "pairwise-twin-encrypted-report-ref/v1"
ENCRYPTED_CHILD_REFERENCE_SCHEMA = "pairwise-twin-encrypted-child-ref/v1"
ENCRYPTED_RANKING_SUMMARY_SCHEMA = "pairwise-twin-ranking-summary/v1"


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("persisted evaluation artifact contains a malformed object")
    return value


def _boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(
            f"persisted evaluation artifact field {field_name!r} must be boolean"
        )
    return value


def _optional_boolean(value: Any, field_name: str) -> bool | None:
    if value is None:
        return None
    return _boolean(value, field_name)


def _candidate(value: Any) -> Candidate:
    item = _mapping(value)
    return Candidate(
        candidate_id=str(item["candidate_id"]),
        system_id=str(item["system_id"]),
        text=str(item["text"]),
        prompt_id=str(item["prompt_id"]),
        seed=int(item["seed"]),
        metadata=_mapping(item.get("metadata", {})),
    )


def _comparison_payload(record: ComparisonRecord) -> dict[str, Any]:
    """Store candidate references once instead of embedding both answer texts."""
    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "plan": record.plan,
        "left_candidate_id": record.left.candidate_id,
        "right_candidate_id": record.right.candidate_id,
        "decision": record.decision,
        "judge_seed": record.judge_seed,
    }


def _report_payload(report: EvaluationReport) -> dict[str, Any]:
    """Canonical compact artifact; candidates appear once per report."""
    candidates = _unique_candidates(report)
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_id": report.run_id,
        "seed": report.seed,
        "profile_fingerprint": report.profile_fingerprint,
        "prompts": report.prompts,
        "systems": report.systems,
        "candidates": tuple(candidates[key] for key in sorted(candidates)),
        "comparisons": tuple(_comparison_payload(record) for record in report.comparisons),
        "resolved_comparisons": report.resolved_comparisons,
        "ranking": report.ranking,
        "metadata": report.metadata,
    }


def _report_from_json(payload: str) -> EvaluationReport:
    item = _mapping(json.loads(payload))
    prompts = tuple(
        EvaluationPrompt(
            prompt_id=str(prompt["prompt_id"]),
            text=str(prompt["text"]),
            metadata=_mapping(prompt.get("metadata", {})),
        )
        for prompt in item["prompts"]
    )
    compact = item.get("schema_version") == ARTIFACT_SCHEMA_VERSION
    candidates_by_id: dict[str, Candidate] = {}
    if compact:
        for raw_candidate in item.get("candidates", []):
            candidate = _candidate(raw_candidate)
            if candidate.candidate_id in candidates_by_id:
                raise ValueError("persisted evaluation artifact has duplicate candidate IDs")
            candidates_by_id[candidate.candidate_id] = candidate

    comparisons: list[ComparisonRecord] = []
    for raw in item["comparisons"]:
        comparison = _mapping(raw)
        plan = _mapping(comparison["plan"])
        decision = _mapping(comparison["decision"])
        if compact:
            if comparison.get("schema_version") != COMPARISON_SCHEMA_VERSION:
                raise ValueError("persisted comparison has an unsupported schema version")
            left_id = str(comparison["left_candidate_id"])
            right_id = str(comparison["right_candidate_id"])
            try:
                left = candidates_by_id[left_id]
                right = candidates_by_id[right_id]
            except KeyError as exc:
                raise ValueError(
                    "persisted comparison references an unknown candidate"
                ) from exc
        else:
            # Backward-compatible reader for v1 expanded artifacts.
            left = _candidate(comparison["left"])
            right = _candidate(comparison["right"])
        comparisons.append(
            ComparisonRecord(
                plan=ComparisonPlan(
                    comparison_id=str(plan["comparison_id"]),
                    logical_comparison_id=str(plan["logical_comparison_id"]),
                    prompt_id=str(plan["prompt_id"]),
                    left_system_id=str(plan["left_system_id"]),
                    right_system_id=str(plan["right_system_id"]),
                    repetition=int(plan.get("repetition", 0)),
                    swapped=_boolean(plan.get("swapped", False), "plan.swapped"),
                ),
                left=left,
                right=right,
                decision=JudgeDecision(
                    outcome=ComparisonOutcome.normalize(decision["outcome"]),
                    rationale=str(decision.get("rationale", "")),
                    cited_memory_ids=tuple(str(value) for value in decision.get("cited_memory_ids", [])),
                    confidence=decision.get("confidence"),
                    metadata=_mapping(decision.get("metadata", {})),
                ),
                judge_seed=int(comparison["judge_seed"]),
            )
        )
        if (
            left.system_id != comparisons[-1].plan.left_system_id
            or right.system_id != comparisons[-1].plan.right_system_id
            or left.prompt_id != comparisons[-1].plan.prompt_id
            or right.prompt_id != comparisons[-1].plan.prompt_id
        ):
            raise ValueError("persisted comparison candidate identity mismatch")
    resolved = tuple(
        ResolvedComparison(
            logical_comparison_id=str(raw["logical_comparison_id"]),
            prompt_id=str(raw["prompt_id"]),
            repetition=int(raw["repetition"]),
            system_a_id=str(raw["system_a_id"]),
            system_b_id=str(raw["system_b_id"]),
            outcome=ComparisonOutcome.normalize(raw["outcome"]),
            source_comparison_ids=tuple(str(value) for value in raw["source_comparison_ids"]),
            swap_consistent=_optional_boolean(
                raw.get("swap_consistent"), "resolved.swap_consistent"
            ),
        )
        for raw in item["resolved_comparisons"]
    )
    ranking = _mapping(item["ranking"])
    diagnostics = _mapping(ranking["diagnostics"])
    ranking_result = RankingResult(
        ratings=tuple(
            SystemRating(
                system_id=str(raw["system_id"]),
                score=float(raw["score"]),
                rank=int(raw["rank"]) if raw.get("rank") is not None else None,
                component_rank=int(raw["component_rank"]),
                comparisons=int(raw["comparisons"]),
                wins=float(raw["wins"]),
            )
            for raw in ranking["ratings"]
        ),
        diagnostics=RankingDiagnostics(
            connected=_boolean(diagnostics["connected"], "diagnostics.connected"),
            components=tuple(tuple(str(value) for value in group) for group in diagnostics["components"]),
            converged=_boolean(diagnostics["converged"], "diagnostics.converged"),
            iterations=int(diagnostics["iterations"]),
            max_delta=float(diagnostics["max_delta"]),
            log_likelihood=float(diagnostics["log_likelihood"]),
            ignored_both_bad=int(diagnostics.get("ignored_both_bad", 0)),
            ignored_abstain=int(diagnostics.get("ignored_abstain", 0)),
            ignored_invalid=int(diagnostics.get("ignored_invalid", 0)),
        ),
    )
    return EvaluationReport(
        run_id=str(item["run_id"]),
        seed=item["seed"],
        profile_fingerprint=str(item["profile_fingerprint"]),
        prompts=prompts,
        systems=tuple(str(value) for value in item["systems"]),
        comparisons=tuple(comparisons),
        resolved_comparisons=resolved,
        ranking=ranking_result,
        metadata=_mapping(item.get("metadata", {})),
    )


def _report_spec(report: EvaluationReport) -> dict[str, Any]:
    return {
        "run_id": report.run_id,
        "seed": report.seed,
        "profile_fingerprint": report.profile_fingerprint,
        "prompts": report.prompts,
        "systems": report.systems,
        "spec_id": report.metadata.get("spec_id"),
        "reproducibility_manifest": report.metadata.get(
            "reproducibility_manifest"
        ),
    }


def _unique_candidates(report: EvaluationReport) -> dict[str, Candidate]:
    unique: dict[str, Candidate] = {}
    for record in report.comparisons:
        for candidate in (record.left, record.right):
            existing = unique.get(candidate.candidate_id)
            if existing is not None and existing != candidate:
                raise ValueError(
                    f"candidate_id {candidate.candidate_id!r} aliases different candidates"
                )
            unique[candidate.candidate_id] = candidate
    return unique


def _report_manifest(report: EvaluationReport, report_json: str) -> dict[str, Any]:
    manifest = {
        "artifact_digest": report.artifact_digest,
        "candidate_count": len(_unique_candidates(report)),
        "comparison_count": len(report.comparisons),
        "resolved_comparison_count": len(report.resolved_comparisons),
        "rating_count": len(report.ranking.ratings),
        "report_digest": canonical_hash(json.loads(report_json)),
    }
    if json.loads(report_json).get("schema_version") == ARTIFACT_SCHEMA_VERSION:
        manifest["storage_schema_version"] = ARTIFACT_SCHEMA_VERSION
    return manifest


def _encrypted_reference(schema_version: str) -> str:
    return canonical_json(
        {
            "schema_version": schema_version,
            "encrypted": True,
        }
    )


def _is_encrypted_reference(value: Any, schema_version: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        decoded = json.loads(value)
    except ValueError:
        return False
    return decoded == {
        "schema_version": schema_version,
        "encrypted": True,
    }


def _opaque_reference(reference_salt: str, kind: str, value: str) -> str:
    try:
        key = bytes.fromhex(reference_salt)
    except ValueError as exc:
        raise ReportArtifactError("report reference salt is malformed") from exc
    if len(key) != 32:
        raise ReportArtifactError("report reference salt is malformed")
    digest = hmac.new(
        key,
        f"{kind}\0{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"pairwise_outer_ref_{digest}"


def _ranking_summary(
    ranking: RankingResult,
    reference_salt: str,
) -> dict[str, Any]:
    diagnostics = ranking.diagnostics
    return {
        "schema_version": ENCRYPTED_RANKING_SUMMARY_SCHEMA,
        "encrypted": True,
        "ratings": tuple(
            {
                "system_ref": _opaque_reference(
                    reference_salt,
                    "system",
                    rating.system_id,
                ),
                "score": rating.score,
                "rank": rating.rank,
                "component_rank": rating.component_rank,
                "comparisons": rating.comparisons,
                "wins": rating.wins,
            }
            for rating in ranking.ratings
        ),
        "diagnostics": {
            "connected": diagnostics.connected,
            "component_sizes": tuple(
                len(component) for component in diagnostics.components
            ),
            "converged": diagnostics.converged,
            "iterations": diagnostics.iterations,
            "max_delta": diagnostics.max_delta,
            "log_likelihood": diagnostics.log_likelihood,
            "ignored_both_bad": diagnostics.ignored_both_bad,
            "ignored_abstain": diagnostics.ignored_abstain,
            "ignored_invalid": diagnostics.ignored_invalid,
        },
    }


def _insert_normalized_rows(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    report: EvaluationReport,
    encrypted: bool,
    reference_salt: str = "",
) -> None:
    child_marker = _encrypted_reference(
        ENCRYPTED_CHILD_REFERENCE_SCHEMA
    )

    unique_candidates = _unique_candidates(report)
    ref = (
        lambda kind, value: _opaque_reference(
            reference_salt,
            kind,
            value,
        )
        if encrypted
        else str(value)
    )
    for candidate_id in sorted(unique_candidates):
        candidate = unique_candidates[candidate_id]
        conn.execute(
            """
            INSERT INTO twin_eval_candidates
            (user_id, run_id, candidate_id, prompt_id, system_id,
             candidate_json, candidate_digest)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                report.run_id,
                ref("candidate", candidate.candidate_id),
                ref("prompt", candidate.prompt_id),
                ref("system", candidate.system_id),
                (
                    child_marker
                    if encrypted
                    else canonical_json(candidate)
                ),
                canonical_hash(candidate),
            ),
        )
    for record in report.comparisons:
        compact_record = _comparison_payload(record)
        conn.execute(
            """
            INSERT INTO twin_eval_comparisons
            (user_id, run_id, comparison_id, logical_comparison_id,
             left_candidate_id, right_candidate_id, comparison_json,
             comparison_digest)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                report.run_id,
                ref("comparison", record.plan.comparison_id),
                ref(
                    "logical_comparison",
                    record.plan.logical_comparison_id,
                ),
                ref("candidate", record.left.candidate_id),
                ref("candidate", record.right.candidate_id),
                (
                    child_marker
                    if encrypted
                    else canonical_json(compact_record)
                ),
                canonical_hash(compact_record),
            ),
        )
    for resolved in report.resolved_comparisons:
        conn.execute(
            """
            INSERT INTO twin_eval_resolved_comparisons
            (user_id, run_id, logical_comparison_id, resolved_json,
             resolved_digest)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                report.run_id,
                ref(
                    "logical_comparison",
                    resolved.logical_comparison_id,
                ),
                (
                    child_marker
                    if encrypted
                    else canonical_json(resolved)
                ),
                canonical_hash(resolved),
            ),
        )
    for rating in report.ranking.ratings:
        conn.execute(
            """
            INSERT INTO twin_eval_rankings
            (user_id, run_id, system_id, rating_json, rating_digest)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                report.run_id,
                ref("system", rating.system_id),
                (
                    child_marker
                    if encrypted
                    else canonical_json(rating)
                ),
                canonical_hash(rating),
            ),
        )
    conn.execute(
        """
        INSERT INTO twin_eval_ranking_manifests
        (user_id, run_id, ranking_json, ranking_digest)
        VALUES (?, ?, ?, ?)
        """,
        (
            user_id,
            report.run_id,
            (
                canonical_json(
                    _ranking_summary(report.ranking, reference_salt)
                )
                if encrypted
                else canonical_json(report.ranking)
            ),
            canonical_hash(report.ranking),
        ),
    )


def _delete_normalized_rows(
    conn: sqlite3.Connection,
    user_id: str,
    run_id: str,
) -> None:
    for table in (
        "twin_eval_ranking_manifests",
        "twin_eval_rankings",
        "twin_eval_resolved_comparisons",
        "twin_eval_comparisons",
        "twin_eval_candidates",
    ):
        conn.execute(
            f"DELETE FROM {table} WHERE user_id = ? AND run_id = ?",
            (user_id, run_id),
        )


class TwinEvalRepository:
    """Immutable SQLite persistence for exact offline evaluation replay."""

    def __init__(
        self,
        db_path: Path,
        *,
        artifact_cipher: Any | None = None,
        evidence_cipher: Any | None = None,
        allow_plaintext_reports: bool = False,
    ) -> None:
        self.db_path = Path(db_path)
        if (
            artifact_cipher is not None
            and evidence_cipher is not None
            and artifact_cipher is not evidence_cipher
        ):
            raise ValueError(
                "artifact_cipher and evidence_cipher must reference the same cipher"
            )
        self.evidence_cipher = (
            artifact_cipher if artifact_cipher is not None else evidence_cipher
        )
        if not isinstance(allow_plaintext_reports, bool):
            raise ValueError("allow_plaintext_reports must be boolean")
        self.allow_plaintext_reports = allow_plaintext_reports

    def _connect(
        self,
        *,
        maintenance_bypass: bool = False,
    ) -> sqlite3.Connection:
        if maintenance_bypass:
            require_exclusive_database_maintenance(self.db_path)
            conn = sqlite3.connect(self.db_path)
        else:
            conn = maintenance_locked_connect(
                self.db_path,
                lambda: sqlite3.connect(self.db_path),
            )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @staticmethod
    def _user_id(user_id: str) -> str:
        normalized = str(user_id).strip()
        if not normalized:
            raise ValueError("user_id is required for evaluation persistence")
        return normalized

    @staticmethod
    def _delete_report_rows(
        conn: sqlite3.Connection,
        user_id: str,
        run_id: str,
    ) -> None:
        for table in (
            "twin_eval_profile_artifacts",
            "twin_eval_report_artifacts",
            "twin_eval_ranking_manifests",
            "twin_eval_rankings",
            "twin_eval_resolved_comparisons",
            "twin_eval_comparisons",
            "twin_eval_candidates",
            "twin_eval_runs",
        ):
            conn.execute(
                f"DELETE FROM {table} WHERE user_id = ? AND run_id = ?",
                (user_id, run_id),
            )

    @staticmethod
    def _utc_timestamp(value: str, field_name: str) -> str:
        raw = str(value or "").strip()
        try:
            parsed = datetime.fromisoformat(
                raw[:-1] + "+00:00" if raw.endswith("Z") else raw
            )
        except ValueError as exc:
            raise ValueError(
                f"{field_name} must be a timezone-aware timestamp"
            ) from exc
        if parsed.tzinfo is None:
            raise ValueError(
                f"{field_name} must be a timezone-aware timestamp"
            )
        return (
            parsed.astimezone(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _now_utc() -> str:
        return (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

    def _require_evidence_cipher(self) -> Any:
        cipher = self.evidence_cipher
        if cipher is None or not bool(getattr(cipher, "available", False)):
            raise ProfileArtifactEncryptionUnavailable(
                "encrypted profile artifact storage is unavailable"
            )
        for name in ("encrypt_blob", "decrypt_blob", "is_encrypted"):
            if not callable(getattr(cipher, name, None)):
                raise ProfileArtifactEncryptionUnavailable(
                    "encrypted profile artifact storage is unavailable"
                )
        return cipher

    def _cipher_available(self) -> bool:
        cipher = self.evidence_cipher
        return cipher is not None and bool(getattr(cipher, "available", False))

    def _require_report_cipher(self) -> Any:
        cipher = self.evidence_cipher
        if cipher is None or not bool(getattr(cipher, "available", False)):
            raise ReportArtifactEncryptionUnavailable(
                "encrypted report artifact storage is unavailable"
            )
        for name in ("encrypt_blob", "decrypt_blob", "is_encrypted"):
            if not callable(getattr(cipher, name, None)):
                raise ReportArtifactEncryptionUnavailable(
                    "encrypted report artifact storage is unavailable"
                )
        return cipher

    def _load_encrypted_report_json(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        run_id: str,
        artifact_digest: str,
    ) -> ParsedReportArtifact:
        row = conn.execute(
            """
            SELECT *
            FROM twin_eval_report_artifacts
            WHERE user_id = ? AND run_id = ?
            """,
            (user_id, run_id),
        ).fetchone()
        if row is None:
            raise ReportArtifactError(
                "encrypted report reference is missing its artifact"
            )
        if row["artifact_schema_version"] != REPORT_ARTIFACT_SCHEMA_VERSION:
            raise ReportArtifactError(
                "report artifact schema is unsupported"
            )
        if row["artifact_digest"] != artifact_digest:
            raise ReportArtifactError(
                "report artifact run link verification failed"
            )
        cipher = self._require_report_cipher()
        ciphertext = bytes(row["artifact_ciphertext"])
        if not cipher.is_encrypted(ciphertext):
            raise ReportArtifactError(
                "report artifact is not CXE1 encrypted"
            )
        plaintext = cipher.decrypt_blob(
            user_id,
            REPORT_ARTIFACT_ENCRYPTION_PURPOSE,
            ciphertext,
        )
        return parse_report_artifact(
            plaintext,
            expected_user_id=user_id,
            expected_run_id=run_id,
            expected_artifact_id=str(row["artifact_id"]),
            expected_artifact_digest=artifact_digest,
            expected_report_digest=str(row["report_digest"]),
            expected_created_at=str(row["created_at"]),
        )

    @staticmethod
    def _report_has_profile_manifest(report: EvaluationReport) -> bool:
        reproducibility = report.metadata.get("reproducibility_manifest")
        return isinstance(reproducibility, Mapping) and (
            reproducibility.get("profile_manifest") is not None
        )

    def _prepare_report_write(
        self,
        user_id: str,
        report: EvaluationReport,
        *,
        profile_bundle: CortexHeldOutProfileBundle | None = None,
        evidence_expires_at: str | None = None,
    ) -> _PreparedReportWrite:
        user_id = self._user_id(user_id)
        report_json = canonical_json(_report_payload(report))
        artifact_digest = canonical_hash(report, prefix="artifact_")
        if artifact_digest != report.artifact_digest:
            raise ValueError("evaluation report artifact digest is not canonical")
        spec = _report_spec(report)
        manifest = _report_manifest(report, report_json)
        encrypted_profile_artifact: tuple[Any, bytes] | None = None
        encrypted_report_artifact: tuple[Any, bytes] | None = None
        profile_envelope = None
        report_envelope = None
        if profile_bundle is None:
            if self._report_has_profile_manifest(report):
                raise ProfileArtifactEncryptionUnavailable(
                    "Cortex profile reports require an encrypted evidence artifact"
                )
            if evidence_expires_at is not None:
                raise ValueError(
                    "evidence_expires_at requires profile_bundle"
                )
        elif evidence_expires_at is None:
            raise ValueError(
                "evidence_expires_at is required for profile_bundle"
            )
        if profile_bundle is not None and not self._cipher_available():
            raise ProfileArtifactEncryptionUnavailable(
                "encrypted profile artifact storage is unavailable"
            )
        cipher = None
        if self._cipher_available():
            cipher = self._require_report_cipher()
            report_envelope = build_report_artifact_envelope(
                user_id=user_id,
                run_id=report.run_id,
                artifact_id=secrets.token_hex(16),
                artifact_digest=artifact_digest,
                report_json=report_json,
                created_at=self._now_utc(),
            )
        elif not self.allow_plaintext_reports:
            raise ReportArtifactEncryptionUnavailable(
                "new evaluation reports require an available artifact cipher; "
                "plaintext storage is restricted to explicit local/test mode"
            )
        if profile_bundle is not None:
            created_at = self._now_utc()
            expires_at = self._utc_timestamp(
                evidence_expires_at,
                "evidence_expires_at",
            )
            if expires_at <= created_at:
                raise ValueError(
                    "evidence_expires_at must be later than creation time"
                )
            profile_envelope = build_profile_artifact_envelope(
                user_id=user_id,
                report=report,
                bundle=profile_bundle,
                artifact_id=secrets.token_hex(16),
                created_at=created_at,
                expires_at=expires_at,
            )
        if report_envelope is not None:
            report_ciphertext = cipher.encrypt_blob(
                user_id,
                REPORT_ARTIFACT_ENCRYPTION_PURPOSE,
                report_envelope.plaintext,
            )
            if not isinstance(report_ciphertext, (bytes, bytearray)) or not (
                cipher.is_encrypted(report_ciphertext)
            ):
                raise ReportArtifactEncryptionUnavailable(
                    "report artifact cipher did not produce CXE1 ciphertext"
                )
            encrypted_report_artifact = (
                report_envelope,
                bytes(report_ciphertext),
            )
        if profile_envelope is not None:
            cipher = self._require_evidence_cipher()
            ciphertext = cipher.encrypt_blob(
                user_id,
                PROFILE_ARTIFACT_ENCRYPTION_PURPOSE,
                profile_envelope.plaintext,
            )
            if not isinstance(ciphertext, (bytes, bytearray)) or not (
                cipher.is_encrypted(ciphertext)
            ):
                raise ProfileArtifactEncryptionUnavailable(
                    "profile artifact cipher did not produce CXE1 ciphertext"
                )
            encrypted_profile_artifact = (
                profile_envelope,
                bytes(ciphertext),
            )

        encrypted_storage = encrypted_report_artifact is not None
        reference_salt = (
            report_envelope.reference_salt
            if report_envelope is not None
            else ""
        )
        stored_spec_json = (
            _encrypted_reference(ENCRYPTED_REPORT_REFERENCE_SCHEMA)
            if encrypted_storage
            else canonical_json(spec)
        )
        stored_report_json = (
            _encrypted_reference(ENCRYPTED_REPORT_REFERENCE_SCHEMA)
            if encrypted_storage
            else report_json
        )
        stored_seed_json = (
            _encrypted_reference(ENCRYPTED_REPORT_REFERENCE_SCHEMA)
            if encrypted_storage
            else canonical_json(report.seed)
        )

        return _PreparedReportWrite(
            user_id=user_id,
            report=report,
            report_json=report_json,
            artifact_digest=artifact_digest,
            manifest_json=canonical_json(manifest),
            stored_seed_json=stored_seed_json,
            stored_spec_json=stored_spec_json,
            stored_report_json=stored_report_json,
            encrypted_storage=encrypted_storage,
            reference_salt=reference_salt,
            encrypted_report_artifact=encrypted_report_artifact,
            profile_bundle=profile_bundle,
            encrypted_profile_artifact=encrypted_profile_artifact,
        )

    def _save_report_tx(
        self,
        conn: sqlite3.Connection,
        prepared: _PreparedReportWrite,
        *,
        require_new: bool = False,
    ) -> str:
        if not conn.in_transaction:
            raise ValueError(
                "prepared evaluation reports require an active transaction"
            )
        user_id = prepared.user_id
        report = prepared.report
        report_json = prepared.report_json
        artifact_digest = prepared.artifact_digest
        stored_seed_json = prepared.stored_seed_json
        stored_spec_json = prepared.stored_spec_json
        stored_report_json = prepared.stored_report_json
        encrypted_storage = prepared.encrypted_storage
        reference_salt = prepared.reference_salt
        encrypted_report_artifact = (
            prepared.encrypted_report_artifact
        )
        encrypted_profile_artifact = (
            prepared.encrypted_profile_artifact
        )
        profile_bundle = prepared.profile_bundle
        profile_envelope = (
            encrypted_profile_artifact[0]
            if encrypted_profile_artifact is not None
            else None
        )
        try:
            existing = conn.execute(
                "SELECT artifact_digest, report_json FROM twin_eval_runs WHERE user_id = ? AND run_id = ?",
                (user_id, report.run_id),
            ).fetchone()
            if existing is not None:
                existing_report_json = existing["report_json"]
                if _is_encrypted_reference(
                    existing_report_json,
                    ENCRYPTED_REPORT_REFERENCE_SCHEMA,
                ):
                    existing_report_json = self._load_encrypted_report_json(
                        conn,
                        user_id=user_id,
                        run_id=report.run_id,
                        artifact_digest=str(existing["artifact_digest"]),
                    ).report_json
                elif encrypted_storage:
                    raise EvaluationArtifactCollision(
                        "existing run is plaintext and requires explicit migration"
                    )
                if existing["artifact_digest"] == artifact_digest and existing_report_json == report_json:
                    if require_new:
                        raise EvaluationArtifactCollision(
                            "atomic completion requires a new report run"
                        )
                    if encrypted_profile_artifact is not None:
                        stored = conn.execute(
                            """
                            SELECT *
                            FROM twin_eval_profile_artifacts
                            WHERE user_id = ? AND run_id = ?
                            """,
                            (user_id, report.run_id),
                        ).fetchone()
                        if stored is None:
                            raise EvaluationArtifactCollision(
                                "existing run is missing its encrypted profile artifact"
                            )
                        cipher = self._require_evidence_cipher()
                        ciphertext = bytes(stored["artifact_ciphertext"])
                        if not cipher.is_encrypted(ciphertext):
                            raise ProfileArtifactError(
                                "profile artifact is not CXE1 encrypted"
                            )
                        plaintext = cipher.decrypt_blob(
                            user_id,
                            PROFILE_ARTIFACT_ENCRYPTION_PURPOSE,
                            ciphertext,
                        )
                        stored_bundle = parse_profile_artifact(
                            plaintext,
                            expected_user_id=user_id,
                            expected_run_id=report.run_id,
                            expected_artifact_id=str(stored["artifact_id"]),
                            expected_profile_fingerprint=str(
                                stored["profile_fingerprint"]
                            ),
                            expected_scope_digest=str(stored["scope_digest"]),
                            expected_artifact_digest=str(
                                stored["artifact_digest"]
                            ),
                            expected_created_at=str(stored["created_at"]),
                            expected_expires_at=str(stored["expires_at"]),
                        )
                        if (
                            stored_bundle != profile_bundle
                            or str(stored["expires_at"])
                            != profile_envelope.expires_at
                        ):
                            raise EvaluationArtifactCollision(
                                "existing run has different encrypted profile evidence"
                            )
                    return artifact_digest
                raise EvaluationArtifactCollision(
                    f"run_id {report.run_id!r} already identifies a different artifact"
                )
            digest_owner = conn.execute(
                "SELECT run_id FROM twin_eval_runs WHERE user_id = ? AND artifact_digest = ?",
                (user_id, artifact_digest),
            ).fetchone()
            if digest_owner is not None:
                raise EvaluationArtifactCollision(
                    f"artifact digest already belongs to run_id {digest_owner['run_id']!r}"
                )

            conn.execute(
                """
                INSERT INTO twin_eval_runs
                (user_id, run_id, artifact_digest, seed_json, profile_fingerprint,
                 spec_json, manifest_json, report_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    report.run_id,
                    artifact_digest,
                    stored_seed_json,
                    report.profile_fingerprint,
                    stored_spec_json,
                    prepared.manifest_json,
                    stored_report_json,
                ),
            )
            if encrypted_report_artifact is not None:
                report_envelope, report_ciphertext = (
                    encrypted_report_artifact
                )
                conn.execute(
                    """
                    INSERT INTO twin_eval_report_artifacts
                    (user_id, run_id, artifact_id, artifact_schema_version,
                     artifact_digest, report_digest, artifact_ciphertext,
                     created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        report.run_id,
                        report_envelope.artifact_id,
                        REPORT_ARTIFACT_SCHEMA_VERSION,
                        artifact_digest,
                        report_envelope.report_digest,
                        report_ciphertext,
                        report_envelope.created_at,
                    ),
                )
            if encrypted_profile_artifact is not None:
                envelope, ciphertext = encrypted_profile_artifact
                conn.execute(
                    """
                    INSERT INTO twin_eval_profile_artifacts
                    (user_id, run_id, artifact_id, artifact_schema_version,
                     profile_fingerprint, scope_digest, artifact_digest,
                     artifact_ciphertext, expires_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        report.run_id,
                        envelope.artifact_id,
                        PROFILE_ARTIFACT_SCHEMA_VERSION,
                        report.profile_fingerprint,
                        envelope.scope_digest,
                        envelope.artifact_digest,
                        ciphertext,
                        envelope.expires_at,
                        envelope.created_at,
                    ),
                )
            _insert_normalized_rows(
                conn,
                user_id=user_id,
                report=report,
                encrypted=encrypted_storage,
                reference_salt=reference_salt,
            )
            return artifact_digest
        except Exception:
            raise

    def save_report(
        self,
        user_id: str,
        report: EvaluationReport,
        *,
        profile_bundle: CortexHeldOutProfileBundle | None = None,
        evidence_expires_at: str | None = None,
    ) -> str:
        prepared = self._prepare_report_write(
            user_id,
            report,
            profile_bundle=profile_bundle,
            evidence_expires_at=evidence_expires_at,
        )
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            artifact_digest = self._save_report_tx(conn, prepared)
            conn.commit()
            return artifact_digest
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def load_report(self, user_id: str, run_id: str) -> EvaluationReport:
        user_id = self._user_id(user_id)
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT artifact_digest, report_json FROM twin_eval_runs
                WHERE user_id = ? AND run_id = ?
                """,
                (user_id, str(run_id).strip()),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown twin evaluation run_id: {run_id}")
            report_json = row["report_json"]
            if _is_encrypted_reference(
                report_json,
                ENCRYPTED_REPORT_REFERENCE_SCHEMA,
            ):
                report_json = self._load_encrypted_report_json(
                    conn,
                    user_id=user_id,
                    run_id=str(run_id).strip(),
                    artifact_digest=str(row["artifact_digest"]),
                ).report_json
            elif not self.allow_plaintext_reports:
                raise ReportArtifactEncryptionUnavailable(
                    "legacy plaintext report reads require explicit "
                    "local/test mode or a verified migration"
                )
        finally:
            conn.close()
        report = _report_from_json(report_json)
        if report.run_id != run_id:
            raise ValueError("persisted run ID does not match replay artifact")
        if report.artifact_digest != row["artifact_digest"]:
            raise ValueError("persisted evaluation artifact digest verification failed")
        return report

    def preview_legacy_report_migration(
        self,
        user_id: str,
        *,
        limit: int = 100,
    ) -> LegacyMigrationPreview:
        """Classify storage and select the next bounded plaintext batch."""

        user_id = self._user_id(user_id)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1_000
        ):
            raise ValueError(
                "migration preview limit must be between 1 and 1000"
            )
        marker = _encrypted_reference(
            ENCRYPTED_REPORT_REFERENCE_SCHEMA
        )
        conn = self._connect()
        try:
            counts = conn.execute(
                """
                SELECT
                  COALESCE(SUM(
                    CASE WHEN r.report_json IS ?
                              AND a.run_id IS NOT NULL
                         THEN 1 ELSE 0 END
                  ), 0) AS encrypted_count,
                  COALESCE(SUM(
                    CASE WHEN r.report_json IS NOT ?
                              AND a.run_id IS NULL
                         THEN 1 ELSE 0 END
                  ), 0) AS legacy_count,
                  COALESCE(SUM(
                    CASE WHEN NOT (
                                  r.report_json IS ?
                                  AND a.run_id IS NOT NULL
                                )
                              AND NOT (
                                  r.report_json IS NOT ?
                                  AND a.run_id IS NULL
                                )
                         THEN 1 ELSE 0 END
                  ), 0) AS inconsistent_count
                FROM twin_eval_runs AS r
                LEFT JOIN twin_eval_report_artifacts AS a
                  ON a.user_id = r.user_id AND a.run_id = r.run_id
                WHERE r.user_id = ?
                """,
                (marker, marker, marker, marker, user_id),
            ).fetchone()
            legacy = tuple(
                str(row["run_id"])
                for row in conn.execute(
                    """
                    SELECT r.run_id
                    FROM twin_eval_runs AS r
                    LEFT JOIN twin_eval_report_artifacts AS a
                      ON a.user_id = r.user_id
                     AND a.run_id = r.run_id
                    WHERE r.user_id = ?
                      AND r.report_json IS NOT ?
                      AND a.run_id IS NULL
                    ORDER BY r.created_at, r.run_id
                    LIMIT ?
                    """,
                    (user_id, marker, limit),
                )
            )
        finally:
            conn.close()
        run_ids = legacy
        selection_digest = canonical_hash(
            {"user_id": user_id, "run_ids": run_ids},
            prefix="pairwise_legacy_selection_",
        )
        return LegacyMigrationPreview(
            user_id=user_id,
            run_ids=run_ids,
            selection_digest=selection_digest,
            legacy_count=int(counts["legacy_count"]),
            encrypted_count=int(counts["encrypted_count"]),
            inconsistent_count=int(counts["inconsistent_count"]),
        )

    def migrate_legacy_reports(
        self,
        user_id: str,
        *,
        expected_run_ids: tuple[str, ...],
        expected_selection_digest: str,
    ) -> LegacyMigrationResult:
        """Atomically encrypt a preview-locked batch, one run at a time."""

        user_id = self._user_id(user_id)
        run_ids = tuple(str(value).strip() for value in expected_run_ids)
        if (
            not run_ids
            or len(run_ids) > 1_000
            or any(not value for value in run_ids)
            or len(set(run_ids)) != len(run_ids)
        ):
            raise ValueError(
                "expected_run_ids must contain 1..1000 unique run IDs"
            )
        canonical_selection_digest = canonical_hash(
            {"user_id": user_id, "run_ids": run_ids},
            prefix="pairwise_legacy_selection_",
        )
        if expected_selection_digest != canonical_selection_digest:
            raise EvaluationArtifactCollision(
                "legacy migration selection digest is invalid"
            )
        conn = self._connect()
        try:
            rows = conn.execute(
                f"""
                SELECT r.run_id, r.report_json,
                       a.run_id AS artifact_run_id
                FROM twin_eval_runs AS r
                LEFT JOIN twin_eval_report_artifacts AS a
                  ON a.user_id = r.user_id
                 AND a.run_id = r.run_id
                WHERE r.user_id = ?
                  AND r.run_id IN ({",".join("?" for _ in run_ids)})
                """,
                (user_id, *run_ids),
            ).fetchall()
        finally:
            conn.close()
        by_run_id = {str(row["run_id"]): row for row in rows}
        pending: list[str] = []
        already_migrated: list[str] = []
        for run_id in run_ids:
            row = by_run_id.get(run_id)
            if row is None:
                raise EvaluationArtifactCollision(
                    "legacy migration target is missing"
                )
            marker = _is_encrypted_reference(
                row["report_json"],
                ENCRYPTED_REPORT_REFERENCE_SCHEMA,
            )
            has_artifact = row["artifact_run_id"] is not None
            if marker and has_artifact:
                self.replay_bundle(user_id, run_id)
                already_migrated.append(run_id)
            elif not marker and not has_artifact:
                pending.append(run_id)
            else:
                raise EvaluationArtifactCollision(
                    "legacy migration target is inconsistent"
                )
        cipher = self._require_report_cipher()
        migrated: list[str] = []
        for run_id in pending:
            self._migrate_one_legacy_report(
                user_id,
                run_id,
                cipher=cipher,
            )
            migrated.append(run_id)
        return LegacyMigrationResult(
            user_id=user_id,
            migrated_run_ids=tuple(migrated),
            already_migrated_run_ids=tuple(already_migrated),
            selection_digest=expected_selection_digest,
        )

    def _migrate_one_legacy_report(
        self,
        user_id: str,
        run_id: str,
        *,
        cipher: Any,
    ) -> None:
        legacy_reader = TwinEvalRepository(
            self.db_path,
            artifact_cipher=cipher,
            allow_plaintext_reports=True,
        )
        conn = self._connect()
        try:
            conn.execute("PRAGMA secure_delete=ON")
            if conn.execute("PRAGMA secure_delete").fetchone()[0] != 1:
                raise ReportArtifactError(
                    "SQLite secure_delete could not be enabled"
                )
            conn.execute("BEGIN IMMEDIATE")
            legacy_bundle = legacy_reader.replay_bundle(
                user_id,
                run_id,
                _conn=conn,
            )
            report_json = canonical_json(legacy_bundle["report"])
            report = _report_from_json(report_json)
            source = conn.execute(
                """
                SELECT created_at, report_json
                FROM twin_eval_runs
                WHERE user_id = ? AND run_id = ?
                """,
                (user_id, run_id),
            ).fetchone()
            if source is None:
                raise KeyError(
                    f"unknown twin evaluation run_id: {run_id}"
                )
            if _is_encrypted_reference(
                source["report_json"],
                ENCRYPTED_REPORT_REFERENCE_SCHEMA,
            ):
                raise EvaluationArtifactCollision(
                    "legacy report was already migrated"
                )
            created_at = str(source["created_at"])
            envelope = build_report_artifact_envelope(
                user_id=user_id,
                run_id=run_id,
                artifact_id=secrets.token_hex(16),
                artifact_digest=report.artifact_digest,
                report_json=report_json,
                created_at=created_at,
            )
            ciphertext = cipher.encrypt_blob(
                user_id,
                REPORT_ARTIFACT_ENCRYPTION_PURPOSE,
                envelope.plaintext,
            )
            if not isinstance(
                ciphertext,
                (bytes, bytearray),
            ) or not cipher.is_encrypted(ciphertext):
                raise ReportArtifactEncryptionUnavailable(
                    "report migration did not produce CXE1 ciphertext"
                )
            verified = parse_report_artifact(
                cipher.decrypt_blob(
                    user_id,
                    REPORT_ARTIFACT_ENCRYPTION_PURPOSE,
                    bytes(ciphertext),
                ),
                expected_user_id=user_id,
                expected_run_id=run_id,
                expected_artifact_id=envelope.artifact_id,
                expected_artifact_digest=report.artifact_digest,
                expected_report_digest=envelope.report_digest,
                expected_created_at=created_at,
            )
            if verified.report_json != report_json:
                raise ReportArtifactError(
                    "report migration verification changed the report"
                )
            conn.execute(
                """
                INSERT INTO twin_eval_report_artifacts
                (user_id, run_id, artifact_id, artifact_schema_version,
                 artifact_digest, report_digest, artifact_ciphertext,
                 created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    run_id,
                    envelope.artifact_id,
                    REPORT_ARTIFACT_SCHEMA_VERSION,
                    report.artifact_digest,
                    envelope.report_digest,
                    bytes(ciphertext),
                    created_at,
                ),
            )
            marker = _encrypted_reference(
                ENCRYPTED_REPORT_REFERENCE_SCHEMA
            )
            conn.execute(
                """
                UPDATE twin_eval_runs
                SET seed_json = ?, spec_json = ?, report_json = ?
                WHERE user_id = ? AND run_id = ?
                """,
                (marker, marker, marker, user_id, run_id),
            )
            _delete_normalized_rows(conn, user_id, run_id)
            _insert_normalized_rows(
                conn,
                user_id=user_id,
                report=report,
                encrypted=True,
                reference_salt=envelope.reference_salt,
            )
            self.replay_bundle(
                user_id,
                run_id,
                _conn=conn,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def audit_report_storage(
        self,
        user_id: str,
        *,
        require_clean: bool = False,
    ) -> dict[str, Any]:
        """Verify logical storage and optionally enforce the release gate."""

        user_id = self._user_id(user_id)
        marker = _encrypted_reference(
            ENCRYPTED_REPORT_REFERENCE_SCHEMA
        )
        malformed = 0
        verified = 0
        legacy = 0
        encrypted = 0
        inconsistent = 0
        permissive_reader = TwinEvalRepository(
            self.db_path,
            artifact_cipher=self.evidence_cipher,
            allow_plaintext_reports=True,
        )
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            rows = conn.execute(
                """
                SELECT r.run_id, r.report_json,
                       a.run_id AS artifact_run_id
                FROM twin_eval_runs AS r
                LEFT JOIN twin_eval_report_artifacts AS a
                  ON a.user_id = r.user_id AND a.run_id = r.run_id
                WHERE r.user_id = ?
                ORDER BY r.created_at, r.run_id
                """,
                (user_id,),
            )
            inventory = tuple(rows)
            for row in inventory:
                is_marker = row["report_json"] == marker
                has_artifact = row["artifact_run_id"] is not None
                if is_marker and has_artifact:
                    encrypted += 1
                elif not is_marker and not has_artifact:
                    legacy += 1
                else:
                    inconsistent += 1
                try:
                    permissive_reader.replay_bundle(
                        user_id,
                        str(row["run_id"]),
                        _conn=conn,
                    )
                    verified += 1
                except ReportArtifactEncryptionUnavailable:
                    raise
                except (
                    KeyError,
                    TypeError,
                    ValueError,
                    KeyringError,
                ):
                    malformed += 1
            conn.rollback()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        clean = (
            legacy == 0
            and inconsistent == 0
            and malformed == 0
        )
        result = {
            "user_id": user_id,
            "legacy_count": legacy,
            "encrypted_count": encrypted,
            "inconsistent_count": inconsistent,
            "verified_count": verified,
            "malformed_count": malformed,
            "clean": clean,
        }
        if require_clean and not clean:
            raise ReportArtifactError(
                "pairwise report storage is not migration-clean"
            )
        return result

    def finalize_legacy_report_migration(
        self,
        *,
        exclusive_maintenance: bool = False,
    ) -> dict[str, Any]:
        """Physically scrub deleted plaintext during an explicit outage."""

        if exclusive_maintenance is not True:
            raise ReportArtifactError(
                "physical cleanup requires explicit exclusive maintenance"
            )
        try:
            with exclusive_database_maintenance(self.db_path):
                conn = self._connect(maintenance_bypass=True)
                try:
                    report_keys = tuple(
                        (str(row["user_id"]), str(row["run_id"]))
                        for row in conn.execute(
                            """
                            SELECT user_id, run_id FROM twin_eval_runs
                            ORDER BY user_id, run_id
                            """
                        )
                    )
                    for user_id, run_id in report_keys:
                        self.replay_bundle(
                            user_id,
                            run_id,
                            _conn=conn,
                        )
                    rows = conn.execute(
                        """
                        SELECT r.report_json,
                               a.run_id AS artifact_run_id
                        FROM twin_eval_runs AS r
                        LEFT JOIN twin_eval_report_artifacts AS a
                          ON a.user_id = r.user_id
                         AND a.run_id = r.run_id
                        """
                    ).fetchall()
                    if any(
                        not _is_encrypted_reference(
                            row["report_json"],
                            ENCRYPTED_REPORT_REFERENCE_SCHEMA,
                        )
                        or row["artifact_run_id"] is None
                        for row in rows
                    ):
                        raise ReportArtifactError(
                            "physical cleanup requires zero "
                            "legacy/inconsistent runs"
                        )
                    conn.execute("PRAGMA secure_delete=ON")
                    if (
                        conn.execute(
                            "PRAGMA secure_delete"
                        ).fetchone()[0]
                        != 1
                    ):
                        raise ReportArtifactError(
                            "SQLite secure_delete could not be enabled"
                        )
                    first_checkpoint = tuple(
                        conn.execute(
                            "PRAGMA wal_checkpoint(TRUNCATE)"
                        ).fetchone()
                    )
                    if first_checkpoint[0] != 0 or (
                        first_checkpoint[1] >= 0
                        and first_checkpoint[1] != first_checkpoint[2]
                    ):
                        raise ReportArtifactError(
                            "pre-VACUUM WAL checkpoint is busy"
                        )
                    conn.execute("VACUUM")
                    second_checkpoint = tuple(
                        conn.execute(
                            "PRAGMA wal_checkpoint(TRUNCATE)"
                        ).fetchone()
                    )
                    if second_checkpoint[0] != 0 or (
                        second_checkpoint[1] >= 0
                        and second_checkpoint[1] != second_checkpoint[2]
                    ):
                        raise ReportArtifactError(
                            "post-VACUUM WAL checkpoint is busy"
                        )
                    integrity = tuple(
                        str(row[0])
                        for row in conn.execute(
                            "PRAGMA integrity_check"
                        )
                    )
                    foreign_keys = tuple(
                        tuple(row)
                        for row in conn.execute(
                            "PRAGMA foreign_key_check"
                        )
                    )
                    if integrity != ("ok",) or foreign_keys:
                        raise ReportArtifactError(
                            "post-migration SQLite integrity "
                            "verification failed"
                        )
                    for user_id, run_id in report_keys:
                        self.replay_bundle(
                            user_id,
                            run_id,
                            _conn=conn,
                        )
                    result = {
                        "sqlite_version": sqlite3.sqlite_version,
                        "exclusive_maintenance_acknowledged": True,
                        "exclusive_maintenance_fence_acquired": True,
                        "first_checkpoint": first_checkpoint,
                        "second_checkpoint": second_checkpoint,
                        "integrity_check": integrity,
                        "foreign_key_violations": foreign_keys,
                        "backup_remediation_required": True,
                        "verified_report_count": len(report_keys),
                    }
                finally:
                    conn.close()
        except DatabaseMaintenanceBusy as exc:
            raise ReportArtifactError(
                "physical cleanup requires all Cortex database "
                "connections and processes to stop"
            ) from exc
        return result

    def load_profile_artifact(
        self,
        user_id: str,
        run_id: str,
    ) -> CortexHeldOutProfileBundle:
        """Decrypt and verify one unexpired frozen evidence bundle."""

        user_id = self._user_id(user_id)
        normalized_run_id = str(run_id).strip()
        if not normalized_run_id:
            raise ValueError("run_id is required for profile artifact replay")
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT p.*, r.profile_fingerprint AS run_profile_fingerprint
                FROM twin_eval_profile_artifacts AS p
                JOIN twin_eval_runs AS r
                  ON r.user_id = p.user_id AND r.run_id = p.run_id
                WHERE p.user_id = ? AND p.run_id = ?
                """,
                (user_id, normalized_run_id),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise KeyError(
                f"unknown twin evaluation profile artifact: {run_id}"
            )
        if row["artifact_schema_version"] != (
            PROFILE_ARTIFACT_SCHEMA_VERSION
        ):
            raise ProfileArtifactError(
                "profile artifact schema is unsupported"
            )
        if row["profile_fingerprint"] != row["run_profile_fingerprint"]:
            raise ProfileArtifactError(
                "profile artifact report link verification failed"
            )
        if str(row["expires_at"]) <= self._now_utc():
            raise ProfileArtifactExpired("profile artifact has expired")
        cipher = self._require_evidence_cipher()
        ciphertext = bytes(row["artifact_ciphertext"])
        if not cipher.is_encrypted(ciphertext):
            raise ProfileArtifactError(
                "profile artifact is not CXE1 encrypted"
            )
        plaintext = cipher.decrypt_blob(
            user_id,
            PROFILE_ARTIFACT_ENCRYPTION_PURPOSE,
            ciphertext,
        )
        return parse_profile_artifact(
            plaintext,
            expected_user_id=user_id,
            expected_run_id=normalized_run_id,
            expected_artifact_id=str(row["artifact_id"]),
            expected_profile_fingerprint=str(row["profile_fingerprint"]),
            expected_scope_digest=str(row["scope_digest"]),
            expected_artifact_digest=str(row["artifact_digest"]),
            expected_created_at=str(row["created_at"]),
            expected_expires_at=str(row["expires_at"]),
        )

    def delete_report(
        self,
        user_id: str,
        run_id: str,
        *,
        expected_artifact_digest: str | None = None,
    ) -> bool:
        """Delete one exact user-scoped artifact and all normalized audit rows."""
        user_id = self._user_id(user_id)
        normalized_run_id = str(run_id).strip()
        if not normalized_run_id:
            raise ValueError("run_id is required for evaluation deletion")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT artifact_digest FROM twin_eval_runs
                WHERE user_id = ? AND run_id = ?
                """,
                (user_id, normalized_run_id),
            ).fetchone()
            if row is None:
                conn.rollback()
                return False
            if (
                expected_artifact_digest is not None
                and row["artifact_digest"] != expected_artifact_digest
            ):
                raise EvaluationArtifactCollision(
                    "artifact digest does not match the requested deletion target"
                )
            if conn.execute(
                """
                SELECT 1 FROM twin_eval_execution_requests
                WHERE user_id = ? AND result_run_id = ?
                LIMIT 1
                """,
                (user_id, normalized_run_id),
            ).fetchone():
                raise EvaluationArtifactInUse(
                    "evaluation report is referenced by a completed execution"
                )
            self._delete_report_rows(conn, user_id, normalized_run_id)
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def purge_reports_before(
        self,
        user_id: str,
        before: str,
        *,
        limit: int = 100,
        expected_run_ids: tuple[str, ...] | None = None,
    ) -> tuple[str, ...]:
        """Delete a bounded batch of one user's artifacts older than an ISO timestamp."""
        user_id = self._user_id(user_id)
        normalized_before = str(before).strip()
        if not normalized_before:
            raise ValueError("before timestamp is required")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1_000:
            raise ValueError("purge limit must be an integer between 1 and 1000")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT julianday(?) AS value",
                (normalized_before,),
            ).fetchone()["value"] is None:
                raise ValueError("before timestamp must be parseable by SQLite")
            rows = conn.execute(
                """
                SELECT r.run_id FROM twin_eval_runs AS r
                WHERE r.user_id = ?
                  AND julianday(r.created_at) < julianday(?)
                  AND NOT EXISTS (
                    SELECT 1
                    FROM twin_eval_execution_requests AS e
                    WHERE e.user_id = r.user_id
                      AND e.result_run_id = r.run_id
                  )
                ORDER BY r.created_at, r.run_id
                LIMIT ?
                """,
                (user_id, normalized_before, limit),
            ).fetchall()
            run_ids = tuple(str(row["run_id"]) for row in rows)
            if expected_run_ids is not None and run_ids != tuple(expected_run_ids):
                raise EvaluationArtifactCollision(
                    "retention target changed after preview"
                )
            for normalized_run_id in run_ids:
                self._delete_report_rows(conn, user_id, normalized_run_id)
            conn.commit()
            return run_ids
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_expired_profile_artifacts(
        self,
        user_id: str,
        as_of: str,
        *,
        limit: int = 100,
    ) -> tuple[str, ...]:
        """Preview an exact bounded batch of expired encrypted evidence."""

        user_id = self._user_id(user_id)
        normalized_as_of = self._utc_timestamp(as_of, "as_of")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1_000
        ):
            raise ValueError("preview limit must be between 1 and 1000")
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT run_id
                FROM twin_eval_profile_artifacts
                WHERE user_id = ?
                  AND julianday(expires_at) <= julianday(?)
                ORDER BY expires_at, run_id
                LIMIT ?
                """,
                (user_id, normalized_as_of, limit),
            ).fetchall()
            return tuple(str(row["run_id"]) for row in rows)
        finally:
            conn.close()

    def purge_expired_profile_artifacts(
        self,
        user_id: str,
        as_of: str,
        *,
        limit: int = 100,
        expected_run_ids: tuple[str, ...] | None = None,
    ) -> tuple[str, ...]:
        """Delete a preview-locked batch of expired evidence ciphertext."""

        user_id = self._user_id(user_id)
        normalized_as_of = self._utc_timestamp(as_of, "as_of")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1_000
        ):
            raise ValueError("purge limit must be between 1 and 1000")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT run_id
                FROM twin_eval_profile_artifacts
                WHERE user_id = ?
                  AND julianday(expires_at) <= julianday(?)
                ORDER BY expires_at, run_id
                LIMIT ?
                """,
                (user_id, normalized_as_of, limit),
            ).fetchall()
            run_ids = tuple(str(row["run_id"]) for row in rows)
            if (
                expected_run_ids is not None
                and run_ids != tuple(expected_run_ids)
            ):
                raise EvaluationArtifactCollision(
                    "profile retention target changed after preview"
                )
            for normalized_run_id in run_ids:
                conn.execute(
                    """
                    DELETE FROM twin_eval_profile_artifacts
                    WHERE user_id = ? AND run_id = ?
                    """,
                    (user_id, normalized_run_id),
                )
            conn.commit()
            return run_ids
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_reports_before(
        self,
        user_id: str,
        before: str,
        *,
        limit: int = 100,
    ) -> tuple[str, ...]:
        """Preview the exact bounded batch selected by purge_reports_before."""
        user_id = self._user_id(user_id)
        normalized_before = str(before).strip()
        if not normalized_before:
            raise ValueError("before timestamp is required")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1_000
        ):
            raise ValueError("preview limit must be an integer between 1 and 1000")
        conn = self._connect()
        try:
            if conn.execute(
                "SELECT julianday(?) AS value",
                (normalized_before,),
            ).fetchone()["value"] is None:
                raise ValueError("before timestamp must be parseable by SQLite")
            rows = conn.execute(
                """
                SELECT run_id FROM twin_eval_runs
                WHERE user_id = ? AND julianday(created_at) < julianday(?)
                ORDER BY created_at, run_id
                LIMIT ?
                """,
                (user_id, normalized_before, limit),
            ).fetchall()
            return tuple(str(row["run_id"]) for row in rows)
        finally:
            conn.close()

    def replay_bundle(
        self,
        user_id: str,
        run_id: str,
        *,
        _conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        """Return a fully cross-verified immutable replay bundle."""
        user_id = self._user_id(user_id)
        conn = _conn if _conn is not None else self._connect()
        owns_connection = _conn is None
        try:
            row = conn.execute(
                """
                SELECT artifact_digest, seed_json, spec_json, manifest_json,
                       report_json
                FROM twin_eval_runs WHERE user_id = ? AND run_id = ?
                """,
                (user_id, str(run_id).strip()),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown twin evaluation run_id: {run_id}")
            stored_report_json = row["report_json"]
            encrypted_storage = _is_encrypted_reference(
                stored_report_json,
                ENCRYPTED_REPORT_REFERENCE_SCHEMA,
            )
            if not encrypted_storage and not self.allow_plaintext_reports:
                raise ReportArtifactEncryptionUnavailable(
                    "legacy plaintext report replay requires explicit "
                    "local/test mode or a verified migration"
                )
            parsed_report = (
                self._load_encrypted_report_json(
                    conn,
                    user_id=user_id,
                    run_id=str(run_id).strip(),
                    artifact_digest=str(row["artifact_digest"]),
                )
                if encrypted_storage
                else None
            )
            report_json = (
                parsed_report.report_json
                if parsed_report is not None
                else stored_report_json
            )
            reference_salt = (
                parsed_report.reference_salt
                if parsed_report is not None
                else ""
            )
            report = _report_from_json(report_json)
            if report.run_id != run_id or report.artifact_digest != row["artifact_digest"]:
                raise ValueError("persisted evaluation artifact digest verification failed")

            expected_spec_json = (
                _encrypted_reference(ENCRYPTED_REPORT_REFERENCE_SCHEMA)
                if encrypted_storage
                else canonical_json(_report_spec(report))
            )
            expected_manifest_json = canonical_json(
                _report_manifest(report, report_json)
            )
            if row["spec_json"] != expected_spec_json:
                raise ValueError("persisted evaluation spec verification failed")
            expected_seed_json = (
                _encrypted_reference(ENCRYPTED_REPORT_REFERENCE_SCHEMA)
                if encrypted_storage
                else canonical_json(report.seed)
            )
            if row["seed_json"] != expected_seed_json:
                raise ValueError("persisted evaluation seed verification failed")
            if row["manifest_json"] != expected_manifest_json:
                raise ValueError("persisted evaluation manifest verification failed")

            def verified_rows(
                table: str,
                id_column: str,
                json_column: str,
                digest_column: str,
                expected: Mapping[str, Any],
            ) -> None:
                rows = conn.execute(
                    f"""
                    SELECT {id_column}, {json_column}, {digest_column}
                    FROM {table} WHERE user_id = ? AND run_id = ?
                    """,
                    (user_id, run_id),
                ).fetchall()
                actual = {
                    str(child[id_column]): (child[json_column], child[digest_column])
                    for child in rows
                }
                id_kind = {
                    "twin_eval_candidates": "candidate",
                    "twin_eval_comparisons": "comparison",
                    "twin_eval_resolved_comparisons": (
                        "logical_comparison"
                    ),
                    "twin_eval_rankings": "system",
                }[table]
                expected_rows = {
                    (
                        _opaque_reference(
                            reference_salt,
                            id_kind,
                            str(child_id),
                        )
                        if encrypted_storage
                        else str(child_id)
                    ): (
                        (
                            _encrypted_reference(
                                ENCRYPTED_CHILD_REFERENCE_SCHEMA
                            )
                            if encrypted_storage
                            else canonical_json(value)
                        ),
                        canonical_hash(value),
                    )
                    for child_id, value in expected.items()
                }
                if actual != expected_rows:
                    raise ValueError(f"persisted {table} verification failed")

            verified_rows(
                "twin_eval_candidates",
                "candidate_id",
                "candidate_json",
                "candidate_digest",
                _unique_candidates(report),
            )
            verified_rows(
                "twin_eval_comparisons",
                "comparison_id",
                "comparison_json",
                "comparison_digest",
                {
                    record.plan.comparison_id: _comparison_payload(record)
                    for record in report.comparisons
                },
            )
            verified_rows(
                "twin_eval_resolved_comparisons",
                "logical_comparison_id",
                "resolved_json",
                "resolved_digest",
                {
                    resolved.logical_comparison_id: resolved
                    for resolved in report.resolved_comparisons
                },
            )
            verified_rows(
                "twin_eval_rankings",
                "system_id",
                "rating_json",
                "rating_digest",
                {rating.system_id: rating for rating in report.ranking.ratings},
            )
            ranking_row = conn.execute(
                """
                SELECT ranking_json, ranking_digest
                FROM twin_eval_ranking_manifests
                WHERE user_id = ? AND run_id = ?
                """,
                (user_id, run_id),
            ).fetchone()
            expected_ranking = (
                (
                    canonical_json(
                        _ranking_summary(
                            report.ranking,
                            reference_salt,
                        )
                    )
                    if encrypted_storage
                    else canonical_json(report.ranking)
                ),
                canonical_hash(report.ranking),
            )
            if ranking_row is None or (
                ranking_row["ranking_json"],
                ranking_row["ranking_digest"],
            ) != expected_ranking:
                raise ValueError("persisted ranking manifest verification failed")
            return {
                "artifact_digest": row["artifact_digest"],
                "spec": _report_spec(report),
                "manifest": json.loads(row["manifest_json"]),
                "report": json.loads(report_json),
            }
        finally:
            if owns_connection:
                conn.close()
