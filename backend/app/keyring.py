"""Per-user envelope encryption for the hosted plane (docs/ACCOUNTS_ENCRYPTION_DESIGN.md §4).

Key hierarchy
    Level 0  root KEK behind a ``KeyProvider`` (``LocalKekProvider`` now; a KMS
             provider slots in behind the same wrap/unwrap interface later).
    Level 1  per-user 32-byte master DEK, wrapped by the KEK with
             AAD = user_id || kek_id || dek_version and stored in the keyring's
             own SQLite file (``user_keys`` + ``user_key_wraps``) — never in the
             data-plane shard DBs.
    Level 2  purpose subkeys, derived and never stored:
             HKDF-SHA256(master_dek, info=b"cortex:v1:" + purpose).

Ciphertext format (CXE1, self-describing and storage-agnostic — survives every
storage migration byte-identical):

    b"CXE1" || kek_id_len(1B) || kek_id || dek_version(4B big-endian)
            || nonce(12B) || AES-256-GCM ciphertext+tag

with AAD = user_id || purpose || dek_version, so a blob copied into another
user's row or another purpose domain fails authentication. The embedded kek_id
is advisory (KEK rotation re-wraps DEK rows without rewriting data blobs); the
authoritative kek_id for unwrap lives on the ``user_key_wraps`` row selected by
the blob's dek_version.

Deletion = crypto-shredding: ``crypto_shred(user_id)`` zeroizes and deletes the
user's wrap rows, tombstones ``user_keys.destroyed_at``, and purges the DEK
cache — every historical blob for that user becomes permanently unreadable.

This module is hosted-plane only (requires the ``cryptography`` package). The
stdlib-only local path (vault.py / standalone_server.py) must never import it
at module level.
"""

from __future__ import annotations

import base64
import os
import secrets
import struct
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .sqlite_runtime import sqlite3

CXE1_MAGIC = b"CXE1"
PURPOSES = ("credentials", "content", "vault", "backup")
HKDF_INFO_PREFIX = b"cortex:v1:"
DEFAULT_NONCE_BUDGET = 2**28  # per (user, dek_version); far below the GCM 2^32 bound
DEFAULT_CACHE_SIZE = 512
DEFAULT_CACHE_TTL_SECONDS = 300.0
_WRAP_NONCE_LEN = 12
_DATA_NONCE_LEN = 12
_GCM_TAG_LEN = 16
_DEK_LEN = 32


class KeyringError(Exception):
    """Base class for all keyring failures."""


class KekConfigError(KeyringError):
    """The configured KEK (env/file) is malformed or mismatched."""


class EncryptionUnavailableError(KeyringError):
    """No KEK is configured, so encryption/decryption is cleanly disabled."""


class ShreddedKeyError(KeyringError):
    """The user's key material was crypto-shredded; their ciphertext is gone forever."""


class DecryptionError(KeyringError):
    """A blob failed authentication (wrong user, wrong purpose, or corruption)."""


def is_encrypted(blob: bytes | bytearray | memoryview) -> bool:
    """True if ``blob`` carries the CXE1 envelope; False = legacy plaintext."""
    return bytes(blob[:4]) == CXE1_MAGIC


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _wrap_aad(user_id: str, kek_id: str, dek_version: int) -> bytes:
    return f"{user_id}|{kek_id}|{dek_version}".encode("utf-8")


def _data_aad(user_id: str, purpose: str, dek_version: int) -> bytes:
    return f"{user_id}|{purpose}|{dek_version}".encode("utf-8")


class KeyProvider:
    """Root-KEK holder interface. Exactly two planned impls: LocalKekProvider
    (env/secret-file, self-hosting forever supported) and a KMS provider later —
    both expose wrap/unwrap so a KMS EncryptionContext binding slots in without
    touching the keyring."""

    @property
    def available(self) -> bool:
        raise NotImplementedError

    @property
    def kek_id(self) -> str:
        raise NotImplementedError

    def wrap_dek(self, user_id: str, dek_version: int, dek: bytes) -> tuple[str, bytes]:
        """Wrap ``dek`` bound to (user_id, dek_version); returns (kek_id, wrapped)."""
        raise NotImplementedError

    def unwrap_dek(self, user_id: str, dek_version: int, kek_id: str, wrapped: bytes) -> bytes:
        raise NotImplementedError


