from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.source_ingest import (
    MAX_PARSE_NESTING_DEPTH,
    _collect_browser_bookmark_entries,
    _json_text_content,
    import_source_records,
)


def _nested_json_content_body(depth: int) -> str:
    """Return a raw JSON string for a `body` value nested `depth` levels deep via
    the recursive `content` key that `_json_text_content` descends into. Built as
    a string (not via ``json.dumps``) because the JSON *encoder* would itself hit
    the recursion limit on a structure this deep."""
    return "{\"content\":[" * depth + "{\"text\":\"deep leaf value\"}" + "]}" * depth


def _github_export_json(depth: int) -> str:
    """A GitHub-style issues export: one shallow row plus one row whose `body` is
    nested `depth` levels deep. Routed through `_format_json_export` ->
    `_structured_row_parts` -> `_structured_value_text` -> `_json_text_content`."""
    shallow_row = (
        "{\"number\":1,\"title\":\"Shallow issue\","
        "\"body\":\"shallow marker text\"}"
    )
    deep_row = (
        "{\"number\":2,\"title\":\"Deep issue\","
        "\"body\":" + _nested_json_content_body(depth) + "}"
    )
    return "[" + shallow_row + "," + deep_row + "]"


def _nested_bookmark_folder(depth: int) -> str:
    """Raw JSON for a Chrome/Edge bookmark folder nested `depth` levels deep, with
    a single URL leaf at the bottom. Built as a string for the same reason as
    above."""
    leaf = "{\"type\":\"url\",\"name\":\"Deep link\",\"url\":\"https://deep.example\"}"
    return "{\"type\":\"folder\",\"name\":\"F\",\"children\":[" * depth + leaf + "]}" * depth


def _browser_bookmarks_json(depth: int) -> str:
    """A Chrome/Edge `Bookmarks` file: one shallow URL plus a folder tree nested
    `depth` levels deep. Routed through `_parse_browser_bookmarks_json_asset` ->
    `_collect_browser_bookmark_entries`."""
    shallow = "{\"type\":\"url\",\"name\":\"Shallow bm\",\"url\":\"https://shallow.example\"}"
    deep = _nested_bookmark_folder(depth)
    return (
        "{\"roots\":{\"bookmark_bar\":{\"type\":\"folder\",\"name\":\"Bar\",\"children\":["
        + shallow + "," + deep + "]}}}"
    )


class ParserRecursionLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_deeply_nested_json_export_row_does_not_crash_import(self) -> None:
        # Depth 3000 is well past the decode headroom that would trip the parser
        # traversal without a depth cap (this same input raises RecursionError
        # before the fix), but shallow enough that the file still decodes so we
        # can prove the shallow row is still extracted rather than lost.
        github = self.root / "GitHub" / "Project Cortex"
        github.mkdir(parents=True)
        (github / "issues.json").write_text(_github_export_json(3000), encoding="utf-8")

        # The import path must NOT raise RecursionError.
        records = import_source_records([str(github)], max_records=10)

        self.assertTrue(records)
        self.assertTrue(all(r.source == "github" for r in records))
        content = "\n".join(r.content for r in records)
        self.assertIn("--- Rows ---", content)
        # Shallow/normal content is still extracted despite the pathological row.
        self.assertIn("shallow marker text", content)

    def test_extreme_json_export_nesting_degrades_without_crashing(self) -> None:
        # The task's 5000-level extreme: even when a single crafted row is nested
        # so deeply that the JSON decoder itself gives up, ingestion must return
        # (dropping the bad row) rather than crashing the whole import.
        github = self.root / "GitHub" / "Deep Repo"
        github.mkdir(parents=True)
        (github / "issues.json").write_text(_github_export_json(5000), encoding="utf-8")

        records = import_source_records([str(github)], max_records=10)

        # No RecursionError; the shallow row still survives.
        self.assertTrue(any("shallow marker text" in r.content for r in records))

    def test_deeply_nested_bookmark_tree_does_not_crash_import(self) -> None:
        # Depth 3000 folder tree: raises RecursionError before the fix, decodes
        # (so the shallow bookmark survives) after it.
        browser = self.root / "browser"
        browser.mkdir()
        (browser / "Bookmarks").write_text(_browser_bookmarks_json(3000), encoding="utf-8")

        records = import_source_records([str(browser)], max_records=10)

        by_source = {record.source: record for record in records}
        self.assertIn("browser-bookmarks", by_source)
        # The shallow bookmark is still extracted; the runaway folder is dropped.
        self.assertIn("shallow.example", by_source["browser-bookmarks"].content)

    def test_extreme_bookmark_nesting_degrades_without_crashing(self) -> None:
        # The task's 5000-level extreme for bookmarks: the whole file is one giant
        # nested document, so it may fail to decode, but ingestion must still
        # return without raising RecursionError.
        browser = self.root / "deep-browser"
        browser.mkdir()
        (browser / "Bookmarks").write_text(_browser_bookmarks_json(5000), encoding="utf-8")

        # The assertion here is simply that this returns without raising.
        records = import_source_records([str(browser)], max_records=10)
        self.assertIsInstance(records, list)

    def test_depth_cap_is_a_sane_named_constant(self) -> None:
        # The cap should never trip on real exports (a handful of levels) but must
        # stop runaway nesting; 200 is comfortably between those.
        self.assertGreaterEqual(MAX_PARSE_NESTING_DEPTH, 50)
        self.assertLessEqual(MAX_PARSE_NESTING_DEPTH, 1000)

    def test_parser_helpers_degrade_directly_on_deep_input(self) -> None:
        import json

        # Direct check that the traversals themselves stop rather than overflow.
        deep_content = json.loads(_nested_json_content_body(4000))
        self.assertEqual(_json_text_content(deep_content), "")

        deep_bookmark = json.loads(
            "{\"type\":\"folder\",\"name\":\"F\",\"children\":[" * 4000
            + "{\"type\":\"url\",\"name\":\"Deep\",\"url\":\"https://deep.example\"}"
            + "]}" * 4000
        )
        entries: list[tuple[str, str, str]] = []
        _collect_browser_bookmark_entries("", deep_bookmark, entries)
        self.assertEqual(entries, [])

        # A shallow tree within the cap is still fully collected.
        shallow_bookmark = json.loads(
            "{\"type\":\"folder\",\"name\":\"Bar\",\"children\":["
            "{\"type\":\"url\",\"name\":\"Cortex\",\"url\":\"https://example.com/cortex\"}]}"
        )
        shallow_entries: list[tuple[str, str, str]] = []
        _collect_browser_bookmark_entries("root", shallow_bookmark, shallow_entries)
        self.assertEqual(
            shallow_entries,
            [("root / Bar", "Cortex", "https://example.com/cortex")],
        )


if __name__ == "__main__":
    unittest.main()
