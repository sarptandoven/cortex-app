from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from .domain import (
    EvaluationPrompt,
    canonical_json,
    derive_seed,
)
from .profile_artifacts import parse_cortex_profile_bundle


CALL_CHECKPOINT_SCHEMA = "pairwise-execution-call-checkpoint/v1"
CALL_CHECKPOINT_ENCRYPTION_PURPOSE = "twin_eval_execution_call"
CALL_COORDINATE_SCHEMA = "pairwise-call-coordinate/v1"
CALL_ADAPTER_INPUT_SCHEMA = "pairwise-candidate-adapter-input/v1"


class _TrustedAdapterEndpoint(Protocol):
    adapter_revision: str
    max_input_chars: int
    max_output_chars: int

    @property
    def manifest(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class _CandidateCallDefinition:
    ordinal: int
    coordinate: Mapping[str, Any]
    adapter_input: Mapping[str, Any] = field(repr=False)
    adapter_revision: str
    candidate_call_count: int


class _PairwiseDispatchCapability:
    """One in-memory capability returned only after checkpoint commit."""

    __slots__ = (
        "_user_id",
        "_evaluation_id",
        "_call_id",
        "_call_ordinal",
        "_lease_generation",
        "_adapter_revision",
        "_authorized_at",
        "_call_deadline_at",
        "_permit",
        "_adapter_input",
        "_provider_idempotency_key",
        "_burn_lock",
        "_burned",
    )

    def __init__(
        self,
        *,
        user_id: str,
        evaluation_id: str,
        call_id: str,
        call_ordinal: int,
        lease_generation: int,
        adapter_revision: str,
        authorized_at: str,
        call_deadline_at: str,
        permit: str,
        adapter_input: Mapping[str, Any],
        provider_idempotency_key: str | None = None,
    ) -> None:
        object.__setattr__(self, "_user_id", user_id)
        object.__setattr__(self, "_evaluation_id", evaluation_id)
        object.__setattr__(self, "_call_id", call_id)
        object.__setattr__(self, "_call_ordinal", call_ordinal)
        object.__setattr__(
            self, "_lease_generation", lease_generation
        )
        object.__setattr__(
            self, "_adapter_revision", adapter_revision
        )
        object.__setattr__(self, "_authorized_at", authorized_at)
        object.__setattr__(
            self, "_call_deadline_at", call_deadline_at
        )
        object.__setattr__(self, "_permit", permit)
        object.__setattr__(self, "_adapter_input", adapter_input)
        object.__setattr__(
            self,
            "_provider_idempotency_key",
            provider_idempotency_key,
        )
        object.__setattr__(self, "_burn_lock", Lock())
        object.__setattr__(self, "_burned", False)

    def __setattr__(self, name: str, value: Any) -> None:
        del name, value
        raise AttributeError("dispatch capabilities are immutable")

    def __reduce__(self):
        raise TypeError("dispatch capabilities cannot be serialized")

    def __repr__(self) -> str:
        return (
            "_PairwiseDispatchCapability("
            f"user_id={self._user_id!r}, "
            f"evaluation_id={self._evaluation_id!r}, "
            f"call_id={self._call_id!r}, "
            f"call_ordinal={self._call_ordinal!r}, "
            f"lease_generation={self._lease_generation!r}, "
            f"adapter_revision={self._adapter_revision!r}, "
            f"authorized_at={self._authorized_at!r}, "
            f"call_deadline_at={self._call_deadline_at!r})"
        )

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def evaluation_id(self) -> str:
        return self._evaluation_id

    @property
    def call_id(self) -> str:
        return self._call_id

    @property
    def call_ordinal(self) -> int:
        return self._call_ordinal

    @property
    def lease_generation(self) -> int:
        return self._lease_generation

    @property
    def adapter_revision(self) -> str:
        return self._adapter_revision

    @property
    def authorized_at(self) -> str:
        return self._authorized_at

    @property
    def call_deadline_at(self) -> str:
        return self._call_deadline_at

    @property
    def permit(self) -> str:
        with self._burn_lock:
            if self._burned:
                raise RuntimeError(
                    "dispatch capability was already burned"
                )
            return self._permit

    @property
    def adapter_input(self) -> Mapping[str, Any]:
        with self._burn_lock:
            if self._burned:
                raise RuntimeError(
                    "dispatch capability was already burned"
                )
            return self._adapter_input

    @property
    def provider_idempotency_key(self) -> str | None:
        with self._burn_lock:
            if self._burned:
                raise RuntimeError(
                    "dispatch capability was already burned"
                )
            return self._provider_idempotency_key

    def _burn_after_commit(self) -> None:
        """Erase secret material after the durable consume fence commits."""

        with self._burn_lock:
            if self._burned:
                return
            object.__setattr__(self, "_permit", None)
            object.__setattr__(self, "_adapter_input", None)
            object.__setattr__(
                self, "_provider_idempotency_key", None
            )
            object.__setattr__(self, "_burned", True)


class _ConsumedCandidateDispatch:
    """Post-commit handoff whose private transport payload can be taken once."""

    __slots__ = (
        "_user_id",
        "_evaluation_id",
        "_call_id",
        "_call_ordinal",
        "_adapter_revision",
        "_consumed_at",
        "_call_deadline_at",
        "_transport_input",
        "_provider_idempotency_key",
        "_take_lock",
    )

    def __init__(
        self,
        *,
        user_id: str,
        evaluation_id: str,
        call_id: str,
        call_ordinal: int,
        adapter_revision: str,
        consumed_at: str,
        call_deadline_at: str,
        transport_input: Mapping[str, Any],
        provider_idempotency_key: str | None,
    ) -> None:
        object.__setattr__(self, "_user_id", user_id)
        object.__setattr__(self, "_evaluation_id", evaluation_id)
        object.__setattr__(self, "_call_id", call_id)
        object.__setattr__(self, "_call_ordinal", call_ordinal)
        object.__setattr__(
            self, "_adapter_revision", adapter_revision
        )
        object.__setattr__(self, "_consumed_at", consumed_at)
        object.__setattr__(
            self, "_call_deadline_at", call_deadline_at
        )
        object.__setattr__(self, "_transport_input", transport_input)
        object.__setattr__(
            self,
            "_provider_idempotency_key",
            provider_idempotency_key,
        )
        object.__setattr__(self, "_take_lock", Lock())

    def __setattr__(self, name: str, value: Any) -> None:
        del name, value
        raise AttributeError("consumed dispatches are immutable")

    def __reduce__(self):
        raise TypeError("consumed dispatches cannot be serialized")

    def __repr__(self) -> str:
        return (
            "_ConsumedCandidateDispatch("
            f"user_id={self._user_id!r}, "
            f"evaluation_id={self._evaluation_id!r}, "
            f"call_id={self._call_id!r}, "
            f"call_ordinal={self._call_ordinal!r}, "
            f"adapter_revision={self._adapter_revision!r}, "
            f"consumed_at={self._consumed_at!r}, "
            f"call_deadline_at={self._call_deadline_at!r})"
        )

    @property
    def call_id(self) -> str:
        return self._call_id

    @property
    def consumed_at(self) -> str:
        return self._consumed_at

    def _take_transport_input(
        self,
    ) -> tuple[Mapping[str, Any], str | None]:
        """Transfer the secret payload once to a future internal transport."""

        with self._take_lock:
            transport_input = self._transport_input
            if transport_input is None:
                raise RuntimeError(
                    "consumed dispatch transport input was already taken"
                )
            provider_key = self._provider_idempotency_key
            object.__setattr__(self, "_transport_input", None)
            object.__setattr__(
                self, "_provider_idempotency_key", None
            )
            return transport_input, provider_key


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        raise ValueError(f"{name} must be an object")
    return value


def _text(value: Any, name: str, *, maximum: int = 50_000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if len(value) > maximum:
        raise ValueError(f"{name} must not exceed {maximum} characters")
    return value


def build_candidate_call_definition(
    artifact: Mapping[str, Any],
    *,
    prompt_id: str,
    system_id: str,
    endpoint: _TrustedAdapterEndpoint,
) -> _CandidateCallDefinition:
    """Derive one candidate call solely from the authenticated parent."""

    artifact = _mapping(artifact, "execution artifact")
    request = _mapping(artifact.get("request"), "execution request")
    prompt_id = _text(prompt_id, "prompt_id", maximum=200).strip()
    system_id = _text(system_id, "system_id", maximum=200).strip()
    raw_prompts = request.get("prompts")
    raw_systems = request.get("system_ids")
    if not isinstance(raw_prompts, list) or not isinstance(
        raw_systems, list
    ):
        raise ValueError("execution request call plan is malformed")

    prompts: list[tuple[EvaluationPrompt, Mapping[str, Any]]] = []
    for index, raw_prompt in enumerate(raw_prompts):
        prompt = _mapping(raw_prompt, f"request.prompts[{index}]")
        value = EvaluationPrompt(
            prompt_id=_text(
                prompt.get("prompt_id"),
                f"request.prompts[{index}].prompt_id",
                maximum=200,
            ).strip(),
            text=_text(
                prompt.get("text"),
                f"request.prompts[{index}].text",
            ),
            metadata=_mapping(
                prompt.get("metadata", {}),
                f"request.prompts[{index}].metadata",
            ),
        )
        prompts.append((value, prompt))
    prompts.sort(key=lambda item: item[0].prompt_id)
    prompt_ids = tuple(item[0].prompt_id for item in prompts)
    if len(set(prompt_ids)) != len(prompt_ids):
        raise ValueError("execution request has duplicate prompt IDs")
    systems = tuple(
        sorted(
            _text(value, "request.system_id", maximum=200).strip()
            for value in raw_systems
        )
    )
    if len(systems) < 2 or len(set(systems)) != len(systems):
        raise ValueError("execution request systems are malformed")

    prompt_by_id = {item[0].prompt_id: item for item in prompts}
    if prompt_id not in prompt_by_id or system_id not in systems:
        raise ValueError("candidate call is not in the authenticated plan")
    config_manifest = _mapping(
        artifact.get("config_manifest"), "config_manifest"
    )
    raw_config_systems = config_manifest.get("systems")
    if not isinstance(raw_config_systems, list):
        raise ValueError("execution adapter config is malformed")
    system_revisions = {
        _text(
            _mapping(item, "config system").get("system_id"),
            "config system_id",
            maximum=200,
        ).strip(): _text(
            _mapping(item, "config system").get("revision"),
            "config system revision",
            maximum=200,
        ).strip()
        for item in raw_config_systems
    }
    revision = system_revisions.get(system_id)
    if revision != endpoint.adapter_revision:
        raise ValueError("candidate adapter revision is not authenticated")
    raw_endpoints = config_manifest.get("adapter_endpoints")
    if not isinstance(raw_endpoints, list) or endpoint.manifest not in (
        _mapping(item, "config adapter endpoint")
        for item in raw_endpoints
    ):
        raise ValueError("candidate adapter endpoint is not authenticated")

    bundle = parse_cortex_profile_bundle(
        artifact.get("profile_bundle")
    )
    prompt, raw_prompt = prompt_by_id[prompt_id]
    scope_profile = getattr(
        bundle.citation_policy, "scope_profile", None
    )
    if not callable(scope_profile):
        raise ValueError("candidate call requires prompt-scoped evidence")
    scoped_profile = scope_profile(prompt, bundle.profile)
    root_seed = derive_seed(
        request.get("seed"),
        bundle.profile.fingerprint,
        prompt_ids,
        systems,
    )
    candidate_seed = derive_seed(
        root_seed, "candidate", prompt_id, system_id
    )
    coordinate = {
        "schema_version": CALL_COORDINATE_SCHEMA,
        "kind": "candidate",
        "prompt_id": prompt_id,
        "system_id": system_id,
        "system_revision": revision,
        "candidate_seed": candidate_seed,
    }
    adapter_input = {
        "schema_version": CALL_ADAPTER_INPUT_SCHEMA,
        "endpoint": endpoint.manifest,
        "system_id": system_id,
        "prompt": raw_prompt,
        "profile": scoped_profile,
        "seed": candidate_seed,
        "max_output_chars": endpoint.max_output_chars,
    }
    if len(canonical_json(adapter_input)) > endpoint.max_input_chars:
        raise ValueError("candidate adapter input exceeds its trusted limit")
    prompt_index = prompt_ids.index(prompt_id)
    system_index = systems.index(system_id)
    return _CandidateCallDefinition(
        ordinal=prompt_index * len(systems) + system_index,
        coordinate=MappingProxyType(dict(coordinate)),
        adapter_input=MappingProxyType(dict(adapter_input)),
        adapter_revision=revision,
        candidate_call_count=len(prompt_ids) * len(systems),
    )
