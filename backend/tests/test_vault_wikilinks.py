"""Objective 5 (end-to-end): a saved capture writes a Markdown note whose "## Links" section
carries real [[Entity Name]] wikilinks (canonical names, not raw ids) + topics, and co-mentioning
memories cross-link via "## Backlinks" — while the durable JSON stays free of the transient link
inputs and the note round-trips losslessly."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.extractor import extract_context
from backend.app.storage import CortexStore
from backend.app.vault_markdown import parse_memory_markdown

USER = "wikilink-user"


class VaultWikilinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        db = root / "w.sqlite"
        init_db(db)
        self.store = CortexStore(db, root / "vault")
        self.vault_root = root / "vault"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _save(self, content: str) -> dict:
        return self.store.save_capture(
            user_id=USER, content=content, source="macos", source_url=None, title=None,
            extracted=extract_context(content, "macos"), cite_capture_provenance=True, auto_approve=True,
        )

    def _note_texts(self) -> list[str]:
        return [p.read_text(encoding="utf-8") for p in (self.vault_root / "memories").rglob("*.md")]

    def test_capture_note_has_named_wikilinks(self) -> None:
        self._save("Decision: Marcus and Dana approved the database migration plan for the launch.")
        notes = self._note_texts()
        self.assertTrue(notes, "no memory note written")
        joined = "\n".join(notes)
        self.assertIn("## Links", joined)
        # At least one [[wikilink]] present, and it is NOT a raw entity id (person_/ent_ prefix).
        self.assertIn("[[", joined)
        for note in notes:
            if "## Links" in note:
                links_block = note.split("## Links", 1)[1]
                self.assertNotIn("[[person_", links_block)  # names resolved, not raw ids
                self.assertNotIn("[[ent_", links_block)

    def test_backlinks_cross_link_co_mentions(self) -> None:
        # Two captures sharing an entity should backlink each other once both exist.
        self._save("Marcus owns the billing service and is the on-call lead.")
        self._save("Marcus is migrating the billing service to the new database.")
        joined = "\n".join(self._note_texts())
        self.assertIn("## Backlinks", joined)
        self.assertIn("- [[mem_", joined)  # note-to-note wikilink by memory id

    def test_note_round_trips_and_json_is_clean(self) -> None:
        self._save("Marcus and Dana approved the database migration.")
        # The Markdown note round-trips: content has no absorbed wikilinks.
        for note in self._note_texts():
            parsed = parse_memory_markdown(note)
            self.assertNotIn("[[", parsed["content"])
        # The durable JSON never carries the transient _link_names/_backlinks render inputs.
        for json_path in (self.vault_root / "memories").rglob("*.json"):
            text = json_path.read_text(encoding="utf-8")
            self.assertNotIn("_link_names", text)
            self.assertNotIn("_backlinks", text)

    def test_returned_memories_have_no_internal_keys(self) -> None:
        result = self._save("Marcus approved the plan.")
        for memory in result["memories"]:
            self.assertNotIn("_link_names", memory)
            self.assertNotIn("_backlinks", memory)


if __name__ == "__main__":
    unittest.main()
