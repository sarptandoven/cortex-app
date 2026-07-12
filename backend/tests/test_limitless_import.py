"""Tests for the Limitless / Rewind lifelog importer.

Covers the realistic export shapes a "Rewind refugee" arrives with — the
Developer-API lifelogs envelope, a bare list, JSONL, a single lifelog object, a
markdown/text transcript dump, and a .zip of any of those — plus the robustness
guarantees (skip a malformed lifelog, tolerate an empty export, never misclaim
an unrelated JSON) and the import auto-detection.
"""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from backend.app import source_ingest
from backend.app.source_ingest import (
    SourceAsset,
    analyze_sources,
    import_source_records,
    scan_export_candidates,
)


def _asset(name: str, text: str, *, display_path: str | None = None) -> SourceAsset:
    return SourceAsset(name=name, display_path=display_path or name, data=text.encode("utf-8"))


def _lifelog(title: str, day: str, turns: list[tuple[str, str, str]]) -> dict:
    """Build a lifelog with `contents` shaped like the real Limitless API."""
    contents = [{"type": "heading1", "content": title, "startTime": f"{day}T09:00:00Z"}]
    for speaker, identifier, text in turns:
        contents.append(
            {
                "type": "blockquote",
                "content": text,
                "speakerName": speaker,
                "speakerIdentifier": identifier,
                "startTime": f"{day}T09:05:00Z",
                "endTime": f"{day}T09:06:00Z",
                "startOffsetMs": 300000,
                "children": [],
            }
        )
    return {
        "id": f"lifelog-{day}",
        "title": title,
        "markdown": f"# {title}\n\n> {turns[0][2] if turns else ''}",
        "startTime": f"{day}T09:00:00Z",
        "endTime": f"{day}T10:30:00Z",
        "isStarred": False,
        "updatedAt": f"{day}T11:00:00Z",
        "contents": contents,
    }


class LimitlessJsonShapeTests(unittest.TestCase):
    def test_api_envelope_parses_each_lifelog(self) -> None:
        payload = {
            "data": {
                "lifelogs": [
                    _lifelog(
                        "Standup",
                        "2026-07-01",
                        [
                            ("You", "user", "We ship the importer today."),
                            ("Maya", None, "I'll take the landing page copy."),
                        ],
                    ),
                    _lifelog(
                        "Coffee chat",
                        "2026-07-02",
                        [("You", "user", "Rewind got shut down by Meta.")],
                    ),
                ]
            },
            "meta": {"lifelogs": {"nextCursor": None, "count": 2}},
        }
        asset = _asset("limitless/lifelogs.json", json.dumps(payload))
        records = source_ingest._parse_limitless([asset], "")
        self.assertEqual(len(records), 2)
        self.assertTrue(all(r.source == "limitless" for r in records))
        first = records[0]
        self.assertEqual(first.title, "Standup - 2026-07-01")
        self.assertIn("--- Transcript ---", first.content)
        self.assertIn("You: We ship the importer today.", first.content)
        self.assertIn("Maya: I'll take the landing page copy.", first.content)
        # Timestamps are surfaced on the transcript lines.
        self.assertIn("2026-07-01T09:05:00Z You:", first.content)
        # source_url is a locatable citation back to the lifelog.
        self.assertIn("service=limitless", first.source_url or "")
        self.assertIn("lifelog_id=lifelog-2026-07-01", first.source_url or "")

    def test_bare_list_of_lifelogs(self) -> None:
        payload = [
            _lifelog("Day one", "2026-06-01", [("You", "user", "Hello world.")]),
            _lifelog("Day two", "2026-06-02", [("Alex", None, "Nice to meet you.")]),
        ]
        asset = _asset("rewind-export/export.json", json.dumps(payload))
        records = source_ingest._parse_limitless([asset], "")
        self.assertEqual(len(records), 2)
        self.assertIn("Hello world.", records[0].content)

    def test_single_lifelog_object(self) -> None:
        payload = _lifelog("Solo note", "2026-05-05", [("You", "user", "Just thinking out loud.")])
        asset = _asset("limitless_lifelog.json", json.dumps(payload))
        records = source_ingest._parse_limitless([asset], "")
        self.assertEqual(len(records), 1)
        self.assertIn("Just thinking out loud.", records[0].content)

    def test_jsonl_one_lifelog_per_line(self) -> None:
        lines = [
            json.dumps(_lifelog("A", "2026-04-01", [("You", "user", "Line one entry.")])),
            json.dumps(_lifelog("B", "2026-04-02", [("You", "user", "Line two entry.")])),
        ]
        asset = _asset("limitless/lifelogs.jsonl", "\n".join(lines))
        records = source_ingest._parse_limitless([asset], "")
        self.assertEqual(len(records), 2)
        self.assertIn("Line one entry.", records[0].content)
        self.assertIn("Line two entry.", records[1].content)

    def test_markdown_only_lifelog_falls_back_to_markdown(self) -> None:
        # Older/partial exports carry only the rendered `markdown`, no `contents`.
        payload = {
            "id": "x1",
            "title": "Rendered day",
            "startTime": "2026-03-03T08:00:00Z",
            "markdown": "# Rendered day\n\n> You: fallback transcript text here",
        }
        asset = _asset("limitless/export.json", json.dumps(payload))
        records = source_ingest._parse_limitless([asset], "")
        self.assertEqual(len(records), 1)
        self.assertIn("fallback transcript text here", records[0].content)


