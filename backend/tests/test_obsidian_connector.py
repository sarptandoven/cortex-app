from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.connectors.obsidian import (
    MAX_NOTE_BYTES,
    parse_note,
    scan_vault,
    stable_block_external_id,
    stable_external_id,
    stable_section_external_id,
)
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
        self.assertEqual(record["external_id"], stable_section_external_id(self.vault, note, "project-atlas"))
        self.assertTrue(record["source_url"].startswith("file://"))
        self.assertEqual(record["title"], "Project Atlas")
        self.assertIn("Dana", record["content"])
        self.assertIn("Atlas should use source-backed retrieval", record["content"])
        for leaked in ("[[", "]]", "dataview", "TABLE file.mtime", "Template block", "#cortex"):
            self.assertNotIn(leaked, record["content"])
        self.assertEqual(record["metadata"]["tags"], ["cortex", "people/dana"])
        self.assertEqual(record["metadata"]["record_scope"], "section")
        self.assertEqual(record["metadata"]["note_external_id"], stable_external_id(self.vault, note))
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

    def test_empty_vault_does_not_count_as_synced_source(self) -> None:
        empty = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")

        self.assertEqual(empty["status"], "empty")
        self.assertEqual(empty["saved"], 0)
        self.assertEqual(empty["received"], 0)
        self.assertEqual(empty["source_account"]["status"], "empty")
        self.assertEqual(empty["source_account"]["auth_state"], "needs-content")
        self.assertEqual(empty["scan"]["records_found"], 0)
        self.assertEqual(empty["scan"]["records_returned"], 0)

        readiness = self.store.source_readiness_report(self.user_id)
        obsidian = next(item for item in readiness["sources"] if item["source"] == "obsidian")
        self.assertEqual(obsidian["status"], "empty")
        self.assertEqual(obsidian["accounts"], 1)
        self.assertEqual(obsidian["captures"], 0)
        self.assertEqual(obsidian["active_memories"], 0)
        self.assertEqual(readiness["summary"]["synced"], 0)
        self.assertEqual(readiness["summary"]["sources_with_data"], 0)
        self.assertEqual(readiness["summary"]["empty"], 1)
        self.assertIn("No Markdown notes", obsidian["next_action"])

    def test_vault_with_no_memory_ready_notes_does_not_count_as_synced_source(self) -> None:
        self.write_note("Empty.md", "")

        empty = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")

        self.assertEqual(empty["status"], "empty")
        self.assertEqual(empty["source_account"]["status"], "empty")
        self.assertEqual(empty["scan"]["files_seen"], 1)
        self.assertEqual(empty["scan"]["records_found"], 0)
        self.assertEqual(empty["scan"]["records_returned"], 0)

        readiness = self.store.source_readiness_report(self.user_id)
        obsidian = next(item for item in readiness["sources"] if item["source"] == "obsidian")
        self.assertEqual(obsidian["status"], "empty")
        self.assertEqual(obsidian["active_memories"], 0)
        self.assertEqual(readiness["summary"]["synced"], 0)
        self.assertEqual(readiness["summary"]["sources_with_data"], 0)

    def test_oversized_note_reports_actionable_scan_error(self) -> None:
        self.write_note("Large/Oversized.md", "A" * (MAX_NOTE_BYTES + 1))

        scan = scan_vault(self.vault, max_records=20)

        self.assertEqual(scan.files_seen, 1)
        self.assertEqual(scan.records_found, 0)
        self.assertEqual(scan.records_returned, 0)
        self.assertEqual(scan.skipped, 1)
        self.assertEqual(len(scan.errors), 1)
        self.assertEqual(scan.errors[0]["path"], "Large/Oversized.md")
        self.assertEqual(scan.errors[0]["reason"], "oversized_note")
        self.assertEqual(scan.errors[0]["max_bytes"], MAX_NOTE_BYTES)
        self.assertIn("larger than", scan.errors[0]["error"])

        synced = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")
        self.assertEqual(synced["status"], "partial")
        self.assertEqual(synced["skipped"], 1)
        self.assertEqual(synced["failed"], 1)
        self.assertEqual(synced["errors"][0]["reason"], "oversized_note")

        readiness = self.store.source_readiness_report(self.user_id)
        obsidian = next(item for item in readiness["sources"] if item["source"] == "obsidian")
        self.assertEqual(obsidian["status"], "needs_attention")
        self.assertIn("larger than", " ".join(obsidian["warnings"]))

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
        self.assertEqual(synced["saved"], 3)
        for record in synced["records"]:
            self.assertTrue(self.store.approve_capture(self.user_id, record["capture_id"]))

        decision_hits = self.store.search(self.user_id, "late explicit decisions searchable", limit=5)
        procedure_hits = self.store.search(self.user_id, "prioritize explicit procedures during extraction", limit=5)

        self.assertTrue(any(hit["kind"] == "decision" and "late explicit decisions" in hit["content"] for hit in decision_hits))
        self.assertTrue(any(hit["kind"] == "procedure" and "prioritize explicit procedures" in hit["content"] for hit in procedure_hits))

    def test_scan_vault_splits_multi_heading_note_into_stable_section_records(self) -> None:
        note = self.write_note(
            "Projects/Sections.md",
            """# Decision
Decision: Cortex should sync Obsidian headings as stable memory sections.

## Procedure
Procedure: When an Obsidian heading changes, only that section should update.
""",
        )

        scan = scan_vault(self.vault, max_records=20)

        self.assertEqual(scan.records_found, 1)
        self.assertEqual(scan.records_returned, 2)
        records = [record.to_source_account_record() for record in scan.records]
        self.assertEqual(records[0]["external_id"], stable_section_external_id(self.vault, note, "decision"))
        self.assertEqual(records[1]["external_id"], stable_section_external_id(self.vault, note, "procedure"))
        self.assertEqual(records[0]["metadata"]["record_scope"], "section")
        self.assertEqual(records[0]["metadata"]["line_start"], 1)
        self.assertEqual(records[1]["metadata"]["line_start"], 4)
        self.assertEqual(records[1]["metadata"]["section_title"], "Procedure")
        self.assertIn("only that section should update", records[1]["content"])

    def test_sync_vault_updates_only_changed_section(self) -> None:
        note = self.write_note(
            "Projects/Section Updates.md",
            """# Decision
Decision: Cortex should keep stable section records for Obsidian sync.

## Procedure
Procedure: Cortex should resync only changed Obsidian sections.
""",
        )

        first = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")
        self.assertEqual(first["saved"], 2)
        by_external_id = {record["title"].rsplit(" / ", 1)[-1]: record["capture_id"] for record in first["records"]}

        note.write_text(
            """# Decision
Decision: Cortex should keep stable section records for Obsidian sync.

## Procedure
Procedure: Cortex should update one changed Obsidian section without rewriting the rest.
""",
            encoding="utf-8",
        )
        changed = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")

        self.assertEqual(changed["saved"], 1)
        self.assertEqual(changed["skipped"], 1)
        statuses = {record["title"].rsplit(" / ", 1)[-1]: record for record in changed["records"]}
        self.assertEqual(statuses["Decision"]["status"], "duplicate")
        self.assertEqual(statuses["Decision"]["capture_id"], by_external_id["Decision"])
        self.assertEqual(statuses["Procedure"]["status"], "updated")
        self.assertEqual(statuses["Procedure"]["capture_id"], by_external_id["Procedure"])

    def test_sync_vault_archives_removed_section(self) -> None:
        note = self.write_note(
            "Projects/Removed Section.md",
            """# Keep
Decision: Cortex should keep this Obsidian section searchable.

## Remove
Decision: Cortex should archive the zinnia-retire marker when an Obsidian section is removed.
""",
        )
        first = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")
        self.assertEqual(first["saved"], 2)
        for record in first["records"]:
            self.assertTrue(self.store.approve_capture(self.user_id, record["capture_id"]))
        self.assertTrue(self.store.search(self.user_id, "zinnia-retire marker", limit=5))

        note.write_text(
            """# Keep
Decision: Cortex should keep this Obsidian section searchable.
""",
            encoding="utf-8",
        )
        second = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")

        self.assertEqual(second["saved"], 0)
        self.assertEqual(second["skipped"], 1)
        self.assertEqual(second["archived_missing"], 1)
        self.assertTrue(self.store.search(self.user_id, "keep this Obsidian section searchable", limit=5))
        self.assertEqual(self.store.search(self.user_id, "zinnia-retire marker", limit=5), [])
        removed_capture_id = next(record["capture_id"] for record in first["records"] if record["title"].endswith("Remove"))
        self.assertFalse(self.store.approve_capture(self.user_id, removed_capture_id))

    def test_sync_vault_archives_removed_section_when_multiple_sections_remain(self) -> None:
        note = self.write_note(
            "Projects/Removed Middle Section.md",
            """# Keep One
Decision: Cortex should keep the first Obsidian section searchable.

## Remove
Decision: Cortex should archive the azalea-middle marker when one section is removed.

## Keep Two
Decision: Cortex should keep the second Obsidian section searchable.
""",
        )
        first = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")
        self.assertEqual(first["saved"], 3)
        self.assertEqual(first["scan"]["records_found"], 1)
        self.assertEqual(first["scan"]["records_returned"], 3)
        for record in first["records"]:
            self.assertTrue(self.store.approve_capture(self.user_id, record["capture_id"]))
        self.assertTrue(self.store.search(self.user_id, "azalea-middle marker", limit=5))

        note.write_text(
            """# Keep One
Decision: Cortex should keep the first Obsidian section searchable.

## Keep Two
Decision: Cortex should keep the second Obsidian section searchable.
""",
            encoding="utf-8",
        )
        second = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")

        self.assertEqual(second["saved"], 0)
        self.assertEqual(second["skipped"], 2)
        self.assertEqual(second["archived_missing"], 1)
        self.assertEqual(second["scan"]["records_found"], 1)
        self.assertEqual(second["scan"]["records_returned"], 2)
        self.assertTrue(self.store.search(self.user_id, "first Obsidian section searchable", limit=5))
        self.assertTrue(self.store.search(self.user_id, "second Obsidian section searchable", limit=5))
        self.assertEqual(self.store.search(self.user_id, "azalea-middle marker", limit=5), [])

    def test_sync_vault_preserves_approved_memory_when_note_moves(self) -> None:
        note = self.write_note(
            "Projects/Move Plan.md",
            "Decision: Cortex should keep approved memory searchable when notes move folders.",
        )
        first = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")
        self.assertEqual(first["saved"], 1)
        capture_id = first["records"][0]["capture_id"]
        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))
        self.assertTrue(self.store.search(self.user_id, "notes move folders", limit=5))

        moved = self.vault / "Archive/Move Plan.md"
        moved.parent.mkdir(parents=True, exist_ok=True)
        note.rename(moved)
        second = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")

        self.assertEqual(second["saved"], 1)
        self.assertEqual(second["archived_missing"], 0)
        self.assertEqual(second["records"][0]["status"], "updated")
        self.assertEqual(second["records"][0]["capture_id"], capture_id)
        found = self.store.search(self.user_id, "notes move folders", limit=5)
        self.assertTrue(found)
        self.assertTrue(found[0]["source_url"].startswith(moved.resolve().as_uri()))
        with connect(self.db_path) as conn:
            captures = conn.execute(
                """
                SELECT id, external_id, source_url, review_status
                FROM captures
                WHERE user_id = ?
                  AND source = 'obsidian'
                """,
                (self.user_id,),
            ).fetchall()
        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0]["id"], capture_id)
        self.assertEqual(captures[0]["external_id"], "Archive/Move Plan.md")
        self.assertTrue(captures[0]["source_url"].startswith(moved.resolve().as_uri()))
        self.assertEqual(captures[0]["review_status"], "approved")

    def test_scan_vault_uses_explicit_obsidian_block_refs_as_stable_records(self) -> None:
        note = self.write_note(
            "Projects/Blocks.md",
            """# Decisions
Decision: Cortex should preserve explicit Obsidian block anchors for important memory. ^decision-anchor

## Procedure
Procedure: Cortex should strip block ids from remembered text. ^procedure-anchor
""",
        )

        scan = scan_vault(self.vault, max_records=20)

        self.assertEqual(scan.records_found, 1)
        self.assertEqual(scan.records_returned, 2)
        records = [record.to_source_account_record() for record in scan.records]
        self.assertEqual(records[0]["external_id"], stable_block_external_id(self.vault, note, "decision-anchor"))
        self.assertEqual(records[1]["external_id"], stable_block_external_id(self.vault, note, "procedure-anchor"))
        self.assertEqual(records[0]["metadata"]["record_scope"], "block")
        self.assertEqual(records[0]["metadata"]["line_start"], 2)
        self.assertEqual(records[0]["metadata"]["block_id"], "decision-anchor")
        self.assertIn("preserve explicit Obsidian block anchors", records[0]["content"])
        self.assertIn("strip block ids from remembered text", records[1]["content"])
        self.assertNotIn("^decision-anchor", records[0]["content"])
        self.assertNotIn("^procedure-anchor", records[1]["content"])

    def test_sync_vault_updates_changed_explicit_block_ref(self) -> None:
        note = self.write_note(
            "Projects/Block Updates.md",
            "Decision: Cortex should keep explicit block refs stable across edits. ^stable-block",
        )

        first = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")
        self.assertEqual(first["saved"], 1)
        first_capture_id = first["records"][0]["capture_id"]
        self.assertEqual(first["records"][0]["source_url"], note.resolve().as_uri())

        note.write_text(
            "Decision: Cortex should update explicit block refs without changing capture identity. ^stable-block",
            encoding="utf-8",
        )
        changed = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")

        self.assertEqual(changed["saved"], 1)
        self.assertEqual(changed["records"][0]["status"], "updated")
        self.assertEqual(changed["records"][0]["capture_id"], first_capture_id)
        self.assertEqual(changed["records"][0]["title"], "Block Updates / stable-block")

    def test_sync_vault_archives_removed_explicit_block_ref(self) -> None:
        note = self.write_note(
            "Projects/Block Removed.md",
            """Decision: Cortex should keep this regular note memory available.

Decision: Cortex should archive the orchid-block marker when an explicit block disappears. ^orchid-block
""",
        )
        first = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")
        self.assertEqual(first["saved"], 2)
        for record in first["records"]:
            self.assertTrue(self.store.approve_capture(self.user_id, record["capture_id"]))
        self.assertTrue(self.store.search(self.user_id, "orchid-block marker", limit=5))

        note.write_text(
            "Decision: Cortex should keep this regular note memory available.",
            encoding="utf-8",
        )
        second = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")

        self.assertEqual(second["saved"], 0)
        self.assertEqual(second["skipped"], 1)
        self.assertEqual(second["archived_missing"], 1)
        self.assertTrue(self.store.search(self.user_id, "regular note memory available", limit=5))
        self.assertEqual(self.store.search(self.user_id, "orchid-block marker", limit=5), [])

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
        self.assertIn("offset=2;", scan.cursor_value)

        second = scan_vault(self.vault, max_records=2, cursor_value=scan.cursor_value)
        self.assertEqual(second.records_found, 5)
        self.assertEqual(second.records_returned, 2)
        self.assertTrue(second.truncated)
        self.assertIn("offset=4;", second.cursor_value)
        self.assertTrue(
            {record.external_id for record in scan.records}.isdisjoint(
                {record.external_id for record in second.records}
            )
        )

        third = scan_vault(self.vault, max_records=2, cursor_value=second.cursor_value)
        self.assertEqual(third.records_found, 5)
        self.assertEqual(third.records_returned, 1)
        self.assertTrue(third.truncated)
        self.assertIn("offset=0;", third.cursor_value)

    def test_sync_vault_backfills_truncated_scans_across_runs(self) -> None:
        for index in range(5):
            self.write_note(
                f"Backfill/Note {index}.md",
                f"Decision: Cortex Obsidian backfill marker {index} should sync across repeated limited scans.",
            )

        first = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync", max_records=2)
        for capture_id in first["capture_ids"]:
            self.assertTrue(self.store.approve_capture(self.user_id, capture_id))
        first_readiness = self.store.source_readiness_report(self.user_id)
        first_obsidian = next(item for item in first_readiness["sources"] if item["source"] == "obsidian")
        self.assertEqual(first_obsidian["status"], "syncing")
        self.assertIn("still scanning", first_obsidian["next_action"])
        second = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync", max_records=2)
        third = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync", max_records=2)

        self.assertEqual(first["saved"], 2)
        self.assertEqual(second["saved"], 2)
        self.assertEqual(third["saved"], 1)
        self.assertTrue(first["scan"]["truncated"])
        self.assertTrue(second["scan"]["truncated"])
        self.assertTrue(third["scan"]["truncated"])
        self.assertIn("offset=2;", first["cursor"]["cursor_value"])
        self.assertIn("offset=4;", second["cursor"]["cursor_value"])
        self.assertIn("offset=0;", third["cursor"]["cursor_value"])
        with connect(self.db_path) as conn:
            capture_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM captures
                WHERE user_id = ?
                  AND source = 'obsidian'
                """,
                (self.user_id,),
            ).fetchone()[0]
        self.assertEqual(capture_count, 5)

    def test_sync_vault_batches_large_complete_scan_and_archives_removed_notes(self) -> None:
        for index in range(502):
            self.write_note(
                f"Large/Note {index:03d}.md",
                f"Decision: Cortex large vault marker {index:03d} should sync without a single oversized backend batch.",
            )

        first = self.store.sync_obsidian_vault(
            self.user_id,
            vault_path=str(self.vault),
            processing="async",
            max_records=5000,
        )

        self.assertEqual(first["status"], "complete")
        self.assertEqual(first["received"], 502)
        self.assertEqual(first["queued"], 502)
        self.assertEqual(first["failed"], 0)
        self.assertEqual(first["archived_missing"], 0)
        self.assertEqual(first["scan"]["records_returned"], 502)
        self.assertFalse(first["scan"]["truncated"])

        removed = self.vault / "Large/Note 501.md"
        removed.unlink()
        second = self.store.sync_obsidian_vault(
            self.user_id,
            vault_path=str(self.vault),
            processing="async",
            max_records=5000,
        )

        self.assertEqual(second["status"], "complete")
        self.assertEqual(second["received"], 501)
        self.assertEqual(second["queued"], 0)
        self.assertEqual(second["saved"], 0)
        self.assertEqual(second["skipped"], 501)
        self.assertTrue(all(record["status"] == "duplicate" for record in second["records"]))
        self.assertEqual(second["archived_missing"], 1)
        self.assertEqual(second["failed"], 0)
        self.assertFalse(second["scan"]["truncated"])
        with connect(self.db_path) as conn:
            active_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM captures
                WHERE user_id = ?
                  AND source = 'obsidian'
                  AND review_status != 'archived'
                """,
                (self.user_id,),
            ).fetchone()[0]
        self.assertEqual(active_count, 501)

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
