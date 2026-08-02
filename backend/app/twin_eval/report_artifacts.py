from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any, Mapping

from .domain import canonical_hash, canonical_json


REPORT_ARTIFACT_SCHEMA_VERSION = "cortex-twin-eval-report-artifact/v1"
REPORT_ARTIFACT_ENCRYPTION_PURPOSE = "twin_eval_report"
MAX_REPORT_ARTIFACT_BYTES = 64 * 1024 * 1024


class ReportArtifactError(ValueError):
    """An encrypted evaluation report failed strict validation."""


class ReportArtifactEncryptionUnavailable(ReportArtifactError):
    """A private report cannot be stored or loaded without encryption."""


@dataclass(frozen=True)
class ReportArtifactEnvelope:
    artifact_id: str
    report_digest: str
    reference_salt: str
    created_at: str
    plaintext: bytes


@dataclass(frozen=True)
class ParsedReportArtifact:
    report_json: str
    reference_salt: str


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise ReportArtifactError(f"{field_name} must be an object")
    return value


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReportArtifactError(f"{field_name} must be a non-empty string")
    return value


def _exact_keys(
    value: Mapping[str, Any],
    expected: tuple[str, ...],
    field_name: str,
) -> None:
    if set(value) != set(expected):
        raise ReportArtifactError(
            f"{field_name} has unexpected or missing fields"
        )


def build_report_artifact_envelope(
    *,
    user_id: str,
    run_id: str,
    artifact_id: str,
    artifact_digest: str,
    report_json: str,
    created_at: str,
) -> ReportArtifactEnvelope:
    """Build one self-binding canonical report envelope before encryption."""

    try:
        report = json.loads(report_json)
    except (TypeError, ValueError) as exc:
        raise ReportArtifactError("report payload is not valid JSON") from exc
    if not isinstance(report, dict):
        raise ReportArtifactError("report payload must be an object")
    canonical_report = canonical_json(report)
    if canonical_report != report_json:
        raise ReportArtifactError("report payload must use canonical JSON")
    report_digest = canonical_hash(
        report,
        prefix="pairwise_report_payload_",
    )
    reference_salt = secrets.token_hex(32)
    envelope = {
        "schema_version": REPORT_ARTIFACT_SCHEMA_VERSION,
        "user_id": _required_string(user_id, "user_id"),
        "run_id": _required_string(run_id, "run_id"),
        "artifact_id": _required_string(artifact_id, "artifact_id"),
        "artifact_digest": _required_string(
            artifact_digest,
            "artifact_digest",
        ),
        "report_digest": report_digest,
        "reference_salt": reference_salt,
        "created_at": _required_string(created_at, "created_at"),
        "report": report,
    }
    plaintext = canonical_json(envelope).encode("utf-8")
    if len(plaintext) > MAX_REPORT_ARTIFACT_BYTES:
        raise ReportArtifactError(
            f"report artifact exceeds {MAX_REPORT_ARTIFACT_BYTES} bytes"
        )
    return ReportArtifactEnvelope(
        artifact_id=artifact_id,
        report_digest=report_digest,
        reference_salt=reference_salt,
        created_at=created_at,
        plaintext=plaintext,
    )


def parse_report_artifact(
    plaintext: bytes,
    *,
    expected_user_id: str,
    expected_run_id: str,
    expected_artifact_id: str,
    expected_artifact_digest: str,
    expected_report_digest: str,
    expected_created_at: str,
) -> ParsedReportArtifact:
    """Validate every authenticated field and return canonical report JSON."""

    if not isinstance(plaintext, bytes):
        raise ReportArtifactError("report artifact plaintext must be bytes")
    if not plaintext or len(plaintext) > MAX_REPORT_ARTIFACT_BYTES:
        raise ReportArtifactError("report artifact size is invalid")
    try:
        decoded = plaintext.decode("utf-8")
        raw = json.loads(decoded)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ReportArtifactError("report artifact is malformed") from exc
    envelope = _mapping(raw, "report artifact")
    _exact_keys(
        envelope,
        (
            "schema_version",
            "user_id",
            "run_id",
            "artifact_id",
            "artifact_digest",
            "report_digest",
            "reference_salt",
            "created_at",
            "report",
        ),
        "report artifact",
    )
    expected = {
        "schema_version": REPORT_ARTIFACT_SCHEMA_VERSION,
        "user_id": expected_user_id,
        "run_id": expected_run_id,
        "artifact_id": expected_artifact_id,
        "artifact_digest": expected_artifact_digest,
        "report_digest": expected_report_digest,
        "created_at": expected_created_at,
    }
    for field_name, expected_value in expected.items():
        if envelope.get(field_name) != expected_value:
            raise ReportArtifactError(
                f"report artifact {field_name} verification failed"
            )
    reference_salt = _required_string(
        envelope["reference_salt"],
        "reference_salt",
    )
    try:
        salt_bytes = bytes.fromhex(reference_salt)
    except ValueError as exc:
        raise ReportArtifactError(
            "report artifact reference_salt is malformed"
        ) from exc
    if len(salt_bytes) != 32 or reference_salt != reference_salt.lower():
        raise ReportArtifactError(
            "report artifact reference_salt is malformed"
        )
    report = _mapping(envelope["report"], "report")
    actual_report_digest = canonical_hash(
        report,
        prefix="pairwise_report_payload_",
    )
    if actual_report_digest != expected_report_digest:
        raise ReportArtifactError(
            "report artifact payload digest verification failed"
        )
    canonical_plaintext = canonical_json(envelope).encode("utf-8")
    if canonical_plaintext != plaintext:
        raise ReportArtifactError("report artifact is not canonical")
    return ParsedReportArtifact(
        report_json=canonical_json(report),
        reference_salt=reference_salt,
    )
