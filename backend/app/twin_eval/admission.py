from __future__ import annotations

import hashlib
import hmac
import math
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from .domain import canonical_hash, canonical_json
from .preflight import (
    EstimateRange,
    PairwisePreflightEstimate,
    PreflightAssumptions,
    PreflightBudget,
)

PAIRWISE_ADMISSION_RECEIPT_SCHEMA = "pairwise-admission-receipt/v1"
MIN_ADMISSION_SIGNING_KEY_BYTES = 32
MAX_ADMISSION_RECEIPT_TTL_SECONDS = 60 * 60


def _positive_integer(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_number(value: float, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value <= 0
    ):
        raise ValueError(f"{name} must be finite and positive")
    return float(value)


def _harden_range(
    requested: EstimateRange,
    floor: EstimateRange,
) -> EstimateRange:
    expected = max(requested.expected, floor.expected)
    upper = max(requested.upper, floor.upper, expected)
    return EstimateRange(
        max(requested.lower, floor.lower),
        expected,
        upper,
    )


def _tightest_integer(
    requested: int | None,
    server_limit: int,
) -> int:
    return server_limit if requested is None else min(requested, server_limit)


def _tightest_number(
    requested: float | None,
    server_limit: float,
) -> float:
    return server_limit if requested is None else min(requested, server_limit)


@dataclass(frozen=True)
class PairwiseAdmissionPolicy:
    """Operator-owned limits that callers cannot weaken."""

    max_provider_calls: int = 1_000
    max_total_tokens: int = 10_000_000
    max_duration_seconds: float = 86_400
    max_parallel_generations: int = 1
    max_parallel_judgments: int = 1
    max_chars_per_token: float = 4.0
    minimum_candidate_output_chars: EstimateRange = field(
        default_factory=lambda: EstimateRange(1_000, 4_000, 16_000)
    )
    minimum_judge_output_tokens_per_call: EstimateRange = field(
        default_factory=lambda: EstimateRange(64, 256, 1_024)
    )
    minimum_generator_latency_seconds: EstimateRange = field(
        default_factory=lambda: EstimateRange(1, 5, 30)
    )
    minimum_judge_latency_seconds: EstimateRange = field(
        default_factory=lambda: EstimateRange(1, 5, 60)
    )
    minimum_generator_request_overhead_chars: int = 1_000
    minimum_judge_request_overhead_chars: int = 2_000

    def __post_init__(self) -> None:
        for name in (
            "max_provider_calls",
            "max_total_tokens",
            "max_parallel_generations",
            "max_parallel_judgments",
        ):
            _positive_integer(getattr(self, name), name)
        _positive_number(self.max_duration_seconds, "max_duration_seconds")
        _positive_number(self.max_chars_per_token, "max_chars_per_token")
        for name in (
            "minimum_generator_request_overhead_chars",
            "minimum_judge_request_overhead_chars",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    def apply(
        self,
        requested_assumptions: PreflightAssumptions,
        requested_budget: PreflightBudget,
    ) -> tuple[PreflightAssumptions, PreflightBudget, dict[str, Any]]:
        assumptions = PreflightAssumptions(
            candidate_output_chars=_harden_range(
                requested_assumptions.candidate_output_chars,
                self.minimum_candidate_output_chars,
            ),
            judge_output_tokens_per_call=_harden_range(
                requested_assumptions.judge_output_tokens_per_call,
                self.minimum_judge_output_tokens_per_call,
            ),
            generator_latency_seconds=_harden_range(
                requested_assumptions.generator_latency_seconds,
                self.minimum_generator_latency_seconds,
            ),
            judge_latency_seconds=_harden_range(
                requested_assumptions.judge_latency_seconds,
                self.minimum_judge_latency_seconds,
            ),
            chars_per_token=min(
                requested_assumptions.chars_per_token,
                self.max_chars_per_token,
            ),
            generator_request_overhead_chars=max(
                requested_assumptions.generator_request_overhead_chars,
                self.minimum_generator_request_overhead_chars,
            ),
            judge_request_overhead_chars=max(
                requested_assumptions.judge_request_overhead_chars,
                self.minimum_judge_request_overhead_chars,
            ),
            max_parallel_generations=min(
                requested_assumptions.max_parallel_generations,
                self.max_parallel_generations,
            ),
            max_parallel_judgments=min(
                requested_assumptions.max_parallel_judgments,
                self.max_parallel_judgments,
            ),
            pricing=requested_assumptions.pricing,
        )
        budget = PreflightBudget(
            max_provider_calls=_tightest_integer(
                requested_budget.max_provider_calls,
                self.max_provider_calls,
            ),
            max_total_tokens=_tightest_integer(
                requested_budget.max_total_tokens,
                self.max_total_tokens,
            ),
            max_cost_usd=requested_budget.max_cost_usd,
            max_duration_seconds=_tightest_number(
                requested_budget.max_duration_seconds,
                self.max_duration_seconds,
            ),
        )
        policy = {
            "schema_version": "pairwise-admission-policy/v1",
            "server_enforced": True,
            "assumptions_hardened": assumptions != requested_assumptions,
            "server_limits": {
                "max_provider_calls": self.max_provider_calls,
                "max_total_tokens": self.max_total_tokens,
                "max_duration_seconds": self.max_duration_seconds,
                "max_parallel_generations": self.max_parallel_generations,
                "max_parallel_judgments": self.max_parallel_judgments,
                "max_chars_per_token": self.max_chars_per_token,
            },
            "assumption_floors": {
                "candidate_output_chars": (
                    self.minimum_candidate_output_chars.to_dict(integral=True)
                ),
                "judge_output_tokens_per_call": (
                    self.minimum_judge_output_tokens_per_call.to_dict(integral=True)
                ),
                "generator_latency_seconds": (
                    self.minimum_generator_latency_seconds.to_dict()
                ),
                "judge_latency_seconds": (
                    self.minimum_judge_latency_seconds.to_dict()
                ),
                "generator_request_overhead_chars": (
                    self.minimum_generator_request_overhead_chars
                ),
                "judge_request_overhead_chars": (
                    self.minimum_judge_request_overhead_chars
                ),
            },
            "client_limits_can_only_tighten": True,
            "server_cost_limit_enforced": False,
        }
        return assumptions, budget, policy


def pairwise_admission_policy_from_settings(settings: Any) -> PairwiseAdmissionPolicy:
    return PairwiseAdmissionPolicy(
        max_provider_calls=settings.pairwise_preflight_max_provider_calls,
        max_total_tokens=settings.pairwise_preflight_max_total_tokens,
        max_duration_seconds=settings.pairwise_preflight_max_duration_seconds,
        max_parallel_generations=settings.pairwise_preflight_max_parallel_generations,
        max_parallel_judgments=settings.pairwise_preflight_max_parallel_judgments,
    )


def pairwise_admission_signing_key_is_valid(signing_key: str) -> bool:
    return (
        isinstance(signing_key, str)
        and len(signing_key.encode("utf-8")) >= MIN_ADMISSION_SIGNING_KEY_BYTES
    )


def _receipt_claims(
    estimate: PairwisePreflightEstimate,
    *,
    issued_at: int,
    expires_at: int,
) -> dict[str, Any]:
    result = estimate.to_dict()
    policy = result.get("admission_policy")
    if not isinstance(policy, Mapping) or not policy.get("server_enforced"):
        raise ValueError("pairwise admission receipt requires a server policy")
    policy_digest = policy.get("policy_digest")
    schedule_digest = result.get("schedule", {}).get("schedule_digest")
    if not isinstance(policy_digest, str) or not isinstance(schedule_digest, str):
        raise ValueError("pairwise admission receipt is missing estimate digests")
    return {
        "schema_version": PAIRWISE_ADMISSION_RECEIPT_SCHEMA,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "schedule_digest": schedule_digest,
        "policy_digest": policy_digest,
        "estimate_digest": canonical_hash(
            result,
            prefix="pairwise_estimate_",
        ),
    }


def _receipt_signature(
    claims: Mapping[str, Any],
    *,
    payload: Mapping[str, Any],
    subject: str,
    signing_key: str,
) -> str:
    message = {
        "claims": claims,
        "request": payload,
        "subject": subject,
    }
    return hmac.new(
        signing_key.encode("utf-8"),
        canonical_json(message).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def create_pairwise_admission_receipt(
    payload: Mapping[str, Any],
    estimate: PairwisePreflightEstimate,
    *,
    subject: str,
    signing_key: str,
    ttl_seconds: int = 15 * 60,
    now_unix: int | None = None,
) -> dict[str, Any]:
    """Create a private-request-bound receipt without persisting request content."""

    if not estimate.within_budget:
        raise ValueError("pairwise admission receipt requires an approved budget")
    if not pairwise_admission_signing_key_is_valid(signing_key):
        raise ValueError(
            "pairwise admission signing key must contain at least "
            f"{MIN_ADMISSION_SIGNING_KEY_BYTES} bytes"
        )
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("pairwise admission subject must be a non-empty string")
    if (
        isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, int)
        or not 1 <= ttl_seconds <= MAX_ADMISSION_RECEIPT_TTL_SECONDS
    ):
        raise ValueError(
            "pairwise admission receipt ttl_seconds must be between 1 and "
            f"{MAX_ADMISSION_RECEIPT_TTL_SECONDS}"
        )
    issued_at = int(time.time()) if now_unix is None else int(now_unix)
    claims = _receipt_claims(
        estimate,
        issued_at=issued_at,
        expires_at=issued_at + ttl_seconds,
    )
    signature = _receipt_signature(
        claims,
        payload=payload,
        subject=subject,
        signing_key=signing_key,
    )
    return {
        "available": True,
        **claims,
        "receipt_id": f"pairwise_admission_{signature[:32]}",
        "signature": signature,
    }


def verify_pairwise_admission_receipt(
    receipt: Mapping[str, Any],
    payload: Mapping[str, Any],
    estimate: PairwisePreflightEstimate,
    *,
    subject: str,
    signing_key: str,
    now_unix: int | None = None,
) -> None:
    """Validate a receipt against a resubmitted request and current preflight."""

    if not pairwise_admission_signing_key_is_valid(signing_key):
        raise ValueError("invalid pairwise admission receipt")
    if not isinstance(receipt, Mapping):
        raise ValueError("invalid pairwise admission receipt")
    expected_fields = {
        "available",
        "schema_version",
        "issued_at",
        "expires_at",
        "schedule_digest",
        "policy_digest",
        "estimate_digest",
        "receipt_id",
        "signature",
    }
    if set(receipt) != expected_fields or receipt.get("available") is not True:
        raise ValueError("invalid pairwise admission receipt")
    issued_at = receipt.get("issued_at")
    expires_at = receipt.get("expires_at")
    if (
        isinstance(issued_at, bool)
        or not isinstance(issued_at, int)
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, int)
        or expires_at <= issued_at
        or expires_at - issued_at > MAX_ADMISSION_RECEIPT_TTL_SECONDS
    ):
        raise ValueError("invalid pairwise admission receipt")
    current_time = int(time.time()) if now_unix is None else int(now_unix)
    if issued_at > current_time + 60:
        raise ValueError("invalid pairwise admission receipt")
    if current_time >= expires_at:
        raise ValueError("pairwise admission receipt expired")

    expected_claims = _receipt_claims(
        estimate,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    claims = {
        key: receipt.get(key)
        for key in (
            "schema_version",
            "issued_at",
            "expires_at",
            "schedule_digest",
            "policy_digest",
            "estimate_digest",
        )
    }
    if claims != expected_claims:
        raise ValueError("invalid pairwise admission receipt")
    signature = receipt.get("signature")
    expected_signature = _receipt_signature(
        claims,
        payload=payload,
        subject=subject,
        signing_key=signing_key,
    )
    if (
        not isinstance(signature, str)
        or not hmac.compare_digest(signature, expected_signature)
        or receipt.get("receipt_id")
        != f"pairwise_admission_{expected_signature[:32]}"
    ):
        raise ValueError("invalid pairwise admission receipt")
