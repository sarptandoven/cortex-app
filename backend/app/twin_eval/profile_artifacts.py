from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from .domain import (
    CitedProfileItem,
    EvaluationReport,
    HeldOutProfile,
    canonical_hash,
    canonical_json,
)
from .policies import PromptScopedCitationPolicy
from .profile_adapter import (
    CortexHeldOutProfileBundle,
    CortexProfileManifest,
    PromptProfileCoverage,
)


PROFILE_ARTIFACT_SCHEMA_VERSION = "cortex-pairwise-profile-artifact/v1"
PROFILE_ARTIFACT_ENCRYPTION_PURPOSE = "twin_eval_evidence"
MAX_PROFILE_ARTIFACT_BYTES = 2_000_000


class ProfileArtifactError(ValueError):
    """An encrypted profile artifact failed structural or link validation."""


class ProfileArtifactEncryptionUnavailable(ProfileArtifactError):
    """Frozen evidence cannot be persisted without an active CXE1 cipher."""


class ProfileArtifactExpired(ProfileArtifactError):
    """Frozen evidence is past its retention deadline."""


@dataclass(frozen=True)
class ProfileArtifactEnvelope:
    artifact_id: str
    artifact_digest: str
    scope_digest: str
    created_at: str
    expires_at: str
    plaintext: bytes


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProfileArtifactError("profile artifact is malformed")
    return value


def _exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
) -> None:
    if set(value) != expected:
        raise ProfileArtifactError("profile artifact is malformed")


def _safe_manifest(manifest: CortexProfileManifest) -> dict[str, Any]:
    return {
        "schema_version": manifest.schema_version,
        "builder_id": manifest.builder_id,
        "as_of": manifest.as_of,
        "config_digest": manifest.config_digest,
        "selection_digest": manifest.selection_digest,
        "prompt_scope_digests": manifest.prompt_scope_digests,
    }


def _policy_config(
    policy: PromptScopedCitationPolicy,
) -> dict[str, Any]:
    return json.loads(canonical_json(policy.reproducibility_config()))


def serialize_cortex_profile_bundle(
    bundle: CortexHeldOutProfileBundle,
) -> dict[str, Any]:
    """Canonical private bundle used by encrypted pre-run control planes."""

    _validate_bundle_semantics(bundle)
    if any(item.source_url is not None for item in bundle.profile.items):
        raise ProfileArtifactError(
            "profile artifact contains a forbidden source locator"
        )
    if any(
        item.author_class != "user"
        or item.status != "active"
        or item.trust_score <= 0
        for item in bundle.profile.items
    ):
        raise ProfileArtifactError(
            "profile artifact contains ineligible evidence"
        )
    if canonical_json(bundle.profile.metadata.get("profile_manifest")) != (
        canonical_json(_safe_manifest(bundle.manifest))
    ):
        raise ProfileArtifactError(
            "profile artifact manifest does not match profile"
        )
    return json.loads(
        canonical_json(
            {
                "profile": bundle.profile,
                "citation_policy": _policy_config(
                    bundle.citation_policy
                ),
                "coverage": bundle.coverage,
                "builder_manifest": bundle.manifest,
            }
        )
    )


def _validate_bundle_semantics(
    bundle: CortexHeldOutProfileBundle,
) -> None:
    scopes = bundle.citation_policy.allowed_memory_ids
    profile_ids = {item.memory_id for item in bundle.profile.items}
    scoped_ids = {
        memory_id
        for memory_ids in scopes.values()
        for memory_id in memory_ids
    }
    if not scopes or any(not memory_ids for memory_ids in scopes.values()):
        raise ProfileArtifactError(
            "profile artifact has an empty prompt scope"
        )
    if scoped_ids != profile_ids:
        raise ProfileArtifactError(
            "profile artifact scope does not exactly cover the profile"
        )
    expected_scope_digests = tuple(
        (
            prompt_id,
            canonical_hash(
                {"memory_ids": scopes[prompt_id]},
                prefix="pairwise_prompt_scope_",
            ),
        )
        for prompt_id in sorted(scopes)
    )
    if bundle.manifest.prompt_scope_digests != expected_scope_digests:
        raise ProfileArtifactError(
            "profile artifact prompt-scope digest verification failed"
        )

    coverage_by_prompt = {
        item.prompt_id: item for item in bundle.coverage
    }
    if (
        len(coverage_by_prompt) != len(bundle.coverage)
        or tuple(item.prompt_id for item in bundle.coverage)
        != tuple(sorted(scopes))
        or set(coverage_by_prompt) != set(scopes)
    ):
        raise ProfileArtifactError(
            "profile artifact coverage does not match prompt scopes"
        )
    for prompt_id, memory_ids in scopes.items():
        coverage = coverage_by_prompt[prompt_id]
        if (
            coverage.status != "sufficient"
            or coverage.selected_items != len(memory_ids)
            or coverage.conflicts_resolved < 0
        ):
            raise ProfileArtifactError(
                "profile artifact coverage counts are inconsistent"
            )
        reasons = [reason for reason, _count in coverage.excluded_by_reason]
        if (
            len(reasons) != len(set(reasons))
            or any(not reason for reason in reasons)
            or any(
                isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
                for _reason, count in coverage.excluded_by_reason
            )
        ):
            raise ProfileArtifactError(
                "profile artifact exclusion counts are invalid"
            )