class LimitlessTextDumpTests(unittest.TestCase):
    def test_markdown_transcript_dump(self) -> None:
        text = (
            "# Lifelog 2026-02-14\n\n"
            "## Morning sync\n"
            "> You: Let's talk roadmap.\n"
            "> Priya: Sounds good, I have notes.\n"
        )
        asset = _asset("Limitless/2026-02-14.md", text)
        records = source_ingest._parse_limitless([asset], "")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source, "limitless")
        self.assertIn("2026-02-14", records[0].title)
        self.assertIn("Let's talk roadmap.", records[0].content)

    def test_plain_text_transcript_dump_with_path_marker(self) -> None:
        asset = _asset(
            "rewind/session.txt",
            "You: remembering the meeting\nThem: yes exactly\n",
            display_path="/exports/rewind/session.txt",
        )
        records = source_ingest._parse_limitless([asset], "")
        self.assertEqual(len(records), 1)
        self.assertIn("remembering the meeting", records[0].content)


class LimitlessDetectionTests(unittest.TestCase):
    def test_content_sniff_recognizes_unnamed_export(self) -> None:
        # No `limitless`/`rewind` in the filename or path — only the lifelog
        # fingerprint. It must still be recognized.
        payload = {"data": {"lifelogs": [_lifelog("Sniff", "2026-01-01", [("You", "user", "sniffed content")])]}}
        asset = _asset("data/export-2026.json", json.dumps(payload), display_path="/tmp/data/export-2026.json")
        records = source_ingest._parse_limitless([asset], "")
        self.assertEqual(len(records), 1)
        self.assertIn("sniffed content", records[0].content)

    def test_unrelated_json_is_not_misclaimed(self) -> None:
        # A generic chat/config JSON with no lifelog fingerprint must be left for
        # other parsers, not swallowed by Limitless.
        payload = {"messages": [{"role": "user", "content": "hi"}], "settings": {"theme": "dark"}}
        asset = _asset("config.json", json.dumps(payload), display_path="/tmp/config.json")
        self.assertFalse(source_ingest._looks_like_limitless_asset(asset, ""))
        self.assertEqual(source_ingest._parse_limitless([asset], ""), [])

    def test_hint_forces_recognition(self) -> None:
        payload = [_lifelog("Hinted", "2026-01-02", [("You", "user", "hinted content")])]
        asset = _asset("whatever.json", json.dumps(payload), display_path="/tmp/whatever.json")
        records = source_ingest._parse_limitless([asset], "limitless")
        self.assertEqual(len(records), 1)


