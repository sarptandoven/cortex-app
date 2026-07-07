"""Account / identity / session control store for the hosted plane.

Implements section 2 of docs/ACCOUNTS_ENCRYPTION_DESIGN.md: the account,
identity, credential, session, refresh-history, auth-flow, and audit tables
that live in their own control-plane SQLite file
(`<shard_root>/control/accounts.sqlite`, sibling of `token_index.sqlite`).

Design constraints honored here:
- Framework-free: pure stdlib (sqlite3/threading/json/secrets/datetime). No
  fastapi, no cryptography, no argon2 — auth *logic* lives in authn.py.
- `ControlStore` is the storage contract; `SQLiteControlStore` is the only
  implementation today. A future `PostgresControlStore` must pass the same
  contract tests (backend/tests/test_accounts.py) against this interface.
- Schema is written in the portable TEXT/INTEGER/BLOB SQL subset both engines
  accept, created idempotently in `_connect` (same pattern as
  sharding.TokenControlIndex).
- All timestamps are ISO-8601 UTC at second precision, so lexicographic
  comparison equals chronological comparison.
- `user_id` stays the immutable, non-PII shard-routing key; identity gets its
  own `account_id` that points at it. Account merge is never an UPDATE of
  user_id (load-bearing invariant, see design doc section 5).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

ACCOUNT_STATUSES = ("pending_verification", "active", "suspended", "deleted")
FLOW_KINDS = (
    "oidc",
    "app_login",
    "email_verify",
    "password_reset",
    "account_claim",
    "link_challenge",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(moment: datetime) -> str:
    """Canonical ISO-8601 UTC second-precision timestamp used everywhere."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class ControlStore(ABC):
    """Persistence contract for accounts/identities/credentials/sessions/flows.

    Pure storage: no hashing, no token policy, no timing discipline — that is
    authn.AccountsService's job. Every mutator takes explicit ISO timestamps
    so the caller's injectable clock is the single source of time.
    """

    # -- accounts ---------------------------------------------------------
    @abstractmethod
    def create_account(
        self,
        *,
        account_id: str,
        user_id: str,
        primary_email: Optional[str],
        display_name: str,
        status: str,
        email_verified_at: Optional[str],
        now: str,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def get_account(self, account_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def get_account_by_email(self, primary_email: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def get_account_by_user_id(self, user_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def update_account_fields(
        self,
        account_id: str,
        *,
        now: str,
        status: Optional[str] = None,
        primary_email: Optional[str] = None,
        email_verified_at: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> dict[str, Any] | None: ...

    # -- identities -------------------------------------------------------
    @abstractmethod
    def create_identity(
        self,
        *,
        identity_id: str,
        account_id: str,
        provider: str,
        provider_subject: str,
        email: Optional[str],
        email_verified: bool,
        profile: Optional[dict[str, Any]],
        now: str,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def get_identity(self, provider: str, provider_subject: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def list_identities(self, account_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def delete_identity(self, account_id: str, identity_id: str) -> bool: ...

    @abstractmethod
    def touch_identity_login(self, identity_id: str, now: str) -> None: ...

    # -- password credentials ----------------------------------------------
    @abstractmethod
    def set_password_credential(self, account_id: str, password_hash: str, now: str) -> None: ...

    @abstractmethod
    def get_password_credential(self, account_id: str) -> dict[str, Any] | None: ...

    # -- sessions ----------------------------------------------------------
    @abstractmethod
    def create_session(self, record: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def get_session(self, session_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def find_sessions_by_access_lookup(self, access_lookup_hash: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def find_sessions_by_refresh_lookup(self, refresh_lookup_hash: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def list_sessions(self, account_id: str, *, include_revoked: bool = False) -> list[dict[str, Any]]: ...

    @abstractmethod
    def rotate_session(
        self,
        session_id: str,
        *,
        expected_refresh_lookup_hash: str,
        access_lookup_hash: str,
        access_salt: str,
        access_hash: str,
        access_expires_at: str,
        refresh_lookup_hash: str,
        refresh_salt: str,
        refresh_hash: str,
        refresh_idle_expires_at: str,
        now: str,
    ) -> bool: ...

    @abstractmethod
    def revoke_session(self, session_id: str, *, reason: str, now: str) -> bool: ...

    @abstractmethod
    def revoke_account_sessions(
        self, account_id: str, *, reason: str, now: str, keep_session_id: Optional[str] = None
    ) -> int: ...

    @abstractmethod
    def revoke_family(self, family_id: str, *, reason: str, now: str) -> int: ...

    @abstractmethod
    def find_refresh_history(self, lookup_hash: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def touch_session_last_seen(self, session_id: str, now: str) -> None: ...

    # -- auth flows ---------------------------------------------------------
    @abstractmethod
    def create_flow(
        self,
        *,
        flow_id: str,
        kind: str,
        payload: Optional[dict[str, Any]],
        secret_hash: Optional[str],
        expires_at: str,
        now: str,
        provider: Optional[str] = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def get_flow(self, flow_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def consume_flow(self, flow_id: str, now: str) -> dict[str, Any] | None:
        """Atomically mark the flow consumed. Returns the row only for the one
        caller that actually consumed it (single-use winner); None otherwise."""
        ...

    # -- audit ---------------------------------------------------------------
    @abstractmethod
    def record_audit_event(
        self,
        event: str,
        *,
        now: str,
        account_id: Optional[str] = None,
        identity_id: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        detail: Optional[dict[str, Any]] = None,
    ) -> None: ...

    @abstractmethod
    def list_audit_events(
        self, *, account_id: Optional[str] = None, event: Optional[str] = None, limit: int = 100
    ) -> list[dict[str, Any]]: ...


class SQLiteControlStore(ControlStore):
    """SQLite implementation of ControlStore.

    One file, WAL mode, thread-safe: a process-wide lock serializes every
    operation onto short-lived per-call connections (identical discipline to
    sharding.TokenControlIndex). Session verification stays one indexed read.
    """

    SCHEMA = """
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS accounts (
      account_id        TEXT PRIMARY KEY,
      user_id           TEXT NOT NULL UNIQUE,
      primary_email     TEXT,
      email_verified_at TEXT,
      display_name      TEXT NOT NULL DEFAULT '',
      status            TEXT NOT NULL DEFAULT 'pending_verification'
                        CHECK (status IN ('pending_verification','active','suspended','deleted')),
      created_at        TEXT NOT NULL,
      updated_at        TEXT NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_email
      ON accounts(primary_email) WHERE primary_email IS NOT NULL;

    CREATE TABLE IF NOT EXISTS account_identities (
      identity_id       TEXT PRIMARY KEY,
      account_id        TEXT NOT NULL REFERENCES accounts(account_id),
      provider          TEXT NOT NULL,
      provider_subject  TEXT NOT NULL,
      email             TEXT,
      email_verified    INTEGER NOT NULL DEFAULT 0,
      profile_json      TEXT NOT NULL DEFAULT '{}',
      created_at        TEXT NOT NULL,
      last_login_at     TEXT,
      UNIQUE (provider, provider_subject)
    );
    CREATE INDEX IF NOT EXISTS idx_identities_account ON account_identities(account_id);

    CREATE TABLE IF NOT EXISTS account_password_credentials (
      account_id        TEXT PRIMARY KEY REFERENCES accounts(account_id),
      password_hash     TEXT NOT NULL,
      updated_at        TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS auth_sessions (
      session_id              TEXT PRIMARY KEY,
      account_id              TEXT NOT NULL,
      user_id                 TEXT NOT NULL,
      client                  TEXT NOT NULL,
      access_lookup_hash      TEXT NOT NULL,
      access_salt             TEXT NOT NULL,
      access_hash             TEXT NOT NULL,
      access_expires_at       TEXT NOT NULL,
      refresh_family_id       TEXT NOT NULL,
      refresh_lookup_hash     TEXT NOT NULL,
      refresh_salt            TEXT NOT NULL,
      refresh_hash            TEXT NOT NULL,
      refresh_expires_at      TEXT NOT NULL,
      refresh_idle_expires_at TEXT NOT NULL,
      created_ip              TEXT,
      user_agent              TEXT,
      created_at              TEXT NOT NULL,
      last_seen_at            TEXT,
      revoked_at              TEXT,
      revoke_reason           TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_access
      ON auth_sessions(access_lookup_hash) WHERE revoked_at IS NULL;
    CREATE INDEX IF NOT EXISTS idx_sessions_refresh
      ON auth_sessions(refresh_lookup_hash) WHERE revoked_at IS NULL;
    CREATE INDEX IF NOT EXISTS idx_sessions_account
      ON auth_sessions(account_id, revoked_at);
    CREATE INDEX IF NOT EXISTS idx_sessions_family
      ON auth_sessions(refresh_family_id, revoked_at);

    CREATE TABLE IF NOT EXISTS refresh_token_history (
      lookup_hash  TEXT PRIMARY KEY,
      family_id    TEXT NOT NULL,
      session_id   TEXT NOT NULL,
      rotated_at   TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_rth_family ON refresh_token_history(family_id);

    CREATE TABLE IF NOT EXISTS auth_flows (
      flow_id      TEXT PRIMARY KEY,
      kind         TEXT NOT NULL,
      provider     TEXT,
      payload_json TEXT NOT NULL DEFAULT '{}',
      secret_hash  TEXT,
      created_at   TEXT NOT NULL,
      expires_at   TEXT NOT NULL,
      consumed_at  TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_flows_kind ON auth_flows(kind, expires_at);

    CREATE TABLE IF NOT EXISTS audit_auth_events (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      account_id   TEXT,
      event        TEXT NOT NULL,
      identity_id  TEXT,
      ip           TEXT,
      user_agent   TEXT,
      detail_json  TEXT NOT NULL DEFAULT '{}',
      created_at   TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_audit_account ON audit_auth_events(account_id, created_at);
    """

    def __init__(self, path: Path | str, *, clock: Callable[[], datetime] = utc_now) -> None:
        self.path = Path(path).expanduser()
        self._clock = clock
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ util
    def now_iso(self) -> str:
        return iso_utc(self._clock())

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.executescript(self.SCHEMA)
        return conn

    def _one(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                return _row_dict(conn.execute(sql, params).fetchone())
            finally:
                conn.close()

    def _all(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                return [dict(row) for row in conn.execute(sql, params).fetchall()]
            finally:
                conn.close()

    # -------------------------------------------------------------- accounts
    def create_account(
        self,
        *,
        account_id: str,
        user_id: str,
        primary_email: Optional[str],
        display_name: str,
        status: str,
        email_verified_at: Optional[str],
        now: str,
    ) -> dict[str, Any]:
        if status not in ACCOUNT_STATUSES:
            raise ValueError(f"invalid account status: {status!r}")
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO accounts
                          (account_id, user_id, primary_email, email_verified_at,
                           display_name, status, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            account_id,
                            user_id,
                            primary_email,
                            email_verified_at,
                            display_name or "",
                            status,
                            now,
                            now,
                        ),
                    )
            finally:
                conn.close()
        account = self.get_account(account_id)
        assert account is not None
        return account

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM accounts WHERE account_id = ?", (account_id,))

    def get_account_by_email(self, primary_email: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM accounts WHERE primary_email = ?", (primary_email,))

    def get_account_by_user_id(self, user_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM accounts WHERE user_id = ?", (user_id,))

    # -------------------------------------------------------------- admin / metrics

    def count_accounts(self, *, status: Optional[str] = None) -> int:
        if status is not None:
            row = self._one("SELECT COUNT(*) AS n FROM accounts WHERE status = ?", (status,))
        else:
            row = self._one("SELECT COUNT(*) AS n FROM accounts", ())
        return int((row or {}).get("n", 0))

    def admin_metrics(self) -> dict[str, Any]:
        """Aggregate account metrics for the admin dashboard: totals, status + provider
        breakdowns, verified count, active sessions, and a daily signup series."""
        total = self.count_accounts()
        by_status = {
            row["status"]: int(row["n"])
            for row in self._all("SELECT status, COUNT(*) AS n FROM accounts GROUP BY status", ())
        }
        by_provider = {
            row["provider"]: int(row["n"])
            for row in self._all(
                "SELECT provider, COUNT(DISTINCT account_id) AS n "
                "FROM account_identities GROUP BY provider",
                (),
            )
        }
        verified = int(
            (self._one("SELECT COUNT(*) AS n FROM accounts WHERE email_verified_at IS NOT NULL", ()) or {}).get("n", 0)
        )
        # created_at is ISO-8601; substr(1,10) is the calendar day. Newest 30 days, ascending.
        signups_by_day = [
            {"day": row["day"], "count": int(row["n"])}
            for row in self._all(
                "SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS n "
                "FROM accounts GROUP BY day ORDER BY day DESC LIMIT 30",
                (),
            )
        ]
        accounts_with_session = int(
            (self._one("SELECT COUNT(DISTINCT account_id) AS n FROM auth_sessions WHERE revoked_at IS NULL", ()) or {}).get("n", 0)
        )
        return {
            "total_accounts": total,
            "active": by_status.get("active", 0),
            "pending": by_status.get("pending_verification", 0),
            "suspended": by_status.get("suspended", 0),
            "email_verified": verified,
            "accounts_with_active_session": accounts_with_session,
            "by_status": by_status,
            "by_provider": by_provider,
            "signups_by_day": list(reversed(signups_by_day)),
        }

    def list_accounts(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
        query: Optional[str] = None,
    ) -> dict[str, Any]:
        """Paginated account listing for the admin dashboard (newest first). `query` matches
        email or display name; each row carries its linked sign-in providers."""
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if query:
            where.append("(primary_email LIKE ? OR display_name LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like])
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = int(
            (self._one(f"SELECT COUNT(*) AS n FROM accounts{clause}", tuple(params)) or {}).get("n", 0)
        )
        rows = self._all(
            "SELECT account_id, user_id, primary_email, display_name, status, "
            "email_verified_at, created_at, updated_at "
            f"FROM accounts{clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            tuple(params) + (limit, offset),
        )
        for row in rows:
            idents = self._all(
                "SELECT provider FROM account_identities WHERE account_id = ? ORDER BY provider",
                (row["account_id"],),
            )
            row["providers"] = [item["provider"] for item in idents]
        return {"total": total, "limit": limit, "offset": offset, "accounts": rows}

    _ACCOUNT_MUTABLE_FIELDS = ("status", "primary_email", "email_verified_at", "display_name")

    def update_account_fields(
        self,
        account_id: str,
        *,
        now: str,
        status: Optional[str] = None,
        primary_email: Optional[str] = None,
        email_verified_at: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> dict[str, Any] | None:
        if status is not None and status not in ACCOUNT_STATUSES:
            raise ValueError(f"invalid account status: {status!r}")
        updates: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("status", status),
            ("primary_email", primary_email),
            ("email_verified_at", email_verified_at),
            ("display_name", display_name),
        ):
            if value is not None:
                updates.append(f"{column} = ?")
                params.append(value)
        if not updates:
            return self.get_account(account_id)
        updates.append("updated_at = ?")
        params.extend([now, account_id])
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        f"UPDATE accounts SET {', '.join(updates)} WHERE account_id = ?",
                        tuple(params),
                    )
            finally:
                conn.close()
        return self.get_account(account_id)

    # ------------------------------------------------------------ identities
    def create_identity(
        self,
        *,
        identity_id: str,
        account_id: str,
        provider: str,
        provider_subject: str,
        email: Optional[str],
        email_verified: bool,
        profile: Optional[dict[str, Any]],
        now: str,
    ) -> dict[str, Any]:
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO account_identities
                          (identity_id, account_id, provider, provider_subject,
                           email, email_verified, profile_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            identity_id,
                            account_id,
                            provider,
                            provider_subject,
                            email,
                            1 if email_verified else 0,
                            json.dumps(profile or {}),
                            now,
                        ),
                    )
            finally:
                conn.close()
        identity = self._one(
            "SELECT * FROM account_identities WHERE identity_id = ?", (identity_id,)
        )
        assert identity is not None
        return identity

    def get_identity(self, provider: str, provider_subject: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM account_identities WHERE provider = ? AND provider_subject = ?",
            (provider, provider_subject),
        )

    def list_identities(self, account_id: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT * FROM account_identities WHERE account_id = ? ORDER BY created_at, identity_id",
            (account_id,),
        )

    def delete_identity(self, account_id: str, identity_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    cursor = conn.execute(
                        "DELETE FROM account_identities WHERE identity_id = ? AND account_id = ?",
                        (identity_id, account_id),
                    )
                    return cursor.rowcount == 1
            finally:
                conn.close()

    def touch_identity_login(self, identity_id: str, now: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        "UPDATE account_identities SET last_login_at = ? WHERE identity_id = ?",
                        (now, identity_id),
                    )
            finally:
                conn.close()

    # ----------------------------------------------------------- credentials
    def set_password_credential(self, account_id: str, password_hash: str, now: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO account_password_credentials (account_id, password_hash, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(account_id) DO UPDATE SET password_hash = excluded.password_hash,
                                                              updated_at = excluded.updated_at
                        """,
                        (account_id, password_hash, now),
                    )
            finally:
                conn.close()

    def get_password_credential(self, account_id: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM account_password_credentials WHERE account_id = ?", (account_id,)
        )

    # -------------------------------------------------------------- sessions
    _SESSION_COLUMNS = (
        "session_id",
        "account_id",
        "user_id",
        "client",
        "access_lookup_hash",
        "access_salt",
        "access_hash",
        "access_expires_at",
        "refresh_family_id",
        "refresh_lookup_hash",
        "refresh_salt",
        "refresh_hash",
        "refresh_expires_at",
        "refresh_idle_expires_at",
        "created_ip",
        "user_agent",
        "created_at",
        "last_seen_at",
        "revoked_at",
        "revoke_reason",
    )

    def create_session(self, record: dict[str, Any]) -> dict[str, Any]:
        row = {column: record.get(column) for column in self._SESSION_COLUMNS}
        placeholders = ", ".join("?" for _ in self._SESSION_COLUMNS)
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        f"INSERT INTO auth_sessions ({', '.join(self._SESSION_COLUMNS)}) "
                        f"VALUES ({placeholders})",
                        tuple(row[column] for column in self._SESSION_COLUMNS),
                    )
            finally:
                conn.close()
        session = self.get_session(str(record["session_id"]))
        assert session is not None
        return session

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM auth_sessions WHERE session_id = ?", (session_id,))

    def find_sessions_by_access_lookup(self, access_lookup_hash: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT * FROM auth_sessions WHERE access_lookup_hash = ? AND revoked_at IS NULL",
            (access_lookup_hash,),
        )

    def find_sessions_by_refresh_lookup(self, refresh_lookup_hash: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT * FROM auth_sessions WHERE refresh_lookup_hash = ? AND revoked_at IS NULL",
            (refresh_lookup_hash,),
        )

    def list_sessions(self, account_id: str, *, include_revoked: bool = False) -> list[dict[str, Any]]:
        if include_revoked:
            return self._all(
                "SELECT * FROM auth_sessions WHERE account_id = ? ORDER BY created_at, session_id",
                (account_id,),
            )
        return self._all(
            "SELECT * FROM auth_sessions WHERE account_id = ? AND revoked_at IS NULL "
            "ORDER BY created_at, session_id",
            (account_id,),
        )

    def rotate_session(
        self,
        session_id: str,
        *,
        expected_refresh_lookup_hash: str,
        access_lookup_hash: str,
        access_salt: str,
        access_hash: str,
        access_expires_at: str,
        refresh_lookup_hash: str,
        refresh_salt: str,
        refresh_hash: str,
        refresh_idle_expires_at: str,
        now: str,
    ) -> bool:
        """Atomic rotation: archive the old refresh lookup into history and swap
        both token hash pairs on the live row, guarded on the *expected* old
        refresh lookup so a concurrent rotation loses cleanly (rowcount 0)."""
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    row = conn.execute(
                        "SELECT refresh_family_id, refresh_lookup_hash FROM auth_sessions "
                        "WHERE session_id = ? AND revoked_at IS NULL",
                        (session_id,),
                    ).fetchone()
                    if row is None or row["refresh_lookup_hash"] != expected_refresh_lookup_hash:
                        return False
                    conn.execute(
                        "INSERT OR IGNORE INTO refresh_token_history "
                        "(lookup_hash, family_id, session_id, rotated_at) VALUES (?, ?, ?, ?)",
                        (expected_refresh_lookup_hash, row["refresh_family_id"], session_id, now),
                    )
                    cursor = conn.execute(
                        """
                        UPDATE auth_sessions
                        SET access_lookup_hash = ?, access_salt = ?, access_hash = ?,
                            access_expires_at = ?, refresh_lookup_hash = ?, refresh_salt = ?,
                            refresh_hash = ?, refresh_idle_expires_at = ?, last_seen_at = ?
                        WHERE session_id = ? AND refresh_lookup_hash = ? AND revoked_at IS NULL
                        """,
                        (
                            access_lookup_hash,
                            access_salt,
                            access_hash,
                            access_expires_at,
                            refresh_lookup_hash,
                            refresh_salt,
                            refresh_hash,
                            refresh_idle_expires_at,
                            now,
                            session_id,
                            expected_refresh_lookup_hash,
                        ),
                    )
                    return cursor.rowcount == 1
            finally:
                conn.close()

    def revoke_session(self, session_id: str, *, reason: str, now: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    cursor = conn.execute(
                        "UPDATE auth_sessions SET revoked_at = ?, revoke_reason = ? "
                        "WHERE session_id = ? AND revoked_at IS NULL",
                        (now, reason, session_id),
                    )
                    return cursor.rowcount == 1
            finally:
                conn.close()

    def revoke_account_sessions(
        self, account_id: str, *, reason: str, now: str, keep_session_id: Optional[str] = None
    ) -> int:
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    if keep_session_id:
                        cursor = conn.execute(
                            "UPDATE auth_sessions SET revoked_at = ?, revoke_reason = ? "
                            "WHERE account_id = ? AND session_id != ? AND revoked_at IS NULL",
                            (now, reason, account_id, keep_session_id),
                        )
                    else:
                        cursor = conn.execute(
                            "UPDATE auth_sessions SET revoked_at = ?, revoke_reason = ? "
                            "WHERE account_id = ? AND revoked_at IS NULL",
                            (now, reason, account_id),
                        )
                    return cursor.rowcount
            finally:
                conn.close()

    def revoke_family(self, family_id: str, *, reason: str, now: str) -> int:
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    cursor = conn.execute(
                        "UPDATE auth_sessions SET revoked_at = ?, revoke_reason = ? "
                        "WHERE refresh_family_id = ? AND revoked_at IS NULL",
                        (now, reason, family_id),
                    )
                    return cursor.rowcount
            finally:
                conn.close()

    def find_refresh_history(self, lookup_hash: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM refresh_token_history WHERE lookup_hash = ?", (lookup_hash,)
        )

    def touch_session_last_seen(self, session_id: str, now: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        "UPDATE auth_sessions SET last_seen_at = ? WHERE session_id = ?",
                        (now, session_id),
                    )
            finally:
                conn.close()

    # ------------------------------------------------------------ auth flows
    def create_flow(
        self,
        *,
        flow_id: str,
        kind: str,
        payload: Optional[dict[str, Any]],
        secret_hash: Optional[str],
        expires_at: str,
        now: str,
        provider: Optional[str] = None,
    ) -> dict[str, Any]:
        if kind not in FLOW_KINDS:
            raise ValueError(f"invalid flow kind: {kind!r}")
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO auth_flows
                          (flow_id, kind, provider, payload_json, secret_hash, created_at, expires_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (flow_id, kind, provider, json.dumps(payload or {}), secret_hash, now, expires_at),
                    )
            finally:
                conn.close()
        flow = self.get_flow(flow_id)
        assert flow is not None
        return flow

    def get_flow(self, flow_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM auth_flows WHERE flow_id = ?", (flow_id,))

    def consume_flow(self, flow_id: str, now: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    cursor = conn.execute(
                        "UPDATE auth_flows SET consumed_at = ? "
                        "WHERE flow_id = ? AND consumed_at IS NULL",
                        (now, flow_id),
                    )
                    if cursor.rowcount != 1:
                        return None
                    return _row_dict(
                        conn.execute(
                            "SELECT * FROM auth_flows WHERE flow_id = ?", (flow_id,)
                        ).fetchone()
                    )
            finally:
                conn.close()

    # ----------------------------------------------------------------- audit
    def record_audit_event(
        self,
        event: str,
        *,
        now: str,
        account_id: Optional[str] = None,
        identity_id: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        detail: Optional[dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO audit_auth_events
                          (account_id, event, identity_id, ip, user_agent, detail_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (account_id, event, identity_id, ip, user_agent, json.dumps(detail or {}), now),
                    )
            finally:
                conn.close()

    def list_audit_events(
        self, *, account_id: Optional[str] = None, event: Optional[str] = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        filters: list[str] = []
        params: list[Any] = []
        if account_id is not None:
            filters.append("account_id = ?")
            params.append(account_id)
        if event is not None:
            filters.append("event = ?")
            params.append(event)
        where = f"WHERE {' AND '.join(filters)} " if filters else ""
        params.append(max(1, min(int(limit or 100), 1000)))
        return self._all(
            f"SELECT * FROM audit_auth_events {where}ORDER BY id DESC LIMIT ?",
            tuple(params),
        )


__all__ = [
    "ACCOUNT_STATUSES",
    "FLOW_KINDS",
    "ControlStore",
    "SQLiteControlStore",
    "iso_utc",
    "utc_now",
]
