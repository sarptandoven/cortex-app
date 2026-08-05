"""Upgrade-path regression tests for the per-user app database.

Almost every other test exercises init_db against a *fresh* database, which is
blind to an entire bug class: schema/migration code that works on a brand-new DB
but raises (or silently keeps the old shape) when run against a database created
by an OLDER Cortex version whose tables predate the newest columns/indexes. That
class already shipped a real startup crash once (the sharding control-index
`lookup_hash` ordering bug).

test_storage_lifecycle already has one hand-written legacy-upgrade test, but it
pins a single fixed historical schema and only covers captures + memories. This
file generalizes that guard: the "old DB" is derived from MIGRATIONS itself (each
ALTER names a column a prior version's CREATE TABLE lacked), so it automatically
covers every migrated table (including import_sessions) and stays correct as new
migrations are added without hand-maintaining a duplicate historical schema. It
also asserts every POST_MIGRATION_INDEXES index is rebuilt and that fresh and
upgraded databases converge on the same columns.
"""

from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.app.database import (
    MIGRATIONS,
    POST_MIGRATION_INDEXES,
    SCHEMA,
    init_db,
)
from backend.app.storage import CortexStore


_ALTER_ADD_COLUMN = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)", re.IGNORECASE
)


def _migration_columns() -> list[tuple[str, str]]:
    """(table, column) for every ADD COLUMN migration, i.e. the columns an old
    database is missing until the lightweight migrations run."""
    pairs: list[tuple[str, str]] = []
    for statement in MIGRATIONS:
        match = _ALTER_ADD_COLUMN.search(statement)
        assert match, f"MIGRATIONS entry not an ADD COLUMN: {statement!r}"
        pairs.append((match.group(1), match.group(2)))
    return pairs


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _index_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }


def _build_pre_migration_db(path: Path) -> list[tuple[str, str]]:
    """Create a database that looks like it was made by an older Cortex version:
    the current base tables with every migration-added column removed. Returns the
    (table, column) pairs actually dropped and asserts the simulation succeeded so
    the test can never silently pass by testing a fully-modern schema.
    """
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        dropped: list[tuple[str, str]] = []
        for table, column in _migration_columns():
            # Inline SCHEMA indexes only cover base columns, so migration columns
            # are always droppable; guard anyway so a future indexed migration
            # column surfaces as an explicit failure rather than a false pass.
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
            dropped.append((table, column))
        conn.commit()

        # The fixture is only meaningful if the columns are genuinely absent.
        for table, column in dropped:
            assert column not in _table_columns(conn, table), (
                f"pre-migration fixture failed to drop {table}.{column}"
            )
        return dropped
    finally:
        conn.close()


class DatabaseUpgradePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "cortex-old.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_init_db_upgrades_pre_migration_database(self) -> None:
        dropped = _build_pre_migration_db(self.db_path)
        self.assertTrue(dropped, "expected at least one migration column to drop")

        # The actual regression guard: upgrading an existing older DB must not raise.
        init_db(self.db_path)

        conn = sqlite3.connect(self.db_path)
        try:
            # Every migration column is restored by the lightweight migrations.
            for table, column in dropped:
                self.assertIn(
                    column,
                    _table_columns(conn, table),
                    f"{table}.{column} was not restored by init_db",
                )

            # POST_MIGRATION_INDEXES (which reference migration columns) are built
            # only after the ALTERs, so they must all exist post-upgrade.
            expected_indexes = set(
                re.findall(r"CREATE INDEX IF NOT EXISTS (\w+)", POST_MIGRATION_INDEXES)
            )
            self.assertTrue(expected_indexes)
            missing = expected_indexes - _index_names(conn)
            self.assertFalse(
                missing, f"post-migration indexes missing after upgrade: {missing}"
            )

            # A migration-dependent column must be writable and readable end to end.
            conn.execute(
                "INSERT INTO captures (id, user_id, source, raw_text, review_status, captured_at) "
                "VALUES ('c1', 'u1', 'test', 'hello', 'pending', '2026-01-01T00:00:00Z')"
            )
            conn.execute(
                "INSERT INTO memories (id, user_id, kind, content, source, layer, sector, "
                "provenance_json, captured_at) "
                "VALUES ('m1', 'u1', 'semantic', 'hi', 'test', 'semantic', 'work', '{}', "
                "'2026-01-01T00:00:00Z')"
            )
            conn.commit()
            row = conn.execute(
                "SELECT review_status FROM captures WHERE id='c1'"
            ).fetchone()
            self.assertEqual(row[0], "pending")
            row = conn.execute(
                "SELECT layer, sector FROM memories WHERE id='m1'"
            ).fetchone()
            self.assertEqual(tuple(row), ("semantic", "work"))
        finally:
            conn.close()

    def test_init_db_is_idempotent_on_upgraded_database(self) -> None:
        # The overwhelmingly common real path: an already-current DB re-opened at
        # every launch. Running init_db repeatedly must never raise.
        _build_pre_migration_db(self.db_path)
        init_db(self.db_path)
        init_db(self.db_path)
        init_db(self.db_path)

        conn = sqlite3.connect(self.db_path)
        try:
            for table, column in _migration_columns():
                self.assertIn(column, _table_columns(conn, table))
        finally:
            conn.close()

    def test_init_db_on_fresh_database_matches_upgraded_schema(self) -> None:
        # Fresh and upgraded databases must converge on the same column set, so an
        # older user is never left missing a column a fresh install has.
        fresh_path = Path(self._tmp.name) / "cortex-fresh.db"
        init_db(fresh_path)

        _build_pre_migration_db(self.db_path)
        init_db(self.db_path)

        fresh = sqlite3.connect(fresh_path)
        upgraded = sqlite3.connect(self.db_path)
        try:
            for table in {t for t, _ in _migration_columns()}:
                self.assertEqual(
                    _table_columns(fresh, table),
                    _table_columns(upgraded, table),
                    f"{table} columns diverge between fresh and upgraded DBs",
                )
        finally:
            fresh.close()
            upgraded.close()


class StoreOwnedColumnUpgradeTests(unittest.TestCase):
    """Same upgrade-path guard for the store-owned lightweight migration: the memories
    `occurrences` column is added by CortexStore (which owns the dedup/occurrence
    semantics), not by database.MIGRATIONS, so the generic test above cannot see it.
    Every production path opens a store right after init_db, so 'init_db + store open'
    is the real upgrade unit."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "cortex-store-owned.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _columns(self) -> set[str]:
        conn = sqlite3.connect(self.db_path)
        try:
            return _table_columns(conn, "memories")
        finally:
            conn.close()

    def test_store_open_adds_memories_occurrences_column(self) -> None:
        # The column's canonical home is database.py (SCHEMA + MIGRATIONS), so init_db alone
        # already provides it; the store-side ensure stays as a harmless no-op safety net for
        # DBs created by older init_db versions.
        init_db(self.db_path)
        self.assertIn("occurrences", self._columns())

        CortexStore(self.db_path, vault_path=Path(self._tmp.name) / "vault")
        self.assertIn("occurrences", self._columns())

        conn = sqlite3.connect(self.db_path)
        try:
            # Legacy rows (inserted without the column) read back the default of 1.
            conn.execute(
                "INSERT INTO memories (id, user_id, kind, content, source, layer, sector, "
                "provenance_json, captured_at) "
                "VALUES ('m1', 'u1', 'semantic', 'hi', 'test', 'semantic', 'work', '{}', "
                "'2026-01-01T00:00:00Z')"
            )
            conn.commit()
            row = conn.execute("SELECT occurrences FROM memories WHERE id='m1'").fetchone()
            self.assertEqual(row[0], 1)
        finally:
            conn.close()

    def test_store_open_upgrade_is_idempotent(self) -> None:
        # The common real path: an already-current DB re-opened at every launch.
        init_db(self.db_path)
        for _ in range(3):
            CortexStore(self.db_path, vault_path=Path(self._tmp.name) / "vault")
        self.assertIn("occurrences", self._columns())

    def test_store_open_upgrades_pre_migration_database(self) -> None:
        # Oldest supported shape: a pre-MIGRATIONS database, upgraded by init_db and
        # then opened by the store — both migration layers must apply cleanly.
        _build_pre_migration_db(self.db_path)
        init_db(self.db_path)
        CortexStore(self.db_path, vault_path=Path(self._tmp.name) / "vault")
        columns = self._columns()
        self.assertIn("occurrences", columns)
        self.assertIn("layer", columns)


if __name__ == "__main__":
    unittest.main()
