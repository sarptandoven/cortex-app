from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from backend.app.database import init_db
from backend.app.source_ingest import analyze_sources, import_source_records, import_source_records_page
from backend.app.storage import CortexStore


class SourceIngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _store(self, db_path: Path, vault_path: Path) -> CortexStore:
        store = CortexStore(db_path, vault_path)
        store.update_settings("test-user", {"allow_pending_in_context": True})
        return store

    def _write_notes(self, count: int) -> Path:
        notes = self.root / "notes"
        notes.mkdir()
        for index in range(count):
            (notes / f"note-{index}.md").write_text(
                f"Note {index}: durable memory fixture number {index}.", encoding="utf-8"
            )
        return notes

    def test_import_source_records_page_paginates_without_skips_or_dupes(self) -> None:
        notes = self._write_notes(5)

        page0 = import_source_records_page([str(notes)], max_records=2, offset=0)
        self.assertEqual(page0["total"], 5)
        self.assertEqual(page0["returned"], 2)
        self.assertTrue(page0["has_more"])
        self.assertEqual(page0["next_offset"], 2)

        page1 = import_source_records_page([str(notes)], max_records=2, offset=page0["next_offset"])
        self.assertEqual(page1["returned"], 2)
        self.assertTrue(page1["has_more"])
        self.assertEqual(page1["next_offset"], 4)

        page2 = import_source_records_page([str(notes)], max_records=2, offset=page1["next_offset"])
        self.assertEqual(page2["returned"], 1)
        self.assertFalse(page2["has_more"])
        self.assertIsNone(page2["next_offset"])

        # Every record appears exactly once across the pages: no skips, no dupes.
        urls = [record.source_url for record in (*page0["records"], *page1["records"], *page2["records"])]
        self.assertEqual(len(urls), 5)
        self.assertEqual(len(set(urls)), 5)

    def test_import_sources_paginates_and_dedupes_large_exports(self) -> None:
        notes = self._write_notes(5)
        db_path = self.root / "index.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "vault")

        def run(offset: int) -> dict:
            return store.import_sources(
                user_id="test-user", paths=[str(notes)], processing="async", max_records=2, offset=offset
            )

        page0 = run(0)
        self.assertEqual(page0["records_available"], 5)
        self.assertEqual(page0["queued"], 2)
        self.assertTrue(page0["has_more"])
        self.assertEqual(page0["next_offset"], 2)

        page1 = run(page0["next_offset"])
        self.assertEqual(page1["queued"], 2)
        self.assertTrue(page1["has_more"])

        page2 = run(page1["next_offset"])
        self.assertEqual(page2["queued"], 1)
        self.assertFalse(page2["has_more"])

        # After the full export is imported, re-importing any window is fully
        # deduplicated by content hash (nothing new queued).
        again = run(0)
        self.assertEqual(again["queued"], 0)
        self.assertEqual(again["skipped"], 2)

    def test_detects_common_service_exports(self) -> None:
        self._write_chatgpt_export()
        self._write_claude_export()
        self._write_slack_export()
        self._write_discord_export()
        self._write_telegram_export()
        self._write_google_keep_export()
        self._write_notion_export()
        self._write_email_export()
        self._write_gmail_mbox_zip()
        self._write_consumer_ai_transcript_exports()
        self._write_whatsapp_export()
        self._write_browser_bookmarks_export()
        self._write_calendar_export()
        self._write_contacts_export()
        self._write_twitter_export()
        self._write_linkedin_export()
        self._write_cloud_and_work_exports()
        self._write_collaboration_exports()

        records = import_source_records([str(self.root)], max_records=50)
        sources = {record.source for record in records}

        self.assertIn("chatgpt", sources)
        self.assertIn("claude", sources)
        self.assertIn("gemini", sources)
        self.assertIn("perplexity", sources)
        self.assertIn("copilot", sources)
        self.assertIn("grok", sources)
        self.assertIn("poe", sources)
        self.assertIn("notebooklm", sources)
        self.assertIn("slack", sources)
        self.assertIn("discord", sources)
        self.assertIn("telegram", sources)
        self.assertIn("google-keep", sources)
        self.assertIn("notion", sources)
        self.assertIn("email", sources)
        self.assertIn("whatsapp", sources)
        self.assertIn("browser-bookmarks", sources)
        self.assertIn("calendar", sources)
        self.assertIn("contacts", sources)
        self.assertIn("twitter-x", sources)
        self.assertIn("linkedin", sources)
        self.assertIn("google-chat", sources)
        self.assertIn("teams", sources)
        self.assertIn("zoom", sources)
        self.assertIn("cloud-docs", sources)
        self.assertIn("apple-notes", sources)
        self.assertIn("jira", sources)
        self.assertEqual([(record.source, record.title) for record in records if not record.source_url], [])
        self.assertTrue(any("Project Atlas" in record.content for record in records))
        self.assertTrue(any("concise technical answers" in record.content for record in records))
        self.assertTrue(any("Project GeminiTranscript" in record.content for record in records))
        self.assertTrue(any("Project PerplexityTranscript" in record.content for record in records))
        self.assertTrue(any("Project CopilotTranscript" in record.content for record in records))
        self.assertTrue(any("Project GrokTranscript" in record.content for record in records))
        self.assertTrue(any("Project PoeTranscript" in record.content for record in records))
        self.assertTrue(any("Project NotebooklmTranscript" in record.content for record in records))
        self.assertTrue(any("Project Kestrel launch" in record.content for record in records))
        self.assertTrue(any("Ada Lovelace" in record.content for record in records))
        self.assertTrue(any("browser research" in record.content for record in records))
        self.assertTrue(any("Google Chat launch plan" in record.content for record in records))
        self.assertTrue(any("Teams migration note" in record.content for record in records))
        self.assertTrue(any("Zoom transcript memory" in record.content for record in records))

        source_urls: dict[str, list[str]] = {}
        for record in records:
            source_urls.setdefault(record.source, []).append(record.source_url or "")
        self.assertTrue(any("service=gemini" in url and "conversation=Project%20Gemini%20transcript" in url for url in source_urls["gemini"]))
        self.assertTrue(any("service=perplexity" in url and "conversation=Project%20Perplexity%20transcript" in url for url in source_urls["perplexity"]))
        self.assertTrue(any("service=copilot" in url and "conversation=Project%20Microsoft%20Copilot%20transcript" in url for url in source_urls["copilot"]))
        self.assertTrue(any("service=grok" in url and "conversation=Project%20Grok%20transcript" in url for url in source_urls["grok"]))
        self.assertTrue(any("service=poe" in url and "conversation=Project%20Poe%20transcript" in url for url in source_urls["poe"]))
        self.assertTrue(any("service=notebooklm" in url and "conversation=Project%20NotebookLM%20transcript" in url for url in source_urls["notebooklm"]))
        self.assertTrue(any("service=discord" in url and "channel=c123" in url for url in source_urls["discord"]))
        self.assertTrue(any("service=telegram" in url and "chat=Project%20Telegram" in url for url in source_urls["telegram"]))
        self.assertTrue(any("service=google-keep" in url and "note=Preference" in url for url in source_urls["google-keep"]))
        self.assertTrue(any("service=google-chat" in url and "conversation=Project%20Space" in url for url in source_urls["google-chat"]))
        self.assertTrue(any("service=teams" in url and "conversation=General" in url for url in source_urls["teams"]))
        self.assertTrue(any("service=zoom" in url and "transcript=Project%20Sync" in url for url in source_urls["zoom"]))
        self.assertTrue(any("service=twitter-x" in url and "archive=tweets" in url for url in source_urls["twitter-x"]))
        self.assertTrue(any("service=twitter-x" in url and "archive=direct-messages" in url for url in source_urls["twitter-x"]))
        self.assertTrue(any("service=linkedin" in url and "export=messages" in url for url in source_urls["linkedin"]))
        self.assertTrue(any("service=linkedin" in url and "export=connections" in url for url in source_urls["linkedin"]))

        analysis = analyze_sources([str(self.root)])
        self.assertGreaterEqual(analysis["records_found"], 16)
        self.assertTrue(analysis["supported_sources"])
        self.assertTrue(all(sample["source_url"] for sample in analysis["sample"]))

    def test_branded_exports_emit_canonical_import_source_ids(self) -> None:
        self._write_gmail_mbox_zip()
        gmail_records = import_source_records([str(self.root / "gmail")], source_hint="gmail", max_records=10)
        self.assertTrue(gmail_records)
        self.assertEqual({record.source for record in gmail_records}, {"email"})
        self.assertTrue(all("service=email" in (record.source_url or "") for record in gmail_records))

        drive = self.root / "Google Drive" / "Docs"
        drive.mkdir(parents=True)
        (drive / "Roadmap.md").write_text("Google Drive export should import as cloud docs.", encoding="utf-8")
        local_docs = self.root / "local-docs"
        local_docs.mkdir()
        (local_docs / "Writing.md").write_text("Plain writing sample should import as docs.", encoding="utf-8")
        github = self.root / "GitHub" / "Project Cortex"
        github.mkdir(parents=True)
        (github / "issues.csv").write_text("Title,Body\nMemory UI,GitHub import should be cited.\n", encoding="utf-8")

        records = import_source_records([str(drive), str(local_docs), str(github)], max_records=10)
        by_source = {record.source: record for record in records}
        self.assertIn("cloud-docs", by_source)
        self.assertIn("docs", by_source)
        self.assertIn("github", by_source)
        self.assertIn("service=cloud-docs", by_source["cloud-docs"].source_url or "")
        self.assertIn("provider=google-drive", by_source["cloud-docs"].source_url or "")
        self.assertIn("document=Roadmap", by_source["cloud-docs"].source_url or "")
        self.assertEqual(by_source["docs"].source_url, str(local_docs / "Writing.md"))
        self.assertIn("service=github", by_source["github"].source_url or "")
        self.assertIn("repository=Project%20Cortex", by_source["github"].source_url or "")
        self.assertIn("file=issues.csv", by_source["github"].source_url or "")

    def test_github_json_export_is_formatted_as_rows(self) -> None:
        github = self.root / "GitHub" / "Project Cortex"
        github.mkdir(parents=True)
        payload = [
            {
                "number": 42,
                "title": "Project JsonHub issue",
                "body": "We decided Project JsonHub should preserve issue JSON rows.",
                "state": "open",
                "user": {"login": "ada"},
                "labels": [{"name": "importer"}, {"name": "beta"}],
            },
            {
                "number": 43,
                "title": "Project JsonHub pull request",
                "body": "Project JsonHub pull requests should share row formatting.",
                "state": "closed",
                "pull_request": {"html_url": "https://github.com/example/repo/pull/43"},
            },
        ]
        (github / "issues.json").write_text(json.dumps(payload), encoding="utf-8")

        records = import_source_records([str(github)], max_records=10)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source, "github")
        self.assertIn("--- Rows ---", records[0].content)
        self.assertIn("Row 1\nnumber: 42", records[0].content)
        self.assertIn("body: We decided Project JsonHub should preserve issue JSON rows.", records[0].content)
        self.assertIn("labels: importer; beta", records[0].content)
        self.assertIn("Row 2\nnumber: 43", records[0].content)
        self.assertIn("service=github", records[0].source_url or "")
        self.assertIn("repository=Project%20Cortex", records[0].source_url or "")
        self.assertIn("file=issues.json", records[0].source_url or "")

    def test_jira_json_issues_export_is_formatted_as_rows(self) -> None:
        jira = self.root / "Jira"
        jira.mkdir()
        payload = {
            "issues": [
                {
                    "key": "CX-9",
                    "fields": {
                        "summary": "We decided Project JsonJira should preserve Jira JSON rows.",
                        "description": {
                            "type": "doc",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": "Project JsonJira description should stay retrievable.",
                                        }
                                    ],
                                }
                            ],
                        },
                        "status": {"name": "Done"},
                        "assignee": {"displayName": "Ada Lovelace"},
                    },
                }
            ]
        }
        (jira / "issues.json").write_text(json.dumps(payload), encoding="utf-8")

        records = import_source_records([str(jira)], max_records=10)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source, "jira")
        self.assertIn("Row 1\nkey: CX-9", records[0].content)
        self.assertIn("summary: We decided Project JsonJira should preserve Jira JSON rows.", records[0].content)
        self.assertIn("description: Project JsonJira description should stay retrievable.", records[0].content)
        self.assertIn("status: Done", records[0].content)
        self.assertIn("assignee: Ada Lovelace", records[0].content)
        self.assertIn("service=jira", records[0].source_url or "")
        self.assertIn("file=issues.json", records[0].source_url or "")

    def test_linear_jsonl_export_is_formatted_as_rows(self) -> None:
        linear = self.root / "Linear"
        linear.mkdir()
        rows = [
            {
                "identifier": "COR-101",
                "title": "Project JsonLinear issue",
                "description": "We decided Project JsonLinear should preserve JSONL issue rows.",
                "state": {"name": "In Progress"},
                "labels": [{"name": "memory"}, {"name": "import"}],
            },
            {
                "identifier": "COR-102",
                "title": "Project JsonLinear follow-up",
                "description": "Project JsonLinear follow-up rows should stay citable.",
                "state": {"name": "Todo"},
            },
        ]
        (linear / "issues.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

        records = import_source_records([str(linear)], max_records=10)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source, "linear")
        self.assertIn("Row 1\nidentifier: COR-101", records[0].content)
        self.assertIn("description: We decided Project JsonLinear should preserve JSONL issue rows.", records[0].content)
        self.assertIn("state: In Progress", records[0].content)
        self.assertIn("labels: memory; import", records[0].content)
        self.assertIn("Row 2\nidentifier: COR-102", records[0].content)
        self.assertIn("service=linear", records[0].source_url or "")
        self.assertIn("file=issues.jsonl", records[0].source_url or "")

    def test_store_import_sources_queues_and_processes_records(self) -> None:
        self._write_chatgpt_export()
        db_path = self.root / "index.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "vault")

        result = store.import_sources(
            user_id="test-user",
            paths=[str(self.root / "chatgpt")],
            processing="async",
            max_records=20,
        )

        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["queued"], 1)
        self.assertEqual(result["sources"], [{"source": "chatgpt", "count": 1}])
        self.assertTrue(result["import_id"].startswith("imp_"))

        imports = store.list_imports("test-user")
        self.assertEqual(len(imports), 1)
        self.assertEqual(imports[0]["import_id"], result["import_id"])
        self.assertEqual(imports[0]["records_found"], 1)
        self.assertEqual(imports[0]["queued"], 1)
        self.assertTrue(imports[0]["can_delete"])

        ran = store.run_due_jobs("test-user", limit=10)
        self.assertEqual(ran["processed"], 1)
        results = store.search("test-user", "Project Atlas", limit=5)
        self.assertTrue(results)

        detail = store.get_import("test-user", result["import_id"])
        self.assertIsNotNone(detail)
        self.assertEqual(len(detail["records"]), 1)
        self.assertEqual(len(detail["captures"]), 1)

        deleted = store.delete_import("test-user", result["import_id"])
        self.assertTrue(deleted["deleted"])
        self.assertEqual(deleted["deleted_captures"], 1)
        self.assertFalse(store.search("test-user", "Project Atlas", limit=5))

        deleted_again = store.delete_import("test-user", result["import_id"])
        self.assertFalse(deleted_again["deleted"])
        self.assertEqual(deleted_again["status"], "already_deleted")

    def test_long_chat_exports_are_chunked_before_extraction(self) -> None:
        self._write_long_chatgpt_export()
        records = import_source_records([str(self.root / "chatgpt-long")], max_records=10)

        self.assertGreaterEqual(len(records), 2)
        self.assertTrue(any("chunk=2" in (record.source_url or "") for record in records))
        self.assertTrue(any("Project Longtail" in record.content for record in records[1:]))

        db_path = self.root / "longtail.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "longtail-vault")
        result = store.import_sources(
            user_id="test-user",
            paths=[str(self.root / "chatgpt-long")],
            processing="sync",
            max_records=10,
        )

        self.assertEqual(result["failed"], 0)
        self.assertGreaterEqual(result["saved"], 2)
        self.assertEqual(result["records_found"], len(records))
        hits = store.search("test-user", "Project Longtail late export decisions", limit=5)
        self.assertTrue(any("Project Longtail" in memory["content"] for memory in hits))
        self.assertTrue(any("chunk=2" in (memory["source_url"] or "") for memory in hits))

    def test_jsonl_transcript_skips_corrupt_line_instead_of_dropping_whole_file(self) -> None:
        # A single truncated/corrupt line in a large JSONL export must not discard every
        # valid conversation already parsed. JSONL lines are independent records.
        from backend.app.source_ingest import SourceAsset, _parse_consumer_ai_jsonl_asset

        lines = [
            json.dumps({"role": "user", "text": "First message about Project Aurora."}),
            json.dumps({"role": "assistant", "text": "Acknowledged Project Aurora."}),
            "{ this line is truncated and not valid json",
            json.dumps({"role": "user", "text": "Later message about Project Zenith after the corruption."}),
        ]
        asset = SourceAsset(
            name="chatgpt.jsonl",
            display_path="export/chatgpt.jsonl",
            data="\n".join(lines).encode("utf-8"),
        )
        records = _parse_consumer_ai_jsonl_asset(asset, source="chatgpt", provider="ChatGPT")

        self.assertTrue(records, "a corrupt line silently abandoned the whole JSONL file")
        joined = "\n".join(record.content for record in records)
        # Both the pre-corruption and post-corruption messages survive.
        self.assertIn("Project Aurora", joined)
        self.assertIn("Project Zenith", joined)

    def test_service_metadata_dates_become_memory_dates(self) -> None:
        self._write_dated_service_exports()
        db_path = self.root / "service-dates.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "service-dates-vault")

        result = store.import_sources(
            user_id="test-user",
            paths=[
                str(self.root / "dated-chatgpt"),
                str(self.root / "dated-claude"),
                str(self.root / "dated-keep"),
                str(self.root / "dated-twitter"),
            ],
            processing="sync",
            max_records=10,
        )

        self.assertEqual(result["failed"], 0)
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                """
                SELECT source, content, occurred_at
                FROM memories
                WHERE content LIKE '%Project MetadataDate%'
                ORDER BY source, content
                """
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 4)
        by_source = {row[0]: row[2] for row in rows}
        self.assertEqual(by_source["chatgpt"], "2026-06-29")
        self.assertEqual(by_source["claude"], "2026-06-30")
        self.assertEqual(by_source["google-keep"], "2026-07-01")
        self.assertEqual(by_source["twitter-x"], "2026-07-02")

    def test_repeated_import_skips_duplicates_without_deleting_original(self) -> None:
        self._write_chatgpt_export()
        db_path = self.root / "index.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "vault")

        first = store.import_sources(
            user_id="test-user",
            paths=[str(self.root / "chatgpt")],
            processing="async",
            max_records=20,
        )
        self.assertEqual(first["queued"], 1)
        self.assertEqual(first["skipped"], 0)
        store.run_due_jobs("test-user", limit=10)
        self.assertTrue(store.search("test-user", "Project Atlas", limit=5))

        second = store.import_sources(
            user_id="test-user",
            paths=[str(self.root / "chatgpt")],
            processing="async",
            max_records=20,
        )
        self.assertNotEqual(first["import_id"], second["import_id"])
        self.assertEqual(second["queued"], 0)
        self.assertEqual(second["saved"], 0)
        self.assertEqual(second["skipped"], 1)
        self.assertEqual(second["records"][0]["status"], "duplicate")

        detail = store.get_import("test-user", second["import_id"])
        self.assertIsNotNone(detail)
        self.assertEqual(detail["skipped"], 1)
        self.assertEqual(detail["records"][0]["status"], "duplicate")
        self.assertEqual(detail["captures"], [])
        self.assertFalse(detail["can_delete"])

        deleted_duplicate_session = store.delete_import("test-user", second["import_id"])
        self.assertTrue(deleted_duplicate_session["deleted"])
        self.assertEqual(deleted_duplicate_session["deleted_captures"], 0)
        self.assertTrue(store.search("test-user", "Project Atlas", limit=5))

    def test_near_duplicate_active_memories_reuse_existing_memory(self) -> None:
        db_path = self.root / "memory-dedupe.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "vault")
        store.update_settings("test-user", {"review_new_captures": False})

        first = store.save_capture(
            user_id="test-user",
            content="We decided Project Dedup should keep one memory with citations.",
            source="email",
            source_url="mail://first",
            title="First",
            extracted={
                "_timestamp": "2026-06-29T10:00:00Z",
                "summary": "",
                "records": [
                    {
                        "id": "mem_first_dedup",
                        "kind": "decision",
                        "layer": "decision",
                        "content": "We decided Project Dedup should keep one memory with citations.",
                        "summary": "",
                        "confidence": "confirmed",
                        "importance": 4,
                        "topics": ["dedup"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )
        second = store.save_capture(
            user_id="test-user",
            content="we decided project dedup should keep one memory with citations! https://example.com/thread",
            source="email",
            source_url="mail://second",
            title="Second",
            extracted={
                "_timestamp": "2026-06-29T10:01:00Z",
                "summary": "",
                "records": [
                    {
                        "id": "mem_second_dedup",
                        "kind": "decision",
                        "layer": "decision",
                        "content": "we decided project dedup should keep one memory with citations! https://example.com/thread",
                        "summary": "",
                        "confidence": "confirmed",
                        "importance": 4,
                        "topics": ["dedup"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )
        third = store.save_capture(
            user_id="test-user",
            content="Decision: Project Dedup should keep one memory with citations.",
            source="email",
            source_url="mail://third",
            title="Third",
            extracted={
                "_timestamp": "2026-06-29T10:02:00Z",
                "summary": "",
                "records": [
                    {
                        "id": "mem_third_dedup",
                        "kind": "decision",
                        "layer": "decision",
                        "content": "Decision: Project Dedup should keep one memory with citations.",
                        "summary": "",
                        "confidence": "confirmed",
                        "importance": 4,
                        "topics": ["dedup"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )

        self.assertEqual(second["memories"][0]["id"], first["memories"][0]["id"])
        self.assertEqual(third["memories"][0]["id"], first["memories"][0]["id"])
        conn = sqlite3.connect(db_path)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE user_id = ? AND status = 'active' AND layer = 'decision'",
                ("test-user",),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1)

    def test_html_with_links_is_not_misclassified_as_bookmarks(self) -> None:
        folder = self.root / "Notion Export"
        folder.mkdir()
        (folder / "Project Notes.html").write_text(
            "<html><body><h1>Project Notes</h1><p>Notion paragraph with a useful decision.</p><a href=\"https://example.com\">Reference</a></body></html>",
            encoding="utf-8",
        )

        records = import_source_records([str(folder)], max_records=10)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source, "notion")
        self.assertIn("Notion paragraph", records[0].content)
        self.assertIn("service=notion", records[0].source_url or "")
        self.assertIn("page=Project%20Notes", records[0].source_url or "")

    def test_consumer_ai_detection_ignores_provider_names_in_distant_parent_paths(self) -> None:
        folder = self.root / "poe-parent" / "docs"
        folder.mkdir(parents=True)
        (folder / "Citation Plan.md").write_text(
            "We decided Project CitationParent should import as docs even when a parent path contains poe.",
            encoding="utf-8",
        )

        records = import_source_records([str(folder)], max_records=10)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source, "docs")
        self.assertIn("Project CitationParent", records[0].content)
        self.assertEqual(records[0].source_url, str(folder / "Citation Plan.md"))

    def test_slack_metadata_is_skipped_and_rich_message_text_is_preserved(self) -> None:
        channel = self.root / "slack" / "general"
        channel.mkdir(parents=True)
        (self.root / "slack" / "users.json").write_text(json.dumps([{"id": "U1", "name": "sarpt"}]), encoding="utf-8")
        messages = [
            {
                "type": "message",
                "user": "U1",
                "text": "",
                "ts": "1700000000.0001",
                "attachments": [{"fallback": "Fallback launch decision", "title": "Launch Plan"}],
                "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "Block detail for Slack import"}}],
            }
        ]
        (channel / "2026-06-29.json").write_text(json.dumps(messages), encoding="utf-8")

        records = import_source_records([str(self.root / "slack")], max_records=10)
        sources = [record.source for record in records]

        self.assertEqual(sources, ["slack"])
        self.assertIn("Fallback launch decision", records[0].content)
        self.assertIn("Block detail", records[0].content)

    def test_multipart_email_prefers_plain_body_once_and_skips_attachments(self) -> None:
        folder = self.root / "mail"
        folder.mkdir()
        message = EmailMessage()
        message["Subject"] = "Multipart plan"
        message["From"] = "alex@example.com"
        message["To"] = "sarpt@example.com"
        message.set_content("Plain body should appear once.")
        message.add_alternative("<html><body><p>Plain body should appear once.</p></body></html>", subtype="html")
        message.add_attachment("Attachment phrase should not import.", filename="notes.txt")
        (folder / "multipart.eml").write_bytes(message.as_bytes())

        records = import_source_records([str(folder)], max_records=10)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].content.count("Plain body should appear once."), 1)
        self.assertNotIn("Attachment phrase", records[0].content)

    def test_calendar_and_contacts_preserve_escaped_newlines(self) -> None:
        calendar = self.root / "calendar"
        calendar.mkdir()
        (calendar / "calendar.ics").write_text(
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Line Test\nDTSTART:20260629T170000Z\nDESCRIPTION:Line one\\nLine two\nEND:VEVENT\nEND:VCALENDAR\n",
            encoding="utf-8",
        )
        contacts = self.root / "contacts"
        contacts.mkdir()
        (contacts / "contacts.vcf").write_text(
            "BEGIN:VCARD\nVERSION:3.0\nFN:Ada Lovelace\nNOTE:First note\\nSecond note\nEND:VCARD\n",
            encoding="utf-8",
        )

        records = import_source_records([str(calendar), str(contacts)], max_records=10)
        combined = "\n".join(record.content for record in records)

        self.assertIn("Line one\nLine two", combined)
        self.assertIn("First note\nSecond note", combined)
        urls = {record.source: record.source_url or "" for record in records}
        self.assertIn("service=calendar", urls["calendar"])
        self.assertIn("first_event=Line%20Test", urls["calendar"])
        self.assertIn("service=contacts", urls["contacts"])
        self.assertIn("first_contact=Ada%20Lovelace", urls["contacts"])

    def test_browser_json_bookmarks_and_history_sqlite_are_imported(self) -> None:
        browser = self.root / "browser"
        browser.mkdir()
        bookmarks = {
            "roots": {
                "bookmark_bar": {
                    "type": "folder",
                    "name": "Bookmarks Bar",
                    "children": [
                        {"type": "url", "name": "Cortex research", "url": "https://example.com/cortex"}
                    ],
                }
            }
        }
        (browser / "Bookmarks").write_text(json.dumps(bookmarks), encoding="utf-8")
        history = browser / "History"
        conn = sqlite3.connect(history)
        try:
            conn.execute("CREATE TABLE urls (url TEXT, title TEXT, visit_count INTEGER, last_visit_time INTEGER)")
            conn.execute(
                "INSERT INTO urls (url, title, visit_count, last_visit_time) VALUES (?, ?, ?, ?)",
                ("https://example.com/history", "History result", 3, 100),
            )
            conn.commit()
        finally:
            conn.close()

        records = import_source_records([str(browser)], max_records=10)
        by_source = {record.source: record for record in records}

        self.assertIn("browser-bookmarks", by_source)
        self.assertIn("browser-history", by_source)
        self.assertIn("Cortex research", by_source["browser-bookmarks"].content)
        self.assertIn("History result", by_source["browser-history"].content)

    def test_contacts_csv_is_formatted_as_contacts(self) -> None:
        contacts = self.root / "contacts"
        contacts.mkdir()
        (contacts / "Google Contacts.csv").write_text(
            "Name,E-mail 1 - Value,Phone 1 - Value,Organization 1 - Name,Notes\nAda Lovelace,ada@example.com,+15551234567,Cortex Labs,Research collaborator\n",
            encoding="utf-8",
        )

        records = import_source_records([str(contacts)], max_records=10)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source, "contacts")
        self.assertIn("Ada Lovelace", records[0].content)
        self.assertIn("ada@example.com", records[0].content)
        self.assertIn("Research collaborator", records[0].content)

    def test_store_import_sources_sync_materializes_email(self) -> None:
        self._write_email_export()
        db_path = self.root / "index.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "vault")

        result = store.import_sources(
            user_id="test-user",
            paths=[str(self.root / "mail")],
            processing="sync",
            max_records=20,
        )

        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["saved"], 1)
        self.assertTrue(store.search("test-user", "migration plan", limit=5))

    def test_external_email_sender_preferences_are_not_user_preferences(self) -> None:
        folder = self.root / "external-mail"
        folder.mkdir()
        message = EmailMessage()
        message["Subject"] = "Outside onboarding advice"
        message["From"] = "Alex Advisor <alex@example.com>"
        message["To"] = "Sarpt <sarpt@example.com>"
        message["Date"] = "Mon, 29 Jun 2026 10:00:00 +0000"
        message.set_content(
            "I prefer long onboarding checklists for you.\n"
            "My writing style is verbose and salesy.\n"
            "We decided Project Atlas should keep five clear tabs."
        )
        (folder / "outside.eml").write_bytes(message.as_bytes())

        db_path = self.root / "index.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "vault")
        result = store.import_sources(
            user_id="test-user",
            paths=[str(folder)],
            processing="sync",
            max_records=10,
        )

        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["saved"], 1)
        self.assertFalse(store.search("test-user", "long onboarding checklists", limit=5))
        decision_results = store.search("test-user", "Project Atlas five clear tabs", limit=5)
        self.assertTrue(decision_results)
        self.assertTrue(all(item["source_url"] for item in decision_results))

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT kind, layer, content, source_url FROM memories").fetchall()
        finally:
            conn.close()
        joined = "\n".join(row["content"] for row in rows)
        self.assertNotIn("long onboarding checklists", joined)
        self.assertNotIn("verbose and salesy", joined)
        self.assertTrue(all(row["source_url"] for row in rows))
        self.assertFalse(any(row["kind"] in {"preference", "style", "negative"} for row in rows))

    def test_self_authored_email_strips_quoted_threads_and_footers(self) -> None:
        folder = self.root / "quoted-mail"
        folder.mkdir()
        message = EmailMessage()
        message["Subject"] = "Quoted thread cleanup"
        message["From"] = "Sarpt <sarpt@example.com>"
        message["To"] = "Alex <alex@example.com>"
        message["Date"] = "Mon, 29 Jun 2026 10:00:00 +0000"
        message.set_content(
            "I prefer concise product updates with citations.\n"
            "We decided Project ThreadClean should ignore quoted history.\n\n"
            "On Mon, Jun 29, 2026 at 9:00 AM Alex <alex@example.com> wrote:\n"
            "> I prefer ceremonial launch emails.\n"
            "> My writing style is verbose and salesy.\n\n"
            "-- \n"
            "Sarpt\n"
            "Unsubscribe from these updates\n"
        )
        (folder / "quoted.eml").write_bytes(message.as_bytes())

        records = import_source_records([str(folder)], max_records=10)

        self.assertEqual(len(records), 1)
        self.assertIn("concise product updates", records[0].content)
        self.assertIn("Project ThreadClean", records[0].content)
        self.assertNotIn("ceremonial launch emails", records[0].content)
        self.assertNotIn("verbose and salesy", records[0].content)
        self.assertNotIn("Unsubscribe", records[0].content)

        db_path = self.root / "quoted.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "quoted-vault")
        store.update_settings("test-user", {"identity_aliases": ["Sarpt", "sarpt@example.com"]})
        result = store.import_sources(
            user_id="test-user",
            paths=[str(folder)],
            processing="sync",
            max_records=10,
        )

        self.assertEqual(result["failed"], 0)
        self.assertTrue(store.search("test-user", "concise product updates citations", limit=5))
        self.assertTrue(store.search("test-user", "Project ThreadClean quoted history", limit=5))
        self.assertFalse(store.search("test-user", "ceremonial launch emails", limit=5))
        self.assertFalse(store.search("test-user", "verbose and salesy", limit=5))

    def test_email_body_from_line_is_preserved_when_not_quoted_header(self) -> None:
        folder = self.root / "from-body-mail"
        folder.mkdir()
        message = EmailMessage()
        message["Subject"] = "Body from line"
        message["From"] = "Sarpt <sarpt@example.com>"
        message["To"] = "Alex <alex@example.com>"
        message["Date"] = "Mon, 29 Jun 2026 10:00:00 +0000"
        message.set_content(
            "From: our perspective, Project FromLine should keep legitimate body prose.\n"
            "We decided Project FromLine should preserve non-header from lines."
        )
        (folder / "from-line.eml").write_bytes(message.as_bytes())

        records = import_source_records([str(folder)], max_records=10)

        self.assertEqual(len(records), 1)
        self.assertIn("Project FromLine should keep legitimate body prose", records[0].content)
        self.assertIn("preserve non-header from lines", records[0].content)

    def test_identity_aliases_allow_self_authored_slack_and_email_preferences(self) -> None:
        slack = self.root / "slack" / "general"
        slack.mkdir(parents=True)
        (self.root / "slack" / "users.json").write_text(
            json.dumps([{"id": "U1", "name": "sarpt"}, {"id": "U2", "name": "dana"}]),
            encoding="utf-8",
        )
        (slack / "2026-06-29.json").write_text(
            json.dumps(
                [
                    {"type": "message", "user": "U1", "text": "I prefer async standups with concise summaries.", "ts": "1700000000.0001"},
                    {"type": "message", "user": "U1", "text": "My writing style uses short direct paragraphs.", "ts": "1700000000.0002"},
                    {"type": "message", "user": "U1", "text": "Never use ceremonial launch intros in engineering updates.", "ts": "1700000000.0003"},
                    {"type": "message", "user": "U2", "text": "I prefer long onboarding rituals.", "ts": "1700000001.0001"},
                ]
            ),
            encoding="utf-8",
        )
        mail = self.root / "self-mail"
        mail.mkdir()
        message = EmailMessage()
        message["Subject"] = "Self-authored notes"
        message["From"] = "Sarpt <sarpt@example.com>"
        message["To"] = "notes@example.com"
        message["Date"] = "Mon, 29 Jun 2026 10:00:00 +0000"
        message.set_content("I prefer terse launch notes with cited source links.")
        (mail / "self.eml").write_bytes(message.as_bytes())

        db_path = self.root / "index.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "vault")
        settings = store.update_settings("test-user", {"identity_aliases": ["sarpt", "sarpt@example.com"]})
        self.assertEqual(settings["identity_aliases"], ["sarpt", "sarpt@example.com"])

        result = store.import_sources(
            user_id="test-user",
            paths=[str(self.root / "slack"), str(mail)],
            processing="sync",
            max_records=10,
        )

        self.assertEqual(result["failed"], 0)
        self.assertTrue(store.search("test-user", "async standups concise summaries", limit=5))
        self.assertTrue(store.search("test-user", "short direct paragraphs", limit=5))
        self.assertTrue(store.search("test-user", "ceremonial launch intros", limit=5))
        self.assertTrue(store.search("test-user", "terse launch notes cited source links", limit=5))
        self.assertFalse(store.search("test-user", "long onboarding rituals", limit=5))

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT kind, layer, content FROM memories").fetchall()
        finally:
            conn.close()
        preference_text = "\n".join(row["content"] for row in rows if row["kind"] == "preference")
        style_text = "\n".join(row["content"] for row in rows if row["kind"] == "style")
        negative_text = "\n".join(row["content"] for row in rows if row["kind"] == "negative")
        self.assertIn("async standups", preference_text)
        self.assertIn("terse launch notes", preference_text)
        self.assertNotIn("long onboarding rituals", preference_text)
        self.assertIn("short direct paragraphs", style_text)
        self.assertIn("ceremonial launch intros", negative_text)

    def test_discord_authors_preserve_self_authored_identity(self) -> None:
        channel = self.root / "discord-identity" / "messages" / "c123"
        channel.mkdir(parents=True)
        (channel / "messages.csv").write_text(
            "ID,Timestamp,Author,Contents,Attachments\n"
            "1,2026-06-29T10:00:00,Sarpt,I prefer Discord importer identity for concise incident notes.,\n"
            "2,2026-06-29T10:01:00,Alex,I prefer noisy Discord exports for everyone.,\n"
            "3,2026-06-29T10:02:00,Sarpt,My writing style uses direct Discord bullets.,\n",
            encoding="utf-8",
        )

        records = import_source_records([str(self.root / "discord-identity")], max_records=10)
        self.assertEqual(len(records), 1)
        self.assertIn("Sarpt: I prefer Discord importer identity", records[0].content)
        self.assertIn("Alex: I prefer noisy Discord exports", records[0].content)
        self.assertIn("service=discord", records[0].source_url or "")

        db_path = self.root / "discord-identity.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "discord-identity-vault")
        store.update_settings("test-user", {"identity_aliases": ["Sarpt"]})

        result = store.import_sources(
            user_id="test-user",
            paths=[str(self.root / "discord-identity")],
            processing="sync",
            max_records=10,
        )

        self.assertEqual(result["failed"], 0)
        self.assertTrue(store.search("test-user", "Discord importer identity concise incident notes", limit=5))
        self.assertTrue(store.search("test-user", "direct Discord bullets", limit=5))
        self.assertFalse(store.search("test-user", "noisy Discord exports for everyone", limit=5))

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT kind, content, source_url FROM memories").fetchall()
        finally:
            conn.close()
        preference_text = "\n".join(row["content"] for row in rows if row["kind"] == "preference")
        style_text = "\n".join(row["content"] for row in rows if row["kind"] == "style")
        source_urls = "\n".join(row["source_url"] or "" for row in rows)
        self.assertIn("Discord importer identity", preference_text)
        self.assertNotIn("noisy Discord exports", preference_text)
        self.assertIn("direct Discord bullets", style_text)
        self.assertIn("service=discord", source_urls)

    def test_source_accounts_contribute_identity_aliases_for_imports(self) -> None:
        slack = self.root / "account-slack" / "general"
        slack.mkdir(parents=True)
        (self.root / "account-slack" / "users.json").write_text(
            json.dumps(
                [
                    {"id": "U1", "name": "u1", "real_name": "Sarpt Tandoven"},
                    {"id": "U2", "name": "dana", "real_name": "Dana Partner"},
                ]
            ),
            encoding="utf-8",
        )
        (slack / "2026-06-29.json").write_text(
            json.dumps(
                [
                    {"type": "message", "user": "U1", "text": "I prefer source-account identity matching for Slack imports.", "ts": "1782739200.0001"},
                    {"type": "message", "user": "U2", "text": "I prefer sprawling review memos for the team.", "ts": "1782739201.0001"},
                ]
            ),
            encoding="utf-8",
        )
        mail = self.root / "account-mail"
        mail.mkdir()
        message = EmailMessage()
        message["Subject"] = "Self-authored source account note"
        message["From"] = "Sarpt <sarpt@example.com>"
        message["To"] = "notes@example.com"
        message["Date"] = "Mon, 29 Jun 2026 10:00:00 +0000"
        message.set_content("I prefer Gmail account identity to classify self-authored email preferences.")
        (mail / "self.eml").write_bytes(message.as_bytes())

        db_path = self.root / "source-account-identity.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "source-account-identity-vault")
        store.upsert_source_account(
            "test-user",
            source="gmail",
            account_label="Sarpt",
            account_identifier="sarpt@example.com",
            connection_type="oauth",
            status="connected",
        )
        store.upsert_source_account(
            "test-user",
            source="slack",
            account_label="Sarpt Tandoven",
            account_identifier="U1",
            connection_type="oauth",
            status="connected",
        )

        result = store.import_sources(
            user_id="test-user",
            paths=[str(self.root / "account-slack"), str(mail)],
            processing="sync",
            max_records=10,
        )

        self.assertEqual(result["failed"], 0)
        self.assertTrue(store.search("test-user", "source-account identity matching Slack imports", limit=5))
        self.assertTrue(store.search("test-user", "Gmail account identity classify email preferences", limit=5))
        self.assertFalse(store.search("test-user", "sprawling review memos", limit=5))

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT kind, source, content FROM memories").fetchall()
        finally:
            conn.close()
        preference_text = "\n".join(row["content"] for row in rows if row["kind"] == "preference")
        self.assertIn("source-account identity matching", preference_text)
        self.assertIn("Gmail account identity", preference_text)
        self.assertNotIn("sprawling review memos", preference_text)

    def test_source_account_identity_aliases_apply_to_async_import_jobs(self) -> None:
        mail = self.root / "async-account-mail"
        mail.mkdir()
        message = EmailMessage()
        message["Subject"] = "Async source account note"
        message["From"] = "Sarpt <sarpt@example.com>"
        message["To"] = "notes@example.com"
        message["Date"] = "Mon, 29 Jun 2026 10:00:00 +0000"
        message.set_content("I prefer async import workers to keep source-account identity.")
        (mail / "self.eml").write_bytes(message.as_bytes())

        db_path = self.root / "async-source-account-identity.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "async-source-account-identity-vault")
        store.upsert_source_account(
            "test-user",
            source="gmail",
            account_label="Sarpt",
            account_identifier="sarpt@example.com",
            connection_type="oauth",
            status="connected",
        )

        result = store.import_sources(
            user_id="test-user",
            paths=[str(mail)],
            processing="async",
            max_records=10,
        )
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["queued"], 1)

        ran = store.run_due_jobs("test-user", limit=10)
        self.assertEqual(ran["processed"], 1)
        self.assertTrue(store.search("test-user", "async import workers source-account identity", limit=5))

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT kind, content FROM memories").fetchall()
        finally:
            conn.close()
        preference_text = "\n".join(row["content"] for row in rows if row["kind"] == "preference")
        self.assertIn("async import workers", preference_text)

    def test_slack_profile_email_alias_matches_real_name_speaker(self) -> None:
        slack = self.root / "profile-slack" / "general"
        slack.mkdir(parents=True)
        (self.root / "profile-slack" / "users.json").write_text(
            json.dumps(
                [
                    {
                        "id": "U1",
                        "name": "sarpt",
                        "real_name": "Sarpt Tandoven",
                        "profile": {"email": "sarpt@example.com"},
                    },
                    {
                        "id": "U2",
                        "name": "dana",
                        "real_name": "Dana Partner",
                        "profile": {"email": "dana@example.com"},
                    },
                ]
            ),
            encoding="utf-8",
        )
        (slack / "2026-06-29.json").write_text(
            json.dumps(
                [
                    {"type": "message", "user": "U1", "text": "I prefer source-backed AI handoffs with short citations.", "ts": "1782739200.0001"},
                    {"type": "message", "user": "U1", "text": "My writing style uses direct status notes.", "ts": "1782739201.0001"},
                    {"type": "message", "user": "U2", "text": "I prefer long stakeholder recaps for everyone.", "ts": "1782739202.0001"},
                ]
            ),
            encoding="utf-8",
        )

        db_path = self.root / "profile-slack.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "profile-slack-vault")
        store.update_settings("test-user", {"identity_aliases": ["sarpt@example.com"]})
        result = store.import_sources(
            user_id="test-user",
            paths=[str(self.root / "profile-slack")],
            processing="sync",
            max_records=10,
        )

        self.assertEqual(result["failed"], 0)
        self.assertTrue(store.search("test-user", "source-backed AI handoffs short citations", limit=5))
        self.assertTrue(store.search("test-user", "direct status notes", limit=5))
        self.assertFalse(store.search("test-user", "long stakeholder recaps", limit=5))

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT kind, content FROM memories").fetchall()
        finally:
            conn.close()
        preference_text = "\n".join(row["content"] for row in rows if row["kind"] == "preference")
        style_text = "\n".join(row["content"] for row in rows if row["kind"] == "style")
        self.assertIn("source-backed AI handoffs", preference_text)
        self.assertIn("direct status notes", style_text)
        self.assertNotIn("long stakeholder recaps", preference_text)

    def test_key_source_imports_preserve_citations_through_search(self) -> None:
        self._write_chatgpt_export()
        self._write_claude_export()
        self._write_slack_export()
        self._write_email_export()
        self._write_notion_export()
        self._write_cloud_and_work_exports()
        self._write_imessage_db()
        github = self.root / "GitHub" / "Project Cortex"
        github.mkdir(parents=True)
        (github / "issues.csv").write_text(
            "Title,Body\nSource paths,GitHub import should preserve source paths for cited work memory.\n",
            encoding="utf-8",
        )
        docs = self.root / "docs"
        docs.mkdir()
        (docs / "Source Citation.md").write_text(
            "Docs import should preserve a source citation smoke test for retrieval.",
            encoding="utf-8",
        )

        db_path = self.root / "index.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "vault")
        paths = [
            self.root / "chatgpt",
            self.root / "claude",
            self.root / "slack",
            self.root / "mail",
            self.root / "Notion Export",
            self.root / "Apple Notes",
            self.root / "Google Drive",
            self.root / "GitHub",
            self.root / "Messages",
            docs,
        ]
        result = store.import_sources(
            user_id="test-user",
            paths=[str(path) for path in paths],
            processing="sync",
            max_records=30,
        )

        self.assertEqual(result["failed"], 0)
        self.assertTrue(all(record["source_url"] for record in result["records"]))
        detail = store.get_import("test-user", result["import_id"])
        self.assertIsNotNone(detail)
        expected_queries = {
            "chatgpt": "local-first memory",
            "claude": "concise technical answers",
            "slack": "Cortex importer this week",
            "email": "migration plan approved memory candidates",
            "notion": "Notion markdown exports",
            "apple-notes": "concrete language",
            "cloud-docs": "Cloud docs model context",
            "github": "GitHub import should preserve source paths",
            "messages": "iMessage source citation coverage",
            "docs": "source citation smoke test",
        }
        imported_sources = {record["source"] for record in detail["records"] if record["source_url"]}
        self.assertTrue(set(expected_queries).issubset(imported_sources))
        self.assertTrue(all(record["source_url"] for record in detail["records"]))
        self.assertTrue(all(capture["source_url"] for capture in detail["captures"]))
        source_urls: dict[str, list[str]] = {}
        for record in detail["records"]:
            source_urls.setdefault(record["source"], []).append(record["source_url"])
        self.assertTrue(any("service=chatgpt" in url and "conversation=Project%20Atlas%20planning" in url for url in source_urls["chatgpt"]))
        self.assertTrue(any("service=claude" in url and "conversation=Writing%20style" in url for url in source_urls["claude"]))
        self.assertTrue(any("service=slack" in url and "channel=general" in url and "first_ts=1700000000.0001" in url for url in source_urls["slack"]))
        self.assertTrue(any("service=email" in url and "subject=Cortex%20migration%20plan" in url for url in source_urls["email"]))
        self.assertTrue(any("service=notion" in url and "page=Roadmap" in url for url in source_urls["notion"]))
        self.assertTrue(any("service=cloud-docs" in url and "provider=google-drive" in url and "document=Strategy" in url for url in source_urls["cloud-docs"]))
        self.assertTrue(any("service=github" in url and "repository=Project%20Cortex" in url and "file=issues.csv" in url for url in source_urls["github"]))
        self.assertTrue(any("service=messages" in url and "chat=Project%20Cortex" in url and "file=chat.db" in url for url in source_urls["messages"]))
        self.assertTrue(any("service=apple-notes" in url and "file=Voice.html" in url for url in source_urls["apple-notes"]))

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            memory_rows = conn.execute("SELECT source, source_url FROM memories").fetchall()
        finally:
            conn.close()
        memory_sources = {row["source"] for row in memory_rows if row["source_url"]}
        self.assertTrue(set(expected_queries).issubset(memory_sources))

        for source, query in expected_queries.items():
            with self.subTest(source=source):
                hits = [item for item in store.search("test-user", query, limit=8) if item["source"] == source]
                self.assertTrue(hits)
                self.assertTrue(all(item["source_url"] for item in hits))

    def test_capped_multi_service_import_keeps_later_service_sources_retrievable(self) -> None:
        chatgpt = self.root / "chatgpt-heavy"
        chatgpt.mkdir()
        chatgpt_payload = []
        for index in range(30):
            chatgpt_payload.append(
                {
                    "title": f"Project Fairshare noisy chat {index}",
                    "create_time": 1_700_000_000 + index,
                    "mapping": {
                        "a": {
                            "message": {
                                "author": {"role": "user"},
                                "create_time": 1_700_000_001 + index,
                                "content": {
                                    "parts": [
                                        f"Project Fairshare noisy chat filler {index} should not crowd out every other service."
                                    ]
                                },
                            }
                        }
                    },
                }
            )
        (chatgpt / "conversations.json").write_text(json.dumps(chatgpt_payload), encoding="utf-8")
        github = self.root / "GitHub" / "Project Cortex"
        github.mkdir(parents=True)
        (github / "issues.csv").write_text(
            "Title,Body\n"
            "Fairshare canonical issue,We decided Project Fairshare canonical GitHub issue should survive capped multi-service import.\n",
            encoding="utf-8",
        )

        db_path = self.root / "fairshare.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "fairshare-vault")
        result = store.import_sources(
            user_id="test-user",
            paths=[str(chatgpt), str(github)],
            processing="sync",
            max_records=10,
        )

        self.assertEqual(result["failed"], 0)
        sources = {item["source"] for item in result["sources"]}
        self.assertIn("chatgpt", sources)
        self.assertIn("github", sources)
        results = store.search("test-user", "Project Fairshare canonical GitHub issue", limit=5)
        github_hit = next((item for item in results if item["source"] == "github"), None)

        self.assertIsNotNone(github_hit)
        assert github_hit is not None
        self.assertIn("service=github", github_hit["source_url"])
        self.assertIn("line=", github_hit["source_url"])
        self.assertIn("excerpt=", github_hit["source_url"])

    def test_imported_memories_get_granular_source_url_fragments(self) -> None:
        slack = self.root / "granular-slack" / "general"
        slack.mkdir(parents=True)
        (self.root / "granular-slack" / "users.json").write_text(
            json.dumps([{"id": "U1", "name": "sarpt"}]),
            encoding="utf-8",
        )
        (slack / "2026-06-29.json").write_text(
            json.dumps(
                [
                    {"type": "message", "user": "U1", "text": "We decided Project Pinpoint first message should stay searchable.", "ts": "1700000000.0001"},
                    {"type": "message", "user": "U1", "text": "We decided Project Pinpoint second Slack message should have a precise citation.", "ts": "1700000001.0001"},
                ]
            ),
            encoding="utf-8",
        )
        github = self.root / "GitHub" / "Project Cortex"
        github.mkdir(parents=True)
        (github / "issues.csv").write_text(
            "Title,Body\n"
            "First row,We decided Project Needle first row should stay searchable.\n"
            "Second row,We decided Project Needle row citation should point at row two precisely.\n",
            encoding="utf-8",
        )
        calendar = self.root / "calendar-granular"
        calendar.mkdir()
        (calendar / "calendar.ics").write_text(
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\n"
            "SUMMARY:Project Aurora first event\n"
            "DTSTART:20260701T170000Z\n"
            "DTEND:20260701T180000Z\n"
            "DESCRIPTION:We decided Project Aurora first event should stay searchable.\n"
            "END:VEVENT\n"
            "BEGIN:VEVENT\n"
            "SUMMARY:Project Aurora second event\n"
            "DTSTART:20260702T170000Z\n"
            "DTEND:20260702T180000Z\n"
            "DESCRIPTION:We decided Project Aurora event citation should point at the second calendar event.\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n",
            encoding="utf-8",
        )

        db_path = self.root / "granular-citations.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "granular-vault")
        result = store.import_sources(
            user_id="test-user",
            paths=[str(self.root / "granular-slack"), str(github), str(calendar)],
            processing="sync",
            max_records=10,
        )

        self.assertEqual(result["failed"], 0)
        slack_hits = store.search("test-user", "Project Pinpoint second Slack precise citation", limit=5)
        self.assertTrue(slack_hits)
        self.assertTrue(any("message=2" in (hit["source_url"] or "") and "line=" in (hit["source_url"] or "") for hit in slack_hits))

        row_hits = store.search("test-user", "Project Needle row citation row two", limit=5)
        self.assertTrue(row_hits)
        self.assertTrue(any("row=2" in (hit["source_url"] or "") and "line=" in (hit["source_url"] or "") for hit in row_hits))

        event_hits = store.search("test-user", "Project Aurora event citation second calendar event", limit=5)
        self.assertTrue(event_hits)
        self.assertTrue(any("event=2" in (hit["source_url"] or "") and "line=" in (hit["source_url"] or "") for hit in event_hits))

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT content, source_url, raw_excerpt
                FROM memories
                WHERE content LIKE '%Project Pinpoint second%'
                   OR content LIKE '%Project Needle row citation%'
                   OR content LIKE '%Project Aurora event citation%'
                """
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["raw_excerpt"] and "Source:" not in row["raw_excerpt"] for row in rows))
        self.assertTrue(all("excerpt=" in row["source_url"] for row in rows))

    def test_chatgpt_claude_email_memories_get_granular_source_url_fragments(self) -> None:
        self._write_chatgpt_export()
        self._write_claude_export()
        self._write_email_export()
        self._write_consumer_ai_transcript_exports()

        db_path = self.root / "chat-email-granular.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "chat-email-granular-vault")
        result = store.import_sources(
            user_id="test-user",
            paths=[
                str(self.root / "chatgpt"),
                str(self.root / "claude"),
                str(self.root / "mail"),
                str(self.root / "gemini"),
                str(self.root / "perplexity"),
                str(self.root / "copilot"),
                str(self.root / "grok"),
                str(self.root / "poe"),
                str(self.root / "notebooklm"),
            ],
            processing="sync",
            max_records=20,
        )

        self.assertEqual(result["failed"], 0)
        expectations = {
            "chatgpt": ("Project Atlas local-first memory", ("service=chatgpt", "conversation=Project%20Atlas%20planning", "line=", "message=1", "excerpt=")),
            "claude": ("concise technical answers clear tradeoffs", ("service=claude", "conversation=Writing%20style", "line=", "message=1", "excerpt=")),
            "email": ("migration plan approved memory candidates", ("service=email", "subject=Cortex%20migration%20plan", "line=", "excerpt=")),
            "gemini": ("Project GeminiTranscript preserve line message excerpt citations", ("service=gemini", "conversation=Project%20Gemini%20transcript", "line=", "message=2", "excerpt=")),
            "perplexity": ("Project PerplexityTranscript preserve line message excerpt citations", ("service=perplexity", "conversation=Project%20Perplexity%20transcript", "line=", "message=2", "excerpt=")),
            "copilot": ("Project CopilotTranscript preserve line message excerpt citations", ("service=copilot", "conversation=Project%20Microsoft%20Copilot%20transcript", "line=", "message=2", "excerpt=")),
            "grok": ("Project GrokTranscript preserve line message excerpt citations", ("service=grok", "conversation=Project%20Grok%20transcript", "line=", "message=2", "excerpt=")),
            "poe": ("Project PoeTranscript preserve line message excerpt citations", ("service=poe", "conversation=Project%20Poe%20transcript", "line=", "message=2", "excerpt=")),
            "notebooklm": ("Project NotebooklmTranscript preserve line message excerpt citations", ("service=notebooklm", "conversation=Project%20NotebookLM%20transcript", "line=", "message=2", "excerpt=")),
        }
        for source, (query, fragments) in expectations.items():
            with self.subTest(source=source):
                hits = [item for item in store.search("test-user", query, limit=8) if item["source"] == source]
                self.assertTrue(hits)
                for fragment in fragments:
                    self.assertIn(fragment, hits[0]["source_url"] or "")
                self.assertNotIn("Source:", hits[0]["raw_excerpt"])

    def test_sync_source_import_uses_deterministic_extraction_by_default(self) -> None:
        docs = self.root / "docs"
        docs.mkdir()
        note = docs / "Deterministic Import.md"
        note.write_text(
            "I prefer deterministic source import memories for production readiness.",
            encoding="utf-8",
        )

        db_path = self.root / "index.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "vault")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}), patch(
            "backend.app.extractor._extract_with_claude",
            side_effect=BaseException("model extraction should not run for source imports"),
        ) as model_extract:
            result = store.import_sources(
                user_id="test-user",
                paths=[str(note)],
                processing="sync",
                max_records=10,
            )

        self.assertEqual(result["failed"], 0)
        self.assertGreaterEqual(result["saved"], 1)
        model_extract.assert_not_called()
        self.assertTrue(store.search("test-user", "deterministic source import memories", limit=3))

    def test_async_source_import_uses_deterministic_extraction_by_default(self) -> None:
        docs = self.root / "docs"
        docs.mkdir()
        note = docs / "Async Deterministic Import.md"
        note.write_text(
            "I prefer queued deterministic source import memories for production readiness.",
            encoding="utf-8",
        )

        db_path = self.root / "index.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "vault")
        result = store.import_sources(
            user_id="test-user",
            paths=[str(note)],
            processing="async",
            max_records=10,
        )

        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["queued"], 1)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}), patch(
            "backend.app.extractor._extract_with_claude",
            side_effect=BaseException("model extraction should not run for queued source imports"),
        ) as model_extract:
            ran = store.run_due_jobs("test-user", limit=10)

        self.assertEqual(ran["processed"], 1)
        model_extract.assert_not_called()
        self.assertTrue(store.search("test-user", "queued deterministic source import memories", limit=3))

    def test_generic_file_import_preserves_source_url_for_citations(self) -> None:
        docs = self.root / "docs"
        docs.mkdir()
        note = docs / "Citation Plan.md"
        note.write_text(
            "On June 29, 2026, we shipped the source-url citation path. "
            "We decided imported file memories should keep their source path.",
            encoding="utf-8",
        )

        records = import_source_records([str(note)], max_records=10)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source_url, str(note))

        db_path = self.root / "index.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "vault")
        result = store.import_sources(
            user_id="test-user",
            paths=[str(note)],
            processing="sync",
            max_records=10,
        )

        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["saved"], 1)
        detail = store.get_import("test-user", result["import_id"])
        self.assertIsNotNone(detail)
        self.assertEqual(detail["records"][0]["source_url"], str(note))
        self.assertEqual(detail["captures"][0]["source_url"], str(note))

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            memory_rows = conn.execute("SELECT content, source_url FROM memories").fetchall()
        finally:
            conn.close()
        self.assertTrue(memory_rows)
        self.assertTrue(all(row["source_url"].startswith(str(note)) for row in memory_rows))
        self.assertTrue(all("line=" in row["source_url"] for row in memory_rows))
        memory_content = "\n".join(row["content"] for row in memory_rows)
        self.assertNotIn("Source file:", memory_content)
        self.assertNotIn("Path:", memory_content)

    def test_docs_headings_keep_personal_memory_but_work_tool_rows_do_not(self) -> None:
        docs = self.root / "docs"
        docs.mkdir()
        (docs / "Voice Guide.md").write_text(
            "Voice:\n"
            "My writing style uses terse project notes.\n\n"
            "Preference:\n"
            "I prefer source-backed answers with direct caveats.\n\n"
            "Avoid:\n"
            "Never use ceremonial launch intros.\n",
            encoding="utf-8",
        )
        github = self.root / "GitHub" / "Project Cortex"
        github.mkdir(parents=True)
        (github / "issues.csv").write_text(
            "Title,Body\n"
            "External preference,I prefer every ticket to use long public launch testimonials.\n"
            "Useful decision,We decided Project Gate should keep issue decisions as semantic memory.\n",
            encoding="utf-8",
        )

        db_path = self.root / "personal-gating.sqlite"
        init_db(db_path)
        store = self._store(db_path, self.root / "personal-gating-vault")
        result = store.import_sources(
            user_id="test-user",
            paths=[str(docs), str(github)],
            processing="sync",
            max_records=10,
        )

        self.assertEqual(result["failed"], 0)
        self.assertTrue(store.search("test-user", "terse project notes", limit=5))
        self.assertTrue(store.search("test-user", "direct caveats", limit=5))
        self.assertTrue(store.search("test-user", "ceremonial launch intros", limit=5))
        self.assertTrue(store.search("test-user", "Project Gate issue decisions semantic memory", limit=5))
        self.assertFalse(store.search("test-user", "long public launch testimonials", limit=5))

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT source, kind, content FROM memories").fetchall()
        finally:
            conn.close()
        docs_kinds = {row["kind"] for row in rows if row["source"] == "docs"}
        github_text = "\n".join(row["content"] for row in rows if row["source"] == "github")
        self.assertTrue({"style", "preference", "negative"}.issubset(docs_kinds))
        self.assertNotIn("long public launch testimonials", github_text)
        self.assertFalse(any(row["source"] == "github" and row["kind"] in {"preference", "style", "negative"} for row in rows))

    def _write_chatgpt_export(self) -> None:
        folder = self.root / "chatgpt"
        folder.mkdir()
        payload = [
            {
                "title": "Project Atlas planning",
                "create_time": 1_700_000_000,
                "mapping": {
                    "a": {
                        "message": {
                            "author": {"role": "user"},
                            "create_time": 1_700_000_001,
                            "content": {"parts": ["We decided Project Atlas should use local-first memory."]},
                        }
                    },
                    "b": {
                        "message": {
                            "author": {"role": "assistant"},
                            "create_time": 1_700_000_002,
                            "content": {"parts": ["Confirmed. Keep citations with every memory."]},
                        }
                    },
                },
            }
        ]
        (folder / "conversations.json").write_text(json.dumps(payload), encoding="utf-8")

    def _write_long_chatgpt_export(self) -> None:
        folder = self.root / "chatgpt-long"
        folder.mkdir()
        mapping = {}
        for index in range(34):
            mapping[f"filler-{index}"] = {
                "message": {
                    "author": {"role": "user"},
                    "create_time": 1_782_700_000 + index,
                    "content": {"parts": [f"Longtail filler note {index} should keep ordinary project context available."]},
                }
            }
        mapping["late-decision"] = {
            "message": {
                "author": {"role": "user"},
                "create_time": 1_782_700_100,
                "content": {"parts": ["We decided Project Longtail should preserve late export decisions after chunking."]},
            }
        }
        payload = [
            {
                "title": "Project Longtail history",
                "create_time": 1_782_700_000,
                "mapping": mapping,
            }
        ]
        (folder / "conversations.json").write_text(json.dumps(payload), encoding="utf-8")

    def _write_dated_service_exports(self) -> None:
        chatgpt = self.root / "dated-chatgpt"
        chatgpt.mkdir()
        chatgpt_payload = [
            {
                "title": "Metadata date ChatGPT",
                "create_time": 1_782_734_400,
                "mapping": {
                    "decision": {
                        "message": {
                            "author": {"role": "user"},
                            "create_time": 1_782_734_400,
                            "content": {"parts": ["We decided Project MetadataDate ChatGPT should use message timestamps."]},
                        }
                    }
                },
            }
        ]
        (chatgpt / "conversations.json").write_text(json.dumps(chatgpt_payload), encoding="utf-8")

        claude = self.root / "dated-claude"
        claude.mkdir()
        claude_payload = [
            {
                "name": "Metadata date Claude",
                "created_at": "2026-06-30T10:00:00Z",
                "chat_messages": [
                    {"sender": "human", "text": "We decided Project MetadataDate Claude should use conversation timestamps."}
                ],
            }
        ]
        (claude / "conversations.json").write_text(json.dumps(claude_payload), encoding="utf-8")

        keep = self.root / "dated-keep"
        keep.mkdir()
        keep_payload = {
            "title": "Metadata date Keep",
            "createdTimestampUsec": 1_782_864_000_000_000,
            "textContent": "We decided Project MetadataDate Keep should use note timestamps.",
        }
        (keep / "metadata-date.json").write_text(json.dumps(keep_payload), encoding="utf-8")

        twitter = self.root / "dated-twitter" / "data"
        twitter.mkdir(parents=True)
        tweets = [
            {
                "tweet": {
                    "created_at": "Thu Jul 02 10:00:00 +0000 2026",
                    "full_text": "We decided Project MetadataDate Twitter should use archive timestamps.",
                }
            }
        ]
        (twitter / "tweets.js").write_text("window.YTD.tweets.part0 = " + json.dumps(tweets), encoding="utf-8")

    def _write_claude_export(self) -> None:
        folder = self.root / "claude"
        folder.mkdir()
        payload = [
            {
                "name": "Writing style",
                "created_at": "2026-06-29T00:00:00Z",
                "chat_messages": [
                    {"sender": "human", "text": "I prefer concise technical answers with clear tradeoffs."},
                    {"sender": "assistant", "text": "I will keep the style concise and concrete."},
                ],
            }
        ]
        (folder / "conversations.json").write_text(json.dumps(payload), encoding="utf-8")

    def _write_consumer_ai_transcript_exports(self) -> None:
        providers = {
            "gemini": "Gemini",
            "perplexity": "Perplexity",
            "copilot": "Microsoft Copilot",
            "grok": "Grok",
            "poe": "Poe",
            "notebooklm": "NotebookLM",
        }
        for source, provider in providers.items():
            folder = self.root / source
            folder.mkdir()
            project_token = "Notebooklm" if source == "notebooklm" else source.title().replace("-", "")
            payload = {
                "conversations": [
                    {
                        "id": f"{source}-fixture-1",
                        "title": f"Project {provider} transcript",
                        "created_at": "2026-06-29T12:00:00Z",
                        "messages": [
                            {
                                "role": "assistant",
                                "created_at": "2026-06-29T12:00:01Z",
                                "text": f"I prefer verbose summaries for {provider} imports.",
                            },
                            {
                                "role": "user",
                                "created_at": "2026-06-29T12:00:02Z",
                                "text": f"We decided Project {project_token}Transcript should preserve line, message, and excerpt citations.",
                            },
                        ],
                    }
                ]
            }
            (folder / "transcript.json").write_text(json.dumps(payload), encoding="utf-8")

    def _write_slack_export(self) -> None:
        channel = self.root / "slack" / "general"
        channel.mkdir(parents=True)
        (self.root / "slack" / "users.json").write_text(json.dumps([{"id": "U1", "name": "sarpt"}]), encoding="utf-8")
        messages = [{"type": "message", "user": "U1", "text": "Ship the Cortex importer this week.", "ts": "1700000000.0001"}]
        (channel / "2026-06-29.json").write_text(json.dumps(messages), encoding="utf-8")

    def _write_discord_export(self) -> None:
        channel = self.root / "discord" / "messages" / "c123"
        channel.mkdir(parents=True)
        (channel / "messages.csv").write_text("ID,Timestamp,Contents,Attachments\n1,2026-06-29,Discord decision memory,\n", encoding="utf-8")

    def _write_telegram_export(self) -> None:
        folder = self.root / "telegram"
        folder.mkdir()
        payload = {
            "chats": {
                "list": [
                    {
                        "name": "Project Telegram",
                        "messages": [
                            {
                                "id": 1,
                                "date": "2026-06-29T10:00:00",
                                "from": "Sarpt",
                                "text": "Telegram export should keep source citations.",
                            }
                        ],
                    }
                ]
            }
        }
        (folder / "result.json").write_text(json.dumps(payload), encoding="utf-8")

    def _write_google_keep_export(self) -> None:
        folder = self.root / "takeout" / "Keep"
        folder.mkdir(parents=True)
        note = {"title": "Preference", "textContent": "Never use vague summaries when a cited answer is possible."}
        (folder / "preference.json").write_text(json.dumps(note), encoding="utf-8")

    def _write_notion_export(self) -> None:
        folder = self.root / "Notion Export"
        folder.mkdir()
        (folder / "Roadmap.md").write_text("# Roadmap\n\nCortex importer supports Notion markdown exports.", encoding="utf-8")

    def _write_email_export(self) -> None:
        folder = self.root / "mail"
        folder.mkdir()
        message = EmailMessage()
        message["Subject"] = "Cortex migration plan"
        message["From"] = "alex@example.com"
        message["To"] = "sarpt@example.com"
        message["Date"] = "Mon, 29 Jun 2026 10:00:00 +0000"
        message.set_content("The migration plan is to import email as approved memory candidates.")
        (folder / "migration.eml").write_bytes(message.as_bytes())

    def _write_gmail_mbox_zip(self) -> None:
        folder = self.root / "gmail"
        folder.mkdir()
        mbox_text = """From alex@example.com Mon Jun 29 10:00:00 2026
Subject: Gmail import decision
From: alex@example.com
To: sarpt@example.com
Date: Mon, 29 Jun 2026 10:00:00 +0000

Gmail import should preserve important project mail.
"""
        with zipfile.ZipFile(folder / "takeout.zip", "w") as archive:
            archive.writestr("Takeout/Mail/All mail Including Spam and Trash.mbox", mbox_text)

    def _write_whatsapp_export(self) -> None:
        folder = self.root / "whatsapp"
        folder.mkdir()
        text = "[6/29/26, 10:00] Alex: WhatsApp exports should become episodic memory.\n"
        (folder / "WhatsApp Chat with Alex.txt").write_text(text, encoding="utf-8")

    def _write_imessage_db(self) -> None:
        folder = self.root / "Messages"
        folder.mkdir()
        db_path = folder / "chat.db"
        timestamp = int((datetime(2026, 6, 29, 10, 0, tzinfo=timezone.utc).timestamp() - 978_307_200) * 1_000_000_000)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, date INTEGER, is_from_me INTEGER, handle_id INTEGER)")
            conn.execute("CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT)")
            conn.execute("CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, display_name TEXT)")
            conn.execute("CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER)")
            conn.execute("INSERT INTO handle (ROWID, id) VALUES (1, ?)", ("+15551234567",))
            conn.execute("INSERT INTO chat (ROWID, display_name) VALUES (1, ?)", ("Project Cortex",))
            conn.execute(
                "INSERT INTO message (ROWID, text, date, is_from_me, handle_id) VALUES (1, ?, ?, 1, 1)",
                ("We decided iMessage source citation coverage should use chat database paths.", timestamp),
            )
            conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (1, 1)")
            conn.commit()
        finally:
            conn.close()

    def _write_browser_bookmarks_export(self) -> None:
        folder = self.root / "browser"
        folder.mkdir()
        html = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p><DT><A HREF="https://example.com/research">browser research for Cortex retrieval</A></DT></DL>
"""
        (folder / "Bookmarks.html").write_text(html, encoding="utf-8")

    def _write_calendar_export(self) -> None:
        folder = self.root / "calendar"
        folder.mkdir()
        ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Project Kestrel launch
DTSTART:20260629T170000Z
DTEND:20260629T180000Z
LOCATION:Remote
DESCRIPTION:Review source ingestion readiness.
END:VEVENT
END:VCALENDAR
"""
        (folder / "calendar.ics").write_text(ics, encoding="utf-8")

    def _write_contacts_export(self) -> None:
        folder = self.root / "contacts"
        folder.mkdir()
        vcf = """BEGIN:VCARD
VERSION:3.0
FN:Ada Lovelace
ORG:Cortex Labs
TITLE:Research Lead
EMAIL:ada@example.com
NOTE:Important collaborator for adaptation memory.
END:VCARD
"""
        (folder / "contacts.vcf").write_text(vcf, encoding="utf-8")

    def _write_twitter_export(self) -> None:
        folder = self.root / "twitter" / "data"
        folder.mkdir(parents=True)
        tweets = [{"tweet": {"created_at": "Mon Jun 29 10:00:00 +0000 2026", "full_text": "Cortex should remember public writing style.", "favorite_count": "2"}}]
        dms = [{"dmConversation": {"conversationId": "1-2", "messages": [{"messageCreate": {"senderId": "1", "createdAt": "2026-06-29T10:00:00.000Z", "text": "Twitter DM context can matter."}}]}}]
        (folder / "tweets.js").write_text("window.YTD.tweets.part0 = " + json.dumps(tweets), encoding="utf-8")
        (folder / "direct-messages.js").write_text("window.YTD.direct_messages.part0 = " + json.dumps(dms), encoding="utf-8")

    def _write_linkedin_export(self) -> None:
        folder = self.root / "linkedin"
        folder.mkdir()
        messages = "CONVERSATION ID,CONVERSATION TITLE,FROM,DATE,CONTENT\n1,Cortex Advisors,Ada Lovelace,2026-06-29,LinkedIn message about source coverage.\n"
        connections = "First Name,Last Name,Company,Position,Connected On,Email Address\nGrace,Hopper,Compiler Co,Advisor,2026-06-01,grace@example.com\n"
        (folder / "Messages.csv").write_text(messages, encoding="utf-8")
        (folder / "Connections.csv").write_text(connections, encoding="utf-8")

    def _write_cloud_and_work_exports(self) -> None:
        drive = self.root / "Google Drive" / "Docs"
        drive.mkdir(parents=True)
        (drive / "Strategy.md").write_text("# Strategy\n\nCloud docs should become model context.", encoding="utf-8")
        notes = self.root / "Apple Notes"
        notes.mkdir()
        (notes / "Voice.html").write_text("<html><body><h1>Voice</h1><p>Prefer concrete language.</p></body></html>", encoding="utf-8")
        jira = self.root / "Jira"
        jira.mkdir()
        (jira / "issues.csv").write_text("Key,Summary,Status\nCX-1,Importer should support work tools,Done\n", encoding="utf-8")

    def _write_collaboration_exports(self) -> None:
        google_chat = self.root / "Takeout" / "Google Chat" / "Project Space"
        google_chat.mkdir(parents=True)
        (google_chat / "messages.json").write_text(
            json.dumps(
                {
                    "messages": [
                        {
                            "created_date": "2026-06-29T10:00:00Z",
                            "creator": {"name": "Ada Lovelace", "email": "ada@example.com"},
                            "text": "Google Chat launch plan should be imported with citations.",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        teams = self.root / "Microsoft Teams" / "General"
        teams.mkdir(parents=True)
        (teams / "messages.json").write_text(
            json.dumps(
                {
                    "messages": [
                        {
                            "createdDateTime": "2026-06-29T11:00:00Z",
                            "from": {"user": {"displayName": "Grace Hopper"}},
                            "body": {"content": "<p>Teams migration note should become retrievable memory.</p>"},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        zoom = self.root / "Zoom"
        zoom.mkdir()
        (zoom / "Project Sync.vtt").write_text(
            "WEBVTT\n\n1\n00:00:01.000 --> 00:00:04.000\nAda: Zoom transcript memory should keep speaker lines.\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
