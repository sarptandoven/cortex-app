"""ControlStore contract tests (design doc section 2 schema + semantics).

Written against the abstract ControlStore interface: `ControlStoreContract`
holds every test, and `SQLiteControlStoreTests` binds it to the SQLite
implementation. A future PostgresControlStore must pass by adding one more
subclass that overrides `make_store` — nothing else.
"""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.accounts import ControlStore, SQLiteControlStore, iso_utc

T0 = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def _session_record(
    session_id: str = "sess_1",
    *,
    account_id: str = "acct_1",
    user_id: str = "u_1",
    family_id: str = "fam_1",
    access_lookup: str = "al_1",
    refresh_lookup: str = "rl_1",
    now: str = iso_utc(T0),
) -> dict:
    return {
        "session_id": session_id,
        "account_id": account_id,
        "user_id": user_id,
        "client": "web",
        "access_lookup_hash": access_lookup,
        "access_salt": "as",
        "access_hash": "ah",
        "access_expires_at": iso_utc(T0 + timedelta(hours=1)),
        "refresh_family_id": family_id,
        "refresh_lookup_hash": refresh_lookup,
        "refresh_salt": "rs",
        "refresh_hash": "rh",
        "refresh_expires_at": iso_utc(T0 + timedelta(days=90)),
        "refresh_idle_expires_at": iso_utc(T0 + timedelta(days=30)),
        "created_ip": None,
        "user_agent": None,
        "created_at": now,
        "last_seen_at": now,
    }


