from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from backend.app import mcp_tools
from backend.app.database import connect, init_db
from backend.app.extractor import extract_context
from backend.app.storage import CortexStore


class PortableMemoryProtocolTests(unittest.TestCase):
    """M8 Phase 1: signed, tenant-rebound, idempotent memory portability."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.source_db = self.root / "source.db"
        self.target_db = self.root / "target.db"
        init_db(self.source_db)
        init_db(self.target_db)
        self.source = CortexStore(self.source_db, self.root / "source-vault")
        self.target = CortexStore(self.target_db, self.root / "target-vault")
        self.source._vector_ready = lambda conn: False
        self.target._vector_ready = lambda conn: False
        self.source_user = "portable-source"
        self.target_user = "portable-target"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, count: int = 2) -> None:
        for index in range(count):
            content = f"Portable decision {index}: the launch database is PostgreSQL."
            result = self.source.save_capture(
                user_id=self.source_user,
                content=content,
                source="note",
                source_url=f"note://portable/{index}",
                title=f"Portable {index}",
                extracted=extract_context(content),
                external_id=f"portable-{index}",
            )
            self.source.approve_capture(self.source_user, result["capture_id"])

    def _memory_rows(self) -> list[dict]:
        with connect(self.target_db) as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM memories WHERE user_id = ? ORDER BY id",
                    (self.target_user,),
                ).fetchall()
            ]

    def test_signed_export_has_stable_identity_and_supports_signer_pinning(self) -> None:
        self._seed(1)
        first = self.source.export_portable_bundle(self.source_user)
        second = self.source.export_portable_bundle(self.source_user)

        self.assertEqual(first["cortex_bundle_version"], "v2")
        self.assertEqual(first["protocol"]["name"], "cortex-portable-memory")
        self.assertEqual(first["signature"]["algorithm"], "ed25519")
        self.assertEqual(first["signature"]["key_id"], second["signature"]["key_id"])
        key_id = first["signature"]["key_id"]
        self.assertTrue(
            self.target.verify_portable_bundle(first, expected_signing_key_id=key_id)["verified"]
        )
        wrong = self.target.verify_portable_bundle(first, expected_signing_key_id="0" * 64)
        self.assertFalse(wrong["verified"])
        self.assertFalse(wrong["signer_matches"])

    def test_legacy_unsigned_bundle_still_verifies_but_cannot_be_imported(self) -> None:
        self._seed(1)
        signed = self.source.export_portable_bundle(self.source_user)
        legacy = copy.deepcopy(signed)
        legacy["cortex_bundle_version"] = "v1"
        legacy.pop("protocol")
        legacy.pop("integrity_proof")
        legacy.pop("signature")
        legacy["manifest"].pop("signing_key_id", None)

        verdict = self.target.verify_portable_bundle(legacy)
        self.assertTrue(verdict["verified"])
        self.assertTrue(verdict["legacy_bundle"])
        self.assertFalse(verdict["importable"])
        with self.assertRaises(ValueError):
            self.target.import_portable_bundle(self.target_user, legacy)

    def test_tampering_payload_manifest_proof_or_signature_is_rejected(self) -> None:
        self._seed(2)
        bundle = self.source.export_portable_bundle(self.source_user)
        variants = []

        payload = copy.deepcopy(bundle)
        payload["payload"]["memories"][0]["content"] = "tampered content"
        variants.append(payload)

        tenant = copy.deepcopy(bundle)
        tenant["manifest"]["user_id"] = "other-tenant"
        variants.append(tenant)

        proof = copy.deepcopy(bundle)
        proof["integrity_proof"]["event_fingerprints"][0] = "0" * 64
        variants.append(proof)

        signature = copy.deepcopy(bundle)
        original_first = signature["signature"]["value"][0]
        signature["signature"]["value"] = (
            ("A" if original_first != "A" else "B") + signature["signature"]["value"][1:]
        )
        variants.append(signature)

        noncanonical_signature = copy.deepcopy(bundle)
        noncanonical_signature["signature"]["value"] = (
            noncanonical_signature["signature"]["value"][:8]
            + "!"
            + noncanonical_signature["signature"]["value"][8:]
        )
        variants.append(noncanonical_signature)

        wrong_collection_type = copy.deepcopy(bundle)
        wrong_collection_type["payload"]["memories"] = "not-a-memory-list"
        variants.append(wrong_collection_type)

        for tampered in variants:
            with self.subTest(tamper=list(tampered.keys())):
                verdict = self.target.verify_portable_bundle(tampered)
                self.assertFalse(verdict["verified"])
                with self.assertRaises(ValueError):
                    self.target.import_portable_bundle(self.target_user, tampered)
        self.assertEqual(self._memory_rows(), [])

    def test_pinned_import_preserves_lineage_trust_and_is_idempotent(self) -> None:
        self._seed(2)
        bundle = self.source.export_portable_bundle(self.source_user)
        key_id = bundle["signature"]["key_id"]

        imported = self.target.import_portable_bundle(
            self.target_user,
            bundle,
            expected_signing_key_id=key_id,
        )
        self.assertEqual(imported["memories_inserted"], 2)
        self.assertEqual(imported["memories_skipped"], 0)
        rows = self._memory_rows()
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["user_id"], self.target_user)
            self.assertEqual(row["author_class"], "user")
            self.assertEqual(row["trust_score"], 1.0)
            provenance = json.loads(row["provenance_json"])
            lineage = provenance["portable_lineage"][-1]
            self.assertEqual(lineage["signing_key_id"], key_id)
            self.assertEqual(lineage["source_user_id"], self.source_user)
            self.assertTrue(lineage["signer_pinned"])
            self.assertIn(lineage["source_memory_id"], imported["memory_id_map"])

        replay = self.target.import_portable_bundle(
            self.target_user,
            bundle,
            expected_signing_key_id=key_id,
        )
        self.assertEqual(replay["memories_inserted"], 0)
        self.assertEqual(replay["memories_skipped"], 2)
        self.assertEqual(len(self._memory_rows()), 2)

    def test_unpinned_import_downgrades_remote_authorship(self) -> None:
        self._seed(1)
        bundle = self.source.export_portable_bundle(self.source_user)
        imported = self.target.import_portable_bundle(self.target_user, bundle)
        self.assertFalse(imported["signer_pinned"])
        row = self._memory_rows()[0]
        self.assertEqual(row["author_class"], "connector")
        self.assertLessEqual(row["trust_score"], 0.7)
        lineage = json.loads(row["provenance_json"])["portable_lineage"][-1]
        self.assertEqual(lineage["source_author_class"], "user")
        self.assertFalse(lineage["signer_pinned"])

    def test_vault_failure_rolls_back_database_import(self) -> None:
        self._seed(2)
        bundle = self.source.export_portable_bundle(self.source_user)
        original_write_memory = self.target.vault.write_memory
        calls = 0

        def fail_write_memory(record: dict) -> Path:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated full disk")
            return original_write_memory(record)

        self.target.vault.write_memory = fail_write_memory  # type: ignore[method-assign]
        try:
            with self.assertRaises(OSError):
                self.target.import_portable_bundle(self.target_user, bundle)
        finally:
            self.target.vault.write_memory = original_write_memory  # type: ignore[method-assign]
        self.assertEqual(self._memory_rows(), [])
        self.assertEqual(list((self.root / "target-vault" / "memories").rglob("*.json")), [])
        self.assertEqual(list((self.root / "target-vault" / "memories").rglob("*.md")), [])
        self.assertEqual(list((self.root / "target-vault" / "captures").rglob("*.json")), [])

    def test_changed_signed_source_memory_conflicts_instead_of_silently_skipping(self) -> None:
        self._seed(1)
        first = self.source.export_portable_bundle(self.source_user)
        self.target.import_portable_bundle(self.target_user, first)
        source_memory_id = first["payload"]["memories"][0]["id"]
        with connect(self.source_db) as conn:
            conn.execute(
                "UPDATE memories SET importance = importance + 1 WHERE user_id = ? AND id = ?",
                (self.source_user, source_memory_id),
            )
        changed = self.source.export_portable_bundle(self.source_user)
        with self.assertRaisesRegex(ValueError, "conflicts with an earlier import"):
            self.target.import_portable_bundle(self.target_user, changed)
        self.assertEqual(len(self._memory_rows()), 1)

    def test_mcp_import_requires_write_scope_and_round_trips_searchable_memory(self) -> None:
        self._seed(1)
        bundle = self.source.export_portable_bundle(self.source_user)
        key_id = bundle["signature"]["key_id"]

        self.assertIn("import_memory_bundle", mcp_tools.WRITE_TOOLS)
        self.assertNotIn("import_memory_bundle", mcp_tools.READ_TOOLS)
        with self.assertRaises(PermissionError):
            mcp_tools.call_tool(
                self.target,
                self.target_user,
                "import_memory_bundle",
                {"bundle": bundle, "expected_signing_key_id": key_id},
                token_scopes=["read"],
            )
        result = mcp_tools.call_tool(
            self.target,
            self.target_user,
            "import_memory_bundle",
            {"bundle": bundle, "expected_signing_key_id": key_id},
            token_scopes=["write"],
        )
        self.assertEqual(result["memories_inserted"], 1)
        with connect(self.target_db) as conn:
            hit = conn.execute(
                "SELECT memory_id FROM memory_fts WHERE memory_fts MATCH ?",
                ("PostgreSQL",),
            ).fetchone()
        self.assertIsNotNone(hit)


if __name__ == "__main__":
    unittest.main()