def validate_profile_artifact_link(
    report: EvaluationReport,
    bundle: CortexHeldOutProfileBundle,
) -> None:
    _validate_bundle_semantics(bundle)
    if report.profile_fingerprint != bundle.profile.fingerprint:
        raise ProfileArtifactError(
            "profile artifact does not match evaluation report"
        )
    if any(item.source_url is not None for item in bundle.profile.items):
        raise ProfileArtifactError(
            "profile artifact contains a forbidden source locator"
        )
    if any(
        item.author_class != "user"
        or item.status != "active"
        or item.trust_score <= 0
        for item in bundle.profile.items
    ):
        raise ProfileArtifactError(
            "profile artifact contains ineligible evidence"
        )

    reproducibility = _mapping(
        report.metadata.get("reproducibility_manifest")
    )
    if canonical_json(reproducibility.get("citation_policy")) != (
        canonical_json(
            {
                "id": bundle.citation_policy.policy_id,
                "config": _policy_config(bundle.citation_policy),
            }
        )
    ):
        raise ProfileArtifactError(
            "profile artifact citation scope does not match report"
        )
    report_manifest = reproducibility.get("profile_manifest")
    if canonical_json(report_manifest) != canonical_json(
        _safe_manifest(bundle.manifest)
    ):
        raise ProfileArtifactError(
            "profile artifact manifest does not match report"
        )
    if canonical_json(bundle.profile.metadata.get("profile_manifest")) != (
        canonical_json(_safe_manifest(bundle.manifest))
    ):
        raise ProfileArtifactError(
            "profile artifact manifest does not match profile"
        )


def build_profile_artifact_envelope(
    *,
    user_id: str,
    report: EvaluationReport,
    bundle: CortexHeldOutProfileBundle,
    artifact_id: str,
    created_at: str,
    expires_at: str,
) -> ProfileArtifactEnvelope:
    validate_profile_artifact_link(report, bundle)
    policy_config = _policy_config(bundle.citation_policy)
    scope_digest = canonical_hash(
        policy_config,
        prefix="pairwise_profile_scope_",
    )
    payload = {
        "schema_version": PROFILE_ARTIFACT_SCHEMA_VERSION,
        "user_id": user_id,
        "run_id": report.run_id,
        "artifact_id": artifact_id,
        "profile_fingerprint": bundle.profile.fingerprint,
        "scope_digest": scope_digest,
        "created_at": created_at,
        "expires_at": expires_at,
        "profile": bundle.profile,
        "citation_policy": policy_config,
        "coverage": bundle.coverage,
        "builder_manifest": bundle.manifest,
    }
    plaintext = canonical_json(payload).encode("utf-8")
    if len(plaintext) > MAX_PROFILE_ARTIFACT_BYTES:
        raise ProfileArtifactError(
            "profile artifact exceeds encrypted storage limit"
        )
    return ProfileArtifactEnvelope(
        artifact_id=artifact_id,
        artifact_digest=canonical_hash(
            payload,
            prefix="pairwise_profile_artifact_",
        ),
        scope_digest=scope_digest,
        created_at=created_at,
        expires_at=expires_at,
        plaintext=plaintext,
    )


