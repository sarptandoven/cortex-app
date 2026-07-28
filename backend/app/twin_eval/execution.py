from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, Sequence

from ..database_maintenance import maintenance_locked_connect
from ..sqlite_runtime import sqlite3
from .admission import PairwiseAdmissionPolicy
from .application import (
    build_pairwise_preflight_response,
    require_pairwise_admission_receipt,
)
from .domain import (
    EvaluationPrompt,
    EvaluationReport,
    canonical_hash,
    canonical_json,
)
from .execution_authority import (
    PairwiseDispatchAuthorityError,
    PairwiseDispatchAuthorityStore,
)
from .execution_checkpoints import (
    CALL_CHECKPOINT_ENCRYPTION_PURPOSE,
    CALL_CHECKPOINT_SCHEMA,
    _ConsumedCandidateDispatch,
    _PairwiseDispatchCapability,
    build_candidate_call_definition,
)
from .profile_adapter import CortexHeldOutProfileBundle
from .profile_artifacts import (
    parse_cortex_profile_bundle,
    serialize_cortex_profile_bundle,
)
from .protocols import DeterministicGenerator, OracleJudge
from .ranking import BradleyTerryRanker
from .repository import TwinEvalRepository
from .runner import PairwiseEvaluationRunner
from .strategies import RepeatedSwappedStrategy


EXECUTION_ARTIFACT_SCHEMA = "pairwise-execution-request/v1"
EXECUTION_RESULT_SCHEMA = "pairwise-execution-result/v1"
EXECUTION_ENCRYPTION_PURPOSE = "twin_eval_execution"
PAIRWISE_CONSENT_SCOPE = "pairwise_remote_evaluation"
_TERMINAL_STATUSES = frozenset({"cancelled", "succeeded", "failed"})
_WORKER_FAILURE_CODES = frozenset({"artifact_invalid", "internal_error"})
_PUBLIC_STATUSES = frozenset(
    {
        "prepared",
        "queued",
        "running",
        "cancel_requested",
        *_TERMINAL_STATUSES,
    }
)


class PairwiseExecutionError(ValueError):
    """Base error for the disabled-by-default pairwise execution control plane."""


class PairwiseExecutionUnavailable(PairwiseExecutionError):
    """A trusted dependency or authoritative grant is unavailable."""


class PairwiseExecutionConflict(PairwiseExecutionError):
    """A consumed identifier or lifecycle state conflicts with the request."""


class PairwiseExecutionNotFound(PairwiseExecutionError):
    """No user-scoped execution record exists."""


def _utc_now() -> str:
    return _datetime_to_text(datetime.now(timezone.utc))


