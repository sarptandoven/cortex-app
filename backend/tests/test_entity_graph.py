from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import connect, init_db
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

    def test_entity_neighborhood_returns_cited_connected_subgraph(self) -> None:
        self._seed_graph()  # Alice-Zephyr x2, Alice-Bob x1
        hood = self.store.entity_neighborhood(self.user_id, "Alice")
        self.assertIsNotNone(hood)
        self.assertEqual(hood["focal"]["label"], "Alice")
        labels = [c["label"] for c in hood["connections"]]
        self.assertIn("Project Zephyr", labels)
        self.assertIn("Bob", labels)
        # Strongest connection first (Zephyr, 2 shared memories > Bob, 1), each cited.
        self.assertEqual(hood["connections"][0]["label"], "Project Zephyr")
        for connection in hood["connections"]:
            self.assertTrue(connection["shared_memory_ids"], connection)
        # Resolves by id too, and abstains on an unknown entity.
        self.assertIsNotNone(self.store.entity_neighborhood(self.user_id, "ent_alice"))
        self.assertIsNone(self.store.entity_neighborhood(self.user_id, "Nonexistent Person"))

    def test_relationship_context_includes_connections(self) -> None:
        self._seed_graph()
        ctx = self.store.person_context(self.user_id, "Alice")
        self.assertIn("connections", ctx)
        self.assertTrue(any(c["label"] == "Project Zephyr" for c in ctx["connections"]))

    def test_person_map_composes_profile_and_graph(self) -> None:
        self._seed_graph()
        pmap = self.store.person_map(self.user_id)
        self.assertIn("profile", pmap)
        self.assertIn("graph", pmap)
        graph = pmap["graph"]
        self.assertIn("hubs", graph)
        self.assertIn("communities", graph)
        self.assertIn("bridges", graph)
        # Alice is the central hub of this little graph.
        self.assertTrue(graph["hubs"])
        self.assertEqual(graph["hubs"][0]["label"], "Alice")
        self.assertIsInstance(pmap["readiness"], int)

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

    # ---- Objective 9: /v1/graph carries the analysis for the Constellation UI ----

    def test_graph_response_carries_centrality_community_and_hub_flags(self) -> None:
        self._seed_graph()
        graph = self.store.graph(self.user_id, limit=200)
        by_id = {n["id"]: n for n in graph["nodes"]}
        alice = by_id["ent_alice"]
        self.assertIn("centrality", alice)
        self.assertIn("community", alice)
        self.assertTrue(alice["is_hub"])
        self.assertEqual(alice["centrality"], 1.0)  # normalized max
        self.assertGreaterEqual(alice["centrality"], by_id["ent_bob"]["centrality"])

    def test_graph_analysis_block_present_and_deterministic(self) -> None:
        self._seed_graph()
        g1 = self.store.graph(self.user_id, limit=200)
        g2 = self.store.graph(self.user_id, limit=200)
        self.assertIn("analysis", g1)
        self.assertEqual(g1["analysis"]["hub_ids"][0], "ent_alice")
        self.assertGreaterEqual(g1["analysis"]["community_count"], 1)
        self.assertEqual(g1["analysis"], g2["analysis"])  # deterministic -> no git-sync churn

    def test_graph_edges_flag_bridges(self) -> None:
        self._seed_graph()
        graph = self.store.graph(self.user_id, limit=200)
        self.assertTrue(all("is_bridge" in e for e in graph["edges"]))
        self.assertTrue(all(isinstance(e["is_bridge"], bool) for e in graph["edges"]))

    def test_non_entity_nodes_have_no_community(self) -> None:
        self._seed_graph()
        graph = self.store.graph(self.user_id, limit=200)
        non_entity = [n for n in graph["nodes"] if n["id"] not in self.ENTITIES]
        self.assertTrue(non_entity)  # memory / capture nodes are seeded
        self.assertTrue(all("community" not in n for n in non_entity))

    def test_graph_edges_survive_contains_flood_dedup_and_null_missing_evidence(self) -> None:
        # Regression for the Constellation starvation bug: the old edge query took the newest
        # limit*2 graph_edges rows by recency. A flood of newer 'contains' (capture->memory)
        # edges pushed every entity-connecting edge ('co_occurs'/'mentions') out of the window,
        # so all entity nodes rendered isolated. The fix queries edges whose BOTH endpoints are
        # loaded nodes, dedupes per (source, target, kind), and nulls (not drops) evidence_id
        # when the evidence node isn't loaded.
        limit = 30
        old_ts = "2026-01-01T00:00:00+00:00"

        def _seed_at(mem_id: str, content: str, entity_ids: list[str], timestamp: str) -> None:
            self.store.save_capture(
                user_id=self.user_id,
                content=content,
                source="obsidian",
                source_url=f"local-file://{mem_id}",
                title=mem_id,
                extracted={
                    "_timestamp": timestamp,
                    "summary": content,
                    "records": [
                        {"id": mem_id, "kind": "claim", "layer": "semantic", "content": content,
                         "confidence": "confirmed", "importance": 3, "topics": [], "entity_ids": entity_ids}
                    ],
                    "tasks": [],
                    "entities": [self.ENTITIES[e] for e in entity_ids],
                },
            )

        # Old entity-bearing captures: the SAME pair twice -> two co_occurs rows in graph_edges
        # with the same (source, target, kind) but different evidence (each capture id).
        _seed_at("old1", "Alice leads Project Zephyr.", ["ent_alice", "ent_zephyr"], old_ts)
        _seed_at("old2", "Alice reviewed the Project Zephyr roadmap.", ["ent_alice", "ent_zephyr"], old_ts)
        _seed_at("old3", "Alice and Bob paired on the migration.", ["ent_alice", "ent_bob"], old_ts)

        # Flood: more than limit*2 newer entity-free captures, each emitting a newer 'contains'
        # edge. Under the old recency-window query, these crowd out every entity edge.
        for i in range(limit * 2 + 10):
            _seed_at(f"flood{i}", f"Routine note {i}.", [], f"2026-06-01T00:{i // 60:02d}:{i % 60:02d}+00:00")

        graph = self.store.graph(self.user_id, limit=limit)
        node_ids = {n["id"] for n in graph["nodes"]}
        self.assertIn("ent_alice", node_ids)
        self.assertIn("ent_zephyr", node_ids)

        entity_edges = [
            e for e in graph["edges"]
            if e["kind"] in {"co_occurs", "mentions", "involves"}
            and e["source_id"] in self.ENTITIES and e["target_id"] in self.ENTITIES
        ]
        self.assertTrue(entity_edges, "entity-connecting edges must survive the contains flood")
        pair_kinds = {(e["source_id"], e["target_id"], e["kind"]) for e in entity_edges}
        self.assertIn(("ent_alice", "ent_zephyr", "co_occurs"), pair_kinds)

        # Duplicate (source, target, kind) rows exist in the table but are deduplicated here.
        with connect(self.db_path) as conn:
            raw = conn.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE user_id = ? AND source_id = ? AND target_id = ? AND kind = ?",
                (self.user_id, "ent_alice", "ent_zephyr", "co_occurs"),
            ).fetchone()[0]
        self.assertGreaterEqual(raw, 2, "seed must create duplicate co_occurs rows")
        keys = [(e["source_id"], e["target_id"], e["kind"]) for e in graph["edges"]]
        self.assertEqual(len(keys), len(set(keys)), "response edges must be deduped per (source, target, kind)")

        # The old captures (co_occurs evidence) fell out of the capture node window (limit // 3
        # newest), so those edges must come back with evidence_id nulled instead of being dropped.
        az = next(e for e in graph["edges"] if (e["source_id"], e["target_id"], e["kind"]) == ("ent_alice", "ent_zephyr", "co_occurs"))
        self.assertIsNone(az["evidence_id"])
        # And every edge keeps the full API shape.
        for edge in graph["edges"]:
            for key in ("id", "user_id", "source_id", "target_id", "kind", "weight", "evidence_id", "created_at", "is_bridge"):
                self.assertIn(key, edge)
            self.assertEqual(edge["user_id"], self.user_id)

    def test_empty_graph_analysis_block_is_safe(self) -> None:
        graph = self.store.graph("empty-user", limit=50)
        self.assertEqual(graph["analysis"]["community_count"], 0)
        self.assertEqual(graph["analysis"]["hub_ids"], [])
        self.assertEqual(graph["nodes"], [])


if __name__ == "__main__":
    unittest.main()
