from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .database import init_db
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


class StoreRegistry:
    """CortexStore facade that lazily opens the shard for each user-scoped call."""

    USER_ID_KWARG = "user_id"

    def __init__(self, router: ShardRouter, *, default_user_id: str = "local") -> None:
        self.router = router
        self.default_user_id = default_user_id
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
        return payload

    def authenticate_mcp_token(self, token: str, user_id: str | None = None) -> dict[str, Any] | None:
        return self._authenticate_scoped_token(token, audience="mcp", user_id=user_id)

    def authenticate_api_token(self, token: str, user_id: str | None = None) -> dict[str, Any] | None:
        return self._authenticate_scoped_token(token, audience="api", user_id=user_id)

    def _authenticate_scoped_token(self, token: str, *, audience: str, user_id: str | None = None) -> dict[str, Any] | None:
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

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.default_store, name)
        if not callable(attr):
            return attr

        def routed(*args: Any, **kwargs: Any) -> Any:
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
