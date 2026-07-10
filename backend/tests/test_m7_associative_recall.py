from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import connect, init_db
from backend.app.storage import CortexStore, now_iso


class AssociativeRecallTests(unittest.TestCase):
    USER = "m7-associative-user"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.store._vector_ready = lambda conn: False
        self.store.update_settings(
            self.USER,
            {"review_new_captures": False, "allow_pending_in_context": False},
        )
        self.capture_ids: dict[str, str] = {}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(
        self,
        memory_id: str,
        content: str,
        entities: list[tuple[str, str, str]],
        *,
        sector: str = "",
        source: str = "obsidian",
    ) -> str:
        entity_rows = [
            {"id": entity_id, "name": name, "kind": kind, "aliases": [], "context": ""}
            for entity_id, name, kind in entities
        ]
        result = self.store.save_capture(
            user_id=self.USER,
            content=content,
            source=source,
            source_url=f"local-file://m7/{memory_id}.md",
            title=memory_id,
            extracted={
                "_timestamp": now_iso(),
                "summary": content,
                "records": [
                    {
                        "id": memory_id,
                        "kind": "claim",
                        "layer": "semantic",
                        "content": content,
                        "summary": content,
                        "confidence": "confirmed",
                        "importance": 4,
                        "sector": sector,
                        "topics": [],
                        "entity_ids": [row["id"] for row in entity_rows],
                    }
                ],
                "tasks": [],
                "entities": entity_rows,
            },
        )
        self.capture_ids[memory_id] = result["capture_id"]
        return result["capture_id"]

    def _seed_chain(self) -> None:
        self._seed(
            "m7_seed",
            "Dana is accountable for the portfolio named Lunar Ribbon.",
            [("ent_dana", "Dana", "person"), ("ent_lunar", "Lunar Ribbon", "project")],
        )
        self._seed(
            "m7_bridge",
            "Lunar Ribbon maps to the internal program called Orion.",
            [("ent_lunar", "Lunar Ribbon", "project"), ("ent_orion", "Orion", "project")],
        )
        self._seed(
            "m7_target",
            "Orion uses the calibrated release threshold 0.72.",
            [("ent_orion", "Orion", "project")],
        )

    def test_ppr_recovers_a_true_two_hop_memory_that_one_hop_misses(self) -> None:
        self._seed_chain()
        query = "Dana accountable portfolio"

        direct = self.store.search(self.USER, query, limit=8)
        one_hop = self.store.search(
            self.USER,
            query,
            limit=8,
            include_related=True,
            association_mode="one_hop",
        )
        ppr = self.store.search(
            self.USER,
            query,
            limit=8,
            include_related=True,
            association_mode="ppr",
        )

        self.assertNotIn("m7_target", {item["id"] for item in direct})
        self.assertNotIn("m7_target", {item["id"] for item in one_hop})
        by_id = {item["id"]: item for item in ppr}
        self.assertIn("m7_target", by_id)
        relationship = by_id["m7_target"]["relationship"]
        self.assertEqual(relationship["kind"], "associative")
        self.assertEqual(relationship["algorithm"], "personalized_pagerank")
        self.assertEqual(relationship["depth"], 2)
        self.assertEqual(relationship["related_to_id"], "m7_seed")
        self.assertTrue(relationship["path_verified"])
        self.assertEqual(
            [step["target_id"] for step in relationship["path"]],
            ["m7_bridge", "m7_target"],
        )

    def test_associative_payload_is_explicit_and_diagnostic(self) -> None:
        self._seed_chain()
        payload = self.store.public_search_payload(
            self.USER,
            "Dana accountable portfolio",
            limit=8,
            associative=True,
        )

        self.assertTrue(payload["associative"])
        self.assertIn("associative", payload["retrieval"]["used_modes"])
        graph = payload["retrieval"]["associative"]
        self.assertEqual(graph["algorithm"], "personalized_pagerank")
        self.assertEqual(graph["seed_count"], 1)
        self.assertGreaterEqual(graph["node_count"], 3)
        self.assertGreaterEqual(graph["edge_count"], 2)
        self.assertTrue(graph["converged"])

    def test_pending_superseded_and_low_trust_nodes_cannot_propagate(self) -> None:
        for state in ("pending", "superseded", "low_trust"):
            with self.subTest(state=state):
                self.tearDown()
                self.setUp()
                self._seed_chain()
                with connect(self.db_path) as conn:
                    if state == "pending":
                        conn.execute(
                            "UPDATE captures SET review_status = 'pending', approved_at = NULL WHERE user_id = ? AND id = ?",
                            (self.USER, self.capture_ids["m7_target"]),
                        )
                    elif state == "superseded":
                        conn.execute(
                            "UPDATE memories SET status = 'superseded' WHERE user_id = ? AND id = 'm7_target'",
                            (self.USER,),
                        )
                    else:
                        conn.execute(
                            "UPDATE memories SET trust_score = 0.1 WHERE user_id = ? AND id = 'm7_target'",
                            (self.USER,),
                        )
                    conn.commit()

                results = self.store.search(
                    self.USER,
                    "Dana accountable portfolio",
                    limit=8,
                    include_related=True,
                    association_mode="ppr",
                )
                self.assertNotIn("m7_target", {item["id"] for item in results})

    def test_source_and_sector_filters_apply_to_every_hop(self) -> None:
        self._seed(
            "m7_seed",
            "Dana is accountable for the portfolio named Lunar Ribbon.",
            [("ent_dana", "Dana", "person"), ("ent_lunar", "Lunar Ribbon", "project")],
            sector="work",
            source="obsidian",
        )
        self._seed(
            "m7_bridge",
            "Lunar Ribbon maps to the internal program called Orion.",
            [("ent_lunar", "Lunar Ribbon", "project"), ("ent_orion", "Orion", "project")],
            sector="work",
            source="obsidian",
        )
        self._seed(
            "m7_target",
            "Orion uses the calibrated release threshold 0.72.",
            [("ent_orion", "Orion", "project")],
            sector="private",
            source="note",
        )

        scoped = self.store.search(
            self.USER,
            "Dana accountable portfolio",
            limit=8,
            sector="work",
            source="obsidian",
            include_related=True,
            association_mode="ppr",
        )
        self.assertNotIn("m7_target", {item["id"] for item in scoped})

    def test_associative_citation_gate_rejects_tampered_paths(self) -> None:
        self._seed_chain()
        results = self.store.search(
            self.USER,
            "Dana accountable portfolio",
            limit=8,
            include_related=True,
            association_mode="ppr",
        )
        by_id = {item["id"]: item for item in results}
        target = by_id["m7_target"]
        primary = {"m7_seed": by_id["m7_seed"]}
        self.assertTrue(
            self.store._should_include_related_citation(
                "What value governs Dana's portfolio?", target, primary
            )
        )

        tampered = dict(target)
        tampered["relationship"] = {
            **target["relationship"],
            "path": [{**target["relationship"]["path"][0], "source_id": "forged"}, *target["relationship"]["path"][1:]],
        }
        self.assertFalse(
            self.store._should_include_related_citation(
                "What value governs Dana's portfolio?", tampered, primary
            )
        )


if __name__ == "__main__":
    unittest.main()