class LimitlessRobustnessTests(unittest.TestCase):
    def test_malformed_lifelog_is_skipped_not_fatal(self) -> None:
        payload = {
            "data": {
                "lifelogs": [
                    "not-a-dict",  # garbage entry
                    {"id": "empty"},  # no transcript, no markdown -> dropped
                    _lifelog("Good", "2026-07-09", [("You", "user", "survived the bad entries")]),
                ]
            }
        }
        asset = _asset("limitless/lifelogs.json", json.dumps(payload))
        records = source_ingest._parse_limitless([asset], "")
        self.assertEqual(len(records), 1)
        self.assertIn("survived the bad entries", records[0].content)

    def test_corrupt_jsonl_line_does_not_discard_valid_ones(self) -> None:
        lines = [
            json.dumps(_lifelog("Ok1", "2026-07-01", [("You", "user", "valid one")])),
            "{ this is not valid json",
            json.dumps(_lifelog("Ok2", "2026-07-02", [("You", "user", "valid two")])),
        ]
        asset = _asset("limitless/lifelogs.jsonl", "\n".join(lines))
        records = source_ingest._parse_limitless([asset], "")
        self.assertEqual(len(records), 2)
        contents = "\n".join(r.content for r in records)
        self.assertIn("valid one", contents)
        self.assertIn("valid two", contents)

    def test_invalid_json_returns_no_records(self) -> None:
        asset = _asset("limitless/lifelogs.json", "{ not valid json at all")
        self.assertEqual(source_ingest._parse_limitless([asset], "limitless"), [])

    def test_empty_export_returns_no_records(self) -> None:
        empty_envelope = _asset("limitless/lifelogs.json", json.dumps({"data": {"lifelogs": []}}))
        empty_text = _asset("limitless/day.md", "   \n\n   ")
        self.assertEqual(source_ingest._parse_limitless([empty_envelope], ""), [])
        self.assertEqual(source_ingest._parse_limitless([empty_text], ""), [])


class LimitlessEndToEndImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_export(self) -> Path:
        export_dir = self.root / "Limitless Export"
        export_dir.mkdir()
        payload = {
            "data": {
                "lifelogs": [
                    _lifelog("Standup", "2026-07-01", [("You", "user", "importer ships today")]),
                    _lifelog("Retro", "2026-07-02", [("You", "user", "went well overall")]),
                ]
            }
        }
        (export_dir / "lifelogs.json").write_text(json.dumps(payload), encoding="utf-8")
        return export_dir

    def test_folder_import_produces_limitless_records(self) -> None:
        export_dir = self._write_export()
        records = import_source_records([str(export_dir)], max_records=100)
        limitless = [r for r in records if r.source == "limitless"]
        self.assertEqual(len(limitless), 2)
        self.assertIn("importer ships today", "\n".join(r.content for r in limitless))

    def test_zip_export_is_imported(self) -> None:
        export_dir = self._write_export()
        zip_path = self.root / "limitless-export.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.write(export_dir / "lifelogs.json", "Limitless Export/lifelogs.json")
        records = import_source_records([str(zip_path)], max_records=100)
        limitless = [r for r in records if r.source == "limitless"]
        self.assertEqual(len(limitless), 2)

    def test_analyze_sources_reports_limitless(self) -> None:
        export_dir = self._write_export()
        analysis = analyze_sources([str(export_dir)])
        sources = {s["source"]: s["count"] for s in analysis["sources"]}
        self.assertEqual(sources.get("limitless"), 2)
        self.assertTrue(any(s["id"] == "limitless" for s in analysis["supported_sources"]))

    def test_scan_detects_limitless_export_folder(self) -> None:
        # A dropped, already-unzipped export folder named "Limitless ..." with a
        # lifelogs.json is surfaced by the Downloads scanner.
        self._write_export()
        found = scan_export_candidates([str(self.root)])
        limitless = [c for c in found if c["service"] == "limitless"]
        self.assertEqual(len(limitless), 1)
        self.assertGreaterEqual(limitless[0]["records_found"], 2)


if __name__ == "__main__":
    unittest.main()
