from __future__ import annotations

import json
import os
import threading
import unittest
from copy import deepcopy
from dataclasses import replace
from unittest import mock
from urllib import error, request

from backend.app import standalone_server
from backend.app.config import load_settings
from backend.app.twin_eval import (
    AllPairsStrategy,
    CitedProfileItem,
    EvaluationPrompt,
    HeldOutProfile,
    PairwiseAdmissionPolicy,
    build_pairwise_preflight_response,
    create_pairwise_admission_receipt,
    estimate_pairwise_preflight_request,
    estimate_pairwise_workload,
    require_pairwise_admission_receipt,
    require_pairwise_preflight_budget,
    verify_pairwise_admission_receipt,
)

_SIGNING_KEY = "pairwise-test-signing-key-32-bytes-minimum"


def _valid_payload() -> dict:
    return {
        "profile": {
            "profile_id": "profile-1",
            "items": [
                {
                    "memory_id": "memory-1",
                    "content": "Use short, direct answers.",
                }
            ],
        },
        "prompts": [
            {
                "prompt_id": "prompt-1",
                "text": "Draft a project update.",
            }
        ],
        "system_ids": ["candidate-a", "candidate-b"],
        "strategy": {
            "type": "repeated_swapped",
            "repetitions": 2,
            "shuffle": False,
        },
        "budget": {"max_provider_calls": 10},
    }


