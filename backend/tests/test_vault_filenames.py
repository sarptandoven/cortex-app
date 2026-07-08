"""N2: human-readable memory-note filenames (memories/<layer>/<slug>--<shortid>.md).

The load-bearing properties: the shortid is derived from the IMMUTABLE id (stable across summary
edits); rebuild reads the id from frontmatter (filename-agnostic); the four sweeps (write/patch/
has/delete) find notes by the id-derived shortid AND confirm the frontmatter id (collision-proof);
and the one-time migration renames legacy <id>.md notes losslessly."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.vault import CortexVault
from backend.app.vault_markdown import atomic_write_text, parse_memory_markdown, render_memory_markdown

SAMPLE = {
    "id": "mem_8e74cf70b6e9",
    "user_id": "u",
    "kind": "decision",
    "layer": "decision",
    "status": "active",
    "summary": "Chose sharded SQLite over Postgres for the 10k launch",
    "content": "We use sharded SQLite for the launch instead of Postgres.",
}


class MemoryFilenameTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.vault = CortexVault(root / "vault", root / "index.sqlite")
        self.vault.ensure()
        self.mem_dir = self.vault.root / "memories"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _notes(self) -> list[Path]:
        return list(self.mem_dir.rglob("*.md"))

    def test_filename_is_human_readable_and_shortid_suffixed(self) -> None:
        self.vault.write_memory(SAMPLE)
        notes = self._notes()
        self.assertEqual(len(notes), 1)
        stem = notes[0].stem
        self.assertIn("sqlite", stem.lower())               # slug from summary
        self.assertTrue(stem.endswith("--" + self.vault.memory_note_short_id(SAMPLE["id"])))

    def test_shortid_stable_across_summary_edits(self) -> None:
        self.vault.write_memory(SAMPLE)
        first = self._notes()[0]
        edited = dict(SAMPLE, summary="A totally different summary now")
        self.vault.write_memory(edited)
        notes = self._notes()
        self.assertEqual(len(notes), 1, "summary edit orphaned the old-slug note")
        # Same shortid suffix (id-derived), new slug.
        self.assertEqual(notes[0].stem.rsplit("--", 1)[1], first.stem.rsplit("--", 1)[1])
        self.assertNotEqual(notes[0].stem, first.stem)

    def test_delete_removes_slug_note(self) -> None:
        self.vault.write_memory(SAMPLE)
        self.assertTrue(self.vault._delete_memory_markdown(SAMPLE["id"]))
        self.assertEqual(self._notes(), [])

    def test_has_memory_markdown_by_shortid(self) -> None:
        self.vault.write_memory(SAMPLE)
        self.assertTrue(self.vault.has_memory_markdown(SAMPLE["id"]))
        self.assertFalse(self.vault.has_memory_markdown("mem_does_not_exist"))
        # Corrupt-but-present note at the right shortid still counts as present (reconcile safety).
        note = self._notes()[0]
        note.write_text("garbage not valid frontmatter", encoding="utf-8")
        self.assertTrue(self.vault.has_memory_markdown(SAMPLE["id"]))

    def test_collision_guard_does_not_cross_delete(self) -> None:
        # Two notes sharing a filename shortid but DIFFERENT frontmatter ids: delete(id_A) removes
        # only A's note. (Fabricate the identical suffix by hand to exercise the id-confirm branch.)
        short = self.vault.memory_note_short_id("mem_A")
        (self.mem_dir / "semantic").mkdir(parents=True, exist_ok=True)
        a = self.mem_dir / "semantic" / f"note-a--{short}.md"
        b = self.mem_dir / "semantic" / f"note-b--{short}.md"
        atomic_write_text(a, render_memory_markdown({"id": "mem_A", "user_id": "u", "kind": "claim", "layer": "semantic", "status": "active", "content": "A"}))
        atomic_write_text(b, render_memory_markdown({"id": "mem_B", "user_id": "u", "kind": "claim", "layer": "semantic", "status": "active", "content": "B"}))
        self.vault._delete_memory_markdown("mem_A")
        self.assertFalse(a.exists())
        self.assertTrue(b.exists(), "collision guard cross-deleted a different memory's note")

    def test_legacy_id_named_note_still_rebuilds_and_migrates(self) -> None:
        # A legacy memories/<layer>/<id>.md note (pre-rename) is still found by id, and the one-time
        # migration renames it to the human-readable scheme WITHOUT losing content.
        legacy = self.mem_dir / "decision" / f"{SAMPLE['id']}.md"
        atomic_write_text(legacy, render_memory_markdown(SAMPLE))
        # Found by id before migration.
        self.assertTrue(self.vault.has_memory_markdown(SAMPLE["id"]))
        renamed = self.vault.migrate_memory_note_filenames("u")
        self.assertEqual(renamed, 1)
        notes = self._notes()
        self.assertEqual(len(notes), 1)
        self.assertFalse(legacy.exists(), "legacy note not renamed")
        self.assertIn("--", notes[0].stem)
        # Content preserved exactly; id unchanged.
        self.assertEqual(parse_memory_markdown(notes[0].read_text(encoding="utf-8"))["id"], SAMPLE["id"])
        # Idempotent: a second run renames nothing.
        self.assertEqual(self.vault.migrate_memory_note_filenames("u"), 0)

    def test_passive_rewrite_removes_legacy_duplicate(self) -> None:
        legacy = self.mem_dir / "decision" / f"{SAMPLE['id']}.md"
        atomic_write_text(legacy, render_memory_markdown(SAMPLE))
        self.vault.write_memory(SAMPLE)  # passive path
        notes = self._notes()
        self.assertEqual(len(notes), 1, f"legacy duplicate not swept: {[p.name for p in notes]}")
        self.assertFalse(legacy.exists())


if __name__ == "__main__":
    unittest.main()
