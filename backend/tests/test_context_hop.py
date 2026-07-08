"""Objective 8: expand_context tool + ONE bounded auto-hop on low_confidence.

Invariants under test: the hop is default-OFF (parity), NEVER fabricates (cite-or-abstain survives
the hop), and — when enabled — recovers a cited answer from a memory the first search under-ranked.
Plus expand_context's cite-or-abstain + treat_as_data, and its MCP scope gating."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.database import init_db
from backend.app.mcp_tools import call_tool, tool_required_capabilities, READ_TOOLS, TOOLS
from backend.app.storage import CortexStore, now_iso

USER = "hop-user"


class ContextHopTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        self.vault_path = root / "vault"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.vault_path)
        self.store.update_settings(USER, {"review_new_captures": False, "allow_pending_in_context": True})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, mem_id: str, content: str, *, kind: str = "claim", entities=None, cited: bool = True) -> None:
        entities = entities or []
        self.store.save_capture(
            user_id=USER,
            content=content,
            source="obsidian",
            source_url=(f"local-file://{mem_id}.md" if cited else None),
            title=mem_id,
            cite_capture_provenance=cited,
            extracted={
                "_timestamp": now_iso(),
                "summary": content,
                "records": [
                    {"id": mem_id, "kind": kind, "layer": "semantic", "content": content,
                     "confidence": "confirmed", "importance": 3, "topics": [],
                     "entity_ids": [e["id"] for e in entities]}
                ],
                "tasks": [],
                "entities": entities,
            },
        )

    def _result_dict(self, query: str, mem_id: str) -> dict:
        for row in self.store.search(USER, query, limit=12):
            if str(row.get("id")) == mem_id:
                return row
        raise AssertionError(f"{mem_id} not retrievable via {query!r}")

    def _under_rank(self, owner_id: str, budget_id: str):
        """Model a LARGE corpus where the missing-field memory is under-ranked: the first search
        (the plain query, no 'cost'/'spend') sees only the owner memory; the hop query (which the
        builder pads with the field's evidence terms incl. 'cost'/'spend') additionally surfaces the
        budget memory. Both are REAL search-result dicts, so the real cite gate runs on them."""
        owner = self._result_dict("Project Atlas owned by Dana Lee", owner_id)
        budget = self._result_dict("Project Atlas budget spend cost", budget_id)

        def fake_search(user_id, query, **kwargs):
            low = query.lower()
            if "cost" in low or "spend" in low:  # the hop query
                return [owner, budget]
            return [owner]  # first pass: budget memory is under-ranked out of reach

        return mock.patch.object(self.store, "search", side_effect=fake_search)

    # ---- the hop-query builder (pure) ----

    def test_context_hop_queries_builder(self) -> None:
        queries = self.store._context_hop_queries("what is the budget for Project Atlas", ["budget"])
        self.assertTrue(queries)
        self.assertLessEqual(len(queries), 3)
        self.assertTrue(any("cost" in q or "budget" in q for q in queries))
        # No missing fields, but an entity present -> returns the bare entity query (or empty).
        self.assertLessEqual(len(self.store._context_hop_queries("Project Atlas", [])), 3)

    # ---- the auto-hop ----

    def test_hop_disabled_stays_low_confidence(self) -> None:
        self._seed("mem_owner", "Project Atlas is owned by Dana Lee.")
        self._seed("mem_budget", "Project Atlas budget is 40000 dollars for Q3.")
        with self._under_rank("mem_owner", "mem_budget"):
            answer = self.store.answer_query(USER, "what is the budget for Project Atlas", limit=5)
        self.assertEqual(answer["status"], "low_confidence")
        self.assertNotIn("mem_budget", [c.get("id") for c in answer["citations"]])
        self.assertNotIn("recovered_by_hop", answer["evidence"])

    def test_hop_recovers_when_enabled(self) -> None:
        self._seed("mem_owner", "Project Atlas is owned by Dana Lee.")
        self._seed("mem_budget", "Project Atlas budget is 40000 dollars for Q3.")
        with self._under_rank("mem_owner", "mem_budget"), mock.patch.dict(
            "os.environ", {"CORTEX_CONTEXT_HOP": "1"}, clear=False
        ):
            answer = self.store.answer_query(USER, "what is the budget for Project Atlas", limit=5)
        self.assertIn("mem_budget", [c.get("id") for c in answer["citations"]], answer)
        self.assertTrue(answer["evidence"].get("recovered_by_hop"))
        self.assertIn(answer["status"], ("cited", "conflicted"))

    def test_hop_never_fabricates_when_no_new_cited_evidence(self) -> None:
        # Only a cited owner memory (no budget anywhere) -> the hop finds nothing new to cite, so the
        # answer stays low_confidence and every citation still has a real source (cite-or-abstain).
        self._seed("mem_owner", "Project Atlas is owned by Dana Lee.")
        with mock.patch.dict("os.environ", {"CORTEX_CONTEXT_HOP": "1"}, clear=False):
            answer = self.store.answer_query(USER, "what is the budget for Project Atlas", limit=5)
        self.assertEqual(answer["status"], "low_confidence")
        for citation in answer["citations"]:
            self.assertTrue(citation.get("source_url") or citation.get("source"))

    # ---- expand_context ----

    def test_expand_context_cites_or_abstains(self) -> None:
        dana = {"id": "person_dana-lee", "kind": "person", "name": "Dana Lee", "aliases": []}
        self._seed("mem_dec", "Dana Lee decided to use sharded SQLite for the launch.", kind="decision", entities=[dana])
        result = self.store.expand_context(USER, ["Dana Lee"])
        self.assertEqual(result["status"], "cited", result)
        self.assertTrue(result["entities"])
        self.assertTrue(all(e.get("treat_as_data") is True for e in result["entities"]))
        self.assertTrue(result["entities"][0]["cited_memory_ids"])
        # Unknown entity -> abstain.
        empty = self.store.expand_context(USER, ["Nonexistent Person"])
        self.assertEqual(empty["status"], "no_cited_evidence")
        self.assertEqual(empty["entities"], [])

    def test_expand_context_mcp_wiring_and_scope_gate(self) -> None:
        self.assertIn("expand_context", READ_TOOLS)
        self.assertEqual(tool_required_capabilities("expand_context", scoped=True), ["read"])
        self.assertTrue(any(t["name"] == "expand_context" for t in TOOLS))
        dana = {"id": "person_dana-lee", "kind": "person", "name": "Dana Lee", "aliases": []}
        self._seed("mem_dec", "Dana Lee decided to use sharded SQLite.", kind="decision", entities=[dana])
        payload = call_tool(self.store, USER, "expand_context", {"names": ["Dana Lee"]}, token_scopes=["read"])
        self.assertIn("entities", payload)
        self.assertIn("status", payload)
        with self.assertRaises(PermissionError):
            call_tool(self.store, USER, "expand_context", {"names": ["Dana Lee"]}, token_scopes=[])


if __name__ == "__main__":
    unittest.main()
