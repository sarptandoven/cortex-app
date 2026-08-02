from __future__ import annotations

import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from backend.app.database import init_db
from backend.app.sqlite_runtime import sqlite3
from backend.app.twin_eval import PAIRWISE_CONSENT_SCOPE
from backend.app.twin_eval.execution_authority import (
    PairwiseDispatchAuthorityConflict,
    PairwiseDispatchAuthorityError,
    PairwiseDispatchAuthorityStore,
    PairwiseDispatchDenied,
)


_CONFIG_A = "pairwise_execution_config_" + ("a" * 64)
_CONFIG_B = "pairwise_execution_config_" + ("b" * 64)
_CONSENT_VERSION = "remote-processing-consent/v1"
_GRANTED = "2026-07-24T19:00:00Z"
_NOW = "2026-07-24T19:01:00Z"
_EXPIRES = "2026-07-24T20:00:00Z"


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class PairwiseDispatchAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "cortex.sqlite"
        init_db(self.db_path)
        self.now = _datetime(_NOW)
        self.store = PairwiseDispatchAuthorityStore(
            self.db_path,
            clock=lambda: self.now,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _enable_and_grant(self) -> int:
        disabled = self.store.configure_runtime(
            config_digest=_CONFIG_A,
            dispatch_enabled=False,
        )
        self.assertEqual(disabled.config_epoch, 1)
        enabled = self.store.configure_runtime(
            config_digest=_CONFIG_A,
            dispatch_enabled=True,
            expected_epoch=disabled.config_epoch,
        )
        self.store.grant_consent(
            user_id="user-a",
            scope=PAIRWISE_CONSENT_SCOPE,
            consent_version=_CONSENT_VERSION,
            config_digest=_CONFIG_A,
            config_epoch=enabled.config_epoch,
            granted_at=_GRANTED,
            expires_at=_EXPIRES,
        )
        return enabled.config_epoch

    def _require(
        self,
        conn: sqlite3.Connection,
        *,
        config_digest: str = _CONFIG_A,
    ):
        return self.store.require_authorized_tx(
            conn,
            user_id="user-a",
            scope=PAIRWISE_CONSENT_SCOPE,
            consent_version=_CONSENT_VERSION,
            config_digest=config_digest,
        )

    def test_fails_closed_without_active_transaction_runtime_or_consent(
        self,
    ) -> None:
        with self._connect() as conn:
            with self.assertRaises(
                PairwiseDispatchAuthorityError, msg="active"
            ):
                self._require(conn)

        with self.assertRaises(PairwiseDispatchAuthorityConflict):
            self.store.configure_runtime(
                config_digest=_CONFIG_A,
                dispatch_enabled=True,
            )
        self.store.configure_runtime(
            config_digest=_CONFIG_A,
            dispatch_enabled=False,
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaises(PairwiseDispatchDenied):
                self._require(conn)

        with self.assertRaises(PairwiseDispatchDenied):
            self.store.grant_consent(
                user_id="user-a",
                scope=PAIRWISE_CONSENT_SCOPE,
                consent_version=_CONSENT_VERSION,
                config_digest=_CONFIG_A,
                config_epoch=1,
                granted_at=_GRANTED,
                expires_at=_EXPIRES,
            )

    def test_authorization_is_exact_config_scope_version_and_time_bound(
        self,
    ) -> None:
        epoch = self._enable_and_grant()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            authorization = self._require(conn)
        self.assertEqual(authorization.config_epoch, epoch)
        self.assertEqual(authorization.consent_revision, 1)

        for changes in (
            {"scope": "other_scope"},
            {"consent_version": "other-consent/v1"},
            {"config_digest": _CONFIG_B},
        ):
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                arguments = {
                    "user_id": "user-a",
                    "scope": PAIRWISE_CONSENT_SCOPE,
                    "consent_version": _CONSENT_VERSION,
                    "config_digest": _CONFIG_A,
                    **changes,
                }
                with self.assertRaises(PairwiseDispatchDenied):
                    self.store.require_authorized_tx(conn, **arguments)
        self.now = _datetime(_EXPIRES)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaises(PairwiseDispatchDenied):
                self._require(conn)

    def test_runtime_change_invalidates_old_consent_and_fences_stale_admin(
        self,
    ) -> None:
        epoch = self._enable_and_grant()
        with self.assertRaises(PairwiseDispatchAuthorityConflict):
            self.store.configure_runtime(
                config_digest=_CONFIG_B,
                dispatch_enabled=True,
            )
        rotated = self.store.configure_runtime(
            config_digest=_CONFIG_B,
            dispatch_enabled=True,
            expected_epoch=epoch,
        )
        self.assertEqual(rotated.config_epoch, epoch + 1)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaises(PairwiseDispatchDenied):
                self._require(conn, config_digest=_CONFIG_B)
        with self.assertRaises(PairwiseDispatchAuthorityConflict):
            self.store.configure_runtime(
                config_digest=_CONFIG_A,
                dispatch_enabled=False,
                expected_epoch=epoch,
            )

    def test_revocation_survives_disable_and_config_rotation(self) -> None:
        epoch = self._enable_and_grant()
        disabled = self.store.disable_runtime()
        self.assertEqual(disabled.config_epoch, epoch + 1)
        self.assertTrue(
            self.store.revoke_consent(user_id="user-a")
        )

    def test_kill_switch_needs_no_current_digest_or_epoch(self) -> None:
        epoch = self._enable_and_grant()
        rotated = self.store.configure_runtime(
            config_digest=_CONFIG_B,
            dispatch_enabled=True,
            expected_epoch=epoch,
        )
        disabled = self.store.disable_runtime()
        self.assertFalse(disabled.dispatch_enabled)
        self.assertEqual(disabled.config_digest, _CONFIG_B)
        self.assertEqual(
            disabled.config_epoch, rotated.config_epoch + 1
        )

        enabled = self.store.configure_runtime(
            config_digest=_CONFIG_A,
            dispatch_enabled=True,
            expected_epoch=disabled.config_epoch,
        )
        self.store.grant_consent(
            user_id="user-a",
            scope=PAIRWISE_CONSENT_SCOPE,
            consent_version=_CONSENT_VERSION,
            config_digest=_CONFIG_A,
            config_epoch=enabled.config_epoch,
            granted_at=_GRANTED,
            expires_at=_EXPIRES,
        )
        self.store.configure_runtime(
            config_digest=_CONFIG_B,
            dispatch_enabled=True,
            expected_epoch=enabled.config_epoch,
        )
        self.assertTrue(
            self.store.revoke_consent(user_id="user-a")
        )

    def test_database_invariants_and_wrong_connection_fail_closed(
        self,
    ) -> None:
        self._enable_and_grant()
        with self._connect() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    UPDATE twin_eval_dispatch_runtime
                    SET dispatch_enabled = 0
                    WHERE singleton = 1
                    """
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    UPDATE twin_eval_dispatch_consents
                    SET revoked_at = ?
                    WHERE user_id = 'user-a'
                    """,
                    (_NOW,),
                )

        other_path = Path(self.tempdir.name) / "other.sqlite"
        init_db(other_path)
        with sqlite3.connect(other_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(
                PairwiseDispatchAuthorityError, "wrong database"
            ):
                self._require(conn)

    def test_runtime_and_consent_replace_cannot_resurrect_authority(
        self,
    ) -> None:
        self._enable_and_grant()
        with self._connect() as conn:
            runtime = conn.execute(
                "SELECT * FROM twin_eval_dispatch_runtime"
            ).fetchone()
            consent = conn.execute(
                """
                SELECT * FROM twin_eval_dispatch_consents
                WHERE user_id = 'user-a'
                """
            ).fetchone()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "DELETE FROM twin_eval_dispatch_runtime"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO twin_eval_dispatch_runtime
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    tuple(runtime),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO twin_eval_dispatch_consents
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(consent),
                )

    def test_temp_table_shadowing_cannot_forge_authorization(self) -> None:
        epoch = self._enable_and_grant()
        self.assertTrue(
            self.store.revoke_consent(user_id="user-a")
        )
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TEMP TABLE twin_eval_dispatch_runtime (
                  singleton INTEGER,
                  config_digest TEXT,
                  config_epoch INTEGER,
                  dispatch_enabled INTEGER,
                  updated_at TEXT
                );
                CREATE TEMP TABLE twin_eval_dispatch_consents (
                  user_id TEXT,
                  scope TEXT,
                  consent_version TEXT,
                  config_digest TEXT,
                  config_epoch INTEGER,
                  revision INTEGER,
                  granted_at TEXT,
                  expires_at TEXT,
                  revoked_at TEXT
                );
                """
            )
            conn.execute(
                """
                INSERT INTO temp.twin_eval_dispatch_runtime
                VALUES (1, ?, ?, 1, ?)
                """,
                (_CONFIG_A, epoch, _NOW),
            )
            conn.execute(
                """
                INSERT INTO temp.twin_eval_dispatch_consents
                VALUES (?, ?, ?, ?, ?, 99, ?, ?, NULL)
                """,
                (
                    "user-a",
                    PAIRWISE_CONSENT_SCOPE,
                    _CONSENT_VERSION,
                    _CONFIG_A,
                    epoch,
                    _GRANTED,
                    _EXPIRES,
                ),
            )
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaises(PairwiseDispatchDenied):
                self._require(conn)

    def test_expiry_is_sampled_after_waiting_for_write_lock(self) -> None:
        self._enable_and_grant()
        writer = self._connect()
        writer.execute("BEGIN IMMEDIATE")
        started = threading.Event()

        def authorize() -> bool:
            conn = self._connect()
            try:
                conn.execute("BEGIN")
                started.set()
                try:
                    self._require(conn)
                except PairwiseDispatchDenied:
                    conn.rollback()
                    return False
                conn.commit()
                return True
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(authorize)
            self.assertTrue(started.wait(timeout=5))
            time.sleep(0.05)
            self.now = _datetime(_EXPIRES)
            writer.commit()
            writer.close()
            self.assertFalse(future.result(timeout=10))

    def test_regrant_increments_revision_and_user_delete_is_scoped(
        self,
    ) -> None:
        epoch = self._enable_and_grant()
        second = self.store.grant_consent(
            user_id="user-a",
            scope=PAIRWISE_CONSENT_SCOPE,
            consent_version=_CONSENT_VERSION,
            config_digest=_CONFIG_A,
            config_epoch=epoch,
            granted_at=_GRANTED,
            expires_at="2026-07-24T21:00:00Z",
        )
        self.assertEqual(second.consent_revision, 2)
        self.store.grant_consent(
            user_id="user-b",
            scope=PAIRWISE_CONSENT_SCOPE,
            consent_version=_CONSENT_VERSION,
            config_digest=_CONFIG_A,
            config_epoch=epoch,
            granted_at=_GRANTED,
            expires_at=_EXPIRES,
        )
        self.assertTrue(
            self.store.delete_user_consent(user_id="user-a")
        )
        with self._connect() as conn:
            users = [
                str(row[0])
                for row in conn.execute(
                    """
                    SELECT user_id FROM twin_eval_dispatch_consents
                    ORDER BY user_id
                    """
                )
            ]
        self.assertEqual(users, ["user-b"])

    def test_revocation_and_authorization_checkpoint_serialize(
        self,
    ) -> None:
        disabled = self.store.configure_runtime(
            config_digest=_CONFIG_A,
            dispatch_enabled=False,
        )
        epoch = self.store.configure_runtime(
            config_digest=_CONFIG_A,
            dispatch_enabled=True,
            expected_epoch=disabled.config_epoch,
        ).config_epoch
        for index in range(12):
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM twin_eval_dispatch_consents"
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS authority_race_checkpoint (
                      run INTEGER PRIMARY KEY
                    )
                    """
                )
                conn.execute(
                    "DELETE FROM authority_race_checkpoint"
                )
            self.store.grant_consent(
                user_id="user-a",
                scope=PAIRWISE_CONSENT_SCOPE,
                consent_version=_CONSENT_VERSION,
                config_digest=_CONFIG_A,
                config_epoch=epoch,
                granted_at=_GRANTED,
                expires_at=_EXPIRES,
            )
            barrier = threading.Barrier(2)

            def authorize() -> bool:
                conn = self._connect()
                try:
                    barrier.wait()
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        self._require(conn)
                    except PairwiseDispatchDenied:
                        conn.rollback()
                        return False
                    conn.execute(
                        """
                        INSERT INTO authority_race_checkpoint(run)
                        VALUES (?)
                        """,
                        (index,),
                    )
                    conn.commit()
                    return True
                finally:
                    conn.close()

            def revoke() -> bool:
                barrier.wait()
                return self.store.revoke_consent(
                    user_id="user-a",
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                authorized = pool.submit(authorize)
                revoked = pool.submit(revoke)
                authorization_won = authorized.result(timeout=10)
                self.assertTrue(revoked.result(timeout=10))
            with self._connect() as conn:
                checkpoint_count = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) FROM authority_race_checkpoint
                        """
                    ).fetchone()[0]
                )
                revoked_at = conn.execute(
                    """
                    SELECT revoked_at
                    FROM twin_eval_dispatch_consents
                    WHERE user_id = 'user-a'
                    """
                ).fetchone()[0]
            self.assertEqual(
                checkpoint_count, 1 if authorization_won else 0
            )
            self.assertIsNotNone(revoked_at)


if __name__ == "__main__":
    unittest.main()
