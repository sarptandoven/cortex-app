from __future__ import annotations

import re
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from backend.app.database import init_db
from backend.app.extractor import now_iso
from backend.app.storage import CortexStore
from backend.app.twin_eval import EvaluationPrompt
from backend.app.twin_eval.profile_adapter import (
    CortexHeldOutProfileBuilder,
    CortexProfileBuilderConfig,
    InsufficientProfileEvidence,
    MalformedContextPack,
    ProfileBuildError,
    ProfileLimitExceeded,
)


def _item(
    memory_id: str,
    content: str,
    *,
    author_class: str = "user",
    trust_score: float = 1.0,
    source: str = "notes",
) -> dict:
    return {
        "memory_id": memory_id,
        "content": content,
        "layer": "preference",
        "author_class": author_class,
        "trust_score": trust_score,
        "source": source,
        "source_url": f"local-file:///{memory_id}.md",
    }


def _pack(items: list[dict], *, conflicts: list[dict] | None = None) -> dict:
    return {
        "layers": [
            {
                "layer": "identity",
                "items": items,
            }
        ],
        "conflicts": conflicts or [],
    }


class _FakeContext:
    def __init__(self, packs: dict[str, dict]) -> None:
        self.packs = packs
        self.calls: list[tuple[str, str, dict]] = []
        self.snapshot_digests = ["snapshot-stable"]

    def assemble_context(self, user_id: str, task: str = "", **kwargs):
        self.calls.append((user_id, task, kwargs))
        return deepcopy(self.packs[task])

    def redact_export_text(self, value: str) -> str:
        value = re.sub(
            r"\bsk-[A-Za-z0-9_-]{20,}\b",
            "[REDACTED_OPENAI_KEY]",
            value,
        )
        value = re.sub(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
            "[REDACTED_EMAIL]",
            value,
        )
        return re.sub(r"/Users/\S+", "[REDACTED_PATH]", value)

    def pairwise_profile_snapshot_digest(self, user_id: str) -> str:
        del user_id
        if len(self.snapshot_digests) > 1:
            return self.snapshot_digests.pop(0)
        return self.snapshot_digests[0]


