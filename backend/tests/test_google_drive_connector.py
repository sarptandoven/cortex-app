from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.connectors.google_drive import GOOGLE_DOC_MIME_TYPE, fetch_google_drive_records
from backend.app.database import init_db
from backend.app.mcp_tools import call_tool
from backend.app.storage import CortexStore


def _drive_file(file_id: str, *, name: str = "Project Atlas Plan", mime_type: str = GOOGLE_DOC_MIME_TYPE) -> dict:
    return {
        "id": file_id,
        "name": name,
        "mimeType": mime_type,
        "createdTime": "2026-06-29T10:00:00Z",
        "modifiedTime": "2026-06-30T10:00:00Z",
        "webViewLink": f"https://docs.google.com/document/d/{file_id}/edit",
        "owners": [{"emailAddress": "sarp@example.com", "displayName": "Sarp Doven"}],
        "lastModifyingUser": {"emailAddress": "sarp@example.com", "displayName": "Sarp Doven"},
    }


class GoogleDriveConnectorTests(unittest.TestCase):
    def test_fetch_google_drive_records_exports_docs_with_citations(self) -> None:
        calls: list[str] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append(url)
            self.assertEqual(headers["Authorization"], "Bearer drive-test")
            if url.endswith("/about?fields=user(emailAddress,displayName)"):
                return {"user": {"emailAddress": "sarp@example.com", "displayName": "Sarp Doven"}}
            if "/files?" in url:
                self.assertIn("mimeType+%3D+%27application%2Fvnd.google-apps.document%27", url)
                return {"files": [_drive_file("doc-1")], "nextPageToken": "page-2"}
            self.assertTrue(url.endswith("/files/doc-1/export?mimeType=text%2Fplain"))
            return "We decided Google Drive sync should preserve exported Google Doc text."

        sync = fetch_google_drive_records(
            access_token="drive-test",
            mime_types=[GOOGLE_DOC_MIME_TYPE],
            since="2026-06-29T00:00:00Z",
            max_records=1,
            request_value=fake_request,
        )

        self.assertEqual(len(calls), 3)
        self.assertEqual(sync.user_email, "sarp@example.com")
        self.assertEqual(sync.records_found, 1)
        self.assertEqual(sync.records_returned, 1)
        self.assertEqual(sync.next_page_token, "page-2")
        record = sync.records[0].to_source_account_record()
        self.assertEqual(record["external_id"], "google-drive:file:doc-1")
        self.assertEqual(record["source_url"], "https://docs.google.com/document/d/doc-1/edit")
        self.assertIn("File: Project Atlas Plan", record["content"])
        self.assertIn("exported Google Doc text", record["content"])
        self.assertEqual(record["metadata"]["author_role"], "user")
        self.assertEqual(record["metadata"]["user_email"], "sarp@example.com")

    def test_fetch_google_drive_records_requires_access_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "access token"):
            fetch_google_drive_records(access_token="")

    def test_fetch_google_drive_records_skips_unsupported_binary_files(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            if url.endswith("/about?fields=user(emailAddress,displayName)"):
                return {"user": {"emailAddress": "sarp@example.com"}}
            self.assertIn("/files?", url)
            return {"files": [_drive_file("pdf-1", name="Scanned PDF", mime_type="application/pdf")]}

        sync = fetch_google_drive_records(access_token="drive-test", request_value=fake_request)

        self.assertEqual(sync.records_found, 1)
        self.assertEqual(sync.records_returned, 0)
        self.assertEqual(sync.skipped_unsupported, 1)

    def test_fetch_google_drive_records_redacts_access_token_from_errors(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            if url.endswith("/about?fields=user(emailAddress,displayName)"):
                return {"user": {"emailAddress": "sarp@example.com"}}
            raise RuntimeError("Authorization Bearer drive-secret-token failed")

        sync = fetch_google_drive_records(access_token="drive-secret-token", request_value=fake_request)

        self.assertEqual(sync.records_returned, 0)
        self.assertEqual(len(sync.errors), 1)
        self.assertNotIn("drive-secret-token", json.dumps(sync.errors))


class GoogleDriveStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "cortex-test.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, vault_path=self.root / "vault")
        self.user_id = "google-drive-storage-user"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_google_drive_account_sync_persists_citations(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            self.assertEqual(headers["Authorization"], "Bearer drive-sync-token")
            if url.endswith("/about?fields=user(emailAddress,displayName)"):
                return {"user": {"emailAddress": "sarp@example.com", "displayName": "Sarp Doven"}}
            if "/files?" in url:
                return {"files": [_drive_file("doc-sync")]}
            return "We decided Drive retrieval should keep cited source-backed document memory."

        result = self.store.sync_google_drive_account(
            self.user_id,
            access_token="drive-sync-token",
            processing="sync",
            max_records=10,
            request_value=fake_request,
        )

        self.assertEqual(result["source"], "google-drive")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["source_account"]["connection_type"], "oauth-token")
        self.assertEqual(result["source_account"]["account_identifier"], "sarp@example.com")
        self.assertNotIn("drive-sync-token", json.dumps(result))
        capture_id = result["capture_ids"][0]
        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))
        hits = self.store.search(self.user_id, "cited source-backed document memory", limit=5)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["source"], "google-drive")
        self.assertTrue(hits[0]["source_url"].startswith("https://docs.google.com/document/d/doc-sync/edit"))
        self.assertIn("line=", hits[0]["source_url"])
        self.assertIn("excerpt=", hits[0]["source_url"])
        self.assertEqual(hits[0]["provenance"]["record_metadata"]["file_id"], "doc-sync")

    def test_mcp_google_drive_sync_tool_fetches_records_without_exposing_token(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            self.assertEqual(headers["Authorization"], "Bearer drive-mcp-token")
            if url.endswith("/about?fields=user(emailAddress,displayName)"):
                return {"user": {"emailAddress": "sarp@example.com"}}
            if "/files?" in url:
                return {"files": [_drive_file("mcp-doc")]}
            return "We decided MCP Drive sync should write source-account records."

        with self.assertRaises(PermissionError):
            call_tool(
                self.store,
                self.user_id,
                "sync_google_drive",
                {"access_token": "drive-mcp-token"},
                token_scopes=["read"],
            )

        with patch("backend.app.connectors.google_drive._request_value", side_effect=fake_request):
            synced = call_tool(
                self.store,
                self.user_id,
                "sync_google_drive",
                {"access_token": "drive-mcp-token", "processing": "sync", "max_records": 10},
                token_scopes=["write"],
            )

        self.assertEqual(synced["source"], "google-drive")
        self.assertEqual(synced["saved"], 1)
        self.assertNotIn("drive-mcp-token", json.dumps(synced))
        self.assertEqual(synced["records"][0]["source_url"], "https://docs.google.com/document/d/mcp-doc/edit")


if __name__ == "__main__":
    unittest.main()