class LocalKekProvider(KeyProvider):
    """32-byte KEK from ``CORTEX_KEK`` (base64) or a secret file at
    ``CORTEX_KEK_FILE`` (base64 text, or exactly 32 raw bytes); versioned
    ``local:vN`` via ``CORTEX_KEK_VERSION`` (default 1). A malformed configured
    KEK raises ``KekConfigError`` eagerly; no KEK configured at all means
    ``available`` is False and encryption is cleanly disabled."""

    ENV_KEK = "CORTEX_KEK"
    ENV_KEK_FILE = "CORTEX_KEK_FILE"
    ENV_KEK_VERSION = "CORTEX_KEK_VERSION"

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        source = os.environ if env is None else env
        version_raw = str(source.get(self.ENV_KEK_VERSION) or "1").strip() or "1"
        try:
            version = int(version_raw)
        except ValueError as exc:
            raise KekConfigError(
                f"{self.ENV_KEK_VERSION} must be a positive integer, got {version_raw!r}"
            ) from exc
        if version < 1:
            raise KekConfigError(f"{self.ENV_KEK_VERSION} must be >= 1, got {version}")
        self._kek_id = f"local:v{version}"
        self._kek = self._load_kek(source)

    @property
    def available(self) -> bool:
        return self._kek is not None

    @property
    def kek_id(self) -> str:
        return self._kek_id

    def wrap_dek(self, user_id: str, dek_version: int, dek: bytes) -> tuple[str, bytes]:
        kek = self._require_kek()
        nonce = secrets.token_bytes(_WRAP_NONCE_LEN)
        wrapped = nonce + AESGCM(kek).encrypt(nonce, dek, _wrap_aad(user_id, self._kek_id, dek_version))
        return self._kek_id, wrapped

    def unwrap_dek(self, user_id: str, dek_version: int, kek_id: str, wrapped: bytes) -> bytes:
        kek = self._require_kek()
        if kek_id != self._kek_id:
            raise KekConfigError(
                f"wrapped DEK requires KEK {kek_id!r} but this provider holds {self._kek_id!r};"
                " configure the matching CORTEX_KEK/CORTEX_KEK_VERSION or run the re-wrap job"
            )
        if len(wrapped) < _WRAP_NONCE_LEN + _GCM_TAG_LEN:
            raise DecryptionError("wrapped DEK is truncated")
        nonce, ciphertext = wrapped[:_WRAP_NONCE_LEN], wrapped[_WRAP_NONCE_LEN:]
        try:
            return AESGCM(kek).decrypt(nonce, ciphertext, _wrap_aad(user_id, kek_id, dek_version))
        except InvalidTag as exc:
            raise DecryptionError(
                f"wrapped DEK for user {user_id!r} (dek_version {dek_version}) failed authentication"
            ) from exc

    def _require_kek(self) -> bytes:
        if self._kek is None:
            raise EncryptionUnavailableError(
                f"no KEK configured: set {self.ENV_KEK} (base64 of 32 bytes) or {self.ENV_KEK_FILE}"
            )
        return self._kek

    def _load_kek(self, source: Mapping[str, str]) -> bytes | None:
        encoded = str(source.get(self.ENV_KEK) or "").strip()
        if encoded:
            return self._decode_kek(encoded, origin=f"env {self.ENV_KEK}")
        file_path = str(source.get(self.ENV_KEK_FILE) or "").strip()
        if file_path:
            try:
                data = Path(file_path).expanduser().read_bytes()
            except OSError as exc:
                raise KekConfigError(f"cannot read KEK file {file_path!r}: {exc}") from exc
            if len(data) == _DEK_LEN:
                return bytes(data)  # raw 32-byte secret file
            try:
                text = data.decode("utf-8").strip()
            except UnicodeDecodeError as exc:
                raise KekConfigError(
                    f"KEK file {file_path!r} must hold base64 text or exactly 32 raw bytes"
                ) from exc
            return self._decode_kek(text, origin=f"KEK file {file_path!r}")
        return None

    @staticmethod
    def _decode_kek(encoded: str, *, origin: str) -> bytes:
        try:
            kek = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:  # binascii.Error subclasses ValueError
            raise KekConfigError(f"{origin}: KEK is not valid standard base64") from exc
        if len(kek) != _DEK_LEN:
            raise KekConfigError(f"{origin}: KEK must decode to exactly 32 bytes, got {len(kek)}")
        return kek