class CortexProfileAdapterUnitTests(unittest.TestCase):
    def test_build_is_prompt_order_independent_and_prompt_scoped(self) -> None:
        context = _FakeContext(
            {
                "Task A": _pack(
                    [
                        _item("shared", "Keep answers concise."),
                        _item("only-a", "Task A needs a decision first."),
                    ]
                ),
                "Task B": _pack(
                    [
                        _item("only-b", "Task B should use bullets."),
                        _item("shared", "Keep answers concise."),
                    ]
                ),
            }
        )
        builder = CortexHeldOutProfileBuilder(context)
        prompts = (
            EvaluationPrompt("b", "Task B"),
            EvaluationPrompt("a", "Task A"),
        )

        first = builder.build(
            "user-1",
            prompts,
            as_of="2026-07-24T12:00:00-07:00",
        )
        second = builder.build(
            "user-1",
            tuple(reversed(prompts)),
            as_of="2026-07-24T19:00:00Z",
        )

        self.assertEqual(first.profile, second.profile)
        self.assertEqual(first.manifest, second.manifest)
        self.assertEqual(
            tuple(item.memory_id for item in first.profile.items),
            ("only-a", "only-b", "shared"),
        )
        self.assertEqual(
            first.citation_policy.allowed_memory_ids,
            {
                "a": ("only-a", "shared"),
                "b": ("only-b", "shared"),
            },
        )
        self.assertTrue(all(item.source_url is None for item in first.profile.items))
        self.assertEqual([call[1] for call in context.calls[:2]], ["Task A", "Task B"])
        for _, _, options in context.calls:
            self.assertEqual(options["as_of"], "2026-07-24T19:00:00Z")
            self.assertFalse(options["record_reuse"])
            self.assertFalse(options["use_hot_cache"])
            self.assertFalse(options["pin"])

    def test_filters_non_owner_zero_trust_uncited_and_tasks(self) -> None:
        context = _FakeContext(
            {
                "Task": {
                    "layers": [
                        {
                            "layer": "identity",
                            "items": [
                                _item("eligible", "Owner preference."),
                                _item(
                                    "agent",
                                    "Agent suggestion.",
                                    author_class="agent",
                                ),
                                _item("zero", "Untrusted.", trust_score=0),
                                {
                                    **_item("uncited", "No source.", source=""),
                                    "source_url": None,
                                },
                                {
                                    "task_id": "task-1",
                                    "content": "Open task.",
                                },
                            ],
                        }
                    ],
                    "conflicts": [],
                }
            }
        )

        bundle = CortexHeldOutProfileBuilder(context).build(
            "user-1",
            (EvaluationPrompt("p", "Task"),),
            as_of="2026-07-24T19:00:00Z",
        )

        self.assertEqual(
            tuple(item.memory_id for item in bundle.profile.items),
            ("eligible",),
        )
        excluded = dict(bundle.coverage[0].excluded_by_reason)
        self.assertEqual(excluded["non_owner"], 1)
        self.assertEqual(excluded["non_positive_trust"], 1)
        self.assertEqual(excluded["uncited"], 1)
        self.assertEqual(excluded["non_memory"], 1)

    def test_redacts_again_and_never_exposes_source_locator(self) -> None:
        secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
        context = _FakeContext(
            {
                "Task": _pack(
                    [
                        _item(
                            "private",
                            f"Email owner@example.com key {secret} at /Users/alice/private.txt",
                        )
                    ]
                )
            }
        )

        bundle = CortexHeldOutProfileBuilder(context).build(
            "user-1",
            (EvaluationPrompt("p", "Task"),),
            as_of="2026-07-24T19:00:00Z",
        )
        serialized = bundle.profile.items[0].content

        self.assertNotIn(secret, serialized)
        self.assertNotIn("owner@example.com", serialized)
        self.assertNotIn("/Users/alice", serialized)
        self.assertIsNone(bundle.profile.items[0].source_url)

    def test_resolves_only_explicit_eligible_conflict_preference(self) -> None:
        context = _FakeContext(
            {
                "Task": _pack(
                    [
                        _item("old", "Use long updates."),
                        _item("current", "Use short updates."),
                    ],
                    conflicts=[
                        {
                            "memory_ids": ["old", "current"],
                            "prefer": "current",
                        }
                    ],
                )
            }
        )

        bundle = CortexHeldOutProfileBuilder(context).build(
            "user-1",
            (EvaluationPrompt("p", "Task"),),
            as_of="2026-07-24T19:00:00Z",
        )

        self.assertEqual(
            tuple(item.memory_id for item in bundle.profile.items),
            ("current",),
        )
        self.assertEqual(bundle.coverage[0].conflicts_resolved, 1)

        context.packs["Task"]["conflicts"][0]["prefer"] = "missing"
        with self.assertRaisesRegex(ProfileBuildError, "unresolved conflict"):
            CortexHeldOutProfileBuilder(context).build(
                "user-1",
                (EvaluationPrompt("p", "Task"),),
                as_of="2026-07-24T19:00:00Z",
            )

    def test_sparse_and_changed_evidence_fail_without_partial_bundle(self) -> None:
        context = _FakeContext(
            {
                "Has evidence": _pack([_item("shared", "First snapshot.")]),
                "No evidence": _pack(
                    [_item("agent", "Not owner evidence.", author_class="agent")]
                ),
            }
        )
        with self.assertRaises(InsufficientProfileEvidence) as raised:
            CortexHeldOutProfileBuilder(context).build(
                "user-1",
                (
                    EvaluationPrompt("a", "Has evidence"),
                    EvaluationPrompt("b", "No evidence"),
                ),
                as_of="2026-07-24T19:00:00Z",
            )
        self.assertEqual(raised.exception.prompt_ids, ("b",))

        context.packs["No evidence"] = _pack(
            [_item("shared", "Changed snapshot.")]
        )
        with self.assertRaisesRegex(ProfileBuildError, "changed"):
            CortexHeldOutProfileBuilder(context).build(
                "user-1",
                (
                    EvaluationPrompt("a", "Has evidence"),
                    EvaluationPrompt("b", "No evidence"),
                ),
                as_of="2026-07-24T19:00:00Z",
            )

    def test_malformed_pack_and_limits_fail_closed(self) -> None:
        with self.assertRaises(MalformedContextPack):
            CortexHeldOutProfileBuilder(
                _FakeContext({"Task": {"layers": "bad"}})
            ).build(
                "user-1",
                (EvaluationPrompt("p", "Task"),),
                as_of="2026-07-24T19:00:00Z",
            )

        builder = CortexHeldOutProfileBuilder(
            _FakeContext({"Task": _pack([_item("m", "12345")])}),
            CortexProfileBuilderConfig(max_item_chars=4),
        )
        with self.assertRaisesRegex(ProfileLimitExceeded, "max_item_chars=4"):
            builder.build(
                "user-1",
                (EvaluationPrompt("p", "Task"),),
                as_of="2026-07-24T19:00:00Z",
            )

        with self.assertRaisesRegex(ProfileLimitExceeded, "retrieval_query"):
            CortexHeldOutProfileBuilder(
                _FakeContext({}),
                CortexProfileBuilderConfig(max_retrieval_query_chars=4),
            ).build(
                "user-1",
                (EvaluationPrompt("p", "12345"),),
                as_of="2026-07-24T19:00:00Z",
            )

    def test_requires_explicit_timezone_aware_snapshot(self) -> None:
        builder = CortexHeldOutProfileBuilder(_FakeContext({}))
        for value in ("", "2026-07-24T19:00:00", "not-a-time"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "timezone-aware"):
                    builder.build(
                        "user-1",
                        (EvaluationPrompt("p", "Task"),),
                        as_of=value,
                    )

    def test_detects_cortex_mutation_during_multi_prompt_build(self) -> None:
        context = _FakeContext(
            {
                "Task A": _pack([_item("a", "Evidence A.")]),
                "Task B": _pack([_item("b", "Evidence B.")]),
            }
        )
        context.snapshot_digests = ["before", "after"]

        with self.assertRaisesRegex(ProfileBuildError, "changed during"):
            CortexHeldOutProfileBuilder(context).build(
                "user-1",
                (
                    EvaluationPrompt("a", "Task A"),
                    EvaluationPrompt("b", "Task B"),
                ),
                as_of="2026-07-24T19:00:00Z",
            )

    def test_unrelated_build_guard_change_does_not_change_profile_identity(
        self,
    ) -> None:
        context = _FakeContext(
            {"Task": _pack([_item("evidence", "Stable evidence.")])}
        )
        builder = CortexHeldOutProfileBuilder(context)
        prompts = (EvaluationPrompt("p", "Task"),)

        first = builder.build(
            "user-1",
            prompts,
            as_of="2026-07-24T19:00:00Z",
        )
        context.snapshot_digests = ["new-unrelated-corpus-revision"]
        second = builder.build(
            "user-1",
            prompts,
            as_of="2026-07-24T19:00:00Z",
        )

        self.assertEqual(first.profile, second.profile)
        self.assertEqual(first.profile.fingerprint, second.profile.fingerprint)
        self.assertNotEqual(
            first.manifest.snapshot_digest,
            second.manifest.snapshot_digest,
        )

    def test_rejects_token_budget_that_cortex_would_silently_clamp(self) -> None:
        for value in (299, 6_001):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError,
                    "between 300 and 6000",
                ):
                    CortexProfileBuilderConfig(
                        token_budget_per_prompt=value
                    )


class CortexProfileAdapterIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        init_db(root / "cortex.db")
        self.store = CortexStore(root / "cortex.db", root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "profile-user"
        self.store.update_settings(
            self.user_id,
            {
                "review_new_captures": False,
                "redact_sensitive_context": False,
            },
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(
        self,
        user_id: str,
        memory_id: str,
        content: str,
        *,
        layer: str = "preference",
    ) -> None:
        extracted = {
            "_timestamp": now_iso(),
            "summary": content,
            "records": [
                {
                    "id": memory_id,
                    "kind": "preference",
                    "layer": layer,
                    "content": content,
                    "confidence": "confirmed",
                    "importance": 3,
                    "topics": [],
                    "entity_ids": [],
                }
            ],
            "tasks": [],
            "entities": [],
        }
        self.store.save_capture(
            user_id=user_id,
            content=content,
            source="macos",
            source_url=None,
            title="",
            extracted=extracted,
            cite_capture_provenance=True,
        )

    def test_real_context_engine_enforces_tenant_scope_and_export_redaction(self) -> None:
        secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
        aws_key = "AKIAABCDEFGHIJKLMNOP"
        jwt = "eyJabcdefgh.eyJijklmnop.signature12345"
        bearer = "Bearer abcdefghijklmnopqrstuvwxyz"
        aws_secret = "aws_secret_access_key=abcdefghijklmnopqrstuvwxyz1234567890"
        private_key = (
            "-----BEGIN PRIVATE KEY-----\n"
            "very-private-material\n"
            "-----END PRIVATE KEY-----"
        )
        incomplete_private_key = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "incomplete-private-material"
        )
        self._seed(
            self.user_id,
            "owned",
            (
                f"Atlas credential is {secret}; email owner@example.com; "
                f"AWS {aws_key}; {aws_secret}; JWT {jwt}; {bearer}; "
                f"{private_key}; {incomplete_private_key}"
            ),
        )
        self._seed(
            "other-user",
            "foreign",
            "Atlas credential belongs to another user.",
        )

        bundle = CortexHeldOutProfileBuilder(self.store).build(
            self.user_id,
            (EvaluationPrompt("p", "Atlas credential"),),
            as_of=now_iso(),
        )
        serialized = " ".join(item.content for item in bundle.profile.items)

        self.assertIn("owned", bundle.citation_policy.allowed_memory_ids["p"])
        self.assertNotIn("foreign", bundle.citation_policy.allowed_memory_ids["p"])
        self.assertNotIn(secret, serialized)
        self.assertNotIn("owner@example.com", serialized)
        self.assertNotIn(aws_key, serialized)
        self.assertNotIn(jwt, serialized)
        self.assertNotIn("very-private-material", serialized)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz1234567890", serialized)
        self.assertNotIn("incomplete-private-material", serialized)
        self.assertIn("[REDACTED_OPENAI_KEY]", serialized)
        self.assertIn("[REDACTED_EMAIL]", serialized)
        self.assertIn("[REDACTED_AWS_ACCESS_KEY]", serialized)
        self.assertIn("[REDACTED_JWT]", serialized)
        self.assertIn("[REDACTED_PRIVATE_KEY]", serialized)
        self.assertIn("Bearer [REDACTED_TOKEN]", serialized)

    def test_real_context_engine_resolves_structured_preference_conflict(self) -> None:
        # The ids intentionally make "a-current" win the deterministic ranking-order
        # fallback when both captures share the same second-level timestamp.
        self._seed(
            self.user_id,
            "z-stale",
            "The storage backend is SQLite.",
        )
        self._seed(
            self.user_id,
            "a-current",
            "The storage backend is PostgreSQL.",
        )

        bundle = CortexHeldOutProfileBuilder(self.store).build(
            self.user_id,
            (
                EvaluationPrompt(
                    "p",
                    "Write a short update about the storage backend.",
                ),
            ),
            as_of=now_iso(),
        )

        self.assertEqual(
            bundle.citation_policy.allowed_memory_ids["p"],
            ("a-current",),
        )
        self.assertEqual(bundle.coverage[0].conflicts_resolved, 1)
        self.assertEqual(
            dict(bundle.coverage[0].excluded_by_reason)[
                "superseded_conflict"
            ],
            1,
        )

    def test_selected_conflicts_are_not_truncated_by_global_corpus_size(
        self,
    ) -> None:
        for index in range(101):
            self._seed(
                self.user_id,
                f"a-filler-{index:03d}",
                f"Historical note number {index}.",
                layer="semantic",
            )
        self._seed(
            self.user_id,
            "zz-stale",
            "The storage backend is SQLite.",
        )
        self._seed(
            self.user_id,
            "zy-current",
            "The storage backend is PostgreSQL.",
        )

        bundle = CortexHeldOutProfileBuilder(self.store).build(
            self.user_id,
            (
                EvaluationPrompt(
                    "p",
                    "Write a short update about the storage backend.",
                ),
            ),
            as_of=now_iso(),
        )

        allowed = bundle.citation_policy.allowed_memory_ids["p"]
        self.assertIn("zy-current", allowed)
        self.assertNotIn("zz-stale", allowed)
        self.assertEqual(bundle.coverage[0].conflicts_resolved, 1)


if __name__ == "__main__":
    unittest.main()
