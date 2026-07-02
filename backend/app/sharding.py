from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .database import init_db
from .sqlite_runtime import sqlite3
from .storage import CortexStore


@dataclass(frozen=True)
class ShardAssignment:
    mode: str
    shard_id: str
    db_path: Path
    vault_path: Path

    def as_dict(self) -> dict[str, str]:
        return {
            "mode": self.mode,
            "shard_id": self.shard_id,
            "db_path": str(self.db_path),
            "vault_path": str(self.vault_path),
        }


class ShardRouter:
    """Maps users to local, per-user, or bucketed shard storage paths."""

    VALID_MODES = {"local", "user", "bucket"}

    def __init__(
        self,
        *,
        mode: str,
        db_path: Path,
        vault_path: Path,
        shard_root: Path | None = None,
        shard_count: int = 16,
    ) -> None:
        normalized_mode = mode.strip().lower()
        if normalized_mode not in self.VALID_MODES:
            raise ValueError(f"Unsupported shard mode: {mode}")
        self.mode = normalized_mode
        self.db_path = Path(db_path).expanduser()
        self.vault_path = Path(vault_path).expanduser()
        self.shard_root = Path(shard_root).expanduser() if shard_root else self.db_path.parent / "shards"
        self.shard_count = max(1, int(shard_count))

    @classmethod
    def from_settings(cls, settings: Settings) -> "ShardRouter":
        return cls(
            mode=settings.shard_mode,
            db_path=settings.db_path,
            vault_path=settings.vault_path,
            shard_root=settings.shard_root,
            shard_count=settings.shard_count,
        )

    def assignment_for(self, user_id: str) -> ShardAssignment:
        user_key = (user_id or "local").strip() or "local"
        if self.mode == "local":
            return ShardAssignment("local", "local", self.db_path, self.vault_path)
        if self.mode == "user":
            shard_id = self._user_shard_id(user_key)
            root = self.shard_root / "users" / shard_id
        else:
            bucket = self._bucket_for(user_key)
            shard_id = f"bucket-{bucket:04d}"
            root = self.shard_root / "buckets" / shard_id
        return ShardAssignment(self.mode, shard_id, root / "index.sqlite", root / "Cortex.vault")

    def payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "shard_count": self.shard_count if self.mode == "bucket" else 1,
            "shard_root": str(self.shard_root),
        }

    def _bucket_for(self, user_id: str) -> int:
        digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        return int(digest[:12], 16) % self.shard_count

    def _user_shard_id(self, user_id: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", user_id).strip("-._").lower()[:48] or "user"
        digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]
        return f"{slug}-{digest}"


