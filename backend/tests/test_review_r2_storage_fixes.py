from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from backend.app.database import connect, init_db
from backend.app.storage import CortexStore, now_iso, _normalize_validity_bound


class ReviewR2StorageFixTests(unittest.TestCase):
    """Regression coverage for four just-landed data-integrity fixes in storage.py:

    1) reconcile_vault_edits mass-delete floor guard (refuse implausibly large sweeps).
    2) _backfill_memory_markdown must NOT mark the per-user flag done on a write failure.
    3) _enqueue_embed_memory_job must only revive TERMINAL jobs, never a 'running' one.
    4) _normalize_validity_bound canonicalizes validity bounds and the write path applies it.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        # Deterministic lexical retrieval (no vector backend needed for search assertions).
        self.store._vector_ready = lambda conn: False
        self.user_id = "r2-user"
        self.store.update_settings(
            self.user_id,
            {"review_new_captures": False, "allow_pending_in_context": True},
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ---- helpers ---------------------------------------------------------

    def _seed_memories(self, n: int, *, extra_record: dict | None = None) -> list[str]:
        """Seed N distinct memories (ids mem_0..mem_{n-1}) via save_capture(extracted=...)
        and confirm each one is present in the memories table."""
        ids: list[str] = []
        for i in range(n):
            record = {
                "id": f"mem_{i}",
                "kind": "claim",
                "layer": "semantic",
                "content": f"Fact number {i} about widget {i}.",
                "confidence": "confirmed",
                "importance": 3,
                "topics": ["widget"],
                "entity_ids": [],
            }
            if i == 0 and extra_record:
                record.update(extra_record)
            self.store.save_capture(
                user_id=self.user_id,
                content=f"Fact number {i} about widget {i}.",
                source="obsidian",
                source_url=f"local-file://note-{i}.md",
                title=f"Note {i}",
                extracted={
                    "_timestamp": now_iso(),
                    "summary": f"summary {i}",
                    "records": [record],
                    "tasks": [],
                    "entities": [],
                },
            )
            ids.append(f"mem_{i}")
        # Confirm every seeded id landed in the memories table.
        with connect(self.db_path) as conn:
            for memory_id in ids:
                row = conn.execute(
                    "SELECT id FROM memories WHERE id = ? AND user_id = ?",
                    (memory_id, self.user_id),
                ).fetchone()
                self.assertIsNotNone(row, f"seeded memory {memory_id} missing from memories table")
        return ids

    def _memory_ids_in_db(self) -> set[str]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id FROM memories WHERE user_id = ?", (self.user_id,)
            ).fetchall()
        return {row["id"] for row in rows}

    def _memories_dir(self) -> Path:
        return self.store.vault.root / "memories"

    # ---- Fix 1: reconcile mass-delete FLOOR GUARD ------------------------

    def test_mass_delete_blocked_when_notes_folder_cleared(self) -> None:
        self._seed_memories(12)
        self.store.ensure_vault_backfilled(self.user_id)  # set the markdown-backfill flag
        self.assertTrue(self.store.search(self.user_id, "widget", limit=10))

        # Delete every note file but KEEP the memories/ directory present (simulates a sync
        # client momentarily clearing the folder). 12 removals >= 10 absolute AND == 100% >= 50%.
        memories_dir = self._memories_dir()
        deleted = 0
        for path in memories_dir.rglob("*.md"):
            path.unlink()
            deleted += 1
        self.assertGreaterEqual(deleted, 12, "expected the seeded notes to have been written")
        self.assertTrue(memories_dir.is_dir(), "memories/ directory must remain present")

        result = self.store.reconcile_vault_edits(self.user_id)
        self.assertIs(result.get("mass_delete_blocked"), True, result)
        self.assertEqual(result["removed"], 0, result)

        # All 12 memories survive in the DB and stay searchable.
        self.assertEqual(len(self._memory_ids_in_db()), 12)
        self.assertTrue(self.store.search(self.user_id, "widget", limit=10))

    def test_missing_memories_dir_never_deletes(self) -> None:
        self._seed_memories(3)
        self.store.ensure_vault_backfilled(self.user_id)

        # Whole memories/ directory gone (transiently unmounted external drive). Must never be
        # read as "the user deleted everything".
        shutil.rmtree(self._memories_dir())
        self.assertFalse(self._memories_dir().exists())

        result = self.store.reconcile_vault_edits(self.user_id)
        self.assertEqual(result["removed"], 0, result)
        self.assertEqual(self._memory_ids_in_db(), {"mem_0", "mem_1", "mem_2"})

    def test_small_deletion_is_still_honored(self) -> None:
        self._seed_memories(12)
        self.store.ensure_vault_backfilled(self.user_id)

        # Delete exactly ONE note. Small deletions are always honored (below the floor guard).
        matches = list(self._memories_dir().rglob("mem_0.md"))
        self.assertTrue(matches, "no markdown note found for mem_0")
        matches[0].unlink()

        result = self.store.reconcile_vault_edits(self.user_id)
        self.assertEqual(result["removed"], 1, result)
        self.assertFalse(result.get("mass_delete_blocked"), result)

        remaining = self._memory_ids_in_db()
        self.assertNotIn("mem_0", remaining)
        self.assertEqual(remaining, {f"mem_{i}" for i in range(1, 12)})

    # ---- Fix 2: backfill NOT marked done on write failure ----------------

    def test_backfill_not_marked_done_when_note_write_fails(self) -> None:
        self._seed_memories(1)
        # Remove the note so mem_0 is a DB row with no note. Do NOT call ensure_vault_backfilled
        # (that would set the flag before the failing run under test).
        matches = list(self._memories_dir().rglob("mem_0.md"))
        self.assertTrue(matches, "no markdown note found for mem_0")
        matches[0].unlink()
        self.assertFalse(self.store.vault.has_memory_markdown("mem_0"))

        # Simulate a silently-swallowed write failure: _write_memory_markdown becomes a no-op.
        self.store.vault._write_memory_markdown = lambda record: None

        self.store._backfill_memory_markdown(self.user_id)

        # The per-user flag MUST stay unset so the migration retries (never lets reconcile treat
        # this note-less memory as a deletion), and the note is still absent.
        self.assertFalse(self.store.vault.markdown_backfill_done(self.user_id))
        self.assertFalse(self.store.vault.has_memory_markdown("mem_0"))

    # ---- Fix 3: embed-job re-enqueue only revives TERMINAL jobs ----------

    def _enqueue_once(self) -> dict:
        with connect(self.db_path) as conn:
            job = self.store._enqueue_embed_memory_job(
                conn,
                memory_id="m_embed",
                capture_id=None,
                user_id=self.user_id,
                content="hello world facts about widgets",
                summary=None,
                source="test",
                layer="semantic",
                topics=[],
                captured_at=now_iso(),
            )
            conn.commit()
        return job

    def _reenqueue_same(self) -> None:
        with connect(self.db_path) as conn:
            self.store._enqueue_embed_memory_job(
                conn,
                memory_id="m_embed",
                capture_id=None,
                user_id=self.user_id,
                content="hello world facts about widgets",
                summary=None,
                source="test",
                layer="semantic",
                topics=[],
                captured_at=now_iso(),
            )
            conn.commit()

    def _job_status_attempts(self, job_id: str) -> tuple[str, int]:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT status, attempts FROM memory_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        self.assertIsNotNone(row, f"job {job_id} missing")
        return row["status"], row["attempts"]

    def test_reenqueue_leaves_running_job_untouched(self) -> None:
        self.store._vector_ready = lambda conn: True
        self.store._memory_vector_current = lambda *a, **k: False

        job1 = self._enqueue_once()
        self.assertIsNotNone(job1)
        self.assertEqual(job1["status"], "queued")

        # Move the job into an actively-running lease held by a live worker.
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE memory_jobs SET status = 'running', attempts = 1, locked_by = 'worker-a', "
                "locked_until = '2099-01-01T00:00:00Z' WHERE id = ?",
                (job1["id"],),
            )
            conn.commit()

        # Re-enqueue must not touch a running job (no double-run, no attempt-counter reset).
        self._reenqueue_same()

        status, attempts = self._job_status_attempts(job1["id"])
        self.assertEqual(status, "running")
        self.assertEqual(attempts, 1)

    def test_reenqueue_revives_failed_job(self) -> None:
        self.store._vector_ready = lambda conn: True
        self.store._memory_vector_current = lambda *a, **k: False

        job1 = self._enqueue_once()
        self.assertIsNotNone(job1)
        self.assertEqual(job1["status"], "queued")

        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE memory_jobs SET status = 'failed', attempts = 3, "
                "completed_at = '2026-07-02T00:00:00+00:00' WHERE id = ?",
                (job1["id"],),
            )
            conn.commit()

        # A terminal (failed) job revives for a fresh run: back to queued, attempts reset to 0.
        self._reenqueue_same()

        status, attempts = self._job_status_attempts(job1["id"])
        self.assertEqual(status, "queued")
        self.assertEqual(attempts, 0)

    # ---- Fix 4: temporal validity normalization --------------------------

    def test_normalize_validity_bound_unit(self) -> None:
        self.assertEqual(
            _normalize_validity_bound("2026-07-02", end_of_day=True),
            "2026-07-02T23:59:59+00:00",
        )
        self.assertEqual(
            _normalize_validity_bound("2026-07-02", end_of_day=False),
            "2026-07-02T00:00:00+00:00",
        )
        self.assertEqual(
            _normalize_validity_bound("2026-07-02T23:59:59Z", end_of_day=True),
            "2026-07-02T23:59:59+00:00",
        )
        # Already normalized -> unchanged.
        self.assertEqual(
            _normalize_validity_bound("2026-07-02T10:00:00+00:00", end_of_day=True),
            "2026-07-02T10:00:00+00:00",
        )
        # Empty / None pass through unchanged.
        self.assertEqual(_normalize_validity_bound("", end_of_day=True), "")
        self.assertIsNone(_normalize_validity_bound(None, end_of_day=True))

    def test_valid_to_stored_normalized_and_still_retrievable(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._seed_memories(1, extra_record={"valid_to": today})

        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT valid_to FROM memories WHERE id = ? AND user_id = ?",
                ("mem_0", self.user_id),
            ).fetchone()
        self.assertIsNotNone(row, "mem_0 missing from memories table")
        # The write path must have normalized the date-only bound to end-of-day.
        self.assertEqual(row["valid_to"], f"{today}T23:59:59+00:00")

        # A still-valid memory on its final valid day must NOT be dropped from retrieval.
        hits = self.store.search(self.user_id, "widget", limit=10)
        self.assertTrue(any(h["id"] == "mem_0" for h in hits), hits)


if __name__ == "__main__":
    unittest.main()
