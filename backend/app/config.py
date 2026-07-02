from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


INSECURE_DEV_API_KEY = "dev-local-key"


@dataclass(frozen=True)
class Settings:
    vault_path: Path
    db_path: Path
    api_key: str
    public_base_url: str
    app_name: str = "Cortex"
    default_user_id: str = "local"
    anthropic_api_key: str = ""
    mcp_api_key: str = ""
    mcp_api_key_scopes: str = ""
    shard_mode: str = "local"
    shard_root: Path | None = None
    shard_count: int = 16
    store_cache_size: int = 512
    rate_limit_per_minute: int = 0
    default_memory_quota: int = 0
    require_scoped_api_tokens: bool = False
    sync_signing_key: str = ""
    hosted_database_url: str = ""
    hosted_vector_backend: str = ""
    worker_mode: str = "inline"
    observability_enabled: bool = False
    embedding_provider: str = "hash"


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    root = Path(__file__).resolve().parents[1]
    db_env = os.environ.get("CORTEX_DB_PATH")
    vault_env = os.environ.get("CORTEX_VAULT_PATH")
    if vault_env:
        vault_path = Path(vault_env).expanduser()
    elif db_env:
        vault_path = Path(db_env).expanduser().parent
    else:
        vault_path = root / "data" / "Cortex.vault"
    db_path = Path(db_env).expanduser() if db_env else vault_path / "index.sqlite"
    require_scoped_api_tokens = os.environ.get("CORTEX_REQUIRE_SCOPED_API_TOKENS", "").strip().lower() in {"1", "true", "yes", "on"}
    api_key = os.environ.get("CORTEX_API_KEY", "").strip()
    if api_key == INSECURE_DEV_API_KEY and not _truthy_env("CORTEX_ALLOW_INSECURE_DEV_TOKEN"):
        raise RuntimeError(
            "CORTEX_API_KEY uses the insecure sample token 'dev-local-key'. "
            "Set a long random token, or set CORTEX_ALLOW_INSECURE_DEV_TOKEN=1 only for local development."
        )
    return Settings(
        vault_path=vault_path,
        db_path=db_path,
        api_key=api_key,
        public_base_url=os.environ.get("CORTEX_PUBLIC_BASE_URL", "http://127.0.0.1:8766"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        mcp_api_key=os.environ.get("CORTEX_MCP_API_KEY", ""),
        mcp_api_key_scopes=os.environ.get("CORTEX_MCP_API_KEY_SCOPES", ""),
        shard_mode=os.environ.get("CORTEX_SHARD_MODE", "local"),
        shard_root=Path(os.environ["CORTEX_SHARD_ROOT"]).expanduser() if os.environ.get("CORTEX_SHARD_ROOT") else None,
        shard_count=max(1, int(os.environ.get("CORTEX_SHARD_COUNT", "16"))),
        store_cache_size=max(1, int(os.environ.get("CORTEX_STORE_CACHE_SIZE", "512") or "512")),
        rate_limit_per_minute=max(0, int(os.environ.get("CORTEX_RATE_LIMIT_PER_MINUTE", "0") or "0")),
        default_memory_quota=max(0, int(os.environ.get("CORTEX_DEFAULT_MEMORY_QUOTA", "0") or "0")),
        require_scoped_api_tokens=require_scoped_api_tokens,
        sync_signing_key=os.environ.get("CORTEX_SYNC_SIGNING_KEY", ""),
        hosted_database_url=os.environ.get("CORTEX_HOSTED_DATABASE_URL", os.environ.get("DATABASE_URL", "")).strip(),
        hosted_vector_backend=os.environ.get("CORTEX_HOSTED_VECTOR_BACKEND", "").strip().lower(),
        worker_mode=os.environ.get("CORTEX_WORKER_MODE", "inline").strip().lower() or "inline",
        observability_enabled=_truthy_env("CORTEX_OBSERVABILITY_ENABLED"),
        embedding_provider=os.environ.get("CORTEX_EMBEDDING_PROVIDER", "hash").strip().lower() or "hash",
    )
