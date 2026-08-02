from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

from .domain import (
    CitedProfileItem,
    EvaluationPrompt,
    HeldOutProfile,
    canonical_hash,
)
from .policies import PromptScopedCitationPolicy


class ContextAssembler(Protocol):
    def assemble_context(
        self,
        user_id: str,
        task: str = "",
        **kwargs: Any,
    ) -> dict[str, Any] | str: ...

    def redact_export_text(self, value: str) -> str: ...

    def pairwise_profile_snapshot_digest(self, user_id: str) -> str: ...


class ProfileBuildError(ValueError):
    """Base failure for the Cortex-to-pairwise trust boundary."""


class InsufficientProfileEvidence(ProfileBuildError):
    def __init__(self, prompt_ids: Sequence[str]) -> None:
        self.prompt_ids = tuple(sorted(prompt_ids))
        super().__init__(
            "one or more prompts have no eligible owner-authored evidence"
        )


class MalformedContextPack(ProfileBuildError):
    def __init__(self) -> None:
        super().__init__("Cortex returned a malformed context pack")


class ProfileLimitExceeded(ProfileBuildError):
    pass


@dataclass(frozen=True)
class CortexProfileBuilderConfig:
    token_budget_per_prompt: int = 2_000
    max_prompts: int = 100
    max_retrieval_query_chars: int = 500
    max_profile_items: int = 512
    max_item_chars: int = 50_000
    max_total_chars: int = 1_000_000

    def __post_init__(self) -> None:
        for name in (
            "token_budget_per_prompt",
            "max_prompts",
            "max_retrieval_query_chars",
            "max_profile_items",
            "max_item_chars",
            "max_total_chars",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not 300 <= self.token_budget_per_prompt <= 6_000:
            raise ValueError(
                "token_budget_per_prompt must be between 300 and 6000"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_budget_per_prompt": self.token_budget_per_prompt,
            "max_prompts": self.max_prompts,
            "max_retrieval_query_chars": self.max_retrieval_query_chars,
            "max_profile_items": self.max_profile_items,
            "max_item_chars": self.max_item_chars,
            "max_total_chars": self.max_total_chars,
        }


@dataclass(frozen=True)
class PromptProfileCoverage:
    prompt_id: str
    status: str
    selected_items: int
    conflicts_resolved: int
    excluded_by_reason: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class CortexProfileManifest:
    schema_version: str
    builder_id: str
    as_of: str
    snapshot_digest: str
    config_digest: str
    selection_digest: str
    prompt_scope_digests: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CortexHeldOutProfileBundle:
    profile: HeldOutProfile
    citation_policy: PromptScopedCitationPolicy
    coverage: tuple[PromptProfileCoverage, ...]
    manifest: CortexProfileManifest


def _normalize_as_of(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("as_of must be an explicit timezone-aware timestamp")
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(
            raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        )
    except ValueError as exc:
        raise ValueError(
            "as_of must be an explicit timezone-aware timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise ValueError("as_of must be an explicit timezone-aware timestamp")
    return (
        parsed.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MalformedContextPack()
    return value


class CortexHeldOutProfileBuilder:
    """Build a minimized, prompt-scoped pairwise profile from Cortex context."""

    builder_id = "cortex_context_profile_v1"

    def __init__(
        self,
        context: ContextAssembler,
        config: CortexProfileBuilderConfig | None = None,
    ) -> None:
        self.context = context
        self.config = config or CortexProfileBuilderConfig()

    def build(
        self,
        user_id: str,
        prompts: Sequence[EvaluationPrompt],
        *,
        as_of: str,
        sector: str | None = None,
    ) -> CortexHeldOutProfileBundle:
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id is required")
        frozen_as_of = _normalize_as_of(as_of)
        prompt_tuple = tuple(sorted(prompts, key=lambda item: item.prompt_id))
        if not prompt_tuple:
            raise ValueError("at least one evaluation prompt is required")
        if len(prompt_tuple) > self.config.max_prompts:
            raise ProfileLimitExceeded(
                f"profile build exceeds max_prompts={self.config.max_prompts}"
            )
        prompt_ids = tuple(prompt.prompt_id for prompt in prompt_tuple)
        if len(prompt_ids) != len(set(prompt_ids)):
            raise ValueError("prompt_id values must be unique")
        if any(
            len(prompt.text) > self.config.max_retrieval_query_chars
            for prompt in prompt_tuple
        ):
            raise ProfileLimitExceeded(
                "evaluation prompt exceeds "
                f"max_retrieval_query_chars={self.config.max_retrieval_query_chars}"
            )

        all_items: dict[str, CitedProfileItem] = {}
        scopes: dict[str, tuple[str, ...]] = {}
        coverage: list[PromptProfileCoverage] = []
        insufficient: list[str] = []
        snapshot_digest = self.context.pairwise_profile_snapshot_digest(
            user_id
        )
        if not isinstance(snapshot_digest, str) or not snapshot_digest:
            raise ProfileBuildError("Cortex profile snapshot is unavailable")

        for prompt in prompt_tuple:
            pack = self.context.assemble_context(
                user_id,
                prompt.text,
                surface="pairwise-evaluation",
                token_budget=self.config.token_budget_per_prompt,
                sector=sector,
                as_of=frozen_as_of,
                include_identity=True,
                format="json",
                pin=False,
                record_reuse=False,
                use_hot_cache=False,
                response_format="text",
            )
            pack_mapping = _mapping(pack)
            raw_layers = pack_mapping.get("layers")
            raw_conflicts = pack_mapping.get("conflicts", [])
            if not isinstance(raw_layers, list) or not isinstance(
                raw_conflicts,
                list,
            ):
                raise MalformedContextPack()

            selected: dict[str, CitedProfileItem] = {}
            excluded: dict[str, int] = {}

            def exclude(reason: str) -> None:
                excluded[reason] = excluded.get(reason, 0) + 1

            for raw_layer in raw_layers:
                layer = _mapping(raw_layer)
                raw_items = layer.get("items", [])
                if not isinstance(raw_items, list):
                    raise MalformedContextPack()
                for raw_item in raw_items:
                    item = _mapping(raw_item)
                    memory_id = item.get("memory_id")
                    if memory_id is None and item.get("task_id") is not None:
                        exclude("non_memory")
                        continue
                    if not isinstance(memory_id, str) or not memory_id.strip():
                        raise MalformedContextPack()
                    content = item.get("content")
                    if not isinstance(content, str) or not content.strip():
                        raise MalformedContextPack()
                    author_class = item.get("author_class")
                    if author_class != "user":
                        exclude("non_owner")
                        continue
                    trust_score = item.get("trust_score")
                    if (
                        isinstance(trust_score, bool)
                        or not isinstance(trust_score, (int, float))
                        or not 0 < float(trust_score) <= 1
                    ):
                        exclude("non_positive_trust")
                        continue
                    if not item.get("source") and not item.get("source_url"):
                        exclude("uncited")
                        continue
                    redacted = self.context.redact_export_text(content)
                    if not isinstance(redacted, str) or not redacted.strip():
                        exclude("empty_after_redaction")
                        continue
                    if len(redacted) > self.config.max_item_chars:
                        raise ProfileLimitExceeded(
                            "profile item exceeds "
                            f"max_item_chars={self.config.max_item_chars}"
                        )
                    candidate = CitedProfileItem(
                        memory_id=memory_id.strip(),
                        content=redacted,
                        source_url=None,
                        layer=str(item.get("layer") or layer.get("layer") or "")
                        or None,
                        author_class="user",
                        status="active",
                        trust_score=float(trust_score),
                    )
                    existing = selected.get(candidate.memory_id)
                    if existing is not None and existing != candidate:
                        raise ProfileBuildError(
                            "Cortex memory changed during profile assembly"
                        )
                    selected[candidate.memory_id] = candidate

            conflicts_resolved = 0
            for raw_conflict in raw_conflicts:
                conflict = _mapping(raw_conflict)
                raw_ids = conflict.get("memory_ids")
                preferred = conflict.get("prefer")
                if not isinstance(raw_ids, list) or any(
                    not isinstance(memory_id, str) for memory_id in raw_ids
                ):
                    raise MalformedContextPack()
                in_scope = set(raw_ids).intersection(selected)
                if len(in_scope) < 2:
                    continue
                if not isinstance(preferred, str) or preferred not in in_scope:
                    raise ProfileBuildError(
                        "eligible profile evidence has an unresolved conflict"
                    )
                for memory_id in sorted(in_scope - {preferred}):
                    selected.pop(memory_id, None)
                    exclude("superseded_conflict")
                conflicts_resolved += 1

            scope = tuple(sorted(selected))
            scopes[prompt.prompt_id] = scope
            if not scope:
                insufficient.append(prompt.prompt_id)
                status = "insufficient"
            else:
                status = "sufficient"
            coverage.append(
                PromptProfileCoverage(
                    prompt_id=prompt.prompt_id,
                    status=status,
                    selected_items=len(scope),
                    conflicts_resolved=conflicts_resolved,
                    excluded_by_reason=tuple(sorted(excluded.items())),
                )
            )
            for memory_id in scope:
                candidate = selected[memory_id]
                existing = all_items.get(memory_id)
                if existing is not None and existing != candidate:
                    raise ProfileBuildError(
                        "Cortex memory changed during profile assembly"
                    )
                all_items[memory_id] = candidate

        if (
            self.context.pairwise_profile_snapshot_digest(user_id)
            != snapshot_digest
        ):
            raise ProfileBuildError(
                "Cortex changed during profile assembly; retry the profile build"
            )
        if insufficient:
            raise InsufficientProfileEvidence(insufficient)
        if len(all_items) > self.config.max_profile_items:
            raise ProfileLimitExceeded(
                f"profile build exceeds max_profile_items={self.config.max_profile_items}"
            )
        total_chars = sum(len(item.content) for item in all_items.values())
        if total_chars > self.config.max_total_chars:
            raise ProfileLimitExceeded(
                f"profile build exceeds max_total_chars={self.config.max_total_chars}"
            )

        ordered_items = tuple(all_items[key] for key in sorted(all_items))
        selection_identity = {
            "builder_id": self.builder_id,
            "as_of": frozen_as_of,
            "config": self.config.to_dict(),
            "items": ordered_items,
            "scopes": scopes,
        }
        selection_digest = canonical_hash(
            selection_identity,
            prefix="pairwise_profile_selection_",
        )
        config_digest = canonical_hash(
            self.config.to_dict(),
            prefix="pairwise_profile_config_",
        )
        prompt_scope_digests = tuple(
            (
                prompt_id,
                canonical_hash(
                    {"memory_ids": scopes[prompt_id]},
                    prefix="pairwise_prompt_scope_",
                ),
            )
            for prompt_id in sorted(scopes)
        )
        profile_manifest = {
            "schema_version": "cortex-pairwise-profile-manifest/v1",
            "builder_id": self.builder_id,
            "as_of": frozen_as_of,
            "config_digest": config_digest,
            "selection_digest": selection_digest,
            "prompt_scope_digests": prompt_scope_digests,
        }
        profile = HeldOutProfile(
            profile_id=canonical_hash(
                selection_identity,
                prefix="cortex_pairwise_profile_",
            ),
            items=ordered_items,
            metadata={
                "as_of": frozen_as_of,
                "builder_id": self.builder_id,
                "item_count": len(ordered_items),
                "prompt_count": len(prompt_tuple),
                "selection_digest": selection_digest,
                # Safe to persist with a report: digests and counts only, never
                # memory content, source locators, or raw retrieval queries.
                "profile_manifest": profile_manifest,
            },
        )
        policy = PromptScopedCitationPolicy(scopes)
        manifest = CortexProfileManifest(
            schema_version=profile_manifest["schema_version"],
            builder_id=self.builder_id,
            as_of=frozen_as_of,
            snapshot_digest=snapshot_digest,
            config_digest=config_digest,
            selection_digest=selection_digest,
            prompt_scope_digests=prompt_scope_digests,
        )
        return CortexHeldOutProfileBundle(
            profile=profile,
            citation_policy=policy,
            coverage=tuple(coverage),
            manifest=manifest,
        )
