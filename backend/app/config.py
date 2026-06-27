from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    vault_path: Path
    db_path: Path
    api_key: str
    public_base_url: str
    app_name: str = "Cortex"
    default_user_id: str = "local"
    anthropic_api_key: str = ""


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
    return Settings(
        vault_path=vault_path,
        db_path=db_path,
        api_key=os.environ.get("CORTEX_API_KEY", "dev-local-key"),
        public_base_url=os.environ.get("CORTEX_PUBLIC_BASE_URL", "http://127.0.0.1:8766"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
    )
