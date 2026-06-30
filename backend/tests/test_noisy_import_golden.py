from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.storage import CortexStore


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "noisy_imports"


class NoisyImportGoldenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "golden.sqlite"
        self.vault_path = self.root / "Cortex.vault"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.vault_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_noisy_import_fixture_matches_manifest(self) -> None:
        manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.store.update_settings("test-user", {"identity_aliases": manifest["identity_aliases"]})
        result = self.store.import_sources(
            user_id="test-user",
            paths=[str(FIXTURE_ROOT / path) for path in manifest["paths"]],
            processing="sync",
            max_records=40,
        )

        self.assertEqual(result["failed"], 0)
        self.assertGreater(result["saved"], 0)

        rows = self._memory_rows()
        counts_by_source: dict[str, int] = {}
        for row in rows:
            counts_by_source[row["source"]] = counts_by_source.get(row["source"], 0) + 1
        for source, minimum in manifest.get("minimum_memories_by_source", {}).items():
            with self.subTest(minimum_memories_by_source=source):
                self.assertGreaterEqual(counts_by_source.get(source, 0), minimum)

        joined_content = "\n".join(row["content"] for row in rows)
        joined_excerpt = "\n".join(row["raw_excerpt"] or "" for row in rows)
        for phrase in manifest["rejected_phrases"]:
            self.assertNotIn(phrase, joined_content)
            self.assertNotIn(phrase, joined_excerpt)
        for phrase in manifest["boilerplate_phrases"]:
            self.assertNotIn(phrase, joined_content)
            self.assertNotIn(phrase, joined_excerpt)
        for guard in manifest.get("external_speaker_rejected_personal_signals", []):
            with self.subTest(external_speaker_rejected_personal_signal=guard):
                leaked = [
                    dict(row)
                    for row in rows
                    if row["source"] == guard["source"]
                    and row["layer"] == guard["layer"]
                    and guard["phrase"] in row["content"]
                ]
                self.assertEqual([], leaked)

        original_vector_ready = self.store._vector_ready
        self.store._vector_ready = lambda conn: False
        try:
            for rejected in manifest.get("rejected_queries", []):
                with self.subTest(rejected_query=rejected["query"]):
                    rejected_hits = self.store.search("test-user", rejected["query"], limit=3)
                    self.assertEqual([], rejected_hits)
                    must_not_include = rejected.get("must_not_include")
                    if must_not_include:
                        self.assertNotIn(must_not_include, "\n".join(hit["content"] for hit in rejected_hits))
        finally:
            self.store._vector_ready = original_vector_ready

        for expected in manifest["expected"]:
            with self.subTest(expected=expected["name"]):
                hits = [
                    hit
                    for hit in self.store.search("test-user", expected["query"], limit=8)
                    if hit["source"] == expected["source"]
                    and hit["layer"] == expected["layer"]
                    and hit["kind"] == expected["kind"]
                    and expected["must_include"] in hit["content"]
                ]
                self.assertTrue(hits)
                top = hits[0]
                if expected.get("occurred_at"):
                    self.assertEqual(top["occurred_at"], expected["occurred_at"])
                self.assertTrue(top["source_url"])
                for fragment in expected["source_url_contains"]:
                    self.assertIn(fragment, top["source_url"])
                self.assertTrue(top["raw_excerpt"])

        sanitization = manifest.get("local_file_citation_sanitization")
        if sanitization:
            answer = self.store.answer_query("test-user", sanitization["query"], limit=3)
            context = self.store.context_pack("test-user", query=sanitization["query"], limit=3)
            combined = json.dumps(answer, sort_keys=True) + "\n" + context
            for fragment in sanitization["raw_path_fragments"]:
                with self.subTest(local_file_raw_path_fragment=fragment):
                    self.assertNotIn(fragment, combined)
            for fragment in sanitization["sanitized_source_contains"]:
                with self.subTest(local_file_sanitized_source_fragment=fragment):
                    self.assertIn(fragment, combined)

    def _memory_rows(self) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(
                """
                SELECT kind, layer, source, content, source_url, occurred_at, raw_excerpt
                FROM memories
                WHERE user_id = ?
                ORDER BY source, kind, content
                """,
                ("test-user",),
            ).fetchall()
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
