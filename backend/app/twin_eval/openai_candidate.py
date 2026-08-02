from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping, Protocol


OPENAI_CANDIDATE_PARSER_REVISION = "openai-responses-candidate/v1"
OPENAI_CANDIDATE_OUTCOME_SCHEMA = "pairwise-candidate-outcome/v1"


class _OpenAICandidateEndpoint(Protocol):
    adapter_revision: str
    model_id: str
    max_output_chars: int
    max_input_tokens: int
    max_output_tokens: int
    response_parser_revision: str


class OpenAICandidateResponseError(ValueError):
    """Content-free rejection of an untrusted provider response."""

    def __init__(self, code: str) -> None:
        dispositions = {
            "provider_failed": "definitive_failure",
            "provider_incomplete": "definitive_failure",
            "provider_refusal": "definitive_failure",
            "provider_nonterminal": "outcome_unknown",
        }
        self.code = code
        self.disposition = dispositions.get(
            code, "invalid_provider_response"
        )
        super().__init__(
            f"OpenAI candidate response was rejected: {code}"
        )


class _ParsedOpenAICandidateOutcome:
    """Normalized output; a recorder must still bind it to one dispatch."""

    __slots__ = (
        "_response_id",
        "_adapter_revision",
        "_model_id",
        "_output_text",
        "_usage",
    )

    def __init__(
        self,
        *,
        response_id: str,
        adapter_revision: str,
        model_id: str,
        output_text: str,
        usage: Mapping[str, int],
    ) -> None:
        object.__setattr__(self, "_response_id", response_id)
        object.__setattr__(
            self, "_adapter_revision", adapter_revision
        )
        object.__setattr__(self, "_model_id", model_id)
        object.__setattr__(self, "_output_text", output_text)
        object.__setattr__(
            self, "_usage", MappingProxyType(dict(usage))
        )

    def __setattr__(self, name: str, value: Any) -> None:
        del name, value
        raise AttributeError("parsed candidate outcomes are immutable")

    def __reduce__(self):
        raise TypeError("parsed candidate outcomes cannot be serialized")

    def __repr__(self) -> str:
        return (
            "_ParsedOpenAICandidateOutcome("
            f"adapter_revision={self._adapter_revision!r}, "
            f"model_id={self._model_id!r}, "
            f"output_chars={len(self._output_text)!r}, "
            f"usage={dict(self._usage)!r})"
        )

    @property
    def response_id(self) -> str:
        return self._response_id

    @property
    def model_id(self) -> str:
        return self._model_id

    def _snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": OPENAI_CANDIDATE_OUTCOME_SCHEMA,
            "provider": "openai_responses",
            "response_id": self._response_id,
            "adapter_revision": self._adapter_revision,
            "response_parser_revision": (
                OPENAI_CANDIDATE_PARSER_REVISION
            ),
            "model_id": self._model_id,
            "output_text": self._output_text,
            "usage": dict(self._usage),
        }


