from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import zipfile
import fcntl
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .config import APP_BRAND
from .vault_markdown import (
    atomic_write_text,
    parse_memory_markdown,
    render_daily_markdown,
    render_entity_moc_markdown,
    render_home_markdown,
    render_memory_markdown,
)


# A reentrant thread + process lock per vault root. Hosted deployments run multiple API workers
# plus a background worker against the same bucket vault. A threading.RLock alone does not
# serialize those processes: concurrent read-modify-write cycles silently lose settings,
# credentials, and events. The outermost acquisition takes an advisory flock; nested calls in the
# same thread reuse it. Cortex targets macOS/Linux, where fcntl.flock is available.
class _VaultInterProcessRLock:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._thread_lock = threading.RLock()
        self._local = threading.local()

    def __enter__(self) -> "_VaultInterProcessRLock":
        self._thread_lock.acquire()
        depth = int(getattr(self._local, "depth", 0))
        if depth == 0:
            handle = None
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                handle = (self.root / ".cortex-vault.lock").open("a+b")
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                self._local.handle = handle
            except Exception:
                if handle is not None:
                    handle.close()
                self._thread_lock.release()
                raise
        self._local.depth = depth + 1
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        depth = int(getattr(self._local, "depth", 1)) - 1
        self._local.depth = depth
        try:
            if depth == 0:
                handle = getattr(self._local, "handle", None)
                if handle is not None:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    finally:
                        handle.close()
                        del self._local.handle
        finally:
            self._thread_lock.release()


_VAULT_LOCKS: dict[str, _VaultInterProcessRLock] = {}
_VAULT_LOCKS_GUARD = threading.Lock()


def _vault_lock_for(root: Path) -> _VaultInterProcessRLock:
    key = os.path.abspath(os.path.expanduser(str(root)))
    with _VAULT_LOCKS_GUARD:
        lock = _VAULT_LOCKS.get(key)
        if lock is None:
            lock = _VaultInterProcessRLock(Path(key))
            _VAULT_LOCKS[key] = lock
        return lock


class CredentialEncryptionRequiredError(RuntimeError):
    """Raised when CORTEX_REQUIRE_ENCRYPTED_CREDENTIALS enforcement is on but the
    vault has no cipher to encrypt a credential with — writing plaintext would
    violate the enforcement contract, so the write is refused instead."""


VAULT_FORMAT = "cortex-local-vault"
VAULT_VERSION = 1
VAULT_DIRECTORIES = (
    "imports",
    "source_accounts",
    "sync_cursors",
    "sync_devices",
    "sync_receipts",
    "captures",
    "memories",
    "tasks",
    "entities",
    "graph_edges",
    "deletion_tombstones",
    "attachments",
    "backups",
    "exports",
    # Pinned context packs (Phase 0 substrate). Machine-owned, content-addressed JSON blobs.
    # DELIBERATELY absent from RESTORE_DIRECTORIES: packs are an audit artifact of past agent
    # calls, not durable user records — reconcile (globs memories/** only) and backup restore
    # ignore them, while git/iCloud still syncs them for cross-device replay.
    "context_packs",
)
RESTORE_ROOT_FILES = {"manifest.json", "settings.json", "events.jsonl"}
RESTORE_DIRECTORIES = {"imports", "source_accounts", "sync_cursors", "sync_devices", "sync_receipts", "captures", "memories", "tasks", "entities", "graph_edges", "deletion_tombstones", "attachments"}
# Entity Map-of-Content folders. DELIBERATELY not in VAULT_DIRECTORIES / RESTORE_DIRECTORIES:
# reconcile globs only memories/**, and backup restores only RESTORE_DIRECTORIES, so these
# machine-owned, fully-regenerable pages are invisible to both. They still sync via git/iCloud
# (that is the whole point — a browsable People/Projects/Topics graph in the vault).
ENTITY_MOC_DIRECTORIES = ("People", "Projects", "Orgs", "Topics")
ENTITY_KIND_TO_MOC_DIR = {"person": "People", "project": "Projects", "org": "Orgs", "topic": "Topics"}
# Machine-owned vault pages (N3/N4/N5). Like the MOC folders, these are DELIBERATELY not in
# VAULT_DIRECTORIES / RESTORE_DIRECTORIES, so reconcile (globs memories/** only) and backup ignore
# them; they still sync via git/iCloud (that is the point — a browsable vault). Fully regenerable.
HOME_PAGE_FILENAME = f"{APP_BRAND} — Start Here.md"
DAILY_DIRECTORY = "Journal"
CONSTELLATION_CANVAS_FILENAME = "Constellation.canvas"
# A generated daily page is named exactly YYYY-MM-DD.md. Pruning/listing keys on this shape so a
# user's own note dropped into Journal/ is never mistaken for a generated page (and never deleted).
_DAILY_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
BACKUP_DENY_FILENAMES = {
    ".cortex-vault.lock",
    ".env",
    ".netrc",
    "credentials.json",
    "cortex-app.log",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}
BACKUP_DENY_NAME_PREFIXES = (".cortex-backup-",)


def vault_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_segment(value: str | None, fallback: str = "unknown") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value or "").strip("-._")
    return cleaned[:80] or fallback


