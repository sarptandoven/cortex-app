"""Tier-2 entity resolution: conservative auto-merge (exact name / email / UNAMBIGUOUS first name)
that collapses fragments into one canonical node WITHOUT ever fusing two distinct people (a wrong
merge is worse than a duplicate)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import connect, init_db
from backend.app.storage import CortexStore

USER = "ent-user"
TS = "2026-07-08T00:00:00+00:00"


class EntityResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        db = root / "e.sqlite"
        init_db(db)
        self.store = CortexStore(db, root / "vault")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _saves(self, *entities: tuple[str, str]) -> dict:
        """Save entities in order within one connection; return the last resolved entity dict."""
        result: dict = {}
        with connect(self.store.db_path) as conn:
            for eid, name in entities:
                result = self.store._save_entity(
                    conn, USER, {"id": eid, "kind": "person", "name": name, "aliases": []}, TS
                )
        return result

    def test_email_merges_into_named_person(self) -> None:
        r = self._saves(("person_marcus", "Marcus"), ("person_marcus-acme-com", "marcus@acme.com"))
        self.assertEqual(r["id"], "person_marcus")            # merged into the canonical node
        self.assertIn("marcus@acme.com", r["aliases"])         # the email kept as an alias

    def test_unambiguous_first_name_merges(self) -> None:
        r = self._saves(("person_marcus-feld", "Marcus Feld"), ("person_marcus", "Marcus"))
        self.assertEqual(r["id"], "person_marcus-feld")

    def test_ambiguous_first_name_does_not_merge(self) -> None:
        # Two people named Marcus -> "Marcus" is ambiguous -> must NOT auto-merge into either.
        r = self._saves(
            ("person_marcus-feld", "Marcus Feld"),
            ("person_marcus-chen", "Marcus Chen"),
            ("person_marcus", "Marcus"),
        )
        self.assertEqual(r["id"], "person_marcus")             # stays its own node

    def test_distinct_people_never_merge(self) -> None:
        r = self._saves(("person_alice", "Alice"), ("person_bob", "Bob"))
        self.assertEqual(r["id"], "person_bob")

    def test_punct_case_variant_merges_via_resolver(self) -> None:
        r = self._saves(("person_jane-doe", "Jane Doe"), ("person_jane-doe-x", "jane  DOE"))
        self.assertEqual(r["id"], "person_jane-doe")


if __name__ == "__main__":
    unittest.main()