def _datetime_to_text(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_datetime(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise PairwiseExecutionUnavailable(
            f"{name} must be a timezone-aware timestamp"
        )
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(
            raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        )
    except ValueError as exc:
        raise PairwiseExecutionUnavailable(
            f"{name} must be a timezone-aware timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise PairwiseExecutionUnavailable(
            f"{name} must be a timezone-aware timestamp"
        )
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _required_text(value: Any, name: str, *, maximum: int = 200) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PairwiseExecutionError(f"{name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise PairwiseExecutionError(
            f"{name} must not exceed {maximum} characters"
        )
    return normalized


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        raise PairwiseExecutionError(f"{name} must be an object")
    return value


def _json_snapshot(value: Any, name: str) -> Any:
    try:
        return json.loads(canonical_json(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PairwiseExecutionError(
            f"{name} must contain only canonical JSON values"
        ) from exc


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _deep_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _secret_binding(
    secret: str,
    *,
    namespace: str,
    user_id: str,
    value: Any,
) -> str:
    message = canonical_json(
        {
            "namespace": namespace,
            "user_id": user_id,
            "value": value,
        }
    ).encode("utf-8")
    return hmac.new(
        secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


@dataclass(frozen=True)
class TrustedPairwiseAdapterEndpoint:
    """Server-owned identity and limits for one immutable adapter revision."""

    adapter_revision: str
    endpoint_id: str
    model_id: str
    request_schema_version: str
    response_parser_revision: str
    timeout_seconds: int
    max_input_chars: int
    max_output_chars: int
    max_input_tokens: int
    max_output_tokens: int
    supports_idempotency: bool = False
    idempotency_field: str | None = None
    _manifest_json: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        adapter_revision = _required_text(
            self.adapter_revision, "adapter_revision"
        )
        endpoint_id = _required_text(self.endpoint_id, "endpoint_id")
        model_id = _required_text(self.model_id, "model_id")
        request_schema_version = _required_text(
            self.request_schema_version, "request_schema_version"
        )
        response_parser_revision = _required_text(
            self.response_parser_revision,
            "response_parser_revision",
        )
        for name, minimum, maximum in (
            ("timeout_seconds", 1, 15 * 60),
            ("max_input_chars", 1, 2_000_000),
            ("max_output_chars", 1, 200_000),
            ("max_input_tokens", 1, 10_000_000),
            ("max_output_tokens", 1, 1_000_000),
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not minimum <= value <= maximum
            ):
                raise PairwiseExecutionUnavailable(
                    f"{name} must be between {minimum} and {maximum}"
                )
        if not isinstance(self.supports_idempotency, bool):
            raise PairwiseExecutionUnavailable(
                "supports_idempotency must be boolean"
            )
        idempotency_field = self.idempotency_field
        if self.supports_idempotency:
            idempotency_field = _required_text(
                idempotency_field, "idempotency_field", maximum=100
            )
        elif idempotency_field is not None:
            raise PairwiseExecutionUnavailable(
                "idempotency_field requires endpoint idempotency support"
            )
        manifest_json = canonical_json(
            {
                "schema_version": "pairwise-adapter-endpoint/v1",
                "adapter_revision": adapter_revision,
                "endpoint_id": endpoint_id,
                "model_id": model_id,
                "request_schema_version": request_schema_version,
                "response_parser_revision": response_parser_revision,
                "timeout_seconds": self.timeout_seconds,
                "max_input_chars": self.max_input_chars,
                "max_output_chars": self.max_output_chars,
                "max_input_tokens": self.max_input_tokens,
                "max_output_tokens": self.max_output_tokens,
                "supports_idempotency": self.supports_idempotency,
                "idempotency_field": idempotency_field,
            }
        )
        object.__setattr__(self, "adapter_revision", adapter_revision)
        object.__setattr__(self, "endpoint_id", endpoint_id)
        object.__setattr__(self, "model_id", model_id)
        object.__setattr__(
            self, "request_schema_version", request_schema_version
        )
        object.__setattr__(
            self,
            "response_parser_revision",
            response_parser_revision,
        )
        object.__setattr__(
            self, "idempotency_field", idempotency_field
        )
        object.__setattr__(self, "_manifest_json", manifest_json)

    @property
    def manifest(self) -> dict[str, Any]:
        return json.loads(self._manifest_json)


@dataclass(frozen=True)
class TrustedPairwiseExecutionConfig:
    """Stable, non-secret identities for future server-owned adapters."""

    system_revisions: Mapping[str, str]
    judge_revision: str
    assumptions: Mapping[str, Any]
    consent_version: str
    request_retention_seconds: int = 7 * 24 * 60 * 60
    adapter_endpoints: Mapping[
        str, TrustedPairwiseAdapterEndpoint
    ] = field(default_factory=dict)
    _manifest_json: str = field(init=False, repr=False)
    _digest: str = field(init=False, repr=False)
    _system_ids: tuple[str, ...] = field(init=False, repr=False)
    _assumptions_json: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        systems: dict[str, str] = {}
        for raw_id, raw_revision in _mapping(
            self.system_revisions, "system_revisions"
        ).items():
            system_id = _required_text(raw_id, "system_id")
            systems[system_id] = _required_text(
                raw_revision, f"system_revisions[{system_id!r}]"
            )
        if len(systems) < 2:
            raise PairwiseExecutionUnavailable(
                "trusted execution requires at least two allowlisted systems"
            )
        judge_revision = _required_text(
            self.judge_revision, "judge_revision"
        )
        consent_version = _required_text(
            self.consent_version, "consent_version"
        )
        assumptions = _json_snapshot(
            _mapping(self.assumptions, "assumptions"),
            "assumptions",
        )
        adapter_endpoints: dict[
            str, TrustedPairwiseAdapterEndpoint
        ] = {}
        for raw_revision, raw_endpoint in _mapping(
            self.adapter_endpoints, "adapter_endpoints"
        ).items():
            revision = _required_text(
                raw_revision, "adapter_endpoint_revision"
            )
            if (
                not isinstance(
                    raw_endpoint, TrustedPairwiseAdapterEndpoint
                )
                or raw_endpoint.adapter_revision != revision
            ):
                raise PairwiseExecutionUnavailable(
                    "adapter endpoints must be trusted objects keyed by "
                    "their exact revision"
                )
            adapter_endpoints[revision] = raw_endpoint
        retention = self.request_retention_seconds
        if (
            isinstance(retention, bool)
            or not isinstance(retention, int)
            or not 60 <= retention <= 30 * 24 * 60 * 60
        ):
            raise PairwiseExecutionUnavailable(
                "request_retention_seconds must be between 60 and 2592000"
            )
        manifest = {
            "schema_version": "pairwise-trusted-execution-config/v1",
            "systems": [
                {"system_id": key, "revision": systems[key]}
                for key in sorted(systems)
            ],
            "judge_revision": judge_revision,
            "assumptions": assumptions,
            "consent_version": consent_version,
            "request_retention_seconds": retention,
            "adapter_endpoints": [
                adapter_endpoints[key].manifest
                for key in sorted(adapter_endpoints)
            ],
            "adapter_registry_digest": canonical_hash(
                [
                    adapter_endpoints[key].manifest
                    for key in sorted(adapter_endpoints)
                ],
                prefix="pairwise_adapter_registry_",
            ),
            "remote_execution_enabled": False,
        }
        manifest_json = canonical_json(manifest)
        object.__setattr__(
            self, "system_revisions", MappingProxyType(dict(systems))
        )
        object.__setattr__(
            self, "assumptions", _deep_freeze(assumptions)
        )
        object.__setattr__(
            self,
            "adapter_endpoints",
            MappingProxyType(dict(adapter_endpoints)),
        )
        object.__setattr__(self, "judge_revision", judge_revision)
        object.__setattr__(self, "consent_version", consent_version)
        object.__setattr__(self, "_system_ids", tuple(sorted(systems)))
        object.__setattr__(
            self, "_assumptions_json", canonical_json(assumptions)
        )
        object.__setattr__(self, "_manifest_json", manifest_json)
        object.__setattr__(
            self,
            "_digest",
            canonical_hash(
                manifest,
                prefix="pairwise_execution_config_",
            ),
        )

    @property
    def manifest(self) -> dict[str, Any]:
        return json.loads(self._manifest_json)

    @property
    def digest(self) -> str:
        return self._digest

    def assumptions_snapshot(self) -> dict[str, Any]:
        return json.loads(self._assumptions_json)

    def endpoint_for_revision(
        self, revision: str
    ) -> TrustedPairwiseAdapterEndpoint:
        try:
            return self.adapter_endpoints[revision]
        except KeyError as exc:
            raise PairwiseExecutionUnavailable(
                "trusted adapter endpoint is unavailable"
            ) from exc

    def validate_system_ids(self, raw_system_ids: Any) -> tuple[str, ...]:
        if not isinstance(raw_system_ids, (list, tuple)):
            raise PairwiseExecutionError("system_ids must be an array")
        system_ids = tuple(
            _required_text(value, f"system_ids[{index}]")
            for index, value in enumerate(raw_system_ids)
        )
        if len(system_ids) < 2 or len(system_ids) != len(set(system_ids)):
            raise PairwiseExecutionError(
                "system_ids must contain at least two unique systems"
            )
        unknown = sorted(set(system_ids) - set(self._system_ids))
        if unknown:
            raise PairwiseExecutionUnavailable(
                "untrusted pairwise systems: " + ", ".join(unknown)
            )
        return system_ids


@dataclass(frozen=True)
class PairwiseConsentGrant:
    user_id: str
    scope: str
    consent_version: str
    config_digest: str
    granted_at: str
    expires_at: str
    revoked_at: str | None = None


class PairwiseConsentAuthority(Protocol):
    def get_pairwise_consent(
        self, user_id: str
    ) -> PairwiseConsentGrant | None: ...


class PairwiseProfileBuilder(Protocol):
    def build(
        self,
        user_id: str,
        prompts: Sequence[EvaluationPrompt],
        *,
        as_of: str,
        sector: str | None = None,
    ) -> CortexHeldOutProfileBundle: ...


@dataclass(frozen=True)
class PairwiseExecutionStatus:
    evaluation_id: str
    status: str
    config_digest: str
    receipt_id: str
    consent_version: str
    created_at: str
    updated_at: str
    request_expires_at: str
    content_retained: bool
    attempt_count: int = 0
    provider_calls_reserved: int = 0
    provider_calls_dispatched: int = 0
    remote_outcome_unknown: bool = False
    cancel_requested_at: str | None = None
    content_deleted_at: str | None = None
    result_run_id: str | None = None
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "pairwise-execution-status/v1",
            "evaluation_id": self.evaluation_id,
            "status": self.status,
            "config_digest": self.config_digest,
            "receipt_id": self.receipt_id,
            "consent_version": self.consent_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "request_expires_at": self.request_expires_at,
            "content_retained": self.content_retained,
            "attempt_count": self.attempt_count,
            "provider_calls_reserved": self.provider_calls_reserved,
            "provider_calls_dispatched": self.provider_calls_dispatched,
            "remote_outcome_unknown": self.remote_outcome_unknown,
            "cancel_requested_at": self.cancel_requested_at,
            "content_deleted_at": self.content_deleted_at,
            "result_run_id": self.result_run_id,
            "error_code": self.error_code,
            "remote_execution_enabled": False,
        }


@dataclass(frozen=True)
class _PairwiseExecutionLease:
    """Opaque in-process capability; the raw token is never persisted."""

    user_id: str
    evaluation_id: str
    worker_id: str
    token: str = field(repr=False)
    generation: int
    lease_expires_at: str
    execution_deadline_at: str
    artifact: Mapping[str, Any] = field(repr=False)


class _PairwiseExecutionRepository:
    """Private encrypted storage used only after service-level verification."""

    def __init__(
        self,
        db_path: Path,
        *,
        cipher: Any,
        binding_keys: Mapping[str, str],
        active_binding_key_id: str,
        config: TrustedPairwiseExecutionConfig,
    ) -> None:
        self.db_path = Path(db_path)
        if not isinstance(config, TrustedPairwiseExecutionConfig):
            raise PairwiseExecutionUnavailable(
                "trusted execution config is unavailable"
            )
        if cipher is None or not bool(getattr(cipher, "available", False)):
            raise PairwiseExecutionUnavailable(
                "pairwise execution request encryption is unavailable"
            )
        for method in ("encrypt_blob", "decrypt_blob", "is_encrypted"):
            if not callable(getattr(cipher, method, None)):
                raise PairwiseExecutionUnavailable(
                    "pairwise execution request encryption is unavailable"
                )
        normalized_keys: dict[str, str] = {}
        raw_binding_keys = _mapping(binding_keys, "binding_keys")
        if not 1 <= len(raw_binding_keys) <= 16:
            raise PairwiseExecutionUnavailable(
                "binding_keys must contain between 1 and 16 versions"
            )
        for raw_id, raw_key in raw_binding_keys.items():
            key_id = _required_text(raw_id, "binding_key_id")
            encoded_key = (
                raw_key.encode("utf-8")
                if isinstance(raw_key, str)
                else b""
            )
            if not 32 <= len(encoded_key) <= 10_000:
                raise PairwiseExecutionUnavailable(
                    "pairwise execution binding keys must contain 32 to 10000 bytes"
                )
            normalized_keys[key_id] = raw_key
        active_binding_key_id = _required_text(
            active_binding_key_id, "active_binding_key_id"
        )
        if active_binding_key_id not in normalized_keys:
            raise PairwiseExecutionUnavailable(
                "active pairwise execution binding key is unavailable"
            )
        self.cipher = cipher
        self._binding_keys = MappingProxyType(normalized_keys)
        self._active_binding_key_id = active_binding_key_id
        self._config = config
        self._dispatch_authority = PairwiseDispatchAuthorityStore(
            self.db_path
        )
        self._report_repository = TwinEvalRepository(
            self.db_path,
            evidence_cipher=cipher,
        )

    def _binding_key(self, key_id: str) -> str:
        try:
            return self._binding_keys[key_id]
        except KeyError as exc:
            raise PairwiseExecutionUnavailable(
                "historical pairwise execution binding key is unavailable"
            ) from exc

    def _connect(self) -> sqlite3.Connection:
        conn = maintenance_locked_connect(
            self.db_path,
            lambda: sqlite3.connect(self.db_path),
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @staticmethod
    def _status(row: sqlite3.Row) -> PairwiseExecutionStatus:
        status = str(row["status"])
        if status not in _PUBLIC_STATUSES:
            raise PairwiseExecutionError(
                "persisted pairwise execution has an invalid status"
            )
        return PairwiseExecutionStatus(
            evaluation_id=str(row["evaluation_id"]),
            status=status,
            config_digest=str(row["config_digest"]),
            receipt_id=str(row["receipt_id"]),
            consent_version=str(row["consent_version"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            request_expires_at=str(row["request_expires_at"]),
            content_retained=row["request_ciphertext"] is not None,
            attempt_count=int(row["attempt_count"]),
            provider_calls_reserved=int(
                row["provider_calls_reserved"]
            ),
            provider_calls_dispatched=int(
                row["provider_calls_dispatched"]
            ),
            remote_outcome_unknown=bool(
                row["remote_outcome_unknown"]
            ),
            cancel_requested_at=row["cancel_requested_at"],
            content_deleted_at=row["content_deleted_at"],
            result_run_id=row["result_run_id"],
            error_code=row["error_code"],
        )

    @staticmethod
    def _assert_retry_matches(
        receipt_row: sqlite3.Row | None,
        idempotency_row: sqlite3.Row | None,
        *,
        receipt_id: str,
        idempotency_digest: str,
        request_binding: str,
        config_digest: str,
        consent_version: str,
    ) -> sqlite3.Row | None:
        if receipt_row is None and idempotency_row is None:
            return None
        if (
            receipt_row is None
            or idempotency_row is None
            or receipt_row["evaluation_id"]
            != idempotency_row["evaluation_id"]
        ):
            raise PairwiseExecutionConflict(
                "receipt and idempotency key must remain bound to one evaluation"
            )
        row = receipt_row
        if (
            str(row["receipt_id"]) != receipt_id
            or str(row["idempotency_digest"]) != idempotency_digest
            or not hmac.compare_digest(
                str(row["request_binding"]), request_binding
            )
            or str(row["config_digest"]) != config_digest
            or str(row["consent_version"]) != consent_version
        ):
            raise PairwiseExecutionConflict(
                "pairwise execution idempotency collision"
            )
        return row

    def submit_verified(
        self,
        *,
        user_id: str,
        request: Mapping[str, Any],
        profile_bundle: Mapping[str, Any],
        receipt: Mapping[str, Any],
        config_manifest: Mapping[str, Any],
        config_digest: str,
        consent_version: str,
        retention_seconds: int,
        idempotency_key: str,
        commit_guard: Callable[[], PairwiseConsentGrant],
    ) -> PairwiseExecutionStatus:
        user_id = _required_text(user_id, "user_id", maximum=500)
        request = _mapping(
            _json_snapshot(_mapping(request, "request"), "request"),
            "request",
        )
        profile_bundle = _mapping(
            _json_snapshot(profile_bundle, "profile_bundle"),
            "profile_bundle",
        )
        receipt = _mapping(
            _json_snapshot(_mapping(receipt, "receipt"), "receipt"),
            "receipt",
        )
        config_manifest = _mapping(
            _json_snapshot(config_manifest, "config_manifest"),
            "config_manifest",
        )
        idempotency_key = _required_text(
            idempotency_key, "idempotency_key", maximum=200
        )
        receipt_id = _required_text(
            receipt.get("receipt_id"), "receipt.receipt_id", maximum=200
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                receipt_row = conn.execute(
                    """
                    SELECT * FROM twin_eval_execution_requests
                    WHERE user_id = ? AND receipt_id = ?
                    """,
                    (user_id, receipt_id),
                ).fetchone()
                binding_key_id = (
                    str(receipt_row["binding_key_id"])
                    if receipt_row is not None
                    else self._active_binding_key_id
                )
                binding_key = self._binding_key(binding_key_id)
                request_binding = _secret_binding(
                    binding_key,
                    namespace="request",
                    user_id=user_id,
                    value=request,
                )
                idempotency_digest = _secret_binding(
                    binding_key,
                    namespace="idempotency",
                    user_id=user_id,
                    value=idempotency_key,
                )
                idempotency_row = conn.execute(
                    """
                    SELECT * FROM twin_eval_execution_requests
                    WHERE user_id = ? AND idempotency_digest = ?
                    """,
                    (user_id, idempotency_digest),
                ).fetchone()
                if receipt_row is None and idempotency_row is None:
                    historical_matches: list[sqlite3.Row] = []
                    for historical_id, historical_key in (
                        self._binding_keys.items()
                    ):
                        if historical_id == binding_key_id:
                            continue
                        historical_digest = _secret_binding(
                            historical_key,
                            namespace="idempotency",
                            user_id=user_id,
                            value=idempotency_key,
                        )
                        historical_row = conn.execute(
                            """
                            SELECT *
                            FROM twin_eval_execution_requests
                            WHERE user_id = ?
                              AND idempotency_digest = ?
                            """,
                            (user_id, historical_digest),
                        ).fetchone()
                        if historical_row is not None:
                            historical_matches.append(historical_row)
                    if len(historical_matches) > 1:
                        raise PairwiseExecutionConflict(
                            "idempotency key has ambiguous historical bindings"
                        )
                    if historical_matches:
                        idempotency_row = historical_matches[0]
                existing = self._assert_retry_matches(
                    receipt_row,
                    idempotency_row,
                    receipt_id=receipt_id,
                    idempotency_digest=idempotency_digest,
                    request_binding=request_binding,
                    config_digest=config_digest,
                    consent_version=consent_version,
                )
                if existing is not None:
                    conn.commit()
                    return self._status(existing)

                grant = commit_guard()
                created = datetime.now(timezone.utc).replace(microsecond=0)
                created_at = _datetime_to_text(created)
                request_expires_at = _datetime_to_text(
                    created + timedelta(seconds=retention_seconds)
                )
                evaluation_id = "pairwise_eval_" + secrets.token_hex(16)
                artifact_identity = {
                    "schema_version": EXECUTION_ARTIFACT_SCHEMA,
                    "evaluation_id": evaluation_id,
                    "user_id": user_id,
                    "binding_key_id": binding_key_id,
                    "receipt": receipt,
                    "request": request,
                    "profile_bundle": profile_bundle,
                    "request_binding": request_binding,
                    "config_manifest": config_manifest,
                    "config_digest": config_digest,
                    "consent_grant": _json_snapshot(
                        grant, "consent_grant"
                    ),
                    "consent_version": consent_version,
                    "created_at": created_at,
                    "request_expires_at": request_expires_at,
                }
                artifact_digest = canonical_hash(
                    artifact_identity,
                    prefix="pairwise_execution_artifact_",
                )
                plaintext = canonical_json(
                    {
                        **artifact_identity,
                        "artifact_digest": artifact_digest,
                    }
                ).encode("utf-8")
                ciphertext = self.cipher.encrypt_blob(
                    user_id,
                    EXECUTION_ENCRYPTION_PURPOSE,
                    plaintext,
                )
                if not self.cipher.is_encrypted(ciphertext):
                    raise PairwiseExecutionUnavailable(
                        "pairwise execution request was not CXE1 encrypted"
                    )
                conn.execute(
                    """
                    INSERT INTO twin_eval_execution_requests
                    (
                      user_id, evaluation_id, receipt_id, binding_key_id,
                      idempotency_digest, request_binding, config_digest,
                      artifact_digest, request_ciphertext, consent_version,
                      receipt_consumed_at, request_expires_at,
                      status, created_at, updated_at
                    )
                    VALUES (
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      'prepared', ?, ?
                    )
                    """,
                    (
                        user_id,
                        evaluation_id,
                        receipt_id,
                        binding_key_id,
                        idempotency_digest,
                        request_binding,
                        config_digest,
                        artifact_digest,
                        ciphertext,
                        consent_version,
                        created_at,
                        request_expires_at,
                        created_at,
                        created_at,
                    ),
                )
                row = conn.execute(
                    """
                    SELECT * FROM twin_eval_execution_requests
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    (user_id, evaluation_id),
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        if row is None:
            raise PairwiseExecutionError(
                "pairwise execution submission was not persisted"
            )
        return self._status(row)

    def get_status(
        self, user_id: str, evaluation_id: str
    ) -> PairwiseExecutionStatus:
        user_id = _required_text(user_id, "user_id", maximum=500)
        evaluation_id = _required_text(
            evaluation_id, "evaluation_id", maximum=200
        )
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM twin_eval_execution_requests
                WHERE user_id = ? AND evaluation_id = ?
                """,
                (user_id, evaluation_id),
            ).fetchone()
        if row is None:
            raise PairwiseExecutionNotFound(
                "pairwise execution was not found"
            )
        return self._status(row)

    def load_request(
        self, user_id: str, evaluation_id: str
    ) -> Mapping[str, Any]:
        user_id = _required_text(user_id, "user_id", maximum=500)
        evaluation_id = _required_text(
            evaluation_id, "evaluation_id", maximum=200
        )
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM twin_eval_execution_requests
                WHERE user_id = ? AND evaluation_id = ?
                """,
                (user_id, evaluation_id),
            ).fetchone()
        if row is None:
            raise PairwiseExecutionNotFound(
                "pairwise execution was not found"
            )
        return self._validate_execution_artifact_row(
            row,
            user_id=user_id,
            evaluation_id=evaluation_id,
        )

    def _validate_execution_artifact_row(
        self,
        row: sqlite3.Row,
        *,
        user_id: str,
        evaluation_id: str,
    ) -> Mapping[str, Any]:
        if row["request_ciphertext"] is None:
            raise PairwiseExecutionNotFound(
                "pairwise execution private request was deleted"
            )
        ciphertext = bytes(row["request_ciphertext"])
        if not self.cipher.is_encrypted(ciphertext):
            raise PairwiseExecutionError(
                "pairwise execution request is not CXE1 encrypted"
            )
        try:
            plaintext = self.cipher.decrypt_blob(
                user_id,
                EXECUTION_ENCRYPTION_PURPOSE,
                ciphertext,
            )
        except Exception as exc:
            raise PairwiseExecutionError(
                "pairwise execution request could not be authenticated"
            ) from exc
        try:
            artifact = json.loads(plaintext.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PairwiseExecutionError(
                "pairwise execution request is malformed"
            ) from exc
        artifact = _mapping(artifact, "execution artifact")
        expected_fields = {
            "schema_version",
            "evaluation_id",
            "user_id",
            "binding_key_id",
            "receipt",
            "request",
            "profile_bundle",
            "request_binding",
            "config_manifest",
            "config_digest",
            "consent_grant",
            "consent_version",
            "created_at",
            "request_expires_at",
            "artifact_digest",
        }
        receipt = _mapping(artifact.get("receipt"), "artifact.receipt")
        config_manifest = _mapping(
            artifact.get("config_manifest"),
            "artifact.config_manifest",
        )
        consent_grant = _mapping(
            artifact.get("consent_grant"),
            "artifact.consent_grant",
        )
        parsed_bundle = parse_cortex_profile_bundle(
            artifact.get("profile_bundle")
        )
        request = _mapping(
            artifact.get("request"),
            "artifact.request",
        )
        if (
            set(artifact) != expected_fields
            or artifact.get("schema_version") != EXECUTION_ARTIFACT_SCHEMA
            or artifact.get("evaluation_id") != evaluation_id
            or artifact.get("user_id") != user_id
            or artifact.get("binding_key_id") != row["binding_key_id"]
            or receipt.get("receipt_id") != row["receipt_id"]
            or artifact.get("request_binding") != row["request_binding"]
            or artifact.get("config_digest") != row["config_digest"]
            or artifact.get("consent_version") != row["consent_version"]
            or artifact.get("created_at") != row["created_at"]
            or artifact.get("request_expires_at")
            != row["request_expires_at"]
            or row["receipt_consumed_at"] != row["created_at"]
            or consent_grant.get("user_id") != user_id
            or consent_grant.get("scope") != PAIRWISE_CONSENT_SCOPE
            or consent_grant.get("consent_version")
            != row["consent_version"]
            or consent_grant.get("config_digest")
            != row["config_digest"]
            or canonical_json(parsed_bundle.profile)
            != canonical_json(request.get("profile"))
            or request.get("profile_bundle_digest")
            != canonical_hash(
                artifact.get("profile_bundle"),
                prefix="pairwise_execution_profile_bundle_",
            )
            or request.get("execution_config_digest")
            != row["config_digest"]
            or canonical_hash(
                config_manifest,
                prefix="pairwise_execution_config_",
            )
            != row["config_digest"]
            or not hmac.compare_digest(
                str(row["request_binding"]),
                _secret_binding(
                    self._binding_key(str(row["binding_key_id"])),
                    namespace="request",
                    user_id=user_id,
                    value=artifact.get("request"),
                ),
            )
        ):
            raise PairwiseExecutionError(
                "pairwise execution artifact binding failed"
            )
        identity = {
            key: artifact[key]
            for key in expected_fields
            if key != "artifact_digest"
        }
        expected_digest = canonical_hash(
            identity,
            prefix="pairwise_execution_artifact_",
        )
        if (
            artifact.get("artifact_digest") != expected_digest
            or str(row["artifact_digest"]) != expected_digest
        ):
            raise PairwiseExecutionError(
                "pairwise execution artifact digest verification failed"
            )
        return artifact

    @staticmethod
    def _lease_time(
        now_utc: str | None,
    ) -> tuple[datetime, str]:
        now = (
            _parse_datetime(now_utc, "now_utc")
            if now_utc is not None
            else datetime.now(timezone.utc)
        ).replace(microsecond=0)
        return now, _datetime_to_text(now)

    @staticmethod
    def _lease_seconds(value: int, name: str, maximum: int) -> int:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 5 <= value <= maximum
        ):
            raise PairwiseExecutionError(
                f"{name} must be between 5 and {maximum} seconds"
            )
        return value

    def _lease_digest(
        self,
        row: sqlite3.Row,
        *,
        worker_id: str,
        token: str,
        generation: int,
    ) -> str:
        return _secret_binding(
            self._binding_key(str(row["binding_key_id"])),
            namespace="lease_token",
            user_id=str(row["user_id"]),
            value={
                "evaluation_id": str(row["evaluation_id"]),
                "worker_id": worker_id,
                "generation": generation,
                "token": token,
            },
        )

    def _require_lease(
        self,
        row: sqlite3.Row | None,
        lease: _PairwiseExecutionLease,
        *,
        statuses: frozenset[str],
        now: datetime | None,
    ) -> None:
        if row is None:
            raise PairwiseExecutionNotFound(
                "pairwise execution was not found"
            )
        generation = int(row["lease_generation"])
        expected_digest = self._lease_digest(
            row,
            worker_id=lease.worker_id,
            token=lease.token,
            generation=lease.generation,
        )
        if (
            row["user_id"] != lease.user_id
            or row["evaluation_id"] != lease.evaluation_id
            or row["status"] not in statuses
            or row["lease_owner"] != lease.worker_id
            or generation != lease.generation
            or row["lease_token_digest"] is None
            or not hmac.compare_digest(
                str(row["lease_token_digest"]), expected_digest
            )
        ):
            raise PairwiseExecutionConflict(
                "pairwise execution lease is no longer active"
            )
        if now is not None:
            lease_expires = _parse_datetime(
                row["lease_expires_at"], "lease_expires_at"
            )
            deadline = _parse_datetime(
                row["execution_deadline_at"],
                "execution_deadline_at",
            )
            request_expires = _parse_datetime(
                row["request_expires_at"], "request_expires_at"
            )
            if not now < min(lease_expires, deadline, request_expires):
                raise PairwiseExecutionConflict(
                    "pairwise execution lease is no longer active"
                )

    def _assert_active_lease(
        self,
        lease: _PairwiseExecutionLease,
        *,
        now: datetime,
    ) -> None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM twin_eval_execution_requests
                WHERE user_id = ? AND evaluation_id = ?
                """,
                (lease.user_id, lease.evaluation_id),
            ).fetchone()
        self._require_lease(
            row,
            lease,
            statuses=frozenset({"running"}),
            now=now,
        )

    @staticmethod
    def _clear_lease_sql() -> str:
        return """
            lease_owner = NULL,
            lease_token_digest = NULL,
            lease_expires_at = NULL,
            execution_deadline_at = NULL,
            last_heartbeat_at = NULL
        """

    @staticmethod
    def _mark_dispatching_unknown_tx(
        conn: sqlite3.Connection,
        *,
        user_id: str,
        evaluation_id: str,
        timestamp: str,
    ) -> bool:
        existing = conn.execute(
            """
            SELECT 1
            FROM main.twin_eval_execution_call_checkpoints
            WHERE user_id = ? AND evaluation_id = ?
              AND state IN ('dispatching', 'outcome_unknown')
            LIMIT 1
            """,
            (user_id, evaluation_id),
        ).fetchone()
        if existing is None:
            return False
        conn.execute(
            """
            UPDATE main.twin_eval_execution_requests
            SET remote_outcome_unknown = 1,
                updated_at = ?
            WHERE user_id = ? AND evaluation_id = ?
            """,
            (timestamp, user_id, evaluation_id),
        )
        conn.execute(
            """
            UPDATE main.twin_eval_execution_call_checkpoints
            SET state = 'outcome_unknown',
                outcome_unknown_at = CASE
                  WHEN ? < consumed_at THEN consumed_at
                  ELSE ?
                END
            WHERE user_id = ? AND evaluation_id = ?
              AND state = 'dispatching'
            """,
            (timestamp, timestamp, user_id, evaluation_id),
        )
        return True

    def _reap_expired_tx(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        timestamp: str,
    ) -> tuple[str, ...]:
        rows = conn.execute(
            """
            SELECT evaluation_id, status
            FROM main.twin_eval_execution_requests
            WHERE user_id = ?
              AND status IN ('running', 'cancel_requested')
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            ORDER BY lease_expires_at, evaluation_id
            """,
            (user_id, timestamp),
        ).fetchall()
        for row in rows:
            cancelled = row["status"] == "cancel_requested"
            ambiguous = self._mark_dispatching_unknown_tx(
                conn,
                user_id=user_id,
                evaluation_id=str(row["evaluation_id"]),
                timestamp=timestamp,
            )
            conn.execute(
                f"""
                UPDATE main.twin_eval_execution_requests
                SET status = ?,
                    error_code = ?,
                    completed_at = ?,
                    updated_at = ?,
                    {self._clear_lease_sql()}
                WHERE user_id = ? AND evaluation_id = ?
                  AND status = ?
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                """,
                (
                    "cancelled" if cancelled else "failed",
                    (
                        "remote_outcome_unknown"
                        if ambiguous
                        else None
                        if cancelled
                        else "worker_lease_expired"
                    ),
                    timestamp,
                    timestamp,
                    user_id,
                    row["evaluation_id"],
                    row["status"],
                    timestamp,
                ),
            )
        return tuple(str(row["evaluation_id"]) for row in rows)

    def queue(
        self,
        user_id: str,
        evaluation_id: str,
        *,
        now_utc: str | None = None,
    ) -> PairwiseExecutionStatus:
        """Private activation boundary; no route calls this while disabled."""

        user_id = _required_text(user_id, "user_id", maximum=500)
        evaluation_id = _required_text(
            evaluation_id, "evaluation_id", maximum=200
        )
        now, timestamp = self._lease_time(now_utc)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    SELECT * FROM twin_eval_execution_requests
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    (user_id, evaluation_id),
                ).fetchone()
                if row is None:
                    raise PairwiseExecutionNotFound(
                        "pairwise execution was not found"
                    )
                if row["status"] == "queued":
                    if (
                        row["request_ciphertext"] is not None
                        and now
                        < _parse_datetime(
                            row["request_expires_at"],
                            "request_expires_at",
                        )
                    ):
                        conn.commit()
                        return self._status(row)
                    raise PairwiseExecutionConflict(
                        "pairwise execution cannot be queued"
                    )
                if (
                    row["status"] != "prepared"
                    or row["request_ciphertext"] is None
                    or not now
                    < _parse_datetime(
                        row["request_expires_at"],
                        "request_expires_at",
                    )
                ):
                    raise PairwiseExecutionConflict(
                        "pairwise execution cannot be queued"
                    )
                updated = conn.execute(
                    """
                    UPDATE twin_eval_execution_requests
                    SET status = 'queued', queued_at = ?, updated_at = ?
                    WHERE user_id = ? AND evaluation_id = ?
                      AND status = 'prepared'
                      AND request_ciphertext IS NOT NULL
                    """,
                    (timestamp, timestamp, user_id, evaluation_id),
                )
                if updated.rowcount != 1:
                    raise PairwiseExecutionConflict(
                        "pairwise execution cannot be queued"
                    )
                row = conn.execute(
                    """
                    SELECT * FROM twin_eval_execution_requests
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    (user_id, evaluation_id),
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self._status(row)

    def claim_next(
        self,
        user_id: str,
        worker_id: str,
        *,
        now_utc: str | None = None,
        lease_seconds: int = 60,
        execution_deadline_seconds: int = 15 * 60,
    ) -> _PairwiseExecutionLease | None:
        user_id = _required_text(user_id, "user_id", maximum=500)
        worker_id = _required_text(
            worker_id, "worker_id", maximum=200
        )
        lease_seconds = self._lease_seconds(
            lease_seconds, "lease_seconds", 15 * 60
        )
        execution_deadline_seconds = self._lease_seconds(
            execution_deadline_seconds,
            "execution_deadline_seconds",
            60 * 60,
        )
        if execution_deadline_seconds < lease_seconds:
            raise PairwiseExecutionError(
                "execution deadline must not be shorter than the lease"
            )
        now, timestamp = self._lease_time(now_utc)
        token = secrets.token_urlsafe(32)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._reap_expired_tx(
                    conn, user_id=user_id, timestamp=timestamp
                )
                row = conn.execute(
                    """
                    SELECT *
                    FROM twin_eval_execution_requests
                    WHERE user_id = ?
                      AND status = 'queued'
                      AND attempt_count = 0
                      AND request_ciphertext IS NOT NULL
                      AND request_expires_at > ?
                    ORDER BY queued_at, created_at, evaluation_id
                    LIMIT 1
                    """,
                    (user_id, timestamp),
                ).fetchone()
                if row is None:
                    conn.commit()
                    return None
                generation = int(row["lease_generation"]) + 1
                deadline = min(
                    now + timedelta(seconds=execution_deadline_seconds),
                    _parse_datetime(
                        row["request_expires_at"],
                        "request_expires_at",
                    ),
                )
                lease_expires = min(
                    now + timedelta(seconds=lease_seconds), deadline
                )
                lease_expires_at = _datetime_to_text(lease_expires)
                execution_deadline_at = _datetime_to_text(deadline)
                token_digest = self._lease_digest(
                    row,
                    worker_id=worker_id,
                    token=token,
                    generation=generation,
                )
                updated = conn.execute(
                    """
                    UPDATE twin_eval_execution_requests
                    SET status = 'running',
                        attempt_count = 1,
                        lease_generation = ?,
                        lease_owner = ?,
                        lease_token_digest = ?,
                        lease_expires_at = ?,
                        execution_deadline_at = ?,
                        started_at = ?,
                        last_heartbeat_at = ?,
                        updated_at = ?
                    WHERE user_id = ? AND evaluation_id = ?
                      AND status = 'queued' AND attempt_count = 0
                      AND request_ciphertext IS NOT NULL
                      AND request_expires_at > ?
                    """,
                    (
                        generation,
                        worker_id,
                        token_digest,
                        lease_expires_at,
                        execution_deadline_at,
                        timestamp,
                        timestamp,
                        timestamp,
                        user_id,
                        row["evaluation_id"],
                        timestamp,
                    ),
                )
                if updated.rowcount != 1:
                    raise PairwiseExecutionConflict(
                        "pairwise execution claim lost its race"
                    )
                evaluation_id = str(row["evaluation_id"])
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        lease = _PairwiseExecutionLease(
            user_id=user_id,
            evaluation_id=evaluation_id,
            worker_id=worker_id,
            token=token,
            generation=generation,
            lease_expires_at=lease_expires_at,
            execution_deadline_at=execution_deadline_at,
            artifact={},
        )
        try:
            artifact = self.load_request(user_id, evaluation_id)
        except Exception:
            self.fail(
                lease,
                error_code="artifact_invalid",
                now_utc=timestamp,
            )
            raise
        self._assert_active_lease(lease, now=now)
        return _PairwiseExecutionLease(
            user_id=lease.user_id,
            evaluation_id=lease.evaluation_id,
            worker_id=lease.worker_id,
            token=lease.token,
            generation=lease.generation,
            lease_expires_at=lease.lease_expires_at,
            execution_deadline_at=lease.execution_deadline_at,
            artifact=_deep_freeze(artifact),
        )

    def renew(
        self,
        lease: _PairwiseExecutionLease,
        *,
        now_utc: str | None = None,
        lease_seconds: int = 60,
    ) -> _PairwiseExecutionLease:
        lease_seconds = self._lease_seconds(
            lease_seconds, "lease_seconds", 15 * 60
        )
        now, timestamp = self._lease_time(now_utc)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    SELECT * FROM twin_eval_execution_requests
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    (lease.user_id, lease.evaluation_id),
                ).fetchone()
                self._require_lease(
                    row,
                    lease,
                    statuses=frozenset({"running"}),
                    now=now,
                )
                expires = min(
                    now + timedelta(seconds=lease_seconds),
                    _parse_datetime(
                        row["execution_deadline_at"],
                        "execution_deadline_at",
                    ),
                    _parse_datetime(
                        row["request_expires_at"],
                        "request_expires_at",
                    ),
                )
                expires_at = _datetime_to_text(expires)
                updated = conn.execute(
                    """
                    UPDATE twin_eval_execution_requests
                    SET lease_expires_at = ?, last_heartbeat_at = ?,
                        updated_at = ?
                    WHERE user_id = ? AND evaluation_id = ?
                      AND status = 'running'
                      AND lease_generation = ?
                      AND lease_token_digest = ?
                    """,
                    (
                        expires_at,
                        timestamp,
                        timestamp,
                        lease.user_id,
                        lease.evaluation_id,
                        lease.generation,
                        row["lease_token_digest"],
                    ),
                )
                if updated.rowcount != 1:
                    raise PairwiseExecutionConflict(
                        "pairwise execution lease is no longer active"
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return _PairwiseExecutionLease(
            user_id=lease.user_id,
            evaluation_id=lease.evaluation_id,
            worker_id=lease.worker_id,
            token=lease.token,
            generation=lease.generation,
            lease_expires_at=expires_at,
            execution_deadline_at=lease.execution_deadline_at,
            artifact=lease.artifact,
        )

    def _begin_candidate_call(
        self,
        lease: _PairwiseExecutionLease,
        *,
        prompt_id: str,
        system_id: str,
    ) -> _PairwiseDispatchCapability:
        """Atomically reserve one candidate call without performing I/O."""

        config = self._config
        prompt_id = _required_text(prompt_id, "prompt_id")
        system_id = _required_text(system_id, "system_id")
        permit = secrets.token_urlsafe(32)
        capability_values: dict[str, Any] | None = None

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    SELECT * FROM main.twin_eval_execution_requests
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    (lease.user_id, lease.evaluation_id),
                ).fetchone()
                if row is None:
                    raise PairwiseExecutionNotFound(
                        "pairwise execution was not found"
                    )
                try:
                    authorization = (
                        self._dispatch_authority.require_authorized_tx(
                            conn,
                            user_id=lease.user_id,
                            scope=PAIRWISE_CONSENT_SCOPE,
                            consent_version=str(
                                row["consent_version"]
                            ),
                            config_digest=str(row["config_digest"]),
                        )
                    )
                except PairwiseDispatchAuthorityError as exc:
                    raise PairwiseExecutionUnavailable(
                        "dispatch authorization is unavailable"
                    ) from exc
                now = _parse_datetime(
                    authorization.authorized_at,
                    "authorization.authorized_at",
                )
                self._require_lease(
                    row,
                    lease,
                    statuses=frozenset({"running"}),
                    now=now,
                )
                artifact = self._validate_execution_artifact_row(
                    row,
                    user_id=lease.user_id,
                    evaluation_id=lease.evaluation_id,
                )
                if (
                    config.digest != row["config_digest"]
                    or canonical_json(config.manifest)
                    != canonical_json(artifact.get("config_manifest"))
                    or canonical_json(artifact)
                    != canonical_json(lease.artifact)
                ):
                    raise PairwiseExecutionConflict(
                        "candidate call config or lease artifact changed"
                    )
                try:
                    revision = config.system_revisions[system_id]
                except KeyError as exc:
                    raise PairwiseExecutionConflict(
                        "candidate call is not in the authenticated plan"
                    ) from exc
                endpoint = config.endpoint_for_revision(revision)
                try:
                    definition = build_candidate_call_definition(
                        artifact,
                        prompt_id=prompt_id,
                        system_id=system_id,
                        endpoint=endpoint,
                    )
                except (TypeError, ValueError) as exc:
                    raise PairwiseExecutionConflict(
                        "candidate call could not be derived"
                    ) from exc

                binding_key_id = str(row["binding_key_id"])
                binding_key = self._binding_key(binding_key_id)
                call_identity = {
                    "evaluation_id": lease.evaluation_id,
                    "request_artifact_digest": str(
                        row["artifact_digest"]
                    ),
                    "coordinate": definition.coordinate,
                }
                coordinate_binding = _secret_binding(
                    binding_key,
                    namespace="call_coordinate",
                    user_id=lease.user_id,
                    value=call_identity,
                )
                call_id = (
                    "pairwise_call_" + coordinate_binding[:32]
                )
                existing = conn.execute(
                    """
                    SELECT call_id
                    FROM main.twin_eval_execution_call_checkpoints
                    WHERE user_id = ? AND evaluation_id = ?
                      AND (
                        call_id = ?
                        OR call_ordinal = ?
                        OR coordinate_binding = ?
                      )
                    LIMIT 1
                    """,
                    (
                        lease.user_id,
                        lease.evaluation_id,
                        call_id,
                        definition.ordinal,
                        coordinate_binding,
                    ),
                ).fetchone()
                if existing is not None:
                    raise PairwiseExecutionConflict(
                        "candidate call was already reserved"
                    )
                checkpoint_count = int(
                    conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM main.twin_eval_execution_call_checkpoints
                        WHERE user_id = ? AND evaluation_id = ?
                        """,
                        (lease.user_id, lease.evaluation_id),
                    ).fetchone()[0]
                )
                if (
                    int(row["provider_calls_reserved"])
                    != checkpoint_count
                    or checkpoint_count
                    >= definition.candidate_call_count
                ):
                    raise PairwiseExecutionConflict(
                        "candidate call budget is unavailable"
                    )

                lease_token_digest = str(row["lease_token_digest"])
                permit_digest = _secret_binding(
                    binding_key,
                    namespace="call_permit",
                    user_id=lease.user_id,
                    value={
                        "call_id": call_id,
                        "worker_id": lease.worker_id,
                        "lease_generation": lease.generation,
                        "permit": permit,
                    },
                )
                payload_binding = _secret_binding(
                    binding_key,
                    namespace="call_payload",
                    user_id=lease.user_id,
                    value=definition.adapter_input,
                )
                adapter_binding = _secret_binding(
                    binding_key,
                    namespace="call_adapter",
                    user_id=lease.user_id,
                    value=endpoint.manifest,
                )
                provider_idempotency_key = (
                    "pairwise_idem_"
                    + _secret_binding(
                        binding_key,
                        namespace="provider_idempotency",
                        user_id=lease.user_id,
                        value={
                            "call_id": call_id,
                            "endpoint": endpoint.manifest,
                        },
                    )[:48]
                    if endpoint.supports_idempotency
                    else None
                )
                call_deadline = min(
                    now + timedelta(seconds=endpoint.timeout_seconds),
                    _parse_datetime(
                        row["lease_expires_at"], "lease_expires_at"
                    ),
                    _parse_datetime(
                        row["execution_deadline_at"],
                        "execution_deadline_at",
                    ),
                    _parse_datetime(
                        row["request_expires_at"],
                        "request_expires_at",
                    ),
                )
                call_deadline_at = _datetime_to_text(call_deadline)
                checkpoint_identity = {
                    "schema_version": CALL_CHECKPOINT_SCHEMA,
                    "user_id": lease.user_id,
                    "evaluation_id": lease.evaluation_id,
                    "call_id": call_id,
                    "call_kind": "candidate",
                    "call_ordinal": definition.ordinal,
                    "coordinate": definition.coordinate,
                    "adapter_input": definition.adapter_input,
                    "adapter_revision": definition.adapter_revision,
                    "adapter_endpoint": endpoint.manifest,
                    "request_artifact_digest": str(
                        row["artifact_digest"]
                    ),
                    "config_digest": str(row["config_digest"]),
                    "authorization": {
                        "config_epoch": authorization.config_epoch,
                        "consent_revision": (
                            authorization.consent_revision
                        ),
                        "authorized_at": authorization.authorized_at,
                        "consent_expires_at": (
                            authorization.expires_at
                        ),
                    },
                    "lease": {
                        "worker_id": lease.worker_id,
                        "generation": lease.generation,
                        "token_digest": lease_token_digest,
                        "lease_expires_at": str(
                            row["lease_expires_at"]
                        ),
                        "execution_deadline_at": str(
                            row["execution_deadline_at"]
                        ),
                    },
                    "permit_digest": permit_digest,
                    "provider_idempotency_key": (
                        provider_idempotency_key
                    ),
                    "reserved_at": authorization.authorized_at,
                    "call_deadline_at": call_deadline_at,
                }
                checkpoint_binding = _secret_binding(
                    binding_key,
                    namespace="call_checkpoint",
                    user_id=lease.user_id,
                    value=checkpoint_identity,
                )
                plaintext = canonical_json(
                    {
                        **checkpoint_identity,
                        "checkpoint_binding": checkpoint_binding,
                    }
                ).encode("utf-8")
                ciphertext = self.cipher.encrypt_blob(
                    lease.user_id,
                    CALL_CHECKPOINT_ENCRYPTION_PURPOSE,
                    plaintext,
                )
                if not self.cipher.is_encrypted(ciphertext):
                    raise PairwiseExecutionUnavailable(
                        "candidate call checkpoint was not CXE1 encrypted"
                    )
                updated = conn.execute(
                    """
                    UPDATE main.twin_eval_execution_requests
                    SET provider_calls_reserved =
                          provider_calls_reserved + 1,
                        updated_at = ?
                    WHERE user_id = ? AND evaluation_id = ?
                      AND status = 'running'
                      AND provider_calls_reserved = ?
                      AND provider_calls_reserved < ?
                      AND lease_owner = ?
                      AND lease_generation = ?
                      AND lease_token_digest = ?
                      AND lease_expires_at > ?
                      AND execution_deadline_at > ?
                      AND request_expires_at > ?
                    """,
                    (
                        authorization.authorized_at,
                        lease.user_id,
                        lease.evaluation_id,
                        checkpoint_count,
                        definition.candidate_call_count,
                        lease.worker_id,
                        lease.generation,
                        lease_token_digest,
                        authorization.authorized_at,
                        authorization.authorized_at,
                        authorization.authorized_at,
                    ),
                )
                if updated.rowcount != 1:
                    raise PairwiseExecutionConflict(
                        "candidate call lost its authorization race"
                    )
                conn.execute(
                    """
                    INSERT INTO main.twin_eval_execution_call_checkpoints
                    (
                      user_id, evaluation_id, call_id, call_kind,
                      call_ordinal, binding_key_id, coordinate_binding,
                      payload_binding, adapter_binding,
                      checkpoint_binding, checkpoint_ciphertext,
                      request_artifact_digest, config_digest,
                      consent_config_epoch, consent_revision,
                      lease_generation, lease_token_digest,
                      permit_digest, idempotency_supported, state,
                      paid_attempt_count, reserved_at, call_deadline_at
                    )
                    VALUES (
                      ?, ?, ?, 'candidate', ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, 'reserved', 0, ?, ?
                    )
                    """,
                    (
                        lease.user_id,
                        lease.evaluation_id,
                        call_id,
                        definition.ordinal,
                        binding_key_id,
                        coordinate_binding,
                        payload_binding,
                        adapter_binding,
                        checkpoint_binding,
                        ciphertext,
                        row["artifact_digest"],
                        row["config_digest"],
                        authorization.config_epoch,
                        authorization.consent_revision,
                        lease.generation,
                        lease_token_digest,
                        permit_digest,
                        int(endpoint.supports_idempotency),
                        authorization.authorized_at,
                        call_deadline_at,
                    ),
                )
                capability_values = {
                    "user_id": lease.user_id,
                    "evaluation_id": lease.evaluation_id,
                    "call_id": call_id,
                    "call_ordinal": definition.ordinal,
                    "lease_generation": lease.generation,
                    "adapter_revision": definition.adapter_revision,
                    "authorized_at": authorization.authorized_at,
                    "call_deadline_at": call_deadline_at,
                    "permit": permit,
                    "adapter_input": _deep_freeze(
                        _json_snapshot(
                            definition.adapter_input,
                            "adapter_input",
                        )
                    ),
                    "provider_idempotency_key": (
                        provider_idempotency_key
                    ),
                }
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise PairwiseExecutionConflict(
                    "candidate call reservation conflicted"
                ) from exc
            except Exception:
                conn.rollback()
                raise
        if capability_values is None:
            raise PairwiseExecutionConflict(
                "candidate call reservation was not persisted"
            )
        return _PairwiseDispatchCapability(**capability_values)

    def _validate_candidate_checkpoint(
        self,
        checkpoint_row: sqlite3.Row,
        request_row: sqlite3.Row,
        lease: _PairwiseExecutionLease,
        capability: _PairwiseDispatchCapability,
        authorization: Any,
    ) -> tuple[Mapping[str, Any], str]:
        """Authenticate a reserved checkpoint and its one-shot capability."""

        if checkpoint_row["binding_key_id"] != request_row["binding_key_id"]:
            raise PairwiseExecutionConflict(
                "candidate checkpoint binding key changed"
            )
        binding_key = self._binding_key(
            str(checkpoint_row["binding_key_id"])
        )
        ciphertext = bytes(checkpoint_row["checkpoint_ciphertext"])
        if not self.cipher.is_encrypted(ciphertext):
            raise PairwiseExecutionConflict(
                "candidate checkpoint is not CXE1 encrypted"
            )
        try:
            plaintext = self.cipher.decrypt_blob(
                lease.user_id,
                CALL_CHECKPOINT_ENCRYPTION_PURPOSE,
                ciphertext,
            )
            checkpoint = _mapping(
                json.loads(plaintext.decode("utf-8")),
                "candidate checkpoint",
            )
        except Exception as exc:
            raise PairwiseExecutionConflict(
                "candidate checkpoint could not be authenticated"
            ) from exc
        expected_fields = {
            "schema_version",
            "user_id",
            "evaluation_id",
            "call_id",
            "call_kind",
            "call_ordinal",
            "coordinate",
            "adapter_input",
            "adapter_revision",
            "adapter_endpoint",
            "request_artifact_digest",
            "config_digest",
            "authorization",
            "lease",
            "permit_digest",
            "provider_idempotency_key",
            "reserved_at",
            "call_deadline_at",
            "checkpoint_binding",
        }
        if set(checkpoint) != expected_fields:
            raise PairwiseExecutionConflict(
                "candidate checkpoint has an invalid shape"
            )
        checkpoint_authorization = _mapping(
            checkpoint.get("authorization"),
            "candidate checkpoint authorization",
        )
        checkpoint_lease = _mapping(
            checkpoint.get("lease"),
            "candidate checkpoint lease",
        )
        coordinate = _mapping(
            checkpoint.get("coordinate"),
            "candidate checkpoint coordinate",
        )
        adapter_input = _mapping(
            checkpoint.get("adapter_input"),
            "candidate checkpoint adapter input",
        )
        adapter_revision = str(checkpoint.get("adapter_revision"))
        try:
            endpoint = self._config.endpoint_for_revision(
                adapter_revision
            )
        except PairwiseExecutionUnavailable as exc:
            raise PairwiseExecutionConflict(
                "candidate checkpoint adapter is unavailable"
            ) from exc
        endpoint_manifest = endpoint.manifest
        call_identity = {
            "evaluation_id": lease.evaluation_id,
            "request_artifact_digest": str(
                request_row["artifact_digest"]
            ),
            "coordinate": coordinate,
        }
        coordinate_binding = _secret_binding(
            binding_key,
            namespace="call_coordinate",
            user_id=lease.user_id,
            value=call_identity,
        )
        expected_call_id = "pairwise_call_" + coordinate_binding[:32]
        payload_binding = _secret_binding(
            binding_key,
            namespace="call_payload",
            user_id=lease.user_id,
            value=adapter_input,
        )
        adapter_binding = _secret_binding(
            binding_key,
            namespace="call_adapter",
            user_id=lease.user_id,
            value=endpoint_manifest,
        )
        permit_digest = _secret_binding(
            binding_key,
            namespace="call_permit",
            user_id=lease.user_id,
            value={
                "call_id": capability.call_id,
                "worker_id": lease.worker_id,
                "lease_generation": lease.generation,
                "permit": capability.permit,
            },
        )
        checkpoint_identity = {
            key: checkpoint[key]
            for key in checkpoint
            if key != "checkpoint_binding"
        }
        checkpoint_binding = _secret_binding(
            binding_key,
            namespace="call_checkpoint",
            user_id=lease.user_id,
            value=checkpoint_identity,
        )
        expected_provider_key = (
            "pairwise_idem_"
            + _secret_binding(
                binding_key,
                namespace="provider_idempotency",
                user_id=lease.user_id,
                value={
                    "call_id": capability.call_id,
                    "endpoint": endpoint_manifest,
                },
            )[:48]
            if endpoint.supports_idempotency
            else None
        )
        string_bindings = (
            (checkpoint_row["coordinate_binding"], coordinate_binding),
            (checkpoint_row["payload_binding"], payload_binding),
            (checkpoint_row["adapter_binding"], adapter_binding),
            (checkpoint_row["permit_digest"], permit_digest),
            (checkpoint_row["checkpoint_binding"], checkpoint_binding),
            (checkpoint.get("permit_digest"), permit_digest),
            (checkpoint.get("checkpoint_binding"), checkpoint_binding),
        )
        if any(
            not hmac.compare_digest(str(actual), expected)
            for actual, expected in string_bindings
        ):
            raise PairwiseExecutionConflict(
                "candidate checkpoint binding is invalid"
            )
        if (
            checkpoint.get("schema_version") != CALL_CHECKPOINT_SCHEMA
            or checkpoint.get("user_id") != lease.user_id
            or checkpoint.get("evaluation_id") != lease.evaluation_id
            or checkpoint.get("call_id") != expected_call_id
            or checkpoint.get("call_kind") != "candidate"
            or int(checkpoint.get("call_ordinal", -1))
            != int(checkpoint_row["call_ordinal"])
            or checkpoint.get("adapter_endpoint") != endpoint_manifest
            or checkpoint.get("request_artifact_digest")
            != request_row["artifact_digest"]
            or checkpoint.get("config_digest")
            != request_row["config_digest"]
            or checkpoint_authorization.get("config_epoch")
            != int(checkpoint_row["consent_config_epoch"])
            or checkpoint_authorization.get("consent_revision")
            != int(checkpoint_row["consent_revision"])
            or authorization.config_epoch
            != int(checkpoint_row["consent_config_epoch"])
            or authorization.consent_revision
            != int(checkpoint_row["consent_revision"])
            or checkpoint_lease.get("worker_id") != lease.worker_id
            or checkpoint_lease.get("generation") != lease.generation
            or checkpoint_lease.get("token_digest")
            != request_row["lease_token_digest"]
            or checkpoint_row["lease_generation"] != lease.generation
            or checkpoint_row["lease_token_digest"]
            != request_row["lease_token_digest"]
            or checkpoint_row["request_artifact_digest"]
            != request_row["artifact_digest"]
            or checkpoint_row["config_digest"]
            != request_row["config_digest"]
            or checkpoint_row["call_id"] != capability.call_id
            or checkpoint_row["call_id"] != expected_call_id
            or checkpoint_row["call_kind"] != "candidate"
            or checkpoint_row["state"] != "reserved"
            or int(checkpoint_row["paid_attempt_count"]) != 0
            or checkpoint_row["consumed_at"] is not None
            or checkpoint_row["consume_binding"] is not None
            or checkpoint_row["outcome_unknown_at"] is not None
            or capability.user_id != lease.user_id
            or capability.evaluation_id != lease.evaluation_id
            or capability.call_id != expected_call_id
            or capability.call_ordinal
            != int(checkpoint_row["call_ordinal"])
            or capability.lease_generation != lease.generation
            or capability.adapter_revision != adapter_revision
            or capability.authorized_at
            != checkpoint_authorization.get("authorized_at")
            or capability.call_deadline_at
            != checkpoint.get("call_deadline_at")
            or canonical_json(capability.adapter_input)
            != canonical_json(adapter_input)
            or capability.provider_idempotency_key
            != checkpoint.get("provider_idempotency_key")
            or capability.provider_idempotency_key
            != expected_provider_key
            or bool(checkpoint_row["idempotency_supported"])
            != endpoint.supports_idempotency
            or checkpoint.get("reserved_at")
            != checkpoint_authorization.get("authorized_at")
            or checkpoint_row["reserved_at"]
            != checkpoint.get("reserved_at")
            or checkpoint_row["call_deadline_at"]
            != checkpoint.get("call_deadline_at")
        ):
            raise PairwiseExecutionConflict(
                "candidate checkpoint does not match its capability"
            )
        return adapter_input, permit_digest

    def _consume_candidate_capability(
        self,
        lease: _PairwiseExecutionLease,
        capability: _PairwiseDispatchCapability,
    ) -> _ConsumedCandidateDispatch:
        """Irreversibly cross the one-shot pre-I/O dispatch boundary."""

        if type(capability) is not _PairwiseDispatchCapability:
            raise PairwiseExecutionConflict(
                "candidate dispatch capability is invalid"
            )
        consumed_values: dict[str, Any] | None = None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                request_row = conn.execute(
                    """
                    SELECT * FROM main.twin_eval_execution_requests
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    (lease.user_id, lease.evaluation_id),
                ).fetchone()
                if request_row is None:
                    raise PairwiseExecutionNotFound(
                        "pairwise execution was not found"
                    )
                checkpoint_row = conn.execute(
                    """
                    SELECT *
                    FROM main.twin_eval_execution_call_checkpoints
                    WHERE user_id = ? AND evaluation_id = ?
                      AND call_id = ?
                    """,
                    (
                        lease.user_id,
                        lease.evaluation_id,
                        capability.call_id,
                    ),
                ).fetchone()
                if checkpoint_row is None:
                    raise PairwiseExecutionConflict(
                        "candidate checkpoint is unavailable"
                    )
                if (
                    checkpoint_row["state"] != "reserved"
                    or int(checkpoint_row["paid_attempt_count"]) != 0
                    or checkpoint_row["consumed_at"] is not None
                ):
                    raise PairwiseExecutionConflict(
                        "candidate capability was already consumed"
                    )
                try:
                    authorization = (
                        self._dispatch_authority.require_authorized_tx(
                            conn,
                            user_id=lease.user_id,
                            scope=PAIRWISE_CONSENT_SCOPE,
                            consent_version=str(
                                request_row["consent_version"]
                            ),
                            config_digest=str(
                                request_row["config_digest"]
                            ),
                        )
                    )
                except PairwiseDispatchAuthorityError as exc:
                    raise PairwiseExecutionUnavailable(
                        "dispatch authorization is unavailable"
                    ) from exc
                now = _parse_datetime(
                    authorization.authorized_at,
                    "authorization.authorized_at",
                )
                self._require_lease(
                    request_row,
                    lease,
                    statuses=frozenset({"running"}),
                    now=now,
                )
                call_deadline = _parse_datetime(
                    checkpoint_row["call_deadline_at"],
                    "call_deadline_at",
                )
                if not now < call_deadline:
                    raise PairwiseExecutionConflict(
                        "candidate dispatch deadline expired"
                    )
                artifact = self._validate_execution_artifact_row(
                    request_row,
                    user_id=lease.user_id,
                    evaluation_id=lease.evaluation_id,
                )
                if (
                    self._config.digest != request_row["config_digest"]
                    or canonical_json(self._config.manifest)
                    != canonical_json(artifact.get("config_manifest"))
                    or canonical_json(artifact)
                    != canonical_json(lease.artifact)
                ):
                    raise PairwiseExecutionConflict(
                        "candidate dispatch config or artifact changed"
                    )
                adapter_input, permit_digest = (
                    self._validate_candidate_checkpoint(
                        checkpoint_row,
                        request_row,
                        lease,
                        capability,
                        authorization,
                    )
                )
                consume_binding = _secret_binding(
                    self._binding_key(
                        str(checkpoint_row["binding_key_id"])
                    ),
                    namespace="call_consume",
                    user_id=lease.user_id,
                    value={
                        "call_id": capability.call_id,
                        "checkpoint_binding": str(
                            checkpoint_row["checkpoint_binding"]
                        ),
                        "permit_digest": permit_digest,
                        "lease_generation": lease.generation,
                        "lease_token_digest": str(
                            request_row["lease_token_digest"]
                        ),
                        "consumed_at": authorization.authorized_at,
                    },
                )
                updated = conn.execute(
                    """
                    UPDATE main.twin_eval_execution_call_checkpoints
                    SET state = 'dispatching',
                        paid_attempt_count = 1,
                        consumed_at = ?,
                        consume_binding = ?
                    WHERE user_id = ? AND evaluation_id = ?
                      AND call_id = ?
                      AND state = 'reserved'
                      AND paid_attempt_count = 0
                      AND consumed_at IS NULL
                      AND consume_binding IS NULL
                      AND outcome_unknown_at IS NULL
                      AND checkpoint_binding = ?
                      AND permit_digest = ?
                      AND lease_generation = ?
                      AND lease_token_digest = ?
                      AND consent_config_epoch = ?
                      AND consent_revision = ?
                      AND call_deadline_at > ?
                    """,
                    (
                        authorization.authorized_at,
                        consume_binding,
                        lease.user_id,
                        lease.evaluation_id,
                        capability.call_id,
                        checkpoint_row["checkpoint_binding"],
                        permit_digest,
                        lease.generation,
                        request_row["lease_token_digest"],
                        authorization.config_epoch,
                        authorization.consent_revision,
                        authorization.authorized_at,
                    ),
                )
                if updated.rowcount != 1:
                    raise PairwiseExecutionConflict(
                        "candidate capability was already consumed"
                    )
                tombstone = conn.execute(
                    """
                    UPDATE main.twin_eval_execution_requests
                    SET provider_calls_dispatched =
                          provider_calls_dispatched + 1,
                        updated_at = ?
                    WHERE user_id = ? AND evaluation_id = ?
                      AND status = 'running'
                      AND provider_calls_dispatched <
                          provider_calls_reserved
                      AND lease_owner = ?
                      AND lease_generation = ?
                      AND lease_token_digest = ?
                    """,
                    (
                        authorization.authorized_at,
                        lease.user_id,
                        lease.evaluation_id,
                        lease.worker_id,
                        lease.generation,
                        request_row["lease_token_digest"],
                    ),
                )
                if tombstone.rowcount != 1:
                    raise PairwiseExecutionConflict(
                        "candidate dispatch tombstone conflicted"
                    )
                consumed_values = {
                    "user_id": lease.user_id,
                    "evaluation_id": lease.evaluation_id,
                    "call_id": capability.call_id,
                    "call_ordinal": capability.call_ordinal,
                    "adapter_revision": capability.adapter_revision,
                    "consumed_at": authorization.authorized_at,
                    "call_deadline_at": capability.call_deadline_at,
                    "transport_input": _deep_freeze(
                        _json_snapshot(
                            adapter_input,
                            "candidate transport input",
                        )
                    ),
                    "provider_idempotency_key": (
                        capability.provider_idempotency_key
                    ),
                }
                conn.commit()
                capability._burn_after_commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise PairwiseExecutionConflict(
                    "candidate capability consumption conflicted"
                ) from exc
            except Exception:
                conn.rollback()
                raise
        if consumed_values is None:
            raise PairwiseExecutionConflict(
                "candidate capability was not consumed"
            )
        return _ConsumedCandidateDispatch(**consumed_values)

    @staticmethod
    def _fixture_result_manifest(
        lease: _PairwiseExecutionLease,
    ) -> dict[str, Any]:
        artifact = lease.artifact
        request = _mapping(
            artifact.get("request"), "lease.artifact.request"
        )
        return {
            "schema_version": EXECUTION_RESULT_SCHEMA,
            "evaluation_id": lease.evaluation_id,
            "request_artifact_digest": artifact.get(
                "artifact_digest"
            ),
            "execution_config_digest": artifact.get("config_digest"),
            "profile_bundle_digest": request.get(
                "profile_bundle_digest"
            ),
            "executor_id": "deterministic_fixture_v1",
        }

    def _build_fixture_report(
        self,
        lease: _PairwiseExecutionLease,
    ) -> EvaluationReport:
        artifact = _mapping(
            json.loads(canonical_json(lease.artifact)),
            "lease.artifact",
        )
        request = _mapping(
            artifact.get("request"), "lease.artifact.request"
        )
        strategy = _mapping(
            request.get("strategy"), "request.strategy"
        )
        if (
            set(strategy) != {"type", "repetitions", "shuffle"}
            or strategy.get("type") != "repeated_swapped"
            or isinstance(strategy.get("repetitions"), bool)
            or not isinstance(strategy.get("repetitions"), int)
            or not isinstance(strategy.get("shuffle"), bool)
        ):
            raise PairwiseExecutionUnavailable(
                "deterministic fixture does not support this strategy"
            )
        raw_prompts = request.get("prompts")
        if not isinstance(raw_prompts, list):
            raise PairwiseExecutionError(
                "pairwise execution fixture prompts are malformed"
            )
        prompts = tuple(
            EvaluationPrompt(
                prompt_id=_required_text(
                    _mapping(item, "request.prompt").get("prompt_id"),
                    "request.prompt.prompt_id",
                ),
                text=_required_text(
                    _mapping(item, "request.prompt").get("text"),
                    "request.prompt.text",
                    maximum=50_000,
                ),
                metadata=_mapping(
                    _mapping(item, "request.prompt").get(
                        "metadata", {}
                    ),
                    "request.prompt.metadata",
                ),
            )
            for item in raw_prompts
        )
        profile_bundle = parse_cortex_profile_bundle(
            artifact.get("profile_bundle")
        )
        systems = tuple(sorted(request.get("system_ids", ())))
        if len(systems) < 2:
            raise PairwiseExecutionError(
                "pairwise execution fixture systems are malformed"
            )
        winner = systems[0]
        runner = PairwiseEvaluationRunner(
            tuple(
                DeterministicGenerator(
                    system_id,
                    (
                        lambda prompt, profile, seed, value=system_id:
                        f"fixture-{value}"
                    ),
                )
                for system_id in systems
            ),
            OracleJudge(
                {
                    prompt.prompt_id: winner
                    for prompt in prompts
                }
            ),
            RepeatedSwappedStrategy(
                repetitions=int(strategy["repetitions"]),
                shuffle=bool(strategy["shuffle"]),
            ),
            BradleyTerryRanker(),
            metadata={
                "execution_result_manifest": (
                    self._fixture_result_manifest(lease)
                )
            },
            citation_policy=profile_bundle.citation_policy,
            blind_judge_inputs=False,
        )
        return runner.run(
            profile_bundle.profile,
            prompts,
            seed=request.get("seed"),
        )

    def _validate_completion_report(
        self,
        lease: _PairwiseExecutionLease,
        report: EvaluationReport,
    ) -> tuple[CortexHeldOutProfileBundle, Mapping[str, Any]]:
        if not isinstance(report, EvaluationReport):
            raise PairwiseExecutionError(
                "pairwise execution result must be an evaluation report"
            )
        artifact = _mapping(
            json.loads(canonical_json(lease.artifact)),
            "lease.artifact",
        )
        request = _mapping(
            artifact.get("request"), "lease.artifact.request"
        )
        profile_bundle_value = _mapping(
            artifact.get("profile_bundle"),
            "lease.artifact.profile_bundle",
        )
        profile_bundle = parse_cortex_profile_bundle(
            profile_bundle_value
        )
        expected_report = self._build_fixture_report(lease)
        if report != expected_report:
            raise PairwiseExecutionConflict(
                "pairwise execution report does not match its request"
            )
        return profile_bundle, artifact

    def _completion_binding(
        self,
        row: sqlite3.Row,
        lease: _PairwiseExecutionLease,
        report: EvaluationReport,
    ) -> str:
        return _secret_binding(
            self._binding_key(str(row["binding_key_id"])),
            namespace="completion",
            user_id=lease.user_id,
            value={
                "evaluation_id": lease.evaluation_id,
                "worker_id": lease.worker_id,
                "generation": lease.generation,
                "token": lease.token,
                "run_id": report.run_id,
                "artifact_digest": report.artifact_digest,
            },
        )

    def _completed_retry_status(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        lease: _PairwiseExecutionLease,
        report: EvaluationReport,
    ) -> PairwiseExecutionStatus | None:
        if row["status"] != "succeeded":
            return None
        completion_binding = self._completion_binding(
            row, lease, report
        )
        stored_run = conn.execute(
            """
            SELECT artifact_digest
            FROM twin_eval_runs
            WHERE user_id = ? AND run_id = ?
            """,
            (lease.user_id, report.run_id),
        ).fetchone()
        if (
            row["result_run_id"] == report.run_id
            and row["result_artifact_digest"] == report.artifact_digest
            and row["completion_binding"] is not None
            and hmac.compare_digest(
                str(row["completion_binding"]), completion_binding
            )
            and stored_run is not None
            and stored_run["artifact_digest"] == report.artifact_digest
        ):
            return self._status(row)
        raise PairwiseExecutionConflict(
            "pairwise execution already has a different result"
        )

    def _require_persisted_report(
        self,
        user_id: str,
        report: EvaluationReport,
    ) -> None:
        try:
            persisted = self._report_repository.load_report(
                user_id, report.run_id
            )
        except Exception as exc:
            raise PairwiseExecutionConflict(
                "persisted pairwise execution result is invalid"
            ) from exc
        if persisted != report:
            raise PairwiseExecutionConflict(
                "persisted pairwise execution result is invalid"
            )

    def complete_with_report(
        self,
        lease: _PairwiseExecutionLease,
        report: EvaluationReport,
        *,
        now_utc: str | None = None,
    ) -> PairwiseExecutionStatus:
        """Atomically persist one fixture report and consume its live lease."""

        profile_bundle, execution_artifact = (
            self._validate_completion_report(lease, report)
        )
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT * FROM twin_eval_execution_requests
                WHERE user_id = ? AND evaluation_id = ?
                """,
                (lease.user_id, lease.evaluation_id),
            ).fetchone()
            if existing is None:
                raise PairwiseExecutionNotFound(
                    "pairwise execution was not found"
                )
            completed_retry = self._completed_retry_status(
                conn, existing, lease, report
            )
        if completed_retry is not None:
            self._require_persisted_report(lease.user_id, report)
            return completed_retry
        prepared = self._report_repository._prepare_report_write(
            lease.user_id,
            report,
            profile_bundle=profile_bundle,
            evidence_expires_at=str(
                execution_artifact["request_expires_at"]
            ),
        )
        now, timestamp = self._lease_time(now_utc)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    SELECT * FROM twin_eval_execution_requests
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    (lease.user_id, lease.evaluation_id),
                ).fetchone()
                if row is None:
                    raise PairwiseExecutionNotFound(
                        "pairwise execution was not found"
                    )
                completed_retry = self._completed_retry_status(
                    conn, row, lease, report
                )
                if completed_retry is not None:
                    conn.commit()
                    self._require_persisted_report(
                        lease.user_id, report
                    )
                    return completed_retry
                completion_binding = self._completion_binding(
                    row, lease, report
                )
                self._require_lease(
                    row,
                    lease,
                    statuses=frozenset({"running"}),
                    now=now,
                )
                authenticated_artifact = (
                    self._validate_execution_artifact_row(
                        row,
                        user_id=lease.user_id,
                        evaluation_id=lease.evaluation_id,
                    )
                )
                if canonical_json(authenticated_artifact) != canonical_json(
                    execution_artifact
                ):
                    raise PairwiseExecutionConflict(
                        "pairwise execution lease artifact is invalid"
                    )
                if (
                    row["artifact_digest"]
                    != execution_artifact.get("artifact_digest")
                    or row["config_digest"]
                    != execution_artifact.get("config_digest")
                    or row["receipt_id"]
                    != _mapping(
                        execution_artifact.get("receipt"),
                        "lease.artifact.receipt",
                    ).get("receipt_id")
                    or row["request_expires_at"]
                    != execution_artifact.get("request_expires_at")
                ):
                    raise PairwiseExecutionConflict(
                        "pairwise execution result binding is invalid"
                    )
                if conn.execute(
                    """
                    SELECT 1
                    FROM main.twin_eval_execution_call_checkpoints
                    WHERE user_id = ? AND evaluation_id = ?
                      AND state IN (
                        'reserved', 'dispatching', 'outcome_unknown'
                      )
                    LIMIT 1
                    """,
                    (lease.user_id, lease.evaluation_id),
                ).fetchone() is not None:
                    raise PairwiseExecutionConflict(
                        "open candidate calls require recorded outcomes"
                    )
                self._report_repository._save_report_tx(
                    conn, prepared, require_new=True
                )
                updated = conn.execute(
                    f"""
                    UPDATE twin_eval_execution_requests
                    SET status = 'succeeded',
                        result_run_id = ?,
                        result_artifact_digest = ?,
                        completion_binding = ?,
                        error_code = NULL,
                        completed_at = ?,
                        updated_at = ?,
                        {self._clear_lease_sql()}
                    WHERE user_id = ? AND evaluation_id = ?
                      AND status = 'running'
                      AND result_run_id IS NULL
                      AND lease_owner = ?
                      AND lease_generation = ?
                      AND lease_token_digest = ?
                      AND lease_expires_at > ?
                      AND execution_deadline_at > ?
                      AND request_expires_at > ?
                    """,
                    (
                        report.run_id,
                        report.artifact_digest,
                        completion_binding,
                        timestamp,
                        timestamp,
                        lease.user_id,
                        lease.evaluation_id,
                        lease.worker_id,
                        lease.generation,
                        row["lease_token_digest"],
                        timestamp,
                        timestamp,
                        timestamp,
                    ),
                )
                if updated.rowcount != 1:
                    raise PairwiseExecutionConflict(
                        "pairwise execution lease is no longer active"
                    )
                row = conn.execute(
                    """
                    SELECT * FROM twin_eval_execution_requests
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    (lease.user_id, lease.evaluation_id),
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self._status(row)

    def fail(
        self,
        lease: _PairwiseExecutionLease,
        *,
        error_code: str,
        now_utc: str | None = None,
    ) -> PairwiseExecutionStatus:
        if error_code not in _WORKER_FAILURE_CODES:
            raise PairwiseExecutionError(
                "pairwise execution failure code is not allowlisted"
            )
        now, timestamp = self._lease_time(now_utc)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    SELECT * FROM twin_eval_execution_requests
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    (lease.user_id, lease.evaluation_id),
                ).fetchone()
                self._require_lease(
                    row,
                    lease,
                    statuses=frozenset({"running"}),
                    now=now,
                )
                ambiguous = self._mark_dispatching_unknown_tx(
                    conn,
                    user_id=lease.user_id,
                    evaluation_id=lease.evaluation_id,
                    timestamp=timestamp,
                )
                updated = conn.execute(
                    f"""
                    UPDATE twin_eval_execution_requests
                    SET status = 'failed', error_code = ?,
                        completed_at = ?, updated_at = ?,
                        {self._clear_lease_sql()}
                    WHERE user_id = ? AND evaluation_id = ?
                      AND status = 'running'
                      AND lease_generation = ?
                      AND lease_token_digest = ?
                    """,
                    (
                        (
                            "remote_outcome_unknown"
                            if ambiguous
                            else error_code
                        ),
                        timestamp,
                        timestamp,
                        lease.user_id,
                        lease.evaluation_id,
                        lease.generation,
                        row["lease_token_digest"],
                    ),
                )
                if updated.rowcount != 1:
                    raise PairwiseExecutionConflict(
                        "pairwise execution lease is no longer active"
                    )
                row = conn.execute(
                    """
                    SELECT * FROM twin_eval_execution_requests
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    (lease.user_id, lease.evaluation_id),
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self._status(row)

    def acknowledge_cancel(
        self,
        lease: _PairwiseExecutionLease,
        *,
        now_utc: str | None = None,
    ) -> PairwiseExecutionStatus:
        now, timestamp = self._lease_time(now_utc)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    SELECT * FROM twin_eval_execution_requests
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    (lease.user_id, lease.evaluation_id),
                ).fetchone()
                self._require_lease(
                    row,
                    lease,
                    statuses=frozenset({"cancel_requested"}),
                    now=now,
                )
                ambiguous = self._mark_dispatching_unknown_tx(
                    conn,
                    user_id=lease.user_id,
                    evaluation_id=lease.evaluation_id,
                    timestamp=timestamp,
                )
                updated = conn.execute(
                    f"""
                    UPDATE twin_eval_execution_requests
                    SET status = 'cancelled', completed_at = ?,
                        updated_at = ?, error_code = ?,
                        {self._clear_lease_sql()}
                    WHERE user_id = ? AND evaluation_id = ?
                      AND status = 'cancel_requested'
                      AND lease_generation = ?
                      AND lease_token_digest = ?
                    """,
                    (
                        timestamp,
                        timestamp,
                        (
                            "remote_outcome_unknown"
                            if ambiguous
                            else None
                        ),
                        lease.user_id,
                        lease.evaluation_id,
                        lease.generation,
                        row["lease_token_digest"],
                    ),
                )
                if updated.rowcount != 1:
                    raise PairwiseExecutionConflict(
                        "pairwise execution lease is no longer active"
                    )
                row = conn.execute(
                    """
                    SELECT * FROM twin_eval_execution_requests
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    (lease.user_id, lease.evaluation_id),
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self._status(row)

    def reap_expired_leases(
        self,
        user_id: str,
        *,
        now_utc: str | None = None,
    ) -> tuple[str, ...]:
        user_id = _required_text(user_id, "user_id", maximum=500)
        _now, timestamp = self._lease_time(now_utc)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                evaluation_ids = self._reap_expired_tx(
                    conn, user_id=user_id, timestamp=timestamp
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return evaluation_ids

    def cancel(
        self, user_id: str, evaluation_id: str
    ) -> PairwiseExecutionStatus:
        user_id = _required_text(user_id, "user_id", maximum=500)
        evaluation_id = _required_text(
            evaluation_id, "evaluation_id", maximum=200
        )
        timestamp = _utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    SELECT * FROM twin_eval_execution_requests
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    (user_id, evaluation_id),
                ).fetchone()
                if row is None:
                    raise PairwiseExecutionNotFound(
                        "pairwise execution was not found"
                    )
                status = str(row["status"])
                if status in {"prepared", "queued"}:
                    next_status = "cancelled"
                elif status == "running":
                    next_status = "cancel_requested"
                else:
                    next_status = status
                if next_status != status:
                    terminal_fields = (
                        f""",
                            completed_at = ?,
                            {self._clear_lease_sql()}
                        """
                        if next_status == "cancelled"
                        else ""
                    )
                    parameters: list[Any] = [
                        next_status,
                        timestamp,
                        timestamp,
                    ]
                    if next_status == "cancelled":
                        parameters.append(timestamp)
                    parameters.extend(
                        [user_id, evaluation_id, status]
                    )
                    conn.execute(
                        f"""
                        UPDATE twin_eval_execution_requests
                        SET status = ?, cancel_requested_at = ?,
                            updated_at = ?
                            {terminal_fields}
                        WHERE user_id = ? AND evaluation_id = ?
                          AND status = ?
                        """,
                        parameters,
                    )
                row = conn.execute(
                    """
                    SELECT * FROM twin_eval_execution_requests
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    (user_id, evaluation_id),
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self._status(row)

    def delete_request_content(
        self, user_id: str, evaluation_id: str
    ) -> PairwiseExecutionStatus:
        user_id = _required_text(user_id, "user_id", maximum=500)
        evaluation_id = _required_text(
            evaluation_id, "evaluation_id", maximum=200
        )
        timestamp = _utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    SELECT * FROM twin_eval_execution_requests
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    (user_id, evaluation_id),
                ).fetchone()
                if row is None:
                    raise PairwiseExecutionNotFound(
                        "pairwise execution was not found"
                    )
                if row["status"] in {"running", "cancel_requested"}:
                    raise PairwiseExecutionConflict(
                        "running execution content cannot be deleted"
                    )
                next_status = (
                    "cancelled"
                    if row["status"] in {"prepared", "queued"}
                    else row["status"]
                )
                conn.execute(
                    """
                    DELETE FROM twin_eval_execution_call_checkpoints
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    (user_id, evaluation_id),
                )
                conn.execute(
                    f"""
                    UPDATE twin_eval_execution_requests
                    SET request_ciphertext = NULL,
                        content_deleted_at = COALESCE(
                          content_deleted_at, ?
                        ),
                        status = ?, updated_at = ?,
                        completed_at = CASE
                          WHEN ? = 'cancelled'
                          THEN COALESCE(completed_at, ?)
                          ELSE completed_at
                        END,
                        {self._clear_lease_sql()}
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    (
                        timestamp,
                        next_status,
                        timestamp,
                        next_status,
                        timestamp,
                        user_id,
                        evaluation_id,
                    ),
                )
                row = conn.execute(
                    """
                    SELECT * FROM twin_eval_execution_requests
                    WHERE user_id = ? AND evaluation_id = ?
                    """,
                    (user_id, evaluation_id),
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self._status(row)

    def purge_expired(
        self,
        *,
        now_utc: str | None = None,
        limit: int = 1000,
    ) -> tuple[str, ...]:
        cutoff = _datetime_to_text(
            _parse_datetime(now_utc, "now_utc")
            if now_utc is not None
            else datetime.now(timezone.utc)
        )
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise PairwiseExecutionError("limit must be an integer")
        limit = max(1, min(limit, 10_000))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    """
                    SELECT user_id, evaluation_id, status,
                           remote_outcome_unknown
                    FROM twin_eval_execution_requests
                    WHERE request_ciphertext IS NOT NULL
                      AND request_expires_at <= ?
                    ORDER BY request_expires_at, user_id, evaluation_id
                    LIMIT ?
                    """,
                    (cutoff, limit),
                ).fetchall()
                for row in rows:
                    next_status = (
                        row["status"]
                        if row["status"] in _TERMINAL_STATUSES
                        else "cancelled"
                    )
                    ambiguous = bool(
                        row["remote_outcome_unknown"]
                    ) or self._mark_dispatching_unknown_tx(
                        conn,
                        user_id=str(row["user_id"]),
                        evaluation_id=str(row["evaluation_id"]),
                        timestamp=cutoff,
                    )
                    conn.execute(
                        """
                        DELETE FROM twin_eval_execution_call_checkpoints
                        WHERE user_id = ? AND evaluation_id = ?
                        """,
                        (row["user_id"], row["evaluation_id"]),
                    )
                    conn.execute(
                        f"""
                        UPDATE twin_eval_execution_requests
                        SET request_ciphertext = NULL,
                            content_deleted_at = ?,
                            status = ?, updated_at = ?,
                            remote_outcome_unknown = CASE
                              WHEN ? THEN 1
                              ELSE remote_outcome_unknown
                            END,
                            completed_at = CASE
                              WHEN ? = 'cancelled'
                              THEN COALESCE(completed_at, ?)
                              ELSE completed_at
                            END,
                            error_code = CASE
                              WHEN ?
                              THEN 'remote_outcome_unknown'
                              WHEN ? = 'failed'
                                   AND error_code IS NULL
                              THEN 'worker_lease_expired'
                              ELSE error_code
                            END,
                            {self._clear_lease_sql()}
                        WHERE user_id = ? AND evaluation_id = ?
                          AND request_ciphertext IS NOT NULL
                        """,
                        (
                            cutoff,
                            next_status,
                            cutoff,
                            int(ambiguous),
                            next_status,
                            cutoff,
                            int(ambiguous),
                            next_status,
                            row["user_id"],
                            row["evaluation_id"],
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return tuple(str(row["evaluation_id"]) for row in rows)


class PairwiseExecutionService:
    """Server-owned profile, consent, admission, and encrypted persistence."""

    _SPEC_FIELDS = frozenset(
        {
            "as_of",
            "prompts",
            "system_ids",
            "strategy",
            "seed",
            "budget",
        }
    )

    def __init__(
        self,
        db_path: Path,
        *,
        cipher: Any,
        profile_builder: PairwiseProfileBuilder,
        consent_authority: PairwiseConsentAuthority,
        policy: PairwiseAdmissionPolicy,
        signing_key: str,
        binding_keys: Mapping[str, str],
        active_binding_key_id: str,
        config: TrustedPairwiseExecutionConfig,
    ) -> None:
        self.profile_builder = profile_builder
        self.consent_authority = consent_authority
        self.policy = policy
        self.signing_key = signing_key
        self.config = config
        self._repository = _PairwiseExecutionRepository(
            db_path,
            cipher=cipher,
            binding_keys=binding_keys,
            active_binding_key_id=active_binding_key_id,
            config=config,
        )

    def _build_request(
        self,
        user_id: str,
        spec: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        spec = _mapping(
            _json_snapshot(_mapping(spec, "spec"), "spec"),
            "spec",
        )
        unknown = sorted(set(spec) - self._SPEC_FIELDS)
        if unknown:
            raise PairwiseExecutionError(
                "spec contains unknown fields: " + ", ".join(unknown)
            )
        as_of = _required_text(
            spec.get("as_of"), "spec.as_of", maximum=100
        )
        raw_prompts = spec.get("prompts")
        if not isinstance(raw_prompts, list) or not raw_prompts:
            raise PairwiseExecutionError(
                "spec.prompts must be a non-empty array"
            )
        if len(raw_prompts) > 100:
            raise PairwiseExecutionError(
                "spec.prompts must contain at most 100 items"
            )
        prompts: list[EvaluationPrompt] = []
        for index, raw_prompt in enumerate(raw_prompts):
            prompt = _mapping(
                raw_prompt, f"spec.prompts[{index}]"
            )
            unknown_prompt = sorted(
                set(prompt) - {"prompt_id", "text", "metadata"}
            )
            if unknown_prompt:
                raise PairwiseExecutionError(
                    f"spec.prompts[{index}] contains unknown fields: "
                    + ", ".join(unknown_prompt)
                )
            prompts.append(
                EvaluationPrompt(
                    prompt_id=_required_text(
                        prompt.get("prompt_id"),
                        f"spec.prompts[{index}].prompt_id",
                    ),
                    text=_required_text(
                        prompt.get("text"),
                        f"spec.prompts[{index}].text",
                        maximum=50_000,
                    ),
                    metadata=_mapping(
                        prompt.get("metadata", {}),
                        f"spec.prompts[{index}].metadata",
                    ),
                )
            )
        system_ids = self.config.validate_system_ids(
            spec.get("system_ids")
        )
        bundle = self.profile_builder.build(
            user_id,
            tuple(prompts),
            as_of=as_of,
        )
        if not isinstance(bundle, CortexHeldOutProfileBundle):
            raise PairwiseExecutionUnavailable(
                "Cortex profile builder returned an invalid bundle"
            )
        if (
            bundle.manifest.builder_id
            != "cortex_context_profile_v1"
            or bundle.manifest.as_of != as_of
            or bundle.profile.metadata.get("builder_id")
            != bundle.manifest.builder_id
            or bundle.profile.metadata.get("selection_digest")
            != bundle.manifest.selection_digest
        ):
            raise PairwiseExecutionUnavailable(
                "Cortex profile bundle failed manifest binding"
            )
        request = {
            "profile": _json_snapshot(bundle.profile, "profile"),
            "prompts": _json_snapshot(tuple(prompts), "prompts"),
            "system_ids": list(system_ids),
            "strategy": _json_snapshot(
                spec.get("strategy", {}), "strategy"
            ),
            "seed": spec.get("seed", 0),
            "assumptions": self.config.assumptions_snapshot(),
            "budget": _json_snapshot(spec.get("budget", {}), "budget"),
        }
        profile_bundle = serialize_cortex_profile_bundle(bundle)
        request["profile_bundle_digest"] = canonical_hash(
            profile_bundle,
            prefix="pairwise_execution_profile_bundle_",
        )
        request["execution_config_digest"] = self.config.digest
        normalized_request = _mapping(
            _json_snapshot(request, "request"),
            "request",
        )
        return dict(normalized_request), profile_bundle

    def _require_consent(
        self,
        user_id: str,
        *,
        now_unix: int | None,
    ) -> PairwiseConsentGrant:
        grant = self.consent_authority.get_pairwise_consent(user_id)
        if not isinstance(grant, PairwiseConsentGrant):
            raise PairwiseExecutionUnavailable(
                "current remote-processing consent is required"
            )
        now = datetime.fromtimestamp(
            int(time.time()) if now_unix is None else int(now_unix),
            tz=timezone.utc,
        ).replace(microsecond=0)
        granted_at = _parse_datetime(
            grant.granted_at, "consent.granted_at"
        )
        expires_at = _parse_datetime(
            grant.expires_at, "consent.expires_at"
        )
        if (
            grant.user_id != user_id
            or grant.scope != PAIRWISE_CONSENT_SCOPE
            or grant.consent_version != self.config.consent_version
            or grant.config_digest != self.config.digest
            or grant.revoked_at is not None
            or not granted_at <= now < expires_at
        ):
            raise PairwiseExecutionUnavailable(
                "current remote-processing consent is required"
            )
        return grant

    def prepare(
        self,
        *,
        user_id: str,
        spec: Mapping[str, Any],
        receipt_ttl_seconds: int = 15 * 60,
        now_unix: int | None = None,
    ) -> dict[str, Any]:
        request, _profile_bundle = self._build_request(user_id, spec)
        return build_pairwise_preflight_response(
            request,
            policy=self.policy,
            subject=user_id,
            signing_key=self.signing_key,
            receipt_ttl_seconds=receipt_ttl_seconds,
            now_unix=now_unix,
        )

    def submit(
        self,
        *,
        user_id: str,
        spec: Mapping[str, Any],
        receipt: Mapping[str, Any],
        idempotency_key: str,
        now_unix: int | None = None,
    ) -> PairwiseExecutionStatus:
        request, profile_bundle = self._build_request(user_id, spec)
        receipt = _mapping(
            _json_snapshot(_mapping(receipt, "receipt"), "receipt"),
            "receipt",
        )
        config_manifest = self.config.manifest
        config_digest = self.config.digest

        def commit_guard() -> PairwiseConsentGrant:
            require_pairwise_admission_receipt(
                request,
                receipt,
                policy=self.policy,
                subject=user_id,
                signing_key=self.signing_key,
                now_unix=now_unix,
            )
            return self._require_consent(
                user_id,
                now_unix=now_unix,
            )

        return self._repository.submit_verified(
            user_id=user_id,
            request=request,
            profile_bundle=profile_bundle,
            receipt=receipt,
            config_manifest=config_manifest,
            config_digest=config_digest,
            consent_version=self.config.consent_version,
            retention_seconds=self.config.request_retention_seconds,
            idempotency_key=idempotency_key,
            commit_guard=commit_guard,
        )

    def get_status(
        self, user_id: str, evaluation_id: str
    ) -> PairwiseExecutionStatus:
        return self._repository.get_status(user_id, evaluation_id)

    def cancel(
        self, user_id: str, evaluation_id: str
    ) -> PairwiseExecutionStatus:
        return self._repository.cancel(user_id, evaluation_id)

    def delete_request_content(
        self, user_id: str, evaluation_id: str
    ) -> PairwiseExecutionStatus:
        return self._repository.delete_request_content(
            user_id, evaluation_id
        )

    def purge_expired(
        self,
        *,
        now_utc: str | None = None,
        limit: int = 1000,
    ) -> tuple[str, ...]:
        return self._repository.purge_expired(
            now_utc=now_utc,
            limit=limit,
        )
