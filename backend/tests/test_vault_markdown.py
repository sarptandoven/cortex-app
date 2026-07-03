from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from backend.app.vault import CortexVault
from backend.app.vault_markdown import (
    atomic_write_text,
    parse_memory_markdown,
    render_memory_markdown,
)


SAMPLE_MEMORY = {
    "id": "mem_8e74cf70b6e9",
    "capture_id": "cap_336e27347d70",
    "user_id": "sarp",
    "kind": "decision",
    "layer": "decision",
    "content": "We use sharded SQLite for the Cortex 10k launch instead of Postgres.",
    "summary": "Chose sharded SQLite over Postgres for 10k.",
    "source": "obsidian",
    "source_url": "local-file://cortex-project.md#line=3",
    "confidence": "confirmed",
    "importance": 4,
    "status": "active",
    "sector": "engineering",
    "topics": ["database", "launch", "scaling"],
    "entity_ids": ["ent_cortex", "ent_dana"],
    "occurred_at": "2026-06-15",
    "valid_from": "2026-06-15",
    "valid_to": None,
    "superseded_by": None,
    "captured_at": "2026-07-02T15:04:00Z",
    "updated_at": "2026-07-02T15:04:00Z",
    "raw_excerpt": "Decision: we use sharded SQLite for the 10k launch instead of Postgres.",
    "provenance": {"capture_id": "cap_336e27347d70", "external_id": "cortex-project.md#heading=decision"},
}


class MarkdownCodecTests(unittest.TestCase):
    def test_round_trip_preserves_typed_fields_and_body(self) -> None:
        text = render_memory_markdown(SAMPLE_MEMORY)
        parsed = parse_memory_markdown(text)
        for key, value in SAMPLE_MEMORY.items():
            if value is None:
                # None fields are omitted from frontmatter; absence == None, which is fine.
                self.assertIsNone(parsed.get(key), f"{key} should round-trip to None")
            else:
                self.assertEqual(parsed.get(key), value, f"{key} did not round-trip")

    def test_rendered_frontmatter_is_valid_obsidian_yaml(self) -> None:
        # Obsidian parses YAML frontmatter; prove a real YAML parser reads what we emit.
        try:
            import yaml  # available in the test env even though the shipping server runs -S
        except Exception:
            self.skipTest("PyYAML not available in this environment")
        text = render_memory_markdown(SAMPLE_MEMORY)
        self.assertTrue(text.startswith("---\n"))
        frontmatter = text.split("---\n", 2)[1]
        loaded = yaml.safe_load(frontmatter)
        self.assertEqual(loaded["id"], SAMPLE_MEMORY["id"])
        self.assertEqual(loaded["importance"], 4)
        self.assertEqual(loaded["topics"], ["database", "launch", "scaling"])
        self.assertEqual(loaded["provenance"]["external_id"], "cortex-project.md#heading=decision")

    def test_body_is_the_human_readable_memory_content(self) -> None:
        text = render_memory_markdown(SAMPLE_MEMORY)
        after = text.split("---", 2)[2].strip()
        self.assertEqual(after, SAMPLE_MEMORY["content"])

    def test_lenient_parse_of_hand_edited_frontmatter(self) -> None:
        # A user editing in Obsidian may write bare YAML rather than JSON-quoted values.
        text = "---\nid: mem_x\nimportance: 5\nstatus: active\ntopics: [a, b]\n---\nEdited body.\n"
        parsed = parse_memory_markdown(text)
        self.assertEqual(parsed["id"], "mem_x")
        self.assertEqual(parsed["importance"], 5)
        self.assertEqual(parsed["status"], "active")
        self.assertEqual(parsed["topics"], ["a", "b"])
        self.assertEqual(parsed["content"], "Edited body.")

    def test_atomic_write_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "note.md"
            atomic_write_text(path, "hello\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "hello\n")
            # No leftover temp files.
            self.assertEqual([p.name for p in path.parent.iterdir()], ["note.md"])

    def test_module_is_stdlib_only_under_dash_S(self) -> None:
        # The shipping backend runs `python3 -S` (no site-packages). The Markdown layer must
        # import cleanly there and must NOT depend on PyYAML.
        result = subprocess.run(
            [sys.executable, "-S", "-c", "import backend.app.vault_markdown as m; m.render_memory_markdown({'id':'x','content':'y'})"],
            cwd=str(Path(__file__).resolve().parents[2]),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class VaultMemoryMirrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.vault = CortexVault(root / "vault", root / "index.sqlite")
        self.vault.ensure()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_write_memory_creates_readable_markdown_mirror(self) -> None:
        self.vault.write_memory(SAMPLE_MEMORY)
        md_path = self.vault.memory_markdown_path(SAMPLE_MEMORY)
        self.assertTrue(md_path.exists(), "markdown mirror not written")
        self.assertEqual(md_path, self.vault.root / "memories" / "decision" / "mem_8e74cf70b6e9.md")
        text = md_path.read_text(encoding="utf-8")
        self.assertIn("kind: \"decision\"", text)
        self.assertIn("sharded SQLite", text)
        # The JSON record still exists (source of truth in Phase 1).
        self.assertTrue((self.vault.root / "memories" / "decision" / "mem_8e74cf70b6e9.json").exists())
        # And it round-trips back to the same fields.
        parsed = parse_memory_markdown(text)
        self.assertEqual(parsed["id"], SAMPLE_MEMORY["id"])
        self.assertEqual(parsed["content"], SAMPLE_MEMORY["content"])

    def test_markdown_mirror_can_be_disabled(self) -> None:
        self.vault.markdown_mirror = False
        self.vault.write_memory(SAMPLE_MEMORY)
        self.assertFalse(self.vault.memory_markdown_path(SAMPLE_MEMORY).exists())


if __name__ == "__main__":
    unittest.main()
