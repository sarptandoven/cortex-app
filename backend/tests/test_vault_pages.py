"""N3/N4/N5: machine-owned vault pages — Home ("Cortex — Start Here.md"), daily Journal notes,
and the Obsidian Constellation.canvas. Same safety model as O6 entity MOC: flag-gated
(CORTEX_ENTITY_MOC), deterministic, and reconcile-invisible (outside memories/)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.database import init_db
from backend.app.extractor import extract_context
from backend.app.storage import CortexStore, now_iso

USER = "pages-user"
ENABLED = {"CORTEX_ENTITY_MOC": "1"}


class VaultPagesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        db = root / "p.sqlite"
        init_db(db)
        self.store = CortexStore(db, root / "vault")
        self.store.update_settings(USER, {"review_new_captures": False, "allow_pending_in_context": True})
        self.vault_root = root / "vault"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, mid: str, content: str, entities) -> None:
        self.store.save_capture(
            user_id=USER, content=content, source="obsidian", source_url=f"x://{mid}", title=mid,
            cite_capture_provenance=True,
            extracted={
                "_timestamp": now_iso(), "summary": content,
                "records": [{"id": mid, "kind": "decision", "layer": "decision", "content": content,
                             "confidence": "confirmed", "importance": 4, "topics": ["database"],
                             "entity_ids": [e["id"] for e in entities]}],
                "tasks": [], "entities": entities,
            },
        )

    def _seed_graph(self) -> None:
        dana = {"id": "person_dana", "kind": "person", "name": "Dana Lee", "aliases": []}
        atlas = {"id": "project_atlas", "kind": "project", "name": "Project Atlas", "aliases": []}
        self._seed("mem_a", "Dana Lee leads Project Atlas and chose sharded SQLite.", [dana, atlas])
        self._seed("mem_b", "Dana Lee reviewed the Project Atlas migration plan.", [dana, atlas])

    # ---- flag gating + reconcile-invisibility (the shared safety model) ----

    def test_disabled_generates_no_pages(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORTEX_ENTITY_MOC", None)
            self._seed_graph()
        self.assertFalse((self.vault_root / "Cortex — Start Here.md").exists())
        self.assertFalse((self.vault_root / "Journal").exists() and list((self.vault_root / "Journal").glob("*.md")))
        self.assertFalse((self.vault_root / "Constellation.canvas").exists())

    def test_pages_are_invisible_to_reconcile(self) -> None:
        with mock.patch.dict(os.environ, ENABLED, clear=False):
            self._seed_graph()
            self.assertTrue((self.vault_root / "Cortex — Start Here.md").exists())
            self.assertTrue(list((self.vault_root / "Journal").glob("*.md")))
            self.assertTrue((self.vault_root / "Constellation.canvas").exists())
            records = list(self.store.vault.iter_memory_markdown_records(USER))
            self.assertTrue(all(not str(r.get("id") or "").startswith("Cortex") for r in records))
            result = self.store.reconcile_vault_edits(USER)
            self.assertEqual(result.get("added", 0), 0)
            self.assertEqual(result.get("removed", 0), 0)

    # ---- N3 Home ----

    def test_home_page_has_hubs_folders_and_resolving_links(self) -> None:
        with mock.patch.dict(os.environ, ENABLED, clear=False):
            self._seed_graph()
        text = (self.vault_root / "Cortex — Start Here.md").read_text(encoding="utf-8")
        self.assertIn("cortex_generated: true", text)
        self.assertIn("## Map of Content", text)
        self.assertIn("[People](People/)", text)
        self.assertIn("## Top hubs", text)
        self.assertIn("Dana Lee", text)
        # Hub wikilinks resolve to a real MOC page basename.
        import re
        targets = set(re.findall(r"\[\[([^\]|]+)\|", text))
        moc_stems = {p.stem for folder in ("People", "Projects", "Orgs", "Topics") for p in (self.vault_root / folder).glob("*.md")} if any((self.vault_root / f).exists() for f in ("People", "Projects")) else set()
        self.assertTrue(targets & moc_stems, f"home hub links {targets} resolve to no MOC page {moc_stems}")

    def test_home_page_deterministic(self) -> None:
        with mock.patch.dict(os.environ, ENABLED, clear=False):
            self._seed_graph()
            first = (self.vault_root / "Cortex — Start Here.md").read_text(encoding="utf-8")
            p1 = self.store.build_home_page(USER)
            p2 = self.store.build_home_page(USER)
        self.assertEqual(p1, p2)

    # ---- N4 Journal ----

    def test_daily_page_lists_days_memories_and_entities(self) -> None:
        with mock.patch.dict(os.environ, ENABLED, clear=False):
            self._seed_graph()
        days = list((self.vault_root / "Journal").glob("*.md"))
        self.assertEqual(len(days), 1)  # both captures same day
        text = days[0].read_text(encoding="utf-8")
        self.assertIn("## Learned", text)
        self.assertIn("## Entities", text)
        # Memory links resolve to real note stems.
        import re
        targets = set(re.findall(r"\[\[([^\]|]+)\|", text))
        note_stems = {p.stem for p in (self.vault_root / "memories").rglob("*.md")}
        self.assertTrue(targets & note_stems)

    def test_daily_interactive_regen_only_touches_the_capture_day(self) -> None:
        # A page for an OLD day must survive an interactive capture on a NEW day (targeted regen).
        with mock.patch.dict(os.environ, ENABLED, clear=False):
            self.store.build_daily_pages  # noqa: warm attr
            # Hand-place an old-day page, then capture today: the old page must NOT be pruned.
            (self.vault_root / "Journal").mkdir(parents=True, exist_ok=True)
            old = self.vault_root / "Journal" / "2000-01-01.md"
            old.write_text("---\ncortex_generated: true\ndate: \"2000-01-01\"\n---\n# 2000-01-01\n", encoding="utf-8")
            self._seed_graph()
        self.assertTrue(old.exists(), "targeted daily regen wrongly pruned an out-of-window day page")

    def test_daily_build_bounded_window(self) -> None:
        with mock.patch.dict(os.environ, ENABLED, clear=False):
            self._seed_graph()
            pages = self.store.build_daily_pages(USER, window=1)
        self.assertLessEqual(len(pages), 1)

    # ---- N5 Canvas ----

    def test_canvas_is_valid_and_links_to_moc(self) -> None:
        with mock.patch.dict(os.environ, ENABLED, clear=False):
            self._seed_graph()
        doc = json.loads((self.vault_root / "Constellation.canvas").read_text(encoding="utf-8"))
        self.assertIn("nodes", doc)
        self.assertIn("edges", doc)
        self.assertEqual(len(doc["nodes"]), 2)  # Dana + Atlas
        for node in doc["nodes"]:
            self.assertEqual(node["type"], "file")
            self.assertTrue(node["file"].endswith(".md"))
            self.assertIn(node["file"].split("/")[0], ("People", "Projects", "Orgs", "Topics"))
            for key in ("x", "y", "width", "height", "color"):
                self.assertIn(key, node)

    def test_canvas_deterministic(self) -> None:
        with mock.patch.dict(os.environ, ENABLED, clear=False):
            self._seed_graph()
            self.assertEqual(self.store.build_constellation_canvas(USER), self.store.build_constellation_canvas(USER))


if __name__ == "__main__":
    unittest.main()
