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

    def test_focused_queries_retrieve_expected_layer_and_content(self) -> None:
        result = evaluate_retrieval(self.store, self.user_id)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["seeded_memories"], len(MEMORY_LAYERS))
        self.assertEqual(result["distractor_memories"], len(DISTRACTOR_MEMORIES))
        self.assertEqual(result["noisy_import_memories"], 7)
        self.assertEqual(set(result["seeded_layers"]), MEMORY_LAYERS)
        expected_noisy_cases = 6
        self.assertEqual(len(result["checks"]), len(RETRIEVAL_CASES) + expected_noisy_cases)
        self.assertEqual(result["metrics"]["overall"]["case_count"], len(RETRIEVAL_CASES) + expected_noisy_cases)
        self.assertEqual(result["metrics"]["overall"]["top1_accuracy"], 1.0)
        self.assertEqual(result["metrics"]["overall"]["recall@1"], 1.0)
        self.assertEqual(result["metrics"]["overall"]["recall@3"], 1.0)
        self.assertEqual(result["metrics"]["by_category"]["noisy_import"]["case_count"], 2)
        self.assertEqual(result["metrics"]["by_category"]["noisy_import_preference"]["case_count"], 1)
        self.assertEqual(result["metrics"]["by_category"]["noisy_import_style"]["case_count"], 1)
        self.assertEqual(result["metrics"]["by_category"]["noisy_import_negative"]["case_count"], 1)
        self.assertEqual(result["metrics"]["by_category"]["noisy_import_docs"]["case_count"], 1)

        categories = {case.category for case in RETRIEVAL_CASES} | {
            "noisy_import",
            "noisy_import_preference",
            "noisy_import_style",
            "noisy_import_negative",
            "noisy_import_docs",
        }
        self.assertIn("paraphrase", categories)
        self.assertIn("style_recall", categories)
        self.assertIn("negative_recall", categories)
        self.assertEqual(set(result["metrics"]["by_category"]), categories)

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
        ]
        for query, expected_layer, expected_id in cases:
            with self.subTest(query=query):
                results = self.store.search(self.user_id, query, limit=3)
                self.assertEqual(results[0]["id"], expected_id)
                self.assertEqual(results[0]["layer"], expected_layer)


if __name__ == "__main__":
    unittest.main()
