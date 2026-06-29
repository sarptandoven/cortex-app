from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib import error, request

from backend.app.config import Settings

MODULE_TMP = tempfile.TemporaryDirectory()
os.environ["CORTEX_DB_PATH"] = str(Path(MODULE_TMP.name) / "bootstrap.sqlite")
os.environ["CORTEX_VAULT_PATH"] = str(Path(MODULE_TMP.name) / "bootstrap.vault")
os.environ["CORTEX_API_KEY"] = "test-token"

from backend.app import standalone_server


class FakeStore:
    def __init__(self) -> None:
        self.search_calls: list[tuple[str, str, int, str | None, str | None]] = []
        self.answer_calls: list[tuple[str, str, int]] = []
        self.delete_capture_calls: list[tuple[str, str]] = []
        self.delete_backups_calls: list[str] = []
        self.delete_user_data_calls: list[tuple[str, bool]] = []
        self.restore_latest_backup_calls: list[str] = []
        self.agent_events: list[dict] = []
        self.import_analysis_calls: list[tuple[list[str], str, int]] = []
        self.import_sources_calls: list[tuple[str, list[str], str, str, int]] = []
        self.list_imports_calls: list[tuple[str, int, bool]] = []
        self.get_import_calls: list[tuple[str, str]] = []
        self.delete_import_calls: list[tuple[str, str]] = []
        self.revoke_token_calls: list[tuple[str, str]] = []
        self.source_account_calls: list[tuple[str, str]] = []
        self.sync_cursor_calls: list[tuple[str, str, str | None]] = []
        self.sync_device_calls: list[tuple[str, str]] = []
        self.sync_receipt_calls: list[tuple[str, str, str, str]] = []
        self.sync_feed_calls: list[tuple[str, str, int, str, str, dict | None]] = []
        self.source_account_disconnected = False
        self.sync_device_revoked = False

    def search(self, user_id: str, query: str, limit: int, kind: str | None = None, layer: str | None = None) -> list[dict]:
        self.search_calls.append((user_id, query, limit, kind, layer))
        return [
            {
                "id": "memory-1",
                "kind": kind or "claim",
                "layer": layer or "semantic",
                "content": "Layer-aware result",
                "source": "unit-test",
            }
        ]

    def answer_query(self, user_id: str, query: str, limit: int) -> dict:
        self.answer_calls.append((user_id, query, limit))
        result = {
            "id": "memory-1",
            "kind": "claim",
            "layer": "semantic",
            "content": "Layer-aware result",
            "source": "unit-test",
            "source_url": "/tmp/source.md",
        }
        return {
            "query": query,
            "answer": "Cortex found 1 cited memory for this question:\n[1] Layer-aware result (/tmp/source.md)",
            "citations": [
                {
                    "index": 1,
                    "id": "memory-1",
                    "kind": "claim",
                    "layer": "semantic",
                    "source": "unit-test",
                    "source_url": "/tmp/source.md",
                    "excerpt": "Layer-aware result",
                    "topics": [],
                }
            ],
            "results": [result],
        }

    def delete_capture(self, user_id: str, capture_id: str) -> bool:
        self.delete_capture_calls.append((user_id, capture_id))
        return capture_id == "capture-1"

    def delete_backups(self, user_id: str) -> dict:
        self.delete_backups_calls.append(user_id)
        return {"deleted_at": "2026-01-01T00:00:00Z", "deleted": 2, "bytes_deleted": 128}

    def restore_latest_backup(self, user_id: str) -> dict:
        self.restore_latest_backup_calls.append(user_id)
        return {"restored_at": "2026-01-01T00:00:00Z", "backup_path": "/tmp/backup.zip", "rebuild": {"captures": 1, "memories": 2}}

    def diagnostics(self, user_id: str) -> dict:
        return {
            "status": "ok",
            "quick_check": "ok",
            "schema_version": 1,
            "db_path": "/tmp/index.sqlite",
            "db_size_bytes": 0,
            "wal_size_bytes": 0,
            "counts": {},
            "fts_orphans": 0,
            "inactive_fts_rows": 0,
            "relation_orphans": 0,
            "last_event_at": None,
            "vector": {"available": False},
            "embedding": {
                "provider": "hash",
                "model": "cortex-hash-v1",
                "dimensions": 384,
                "schema_dimensions": 384,
                "index_compatible": True,
                "network_required": False,
                "strict": False,
            },
            "vault": {"record_counts": {}},
        }

    def memory_quality_report(self, user_id: str) -> dict:
        return {
            "generated_at": "2026-01-01T00:00:00Z",
            "score": 72,
            "status": "usable",
            "citation_coverage": 0.75,
            "date_coverage": 0.5,
            "review_coverage": 0.5,
            "layer_coverage": 0.5,
            "layers_present": ["semantic", "decision", "preference"],
            "totals": {"captures": 2, "pending_captures": 1, "approved_captures": 1, "archived_captures": 0, "active_memories": 4, "cited_memories": 3, "uncited_memories": 1, "dated_memories": 2, "temporal_memories": 2, "dated_temporal_memories": 1, "undated_temporal_memories": 1},
            "source_health": [
                {
                    "source": "unit-test",
                    "captures": 2,
                    "pending": 1,
                    "approved": 1,
                    "archived": 0,
                    "active_memories": 4,
                    "cited_memories": 3,
                    "uncited_memories": 1,
                    "dated_memories": 2,
                    "temporal_memories": 2,
                    "dated_temporal_memories": 1,
                    "undated_temporal_memories": 1,
                    "citation_coverage": 0.75,
                    "date_coverage": 0.5,
                    "last_seen": "2026-01-01T00:00:00Z",
                    "status": "needs_attention",
                    "warnings": ["missing citations"],
                }
            ],
            "warnings": ["Some active memories are missing source citations."],
            "recommendations": ["Prefer source imports and URL/file captures so retrieved memory has citations."],
        }

    def sync_change_feed(
        self,
        user_id: str,
        *,
        after: str = "",
        limit: int = 100,
        device_id: str = "",
        signing_key: str = "",
        shard: dict | None = None,
    ) -> dict:
        self.sync_feed_calls.append((user_id, after, limit, device_id, signing_key, shard))
        device = self.list_sync_devices(user_id, include_revoked=True)[0] if device_id else None
        warnings = ["device_revoked"] if device and device.get("revoked_at") else []
        signature = None
        if device:
            signature = {
                "algorithm": "hmac-sha256",
                "configured": bool(signing_key and not warnings),
                "device_id": device["id"],
                "payload_hash": "sha256:test",
                "value": "hmac-sha256:test",
            }
        if after == "evt_missing":
            return {
                "generated_at": "2026-01-01T00:00:00Z",
                "sync_contract": 1,
                "content_included": False,
                "cursor": after,
                "next_cursor": after,
                "has_more": False,
                "high_watermark": {"event_id": after, "created_at": None},
                "shard": shard,
                "counts": {"captures": 0, "memories": 0, "tasks": 0, "entities": 0, "imports": 0, "source_accounts": 0, "sync_cursors": 0, "sync_devices": 1 if device else 0, "events": 0},
                "device": device,
                "changes": [],
                "warnings": ["cursor_not_found"] + warnings,
                "signature": signature,
            }
        return {
            "generated_at": "2026-01-01T00:00:00Z",
            "sync_contract": 1,
            "content_included": False,
            "cursor": after,
            "next_cursor": "evt_test",
            "has_more": False,
            "high_watermark": {"event_id": "evt_test", "created_at": "2026-01-01T00:00:00Z"},
            "shard": shard,
            "counts": {"captures": 1, "memories": 1, "tasks": 0, "entities": 0, "imports": 0, "source_accounts": 0, "sync_cursors": 0, "sync_devices": 1 if device else 0, "events": 1},
            "device": device,
            "changes": [
                {
                    "id": "evt_test",
                    "created_at": "2026-01-01T00:00:00Z",
                    "object_type": "capture",
                    "event_type": "created",
                    "object_id": "cap_test",
                    "object_id_hash": "hash",
                    "object_id_redacted": False,
                    "metadata_keys": ["content_chars"],
                    "safe_metadata": {"content_chars": 42},
                }
            ],
            "warnings": warnings,
            "signature": signature,
        }

    def supported_import_sources(self) -> list[dict]:
        return [{"id": "chatgpt", "name": "ChatGPT", "formats": ["conversations.json"], "status": "native"}]

    def source_connector_catalog(self) -> list[dict]:
        return [
            {
                "id": "gmail",
                "name": "Gmail",
                "category": "communication",
                "live_status": "planned",
                "import_status": "generic",
                "formats": [],
            }
        ]

    def source_readiness_report(self, user_id: str) -> dict:
        return {
            "generated_at": "2026-01-01T00:00:00Z",
            "summary": {
                "sources_total": 1,
                "import_ready": 1,
                "planned_live": 1,
                "connected": 1 if not self.source_account_disconnected else 0,
                "synced": 1 if not self.source_account_disconnected else 0,
                "sources_with_data": 0,
                "needs_review": 0,
                "needs_attention": 1 if self.source_account_disconnected else 0,
                "active_memories": 0,
            },
            "sources": [
                {
                    "source": "gmail",
                    "name": "Gmail",
                    "category": "communication",
                    "status": "needs_attention" if self.source_account_disconnected else "synced",
                    "next_action": "Reconnect Gmail." if self.source_account_disconnected else "Source sync has completed; review new memories as they arrive.",
                    "import_status": "generic",
                    "live_status": "planned",
                    "auth": "oauth",
                    "formats": [],
                    "accounts": 1 if not self.source_account_disconnected else 0,
                    "cursors": 1,
                    "captures": 0,
                    "pending": 0,
                    "approved": 0,
                    "archived": 0,
                    "active_memories": 0,
                    "citation_coverage": 0,
                    "last_seen_at": "2026-01-01T00:00:00Z",
                    "warnings": ["Reconnect Gmail."] if self.source_account_disconnected else [],
                }
            ],
            "recommendations": ["Source readiness is healthy for local beta use."],
        }

    def list_source_accounts(self, user_id: str, *, include_disconnected: bool = False) -> list[dict]:
        if self.source_account_disconnected and not include_disconnected:
            return []
        status = "disconnected" if self.source_account_disconnected else "connected"
        return [
            {
                "id": "sacct_test",
                "user_id": user_id,
                "source": "gmail",
                "account_label": "Standalone Gmail",
                "account_identifier": "standalone@example.com",
                "connection_type": "oauth",
                "status": status,
                "auth_state": "healthy" if not self.source_account_disconnected else "revoked",
                "policy": {"sync": "incremental"},
                "metadata": {},
                "last_sync_at": "2026-01-01T00:00:00Z",
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "disconnected_at": "2026-01-01T00:00:01Z" if self.source_account_disconnected else None,
            }
        ]

    def upsert_source_account(
        self,
        user_id: str,
        *,
        source: str,
        account_label: str = "",
        account_identifier: str | None = None,
        connection_type: str = "manual",
        status: str = "available",
        auth_state: str = "not_configured",
        policy: dict | None = None,
        metadata: dict | None = None,
        last_error: str | None = None,
    ) -> dict:
        if not source:
            raise ValueError("source is required")
        self.source_account_calls.append((user_id, source))
        self.source_account_disconnected = False
        return self.list_source_accounts(user_id)[0] | {
            "source": source.lower(),
            "account_label": account_label or "Standalone Gmail",
            "account_identifier": account_identifier,
            "connection_type": connection_type,
            "status": status,
            "auth_state": auth_state,
            "policy": policy or {},
            "metadata": metadata or {},
            "last_error": last_error,
        }

    def disconnect_source_account(self, user_id: str, account_id: str) -> dict | None:
        if account_id != "sacct_test":
            return None
        self.source_account_disconnected = True
        return self.list_source_accounts(user_id, include_disconnected=True)[0]

    def list_sync_cursors(self, user_id: str, *, source_account_id: str | None = None) -> list[dict]:
        return [
            {
                "id": "sync_test",
                "user_id": user_id,
                "source_account_id": source_account_id,
                "source": "gmail",
                "cursor_name": "messages",
                "cursor_value": "cursor-1",
                "high_water_mark": "2026-01-01T00:00:00Z",
                "state": {"batch": 1},
                "last_started_at": "2026-01-01T00:00:00Z",
                "last_completed_at": "2026-01-01T00:00:00Z",
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ]

    def upsert_sync_cursor(
        self,
        user_id: str,
        *,
        source: str,
        cursor_name: str,
        cursor_value: str | None = None,
        high_water_mark: str | None = None,
        state: dict | None = None,
        source_account_id: str | None = None,
        last_error: str | None = None,
        completed: bool = True,
    ) -> dict:
        if not source or not cursor_name:
            raise ValueError("source and cursor_name are required")
        if source_account_id == "sacct_missing":
            raise ValueError("source account not found")
        self.sync_cursor_calls.append((user_id, cursor_name, source_account_id))
        return self.list_sync_cursors(user_id, source_account_id=source_account_id)[0] | {
            "source": source.lower(),
            "cursor_name": cursor_name,
            "cursor_value": cursor_value,
            "high_water_mark": high_water_mark,
            "state": state or {},
            "last_error": last_error,
            "last_completed_at": "2026-01-01T00:00:00Z" if completed and not last_error else None,
        }

    def list_sync_devices(self, user_id: str, *, include_revoked: bool = False) -> list[dict]:
        if self.sync_device_revoked and not include_revoked:
            return []
        return [
            {
                "id": "sdev_test",
                "user_id": user_id,
                "device_name": "Standalone Mac",
                "platform": "macos",
                "fingerprint": "abcd1234abcd1234",
                "public_key": None,
                "capabilities": ["manifest"],
                "first_cursor": None,
                "last_cursor": None,
                "last_seen_at": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "revoked_at": "2026-01-01T00:00:01Z" if self.sync_device_revoked else None,
            }
        ]

    def register_sync_device(
        self,
        user_id: str,
        *,
        device_name: str,
        platform: str = "unknown",
        device_key: str | None = None,
        public_key: str | None = None,
        capabilities: list[str] | None = None,
    ) -> dict:
        if not device_name:
            raise ValueError("device_name is required")
        self.sync_device_calls.append((user_id, device_name))
        self.sync_device_revoked = False
        return self.list_sync_devices(user_id)[0] | {
            "device_name": device_name,
            "platform": platform.lower(),
            "public_key": public_key,
            "capabilities": capabilities or [],
            "device_key": device_key or "csd_test",
        }

    def revoke_sync_device(self, user_id: str, device_id: str) -> dict | None:
        if device_id != "sdev_test":
            return None
        self.sync_device_revoked = True
        return self.list_sync_devices(user_id, include_revoked=True)[0]

    def list_sync_receipts(self, user_id: str, device_id: str, *, limit: int = 50) -> list[dict]:
        if device_id != "sdev_test":
            return []
        return [
            {
                "id": "srec_test",
                "user_id": user_id,
                "device_id": device_id,
                "cursor": "evt_test",
                "status": "uploaded",
                "manifest_hash": "sha256:test",
                "remote_ref": "local-sync://standalone/upload-1",
                "error": None,
                "stats": {"changes": 1},
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ][:limit]

    def record_sync_receipt(
        self,
        user_id: str,
        device_id: str,
        *,
        cursor: str,
        status: str = "accepted",
        manifest_hash: str | None = None,
        remote_ref: str | None = None,
        error: str | None = None,
        stats: dict | None = None,
    ) -> dict:
        if device_id != "sdev_test":
            raise ValueError("sync device not found")
        if self.sync_device_revoked:
            raise ValueError("sync device is revoked")
        self.sync_receipt_calls.append((user_id, device_id, cursor, status))
        return self.list_sync_receipts(user_id, device_id)[0] | {
            "cursor": cursor,
            "status": status,
            "manifest_hash": manifest_hash,
            "remote_ref": remote_ref,
            "error": error,
            "stats": stats or {},
        }

    def analyze_import_sources(self, paths: list[str], source_hint: str = "", max_records: int = 500) -> dict:
        self.import_analysis_calls.append((paths, source_hint, max_records))
        return {"records_found": 1, "sources": [{"source": "chatgpt", "count": 1}], "sample": [], "supported_sources": self.supported_import_sources()}

    def import_sources(self, *, user_id: str, paths: list[str], source_hint: str = "", processing: str = "async", max_records: int = 1000) -> dict:
        self.import_sources_calls.append((user_id, paths, source_hint, processing, max_records))
        return {
            "import_id": "imp_test",
            "status": "complete",
            "records_found": 1,
            "queued": 1 if processing == "async" else 0,
            "saved": 1 if processing == "sync" else 0,
            "failed": 0,
            "skipped": 0,
            "sources": [{"source": "chatgpt", "count": 1}],
            "records": [],
            "errors": [],
        }

    def list_imports(self, user_id: str, limit: int = 50, include_deleted: bool = True) -> list[dict]:
        self.list_imports_calls.append((user_id, limit, include_deleted))
        return [{"import_id": "imp_test", "status": "complete", "records_found": 1, "queued": 1, "saved": 0, "failed": 0, "skipped": 0, "sources": [{"source": "chatgpt", "count": 1}], "can_delete": True}]

    def get_import(self, user_id: str, import_id: str) -> dict | None:
        self.get_import_calls.append((user_id, import_id))
        if import_id != "imp_test":
            return None
        return {"import_id": import_id, "status": "complete", "records_found": 1, "records": [], "captures": []}

    def delete_import(self, user_id: str, import_id: str) -> dict:
        self.delete_import_calls.append((user_id, import_id))
        if import_id != "imp_test":
            raise FileNotFoundError("Import not found")
        return {"import_id": import_id, "deleted": True, "status": "deleted", "deleted_captures": 1, "deleted_memories": 2, "deleted_tasks": 0, "deleted_edges": 2}

    def delete_user_data(self, user_id: str, *, include_backups: bool = True) -> dict:
        self.delete_user_data_calls.append((user_id, include_backups))
        return {"deleted_at": "2026-01-01T00:00:00Z", "include_backups": include_backups}

    def authenticate_mcp_token(self, token: str) -> dict | None:
        if token != "cxm-standalone-token":
            return None
        return {
            "token_id": "tok_standalone",
            "user_id": "local",
            "label": "Standalone test MCP",
            "audience": "mcp",
            "scopes": ["read"],
            "admin": False,
        }

    def authenticate_api_token(self, token: str, user_id: str | None = None) -> dict | None:
        if token != "cxa-standalone-token":
            return None
        return {
            "token_id": "tok_standalone_api",
            "user_id": "alice",
            "label": "Standalone test API",
            "audience": "api",
            "scopes": ["read"],
            "admin": False,
        }

    def ensure_api_token(self, user_id: str, token: str, *, label: str, scopes) -> dict:
        return {"token_id": "tok_api", "user_id": user_id, "label": label, "audience": "api", "scopes": scopes or [], "updated_at": "2026-01-01T00:00:00Z"}

    def ensure_mcp_token(self, user_id: str, token: str, *, label: str, scopes, token_id: str) -> dict:
        return {"token_id": token_id, "user_id": user_id, "label": label, "audience": "mcp", "scopes": scopes or [], "updated_at": "2026-01-01T00:00:00Z"}

    def list_tokens(self, user_id: str, *, audience: str | None = None, include_revoked: bool = False) -> list[dict]:
        tokens = [
            {
                "token_id": "tok_standalone_api",
                "user_id": user_id,
                "label": "Standalone test API",
                "audience": "api",
                "scopes": ["read"],
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "last_used_at": None,
                "revoked_at": None,
            }
        ]
        return [token for token in tokens if not audience or token["audience"] == audience]

    def revoke_token(self, user_id: str, token_id: str) -> dict | None:
        self.revoke_token_calls.append((user_id, token_id))
        if token_id != "tok_standalone_api":
            return None
        return {
            "token_id": token_id,
            "user_id": user_id,
            "label": "Standalone test API",
            "audience": "api",
            "scopes": ["read"],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:01Z",
            "last_used_at": None,
            "revoked_at": "2026-01-01T00:00:01Z",
            "revoked": True,
        }

    def require_agent_access(self, user_id: str, capability: str) -> None:
        return None

    def agent_payload(self, user_id: str, value):
        return value

    def record_agent_event(self, user_id: str, tool_name: str, args: dict, *, success: bool, error: str | None = None, token: dict | None = None) -> None:
        self.agent_events.append({"user_id": user_id, "tool": tool_name, "success": success, "error": error, "token": token})


class StandaloneServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.original_store = standalone_server.store
        self.original_settings = standalone_server.settings
        self.original_origins = standalone_server.ALLOWED_CORS_ORIGINS
        self.fake_store = FakeStore()
        standalone_server.store = self.fake_store
        standalone_server.settings = Settings(
            vault_path=Path(self.tmp.name) / "vault",
            db_path=Path(self.tmp.name) / "index.sqlite",
            api_key="test-token",
            public_base_url="http://127.0.0.1:8766",
        )
        standalone_server.ALLOWED_CORS_ORIGINS = {"http://127.0.0.1:8766", "http://localhost:8766"}
        self.server = standalone_server.ThreadingHTTPServer(("127.0.0.1", 0), standalone_server.CortexRequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        standalone_server.store = self.original_store
        standalone_server.settings = self.original_settings
        standalone_server.ALLOWED_CORS_ORIGINS = self.original_origins
        self.tmp.cleanup()

    def get(self, path: str, origin: str | None = None):
        headers = {"Authorization": "Bearer test-token"}
        if origin:
            headers["Origin"] = origin
        return request.urlopen(request.Request(self.base_url + path, headers=headers), timeout=5)

    def delete(self, path: str):
        headers = {"Authorization": "Bearer test-token"}
        return request.urlopen(request.Request(self.base_url + path, headers=headers, method="DELETE"), timeout=5)

    def post(self, path: str):
        headers = {"Authorization": "Bearer test-token"}
        return request.urlopen(request.Request(self.base_url + path, headers=headers, method="POST"), timeout=5)

    def post_json(self, path: str, payload: dict):
        headers = {"Authorization": "Bearer test-token", "Content-Type": "application/json"}
        data = json.dumps(payload).encode("utf-8")
        return request.urlopen(request.Request(self.base_url + path, data=data, headers=headers, method="POST"), timeout=5)

    def test_capture_page_does_not_echo_invalid_token(self) -> None:
        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(self.base_url + "/capture?token=wrong-token&content=Remember", timeout=5)

        self.assertEqual(context.exception.code, 401)
        body = context.exception.read().decode("utf-8")
        self.assertIn("Missing or invalid Cortex capture token", body)
        self.assertNotIn("wrong-token", body)

    def test_search_forwards_layer_and_kind_to_store(self) -> None:
        with self.get("/v1/search?query=voice&kind=style&layer=style&limit=7") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["results"][0]["layer"], "style")
        self.assertEqual(self.fake_store.search_calls, [("local", "voice", 7, "style", "style")])

    def test_ask_route_forwards_to_store(self) -> None:
        with self.get("/v1/ask?query=voice&limit=2") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertIn("cited memory", payload["answer"])
        self.assertEqual(payload["citations"][0]["source_url"], "/tmp/source.md")
        self.assertEqual(self.fake_store.answer_calls, [("local", "voice", 2)])

    def test_cors_does_not_allow_arbitrary_origin(self) -> None:
        with self.get("/v1/search?query=voice", origin="https://example.invalid") as response:
            headers = response.headers

        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

    def test_cors_echoes_configured_local_origin(self) -> None:
        with self.get("/v1/search?query=voice", origin="http://localhost:8766") as response:
            headers = response.headers

        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "http://localhost:8766")
        self.assertEqual(headers.get("Vary"), "Origin")

    def test_delete_capture_forwards_to_store(self) -> None:
        with self.delete("/v1/captures/capture-1") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload, {"deleted": True})
        self.assertEqual(self.fake_store.delete_capture_calls, [("local", "capture-1")])

    def test_delete_backups_forwards_to_store(self) -> None:
        with self.delete("/v1/backups") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["deleted"], 2)
        self.assertEqual(self.fake_store.delete_backups_calls, ["local"])

    def test_diagnostics_exposes_embedding_provider_contract(self) -> None:
        with self.get("/v1/diagnostics") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["embedding"]["provider"], "hash")
        self.assertEqual(payload["embedding"]["model"], "cortex-hash-v1")
        self.assertEqual(payload["embedding"]["dimensions"], 384)
        self.assertTrue(payload["embedding"]["index_compatible"])

    def test_memory_quality_route_forwards_to_store(self) -> None:
        with self.get("/v1/memory/quality") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["score"], 72)
        self.assertEqual(payload["source_health"][0]["source"], "unit-test")

    def test_sync_changes_route_forwards_to_store(self) -> None:
        with self.get("/v1/sync/changes?limit=1&device_id=sdev_test") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["sync_contract"], 1)
        self.assertFalse(payload["content_included"])
        self.assertEqual(payload["changes"][0]["id"], "evt_test")
        self.assertEqual(payload["device"]["id"], "sdev_test")
        self.assertFalse(payload["signature"]["configured"])
        self.assertEqual(self.fake_store.sync_feed_calls[-1], ("local", "", 1, "sdev_test", "", None))

        with self.get("/v1/sync/changes?after=evt_missing") as response:
            invalid = json.loads(response.read().decode("utf-8"))

        self.assertEqual(invalid["warnings"], ["cursor_not_found"])
        self.assertEqual(invalid["changes"], [])

    def test_restore_latest_backup_forwards_to_store(self) -> None:
        with self.post("/v1/backups/restore-latest") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["rebuild"]["captures"], 1)
        self.assertEqual(self.fake_store.restore_latest_backup_calls, ["local"])

    def test_import_sources_routes_validate_and_forward_to_store(self) -> None:
        with self.get("/v1/imports/sources") as response:
            catalog = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(catalog["results"][0]["id"], "chatgpt")

        with self.post_json("/v1/imports/analyze", {"paths": ["/tmp/conversations.json"], "max_records": 5}) as response:
            analysis = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(analysis["records_found"], 1)
        self.assertEqual(self.fake_store.import_analysis_calls, [(["/tmp/conversations.json"], "", 5)])

        with self.post_json("/v1/imports", {"paths": ["/tmp/conversations.json"], "processing": "sync", "source_hint": "chatgpt"}) as response:
            imported = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(imported["saved"], 1)
        self.assertEqual(self.fake_store.import_sources_calls, [("local", ["/tmp/conversations.json"], "chatgpt", "sync", 1000)])

        with self.get("/v1/imports?limit=10&include_deleted=false") as response:
            history = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(history["results"][0]["import_id"], "imp_test")
        self.assertEqual(self.fake_store.list_imports_calls, [("local", 10, False)])

        with self.get("/v1/imports/imp_test") as response:
            detail = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(detail["import_id"], "imp_test")
        self.assertEqual(self.fake_store.get_import_calls, [("local", "imp_test")])

        with self.delete("/v1/imports/imp_test") as response:
            deleted = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertTrue(deleted["deleted"])
        self.assertEqual(self.fake_store.delete_import_calls, [("local", "imp_test")])

    def test_import_sources_rejects_invalid_payloads(self) -> None:
        with self.assertRaises(error.HTTPError) as context:
            self.post_json("/v1/imports", {"paths": "/tmp/conversations.json"})
        self.assertEqual(context.exception.code, 422)

        with self.assertRaises(error.HTTPError) as context:
            self.post_json("/v1/imports", {"paths": ["/tmp/conversations.json"], "processing": "later"})
        self.assertEqual(context.exception.code, 422)

        with self.assertRaises(error.HTTPError) as context:
            self.post_json("/v1/imports/analyze", {"paths": ["/tmp/conversations.json"], "max_records": "many"})
        self.assertEqual(context.exception.code, 422)

    def test_source_account_and_sync_cursor_routes_forward_to_store(self) -> None:
        with self.get("/v1/source-accounts/catalog") as response:
            catalog = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(catalog["results"][0]["id"], "gmail")

        with self.get("/v1/sources/readiness") as response:
            readiness = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(readiness["sources"][0]["source"], "gmail")
        self.assertEqual(readiness["summary"]["sources_total"], 1)

        with self.post_json(
            "/v1/source-accounts",
            {
                "source": "Gmail",
                "account_label": "Standalone Gmail",
                "account_identifier": "standalone@example.com",
                "connection_type": "oauth",
                "status": "connected",
                "auth_state": "healthy",
                "policy": {"sync": "incremental"},
            },
        ) as response:
            account = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(account["source"], "gmail")
        self.assertEqual(self.fake_store.source_account_calls, [("local", "Gmail")])

        with self.post_json(
            "/v1/sync-cursors",
            {
                "source": "gmail",
                "source_account_id": "sacct_test",
                "cursor_name": "messages",
                "cursor_value": "cursor-1",
                "high_water_mark": "2026-01-01T00:00:00Z",
                "state": {"batch": 1},
            },
        ) as response:
            cursor = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(cursor["source_account_id"], "sacct_test")
        self.assertEqual(cursor["state"]["batch"], 1)
        self.assertEqual(self.fake_store.sync_cursor_calls, [("local", "messages", "sacct_test")])

        with self.get("/v1/source-accounts") as response:
            accounts = json.loads(response.read().decode("utf-8"))
        self.assertEqual(accounts["results"][0]["id"], "sacct_test")

        with self.get("/v1/sync-cursors?source_account_id=sacct_test") as response:
            cursors = json.loads(response.read().decode("utf-8"))
        self.assertEqual(cursors["results"][0]["id"], "sync_test")

        with self.post_json(
            "/v1/sync/devices",
            {"device_name": "Standalone Mac", "platform": "macOS", "capabilities": ["manifest"]},
        ) as response:
            device = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 200)
        self.assertEqual(device["id"], "sdev_test")
        self.assertEqual(device["device_key"], "csd_test")
        self.assertEqual(self.fake_store.sync_device_calls, [("local", "Standalone Mac")])

        with self.get("/v1/sync/devices") as response:
            devices = json.loads(response.read().decode("utf-8"))
        self.assertEqual(devices["results"][0]["id"], "sdev_test")
        self.assertNotIn("device_key", devices["results"][0])

        with self.post_json(
            "/v1/sync/devices/sdev_test/receipts",
            {
                "cursor": "evt_test",
                "status": "uploaded",
                "manifest_hash": "sha256:test",
                "remote_ref": "local-sync://standalone/upload-1",
                "stats": {"changes": 1},
            },
        ) as response:
            receipt = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 200)
        self.assertEqual(receipt["id"], "srec_test")
        self.assertEqual(receipt["status"], "uploaded")
        self.assertEqual(self.fake_store.sync_receipt_calls, [("local", "sdev_test", "evt_test", "uploaded")])

        with self.get("/v1/sync/devices/sdev_test/receipts?limit=5") as response:
            receipts = json.loads(response.read().decode("utf-8"))
        self.assertEqual(receipts["results"][0]["id"], "srec_test")

        with self.post_json(
            "/v1/sync-cursors",
            {"source": "gmail", "source_account_id": "sacct_test", "cursor_name": "messages", "completed": "false", "last_error": "paused"},
        ) as response:
            failed_cursor = json.loads(response.read().decode("utf-8"))
        self.assertIsNone(failed_cursor["last_completed_at"])
        self.assertEqual(failed_cursor["last_error"], "paused")

        with self.assertRaises(error.HTTPError) as context:
            self.post_json("/v1/sync-cursors", {"source": "gmail", "source_account_id": "sacct_missing", "cursor_name": "messages"})
        self.assertEqual(context.exception.code, 422)

        with self.delete("/v1/source-accounts/sacct_test") as response:
            disconnected = json.loads(response.read().decode("utf-8"))
        self.assertEqual(disconnected["status"], "disconnected")

        with self.assertRaises(error.HTTPError) as context:
            self.delete("/v1/source-accounts/sacct_missing")
        self.assertEqual(context.exception.code, 404)

        with self.delete("/v1/sync/devices/sdev_test") as response:
            revoked_device = json.loads(response.read().decode("utf-8"))
        self.assertEqual(revoked_device["id"], "sdev_test")
        self.assertIsNotNone(revoked_device["revoked_at"])

        with self.get("/v1/sync/devices") as response:
            active_devices = json.loads(response.read().decode("utf-8"))
        self.assertEqual(active_devices["results"], [])

        with self.get("/v1/sync/devices?include_revoked=true") as response:
            all_devices = json.loads(response.read().decode("utf-8"))
        self.assertEqual(all_devices["results"][0]["revoked_at"], "2026-01-01T00:00:01Z")

        with self.assertRaises(error.HTTPError) as context:
            self.delete("/v1/sync/devices/sdev_missing")
        self.assertEqual(context.exception.code, 404)

        with self.get("/v1/source-accounts") as response:
            active = json.loads(response.read().decode("utf-8"))
        self.assertEqual(active["results"], [])

        with self.get("/v1/source-accounts?include_disconnected=true") as response:
            all_accounts = json.loads(response.read().decode("utf-8"))
        self.assertEqual(all_accounts["results"][0]["status"], "disconnected")

    def test_delete_user_data_forwards_include_backups_flag(self) -> None:
        with self.delete("/v1/user-data?include_backups=false") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertFalse(payload["include_backups"])
        self.assertEqual(self.fake_store.delete_user_data_calls, [("local", False)])

    def test_scoped_mcp_token_can_call_mcp_but_not_rest(self) -> None:
        scoped_headers = {"Authorization": "Bearer cxm-standalone-token"}
        mcp_request = request.Request(
            self.base_url + "/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "search_memory", "arguments": {"query": "voice"}}}).encode("utf-8"),
            headers={**scoped_headers, "Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(mcp_request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertIn("result", payload)
        self.assertEqual(self.fake_store.search_calls[-1], ("local", "voice", 8, None, None))
        self.assertEqual(self.fake_store.agent_events[-1]["token"]["token_id"], "tok_standalone")

        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(request.Request(self.base_url + "/v1/search?query=voice", headers=scoped_headers), timeout=5)
        self.assertEqual(context.exception.code, 401)

    def test_scoped_api_token_prevents_user_header_impersonation_when_required(self) -> None:
        standalone_server.settings = Settings(
            vault_path=Path(self.tmp.name) / "vault",
            db_path=Path(self.tmp.name) / "index.sqlite",
            api_key="test-token",
            public_base_url="http://127.0.0.1:8766",
            require_scoped_api_tokens=True,
        )

        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(
                request.Request(
                    self.base_url + "/v1/stats",
                    headers={"Authorization": "Bearer test-token", "X-Cortex-User": "alice"},
                ),
                timeout=5,
            )
        self.assertEqual(context.exception.code, 403)

        with request.urlopen(
            request.Request(
                self.base_url + "/v1/search?query=voice",
                headers={"Authorization": "Bearer cxa-standalone-token", "X-Cortex-User": "alice"},
            ),
            timeout=5,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["results"][0]["content"], "Layer-aware result")
        self.assertEqual(self.fake_store.search_calls[-1][0], "alice")

        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(
                request.Request(
                    self.base_url + "/v1/context-pack?query=voice",
                    headers={"Authorization": "Bearer cxa-standalone-token", "X-Cortex-User": "alice"},
                ),
                timeout=5,
            )
        self.assertEqual(context.exception.code, 403)
        self.assertIn("export scope", context.exception.read().decode("utf-8"))

        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(
                request.Request(
                    self.base_url + "/v1/captures/capture-1",
                    headers={"Authorization": "Bearer cxa-standalone-token", "X-Cortex-User": "alice"},
                    method="DELETE",
                ),
                timeout=5,
            )
        self.assertEqual(context.exception.code, 403)
        self.assertIn("destructive scope", context.exception.read().decode("utf-8"))

        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(
                request.Request(
                    self.base_url + "/v1/search?query=voice",
                    headers={"Authorization": "Bearer cxa-standalone-token", "X-Cortex-User": "bob"},
                ),
                timeout=5,
            )
        self.assertEqual(context.exception.code, 403)

    def test_global_token_cannot_select_user_in_sharded_mode(self) -> None:
        standalone_server.settings = Settings(
            vault_path=Path(self.tmp.name) / "vault",
            db_path=Path(self.tmp.name) / "index.sqlite",
            api_key="test-token",
            public_base_url="http://127.0.0.1:8766",
            shard_mode="user",
        )

        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(
                request.Request(
                    self.base_url + "/v1/stats",
                    headers={"Authorization": "Bearer test-token", "X-Cortex-User": "alice"},
                ),
                timeout=5,
            )
        self.assertEqual(context.exception.code, 403)
        self.assertIn("sharded mode", context.exception.read().decode("utf-8"))

        mcp_request = request.Request(
            self.base_url + "/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 17, "method": "tools/list", "params": {}}).encode("utf-8"),
            headers={"Authorization": "Bearer test-token", "X-Cortex-User": "alice", "Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(mcp_request, timeout=5)
        self.assertEqual(context.exception.code, 403)
        self.assertIn("sharded mode", context.exception.read().decode("utf-8"))

    def test_integration_tokens_can_be_listed_and_revoked(self) -> None:
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "alice"}
        with request.urlopen(request.Request(self.base_url + "/v1/integrations/tokens?audience=api", headers=headers), timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["results"][0]["token_id"], "tok_standalone_api")
        self.assertNotIn("token_hash", payload["results"][0])

        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(request.Request(self.base_url + "/v1/integrations/tokens?audience=browser", headers=headers), timeout=5)
        self.assertEqual(context.exception.code, 422)

        with request.urlopen(
            request.Request(
                self.base_url + "/v1/integrations/tokens/tok_standalone_api",
                headers=headers,
                method="DELETE",
            ),
            timeout=5,
        ) as response:
            revoked = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertTrue(revoked["revoked"])
        self.assertEqual(self.fake_store.revoke_token_calls, [("alice", "tok_standalone_api")])


if __name__ == "__main__":
    unittest.main()