class PairwisePreflightApplicationTests(unittest.TestCase):
    def test_product_request_is_zero_call_and_content_redacted(self) -> None:
        estimate = estimate_pairwise_preflight_request(_valid_payload()).to_dict()
        serialized = json.dumps(estimate)

        self.assertEqual(estimate["provider_calls_made"], 0)
        self.assertEqual(estimate["schedule"]["candidate_generations"], 2)
        self.assertEqual(estimate["schedule"]["raw_judgments"], 4)
        self.assertEqual(estimate["schedule"]["logical_comparisons"], 2)
        self.assertTrue(estimate["budget"]["within_budget"])
        self.assertNotIn("Use short, direct answers.", serialized)
        self.assertNotIn("Draft a project update.", serialized)

    def test_budget_gate_rejects_upper_bound_violation(self) -> None:
        payload = _valid_payload()
        payload["budget"] = {"max_provider_calls": 1}
        with self.assertRaisesRegex(ValueError, "provider_calls"):
            require_pairwise_preflight_budget(payload)

    def test_signed_receipt_binds_private_request_without_exposing_it(self) -> None:
        payload = _valid_payload()
        estimate = estimate_pairwise_preflight_request(
            payload,
            policy=PairwiseAdmissionPolicy(),
        )
        receipt = create_pairwise_admission_receipt(
            payload,
            estimate,
            subject="user-1",
            signing_key=_SIGNING_KEY,
            ttl_seconds=60,
            now_unix=1_000,
        )

        verify_pairwise_admission_receipt(
            receipt,
            payload,
            estimate,
            subject="user-1",
            signing_key=_SIGNING_KEY,
            now_unix=1_001,
        )
        serialized = json.dumps(receipt)
        self.assertTrue(receipt["available"])
        self.assertNotIn("Use short, direct answers.", serialized)
        self.assertNotIn("Draft a project update.", serialized)

    def test_receipt_rejects_tampering_expiry_and_cross_user_replay(self) -> None:
        payload = _valid_payload()
        estimate = estimate_pairwise_preflight_request(
            payload,
            policy=PairwiseAdmissionPolicy(),
        )
        receipt = create_pairwise_admission_receipt(
            payload,
            estimate,
            subject="user-1",
            signing_key=_SIGNING_KEY,
            ttl_seconds=60,
            now_unix=1_000,
        )
        tampered_payload = deepcopy(payload)
        tampered_payload["prompts"][0]["text"] = "Changed private prompt"

        with self.assertRaisesRegex(ValueError, "invalid"):
            verify_pairwise_admission_receipt(
                receipt,
                tampered_payload,
                estimate,
                subject="user-1",
                signing_key=_SIGNING_KEY,
                now_unix=1_001,
            )
        with self.assertRaisesRegex(ValueError, "invalid"):
            verify_pairwise_admission_receipt(
                receipt,
                payload,
                estimate,
                subject="user-2",
                signing_key=_SIGNING_KEY,
                now_unix=1_001,
            )
        with self.assertRaisesRegex(ValueError, "expired"):
            verify_pairwise_admission_receipt(
                receipt,
                payload,
                estimate,
                subject="user-1",
                signing_key=_SIGNING_KEY,
                now_unix=1_060,
            )

    def test_preflight_response_only_issues_receipt_after_admission(self) -> None:
        approved = build_pairwise_preflight_response(
            _valid_payload(),
            policy=PairwiseAdmissionPolicy(),
            subject="user-1",
            signing_key=_SIGNING_KEY,
            now_unix=1_000,
        )
        unsigned = build_pairwise_preflight_response(
            _valid_payload(),
            policy=PairwiseAdmissionPolicy(),
            subject="user-1",
        )
        over_budget_payload = _valid_payload()
        over_budget_payload["budget"] = {"max_provider_calls": 1}
        rejected = build_pairwise_preflight_response(
            over_budget_payload,
            policy=PairwiseAdmissionPolicy(),
            subject="user-1",
            signing_key=_SIGNING_KEY,
            now_unix=1_000,
        )

        self.assertTrue(approved["admission_receipt"]["available"])
        self.assertEqual(
            unsigned["admission_receipt"]["reason"],
            "server_signing_key_unavailable",
        )
        self.assertEqual(
            rejected["admission_receipt"]["reason"],
            "budget_exceeded",
        )

    def test_execution_gate_reruns_current_policy_before_receipt_verification(
        self,
    ) -> None:
        payload = _valid_payload()
        original_policy = PairwiseAdmissionPolicy()
        estimate = estimate_pairwise_preflight_request(
            payload,
            policy=original_policy,
        )
        receipt = create_pairwise_admission_receipt(
            payload,
            estimate,
            subject="user-1",
            signing_key=_SIGNING_KEY,
            now_unix=1_000,
        )

        verified = require_pairwise_admission_receipt(
            payload,
            receipt,
            policy=original_policy,
            subject="user-1",
            signing_key=_SIGNING_KEY,
            now_unix=1_001,
        )
        self.assertTrue(verified.within_budget)
        with self.assertRaisesRegex(ValueError, "invalid"):
            require_pairwise_admission_receipt(
                payload,
                receipt,
                policy=PairwiseAdmissionPolicy(max_provider_calls=999),
                subject="user-1",
                signing_key=_SIGNING_KEY,
                now_unix=1_001,
            )

    def test_server_policy_hardens_client_controlled_assumptions(self) -> None:
        payload = _valid_payload()
        payload["assumptions"] = {
            "candidate_output_chars": {
                "lower": 0,
                "expected": 1,
                "upper": 1,
            },
            "judge_output_tokens_per_call": {
                "lower": 0,
                "expected": 1,
                "upper": 1,
            },
            "generator_latency_seconds": {
                "lower": 0,
                "expected": 0,
                "upper": 0,
            },
            "judge_latency_seconds": {
                "lower": 0,
                "expected": 0,
                "upper": 0,
            },
            "chars_per_token": 100,
            "generator_request_overhead_chars": 0,
            "judge_request_overhead_chars": 0,
            "max_parallel_generations": 100,
            "max_parallel_judgments": 100,
        }
        payload["budget"] = {
            "max_provider_calls": 1_000_000,
            "max_total_tokens": 1_000_000_000,
            "max_duration_seconds": 1_000_000,
        }
        estimate = estimate_pairwise_preflight_request(
            payload,
            policy=PairwiseAdmissionPolicy(),
        ).to_dict()

        self.assertTrue(estimate["admission_policy"]["server_enforced"])
        self.assertTrue(estimate["admission_policy"]["assumptions_hardened"])
        self.assertEqual(
            estimate["assumptions"]["candidate_output_chars"],
            {"lower": 1000, "expected": 4000, "upper": 16000},
        )
        self.assertEqual(estimate["assumptions"]["chars_per_token"], 4)
        self.assertEqual(estimate["concurrency"]["generation"], 1)
        self.assertEqual(estimate["concurrency"]["judgment"], 1)
        self.assertEqual(estimate["budget"]["limits"]["max_provider_calls"], 1000)
        self.assertEqual(
            estimate["budget"]["limits"]["max_total_tokens"],
            10_000_000,
        )
        self.assertEqual(
            estimate["budget"]["limits"]["max_duration_seconds"],
            86_400,
        )
        self.assertTrue(
            estimate["admission_policy"]["policy_digest"].startswith(
                "pairwise_policy_"
            ),
        )

        repeated = estimate_pairwise_preflight_request(
            payload,
            policy=PairwiseAdmissionPolicy(),
        ).to_dict()
        default_request = estimate_pairwise_preflight_request(
            _valid_payload(),
            policy=PairwiseAdmissionPolicy(),
        ).to_dict()
        changed = estimate_pairwise_preflight_request(
            payload,
            policy=PairwiseAdmissionPolicy(max_provider_calls=999),
        ).to_dict()
        self.assertEqual(
            estimate["admission_policy"]["policy_digest"],
            repeated["admission_policy"]["policy_digest"],
        )
        self.assertEqual(
            estimate["admission_policy"]["policy_digest"],
            default_request["admission_policy"]["policy_digest"],
        )
        self.assertNotEqual(
            estimate["admission_policy"]["policy_digest"],
            changed["admission_policy"]["policy_digest"],
        )

    def test_client_budget_can_tighten_but_not_loosen_server_policy(self) -> None:
        payload = _valid_payload()
        payload["budget"] = {
            "max_provider_calls": 5,
            "max_total_tokens": 1_000_000_000,
        }
        estimate = estimate_pairwise_preflight_request(
            payload,
            policy=PairwiseAdmissionPolicy(),
        )
        self.assertFalse(estimate.within_budget)
        limits = estimate.to_dict()["budget"]["limits"]
        self.assertEqual(limits["max_provider_calls"], 5)
        self.assertEqual(limits["max_total_tokens"], 10_000_000)

    def test_request_rejects_unknown_fields_and_ambiguous_values(self) -> None:
        cases = (
            ("unknown top-level field", {"surprise": True}, "unknown fields"),
            ("boolean seed", {"seed": True}, "seed must"),
            (
                "duplicate system",
                {"system_ids": ["candidate-a", "candidate-a"]},
                "uniquely named",
            ),
            (
                "partial pricing",
                {"assumptions": {"pricing": {"generator_input_per_million_tokens": 1}}},
                "missing fields",
            ),
            (
                "unknown strategy",
                {"strategy": {"type": "round_robin"}},
                "strategy.type",
            ),
        )
        for name, update, expected in cases:
            with self.subTest(name=name):
                payload = _valid_payload()
                payload.update(update)
                with self.assertRaisesRegex(ValueError, expected):
                    estimate_pairwise_preflight_request(payload)

    def test_anchor_request_uses_exact_strategy_schedule(self) -> None:
        payload = _valid_payload()
        payload["system_ids"] = ["anchor", "b", "c"]
        payload["strategy"] = {
            "type": "anchor",
            "anchor_system_id": "anchor",
            "repetitions": 3,
            "swap_sides": True,
            "shuffle": False,
        }
        estimate = estimate_pairwise_preflight_request(payload).to_dict()
        self.assertEqual(estimate["schedule"]["raw_judgments"], 12)
        self.assertEqual(estimate["schedule"]["logical_comparisons"], 6)

    def test_profile_and_prompt_content_are_not_silently_trimmed(self) -> None:
        payload = _valid_payload()
        payload["profile"]["items"][0]["content"] = "  preserve evidence spacing  "
        payload["prompts"][0]["text"] = "  preserve prompt spacing  "
        request_estimate = estimate_pairwise_preflight_request(payload).to_dict()
        direct_estimate = estimate_pairwise_workload(
            HeldOutProfile(
                "profile-1",
                (
                    CitedProfileItem(
                        "memory-1",
                        "  preserve evidence spacing  ",
                    ),
                ),
            ),
            (
                EvaluationPrompt(
                    "prompt-1",
                    "  preserve prompt spacing  ",
                ),
            ),
            ("candidate-a", "candidate-b"),
            AllPairsStrategy(
                repetitions=2,
                swap_sides=True,
                shuffle=False,
                strategy_id="repeated_swapped",
            ),
        ).to_dict()
        self.assertEqual(
            request_estimate["schedule"]["root_seed"],
            direct_estimate["schedule"]["root_seed"],
        )
        self.assertEqual(
            request_estimate["schedule"]["schedule_digest"],
            direct_estimate["schedule"]["schedule_digest"],
        )

    def test_preflight_enforces_runner_input_ceiling(self) -> None:
        profile = HeldOutProfile(
            "large",
            (CitedProfileItem("m", "large evidence"),),
        )
        prompts = (EvaluationPrompt("p", "large prompt"),)
        with self.assertRaisesRegex(ValueError, "max_input_chars=10"):
            estimate_pairwise_workload(
                profile,
                prompts,
                ("a", "b"),
                AllPairsStrategy(),
                max_input_chars=10,
            )

    def test_oversized_builtin_schedule_is_rejected_before_plan_allocation(self) -> None:
        payload = _valid_payload()
        payload["prompts"] = [
            {"prompt_id": f"prompt-{index}", "text": "Evaluate."}
            for index in range(100)
        ]
        payload["strategy"] = {
            "type": "repeated_swapped",
            "repetitions": 10_000,
        }
        with self.assertRaisesRegex(ValueError, "max_plans=100000"):
            estimate_pairwise_preflight_request(payload)

    def test_operator_policy_settings_are_configurable_and_fail_safe(self) -> None:
        names = {
            "CORTEX_PAIRWISE_MAX_PROVIDER_CALLS": "77",
            "CORTEX_PAIRWISE_MAX_TOTAL_TOKENS": "123456",
            "CORTEX_PAIRWISE_MAX_DURATION_SECONDS": "321.5",
            "CORTEX_PAIRWISE_MAX_PARALLEL_GENERATIONS": "3",
            "CORTEX_PAIRWISE_MAX_PARALLEL_JUDGMENTS": "4",
            "CORTEX_PAIRWISE_ADMISSION_SIGNING_KEY": _SIGNING_KEY,
            "CORTEX_PAIRWISE_ADMISSION_RECEIPT_TTL_SECONDS": "1200",
        }
        with mock.patch.dict(os.environ, names, clear=False):
            settings = load_settings()
        self.assertEqual(settings.pairwise_preflight_max_provider_calls, 77)
        self.assertEqual(settings.pairwise_preflight_max_total_tokens, 123456)
        self.assertEqual(settings.pairwise_preflight_max_duration_seconds, 321.5)
        self.assertEqual(settings.pairwise_preflight_max_parallel_generations, 3)
        self.assertEqual(settings.pairwise_preflight_max_parallel_judgments, 4)
        self.assertEqual(settings.pairwise_admission_signing_key, _SIGNING_KEY)
        self.assertEqual(settings.pairwise_admission_receipt_ttl_seconds, 1200)

        invalid = {name: "invalid" for name in names}
        with mock.patch.dict(os.environ, invalid, clear=False):
            fallback = load_settings()
        self.assertEqual(fallback.pairwise_preflight_max_provider_calls, 1000)
        self.assertEqual(fallback.pairwise_preflight_max_total_tokens, 10_000_000)
        self.assertEqual(fallback.pairwise_preflight_max_duration_seconds, 86_400)


