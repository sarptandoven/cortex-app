"""Import-scale bounds: caps are batch sizes, never silent data loss.

Regression tests for the "Deferred" table in docs/PIPELINE_SILENT_LOSS_AUDIT.md:
per-parser item caps used to silently drop everything past the cap (tweets past
1000, mbox mail past 500, whole files > 12MB read as empty). The sanctioned fix
chunks the overflow into additional records — `(part i/n)` siblings with chunk
metadata — and surfaces truncated reads as `source_file_truncated`.
"""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from backend.app.source_ingest import (
    MAX_TEXT_BYTES,
    SourceAsset,
    _assets_from_zip,
    _format_csv_export,
    _parse_mbox,
    _parse_single_asset,
    _parse_twitter_archive,
    import_source_records,
)


class OversizedFileTruncationTests(unittest.TestCase):
    """Whole-file loss: > MAX_TEXT_BYTES used to read as b"" (nothing imported)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_oversized_markdown(self) -> Path:
        folder = self.root / "docs"
        folder.mkdir()
        path = folder / "big-notes.md"
        line = "durable memory filler line for the oversized import fixture\n"
        repeats = (MAX_TEXT_BYTES // len(line)) + 10
        path.write_text(line * repeats, encoding="utf-8")
        self.assertGreater(path.stat().st_size, MAX_TEXT_BYTES)
        return path

    def test_read_bytes_returns_first_window_and_flags_truncation(self) -> None:
        path = self._write_oversized_markdown()
        asset = SourceAsset(name=path.name, display_path=str(path), filesystem_path=path)

        data = asset.read_bytes()

        self.assertEqual(len(data), MAX_TEXT_BYTES)
        self.assertTrue(asset.read_truncated)
        # The window is real file content, not empty/garbage.
        self.assertTrue(data.startswith(b"durable memory filler line"))

    def test_small_file_read_is_not_flagged(self) -> None:
        path = self.root / "small.md"
        path.write_text("small note", encoding="utf-8")
        asset = SourceAsset(name=path.name, display_path=str(path), filesystem_path=path)

        self.assertEqual(asset.read_bytes(), b"small note")
        self.assertFalse(asset.read_truncated)

    def test_import_marks_records_from_truncated_files(self) -> None:
        self._write_oversized_markdown()

        records = import_source_records([str(self.root / "docs")], max_records=10)

        self.assertTrue(records)
        for record in records:
            self.assertIs(record.metadata.get("source_file_truncated"), True)
        # And the content that was read survives into the record.
        self.assertIn("durable memory filler line", records[0].content)

    def test_zip_member_past_cap_is_truncated_not_skipped(self) -> None:
        zip_path = self.root / "export.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("notes/huge.md", "m" * (MAX_TEXT_BYTES + 100))
            archive.writestr("notes/tiny.md", "tiny note")

        assets = {asset.name: asset for asset in _assets_from_zip(zip_path)}

        self.assertIn("notes/huge.md", assets)
        huge = assets["notes/huge.md"]
        self.assertTrue(huge.read_truncated)
        self.assertEqual(len(huge.read_bytes()), MAX_TEXT_BYTES)
        tiny = assets["notes/tiny.md"]
        self.assertFalse(tiny.read_truncated)
        self.assertEqual(tiny.read_bytes(), b"tiny note")


class TwitterBatchingTests(unittest.TestCase):
    """The 1000-tweet cap is now a batch size: one record per 1000 tweets."""

    @staticmethod
    def _tweets_asset(count: int) -> SourceAsset:
        tweets = [
            {
                "tweet": {
                    "created_at": "Mon Jun 29 10:00:00 +0000 2026",
                    "full_text": f"tweet-marker-{index} public writing sample.",
                    "favorite_count": "1",
                }
            }
            for index in range(count)
        ]
        body = "window.YTD.tweets.part0 = " + json.dumps(tweets)
        return SourceAsset(
            name="data/tweets.js",
            display_path="/exports/twitter-archive.zip::data/tweets.js",
            data=body.encode("utf-8"),
        )

    def test_2500_tweets_become_three_labeled_parts(self) -> None:
        records = _parse_twitter_archive([self._tweets_asset(2500)], "twitter-x")

        self.assertEqual(len(records), 3)
        self.assertEqual(
            [record.title for record in records],
            [
                "Twitter/X tweets (part 1/3)",
                "Twitter/X tweets (part 2/3)",
                "Twitter/X tweets (part 3/3)",
            ],
        )
        self.assertEqual([record.metadata.get("chunk_index") for record in records], [1, 2, 3])
        self.assertEqual({record.metadata.get("chunk_count") for record in records}, {3})

        # Every tweet survives, in order across parts — nothing dropped.
        combined = "\n".join(record.content for record in records)
        for index in range(2500):
            self.assertIn(f"tweet-marker-{index} ", combined)
        self.assertIn("tweet-marker-0 ", records[0].content)
        self.assertIn("tweet-marker-999 ", records[0].content)
        self.assertNotIn("tweet-marker-1000 ", records[0].content)
        self.assertIn("tweet-marker-2499 ", records[2].content)

        # Each part gets its own locator.
        urls = [record.source_url for record in records]
        self.assertEqual(len(set(urls)), 3)
        for index, url in enumerate(urls, start=1):
            self.assertIn(f"chunk={index}", url or "")

    def test_small_archive_is_byte_identical_single_record(self) -> None:
        records = _parse_twitter_archive([self._tweets_asset(100)], "twitter-x")

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.title, "Twitter/X tweets")
        self.assertNotIn("part", record.title)
        self.assertNotIn("chunk_index", record.metadata)
        self.assertNotIn("chunk_count", record.metadata)
        self.assertNotIn("chunk=", record.source_url or "")
        for index in range(100):
            self.assertIn(f"tweet-marker-{index} ", record.content)


class CsvExportBatchingTests(unittest.TestCase):
    """Structured CSV exports: rows past 1000 batch into new parts, all kept."""

    @staticmethod
    def _issues_asset(count: int) -> tuple[SourceAsset, str]:
        text = "Title,Body\n" + "".join(
            f"Issue {index},row-marker-{index} needs triage\n" for index in range(1, count + 1)
        )
        asset = SourceAsset(
            name="issues.csv",
            display_path="/exports/github/issues.csv",
            data=text.encode("utf-8"),
        )
        return asset, text

    def test_formatter_keeps_every_row_across_batches(self) -> None:
        asset, text = self._issues_asset(2350)

        texts = _format_csv_export(asset, text, "github")

        self.assertEqual(len(texts), 3)
        combined = "\n".join(texts)
        for index in range(1, 2351):
            self.assertIn(f"row-marker-{index} ", combined)
        # Row numbering is global across parts.
        self.assertIn("Row 1\n", texts[0])
        self.assertIn("Row 1001\n", texts[1])
        self.assertIn("Row 2350\n", texts[2])

    def test_records_are_labeled_parts_with_chunk_metadata(self) -> None:
        asset, _ = self._issues_asset(2350)

        records = _parse_single_asset(asset, "")

        self.assertEqual(len(records), 3)
        self.assertEqual([record.source for record in records], ["github"] * 3)
        self.assertEqual(
            [record.title for record in records],
            ["issues (part 1/3)", "issues (part 2/3)", "issues (part 3/3)"],
        )
        self.assertEqual([record.metadata.get("chunk_index") for record in records], [1, 2, 3])
        self.assertEqual({record.metadata.get("chunk_count") for record in records}, {3})
        combined = "\n".join(record.content for record in records)
        for index in range(1, 2351):
            self.assertIn(f"row-marker-{index} ", combined)

    def test_small_csv_is_single_unlabeled_record(self) -> None:
        asset, _ = self._issues_asset(5)

        records = _parse_single_asset(asset, "")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "issues")
        self.assertNotIn("chunk_index", records[0].metadata)


class MboxCapRaiseTests(unittest.TestCase):
    """mbox: 600 messages used to lose 100 past the 500 cap; now all come through."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_600_message_mbox_yields_600_records(self) -> None:
        path = self.root / "mail.mbox"
        chunks = []
        for index in range(600):
            chunks.append(
                "From alex@example.com Thu Jul  2 10:00:00 2026\n"
                f"Subject: Scale message {index}\n"
                "From: alex@example.com\n"
                "To: sarpt@example.com\n"
                "Date: Thu, 02 Jul 2026 10:00:00 +0000\n"
                "\n"
                f"Scale mbox body marker {index}.\n"
                "\n"
            )
        path.write_text("".join(chunks), encoding="utf-8")
        asset = SourceAsset(name=path.name, display_path=str(path), filesystem_path=path)

        records = _parse_mbox(asset, "")

        self.assertEqual(len(records), 600)
        titles = {record.title for record in records}
        self.assertIn("Scale message 0", titles)
        self.assertIn("Scale message 599", titles)
        combined = "\n".join(record.content for record in records)
        self.assertIn("Scale mbox body marker 599", combined)


if __name__ == "__main__":
    unittest.main()