def _parse_profile(value: Any) -> HeldOutProfile:
    raw = _mapping(value)
    _exact_keys(raw, {"profile_id", "items", "metadata"})
    raw_items = raw["items"]
    if not isinstance(raw_items, list):
        raise ProfileArtifactError("profile artifact is malformed")
    profile_id = raw["profile_id"]
    if not isinstance(profile_id, str) or not profile_id:
        raise ProfileArtifactError("profile artifact is malformed")
    if not isinstance(raw["metadata"], Mapping):
        raise ProfileArtifactError("profile artifact is malformed")
    items: list[CitedProfileItem] = []
    for raw_item in raw_items:
        item = _mapping(raw_item)
        _exact_keys(
            item,
            {
                "memory_id",
                "content",
                "source_url",
                "layer",
                "author_class",
                "status",
                "trust_score",
            },
        )
        if item.get("source_url") is not None:
            raise ProfileArtifactError(
                "profile artifact contains a forbidden source locator"
            )
        for name in ("memory_id", "content", "author_class", "status"):
            if not isinstance(item[name], str) or not item[name]:
                raise ProfileArtifactError("profile artifact is malformed")
        if item["layer"] is not None and not isinstance(item["layer"], str):
            raise ProfileArtifactError("profile artifact is malformed")
        trust_score = item["trust_score"]
        if (
            isinstance(trust_score, bool)
            or not isinstance(trust_score, (int, float))
        ):
            raise ProfileArtifactError("profile artifact is malformed")
        items.append(
            CitedProfileItem(
                memory_id=item["memory_id"],
                content=item["content"],
                source_url=None,
                layer=item["layer"],
                author_class=item["author_class"],
                status=item["status"],
                trust_score=float(trust_score),
            )
        )
    return HeldOutProfile(
        profile_id=profile_id,
        items=tuple(items),
        metadata=_mapping(raw["metadata"]),
    )


def _parse_policy(value: Any) -> PromptScopedCitationPolicy:
    raw = _mapping(value)
    _exact_keys(
        raw,
        {
            "allowed_memory_ids",
            "require_scope_for_decisive",
            "profile_scope_mode",
        },
    )
    if raw["profile_scope_mode"] != "exact_prompt_scope_v1":
        raise ProfileArtifactError("profile artifact scope mode is unsupported")
    allowed = _mapping(raw["allowed_memory_ids"])
    if not isinstance(raw["require_scope_for_decisive"], bool):
        raise ProfileArtifactError("profile artifact is malformed")
    normalized_allowed: dict[str, tuple[str, ...]] = {}
    for prompt_id, memory_ids in allowed.items():
        if (
            not isinstance(prompt_id, str)
            or not prompt_id
            or not isinstance(memory_ids, list)
            or any(
                not isinstance(memory_id, str) or not memory_id
                for memory_id in memory_ids
            )
        ):
            raise ProfileArtifactError("profile artifact is malformed")
        normalized_allowed[prompt_id] = tuple(memory_ids)
    return PromptScopedCitationPolicy(
        normalized_allowed,
        require_scope_for_decisive=raw["require_scope_for_decisive"],
    )


def _parse_coverage(value: Any) -> tuple[PromptProfileCoverage, ...]:
    if not isinstance(value, list):
        raise ProfileArtifactError("profile artifact is malformed")
    coverage: list[PromptProfileCoverage] = []
    for raw_value in value:
        raw = _mapping(raw_value)
        _exact_keys(
            raw,
            {
                "prompt_id",
                "status",
                "selected_items",
                "conflicts_resolved",
                "excluded_by_reason",
            },
        )
        excluded = raw["excluded_by_reason"]
        if not isinstance(excluded, list):
            raise ProfileArtifactError("profile artifact is malformed")
        for name in (
            "prompt_id",
            "status",
        ):
            if not isinstance(raw[name], str) or not raw[name]:
                raise ProfileArtifactError("profile artifact is malformed")
        for name in (
            "selected_items",
            "conflicts_resolved",
        ):
            if (
                isinstance(raw[name], bool)
                or not isinstance(raw[name], int)
                or raw[name] < 0
            ):
                raise ProfileArtifactError("profile artifact is malformed")
        normalized_excluded: list[tuple[str, int]] = []
        for item in excluded:
            if (
                not isinstance(item, list)
                or len(item) != 2
                or not isinstance(item[0], str)
                or not item[0]
                or isinstance(item[1], bool)
                or not isinstance(item[1], int)
                or item[1] < 0
            ):
                raise ProfileArtifactError("profile artifact is malformed")
            normalized_excluded.append((item[0], item[1]))
        coverage.append(
            PromptProfileCoverage(
                prompt_id=raw["prompt_id"],
                status=raw["status"],
                selected_items=raw["selected_items"],
                conflicts_resolved=raw["conflicts_resolved"],
                excluded_by_reason=tuple(normalized_excluded),
            )
        )
    return tuple(coverage)


