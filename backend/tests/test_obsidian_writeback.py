from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import connect, init_db
from backend.app.extractor import extract_context
from backend.app import mcp_tools
from backend.app.connectors.obsidian import scan_vault
from backend.app.obsidian_writeback import (
    PEOPLE_DIRNAME,
    PROFILE_FILENAME,
    README_FILENAME,
    WRITEBACK_DIRNAME,
    parse_generated_marker,
    person_page_filename,
)
from backend.app.storage import CortexStore


class ObsidianWritebackTests(unittest.TestCase):
    """Obsidian write-back: Cortex maintains a machine-owned, CITED Cortex/ folder inside the
    user's own vault. Locked invariants: only marker-owned files are ever written or pruned
    (a user's file with the same name wins); renders are deterministic so an unchanged corpus
    writes zero bytes (idempotent); every bullet carries its memory id; generated pages are
    excluded from connector ingestion (Cortex never eats its own output); the tool is
    export-scoped and honors the allow_agent_exports trust gate."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "writeback-user"
        # write_obsidian_pages is export-scoped; the trust gate (allow_agent_exports, default
        # OFF) applies — enabled here, test_export_trust_gate proves the default blocks it.
        self.store.update_settings(
            self.user_id, {"review_new_captures": False, "allow_agent_exports": True}
        )
        self.obsidian_vault = self.root / "user-vault"
        self.obsidian_vault.mkdir()
        (self.obsidian_vault / "note.md").write_text(
            "I prefer short bullet-point status updates over long prose.", encoding="utf-8"
        )
        self._seed_corpus()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_corpus(self) -> None:
        for text in (
            "I decided to use Postgres for the ledger service after comparing options with Jane Smith.",
            "I prefer short bullet-point status updates over long prose reports.",
            "Jane Smith agreed to review the ledger schema by Friday.",
        ):
            self.store.save_capture(
                user_id=self.user_id,
                content=text,
                source="note",
                source_url="obsidian://vault/notes/ledger.md",
                title=None,
                extracted=extract_context(text),
            )

    def _call(self, name: str, args: dict):
        return mcp_tools.call_tool(self.store, self.user_id, name, args)

    def _write(self, **kw):
        return self.store.write_obsidian_pages(
            self.user_id, vault_path=str(self.obsidian_vault), **kw
        )

    # -- the pages ------------------------------------------------------------------------

    def test_writes_readme_profile_and_person_pages(self) -> None:
        result = self._write()
        folder = self.obsidian_vault / WRITEBACK_DIRNAME
        self.assertTrue((folder / README_FILENAME).is_file())
        self.assertTrue((folder / PROFILE_FILENAME).is_file())
        people = sorted((folder / PEOPLE_DIRNAME).glob("*.md"))
        self.assertTrue(people, "expected at least one person page (Jane Smith)")
        self.assertIn(f"{WRITEBACK_DIRNAME}/{PROFILE_FILENAME}", result["written"])
        self.assertEqual(result["people_pages"], len(people))
        # Every generated page carries the machine-owned marker.
        for page in [folder / README_FILENAME, folder / PROFILE_FILENAME, *people]:
            marker = parse_generated_marker(page.read_text(encoding="utf-8"))
            self.assertTrue(marker["generated"], page.name)

    def test_pages_are_cited(self) -> None:
        self._write()
        folder = self.obsidian_vault / WRITEBACK_DIRNAME
        profile_text = (folder / PROFILE_FILENAME).read_text(encoding="utf-8")
        self.assertIn("`mem_", profile_text)  # memory ids on bullets
        person_pages = list((folder / PEOPLE_DIRNAME).glob("*.md"))
        person_text = person_pages[0].read_text(encoding="utf-8")
        self.assertIn("Jane Smith", person_text)
        self.assertIn("`mem_", person_text)

    def test_rerun_is_idempotent(self) -> None:
        self._write()
        folder = self.obsidian_vault / WRITEBACK_DIRNAME
        before = {p: p.read_bytes() for p in folder.rglob("*.md")}
        result = self._write()
        self.assertEqual(result["written"], [])
        self.assertEqual(sorted(result["unchanged"]),
                         sorted(str(p.relative_to(self.obsidian_vault)) for p in before))
        after = {p: p.read_bytes() for p in folder.rglob("*.md")}
        self.assertEqual(before, after)

    def test_corpus_change_updates_profile(self) -> None:
        self._write()
        self.store.save_capture(
            user_id=self.user_id,
            content="I decided to adopt trunk-based development for the ledger repo.",
            source="note",
            source_url="obsidian://vault/notes/process.md",
            title=None,
            extracted=extract_context("I decided to adopt trunk-based development for the ledger repo."),
        )
        result = self._write()
        self.assertIn(f"{WRITEBACK_DIRNAME}/{PROFILE_FILENAME}", result["written"])
        text = (self.obsidian_vault / WRITEBACK_DIRNAME / PROFILE_FILENAME).read_text(encoding="utf-8")
        self.assertIn("trunk-based", text)

    # -- ownership safety -----------------------------------------------------------------

    def test_user_file_with_same_name_is_never_overwritten(self) -> None:
        folder = self.obsidian_vault / WRITEBACK_DIRNAME
        folder.mkdir()
        user_text = "# My own profile notes\n\nHands off, Cortex.\n"
        (folder / PROFILE_FILENAME).write_text(user_text, encoding="utf-8")
        result = self._write()
        self.assertIn(f"{WRITEBACK_DIRNAME}/{PROFILE_FILENAME}", result["skipped_unowned"])
        self.assertEqual((folder / PROFILE_FILENAME).read_text(encoding="utf-8"), user_text)

    def test_prunes_only_marker_owned_stale_person_pages(self) -> None:
        self._write()
        people_dir = self.obsidian_vault / WRITEBACK_DIRNAME / PEOPLE_DIRNAME
        # A stale GENERATED page (person no longer in the top set) is pruned...
        stale = people_dir / person_page_filename("ent_gone", "Gone Person")
        stale.write_text(
            '---\ncortex_generated: true\ncortex_page: "person"\n---\n\n# Gone Person\n',
            encoding="utf-8",
        )
        # ...but a USER file in People/ is untouched.
        user_note = people_dir / "my-own-notes-on-jane.md"
        user_note.write_text("# Jane\n\nMy private notes.\n", encoding="utf-8")
        result = self._write()
        self.assertIn(f"{WRITEBACK_DIRNAME}/{PEOPLE_DIRNAME}/{stale.name}", result["pruned"])
        self.assertFalse(stale.exists())
        self.assertTrue(user_note.exists())

    def test_requires_vault(self) -> None:
        with self.assertRaises(ValueError):
            self.store.write_obsidian_pages(self.user_id)  # no connected vault, no explicit path
        # Bad explicit path: same ValueError contract as sync_obsidian_vault / vault_identity.
        with self.assertRaises(ValueError):
            self.store.write_obsidian_pages(self.user_id, vault_path=str(self.root / "nope"))

    def test_defaults_to_connected_vault(self) -> None:
        self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.obsidian_vault))
        result = self.store.write_obsidian_pages(self.user_id)
        self.assertEqual(Path(result["vault_path"]), self.obsidian_vault.resolve())
        self.assertTrue((self.obsidian_vault / WRITEBACK_DIRNAME / PROFILE_FILENAME).is_file())

    # -- the loop must not close ------------------------------------------------------------

    def test_generated_pages_are_excluded_from_ingestion(self) -> None:
        self._write()
        scan = scan_vault(self.obsidian_vault, max_records=100)
        scanned_paths = {record.metadata.get("relative_path") for record in scan.records}
        self.assertIn("note.md", scanned_paths)  # the user's own note ingests
        for scanned in scanned_paths:
            self.assertFalse(
                str(scanned).startswith(f"{WRITEBACK_DIRNAME}/"),
                f"generated page leaked into ingestion: {scanned}",
            )

    def test_user_note_inside_cortex_folder_still_ingests(self) -> None:
        # Exclusion is marker-based, not folder-based: a user's own note dropped in Cortex/
        # has no marker and ingests normally.
        self._write()
        own = self.obsidian_vault / WRITEBACK_DIRNAME / "my-scratch.md"
        own.write_text("A user note that happens to live in the Cortex folder.", encoding="utf-8")
        scan = scan_vault(self.obsidian_vault, max_records=100)
        scanned_paths = {record.metadata.get("relative_path") for record in scan.records}
        self.assertIn(f"{WRITEBACK_DIRNAME}/my-scratch.md", scanned_paths)

    def test_full_sync_then_writeback_roundtrip_never_reingests(self) -> None:
        # End-to-end: connect the vault, write back, re-sync — capture count from the vault
        # must not grow because of generated pages.
        first = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.obsidian_vault))
        self.assertEqual(first["failed"], 0)
        saved_before = int(first["saved"])
        self._write()
        second = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.obsidian_vault))
        self.assertEqual(second["failed"], 0)
        self.assertEqual(int(second["saved"]), 0, "re-sync after write-back must save nothing new")
        self.assertGreaterEqual(saved_before, 1)

    # -- scope discipline -------------------------------------------------------------------

    def test_export_scoped_tool(self) -> None:
        self.assertIn("write_obsidian_pages", mcp_tools.EXPORT_TOOLS)
        self.assertEqual(
            mcp_tools.tool_required_capabilities("write_obsidian_pages"), ["read", "export"]
        )
        with self.assertRaises(PermissionError):
            mcp_tools.call_tool(
                self.store, self.user_id, "write_obsidian_pages",
                {"vault_path": str(self.obsidian_vault)},
                token_scopes=["read", "write"],
            )
        result = mcp_tools.call_tool(
            self.store, self.user_id, "write_obsidian_pages",
            {"vault_path": str(self.obsidian_vault)},
            token_scopes=["read", "export"],
        )
        self.assertTrue(result["written"])

    def test_export_trust_gate_defaults_off(self) -> None:
        self.store.update_settings(self.user_id, {"allow_agent_exports": False})
        with self.assertRaises(PermissionError):
            self._call("write_obsidian_pages", {"vault_path": str(self.obsidian_vault)})

    def test_not_advertised_as_read_only(self) -> None:
        # It writes user files; a client must never auto-approve it as a read.
        tool = next(t for t in mcp_tools.tools_for_scopes(None) if t["name"] == "write_obsidian_pages")
        self.assertFalse(tool["annotations"]["readOnlyHint"])

    def test_writeback_emits_audit_event(self) -> None:
        self._write()
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM memory_events WHERE user_id = ? AND object_type = 'obsidian_writeback' ORDER BY created_at DESC",
                (self.user_id,),
            ).fetchone()
        self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()
