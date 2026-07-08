from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.extractor import extract_context
from backend.app.provenance import (
    AUTHOR_CLASSES,
    base_trust_score,
    classify_author,
    normalize_author_class,
    normalize_trust_score,
)
from backend.app.storage import CortexStore, connect


class ClassifyAuthorTests(unittest.TestCase):
    """The classification precedence is load-bearing: the live save path, the one-shot
    backfill, and vault rebuilds must all converge on the same answer for the same record."""

    def test_self_authored_wins_over_everything(self) -> None:
        self.assertEqual(
            classify_author(self_authored=True, source="claude", source_account_id="acct"),
            "user",
        )

    def test_connector_account_beats_capture_url(self) -> None:
        self.assertEqual(
            classify_author(source_account_id="acct", source_url="cortex-capture://x"),
            "connector",
        )

    def test_capture_url_is_user(self) -> None:
        self.assertEqual(classify_author(source_url="cortex-capture://abc", source="claude"), "user")

    def test_personal_layer_is_user_even_via_agent_transport(self) -> None:
        for layer in ("preference", "style", "negative"):
            self.assertEqual(classify_author(layer=layer, source="mcp"), "user")

    def test_source_label_buckets(self) -> None:
        self.assertEqual(classify_author(source="claude"), "agent")
        self.assertEqual(classify_author(source="capture"), "user")
        self.assertEqual(classify_author(source="some-new-thing"), "unknown")

    def test_provenance_account_id_counts_as_connector(self) -> None:
        self.assertEqual(classify_author(provenance={"source_account_id": "a"}), "connector")

    def test_normalizers(self) -> None:
        self.assertEqual(normalize_author_class(" USER "), "user")
        self.assertEqual(normalize_author_class("bogus"), "unknown")
        self.assertEqual(normalize_author_class(None), "unknown")
        for cls in AUTHOR_CLASSES:
            self.assertEqual(normalize_trust_score(None, cls), base_trust_score(cls))
        self.assertEqual(normalize_trust_score(2.0, "agent"), 1.0)
        self.assertEqual(normalize_trust_score(-1, "agent"), 0.0)
        self.assertEqual(normalize_trust_score(0.7, "agent"), 0.7)


