"""Regression test for GitHub issue/PR pagination data loss.

Prior to the fix, ``fetch_github_records`` shrank ``per_page`` on every loop
iteration while paginating by page *number*. GitHub computes the offset as
``(page - 1) * per_page`` from the *current* ``per_page`` value, so after the
first full page the offset math was permanently wrong and every item past the
first 100 was silently skipped.

These tests inject a fake HTTP fetch that emulates GitHub's server-side
offset/pagination semantics and assert that all items are fetched with correct,
non-overlapping offsets.
"""

from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from backend.app.connectors import github


class _FakeGitHub:
    """Emulates GitHub's issues endpoint using true offset pagination.

    GitHub returns items ``[(page - 1) * per_page : page * per_page]`` from the
    full dataset. A client that varies ``per_page`` between requests therefore
    reads overlapping/duplicated windows and never reaches the tail of the list.
    """

    def __init__(self, total_issues: int) -> None:
        # Newest-first ordering, ids 1..total (arbitrary but stable).
        self._issues = [
            {
                "number": i,
                "title": f"Issue {i}",
                "state": "open",
                "html_url": f"https://github.com/octo/repo/issues/{i}",
                "user": {"login": "octocat"},
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "comments": 0,
            }
            for i in range(1, total_issues + 1)
        ]
        self.requested_windows: list[tuple[int, int]] = []

    def __call__(self, url: str, headers: dict[str, str]):
        query = parse_qs(urlparse(url).query)
        per_page = int(query["per_page"][0])
        page = int(query["page"][0])
        start = (page - 1) * per_page
        end = start + per_page
        self.requested_windows.append((start, end))
        return self._issues[start:end]


class GitHubPaginationDataLossTest(unittest.TestCase):
    def test_fetches_all_items_past_first_page(self) -> None:
        fake = _FakeGitHub(total_issues=150)

        sync = github.fetch_github_records(
            token="fake-token",
            repositories=["octo/repo"],
            include_comments=False,
            max_records=150,
            request_json=fake,
        )

        numbers = sorted(int(record.metadata["github_number"]) for record in sync.records)
        # Every issue 1..150 must be present exactly once. Before the fix,
        # items 101..150 were never fetched (only 100 records returned).
        self.assertEqual(len(sync.records), 150)
        self.assertEqual(numbers, list(range(1, 151)))

    def test_requested_windows_are_non_overlapping_and_contiguous(self) -> None:
        fake = _FakeGitHub(total_issues=250)

        github.fetch_github_records(
            token="fake-token",
            repositories=["octo/repo"],
            include_comments=False,
            max_records=250,
            request_json=fake,
        )

        # Fixed page size across all requests.
        per_page_values = {end - start for start, end in fake.requested_windows}
        self.assertEqual(per_page_values, {100})

        # Windows advance by a full page each time: 0-100, 100-200, 200-300...
        starts = [start for start, _ in fake.requested_windows]
        self.assertEqual(starts, [0, 100, 200])
        for previous, current in zip(fake.requested_windows, fake.requested_windows[1:]):
            self.assertEqual(current[0], previous[1])

    def test_stops_at_max_records_without_over_fetching(self) -> None:
        fake = _FakeGitHub(total_issues=500)

        sync = github.fetch_github_records(
            token="fake-token",
            repositories=["octo/repo"],
            include_comments=False,
            max_records=120,
            request_json=fake,
        )

        self.assertEqual(len(sync.records), 120)
        numbers = sorted(int(record.metadata["github_number"]) for record in sync.records)
        self.assertEqual(numbers, list(range(1, 121)))


if __name__ == "__main__":
    unittest.main()
