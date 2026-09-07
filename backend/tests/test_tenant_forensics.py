from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.storage import CortexStore
from backend.app.tenant_forensics import cross_tenant_integrity_report, format_report, main


def _extraction(memory_id: str, content: str) -> dict:
    return {
        "_timestamp": "2026-06-29T12:00:00Z",
        "summary": content,
        "records": [
            {"id": memory_id, "kind": "observation", "layer": "semantic", "content": content,
             "confidence": "confirmed", "importance": 3, "topics": ["alpha"], "entity_ids": []}
        ],
        "tasks": [],
        "entities": [],
    }


class TenantForensicsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "index.sqlite"
        init_db(self.db)
        self.store = CortexStore(self.db, self.root / "vault")
        self.store._vector_ready = lambda conn: False

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _save(self, user_id: str, memory_id: str, content: str) -> None:
        self.store.save_capture(
            user_id=user_id,
            content=f"{content} capture",
            source="unit-test",
            source_url=None,
            title=user_id,
            extracted=_extraction(memory_id, content),
            auto_approve=True,
        )

    def _simulate_pre_guard_clobber(self) -> None:
        """Reproduce exactly what the old code left behind: tenant-b's INSERT OR REPLACE takes over
        tenant-a's `memories` row, while tenant-a's append-only audit rows survive pointing at it."""
        conn = sqlite3.connect(self.db)
        try:
            row = conn.execute(
                "SELECT * FROM memories WHERE user_id = 'tenant-a' LIMIT 1"
            ).fetchone()
            assert row is not None
            columns = [d[0] for d in conn.execute("SELECT * FROM memories LIMIT 0").description]
            values = dict(zip(columns, row))
            values["user_id"] = "tenant-b"
            values["content"] = "Tenant B content that overwrote A"
            placeholders = ", ".join("?" for _ in columns)
            conn.execute(
                f"INSERT OR REPLACE INTO memories ({', '.join(columns)}) VALUES ({placeholders})",
                [values[c] for c in columns],
            )
            conn.commit()
        finally:
            conn.close()

    def test_clean_multi_tenant_database_reports_no_findings(self) -> None:
        self._save("tenant-a", "mem_a", "Tenant A content")
        self._save("tenant-b", "mem_b", "Tenant B content")

        report = cross_tenant_integrity_report(self.db)

        self.assertFalse(report["clobber_evidence"])
        self.assertFalse(report["suspicious"])
        self.assertEqual(report["findings"], [])
        self.assertTrue(report["multi_tenant"])

    def test_guard_handled_collision_is_not_flagged(self) -> None:
        """Two tenants submitting the same id is now handled by the write-side guard; that is
        normal operation and must not be reported as a clobber."""
        self._save("tenant-a", "shared_id", "Tenant A content")
        self._save("tenant-b", "shared_id", "Tenant B content")

        report = cross_tenant_integrity_report(self.db)

        self.assertFalse(report["clobber_evidence"], f"guard-handled collision must be clean: {report['findings']}")
        self.assertFalse(report["suspicious"])

    def test_detects_a_pre_guard_clobber(self) -> None:
        self._save("tenant-a", "shared_id", "Tenant A content that gets destroyed")
        self._simulate_pre_guard_clobber()

        report = cross_tenant_integrity_report(self.db)

        self.assertTrue(report["clobber_evidence"], "the clobber must be detected")
        checks = {finding["check"] for finding in report["findings"]}
        self.assertIn("memory_events", checks, "the append-only audit log is the primary signal")
        finding = next(f for f in report["findings"] if f["check"] == "memory_events")
        self.assertEqual(finding["confidence"], "high")
        self.assertEqual(sorted(finding["tenants_involved"]), ["tenant-a", "tenant-b"])

    def test_total_erasure_of_a_tenant_is_still_detected(self) -> None:
        """Regression: the worst case leaves the FEWEST survivors. When the victim's only memory is
        taken over, `memories` holds a single tenant — gating detection on the surviving tenant
        count would report a total wipe as a harmless single-tenant database."""
        self._save("tenant-a", "shared_id", "Tenant A sole memory")
        self._simulate_pre_guard_clobber()

        conn = sqlite3.connect(self.db)
        try:
            survivors = conn.execute("SELECT COUNT(DISTINCT user_id) FROM memories").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(survivors, 1, "precondition: the victim is gone from memories")

        report = cross_tenant_integrity_report(self.db)
        self.assertTrue(report["clobber_evidence"], "a total wipe must still be flagged")
        self.assertGreater(report["distinct_tenants_seen"], 1, "the victim is still visible in the audit log")

    def test_report_is_read_only(self) -> None:
        self._save("tenant-a", "mem_a", "Tenant A content")
        before = self.db.read_bytes()

        cross_tenant_integrity_report(self.db)

        self.assertEqual(self.db.read_bytes(), before, "forensics must never write to the database")

    def test_cli_exit_code_signals_evidence(self) -> None:
        self._save("tenant-a", "shared_id", "Tenant A content")
        self.assertEqual(main([str(self.db)]), 0, "clean database exits 0")

        self._simulate_pre_guard_clobber()
        self.assertEqual(main([str(self.db)]), 1, "evidence exits 1 so it can gate a deploy check")

    def test_missing_database_is_reported_not_crashed(self) -> None:
        self.assertEqual(main([str(self.root / "nope.sqlite")]), 2)
        with self.assertRaises(FileNotFoundError):
            cross_tenant_integrity_report(self.root / "nope.sqlite")

    def test_format_report_renders_findings(self) -> None:
        self._save("tenant-a", "shared_id", "Tenant A content")
        self._simulate_pre_guard_clobber()

        text = format_report(cross_tenant_integrity_report(self.db))

        self.assertIn("CROSS-TENANT CLOBBER EVIDENCE FOUND", text)
        self.assertIn("memory_events", text)
        self.assertIn("tenant-a", text)


if __name__ == "__main__":
    unittest.main()
