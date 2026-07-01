from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.storage import CortexStore, MEMORY_LAYERS
from scripts.retrieval_eval import (
    DISTRACTOR_MEMORIES,
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

    def test_seed_representative_memories_covers_every_layer(self) -> None:
        memories = seed_representative_memories(self.store, self.user_id)

        self.assertEqual({memory["layer"] for memory in memories}, MEMORY_LAYERS)
        self.assertEqual(len(memories), len(MEMORY_LAYERS))

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

    def test_focused_queries_retrieve_expected_layer_and_content(self) -> None:
        result = evaluate_retrieval(self.store, self.user_id)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["seeded_memories"], len(MEMORY_LAYERS))
        self.assertEqual(result["distractor_memories"], len(DISTRACTOR_MEMORIES))
        self.assertEqual(result["focused_retrieval_memories"], 8)
        self.assertEqual(result["noisy_import_memories"], 11)
        self.assertGreaterEqual(result["direct_connector_memories"], 13)
        self.assertEqual(set(result["seeded_layers"]), MEMORY_LAYERS)
        expected_noisy_cases = 11
        expected_direct_connector_cases = 13
        expected_source_backed_cases = 1
        expected_case_count = len(RETRIEVAL_CASES) + expected_noisy_cases + expected_direct_connector_cases + expected_source_backed_cases
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
        self.assertEqual(result["metrics"]["by_category"]["sector_scoping"]["case_count"], 2)
        self.assertEqual(result["metrics"]["by_category"]["temporal_validity"]["case_count"], 1)
        self.assertEqual(result["metrics"]["by_category"]["related_memory"]["case_count"], 1)
        self.assertEqual(result["metrics"]["by_category"]["source_backed_ranking"]["case_count"], 1)
        self.assertEqual(result["source_backed_fallback"]["top_result"], "rq_source_backed_lexical_fallback_cited")
        self.assertIn("lexical_fallback", result["source_backed_fallback"]["retrieval_modes"])

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
            "source_backed_ranking",
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

        self.assertEqual(answer["citations"][0]["id"], "ask_source_backed_cited")
        self.assertEqual(answer["citations"][0]["line_start"], "34")
        self.assertEqual(answer["citations"][0]["source_excerpt"], "atlas-ask-source")
        self.assertTrue(all(citation["source_url"] for citation in answer["citations"]))
        self.assertNotIn("ask_source_backed_uncited", {citation["id"] for citation in answer["citations"]})
        self.assertEqual(answer["results"][0]["id"], "ask_source_backed_cited")

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


if __name__ == "__main__":
    unittest.main()
