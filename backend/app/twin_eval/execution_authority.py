from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..database_maintenance import maintenance_locked_connect
from ..sqlite_runtime import sqlite3


class PairwiseDispatchAuthorityError(ValueError):
    """Base error for the private dispatch-authorization store."""


class PairwiseDispatchDenied(PairwiseDispatchAuthorityError):
    """The current runtime config or user consent does not authorize dispatch."""


class PairwiseDispatchAuthorityConflict(PairwiseDispatchAuthorityError):
    """A caller attempted to mutate a stale runtime or consent epoch."""


@dataclass(frozen=True)
class PairwiseDispatchRuntime:
    config_digest: str
    config_epoch: int
    dispatch_enabled: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PairwiseDispatchAuthorization:
    """Content-free proof that consent matched the locked runtime epoch."""

    user_id: str
    scope: str
    consent_version: str
    config_digest: str
    config_epoch: int
    consent_revision: int
    granted_at: str
    expires_at: str
    authorized_at: str


def _required_text(value: object, name: str, *, maximum: int = 200) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PairwiseDispatchAuthorityError(
            f"{name} must be a non-empty string"
        )
    normalized = value.strip()
    if len(normalized) > maximum:
        raise PairwiseDispatchAuthorityError(
            f"{name} must not exceed {maximum} characters"
        )
    return normalized


