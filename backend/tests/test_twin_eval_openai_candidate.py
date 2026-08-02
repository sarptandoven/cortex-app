from __future__ import annotations

import pickle
import unittest
from dataclasses import replace

from backend.app.twin_eval.execution import (
    TrustedPairwiseAdapterEndpoint,
)
from backend.app.twin_eval.openai_candidate import (
    OPENAI_CANDIDATE_PARSER_REVISION,
    OpenAICandidateResponseError,
    parse_openai_candidate_response,
)


class OpenAICandidateParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.endpoint = TrustedPairwiseAdapterEndpoint(
            adapter_revision="openai-candidate@sha256:abc",
            endpoint_id="openai-responses",
            model_id="gpt-test-2026-01-01",
            request_schema_version="openai-responses-request/v1",
            response_parser_revision=(
                OPENAI_CANDIDATE_PARSER_REVISION
            ),
            timeout_seconds=30,
            max_input_chars=100_000,
            max_output_chars=1_000,
            max_input_tokens=10_000,
            max_output_tokens=1_000,
            supports_idempotency=True,
            idempotency_field="Idempotency-Key",
        )

    def _response(self, text: str = "A private candidate.") -> dict:
        return {
            "id": "resp_test_123",
            "object": "response",
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "model": self.endpoint.model_id,
            "output": [
                {
                    "id": "reasoning_1",
                    "type": "reasoning",
                    "summary": [],
                },
                {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": text,
                            "annotations": [],
                        }
                    ],
                },
            ],
            "usage": {
                "input_tokens": 12,
                "output_tokens": 4,
                "total_tokens": 16,
                "input_tokens_details": {"cached_tokens": 2},
                "output_tokens_details": {"reasoning_tokens": 1},
            },
        }

    def test_completed_response_normalizes_to_opaque_outcome(self) -> None:
        secret = "do-not-log-this-candidate"
        outcome = parse_openai_candidate_response(
            self._response(secret), self.endpoint
        )
        self.assertEqual(outcome.response_id, "resp_test_123")
        self.assertEqual(outcome.model_id, self.endpoint.model_id)
        self.assertNotIn(secret, repr(outcome))
        snapshot = outcome._snapshot()
        self.assertEqual(snapshot["output_text"], secret)
        self.assertEqual(
            snapshot["adapter_revision"],
            self.endpoint.adapter_revision,
        )
        self.assertEqual(
            snapshot["response_parser_revision"],
            OPENAI_CANDIDATE_PARSER_REVISION,
        )
        self.assertEqual(snapshot["usage"]["cached_input_tokens"], 2)
        self.assertEqual(
            snapshot["usage"]["reasoning_output_tokens"], 1
        )
        with self.assertRaisesRegex(
            TypeError, "cannot be serialized"
        ):
            pickle.dumps(outcome)
        with self.assertRaisesRegex(AttributeError, "immutable"):
            outcome.response_id = "forged"

    def test_parser_and_model_must_match_trusted_endpoint(self) -> None:
        with self.assertRaisesRegex(
            OpenAICandidateResponseError,
            "parser_revision_mismatch",
        ):
            parse_openai_candidate_response(
                self._response(),
                replace(
                    self.endpoint,
                    response_parser_revision="other-parser/v1",
                ),
            )
        response = self._response()
        response["model"] = "untrusted-model"
        with self.assertRaisesRegex(
            OpenAICandidateResponseError, "model_mismatch"
        ):
            parse_openai_candidate_response(response, self.endpoint)

    def test_nonterminal_incomplete_and_failed_are_distinct(self) -> None:
        cases = {
            "queued": "provider_nonterminal",
            "in_progress": "provider_nonterminal",
            "incomplete": "provider_incomplete",
            "failed": "provider_failed",
            "cancelled": "provider_failed",
        }
        for status, code in cases.items():
            with self.subTest(status=status):
                response = self._response()
                response["status"] = status
                with self.assertRaisesRegex(
                    OpenAICandidateResponseError, code
                ):
                    parse_openai_candidate_response(
                        response, self.endpoint
                    )
        wrong_model = self._response()
        wrong_model["status"] = "failed"
        wrong_model["model"] = "wrong-model"
        with self.assertRaises(
            OpenAICandidateResponseError
        ) as caught:
            parse_openai_candidate_response(
                wrong_model, self.endpoint
            )
        self.assertEqual(caught.exception.code, "model_mismatch")
        self.assertEqual(
            caught.exception.disposition,
            "invalid_provider_response",
        )
        missing_id = self._response()
        missing_id["status"] = "incomplete"
        missing_id["id"] = None
        with self.assertRaises(
            OpenAICandidateResponseError
        ) as caught:
            parse_openai_candidate_response(
                missing_id, self.endpoint
            )
        self.assertEqual(caught.exception.code, "invalid_response_id")
        self.assertEqual(
            caught.exception.disposition,
            "invalid_provider_response",
        )

    def test_provider_error_and_refusal_content_never_leaks(self) -> None:
        secret = "provider-echoed-private-prompt"
        failed = self._response()
        failed["error"] = {"message": secret, "code": "server_error"}
        with self.assertRaises(OpenAICandidateResponseError) as caught:
            parse_openai_candidate_response(failed, self.endpoint)
        self.assertEqual(caught.exception.code, "provider_failed")
        self.assertEqual(
            caught.exception.disposition, "definitive_failure"
        )
        self.assertNotIn(secret, str(caught.exception))

        refusal = self._response()
        refusal["output"][1]["content"] = [
            {"type": "refusal", "refusal": secret}
        ]
        with self.assertRaises(OpenAICandidateResponseError) as caught:
            parse_openai_candidate_response(refusal, self.endpoint)
        self.assertEqual(caught.exception.code, "provider_refusal")
        self.assertNotIn(secret, str(caught.exception))

    def test_tool_output_and_multiple_messages_fail_closed(self) -> None:
        tool = self._response()
        tool["output"].append(
            {"type": "web_search_call", "status": "completed"}
        )
        with self.assertRaisesRegex(
            OpenAICandidateResponseError, "unexpected_output_item"
        ):
            parse_openai_candidate_response(tool, self.endpoint)

        multiple = self._response()
        multiple["output"].append(dict(multiple["output"][1]))
        with self.assertRaisesRegex(
            OpenAICandidateResponseError, "invalid_output"
        ):
            parse_openai_candidate_response(multiple, self.endpoint)

    def test_empty_and_oversized_output_fail_closed(self) -> None:
        for text, code in (
            ("   ", "empty_output"),
            ("x" * 1_001, "output_too_large"),
        ):
            with self.subTest(code=code):
                with self.assertRaisesRegex(
                    OpenAICandidateResponseError, code
                ):
                    parse_openai_candidate_response(
                        self._response(text), self.endpoint
                    )

    def test_usage_requires_exact_nonnegative_integer_accounting(
        self,
    ) -> None:
        bad_usage = (
            None,
            {
                "input_tokens": True,
                "output_tokens": 4,
                "total_tokens": 5,
            },
            {
                "input_tokens": 12,
                "output_tokens": 4,
                "total_tokens": 99,
            },
            {
                "input_tokens": 12,
                "output_tokens": 4,
                "total_tokens": 16,
                "input_tokens_details": {"cached_tokens": 13},
            },
            {
                "input_tokens": 10_001,
                "output_tokens": 4,
                "total_tokens": 10_005,
            },
            {
                "input_tokens": 12,
                "output_tokens": 4,
                "total_tokens": 16,
                "output_tokens_details": {"reasoning_tokens": 5},
            },
        )
        for usage in bad_usage:
            with self.subTest(usage=usage):
                response = self._response()
                response["usage"] = usage
                with self.assertRaises(OpenAICandidateResponseError):
                    parse_openai_candidate_response(
                        response, self.endpoint
                    )

    def test_sdk_convenience_text_cannot_bypass_raw_output_shape(self) -> None:
        response = self._response()
        response["output_text"] = "bypass"
        response["output"] = []
        with self.assertRaisesRegex(
            OpenAICandidateResponseError, "invalid_output"
        ):
            parse_openai_candidate_response(response, self.endpoint)
        response = self._response()
        response["object"] = "chat.completion"
        with self.assertRaisesRegex(
            OpenAICandidateResponseError, "invalid_response"
        ):
            parse_openai_candidate_response(response, self.endpoint)


if __name__ == "__main__":
    unittest.main()
