from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
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
    """

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
            finally:
                conn.close()

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
                  SELECT user_id
                  FROM scoped_token_index
                  WHERE revoked_at IS NULL
                  GROUP BY user_id
                  HAVING
                    SUM(CASE WHEN audience = 'api' THEN 1 ELSE 0 END) > 0
                    AND SUM(CASE WHEN audience = 'mcp' THEN 1 ELSE 0 END) > 0
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
                SELECT user_id
                FROM scoped_token_index
                WHERE revoked_at IS NULL
                GROUP BY user_id
                HAVING
                  SUM(CASE WHEN audience = 'api' THEN 1 ELSE 0 END) > 0
                  AND SUM(CASE WHEN audience = 'mcp' THEN 1 ELSE 0 END) > 0
                ORDER BY user_id
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

    def __init__(self, router: ShardRouter, *, default_user_id: str = "local") -> None:
        self.router = router
        self.default_user_id = default_user_id
        self.token_index = TokenControlIndex(router.shard_root / "control" / "token_index.sqlite")
        self._stores: dict[str, CortexStore] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> "StoreRegistry":
        return cls(ShardRouter.from_settings(settings), default_user_id=settings.default_user_id)

    @property
    def default_store(self) -> CortexStore:
        return self.store_for_user(self.default_user_id)

    def store_for_user(self, user_id: str) -> CortexStore:
        assignment = self.router.assignment_for(user_id)
        cache_key = str(assignment.db_path)
        with self._lock:
            store = self._stores.get(cache_key)
            if store is None:
                assignment.db_path.parent.mkdir(parents=True, exist_ok=True)
                assignment.vault_path.mkdir(parents=True, exist_ok=True)
                init_db(assignment.db_path)
                store = CortexStore(assignment.db_path, assignment.vault_path)
                self._stores[cache_key] = store
            return store

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
        indexed = self.token_index.authenticate(token, audience=audience, user_id=user_id)
        if indexed:
            return indexed
        if user_id:
            method = getattr(self.store_for_user(user_id), f"authenticate_{audience}_token")
            return method(token)
        method = getattr(self.default_store, f"authenticate_{audience}_token")
        scoped = method(token)
        if scoped:
            return scoped
        for store in list(self._stores.values()):
            method = getattr(store, f"authenticate_{audience}_token")
            scoped = method(token)
            if scoped:
                return scoped
        return None

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
