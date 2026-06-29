from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


VAULT_FORMAT = "cortex-local-vault"
VAULT_VERSION = 1
VAULT_DIRECTORIES = (
    "imports",
    "captures",
    "memories",
    "tasks",
    "entities",
    "graph_edges",
    "deletion_tombstones",
    "attachments",
    "backups",
    "exports",
)
RESTORE_ROOT_FILES = {"manifest.json", "settings.json", "events.jsonl"}
RESTORE_DIRECTORIES = {"imports", "captures", "memories", "tasks", "entities", "graph_edges", "deletion_tombstones", "attachments"}


def vault_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_segment(value: str | None, fallback: str = "unknown") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value or "").strip("-._")
    return cleaned[:80] or fallback


class CortexVault:
    """User-owned local vault with JSON records and a rebuildable SQLite index."""

    def __init__(self, root: Path, index_path: Path):
        self.root = Path(root).expanduser()
        self.index_path = Path(index_path).expanduser()

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
    def backups_dir(self) -> Path:
        return self.root / "backups"

    @property
    def tombstones_dir(self) -> Path:
        return self.root / "deletion_tombstones"

    def ensure(self) -> None:
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
                    "name": "Cortex Vault",
                    "format": VAULT_FORMAT,
                    "version": VAULT_VERSION,
                    "created_at": vault_now(),
                    "updated_at": vault_now(),
                    "source_of_truth": "json_records_and_events",
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
                    "source_of_truth": "json_records_and_events",
                    "index_role": "rebuildable_local_search_index",
                    "index_path": self._relative_or_absolute(self.index_path),
                    "directories": list(VAULT_DIRECTORIES),
                }
            )
            self._write_json(self.manifest_path, manifest)

    def write_settings(self, user_id: str, settings: dict[str, Any]) -> Path:
        self.ensure()
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

    def write_capture(self, record: dict[str, Any]) -> Path:
        day = safe_segment(str(record.get("captured_at", ""))[:10], "undated")
        path = self.root / "captures" / day / f"{safe_segment(record.get('id'), 'capture')}.json"
        return self._write_record(path, "capture", record)

    def write_import(self, record: dict[str, Any]) -> Path:
        day = safe_segment(str(record.get("created_at", ""))[:10], "undated")
        path = self.root / "imports" / day / f"{safe_segment(record.get('id'), 'import')}.json"
        return self._write_record(path, "import", record)

    def write_memory(self, record: dict[str, Any]) -> Path:
        kind = safe_segment(record.get("kind"), "memory")
        path = self.root / "memories" / kind / f"{safe_segment(record.get('id'), 'memory')}.json"
        return self._write_record(path, "memory", record)

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

    def patch_memory(self, memory_id: str, updates: dict[str, Any]) -> bool:
        return self._patch_first("memories", memory_id, updates)

    def patch_task(self, task_id: str, updates: dict[str, Any]) -> bool:
        return self._patch_first("tasks", task_id, updates)

    def delete_capture(self, capture_id: str) -> bool:
        return self._delete_first("captures", capture_id)

    def delete_import(self, import_id: str) -> bool:
        return self._delete_first("imports", import_id)

    def delete_memory(self, memory_id: str) -> bool:
        return self._delete_first("memories", memory_id)

    def delete_task(self, task_id: str) -> bool:
        return self._delete_first("tasks", task_id)

    def delete_edge(self, edge_id: str) -> bool:
        return self._delete_first("graph_edges", edge_id)

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
        backup_path = self.backups_dir / f"cortex-vault-{timestamp}.zip"
        with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(self.root.rglob("*")):
                if path.is_dir():
                    continue
                relative = path.relative_to(self.root)
                if relative.parts and relative.parts[0] == "backups":
                    continue
                if path == self.index_path or path.name in {self.index_path.name + "-wal", self.index_path.name + "-shm"}:
                    continue
                archive.write(path, relative.as_posix())
            if sqlite_backup_path.exists():
                archive.write(sqlite_backup_path, "index.sqlite")
        return backup_path

    def delete_backups(self) -> dict[str, Any]:
        self.ensure()
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
        self.ensure()
        counts: dict[str, int] = {
            "captures": 0,
            "memories": 0,
            "tasks": 0,
            "entities": 0,
            "graph_edges": 0,
            "deletion_tombstones": 0,
            "events": 0,
            "settings": 0,
            "attachments": 0,
            "backups": 0,
        }
        for record_dir in ("imports", "captures", "memories", "tasks", "entities", "graph_edges"):
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

        attachments_dir = self.root / "attachments"
        if attachments_dir.exists():
            for path in sorted(attachments_dir.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                    counts["attachments"] += 1
                elif path.is_dir():
                    try:
                        path.rmdir()
                    except OSError:
                        pass

        settings = self._read_json(self.settings_path, {"users": {}})
        users = settings.get("users")
        if isinstance(users, dict) and user_id in users:
            users.pop(user_id, None)
            settings["updated_at"] = vault_now()
            self._write_json(self.settings_path, settings)
            counts["settings"] = 1

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
            tmp_path = self.events_path.with_name(f".{self.events_path.name}.{os.getpid()}.tmp")
            with tmp_path.open("w", encoding="utf-8") as handle:
                handle.writelines(retained)
            os.replace(tmp_path, self.events_path)

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
        self.ensure()
        backup_path = Path(backup_path).expanduser().resolve()
        backups_dir = self.backups_dir.resolve()
        if backup_path.parent != backups_dir:
            raise ValueError("backup path must be inside the Cortex backups directory")
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

                for directory in RESTORE_DIRECTORIES:
                    target = self.root / directory
                    shutil.rmtree(target, ignore_errors=True)
                    source = tmp_root / directory
                    if source.exists():
                        shutil.copytree(source, target)
                    else:
                        target.mkdir(parents=True, exist_ok=True)

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
        payload = {
            "vault_record_type": record_type,
            "vault_record_version": VAULT_VERSION,
            "vault_updated_at": vault_now(),
            **record,
        }
        return self._write_json(path, payload)

    def _patch_first(self, record_dir: str, record_id: str, updates: dict[str, Any]) -> bool:
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
        return deleted, deleted_ids

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
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_path, path)
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