class ControlStoreContract:
    """Engine-agnostic behavioral contract. Subclasses provide make_store()."""

    def make_store(self) -> ControlStore:  # pragma: no cover - overridden
        raise NotImplementedError

    def setUp(self) -> None:  # noqa: N802 (unittest)
        self.clock = FakeClock()
        self.store = self.make_store()
        self.now = iso_utc(T0)

    def _mk_account(self, n: int = 1, email: str | None = "user{n}@example.com") -> dict:
        return self.store.create_account(
            account_id=f"acct_{n}",
            user_id=f"u_{n}",
            primary_email=email.format(n=n) if email else None,
            display_name=f"User {n}",
            status="pending_verification",
            email_verified_at=None,
            now=self.now,
        )

    # ------------------------------------------------------------- accounts
    def test_account_create_and_lookups(self) -> None:
        account = self._mk_account(1)
        self.assertEqual(account["account_id"], "acct_1")
        self.assertEqual(account["user_id"], "u_1")
        self.assertEqual(account["status"], "pending_verification")
        self.assertEqual(account["created_at"], self.now)
        self.assertEqual(account["updated_at"], self.now)
        self.assertIsNone(account["email_verified_at"])
        self.assertEqual(self.store.get_account("acct_1")["primary_email"], "user1@example.com")
        self.assertEqual(self.store.get_account_by_email("user1@example.com")["account_id"], "acct_1")
        self.assertEqual(self.store.get_account_by_user_id("u_1")["account_id"], "acct_1")
        self.assertIsNone(self.store.get_account("acct_missing"))
        self.assertIsNone(self.store.get_account_by_email("missing@example.com"))
        self.assertIsNone(self.store.get_account_by_user_id("u_missing"))

    def test_account_email_unique(self) -> None:
        self._mk_account(1)
        with self.assertRaises(Exception):
            self.store.create_account(
                account_id="acct_2",
                user_id="u_2",
                primary_email="user1@example.com",
                display_name="",
                status="pending_verification",
                email_verified_at=None,
                now=self.now,
            )

    def test_account_user_id_unique(self) -> None:
        self._mk_account(1)
        with self.assertRaises(Exception):
            self.store.create_account(
                account_id="acct_2",
                user_id="u_1",
                primary_email="other@example.com",
                display_name="",
                status="active",
                email_verified_at=None,
                now=self.now,
            )

    def test_account_null_email_allowed_multiple(self) -> None:
        # Partial unique index: NULL primary_email must not collide (unclaimed
        # legacy users / unverified OAuth signups).
        self._mk_account(1, email=None)
        self._mk_account(2, email=None)
        self.assertIsNone(self.store.get_account("acct_1")["primary_email"])
        self.assertIsNone(self.store.get_account("acct_2")["primary_email"])

    def test_account_status_constrained(self) -> None:
        with self.assertRaises(Exception):
            self.store.create_account(
                account_id="acct_bad",
                user_id="u_bad",
                primary_email=None,
                display_name="",
                status="bogus",
                email_verified_at=None,
                now=self.now,
            )
        self._mk_account(1)
        with self.assertRaises(Exception):
            self.store.update_account_fields("acct_1", now=self.now, status="bogus")

    def test_update_account_fields(self) -> None:
        self._mk_account(1)
        later = iso_utc(T0 + timedelta(minutes=5))
        updated = self.store.update_account_fields(
            "acct_1", now=later, status="active", email_verified_at=later, display_name="Renamed"
        )
        self.assertEqual(updated["status"], "active")
        self.assertEqual(updated["email_verified_at"], later)
        self.assertEqual(updated["display_name"], "Renamed")
        self.assertEqual(updated["updated_at"], later)
        self.assertEqual(updated["created_at"], self.now)  # unchanged

    # ----------------------------------------------------------- identities
    def test_identity_crud_and_unique_provider_subject(self) -> None:
        self._mk_account(1)
        identity = self.store.create_identity(
            identity_id="idn_1",
            account_id="acct_1",
            provider="google",
            provider_subject="sub-123",
            email="user1@example.com",
            email_verified=True,
            profile={"name": "User"},
            now=self.now,
        )
        self.assertEqual(identity["provider"], "google")
        self.assertEqual(identity["email_verified"], 1)
        found = self.store.get_identity("google", "sub-123")
        self.assertEqual(found["identity_id"], "idn_1")
        self.assertIsNone(self.store.get_identity("google", "sub-999"))
        with self.assertRaises(Exception):  # UNIQUE(provider, provider_subject)
            self.store.create_identity(
                identity_id="idn_2",
                account_id="acct_1",
                provider="google",
                provider_subject="sub-123",
                email=None,
                email_verified=False,
                profile=None,
                now=self.now,
            )
        self.assertEqual(len(self.store.list_identities("acct_1")), 1)
        self.store.touch_identity_login("idn_1", iso_utc(T0 + timedelta(minutes=1)))
        self.assertEqual(
            self.store.get_identity("google", "sub-123")["last_login_at"],
            iso_utc(T0 + timedelta(minutes=1)),
        )
        self.assertTrue(self.store.delete_identity("acct_1", "idn_1"))
        self.assertFalse(self.store.delete_identity("acct_1", "idn_1"))
        self.assertIsNone(self.store.get_identity("google", "sub-123"))

    def test_delete_identity_requires_owning_account(self) -> None:
        self._mk_account(1)
        self._mk_account(2)
        self.store.create_identity(
            identity_id="idn_1",
            account_id="acct_1",
            provider="github",
            provider_subject="42",
            email=None,
            email_verified=False,
            profile=None,
            now=self.now,
        )
        self.assertFalse(self.store.delete_identity("acct_2", "idn_1"))
        self.assertIsNotNone(self.store.get_identity("github", "42"))

    # ---------------------------------------------------------- credentials
    def test_password_credential_upsert(self) -> None:
        self._mk_account(1)
        self.assertIsNone(self.store.get_password_credential("acct_1"))
        self.store.set_password_credential("acct_1", "$argon2id$v1", self.now)
        row = self.store.get_password_credential("acct_1")
        self.assertEqual(row["password_hash"], "$argon2id$v1")
        later = iso_utc(T0 + timedelta(minutes=2))
        self.store.set_password_credential("acct_1", "$argon2id$v2", later)
        row = self.store.get_password_credential("acct_1")
        self.assertEqual(row["password_hash"], "$argon2id$v2")
        self.assertEqual(row["updated_at"], later)

    # -------------------------------------------------------------- sessions
    def test_session_create_and_lookup_paths(self) -> None:
        self._mk_account(1)
        created = self.store.create_session(_session_record())
        self.assertIsNone(created["revoked_at"])
        self.assertEqual(self.store.get_session("sess_1")["account_id"], "acct_1")
        by_access = self.store.find_sessions_by_access_lookup("al_1")
        self.assertEqual([row["session_id"] for row in by_access], ["sess_1"])
        by_refresh = self.store.find_sessions_by_refresh_lookup("rl_1")
        self.assertEqual([row["session_id"] for row in by_refresh], ["sess_1"])
        self.assertEqual(self.store.find_sessions_by_access_lookup("nope"), [])

    def test_revoke_session_and_lookup_exclusion(self) -> None:
        self._mk_account(1)
        self.store.create_session(_session_record())
        self.assertTrue(self.store.revoke_session("sess_1", reason="logout", now=self.now))
        self.assertFalse(self.store.revoke_session("sess_1", reason="logout", now=self.now))
        row = self.store.get_session("sess_1")
        self.assertEqual(row["revoked_at"], self.now)
        self.assertEqual(row["revoke_reason"], "logout")
        self.assertEqual(self.store.find_sessions_by_access_lookup("al_1"), [])
        self.assertEqual(self.store.find_sessions_by_refresh_lookup("rl_1"), [])
        self.assertEqual(self.store.list_sessions("acct_1"), [])
        self.assertEqual(len(self.store.list_sessions("acct_1", include_revoked=True)), 1)

    def test_revoke_account_sessions_with_keep(self) -> None:
        self._mk_account(1)
        for i in (1, 2, 3):
            self.store.create_session(
                _session_record(f"sess_{i}", access_lookup=f"al_{i}", refresh_lookup=f"rl_{i}", family_id=f"fam_{i}")
            )
        revoked = self.store.revoke_account_sessions(
            "acct_1", reason="password_change", now=self.now, keep_session_id="sess_2"
        )
        self.assertEqual(revoked, 2)
        self.assertIsNone(self.store.get_session("sess_2")["revoked_at"])
        self.assertEqual(self.store.get_session("sess_1")["revoke_reason"], "password_change")
        self.assertEqual(self.store.revoke_account_sessions("acct_1", reason="all", now=self.now), 1)
        self.assertEqual(self.store.list_sessions("acct_1"), [])

    def test_rotate_session_atomically_archives_history(self) -> None:
        self._mk_account(1)
        self.store.create_session(_session_record())
        ok = self.store.rotate_session(
            "sess_1",
            expected_refresh_lookup_hash="rl_1",
            access_lookup_hash="al_2",
            access_salt="as2",
            access_hash="ah2",
            access_expires_at=iso_utc(T0 + timedelta(hours=2)),
            refresh_lookup_hash="rl_2",
            refresh_salt="rs2",
            refresh_hash="rh2",
            refresh_idle_expires_at=iso_utc(T0 + timedelta(days=31)),
            now=iso_utc(T0 + timedelta(days=1)),
        )
        self.assertTrue(ok)
        row = self.store.get_session("sess_1")
        self.assertEqual(row["refresh_lookup_hash"], "rl_2")
        self.assertEqual(row["access_lookup_hash"], "al_2")
        history = self.store.find_refresh_history("rl_1")
        self.assertIsNotNone(history)
        self.assertEqual(history["family_id"], "fam_1")
        self.assertEqual(history["session_id"], "sess_1")
        self.assertIsNone(self.store.find_refresh_history("rl_2"))

    def test_rotate_session_guarded_on_expected_lookup(self) -> None:
        self._mk_account(1)
        self.store.create_session(_session_record())
        stale = self.store.rotate_session(
            "sess_1",
            expected_refresh_lookup_hash="rl_stale",
            access_lookup_hash="al_x",
            access_salt="s",
            access_hash="h",
            access_expires_at=self.now,
            refresh_lookup_hash="rl_x",
            refresh_salt="s",
            refresh_hash="h",
            refresh_idle_expires_at=self.now,
            now=self.now,
        )
        self.assertFalse(stale)
        # Nothing changed, nothing archived.
        self.assertEqual(self.store.get_session("sess_1")["refresh_lookup_hash"], "rl_1")
        self.assertIsNone(self.store.find_refresh_history("rl_stale"))

    def test_rotate_revoked_session_refused(self) -> None:
        self._mk_account(1)
        self.store.create_session(_session_record())
        self.store.revoke_session("sess_1", reason="logout", now=self.now)
        ok = self.store.rotate_session(
            "sess_1",
            expected_refresh_lookup_hash="rl_1",
            access_lookup_hash="al_2",
            access_salt="s",
            access_hash="h",
            access_expires_at=self.now,
            refresh_lookup_hash="rl_2",
            refresh_salt="s",
            refresh_hash="h",
            refresh_idle_expires_at=self.now,
            now=self.now,
        )
        self.assertFalse(ok)

    def test_revoke_family(self) -> None:
        self._mk_account(1)
        self.store.create_session(_session_record("sess_1", access_lookup="al_1", refresh_lookup="rl_1", family_id="fam_x"))
        self.store.create_session(_session_record("sess_2", access_lookup="al_2", refresh_lookup="rl_2", family_id="fam_x"))
        self.store.create_session(_session_record("sess_3", access_lookup="al_3", refresh_lookup="rl_3", family_id="fam_y"))
        revoked = self.store.revoke_family("fam_x", reason="refresh_reuse", now=self.now)
        self.assertEqual(revoked, 2)
        self.assertEqual(self.store.get_session("sess_1")["revoke_reason"], "refresh_reuse")
        self.assertEqual(self.store.get_session("sess_2")["revoke_reason"], "refresh_reuse")
        self.assertIsNone(self.store.get_session("sess_3")["revoked_at"])

    def test_touch_session_last_seen(self) -> None:
        self._mk_account(1)
        self.store.create_session(_session_record())
        later = iso_utc(T0 + timedelta(minutes=10))
        self.store.touch_session_last_seen("sess_1", later)
        self.assertEqual(self.store.get_session("sess_1")["last_seen_at"], later)

    # ------------------------------------------------------------ auth flows
    def test_flow_create_get_consume_single_use(self) -> None:
        flow = self.store.create_flow(
            flow_id="flw_1",
            kind="email_verify",
            payload={"account_id": "acct_1"},
            secret_hash="salt$digest",
            expires_at=iso_utc(T0 + timedelta(minutes=30)),
            now=self.now,
        )
        self.assertIsNone(flow["consumed_at"])
        self.assertEqual(self.store.get_flow("flw_1")["kind"], "email_verify")
        first = self.store.consume_flow("flw_1", self.now)
        self.assertIsNotNone(first)
        self.assertEqual(first["consumed_at"], self.now)
        self.assertIn("acct_1", first["payload_json"])
        # Single use: second consumer loses.
        self.assertIsNone(self.store.consume_flow("flw_1", self.now))
        self.assertIsNone(self.store.consume_flow("flw_missing", self.now))

    def test_flow_kind_validated(self) -> None:
        with self.assertRaises(Exception):
            self.store.create_flow(
                flow_id="flw_bad",
                kind="not_a_kind",
                payload=None,
                secret_hash=None,
                expires_at=self.now,
                now=self.now,
            )

    def test_flow_consume_single_winner_under_concurrency(self) -> None:
        self.store.create_flow(
            flow_id="flw_race",
            kind="password_reset",
            payload=None,
            secret_hash="s$d",
            expires_at=iso_utc(T0 + timedelta(minutes=30)),
            now=self.now,
        )
        winners: list[dict] = []
        barrier = threading.Barrier(8)

        def consume() -> None:
            barrier.wait()
            row = self.store.consume_flow("flw_race", self.now)
            if row is not None:
                winners.append(row)

        threads = [threading.Thread(target=consume) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(winners), 1)

    # ----------------------------------------------------------------- audit
    def test_audit_events_recorded_and_filtered(self) -> None:
        self.store.record_audit_event("login_ok", now=self.now, account_id="acct_1")
        self.store.record_audit_event("login_fail", now=self.now)
        self.store.record_audit_event(
            "refresh_reuse", now=self.now, account_id="acct_1", detail={"family_id": "fam_1"}
        )
        all_events = self.store.list_audit_events()
        self.assertEqual(len(all_events), 3)
        for_account = self.store.list_audit_events(account_id="acct_1")
        self.assertEqual({row["event"] for row in for_account}, {"login_ok", "refresh_reuse"})
        reuse = self.store.list_audit_events(event="refresh_reuse")
        self.assertEqual(len(reuse), 1)
        self.assertIn("fam_1", reuse[0]["detail_json"])

    # --------------------------------------------------------- thread safety
    def test_concurrent_account_and_session_writes(self) -> None:
        errors: list[Exception] = []
        barrier = threading.Barrier(8)

        def worker(n: int) -> None:
            barrier.wait()
            try:
                self.store.create_account(
                    account_id=f"acct_c{n}",
                    user_id=f"u_c{n}",
                    primary_email=f"c{n}@example.com",
                    display_name="",
                    status="active",
                    email_verified_at=self.now,
                    now=self.now,
                )
                self.store.create_session(
                    _session_record(
                        f"sess_c{n}",
                        account_id=f"acct_c{n}",
                        user_id=f"u_c{n}",
                        family_id=f"fam_c{n}",
                        access_lookup=f"al_c{n}",
                        refresh_lookup=f"rl_c{n}",
                    )
                )
            except Exception as exc:  # pragma: no cover - failure reporting
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        for n in range(8):
            self.assertIsNotNone(self.store.get_account(f"acct_c{n}"))
            self.assertEqual(len(self.store.find_sessions_by_access_lookup(f"al_c{n}")), 1)


class SQLiteControlStoreTests(ControlStoreContract, unittest.TestCase):
    def make_store(self) -> SQLiteControlStore:
        self._tmp = tempfile.TemporaryDirectory(prefix="cortex-accounts-test-")
        self.addCleanup(self._tmp.cleanup)
        return SQLiteControlStore(Path(self._tmp.name) / "control" / "accounts.sqlite", clock=self.clock)

    def test_wal_mode_enabled(self) -> None:
        self.store.get_account("warmup")  # force file creation
        conn = sqlite3.connect(self.store.path)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(str(mode).lower(), "wal")

    def test_schema_is_idempotent_across_connections(self) -> None:
        self._mk_account(1)
        # A brand-new store object over the same file must see the data and
        # re-running the CREATE IF NOT EXISTS schema must not disturb it.
        reopened = SQLiteControlStore(self.store.path, clock=self.clock)
        self.assertEqual(reopened.get_account("acct_1")["user_id"], "u_1")
        reopened.create_account(
            account_id="acct_2",
            user_id="u_2",
            primary_email=None,
            display_name="",
            status="active",
            email_verified_at=None,
            now=self.now,
        )
        self.assertIsNotNone(self.store.get_account("acct_2"))


if __name__ == "__main__":
    unittest.main()
