"""Regression tests for PURGE-BY-SOURCE.

The "manage / purge stored data by source" surface lets a user inspect what each source
contributed (``source_memory_stats``) and wipe a single source wholesale
(``purge_source_memories``) when it imported garbage. A purge must remove EVERYTHING derived
from that source — memories, their captures, derived tasks, graph edges, vault mirror files,
and search/vector rows — then run orphan cleanup so the readiness gate (``diagnostics``) stays
green. It must be case-insensitive, leave sibling sources untouched, and be reachable (and
correctly scope-gated) through the MCP tools ``list_memory_sources`` (read) and
``delete_source_memories`` (write + destructive).
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

# Deterministic embeddings — the purge exercises the vector/search index rows.
os.environ["CORTEX_EMBEDDING_PROVIDER"] = "hash"

from backend.app import mcp_tools
from backend.app.database import init_db
from backend.app.extractor import extract_context
from backend.app.storage import CortexStore


class PurgeSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.user_id = "purge-user"

    def _save(self, content: str, *, source: str) -> dict:
        """Save a single-fact capture that distills to one memory at save time (no worker)."""
        return self.store.save_capture(
            user_id=self.user_id,
            content=content,
            source=source,
            source_url=None,
            title=None,
            extracted=extract_context(content, source),
            cite_capture_provenance=True,
            auto_approve=True,
        )

    def _bucket(self, stats: list[dict], source: str) -> dict | None:
        for bucket in stats:
            if (bucket["source"] or "").lower() == source.lower():
                return bucket
        return None

    def _seed_two_sources(self) -> None:
        self._save("My favorite programming language is Rust.", source="chatgpt")
        self._save("My favorite color is blue.", source="chatgpt")
        self._save("My flight to Tokyo departs at 9am on Friday.", source="gmail")

    def test_source_memory_stats_buckets_and_counts(self) -> None:
        """stats groups active memories by source, count desc, with correct counts."""
        self._seed_two_sources()
        stats = self.store.source_memory_stats(self.user_id)
        chatgpt = self._bucket(stats, "chatgpt")
        gmail = self._bucket(stats, "gmail")
        self.assertIsNotNone(chatgpt)
        self.assertIsNotNone(gmail)
        self.assertEqual(chatgpt["count"], 2)
        self.assertEqual(gmail["count"], 1)
        self.assertIsNotNone(chatgpt["last_captured_at"])
        # Ordered by count desc: the 2-memory chatgpt bucket precedes the 1-memory gmail bucket.
        sources_in_order = [b["source"] for b in stats]
        self.assertLess(sources_in_order.index("chatgpt"), sources_in_order.index("gmail"))

    def test_purge_removes_only_the_named_source(self) -> None:
        """Purging chatgpt removes exactly its memories/captures; gmail is untouched."""
        self._seed_two_sources()
        result = self.store.purge_source_memories(self.user_id, "chatgpt")
        self.assertTrue(result["deleted"])
        self.assertEqual(result["source"], "chatgpt")
        self.assertEqual(result["memory_count"], 2)
        self.assertEqual(result["capture_count"], 2)

        stats = self.store.source_memory_stats(self.user_id)
        self.assertIsNone(self._bucket(stats, "chatgpt"))  # chatgpt gone
        gmail = self._bucket(stats, "gmail")
        self.assertIsNotNone(gmail)  # gmail survives
        self.assertEqual(gmail["count"], 1)

    def test_purge_keeps_diagnostics_green(self) -> None:
        """After a purge the readiness gate stays 'ok' — no orphaned fts/relations/edges."""
        self._seed_two_sources()
        self.assertEqual(self.store.diagnostics(self.user_id)["status"], "ok")
        self.store.purge_source_memories(self.user_id, "chatgpt")
        diag = self.store.diagnostics(self.user_id)
        self.assertEqual(diag["status"], "ok")
        self.assertEqual(diag["fts_orphans"], 0)
        self.assertEqual(diag["relation_orphans"], 0)

    def test_purge_is_case_insensitive(self) -> None:
        """Purging 'ChatGPT' removes memories stored under 'chatgpt'."""
        self._seed_two_sources()
        result = self.store.purge_source_memories(self.user_id, "ChatGPT")
        self.assertTrue(result["deleted"])
        self.assertEqual(result["memory_count"], 2)
        self.assertIsNone(self._bucket(self.store.source_memory_stats(self.user_id), "chatgpt"))

    def test_purge_nonexistent_source_is_a_noop(self) -> None:
        """A source with nothing to purge returns deleted=False and zero counts, no error."""
        self._seed_two_sources()
        result = self.store.purge_source_memories(self.user_id, "notion")
        self.assertFalse(result["deleted"])
        self.assertEqual(result["memory_count"], 0)
        self.assertEqual(result["capture_count"], 0)
        self.assertEqual(result["task_count"], 0)
        self.assertEqual(result["edge_count"], 0)
        # Nothing was collaterally removed.
        self.assertEqual(len(self.store.source_memory_stats(self.user_id)), 2)

    def test_empty_source_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            self.store.purge_source_memories(self.user_id, "")
        with self.assertRaises(ValueError):
            self.store.purge_source_memories(self.user_id, "   ")

    def test_search_no_longer_returns_purged_content(self) -> None:
        """After purge, search returns none of the purged memory ids for its unique term."""
        self._seed_two_sources()
        before = self.store.search(self.user_id, "Rust")
        self.assertGreaterEqual(len(before), 1)
        purged_ids = {row["id"] for row in before}
        self.store.purge_source_memories(self.user_id, "chatgpt")
        after_ids = {row["id"] for row in self.store.search(self.user_id, "Rust")}
        self.assertEqual(purged_ids & after_ids, set())

    def test_mcp_list_sources_and_scoped_destructive_purge(self) -> None:
        """MCP: list_memory_sources returns buckets; delete_source_memories purges only with
        write+destructive scopes; a read-only token raises PermissionError and deletes nothing."""
        self._seed_two_sources()
        # Destructive agent actions are off by default; enable them for the authorized call.
        self.store.update_settings(self.user_id, {"allow_agent_destructive_actions": True})

        listed = mcp_tools.call_tool(
            self.store, self.user_id, "list_memory_sources", {}, token_scopes=["read"]
        )
        listed_sources = {b["source"] for b in listed["sources"]}
        self.assertIn("chatgpt", listed_sources)
        self.assertIn("gmail", listed_sources)

        # capabilities single-sourced from the scope sets
        caps = mcp_tools.tool_required_capabilities("delete_source_memories")
        self.assertIn("write", caps)
        self.assertIn("destructive", caps)

        # A read-only token must not be able to invoke the destructive purge.
        with self.assertRaises(PermissionError):
            mcp_tools.call_tool(
                self.store, self.user_id, "delete_source_memories",
                {"source": "chatgpt"}, token_scopes=["read"],
            )
        # ...and the read-only rejection deletes nothing.
        self.assertIsNotNone(
            self._bucket(self.store.source_memory_stats(self.user_id), "chatgpt")
        )

        # A token scoped for write+destructive purges the source.
        purged = mcp_tools.call_tool(
            self.store, self.user_id, "delete_source_memories",
            {"source": "chatgpt"}, token_scopes=["read", "write", "destructive"],
        )
        self.assertTrue(purged["deleted"])
        self.assertEqual(purged["memory_count"], 2)
        self.assertIsNone(self._bucket(self.store.source_memory_stats(self.user_id), "chatgpt"))

    def test_purge_survives_a_vault_rebuild(self) -> None:
        """A purge must be DURABLE: rebuilding the index from the vault must NOT resurrect the
        purged data. This pins the tombstone-writing path (purge deletes captures via the shared
        _delete_capture_in_conn primitive, which drops the vault files AND writes tombstones) —
        without it, rebuild_index_from_vault would re-ingest the leftover capture JSON."""
        self._seed_two_sources()
        self.store.purge_source_memories(self.user_id, "chatgpt")
        # The advertised "delete the DB and it comes back from your vault" rebuild.
        self.store.rebuild_index_from_vault(self.user_id)
        stats = self.store.source_memory_stats(self.user_id)
        self.assertIsNone(self._bucket(stats, "chatgpt"), "purged source resurrected by vault rebuild")
        self.assertIsNotNone(self._bucket(stats, "gmail"), "untouched source lost in rebuild")

    def test_case_variant_buckets_fold_and_purge_exactly_what_is_shown(self) -> None:
        """stats groups case-insensitively and purge matches case-insensitively, so the count the
        UI shows for a bucket is EXACTLY what a purge of that bucket removes — never more. Guards
        the over-deletion bug where 'iCloud'/'ICLOUD' showed as two buckets but one purge wiped both."""
        self._save("Note one about cats.", source="iCloud")
        self._save("Note two about dogs.", source="ICLOUD")
        self._save("Note three about birds.", source="icloud")
        stats = self.store.source_memory_stats(self.user_id)
        icloud_buckets = [b for b in stats if (b["source"] or "").lower() == "icloud"]
        self.assertEqual(len(icloud_buckets), 1, "case variants must fold into a single bucket")
        shown = icloud_buckets[0]
        self.assertEqual(shown["count"], 3)
        result = self.store.purge_source_memories(self.user_id, shown["source"])
        self.assertEqual(result["memory_count"], shown["count"], "purged count != displayed count")


if __name__ == "__main__":
    unittest.main()
