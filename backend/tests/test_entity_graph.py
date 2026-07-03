from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.storage import CortexStore, now_iso


class EntityGraphStoreTests(unittest.TestCase):
    """Slice 1 of the personal knowledge graph: build the entity co-mention graph from the
    existing tables, analyze it deterministically (centrality/communities/bridges), and rank the
    Profile's People & projects by centrality (the graphify "god node" idea, on SQLite)."""

    ENTITIES = {
        "ent_alice": {"id": "ent_alice", "kind": "person", "name": "Alice", "aliases": [], "context": ""},
        "ent_zephyr": {"id": "ent_zephyr", "kind": "project", "name": "Project Zephyr", "aliases": [], "context": ""},
        "ent_bob": {"id": "ent_bob", "kind": "person", "name": "Bob", "aliases": [], "context": ""},
    }

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "graph-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, mem_id: str, content: str, entity_ids: list[str], layer: str = "semantic") -> None:
        self.store.save_capture(
            user_id=self.user_id,
            content=content,
            source="obsidian",
            source_url=f"local-file://{mem_id}",
            title=mem_id,
            extracted={
                "_timestamp": now_iso(),
                "summary": content,
                "records": [
                    {"id": mem_id, "kind": "claim", "layer": layer, "content": content,
                     "confidence": "confirmed", "importance": 3, "topics": [], "entity_ids": entity_ids}
                ],
                "tasks": [],
                "entities": [self.ENTITIES[e] for e in entity_ids],
            },
        )

    def _seed_graph(self) -> None:
        # Alice co-occurs with Zephyr twice and with Bob once -> Alice is the central "god node".
        self._seed("m1", "Alice leads Project Zephyr.", ["ent_alice", "ent_zephyr"])
        self._seed("m2", "Alice reviewed the Project Zephyr roadmap.", ["ent_alice", "ent_zephyr"])
        self._seed("m3", "Alice and Bob paired on the migration.", ["ent_alice", "ent_bob"])

    def test_build_entity_graph_nodes_and_comention_edges(self) -> None:
        self._seed_graph()
        nodes, edges = self.store.build_entity_graph(self.user_id)
        ids = {n["id"] for n in nodes}
        self.assertEqual(ids, {"ent_alice", "ent_zephyr", "ent_bob"})
        # Co-mention edges (shared-memory count). The store may ALSO emit explicit graph_edges
        # (e.g. the extractor's "co_occurs") for the same pair; analyze_entity_graph sums them, so
        # here we assert the co_mention edge weight specifically.
        comention = {
            tuple(sorted((e["source"], e["target"]))): e["weight"]
            for e in edges
            if e["relation"] == "co_mention"
        }
        self.assertEqual(comention[("ent_alice", "ent_zephyr")], 2.0)
        self.assertEqual(comention[("ent_alice", "ent_bob")], 1.0)
        self.assertTrue(all(e["confidence"] in {"EXTRACTED", "INFERRED"} for e in edges))

    def test_analysis_ranks_central_entity_first(self) -> None:
        self._seed_graph()
        analysis = self.store.entity_graph_analysis(self.user_id)
        self.assertEqual(analysis["ranked"][0], "ent_alice")
        self.assertGreater(analysis["centrality"]["ent_alice"], analysis["centrality"]["ent_bob"])
        self.assertEqual(analysis["centrality"]["ent_alice"], 1.0)  # normalized max
        self.assertEqual(analysis, self.store.entity_graph_analysis(self.user_id))  # deterministic

    def test_profile_people_ranked_by_centrality(self) -> None:
        self._seed_graph()
        profile = self.store.build_profile(self.user_id)
        people = next((s for s in profile["sections"] if s["id"] == "people_projects"), None)
        self.assertIsNotNone(people, profile)
        # Alice (highest centrality, 3 links) surfaces first; both she and Zephyr clear the
        # >=2-link floor; Bob (1 link) is below it and is omitted from the section.
        self.assertIn("Alice", people["elements"][0]["text"])
        texts = " ".join(el["text"] for el in people["elements"])
        self.assertNotIn("Bob", texts)

    def test_empty_graph_is_safe(self) -> None:
        nodes, edges = self.store.build_entity_graph(self.user_id)
        self.assertEqual(nodes, [])
        self.assertEqual(edges, [])
        analysis = self.store.entity_graph_analysis(self.user_id)
        self.assertEqual(analysis["ranked"], [])

    def test_bridge_surfaces_as_mirror_insight_when_no_repetition(self) -> None:
        # Two tight clusters {Alice,Zephyr} and {Bob,Design} joined by ONE bridge memory that
        # mentions Zephyr AND Bob. No repeated behavioral pattern exists, so the repetition-based
        # Mirror Moment abstains and the graph "surprising connection" fills the slot.
        # Two internally-dense clusters {Alice,Zephyr} and {Bob,Design} (co-mentioned 3x each) with
        # a WEAKER cross-cluster bridge Zephyr<->Bob (2x). Distinct layers so no single behavioral
        # pattern repeats -> the repetition Mirror abstains and the graph bridge is what's left.
        self.ENTITIES["ent_design"] = {"id": "ent_design", "kind": "project", "name": "Design System", "aliases": [], "context": ""}
        for i, layer in enumerate(("episodic", "decision", "style")):
            self._seed(f"az{i}", "Alice and Project Zephyr.", ["ent_alice", "ent_zephyr"], layer=layer)
        for i, layer in enumerate(("semantic", "procedural", "negative")):
            self._seed(f"bd{i}", "Bob and the Design System.", ["ent_bob", "ent_design"], layer=layer)
        # The bridge (weaker than the clusters): Zephyr and Bob co-mentioned twice.
        self._seed("zb0", "Project Zephyr borrowed a pattern from Bob's team.", ["ent_zephyr", "ent_bob"], layer="preference")
        self._seed("zb1", "Bob advised on the Project Zephyr rollout.", ["ent_zephyr", "ent_bob"], layer="episodic")

        insight = self.store.mirror_insight(self.user_id)
        self.assertIsNotNone(insight, "expected a bridge insight when repetition abstains")
        self.assertEqual(insight["layer"], "relationship")
        self.assertIn("connect", insight["headline"].lower())
        self.assertGreaterEqual(len(insight["evidence"]["memory_ids"]), 2)


if __name__ == "__main__":
    unittest.main()
