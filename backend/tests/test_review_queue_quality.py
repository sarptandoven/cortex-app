"""The Review queue must hold only human-reviewable content.

Machine artifacts (ffmpeg concat lists, path dumps) that extract zero memories are
auto-archived at ingest, and a retroactive maintenance sweep applies the same gate to
vaults populated before the gate existed. The mirror insight ("CORTEX NOTICED") must
never quote prompt scaffolding or path debris as an observation about the user.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.extractor import extract_context
from backend.app.mirror import _is_junk_example, _shorten
from backend.app.storage import CortexStore, connect

CONCAT = (
    "file '/Users/u/Documents/Codex/outputs/972d89aa1cce4b53ae34cb42d83d318c/videos/scene_mux/0001_scene_1.mp4'\n"
    "file '/Users/u/Documents/Codex/outputs/972d89aa1cce4b53ae34cb42d83d318c/videos/scene_mux/0002_scene_2.mp4'"
)
NOTE = "We decided to use PostgreSQL for Atlas because of jsonb support."


class ReviewQueueQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        init_db(root / "t.db")
        self.store = CortexStore(root / "t.db", root / "vault")
        self.store._vector_ready = lambda conn: False

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _save(self, content: str, title: str) -> dict:
        extracted = extract_context(content, source="obsidian", extraction_mode="local")
        return self.store.save_capture(
            user_id="u",
            content=content,
            source="obsidian",
            source_url=f"file:///notes/{title}.txt",
            title=title,
            extracted=extracted,
        )

    def test_machine_artifact_capture_is_auto_archived_at_ingest(self) -> None:
        self._save(CONCAT, "concat_scene_mux")
        self._save(NOTE, "atlas-decision")
        pending = self.store.daily_review("u").get("pending") or []
        titles = [p.get("title") for p in pending]
        self.assertEqual(titles, ["atlas-decision"], "concat noise must not reach Review")
        with connect(self.store.db_path) as conn:
            row = conn.execute(
                "SELECT review_status, archived_at FROM captures WHERE user_id='u' AND title='concat_scene_mux'"
            ).fetchone()
        self.assertEqual(row["review_status"], "archived")
        self.assertTrue(row["archived_at"], "auto-archive must stamp archived_at")

    def test_auto_archive_is_audited(self) -> None:
        result = self._save(CONCAT, "concat_scene_mux")
        with connect(self.store.db_path) as conn:
            event = conn.execute(
                "SELECT metadata_json FROM memory_events WHERE user_id='u' AND object_id=? AND event_type='auto_archived'",
                (result["capture_id"],),
            ).fetchone()
        self.assertIsNotNone(event, "auto-archive must leave an audit event")

    def test_prose_with_memories_is_never_auto_archived(self) -> None:
        result = self._save(NOTE, "atlas-decision")
        self.assertGreater(len(result.get("memories") or []), 0)
        pending = self.store.daily_review("u").get("pending") or []
        self.assertEqual(len(pending), 1)

    def test_retroactive_sweep_archives_preexisting_junk(self) -> None:
        # Simulate a pre-gate vault: force the junk capture in as pending with junk memories.
        result = self._save(CONCAT, "concat_scene_mux")
        with connect(self.store.db_path) as conn:
            conn.execute(
                "UPDATE captures SET review_status='pending', archived_at=NULL WHERE user_id='u' AND id=?",
                (result["capture_id"],),
            )
            conn.execute(
                """
                INSERT INTO memories (id, capture_id, user_id, kind, layer, content, source, captured_at, status)
                VALUES ('mem_junk1', ?, 'u', 'event', 'episodic', ?, 'obsidian', '2026-01-01T00:00:00+00:00', 'active')
                """,
                (result["capture_id"], CONCAT.splitlines()[0]),
            )
        swept = self.store.archive_machine_artifact_captures("u")
        self.assertEqual(swept["archived"], 1)
        self.assertEqual(swept["archived_memories"], 1)
        pending = self.store.daily_review("u").get("pending") or []
        self.assertEqual(pending, [])
        # Idempotent: a second sweep finds nothing.
        self.assertEqual(self.store.archive_machine_artifact_captures("u")["archived"], 0)

    def test_sweep_never_touches_approved_captures(self) -> None:
        result = self._save(CONCAT, "concat_scene_mux")
        with connect(self.store.db_path) as conn:
            conn.execute(
                "UPDATE captures SET review_status='approved', archived_at=NULL WHERE user_id='u' AND id=?",
                (result["capture_id"],),
            )
        swept = self.store.archive_machine_artifact_captures("u")
        self.assertEqual(swept["archived"], 0, "a user-approved capture must never be reclassified")

    def test_repair_storage_runs_the_sweep(self) -> None:
        report = self.store.repair_storage("u")
        names = [action["name"] for action in report["actions"]]
        self.assertIn("archive_machine_artifact_captures", names)


class MirrorJunkGateTests(unittest.TestCase):
    def test_instruction_fragments_are_junk(self) -> None:
        self.assertTrue(_is_junk_example("Then, please answer these questions: 1"))
        self.assertTrue(_is_junk_example("Your task is to summarize the following"))

    def test_path_dumps_are_junk(self) -> None:
        self.assertTrue(_is_junk_example("file /a/b/c/d/e.mp4 file /a/b/c/d/f.mp4"))

    def test_real_observations_are_kept(self) -> None:
        self.assertFalse(_is_junk_example("You prefer short factual answers with citations"))
        self.assertFalse(_is_junk_example("Prefers tabs over spaces in Python projects"))

    def test_shorten_renders_escaped_newlines_as_spaces(self) -> None:
        self.assertNotIn("\\n", _shorten(r"Then, please answer:\n 1. First question"))


if __name__ == "__main__":
    unittest.main()


class SourceReadinessDisplayTests(unittest.TestCase):
    """The source chips/rows must show counts and names a user can trust."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        init_db(root / "t.db")
        self.store = CortexStore(root / "t.db", root / "vault")
        self.store._vector_ready = lambda conn: False

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_pending_counts_captures_not_join_rows(self) -> None:
        # One pending capture with several extracted memories must count as ONE
        # pending item. The old SUM over the memories JOIN multiplied it (a
        # 1,470-capture queue displayed as "13,249 pending").
        text = "\n".join(
            f"We decided module {i} ships on Tuesday, agreed by the team." for i in range(6)
        )
        extracted = extract_context(text, source="obsidian", extraction_mode="local")
        result = self.store.save_capture(
            user_id="u", content=text, source="obsidian",
            source_url="file:///n.md", title="n", extracted=extracted,
        )
        self.assertGreater(len(result.get("memories") or []), 1, "test needs a multi-memory capture")
        report = self.store.source_readiness_report("u")
        obsidian = next(s for s in report["sources"] if s["source"] == "obsidian")
        self.assertEqual(obsidian["pending"], 1)
        self.assertEqual(obsidian["captures"], 1)

    def test_off_catalog_source_names_are_humanized(self) -> None:
        extracted = extract_context(
            "A decision was made to keep the export simple.",
            source="structured-export", extraction_mode="local",
        )
        self.store.save_capture(
            user_id="u", content="A decision was made to keep the export simple.",
            source="structured-export", source_url=None, title=None, extracted=extracted,
        )
        report = self.store.source_readiness_report("u")
        item = next(s for s in report["sources"] if s["source"] == "structured-export")
        self.assertEqual(item["name"], "Structured Export")
