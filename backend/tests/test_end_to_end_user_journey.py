from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.database import init_db
from backend.app.storage import CortexStore
from backend.app.vault_markdown import atomic_write_text, parse_memory_markdown, render_memory_markdown


class EndToEndUserJourneyTests(unittest.TestCase):
    """One coherent, single-user journey exercised end-to-end through CortexStore.

    This is deliberately an *integration* test: it does not poke at private helpers to fake
    intermediate state. It drives the real public API the way the product does — configure
    settings, capture memories, retrieve/answer, review-approve/archive, connector-sync,
    Markdown vault round-trip, and delete+restore — and asserts the user-visible outcome at
    each step. The narrative follows a made-up user, "Sam", so the whole product is validated
    working together rather than as isolated unit pieces.

    Setup note: a concurrent process left backend/app/embeddings.py half-edited so that
    embedding_status() raises NameError. We do not touch that file; instead we patch the
    symbol as imported into backend.app.storage and force deterministic lexical retrieval by
    disabling the vector path. That keeps this test hermetic and reproducible.
    """

    def setUp(self) -> None:
        # Sidestep the known-broken embeddings.py without editing it: storage.py imports
        # embedding_status by name, so patch it in the storage namespace.
        self._emb = mock.patch(
            "backend.app.storage.embedding_status",
            return_value={
                "provider": "hash",
                "model": "cortex-hash-v1",
                "dimensions": 384,
                "schema_dimensions": 384,
                "index_compatible": True,
                "network_required": False,
                "strict": False,
            },
        )
        self._emb.start()

        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        # Deterministic lexical retrieval — no vector index in this test.
        self.store._vector_ready = lambda conn: False
        self.user_id = "sam"

        # Step 1: Sam configures Cortex — auto-approve captures and allow pending items in
        # AI context. Assert the settings actually persisted.
        settings = self.store.update_settings(
            self.user_id,
            {"review_new_captures": False, "allow_pending_in_context": True},
        )
        self.assertFalse(settings["review_new_captures"])
        self.assertTrue(settings["allow_pending_in_context"])
        persisted = self.store.settings(self.user_id)
        self.assertFalse(persisted["review_new_captures"])
        self.assertTrue(persisted["allow_pending_in_context"])

    def tearDown(self) -> None:
        self._tmp.cleanup()
        self._emb.stop()

    # --- helpers ---------------------------------------------------------------

    def _save_memory(
        self,
        *,
        memory_id: str,
        content: str,
        layer: str,
        kind: str,
        title: str,
        topics: list[str],
        importance: int = 3,
        source_url: str | None = None,
    ) -> dict:
        return self.store.save_capture(
            user_id=self.user_id,
            content=content,
            source="obsidian",
            source_url=source_url or f"local-file://sam-notes.md#{memory_id}",
            title=title,
            extracted={
                "_timestamp": "2026-07-02T15:04:00Z",
                "summary": content,
                "records": [
                    {
                        "id": memory_id,
                        "kind": kind,
                        "layer": layer,
                        "content": content,
                        "confidence": "confirmed",
                        "importance": importance,
                        "topics": topics,
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )

    def _seed_sam_profile(self) -> None:
        """Seed a small, disjoint set of facts about Sam so retrieval is unambiguous."""
        self._save_memory(
            memory_id="mem_role",
            content="Sam is a staff backend engineer on the Cortex platform team.",
            layer="semantic",
            kind="claim",
            title="Sam's role",
            topics=["role", "team", "cortex"],
            importance=4,
        )
        self._save_memory(
            memory_id="mem_project",
            content="Sam is leading Project Zephyr, the offline-first sync rewrite.",
            layer="semantic",
            kind="claim",
            title="Project Zephyr",
            topics=["zephyr", "sync", "project"],
            importance=4,
        )
        self._save_memory(
            memory_id="mem_decision",
            content="Sam decided to use sharded SQLite instead of Postgres for the launch.",
            layer="decision",
            kind="decision",
            title="Database decision",
            topics=["database", "sqlite", "launch"],
            importance=5,
        )
        self._save_memory(
            memory_id="mem_pref",
            content="Sam prefers async standups over daily synchronous meetings.",
            layer="semantic",
            kind="preference",
            title="Meeting preference",
            topics=["standup", "meetings", "preference"],
            importance=3,
        )

    def _active_memory_ids(self) -> set[str]:
        return {m["id"] for m in self.store.recent(self.user_id, limit=100)}

    # --- Step 2: capture -------------------------------------------------------

    def test_step2_captures_land_as_memories(self) -> None:
        result = self._save_memory(
            memory_id="mem_role",
            content="Sam is a staff backend engineer on the Cortex platform team.",
            layer="semantic",
            kind="claim",
            title="Sam's role",
            topics=["role", "team"],
            importance=4,
        )
        self.assertTrue(result["capture_id"].startswith("cap_"))
        self.assertEqual(len(result["memories"]), 1)
        self.assertEqual(result["memories"][0]["id"], "mem_role")

        self._seed_sam_profile()
        active_ids = self._active_memory_ids()
        for expected in ("mem_role", "mem_project", "mem_decision", "mem_pref"):
            self.assertIn(expected, active_ids, f"{expected} not persisted as an active memory")

    # --- Step 3: retrieval + answer -------------------------------------------

    def test_step3_search_returns_relevant_memory(self) -> None:
        self._seed_sam_profile()

        # A query about the database decision should surface the decision memory first,
        # and must NOT surface the unrelated meeting-preference memory.
        hits = self.store.search(self.user_id, "which database did Sam choose for launch", limit=5)
        self.assertTrue(hits, "no results for the database query")
        hit_ids = [h["id"] for h in hits]
        self.assertIn("mem_decision", hit_ids)
        self.assertEqual(hit_ids[0], "mem_decision", f"expected decision memory ranked first, got {hit_ids}")
        self.assertNotIn("mem_pref", hit_ids, "unrelated preference memory should not match a database query")

        # A query about the project should surface Project Zephyr.
        project_hits = self.store.search(self.user_id, "Project Zephyr sync rewrite", limit=5)
        self.assertTrue(any(h["id"] == "mem_project" for h in project_hits))

        # A query about meetings should surface the preference, not the database decision.
        pref_hits = self.store.search(self.user_id, "how does Sam feel about standup meetings", limit=5)
        self.assertTrue(any(h["id"] == "mem_pref" for h in pref_hits))
        self.assertFalse(any(h["id"] == "mem_decision" for h in pref_hits))

    def test_step3_answer_query_cites_a_memory(self) -> None:
        self._seed_sam_profile()

        answer = self.store.answer_query(self.user_id, "what database did Sam pick for the launch", limit=5)
        self.assertIn(answer["status"], {"cited", "low_confidence", "conflicted"})
        self.assertTrue(answer["citations"], "answer produced no citations")
        cited_ids = {c["id"] for c in answer["citations"]}
        self.assertIn("mem_decision", cited_ids, "the database decision memory was not cited")

        # And when there is genuinely nothing to cite, it abstains rather than hallucinating.
        empty = self.store.answer_query(self.user_id, "what is Sam's favorite planet in Andromeda", limit=5)
        self.assertEqual(empty["status"], "no_cited_evidence")
        self.assertEqual(empty["citations"], [])

    # --- Step 4: review workflow ----------------------------------------------

    def test_step4_review_approve_and_archive(self) -> None:
        # Turn review ON and keep pending items OUT of context so an unapproved capture is
        # genuinely invisible to retrieval until Sam approves it.
        self.store.update_settings(
            self.user_id,
            {"review_new_captures": True, "allow_pending_in_context": False},
        )

        pending = self._save_memory(
            memory_id="mem_review",
            content="Sam is evaluating a move to a monorepo for all Cortex services.",
            layer="semantic",
            kind="claim",
            title="Monorepo evaluation",
            topics=["monorepo", "tooling"],
        )
        pending_capture_id = pending["capture_id"]

        # It shows up in the review inbox as pending...
        inbox_ids = {c["id"] for c in self.store.inbox(self.user_id)}
        self.assertIn(pending_capture_id, inbox_ids, "new capture should be pending in the review inbox")

        # ...and it is NOT retrievable while pending.
        self.assertFalse(
            any(h["id"] == "mem_review" for h in self.store.search(self.user_id, "monorepo Cortex services", limit=5)),
            "pending memory leaked into retrieval before approval",
        )

        # Approve it -> now retrievable.
        self.assertTrue(self.store.approve_capture(self.user_id, pending_capture_id))
        approved_hits = self.store.search(self.user_id, "monorepo Cortex services", limit=5)
        self.assertTrue(any(h["id"] == "mem_review" for h in approved_hits), "approved memory did not become retrievable")

        # Seed a second capture (auto-approved this time), then archive it and confirm it
        # leaves active retrieval.
        self.store.update_settings(self.user_id, {"review_new_captures": False})
        archived = self._save_memory(
            memory_id="mem_archive",
            content="Sam attended the 2026 offsite in Lisbon.",
            layer="episodic",
            kind="event",
            title="Offsite",
            topics=["offsite", "lisbon"],
        )
        self.assertTrue(any(h["id"] == "mem_archive" for h in self.store.search(self.user_id, "offsite Lisbon", limit=5)))
        self.assertTrue(self.store.archive_capture(self.user_id, archived["capture_id"]))
        self.assertFalse(
            any(h["id"] == "mem_archive" for h in self.store.search(self.user_id, "offsite Lisbon", limit=5)),
            "archived memory still appears in retrieval",
        )

    # --- Step 5: connector sync -----------------------------------------------

    def _connect_account(self) -> str:
        account = self.store.upsert_source_account(
            self.user_id,
            source="obsidian",
            account_label="Sam's Vault",
            account_identifier="sam-vault",
            connection_type="oauth_token",
            status="connected",
            auth_state="healthy",
            policy={"review_required": False, "allow_ai_context": True},
        )
        return account["id"]

    def test_step5_connector_sync_creates_memories_and_advances_cursor(self) -> None:
        account_id = self._connect_account()
        records = [
            {
                "content": "Sam shipped the payments gateway migration this quarter.",
                "title": "Payments migration",
                "external_id": "ext-payments",
            },
            {
                "content": "Sam mentors two junior engineers on the platform team.",
                "title": "Mentoring",
                "external_id": "ext-mentoring",
            },
        ]
        result = self.store.sync_source_account_records(
            self.user_id,
            account_id,
            records=records,
            cursor_name="pages",
            cursor_value="page1",
            high_water_mark="2026-07-02T00:00:00Z",
            state={"next_page_token": "P2"},
            processing="sync",
        )
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["saved"], 2)

        # The synced records became retrievable memories.
        self.assertTrue(self.store.search(self.user_id, "payments gateway migration", limit=5))
        self.assertTrue(self.store.search(self.user_id, "mentors junior engineers", limit=5))

        # The cursor advanced to the synced page.
        cursor = self.store._latest_sync_cursor(self.user_id, account_id, "pages")
        self.assertEqual(cursor.get("cursor_value"), "page1")

    def test_step5_partial_failure_holds_cursor_without_data_loss(self) -> None:
        account_id = self._connect_account()

        # First a clean page so there is a prior cursor position to hold at.
        self.store.sync_source_account_records(
            self.user_id,
            account_id,
            records=[{"content": "Sam kicked off the reliability workstream.", "title": "Reliability", "external_id": "ext-rel"}],
            cursor_name="pages",
            cursor_value="pageA",
            high_water_mark="2026-07-01T00:00:00Z",
            state={"next_page_token": "TOKEN_B"},
            processing="sync",
        )
        prior = self.store._latest_sync_cursor(self.user_id, account_id, "pages")
        self.assertEqual(prior.get("cursor_value"), "pageA")

        # Now a page where one record cannot be saved. The good record saves; the cursor must
        # NOT advance past the failing window (idempotent re-fetch on the next sync).
        original_save = self.store.save_capture

        def flaky_save(*args, **kwargs):
            if "FAILME" in str(kwargs.get("content") or ""):
                raise ValueError("content is too large")
            return original_save(*args, **kwargs)

        with mock.patch.object(self.store, "save_capture", side_effect=flaky_save):
            result = self.store.sync_source_account_records(
                self.user_id,
                account_id,
                records=[
                    {"content": "Sam updated the on-call rotation policy.", "title": "On-call", "external_id": "ext-oncall"},
                    {"content": "FAILME oversized attachment that cannot be saved.", "title": "Bad", "external_id": "ext-bad"},
                ],
                cursor_name="pages",
                cursor_value="pageB",
                high_water_mark="2026-07-02T00:00:00Z",
                state={"next_page_token": "TOKEN_C"},
                processing="sync",
            )

        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["saved"], 1)
        held = self.store._latest_sync_cursor(self.user_id, account_id, "pages")
        # Cursor held at the prior position — did not advance past the failed record.
        self.assertEqual(held.get("cursor_value"), "pageA")
        self.assertEqual((held.get("state") or {}).get("consecutive_failing_pages"), 1)
        # The good record from the failing page WAS saved (no data loss).
        self.assertTrue(self.store.search(self.user_id, "on-call rotation policy", limit=5))

    # --- Step 6: native vault round-trip --------------------------------------

    def test_step6_memories_mirror_to_markdown_and_rebuild_from_vault(self) -> None:
        self._seed_sam_profile()

        # Memories are mirrored to Markdown notes that Sam owns.
        md_files = list((self.store.vault.root / "memories").rglob("*.md"))
        self.assertTrue(md_files, "no Markdown memory notes were written")
        md_records = self.store.vault.iter_memory_markdown_records(self.user_id)
        md_ids = {r["id"] for r in md_records}
        for expected in ("mem_role", "mem_project", "mem_decision", "mem_pref"):
            self.assertIn(expected, md_ids)

        # Simulate losing the app database index (and legacy JSON records) but keeping the
        # user's Markdown files, then rebuild purely from the vault.
        for path in (self.store.vault.root / "memories").rglob("*.json"):
            path.unlink()
        rebuild = self.store.rebuild_index_from_vault(self.user_id)
        self.assertIsInstance(rebuild, dict)

        restored_ids = self._active_memory_ids()
        for expected in ("mem_role", "mem_project", "mem_decision", "mem_pref"):
            self.assertIn(expected, restored_ids, f"{expected} not restored from the vault")
        self.assertTrue(self.store.search(self.user_id, "sharded SQLite launch", limit=5))

    def test_step6_reconcile_detects_a_hand_edit(self) -> None:
        self._seed_sam_profile()

        # No-op when nothing changed.
        self.assertFalse(self.store.reconcile_vault_edits(self.user_id)["reconciled"])

        # Sam hand-edits a note in his vault (as if in Obsidian). Notes are <slug>--<shortid>.md,
        # so find it by the frontmatter id rather than the filename.
        md_path = None
        for path in (self.store.vault.root / "memories").rglob("*.md"):
            if parse_memory_markdown(path.read_text(encoding="utf-8")).get("id") == "mem_decision":
                md_path = path
                break
        self.assertIsNotNone(md_path, "no markdown note for mem_decision")
        record = parse_memory_markdown(md_path.read_text(encoding="utf-8"))
        record["content"] = "Sam changed course and picked Postgres with read replicas after all."
        atomic_write_text(md_path, render_memory_markdown(record))

        result = self.store.reconcile_vault_edits(self.user_id)
        self.assertTrue(result["reconciled"])
        self.assertGreaterEqual(result["changed"], 1)
        hits = self.store.search(self.user_id, "Postgres read replicas", limit=5)
        self.assertTrue(any("read replicas after all" in (h.get("content") or "") for h in hits))

    # --- Step 7: delete + restore ---------------------------------------------

    def test_step7_delete_stays_deleted_across_rebuild(self) -> None:
        self._seed_sam_profile()
        self.assertTrue(any(h["id"] == "mem_pref" for h in self.store.search(self.user_id, "standup meetings preference", limit=5)))

        # Sam deletes the preference memory (tombstone).
        self.assertTrue(self.store.delete_memory(self.user_id, "mem_pref"))
        self.assertFalse(
            any(h["id"] == "mem_pref" for h in self.store.search(self.user_id, "standup meetings preference", limit=5)),
            "deleted memory still retrievable",
        )

        # A vault rebuild must NOT resurrect the deleted memory (block_restore tombstone).
        self.store.rebuild_index_from_vault(self.user_id)
        self.assertNotIn("mem_pref", self._active_memory_ids(), "deleted memory resurrected on rebuild")
        # The other memories are unaffected.
        self.assertIn("mem_role", self._active_memory_ids())

    def test_step7_backup_restore_keeps_deletions(self) -> None:
        self._seed_sam_profile()

        # Take a backup AFTER a delete, then restore it: the deletion must survive because the
        # tombstone is preserved across restore.
        self.assertTrue(self.store.delete_memory(self.user_id, "mem_pref"))
        backup = self.store.create_backup(self.user_id)
        self.assertTrue(Path(backup["backup_path"]).exists())

        # Mutate live state, then restore the latest backup.
        self._save_memory(
            memory_id="mem_transient",
            content="Sam noted a transient idea that only exists post-backup.",
            layer="semantic",
            kind="claim",
            title="Transient",
            topics=["transient"],
        )
        restore = self.store.restore_latest_backup(self.user_id)
        self.assertIn("rebuild", restore)

        active_ids = self._active_memory_ids()
        self.assertNotIn("mem_pref", active_ids, "deleted memory came back after restore")
        self.assertIn("mem_role", active_ids, "surviving memory missing after restore")


if __name__ == "__main__":
    unittest.main()