class _ScopedAuthStore:
    def authenticate_api_token(self, token: str, user_id: str | None = None):
        del user_id
        scopes = {
            "read-token": ["read"],
            "write-token": ["write"],
        }.get(token)
        if scopes is None:
            return None
        return {"user_id": "api-user", "scopes": scopes}

    def require_agent_access(self, user_id: str, scope: str) -> None:
        if user_id != "api-user" or scope != "read":
            raise PermissionError("unexpected access request")


class PairwisePreflightStandaloneApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.original_store = standalone_server.store
        cls.original_settings = standalone_server.settings
        cls.original_guards = standalone_server.REQUEST_GUARDS
        standalone_server.store = _ScopedAuthStore()
        standalone_server.settings = replace(
            cls.original_settings,
            api_key="",
            require_scoped_api_tokens=True,
        )
        standalone_server.REQUEST_GUARDS = standalone_server._RequestGuards()
        cls.server = standalone_server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            standalone_server.CortexRequestHandler,
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        standalone_server.store = cls.original_store
        standalone_server.settings = cls.original_settings
        standalone_server.REQUEST_GUARDS = cls.original_guards

    def _post(self, payload: dict, token: str | None):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        req = request.Request(
            self.base_url + "/v1/twin/pairwise/preflight",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            exc.close()
            return exc.code, json.loads(body)

    def test_read_scoped_token_can_preflight_over_real_http(self) -> None:
        status, body = self._post(_valid_payload(), "read-token")
        self.assertEqual(status, 200)
        self.assertEqual(body["provider_calls_made"], 0)
        self.assertEqual(body["schedule"]["total_provider_calls"], 6)
        self.assertTrue(body["admission_policy"]["server_enforced"])
        self.assertFalse(body["admission_receipt"]["available"])

    def test_route_issues_subject_bound_receipt_when_signing_is_configured(self) -> None:
        original = standalone_server.settings
        standalone_server.settings = replace(
            original,
            pairwise_admission_signing_key=_SIGNING_KEY,
        )
        try:
            status, body = self._post(_valid_payload(), "read-token")
        finally:
            standalone_server.settings = original

        self.assertEqual(status, 200)
        self.assertTrue(body["admission_receipt"]["available"])
        self.assertNotIn("Use short, direct answers.", json.dumps(body))

    def test_missing_or_wrong_scope_token_is_rejected(self) -> None:
        missing_status, _ = self._post(_valid_payload(), None)
        wrong_status, body = self._post(_valid_payload(), "write-token")
        self.assertEqual(missing_status, 401)
        self.assertEqual(wrong_status, 403)
        self.assertIn("read scope", body["detail"])

    def test_malformed_request_is_422_and_does_not_echo_private_content(self) -> None:
        payload = _valid_payload()
        payload["strategy"]["unexpected"] = "private-marker"
        status, body = self._post(payload, "read-token")
        serialized = json.dumps(body)
        self.assertEqual(status, 422)
        self.assertIn("unknown fields", body["detail"])
        self.assertNotIn("Use short, direct answers.", serialized)

    def test_route_uses_operator_ceiling_even_when_client_requests_more(self) -> None:
        original = standalone_server.settings
        standalone_server.settings = replace(
            original,
            pairwise_preflight_max_provider_calls=5,
        )
        try:
            payload = _valid_payload()
            payload["budget"] = {"max_provider_calls": 1_000_000}
            status, body = self._post(payload, "read-token")
        finally:
            standalone_server.settings = original
        self.assertEqual(status, 200)
        self.assertFalse(body["budget"]["within_budget"])
        self.assertEqual(body["budget"]["limits"]["max_provider_calls"], 5)


if __name__ == "__main__":
    unittest.main()
