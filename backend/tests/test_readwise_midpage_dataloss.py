from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from backend.app.connectors.readwise import fetch_readwise_records


def _book(book_id: int, highlight_ids: list[int]) -> dict:
    return {
        "user_book_id": book_id,
        "title": f"Book {book_id}",
        "category": "books",
        "updated": "2026-06-30T11:00:00Z",
        "highlights": [
            {
                "id": highlight_id,
                "text": f"Highlight text {highlight_id}",
                "highlighted_at": "2026-06-29T10:00:00Z",
                "updated": "2026-06-30T10:30:00Z",
            }
            for highlight_id in highlight_ids
        ],
    }


class ReadwiseMidPageDataLossTests(unittest.TestCase):
    def test_cap_hit_mid_page_consumes_whole_page_and_does_not_skip_highlights(self) -> None:
        # A single page carries more highlights than max_records. Before the fix
        # the inner loop broke at the cap (dropping the tail of the page) and then
        # advanced next_page_cursor to the following page, so those un-consumed
        # highlights were permanently lost on the next sync. The page must instead
        # be fully consumed (overshooting the cap) before the cursor advances.
        page_one = _book(111, list(range(1, 601)))  # 600 highlights on page one.
        calls: list[tuple[str, dict[str, str]]] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append((url, headers))
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            if len(calls) == 1:
                # First fetch uses the `since` filter (no cursor yet).
                self.assertNotIn("pageCursor", query)
                return {"nextPageCursor": "cursor-page-2", "results": [page_one]}
            self.fail(f"unexpected extra fetch: {query}")

        sync = fetch_readwise_records(
            token="readwise-test",
            since="2026-06-01T00:00:00Z",
            max_records=500,
            request_json=fake_request,
        )

        # Only the first page is fetched (cap reached), and every highlight on it
        # is captured -- overshooting the 500 cap rather than truncating at 500.
        self.assertEqual(len(calls), 1)
        self.assertEqual(sync.records_found, 600)
        self.assertEqual(sync.records_returned, 600)
        external_ids = {record.external_id for record in sync.records}
        self.assertEqual(len(external_ids), 600)
        # No highlight from the page is missing (this is what regressed before).
        for highlight_id in range(1, 601):
            self.assertIn(f"readwise:highlight:{highlight_id}", external_ids)

    def test_cap_hit_between_books_consumes_whole_page(self) -> None:
        # The cap boundary can also land between books within a page. Before the
        # fix the outer break skipped the remaining book(s) on the page while the
        # cursor still advanced -- another silent mid-page loss.
        page_one = [
            _book(111, list(range(1, 401))),   # 400 highlights -> crosses the cap
            _book(222, list(range(401, 701))),  # + 300 more on the SAME page
        ]
        calls: list[str] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append(url)
            if len(calls) == 1:
                return {"nextPageCursor": "cursor-page-2", "results": page_one}
            self.fail("second book on the current page must not trigger a new fetch")

        sync = fetch_readwise_records(
            token="readwise-test",
            since="2026-06-01T00:00:00Z",
            max_records=500,
            request_json=fake_request,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(sync.records_returned, 700)
        external_ids = {record.external_id for record in sync.records}
        # The second book on the page must be fully captured, not skipped.
        self.assertIn("readwise:highlight:401", external_ids)
        self.assertIn("readwise:highlight:700", external_ids)

    def test_resume_after_capped_page_refetches_from_advanced_cursor_without_gap(self) -> None:
        # After a fully-consumed capped page, resuming at next_page_cursor is safe:
        # the cursor points to the next page and no current-page highlights were
        # skipped, so a subsequent sync loses nothing. The cursor advance is only
        # correct because the whole page was consumed.
        page_one = _book(111, list(range(1, 601)))
        page_two = _book(222, list(range(601, 610)))
        calls: list[tuple[str, dict]] = []

        def fake_request(url: str, headers: dict[str, str]):
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            calls.append((url, query))
            if "pageCursor" not in query:
                return {"nextPageCursor": "cursor-page-2", "results": [page_one]}
            self.assertEqual(query["pageCursor"], ["cursor-page-2"])
            return {"nextPageCursor": None, "results": [page_two]}

        first = fetch_readwise_records(
            token="readwise-test",
            since="2026-06-01T00:00:00Z",
            max_records=500,
            request_json=fake_request,
        )
        # First sync stops after the capped page and hands back the next cursor.
        self.assertEqual(first.next_page_cursor, "cursor-page-2")
        self.assertEqual(first.records_returned, 600)

        # A subsequent sync resumes from the advanced cursor. `since` is dropped
        # while a page cursor is set, but nothing was skipped on page one so the
        # union of both syncs covers every highlight with no gap.
        second = fetch_readwise_records(
            token="readwise-test",
            since="2026-06-01T00:00:00Z",
            page_cursor=first.next_page_cursor,
            max_records=500,
            request_json=fake_request,
        )
        second_ids = {record.external_id for record in second.records}
        first_ids = {record.external_id for record in first.records}
        for highlight_id in range(1, 610):
            self.assertTrue(
                f"readwise:highlight:{highlight_id}" in first_ids
                or f"readwise:highlight:{highlight_id}" in second_ids,
                msg=f"highlight {highlight_id} was lost across the two syncs",
            )


if __name__ == "__main__":
    unittest.main()