def _parse_manifest(value: Any) -> CortexProfileManifest:
    raw = _mapping(value)
    _exact_keys(
        raw,
        {
            "schema_version",
            "builder_id",
            "as_of",
            "snapshot_digest",
            "config_digest",
            "selection_digest",
            "prompt_scope_digests",
        },
    )
    raw_scopes = raw["prompt_scope_digests"]
    if not isinstance(raw_scopes, list):
        raise ProfileArtifactError("profile artifact is malformed")
    for name in (
        "schema_version",
        "builder_id",
        "as_of",
        "snapshot_digest",
        "config_digest",
        "selection_digest",
    ):
        if not isinstance(raw[name], str) or not raw[name]:
            raise ProfileArtifactError("profile artifact is malformed")
    normalized_scopes: list[tuple[str, str]] = []
    for item in raw_scopes:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not item[0]
            or not isinstance(item[1], str)
            or not item[1]
        ):
            raise ProfileArtifactError("profile artifact is malformed")
        normalized_scopes.append((item[0], item[1]))
    return CortexProfileManifest(
        schema_version=raw["schema_version"],
        builder_id=raw["builder_id"],
        as_of=raw["as_of"],
        snapshot_digest=raw["snapshot_digest"],
        config_digest=raw["config_digest"],
        selection_digest=raw["selection_digest"],
        prompt_scope_digests=tuple(normalized_scopes),
    )


def parse_cortex_profile_bundle(
    value: Any,
) -> CortexHeldOutProfileBundle:
    """Strictly reconstruct a private bundle before any provider disclosure."""

    payload = _mapping(value)
    _exact_keys(
        payload,
        {
            "profile",
            "citation_policy",
            "coverage",
            "builder_manifest",
        },
    )
    try:
        bundle = CortexHeldOutProfileBundle(
            profile=_parse_profile(payload["profile"]),
            citation_policy=_parse_policy(payload["citation_policy"]),
            coverage=_parse_coverage(payload["coverage"]),
            manifest=_parse_manifest(payload["builder_manifest"]),
        )
        _validate_bundle_semantics(bundle)
    except ProfileArtifactError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ProfileArtifactError(
            "profile artifact is malformed"
        ) from exc
    serialize_cortex_profile_bundle(bundle)
    return bundle


def parse_profile_artifact(
    plaintext: bytes,
    *,
    expected_user_id: str,
    expected_run_id: str,
    expected_artifact_id: str,
    expected_profile_fingerprint: str,
    expected_scope_digest: str,
    expected_artifact_digest: str,
    expected_created_at: str,
    expected_expires_at: str,
) -> CortexHeldOutProfileBundle:
    try:
        decoded = json.loads(bytes(plaintext).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileArtifactError("profile artifact is malformed") from exc
    payload = _mapping(decoded)
    _exact_keys(
        payload,
        {
            "schema_version",
            "user_id",
            "run_id",
            "artifact_id",
            "profile_fingerprint",
            "scope_digest",
            "created_at",
            "expires_at",
            "profile",
            "citation_policy",
            "coverage",
            "builder_manifest",
        },
    )
    expected_values = {
        "schema_version": PROFILE_ARTIFACT_SCHEMA_VERSION,
        "user_id": expected_user_id,
        "run_id": expected_run_id,
        "artifact_id": expected_artifact_id,
        "profile_fingerprint": expected_profile_fingerprint,
        "scope_digest": expected_scope_digest,
        "created_at": expected_created_at,
        "expires_at": expected_expires_at,
    }
    if any(payload.get(key) != value for key, value in expected_values.items()):
        raise ProfileArtifactError("profile artifact link verification failed")
    if canonical_hash(
        payload,
        prefix="pairwise_profile_artifact_",
    ) != expected_artifact_digest:
        raise ProfileArtifactError("profile artifact digest verification failed")

    try:
        bundle = CortexHeldOutProfileBundle(
            profile=_parse_profile(payload["profile"]),
            citation_policy=_parse_policy(payload["citation_policy"]),
            coverage=_parse_coverage(payload["coverage"]),
            manifest=_parse_manifest(payload["builder_manifest"]),
        )
        _validate_bundle_semantics(bundle)
    except ProfileArtifactError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ProfileArtifactError("profile artifact is malformed") from exc
    if bundle.profile.fingerprint != expected_profile_fingerprint:
        raise ProfileArtifactError(
            "profile artifact fingerprint verification failed"
        )
    if canonical_hash(
        _policy_config(bundle.citation_policy),
        prefix="pairwise_profile_scope_",
    ) != expected_scope_digest:
        raise ProfileArtifactError("profile artifact scope verification failed")
    if canonical_json(bundle.profile.metadata.get("profile_manifest")) != (
        canonical_json(_safe_manifest(bundle.manifest))
    ):
        raise ProfileArtifactError(
            "profile artifact manifest verification failed"
        )
    return bundle
