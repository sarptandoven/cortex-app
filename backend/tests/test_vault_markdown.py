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
    _wikilink,
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
        after = text.split("---", 2)[2]
        # The human-readable content is the body up to the auto-generated links block, and it
        # always round-trips back cleanly via parse (which strips the generated block).
        content_part = after.split("<!-- cortex:generated-links -->", 1)[0].strip()
        self.assertEqual(content_part, SAMPLE_MEMORY["content"])
        self.assertEqual(parse_memory_markdown(text)["content"], SAMPLE_MEMORY["content"])

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
        # import cleanly there and must NOT depend on PyYAML. Exercise the generated-links path too.
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                "import backend.app.vault_markdown as m; "
                "m.render_memory_markdown({'id':'x','content':'y','entity_ids':['e'],"
                "'_link_names':{'e':'E'},'_backlinks':[{'id':'m','label':'l'}]})",
            ],
            cwd=str(Path(__file__).resolve().parents[2]),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    # ---- Objective 5: generated [[wikilinks]] + Backlinks (lossless & idempotent) ----

    def test_links_section_emitted_with_resolved_names(self) -> None:
        rec = dict(SAMPLE_MEMORY)
        rec["_link_names"] = {"ent_cortex": "Cortex", "ent_dana": "Dana Lee"}
        text = render_memory_markdown(rec)
        self.assertIn("## Links", text)
        self.assertIn("[[Cortex]]", text)
        self.assertIn("[[Dana Lee]]", text)
        self.assertIn("[[database]]", text)  # topic wikilink
        self.assertIn("[[launch]]", text)

    def test_unresolved_entity_id_falls_back_to_id(self) -> None:
        text = render_memory_markdown(dict(SAMPLE_MEMORY))  # no _link_names
        self.assertIn("[[ent_cortex]]", text)  # raw id used, never crashes

    def test_backlinks_section_emitted(self) -> None:
        rec = dict(SAMPLE_MEMORY)
        rec["_backlinks"] = [{"id": "mem_neighbor1", "label": "Chose Postgres earlier"}]
        text = render_memory_markdown(rec)
        self.assertIn("## Backlinks", text)
        self.assertIn("- [[mem_neighbor1]] — Chose Postgres earlier", text)

    def test_generated_keys_never_leak_into_frontmatter(self) -> None:
        rec = dict(SAMPLE_MEMORY)
        rec["_link_names"] = {"ent_cortex": "Cortex"}
        rec["_backlinks"] = [{"id": "m", "label": "x"}]
        text = render_memory_markdown(rec)
        frontmatter = text.split("---", 2)[1]
        self.assertNotIn("_link_names", frontmatter)
        self.assertNotIn("_backlinks", frontmatter)

    def test_parse_strips_generated_sections_content_is_clean(self) -> None:
        # THE core round-trip guarantee: content must never absorb generated wikilinks.
        rec = dict(SAMPLE_MEMORY)
        rec["_link_names"] = {"ent_cortex": "Cortex", "ent_dana": "Dana Lee"}
        rec["_backlinks"] = [{"id": "mem_n", "label": "neighbor"}]
        parsed = parse_memory_markdown(render_memory_markdown(rec))
        self.assertEqual(parsed["content"], SAMPLE_MEMORY["content"])
        self.assertNotIn("[[", parsed["content"])

    def test_render_parse_render_is_idempotent(self) -> None:
        # No unbounded growth: exactly one Links section after a full round-trip re-render.
        rec = dict(SAMPLE_MEMORY)
        rec["_link_names"] = {"ent_cortex": "Cortex", "ent_dana": "Dana Lee"}
        rec["_backlinks"] = [{"id": "mem_n", "label": "neighbor"}]
        t1 = render_memory_markdown(rec)
        reparsed = parse_memory_markdown(t1)
        reparsed["_link_names"] = rec["_link_names"]
        reparsed["_backlinks"] = rec["_backlinks"]
        reparsed["topics"] = SAMPLE_MEMORY["topics"]
        reparsed["entity_ids"] = SAMPLE_MEMORY["entity_ids"]
        t2 = render_memory_markdown(reparsed)
        self.assertEqual(t1.count("## Links"), 1)
        self.assertEqual(t2.count("## Links"), 1)
        self.assertEqual(t1.count("## Backlinks"), 1)
        self.assertEqual(t2.count("## Backlinks"), 1)

    def test_user_text_below_generated_block_is_preserved(self) -> None:
        # A user who types below the auto-generated block must not lose that text on parse.
        rec = dict(SAMPLE_MEMORY)
        rec["_link_names"] = {"ent_cortex": "Cortex"}
        text = render_memory_markdown(rec)
        edited = text.rstrip("\n") + "\n\nMy own footnote below the links.\n"
        parsed = parse_memory_markdown(edited)
        self.assertIn("My own footnote below the links.", parsed["content"])
        self.assertNotIn("[[", parsed["content"])  # generated block still stripped
        self.assertEqual(parsed["content"].count(SAMPLE_MEMORY["content"]), 1)

    def test_existing_round_trip_unaffected_without_link_maps(self) -> None:
        # Backfill/patch paths render without _link_names/_backlinks -> content still clean.
        parsed = parse_memory_markdown(render_memory_markdown(dict(SAMPLE_MEMORY)))
        self.assertEqual(parsed["content"], SAMPLE_MEMORY["content"])

    def test_wikilink_sanitizes_brackets_and_pipes(self) -> None:
        self.assertEqual(_wikilink("A|B]C"), "[[A B C]]")
        self.assertEqual(_wikilink(""), "")
        self.assertEqual(_wikilink("  spaced  name  "), "[[spaced name]]")

    # Regression: a memory whose OWN content contains the generated-block marker must NOT be
    # truncated or grown on the round-trip (adversarial-review CRITICAL finding).
    def test_content_containing_marker_line_is_not_truncated(self) -> None:
        marker = "<!-- cortex:generated-links -->"
        rec = dict(SAMPLE_MEMORY)
        rec["content"] = f"keep this\n{marker}\nand keep this too"
        rec["entity_ids"] = []
        rec["topics"] = []
        parsed = parse_memory_markdown(render_memory_markdown(rec))
        self.assertEqual(parsed["content"], rec["content"])  # lone marker, no block -> untouched

    def test_content_with_marker_and_real_block_round_trips(self) -> None:
        marker = "<!-- cortex:generated-links -->"
        rec = dict(SAMPLE_MEMORY)
        rec["content"] = f"Before\n{marker}\nAfter user text"
        rec["_link_names"] = {"ent_cortex": "Cortex"}
        t1 = render_memory_markdown(rec)
        parsed = parse_memory_markdown(t1)
        self.assertEqual(parsed["content"], rec["content"])  # user marker + text preserved
        # Idempotent: re-render from the parsed content (with the same link inputs) does not grow.
        reparsed = dict(parsed)
        reparsed["_link_names"] = {"ent_cortex": "Cortex"}
        reparsed["entity_ids"] = SAMPLE_MEMORY["entity_ids"]
        t2 = render_memory_markdown(reparsed)
        self.assertEqual(t2.count("## Links"), 1)
        self.assertEqual(parse_memory_markdown(t2)["content"], rec["content"])

    def test_content_ending_in_marker_survives(self) -> None:
        marker = "<!-- cortex:generated-links -->"
        rec = dict(SAMPLE_MEMORY)
        rec["content"] = f"Here is how cortex marks generated links:\n{marker}"
        rec["_link_names"] = {"ent_cortex": "Cortex"}
        parsed = parse_memory_markdown(render_memory_markdown(rec))
        self.assertEqual(parsed["content"], rec["content"])


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

    def test_layer_change_does_not_orphan_markdown(self) -> None:
        self.vault.write_memory(SAMPLE_MEMORY)  # layer=decision
        moved = dict(SAMPLE_MEMORY)
        moved["layer"] = "semantic"
        self.vault.write_memory(moved)
        notes = list((self.vault.root / "memories").rglob(f"{SAMPLE_MEMORY['id']}.md"))
        self.assertEqual(len(notes), 1, f"orphaned note(s): {[str(p) for p in notes]}")
        self.assertEqual(notes[0], self.vault.root / "memories" / "semantic" / f"{SAMPLE_MEMORY['id']}.md")

    def test_patch_memory_updates_the_markdown_note(self) -> None:
        self.vault.write_memory(SAMPLE_MEMORY)
        self.vault.patch_memory(SAMPLE_MEMORY["id"], {"status": "archived", "superseded_by": "mem_new"})
        record = parse_memory_markdown(self.vault.memory_markdown_path(SAMPLE_MEMORY).read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "archived")
        self.assertEqual(record["superseded_by"], "mem_new")

    def test_patch_markdown_native_memory_without_json(self) -> None:
        path = self.vault.root / "memories" / "semantic" / "mem_native.md"
        atomic_write_text(
            path,
            render_memory_markdown(
                {"id": "mem_native", "user_id": "u", "kind": "claim", "layer": "semantic", "status": "active", "content": "native memory"}
            ),
        )
        self.assertTrue(self.vault.patch_memory("mem_native", {"status": "archived"}))
        self.assertEqual(parse_memory_markdown(path.read_text(encoding="utf-8"))["status"], "archived")

    def test_vault_sync_scaffolding(self) -> None:
        # ensure() ran in setUp; the vault should be safe + self-explanatory to sync/open.
        gitignore = (self.vault.root / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("credentials.json", gitignore)  # secrets never synced
        self.assertIn("*.sqlite", gitignore)          # rebuildable index excluded
        self.assertIn("backups/", gitignore)
        self.assertTrue((self.vault.root / "README.md").exists())

    def test_sync_scaffolding_is_idempotent(self) -> None:
        readme = self.vault.root / "README.md"
        readme.write_text("my own notes about this vault", encoding="utf-8")
        self.vault.ensure()  # must not clobber the user's edits
        self.assertEqual(readme.read_text(encoding="utf-8"), "my own notes about this vault")


if __name__ == "__main__":
    unittest.main()