def _timestamp(value: object, name: str) -> tuple[str, datetime]:
    raw = _required_text(value, name, maximum=100)
    try:
        parsed = datetime.fromisoformat(
            raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        )
    except ValueError as exc:
        raise PairwiseDispatchAuthorityError(
            f"{name} must be a timezone-aware timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise PairwiseDispatchAuthorityError(
            f"{name} must be a timezone-aware timestamp"
        )
    normalized = (
        parsed.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    return normalized, parsed.astimezone(timezone.utc).replace(microsecond=0)


class PairwiseDispatchAuthorityStore:
    """Durable, transaction-local authority for future provider dispatch.

    The store contains only consent/config metadata. It deliberately does not
    contain profile, prompt, candidate, credential, or provider payload data.
    A future begin-call transaction must call ``require_authorized_tx`` on the
    same connection before inserting its encrypted checkpoint.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _trusted_now(self) -> tuple[str, datetime]:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise PairwiseDispatchAuthorityError(
                "dispatch authority clock must return an aware datetime"
            )
        return _timestamp(value.isoformat(), "authority clock")

    def _connect(self) -> sqlite3.Connection:
        conn = maintenance_locked_connect(
            self.db_path,
            lambda: sqlite3.connect(self.db_path),
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @staticmethod
    def _runtime(row: sqlite3.Row) -> PairwiseDispatchRuntime:
        return PairwiseDispatchRuntime(
            config_digest=str(row["config_digest"]),
            config_epoch=int(row["config_epoch"]),
            dispatch_enabled=bool(row["dispatch_enabled"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def configure_runtime(
        self,
        *,
        config_digest: str,
        dispatch_enabled: bool = False,
        expected_epoch: int | None = None,
    ) -> PairwiseDispatchRuntime:
        """Install or rotate the operational config with optimistic fencing."""

        config_digest = _required_text(
            config_digest, "config_digest", maximum=200
        )
        if not isinstance(dispatch_enabled, bool):
            raise PairwiseDispatchAuthorityError(
                "dispatch_enabled must be a boolean"
            )
        if expected_epoch is not None and (
            isinstance(expected_epoch, bool)
            or not isinstance(expected_epoch, int)
            or expected_epoch < 1
        ):
            raise PairwiseDispatchAuthorityError(
                "expected_epoch must be a positive integer"
            )
        now_text, _ = self._trusted_now()

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM main.twin_eval_dispatch_runtime
                WHERE singleton = 1
                """
            ).fetchone()
            if row is None:
                if expected_epoch is not None:
                    raise PairwiseDispatchAuthorityConflict(
                        "dispatch runtime epoch is unavailable"
                    )
                if dispatch_enabled:
                    raise PairwiseDispatchAuthorityConflict(
                        "dispatch runtime must be initialized disabled"
                    )
                conn.execute(
                    """
                    INSERT INTO main.twin_eval_dispatch_runtime
                    (
                      singleton, config_digest, config_epoch,
                      dispatch_enabled, created_at, updated_at
                    )
                    VALUES (1, ?, 1, ?, ?, ?)
                    """,
                    (
                        config_digest,
                        int(dispatch_enabled),
                        now_text,
                        now_text,
                    ),
                )
            else:
                current = self._runtime(row)
                if (
                    expected_epoch is not None
                    and current.config_epoch != expected_epoch
                ):
                    raise PairwiseDispatchAuthorityConflict(
                        "dispatch runtime epoch changed"
                    )
                if (
                    current.config_digest == config_digest
                    and current.dispatch_enabled == dispatch_enabled
                ):
                    return current
                if expected_epoch is None:
                    raise PairwiseDispatchAuthorityConflict(
                        "expected_epoch is required for runtime changes; "
                        "use disable_runtime for the unconditional kill switch"
                    )
                conn.execute(
                    """
                    UPDATE main.twin_eval_dispatch_runtime
                    SET config_digest = ?,
                        config_epoch = config_epoch + 1,
                        dispatch_enabled = ?,
                        updated_at = ?
                    WHERE singleton = 1 AND config_epoch = ?
                    """,
                    (
                        config_digest,
                        int(dispatch_enabled),
                        now_text,
                        current.config_epoch,
                    ),
                )
            updated = conn.execute(
                """
                SELECT * FROM main.twin_eval_dispatch_runtime
                WHERE singleton = 1
                """
            ).fetchone()
            if updated is None:
                raise PairwiseDispatchAuthorityConflict(
                    "dispatch runtime update was not persisted"
                )
            return self._runtime(updated)

    def disable_runtime(self) -> PairwiseDispatchRuntime:
        """Unconditionally advance and close the operational kill switch."""

        now_text, _ = self._trusted_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM main.twin_eval_dispatch_runtime
                WHERE singleton = 1
                """
            ).fetchone()
            if row is None:
                raise PairwiseDispatchAuthorityConflict(
                    "dispatch runtime is unavailable"
                )
            current = self._runtime(row)
            if not current.dispatch_enabled:
                return current
            conn.execute(
                """
                UPDATE main.twin_eval_dispatch_runtime
                SET config_epoch = config_epoch + 1,
                    dispatch_enabled = 0,
                    updated_at = ?
                WHERE singleton = 1
                """,
                (now_text,),
            )
            updated = conn.execute(
                """
                SELECT * FROM main.twin_eval_dispatch_runtime
                WHERE singleton = 1
                """
            ).fetchone()
            if updated is None:
                raise PairwiseDispatchAuthorityConflict(
                    "dispatch runtime disable was not persisted"
                )
            return self._runtime(updated)

    def grant_consent(
        self,
        *,
        user_id: str,
        scope: str,
        consent_version: str,
        config_digest: str,
        config_epoch: int,
        granted_at: str,
        expires_at: str,
    ) -> PairwiseDispatchAuthorization:
        """Bind authoritative consent to one currently enabled config epoch."""

        user_id = _required_text(user_id, "user_id")
        scope = _required_text(scope, "scope")
        consent_version = _required_text(
            consent_version, "consent_version"
        )
        config_digest = _required_text(
            config_digest, "config_digest", maximum=200
        )
        if (
            isinstance(config_epoch, bool)
            or not isinstance(config_epoch, int)
            or config_epoch < 1
        ):
            raise PairwiseDispatchAuthorityError(
                "config_epoch must be a positive integer"
            )
        granted_text, granted = _timestamp(granted_at, "granted_at")
        expires_text, expires = _timestamp(expires_at, "expires_at")
        now_text, now = self._trusted_now()
        if not granted <= now < expires:
            raise PairwiseDispatchDenied(
                "consent must be active when it is recorded"
            )

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            runtime = conn.execute(
                """
                SELECT * FROM main.twin_eval_dispatch_runtime
                WHERE singleton = 1
                """
            ).fetchone()
            if (
                runtime is None
                or not bool(runtime["dispatch_enabled"])
                or str(runtime["config_digest"]) != config_digest
                or int(runtime["config_epoch"]) != config_epoch
            ):
                raise PairwiseDispatchDenied(
                    "consent does not match the active dispatch config"
                )
            existing = conn.execute(
                """
                SELECT revision, created_at
                FROM main.twin_eval_dispatch_consents
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            revision = (
                1 if existing is None else int(existing["revision"]) + 1
            )
            created_at = (
                now_text
                if existing is None
                else str(existing["created_at"])
            )
            values = (
                scope,
                consent_version,
                config_digest,
                config_epoch,
                revision,
                granted_text,
                expires_text,
                now_text,
            )
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO main.twin_eval_dispatch_consents
                    (
                      user_id, scope, consent_version, config_digest,
                      config_epoch, revision, granted_at, expires_at,
                      revoked_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        user_id,
                        scope,
                        consent_version,
                        config_digest,
                        config_epoch,
                        revision,
                        granted_text,
                        expires_text,
                        created_at,
                        now_text,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE main.twin_eval_dispatch_consents
                    SET scope = ?,
                        consent_version = ?,
                        config_digest = ?,
                        config_epoch = ?,
                        revision = ?,
                        granted_at = ?,
                        expires_at = ?,
                        revoked_at = NULL,
                        updated_at = ?
                    WHERE user_id = ?
                    """,
                    (*values, user_id),
                )
            return PairwiseDispatchAuthorization(
                user_id=user_id,
                scope=scope,
                consent_version=consent_version,
                config_digest=config_digest,
                config_epoch=config_epoch,
            consent_revision=revision,
            granted_at=granted_text,
            expires_at=expires_text,
            authorized_at=now_text,
            )

    def revoke_consent(
        self,
        *,
        user_id: str,
    ) -> bool:
        """Revoke consent under the same write lock used by authorization."""

        user_id = _required_text(user_id, "user_id")
        now_text, now = self._trusted_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT granted_at, revoked_at
                FROM main.twin_eval_dispatch_consents
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            if row is None or row["revoked_at"] is not None:
                return False
            _, granted = _timestamp(row["granted_at"], "granted_at")
            if now < granted:
                raise PairwiseDispatchAuthorityError(
                    "revoked_at cannot precede granted_at"
                )
            conn.execute(
                """
                UPDATE main.twin_eval_dispatch_consents
                SET revoked_at = ?, revision = revision + 1, updated_at = ?
                WHERE user_id = ? AND revoked_at IS NULL
                """,
                (now_text, now_text, user_id),
            )
            return True

    def require_authorized_tx(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        scope: str,
        consent_version: str,
        config_digest: str,
    ) -> PairwiseDispatchAuthorization:
        """Revalidate consent inside the caller's checkpoint transaction.

        The no-op update acquires SQLite's write reservation even if a caller
        accidentally began a deferred transaction. Revocation/config rotation
        therefore serializes before or after this authorization boundary.
        """

        if (
            not callable(getattr(conn, "execute", None))
            or not bool(getattr(conn, "in_transaction", False))
        ):
            raise PairwiseDispatchAuthorityError(
                "authorization requires an active database transaction"
            )
        database_rows = conn.execute("PRAGMA database_list").fetchall()
        main_paths = [
            Path(str(row[2])).resolve()
            for row in database_rows
            if str(row[1]) == "main" and str(row[2])
        ]
        if main_paths != [self.db_path.resolve()]:
            raise PairwiseDispatchAuthorityError(
                "authorization transaction uses the wrong database"
            )
        user_id = _required_text(user_id, "user_id")
        scope = _required_text(scope, "scope")
        consent_version = _required_text(
            consent_version, "consent_version"
        )
        config_digest = _required_text(
            config_digest, "config_digest", maximum=200
        )
        locked = conn.execute(
            """
            UPDATE main.twin_eval_dispatch_runtime
            SET updated_at = updated_at
            WHERE singleton = 1
            """
        )
        if int(locked.rowcount or 0) != 1:
            raise PairwiseDispatchDenied(
                "dispatch runtime is not configured"
            )
        authorized_at, now = self._trusted_now()
        runtime_cursor = conn.execute(
            """
            SELECT
              config_digest, config_epoch, dispatch_enabled
            FROM main.twin_eval_dispatch_runtime
            WHERE singleton = 1
            """
        )
        runtime_row = runtime_cursor.fetchone()
        runtime = (
            None
            if runtime_row is None
            else {
                str(column[0]): runtime_row[index]
                for index, column in enumerate(
                    runtime_cursor.description or ()
                )
            }
        )
        consent_cursor = conn.execute(
            """
            SELECT
              scope, consent_version, config_digest, config_epoch,
              revision, granted_at, expires_at, revoked_at
            FROM main.twin_eval_dispatch_consents
            WHERE user_id = ?
            """,
            (user_id,),
        )
        consent_row = consent_cursor.fetchone()
        consent = (
            None
            if consent_row is None
            else {
                str(column[0]): consent_row[index]
                for index, column in enumerate(
                    consent_cursor.description or ()
                )
            }
        )
        if (
            runtime is None
            or not bool(runtime["dispatch_enabled"])
            or str(runtime["config_digest"]) != config_digest
            or consent is None
            or str(consent["scope"]) != scope
            or str(consent["consent_version"]) != consent_version
            or str(consent["config_digest"]) != config_digest
            or int(consent["config_epoch"])
            != int(runtime["config_epoch"])
            or consent["revoked_at"] is not None
        ):
            raise PairwiseDispatchDenied(
                "current transaction-local dispatch consent is required"
            )
        _, granted = _timestamp(consent["granted_at"], "granted_at")
        _, expires = _timestamp(consent["expires_at"], "expires_at")
        if not granted <= now < expires:
            raise PairwiseDispatchDenied(
                "current transaction-local dispatch consent is required"
            )
        return PairwiseDispatchAuthorization(
            user_id=user_id,
            scope=scope,
            consent_version=consent_version,
            config_digest=config_digest,
            config_epoch=int(runtime["config_epoch"]),
            consent_revision=int(consent["revision"]),
            granted_at=str(consent["granted_at"]),
            expires_at=str(consent["expires_at"]),
            authorized_at=authorized_at,
        )

    def delete_user_consent(self, *, user_id: str) -> bool:
        user_id = _required_text(user_id, "user_id")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                DELETE FROM main.twin_eval_dispatch_consents
                WHERE user_id = ?
                """,
                (user_id,),
            )
            return int(cursor.rowcount or 0) == 1
