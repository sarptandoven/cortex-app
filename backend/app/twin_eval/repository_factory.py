from __future__ import annotations

from pathlib import Path

from .repository import TwinEvalRepository


def build_cli_repository(
    db_path: Path,
    *,
    keyring_db_path: Path | None = None,
    allow_plaintext_reports: bool = False,
) -> TwinEvalRepository:
    """Build a strict CLI repository without importing crypto unnecessarily."""

    if keyring_db_path is not None and allow_plaintext_reports:
        raise ValueError(
            "keyring_db_path and allow_plaintext_reports are mutually exclusive"
        )
    if keyring_db_path is None:
        return TwinEvalRepository(
            db_path,
            allow_plaintext_reports=allow_plaintext_reports,
        )

    from ..keyring import LocalKekProvider, UserKeyring

    provider = LocalKekProvider()
    if not provider.available:
        raise ValueError(
            "encrypted report access requires CORTEX_KEK or CORTEX_KEK_FILE"
        )
    cipher = UserKeyring(keyring_db_path, provider)
    return TwinEvalRepository(db_path, artifact_cipher=cipher)
