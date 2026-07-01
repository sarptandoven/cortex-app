from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from backend.app.connectors.zotero import fetch_zotero_records


class ZoteroConnectorTests(unittest.TestCase):
    def test_fetch_zotero_records_normalizes_items_notes_and_annotations(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append((url, headers))
            parsed = urlparse(url)
            self.assertEqual(parsed.path, "/api/users/0/items")
            query = parse_qs(parsed.query)
            self.assertEqual(query["format"], ["json"])
            self.assertEqual(query["include"], ["data"])
            self.assertEqual(query["sort"], ["dateModified"])
            self.assertEqual(query["direction"], ["desc"])
            self.assertEqual(query["limit"], ["10"])
            self.assertEqual(query["start"], ["0"])
            self.assertEqual(headers["Zotero-API-Version"], "3")
            self.assertNotIn("Zotero-API-Key", headers)
            return [
                {
                    "key": "ITEM1234",
                    "version": 87,
                    "links": {"alternate": {"href": "https://www.zotero.org/users/0/items/ITEM1234"}},
                    "data": {
                        "key": "ITEM1234",
                        "itemType": "journalArticle",
                        "title": "Local-first memory systems",
                        "creators": [{"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"}],
                        "publicationTitle": "Cortex Journal",
                        "date": "2026",
                        "DOI": "10.0000/cortex",
                        "abstractNote": "We decided Cortex should retrieve Zotero items with exact source citations.",
                        "dateAdded": "2026-06-29T10:00:00Z",
                        "dateModified": "2026-06-30T10:30:00Z",
                        "tags": [{"tag": "retrieval"}],
                    },
                },
                {
                    "key": "NOTE1234",
                    "version": 88,
                    "data": {
                        "key": "NOTE1234",
                        "itemType": "note",
                        "parentItem": "ITEM1234",
                        "note": "<p>We decided Zotero notes should become one source-account record.</p>",
                        "dateModified": "2026-06-30T11:00:00Z",
                    },
                },
                {
                    "key": "ANNO1234",
                    "version": 89,
                    "data": {
                        "key": "ANNO1234",
                        "itemType": "annotation",
                        "parentItem": "ITEM1234",
                        "annotationType": "highlight",
                        "annotationText": "Annotations should preserve stable Zotero citations.",
                        "annotationComment": "Important for research recall.",
                        "annotationPageLabel": "42",
                        "dateModified": "2026-06-30T12:00:00Z",
                    },
                },
                {
                    "key": "PDF1234",
                    "version": 90,
                    "data": {
                        "key": "PDF1234",
                        "itemType": "attachment",
                        "title": "Local PDF",
                        "contentType": "application/pdf",
                    },
                },
            ]

        sync = fetch_zotero_records(max_records=10, request_json=fake_request)

        self.assertEqual(len(calls), 1)
        self.assertEqual(sync.records_found, 4)
        self.assertEqual(sync.records_returned, 3)
        self.assertEqual(sync.high_water_mark, "89")
        item = sync.records[0].to_source_account_record()
        self.assertEqual(item["external_id"], "zotero:user:0:item:ITEM1234")
        self.assertEqual(item["source_url"], "zotero://select/library/items/ITEM1234")
        self.assertIn("Alternate URL: https://www.zotero.org/users/0/items/ITEM1234", item["content"])
        self.assertIn("Abstract:", item["content"])
        self.assertEqual(item["metadata"]["record_kind"], "item")
        note = sync.records[1].to_source_account_record()
        self.assertIn("Note:", note["content"])
        self.assertIn("Zotero notes should become one source-account record.", note["content"])
        annotation = sync.records[2].to_source_account_record()
        self.assertIn("Annotation:", annotation["content"])
        self.assertIn("Text: Annotations should preserve stable Zotero citations.", annotation["content"])
        self.assertNotIn("PDF1234", "\n".join(record.content for record in sync.records))

    def test_fetch_zotero_records_supports_web_token_group_library_and_cursor(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            self.assertEqual(parsed.scheme, "https")
            self.assertEqual(parsed.netloc, "api.zotero.org")
            self.assertEqual(parsed.path, "/groups/12345/items")
            self.assertEqual(query["since"], ["77"])
            self.assertEqual(query["start"], ["5"])
            self.assertEqual(headers["Zotero-API-Key"], "zotero-token")
            return [
                {
                    "key": "GRP12345",
                    "version": 78,
                    "data": {
                        "key": "GRP12345",
                        "itemType": "book",
                        "title": "Group research",
                        "dateModified": "2026-06-30T10:00:00Z",
                    },
                }
            ]

        sync = fetch_zotero_records(
            token="zotero-token",
            library_type="group",
            library_id="12345",
            since="77",
            cursor="5",
            max_records=1,
            api_base_url="https://api.zotero.org",
            request_json=fake_request,
        )

        self.assertEqual(sync.records_returned, 1)
        self.assertEqual(sync.next_cursor, "6")
        record = sync.records[0].to_source_account_record()
        self.assertEqual(record["source_url"], "zotero://select/groups/12345/items/GRP12345")

    def test_fetch_zotero_records_rejects_invalid_since(self) -> None:
        with self.assertRaisesRegex(ValueError, "since"):
            fetch_zotero_records(since="2026-06-30T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
