from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app import mcp_tools
from backend.app.database import init_db
from backend.app.mcp_tools import call_tool, mql_parse
from backend.app.smp import RECEIPT_VERDICTS, build_receipt
from backend.app.storage import CortexStore


class MqlParseTests(unittest.TestCase):
    """The query_memory JSON Schema IS the DSL. mql_parse validates it and rejects anything
    malformed or over-scoped; it can only ever produce read-scoped retrieval kwargs."""

    def test_requires_exactly_one_of_ask_or_find(self) -> None:
        with self.assertRaises(ValueError):
            mql_parse({})
        with self.assertRaises(ValueError):
            mql_parse({"ask": "x", "find": "y"})
        self.assertEqual(mql_parse({"ask": "what did I decide"})["mode"], "ask")
        self.assertEqual(mql_parse({"find": "database"})["mode"], "find")

    def test_rejects_unknown_layer(self) -> None:
        with self.assertRaises(ValueError):
            mql_parse({"find": "x", "layers": ["not_a_layer"]})
        parsed = mql_parse({"find": "x", "layers": ["decision", "preference", "decision"]})
        self.assertEqual(parsed["layers"], ["decision", "preference"])

    def test_rejects_out_of_range_thresholds_and_budget(self) -> None:
        for bad in ({"min_relevance": 1.5}, {"min_trust": -0.2}, {"min_relevance": "abc"}):
            with self.assertRaises(ValueError):
                mql_parse({"find": "x", **bad})
        with self.assertRaises(ValueError):
            mql_parse({"find": "x", "budget_tokens": 10})
        with self.assertRaises(ValueError):
            mql_parse({"find": "x", "budget_tokens": 999999})

    def test_rejects_bad_expand_direction(self) -> None:
        with self.assertRaises(ValueError):
            mql_parse({"find": "x", "expand": "sideways"})
        self.assertEqual(mql_parse({"find": "x", "expand": "neighbors"})["expand"], "neighbors")

    def test_k_is_bounded(self) -> None:
        self.assertEqual(mql_parse({"find": "x", "k": 9999})["k"], mcp_tools.MQL_MAX_K)
        self.assertEqual(mql_parse({"find": "x", "k": 0})["k"], 1)


class MqlNarrowSubsetTests(unittest.TestCase):
    """The narrowing pass only removes items → the filtered result is a strict subset of the
    unfiltered retrieval (the subset invariant)."""

    def _refs(self, result: dict) -> set[str]:
        return {item["ref"] for item in result["items"]}

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "cortex.db"
        init_db(db_path)
        self.store = CortexStore(db_path, Path(self._tmp.name) / "vault")
        self.user = "mql-user"
        self.store.update_settings(
            self.user,
            {"allow_agent_reads": True, "allow_agent_writes": True, "review_new_captures": False},
        )
        for text, source in [
            ("I prefer Postgres over MySQL for production databases.", "notes"),
            ("We decided to ship the beta on Friday.", "notes"),
            ("The database migration ran successfully last night.", "notes"),
            ("I dislike long meetings without an agenda.", "notes"),
            ("Postgres connection pooling should use pgbouncer.", "notes"),
        ]:
            call_tool(self.store, self.user, "remember_this", {"content": text, "source": source})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_filters_narrow_to_subset(self) -> None:
        base = call_tool(self.store, self.user, "query_memory", {"find": "database", "k": 50})
        filtered = call_tool(
            self.store,
            self.user,
            "query_memory",
            {"find": "database", "k": 50, "layers": ["preference"]},
        )
        self.assertTrue(self._refs(filtered) <= self._refs(base))
        self.assertLessEqual(filtered["count"], base["count"])

    def test_min_trust_narrows_to_subset(self) -> None:
        base = call_tool(self.store, self.user, "query_memory", {"find": "database", "k": 50})
        strict = call_tool(
            self.store, self.user, "query_memory", {"find": "database", "k": 50, "min_trust": 0.99}
        )
        self.assertTrue(self._refs(strict) <= self._refs(base))

    def test_query_memory_is_read_only_and_scoped(self) -> None:
        self.assertIn("query_memory", mcp_tools.READ_TOOLS)
        self.assertEqual(mcp_tools.tool_required_capabilities("query_memory", scoped=True), ["read"])
        with self.assertRaises(PermissionError):
            call_tool(self.store, self.user, "query_memory", {"find": "x"}, token_scopes=[])

    def test_ask_mode_returns_cited_items(self) -> None:
        result = call_tool(self.store, self.user, "query_memory", {"ask": "what database do I prefer?"})
        self.assertEqual(result["mode"], "ask")
        self.assertEqual(result["scope"], "read")
        self.assertIn("items", result)


class ExpandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "cortex.db"
        init_db(db_path)
        self.store = CortexStore(db_path, Path(self._tmp.name) / "vault")
        self.user = "expand-user"
        self.store.update_settings(
            self.user,
            {"allow_agent_reads": True, "allow_agent_writes": True, "review_new_captures": False},
        )
        # Distinct facts (near-identical captures dedup into one memory) that share the keyword
        # "project" so search returns several rows to page over.
        for fact in [
            "Sarah leads the payments project and owns billing.",
            "Tom runs the search project with the retrieval team.",
            "The mobile project shipped to the App Store last week.",
            "Our infrastructure project migrated to Kubernetes.",
            "The analytics project needs a design review from Sarah.",
            "The onboarding project launches next quarter.",
        ]:
            call_tool(self.store, self.user, "remember_this", {"content": fact, "source": "notes"})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_expand_document_returns_full_memory(self) -> None:
        hits = call_tool(self.store, self.user, "query_memory", {"find": "project", "k": 3})
        ref = hits["items"][0]["ref"]
        doc = call_tool(self.store, self.user, "expand", {"ref": ref, "direction": "document"})
        self.assertTrue(doc["found"])
        self.assertEqual(doc["document"]["id"], ref)

    def test_expand_document_unknown_ref_is_honest(self) -> None:
        doc = call_tool(self.store, self.user, "expand", {"ref": "mem_nope", "direction": "document"})
        self.assertFalse(doc["found"])
        self.assertIsNone(doc["document"])

    def test_expand_neighbors_returns_graph(self) -> None:
        result = call_tool(self.store, self.user, "expand", {"ref": "Sarah", "direction": "neighbors"})
        self.assertEqual(result["direction"], "neighbors")
        self.assertIn("status", result)

    def test_expand_more_pages_cursor(self) -> None:
        page1 = call_tool(self.store, self.user, "query_memory", {"find": "project", "k": 2})
        self.assertTrue(page1["cursor"])
        page2 = call_tool(self.store, self.user, "expand", {"ref": page1["cursor"], "direction": "more"})
        p1 = {item["ref"] for item in page1["items"]}
        p2 = {item["ref"] for item in page2["items"]}
        self.assertFalse(p1 & p2)  # no overlap between pages

    def test_expand_more_unrecognized_cursor(self) -> None:
        result = call_tool(self.store, self.user, "expand", {"ref": "not-a-cursor", "direction": "more"})
        self.assertFalse(result["found"])
        self.assertEqual(result["reason"], "unrecognized_cursor")

    def test_expand_is_read_scoped(self) -> None:
        self.assertIn("expand", mcp_tools.READ_TOOLS)
        with self.assertRaises(PermissionError):
            call_tool(self.store, self.user, "expand", {"ref": "x"}, token_scopes=[])


class ReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "cortex.db"
        init_db(db_path)
        self.store = CortexStore(db_path, Path(self._tmp.name) / "vault")
        self.user = "receipt-user"
        self.store.update_settings(
            self.user,
            {"allow_agent_reads": True, "allow_agent_writes": True, "review_new_captures": False},
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_remember_this_returns_typed_receipt(self) -> None:
        saved = call_tool(
            self.store, self.user, "remember_this", {"content": "I prefer tabs over spaces."}
        )
        self.assertIn("capture_id", saved)  # backward-compatible shape preserved
        receipt = saved["receipt"]
        self.assertEqual(
            set(receipt),
            {"tool", "stored_id", "assigned_layer", "verdict", "dedup_basis", "occurrences", "supersede_handle"},
        )
        self.assertIn(receipt["verdict"], RECEIPT_VERDICTS)
        self.assertTrue(receipt["stored_id"])

    def test_propose_memory_routes_to_review_with_receipt(self) -> None:
        proposed = call_tool(
            self.store, self.user, "propose_memory", {"content": "Maybe we should switch to Rust."}
        )
        self.assertEqual(proposed["review_status"], "pending")
        self.assertEqual(proposed["receipt"]["verdict"], "new")
        self.assertIn("propose_memory", mcp_tools.WRITE_TOOLS)

    def test_propose_memory_requires_write_scope(self) -> None:
        with self.assertRaises(PermissionError):
            call_tool(self.store, self.user, "propose_memory", {"content": "x"}, token_scopes=["read"])

    def test_build_receipt_defensive_defaults(self) -> None:
        receipt = build_receipt({}, tool="remember_this")
        self.assertEqual(receipt["verdict"], "new")
        self.assertEqual(receipt["occurrences"], 1)
        self.assertIsNone(receipt["dedup_basis"])

    def test_build_receipt_reads_storage_verdict(self) -> None:
        receipt = build_receipt(
            {
                "stored_id": "mem_9",
                "assigned_layer": "preference",
                "verdict": "echo_strengthened",
                "dedup_basis": "content_hash",
                "occurrences": 3,
                "supersede_handle": "sh_1",
            }
        )
        self.assertEqual(receipt["verdict"], "echo_strengthened")
        self.assertEqual(receipt["occurrences"], 3)
        self.assertEqual(receipt["dedup_basis"], "content_hash")


if __name__ == "__main__":
    unittest.main()
