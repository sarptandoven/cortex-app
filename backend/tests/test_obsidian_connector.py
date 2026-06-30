from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.connectors.obsidian import parse_note, scan_vault, stable_external_id
from backend.app.database import connect, init_db
from backend.app.storage import CortexStore


class ObsidianConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "Demo Vault"
        self.vault.mkdir()
        self.db_path = self.root / "cortex.sqlite"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path)
        self.user_id = "obsidian-test"
        self.store.update_settings(self.user_id, {"allow_pending_in_context": True})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_note(self, relative_path: str, text: str) -> Path:
        path = self.vault / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_scan_vault_cleans_obsidian_markdown_and_keeps_file_citations(self) -> None:
        note_text = """---
title: Project Atlas
tags: #cortex #people/dana
---
# Project Atlas

> [!NOTE] Template block
> This callout should not become the note.

```dataview
TABLE file.mtime
FROM #cortex
```

- [ ] Follow up with [[People/Dana|Dana]] about [[Project Atlas|Atlas]].
I decided [[Project Atlas|Atlas]] should use [source-backed retrieval](https://example.com).
I prefer #cortex notes that preserve citations.
"""
        note = self.write_note(
            "Projects/Atlas.md",
            note_text,
        )
        self.write_note(".obsidian/Internal.md", "Internal configuration should be skipped.")

        parsed = parse_note(note_text, fallback_title="Atlas.md")
        self.assertEqual(parsed.title, "Project Atlas")
        self.assertEqual(parsed.frontmatter["title"], "Project Atlas")
        self.assertEqual(parsed.tags, ["cortex", "people/dana"])
        self.assertTrue(any(link["target"] == "People/Dana" and link["display"] == "Dana" for link in parsed.wikilinks))
        self.assertEqual(parsed.callouts, [{"type": "note", "title": "Template block"}])
        self.assertEqual(parsed.removed_blocks["code_blocks"], 1)
        self.assertEqual(parsed.removed_blocks["code_languages"], ["dataview"])

        scan = scan_vault(self.vault, max_records=20)

        self.assertEqual(scan.records_found, 1)
        self.assertEqual(scan.records_returned, 1)
        self.assertFalse(scan.truncated)
        self.assertEqual(scan.to_summary()["records_found"], 1)
        self.assertEqual(scan.to_summary()["records_returned"], 1)
        record = scan.records[0].to_source_account_record()
        self.assertEqual(record["external_id"], "Projects/Atlas.md")
        self.assertEqual(record["external_id"], stable_external_id(self.vault, note))
        self.assertTrue(record["source_url"].startswith("file://"))
        self.assertEqual(record["title"], "Project Atlas")
        self.assertIn("Dana", record["content"])
        self.assertIn("Atlas should use source-backed retrieval", record["content"])
        for leaked in ("[[", "]]", "dataview", "TABLE file.mtime", "Template block", "#cortex"):
            self.assertNotIn(leaked, record["content"])
        self.assertEqual(record["metadata"]["tags"], ["cortex", "people/dana"])
        self.assertTrue(any(link["target"] == "Project Atlas" and link["display"] == "Atlas" for link in record["metadata"]["wikilinks"]))
        self.assertEqual(record["metadata"]["callouts"], [{"type": "note", "title": "Template block"}])
        self.assertEqual(record["metadata"]["removed_blocks"]["code_languages"], ["dataview"])

    def test_sync_vault_skips_unchanged_note_and_updates_changed_note(self) -> None:
        note = self.write_note(
            "Decisions/Memory.md",
            "I decided Cortex should use source-backed retrieval for MCP memory.",
        )

        first = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")
        self.assertEqual(first["status"], "complete")
        self.assertEqual(first["saved"], 1)
        self.assertEqual(first["source"], "obsidian")
        self.assertEqual(first["source_account"]["connection_type"], "local-folder")
        self.assertTrue(first["records"][0]["source_url"].startswith(note.resolve().as_uri()))
        first_capture_id = first["records"][0]["capture_id"]

        unchanged = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")
        self.assertEqual(unchanged["saved"], 0)
        self.assertEqual(unchanged["skipped"], 1)
        self.assertEqual(unchanged["records"][0]["capture_id"], first_capture_id)

        note.write_text(
            "I decided Cortex should use hybrid retrieval for approved memory citations.",
            encoding="utf-8",
        )
        changed = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")
        self.assertEqual(changed["saved"], 1)
        self.assertEqual(changed["records"][0]["status"], "updated")
        self.assertEqual(changed["records"][0]["capture_id"], first_capture_id)
        self.assertEqual(self.store.search(self.user_id, "source-backed retrieval MCP memory", limit=5), [])

        with connect(self.db_path) as conn:
            captures = conn.execute(
                """
                SELECT id, external_id, source_url, raw_text
                FROM captures
                WHERE user_id = ? AND source = ?
                ORDER BY captured_at DESC
                """,
                (self.user_id, "obsidian"),
            ).fetchall()

        self.assertTrue(captures)
        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0]["id"], first_capture_id)
        self.assertTrue(all(row["external_id"] == "Decisions/Memory.md" for row in captures))
        self.assertTrue(all(row["source_url"].startswith(note.resolve().as_uri()) for row in captures))
        self.assertIn("hybrid retrieval", captures[0]["raw_text"])
        self.assertNotIn("source-backed retrieval", captures[0]["raw_text"])
        self.assertEqual(self.store.search(self.user_id, "hybrid retrieval approved memory citations", limit=5), [])
        self.assertTrue(self.store.approve_capture(self.user_id, first_capture_id))
        found = self.store.search(self.user_id, "hybrid retrieval approved memory citations", limit=5)
        self.assertTrue(found)
        self.assertEqual(found[0]["source"], "obsidian")
        self.assertEqual(found[0]["provenance"]["record_metadata"]["relative_path"], "Decisions/Memory.md")
        self.assertEqual(found[0]["provenance"]["record_metadata"]["connector"], "obsidian")
        self.assertIn("Memory", found[0]["topics"])

    def test_long_note_keeps_late_explicit_decision_and_procedure(self) -> None:
        low_value_lines = "\n".join(
            f"Project Atlas background note {index} documents routine context without a durable decision."
            for index in range(32)
        )
        self.write_note(
            "Projects/Long Atlas.md",
            f"""# Long Atlas

{low_value_lines}

## Decision
Decision: Project Atlas long notes must keep late explicit decisions searchable after review.

## Procedure
Procedure: Before using long Obsidian notes, Cortex should prioritize explicit procedures during extraction.
""",
        )

        synced = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")
        self.assertEqual(synced["saved"], 1)
        capture_id = synced["records"][0]["capture_id"]
        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))

        decision_hits = self.store.search(self.user_id, "late explicit decisions searchable", limit=5)
        procedure_hits = self.store.search(self.user_id, "prioritize explicit procedures during extraction", limit=5)

        self.assertTrue(any(hit["kind"] == "decision" and "late explicit decisions" in hit["content"] for hit in decision_hits))
        self.assertTrue(any(hit["kind"] == "procedure" and "prioritize explicit procedures" in hit["content"] for hit in procedure_hits))

    def test_scan_vault_reports_partial_coverage_when_limited(self) -> None:
        for index in range(5):
            self.write_note(
                f"Daily/Note {index}.md",
                f"Decision: Cortex partial coverage marker {index} should be visible in Obsidian scan metadata.",
            )

        scan = scan_vault(self.vault, max_records=2)

        self.assertEqual(scan.records_found, 5)
        self.assertEqual(scan.records_returned, 2)
        self.assertTrue(scan.truncated)
        self.assertEqual(scan.to_summary()["records_found"], 5)
        self.assertEqual(scan.to_summary()["records_returned"], 2)
        self.assertTrue(scan.to_summary()["truncated"])

    def test_obsidian_review_required_policy_overrides_global_auto_approve(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": False})
        self.write_note(
            "Review/Always Pending.md",
            "Decision: Obsidian connector policy should keep first-100 vault memory review-first.",
        )

        synced = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")

        self.assertEqual(synced["status"], "complete")
        self.assertEqual(synced["saved"], 1)
        capture_id = synced["records"][0]["capture_id"]
        inbox = self.store.inbox(self.user_id, limit=10)
        self.assertEqual([item["id"] for item in inbox], [capture_id])
        self.assertEqual(self.store.search(self.user_id, "review-first vault memory", limit=5), [])


if __name__ == "__main__":
    unittest.main()