class UserKeyring:
    """Owns per-user master DEKs (mint/wrap/unwrap/shred) and the CXE1
    encrypt/decrypt surface. Thread-safe; owns its own SQLite file."""

    SCHEMA = """
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS user_keys (
      user_id      TEXT PRIMARY KEY,
      dek_version  INTEGER NOT NULL DEFAULT 1,
      write_count  INTEGER NOT NULL DEFAULT 0,
      created_at   TEXT NOT NULL,
      rotated_at   TEXT,
      destroyed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS user_key_wraps (
      user_id         TEXT NOT NULL,
      dek_version     INTEGER NOT NULL,
      wrap_type       TEXT NOT NULL DEFAULT 'service',
      kek_id          TEXT NOT NULL,
      wrapped_dek     BLOB NOT NULL,
      kdf_params_json TEXT,
      created_at      TEXT NOT NULL,
      PRIMARY KEY (user_id, dek_version, wrap_type)
    );
    """

    SERVICE_WRAP = "service"

    def __init__(
        self,
        db_path: str | Path,
        provider: KeyProvider,
        *,
        nonce_budget: int = DEFAULT_NONCE_BUDGET,
        cache_size: int = DEFAULT_CACHE_SIZE,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.db_path = Path(db_path).expanduser()
        self.provider = provider
        self.nonce_budget = max(1, int(nonce_budget))
        self._cache_size = max(1, int(cache_size))
        self._cache_ttl = float(cache_ttl_seconds)
        self._clock = clock or time.monotonic
        # (user_id, dek_version) -> (dek, expires_at). Bounded LRU with TTL so a
        # long-lived process never holds unwrapped DEKs indefinitely; purged on shred.
        self._cache: "OrderedDict[tuple[str, int], tuple[bytes, float]]" = OrderedDict()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ public

    @property
    def available(self) -> bool:
        return self.provider.available

    @staticmethod
    def is_encrypted(blob: bytes | bytearray | memoryview) -> bool:
        return is_encrypted(blob)

    def encrypt_blob(self, user_id: str, purpose: str, plaintext: bytes) -> bytes:
        user_key = self._normalize_user(user_id)
        self._validate_purpose(purpose)
        if not self.provider.available:
            raise EncryptionUnavailableError(
                "encryption requested but no KEK is configured (CORTEX_KEK / CORTEX_KEK_FILE)"
            )
        kek_id_bytes = self.provider.kek_id.encode("utf-8")
        if not 1 <= len(kek_id_bytes) <= 255:
            raise KekConfigError(f"kek_id {self.provider.kek_id!r} must encode to 1..255 bytes")
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    dek_version, dek, write_count = self._current_key_locked(conn, user_key)
                    conn.execute(
                        "UPDATE user_keys SET write_count = ? WHERE user_id = ?",
                        (write_count + 1, user_key),
                    )
            finally:
                conn.close()
            key = self._purpose_key(dek, purpose)
        nonce = secrets.token_bytes(_DATA_NONCE_LEN)
        ciphertext = AESGCM(key).encrypt(nonce, bytes(plaintext), _data_aad(user_key, purpose, dek_version))
        return (
            CXE1_MAGIC
            + bytes((len(kek_id_bytes),))
            + kek_id_bytes
            + struct.pack(">I", dek_version)
            + nonce
            + ciphertext
        )

    def decrypt_blob(self, user_id: str, purpose: str, blob: bytes) -> bytes:
        raw = bytes(blob)
        if not is_encrypted(raw):
            # Legacy plaintext sniff (design: lazy read-migrate). Callers use
            # is_encrypted() to decide whether to write-back an encrypted copy.
            return raw
        user_key = self._normalize_user(user_id)
        self._validate_purpose(purpose)
        _kek_id_hint, dek_version, nonce, ciphertext = self._parse_envelope(raw)
        if not self.provider.available:
            raise EncryptionUnavailableError(
                "cannot decrypt: blob is CXE1-encrypted but no KEK is configured"
            )
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT dek_version, destroyed_at FROM user_keys WHERE user_id = ?",
                    (user_key,),
                ).fetchone()
                if row is None:
                    raise KeyringError(f"no key material for user {user_key!r}")
                if row["destroyed_at"]:
                    raise ShreddedKeyError(
                        f"key material for user {user_key!r} was crypto-shredded at {row['destroyed_at']}"
                    )
                dek = self._dek_locked(conn, user_key, dek_version)
            finally:
                conn.close()
            key = self._purpose_key(dek, purpose)
        try:
            return AESGCM(key).decrypt(nonce, ciphertext, _data_aad(user_key, purpose, dek_version))
        except InvalidTag as exc:
            raise DecryptionError(
                f"blob failed authentication for user {user_key!r} purpose {purpose!r}"
                " (wrong user, wrong purpose, or corrupted ciphertext)"
            ) from exc

    def crypto_shred(self, user_id: str) -> dict[str, Any]:
        """Destroy the user's key material: zero-overwrite then delete every wrap
        row, tombstone user_keys.destroyed_at, purge cached DEKs. Every CXE1 blob
        ever written for this user (including inside backups) is now unreadable."""
        user_key = self._normalize_user(user_id)
        timestamp = _now_iso()
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    wraps = conn.execute(
                        "SELECT dek_version, wrap_type, length(wrapped_dek) AS n FROM user_key_wraps WHERE user_id = ?",
                        (user_key,),
                    ).fetchall()
                    for wrap in wraps:
                        conn.execute(
                            "UPDATE user_key_wraps SET wrapped_dek = ? WHERE user_id = ? AND dek_version = ? AND wrap_type = ?",
                            (bytes(int(wrap["n"] or 0)), user_key, wrap["dek_version"], wrap["wrap_type"]),
                        )
                    conn.execute("DELETE FROM user_key_wraps WHERE user_id = ?", (user_key,))
                    updated = conn.execute(
                        "UPDATE user_keys SET destroyed_at = ? WHERE user_id = ? AND destroyed_at IS NULL",
                        (timestamp, user_key),
                    ).rowcount
                    if not updated:
                        # Tombstone even a never-keyed user so post-shred encrypts
                        # can never silently mint fresh key material.
                        conn.execute(
                            """
                            INSERT INTO user_keys (user_id, dek_version, write_count, created_at, destroyed_at)
                            VALUES (?, 0, 0, ?, ?)
                            ON CONFLICT(user_id) DO UPDATE SET destroyed_at = COALESCE(user_keys.destroyed_at, excluded.destroyed_at)
                            """,
                            (user_key, timestamp, timestamp),
                        )
            finally:
                conn.close()
            for cache_key in [key for key in self._cache if key[0] == user_key]:
                self._cache.pop(cache_key, None)
        return {"user_id": user_key, "wraps_destroyed": len(wraps), "destroyed_at": timestamp}

    # --------------------------------------------------------------- internals

    @staticmethod
    def _normalize_user(user_id: str) -> str:
        user_key = str(user_id or "").strip()
        if not user_key:
            raise ValueError("user_id is required")
        return user_key

    @staticmethod
    def _validate_purpose(purpose: str) -> None:
        if purpose not in PURPOSES:
            raise ValueError(f"purpose must be one of {PURPOSES}, got {purpose!r}")

    @staticmethod
    def _parse_envelope(blob: bytes) -> tuple[str, int, bytes, bytes]:
        offset = len(CXE1_MAGIC)
        if len(blob) < offset + 1:
            raise DecryptionError("truncated CXE1 blob")
        kek_id_len = blob[offset]
        offset += 1
        header_end = offset + kek_id_len + 4
        if kek_id_len < 1 or len(blob) < header_end + _DATA_NONCE_LEN + _GCM_TAG_LEN:
            raise DecryptionError("truncated CXE1 blob")
        try:
            kek_id = blob[offset : offset + kek_id_len].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DecryptionError("CXE1 blob carries a malformed kek_id") from exc
        (dek_version,) = struct.unpack(">I", blob[offset + kek_id_len : header_end])
        nonce = blob[header_end : header_end + _DATA_NONCE_LEN]
        ciphertext = blob[header_end + _DATA_NONCE_LEN :]
        return kek_id, dek_version, nonce, ciphertext

    @staticmethod
    def _purpose_key(master_dek: bytes, purpose: str) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(),
            length=_DEK_LEN,
            salt=None,
            info=HKDF_INFO_PREFIX + purpose.encode("ascii"),
        ).derive(master_dek)

    def _connect(self) -> "sqlite3.Connection":
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(self.SCHEMA)
        return conn

    def _current_key_locked(self, conn: "sqlite3.Connection", user_id: str) -> tuple[int, bytes, int]:
        """Return (dek_version, dek, write_count) for the user's current key,
        minting v1 for a new user and bumping dek_version when the nonce budget
        for the current version is exhausted. Caller holds self._lock and an
        open transaction."""
        row = conn.execute(
            "SELECT dek_version, write_count, destroyed_at FROM user_keys WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            dek = self._mint_locked(conn, user_id, 1)
            conn.execute(
                "INSERT INTO user_keys (user_id, dek_version, write_count, created_at) VALUES (?, 1, 0, ?)",
                (user_id, _now_iso()),
            )
            return 1, dek, 0
        if row["destroyed_at"]:
            raise ShreddedKeyError(
                f"key material for user {user_id!r} was crypto-shredded at {row['destroyed_at']}"
            )
        dek_version = int(row["dek_version"])
        write_count = int(row["write_count"] or 0)
        if write_count >= self.nonce_budget:
            # Nonce budget exhausted for this version: mint a fresh DEK under the
            # next version for new writes. Old versions stay readable — decrypt
            # selects the wrap row by the blob's embedded dek_version.
            dek_version += 1
            dek = self._mint_locked(conn, user_id, dek_version)
            conn.execute(
                "UPDATE user_keys SET dek_version = ?, write_count = 0, rotated_at = ? WHERE user_id = ?",
                (dek_version, _now_iso(), user_id),
            )
            return dek_version, dek, 0
        return dek_version, self._dek_locked(conn, user_id, dek_version), write_count

    def _mint_locked(self, conn: "sqlite3.Connection", user_id: str, dek_version: int) -> bytes:
        dek = secrets.token_bytes(_DEK_LEN)
        kek_id, wrapped = self.provider.wrap_dek(user_id, dek_version, dek)
        conn.execute(
            """
            INSERT INTO user_key_wraps (user_id, dek_version, wrap_type, kek_id, wrapped_dek, kdf_params_json, created_at)
            VALUES (?, ?, ?, ?, ?, NULL, ?)
            """,
            (user_id, dek_version, self.SERVICE_WRAP, kek_id, wrapped, _now_iso()),
        )
        self._cache_put(user_id, dek_version, dek)
        return dek

    def _dek_locked(self, conn: "sqlite3.Connection", user_id: str, dek_version: int) -> bytes:
        cached = self._cache_get(user_id, dek_version)
        if cached is not None:
            return cached
        row = conn.execute(
            "SELECT kek_id, wrapped_dek FROM user_key_wraps WHERE user_id = ? AND dek_version = ? AND wrap_type = ?",
            (user_id, dek_version, self.SERVICE_WRAP),
        ).fetchone()
        if row is None:
            raise KeyringError(
                f"no service wrap for user {user_id!r} dek_version {dek_version} — key material is missing"
            )
        dek = self.provider.unwrap_dek(user_id, dek_version, str(row["kek_id"]), bytes(row["wrapped_dek"]))
        self._cache_put(user_id, dek_version, dek)
        return dek

    def _cache_get(self, user_id: str, dek_version: int) -> bytes | None:
        key = (user_id, dek_version)
        entry = self._cache.get(key)
        if entry is None:
            return None
        dek, expires_at = entry
        if self._clock() >= expires_at:
            self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return dek

    def _cache_put(self, user_id: str, dek_version: int, dek: bytes) -> None:
        key = (user_id, dek_version)
        self._cache[key] = (dek, self._clock() + self._cache_ttl)
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)


class CredentialCipher:
    """Tiny adapter binding a UserKeyring to CortexVault's loose cipher slot
    with purpose='credentials'. Vault code stays stdlib-clean: it only ever
    duck-calls encrypt/decrypt/is_encrypted on whatever object it was handed."""

    purpose = "credentials"

    def __init__(self, keyring: UserKeyring) -> None:
        self.keyring = keyring

    def encrypt(self, user_id: str, payload: bytes) -> bytes:
        return self.keyring.encrypt_blob(user_id, self.purpose, payload)

    def decrypt(self, user_id: str, blob: bytes) -> bytes:
        return self.keyring.decrypt_blob(user_id, self.purpose, blob)

    @staticmethod
    def is_encrypted(blob: bytes | bytearray | memoryview) -> bool:
        return is_encrypted(blob)
