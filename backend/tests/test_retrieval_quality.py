from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.database import init_db
from backend.app.storage import CortexStore, MEMORY_LAYERS
from scripts.retrieval_eval import (
    DISTRACTOR_MEMORIES,
    EXPANDED_SEED_MEMORY_COUNT,
    METRIC_K_VALUES,
    RETRIEVAL_CASES,
    RELATED_MEMORY_COMPANION_ID,
    RELATED_MEMORY_PRIMARY_ID,
    SECTOR_SCOPE_ATLAS_ID,
    SECTOR_SCOPE_BOREAL_ID,
    TEMPORAL_VALIDITY_CURRENT_ID,
    TEMPORAL_VALIDITY_EXCLUDED_IDS,
    evaluate_retrieval,
    seed_representative_memories,
)
from scripts.retrieval_scale_eval import TARGET_ID, TARGET_SOURCE_URL, run_retrieval_scale_eval


class RetrievalQualityHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "retrieval-quality.sqlite"
        self.vault_path = Path(self.tmp.name) / "Cortex.vault"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.vault_path)
        self.user_id = "retrieval-quality-test"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed_boost_ranking_memories(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})
        records = [
            {
                "id": "boost_writing_semantic",
                "kind": "claim",
                "layer": "semantic",
                "content": "Writing style atlas for engineering summaries is a generic launch copy search example.",
                "importance": 5,
                "topics": ["writing", "style", "atlas"],
            },
            {
                "id": "boost_writing_style",
                "kind": "style",
                "layer": "style",
                "content": "Writing style atlas for engineering summaries: use terse implementation notes and short paragraphs.",
                "importance": 1,
                "topics": ["writing", "style", "atlas"],
            },
            {
                "id": "boost_writing_negative",
                "kind": "negative",
                "layer": "negative",
                "content": "Writing style atlas for engineering summaries: do not use fluffy launch copy.",
                "importance": 1,
                "topics": ["writing", "style", "atlas"],
            },
            {
                "id": "boost_decision_semantic",
                "kind": "claim",
                "layer": "semantic",
                "content": "Planning alpha rank decision appears in a generic project note.",
                "importance": 5,
                "topics": ["planning", "alpha", "rank", "decision"],
            },
            {
                "id": "boost_decision_layer",
                "kind": "decision",
                "layer": "decision",
                "content": "Planning alpha rank decision: choose stdlib unittest for retrieval evaluation.",
                "importance": 1,
                "topics": ["planning", "alpha", "rank", "decision"],
            },
            {
                "id": "boost_preference_semantic",
                "kind": "claim",
                "layer": "semantic",
                "content": "Preference beta rank recommended option risks appears in a generic note.",
                "importance": 5,
                "topics": ["preference", "beta", "rank"],
            },
            {
                "id": "boost_preference_layer",
                "kind": "preference",
                "layer": "preference",
                "content": "Preference beta rank: lead with the recommended option and then list risks.",
                "importance": 1,
                "topics": ["preference", "beta", "rank"],
            },
            {
                "id": "boost_episodic_semantic",
                "kind": "claim",
                "layer": "semantic",
                "content": "What happened gamma summit is a generic recap search example.",
                "importance": 5,
                "topics": ["gamma", "summit"],
            },
            {
                "id": "boost_episodic_layer",
                "kind": "event",
                "layer": "episodic",
                "content": "What happened gamma summit: on 2026-05-01, Vamika met Riley during the importer review.",
                "importance": 1,
                "topics": ["gamma", "summit"],
            },
            {
                "id": "boost_procedural_semantic",
                "kind": "claim",
                "layer": "semantic",
                "content": "Ship delta safely appears in a generic release note and is not the operating workflow.",
                "importance": 5,
                "topics": ["ship", "delta", "release"],
            },
            {
                "id": "boost_procedural_layer",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Ship delta safely playbook: run backend tests, build the Mac app, verify codesign, and check update manifest health.",
                "importance": 1,
                "topics": ["ship", "delta", "playbook"],
            },
        ]
        self.store.save_capture(
            user_id=self.user_id,
            content="\n".join(str(record["content"]) for record in records),
            source="layer-boost-test",
            source_url=None,
            title="Layer boost ranking seed",
            extracted={
                "_timestamp": "2026-05-01T00:00:00+00:00",
                "summary": "Layer boost ranking seed.",
                "records": [
                    {
                        **record,
                        "summary": str(record["content"]),
                        "confidence": "confirmed",
                        "entity_ids": [],
                    }
                    for record in records
                ],
                "tasks": [],
                "entities": [],
            },
        )

    def _seed_project_meridian_mixed_sources(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})
        self.store._vector_ready = lambda conn: False
        fixtures = [
            (
                "obsidian",
                "local-file://Project%20Meridian/Launch.md#line=12&excerpt=meridian-beta",
                "Project Meridian launch note",
                [
                    {
                        "id": "meridian_decision_rollout",
                        "kind": "decision",
                        "layer": "decision",
                        "content": "Project Meridian beta outreach decision: invite only design partners first because support load is still unknown.",
                        "summary": "Project Meridian should start with design partners.",
                        "importance": 5,
                        "sector": "Project Meridian",
                        "topics": ["project-meridian", "beta", "outreach", "decision"],
                    },
                    {
                        "id": "meridian_procedure_launch",
                        "kind": "procedure",
                        "layer": "procedural",
                        "content": "Project Meridian beta outreach procedure: verify source sync health, approve at least one memory, ask a cited question, then send the invite.",
                        "summary": "Project Meridian beta outreach checklist.",
                        "importance": 5,
                        "sector": "Project Meridian",
                        "topics": ["project-meridian", "beta", "procedure"],
                    },
                ],
            ),
            (
                "gmail",
                "https://mail.example.com/thread/meridian-beta-support",
                "Project Meridian support email",
                [
                    {
                        "id": "meridian_reason_support",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "Project Meridian beta outreach reason: support risk means early users need founder-reviewed replies until onboarding confusion is understood.",
                        "summary": "Founder-reviewed replies are needed during early Meridian onboarding.",
                        "importance": 4,
                        "sector": "Project Meridian",
                        "topics": ["project-meridian", "support", "onboarding"],
                    },
                    {
                        "id": "meridian_person_alex",
                        "kind": "event",
                        "layer": "episodic",
                        "content": "Alex Rivera asked for the Project Meridian beta invite to include exact data-retention language before Friday.",
                        "summary": "Alex needs data-retention language in the Meridian invite.",
                        "importance": 4,
                        "sector": "Project Meridian",
                        "topics": ["project-meridian", "alex", "deadline"],
                    },
                ],
            ),
            (
                "slack",
                "https://slack.example.com/archives/C123/p1782912000",
                "Project Meridian product channel",
                [
                    {
                        "id": "meridian_negative_manual",
                        "kind": "negative",
                        "layer": "negative",
                        "content": "Project Meridian beta outreach constraint: do not ask users to manually upload private data; use connected sources and local sync instead.",
                        "summary": "Do not ask Meridian users to manually upload private data.",
                        "importance": 5,
                        "sector": "Project Meridian",
                        "topics": ["project-meridian", "privacy", "negative"],
                    },
                    {
                        "id": "meridian_open_loop",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "Project Meridian open loop: confirm whether the Obsidian plugin installed cleanly before inviting the next tester cohort.",
                        "summary": "Confirm Obsidian plugin install before inviting more Meridian testers.",
                        "importance": 3,
                        "sector": "Project Meridian",
                        "topics": ["project-meridian", "obsidian", "open-loop"],
                    },
                ],
            ),
            (
                "notion",
                "https://notion.example.com/project-meridian-style",
                "Project Meridian messaging",
                [
                    {
                        "id": "meridian_preference_invite",
                        "kind": "preference",
                        "layer": "preference",
                        "content": "I prefer Project Meridian beta invites that lead with privacy, then show exactly what Cortex can cite.",
                        "summary": "Lead Meridian beta invites with privacy and citations.",
                        "importance": 4,
                        "sector": "Project Meridian",
                        "topics": ["project-meridian", "preference", "invite"],
                    },
                    {
                        "id": "meridian_style_invite",
                        "kind": "style",
                        "layer": "style",
                        "content": "Project Meridian writing style: warm, direct, spare, and concrete, with short paragraphs and no hype.",
                        "summary": "Project Meridian style is warm, direct, spare, and concrete.",
                        "importance": 4,
                        "sector": "Project Meridian",
                        "topics": ["project-meridian", "style", "invite"],
                    },
                ],
            ),
        ]
        for source, source_url, title, records in fixtures:
            self.store.save_capture(
                user_id=self.user_id,
                content="\n".join(str(record["content"]) for record in records),
                source=source,
                source_url=source_url,
                title=title,
                extracted={
                    "_timestamp": "2026-07-01T12:00:00Z",
                    "summary": title,
                    "records": [
                        {
                            **record,
                            "confidence": "confirmed",
                            "entity_ids": [],
                        }
                        for record in records
                    ],
                    "tasks": [],
                    "entities": [],
                },
            )

    def test_realistic_mixed_source_project_fixture_feeds_ask_brief_and_adaptation(self) -> None:
        self._seed_project_meridian_mixed_sources()

        answer = self.store.answer_query(
            self.user_id,
            "what did we decide for Project Meridian beta outreach and why",
            limit=6,
            sector="Project Meridian",
        )
        citation_ids = {citation["id"] for citation in answer["citations"]}
        citation_sources = {citation["source"] for citation in answer["citations"]}

        self.assertIn("meridian_decision_rollout", citation_ids)
        self.assertIn("meridian_reason_support", citation_ids)
        self.assertGreaterEqual(len(citation_sources), 3)
        self.assertTrue(all(citation["source_url"] for citation in answer["citations"]))

        brief = self.store.action_brief(
            self.user_id,
            "prepare Project Meridian beta outreach invite for Alex",
            sector="Project Meridian",
            limit=8,
        )

        self.assertEqual(brief["status"], "strong")
        self.assertTrue(brief["current_decisions"])
        self.assertTrue(brief["procedures"])
        self.assertTrue(brief["preferences"])
        self.assertTrue(brief["negative_constraints"])
        self.assertTrue(brief["style_signals"])
        self.assertTrue(brief["execution_checklist"])
        checklist_phases = {item["phase"] for item in brief["execution_checklist"]}
        self.assertTrue({"guardrails", "decision_boundary", "procedure", "verification"}.issubset(checklist_phases))
        self.assertTrue(any("verify source sync health" in item["step"] for item in brief["execution_checklist"]))
        self.assertTrue(any("manually upload private data" in item["step"] for item in brief["execution_checklist"]))
        self.assertGreaterEqual(brief["coverage"]["cited_memories"], 6)
        self.assertGreaterEqual(len({item["source"] for item in brief["coverage"]["source_mix"]}), 4)
        self.assertIn("constraints_present", {item["code"] for item in brief["risk_flags"]})
        primary_ids = {item["id"] for item in brief["primary_context"]}
        for expected_id in (
            "meridian_decision_rollout",
            "meridian_person_alex",
            "meridian_negative_manual",
            "meridian_preference_invite",
            "meridian_style_invite",
            "meridian_open_loop",
        ):
            self.assertIn(expected_id, primary_ids)
        for phrase in (
            "data-retention language",
            "do not ask users to manually upload private data",
            "warm, direct, spare, and concrete",
            "Obsidian plugin installed cleanly",
        ):
            self.assertIn(phrase, brief["markdown"])

        adaptation = self.store.agent_adaptation(
            self.user_id,
            query="Project Meridian beta outreach invite for Alex",
            target="Claude",
            limit=8,
            sector="Project Meridian",
        )
        rule_layers = {rule["layer"] for rule in adaptation["rules"]}
        self.assertTrue({"decision", "procedural", "preference", "negative", "style", "episodic", "semantic"}.issubset(rule_layers))
        rule_ids = {rule["memory_id"] for rule in adaptation["rules"]}
        for expected_id in (
            "meridian_decision_rollout",
            "meridian_person_alex",
            "meridian_negative_manual",
            "meridian_preference_invite",
            "meridian_style_invite",
            "meridian_open_loop",
        ):
            self.assertIn(expected_id, rule_ids)
        self.assertFalse([rule["memory_id"] for rule in adaptation["rules"] if not rule.get("source_url")])
        self.assertFalse(adaptation["coverage_warnings"])

    def test_natural_action_ask_scopes_open_tasks_without_cross_project_leak(self) -> None:
        # A natural, task-intent Ask ("what should I do next for Project X")
        # scoped to a project must surface and cite that project's open tasks and
        # must not leak tasks/decisions from a different project.
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})
        self.store._vector_ready = lambda conn: False

        self.store.save_capture(
            user_id=self.user_id,
            content="Project Meridian beta outreach working note.",
            source="obsidian",
            source_url="local-file://Project%20Meridian/Tasks.md#line=3&excerpt=meridian-task",
            title="Project Meridian tasks",
            extracted={
                "_timestamp": "2026-06-01T00:00:00+00:00",
                "summary": "Meridian tasks.",
                "records": [
                    {
                        "id": "mer_anchor",
                        "kind": "decision",
                        "layer": "decision",
                        "content": "Project Meridian beta outreach: invite design partners first.",
                        "summary": "Meridian design-partner-first.",
                        "confidence": "confirmed",
                        "importance": 5,
                        "sector": "Project Meridian",
                        "topics": ["project-meridian", "beta"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [
                    {
                        "id": "mer_task_invite",
                        "kind": "action",
                        "content": "Send the Project Meridian beta outreach invite to Alex with data-retention language.",
                        "status": "open",
                        "importance": 5,
                        "topics": ["project-meridian", "beta", "outreach"],
                        "entity_ids": [],
                    }
                ],
                "entities": [],
            },
        )
        self.store.save_capture(
            user_id=self.user_id,
            content="Project Atlas outreach working note.",
            source="slack",
            source_url="https://slack.example.com/archives/C999/p1780000000",
            title="Project Atlas tasks",
            extracted={
                "_timestamp": "2026-06-01T00:00:00+00:00",
                "summary": "Atlas tasks.",
                "records": [
                    {
                        "id": "atlas_anchor",
                        "kind": "decision",
                        "layer": "decision",
                        "content": "Project Atlas beta outreach: invite enterprise leads first.",
                        "summary": "Atlas enterprise-first.",
                        "confidence": "confirmed",
                        "importance": 5,
                        "sector": "Project Atlas",
                        "topics": ["project-atlas", "beta"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [
                    {
                        "id": "atlas_task_invite",
                        "kind": "action",
                        "content": "Send the Project Atlas beta outreach invite next.",
                        "status": "open",
                        "importance": 5,
                        "topics": ["project-atlas", "beta", "outreach"],
                        "entity_ids": [],
                    }
                ],
                "entities": [],
            },
        )

        answer = self.store.answer_query(
            self.user_id,
            "what should I do next for Project Meridian beta outreach",
            limit=8,
            sector="Project Meridian",
        )
        result_ids = {item["id"] for item in answer["results"]}
        citation_ids = {citation["id"] for citation in answer["citations"]}

        # The project's own open task is surfaced and cited (source-backed)...
        self.assertIn("mer_task_invite", result_ids)
        self.assertIn("mer_task_invite", citation_ids)
        # ...the project's decision context is still present...
        self.assertIn("mer_anchor", result_ids)
        # ...and nothing from the other project leaks in.
        self.assertNotIn("atlas_task_invite", result_ids)
        self.assertNotIn("atlas_anchor", result_ids)
        self.assertTrue(all(citation["source_url"] for citation in answer["citations"]))

        # A project-scoped context pack and personal profile now include the
        # project's open tasks (previously dropped entirely) without leaking the
        # other project's tasks.
        pack = self.store.context_pack(
            self.user_id, query="Project Meridian beta outreach", limit=8, sector="Project Meridian"
        )
        self.assertIn("data-retention language", pack)  # unique to the Meridian task
        self.assertNotIn("Project Atlas beta outreach invite", pack)

        profile = self.store.personal_profile(
            self.user_id,
            query="Project Meridian beta outreach",
            limit=8,
            sector="Project Meridian",
            include_pending=True,
        )
        profile_text = json.dumps(profile)
        self.assertIn("mer_task_invite", profile_text)
        self.assertNotIn("atlas_task_invite", profile_text)

    def test_seed_representative_memories_covers_every_layer(self) -> None:
        memories = seed_representative_memories(self.store, self.user_id)

        self.assertEqual({memory["layer"] for memory in memories}, MEMORY_LAYERS)
        self.assertEqual(len(memories), len(MEMORY_LAYERS) + EXPANDED_SEED_MEMORY_COUNT)

    def test_natural_language_filler_words_match_memory_without_vectors(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})
        self.store._vector_ready = lambda conn: False
        self.store.save_capture(
            user_id=self.user_id,
            content="Taipei launch positioning seed.",
            source="lexical-fallback-test",
            source_url=None,
            title="Lexical fallback seed",
            extracted={
                "_timestamp": "2026-05-01T00:00:00+00:00",
                "summary": "Lexical fallback seed.",
                "records": [
                    {
                        "id": "nl_taipei_positioning",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "Taipei launch positioning uses the market reliability notes as the standing source.",
                        "summary": "Taipei launch positioning relies on market reliability notes.",
                        "confidence": "confirmed",
                        "importance": 3,
                        "topics": ["taipei", "launch", "positioning"],
                        "entity_ids": [],
                    },
                    {
                        "id": "nl_taipei_distractor",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "Taipei dinner recommendations are saved separately from launch planning.",
                        "summary": "Taipei dinner recommendations.",
                        "confidence": "confirmed",
                        "importance": 5,
                        "topics": ["taipei", "dinner"],
                        "entity_ids": [],
                    },
                ],
                "tasks": [],
                "entities": [],
            },
        )

        query = "can you remember what i said about the taipei launch positioning thing"
        self.assertIn("remember*", self.store._fts_query(query))

        results = self.store.search(self.user_id, query, limit=1)

        self.assertEqual(results[0]["id"], "nl_taipei_positioning")

    def test_lexical_fallback_does_not_pad_existing_strict_results(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})
        self.store._vector_ready = lambda conn: False
        self.store.save_capture(
            user_id=self.user_id,
            content="Strict and fallback Taipei seed.",
            source="lexical-fallback-test",
            source_url=None,
            title="Lexical fallback underfill seed",
            extracted={
                "_timestamp": "2026-05-01T00:00:00+00:00",
                "summary": "Lexical fallback underfill seed.",
                "records": [
                    {
                        "id": "strict_taipei_positioning",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "Taipei launch positioning should keep the reliability proof in the first answer.",
                        "summary": "Taipei launch positioning reliability proof.",
                        "confidence": "confirmed",
                        "importance": 1,
                        "topics": ["taipei", "launch", "positioning"],
                        "entity_ids": [],
                    },
                    {
                        "id": "fallback_taipei_launch",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "Taipei launch timing depends on partner approval.",
                        "summary": "Taipei launch timing.",
                        "confidence": "confirmed",
                        "importance": 5,
                        "topics": ["taipei", "launch"],
                        "entity_ids": [],
                    },
                ],
                "tasks": [],
                "entities": [],
            },
        )

        results = self.store.search(self.user_id, "taipei launch positioning", limit=2)

        self.assertEqual([item["id"] for item in results], ["strict_taipei_positioning"])

    def test_owner_and_release_paraphrases_beat_explicit_non_answer_bait(self) -> None:
        query = "Who is accountable for the Project Aster launch?"
        self.store._vector_ready = lambda conn: False
        self.assertEqual(
            self.store._lexical_fallback_terms(query),
            ["owner", "project", "aster", "release"],
        )

        for order_name, canonical_first in (
            ("canonical-first", True),
            ("bait-first", False),
        ):
            with self.subTest(order=order_name):
                user_id = f"adversarial-paraphrase-{order_name}"
                self.store.update_settings(
                    user_id,
                    {
                        "review_new_captures": False,
                        "allow_pending_in_context": True,
                    },
                )
                canonical_id = f"aster_canonical_{order_name}"
                bait_id = f"aster_bait_{order_name}"
                canonical = {
                    "id": canonical_id,
                    "kind": "claim",
                    "layer": "semantic",
                    "content": "Project Aster's rollout DRI is Mina Chen.",
                    "summary": "Mina Chen is the DRI for the Project Aster rollout.",
                    "confidence": "confirmed",
                    "importance": 3,
                    "topics": ["project-aster", "rollout", "dri"],
                    "entity_ids": [],
                }
                bait = {
                    "id": bait_id,
                    "kind": "claim",
                    "layer": "semantic",
                    "content": (
                        "Project Aster launch accountability template. This "
                        "unassigned template does not name an owner and must not "
                        "be used to answer who is accountable."
                    ),
                    "summary": "An unassigned Project Aster accountability template.",
                    "confidence": "confirmed",
                    "importance": 3,
                    "topics": ["project-aster", "launch", "accountability"],
                    "entity_ids": [],
                }
                records = [canonical, bait] if canonical_first else [bait, canonical]
                self.store.save_capture(
                    user_id=user_id,
                    content="\n".join(str(record["content"]) for record in records),
                    source="adversarial-retrieval-test",
                    source_url="https://example.invalid/project-aster",
                    title="Project Aster ownership fixture",
                    extracted={
                        "_timestamp": "2026-07-30T00:00:00Z",
                        "summary": "Project Aster ownership fixture.",
                        "records": records,
                        "tasks": [],
                        "entities": [],
                    },
                )

                results = self.store.search(user_id, query, limit=5)

                self.assertTrue(results)
                self.assertEqual(results[0]["id"], canonical_id)
                self.assertIn(canonical_id, [item["id"] for item in results])

    def test_lexical_fallback_applies_recency_and_importance_boosts(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})
        self.store._vector_ready = lambda conn: False
        self.store.save_capture(
            user_id=self.user_id,
            content="Project Lattice fallback ranking stale note.",
            source="lexical-fallback-test",
            source_url=None,
            title="Stale fallback ranking seed",
            extracted={
                "_timestamp": "2024-01-01T00:00:00+00:00",
                "summary": "Stale fallback ranking seed.",
                "records": [
                    {
                        "id": "lexical_fallback_stale_low_importance",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": (
                            "Project Lattice fallback ranking stale note repeats lattice fallback ranking "
                            "so strict FTS rank prefers it before near-tie boosts."
                        ),
                        "summary": "Project Lattice fallback ranking stale note.",
                        "confidence": "confirmed",
                        "importance": 1,
                        "topics": ["lattice", "fallback", "ranking"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )
        self.store.save_capture(
            user_id=self.user_id,
            content="Project Lattice fallback ranking current note.",
            source="lexical-fallback-test",
            source_url=None,
            title="Fresh fallback ranking seed",
            extracted={
                "_timestamp": "2026-06-30T00:00:00+00:00",
                "summary": "Fresh fallback ranking seed.",
                "records": [
                    {
                        "id": "lexical_fallback_fresh_high_importance",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "Project Lattice fallback ranking current note should win the fallback near tie.",
                        "summary": "Project Lattice fallback ranking current note.",
                        "confidence": "confirmed",
                        "importance": 5,
                        "topics": ["lattice", "fallback", "ranking"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )
        diagnostics: dict[str, object] = {}

        results = self.store.search(
            self.user_id,
            "Project Lattice fallback ranking unmatchednonce",
            limit=2,
            _diagnostics=diagnostics,
        )

        self.assertEqual(diagnostics["mode_counts"]["fts"], 0)
        self.assertGreaterEqual(diagnostics["mode_counts"]["lexical_fallback"], 2)
        self.assertEqual(results[0]["id"], "lexical_fallback_fresh_high_importance")

    def test_focused_queries_retrieve_expected_layer_and_content(self) -> None:
        result = evaluate_retrieval(self.store, self.user_id)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["seeded_memories"], len(MEMORY_LAYERS) + EXPANDED_SEED_MEMORY_COUNT)
        self.assertEqual(result["distractor_memories"], len(DISTRACTOR_MEMORIES))
        self.assertEqual(result["focused_retrieval_memories"], 11)
        self.assertEqual(result["noisy_import_memories"], 11)
        self.assertGreaterEqual(result["direct_connector_memories"], 13)
        self.assertEqual(set(result["seeded_layers"]), MEMORY_LAYERS)
        # The static corpus is add-only: 21 original cases plus the expanded
        # multi-source corpus. Never shrink this floor.
        self.assertGreaterEqual(len(RETRIEVAL_CASES), 136)
        expected_noisy_cases = 11
        expected_direct_connector_cases = 13
        expected_source_backed_cases = 1
        expected_direct_connector_account_policy_cases = 1
        expected_mixed_source_authority_cases = 1
        expected_case_count = (
            len(RETRIEVAL_CASES)
            + expected_noisy_cases
            + expected_direct_connector_cases
            + expected_source_backed_cases
            + expected_direct_connector_account_policy_cases
            + expected_mixed_source_authority_cases
        )
        self.assertEqual(len(result["checks"]), expected_case_count)
        self.assertEqual(result["metrics"]["overall"]["case_count"], expected_case_count)
        self.assertEqual(result["metrics"]["overall"]["top1_accuracy"], 1.0)
        self.assertEqual(result["metrics"]["overall"]["recall@1"], 1.0)
        self.assertEqual(result["metrics"]["overall"]["recall@3"], 1.0)
        self.assertEqual(result["metrics"]["by_category"]["noisy_import"]["case_count"], 2)
        self.assertEqual(result["metrics"]["by_category"]["noisy_import_preference"]["case_count"], 1)
        self.assertEqual(result["metrics"]["by_category"]["noisy_import_style"]["case_count"], 1)
        self.assertEqual(result["metrics"]["by_category"]["noisy_import_negative"]["case_count"], 1)
        self.assertEqual(result["metrics"]["by_category"]["noisy_import_email"]["case_count"], 1)
        self.assertEqual(result["metrics"]["by_category"]["noisy_import_docs"]["case_count"], 1)
        self.assertEqual(result["metrics"]["by_category"]["noisy_import_notion"]["case_count"], 1)
        self.assertEqual(result["metrics"]["by_category"]["noisy_import_cloud_docs"]["case_count"], 1)
        self.assertEqual(result["metrics"]["by_category"]["noisy_import_calendar"]["case_count"], 1)
        self.assertEqual(result["metrics"]["by_category"]["noisy_import_github"]["case_count"], 1)
        self.assertEqual(result["metrics"]["by_category"]["direct_connector"]["case_count"], expected_direct_connector_cases)
        self.assertEqual(
            result["metrics"]["by_category"]["direct_connector_account_policy"]["case_count"],
            expected_direct_connector_account_policy_cases,
        )
        self.assertEqual(result["metrics"]["by_category"]["sector_scoping"]["case_count"], 10)
        self.assertEqual(result["metrics"]["by_category"]["temporal_validity"]["case_count"], 10)
        self.assertEqual(result["metrics"]["by_category"]["related_memory"]["case_count"], 10)
        self.assertEqual(result["metrics"]["by_category"]["cross_project_no_leak"]["case_count"], 10)
        self.assertEqual(result["metrics"]["by_category"]["entity_recall"]["case_count"], 10)
        self.assertEqual(result["metrics"]["by_category"]["decision_recall"]["case_count"], 10)
        self.assertEqual(result["metrics"]["by_category"]["source_scoped"]["case_count"], 10)
        self.assertEqual(result["metrics"]["by_category"]["focused"]["case_count"], 14)
        self.assertEqual(result["metrics"]["by_category"]["paraphrase"]["case_count"], 12)
        self.assertEqual(result["metrics"]["by_category"]["negative_recall"]["case_count"], 10)
        self.assertEqual(result["metrics"]["by_category"]["style_recall"]["case_count"], 10)
        self.assertEqual(result["metrics"]["by_category"]["temporal_recall"]["case_count"], 10)
        self.assertEqual(result["metrics"]["by_category"]["procedural_recall"]["case_count"], 10)
        self.assertEqual(result["metrics"]["by_category"]["source_backed_ranking"]["case_count"], 1)
        self.assertEqual(result["metrics"]["by_category"]["mixed_source_authority"]["case_count"], expected_mixed_source_authority_cases)
        self.assertEqual(result["source_backed_fallback"]["top_result"], "rq_source_backed_lexical_fallback_cited")
        self.assertIn("lexical_fallback", result["source_backed_fallback"]["retrieval_modes"])
        self.assertEqual(result["mixed_source_authority"]["top_result"], "rq_mixed_source_authority_notion_canonical")
        self.assertEqual(result["mixed_source_authority"]["top_source"], "notion")
        self.assertEqual(result["automatic_connector_account_scope"]["ask_status"], "cited")
        self.assertEqual(result["automatic_connector_account_scope"]["blocked_ask_status"], "no_cited_evidence")

        categories = {case.category for case in RETRIEVAL_CASES} | {
            "noisy_import",
            "noisy_import_preference",
            "noisy_import_style",
            "noisy_import_negative",
            "noisy_import_email",
            "noisy_import_docs",
            "noisy_import_notion",
            "noisy_import_cloud_docs",
            "noisy_import_calendar",
            "noisy_import_github",
            "direct_connector",
            "direct_connector_account_policy",
            "source_backed_ranking",
            "mixed_source_authority",
        }
        self.assertIn("paraphrase", categories)
        self.assertIn("style_recall", categories)
        self.assertIn("negative_recall", categories)
        self.assertEqual(set(result["metrics"]["by_category"]), categories)

        focused_contracts = result["focused_answer_contracts"]
        self.assertEqual(focused_contracts["sector_scoping"]["citation_ids"][0], SECTOR_SCOPE_ATLAS_ID)
        self.assertNotIn(SECTOR_SCOPE_BOREAL_ID, focused_contracts["sector_scoping"]["citation_ids"])
        self.assertEqual(focused_contracts["temporal_validity"]["citation_ids"][0], TEMPORAL_VALIDITY_CURRENT_ID)
        for stale_id in TEMPORAL_VALIDITY_EXCLUDED_IDS:
            self.assertNotIn(stale_id, focused_contracts["temporal_validity"]["citation_ids"])
        self.assertEqual(focused_contracts["related_memory"]["citation_ids"][0], RELATED_MEMORY_PRIMARY_ID)
        self.assertIn(RELATED_MEMORY_COMPANION_ID, focused_contracts["related_memory"]["citation_ids"])
        self.assertEqual(focused_contracts["related_memory"]["relationship"]["kind"], "shared_entity")
        self.assertEqual(focused_contracts["related_memory"]["relationship"]["related_to_id"], RELATED_MEMORY_PRIMARY_ID)
        self.assertEqual(focused_contracts["claim_conflict"]["status"], "conflicted")
        self.assertEqual(focused_contracts["claim_conflict"]["conflict"]["type"], "claim_conflict")
        self.assertEqual(focused_contracts["claim_conflict"]["conflict"]["claim_key"], "launch channel")
        self.assertEqual(focused_contracts["claim_conflict"]["conflict"]["primary_id"], "rq_ask_claim_conflict_updated")
        self.assertEqual(focused_contracts["claim_conflict"]["conflict"]["primary_claim"], "slack")
        self.assertEqual(focused_contracts["claim_conflict"]["conflict"]["conflicting_claim"], "email")
        self.assertEqual(focused_contracts["low_confidence"]["status"], "low_confidence")
        self.assertEqual(focused_contracts["low_confidence"]["evidence"]["missing_fields"], ["owner"])
        self.assertEqual(focused_contracts["low_confidence"]["citation_ids"][0], "rq_ask_low_confidence_related_cited")
        mixed_source = result["mixed_source_project_contracts"]
        self.assertTrue(
            {"guardrails", "decision_boundary", "procedure", "open_action", "verification"}.issubset(
                set(mixed_source["brief_execution_checklist_phases"])
            )
        )
        self.assertTrue(result["no_evidence_answer"]["abstained"])
        self.assertEqual(result["no_evidence_answer"]["citation_ids"], [])
        self.assertIn("rq_ask_no_evidence_uncited_only", result["no_evidence_answer"]["uncited_result_ids"])

        direct_contracts = result["direct_connector_answer_contracts"]
        expected_direct_sources = {
            "calendar",
            "github",
            "gmail",
            "outlook",
            "google-drive",
            "jira",
            "linear",
            "notion",
            "obsidian",
            "raindrop",
            "readwise",
            "slack",
            "zotero",
        }
        self.assertEqual(direct_contracts["source_count"], 13)
        self.assertEqual(set(direct_contracts["sources"]), expected_direct_sources)
        for source, contract in direct_contracts["contracts"].items():
            self.assertIn(source, expected_direct_sources)
            self.assertTrue(contract["source_account_id"].startswith("sacct_"))
            self.assertTrue(contract["source_record_id"].startswith(f"{source}-direct-eval-"))
            self.assertIn("direct-connector", contract["source_url"])
            self.assertIn(f"service={source}", contract["source_url"])
            self.assertIn("line=1", contract["source_url"])

        sector_checks = {check["name"]: check for check in result["checks"] if check["category"] == "sector_scoping"}
        self.assertEqual(sector_checks["sector_scope_atlas_release"]["top_sector"], "Project Atlas")
        self.assertEqual(sector_checks["sector_scope_boreal_release"]["top_sector"], "Project Boreal")
        validity_check = next(check for check in result["checks"] if check["category"] == "temporal_validity")
        for stale_id in TEMPORAL_VALIDITY_EXCLUDED_IDS:
            self.assertNotIn(stale_id, validity_check["result_ids"])
        related_check = next(check for check in result["checks"] if check["category"] == "related_memory")
        self.assertEqual(related_check["related_result"]["id"], RELATED_MEMORY_COMPANION_ID)
        self.assertEqual(related_check["related_result"]["relationship"]["related_to_id"], RELATED_MEMORY_PRIMARY_ID)

        for check in result["checks"]:
            self.assertEqual(check["expected_rank"], 1)
            self.assertEqual(check["result_ids"][0], check["expected_id"])
            for k in METRIC_K_VALUES:
                self.assertIn(f"recall@{k}", check["metrics"])
                self.assertIn(f"precision@{k}", check["metrics"])
                self.assertGreaterEqual(check["metrics"][f"precision@{k}"], 0.0)
                self.assertLessEqual(check["metrics"][f"precision@{k}"], 1.0)

    def test_related_memory_surfaces_even_when_primary_results_fill_limit(self) -> None:
        self.store.update_settings(
            self.user_id,
            {
                "review_new_captures": False,
                "allow_pending_in_context": True,
            },
        )
        self.store._vector_ready = lambda conn: False
        self.store.save_capture(
            user_id=self.user_id,
            content="Project SaturnVault relation saturation fixture.",
            source="retrieval-test",
            source_url="cortex-test://retrieval/related-saturation#line=12&excerpt=relation-saturation",
            title="Relation saturation fixture",
            extracted={
                "_timestamp": "2026-07-01T00:00:00+00:00",
                "summary": "Project SaturnVault relation saturation fixture.",
                "records": [
                    {
                        "id": "related_saturation_primary",
                        "kind": "decision",
                        "layer": "decision",
                        "content": "Project SaturnVault founder-only launch decision path stays local-first for the first beta cohort.",
                        "summary": "SaturnVault founder-only launch decision stays local-first.",
                        "confidence": "confirmed",
                        "importance": 5,
                        "sector": "Project SaturnVault",
                        "topics": ["saturnvault", "launch"],
                        "entity_ids": ["project_saturnvault"],
                    },
                    {
                        "id": "related_saturation_companion",
                        "kind": "procedure",
                        "layer": "procedural",
                        "content": "Before release, run checksum verification, backup creation, support bundle export, and rollback replacement checks.",
                        "summary": "Release readiness requires checksum, backup, support bundle, and rollback checks.",
                        "confidence": "confirmed",
                        "importance": 3,
                        "sector": "Project SaturnVault",
                        "topics": ["saturnvault", "release-readiness"],
                        "entity_ids": ["project_saturnvault"],
                    },
                ],
                "tasks": [],
                "entities": [
                    {
                        "id": "project_saturnvault",
                        "kind": "project",
                        "name": "Project SaturnVault",
                        "aliases": ["SaturnVault"],
                    }
                ],
            },
        )
        self.store.save_capture(
            user_id=self.user_id,
            content="Project SaturnVault founder-only launch decision path appears in a generic duplicate note.",
            source="retrieval-test",
            source_url="cortex-test://retrieval/related-saturation#line=31&excerpt=duplicate",
            title="Relation saturation distractor",
            extracted={
                "_timestamp": "2026-07-01T00:01:00+00:00",
                "summary": "Project SaturnVault duplicate note.",
                "records": [
                    {
                        "id": "related_saturation_distractor",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "Project SaturnVault founder-only launch decision path appears in a generic duplicate note.",
                        "summary": "Generic duplicate SaturnVault launch note.",
                        "confidence": "confirmed",
                        "importance": 4,
                        "sector": "Project SaturnVault",
                        "topics": ["saturnvault", "launch"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )

        results = self.store.search(
            self.user_id,
            "Project SaturnVault founder-only launch decision path",
            limit=2,
            sector="Project SaturnVault",
            include_related=True,
        )
        result_ids = [item["id"] for item in results]

        self.assertEqual(result_ids[0], "related_saturation_primary")
        self.assertIn("related_saturation_companion", result_ids)
        self.assertNotIn("related_saturation_distractor", result_ids)
        companion = next(item for item in results if item["id"] == "related_saturation_companion")
        self.assertEqual(companion["relationship"]["kind"], "shared_entity")
        self.assertEqual(companion["relationship"]["related_to_id"], "related_saturation_primary")

    def test_layer_intent_boosts_rerank_matching_candidates(self) -> None:
        self._seed_boost_ranking_memories()

        writing_results = self.store.search(self.user_id, "writing style atlas engineering summaries", limit=3)
        self.assertEqual({item["layer"] for item in writing_results[:2]}, {"style", "negative"})

        cases = [
            ("planning alpha rank decision", "decision", "boost_decision_layer"),
            ("preference beta rank recommended option risks", "preference", "boost_preference_layer"),
            ("what happened gamma summit", "episodic", "boost_episodic_layer"),
            ("ship delta safely", "procedural", "boost_procedural_layer"),
        ]
        for query, expected_layer, expected_id in cases:
            with self.subTest(query=query):
                results = self.store.search(self.user_id, query, limit=3)
                self.assertEqual(results[0]["id"], expected_id)
                self.assertEqual(results[0]["layer"], expected_layer)

    def test_source_quality_boost_reranks_near_tie_with_citation_and_trusted_metadata(self) -> None:
        self.store.update_settings(
            self.user_id,
            {
                "review_new_captures": False,
                "allow_pending_in_context": True,
                "source_policies": {"github": {"mode": "trusted"}},
            },
        )
        self.store.save_capture(
            user_id=self.user_id,
            content="Atlas reliability contract source quality chooses ranking from generic memo.",
            source="unit-test",
            source_url=None,
            title="Uncited near tie",
            extracted={
                "_timestamp": "2026-05-01T00:00:00+00:00",
                "summary": "Uncited near tie.",
                "records": [
                    {
                        "id": "quality_uncited_manual",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "Atlas reliability contract source quality chooses ranking from generic memo.",
                        "summary": "Generic uncited ranking memo.",
                        "confidence": "confirmed",
                        "importance": 5,
                        "topics": ["atlas", "reliability", "ranking"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )
        self.store.save_capture(
            user_id=self.user_id,
            content="Atlas reliability contract source quality chooses ranking from canonical issue.",
            source="github",
            source_url="cortex-source://github#service=github&file=issues.json&line=12&excerpt=atlas-reliability",
            title="Cited trusted near tie",
            extracted={
                "_timestamp": "2026-05-01T00:00:00+00:00",
                "summary": "Cited trusted near tie.",
                "records": [
                    {
                        "id": "quality_cited_trusted",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "Atlas reliability contract source quality chooses ranking from canonical issue.",
                        "summary": "Canonical cited ranking issue.",
                        "confidence": "confirmed",
                        "importance": 1,
                        "topics": ["atlas", "reliability", "ranking"],
                        "entity_ids": [],
                        "metadata": {"source_quality": "canonical", "verified": True},
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )

        results = self.store.search(self.user_id, "atlas reliability contract source quality ranking", limit=2)

        self.assertEqual(results[0]["id"], "quality_cited_trusted")
        self.assertEqual(results[0]["source_type"], "service")
        self.assertIn("line=12", results[0]["source_url"])

    def test_lexical_fallback_prefers_source_backed_record_over_noisy_summary(self) -> None:
        self.store.update_settings(
            self.user_id,
            {
                "review_new_captures": False,
                "allow_pending_in_context": True,
                "source_policies": {"github": {"mode": "trusted"}},
            },
        )
        self.store._vector_ready = lambda conn: False
        self.store.save_capture(
            user_id=self.user_id,
            content="Project Beacon citation summary noisy fallback seed.",
            source="chatgpt",
            source_url=None,
            title="Noisy generated summary",
            extracted={
                "_timestamp": "2026-05-01T00:00:00+00:00",
                "summary": "Noisy generated summary.",
                "records": [
                    {
                        "id": "fallback_noisy_generated_summary",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": (
                            "Project Beacon citation summary repeats boilerplate. Project Beacon citation summary "
                            "appears again in a generated service digest without source evidence."
                        ),
                        "summary": "Project Beacon citation summary generated service digest.",
                        "confidence": "confirmed",
                        "importance": 5,
                        "topics": ["beacon", "citation", "summary"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )
        self.store.save_capture(
            user_id=self.user_id,
            content="Project Beacon citation summary canonical fallback seed.",
            source="github",
            source_url="cortex-source://github#service=github&repository=cortex&file=issues.json&line=99&excerpt=beacon-source-truth",
            title="Canonical source record",
            extracted={
                "_timestamp": "2026-05-01T00:00:00+00:00",
                "summary": "Canonical source record.",
                "records": [
                    {
                        "id": "fallback_source_backed_canonical",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "Project Beacon citation summary comes from the signed GitHub issue source evidence.",
                        "summary": "Project Beacon citation summary GitHub issue.",
                        "confidence": "confirmed",
                        "importance": 1,
                        "topics": ["beacon", "citation", "summary"],
                        "entity_ids": [],
                        "metadata": {"source_quality": "canonical", "verified": True},
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )
        diagnostics: dict[str, object] = {}

        results = self.store.search(
            self.user_id,
            "Project Beacon citation summary fallbacktoken",
            limit=2,
            _diagnostics=diagnostics,
        )

        self.assertIn("lexical_fallback", diagnostics["used_modes"])
        self.assertEqual(results[0]["id"], "fallback_source_backed_canonical")
        self.assertEqual(results[0]["source_type"], "service")
        self.assertIn("line=99", results[0]["source_url"])

    def test_answer_query_prefers_source_backed_citations_over_uncited_matches(self) -> None:
        self.store.update_settings(
            self.user_id,
            {
                "review_new_captures": False,
                "allow_pending_in_context": True,
            },
        )
        self.store.save_capture(
            user_id=self.user_id,
            content="Atlas ask source-backed ranking points at a generic uncited memo.",
            source="unit-test",
            source_url=None,
            title="Uncited Ask distractor",
            extracted={
                "_timestamp": "2026-05-01T00:00:00+00:00",
                "summary": "Uncited Ask distractor.",
                "records": [
                    {
                        "id": "ask_source_backed_uncited",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "Atlas ask source-backed ranking points at a generic uncited memo.",
                        "summary": "Generic uncited Ask memo.",
                        "confidence": "confirmed",
                        "importance": 5,
                        "topics": ["atlas", "ask", "source-backed"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )
        self.store.save_capture(
            user_id=self.user_id,
            content="Atlas ask source-backed ranking points at the canonical connected source.",
            source="github",
            source_url="cortex-source://github#service=github&file=issues.json&line=34&excerpt=atlas-ask-source",
            title="Cited Ask source",
            extracted={
                "_timestamp": "2026-05-01T00:00:00+00:00",
                "summary": "Cited Ask source.",
                "records": [
                    {
                        "id": "ask_source_backed_cited",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "Atlas ask source-backed ranking points at the canonical connected source.",
                        "summary": "Canonical cited Ask source.",
                        "confidence": "confirmed",
                        "importance": 1,
                        "topics": ["atlas", "ask", "source-backed"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )

        answer = self.store.answer_query(self.user_id, "atlas ask source-backed ranking", limit=2)

        self.assertEqual(answer["status"], "cited")
        self.assertEqual(answer["citations"][0]["id"], "ask_source_backed_cited")
        self.assertEqual(answer["citations"][0]["line_start"], "34")
        self.assertEqual(answer["citations"][0]["source_excerpt"], "atlas-ask-source")
        self.assertTrue(all(citation["source_url"] for citation in answer["citations"]))
        self.assertNotIn("ask_source_backed_uncited", {citation["id"] for citation in answer["citations"]})
        self.assertEqual(answer["results"][0]["id"], "ask_source_backed_cited")

    def test_answer_query_surfaces_conflicting_cited_dates_without_flattening_them(self) -> None:
        self.store.update_settings(
            self.user_id,
            {
                "review_new_captures": False,
                "allow_pending_in_context": True,
            },
        )
        fixtures = [
            (
                "slack",
                "cortex-source://slack#service=slack&channel=C123&message=1782900000000100&line=1&excerpt=helios-july-10",
                "2026-06-01T00:00:00+00:00",
                "helios_launch_original",
                "Project Helios launch date is July 10 according to the original team channel plan.",
            ),
            (
                "github",
                "cortex-source://github#service=github&repository=cortex&file=issues.json&line=44&excerpt=helios-july-17",
                "2026-06-15T00:00:00+00:00",
                "helios_launch_updated",
                "Project Helios launch date moved to July 17 in the current release issue.",
            ),
        ]
        for source, source_url, timestamp, record_id, content in fixtures:
            self.store.save_capture(
                user_id=self.user_id,
                content=content,
                source=source,
                source_url=source_url,
                title=f"{source} Project Helios launch date",
                extracted={
                    "_timestamp": timestamp,
                    "summary": content,
                    "records": [
                        {
                            "id": record_id,
                            "kind": "decision",
                            "layer": "decision",
                            "content": content,
                            "summary": content,
                            "confidence": "confirmed",
                            "importance": 4,
                            "sector": "Project Helios",
                            "topics": ["project-helios", "launch", "date"],
                            "entity_ids": ["project_helios"],
                            "occurred_at": timestamp,
                        }
                    ],
                    "tasks": [],
                    "entities": [
                        {
                            "id": "project_helios",
                            "kind": "project",
                            "name": "Project Helios",
                            "aliases": ["Helios"],
                        }
                    ],
                },
            )

        answer = self.store.answer_query(self.user_id, "Project Helios launch date", limit=4, sector="Project Helios")

        self.assertEqual(answer["status"], "conflicted")
        self.assertEqual(answer["conflicts"][0]["type"], "date_conflict")
        self.assertEqual(answer["conflicts"][0]["primary_id"], "helios_launch_updated")
        self.assertEqual(answer["conflicts"][0]["reason"], "newer_timestamp")
        self.assertEqual(set(answer["conflicts"][0]["date_claims"]), {"july 10", "july 17"})
        self.assertIn("Conflict check", answer["answer"])
        self.assertIn("verify before acting", answer["answer"])
        citation_ids = {citation["id"] for citation in answer["citations"]}
        self.assertIn("helios_launch_original", citation_ids)
        self.assertIn("helios_launch_updated", citation_ids)

    def test_answer_query_keeps_years_in_cited_date_conflicts(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})
        for source, timestamp, record_id, content in (
            (
                "slack",
                "2026-06-01T00:00:00+00:00",
                "helios_renewal_2025",
                "Project Helios renewal date is July 10, 2025 according to the stale billing thread.",
            ),
            (
                "github",
                "2026-06-15T00:00:00+00:00",
                "helios_renewal_2026",
                "Project Helios renewal date moved to July 10, 2026 in the current contract issue.",
            ),
        ):
            self.store.save_capture(
                user_id=self.user_id,
                content=content,
                source=source,
                source_url=f"cortex-source://{source}#service={source}&line=1&excerpt={record_id}",
                title=f"{source} Project Helios renewal date",
                extracted={
                    "_timestamp": timestamp,
                    "summary": content,
                    "records": [
                        {
                            "id": record_id,
                            "kind": "decision",
                            "layer": "decision",
                            "content": content,
                            "summary": content,
                            "confidence": "confirmed",
                            "importance": 4,
                            "sector": "Project Helios",
                            "topics": ["project-helios", "renewal", "date"],
                            "entity_ids": ["project_helios"],
                            "occurred_at": timestamp,
                        }
                    ],
                    "tasks": [],
                    "entities": [{"id": "project_helios", "kind": "project", "name": "Project Helios", "aliases": ["Helios"]}],
                },
            )

        answer = self.store.answer_query(self.user_id, "Project Helios renewal date", limit=4, sector="Project Helios")

        self.assertEqual(answer["status"], "conflicted")
        self.assertEqual(answer["conflicts"][0]["type"], "date_conflict")
        self.assertEqual(set(answer["conflicts"][0]["date_claims"]), {"july 10 2025", "july 10 2026"})

    def test_answer_query_surfaces_conflicting_cited_decision_claims(self) -> None:
        self.store.update_settings(
            self.user_id,
            {
                "review_new_captures": False,
                "allow_pending_in_context": True,
            },
        )
        fixtures = [
            (
                "slack",
                "cortex-source://slack#service=slack&channel=C456&message=1782900000000200&line=1&excerpt=icarus-email",
                "2026-06-02T00:00:00+00:00",
                "icarus_launch_channel_original",
                "Project Icarus launch channel should use email according to the original GTM thread.",
            ),
            (
                "github",
                "cortex-source://github#service=github&repository=cortex&file=issues.json&line=45&excerpt=icarus-slack",
                "2026-06-16T00:00:00+00:00",
                "icarus_launch_channel_updated",
                "Project Icarus launch channel now uses Slack because tester replies need fast triage.",
            ),
        ]
        for source, source_url, timestamp, record_id, content in fixtures:
            self.store.save_capture(
                user_id=self.user_id,
                content=content,
                source=source,
                source_url=source_url,
                title=f"{source} Project Icarus launch channel",
                extracted={
                    "_timestamp": timestamp,
                    "summary": content,
                    "records": [
                        {
                            "id": record_id,
                            "kind": "decision",
                            "layer": "decision",
                            "content": content,
                            "summary": content,
                            "confidence": "confirmed",
                            "importance": 4,
                            "sector": "Project Icarus",
                            "topics": ["project-icarus", "launch", "channel"],
                            "entity_ids": ["project_icarus"],
                            "occurred_at": timestamp,
                        }
                    ],
                    "tasks": [],
                    "entities": [
                        {
                            "id": "project_icarus",
                            "kind": "project",
                            "name": "Project Icarus",
                            "aliases": ["Icarus"],
                        }
                    ],
                },
            )

        answer = self.store.answer_query(self.user_id, "Project Icarus launch channel", limit=4, sector="Project Icarus")

        self.assertEqual(answer["status"], "conflicted")
        self.assertEqual(answer["conflicts"][0]["type"], "claim_conflict")
        self.assertEqual(answer["conflicts"][0]["claim_key"], "launch channel")
        self.assertEqual(answer["conflicts"][0]["primary_id"], "icarus_launch_channel_updated")
        self.assertEqual(answer["conflicts"][0]["primary_claim"], "slack")
        self.assertEqual(answer["conflicts"][0]["conflicting_claim"], "email")
        self.assertEqual(answer["conflicts"][0]["reason"], "newer_timestamp")
        self.assertIn("disagree on launch channel", answer["answer"])
        self.assertIn("verify before acting", answer["answer"])

    def test_mixed_source_trusted_conflict_respects_source_account_scope(self) -> None:
        self.store.update_settings(
            self.user_id,
            {
                "review_new_captures": True,
                "allow_pending_in_context": False,
            },
        )

        accounts: dict[str, dict[str, object]] = {}
        for source, label, policy in (
            ("gmail", "Quasar Gmail", {"review_required": False, "allow_ai_context": True}),
            ("slack", "Quasar Slack", {"review_required": False, "allow_ai_context": True}),
            ("google-drive", "Quasar Drive", {"review_required": False, "allow_ai_context": True}),
            ("notion", "Quasar Notion Canonical", {"mode": "trusted", "review_required": False, "allow_ai_context": True}),
        ):
            accounts[source] = self.store.upsert_source_account(
                self.user_id,
                source=source,
                account_label=label,
                account_identifier=f"{source}-quasar",
                connection_type="api-token",
                status="connected",
                auth_state="healthy",
                policy=policy,
            )

        records = {
            "gmail": {
                "external_id": "quasar-gmail-email",
                "title": "Quasar Gmail stale launch channel",
                "content": "Project Quasar launch channel is email according to the stale Gmail launch thread.",
                "source_url": "cortex-source://gmail#service=gmail&subject=Project%20Quasar&line=1&excerpt=quasar-gmail",
                "metadata": {"project": "Project Quasar", "line_start": 1, "source_quality": "stale"},
            },
            "slack": {
                "external_id": "quasar-slack-channel",
                "title": "Quasar Slack stale launch channel",
                "content": "Project Quasar launch channel is Slack according to the stale GTM Slack thread.",
                "source_url": "cortex-source://slack#service=slack&channel=CQUASAR&line=1&excerpt=quasar-slack",
                "metadata": {"project": "Project Quasar", "line_start": 1, "source_quality": "stale"},
            },
            "google-drive": {
                "external_id": "quasar-drive-plan",
                "title": "Quasar Drive stale launch channel",
                "content": "Project Quasar launch channel is Drive according to the stale planning document.",
                "source_url": "cortex-source://google-drive#service=google-drive&document=quasar-plan&line=1&excerpt=quasar-drive",
                "metadata": {"project": "Project Quasar", "line_start": 1, "source_quality": "draft"},
            },
            "notion": {
                "external_id": "quasar-notion-canonical",
                "title": "Quasar Notion canonical launch channel",
                "content": "Project Quasar launch channel now uses Notion. This is the current canonical source of truth.",
                "source_url": "cortex-source://notion#service=notion&page=quasar-canonical&line=1&excerpt=quasar-notion",
                "metadata": {"project": "Project Quasar", "line_start": 1, "source_quality": "canonical", "verified": True},
            },
        }
        for source, record in records.items():
            synced = self.store.sync_source_account_records(
                self.user_id,
                str(accounts[source]["id"]),
                records=[{"captured_at": "2026-07-02T10:00:00Z", **record}],
                processing="sync",
            )
            self.assertEqual(synced["saved"], 1)
            self.assertGreater(sum(int(item.get("memories") or 0) for item in synced["records"]), 0)

        search_results = self.store.search(self.user_id, "Project Quasar launch channel", limit=6, sector="Project Quasar")
        self.assertTrue(search_results)
        self.assertEqual(search_results[0]["provenance"]["external_id"], "quasar-notion-canonical")
        self.assertEqual(search_results[0]["provenance"]["source_account_id"], accounts["notion"]["id"])

        broad_answer = self.store.answer_query(self.user_id, "Project Quasar launch channel", limit=6, sector="Project Quasar")
        self.assertEqual(broad_answer["status"], "conflicted")
        self.assertEqual(broad_answer["citations"][0]["source_account_id"], accounts["notion"]["id"])
        self.assertEqual(broad_answer["citations"][0]["source_record_id"], "quasar-notion-canonical")
        self.assertEqual(broad_answer["conflicts"][0]["type"], "claim_conflict")
        self.assertEqual(broad_answer["conflicts"][0]["primary_source"], "notion")
        self.assertIn("notion", broad_answer["conflicts"][0]["primary_claim"])

        gmail_search = self.store.search(
            self.user_id,
            "Project Quasar launch channel",
            limit=6,
            source_account_id=str(accounts["gmail"]["id"]),
        )
        self.assertTrue(gmail_search)
        self.assertTrue(all(item["provenance"]["source_account_id"] == accounts["gmail"]["id"] for item in gmail_search))
        self.assertEqual(gmail_search[0]["provenance"]["external_id"], "quasar-gmail-email")

        gmail_answer = self.store.answer_query(
            self.user_id,
            "Project Quasar launch channel",
            limit=6,
            source_account_id=str(accounts["gmail"]["id"]),
        )
        self.assertEqual(gmail_answer["status"], "cited")
        self.assertEqual(gmail_answer["conflicts"], [])
        self.assertTrue(gmail_answer["citations"])
        self.assertTrue(all(citation["source_account_id"] == accounts["gmail"]["id"] for citation in gmail_answer["citations"]))
        self.assertIn("email", gmail_answer["answer"].lower())
        self.assertNotIn("Notion", gmail_answer["answer"])

    def test_trusted_canonical_conflict_primary_beats_newer_stale_source(self) -> None:
        self.store.update_settings(
            self.user_id,
            {
                "review_new_captures": True,
                "allow_pending_in_context": False,
            },
        )
        notion = self.store.upsert_source_account(
            self.user_id,
            source="notion",
            account_label="Vega Notion Canonical",
            account_identifier="notion-vega",
            connection_type="api-token",
            status="connected",
            auth_state="healthy",
            policy={"mode": "trusted", "review_required": False, "allow_ai_context": True},
        )
        slack = self.store.upsert_source_account(
            self.user_id,
            source="slack",
            account_label="Vega Slack Noise",
            account_identifier="slack-vega",
            connection_type="api-token",
            status="connected",
            auth_state="healthy",
            policy={"review_required": False, "allow_ai_context": True},
        )
        self.store.sync_source_account_records(
            self.user_id,
            notion["id"],
            records=[
                {
                    "external_id": "vega-notion-canonical",
                    "title": "Vega canonical launch channel",
                    "content": "Project Vega launch channel is Notion. This is the verified canonical source of truth.",
                    "source_url": "cortex-source://notion#service=notion&page=vega-canonical&line=1&excerpt=vega-notion",
                    "captured_at": "2026-05-01T00:00:00Z",
                    "metadata": {"project": "Project Vega", "source_quality": "canonical", "verified": True, "line_start": 1},
                }
            ],
            processing="sync",
        )
        self.store.sync_source_account_records(
            self.user_id,
            slack["id"],
            records=[
                {
                    "external_id": "vega-slack-stale",
                    "title": "Vega stale Slack launch channel",
                    "content": "Project Vega launch channel is email according to a newer but stale Slack thread.",
                    "source_url": "cortex-source://slack#service=slack&channel=CVEGA&line=1&excerpt=vega-slack",
                    "captured_at": "2026-06-15T00:00:00Z",
                    "metadata": {"project": "Project Vega", "source_quality": "stale", "line_start": 1},
                }
            ],
            processing="sync",
        )

        answer = self.store.answer_query(self.user_id, "Project Vega launch channel", limit=4, sector="Project Vega")

        self.assertEqual(answer["status"], "conflicted")
        self.assertEqual(answer["conflicts"][0]["type"], "claim_conflict")
        self.assertEqual(answer["conflicts"][0]["primary_source"], "notion")
        self.assertEqual(answer["conflicts"][0]["primary_claim"], "notion")
        self.assertEqual(answer["conflicts"][0]["conflicting_source"], "slack")
        self.assertEqual(answer["conflicts"][0]["conflicting_claim"], "email")
        self.assertEqual(answer["conflicts"][0]["reason"], "trusted_canonical_source")
        self.assertEqual(answer["citations"][0]["source_account_id"], notion["id"])
        self.assertEqual(answer["citations"][0]["source_record_id"], "vega-notion-canonical")

    def test_answer_query_does_not_cite_weak_related_source_backed_neighbors(self) -> None:
        self.store.update_settings(
            self.user_id,
            {
                "review_new_captures": False,
                "allow_pending_in_context": True,
            },
        )
        self.store._vector_ready = lambda conn: False
        self.store.save_capture(
            user_id=self.user_id,
            content="Project Anchor cited answer source selection keeps the GitHub issue as the primary evidence.",
            source="github",
            source_url="cortex-source://github#service=github&file=issues.json&line=71&excerpt=anchor-citation",
            title="Project Anchor citation source",
            extracted={
                "_timestamp": "2026-05-01T00:00:00+00:00",
                "summary": "Project Anchor citation source.",
                "records": [
                    {
                        "id": "ask_related_primary_cited",
                        "kind": "decision",
                        "layer": "decision",
                        "content": "Project Anchor cited answer source selection keeps the GitHub issue as the primary evidence.",
                        "summary": "Project Anchor source selection uses GitHub as primary evidence.",
                        "confidence": "confirmed",
                        "importance": 5,
                        "topics": ["anchor", "citation", "source-selection"],
                        "entity_ids": ["project_anchor"],
                    }
                ],
                "tasks": [],
                "entities": [
                    {
                        "id": "project_anchor",
                        "kind": "project",
                        "name": "Project Anchor",
                        "aliases": ["Anchor"],
                        "context": "Ask citation regression fixture.",
                    }
                ],
            },
        )
        self.store.save_capture(
            user_id=self.user_id,
            content="Project Anchor cafeteria seating plan moved to Friday after the facilities thread.",
            source="slack",
            source_url="cortex-source://slack#service=slack&channel=facilities&message=9&line=2&excerpt=anchor-cafeteria",
            title="Project Anchor weak neighbor",
            extracted={
                "_timestamp": "2026-05-01T00:01:00+00:00",
                "summary": "Project Anchor cafeteria note.",
                "records": [
                    {
                        "id": "ask_related_weak_neighbor",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "Project Anchor cafeteria seating plan moved to Friday after the facilities thread.",
                        "summary": "Project Anchor cafeteria seating moved to Friday.",
                        "confidence": "confirmed",
                        "importance": 4,
                        "topics": ["anchor", "cafeteria", "facilities"],
                        "entity_ids": ["project_anchor"],
                    }
                ],
                "tasks": [],
                "entities": [
                    {
                        "id": "project_anchor",
                        "kind": "project",
                        "name": "Project Anchor",
                        "aliases": ["Anchor"],
                        "context": "Ask citation regression fixture.",
                    }
                ],
            },
        )

        answer = self.store.answer_query(self.user_id, "Project Anchor cited answer source selection primary evidence", limit=4)
        citation_ids = {citation["id"] for citation in answer["citations"]}

        self.assertIn("ask_related_primary_cited", citation_ids)
        self.assertNotIn("ask_related_weak_neighbor", citation_ids)
        self.assertEqual(answer["citations"][0]["line_start"], "71")
        self.assertEqual(answer["citations"][0]["source_excerpt"], "anchor-citation")

    def test_answer_query_does_not_present_uncited_matches_as_citations(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})
        self.store.save_capture(
            user_id=self.user_id,
            content="Atlas unsupported reimbursement owner appears only in an uncited scratch note.",
            source="scratch-note",
            source_url=None,
            title="Uncited scratch note",
            extracted={
                "_timestamp": "2026-05-01T00:00:00+00:00",
                "summary": "Uncited scratch note.",
                "records": [
                    {
                        "id": "ask_uncited_match_only",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "Atlas unsupported reimbursement owner appears only in an uncited scratch note.",
                        "summary": "Uncited reimbursement owner scratch note.",
                        "confidence": "confirmed",
                        "importance": 5,
                        "topics": ["atlas", "reimbursement"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )

        answer = self.store.answer_query(self.user_id, "Atlas unsupported reimbursement owner", limit=3)

        self.assertEqual(answer["status"], "no_cited_evidence")
        self.assertEqual(answer["citations"], [])
        self.assertIn("did not find a cited item", answer["answer"])
        self.assertEqual(answer["results"][0]["id"], "ask_uncited_match_only")
        self.assertIsNone(answer["results"][0]["source_url"])

    def test_answer_query_marks_related_but_incomplete_cited_evidence_low_confidence(self) -> None:
        self.store.update_settings(
            self.user_id,
            {
                "review_new_captures": False,
                "allow_pending_in_context": True,
            },
        )
        self.store._vector_ready = lambda conn: False
        self.store.save_capture(
            user_id=self.user_id,
            content="Project Atlas payroll reimbursement policy is approved for local beta expenses.",
            source="github",
            source_url="cortex-source://github#service=github&file=issues.json&line=88&excerpt=atlas-payroll-policy",
            title="Atlas payroll reimbursement policy",
            extracted={
                "_timestamp": "2026-05-01T00:00:00+00:00",
                "summary": "Atlas payroll reimbursement policy.",
                "records": [
                    {
                        "id": "ask_low_confidence_related_cited",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "Project Atlas payroll reimbursement policy is approved for local beta expenses.",
                        "summary": "Atlas payroll reimbursement policy is approved.",
                        "confidence": "confirmed",
                        "importance": 4,
                        "sector": "Project Atlas",
                        "topics": ["project-atlas", "payroll", "reimbursement"],
                        "entity_ids": ["project_atlas"],
                    }
                ],
                "tasks": [],
                "entities": [
                    {
                        "id": "project_atlas",
                        "kind": "project",
                        "name": "Project Atlas",
                        "aliases": ["Atlas"],
                    }
                ],
            },
        )

        answer = self.store.answer_query(self.user_id, "Project Atlas payroll reimbursement owner", limit=3, sector="Project Atlas")

        self.assertEqual(answer["status"], "low_confidence")
        self.assertEqual(answer["evidence"]["missing_fields"], ["owner"])
        self.assertEqual(answer["citations"][0]["id"], "ask_low_confidence_related_cited")
        self.assertIn("Coverage check", answer["answer"])
        self.assertIn("not enough evidence for owner", answer["answer"])
        self.assertIn("verify before acting", answer["answer"])

    def test_source_filter_uses_service_family_aliases_without_overexpanding(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})
        records = [
            ("source_alias_gmail", "gmail", "Aliasfilter mailbox source family includes the Gmail customer escalation."),
            ("source_alias_outlook", "outlook", "Aliasfilter mailbox source family includes the Outlook renewal thread."),
            ("source_alias_drive", "google-drive", "Aliasfilter mailbox source family should not include this Drive planning doc."),
            ("source_alias_slack", "slack", "Aliasfilter mailbox source family should not include this Slack channel note."),
        ]
        for record_id, source, content in records:
            self.store.save_capture(
                user_id=self.user_id,
                content=content,
                source=source,
                source_url=f"cortex-source://{source}#line=1&excerpt=aliasfilter",
                title=f"{source} alias filter seed",
                extracted={
                    "_timestamp": "2026-05-01T00:00:00+00:00",
                    "summary": f"{source} alias filter seed.",
                    "records": [
                        {
                            "id": record_id,
                            "kind": "claim",
                            "layer": "semantic",
                            "content": content,
                            "summary": content,
                            "confidence": "confirmed",
                            "importance": 3,
                            "topics": ["aliasfilter", "mailbox"],
                            "entity_ids": [],
                        }
                    ],
                    "tasks": [],
                    "entities": [],
                },
            )

        email_results = self.store.search(self.user_id, "aliasfilter mailbox source family", limit=10, source="email")
        email_ids = {item["id"] for item in email_results}

        self.assertIn("source_alias_gmail", email_ids)
        self.assertIn("source_alias_outlook", email_ids)
        self.assertNotIn("source_alias_drive", email_ids)
        self.assertNotIn("source_alias_slack", email_ids)

        gmail_results = self.store.search(self.user_id, "aliasfilter mailbox source family", limit=10, source="gmail")
        self.assertEqual({item["id"] for item in gmail_results}, {"source_alias_gmail"})

    def test_scale_eval_checks_ask_citations_and_context_pack(self) -> None:
        scale_db_path = Path(self.tmp.name) / "retrieval-scale-small.sqlite"
        scale_vault_path = Path(self.tmp.name) / "RetrievalScale.vault"

        result = run_retrieval_scale_eval(
            scale_db_path,
            scale_vault_path,
            user_id="retrieval-scale-small-test",
            record_count=40,
            chunk_size=15,
            limit=10,
            max_rank=3,
            max_search_ms=1000.0,
            runs=1,
            warmups=0,
            seed=2026,
        )

        self.assertEqual(result["status"], "ok")
        answer_report = result["answer_report"]
        self.assertEqual(answer_report["status"], "ok")
        self.assertEqual(answer_report["target"]["first_citation_id"], TARGET_ID)
        self.assertEqual(answer_report["target"]["source_url"], TARGET_SOURCE_URL)
        self.assertEqual(answer_report["target"]["line_start"], "42")
        self.assertEqual(answer_report["target"]["source_excerpt"], "canonical-target-record")
        self.assertEqual(answer_report["target"]["result_rank"], 1)
        self.assertTrue(answer_report["context_pack_contains_target"])
        self.assertTrue(answer_report["context_pack_contains_source_url"])

    def _save_near_tie_claim(self, *, record_id: str, content: str, captured_at: str, importance: int = 2) -> None:
        self.store.save_capture(
            user_id=self.user_id,
            content=content,
            source="unit-test",
            source_url=None,
            title=record_id,
            extracted={
                "_timestamp": captured_at,
                "summary": record_id,
                "records": [
                    {
                        "id": record_id,
                        "kind": "claim",
                        "layer": "semantic",
                        "content": content,
                        "summary": content[:80],
                        "confidence": "confirmed",
                        "importance": importance,
                        "topics": ["boreas", "cache", "invalidation"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )

    def test_ask_abstains_on_unrelated_query_instead_of_citing_noise(self) -> None:
        # Core trust contract: Ask cites or abstains. On a small corpus the search
        # fallback returns a best-available memory even when nothing matches; Ask
        # must not present that as a citation for an unrelated question.
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})
        cap = self.store.save_capture(
            user_id=self.user_id,
            content="We decided Cortex Pro will be priced at $19/month because early users found $29 too steep.",
            source="obsidian",
            source_url="local-file://Pricing.md#line=1&excerpt=abc123",
            title="Pricing Decision",
            extracted={
                "_timestamp": "2026-05-01T00:00:00+00:00",
                "summary": "Pricing decision.",
                "records": [
                    {
                        "id": "abstain_pricing_decision",
                        "kind": "decision",
                        "layer": "decision",
                        "content": "We decided Cortex Pro will be priced at $19/month because early users found $29 too steep.",
                        "summary": "Cortex Pro priced at $19/month.",
                        "confidence": "confirmed",
                        "importance": 4,
                        "topics": ["pricing", "cortex"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )
        self.store.approve_capture(self.user_id, cap["capture_id"])

        related = self.store.answer_query(self.user_id, "What did we decide about pricing")
        self.assertEqual(related["status"], "cited")
        self.assertGreaterEqual(len(related["citations"]), 1)

        for unrelated in ("What is the capital of Mongolia", "How do I fix a flat bicycle tire"):
            answer = self.store.answer_query(self.user_id, unrelated)
            self.assertEqual(answer["status"], "no_cited_evidence", unrelated)
            self.assertEqual(answer["citations"], [], unrelated)

    def test_recency_boost_prefers_fresh_memory_in_near_tie(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})
        now = datetime.now(timezone.utc)
        # Stale row repeats a query term so bm25 places it first; only the
        # recency boost can flip the fresh row ahead of it.
        self._save_near_tie_claim(
            record_id="recency_stale_claim",
            content="Boreas cache invalidation strategy uses invalidation markers for cache entries.",
            captured_at=(now - timedelta(days=400)).isoformat(),
        )
        self._save_near_tie_claim(
            record_id="recency_fresh_claim",
            content="Boreas cache invalidation strategy uses versioned markers for entries.",
            captured_at=(now - timedelta(days=1)).isoformat(),
        )

        results = self.store.search(self.user_id, "boreas cache invalidation strategy markers", limit=2)

        self.assertEqual(results[0]["id"], "recency_fresh_claim")
        self.assertEqual(results[1]["id"], "recency_stale_claim")

    def test_importance_boost_prefers_high_importance_in_near_tie(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})
        captured_at = "2026-05-01T00:00:00+00:00"
        # Low-importance row repeats a query term so bm25 places it first;
        # only the importance boost can flip the high-importance row ahead.
        self._save_near_tie_claim(
            record_id="importance_low_claim",
            content="Boreas cache invalidation policy tracks invalidation windows for caches.",
            captured_at=captured_at,
            importance=2,
        )
        self._save_near_tie_claim(
            record_id="importance_high_claim",
            content="Boreas cache invalidation policy tracks rollout windows for caches.",
            captured_at=captured_at,
            importance=5,
        )

        results = self.store.search(self.user_id, "boreas cache invalidation policy windows", limit=2)

        self.assertEqual(results[0]["id"], "importance_high_claim")
        self.assertEqual(results[1]["id"], "importance_low_claim")

    def test_recency_boost_does_not_override_layer_intent(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})
        now = datetime.now(timezone.utc)
        self.store.save_capture(
            user_id=self.user_id,
            content="Gamma launch decision fresh semantic note keeps context but is not the choice.",
            source="unit-test",
            source_url=None,
            title="Fresh semantic distractor",
            extracted={
                "_timestamp": (now - timedelta(days=1)).isoformat(),
                "summary": "Fresh semantic distractor.",
                "records": [
                    {
                        "id": "recency_semantic_distractor",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "Gamma launch decision fresh semantic note keeps context but is not the choice.",
                        "summary": "Fresh semantic gamma note.",
                        "confidence": "confirmed",
                        "importance": 2,
                        "topics": ["gamma", "launch", "decision"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )
        self.store.save_capture(
            user_id=self.user_id,
            content="Gamma launch decision: ship the beta behind a waitlist.",
            source="unit-test",
            source_url=None,
            title="Old decision",
            extracted={
                "_timestamp": (now - timedelta(days=400)).isoformat(),
                "summary": "Old decision.",
                "records": [
                    {
                        "id": "recency_old_decision",
                        "kind": "decision",
                        "layer": "decision",
                        "content": "Gamma launch decision: ship the beta behind a waitlist.",
                        "summary": "Ship the beta behind a waitlist.",
                        "confidence": "confirmed",
                        "importance": 2,
                        "topics": ["gamma", "launch", "decision"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )

        results = self.store.search(self.user_id, "gamma launch decision", limit=2)

        self.assertEqual(results[0]["id"], "recency_old_decision")
        self.assertEqual(results[0]["layer"], "decision")

    def test_source_quality_boost_does_not_override_layer_intent_relevance(self) -> None:
        self.store.update_settings(
            self.user_id,
            {
                "review_new_captures": False,
                "allow_pending_in_context": True,
                "source_policies": {"github": {"mode": "trusted"}},
            },
        )
        self.store.save_capture(
            user_id=self.user_id,
            content="Planning alpha rank decision appears in a trusted GitHub note but is not the final choice.",
            source="github",
            source_url="cortex-source://github#service=github&file=issues.json&line=20&excerpt=planning-alpha",
            title="Trusted semantic distractor",
            extracted={
                "_timestamp": "2026-05-01T00:00:00+00:00",
                "summary": "Trusted semantic distractor.",
                "records": [
                    {
                        "id": "quality_trusted_semantic_distractor",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "Planning alpha rank decision appears in a trusted GitHub note but is not the final choice.",
                        "summary": "Trusted semantic planning note.",
                        "confidence": "confirmed",
                        "importance": 5,
                        "topics": ["planning", "alpha", "rank", "decision"],
                        "entity_ids": [],
                        "metadata": {"source_quality": "verified", "canonical": True},
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )
        self.store.save_capture(
            user_id=self.user_id,
            content="Planning alpha rank decision: choose stdlib unittest for retrieval evaluation.",
            source="unit-test",
            source_url=None,
            title="Decision near tie",
            extracted={
                "_timestamp": "2026-05-01T00:00:00+00:00",
                "summary": "Decision near tie.",
                "records": [
                    {
                        "id": "quality_decision_intent",
                        "kind": "decision",
                        "layer": "decision",
                        "content": "Planning alpha rank decision: choose stdlib unittest for retrieval evaluation.",
                        "summary": "Use stdlib unittest for retrieval evaluation.",
                        "confidence": "confirmed",
                        "importance": 1,
                        "topics": ["planning", "alpha", "rank", "decision"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )

        results = self.store.search(self.user_id, "planning alpha rank decision", limit=2)

        self.assertEqual(results[0]["id"], "quality_decision_intent")
        self.assertEqual(results[0]["layer"], "decision")

    def test_search_diagnostics_reports_degraded_vector_path_and_candidate_depth(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})
        self.store._vector_ready = lambda conn: False
        self.store.save_capture(
            user_id=self.user_id,
            content="Project Diagnostic retrieval should report when vector search is unavailable.",
            source="github",
            source_url="cortex-source://github#service=github&file=issues.json&line=8&excerpt=diagnostic",
            title="Diagnostics seed",
            extracted={
                "_timestamp": "2026-05-01T00:00:00+00:00",
                "summary": "Diagnostics seed.",
                "records": [
                    {
                        "id": "diagnostic_vector_unavailable",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "Project Diagnostic retrieval should report when vector search is unavailable.",
                        "summary": "Search diagnostics report vector unavailability.",
                        "confidence": "confirmed",
                        "importance": 3,
                        "topics": ["diagnostic", "retrieval", "vector"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )
        diagnostics: dict[str, object] = {}

        results = self.store.search(self.user_id, "Project Diagnostic retrieval vector unavailable", limit=3, _diagnostics=diagnostics)

        self.assertEqual(results[0]["id"], "diagnostic_vector_unavailable")
        self.assertEqual(diagnostics["candidate_limit"], 50)
        self.assertTrue(diagnostics["degraded"])
        self.assertIn("vector_index_unavailable", diagnostics["degraded_reasons"])
        self.assertIn("embedding_provider", diagnostics)
        self.assertIn("fts", diagnostics["mode_counts"])

    def test_large_noisy_corpus_retrieves_cited_service_record(self) -> None:
        self.store.update_settings(
            self.user_id,
            {
                "review_new_captures": False,
                "allow_pending_in_context": True,
                "source_policies": {"github": {"mode": "trusted"}},
            },
        )
        distractors = [
            {
                "id": f"scale_noise_{index:03d}",
                "kind": "claim",
                "layer": "semantic",
                "content": (
                    f"Project Cascade retrieval scale note {index}: latency budget discussion mentions "
                    "launch planning, archive cleanup, and unrelated source reviews."
                ),
                "summary": f"Project Cascade noisy scale note {index}.",
                "confidence": "confirmed",
                "importance": 2,
                "topics": ["cascade", "retrieval", "scale", "noise"],
                "entity_ids": [],
            }
            for index in range(360)
        ]
        target = {
            "id": "scale_cited_service_target",
            "kind": "decision",
            "layer": "decision",
            "content": (
                "Project Cascade retrieval scale source truth decision: use the GitHub issue as the canonical "
                "latency budget and cite it before any generic archive note."
            ),
            "summary": "GitHub issue is the canonical Project Cascade latency budget.",
            "confidence": "confirmed",
            "importance": 4,
            "topics": ["cascade", "retrieval", "scale", "latency"],
            "entity_ids": ["project:cascade"],
            "metadata": {"source_quality": "canonical", "verified": True},
        }
        self.store.save_capture(
            user_id=self.user_id,
            content="\n".join([*(record["content"] for record in distractors), target["content"]]),
            source="github",
            source_url="cortex-source://github#service=github&repository=Cortex&file=issues.json&line=412&row=17&excerpt=cascade-latency",
            title="Large noisy corpus",
            extracted={
                "_timestamp": "2026-05-01T00:00:00+00:00",
                "summary": "Large noisy retrieval corpus.",
                "records": [*distractors, target],
                "tasks": [],
                "entities": [],
            },
        )
        diagnostics: dict[str, object] = {}

        results = self.store.search(
            self.user_id,
            "Project Cascade canonical latency budget GitHub source truth decision",
            limit=3,
            _diagnostics=diagnostics,
        )

        self.assertEqual(results[0]["id"], "scale_cited_service_target")
        self.assertEqual(results[0]["layer"], "decision")
        self.assertIn("line=412", results[0]["source_url"])
        self.assertEqual(diagnostics["candidate_limit"], 50)
        self.assertGreaterEqual(diagnostics["mode_counts"]["fts"], 1)
        self.assertLessEqual(len(results), 3)

    def test_search_diversifies_redundant_same_thread_evidence(self) -> None:
        self.store.update_settings(
            self.user_id,
            {
                "review_new_captures": False,
                "allow_pending_in_context": True,
            },
        )
        duplicate_prefix = (
            "Project Aurora launch retrieval safety same thread duplicate evidence says the rollout notes, "
            "beta onboarding, support triage, retry behavior, memory retrieval safety, citation review, "
            "and approval routing all came from the same status thread and should not crowd out other evidence. "
        )
        for index in range(8):
            self.store.save_capture(
                user_id=self.user_id,
                content=f"{duplicate_prefix}Duplicate variant {index} repeats the same Slack status context.",
                source="slack",
                source_url=f"cortex-source://slack#channel=launch&message=aurora-duplicate-{index}",
                title=f"Aurora duplicate Slack note {index}",
                extracted={
                    "_timestamp": f"2026-05-01T00:0{index}:00+00:00",
                    "summary": "Aurora redundant Slack thread.",
                    "records": [
                        {
                            "id": f"aurora_duplicate_{index}",
                            "kind": "claim",
                            "layer": "semantic",
                            "content": f"{duplicate_prefix}Duplicate variant {index} repeats the same Slack status context.",
                            "summary": "Aurora redundant Slack thread.",
                            "confidence": "confirmed",
                            "importance": 5,
                            "topics": ["aurora", "launch", "retrieval", "safety"],
                            "entity_ids": [],
                            "metadata": {"conversation": "aurora-launch-same-thread"},
                        }
                    ],
                    "tasks": [],
                    "entities": [],
                },
            )

        distinct_records = [
            {
                "id": "aurora_canonical_decision",
                "source": "github",
                "source_url": "cortex-source://github#repository=cortex&issue=aurora-decision&line=10",
                "kind": "decision",
                "layer": "decision",
                "content": "Project Aurora launch retrieval safety decision: cite the canonical GitHub issue before same-thread status chatter.",
                "summary": "Canonical Aurora retrieval safety decision.",
                "topics": ["aurora", "launch", "retrieval", "safety", "decision"],
            },
            {
                "id": "aurora_procedure",
                "source": "notion",
                "source_url": "cortex-source://notion#page=aurora-runbook",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Project Aurora launch retrieval safety procedure: verify citations, backup state, and rollback steps before inviting beta users.",
                "summary": "Aurora launch retrieval safety procedure.",
                "topics": ["aurora", "launch", "retrieval", "safety", "procedure"],
            },
            {
                "id": "aurora_negative_constraint",
                "source": "gmail",
                "source_url": "cortex-source://gmail#thread=aurora-risk",
                "kind": "negative",
                "layer": "negative",
                "content": "Project Aurora launch retrieval safety constraint: do not let duplicated Slack thread notes replace decision and runbook evidence.",
                "summary": "Aurora retrieval safety negative constraint.",
                "topics": ["aurora", "launch", "retrieval", "safety", "constraint"],
            },
        ]
        for record in distinct_records:
            self.store.save_capture(
                user_id=self.user_id,
                content=record["content"],
                source=record["source"],
                source_url=record["source_url"],
                title=record["summary"],
                extracted={
                    "_timestamp": "2026-05-02T00:00:00+00:00",
                    "summary": record["summary"],
                    "records": [
                        {
                            "id": record["id"],
                            "kind": record["kind"],
                            "layer": record["layer"],
                            "content": record["content"],
                            "summary": record["summary"],
                            "confidence": "confirmed",
                            "importance": 3,
                            "topics": record["topics"],
                            "entity_ids": [],
                            "metadata": {"source_quality": "canonical"},
                        }
                    ],
                    "tasks": [],
                    "entities": [],
                },
            )

        results = self.store.search(
            self.user_id,
            "Project Aurora launch retrieval safety",
            limit=5,
        )
        result_ids = [item["id"] for item in results]
        duplicate_ids = [item_id for item_id in result_ids if item_id.startswith("aurora_duplicate_")]

        self.assertLessEqual(len(duplicate_ids), 1)
        self.assertIn("aurora_canonical_decision", result_ids)
        self.assertIn("aurora_procedure", result_ids)
        self.assertIn("aurora_negative_constraint", result_ids)

    def test_confidence_breaks_ranking_ties_toward_trusted_memories(self) -> None:
        # Two memories that match the query equally (same query terms, same importance) but
        # differ only in confidence. The low-confidence one is inserted FIRST (so its tiny
        # order boost favors it); confidence must still rank the confirmed one above it, and
        # the low-confidence one last. Before wiring _confidence_boost, confidence was inert.
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})
        self.store._vector_ready = lambda conn: False
        self.store.save_capture(
            user_id=self.user_id,
            content="Orion deployment rollback procedure confidence seed.",
            source="confidence-ranking-test",
            source_url=None,
            title="Confidence ranking seed",
            extracted={
                "_timestamp": "2026-05-01T00:00:00+00:00",
                "summary": "Orion deployment rollback procedure confidence seed.",
                "records": [
                    {
                        "id": "conf_low_rollback",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "The Orion deployment rollback procedure draft omega is unverified.",
                        "summary": "Orion deployment rollback procedure draft omega.",
                        "confidence": "low",
                        "importance": 3,
                        "topics": ["orion", "deployment", "rollback"],
                        "entity_ids": [],
                    },
                    {
                        "id": "conf_confirmed_rollback",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "The Orion deployment rollback procedure alpha is confirmed.",
                        "summary": "Orion deployment rollback procedure alpha.",
                        "confidence": "confirmed",
                        "importance": 3,
                        "topics": ["orion", "deployment", "rollback"],
                        "entity_ids": [],
                    },
                ],
                "tasks": [],
                "entities": [],
            },
        )

        results = self.store.search(self.user_id, "orion deployment rollback procedure", limit=5)
        ranked_ids = [item["id"] for item in results]
        self.assertIn("conf_confirmed_rollback", ranked_ids)
        self.assertIn("conf_low_rollback", ranked_ids)
        self.assertLess(
            ranked_ids.index("conf_confirmed_rollback"),
            ranked_ids.index("conf_low_rollback"),
            "confirmed memory should outrank the low-confidence near-tie",
        )

    def test_confidence_boost_is_inert_for_uniform_confirmed_confidence(self) -> None:
        # The overwhelmingly common case: everything is "confirmed", so the boost is uniform
        # and must not perturb ordering (guards the eval gate against confidence drift).
        store = self.store
        self.assertEqual(store._confidence_boost({"confidence": "confirmed"}), store._confidence_boost({"confidence": "high"}))
        self.assertEqual(store._confidence_boost({"confidence": "unknown-label"}), 0.0)
        self.assertGreater(store._confidence_boost({"confidence": "confirmed"}), store._confidence_boost({"confidence": "low"}))


if __name__ == "__main__":
    unittest.main()