class TokenControlIndex:
    """Small control-plane token index used to route scoped auth before opening user shards."""

    SCHEMA = """
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS scoped_token_index (
      token_id TEXT PRIMARY KEY,
      user_id TEXT NOT NULL,
      audience TEXT NOT NULL,
      label TEXT NOT NULL DEFAULT '',
      token_salt TEXT NOT NULL,
      token_hash TEXT NOT NULL,
      scopes_json TEXT NOT NULL DEFAULT '[]',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      last_used_at TEXT,
      revoked_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_scoped_token_index_audience ON scoped_token_index(audience, revoked_at);
    CREATE INDEX IF NOT EXISTS idx_scoped_token_index_user ON scoped_token_index(user_id, audience, revoked_at);
    CREATE TABLE IF NOT EXISTS users (
      user_id TEXT PRIMARY KEY,
      display_name TEXT NOT NULL DEFAULT '',
      plan TEXT NOT NULL DEFAULT 'free',
      status TEXT NOT NULL DEFAULT 'active',
      metadata_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_users_status ON users(status, created_at);
    """

    ACTIVE_USER_STATUS = "active"
    USER_STATUSES = ("active", "suspended", "deleted")

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()
        self._lock = threading.Lock()

    def upsert(self, *, token: str, metadata: dict[str, Any]) -> None:
        normalized = token.strip()
        if not normalized:
            return
        token_id = str(metadata["token_id"])
        timestamp = _control_now_iso()
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    existing = conn.execute(
                        "SELECT token_salt, created_at FROM scoped_token_index WHERE token_id = ?",
                        (token_id,),
                    ).fetchone()
                    salt = existing["token_salt"] if existing else secrets.token_hex(16)
                    created_at = existing["created_at"] if existing else str(metadata.get("updated_at") or timestamp)
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO scoped_token_index
                        (token_id, user_id, audience, label, token_salt, token_hash, scopes_json,
                         created_at, updated_at, last_used_at, revoked_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                                (SELECT last_used_at FROM scoped_token_index WHERE token_id = ?), NULL)
                        """,
                        (
                            token_id,
                            str(metadata.get("user_id") or ""),
                            str(metadata.get("audience") or ""),
                            str(metadata.get("label") or "")[:120],
                            salt,
                            self._token_hash(normalized, salt),
                            json.dumps(list(metadata.get("scopes") or [])),
                            created_at,
                            str(metadata.get("updated_at") or timestamp),
                            token_id,
                        ),
                    )
            finally:
                conn.close()

    def authenticate(self, token: str, *, audience: str, user_id: str | None = None) -> dict[str, Any] | None:
        normalized = token.strip()
        if not normalized or not self.path.exists():
            return None
        filters = ["audience = ?", "revoked_at IS NULL"]
        params: list[Any] = [audience]
        if user_id:
            filters.append("user_id = ?")
            params.append(user_id)
        timestamp = _control_now_iso()
        conn = self._connect()
        try:
            with conn:
                rows = conn.execute(
                    f"""
                    SELECT token_id, user_id, audience, label, token_salt, token_hash, scopes_json, created_at, last_used_at
                    FROM scoped_token_index
                    WHERE {" AND ".join(filters)}
                    ORDER BY updated_at DESC, created_at DESC
                    """,
                    tuple(params),
                ).fetchall()
                for row in rows:
                    candidate = self._token_hash(normalized, row["token_salt"])
                    if not secrets.compare_digest(candidate, row["token_hash"]):
                        continue
                    # Reject tokens whose owner is suspended/deleted in the user
                    # registry. Tokens minted before the registry existed have no
                    # users row and stay valid (treated as active) for backward
                    # compatibility.
                    status_row = conn.execute(
                        "SELECT status FROM users WHERE user_id = ?",
                        (row["user_id"],),
                    ).fetchone()
                    if status_row is not None and str(status_row["status"] or "") != self.ACTIVE_USER_STATUS:
                        return None
                    conn.execute(
                        "UPDATE scoped_token_index SET last_used_at = ? WHERE token_id = ?",
                        (timestamp, row["token_id"]),
                    )
                    return {
                        "token_id": row["token_id"],
                        "user_id": row["user_id"],
                        "label": row["label"],
                        "audience": row["audience"],
                        "scopes": self._json_list(row["scopes_json"]),
                        "created_at": row["created_at"],
                        "last_used_at": timestamp,
                        "admin": False,
                        "control_index": True,
                    }
        finally:
            conn.close()
        return None

    def revoke(self, *, user_id: str, token_id: str, revoked_at: str | None = None) -> None:
        if not self.path.exists():
            return
        timestamp = revoked_at or _control_now_iso()
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        "UPDATE scoped_token_index SET revoked_at = ?, updated_at = ? WHERE user_id = ? AND token_id = ?",
                        (timestamp, timestamp, user_id, token_id),
                    )
            finally:
                conn.close()

    def delete_user(self, user_id: str) -> None:
        if not self.path.exists():
            return
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute("DELETE FROM scoped_token_index WHERE user_id = ?", (user_id,))
                    conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            finally:
                conn.close()

    def register_user(
        self,
        user_id: str,
        *,
        display_name: str = "",
        plan: str = "free",
        status: str = "active",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_user = str(user_id or "").strip()
        if not normalized_user:
            raise ValueError("user_id is required")
        normalized_status = str(status or self.ACTIVE_USER_STATUS).strip().lower()
        if normalized_status not in self.USER_STATUSES:
            raise ValueError(f"status must be one of {self.USER_STATUSES}")
        timestamp = _control_now_iso()
        payload = json.dumps(metadata if isinstance(metadata, dict) else {}, sort_keys=True)
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    existing = conn.execute(
                        "SELECT created_at FROM users WHERE user_id = ?", (normalized_user,)
                    ).fetchone()
                    created_at = existing["created_at"] if existing else timestamp
                    conn.execute(
                        """
                        INSERT INTO users (user_id, display_name, plan, status, metadata_json, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET
                          display_name = excluded.display_name,
                          plan = excluded.plan,
                          status = excluded.status,
                          metadata_json = excluded.metadata_json,
                          updated_at = excluded.updated_at
                        """,
                        (
                            normalized_user,
                            str(display_name or "")[:160],
                            str(plan or "free")[:60],
                            normalized_status,
                            payload,
                            created_at,
                            timestamp,
                        ),
                    )
            finally:
                conn.close()
        return self.get_user(normalized_user) or {}

    def set_user_status(self, user_id: str, status: str) -> dict[str, Any] | None:
        normalized_user = str(user_id or "").strip()
        normalized_status = str(status or "").strip().lower()
        if not normalized_user:
            return None
        if normalized_status not in self.USER_STATUSES:
            raise ValueError(f"status must be one of {self.USER_STATUSES}")
        if not self.path.exists():
            return None
        timestamp = _control_now_iso()
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    cursor = conn.execute(
                        "UPDATE users SET status = ?, updated_at = ? WHERE user_id = ?",
                        (normalized_status, timestamp, normalized_user),
                    )
                    updated = cursor.rowcount
            finally:
                conn.close()
        if not updated:
            return None
        return self.get_user(normalized_user)

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        normalized_user = str(user_id or "").strip()
        if not normalized_user or not self.path.exists():
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT user_id, display_name, plan, status, metadata_json, created_at, updated_at FROM users WHERE user_id = ?",
                (normalized_user,),
            ).fetchone()
        finally:
            conn.close()
        return self._user_from_row(row) if row else None

    def list_users(self, *, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        capped_limit = max(1, min(int(limit or 100), 1000))
        filters = []
        params: list[Any] = []
        if status:
            filters.append("status = ?")
            params.append(str(status).strip().lower())
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.append(capped_limit)
        conn = self._connect()
        try:
            rows = conn.execute(
                f"""
                SELECT user_id, display_name, plan, status, metadata_json, created_at, updated_at
                FROM users
                {where}
                ORDER BY created_at DESC, user_id
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        finally:
            conn.close()
        return [self._user_from_row(row) for row in rows]

    def count_users(self, *, status: str | None = None) -> int:
        if not self.path.exists():
            return 0
        conn = self._connect()
        try:
            if status:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM users WHERE status = ?", (str(status).strip().lower(),)
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        finally:
            conn.close()
        return int(row["n"] or 0)

    def registered_user_ids(self, *, limit: int = 500, status: str | None = "active") -> list[str]:
        return [str(user["user_id"]) for user in self.list_users(limit=limit, status=status)]

    def _user_from_row(self, row: Any) -> dict[str, Any]:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return {
            "user_id": row["user_id"],
            "display_name": row["display_name"],
            "plan": row["plan"],
            "status": row["status"],
            "metadata": metadata,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def summary(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "path": str(self.path),
                "exists": False,
                "total_tokens": 0,
                "active_tokens": 0,
                "active_api_tokens": 0,
                "active_mcp_tokens": 0,
                "active_users": 0,
                "active_ready_users": 0,
                "revoked_tokens": 0,
            }
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT
                  COUNT(*) AS total_tokens,
                  SUM(CASE WHEN revoked_at IS NULL THEN 1 ELSE 0 END) AS active_tokens,
                  SUM(CASE WHEN revoked_at IS NULL AND audience = 'api' THEN 1 ELSE 0 END) AS active_api_tokens,
                  SUM(CASE WHEN revoked_at IS NULL AND audience = 'mcp' THEN 1 ELSE 0 END) AS active_mcp_tokens,
                  COUNT(DISTINCT CASE WHEN revoked_at IS NULL THEN user_id ELSE NULL END) AS active_users,
                  SUM(CASE WHEN revoked_at IS NOT NULL THEN 1 ELSE 0 END) AS revoked_tokens
                FROM scoped_token_index
                """
            ).fetchone()
            ready = conn.execute(
                """
                SELECT COUNT(*) AS active_ready_users
                FROM (
                  SELECT sti.user_id
                  FROM scoped_token_index sti
                  LEFT JOIN users u ON u.user_id = sti.user_id
                  WHERE sti.revoked_at IS NULL AND COALESCE(u.status, 'active') = 'active'
                  GROUP BY sti.user_id
                  HAVING
                    SUM(CASE WHEN sti.audience = 'api' THEN 1 ELSE 0 END) > 0
                    AND SUM(CASE WHEN sti.audience = 'mcp' THEN 1 ELSE 0 END) > 0
                )
                """
            ).fetchone()
        finally:
            conn.close()
        return {
            "path": str(self.path),
            "exists": True,
            "total_tokens": int(row["total_tokens"] or 0),
            "active_tokens": int(row["active_tokens"] or 0),
            "active_api_tokens": int(row["active_api_tokens"] or 0),
            "active_mcp_tokens": int(row["active_mcp_tokens"] or 0),
            "active_users": int(row["active_users"] or 0),
            "active_ready_users": int(ready["active_ready_users"] or 0),
            "revoked_tokens": int(row["revoked_tokens"] or 0),
        }

    def ready_user_ids(self, *, limit: int = 50) -> list[str]:
        if not self.path.exists():
            return []
        capped_limit = max(1, min(int(limit or 50), 500))
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT sti.user_id
                FROM scoped_token_index sti
                LEFT JOIN users u ON u.user_id = sti.user_id
                WHERE sti.revoked_at IS NULL AND COALESCE(u.status, 'active') = 'active'
                GROUP BY sti.user_id
                HAVING
                  SUM(CASE WHEN sti.audience = 'api' THEN 1 ELSE 0 END) > 0
                  AND SUM(CASE WHEN sti.audience = 'mcp' THEN 1 ELSE 0 END) > 0
                ORDER BY sti.user_id
                LIMIT ?
                """,
                (capped_limit,),
            ).fetchall()
        finally:
            conn.close()
        return [str(row["user_id"]) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.executescript(self.SCHEMA)
        return conn

    def _token_hash(self, token: str, salt: str) -> str:
        return hashlib.sha256(f"{salt}:{token}".encode("utf-8")).hexdigest()

    def _json_list(self, value: str | None) -> list[str]:
        try:
            parsed = json.loads(value or "[]")
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed]


def _control_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class StoreRegistry:
    """CortexStore facade that lazily opens the shard for each user-scoped call."""

    USER_ID_KWARG = "user_id"

    def __init__(self, router: ShardRouter, *, default_user_id: str = "local", store_cache_size: int = 512) -> None:
        self.router = router
        self.default_user_id = default_user_id
        self.token_index = TokenControlIndex(router.shard_root / "control" / "token_index.sqlite")
        # LRU-bounded per-user store cache: `user` mode opens one shard per user,
        # so an unbounded cache would leak memory/handles at 10k+ users. Local and
        # bucket mode stay well under the cap naturally (1 / shard_count stores).
        self._stores: "OrderedDict[str, CortexStore]" = OrderedDict()
        self._store_cache_size = max(1, int(store_cache_size or 512))
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> "StoreRegistry":
        return cls(
            ShardRouter.from_settings(settings),
            default_user_id=settings.default_user_id,
            store_cache_size=getattr(settings, "store_cache_size", 512),
        )

    @property
    def default_store(self) -> CortexStore:
        return self.store_for_user(self.default_user_id)

    def store_for_user(self, user_id: str) -> CortexStore:
        assignment = self.router.assignment_for(user_id)
        cache_key = str(assignment.db_path)
        with self._lock:
            store = self._stores.get(cache_key)
            if store is not None:
                self._stores.move_to_end(cache_key)
                return store
            assignment.db_path.parent.mkdir(parents=True, exist_ok=True)
            assignment.vault_path.mkdir(parents=True, exist_ok=True)
            init_db(assignment.db_path)
            store = CortexStore(assignment.db_path, assignment.vault_path)
            self._stores[cache_key] = store
            self._evict_stores_if_needed()
            return store

    def _evict_stores_if_needed(self) -> None:
        # Caller holds self._lock. Evict least-recently-used shards past the cap.
        # An evicted shard re-materializes on next access (its DB/vault persist on
        # disk); the control index remains the source of truth for auth routing,
        # so eviction never affects correctness, only the in-memory working set.
        while len(self._stores) > self._store_cache_size:
            _key, evicted = self._stores.popitem(last=False)
            close = getattr(evicted, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def assignment_for(self, user_id: str) -> ShardAssignment:
        return self.router.assignment_for(user_id)

    def health_payload(self, *, mode: str, auth: bool) -> dict[str, Any]:
        payload = self.default_store.health_payload(mode=mode, auth=auth)
        payload["sharding"] = {
            **self.router.payload(),
            "active_store_count": len(self._stores),
            "default": self.assignment_for(self.default_user_id).as_dict(),
        }
        if self.router.mode != "local":
            payload["sharding"]["control_plane"] = self.control_plane_status()
        return payload

    def runtime_storage_status(self) -> dict[str, Any]:
        return {
            **self.default_store.runtime_storage_status(),
            "shard_mode": self.router.mode,
            "active_store_count": len(self._stores),
            "default_shard": self.assignment_for(self.default_user_id).as_dict(),
        }

    def control_plane_status(self) -> dict[str, Any]:
        summary = self.token_index.summary()
        ready = summary["active_ready_users"] > 0
        return {
            **summary,
            "status": "ok" if ready else "blocked",
            "requires": ["at least one active user with scoped API and MCP tokens"],
        }

    def hosted_job_health(self, *, ready_user_limit: int = 20) -> dict[str, Any]:
        capped_ready_user_limit = max(1, min(int(ready_user_limit or 20), 500))
        control_summary = self.token_index.summary()
        total_ready_users = int(control_summary.get("active_ready_users") or 0)
        ready_users = self.token_index.ready_user_ids(limit=capped_ready_user_limit)
        aggregate_counts = {"queued": 0, "running": 0, "succeeded": 0, "failed": 0}
        status_counts = {"ok": 0, "attention": 0, "blocked": 0}
        stale_running_count = 0
        recent_failure_count = 0
        oldest_queued_age_seconds: int | None = None

        for user_id in ready_users:
            health = self.store_for_user(user_id).job_health(user_id, failed_limit=5)
            status = str(health.get("status") or "blocked")
            status_counts[status] = status_counts.get(status, 0) + 1
            for key in aggregate_counts:
                aggregate_counts[key] += int((health.get("counts") or {}).get(key) or 0)
            stale_running_count += len(health.get("stale_running") or [])
            recent_failure_count += len(health.get("recent_failures") or [])
            age = health.get("oldest_queued_age_seconds")
            if age is not None:
                age_int = int(age)
                oldest_queued_age_seconds = age_int if oldest_queued_age_seconds is None else max(oldest_queued_age_seconds, age_int)

        if not ready_users:
            status = "blocked"
        elif status_counts.get("blocked", 0) or aggregate_counts["failed"] or stale_running_count:
            status = "blocked"
        elif status_counts.get("attention", 0) or aggregate_counts["queued"] or aggregate_counts["running"]:
            status = "attention"
        else:
            status = "ok"

        return {
            "status": status,
            "ready_user_count": len(ready_users),
            "total_ready_user_count": total_ready_users,
            "ready_user_limit": capped_ready_user_limit,
            "truncated": total_ready_users > len(ready_users),
            "counts": aggregate_counts,
            "user_status_counts": status_counts,
            "stale_running_count": stale_running_count,
            "recent_failure_count": recent_failure_count,
            "oldest_queued_age_seconds": oldest_queued_age_seconds,
            "requires": [
                "no queued backlog",
                "no running backlog",
                "no failed jobs",
                "no stale running jobs",
                "queue health available for ready hosted users",
            ],
        }

    def authenticate_mcp_token(self, token: str, user_id: str | None = None) -> dict[str, Any] | None:
        return self._authenticate_scoped_token(token, audience="mcp", user_id=user_id)

    def authenticate_api_token(self, token: str, user_id: str | None = None) -> dict[str, Any] | None:
        return self._authenticate_scoped_token(token, audience="api", user_id=user_id)

    def _authenticate_scoped_token(self, token: str, *, audience: str, user_id: str | None = None) -> dict[str, Any] | None:
        scoped = self.token_index.authenticate(token, audience=audience, user_id=user_id)
        if scoped is None:
            # Fall back to per-shard token stores (tokens minted before the
            # control index existed, or a cold control index).
            if user_id:
                method = getattr(self.store_for_user(user_id), f"authenticate_{audience}_token")
                scoped = method(token)
            else:
                method = getattr(self.default_store, f"authenticate_{audience}_token")
                scoped = method(token)
                if scoped is None:
                    for store in list(self._stores.values()):
                        method = getattr(store, f"authenticate_{audience}_token")
                        scoped = method(token)
                        if scoped:
                            break
        if not scoped:
            return None
        # Enforce control-plane user status on every path. The control index
        # already rejects suspended/deleted users, but the per-shard fallbacks
        # authenticate against a shard's own token table and would otherwise
        # bypass a suspension; re-check the registry here so a suspended user's
        # tokens are rejected no matter which store matched.
        owner = str(scoped.get("user_id") or "")
        if owner:
            registry_user = self.token_index.get_user(owner)
            if registry_user is not None and str(registry_user.get("status") or "") != self.token_index.ACTIVE_USER_STATUS:
                return None
        return scoped

    def create_api_token(
        self,
        user_id: str,
        *,
        label: str = "REST API client",
        scopes: list[str] | tuple[str, ...] | str | None = None,
    ) -> dict[str, Any]:
        token = "cxa_" + secrets.token_urlsafe(32).replace("-", "").replace("_", "")[:43]
        metadata = self.ensure_api_token(user_id, token, label=label, scopes=scopes)
        return {**metadata, "token": token}

    def create_mcp_token(
        self,
        user_id: str,
        *,
        label: str = "MCP integration",
        scopes: list[str] | tuple[str, ...] | str | None = None,
    ) -> dict[str, Any]:
        token = "cxm_" + secrets.token_urlsafe(32).replace("-", "").replace("_", "")[:43]
        metadata = self.ensure_mcp_token(user_id, token, label=label, scopes=scopes)
        return {**metadata, "token": token}

    def ensure_api_token(
        self,
        user_id: str,
        token: str,
        *,
        label: str = "REST API client",
        scopes: list[str] | tuple[str, ...] | str | None = None,
        token_id: str | None = None,
    ) -> dict[str, Any]:
        metadata = self.store_for_user(user_id).ensure_api_token(
            user_id,
            token,
            label=label,
            scopes=scopes,
            token_id=token_id,
        )
        self.token_index.upsert(token=token, metadata=metadata)
        return metadata

    def ensure_mcp_token(
        self,
        user_id: str,
        token: str,
        *,
        label: str = "MCP integration",
        scopes: list[str] | tuple[str, ...] | str | None = None,
        token_id: str | None = None,
    ) -> dict[str, Any]:
        metadata = self.store_for_user(user_id).ensure_mcp_token(
            user_id,
            token,
            label=label,
            scopes=scopes,
            token_id=token_id,
        )
        self.token_index.upsert(token=token, metadata=metadata)
        return metadata

    def revoke_token(self, user_id: str, token_id: str) -> dict[str, Any] | None:
        revoked = self.store_for_user(user_id).revoke_token(user_id, token_id)
        if revoked:
            self.token_index.revoke(user_id=user_id, token_id=token_id, revoked_at=revoked.get("revoked_at"))
        return revoked

    def delete_user_data(self, user_id: str, *, include_backups: bool = True) -> dict[str, Any]:
        if self.router.mode == "bucket" and include_backups:
            raise ValueError("delete_user_data(include_backups=True) is not tenant-safe in bucket shard mode")
        deleted = self.store_for_user(user_id).delete_user_data(user_id, include_backups=include_backups)
        self.token_index.delete_user(user_id)
        return deleted

    def provision_user(
        self,
        user_id: str,
        *,
        display_name: str = "",
        plan: str = "free",
        metadata: dict[str, Any] | None = None,
        api_scopes: list[str] | tuple[str, ...] | str | None = None,
        mcp_scopes: list[str] | tuple[str, ...] | str | None = ("read",),
        allow_existing: bool = False,
    ) -> dict[str, Any]:
        """Create a new hosted user end to end: register it in the control-plane
        registry, materialize its isolated shard, and mint an initial API + MCP
        token pair (returned once, in plaintext). Idempotency is opt-in via
        `allow_existing`; by default re-provisioning a known user is rejected so a
        second call cannot silently accumulate token pairs."""
        normalized_user = str(user_id or "").strip()
        if not normalized_user:
            raise ValueError("user_id is required")
        if not allow_existing and self.token_index.get_user(normalized_user) is not None:
            raise ValueError(f"user '{normalized_user}' is already provisioned")
        user = self.token_index.register_user(
            normalized_user,
            display_name=display_name,
            plan=plan,
            status=self.token_index.ACTIVE_USER_STATUS,
            metadata=metadata,
        )
        assignment = self.assignment_for(normalized_user)
        self.store_for_user(normalized_user)  # materialize the shard (db + vault)
        api_token = self.create_api_token(normalized_user, label="Provisioned REST token", scopes=api_scopes)
        mcp_token = self.create_mcp_token(normalized_user, label="Provisioned MCP token", scopes=mcp_scopes)
        return {
            "user": user,
            "shard": assignment.as_dict(),
            "api_token": {
                "token": api_token["token"],
                "token_id": api_token.get("token_id"),
                "scopes": api_token.get("scopes"),
            },
            "mcp_token": {
                "token": mcp_token["token"],
                "token_id": mcp_token.get("token_id"),
                "scopes": mcp_token.get("scopes"),
            },
        }

    def list_users(self, *, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        return self.token_index.list_users(limit=limit, status=status)

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        return self.token_index.get_user(user_id)

    def suspend_user(self, user_id: str) -> dict[str, Any] | None:
        return self.token_index.set_user_status(user_id, "suspended")

    def reactivate_user(self, user_id: str) -> dict[str, Any] | None:
        return self.token_index.set_user_status(user_id, self.token_index.ACTIVE_USER_STATUS)

    def deprovision_user(self, user_id: str, *, include_backups: bool | None = None) -> dict[str, Any]:
        # Full removal: delete the shard data and purge tokens + registry row.
        # In bucket mode backups are shared, so default to not touching them.
        drop_backups = (self.router.mode != "bucket") if include_backups is None else bool(include_backups)
        return self.delete_user_data(user_id, include_backups=drop_backups)

    def remember_oauth_pending(
        self,
        *,
        state: str,
        user_id: str,
        flow: str,
        payload: dict[str, Any],
        ttl_seconds: int = 600,
    ) -> None:
        # Pending OAuth state must live in one place the unauthenticated callback
        # can read by `state` alone, so it is kept in the default store rather than
        # routed per-user (the resolved user_id is carried inside the payload and
        # drives the actual token exchange on completion).
        self.default_store.remember_oauth_pending(
            state=state, user_id=user_id, flow=flow, payload=payload, ttl_seconds=ttl_seconds
        )

    def pop_oauth_pending(self, state: str, *, flow: str) -> dict[str, Any] | None:
        return self.default_store.pop_oauth_pending(state, flow=flow)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.default_store, name)
        if not callable(attr):
            return attr

        def routed(*args: Any, **kwargs: Any) -> Any:
            if self.router.mode == "bucket":
                if name in {"create_backup", "delete_backups", "prune_backups", "restore_latest_backup"}:
                    raise ValueError(f"{name} is not tenant-safe in bucket shard mode")
                if name == "delete_user_data" and kwargs.get("include_backups", True):
                    raise ValueError("delete_user_data(include_backups=True) is not tenant-safe in bucket shard mode")
            routed_store = self._store_from_call(args, kwargs)
            method: Callable[..., Any] = getattr(routed_store, name)
            return method(*args, **kwargs)

        return routed

    def _store_from_call(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> CortexStore:
        user_id = kwargs.get(self.USER_ID_KWARG)
        if isinstance(user_id, str):
            return self.store_for_user(user_id)
        if args and isinstance(args[0], str):
            return self.store_for_user(args[0])
        return self.default_store
