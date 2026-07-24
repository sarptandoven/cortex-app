from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

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


class EvaluationArtifactCollision(ValueError):
    """A run ID already exists with different immutable artifact bytes."""


ARTIFACT_SCHEMA_VERSION = "pairwise-twin-artifact/v2"
COMPARISON_SCHEMA_VERSION = "pairwise-twin-comparison/v2"


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


class TwinEvalRepository:
    """Immutable SQLite persistence for exact offline evaluation replay."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
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

    def save_report(self, user_id: str, report: EvaluationReport) -> str:
        user_id = self._user_id(user_id)
        report_json = canonical_json(_report_payload(report))
        artifact_digest = canonical_hash(report, prefix="artifact_")
        if artifact_digest != report.artifact_digest:
            raise ValueError("evaluation report artifact digest is not canonical")
        spec = _report_spec(report)
        unique_candidates = _unique_candidates(report)
        manifest = _report_manifest(report, report_json)

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT artifact_digest, report_json FROM twin_eval_runs WHERE user_id = ? AND run_id = ?",
                (user_id, report.run_id),
            ).fetchone()
            if existing is not None:
                if existing["artifact_digest"] == artifact_digest and existing["report_json"] == report_json:
                    conn.rollback()
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
                    canonical_json(report.seed),
                    report.profile_fingerprint,
                    canonical_json(spec),
                    canonical_json(manifest),
                    report_json,
                ),
            )
            for candidate_id in sorted(unique_candidates):
                candidate = unique_candidates[candidate_id]
                payload = canonical_json(candidate)
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
                        candidate.candidate_id,
                        candidate.prompt_id,
                        candidate.system_id,
                        payload,
                        canonical_hash(candidate),
                    ),
                )
            for record in report.comparisons:
                compact_record = _comparison_payload(record)
                payload = canonical_json(compact_record)
                conn.execute(
                    """
                    INSERT INTO twin_eval_comparisons
                    (user_id, run_id, comparison_id, logical_comparison_id, left_candidate_id,
                     right_candidate_id, comparison_json, comparison_digest)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        report.run_id,
                        record.plan.comparison_id,
                        record.plan.logical_comparison_id,
                        record.left.candidate_id,
                        record.right.candidate_id,
                        payload,
                        canonical_hash(compact_record),
                    ),
                )
            for resolved in report.resolved_comparisons:
                conn.execute(
                    """
                    INSERT INTO twin_eval_resolved_comparisons
                    (user_id, run_id, logical_comparison_id, resolved_json, resolved_digest)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        report.run_id,
                        resolved.logical_comparison_id,
                        canonical_json(resolved),
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
                        rating.system_id,
                        canonical_json(rating),
                        canonical_hash(rating),
                    ),
                )
            conn.execute(
                """
                INSERT INTO twin_eval_ranking_manifests
                (user_id, run_id, ranking_json, ranking_digest) VALUES (?, ?, ?, ?)
                """,
                (
                    user_id,
                    report.run_id,
                    canonical_json(report.ranking),
                    canonical_hash(report.ranking),
                ),
            )
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
        finally:
            conn.close()
        if row is None:
            raise KeyError(f"unknown twin evaluation run_id: {run_id}")
        report = _report_from_json(row["report_json"])
        if report.run_id != run_id:
            raise ValueError("persisted run ID does not match replay artifact")
        if report.artifact_digest != row["artifact_digest"]:
            raise ValueError("persisted evaluation artifact digest verification failed")
        return report

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
                SELECT run_id FROM twin_eval_runs
                WHERE user_id = ? AND julianday(created_at) < julianday(?)
                ORDER BY created_at, run_id
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

    def replay_bundle(self, user_id: str, run_id: str) -> dict[str, Any]:
        """Return a fully cross-verified immutable replay bundle."""
        user_id = self._user_id(user_id)
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT artifact_digest, spec_json, manifest_json, report_json
                FROM twin_eval_runs WHERE user_id = ? AND run_id = ?
                """,
                (user_id, str(run_id).strip()),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown twin evaluation run_id: {run_id}")
            report = _report_from_json(row["report_json"])
            if report.run_id != run_id or report.artifact_digest != row["artifact_digest"]:
                raise ValueError("persisted evaluation artifact digest verification failed")

            expected_spec_json = canonical_json(_report_spec(report))
            expected_manifest_json = canonical_json(_report_manifest(report, row["report_json"]))
            if row["spec_json"] != expected_spec_json:
                raise ValueError("persisted evaluation spec verification failed")
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
                expected_rows = {
                    str(child_id): (canonical_json(value), canonical_hash(value))
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
                canonical_json(report.ranking),
                canonical_hash(report.ranking),
            )
            if ranking_row is None or (
                ranking_row["ranking_json"],
                ranking_row["ranking_digest"],
            ) != expected_ranking:
                raise ValueError("persisted ranking manifest verification failed")
            return {
                "artifact_digest": row["artifact_digest"],
                "spec": json.loads(row["spec_json"]),
                "manifest": json.loads(row["manifest_json"]),
                "report": json.loads(row["report_json"]),
            }
        finally:
            conn.close()