def _text(value: Any, name: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OpenAICandidateResponseError(f"invalid_{name}")
    if len(value) > maximum:
        raise OpenAICandidateResponseError(f"{name}_too_large")
    return value


def _usage(
    response: Mapping[str, Any],
    endpoint: _OpenAICandidateEndpoint,
) -> Mapping[str, int]:
    raw = response.get("usage")
    if not isinstance(raw, Mapping):
        raise OpenAICandidateResponseError("missing_usage")
    values: dict[str, int] = {}
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        value = raw.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise OpenAICandidateResponseError("invalid_usage")
        values[field] = value
    if (
        values["input_tokens"] > endpoint.max_input_tokens
        or values["output_tokens"] > endpoint.max_output_tokens
        or values["total_tokens"]
        > endpoint.max_input_tokens + endpoint.max_output_tokens
    ):
        raise OpenAICandidateResponseError("usage_limit_exceeded")
    if values["total_tokens"] != (
        values["input_tokens"] + values["output_tokens"]
    ):
        raise OpenAICandidateResponseError("invalid_usage")
    details = raw.get("input_tokens_details")
    if details is not None:
        if not isinstance(details, Mapping):
            raise OpenAICandidateResponseError("invalid_usage")
        cached = details.get("cached_tokens", 0)
        if (
            isinstance(cached, bool)
            or not isinstance(cached, int)
            or not 0 <= cached <= values["input_tokens"]
        ):
            raise OpenAICandidateResponseError("invalid_usage")
        values["cached_input_tokens"] = cached
    output_details = raw.get("output_tokens_details")
    if output_details is not None:
        if not isinstance(output_details, Mapping):
            raise OpenAICandidateResponseError("invalid_usage")
        reasoning = output_details.get("reasoning_tokens", 0)
        if (
            isinstance(reasoning, bool)
            or not isinstance(reasoning, int)
            or not 0 <= reasoning <= values["output_tokens"]
        ):
            raise OpenAICandidateResponseError("invalid_usage")
        values["reasoning_output_tokens"] = reasoning
    return MappingProxyType(values)


def parse_openai_candidate_response(
    response: Mapping[str, Any],
    endpoint: _OpenAICandidateEndpoint,
) -> _ParsedOpenAICandidateOutcome:
    """Validate one non-streaming Responses API candidate result offline."""

    if not isinstance(response, Mapping):
        raise OpenAICandidateResponseError("invalid_response")
    if response.get("object") != "response":
        raise OpenAICandidateResponseError("invalid_response")
    if (
        endpoint.response_parser_revision
        != OPENAI_CANDIDATE_PARSER_REVISION
    ):
        raise OpenAICandidateResponseError("parser_revision_mismatch")
    response_id = _text(
        response.get("id"), "response_id", maximum=200
    )
    if not response_id.startswith("resp_"):
        raise OpenAICandidateResponseError("invalid_response_id")
    model_id = _text(response.get("model"), "model_id", maximum=200)
    if model_id != endpoint.model_id:
        raise OpenAICandidateResponseError("model_mismatch")
    status = response.get("status")
    if status == "incomplete":
        raise OpenAICandidateResponseError("provider_incomplete")
    if status in {"failed", "cancelled"} or response.get("error") is not None:
        raise OpenAICandidateResponseError("provider_failed")
    if status != "completed":
        raise OpenAICandidateResponseError("provider_nonterminal")
    if response.get("incomplete_details") is not None:
        raise OpenAICandidateResponseError("provider_incomplete")
    output = response.get("output")
    if not isinstance(output, list) or len(output) > 100:
        raise OpenAICandidateResponseError("invalid_output")
    messages = 0
    fragments: list[str] = []
    output_chars = 0
    for item in output:
        if not isinstance(item, Mapping):
            raise OpenAICandidateResponseError("invalid_output")
        item_type = item.get("type")
        if item_type == "reasoning":
            continue
        if item_type != "message":
            raise OpenAICandidateResponseError("unexpected_output_item")
        messages += 1
        if (
            item.get("role") != "assistant"
            or item.get("status") != "completed"
        ):
            raise OpenAICandidateResponseError("invalid_message")
        content = item.get("content")
        if (
            not isinstance(content, list)
            or not content
            or len(content) > 100
        ):
            raise OpenAICandidateResponseError("invalid_message")
        for part in content:
            if not isinstance(part, Mapping):
                raise OpenAICandidateResponseError("invalid_content")
            part_type = part.get("type")
            if part_type == "refusal":
                raise OpenAICandidateResponseError("provider_refusal")
            if part_type != "output_text":
                raise OpenAICandidateResponseError("invalid_content")
            text = part.get("text")
            if not isinstance(text, str):
                raise OpenAICandidateResponseError("invalid_content")
            fragments.append(text)
            output_chars += len(text)
            if output_chars > endpoint.max_output_chars:
                raise OpenAICandidateResponseError("output_too_large")
    if messages != 1 or not fragments:
        raise OpenAICandidateResponseError("invalid_output")
    output_text = "".join(fragments)
    if not output_text.strip():
        raise OpenAICandidateResponseError("empty_output")
    if len(output_text) > endpoint.max_output_chars:
        raise OpenAICandidateResponseError("output_too_large")
    return _ParsedOpenAICandidateOutcome(
        response_id=response_id,
        adapter_revision=endpoint.adapter_revision,
        model_id=model_id,
        output_text=output_text,
        usage=_usage(response, endpoint),
    )
