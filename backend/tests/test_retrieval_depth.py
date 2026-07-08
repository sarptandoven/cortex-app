"""N6: retrieval depth — fusion entity-overlap boost (increment 1) + a bounded SECOND recovery hop
(increment 2). Both flag-gated, no-op-when-off (byte-identical), and cite-or-abstain preserved."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.database import init_db
from backend.app.storage import CortexStore, ENTITY_OVERLAP_RETRIEVAL_BOOST_MAX, now_iso

USER = "depth-user"


class EntityBoostTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        db = root / "d.sqlite"
        init_db(db)
        self.store = CortexStore(db, root / "vault")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_boost_is_zero_without_plan_entities(self) -> None:
        # The flag-off contract: callers pass an empty set -> exactly 0.0 -> fused score unchanged.
        row = {"content": "Marcus owns billing", "summary": "", "topics": "", "entity_ids_json": "[]"}
        self.assertEqual(self.store._entity_overlap_boost(row, set()), 0.0)

    def test_boost_caps_at_max_and_scales_with_overlap(self) -> None:
        row = {"content": "marcus and dana shipped billing", "summary": "", "topics": "", "entity_ids_json": "[]"}
        full = self.store._entity_overlap_boost(row, {"marcus", "dana"})
        self.assertLessEqual(full, ENTITY_OVERLAP_RETRIEVAL_BOOST_MAX + 1e-9)
        self.assertEqual(round(full, 6), round(ENTITY_OVERLAP_RETRIEVAL_BOOST_MAX, 6))  # both present -> max
        half = self.store._entity_overlap_boost(row, {"marcus", "nobody"})
        self.assertLess(half, full)
        self.assertGreater(half, 0.0)
        self.assertEqual(self.store._entity_overlap_boost(row, {"nobody"}), 0.0)  # none present -> 0

    def test_fusion_flag_off_is_byte_identical(self) -> None:
        # With CORTEX_ENTITY_BOOST unset, fusion order is identical regardless of query entities.
        rows_a = [{"id": "m1"}, {"id": "m2"}]
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("CORTEX_ENTITY_BOOST", None)
            off_plain = self.store._fuse_search_rows("hello", rows_a, [], [], [], 10)
            off_entity = self.store._fuse_search_rows("Marcus Dana", rows_a, [], [], [], 10)
        self.assertEqual([r["id"] for r in off_plain], [r["id"] for r in off_entity])


class SecondHopTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.store.update_settings(USER, {"review_new_captures": False, "allow_pending_in_context": True})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, mem_id: str, content: str) -> None:
        self.store.save_capture(
            user_id=USER, content=content, source="obsidian", source_url=f"local-file://{mem_id}.md",
            title=mem_id, cite_capture_provenance=True,
            extracted={"_timestamp": now_iso(), "summary": content,
                       "records": [{"id": mem_id, "kind": "claim", "layer": "semantic", "content": content,
                                    "confidence": "confirmed", "importance": 3, "topics": [], "entity_ids": []}],
                       "tasks": [], "entities": []},
        )

    def _pool(self) -> dict:
        pool: dict = {}
        for query in ("Project Atlas VendorCo billing budget owner vendor", "billing budget VendorCo quarter dollars"):
            for row in self.store.search(USER, query, limit=12):
                pool[str(row.get("id"))] = row
        return pool

    def _stage(self, owner_id: str, link_id: str, budget_id: str):
        """A 2-link chain: first pass sees only the owner memory (names the intermediate entity but
        no budget); hop 1's query surfaces the link memory; hop 2 (which follows the intermediate
        entity 'VendorCo' from the citations) surfaces the budget memory."""
        pool = self._pool()
        owner, link, budget = pool.get(owner_id), pool.get(link_id), pool.get(budget_id)
        assert owner and link and budget, f"seed memories not all retrievable: {list(pool)}"

        def fake_search(user_id, query, **kwargs):
            low = query.lower()
            if "vendorco" in low and ("cost" in low or "spend" in low or "budget" in low):
                return [owner, link, budget]           # hop 2: follows VendorCo -> budget
            if "cost" in low or "spend" in low:
                return [owner, link]                    # hop 1: query entity + field terms -> link
            return [owner]                              # first pass: only the owner memory

        return mock.patch.object(self.store, "search", side_effect=fake_search)

    def test_second_hop_disabled_does_not_reach_hop2_memory(self) -> None:
        self._seed("mem_owner", "Project Atlas is billed via VendorCo.")
        self._seed("mem_link", "Project Atlas routes billing through VendorCo the vendor.")
        self._seed("mem_budget", "VendorCo billing budget is 40000 dollars per quarter.")
        with self._stage("mem_owner", "mem_link", "mem_budget"), mock.patch.dict(
            "os.environ", {"CORTEX_CONTEXT_HOP": "1"}, clear=False
        ):
            import os
            os.environ.pop("CORTEX_CONTEXT_HOP2", None)
            answer = self.store.answer_query(USER, "what is the budget for Project Atlas", limit=5)
        self.assertNotIn("recovered_by_second_hop", answer["evidence"])

    def test_second_hop_recovers_deep_chain(self) -> None:
        self._seed("mem_owner", "Project Atlas is billed via VendorCo.")
        self._seed("mem_link", "Project Atlas routes billing through VendorCo the vendor.")
        self._seed("mem_budget", "VendorCo billing budget is 40000 dollars per quarter.")
        with self._stage("mem_owner", "mem_link", "mem_budget"), mock.patch.dict(
            "os.environ", {"CORTEX_CONTEXT_HOP": "1", "CORTEX_CONTEXT_HOP2": "1"}, clear=False
        ):
            answer = self.store.answer_query(USER, "what is the budget for Project Atlas", limit=5)
        self.assertTrue(answer["evidence"].get("recovered_by_second_hop"), answer)
        self.assertIn("mem_budget", [c.get("id") for c in answer["citations"]], answer)  # the 2-hop-away fact
        self.assertIn(answer["status"], ("cited", "conflicted"))
        for citation in answer["citations"]:
            self.assertTrue(citation.get("source_url") or citation.get("source"))  # never fabricated

    def test_second_hop_requires_first_hop(self) -> None:
        # HOP2 without HOP is inert (guarded by recovered_by_hop) -> behaves like today.
        self._seed("mem_owner", "Project Atlas is owned by Dana.")
        with mock.patch.dict("os.environ", {"CORTEX_CONTEXT_HOP2": "1"}, clear=False):
            import os
            os.environ.pop("CORTEX_CONTEXT_HOP", None)
            answer = self.store.answer_query(USER, "what is the budget for Project Atlas", limit=5)
        self.assertNotIn("recovered_by_second_hop", answer["evidence"])


if __name__ == "__main__":
    unittest.main()
