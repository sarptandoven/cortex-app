from __future__ import annotations

import json
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


VAULT_FORMAT = "cortex-local-vault"
VAULT_VERSION = 1
VAULT_DIRECTORIES = (
    "captures",
    "memories",
    "tasks",
    "entities",
    "graph_edges",
    "attachments",
    "backups",
    "exports",
)


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

    def patch_capture(self, capture_id: str, updates: dict[str, Any]) -> bool:
        return self._patch_first("captures", capture_id, updates)

    def patch_memory(self, memory_id: str, updates: dict[str, Any]) -> bool:
        return self._patch_first("memories", memory_id, updates)

    def patch_task(self, task_id: str, updates: dict[str, Any]) -> bool:
        return self._patch_first("tasks", task_id, updates)

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

    def diagnostics(self) -> dict[str, Any]:
        self.ensure()
        missing_dirs = [directory for directory in VAULT_DIRECTORIES if not (self.root / directory).is_dir()]
        record_counts = {
            "captures": self._count_json_records("captures"),
            "memories": self._count_json_records("memories"),
            "tasks": self._count_json_records("tasks"),
            "entities": self._count_json_records("entities"),
            "graph_edges": self._count_json_records("graph_edges"),
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
