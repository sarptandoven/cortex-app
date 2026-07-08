"""Objective 6: browsable per-entity Map-of-Content pages (People/Projects/Orgs/Topics).

The load-bearing safety property is reconcile-invisibility: MOC pages live OUTSIDE memories/, are
marked cortex_generated, and must NEVER be parsed as memories or counted toward reconcile's
add/change/remove/mass-delete accounting. Generation is flag-gated (CORTEX_ENTITY_MOC) so the
shared hosted/bucket vault never generates them."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.database import init_db
from backend.app.extractor import extract_context
from backend.app.storage import CortexStore
from backend.app.vault import CortexVault
from backend.app.vault_markdown import parse_memory_markdown, render_entity_moc_markdown

USER = "moc-user"

SAMPLE_PAGE = {
    "cortex_generated": True,
    "entity_id": "person_alice",
    "kind": "person",
    "name": "Alice",
    "aliases": ["alice@acme.com"],
    "centrality": 1.0,
    "community": 0,
    "supporting_count": 4,
    "connections": [
        {"wikilink": "Bob--deadbeef", "name": "Bob", "relation": "works_with", "weight": 3.0},
    ],
    "memory_links": [{"wikilink": "mem_123", "note": "Alice and Bob shipped the migration"}],
    "community_peers": [{"wikilink": "Bob--deadbeef", "name": "Bob"}],
}


class MocRenderTests(unittest.TestCase):
    def test_render_has_frontmatter_and_wikilinks(self) -> None:
        text = render_entity_moc_markdown(SAMPLE_PAGE)
        self.assertTrue(text.startswith("---"))
        self.assertIn("cortex_generated: true", text)
        self.assertIn('kind: "person"', text)
        self.assertIn("centrality:", text)
        self.assertIn("community:", text)
        self.assertIn("supporting_count:", text)
        self.assertIn("# Alice", text)
        self.assertIn("[[Bob--deadbeef|Bob]]", text)  # entity->entity alias wikilink
        self.assertIn("[[mem_123]]", text)             # cited memory wikilink

    def test_render_is_deterministic(self) -> None:
        # No timestamp -> byte-identical output for the same page (no git-sync churn).
        self.assertEqual(render_entity_moc_markdown(SAMPLE_PAGE), render_entity_moc_markdown(SAMPLE_PAGE))

    def test_moc_page_is_not_parsed_as_a_memory(self) -> None:
        # A MOC page must never accidentally satisfy the memory parser as real content.
        parsed = parse_memory_markdown(render_entity_moc_markdown(SAMPLE_PAGE))
        self.assertNotEqual(parsed.get("kind"), "memory")


class MocVaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.vault = CortexVault(root / "vault", root / "index.sqlite")
        self.vault.ensure()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_path_folder_by_kind(self) -> None:
        self.assertTrue(str(self.vault.entity_moc_path({"entity_id": "e", "kind": "project", "name": "Doppl"})).endswith(".md"))
        self.assertIn("/Projects/", str(self.vault.entity_moc_path({"entity_id": "e", "kind": "project", "name": "Doppl"})))
        self.assertIn("/Orgs/", str(self.vault.entity_moc_path({"entity_id": "e", "kind": "org", "name": "Acme"})))
        self.assertIn("/Topics/", str(self.vault.entity_moc_path({"entity_id": "e", "kind": "concept", "name": "AI"})))
        self.assertIn("/People/", str(self.vault.entity_moc_path({"entity_id": "e", "kind": "person", "name": "Al"})))

    def test_write_is_outside_memories_and_stem_matches_link(self) -> None:
        page = dict(SAMPLE_PAGE)
        path = self.vault.write_entity_markdown(page)
        self.assertIsNotNone(path)
        self.assertIn("/People/", str(path))
        self.assertNotIn("/memories/", str(path))
        # The filename stem equals the stem another page would use to link to it.
        self.assertEqual(path.stem, self.vault.entity_moc_stem("person_alice", "Alice"))

    def test_rename_moves_and_removes_stale(self) -> None:
        self.vault.write_entity_markdown({"entity_id": "ent_1", "kind": "person", "name": "Al"})
        self.vault.write_entity_markdown({"entity_id": "ent_1", "kind": "person", "name": "Alice"})
        people = list((self.vault.root / "People").glob("*.md"))
        self.assertEqual(len(people), 1)  # stale "Al" page removed
        self.assertTrue(people[0].stem.startswith("Alice--"))

    def test_prune_removes_orphans(self) -> None:
        import hashlib
        self.vault.write_entity_markdown({"entity_id": "ent_keep", "kind": "person", "name": "Keep"})
        self.vault.write_entity_markdown({"entity_id": "ent_drop", "kind": "person", "name": "Drop"})
        keep = {hashlib.sha1(b"ent_keep").hexdigest()[:8]}
        self.vault.prune_entity_moc_pages(keep)
        remaining = [p.stem for p in (self.vault.root / "People").glob("*.md")]
        self.assertEqual(len(remaining), 1)
        self.assertTrue(remaining[0].startswith("Keep--"))

    def test_mirror_off_writes_nothing(self) -> None:
        self.vault.markdown_mirror = False
        self.assertIsNone(self.vault.write_entity_markdown(dict(SAMPLE_PAGE)))
        self.assertFalse((self.vault.root / "People").exists())


class MocStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        db = root / "m.sqlite"
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

    def test_disabled_by_default_no_pages(self) -> None:
        # Without CORTEX_ENTITY_MOC the shared/hosted path generates nothing.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORTEX_ENTITY_MOC", None)
            self._save("Marcus and Dana shipped the billing service.")
        moc_dirs = [self.vault_root / d for d in ("People", "Projects", "Orgs", "Topics")]
        self.assertFalse(any(d.exists() and list(d.glob("*.md")) for d in moc_dirs))

    def test_enabled_generates_pages_invisible_to_reconcile(self) -> None:
        with mock.patch.dict(os.environ, {"CORTEX_ENTITY_MOC": "1"}, clear=False):
            self._save("Marcus and Dana shipped the billing service together.")
            self._save("Marcus reviewed Dana's migration plan for billing.")
            people = list((self.vault_root / "People").glob("*.md"))
            self.assertTrue(people, "no People MOC pages generated")
            # THE safety assertion: MOC pages are never seen as memories, so reconcile adds/removes
            # nothing on their account (no memory created/deleted from the MOC pages).
            records = list(self.store.vault.iter_memory_markdown_records(USER))
            record_paths = "\n".join(str(r.get("id")) for r in records)
            self.assertNotIn("--", record_paths)  # MOC stems contain '--'; memory ids do not
            result = self.store.reconcile_vault_edits(USER)
            self.assertEqual(result.get("removed", 0), 0)
            self.assertEqual(result.get("added", 0), 0)

    def test_build_pages_deterministic(self) -> None:
        with mock.patch.dict(os.environ, {"CORTEX_ENTITY_MOC": "1"}, clear=False):
            self._save("Marcus and Dana shipped the billing service together.")
        p1 = self.store.build_entity_moc_pages(USER)
        p2 = self.store.build_entity_moc_pages(USER)
        self.assertEqual(p1, p2)


if __name__ == "__main__":
    unittest.main()
