from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.connectors.obsidian import (
    _markdown_sections,
    _records_for_note,
)


class ObsidianPreambleLossTests(unittest.TestCase):
    """Regression guard for silent data loss of pre-heading body text.

    Before the fix, body text appearing BEFORE the first heading in a note that
    also contains headings was dropped from every emitted record: sections began
    at each heading line, and _records_for_note only fell back to a note-level
    record when *every* section was empty.
    """

    NOTE_WITH_PREAMBLE = (
        "Important intro paragraph.\n"
        "\n"
        "# Section One\n"
        "details\n"
    )

    def _records(self, raw: str):
        return _records_for_note(
            Path("/vault"),
            Path("/vault/Note.md"),
            raw,
            modified=0.0,
            size=len(raw.encode("utf-8")),
        )

    def test_markdown_sections_synthesizes_preamble_section(self) -> None:
        sections = _markdown_sections(self.NOTE_WITH_PREAMBLE)
        titles = [section.title for section in sections]
        self.assertIn("Preamble", titles)
        preamble = next(s for s in sections if s.title == "Preamble")
        self.assertIn("Important intro paragraph.", preamble.raw)
        # The preamble section must precede the heading-derived section.
        self.assertEqual(sections[0].title, "Preamble")
        self.assertEqual(sections[1].title, "Section One")

    def test_records_for_note_emits_preamble_content(self) -> None:
        records = self._records(self.NOTE_WITH_PREAMBLE)
        joined = "\n".join(record.content for record in records)
        self.assertIn("Important intro paragraph.", joined)
        # The heading section content must still be present too.
        self.assertIn("details", joined)

    def test_note_without_preamble_is_unchanged(self) -> None:
        raw = "# Section One\ndetails\n"
        sections = _markdown_sections(raw)
        self.assertNotIn("Preamble", [s.title for s in sections])
        records = self._records(raw)
        joined = "\n".join(record.content for record in records)
        self.assertIn("details", joined)

    def test_blank_only_preamble_is_not_emitted(self) -> None:
        raw = "\n\n   \n# Section One\ndetails\n"
        sections = _markdown_sections(raw)
        self.assertNotIn("Preamble", [s.title for s in sections])

    def test_note_without_headings_is_unchanged(self) -> None:
        raw = "Just a flat note with no headings at all.\n"
        sections = _markdown_sections(raw)
        self.assertEqual(sections, [])
        records = self._records(raw)
        joined = "\n".join(record.content for record in records)
        self.assertIn("Just a flat note with no headings at all.", joined)


if __name__ == "__main__":
    unittest.main()