class Phase0SubstrateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "phase0-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _capture(self, content: str, *, source: str) -> dict:
        extracted = extract_context(content, source)
        return self.store.save_capture(
            user_id=self.user_id,
            content=content,
            source=source,
            source_url=None,
            title="",
            extracted=extracted,
        )

    def _memory_rows(self) -> dict[str, sqlite3.Row]:
        with connect(self.db_path) as conn:
            return {
                row["id"]: row
                for row in conn.execute(
                    "SELECT * FROM memories WHERE user_id = ?", (self.user_id,)
                ).fetchall()
            }

    # -- schema / guard ------------------------------------------------------------------

    def test_schema_has_provenance_substrate(self) -> None:
        with connect(self.db_path) as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(memories)")}
            self.assertIn("author_class", cols)
            self.assertIn("trust_score", cols)
            tables = {
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            self.assertIn("agent_sessions", tables)
            self.assertIn("context_packs", tables)

    def test_guard_is_idempotent_on_reopen(self) -> None:
        # Re-constructing the store re-runs _ensure_provenance_substrate on an upgraded DB.
        again = CortexStore(self.db_path, self.root / "vault")
        with connect(self.db_path) as conn:
            cols = [row["name"] for row in conn.execute("PRAGMA table_info(memories)")]
        self.assertEqual(cols.count("author_class"), 1)
        self.assertEqual(cols.count("trust_score"), 1)

    # -- live save path ------------------------------------------------------------------

    def test_live_save_classifies_agent_and_user(self) -> None:
        self._capture(
            "We decided to migrate the Atlas deploy pipeline to Kubernetes next quarter.",
            source="claude",
        )
        rows = self._memory_rows()
        self.assertTrue(rows)
        for row in rows.values():
            layer = row["layer"] or ""
            expected = "user" if layer in ("preference", "style", "negative") else "agent"
            self.assertEqual(row["author_class"], expected, dict(row))
            self.assertEqual(row["trust_score"], base_trust_score(expected))

    # -- backfill ------------------------------------------------------------------------

    def test_backfill_classifies_legacy_rows_once(self) -> None:
        self._capture("Legacy fact: the API gateway timeout is 30 seconds.", source="claude")
        # Simulate pre-Phase-0 rows: reset to the schema default.
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE memories SET author_class = 'unknown', trust_score = 0.5 WHERE user_id = ?",
                (self.user_id,),
            )
        CortexStore(self.db_path, self.root / "vault")  # reopen triggers backfill
        rows = self._memory_rows()
        self.assertTrue(rows)
        for row in rows.values():
            self.assertNotEqual(row["author_class"], "unknown", dict(row))
        # Idempotency: a manual class set later must never be clobbered by a reopen.
        some_id = next(iter(rows))
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE memories SET author_class = 'connector', trust_score = 0.8 WHERE id = ?",
                (some_id,),
            )
        CortexStore(self.db_path, self.root / "vault")
        self.assertEqual(self._memory_rows()[some_id]["author_class"], "connector")

    # -- vault rebuild convergence ---------------------------------------------------------

    def test_vault_rebuild_preserves_author_class_and_rederives_trust(self) -> None:
        self._capture(
            "The design review concluded we keep SQLite as the index store.",
            source="claude",
        )
        before = {
            mid: (row["author_class"], row["trust_score"])
            for mid, row in self._memory_rows().items()
        }
        self.store.rebuild_index_from_vault(self.user_id)
        after = {
            mid: (row["author_class"], row["trust_score"])
            for mid, row in self._memory_rows().items()
        }
        self.assertEqual(before, after)

    def test_rebuild_rederives_unknown_from_durable_fields(self) -> None:
        # A vault record missing author_class (legacy note) must classify from source/layer.
        self._capture("Fact without persisted authorship metadata.", source="claude")
        # Strip author_class from the vault JSON records to simulate pre-Phase-0 notes.
        for path in (self.root / "vault" / "memories").rglob("*.json"):
            record = json.loads(path.read_text())
            record.pop("author_class", None)
            path.write_text(json.dumps(record))
        for path in (self.root / "vault" / "memories").rglob("*.md"):
            text = path.read_text()
            path.write_text(
                "\n".join(line for line in text.splitlines() if not line.startswith("author_class:"))
            )
        self.store.rebuild_index_from_vault(self.user_id)
        for row in self._memory_rows().values():
            self.assertNotEqual(row["author_class"], "unknown", dict(row))

    # -- frontmatter contract ---------------------------------------------------------------

    def test_markdown_frontmatter_has_author_class_never_trust_score(self) -> None:
        self._capture("Frontmatter contract fact for the vault notes.", source="claude")
        notes = list((self.root / "vault" / "memories").rglob("*.md"))
        self.assertTrue(notes)
        text = "\n".join(path.read_text() for path in notes)
        self.assertIn("author_class:", text)
        self.assertNotIn("trust_score", text)

    def test_hand_edited_author_class_does_not_trigger_reconcile(self) -> None:
        self._capture("Reconcile signature fact.", source="claude")
        self.store.ensure_vault_backfilled(self.user_id)
        clean = self.store.reconcile_vault_edits(self.user_id)
        self.assertFalse(clean["reconciled"])
        # Editing ONLY author_class in a note must not count as user-editable divergence.
        note = next((self.root / "vault" / "memories").rglob("*.md"))
        note.write_text(note.read_text().replace("author_class: agent", "author_class: user"))
        result = self.store.reconcile_vault_edits(self.user_id)
        self.assertFalse(result["reconciled"], result)

    # -- context packs ------------------------------------------------------------------------

    def test_context_pack_round_trip_and_immutability(self) -> None:
        payload = json.dumps({"pack": True, "items": [1, 2]}, sort_keys=True).encode()
        sha = hashlib.sha256(payload).hexdigest()
        path = self.store.vault.write_context_pack(sha, payload)
        self.assertEqual(self.store.vault.read_context_pack(sha), payload)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), sha)
        # Immutable: a second write with different bytes must not clobber the original.
        self.store.vault.write_context_pack(sha, b"tampered")
        self.assertEqual(path.read_bytes(), payload)
        self.assertIsNone(self.store.vault.read_context_pack("0" * 64))

    def test_reconcile_and_rebuild_ignore_context_packs(self) -> None:
        self._capture("Packs must be invisible to memory reconciliation.", source="claude")
        payload = b'{"machine": "owned"}'
        sha = hashlib.sha256(payload).hexdigest()
        self.store.vault.write_context_pack(sha, payload)
        self.store.ensure_vault_backfilled(self.user_id)
        result = self.store.reconcile_vault_edits(self.user_id)
        self.assertFalse(result["reconciled"], result)
        self.store.rebuild_index_from_vault(self.user_id)
        # Pack file untouched by the rebuild.
        self.assertEqual(self.store.vault.read_context_pack(sha), payload)


if __name__ == "__main__":
    unittest.main()