class CortexVault:
    """User-owned local vault with JSON records and a rebuildable SQLite index."""

    def __init__(self, root: Path, index_path: Path, cipher: Any | None = None, enforce_encryption: bool = False):
        self.root = Path(root).expanduser()
        self.index_path = Path(index_path).expanduser()
        # Optional per-user credential cipher (duck-typed: encrypt(user_id, bytes) -> bytes,
        # decrypt(user_id, bytes) -> bytes). None = plaintext behavior, byte-identical to
        # before. Kept loosely typed on purpose: the hosted plane injects a keyring-backed
        # adapter (see backend/app/keyring.py) but this stdlib-only module never imports it.
        self.cipher = cipher
        # Encrypted-credentials enforcement (CORTEX_REQUIRE_ENCRYPTED_CREDENTIALS,
        # docs/ACCOUNTS_ENCRYPTION_DESIGN.md §4). When True, write_source_credential
        # MUST refuse to persist a plaintext credential: it always encrypts, and if
        # no cipher is present it raises rather than silently writing plaintext.
        # Default False keeps the local/stdlib path byte-identical (no cipher, no
        # enforcement -> writes plaintext exactly as before). Threaded in alongside
        # the cipher via StoreRegistry.store_for_user in hosted mode.
        self.enforce_encryption = bool(enforce_encryption)
        # Each memory has a human-readable Markdown representation (frontmatter+body).
        # Editable memory fields in Markdown win during rebuild; JSON retains immutable/system
        # fields and remains authoritative for non-memory record types.
        self.markdown_mirror = True
        # Serializes read-modify-write of the shared root-level JSON files (see _vault_lock_for).
        self._lock = _vault_lock_for(self.root)

    def read_snapshot(self) -> _VaultInterProcessRLock:
        """Return the vault-wide lock for a multi-read consistent snapshot.

        Callers that derive destructive decisions from several filesystem
        observations must hold this context across the entire observation.
        """
        return self._lock

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def settings_path(self) -> Path:
        return self.root / "settings.json"

    @property
    def events_path(self) -> Path:
        return self.root / "events.jsonl"

    @property
    def credentials_path(self) -> Path:
        return self.root / "credentials.json"

    def _chmod_credentials_file(self) -> None:
        try:
            if self.credentials_path.exists():
                self.credentials_path.chmod(0o600)
        except OSError:
            pass

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"

    @property
    def tombstones_dir(self) -> Path:
        return self.root / "deletion_tombstones"

    def ensure(self) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            for directory in VAULT_DIRECTORIES:
                (self.root / directory).mkdir(parents=True, exist_ok=True)
            if not self.events_path.exists():
                self.events_path.touch()
            if not self.settings_path.exists():
                self._write_json(
                    self.settings_path,
                    {
                        "vault_record_type": "settings",
                        "vault_record_version": VAULT_VERSION,
                        "updated_at": vault_now(),
                        "users": {},
                    },
                )
            if not self.manifest_path.exists():
                self._write_json(
                    self.manifest_path,
                    {
                        "name": f"{APP_BRAND} Vault",
                        "format": VAULT_FORMAT,
                        "version": VAULT_VERSION,
                        "created_at": vault_now(),
                        "updated_at": vault_now(),
                        "source_of_truth": "markdown_memories_and_json_records_and_events",
                        "index_role": "rebuildable_local_search_index",
                        "index_path": self._relative_or_absolute(self.index_path),
                        "directories": list(VAULT_DIRECTORIES),
                    },
                )
            else:
                manifest = self._read_json(self.manifest_path, {})
                manifest.update(
                    {
                        "format": manifest.get("format", VAULT_FORMAT),
                        "version": manifest.get("version", VAULT_VERSION),
                        "updated_at": vault_now(),
                        "source_of_truth": "markdown_memories_and_json_records_and_events",
                        "index_role": "rebuildable_local_search_index",
                        "index_path": self._relative_or_absolute(self.index_path),
                        "directories": list(VAULT_DIRECTORIES),
                    }
                )
                self._write_json(self.manifest_path, manifest)
            self._ensure_sync_scaffolding()

    def _ensure_sync_scaffolding(self) -> None:
        """Make the vault safe + pleasant to sync (git/iCloud/Syncthing) and to open in any Markdown editor.

        Writes a .gitignore that syncs the durable, user-owned records (Markdown notes + JSON)
        while excluding the rebuildable SQLite index, local backups, temp files, and — critically
        — secrets (credentials.json, logs). Also drops a README so the folder explains itself.
        Both are written only if absent, so a user's edits are never clobbered.
        """
        gitignore_path = self.root / ".gitignore"
        if not gitignore_path.exists():
            gitignore = (
                f"# {APP_BRAND} vault — sync the durable records (Markdown notes + JSON), not the\n"
                "# rebuildable index, local backups, temp files, or secrets.\n"
                "*.sqlite\n"
                "*.sqlite-*\n"
                "*.db\n"
                "*.db-*\n"
                "credentials.json\n"
                ".cortex-vault.lock\n"
                "*.log\n"
                "backups/\n"
                ".*.tmp\n"
                "*.tmp\n"
                ".DS_Store\n"
            )
            try:
                atomic_write_text(gitignore_path, gitignore)
            except OSError:
                pass
        readme_path = self.root / "README.md"
        if not readme_path.exists():
            readme = (
                f"# Your {APP_BRAND} Vault\n\n"
                f"This folder is your {APP_BRAND} memory, stored as plain files you own.\n\n"
                "- `memories/` — your memories as Markdown notes (YAML frontmatter + text). Open\n"
                f"  this folder in any Markdown editor. Edit a note and {APP_BRAND} picks up the\n"
                "  change; add a note and it becomes a memory; delete one to remove it.\n"
                "- `captures/`, `entities/`, `tasks/`, ... — supporting records.\n"
                f"- The SQLite index and `credentials.json` are {APP_BRAND}'s private working files —\n"
                "  the index is rebuildable from the Markdown and JSON records; `credentials.json` holds secrets,\n"
                "  so both are excluded from sync by `.gitignore`.\n\n"
                f"Even if {APP_BRAND} goes away, these Markdown files stay readable and yours.\n"
            )
            try:
                atomic_write_text(readme_path, readme)
            except OSError:
                pass

    def write_settings(self, user_id: str, settings: dict[str, Any]) -> Path:
        self.ensure()
        with self._lock:
            payload = self._read_json(self.settings_path, {"users": {}})
            payload.setdefault("users", {})
            payload["users"][user_id] = {
                "user_id": user_id,
                "settings": settings,
                "updated_at": vault_now(),
            }
            payload["updated_at"] = vault_now()
            return self._write_json(self.settings_path, payload)

    def read_settings(self, user_id: str) -> dict[str, Any] | None:
        payload = self._read_json(self.settings_path, {})
        user = (payload.get("users") or {}).get(user_id)
        if not isinstance(user, dict):
            return None
        settings = user.get("settings")
        return settings if isinstance(settings, dict) else None

    def append_event(self, event: dict[str, Any]) -> None:
        self.ensure()
        payload = {
            "vault_record_type": "event",
            "vault_record_version": VAULT_VERSION,
            **event,
        }
        # Hold the vault lock so an append can't interleave with the delete_user_records
        # read-filter-replace rewrite of events.jsonl (which would drop this line).
        with self._lock:
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def write_capture_bundle(
        self,
        *,
        capture: dict[str, Any],
        memories: list[dict[str, Any]],
        tasks: list[dict[str, Any]],
        entities: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> dict[str, str]:
        # A backup/delete/restore must observe either the whole capture-derived
        # bundle or none of it. The lock is reentrant, so the individual write
        # helpers can retain their own safety boundary.
        with self._lock:
            self.ensure()
            written: dict[str, str] = {}
            written["capture"] = str(self.write_capture(capture))
            for memory in memories:
                written[f"memory:{memory['id']}"] = str(self.write_memory(memory))
            for task in tasks:
                written[f"task:{task['id']}"] = str(self.write_task(task))
            for entity in entities:
                written[f"entity:{entity['id']}"] = str(self.write_entity(entity))
            for edge in edges:
                written[f"edge:{edge['id']}"] = str(self.write_edge(edge))
            return written

    def write_portable_memory_bundle(
        self,
        *,
        capture: dict[str, Any] | None,
        memories: list[dict[str, Any]],
    ) -> None:
        """Write one portable import's vault records as a rollback-safe group.

        SQLite cannot share a transaction with the filesystem. The caller writes this group before
        committing SQLite, so a later vault failure must restore every path already touched. Holding
        the process-wide reentrant vault lock prevents another writer from observing or modifying the
        snapshot while the group is in flight.
        """
        self.ensure()
        paths: list[Path] = []
        if capture is not None:
            day = safe_segment(str(capture.get("captured_at", ""))[:10], "undated")
            paths.append(
                self.root / "captures" / day / f"{safe_segment(capture.get('id'), 'capture')}.json"
            )
        for memory in memories:
            kind = safe_segment(memory.get("kind"), "memory")
            paths.append(
                self.root / "memories" / kind / f"{safe_segment(memory.get('id'), 'memory')}.json"
            )
            paths.append(self.memory_markdown_path(memory))

        with self._lock:
            snapshots = {path: path.read_bytes() if path.exists() else None for path in paths}
            try:
                if capture is not None:
                    self.write_capture(capture)
                for memory in memories:
                    self.write_memory(memory)
            except Exception:
                for path, previous in snapshots.items():
                    if previous is None:
                        path.unlink(missing_ok=True)
                        continue
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temp = path.with_name(path.name + ".portable-rollback.tmp")
                    temp.write_bytes(previous)
                    temp.replace(path)
                raise

    def write_capture(self, record: dict[str, Any]) -> Path:
        day = safe_segment(str(record.get("captured_at", ""))[:10], "undated")
        path = self.root / "captures" / day / f"{safe_segment(record.get('id'), 'capture')}.json"
        return self._write_record(path, "capture", record)

    def write_import(self, record: dict[str, Any]) -> Path:
        day = safe_segment(str(record.get("created_at", ""))[:10], "undated")
        path = self.root / "imports" / day / f"{safe_segment(record.get('id'), 'import')}.json"
        return self._write_record(path, "import", record)

    def context_pack_path(self, pack_sha: str) -> Path:
        """Content-addressed pack location: context_packs/<sha[:2]>/<sha>.json. Sharded on the
        first byte so a long-lived vault never accumulates one giant directory."""
        sha = safe_segment(str(pack_sha or ""), "pack")
        return self.root / "context_packs" / sha[:2] / f"{sha}.json"

    def write_context_pack(self, pack_sha: str, canonical_bytes: bytes) -> Path:
        """Store the EXACT canonical pack bytes (already hashed by the caller). Unlike
        _write_record, no envelope keys are injected: byte-identity is the whole contract —
        sha256(file bytes) == pack_sha must hold forever, so replay/verify can prove integrity.
        Content-addressed files are immutable: an existing file is left untouched."""
        with self._lock:
            path = self.context_pack_path(pack_sha)
            if path.exists():
                return path
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(path.name + ".tmp")
            temp.write_bytes(canonical_bytes)
            temp.replace(path)
            return path

    def read_context_pack(self, pack_sha: str) -> bytes | None:
        path = self.context_pack_path(pack_sha)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def working_canvas_evidence_path(self, raw_sha256: str) -> Path:
        """Content-addressed raw evidence for symbolic working-memory canvas nodes."""
        sha = safe_segment(str(raw_sha256 or ""), "evidence")
        return self.root / "working_canvas" / "evidence" / sha[:2] / f"{sha}.txt"

    def write_working_canvas_evidence(self, raw_sha256: str, raw_bytes: bytes) -> Path:
        with self._lock:
            path = self.working_canvas_evidence_path(raw_sha256)
            if path.exists():
                return path
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(path.name + ".tmp")
            temp.write_bytes(raw_bytes)
            temp.replace(path)
            return path

    def read_working_canvas_evidence(self, raw_sha256: str) -> bytes | None:
        try:
            return self.working_canvas_evidence_path(raw_sha256).read_bytes()
        except FileNotFoundError:
            return None

    def write_source_account(self, record: dict[str, Any]) -> Path:
        user_id = safe_segment(record.get("user_id"), "unknown")
        source = safe_segment(record.get("source"), "source")
        path = self.root / "source_accounts" / user_id / source / f"{safe_segment(record.get('id'), 'source-account')}.json"
        return self._write_record(path, "source_account", record)

    def write_sync_cursor(self, record: dict[str, Any]) -> Path:
        user_id = safe_segment(record.get("user_id"), "unknown")
        source = safe_segment(record.get("source"), "source")
        path = self.root / "sync_cursors" / user_id / source / f"{safe_segment(record.get('id'), 'sync-cursor')}.json"
        return self._write_record(path, "sync_cursor", record)

    def write_sync_device(self, record: dict[str, Any]) -> Path:
        user_id = safe_segment(record.get("user_id"), "unknown")
        path = self.root / "sync_devices" / user_id / f"{safe_segment(record.get('id'), 'sync-device')}.json"
        return self._write_record(path, "sync_device", record)

    def write_sync_receipt(self, record: dict[str, Any]) -> Path:
        user_id = safe_segment(record.get("user_id"), "unknown")
        device_id = safe_segment(record.get("device_id"), "sync-device")
        path = self.root / "sync_receipts" / user_id / device_id / f"{safe_segment(record.get('id'), 'sync-receipt')}.json"
        return self._write_record(path, "sync_receipt", record)

    def write_source_credential(self, *, user_id: str, source_account_id: str, source: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure()
        user_key = str(user_id or "").strip()
        account_key = str(source_account_id or "").strip()
        if not user_key or not account_key:
            raise ValueError("user_id and source_account_id are required")
        if self.enforce_encryption and self.cipher is None:
            # Enforcement on but no cipher reached this vault: refuse loudly rather
            # than silently persisting a plaintext credential (design doc §4). This
            # only fires in a misconfigured hosted deployment — enforcement is off
            # by default and the local/stdlib path never sets it.
            raise CredentialEncryptionRequiredError(
                "CORTEX_REQUIRE_ENCRYPTED_CREDENTIALS is set but no credential cipher "
                "is configured for this vault; refusing to write a plaintext credential"
            )
        now = vault_now()
        with self._lock:
            credentials = self._read_json(
                self.credentials_path,
                {
                    "vault_record_type": "credentials",
                    "vault_record_version": VAULT_VERSION,
                    "users": {},
                },
            )
            credentials.setdefault("users", {})
            user_credentials = credentials["users"].setdefault(user_key, {})
            existing = user_credentials.get(account_key) if isinstance(user_credentials.get(account_key), dict) else {}
            record = {
                "user_id": user_key,
                "source_account_id": account_key,
                "source": safe_segment(source, "source"),
                "created_at": existing.get("created_at") or now,
                "updated_at": now,
            }
            if self.cipher is not None:
                # Hosted mode: the secret payload is stored only as a CXE1 envelope
                # (hex-encoded) encrypted under this user's 'credentials' subkey — in
                # bucket mode credentials.json is shared across tenants, so co-tenant
                # blobs are mutually unreadable and crypto-shred makes them gone forever.
                record["payload_cxe1"] = self._encrypt_credential_payload(user_key, payload)
            else:
                record["payload"] = payload
            user_credentials[account_key] = record
            credentials["vault_updated_at"] = now
            self._write_json(self.credentials_path, credentials)
            self._chmod_credentials_file()
        return {
            "credential_ref": f"source_credential:{account_key}",
            "source_account_id": account_key,
            "source": record["source"],
            "updated_at": now,
        }

    def read_source_credential(self, *, user_id: str, source_account_id: str) -> dict[str, Any] | None:
        credentials = self._read_json(self.credentials_path, {})
        user_credentials = (credentials.get("users") or {}).get(user_id)
        if not isinstance(user_credentials, dict):
            return None
        record = user_credentials.get(source_account_id)
        if not isinstance(record, dict):
            return None
        encrypted = record.get("payload_cxe1")
        payload = record.get("payload")
        if isinstance(encrypted, str) and encrypted:
            if self.cipher is None:
                # Encrypted at rest but this process holds no key material
                # (e.g. local/stdlib runtime opening a hosted vault): unreadable.
                return None
            payload = self._decrypt_credential_payload(user_id, encrypted)
        elif self.cipher is not None and isinstance(payload, dict):
            # Legacy plaintext entry under an encrypting vault: lazy
            # read-migrate — re-store it encrypted so the plaintext copy is gone
            # after the first read (design doc §4, phase 1).
            self._migrate_plaintext_credential(user_id=user_id, source_account_id=source_account_id)
        if not isinstance(payload, dict):
            return None
        return {
            "credential_ref": f"source_credential:{source_account_id}",
            "source_account_id": source_account_id,
            "source": record.get("source"),
            "payload": payload,
            "updated_at": record.get("updated_at"),
        }

    def _encrypt_credential_payload(self, user_id: str, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        blob = self.cipher.encrypt(user_id, raw)
        return bytes(blob).hex()

    def _decrypt_credential_payload(self, user_id: str, encoded: str) -> dict[str, Any] | None:
        raw = self.cipher.decrypt(user_id, bytes.fromhex(encoded))
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _migrate_plaintext_credential(self, *, user_id: str, source_account_id: str) -> None:
        """Best-effort write-back of a legacy plaintext credential as a CXE1 envelope.
        Never lets a migration failure break the read path — the caller already has
        the plaintext payload in hand."""
        try:
            with self._lock:
                credentials = self._read_json(self.credentials_path, {})
                user_credentials = (credentials.get("users") or {}).get(user_id)
                if not isinstance(user_credentials, dict):
                    return
                record = user_credentials.get(source_account_id)
                if not isinstance(record, dict):
                    return
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    return
                record["payload_cxe1"] = self._encrypt_credential_payload(user_id, payload)
                record.pop("payload", None)
                credentials["vault_updated_at"] = vault_now()
                self._write_json(self.credentials_path, credentials)
                self._chmod_credentials_file()
        except Exception:
            pass

    def authorship_key(self) -> bytes:
        """Vault-local HMAC key for the Phase 3 authorship ledger. Stored inside
        credentials.json (already secret: chmod 600, gitignored, excluded from restore)
        so no new secret file is introduced. Created lazily on first use."""
        self.ensure()
        with self._lock:
            credentials = self._read_json(
                self.credentials_path,
                {
                    "vault_record_type": "credentials",
                    "vault_record_version": VAULT_VERSION,
                    "users": {},
                },
            )
            existing = str(credentials.get("authorship_key_hex") or "").strip()
            if existing:
                try:
                    return bytes.fromhex(existing)
                except ValueError:
                    pass  # corrupt hex: rotate below rather than crash forever
            key = os.urandom(32)
            credentials["authorship_key_hex"] = key.hex()
            credentials["vault_updated_at"] = vault_now()
            self._write_json(self.credentials_path, credentials)
            self._chmod_credentials_file()
            return key

    def portable_signing_keypair(self) -> tuple[bytes, bytes, str]:
        """Return the vault's stable Ed25519 portable-memory signing identity.

        The private seed stays in ``credentials.json`` beside the existing authorship key and is
        excluded from exports/backups. Bundles carry only the public key plus its SHA-256 key id,
        so another Cortex instance can verify a signature and optionally pin the expected signer.
        ``cryptography`` is imported lazily to keep ordinary standalone startup dependency-free.
        """
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except ImportError as exc:  # pragma: no cover - packaged backend always includes it
            raise RuntimeError("Portable bundle signing requires the cryptography package.") from exc

        self.ensure()
        with self._lock:
            credentials = self._read_json(
                self.credentials_path,
                {
                    "vault_record_type": "credentials",
                    "vault_record_version": VAULT_VERSION,
                    "users": {},
                },
            )
            seed_hex = str(credentials.get("portable_ed25519_private_key_hex") or "").strip()
            private_key = None
            if seed_hex:
                try:
                    seed = bytes.fromhex(seed_hex)
                    if len(seed) == 32:
                        private_key = Ed25519PrivateKey.from_private_bytes(seed)
                except (TypeError, ValueError):
                    private_key = None
            if private_key is None:
                private_key = Ed25519PrivateKey.generate()
                seed = private_key.private_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PrivateFormat.Raw,
                    encryption_algorithm=serialization.NoEncryption(),
                )
                credentials["portable_ed25519_private_key_hex"] = seed.hex()
                credentials["vault_updated_at"] = vault_now()
                self._write_json(self.credentials_path, credentials)
                self._chmod_credentials_file()
            else:
                seed = bytes.fromhex(seed_hex)
            public_key = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            key_id = hashlib.sha256(public_key).hexdigest()
            return seed, public_key, key_id

    def delete_source_credential(self, *, user_id: str, source_account_id: str) -> bool:
        with self._lock:
            credentials = self._read_json(self.credentials_path, {})
            user_credentials = (credentials.get("users") or {}).get(user_id)
            if not isinstance(user_credentials, dict) or source_account_id not in user_credentials:
                return False
            user_credentials.pop(source_account_id, None)
            credentials["vault_updated_at"] = vault_now()
            self._write_json(self.credentials_path, credentials)
            self._chmod_credentials_file()
            return True

    def write_memory(self, record: dict[str, Any]) -> Path:
        # JSON + Markdown are one logical memory representation. Holding the
        # vault lock across both prevents backups from capturing a mismatched
        # pair during an edit.
        with self._lock:
            kind = safe_segment(record.get("kind"), "memory")
            path = self.root / "memories" / kind / f"{safe_segment(record.get('id'), 'memory')}.json"
            written = self._write_record(path, "memory", record)
            self._write_memory_markdown(record)
            return written

    def memory_note_short_id(self, memory_id: str) -> str:
        """Stable 12-hex (48-bit) fingerprint of a memory id, used as the note-filename
        disambiguator AND by every stale-note sweep. Derived from the immutable id (never the
        summary/content) so it is invariant across edits — that is what makes glob-by-suffix
        reliable. Sweeps that mutate/delete also confirm the parsed frontmatter id, so even an
        (astronomically unlikely) collision can at worst rewrite the same logical note."""
        return hashlib.sha1(
            str(memory_id or "").encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:12]

    def memory_note_stem(self, record: dict[str, Any]) -> str:
        """`<summary-slug>--<shortid>` — the SINGLE source of both the on-disk note filename and the
        note-to-note wikilink target, so backlinks always resolve. The slug (from summary, else
        content, else id) makes the vault browsable; the shortid (hash of the immutable id) keeps
        the file identity fixed across summary edits."""
        memory_id = str(record.get("id") or "")
        label = record.get("summary") or record.get("content") or memory_id
        label = " ".join(str(label).split())  # collapse newlines/whitespace before slugging
        slug = (safe_segment(label, "memory")[:64]).strip("-._") or "memory"
        return f"{slug}--{self.memory_note_short_id(memory_id)}"

    def memory_markdown_path(self, record: dict[str, Any]) -> Path:
        """Human-readable note path: memories/<layer>/<summary-slug>--<shortid>.md. The slug makes
        the vault browsable; the shortid (hash of the immutable id) keeps the filename stable across
        summary edits and lets the stale-note sweeps glob by *--<short>.md. The memory id is still
        parsed from FRONTMATTER on rebuild, so the filename carries no load-bearing identity."""
        layer = safe_segment(record.get("layer") or record.get("kind"), "memory")
        return self.root / "memories" / layer / f"{self.memory_note_stem(record)}.md"

    def _iter_memory_notes_for_id(self, memory_id: str):
        """Yield every note path whose filename shortid matches this memory id (plus any legacy
        <id>.md note from before the slug rename). Replaces the old rglob('<id>.md') now that
        filenames are <slug>--<shortid>.md. Callers that mutate/delete must still confirm the parsed
        frontmatter id to be collision-proof."""
        base = self.root / "memories"
        if not base.exists():
            return
        short = self.memory_note_short_id(memory_id)
        seen: set[Path] = set()
        for pattern in (f"*--{short}.md", f"{safe_segment(memory_id)}.md"):
            for path in base.rglob(pattern):
                if path not in seen:
                    seen.add(path)
                    yield path

    def _write_memory_markdown(self, record: dict[str, Any]) -> None:
        if not self.markdown_mirror:
            return
        try:
            target = self.memory_markdown_path(record)
            # If the memory's layer OR summary changed (new note path), or a legacy <id>.md note
            # exists from before the slug rename, remove any stale note for this id at a different
            # path so a rebuild can't pick up an orphaned duplicate. _iter_memory_notes_for_id
            # matches by the id-derived shortid AND the legacy exact-id name, so this also performs
            # the passive migration: the first re-write of any legacy note cleans up the old file.
            memory_id = str(record.get("id") or "")
            base = self.root / "memories"
            if base.exists():
                for path in list(self._iter_memory_notes_for_id(memory_id)):
                    if path == target:
                        continue
                    # Confirm the note is OURS (or a corrupt/no-id note at our shortid) before
                    # unlinking, exactly like the delete/has/patch sweeps — so a shortid collision
                    # can never delete a DIFFERENT memory's note (the invariant memory_note_short_id
                    # promises).
                    try:
                        parsed_id = parse_memory_markdown(path.read_text(encoding="utf-8")).get("id")
                    except Exception:
                        parsed_id = None
                    if parsed_id and parsed_id != memory_id:
                        continue
                    try:
                        path.unlink()
                        self._prune_empty_parents(path.parent, base)
                    except OSError:
                        pass
            atomic_write_text(target, render_memory_markdown(record))
        except Exception:
            # Additive mirror: never let a Markdown write failure break the authoritative
            # JSON record write above.
            pass

    def migrate_memory_note_filenames(self, user_id: str | None = None) -> int:
        """Rename legacy memories/<layer>/<id>.md notes to the human-readable <slug>--<shortid>.md
        scheme. Idempotent (a note already at its target path is skipped) and content-preserving
        (atomic rename, not a re-render, so no content is ever lost — a stale generated-links block
        simply refreshes on the note's next write). Scoped to user_id when the vault is shared.
        Best-effort; returns the number of notes renamed."""
        with self._lock:
            return self._migrate_memory_note_filenames_locked(user_id)

    def _migrate_memory_note_filenames_locked(self, user_id: str | None = None) -> int:
        if not self.markdown_mirror:
            return 0
        base = self.root / "memories"
        if not base.exists():
            return 0
        renamed = 0
        for path in list(base.rglob("*.md")):
            try:
                record = parse_memory_markdown(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            memory_id = record.get("id")
            if not memory_id:
                continue
            if user_id is not None and record.get("user_id") not in (None, user_id):
                continue
            target = self.memory_markdown_path(record)
            if path == target:
                continue
            try:
                if target.exists():
                    # A new-scheme note for this id already exists (it is authoritative — written by
                    # the current save/patch path). This `path` is a stale legacy duplicate for the
                    # same id; unlink it rather than os.replace over the target, so we never clobber
                    # a note the user may have edited.
                    path.unlink()
                    self._prune_empty_parents(path.parent, base)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(path, target)  # atomic rename; content preserved exactly
                self._prune_empty_parents(path.parent, base)
                renamed += 1
            except OSError:
                continue
        return renamed

    def write_task(self, record: dict[str, Any]) -> Path:
        kind = safe_segment(record.get("kind"), "task")
        path = self.root / "tasks" / kind / f"{safe_segment(record.get('id'), 'task')}.json"
        return self._write_record(path, "task", record)

    def write_entity(self, record: dict[str, Any]) -> Path:
        kind = safe_segment(record.get("kind"), "entity")
        path = self.root / "entities" / kind / f"{safe_segment(record.get('id'), 'entity')}.json"
        return self._write_record(path, "entity", record)

    def write_edge(self, record: dict[str, Any]) -> Path:
        kind = safe_segment(record.get("kind"), "edge")
        path = self.root / "graph_edges" / kind / f"{safe_segment(record.get('id'), 'edge')}.json"
        return self._write_record(path, "graph_edge", record)

    # ---- Entity Map-of-Content (MOC) pages: browsable People/Projects/Orgs/Topics graph ----

    def entity_moc_dir_for_kind(self, kind: str | None) -> str:
        return ENTITY_KIND_TO_MOC_DIR.get(str(kind or "").strip().lower(), "Topics")

    def entity_moc_short_id(self, entity_id: str) -> str:
        """A stable, collision-resistant fingerprint of an entity id, used both as the MOC filename
        disambiguator and by the stale-page sweep. 16 hex = 64 bits: birthday-collision-safe well
        past any realistic entity count (an 8-hex/32-bit id collides around tens of thousands of
        entities and would clobber another entity's page)."""
        return hashlib.sha1(
            str(entity_id or "").encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:16]

    def entity_moc_stem(self, entity_id: str, label: str | None = None) -> str:
        """The MOC note filename stem — the SINGLE source of the entity->entity wikilink target and
        the on-disk filename, so links always resolve. `<slug>--<short>`: slug is human-browsable
        (from the label), short disambiguates + stays stable across label edits."""
        eid = str(entity_id or "")
        return f"{safe_segment(label or eid, 'entity')}--{self.entity_moc_short_id(eid)}"

    def entity_moc_path(self, page: dict[str, Any]) -> Path:
        folder = self.entity_moc_dir_for_kind(page.get("kind"))
        return self.root / folder / f"{self.entity_moc_stem(page.get('entity_id'), page.get('name'))}.md"

    def write_entity_markdown(self, page: dict[str, Any]) -> Path | None:
        """Write/refresh one entity MOC page. Additive & best-effort: a failure here never breaks
        the authoritative JSON entity write (mirrors _write_memory_markdown). If the label changed,
        the file moves — the stale page for this id (same hash8) is removed first."""
        if not self.markdown_mirror:
            return None
        entity_id = str(page.get("entity_id") or "")
        if not entity_id:
            return None
        try:
            with self._lock:
                target = self.entity_moc_path(page)
                short = self.entity_moc_short_id(entity_id)
                for folder in ENTITY_MOC_DIRECTORIES:
                    base = self.root / folder
                    if not base.exists():
                        continue
                    for path in base.glob(f"*--{short}.md"):
                        if path != target:
                            try:
                                path.unlink()
                            except OSError:
                                pass
                atomic_write_text(target, render_entity_moc_markdown(page))
                return target
        except Exception:
            return None

    def prune_entity_moc_pages(self, keep_short_ids: set[str]) -> int:
        """Delete MOC pages whose entity hash8 is not in keep_short_ids (entity deleted/merged away)."""
        with self._lock:
            removed = 0
            for folder in ENTITY_MOC_DIRECTORIES:
                base = self.root / folder
                if not base.exists():
                    continue
                for path in base.glob("*.md"):
                    stem = path.stem
                    short = stem.rsplit("--", 1)[-1] if "--" in stem else ""
                    if short and short not in keep_short_ids:
                        try:
                            path.unlink()
                            removed += 1
                        except OSError:
                            pass
            return removed

    # ---- N3 Home page / N4 Journal / N5 Canvas: machine-owned vault pages (outside memories/) ----

    def write_home_markdown(self, page: dict[str, Any]) -> Path | None:
        """Write/refresh the vault Home root page. Additive & best-effort (a failure never breaks a
        capture); lives at the vault root, invisible to reconcile + backup."""
        if not self.markdown_mirror:
            return None
        try:
            with self._lock:
                target = self.root / HOME_PAGE_FILENAME
                atomic_write_text(target, render_home_markdown(page))
                return target
        except Exception:
            return None

    def daily_markdown_path(self, date: str) -> Path:
        return self.root / DAILY_DIRECTORY / f"{safe_segment(date, 'undated')}.md"

    def write_daily_markdown(self, page: dict[str, Any]) -> Path | None:
        """Write/refresh one Journal/<date>.md page. Best-effort; outside memories/."""
        if not self.markdown_mirror:
            return None
        date = str(page.get("date") or "").strip()
        if not date:
            return None
        try:
            with self._lock:
                target = self.daily_markdown_path(date)
                atomic_write_text(target, render_daily_markdown(page))
                return target
        except Exception:
            return None

    def list_daily_dates(self) -> set[str]:
        """The set of dates that currently have a GENERATED Journal page on disk (YYYY-MM-DD.md only,
        so a user's own note in Journal/ is never counted or pruned)."""
        base = self.root / DAILY_DIRECTORY
        if not base.exists():
            return set()
        return {path.stem for path in base.glob("*.md") if _DAILY_DATE_RE.fullmatch(path.stem)}

    def prune_daily_pages(self, keep_dates: set[str]) -> int:
        """Delete generated Journal pages whose date is not in keep_dates (a day that fell empty
        within the regenerated window). ONLY ever deletes files whose stem is a YYYY-MM-DD date — a
        user's own note dropped into Journal/ (e.g. reading-list.md) is never touched. Days outside
        the caller's window are also never pruned (callers pass the full considered set)."""
        with self._lock:
            removed = 0
            base = self.root / DAILY_DIRECTORY
            if not base.exists():
                return 0
            for path in base.glob("*.md"):
                if not _DAILY_DATE_RE.fullmatch(path.stem):
                    continue  # not a generated day page (user-authored) -> never delete
                if path.stem not in keep_dates:
                    try:
                        path.unlink()
                        removed += 1
                    except OSError:
                        pass
            return removed

    def canvas_path(self, name: str = CONSTELLATION_CANVAS_FILENAME) -> Path:
        stem = safe_segment(name[:-7] if name.endswith(".canvas") else name, "Constellation")
        return self.root / f"{stem}.canvas"

    def write_canvas(self, doc: dict[str, Any], name: str = CONSTELLATION_CANVAS_FILENAME) -> Path | None:
        """Write/refresh the machine-owned Obsidian Canvas export (JSON). Best-effort; atomic (a
        crash mid-write can't leave a half-written .canvas). Deterministic serialization (we control
        key order) so an unchanged graph yields a byte-identical file — no git/iCloud churn."""
        if not self.markdown_mirror:
            return None
        try:
            with self._lock:
                target = self.canvas_path(name)
                atomic_write_text(target, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
                return target
        except Exception:
            return None

    def write_tombstone(
        self,
        *,
        user_id: str,
        object_type: str,
        object_id: str,
        deleted_at: str | None = None,
        reason: str = "deleted",
        related_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        record = {
            "id": f"del_{safe_segment(object_type, 'record')}_{safe_segment(object_id, 'object')}",
            "user_id": user_id,
            "object_type": object_type,
            "object_id": object_id,
            "deleted_at": deleted_at or vault_now(),
            "reason": reason,
            "related_ids": list(dict.fromkeys(related_ids or [])),
            "backup_policy": "block_restore",
            "metadata": metadata or {},
        }
        return self.write_tombstone_record(record)

    def write_tombstone_record(self, record: dict[str, Any]) -> Path:
        object_type = safe_segment(record.get("object_type"), "record")
        object_id = safe_segment(record.get("object_id"), "object")
        user_id = safe_segment(record.get("user_id"), "unknown")
        clean = {
            key: value
            for key, value in record.items()
            if key not in {"vault_record_type", "vault_record_version", "vault_updated_at"}
        }
        path = self.tombstones_dir / user_id / object_type / f"{object_id}.json"
        return self._write_record(path, "deletion_tombstone", clean)

    def patch_capture(self, capture_id: str, updates: dict[str, Any]) -> bool:
        return self._patch_first("captures", capture_id, updates)

    def patch_import(self, import_id: str, updates: dict[str, Any]) -> bool:
        return self._patch_first("imports", import_id, updates)

    def patch_source_account(self, account_id: str, updates: dict[str, Any]) -> bool:
        return self._patch_first("source_accounts", account_id, updates)

    def patch_sync_cursor(self, cursor_id: str, updates: dict[str, Any]) -> bool:
        return self._patch_first("sync_cursors", cursor_id, updates)

    def patch_memory(self, memory_id: str, updates: dict[str, Any]) -> bool:
        with self._lock:
            patched_json = self._patch_first("memories", memory_id, updates)
            # Keep the Markdown note in sync — it's the rebuild source of truth, so a patch that
            # only touched JSON (e.g. status -> archived, superseded_by) would otherwise be reverted
            # on the next Markdown-sourced rebuild. Also patches Markdown-native memories with no JSON.
            patched_markdown = self._patch_memory_markdown(memory_id, updates)
            return patched_json or patched_markdown

    def _patch_memory_markdown(self, memory_id: str, updates: dict[str, Any]) -> bool:
        if not self.markdown_mirror:
            return False
        base = self.root / "memories"
        if not base.exists():
            return False
        for path in self._iter_memory_notes_for_id(memory_id):
            try:
                record = parse_memory_markdown(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if record.get("id") != memory_id:
                continue
            record.update(updates)
            # _write_memory_markdown re-renders and removes any stale-path note (e.g. if the
            # patch changed the layer), so this both updates and de-orphans in one step.
            self._write_memory_markdown(record)
            return True
        return False

    def patch_task(self, task_id: str, updates: dict[str, Any]) -> bool:
        return self._patch_first("tasks", task_id, updates)

    def delete_capture(self, capture_id: str) -> bool:
        return self._delete_first("captures", capture_id)

    def delete_import(self, import_id: str) -> bool:
        return self._delete_first("imports", import_id)

    def delete_memory(self, memory_id: str) -> bool:
        # JSON and Markdown are one logical representation; a backup must not
        # observe the delete between the two filesystem mutations.
        with self._lock:
            removed_json = self._delete_first("memories", memory_id)
            removed_markdown = self._delete_memory_markdown(memory_id)
            return removed_json or removed_markdown

    def delete_task(self, task_id: str) -> bool:
        return self._delete_first("tasks", task_id)

    def delete_edge(self, edge_id: str) -> bool:
        return self._delete_first("graph_edges", edge_id)

    def iter_memory_markdown_records(self, user_id: str | None = None) -> list[dict[str, Any]]:
        """Read memories back from the human-readable Markdown notes (the Phase-2 source of
        truth). Lets the SQLite index be fully rebuilt from the files a user owns/edits, so
        "delete the app database and it comes back from your Markdown" actually holds."""
        base = self.root / "memories"
        if not base.exists():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(base.rglob("*.md")):
            try:
                record = parse_memory_markdown(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not record.get("id"):
                continue
            if user_id is not None and record.get("user_id") != user_id:
                continue
            records.append(record)
        return records

    def has_memory_markdown(self, memory_id: str) -> bool:
        """True if a Markdown note file exists for this memory id (even if it fails to parse) —
        so reconcile treats a corrupt-but-present note as still-present, not as a deletion. Notes are
        now <slug>--<shortid>.md, so we match by the id-derived shortid (plus any legacy <id>.md) and
        confirm the parsed frontmatter id, so a shortid collision can't mask a real deletion."""
        if not memory_id:
            return False
        base = self.root / "memories"
        if not base.exists():
            return False
        for path in self._iter_memory_notes_for_id(memory_id):
            try:
                parsed_id = parse_memory_markdown(path.read_text(encoding="utf-8")).get("id")
            except Exception:
                return True  # unreadable file at our shortid -> present (reconcile safety)
            # Our note, or a corrupt/garbage note at our shortid (no parseable id), counts as present;
            # only a note carrying a DIFFERENT valid id (an impossible-in-practice collision) doesn't.
            if not parsed_id or parsed_id == memory_id:
                return True
        return False

    def markdown_backfill_done(self, user_id: str) -> bool:
        manifest = self._read_json(self.manifest_path, {})
        done = manifest.get("markdown_backfilled_users")
        return isinstance(done, list) and user_id in done

    def mark_markdown_backfill_done(self, user_id: str) -> None:
        with self._lock:
            manifest = self._read_json(self.manifest_path, {"format": VAULT_FORMAT, "version": VAULT_VERSION})
            done = manifest.get("markdown_backfilled_users")
            if not isinstance(done, list):
                done = []
            if user_id not in done:
                done.append(user_id)
            manifest["markdown_backfilled_users"] = done
            manifest["updated_at"] = vault_now()
            self._write_json(self.manifest_path, manifest)

    def iter_records(self, record_dir: str, user_id: str | None = None) -> Iterable[dict[str, Any]]:
        base = self.root / record_dir
        if not base.exists():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(base.rglob("*.json")):
            payload = self._read_json(path, {})
            if not isinstance(payload, dict):
                continue
            if user_id is not None and payload.get("user_id") != user_id:
                continue
            records.append(payload)
        return records

    def iter_tombstones(self, user_id: str | None = None) -> Iterable[dict[str, Any]]:
        return self.iter_records("deletion_tombstones", user_id)

    def apply_tombstones(self, user_id: str) -> dict[str, int]:
        # A tombstone can delete a graph of related records. Freeze that full
        # logical operation so a backup never captures only a subset.
        with self._lock:
            return self._apply_tombstones_locked(user_id)

    def _apply_tombstones_locked(self, user_id: str) -> dict[str, int]:
        self.ensure()
        counts = {
            "applied": 0,
            "captures": 0,
            "imports": 0,
            "memories": 0,
            "tasks": 0,
            "graph_edges": 0,
        }
        for tombstone in self.iter_tombstones(user_id):
            if tombstone.get("backup_policy", "block_restore") != "block_restore":
                continue
            object_type = str(tombstone.get("object_type") or "")
            object_id = str(tombstone.get("object_id") or "")
            if not object_id:
                continue
            related_ids = [
                str(value)
                for value in tombstone.get("related_ids", [])
                if isinstance(value, str) and value
            ]

            if object_type == "import":
                imports_deleted, _ = self._delete_matching_records(
                    "imports",
                    lambda payload: payload.get("user_id") == user_id and payload.get("id") == object_id,
                )
                captures_deleted, capture_ids = self._delete_matching_records(
                    "captures",
                    lambda payload: payload.get("user_id") == user_id and payload.get("import_id") == object_id,
                )
                memories_deleted, memory_ids = self._delete_matching_records(
                    "memories",
                    lambda payload: payload.get("user_id") == user_id and payload.get("capture_id") in capture_ids,
                )
                tasks_deleted, task_ids = self._delete_matching_records(
                    "tasks",
                    lambda payload: payload.get("user_id") == user_id and payload.get("capture_id") in capture_ids,
                )
                counts["imports"] += imports_deleted
                counts["captures"] += captures_deleted
                counts["memories"] += memories_deleted
                counts["tasks"] += tasks_deleted
                counts["graph_edges"] += self._delete_graph_edges_for_ids(
                    user_id,
                    [object_id, *capture_ids, *memory_ids, *task_ids, *related_ids],
                )
                counts["applied"] += 1
            elif object_type == "capture":
                captures_deleted, _ = self._delete_matching_records(
                    "captures",
                    lambda payload: payload.get("user_id") == user_id and payload.get("id") == object_id,
                )
                memories_deleted, memory_ids = self._delete_matching_records(
                    "memories",
                    lambda payload: payload.get("user_id") == user_id and payload.get("capture_id") == object_id,
                )
                tasks_deleted, task_ids = self._delete_matching_records(
                    "tasks",
                    lambda payload: payload.get("user_id") == user_id and payload.get("capture_id") == object_id,
                )
                counts["captures"] += captures_deleted
                counts["memories"] += memories_deleted
                counts["tasks"] += tasks_deleted
                counts["graph_edges"] += self._delete_graph_edges_for_ids(
                    user_id,
                    [object_id, *memory_ids, *task_ids, *related_ids],
                )
                counts["applied"] += 1
            elif object_type == "memory":
                memories_deleted, _ = self._delete_matching_records(
                    "memories",
                    lambda payload: payload.get("user_id") == user_id and payload.get("id") == object_id,
                )
                counts["memories"] += memories_deleted
                counts["graph_edges"] += self._delete_graph_edges_for_ids(user_id, [object_id, *related_ids])
                counts["applied"] += 1
            elif object_type == "task":
                tasks_deleted, _ = self._delete_matching_records(
                    "tasks",
                    lambda payload: payload.get("user_id") == user_id and payload.get("id") == object_id,
                )
                counts["tasks"] += tasks_deleted
                counts["graph_edges"] += self._delete_graph_edges_for_ids(user_id, [object_id, *related_ids])
                counts["applied"] += 1
            elif object_type in {"edge", "graph_edge"}:
                edges_deleted, _ = self._delete_matching_records(
                    "graph_edges",
                    lambda payload: payload.get("user_id") == user_id and payload.get("id") == object_id,
                )
                counts["graph_edges"] += edges_deleted
                counts["applied"] += 1
        return counts

    def iter_events(self, user_id: str | None = None) -> Iterable[dict[str, Any]]:
        with self._lock:
            if not self.events_path.exists():
                return []
            events: list[dict[str, Any]] = []
            with self.events_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if user_id is not None and payload.get("user_id") != user_id:
                        continue
                    events.append(payload)
            return events

    def create_zip_backup(self, timestamp: str, sqlite_backup_path: Path) -> Path:
        self.ensure()
        with self._lock:
            return self._create_zip_backup_locked(timestamp, sqlite_backup_path)

    def _create_zip_backup_locked(self, timestamp: str, sqlite_backup_path: Path) -> Path:
        backup_path = self.backups_dir / f"cortex-vault-{timestamp}.zip"
        with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(self._iter_backup_files()):
                if path == self.index_path or path.name in {self.index_path.name + "-wal", self.index_path.name + "-shm"}:
                    continue
                relative = path.relative_to(self.root)
                archive.write(path, relative.as_posix())
            if sqlite_backup_path.exists():
                archive.write(sqlite_backup_path, "index.sqlite")
        return backup_path

    def _iter_backup_files(self) -> Iterable[Path]:
        for file_name in sorted(RESTORE_ROOT_FILES):
            path = self.root / file_name
            if self._should_include_backup_file(path):
                yield path
        for directory in sorted(RESTORE_DIRECTORIES):
            root = self.root / directory
            if not root.exists():
                continue
            for path in sorted(root.rglob("*")):
                if self._should_include_backup_file(path):
                    yield path

    def _should_include_backup_file(self, path: Path) -> bool:
        if not path.is_file() or path.is_symlink():
            return False
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            return False
        if not relative.parts:
            return False
        name = path.name
        lowered = name.lower()
        if lowered in BACKUP_DENY_FILENAMES:
            return False
        if any(name.startswith(prefix) for prefix in BACKUP_DENY_NAME_PREFIXES):
            return False
        top = relative.parts[0]
        if top in RESTORE_ROOT_FILES:
            return len(relative.parts) == 1
        return top in RESTORE_DIRECTORIES

    def delete_backups(self) -> dict[str, Any]:
        self.ensure()
        with self._lock:
            return self._delete_backups_locked()

    def _delete_backups_locked(self) -> dict[str, Any]:
        deleted = 0
        bytes_deleted = 0
        for path in sorted(self.backups_dir.glob("*")):
            if not path.is_file():
                continue
            bytes_deleted += path.stat().st_size
            path.unlink()
            deleted += 1
        return {"deleted": deleted, "bytes_deleted": bytes_deleted}

    def prune_backups(self, *, keep_latest: int = 20, max_age_days: int = 0) -> dict[str, Any]:
        self.ensure()
        with self._lock:
            return self._prune_backups_locked(keep_latest=keep_latest, max_age_days=max_age_days)

    def _prune_backups_locked(self, *, keep_latest: int = 20, max_age_days: int = 0) -> dict[str, Any]:
        keep_latest = max(0, int(keep_latest))
        max_age_days = max(0, int(max_age_days))
        backups = sorted(
            (path for path in self.backups_dir.glob("*.zip") if path.is_file()),
            key=lambda path: (path.stat().st_mtime, path.name),
            reverse=True,
        )
        now = datetime.now(timezone.utc)
        deleted = 0
        bytes_deleted = 0
        deleted_files: list[dict[str, Any]] = []
        for index, path in enumerate(backups):
            reasons: list[str] = []
            if keep_latest and index >= keep_latest:
                reasons.append("over_retention_limit")
            if max_age_days:
                modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                if now - modified > timedelta(days=max_age_days):
                    reasons.append("expired")
            if not reasons:
                continue
            size = path.stat().st_size
            path.unlink()
            deleted += 1
            bytes_deleted += size
            deleted_files.append({"name": path.name, "bytes": size, "reasons": reasons})
        return {
            "deleted": deleted,
            "bytes_deleted": bytes_deleted,
            "deleted_files": deleted_files,
            "retention": {"keep_latest": keep_latest, "max_age_days": max_age_days},
        }

    def delete_user_records(self, user_id: str, *, include_backups: bool = False) -> dict[str, Any]:
        with self._lock:
            return self._delete_user_records_locked(user_id, include_backups=include_backups)

    def _delete_user_records_locked(self, user_id: str, *, include_backups: bool = False) -> dict[str, Any]:
        self.ensure()
        counts: dict[str, int] = {
            "imports": 0,
            "source_accounts": 0,
            "sync_cursors": 0,
            "sync_devices": 0,
            "sync_receipts": 0,
            "captures": 0,
            "memories": 0,
            "tasks": 0,
            "entities": 0,
            "graph_edges": 0,
            "deletion_tombstones": 0,
            "events": 0,
            "settings": 0,
            "credentials": 0,
            "attachments": 0,
            "backups": 0,
        }
        for record_dir in ("imports", "source_accounts", "sync_cursors", "sync_devices", "sync_receipts", "captures", "memories", "tasks", "entities", "graph_edges"):
            base = self.root / record_dir
            if not base.exists():
                continue
            for path in sorted(base.rglob("*.json")):
                payload = self._read_json(path, {})
                if payload.get("user_id") != user_id:
                    continue
                path.unlink()
                self._prune_empty_parents(path.parent, base)
                counts[record_dir] += 1
                if record_dir == "memories":
                    self._delete_memory_markdown(str(payload.get("id") or ""))

        # Sweep any of this user's Markdown memory notes that have no JSON pair (e.g. a
        # Markdown-native/edited memory), so "delete my data" leaves no readable memory behind.
        memories_base = self.root / "memories"
        if memories_base.exists():
            for path in sorted(memories_base.rglob("*.md")):
                try:
                    record = parse_memory_markdown(path.read_text(encoding="utf-8"))
                except Exception:
                    record = {}
                if record.get("user_id") == user_id:
                    try:
                        path.unlink()
                        self._prune_empty_parents(path.parent, memories_base)
                    except OSError:
                        pass

        # Only delete THIS user's attachments. Every other record type above filters by
        # user_id; attachments must be scoped the same way (attachments/<user_id>/...) so a
        # deletion in a shared vault (local/bucket mode serves many user_ids from one vault)
        # can never destroy another tenant's attachments. Guard against path traversal in
        # user_id so it can only ever touch a direct subdirectory of attachments/.
        attachments_dir = self.root / "attachments"
        user_attachments = attachments_dir / user_id
        try:
            scoped_within_attachments = user_attachments.resolve().parent == attachments_dir.resolve()
        except (OSError, ValueError):
            scoped_within_attachments = False
        if scoped_within_attachments and user_attachments.is_dir():
            for path in sorted(user_attachments.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                    counts["attachments"] += 1
                elif path.is_dir():
                    try:
                        path.rmdir()
                    except OSError:
                        pass
            try:
                user_attachments.rmdir()
            except OSError:
                pass

        # Serialize the shared-file mutations against concurrent write_settings /
        # write_source_credential / append_event so a GDPR delete can't lose another surviving
        # tenant's just-written settings, credential, or event line.
        with self._lock:
            settings = self._read_json(self.settings_path, {"users": {}})
            users = settings.get("users")
            if isinstance(users, dict) and user_id in users:
                users.pop(user_id, None)
                settings["updated_at"] = vault_now()
                self._write_json(self.settings_path, settings)
                counts["settings"] = 1

            credentials = self._read_json(self.credentials_path, {})
            credential_users = credentials.get("users")
            if isinstance(credential_users, dict) and user_id in credential_users:
                removed = credential_users.pop(user_id, {})
                if isinstance(removed, dict):
                    counts["credentials"] = len(removed)
                credentials["vault_updated_at"] = vault_now()
                self._write_json(self.credentials_path, credentials)
                self._chmod_credentials_file()

            if self.events_path.exists():
                retained: list[str] = []
                with self.events_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError:
                            retained.append(line)
                            continue
                        if payload.get("user_id") == user_id:
                            counts["events"] += 1
                        else:
                            retained.append(line)
                tmp_path = self.events_path.with_name(
                    f".{self.events_path.name}.{os.getpid()}.{threading.get_ident()}.{os.urandom(4).hex()}.tmp"
                )
                try:
                    with tmp_path.open("w", encoding="utf-8") as handle:
                        handle.writelines(retained)
                    os.replace(tmp_path, self.events_path)
                except OSError:
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                    raise

        if include_backups:
            tombstones_deleted, _ = self._delete_matching_records(
                "deletion_tombstones",
                lambda payload: payload.get("user_id") == user_id,
            )
            counts["deletion_tombstones"] = tombstones_deleted
            backup_result = self.delete_backups()
            counts["backups"] = int(backup_result["deleted"])
            counts["backup_bytes_deleted"] = int(backup_result["bytes_deleted"])
        return counts

    def restore_from_zip_backup(self, backup_path: Path) -> dict[str, Any]:
        with self._lock:
            return self._restore_from_zip_backup_locked(backup_path)

    def _restore_from_zip_backup_locked(self, backup_path: Path) -> dict[str, Any]:
        self.ensure()
        backup_path = Path(backup_path).expanduser().resolve()
        backups_dir = self.backups_dir.resolve()
        if backup_path.parent != backups_dir:
            raise ValueError(f"backup path must be inside the {APP_BRAND} backups directory")
        if not backup_path.is_file() or backup_path.suffix != ".zip":
            raise FileNotFoundError(f"backup not found: {backup_path}")

        with zipfile.ZipFile(backup_path) as archive:
            members = [info for info in archive.infolist() if not info.is_dir()]
            for info in members:
                self._validate_restore_member(info.filename)
            with tempfile.TemporaryDirectory(prefix="cortex-restore-") as tmp:
                tmp_root = Path(tmp)
                for info in members:
                    path = PurePosixPath(info.filename)
                    if path.name == "index.sqlite":
                        continue
                    target = tmp_root.joinpath(*path.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, target.open("wb") as destination:
                        shutil.copyfileobj(source, destination)

                # Rollback-safe swap (never leave the user's vault half-restored). The old code
                # rmtree'd each LIVE directory and THEN copytree'd the restored copy in — a mid-copy
                # failure (disk full, permission, corrupt member) destroyed the live data with no
                # recovery. Instead: (1) stage every restored dir INSIDE the vault (same filesystem,
                # so the swaps below are atomic renames that can't fail cross-device); (2) only once
                # ALL stages succeed, rename live->`.old` then staged->live; (3) delete `.old` at the
                # very end. Any failure before the swaps leaves the live vault untouched; a failure
                # during the swaps rolls the completed ones back.
                staged: dict[str, Path] = {}
                swapped_old: dict[str, Path] = {}
                try:
                    for directory in RESTORE_DIRECTORIES:
                        source = tmp_root / directory
                        staging = self.root / f".restore-new-{directory}"
                        shutil.rmtree(staging, ignore_errors=True)
                        if source.exists():
                            shutil.copytree(source, staging)
                        else:
                            staging.mkdir(parents=True, exist_ok=True)
                        staged[directory] = staging
                    for directory, staging in staged.items():
                        target = self.root / directory
                        if target.exists():
                            old = self.root / f".restore-old-{directory}"
                            shutil.rmtree(old, ignore_errors=True)
                            os.replace(target, old)
                            swapped_old[directory] = old
                        os.replace(staging, target)
                    for old in swapped_old.values():
                        shutil.rmtree(old, ignore_errors=True)
                except Exception:
                    # Roll back any completed swaps so the live vault is not left partially restored.
                    for directory, old in swapped_old.items():
                        target = self.root / directory
                        try:
                            shutil.rmtree(target, ignore_errors=True)
                            os.replace(old, target)
                        except OSError:
                            pass
                    for staging in staged.values():
                        shutil.rmtree(staging, ignore_errors=True)
                    raise

                for file_name in RESTORE_ROOT_FILES:
                    source = tmp_root / file_name
                    target = self.root / file_name
                    if source.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(source, target)
                    elif file_name == "events.jsonl":
                        target.touch()

        self.ensure()
        return {
            "backup_path": str(backup_path),
            "restored_at": vault_now(),
            "restored_directories": sorted(RESTORE_DIRECTORIES),
        }

    def _validate_restore_member(self, name: str) -> None:
        path = PurePosixPath(name)
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError(f"unsafe backup member path: {name}")
        if "\\" in name:
            raise ValueError(f"unsafe backup member path: {name}")
        if path.name.lower() in BACKUP_DENY_FILENAMES or any(path.name.startswith(prefix) for prefix in BACKUP_DENY_NAME_PREFIXES):
            raise ValueError(f"unsupported backup member path: {name}")
        top = path.parts[0]
        if top in RESTORE_ROOT_FILES:
            if len(path.parts) != 1:
                raise ValueError(f"unsafe backup member path: {name}")
            return
        if top in RESTORE_DIRECTORIES:
            return
        if len(path.parts) == 1 and path.name == "index.sqlite":
            return
        raise ValueError(f"unsupported backup member path: {name}")

    def diagnostics(self) -> dict[str, Any]:
        self.ensure()
        missing_dirs = [directory for directory in VAULT_DIRECTORIES if not (self.root / directory).is_dir()]
        record_counts = {
            "imports": self._count_json_records("imports"),
            "captures": self._count_json_records("captures"),
            "memories": self._count_json_records("memories"),
            "tasks": self._count_json_records("tasks"),
            "entities": self._count_json_records("entities"),
            "graph_edges": self._count_json_records("graph_edges"),
            "deletion_tombstones": self._count_json_records("deletion_tombstones"),
            "attachments": len([path for path in (self.root / "attachments").rglob("*") if path.is_file()]),
            "backups": len([path for path in self.backups_dir.glob("*") if path.is_file()]),
        }
        event_count = 0
        if self.events_path.exists():
            with self.events_path.open("r", encoding="utf-8") as handle:
                event_count = sum(1 for line in handle if line.strip())
        manifest = self._read_json(self.manifest_path, {})
        return {
            "format": manifest.get("format", VAULT_FORMAT),
            "version": int(manifest.get("version", VAULT_VERSION)),
            "path": str(self.root),
            "index_path": str(self.index_path),
            "manifest_path": str(self.manifest_path),
            "settings_path": str(self.settings_path),
            "events_path": str(self.events_path),
            "missing_dirs": missing_dirs,
            "record_counts": record_counts,
            "event_count": event_count,
            "index_exists": self.index_path.exists(),
            "index_size_bytes": self.index_path.stat().st_size if self.index_path.exists() else 0,
        }

    def _write_record(self, path: Path, record_type: str, record: dict[str, Any]) -> Path:
        # Underscore-prefixed keys are internal, in-memory render inputs (e.g. a memory's
        # _link_names / _backlinks used to build the note's [[wikilinks]]); they must never enter
        # durable JSON system representation. The Markdown memory representation still sees the
        # full record because _write_memory_markdown runs on the original dict.
        payload = {
            "vault_record_type": record_type,
            "vault_record_version": VAULT_VERSION,
            "vault_updated_at": vault_now(),
            **{key: value for key, value in record.items() if not key.startswith("_")},
        }
        return self._write_json(path, payload)

    def _patch_first(self, record_dir: str, record_id: str, updates: dict[str, Any]) -> bool:
        with self._lock:
            return self._patch_first_locked(record_dir, record_id, updates)

    def _patch_first_locked(self, record_dir: str, record_id: str, updates: dict[str, Any]) -> bool:
        self.ensure()
        base = self.root / record_dir
        for path in base.rglob(f"{safe_segment(record_id)}.json"):
            payload = self._read_json(path, {})
            if payload.get("id") != record_id:
                continue
            payload.update(updates)
            payload["vault_updated_at"] = vault_now()
            self._write_json(path, payload)
            return True
        for path in base.rglob("*.json"):
            payload = self._read_json(path, {})
            if payload.get("id") == record_id:
                payload.update(updates)
                payload["vault_updated_at"] = vault_now()
                self._write_json(path, payload)
                return True
        return False

    def _delete_first(self, record_dir: str, record_id: str) -> bool:
        with self._lock:
            return self._delete_first_locked(record_dir, record_id)

    def _delete_first_locked(self, record_dir: str, record_id: str) -> bool:
        self.ensure()
        base = self.root / record_dir
        for path in base.rglob(f"{safe_segment(record_id)}.json"):
            payload = self._read_json(path, {})
            if payload.get("id") != record_id:
                continue
            path.unlink()
            self._prune_empty_parents(path.parent, base)
            return True
        for path in base.rglob("*.json"):
            payload = self._read_json(path, {})
            if payload.get("id") != record_id:
                continue
            path.unlink()
            self._prune_empty_parents(path.parent, base)
            return True
        return False

    def _delete_matching_records(self, record_dir: str, predicate) -> tuple[int, list[str]]:
        with self._lock:
            return self._delete_matching_records_locked(record_dir, predicate)

    def _delete_matching_records_locked(self, record_dir: str, predicate) -> tuple[int, list[str]]:
        self.ensure()
        base = self.root / record_dir
        if not base.exists():
            return 0, []
        deleted = 0
        deleted_ids: list[str] = []
        for path in sorted(base.rglob("*.json")):
            payload = self._read_json(path, {})
            if not isinstance(payload, dict) or not predicate(payload):
                continue
            record_id = payload.get("id")
            if isinstance(record_id, str) and record_id:
                deleted_ids.append(record_id)
            path.unlink()
            self._prune_empty_parents(path.parent, base)
            deleted += 1
        # Keep the human-readable Markdown notes in lockstep with deletions so a tombstoned or
        # purged memory can never resurrect on the next Markdown-sourced rebuild.
        if record_dir == "memories":
            for record_id in deleted_ids:
                self._delete_memory_markdown(record_id)
        return deleted, deleted_ids

    def _delete_memory_markdown(self, memory_id: str) -> bool:
        if not memory_id:
            return False
        base = self.root / "memories"
        if not base.exists():
            return False
        removed = False
        # Notes are <slug>--<shortid>.md; match by the id-derived shortid (plus legacy <id>.md) and
        # CONFIRM the parsed frontmatter id before unlinking, so a shortid collision can never delete
        # a different memory's note.
        for path in list(self._iter_memory_notes_for_id(memory_id)):
            try:
                parsed_id = parse_memory_markdown(path.read_text(encoding="utf-8")).get("id")
            except Exception:
                parsed_id = None  # unreadable note for our shortid -> treat as ours, delete it
            if parsed_id and parsed_id != memory_id:
                continue  # a DIFFERENT memory's note (collision) -> never delete it
            try:
                path.unlink()
                self._prune_empty_parents(path.parent, base)
                removed = True
            except OSError:
                pass
        return removed

    def _delete_graph_edges_for_ids(self, user_id: str, object_ids: list[str]) -> int:
        ids = {value for value in object_ids if value}
        if not ids:
            return 0
        deleted, _ = self._delete_matching_records(
            "graph_edges",
            lambda payload: payload.get("user_id") == user_id
            and (
                payload.get("id") in ids
                or payload.get("source_id") in ids
                or payload.get("target_id") in ids
                or payload.get("evidence_id") in ids
            ),
        )
        return deleted

    def _prune_empty_parents(self, path: Path, stop_at: Path) -> None:
        stop_at = stop_at.resolve()
        current = path
        while current.exists() and current.resolve() != stop_at:
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent

    def _count_json_records(self, directory: str) -> int:
        base = self.root / directory
        return len(list(base.rglob("*.json"))) if base.exists() else 0

    def _write_json(self, path: Path, payload: dict[str, Any]) -> Path:
        # Every durable JSON mutation participates in the same process/file
        # lock used by backups, deletion and restore. This is intentionally at
        # the primitive boundary so new record types cannot bypass the freeze.
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Temp name must be unique per writer: the shipping server is multi-threaded (one PID),
            # so a pid-only temp path lets two concurrent writes to the same target collide — one
            # thread's os.replace moves the shared temp out from under the other, raising
            # FileNotFoundError or installing a half-written file. Thread id + randomness makes each
            # write private; the temp is unlinked on failure so a crash mid-write leaves no litter.
            tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.{os.urandom(4).hex()}.tmp")
            try:
                with tmp_path.open("w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                    handle.write("\n")
                os.replace(tmp_path, path)
            except OSError:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
                raise
            return path

    def _read_json(self, path: Path, default: Any) -> Any:
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def _relative_or_absolute(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)
