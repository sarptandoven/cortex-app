from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from backend.app import mcp_tools
from backend.app.database import connect, init_db
from backend.app.storage import CortexStore


class ProofOfBeliefTests(unittest.TestCase):
    """M2 invariants: bi-temporal reconstruction, sealed snapshots, and compact tamper proofs."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "proof-user"
        self.store.update_settings(
            self.user_id,
            {"review_new_captures": False, "allow_pending_in_context": True},
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(
        self,
        memory_id: str,
        content: str,
        *,
        valid_from: str,
        valid_to: str | None = None,
        captured_at: str = "2026-01-01T00:00:00+00:00",
        user_id: str | None = None,
    ) -> dict:
        owner = user_id or self.user_id
        self.store.update_settings(owner, {"review_new_captures": False})
        return self.store.save_capture(
            user_id=owner,
            content=content,
            source="note",
            source_url=f"cortex-capture://{memory_id}",
            title=memory_id,
            extracted={
                "_timestamp": captured_at,
                "summary": content,
                "records": [
                    {
                        "id": memory_id,
                        "kind": "decision",
                        "layer": "decision",
                        "content": content,
                        "summary": content,
                        "confidence": "confirmed",
                        "importance": 4,
                        "occurred_at": valid_from,
                        "valid_from": valid_from,
                        "valid_to": valid_to,
                        "topics": ["launch", "database"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
            cite_capture_provenance=True,
        )

    def _belief_events(self, memory_id: str, *, user_id: str | None = None) -> list[dict]:
        owner = user_id or self.user_id
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, event_type, metadata_json, created_at
                FROM memory_events
                WHERE user_id = ? AND object_id = ?
                  AND event_type IN ('belief_recorded', 'belief_revised', 'belief_superseded')
                ORDER BY created_at ASC, rowid ASC
                """,
                (owner, memory_id),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "event_type": row["event_type"],
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def test_new_belief_is_sealed_and_merkle_verified(self) -> None:
        self._seed(
            "mem_launch_postgres",
            "The launch database is Postgres.",
            valid_from="2026-01-01T00:00:00+00:00",
        )

        proof = self.store.get_belief_proof(
            self.user_id,
            "launch database",
            valid_at="2026-02-01T00:00:00+00:00",
        )

        self.assertFalse(proof["abstained"], proof)
        self.assertEqual([item["memory_id"] for item in proof["beliefs"]], ["mem_launch_postgres"])
        belief = proof["beliefs"][0]
        self.assertTrue(belief["sealed"])
        self.assertTrue(belief["snapshot_verified"])
        self.assertTrue(belief["record_receipt_event_id"])
        self.assertTrue(proof["proof"]["verified"], proof["proof"])
        self.assertTrue(proof["proof"]["receipts"])
        self.assertTrue(self.store.verify_belief_proof(proof)["verified"])

        pinned = self.store.get_belief_proof(
            self.user_id,
            "launch database",
            valid_at=proof["valid_at"],
            known_at=proof["known_at"],
            expected_head=proof["proof"]["chain_head_at_known_at"],
        )
        self.assertTrue(pinned["proof"]["anchor_matches"])
        self.assertTrue(pinned["proof"]["verified"])

    def test_reconstructs_valid_time_at_multiple_transaction_times(self) -> None:
        self._seed(
            "mem_launch_sqlite",
            "The launch database is SQLite.",
            valid_from="2025-01-01T00:00:00+00:00",
            captured_at="2025-01-01T00:00:00+00:00",
        )
        old_recorded_at = self._belief_events("mem_launch_sqlite")[0]["created_at"]
        self._seed(
            "mem_launch_postgres",
            "The launch database is Postgres.",
            valid_from="2026-06-01T00:00:00+00:00",
            captured_at="2026-06-01T00:00:00+00:00",
        )
        self.assertTrue(
            self.store.resolve_conflict(
                self.user_id,
                stale_id="mem_launch_sqlite",
                current_id="mem_launch_postgres",
            )
        )
        old_events = self._belief_events("mem_launch_sqlite")
        superseded_at = next(
            event["created_at"] for event in old_events if event["event_type"] == "belief_superseded"
        )

        before_revision = self.store.get_belief_proof(
            self.user_id,
            "launch database",
            valid_at="2026-07-01T00:00:00+00:00",
            known_at=old_recorded_at,
        )
        self.assertEqual(
            [belief["memory_id"] for belief in before_revision["beliefs"]],
            ["mem_launch_sqlite"],
            before_revision,
        )

        historical = self.store.get_belief_proof(
            self.user_id,
            "launch database",
            valid_at="2026-03-01T00:00:00+00:00",
            known_at=superseded_at,
        )
        self.assertEqual(
            [belief["memory_id"] for belief in historical["beliefs"]],
            ["mem_launch_sqlite"],
            historical,
        )
        self.assertEqual(historical["beliefs"][0]["valid_to"], "2026-06-01T00:00:00+00:00")

        current = self.store.get_belief_proof(
            self.user_id,
            "launch database",
            valid_at="2026-07-01T00:00:00+00:00",
            known_at=superseded_at,
        )
        self.assertEqual(
            [belief["memory_id"] for belief in current["beliefs"]],
            ["mem_launch_postgres"],
            current,
        )
        for result in (before_revision, historical, current):
            self.assertTrue(result["proof"]["verified"], result["proof"])

    def test_historical_snapshot_is_discoverable_after_same_id_text_changes(self) -> None:
        self._seed(
            "mem_same_id_revision",
            "The historical secret database is Postgres.",
            valid_from="2026-01-01T00:00:00+00:00",
        )
        original_recorded_at = self._belief_events("mem_same_id_revision")[0]["created_at"]
        self._seed(
            "mem_same_id_revision",
            "The storage plan now uses an encrypted object ledger.",
            valid_from="2026-06-01T00:00:00+00:00",
            captured_at="2026-06-01T00:00:00+00:00",
        )
        events = self._belief_events("mem_same_id_revision")
        self.assertEqual(
            [event["event_type"] for event in events],
            ["belief_recorded", "belief_revised"],
        )
        self.assertGreater(events[1]["created_at"], events[0]["created_at"])

        # More than the historical candidate cap of newer matching receipts must not hide the old
        # snapshot when known_at predates every one of them. The SQL cutoff must happen before LIMIT.
        base = datetime.fromisoformat(original_recorded_at)
        noise_rows = []
        noise_index_rows = []
        for index in range(205):
            snapshot = self.store._belief_snapshot(
                {
                    "id": f"mem_future_noise_{index}",
                    "kind": "decision",
                    "layer": "decision",
                    "content": f"The historical secret database future noise {index}.",
                    "summary": "",
                    "source": "note",
                    "confidence": "confirmed",
                    "importance": 3,
                    "valid_from": "2026-01-01T00:00:00+00:00",
                    "topics": ["historical", "secret", "database"],
                    "entity_ids": [],
                }
            )
            metadata = {
                "schema_version": 1,
                "snapshot_sha256": self.store._belief_snapshot_sha(snapshot),
                "snapshot": snapshot,
                "reason": "future-noise",
            }
            event = {
                "id": f"evt_future_noise_{index}",
                "object_id": snapshot["memory_id"],
                "object_type": "memory",
                "event_type": "belief_recorded",
                "metadata": metadata,
                "created_at": (base + timedelta(seconds=index + 1)).isoformat(),
            }
            noise_rows.append(
                (
                    event["id"],
                    self.user_id,
                    event["object_id"],
                    event["object_type"],
                    event["event_type"],
                    json.dumps(metadata),
                    self.store._integrity_event_fingerprint(event),
                    event["created_at"],
                )
            )
            noise_index_rows.append(
                (
                    event["id"],
                    self.user_id,
                    snapshot["memory_id"],
                    snapshot["content"],
                    " ".join(snapshot["topics"]),
                    event["created_at"],
                )
            )
        with connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO memory_events
                (id, user_id, object_id, object_type, event_type, metadata_json,
                 fingerprint_sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                noise_rows,
            )
            conn.executemany(
                """
                INSERT INTO belief_snapshot_fts
                (event_id, user_id, memory_id, content, topics, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                noise_index_rows,
            )

        historical = self.store.get_belief_proof(
            self.user_id,
            "historical secret database",
            valid_at="2026-02-01T00:00:00+00:00",
            known_at=original_recorded_at,
            limit=1,
        )
        self.assertFalse(historical["abstained"], historical)
        self.assertEqual(historical["beliefs"][0]["memory_id"], "mem_same_id_revision")
        self.assertIn("Postgres", historical["beliefs"][0]["content"])
        self.assertTrue(historical["proof"]["verified"], historical["proof"])

    def test_verifier_rejects_modified_belief_and_merkle_path(self) -> None:
        self._seed(
            "mem_launch_postgres",
            "The launch database is Postgres.",
            valid_from="2026-01-01T00:00:00+00:00",
        )
        proof = self.store.get_belief_proof(self.user_id, "launch database")

        changed_belief = copy.deepcopy(proof)
        changed_belief["beliefs"][0]["content"] = "The launch database is secretly SQLite."
        belief_verification = self.store.verify_belief_proof(changed_belief)
        self.assertFalse(belief_verification["verified"])
        self.assertTrue(
            any("returned snapshot mismatch" in error for error in belief_verification["errors"]),
            belief_verification,
        )

        changed_path = copy.deepcopy(proof)
        receipt = changed_path["proof"]["receipts"][0]
        if receipt["merkle_path"]:
            receipt["merkle_path"][0]["hash"] = "0" * 64
        else:
            receipt["fingerprint"] = "0" * 64
        path_verification = self.store.verify_belief_proof(changed_path)
        self.assertFalse(path_verification["verified"])
        self.assertTrue(
            any(
                "Merkle inclusion mismatch" in error or "fingerprint mismatch" in error
                for error in path_verification["errors"]
            ),
            path_verification,
        )

        changed_chain = copy.deepcopy(proof)
        changed_chain["proof"]["chain_segment"][0][1] = "f" * 64
        chain_verification = self.store.verify_belief_proof(changed_chain)
        self.assertFalse(chain_verification["verified"])
        self.assertTrue(
            any("chain segment head mismatch" in error for error in chain_verification["errors"]),
            chain_verification,
        )

        suppressed = copy.deepcopy(proof)
        suppressed["beliefs"] = []
        suppressed["proof"]["receipts"] = []
        suppression_verification = self.store.verify_belief_proof(suppressed)
        self.assertFalse(suppression_verification["verified"])
        self.assertFalse(suppression_verification["selection_manifest_matches"])
        self.assertFalse(suppression_verification["completeness_verified"])
        self.assertIn(
            "selection manifest does not match returned beliefs",
            suppression_verification["errors"],
        )

        anchored = self.store.verify_belief_proof(
            proof,
            expected_head=proof["proof"]["chain_head_at_known_at"],
        )
        self.assertTrue(anchored["anchored_verified"], anchored)
        self.assertEqual(anchored["anchor_source"], "external")
        wrong_anchor = self.store.verify_belief_proof(proof, expected_head="0" * 64)
        self.assertFalse(wrong_anchor["verified"])
        self.assertFalse(wrong_anchor["anchored_verified"])

    def test_external_chain_pin_detects_coherent_event_rewrite(self) -> None:
        self._seed(
            "mem_launch_postgres",
            "The launch database is Postgres.",
            valid_from="2026-01-01T00:00:00+00:00",
        )
        original = self.store.get_belief_proof(self.user_id, "launch database")
        pinned_head = original["proof"]["chain_head_at_known_at"]

        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT id, metadata_json FROM memory_events
                WHERE user_id = ? AND object_id = ? AND event_type = 'belief_recorded'
                """,
                (self.user_id, "mem_launch_postgres"),
            ).fetchone()
            metadata = json.loads(row["metadata_json"])
            metadata["snapshot"]["content"] = "The launch database is SQLite."
            metadata["snapshot"]["summary"] = "The launch database is SQLite."
            metadata["snapshot_sha256"] = self.store._belief_snapshot_sha(metadata["snapshot"])
            conn.execute(
                "UPDATE memory_events SET metadata_json = ? WHERE id = ?",
                (json.dumps(metadata), row["id"]),
            )

        rewritten = self.store.get_belief_proof(
            self.user_id,
            "launch database",
            expected_head=pinned_head,
        )
        self.assertFalse(rewritten["proof"]["anchor_matches"])
        self.assertFalse(rewritten["proof"]["verified"])
        self.assertIn(
            "expected chain head does not match the known-at anchor",
            rewritten["proof"]["verification_errors"],
        )

    def test_legacy_unsealed_rows_are_explicit_and_fail_verification(self) -> None:
        self._seed(
            "mem_legacy_postgres",
            "The legacy launch database is Postgres.",
            valid_from="2026-01-01T00:00:00+00:00",
        )
        with connect(self.db_path) as conn:
            conn.execute(
                """
                DELETE FROM memory_events
                WHERE user_id = ? AND object_id = ?
                  AND event_type IN ('belief_recorded', 'belief_revised', 'belief_superseded')
                """,
                (self.user_id, "mem_legacy_postgres"),
            )

        proof = self.store.get_belief_proof(self.user_id, "legacy launch database")
        self.assertFalse(proof["abstained"])
        self.assertFalse(proof["beliefs"][0]["sealed"])
        self.assertFalse(proof["proof"]["verified"])
        self.assertTrue(proof["warnings"])
        self.assertTrue(
            any("legacy/unsealed" in error for error in proof["proof"]["verification_errors"]),
            proof,
        )

    def test_proof_is_tenant_isolated_and_gdpr_deletion_removes_receipts(self) -> None:
        self._seed(
            "mem_user_one",
            "The launch database for tenant one is Postgres.",
            valid_from="2026-01-01T00:00:00+00:00",
        )
        self._seed(
            "mem_user_two",
            "The launch database for tenant two is SQLite.",
            valid_from="2026-01-01T00:00:00+00:00",
            user_id="other-proof-user",
        )

        proof = self.store.get_belief_proof(self.user_id, "launch database tenant")
        ids = {belief["memory_id"] for belief in proof["beliefs"]}
        self.assertIn("mem_user_one", ids)
        self.assertNotIn("mem_user_two", ids)
        receipt_objects = {
            receipt["event"]["object_id"] for receipt in proof["proof"]["receipts"]
        }
        self.assertNotIn("mem_user_two", receipt_objects)

        self.store.delete_user_data(self.user_id, include_backups=False)
        deleted = self.store.get_belief_proof(self.user_id, "launch database tenant")
        self.assertTrue(deleted["abstained"])
        self.assertEqual(deleted["beliefs"], [])
        self.assertEqual(deleted["proof"]["event_count_at_known_at"], 0)
        self.assertTrue(deleted["proof"]["verified"])
        with connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM belief_snapshot_fts WHERE user_id = ?",
                    (self.user_id,),
                ).fetchone()[0],
                0,
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT revision FROM memory_event_revisions WHERE user_id = ?",
                    (self.user_id,),
                ).fetchone()
            )

    def test_vault_rebuild_deduplicates_snapshot_receipts(self) -> None:
        self._seed(
            "mem_rebuild_postgres",
            "The rebuild launch database is Postgres.",
            valid_from="2026-01-01T00:00:00+00:00",
        )
        before = self._belief_events("mem_rebuild_postgres")
        self.store.rebuild_index_from_vault(self.user_id)
        self.store.rebuild_index_from_vault(self.user_id)
        after = self._belief_events("mem_rebuild_postgres")
        self.assertEqual(
            [(event["event_type"], event["metadata"].get("snapshot_sha256")) for event in after],
            [(event["event_type"], event["metadata"].get("snapshot_sha256")) for event in before],
        )

    def test_mcp_tools_are_read_scoped_and_preserve_temporal_arguments(self) -> None:
        self._seed(
            "mem_mcp_postgres",
            "The MCP launch database is Postgres.",
            valid_from="2026-01-01T00:00:00+00:00",
        )
        proof = mcp_tools.call_tool(
            self.store,
            self.user_id,
            "get_belief_proof",
            {
                "topic": "MCP launch database",
                "valid_at": "2026-02-01T00:00:00+00:00",
                "known_at": "2099-01-01T00:00:00+00:00",
                "limit": 5,
            },
            token_scopes=["read"],
        )
        self.assertEqual(proof["valid_at"], "2026-02-01T00:00:00+00:00")
        self.assertEqual(proof["known_at"], "2099-01-01T00:00:00+00:00")
        verified = mcp_tools.call_tool(
            self.store,
            self.user_id,
            "verify_belief_proof",
            {"proof": proof, "expected_head": proof["proof"]["chain_head_at_known_at"]},
            token_scopes=["read"],
        )
        self.assertTrue(verified["verified"], verified)
        self.assertTrue(verified["anchored_verified"], verified)
        self.assertIn("get_belief_proof", mcp_tools.READ_TOOLS)
        self.assertIn("verify_belief_proof", mcp_tools.READ_TOOLS)


if __name__ == "__main__":
    unittest.main()
