from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path
from urllib import error, request

from backend.app.config import Settings

MODULE_TMP = tempfile.TemporaryDirectory()
os.environ["CORTEX_DB_PATH"] = str(Path(MODULE_TMP.name) / "bootstrap.sqlite")
os.environ["CORTEX_VAULT_PATH"] = str(Path(MODULE_TMP.name) / "bootstrap.vault")
os.environ["CORTEX_API_KEY"] = "test-token"

from backend.app import standalone_server


def tearDownModule() -> None:
    MODULE_TMP.cleanup()


class FakeStore:
    def __init__(self) -> None:
        self.search_calls: list[dict] = []
        self.answer_calls: list[dict] = []
        self.delete_capture_calls: list[tuple[str, str]] = []
        self.delete_backups_calls: list[str] = []
        self.delete_user_data_calls: list[tuple[str, bool]] = []
        self.restore_latest_backup_calls: list[str] = []
        self.agent_events: list[dict] = []
        self.import_analysis_calls: list[tuple[list[str], str, int]] = []
        self.import_sources_calls: list[tuple[str, list[str], str, str, int, int]] = []
        self.list_imports_calls: list[tuple[str, int, bool]] = []
        self.get_import_calls: list[tuple[str, str]] = []
        self.delete_import_calls: list[tuple[str, str]] = []
        self.revoke_token_calls: list[tuple[str, str]] = []
        self.source_account_calls: list[tuple[str, str]] = []
        self.source_account_sync_calls: list[tuple[str, str, int, str, bool]] = []
        self.obsidian_sync_calls: list[dict] = []
        self.github_sync_calls: list[dict] = []
        self.gmail_sync_calls: list[dict] = []
        self.google_drive_sync_calls: list[dict] = []
        self.google_oauth_start_calls: list[dict] = []
        self.google_oauth_complete_calls: list[dict] = []
        self.managed_oauth_start_calls: list[dict] = []
        self.managed_oauth_complete_calls: list[dict] = []
        self.source_account_sync_enqueue_calls: list[dict] = []
        self.outlook_sync_calls: list[dict] = []
        self.slack_sync_calls: list[dict] = []
        self.readwise_sync_calls: list[dict] = []
        self.calendar_sync_calls: list[dict] = []
        self.raindrop_sync_calls: list[dict] = []
        self.zotero_sync_calls: list[dict] = []
        self.linear_sync_calls: list[dict] = []
        self.jira_sync_calls: list[dict] = []
        self.notion_sync_calls: list[dict] = []
        self.sync_cursor_calls: list[tuple[str, str, str | None]] = []
        self.sync_device_calls: list[tuple[str, str]] = []
        self.sync_receipt_calls: list[tuple[str, str, str, str]] = []
        self.sync_feed_calls: list[tuple[str, str, int, str, str, dict | None]] = []
        self.job_run_calls: list[tuple[str, int, str, bool]] = []
        self.source_sync_run_calls: list[tuple[str, int, str]] = []
        self.source_account_disconnected = False
        self.sync_device_revoked = False
        self.api_token_scopes = ["read"]
        self.denied_agent_access: set[str] = set()
        self.require_agent_access_calls: list[tuple[str, str]] = []
        self.context_pack_calls: list[tuple[str, str, int, str | None]] = []
        self.assemble_context_calls: list[dict] = []
        self.verify_context_pack_calls: list[tuple[str, str]] = []
        self.oauth_pending: dict[tuple[str, str], dict] = {}

    def remember_oauth_pending(self, *, state, user_id, flow, payload, ttl_seconds: int = 600) -> None:
        normalized_state = str(state or "").strip()
        normalized_flow = str(flow or "").strip()
        if not normalized_state or not normalized_flow:
            return
        stored = {
            key: value
            for key, value in (payload or {}).items()
            if key not in {"created_at", "expires_at", "user_id", "flow", "state"}
        }
        self.oauth_pending[(normalized_state, normalized_flow)] = {"user_id": user_id, "payload": stored}

    def pop_oauth_pending(self, state, *, flow):
        entry = self.oauth_pending.pop((str(state or "").strip(), str(flow or "").strip()), None)
        if not entry:
            return None
        result = dict(entry["payload"])
        result["user_id"] = entry["user_id"]
        return result

    def search(
        self,
        user_id: str,
        query: str,
        limit: int,
        kind: str | None = None,
        layer: str | None = None,
        *,
        sector: str | None = None,
        source: str | None = None,
        source_account_id: str | None = None,
        as_of: str | None = None,
        metadata_filters: dict | None = None,
    ) -> list[dict]:
        self.search_calls.append(
            {
                "user_id": user_id,
                "query": query,
                "limit": limit,
                "kind": kind,
                "layer": layer,
                "sector": sector,
                "source": source,
                "source_account_id": source_account_id,
                "as_of": as_of,
                "metadata_filters": metadata_filters or {},
            }
        )
        return [
            {
                "id": "memory-1",
                "kind": kind or "claim",
                "layer": layer or "semantic",
                "content": "Layer-aware result",
                "source": "unit-test",
            }
        ]

    def answer_query(
        self,
        user_id: str,
        query: str,
        limit: int,
        *,
        sector: str | None = None,
        source: str | None = None,
        source_account_id: str | None = None,
        as_of: str | None = None,
        metadata_filters: dict | None = None,
    ) -> dict:
        self.answer_calls.append(
            {
                "user_id": user_id,
                "query": query,
                "limit": limit,
                "sector": sector,
                "source": source,
                "source_account_id": source_account_id,
                "as_of": as_of,
                "metadata_filters": metadata_filters or {},
            }
        )
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

    def context_pack(self, user_id: str, *, query: str = "", limit: int = 12, sector: str | None = None) -> str:
        self.context_pack_calls.append((user_id, query, limit, sector))
        return "# Cortex Context\n\nLayer-aware result"

    def assemble_context(self, user_id: str, task: str = "", **kwargs) -> dict | str:
        call = {"user_id": user_id, "task": task, **kwargs}
        self.assemble_context_calls.append(call)
        if kwargs.get("format") == "markdown":
            return "# Cortex Context Pack\n\nassembled"
        return {
            "version": 1,
            "task": task,
            "intent": "answer",
            "surface": kwargs.get("surface"),
            "coverage": {"status": "usable"},
            "layers": [],
            "citations": [],
        }

    def verify_context_pack(self, user_id: str, pack_sha: str) -> dict:
        self.verify_context_pack_calls.append((user_id, pack_sha))
        if pack_sha == "missing" * 8:  # 56 chars, clearly not a known sha
            raise ValueError("Unknown context pack sha for this user")
        return {"pack_sha": pack_sha, "status": "match", "verified_storage": True, "diff": {"equal": True}}

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

    def stats(self, user_id: str) -> dict:
        return {"captures": 1, "memories": 1, "tasks": 0, "entities": 0}

    def health_payload(self, *, mode: str, auth: bool) -> dict:
        return {
            "status": "ok",
            "backend_version": "test",
            "mode": mode,
            "auth": auth,
            "sharding": {
                "mode": "local",
                "default": {"shard_id": "local", "db_path": "/tmp/index.sqlite", "vault_path": "/tmp/Cortex.vault"},
            },
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
            "recommendations": ["Prefer connected-source sync and cited captures so retrieved memory has citations."],
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

    def start_google_oauth(
        self,
        source: str,
        *,
        redirect_uri: str | None = None,
        state: str | None = None,
        client_id: str | None = None,
        code_challenge: str | None = None,
        code_challenge_method: str | None = None,
        scopes: list[str] | None = None,
    ) -> dict:
        call = {
            "source": source,
            "redirect_uri": redirect_uri,
            "state": state,
            "client_id": client_id,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "scopes": scopes or [],
        }
        self.google_oauth_start_calls.append(call)
        resolved_state = state or "standalone-oauth-state"
        authorization_url = (
            "https://accounts.google.test/o/oauth2/v2/auth?"
            f"client_id={client_id}&state={resolved_state}&code_challenge={code_challenge}&code_challenge_method={code_challenge_method}"
        )
        return {
            "source": source,
            "provider": "google",
            "authorization_url": authorization_url,
            "authorization_endpoint": "https://accounts.google.test/o/oauth2/v2/auth",
            "token_endpoint": "https://oauth2.google.test/token",
            "redirect_uri": redirect_uri or "http://127.0.0.1:8766/v1/connectors/google/oauth/callback",
            "state": resolved_state,
            "scopes": scopes or ["https://www.googleapis.com/auth/gmail.readonly"],
            "access_type": "offline",
        }

    def complete_google_oauth(
        self,
        user_id: str,
        source: str,
        *,
        code: str,
        redirect_uri: str | None = None,
        state: str | None = None,
        expected_state: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_endpoint: str | None = None,
        code_verifier: str | None = None,
        source_account_id: str | None = None,
        account_label: str | None = None,
        account_identifier: str | None = None,
        query: str | None = None,
        label_ids: list[str] | None = None,
        mime_types: list[str] | None = None,
        include_body: bool = True,
        include_content: bool = True,
    ) -> dict:
        call = {
            "user_id": user_id,
            "source": source,
            "code": code,
            "redirect_uri": redirect_uri,
            "state": state,
            "expected_state": expected_state,
            "client_id": client_id,
            "client_secret": client_secret,
            "token_endpoint": token_endpoint,
            "code_verifier": code_verifier,
            "source_account_id": source_account_id,
            "account_label": account_label,
            "account_identifier": account_identifier,
            "query": query,
            "label_ids": label_ids or [],
            "mime_types": mime_types or [],
            "include_body": include_body,
            "include_content": include_content,
        }
        self.google_oauth_complete_calls.append(call)
        account_id = source_account_id or "sacct_google_oauth_test"
        return {
            "source": source,
            "provider": "google",
            "source_account": {
                "id": account_id,
                "user_id": user_id,
                "source": source,
                "account_label": account_label or "Google",
                "account_identifier": account_identifier or "google",
                "connection_type": "oauth-token",
                "status": "connected",
                "auth_state": "healthy",
                "policy": {"review_required": True, "allow_ai_context": True},
                "metadata": {"managed_oauth": True, "oauth_provider": "google"},
                "last_sync_at": None,
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "disconnected_at": None,
            },
            "credential_ref": "credential://source/sacct_google_oauth_test",
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
            "access_token_expires_at": "2026-01-01T01:00:00Z",
            "sync_plan": {"scheduler_supported": True, "due_now": True},
        }

    def enqueue_source_account_sync(
        self,
        user_id: str,
        account_id: str,
        *,
        processing: str = "async",
        cursor_name: str | None = None,
        max_records: int = 200,
        run_at: str | None = None,
        schedule_token: str | None = None,
    ) -> dict:
        call = {
            "user_id": user_id,
            "account_id": account_id,
            "processing": processing,
            "cursor_name": cursor_name,
            "max_records": max_records,
            "run_at": run_at,
            "schedule_token": schedule_token,
        }
        self.source_account_sync_enqueue_calls.append(call)
        return {"id": "job_google_oauth_sync", "status": "queued", "object_id": account_id}

    def start_managed_oauth(
        self,
        source: str,
        *,
        redirect_uri: str | None = None,
        state: str | None = None,
        client_id: str | None = None,
        scopes: list[str] | None = None,
    ) -> dict:
        call = {
            "source": source,
            "redirect_uri": redirect_uri,
            "state": state,
            "client_id": client_id,
            "scopes": scopes or [],
        }
        self.managed_oauth_start_calls.append(call)
        resolved_state = state or "standalone-managed-oauth-state"
        authorization_url = (
            "https://api.notion.test/v1/oauth/authorize?"
            f"client_id={client_id}&state={resolved_state}&owner=user"
        )
        return {
            "source": source,
            "provider": source,
            "authorization_url": authorization_url,
            "authorization_endpoint": "https://api.notion.test/v1/oauth/authorize",
            "token_endpoint": "https://api.notion.test/v1/oauth/token",
            "redirect_uri": redirect_uri or "http://127.0.0.1:8766/v1/connectors/oauth/callback",
            "state": resolved_state,
            "scopes": scopes or [],
            "access_type": "offline",
        }

    def complete_managed_oauth(
        self,
        user_id: str,
        source: str,
        *,
        code: str,
        redirect_uri: str | None = None,
        state: str | None = None,
        expected_state: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_endpoint: str | None = None,
        source_account_id: str | None = None,
        account_label: str | None = None,
        account_identifier: str | None = None,
        include_content: bool = True,
        api_base_url: str | None = None,
        notion_version: str | None = None,
    ) -> dict:
        call = {
            "user_id": user_id,
            "source": source,
            "code": code,
            "redirect_uri": redirect_uri,
            "state": state,
            "expected_state": expected_state,
            "client_id": client_id,
            "client_secret": client_secret,
            "token_endpoint": token_endpoint,
            "source_account_id": source_account_id,
            "account_label": account_label,
            "account_identifier": account_identifier,
            "include_content": include_content,
            "api_base_url": api_base_url,
            "notion_version": notion_version,
        }
        self.managed_oauth_complete_calls.append(call)
        account_id = source_account_id or "sacct_managed_oauth_test"
        return {
            "source": source,
            "provider": source,
            "source_account": {
                "id": account_id,
                "user_id": user_id,
                "source": source,
                "account_label": account_label or "Notion",
                "account_identifier": account_identifier or "notion-workspace",
                "connection_type": "oauth-token",
                "status": "connected",
                "auth_state": "healthy",
                "policy": {"review_required": True, "allow_ai_context": True},
                "metadata": {"managed_oauth": True, "oauth_provider": source},
                "last_sync_at": None,
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "disconnected_at": None,
            },
            "credential_ref": "credential://source/sacct_managed_oauth_test",
            "scope": "",
            "scopes": [],
            "access_token_expires_at": None,
            "sync_plan": {"scheduler_supported": True, "due_now": True},
        }

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
                    "next_action": "Resume Gmail sync." if self.source_account_disconnected else "Source sync has completed; review new memories as they arrive.",
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
                    "warnings": ["Resume Gmail sync."] if self.source_account_disconnected else [],
                }
            ],
            "recommendations": ["Source readiness is healthy for local beta use."],
        }

    def run_due_source_sync_jobs(self, user_id: str, *, limit: int = 10, worker_id: str = "source-sync-worker") -> dict:
        self.source_sync_run_calls.append((user_id, limit, worker_id))
        return {
            "ran_at": "2026-01-01T00:00:00Z",
            "processed": 1,
            "jobs": [{"id": "job_source_sync", "job_type": "source_account_sync", "status": "succeeded"}],
            "scheduled_source_syncs": {"scheduled": 1, "jobs": [], "skipped": []},
            "pending": 0,
            "failed": 0,
        }

    def run_due_jobs(
        self,
        user_id: str,
        *,
        limit: int = 10,
        worker_id: str = "local-worker",
        schedule_source_syncs: bool = True,
    ) -> dict:
        self.job_run_calls.append((user_id, limit, worker_id, schedule_source_syncs))
        return {
            "ran_at": "2026-01-01T00:00:00Z",
            "processed": 1,
            "jobs": [{"id": "job_general", "job_type": "extract_capture", "status": "succeeded"}],
            "scheduled_source_syncs": {"scheduled": 1, "jobs": [], "skipped": []} if schedule_source_syncs else None,
            "pending": 0,
            "failed": 0,
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
                "retention": {
                    "disconnect_action": "pause_sync",
                    "disconnect_retains": ["source_account", "captures", "memories", "sync_cursors", "local_credentials"],
                    "disconnect_stops": ["scheduled_sync", "new_remote_reads"],
                    "delete_action": "delete_user_data",
                    "delete_endpoint": "/v1/user-data?include_backups=true",
                },
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

    def resume_source_account(self, user_id: str, account_id: str) -> dict | None:
        if account_id != "sacct_test":
            return None
        self.source_account_disconnected = False
        return self.list_source_accounts(user_id)[0]

    def sync_source_account_records(
        self,
        user_id: str,
        account_id: str,
        *,
        records: list[dict],
        cursor_name: str = "default",
        cursor_value: str | None = None,
        high_water_mark: str | None = None,
        state: dict | None = None,
        processing: str = "async",
        archive_missing: bool = False,
        complete_snapshot: bool = False,
    ) -> dict:
        if account_id == "sacct_missing":
            raise ValueError("source account not found")
        if archive_missing and not complete_snapshot:
            raise ValueError("archive_missing requires complete_snapshot=true so partial sync pages cannot archive existing memory")
        self.source_account_sync_calls.append((user_id, account_id, len(records), processing, archive_missing))
        return {
            "source_account_id": account_id,
            "source": "gmail",
            "status": "complete",
            "processing": processing,
            "received": len(records),
            "queued": 0 if processing == "sync" else len(records),
            "saved": len(records) if processing == "sync" else 0,
            "skipped": 0,
            "failed": 0,
            "archived_missing": 1 if archive_missing else 0,
            "capture_ids": ["cap_sync_test"] if records else [],
            "records": [
                {
                    "capture_id": "cap_sync_test",
                    "status": "saved" if processing == "sync" else "queued",
                    "source": "gmail",
                    "source_url": f"source-account://gmail/{account_id}/{records[0].get('external_id') or 'record-1'}",
                    "title": records[0].get("title"),
                }
            ] if records else [],
            "errors": [],
            "cursor": self.list_sync_cursors(user_id, source_account_id=account_id)[0] | {
                "cursor_name": cursor_name,
                "cursor_value": cursor_value,
                "high_water_mark": high_water_mark,
                "state": state or {},
            },
        }

    def sync_obsidian_vault(
        self,
        user_id: str,
        *,
        vault_path: str,
        source_account_id: str | None = None,
        account_label: str | None = None,
        account_identifier: str | None = None,
        processing: str = "sync",
        max_records: int = 1000,
        cursor_name: str = "local-folder",
        review_required: bool = True,
    ) -> dict:
        if not vault_path:
            raise ValueError("vault_path is required")
        call = {
            "user_id": user_id,
            "vault_path": vault_path,
            "source_account_id": source_account_id,
            "account_label": account_label,
            "account_identifier": account_identifier,
            "processing": processing,
            "max_records": max_records,
            "cursor_name": cursor_name,
        }
        self.obsidian_sync_calls.append(call)
        account_id = source_account_id or "sacct_obsidian_test"
        return {
            "source_account_id": account_id,
            "source": "obsidian",
            "status": "complete",
            "processing": processing,
            "received": 1,
            "queued": 0 if processing == "sync" else 1,
            "saved": 1 if processing == "sync" else 0,
            "skipped": 0,
            "failed": 0,
            "archived_missing": 0,
            "capture_ids": ["cap_obsidian_test"],
            "records": [
                {
                    "capture_id": "cap_obsidian_test",
                    "status": "saved" if processing == "sync" else "queued",
                    "source": "obsidian",
                    "source_url": f"obsidian://open?vault=Test&file={Path(vault_path).name}/Decision.md",
                    "title": "Decision",
                }
            ],
            "errors": [],
            "cursor": {
                "id": "sync_obsidian_test",
                "user_id": user_id,
                "source_account_id": account_id,
                "source": "obsidian",
                "cursor_name": cursor_name,
                "cursor_value": "manifest-hash",
                "high_water_mark": "2026-01-01T00:00:00Z",
                "state": {"records": 1},
                "last_error": None,
                "completed_at": "2026-01-01T00:00:00Z",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            "source_account": {
                "id": account_id,
                "user_id": user_id,
                "source": "obsidian",
                "account_label": account_label or "Obsidian: Test",
                "account_identifier": account_identifier or "test-vault",
                "connection_type": "local_folder",
                "status": "connected",
                "auth_state": "healthy",
                "policy": {"review_required": True, "allow_ai_context": True},
                "metadata": {"vault_path": vault_path, "records_returned": 1},
                "last_sync_at": "2026-01-01T00:00:00Z",
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "disconnected_at": None,
            },
            "scan": {
                "vault_name": "Test",
                "vault_path": vault_path,
                "records_found": 1,
                "records_returned": 1,
                "truncated": False,
            },
        }

    def sync_github_account(
        self,
        user_id: str,
        *,
        token: str,
        repositories: list[str],
        source_account_id: str | None = None,
        account_label: str | None = None,
        account_identifier: str | None = None,
        since: str | None = None,
        processing: str = "sync",
        max_records: int = 100,
        include_comments: bool = True,
        max_comments_per_item: int = 10,
        cursor_name: str = "issues",
        api_base_url: str | None = None,
        complete_snapshot: bool = False
    ) -> dict:
        if not token:
            raise ValueError("GitHub token is required")
        if not repositories:
            raise ValueError("At least one GitHub repository is required")
        call = {
            "user_id": user_id,
            "complete_snapshot": complete_snapshot,
            "token": token,
            "repositories": repositories,
            "source_account_id": source_account_id,
            "account_label": account_label,
            "account_identifier": account_identifier,
            "since": since,
            "processing": processing,
            "max_records": max_records,
            "include_comments": include_comments,
            "max_comments_per_item": max_comments_per_item,
            "cursor_name": cursor_name,
            "api_base_url": api_base_url,
        }
        self.github_sync_calls.append(call)
        account_id = source_account_id or "sacct_github_test"
        repository = repositories[0]
        return {
            "source_account_id": account_id,
            "source": "github",
            "status": "complete",
            "processing": processing,
            "received": 1,
            "queued": 0 if processing == "sync" else 1,
            "saved": 1 if processing == "sync" else 0,
            "skipped": 0,
            "failed": 0,
            "archived_missing": 0,
            "capture_ids": ["cap_github_test"],
            "records": [
                {
                    "capture_id": "cap_github_test",
                    "status": "saved" if processing == "sync" else "queued",
                    "source": "github",
                    "source_url": f"https://github.com/{repository}/issues/42",
                    "title": f"{repository} Issue #42: Test issue",
                }
            ],
            "errors": [],
            "cursor": {
                "id": "sync_github_test",
                "user_id": user_id,
                "source_account_id": account_id,
                "source": "github",
                "cursor_name": cursor_name,
                "cursor_value": "2026-01-01T00:00:00Z",
                "high_water_mark": "2026-01-01T00:00:00Z",
                "state": {"repositories": repositories, "records_returned": 1},
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            "source_account": {
                "id": account_id,
                "user_id": user_id,
                "source": "github",
                "account_label": account_label or f"GitHub: {repository}",
                "account_identifier": account_identifier or repository,
                "connection_type": "api_token",
                "status": "connected",
                "auth_state": "healthy",
                "policy": {"review_required": True, "allow_ai_context": True},
                "metadata": {"repositories": repositories, "records_returned": 1, "token_configured": True},
                "last_sync_at": "2026-01-01T00:00:00Z",
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "disconnected_at": None,
            },
            "sync": {
                "connector": "github",
                "connector_version": "test",
                "repositories": repositories,
                "records_found": 1,
                "records_returned": 1,
                "errors": [],
            },
        }

    def sync_gmail_account(
        self,
        user_id: str,
        *,
        access_token: str,
        source_account_id: str | None = None,
        account_label: str | None = None,
        account_identifier: str | None = None,
        query: str | None = None,
        label_ids: list[str] | None = None,
        since: str | None = None,
        page_token: str | None = None,
        processing: str = "sync",
        max_records: int = 50,
        cursor_name: str = "messages",
        include_body: bool = True,
        api_base_url: str | None = None,
        complete_snapshot: bool = False
    ) -> dict:
        if not access_token:
            raise ValueError("Gmail access token is required")
        call = {
            "user_id": user_id,
            "complete_snapshot": complete_snapshot,
            "access_token": access_token,
            "source_account_id": source_account_id,
            "account_label": account_label,
            "account_identifier": account_identifier,
            "query": query,
            "label_ids": label_ids or [],
            "since": since,
            "page_token": page_token,
            "processing": processing,
            "max_records": max_records,
            "cursor_name": cursor_name,
            "include_body": include_body,
            "api_base_url": api_base_url,
        }
        self.gmail_sync_calls.append(call)
        account_id = source_account_id or "sacct_gmail_test"
        return {
            "source_account_id": account_id,
            "source": "gmail",
            "status": "complete",
            "processing": processing,
            "received": 1,
            "queued": 0 if processing == "sync" else 1,
            "saved": 1 if processing == "sync" else 0,
            "skipped": 0,
            "failed": 0,
            "archived_missing": 0,
            "capture_ids": ["cap_gmail_test"],
            "records": [
                {
                    "capture_id": "cap_gmail_test",
                    "status": "saved" if processing == "sync" else "queued",
                    "source": "gmail",
                    "source_url": "gmail://message/msg_123",
                    "title": "Gmail Test message",
                }
            ],
            "errors": [],
            "cursor": {
                "id": "sync_gmail_test",
                "user_id": user_id,
                "source_account_id": account_id,
                "source": "gmail",
                "cursor_name": cursor_name,
                "cursor_value": "page-next",
                "high_water_mark": "2026-01-01T00:00:00Z",
                "state": {"records_returned": 1, "label_ids": label_ids or []},
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            "source_account": {
                "id": account_id,
                "user_id": user_id,
                "source": "gmail",
                "account_label": account_label or "Gmail",
                "account_identifier": account_identifier or "gmail-local",
                "connection_type": "api_token",
                "status": "connected",
                "auth_state": "healthy",
                "policy": {"review_required": True, "allow_ai_context": True},
                "metadata": {"records_returned": 1, "token_configured": True},
                "last_sync_at": "2026-01-01T00:00:00Z",
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "disconnected_at": None,
            },
            "sync": {
                "connector": "gmail",
                "connector_version": "test",
                "records_found": 1,
                "records_returned": 1,
                "errors": [],
            },
        }

    def sync_google_drive_account(
        self,
        user_id: str,
        *,
        access_token: str,
        source_account_id: str | None = None,
        account_label: str | None = None,
        account_identifier: str | None = None,
        query: str | None = None,
        mime_types: list[str] | None = None,
        since: str | None = None,
        page_token: str | None = None,
        processing: str = "sync",
        max_records: int = 50,
        cursor_name: str = "files",
        include_content: bool = True,
        api_base_url: str | None = None,
        complete_snapshot: bool = False
    ) -> dict:
        if not access_token:
            raise ValueError("Google Drive access token is required")
        call = {
            "user_id": user_id,
            "complete_snapshot": complete_snapshot,
            "access_token": access_token,
            "source_account_id": source_account_id,
            "account_label": account_label,
            "account_identifier": account_identifier,
            "query": query,
            "mime_types": mime_types or [],
            "since": since,
            "page_token": page_token,
            "processing": processing,
            "max_records": max_records,
            "cursor_name": cursor_name,
            "include_content": include_content,
            "api_base_url": api_base_url,
        }
        self.google_drive_sync_calls.append(call)
        account_id = source_account_id or "sacct_google_drive_test"
        return {
            "source_account_id": account_id,
            "source": "google-drive",
            "status": "complete",
            "processing": processing,
            "received": 1,
            "queued": 0 if processing == "sync" else 1,
            "saved": 1 if processing == "sync" else 0,
            "skipped": 0,
            "failed": 0,
            "archived_missing": 0,
            "capture_ids": ["cap_google_drive_test"],
            "records": [
                {
                    "capture_id": "cap_google_drive_test",
                    "status": "saved" if processing == "sync" else "queued",
                    "source": "google-drive",
                    "source_url": "https://drive.google.com/file/d/file_123/view",
                    "title": "Drive Test doc",
                }
            ],
            "errors": [],
            "cursor": {
                "id": "sync_google_drive_test",
                "user_id": user_id,
                "source_account_id": account_id,
                "source": "google-drive",
                "cursor_name": cursor_name,
                "cursor_value": "page-next",
                "high_water_mark": "2026-01-01T00:00:00Z",
                "state": {"records_returned": 1, "mime_types": mime_types or []},
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            "source_account": {
                "id": account_id,
                "user_id": user_id,
                "source": "google-drive",
                "account_label": account_label or "Google Drive",
                "account_identifier": account_identifier or "drive-local",
                "connection_type": "api_token",
                "status": "connected",
                "auth_state": "healthy",
                "policy": {"review_required": True, "allow_ai_context": True},
                "metadata": {"records_returned": 1, "token_configured": True},
                "last_sync_at": "2026-01-01T00:00:00Z",
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "disconnected_at": None,
            },
            "sync": {
                "connector": "google-drive",
                "connector_version": "test",
                "records_found": 1,
                "records_returned": 1,
                "errors": [],
            },
        }

    def sync_outlook_account(
        self,
        user_id: str,
        *,
        access_token: str,
        source_account_id: str | None = None,
        account_label: str | None = None,
        account_identifier: str | None = None,
        query: str | None = None,
        since: str | None = None,
        page_token: str | None = None,
        processing: str = "sync",
        max_records: int = 50,
        cursor_name: str = "messages",
        include_body: bool = True,
        api_base_url: str | None = None,
        complete_snapshot: bool = False
    ) -> dict:
        if not access_token:
            raise ValueError("Outlook access token is required")
        call = {
            "user_id": user_id,
            "complete_snapshot": complete_snapshot,
            "access_token": access_token,
            "source_account_id": source_account_id,
            "account_label": account_label,
            "account_identifier": account_identifier,
            "query": query,
            "since": since,
            "page_token": page_token,
            "processing": processing,
            "max_records": max_records,
            "cursor_name": cursor_name,
            "include_body": include_body,
            "api_base_url": api_base_url,
        }
        self.outlook_sync_calls.append(call)
        account_id = source_account_id or "sacct_outlook_test"
        return {
            "source_account_id": account_id,
            "source": "outlook",
            "status": "complete",
            "processing": processing,
            "received": 1,
            "queued": 0 if processing == "sync" else 1,
            "saved": 1 if processing == "sync" else 0,
            "skipped": 0,
            "failed": 0,
            "archived_missing": 0,
            "capture_ids": ["cap_outlook_test"],
            "records": [
                {
                    "capture_id": "cap_outlook_test",
                    "status": "saved" if processing == "sync" else "queued",
                    "source": "outlook",
                    "source_url": "outlook://message/msg_123",
                    "title": "Outlook Test message",
                }
            ],
            "errors": [],
            "cursor": {
                "id": "sync_outlook_test",
                "user_id": user_id,
                "source_account_id": account_id,
                "source": "outlook",
                "cursor_name": cursor_name,
                "cursor_value": "page-next",
                "high_water_mark": "2026-01-01T00:00:00Z",
                "state": {"records_returned": 1},
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            "source_account": {
                "id": account_id,
                "user_id": user_id,
                "source": "outlook",
                "account_label": account_label or "Outlook",
                "account_identifier": account_identifier or "outlook-local",
                "connection_type": "api_token",
                "status": "connected",
                "auth_state": "healthy",
                "policy": {"review_required": True, "allow_ai_context": True},
                "metadata": {"records_returned": 1, "token_configured": True},
                "last_sync_at": "2026-01-01T00:00:00Z",
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "disconnected_at": None,
            },
            "sync": {
                "connector": "outlook",
                "connector_version": "test",
                "records_found": 1,
                "records_returned": 1,
                "errors": [],
            },
        }

    def sync_slack_account(
        self,
        user_id: str,
        *,
        token: str,
        channels: list[str],
        source_account_id: str | None = None,
        account_label: str | None = None,
        account_identifier: str | None = None,
        since: str | None = None,
        processing: str = "sync",
        max_records: int = 100,
        cursor_name: str = "messages",
        workspace_url: str | None = None,
        api_base_url: str | None = None,
        complete_snapshot: bool = False
    ) -> dict:
        if not token:
            raise ValueError("Slack token is required")
        if not channels:
            raise ValueError("At least one Slack channel ID is required")
        call = {
            "user_id": user_id,
            "complete_snapshot": complete_snapshot,
            "token": token,
            "channels": channels,
            "source_account_id": source_account_id,
            "account_label": account_label,
            "account_identifier": account_identifier,
            "since": since,
            "processing": processing,
            "max_records": max_records,
            "cursor_name": cursor_name,
            "workspace_url": workspace_url,
            "api_base_url": api_base_url,
        }
        self.slack_sync_calls.append(call)
        account_id = source_account_id or "sacct_slack_test"
        channel = channels[0]
        channel_id = channel.split("|", 1)[0]
        return {
            "source_account_id": account_id,
            "source": "slack",
            "status": "complete",
            "processing": processing,
            "received": 1,
            "queued": 0 if processing == "sync" else 1,
            "saved": 1 if processing == "sync" else 0,
            "skipped": 0,
            "failed": 0,
            "archived_missing": 0,
            "capture_ids": ["cap_slack_test"],
            "records": [
                {
                    "capture_id": "cap_slack_test",
                    "status": "saved" if processing == "sync" else "queued",
                    "source": "slack",
                    "source_url": f"https://doppl.slack.com/archives/{channel_id}/p1782739200000100",
                    "title": f"Slack {channel_id}",
                }
            ],
            "errors": [],
            "cursor": {
                "id": "sync_slack_test",
                "user_id": user_id,
                "source_account_id": account_id,
                "source": "slack",
                "cursor_name": cursor_name,
                "cursor_value": "2026-06-29T13:20:00Z",
                "high_water_mark": "2026-06-29T13:20:00Z",
                "state": {"channels": channels, "records_returned": 1},
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            "source_account": {
                "id": account_id,
                "user_id": user_id,
                "source": "slack",
                "account_label": account_label or f"Slack: {channel}",
                "account_identifier": account_identifier or channel,
                "connection_type": "api-token",
                "status": "connected",
                "auth_state": "healthy",
                "policy": {"review_required": True, "allow_ai_context": True},
                "metadata": {"channels": channels, "records_returned": 1, "token_configured": True},
                "last_sync_at": "2026-01-01T00:00:00Z",
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "disconnected_at": None,
            },
            "sync": {
                "connector": "slack",
                "connector_version": "test",
                "channels": [{"id": channel_id, "name": "general"}],
                "records_found": 1,
                "records_returned": 1,
                "errors": [],
            },
        }

    def sync_readwise_account(
        self,
        user_id: str,
        *,
        token: str,
        source_account_id: str | None = None,
        account_label: str | None = None,
        account_identifier: str | None = None,
        since: str | None = None,
        page_cursor: str | None = None,
        processing: str = "sync",
        max_records: int = 100,
        cursor_name: str = "highlights",
        api_base_url: str | None = None,
        complete_snapshot: bool = False
    ) -> dict:
        if not token:
            raise ValueError("Readwise token is required")
        call = {
            "user_id": user_id,
            "complete_snapshot": complete_snapshot,
            "token": token,
            "source_account_id": source_account_id,
            "account_label": account_label,
            "account_identifier": account_identifier,
            "since": since,
            "page_cursor": page_cursor,
            "processing": processing,
            "max_records": max_records,
            "cursor_name": cursor_name,
            "api_base_url": api_base_url,
        }
        self.readwise_sync_calls.append(call)
        account_id = source_account_id or "sacct_readwise_test"
        return {
            "source_account_id": account_id,
            "source": "readwise",
            "status": "complete",
            "processing": processing,
            "received": 1,
            "queued": 0 if processing == "sync" else 1,
            "saved": 1 if processing == "sync" else 0,
            "skipped": 0,
            "failed": 0,
            "archived_missing": 0,
            "capture_ids": ["cap_readwise_test"],
            "records": [
                {
                    "capture_id": "cap_readwise_test",
                    "status": "saved" if processing == "sync" else "queued",
                    "source": "readwise",
                    "source_url": "https://readwise.io/bookreview/111",
                    "title": "Readwise Test highlight",
                }
            ],
            "errors": [],
            "cursor": {
                "id": "sync_readwise_test",
                "user_id": user_id,
                "source_account_id": account_id,
                "source": "readwise",
                "cursor_name": cursor_name,
                "cursor_value": "2026-06-30T10:00:00Z",
                "high_water_mark": "2026-06-30T10:00:00Z",
                "state": {"records_returned": 1},
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            "source_account": {
                "id": account_id,
                "user_id": user_id,
                "source": "readwise",
                "account_label": account_label or "Readwise Highlights",
                "account_identifier": account_identifier or "readwise",
                "connection_type": "api-token",
                "status": "connected",
                "auth_state": "healthy",
                "policy": {"review_required": True, "allow_ai_context": True},
                "metadata": {"records_returned": 1, "token_configured": True},
                "last_sync_at": "2026-01-01T00:00:00Z",
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "disconnected_at": None,
            },
            "sync": {
                "connector": "readwise",
                "connector_version": "test",
                "records_found": 1,
                "records_returned": 1,
                "errors": [],
            },
        }

    def sync_calendar_account(
        self,
        user_id: str,
        *,
        ics_path: str | None = None,
        feed_url: str | None = None,
        source_account_id: str | None = None,
        account_label: str | None = None,
        account_identifier: str | None = None,
        since: str | None = None,
        processing: str = "sync",
        max_records: int = 100,
        cursor_name: str = "events",
        complete_snapshot: bool = False
    ) -> dict:
        if bool(ics_path) == bool(feed_url):
            raise ValueError("Provide exactly one of ics_path or feed_url")
        call = {
            "user_id": user_id,
            "complete_snapshot": complete_snapshot,
            "ics_path": ics_path,
            "feed_url": feed_url,
            "source_account_id": source_account_id,
            "account_label": account_label,
            "account_identifier": account_identifier,
            "since": since,
            "processing": processing,
            "max_records": max_records,
            "cursor_name": cursor_name,
        }
        self.calendar_sync_calls.append(call)
        account_id = source_account_id or "sacct_calendar_test"
        return {
            "source_account_id": account_id,
            "source": "calendar",
            "status": "complete",
            "processing": processing,
            "received": 1,
            "queued": 0 if processing == "sync" else 1,
            "saved": 1 if processing == "sync" else 0,
            "skipped": 0,
            "failed": 0,
            "archived_missing": 0,
            "capture_ids": ["cap_calendar_test"],
            "records": [
                {
                    "capture_id": "cap_calendar_test",
                    "status": "saved" if processing == "sync" else "queued",
                    "source": "calendar",
                    "source_url": "source-account://calendar/sacct_calendar_test/calendar:event:event-1",
                    "title": "Calendar Test event",
                }
            ],
            "errors": [],
            "cursor": {
                "id": "sync_calendar_test",
                "user_id": user_id,
                "source_account_id": account_id,
                "source": "calendar",
                "cursor_name": cursor_name,
                "cursor_value": "20260701T160000Z",
                "high_water_mark": "20260701T160000Z",
                "state": {"records_returned": 1},
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            "source_account": {
                "id": account_id,
                "user_id": user_id,
                "source": "calendar",
                "account_label": account_label or "Calendar",
                "account_identifier": account_identifier or "local_file:calendar.ics",
                "connection_type": "local-file" if ics_path else "calendar-feed",
                "status": "connected",
                "auth_state": "healthy",
                "policy": {"review_required": True, "allow_ai_context": True},
                "metadata": {"records_returned": 1, "path_redacted": True, "feed_url_redacted": True},
                "last_sync_at": "2026-01-01T00:00:00Z",
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "disconnected_at": None,
            },
            "sync": {
                "connector": "calendar",
                "connector_version": "test",
                "records_found": 1,
                "records_returned": 1,
                "errors": [],
                "input_type": "local_file" if ics_path else "feed",
            },
        }

    def sync_raindrop_account(
        self,
        user_id: str,
        *,
        token: str,
        collection_id: str = "0",
        source_account_id: str | None = None,
        account_label: str | None = None,
        account_identifier: str | None = None,
        since: str | None = None,
        page: str | None = None,
        processing: str = "sync",
        max_records: int = 100,
        cursor_name: str = "raindrops",
        include_highlights: bool = True,
        api_base_url: str | None = None,
        complete_snapshot: bool = False
    ) -> dict:
        if not token:
            raise ValueError("Raindrop token is required")
        call = {
            "user_id": user_id,
            "complete_snapshot": complete_snapshot,
            "token": token,
            "collection_id": collection_id,
            "source_account_id": source_account_id,
            "account_label": account_label,
            "account_identifier": account_identifier,
            "since": since,
            "page": page,
            "processing": processing,
            "max_records": max_records,
            "cursor_name": cursor_name,
            "include_highlights": include_highlights,
            "api_base_url": api_base_url,
        }
        self.raindrop_sync_calls.append(call)
        account_id = source_account_id or "sacct_raindrop_test"
        return {
            "source_account_id": account_id,
            "source": "raindrop",
            "status": "complete",
            "processing": processing,
            "received": 1,
            "queued": 0 if processing == "sync" else 1,
            "saved": 1 if processing == "sync" else 0,
            "skipped": 0,
            "failed": 0,
            "archived_missing": 0,
            "capture_ids": ["cap_raindrop_test"],
            "records": [
                {
                    "capture_id": "cap_raindrop_test",
                    "status": "saved" if processing == "sync" else "queued",
                    "source": "raindrop",
                    "source_url": "https://example.com/raindrop",
                    "title": "Raindrop Test bookmark",
                }
            ],
            "errors": [],
            "cursor": {
                "id": "sync_raindrop_test",
                "user_id": user_id,
                "source_account_id": account_id,
                "source": "raindrop",
                "cursor_name": cursor_name,
                "cursor_value": "2026-06-30T10:00:00Z",
                "high_water_mark": "2026-06-30T10:00:00Z",
                "state": {"records_returned": 1},
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            "source_account": {
                "id": account_id,
                "user_id": user_id,
                "source": "raindrop",
                "account_label": account_label or "Raindrop Bookmarks",
                "account_identifier": account_identifier or f"collection:{collection_id}",
                "connection_type": "api-token",
                "status": "connected",
                "auth_state": "healthy",
                "policy": {"review_required": True, "allow_ai_context": True},
                "metadata": {"records_returned": 1, "token_configured": True},
                "last_sync_at": "2026-01-01T00:00:00Z",
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "disconnected_at": None,
            },
            "sync": {
                "connector": "raindrop",
                "connector_version": "test",
                "records_found": 1,
                "records_returned": 1,
                "errors": [],
            },
        }

    def sync_zotero_account(
        self,
        user_id: str,
        *,
        token: str | None = None,
        library_type: str = "user",
        library_id: str = "0",
        source_account_id: str | None = None,
        account_label: str | None = None,
        account_identifier: str | None = None,
        since: str | None = None,
        cursor: str | None = None,
        processing: str = "sync",
        max_records: int = 100,
        cursor_name: str = "items",
        include_attachments: bool = False,
        api_base_url: str | None = None,
        complete_snapshot: bool = False
    ) -> dict:
        call = {
            "user_id": user_id,
            "complete_snapshot": complete_snapshot,
            "token": token,
            "library_type": library_type,
            "library_id": library_id,
            "source_account_id": source_account_id,
            "account_label": account_label,
            "account_identifier": account_identifier,
            "since": since,
            "cursor": cursor,
            "processing": processing,
            "max_records": max_records,
            "cursor_name": cursor_name,
            "include_attachments": include_attachments,
            "api_base_url": api_base_url,
        }
        self.zotero_sync_calls.append(call)
        account_id = source_account_id or "sacct_zotero_test"
        return {
            "source_account_id": account_id,
            "source": "zotero",
            "status": "complete",
            "processing": processing,
            "received": 1,
            "queued": 0 if processing == "sync" else 1,
            "saved": 1 if processing == "sync" else 0,
            "skipped": 0,
            "failed": 0,
            "archived_missing": 0,
            "capture_ids": ["cap_zotero_test"],
            "records": [
                {
                    "capture_id": "cap_zotero_test",
                    "status": "saved" if processing == "sync" else "queued",
                    "source": "zotero",
                    "source_url": "zotero://select/library/items/ZTITEM1",
                    "title": "Zotero Test item",
                }
            ],
            "errors": [],
            "cursor": {
                "id": "sync_zotero_test",
                "user_id": user_id,
                "source_account_id": account_id,
                "source": "zotero",
                "cursor_name": cursor_name,
                "cursor_value": "42",
                "high_water_mark": "42",
                "state": {"records_returned": 1},
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            "source_account": {
                "id": account_id,
                "user_id": user_id,
                "source": "zotero",
                "account_label": account_label or "Zotero Library",
                "account_identifier": account_identifier or f"{library_type}:{library_id}",
                "connection_type": "local-api" if not token else "api-token",
                "status": "connected",
                "auth_state": "healthy",
                "policy": {"review_required": True, "allow_ai_context": True},
                "metadata": {"records_returned": 1, "token_configured": bool(token), "attachment_content_imported": False},
                "last_sync_at": "2026-01-01T00:00:00Z",
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "disconnected_at": None,
            },
            "sync": {
                "connector": "zotero",
                "connector_version": "test",
                "records_found": 1,
                "records_returned": 1,
                "errors": [],
            },
        }

    def sync_linear_account(
        self,
        user_id: str,
        *,
        token: str,
        source_account_id: str | None = None,
        account_label: str | None = None,
        account_identifier: str | None = None,
        since: str | None = None,
        cursor: str | None = None,
        processing: str = "sync",
        max_records: int = 100,
        cursor_name: str = "issues",
        api_url: str | None = None,
        complete_snapshot: bool = False
    ) -> dict:
        if not token:
            raise ValueError("Linear token is required")
        call = {
            "user_id": user_id,
            "complete_snapshot": complete_snapshot,
            "token": token,
            "source_account_id": source_account_id,
            "account_label": account_label,
            "account_identifier": account_identifier,
            "since": since,
            "cursor": cursor,
            "processing": processing,
            "max_records": max_records,
            "cursor_name": cursor_name,
            "api_url": api_url,
        }
        self.linear_sync_calls.append(call)
        account_id = source_account_id or "sacct_linear_test"
        return {
            "source_account_id": account_id,
            "source": "linear",
            "status": "complete",
            "processing": processing,
            "received": 1,
            "queued": 0 if processing == "sync" else 1,
            "saved": 1 if processing == "sync" else 0,
            "skipped": 0,
            "failed": 0,
            "archived_missing": 0,
            "capture_ids": ["cap_linear_test"],
            "records": [
                {
                    "capture_id": "cap_linear_test",
                    "status": "saved" if processing == "sync" else "queued",
                    "source": "linear",
                    "source_url": "https://linear.app/doppl/issue/COR-42/test",
                    "title": "Linear Test issue",
                }
            ],
            "errors": [],
            "cursor": {
                "id": "sync_linear_test",
                "user_id": user_id,
                "source_account_id": account_id,
                "source": "linear",
                "cursor_name": cursor_name,
                "cursor_value": "2026-06-30T10:00:00Z",
                "high_water_mark": "2026-06-30T10:00:00Z",
                "state": {"records_returned": 1},
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            "source_account": {
                "id": account_id,
                "user_id": user_id,
                "source": "linear",
                "account_label": account_label or "Linear Issues",
                "account_identifier": account_identifier or "linear",
                "connection_type": "api-token",
                "status": "connected",
                "auth_state": "healthy",
                "policy": {"review_required": True, "allow_ai_context": True},
                "metadata": {"records_returned": 1, "token_configured": True},
                "last_sync_at": "2026-01-01T00:00:00Z",
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "disconnected_at": None,
            },
            "sync": {
                "connector": "linear",
                "connector_version": "test",
                "records_found": 1,
                "records_returned": 1,
                "errors": [],
            },
        }

    def sync_jira_account(
        self,
        user_id: str,
        *,
        email: str,
        api_token: str,
        site_url: str,
        source_account_id: str | None = None,
        account_label: str | None = None,
        account_identifier: str | None = None,
        jql: str | None = None,
        since: str | None = None,
        page_token: str | None = None,
        processing: str = "sync",
        max_records: int = 100,
        cursor_name: str = "issues",
        complete_snapshot: bool = False
    ) -> dict:
        if not email:
            raise ValueError("Jira email is required")
        if not api_token:
            raise ValueError("Jira API token is required")
        if not site_url:
            raise ValueError("Jira site_url is required")
        call = {
            "user_id": user_id,
            "complete_snapshot": complete_snapshot,
            "email": email,
            "api_token": api_token,
            "site_url": site_url,
            "source_account_id": source_account_id,
            "account_label": account_label,
            "account_identifier": account_identifier,
            "jql": jql,
            "since": since,
            "page_token": page_token,
            "processing": processing,
            "max_records": max_records,
            "cursor_name": cursor_name,
        }
        self.jira_sync_calls.append(call)
        account_id = source_account_id or "sacct_jira_test"
        return {
            "source_account_id": account_id,
            "source": "jira",
            "status": "complete",
            "processing": processing,
            "received": 1,
            "queued": 0 if processing == "sync" else 1,
            "saved": 1 if processing == "sync" else 0,
            "skipped": 0,
            "failed": 0,
            "archived_missing": 0,
            "capture_ids": ["cap_jira_test"],
            "records": [
                {
                    "capture_id": "cap_jira_test",
                    "status": "saved" if processing == "sync" else "queued",
                    "source": "jira",
                    "source_url": "https://doppl.atlassian.net/browse/COR-42",
                    "title": "Jira Test issue",
                }
            ],
            "errors": [],
            "cursor": {
                "id": "sync_jira_test",
                "user_id": user_id,
                "source_account_id": account_id,
                "source": "jira",
                "cursor_name": cursor_name,
                "cursor_value": "2026-06-30T10:00:00Z",
                "high_water_mark": "2026-06-30T10:00:00Z",
                "state": {"records_returned": 1},
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            "source_account": {
                "id": account_id,
                "user_id": user_id,
                "source": "jira",
                "account_label": account_label or "Jira Issues",
                "account_identifier": account_identifier or "doppl.atlassian.net",
                "connection_type": "api-token",
                "status": "connected",
                "auth_state": "healthy",
                "policy": {"review_required": True, "allow_ai_context": True},
                "metadata": {"records_returned": 1, "api_token_configured": True},
                "last_sync_at": "2026-01-01T00:00:00Z",
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "disconnected_at": None,
            },
            "sync": {
                "connector": "jira",
                "connector_version": "test",
                "records_found": 1,
                "records_returned": 1,
                "errors": [],
            },
        }

    def sync_notion_account(
        self,
        user_id: str,
        *,
        token: str,
        source_account_id: str | None = None,
        account_label: str | None = None,
        account_identifier: str | None = None,
        since: str | None = None,
        cursor: str | None = None,
        processing: str = "sync",
        max_records: int = 50,
        cursor_name: str = "pages",
        include_content: bool = True,
        api_base_url: str | None = None,
        notion_version: str | None = None,
        complete_snapshot: bool = False
    ) -> dict:
        if not token:
            raise ValueError("Notion token is required")
        call = {
            "user_id": user_id,
            "complete_snapshot": complete_snapshot,
            "token": token,
            "source_account_id": source_account_id,
            "account_label": account_label,
            "account_identifier": account_identifier,
            "since": since,
            "cursor": cursor,
            "processing": processing,
            "max_records": max_records,
            "cursor_name": cursor_name,
            "include_content": include_content,
            "api_base_url": api_base_url,
            "notion_version": notion_version,
        }
        self.notion_sync_calls.append(call)
        account_id = source_account_id or "sacct_notion_test"
        return {
            "source_account_id": account_id,
            "source": "notion",
            "status": "complete",
            "processing": processing,
            "received": 1,
            "queued": 0 if processing == "sync" else 1,
            "saved": 1 if processing == "sync" else 0,
            "skipped": 0,
            "failed": 0,
            "archived_missing": 0,
            "capture_ids": ["cap_notion_test"],
            "records": [
                {
                    "capture_id": "cap_notion_test",
                    "status": "saved" if processing == "sync" else "queued",
                    "source": "notion",
                    "source_url": "https://www.notion.so/doppl/page-1",
                    "title": "Notion Test page",
                }
            ],
            "errors": [],
            "cursor": {
                "id": "sync_notion_test",
                "user_id": user_id,
                "source_account_id": account_id,
                "source": "notion",
                "cursor_name": cursor_name,
                "cursor_value": "2026-06-30T10:00:00Z",
                "high_water_mark": "2026-06-30T10:00:00Z",
                "state": {"records_returned": 1},
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            "source_account": {
                "id": account_id,
                "user_id": user_id,
                "source": "notion",
                "account_label": account_label or "Notion Pages",
                "account_identifier": account_identifier or "notion",
                "connection_type": "api-token",
                "status": "connected",
                "auth_state": "healthy",
                "policy": {"review_required": True, "allow_ai_context": True},
                "metadata": {"records_returned": 1, "token_configured": True},
                "last_sync_at": "2026-01-01T00:00:00Z",
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "disconnected_at": None,
            },
            "sync": {
                "connector": "notion",
                "connector_version": "test",
                "records_found": 1,
                "records_returned": 1,
                "errors": [],
            },
        }

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

    def import_sources(self, *, user_id: str, paths: list[str], source_hint: str = "", processing: str = "async", max_records: int = 1000, offset: int = 0, auto_approve: bool = True) -> dict:
        self.import_sources_calls.append((user_id, paths, source_hint, processing, max_records, offset))
        return {
            "import_id": "imp_test",
            "status": "complete",
            "records_found": 1,
            "records_available": 1,
            "offset": offset,
            "has_more": False,
            "next_offset": None,
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
            "scopes": self.api_token_scopes,
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
        self.require_agent_access_calls.append((user_id, capability))
        if capability in self.denied_agent_access:
            raise PermissionError(f"Cortex agent {capability} actions are disabled")

    def agent_payload(self, user_id: str, value):
        return value

    def record_agent_event(self, user_id: str, tool_name: str, args: dict, *, success: bool, error: str | None = None, token: dict | None = None, result=None) -> None:
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
        # Fresh guards per test so one test's rate-limit spend cannot leak into the next.
        self.original_guards = standalone_server.REQUEST_GUARDS
        standalone_server.REQUEST_GUARDS = standalone_server._RequestGuards()
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
        standalone_server.REQUEST_GUARDS = self.original_guards
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

    def get_raw(self, path: str):
        # Like get(), but never raises on 4xx/5xx: returns (status, headers, body) so guard
        # tests can inspect rejected responses alongside accepted ones.
        req = request.Request(self.base_url + path, headers={"Authorization": "Bearer test-token"})
        try:
            with request.urlopen(req, timeout=5) as response:
                return response.status, response.headers, response.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            exc.close()
            return exc.code, exc.headers, body

    def test_github_discover_endpoint_present_on_shipping_server(self) -> None:
        # The macOS "Find Repositories" button POSTs here; the shipping server must implement it
        # (it previously 404'd because only the FastAPI dev server had the route).
        class _Discovery:
            def to_summary(self):
                return {"repositories": [{"full_name": "doppl-tech/cortex-app"}], "total": 1}

        with mock.patch("backend.app.connectors.github.discover_github_repositories", return_value=_Discovery()) as disc:
            with self.post_json("/v1/connectors/github/discover", {"token": "ghp_x", "limit": 50}) as response:
                self.assertEqual(response.status, 200)
                payload = json.loads(response.read())
        self.assertEqual(payload["repositories"][0]["full_name"], "doppl-tech/cortex-app")
        self.assertEqual(disc.call_args.kwargs["token"], "ghp_x")
        self.assertEqual(disc.call_args.kwargs["limit"], 50)

    def test_slack_discover_endpoint_present_on_shipping_server(self) -> None:
        class _Discovery:
            def to_summary(self):
                return {"channels": [{"id": "C1", "name": "general"}]}

        with mock.patch("backend.app.connectors.slack.discover_slack_channels", return_value=_Discovery()) as disc:
            with self.post_json("/v1/connectors/slack/discover", {"token": "xoxb-x"}) as response:
                self.assertEqual(response.status, 200)
                payload = json.loads(response.read())
        self.assertEqual(payload["channels"][0]["name"], "general")
        self.assertEqual(disc.call_args.kwargs["token"], "xoxb-x")

    def test_malformed_json_body_returns_422_not_500(self) -> None:
        headers = {"Authorization": "Bearer test-token", "Content-Type": "application/json"}
        req = request.Request(
            self.base_url + "/v1/connectors/github/discover",
            data=b"{not valid json",
            headers=headers,
            method="POST",
        )
        with self.assertRaises(error.HTTPError) as ctx:
            request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 422)

    def test_oversized_request_body_returns_413(self) -> None:
        headers = {"Authorization": "Bearer test-token", "Content-Type": "application/json"}
        with mock.patch.object(standalone_server, "MAX_REQUEST_BODY_BYTES", 100):
            data = b'{"blob":"' + b"a" * 500 + b'"}'
            req = request.Request(
                self.base_url + "/v1/connectors/github/discover",
                data=data,
                headers=headers,
                method="POST",
            )
            with self.assertRaises(error.HTTPError) as ctx:
                request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 413)

    def test_standalone_worker_enabled_only_for_local_inline_mode(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            standalone_server.settings = Settings(
                vault_path=Path(self.tmp.name) / "vault",
                db_path=Path(self.tmp.name) / "index.sqlite",
                api_key="test-token",
                public_base_url="http://127.0.0.1:8766",
                shard_mode="local",
                worker_mode="inline",
            )
            self.assertTrue(standalone_server._standalone_worker_enabled())

            standalone_server.settings = Settings(
                vault_path=Path(self.tmp.name) / "vault",
                db_path=Path(self.tmp.name) / "index.sqlite",
                api_key="test-token",
                public_base_url="http://127.0.0.1:8766",
                shard_mode="local",
                worker_mode="external",
            )
            self.assertFalse(standalone_server._standalone_worker_enabled())

        with mock.patch.dict(os.environ, {"CORTEX_STANDALONE_WORKER_ENABLED": "0"}):
            self.assertFalse(standalone_server._standalone_worker_enabled())
        with mock.patch.dict(os.environ, {"CORTEX_STANDALONE_WORKER_ENABLED": "1"}):
            self.assertTrue(standalone_server._standalone_worker_enabled())

    def test_standalone_worker_tick_drains_memory_jobs_without_source_scheduling(self):
        result = standalone_server._run_standalone_worker_tick(limit=7)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(self.fake_store.job_run_calls[-1], ("local", 7, "standalone-local-worker", False))
        self.assertIsNone(result["scheduled_source_syncs"])

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
        self.assertEqual(
            self.fake_store.search_calls,
            [
                {
                    "user_id": "local",
                    "query": "voice",
                    "limit": 7,
                    "kind": "style",
                    "layer": "style",
                    "sector": None,
                    "source": None,
                    "source_account_id": None,
                    "as_of": None,
                    "metadata_filters": {"repository": None, "channel": None, "record_scope": None, "state": None, "project": None},
                }
            ],
        )

    def test_search_forwards_sector_to_store(self) -> None:
        with self.get("/v1/search?query=release&sector=Project%20Atlas&limit=4") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["sector"], "Project Atlas")
        self.assertEqual(self.fake_store.search_calls[-1]["sector"], "Project Atlas")

    def test_search_forwards_source_scope_to_store(self) -> None:
        with self.get("/v1/search?query=review&source=github&source_account_id=sacct_1&repository=doppl-tech/cortex-app&record_scope=pull_request&state=open&limit=3") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertTrue(payload["results"])
        self.assertEqual(self.fake_store.search_calls[-1]["source"], "github")
        self.assertEqual(self.fake_store.search_calls[-1]["source_account_id"], "sacct_1")
        self.assertEqual(
            self.fake_store.search_calls[-1]["metadata_filters"],
            {"repository": "doppl-tech/cortex-app", "channel": None, "record_scope": "pull_request", "state": "open", "project": None},
        )

    def test_search_forwards_as_of_validity_filter_to_store(self) -> None:
        with self.get("/v1/search?query=release&as_of=2019-12-31&limit=4") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertTrue(payload["results"])
        self.assertEqual(self.fake_store.search_calls[-1]["as_of"], "2019-12-31")

    def test_ask_route_forwards_to_store(self) -> None:
        with self.get("/v1/ask?query=voice&limit=2") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertIn("cited memory", payload["answer"])
        self.assertEqual(payload["citations"][0]["source_url"], "/tmp/source.md")
        self.assertEqual(self.fake_store.answer_calls[-1]["query"], "voice")
        self.assertEqual(self.fake_store.answer_calls[-1]["limit"], 2)

    def test_ask_route_forwards_as_of_validity_filter_to_store(self) -> None:
        with self.get("/v1/ask?query=voice&as_of=2019-12-31&limit=2") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertIn("cited memory", payload["answer"])
        self.assertEqual(self.fake_store.answer_calls[-1]["as_of"], "2019-12-31")

    def test_cors_does_not_allow_arbitrary_origin(self) -> None:
        with self.get("/v1/search?query=voice", origin="https://example.invalid") as response:
            headers = response.headers

        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

    def test_cors_echoes_configured_local_origin(self) -> None:
        with self.get("/v1/search?query=voice", origin="http://localhost:8766") as response:
            headers = response.headers

        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "http://localhost:8766")
        self.assertEqual(headers.get("Vary"), "Origin")

    def test_health_and_ready_expose_hosted_readiness_contract(self) -> None:
        with self.get("/health") as response:
            health = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(health["mode"], "standalone")
        hosted_readiness = health["hosted_readiness"]
        self.assertEqual(hosted_readiness["status"], "ok")
        self.assertFalse(hosted_readiness["hosted_mode"])
        self.assertEqual(hosted_readiness["shard_mode"], "local")
        self.assertFalse(hosted_readiness["require_scoped_api_tokens"])
        self.assertEqual(hosted_readiness["global_token_user_switching"], "allowed_local_compatibility")
        self.assertEqual(hosted_readiness["checks"][0]["name"], "scoped_api_tokens_required")

        with self.get("/ready") as response:
            ready = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(ready["status"], "ok")
        self.assertEqual(ready["hosted_readiness"]["status"], "ok")
        self.assertEqual(ready["diagnostics"]["status"], "ok")

    def test_ready_requires_scoped_api_tokens_for_hosted_shard_modes(self) -> None:
        standalone_server.settings = Settings(
            vault_path=Path(self.tmp.name) / "vault",
            db_path=Path(self.tmp.name) / "index.sqlite",
            api_key="test-token",
            public_base_url="http://127.0.0.1:8766",
            shard_mode="bucket",
            require_scoped_api_tokens=False,
        )

        with self.get("/health") as response:
            health = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        hosted_readiness = health["hosted_readiness"]
        self.assertEqual(hosted_readiness["status"], "blocked")
        self.assertTrue(hosted_readiness["hosted_mode"])
        self.assertEqual(hosted_readiness["shard_mode"], "bucket")
        self.assertFalse(hosted_readiness["require_scoped_api_tokens"])
        self.assertEqual(hosted_readiness["global_token_user_switching"], "blocked")

        with self.assertRaises(error.HTTPError) as context:
            self.get("/ready")
        self.assertEqual(context.exception.code, 503)
        detail = json.loads(context.exception.read().decode("utf-8"))["detail"]
        self.assertEqual(detail["status"], "needs_configuration")
        hosted_readiness = detail["hosted_readiness"]
        self.assertEqual(hosted_readiness["status"], "blocked")
        self.assertEqual(hosted_readiness["checks"][0]["status"], "blocked")
        self.assertIn("CORTEX_REQUIRE_SCOPED_API_TOKENS=1", hosted_readiness["checks"][0]["detail"])

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
        self.assertEqual(self.fake_store.import_sources_calls, [("local", ["/tmp/conversations.json"], "chatgpt", "sync", 1000, 0)])

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

        with self.post("/v1/sources/sync-due?limit=7") as response:
            source_sync = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(source_sync["processed"], 1)
        self.assertEqual(self.fake_store.source_sync_run_calls, [("local", 7, "api-source-sync")])

        with self.post("/v1/jobs/run?limit=9&schedule_source_syncs=false") as response:
            general_jobs = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(general_jobs["processed"], 1)
        self.assertIsNone(general_jobs["scheduled_source_syncs"])
        self.assertEqual(self.fake_store.job_run_calls, [("local", 9, "local-worker", False)])

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

        with self.post_json(
            "/v1/source-accounts/sacct_test/sync",
            {
                "processing": "sync",
                "cursor_name": "messages",
                "cursor_value": "cursor-2",
                "high_water_mark": "2026-01-01T00:30:00Z",
                "state": {"batch": 2},
                "records": [
                    {
                        "content": "I decided Gmail sync should feed Cortex directly.",
                        "title": "Standalone Gmail sync",
                        "external_id": "msg-standalone-1",
                        "captured_at": "2026-01-01T00:30:00Z",
                    }
                ],
            },
        ) as response:
            synced = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(synced["source_account_id"], "sacct_test")
        self.assertEqual(synced["processing"], "sync")
        self.assertEqual(synced["saved"], 1)
        self.assertTrue(synced["records"][0]["source_url"].startswith("source-account://gmail/sacct_test/msg-standalone-1"))
        self.assertEqual(synced["cursor"]["cursor_value"], "cursor-2")
        self.assertEqual(self.fake_store.source_account_sync_calls, [("local", "sacct_test", 1, "sync", False)])

        with self.assertRaises(error.HTTPError) as context:
            self.post_json(
                "/v1/source-accounts/sacct_test/sync",
                {
                    "processing": "sync",
                    "cursor_name": "messages",
                    "archive_missing": True,
                    "records": [{"content": "Partial standalone page.", "external_id": "partial"}],
                },
            )
        self.assertEqual(context.exception.code, 422)
        self.assertIn("complete_snapshot", context.exception.read().decode("utf-8"))
        self.assertEqual(self.fake_store.source_account_sync_calls, [("local", "sacct_test", 1, "sync", False)])

        with self.post_json(
            "/v1/source-accounts/sacct_test/sync",
            {
                "processing": "sync",
                "cursor_name": "messages",
                "archive_missing": True,
                "complete_snapshot": True,
                "records": [{"content": "Complete standalone snapshot.", "external_id": "complete"}],
            },
        ) as response:
            complete = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 200)
        self.assertEqual(complete["archived_missing"], 1)
        self.assertEqual(
            self.fake_store.source_account_sync_calls,
            [("local", "sacct_test", 1, "sync", False), ("local", "sacct_test", 1, "sync", True)],
        )

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

        with self.assertRaises(error.HTTPError) as context:
            self.post_json("/v1/source-accounts/sacct_missing/sync", {"records": [{"content": "Missing account"}]})
        self.assertEqual(context.exception.code, 422)

        with self.post_json("/v1/source-accounts/sacct_test/disconnect", {}) as response:
            disconnected = json.loads(response.read().decode("utf-8"))
        self.assertEqual(disconnected["status"], "disconnected")
        self.assertEqual(disconnected["retention"]["disconnect_action"], "pause_sync")
        self.assertIn("memories", disconnected["retention"]["disconnect_retains"])

        with self.assertRaises(error.HTTPError) as context:
            self.post_json("/v1/source-accounts/sacct_missing/disconnect", {})
        self.assertEqual(context.exception.code, 404)

        with self.post_json("/v1/source-accounts/sacct_test/resume", {}) as response:
            resumed = json.loads(response.read().decode("utf-8"))
        self.assertEqual(resumed["status"], "connected")
        self.assertIsNone(resumed["disconnected_at"])

        with self.assertRaises(error.HTTPError) as context:
            self.post_json("/v1/source-accounts/sacct_missing/resume", {})
        self.assertEqual(context.exception.code, 404)

        with self.delete("/v1/source-accounts/sacct_test") as response:
            legacy_disconnected = json.loads(response.read().decode("utf-8"))
        self.assertEqual(legacy_disconnected["retention"]["delete_action"], "delete_user_data")

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

    def test_obsidian_connector_route_forwards_to_store(self) -> None:
        vault_path = str(Path(self.tmp.name) / "Notes")
        with self.post_json(
            "/v1/connectors/obsidian/sync",
            {
                "vault_path": vault_path,
                "source_account_id": "sacct_obsidian_existing",
                "account_label": "Work Notes",
                "account_identifier": "work-vault",
                "processing": "sync",
                "max_records": 5000,
                "cursor_name": "local-folder",
            },
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["source"], "obsidian")
        self.assertEqual(payload["source_account_id"], "sacct_obsidian_existing")
        self.assertEqual(payload["processing"], "sync")
        self.assertEqual(payload["source_account"]["account_label"], "Work Notes")
        self.assertEqual(payload["scan"]["vault_path"], vault_path)
        self.assertEqual(payload["scan"]["records_returned"], 1)
        self.assertEqual(
            self.fake_store.obsidian_sync_calls,
            [
                {
                    "user_id": "local",
                    "vault_path": vault_path,
                    "source_account_id": "sacct_obsidian_existing",
                    "account_label": "Work Notes",
                    "account_identifier": "work-vault",
                    "processing": "sync",
                    "max_records": 5000,
                    "cursor_name": "local-folder",
                }
            ],
        )

        with self.assertRaises(error.HTTPError) as context:
            self.post_json(
                "/v1/connectors/obsidian/sync",
                {
                    "vault_path": vault_path,
                    "max_records": 5001,
                },
            )
        self.assertEqual(context.exception.code, 422)
        self.assertEqual(len(self.fake_store.obsidian_sync_calls), 1)

    def test_github_connector_route_forwards_to_store(self) -> None:
        with self.post_json(
            "/v1/connectors/github/sync",
            {
                "token": "ghp_test",
                "repositories": ["doppl-tech/cortex-app"],
                "source_account_id": "sacct_github_existing",
                "account_label": "Cortex GitHub",
                "account_identifier": "doppl-tech/cortex-app",
                "since": "2026-01-01T00:00:00Z",
                "processing": "sync",
                "max_records": 50,
                "include_comments": True,
                "max_comments_per_item": 7,
                "cursor_name": "issues",
                "api_base_url": "https://api.github.test",
            },
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["source"], "github")
        self.assertEqual(payload["source_account_id"], "sacct_github_existing")
        self.assertEqual(payload["processing"], "sync")
        self.assertEqual(payload["source_account"]["account_label"], "Cortex GitHub")
        self.assertEqual(payload["records"][0]["source_url"], "https://github.com/doppl-tech/cortex-app/issues/42")
        self.assertEqual(
            self.fake_store.github_sync_calls,
            [
                {
                    "complete_snapshot": False,
                    "user_id": "local",
                    "token": "ghp_test",
                    "repositories": ["doppl-tech/cortex-app"],
                    "source_account_id": "sacct_github_existing",
                    "account_label": "Cortex GitHub",
                    "account_identifier": "doppl-tech/cortex-app",
                    "since": "2026-01-01T00:00:00Z",
                    "processing": "sync",
                    "max_records": 50,
                    "include_comments": True,
                    "max_comments_per_item": 7,
                    "cursor_name": "issues",
                    "api_base_url": "https://api.github.test",
                }
            ],
        )

        with self.assertRaises(error.HTTPError) as context:
            self.post_json(
                "/v1/connectors/github/sync",
                {
                    "token": "ghp_test",
                    "repositories": ["doppl-tech/cortex-app"],
                    "max_records": 501,
                },
            )
        self.assertEqual(context.exception.code, 422)
        self.assertEqual(len(self.fake_store.github_sync_calls), 1)

        with self.assertRaises(error.HTTPError) as context:
            self.post_json(
                "/v1/connectors/github/sync",
                {
                    "token": "ghp_test",
                    "repositories": ["doppl-tech/cortex-app"],
                    "max_comments_per_item": 51,
                },
            )
        self.assertEqual(context.exception.code, 422)
        self.assertEqual(len(self.fake_store.github_sync_calls), 1)

    def test_every_connector_sync_route_forwards_complete_snapshot(self) -> None:
        # Parity regression: main.py passed complete_snapshot for every connector sync but the
        # SHIPPING standalone server silently dropped it, so a full-snapshot sync behaved as
        # incremental and stale items were never archived. Each route must forward the flag.
        cases = [
            ("github", "/v1/connectors/github/sync", {"token": "ghp_x", "repositories": ["o/r"]}),
            ("gmail", "/v1/connectors/gmail/sync", {"access_token": "t"}),
            ("google_drive", "/v1/connectors/google-drive/sync", {"access_token": "t"}),
            ("outlook", "/v1/connectors/outlook/sync", {"access_token": "t"}),
            ("slack", "/v1/connectors/slack/sync", {"token": "xoxb", "channels": ["general"]}),
            ("readwise", "/v1/connectors/readwise/sync", {"token": "t"}),
            ("calendar", "/v1/connectors/calendar/sync", {"feed_url": "https://cal.example/basic.ics"}),
            ("raindrop", "/v1/connectors/raindrop/sync", {"token": "t"}),
            ("zotero", "/v1/connectors/zotero/sync", {"token": "t", "library_id": "1"}),
            ("linear", "/v1/connectors/linear/sync", {"token": "t"}),
            ("jira", "/v1/connectors/jira/sync", {"email": "a@b.c", "api_token": "t", "site_url": "https://x.atlassian.net"}),
            ("notion", "/v1/connectors/notion/sync", {"token": "t"}),
        ]
        for name, route, payload in cases:
            with self.subTest(connector=name):
                calls = getattr(self.fake_store, f"{name}_sync_calls")
                calls.clear()
                with self.post_json(route, {**payload, "complete_snapshot": True}) as response:
                    self.assertEqual(response.status, 200)
                self.assertEqual(len(calls), 1, f"{name} route did not reach the store")
                self.assertIs(calls[0].get("complete_snapshot"), True, f"{name} dropped complete_snapshot")

    def test_gmail_connector_route_forwards_to_store(self) -> None:
        with self.post_json(
            "/v1/connectors/gmail/sync",
            {
                "access_token": "gmail_access_test",
                "source_account_id": "sacct_gmail_existing",
                "account_label": "Cortex Gmail",
                "account_identifier": "sdoven@uwaterloo.ca",
                "query": "from:founder@example.com",
                "label_ids": ["INBOX", "IMPORTANT"],
                "since": "2026-01-01T00:00:00Z",
                "page_token": "page-1",
                "processing": "sync",
                "max_records": 50,
                "cursor_name": "messages",
                "include_body": True,
                "api_base_url": "https://gmail-api.test/gmail/v1",
            },
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["source"], "gmail")
        self.assertEqual(payload["source_account_id"], "sacct_gmail_existing")
        self.assertEqual(payload["processing"], "sync")
        self.assertEqual(payload["source_account"]["account_label"], "Cortex Gmail")
        self.assertEqual(payload["records"][0]["source_url"], "gmail://message/msg_123")
        self.assertNotIn("gmail_access_test", json.dumps(payload))
        self.assertEqual(
            self.fake_store.gmail_sync_calls,
            [
                {
                    "complete_snapshot": False,
                    "user_id": "local",
                    "access_token": "gmail_access_test",
                    "source_account_id": "sacct_gmail_existing",
                    "account_label": "Cortex Gmail",
                    "account_identifier": "sdoven@uwaterloo.ca",
                    "query": "from:founder@example.com",
                    "label_ids": ["INBOX", "IMPORTANT"],
                    "since": "2026-01-01T00:00:00Z",
                    "page_token": "page-1",
                    "processing": "sync",
                    "max_records": 50,
                    "cursor_name": "messages",
                    "include_body": True,
                    "api_base_url": "https://gmail-api.test/gmail/v1",
                }
            ],
        )

        with self.assertRaises(error.HTTPError) as context:
            self.post_json(
                "/v1/connectors/gmail/sync",
                {
                    "access_token": "gmail_access_test",
                    "max_records": 201,
                },
            )
        self.assertEqual(context.exception.code, 422)
        self.assertEqual(len(self.fake_store.gmail_sync_calls), 1)

    def test_google_drive_connector_route_forwards_to_store(self) -> None:
        with self.post_json(
            "/v1/connectors/google-drive/sync",
            {
                "access_token": "drive_access_test",
                "source_account_id": "sacct_drive_existing",
                "account_label": "Cortex Drive",
                "account_identifier": "drive:sdoven",
                "query": "modifiedTime > '2026-01-01T00:00:00'",
                "mime_types": ["application/vnd.google-apps.document", "text/plain"],
                "since": "2026-01-01T00:00:00Z",
                "page_token": "page-1",
                "processing": "sync",
                "max_records": 50,
                "cursor_name": "files",
                "include_content": True,
                "api_base_url": "https://drive-api.test/drive/v3",
            },
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["source"], "google-drive")
        self.assertEqual(payload["source_account_id"], "sacct_drive_existing")
        self.assertEqual(payload["processing"], "sync")
        self.assertEqual(payload["source_account"]["account_label"], "Cortex Drive")
        self.assertEqual(payload["records"][0]["source_url"], "https://drive.google.com/file/d/file_123/view")
        self.assertNotIn("drive_access_test", json.dumps(payload))
        self.assertEqual(
            self.fake_store.google_drive_sync_calls,
            [
                {
                    "complete_snapshot": False,
                    "user_id": "local",
                    "access_token": "drive_access_test",
                    "source_account_id": "sacct_drive_existing",
                    "account_label": "Cortex Drive",
                    "account_identifier": "drive:sdoven",
                    "query": "modifiedTime > '2026-01-01T00:00:00'",
                    "mime_types": ["application/vnd.google-apps.document", "text/plain"],
                    "since": "2026-01-01T00:00:00Z",
                    "page_token": "page-1",
                    "processing": "sync",
                    "max_records": 50,
                    "cursor_name": "files",
                    "include_content": True,
                    "api_base_url": "https://drive-api.test/drive/v3",
                }
            ],
        )

        with self.assertRaises(error.HTTPError) as context:
            self.post_json(
                "/v1/connectors/google-drive/sync",
                {
                    "access_token": "drive_access_test",
                    "max_records": 201,
                },
            )
        self.assertEqual(context.exception.code, 422)
        self.assertEqual(len(self.fake_store.google_drive_sync_calls), 1)

    def test_google_oauth_start_route_forwards_pkce_setup_to_store(self) -> None:
        with self.post_json(
            "/v1/connectors/google/oauth/start",
            {
                "source": "gmail",
                "redirect_uri": "http://127.0.0.1:8766/v1/connectors/google/oauth/callback",
                "client_id": "google-client-id",
                "code_verifier": "pkce-verifier-secret",
                "code_challenge": "pkce-challenge",
                "code_challenge_method": "S256",
                "label_ids": ["INBOX"],
            },
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["source"], "gmail")
        self.assertIn("code_challenge=pkce-challenge", payload["authorization_url"])
        self.assertNotIn("pkce-verifier-secret", json.dumps(payload))
        self.assertEqual(
            self.fake_store.google_oauth_start_calls,
            [
                {
                    "source": "gmail",
                    "redirect_uri": "http://127.0.0.1:8766/v1/connectors/google/oauth/callback",
                    "state": None,
                    "client_id": "google-client-id",
                    "code_challenge": "pkce-challenge",
                    "code_challenge_method": "S256",
                    "scopes": [],
                }
            ],
        )

    def test_google_oauth_callback_completes_pending_sign_in_and_queues_sync(self) -> None:
        with self.post_json(
            "/v1/connectors/google/oauth/start",
            {
                "source": "gmail",
                "redirect_uri": "http://127.0.0.1:8766/v1/connectors/google/oauth/callback",
                "client_id": "google-client-id",
                "token_endpoint": "https://oauth2.invalid/token",
                "code_verifier": "pkce-verifier-secret",
                "code_challenge": "pkce-challenge",
                "code_challenge_method": "S256",
                "account_label": "Sarp Gmail",
                "account_identifier": "sarp@example.com",
                "query": "label:inbox",
                "label_ids": ["INBOX"],
            },
        ) as response:
            state = json.loads(response.read().decode("utf-8"))["state"]

        with request.urlopen(
            self.base_url + f"/v1/connectors/google/oauth/callback?code=google-code-secret&state={state}",
            timeout=5,
        ) as response:
            html = response.read().decode("utf-8")

        self.assertEqual(response.status, 200)
        self.assertIn("Google is connected", html)
        self.assertNotIn("google-code-secret", html)
        self.assertNotIn("pkce-verifier-secret", html)
        self.assertEqual(
            self.fake_store.google_oauth_complete_calls[-1],
            {
                "user_id": "local",
                "source": "gmail",
                "code": "google-code-secret",
                "redirect_uri": "http://127.0.0.1:8766/v1/connectors/google/oauth/callback",
                "state": state,
                "expected_state": state,
                "client_id": "google-client-id",
                "client_secret": None,
                "token_endpoint": "https://oauth2.invalid/token",
                "code_verifier": "pkce-verifier-secret",
                "source_account_id": None,
                "account_label": "Sarp Gmail",
                "account_identifier": "sarp@example.com",
                "query": "label:inbox",
                "label_ids": ["INBOX"],
                "mime_types": [],
                "include_body": True,
                "include_content": True,
            },
        )
        self.assertEqual(
            self.fake_store.source_account_sync_enqueue_calls[-1],
            {
                "user_id": "local",
                "account_id": "sacct_google_oauth_test",
                "processing": "async",
                "cursor_name": None,
                "max_records": 200,
                "run_at": None,
                "schedule_token": None,
            },
        )

    def test_managed_oauth_start_route_forwards_notion_setup_to_store(self) -> None:
        with self.post_json(
            "/v1/connectors/oauth/start",
            {
                "source": "notion",
                "redirect_uri": "http://127.0.0.1:8766/v1/connectors/oauth/callback",
                "client_id": "notion-client-id",
                "client_secret": "notion-client-secret",
                "token_endpoint": "https://oauth2.invalid/notion-token",
                "account_label": "Cortex Notion",
                "account_identifier": "notion-workspace",
                "include_content": True,
                "api_base_url": "https://api.notion.test/v1",
                "notion_version": "2026-03-11",
            },
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["source"], "notion")
        self.assertIn("owner=user", payload["authorization_url"])
        self.assertNotIn("notion-client-secret", json.dumps(payload))
        self.assertEqual(
            self.fake_store.managed_oauth_start_calls,
            [
                {
                    "source": "notion",
                    "redirect_uri": "http://127.0.0.1:8766/v1/connectors/oauth/callback",
                    "state": None,
                    "client_id": "notion-client-id",
                    "scopes": [],
                }
            ],
        )

    def test_managed_oauth_callback_completes_pending_notion_sign_in_and_queues_sync(self) -> None:
        with self.post_json(
            "/v1/connectors/oauth/start",
            {
                "source": "notion",
                "redirect_uri": "http://127.0.0.1:8766/v1/connectors/oauth/callback",
                "client_id": "notion-client-id",
                "client_secret": "notion-client-secret",
                "token_endpoint": "https://oauth2.invalid/notion-token",
                "account_label": "Cortex Notion",
                "account_identifier": "notion-workspace",
                "include_content": True,
                "api_base_url": "https://api.notion.test/v1",
                "notion_version": "2026-03-11",
            },
        ) as response:
            state = json.loads(response.read().decode("utf-8"))["state"]

        with request.urlopen(
            self.base_url + f"/v1/connectors/oauth/callback?code=notion-code-secret&state={state}",
            timeout=5,
        ) as response:
            html = response.read().decode("utf-8")

        self.assertEqual(response.status, 200)
        self.assertIn("Source is connected", html)
        self.assertNotIn("notion-code-secret", html)
        self.assertNotIn("notion-client-secret", html)
        self.assertEqual(
            self.fake_store.managed_oauth_complete_calls[-1],
            {
                "user_id": "local",
                "source": "notion",
                "code": "notion-code-secret",
                "redirect_uri": "http://127.0.0.1:8766/v1/connectors/oauth/callback",
                "state": state,
                "expected_state": state,
                "client_id": "notion-client-id",
                "client_secret": "notion-client-secret",
                "token_endpoint": "https://oauth2.invalid/notion-token",
                "source_account_id": None,
                "account_label": "Cortex Notion",
                "account_identifier": "notion-workspace",
                "include_content": True,
                "api_base_url": "https://api.notion.test/v1",
                "notion_version": "2026-03-11",
            },
        )
        self.assertEqual(
            self.fake_store.source_account_sync_enqueue_calls[-1],
            {
                "user_id": "local",
                "account_id": "sacct_managed_oauth_test",
                "processing": "async",
                "cursor_name": None,
                "max_records": 200,
                "run_at": None,
                "schedule_token": None,
            },
        )

    def test_outlook_connector_route_forwards_to_store(self) -> None:
        with self.post_json(
            "/v1/connectors/outlook/sync",
            {
                "access_token": "outlook_access_test",
                "source_account_id": "sacct_outlook_existing",
                "account_label": "Cortex Outlook",
                "account_identifier": "sdoven@uwaterloo.ca",
                "query": "from/emailAddress/address eq 'founder@example.com'",
                "since": "2026-01-01T00:00:00Z",
                "page_token": "page-1",
                "processing": "sync",
                "max_records": 50,
                "cursor_name": "messages",
                "include_body": True,
                "api_base_url": "https://graph-api.test/v1.0",
            },
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["source"], "outlook")
        self.assertEqual(payload["source_account_id"], "sacct_outlook_existing")
        self.assertEqual(payload["processing"], "sync")
        self.assertEqual(payload["source_account"]["account_label"], "Cortex Outlook")
        self.assertEqual(payload["records"][0]["source_url"], "outlook://message/msg_123")
        self.assertNotIn("outlook_access_test", json.dumps(payload))
        self.assertEqual(
            self.fake_store.outlook_sync_calls,
            [
                {
                    "complete_snapshot": False,
                    "user_id": "local",
                    "access_token": "outlook_access_test",
                    "source_account_id": "sacct_outlook_existing",
                    "account_label": "Cortex Outlook",
                    "account_identifier": "sdoven@uwaterloo.ca",
                    "query": "from/emailAddress/address eq 'founder@example.com'",
                    "since": "2026-01-01T00:00:00Z",
                    "page_token": "page-1",
                    "processing": "sync",
                    "max_records": 50,
                    "cursor_name": "messages",
                    "include_body": True,
                    "api_base_url": "https://graph-api.test/v1.0",
                }
            ],
        )

        with self.assertRaises(error.HTTPError) as context:
            self.post_json(
                "/v1/connectors/outlook/sync",
                {
                    "access_token": "outlook_access_test",
                    "max_records": 201,
                },
            )
        self.assertEqual(context.exception.code, 422)
        self.assertEqual(len(self.fake_store.outlook_sync_calls), 1)

    def test_slack_connector_route_forwards_to_store(self) -> None:
        with self.post_json(
            "/v1/connectors/slack/sync",
            {
                "token": "xoxb_test",
                "channels": ["C123ABC|general"],
                "source_account_id": "sacct_slack_existing",
                "account_label": "Cortex Slack",
                "account_identifier": "C123ABC",
                "since": "2026-01-01T00:00:00Z",
                "processing": "sync",
                "max_records": 50,
                "cursor_name": "messages",
                "workspace_url": "https://doppl.slack.com",
                "api_base_url": "https://slack-api.test",
            },
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["source"], "slack")
        self.assertEqual(payload["source_account_id"], "sacct_slack_existing")
        self.assertEqual(payload["processing"], "sync")
        self.assertEqual(payload["source_account"]["account_label"], "Cortex Slack")
        self.assertEqual(payload["records"][0]["source_url"], "https://doppl.slack.com/archives/C123ABC/p1782739200000100")
        self.assertEqual(
            self.fake_store.slack_sync_calls,
            [
                {
                    "complete_snapshot": False,
                    "user_id": "local",
                    "token": "xoxb_test",
                    "channels": ["C123ABC|general"],
                    "source_account_id": "sacct_slack_existing",
                    "account_label": "Cortex Slack",
                    "account_identifier": "C123ABC",
                    "since": "2026-01-01T00:00:00Z",
                    "processing": "sync",
                    "max_records": 50,
                    "cursor_name": "messages",
                    "workspace_url": "https://doppl.slack.com",
                    "api_base_url": "https://slack-api.test",
                }
            ],
        )

        with self.assertRaises(error.HTTPError) as context:
            self.post_json(
                "/v1/connectors/slack/sync",
                {
                    "token": "xoxb_test",
                    "channels": ["C123ABC|general"],
                    "max_records": 201,
                },
            )
        self.assertEqual(context.exception.code, 422)
        self.assertEqual(len(self.fake_store.slack_sync_calls), 1)

    def test_readwise_connector_route_forwards_to_store(self) -> None:
        with self.post_json(
            "/v1/connectors/readwise/sync",
            {
                "token": "readwise_test",
                "source_account_id": "sacct_readwise_existing",
                "account_label": "Cortex Readwise",
                "account_identifier": "readwise-user",
                "since": "2026-01-01T00:00:00Z",
                "page_cursor": "cursor-1",
                "processing": "sync",
                "max_records": 50,
                "cursor_name": "highlights",
                "api_base_url": "https://readwise-api.test",
            },
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["source"], "readwise")
        self.assertEqual(payload["source_account_id"], "sacct_readwise_existing")
        self.assertEqual(payload["processing"], "sync")
        self.assertEqual(payload["source_account"]["account_label"], "Cortex Readwise")
        self.assertEqual(payload["records"][0]["source_url"], "https://readwise.io/bookreview/111")
        self.assertEqual(
            self.fake_store.readwise_sync_calls,
            [
                {
                    "complete_snapshot": False,
                    "user_id": "local",
                    "token": "readwise_test",
                    "source_account_id": "sacct_readwise_existing",
                    "account_label": "Cortex Readwise",
                    "account_identifier": "readwise-user",
                    "since": "2026-01-01T00:00:00Z",
                    "page_cursor": "cursor-1",
                    "processing": "sync",
                    "max_records": 50,
                    "cursor_name": "highlights",
                    "api_base_url": "https://readwise-api.test",
                }
            ],
        )

        with self.assertRaises(error.HTTPError) as context:
            self.post_json(
                "/v1/connectors/readwise/sync",
                {
                    "token": "readwise_test",
                    "max_records": 501,
                },
            )
        self.assertEqual(context.exception.code, 422)
        self.assertEqual(len(self.fake_store.readwise_sync_calls), 1)

    def test_calendar_connector_route_forwards_to_store(self) -> None:
        private_path = "/Users/sarp/Private/Calendar.ics"
        with self.post_json(
            "/v1/connectors/calendar/sync",
            {
                "ics_path": private_path,
                "source_account_id": "sacct_calendar_existing",
                "account_label": "Cortex Calendar",
                "account_identifier": "local_file:Calendar.ics",
                "since": "20260630T100000Z",
                "processing": "sync",
                "max_records": 50,
                "cursor_name": "events",
            },
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["source"], "calendar")
        self.assertEqual(payload["source_account_id"], "sacct_calendar_existing")
        self.assertEqual(payload["processing"], "sync")
        self.assertEqual(payload["source_account"]["account_label"], "Cortex Calendar")
        self.assertTrue(payload["records"][0]["source_url"].startswith("source-account://calendar/"))
        self.assertNotIn(private_path, json.dumps(payload))
        self.assertEqual(
            self.fake_store.calendar_sync_calls,
            [
                {
                    "complete_snapshot": False,
                    "user_id": "local",
                    "ics_path": private_path,
                    "feed_url": None,
                    "source_account_id": "sacct_calendar_existing",
                    "account_label": "Cortex Calendar",
                    "account_identifier": "local_file:Calendar.ics",
                    "since": "20260630T100000Z",
                    "processing": "sync",
                    "max_records": 50,
                    "cursor_name": "events",
                }
            ],
        )

        with self.assertRaises(error.HTTPError) as context:
            self.post_json(
                "/v1/connectors/calendar/sync",
                {
                    "ics_path": private_path,
                    "feed_url": "https://calendar.example.com/private.ics",
                    "max_records": 50,
                },
            )
        self.assertEqual(context.exception.code, 422)
        self.assertEqual(len(self.fake_store.calendar_sync_calls), 1)

    def test_raindrop_connector_route_forwards_to_store(self) -> None:
        with self.post_json(
            "/v1/connectors/raindrop/sync",
            {
                "token": "rd_route_secret",
                "collection_id": "0",
                "source_account_id": "sacct_raindrop_existing",
                "account_label": "Cortex Raindrop",
                "account_identifier": "collection:0",
                "since": "2026-01-01T00:00:00Z",
                "page": "2",
                "processing": "sync",
                "max_records": 50,
                "cursor_name": "raindrops",
                "include_highlights": True,
                "api_base_url": "https://raindrop-api.test/rest/v1",
            },
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["source"], "raindrop")
        self.assertEqual(payload["source_account_id"], "sacct_raindrop_existing")
        self.assertEqual(payload["processing"], "sync")
        self.assertEqual(payload["source_account"]["account_label"], "Cortex Raindrop")
        self.assertEqual(payload["records"][0]["source_url"], "https://example.com/raindrop")
        self.assertNotIn("rd_route_secret", json.dumps(payload))
        self.assertEqual(
            self.fake_store.raindrop_sync_calls,
            [
                {
                    "complete_snapshot": False,
                    "user_id": "local",
                    "token": "rd_route_secret",
                    "collection_id": "0",
                    "source_account_id": "sacct_raindrop_existing",
                    "account_label": "Cortex Raindrop",
                    "account_identifier": "collection:0",
                    "since": "2026-01-01T00:00:00Z",
                    "page": "2",
                    "processing": "sync",
                    "max_records": 50,
                    "cursor_name": "raindrops",
                    "include_highlights": True,
                    "api_base_url": "https://raindrop-api.test/rest/v1",
                }
            ],
        )

        with self.assertRaises(error.HTTPError) as context:
            self.post_json(
                "/v1/connectors/raindrop/sync",
                {
                    "token": "rd_route_secret",
                    "max_records": 501,
                },
            )
        self.assertEqual(context.exception.code, 422)
        self.assertEqual(len(self.fake_store.raindrop_sync_calls), 1)

    def test_zotero_connector_route_forwards_to_store(self) -> None:
        with self.post_json(
            "/v1/connectors/zotero/sync",
            {
                "token": "zotero_test",
                "library_type": "user",
                "library_id": "0",
                "source_account_id": "sacct_zotero_existing",
                "account_label": "Cortex Zotero",
                "account_identifier": "user:0",
                "since": "41",
                "cursor": "10",
                "processing": "sync",
                "max_records": 50,
                "cursor_name": "items",
                "include_attachments": False,
                "api_base_url": "http://localhost:23119/api",
            },
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["source"], "zotero")
        self.assertEqual(payload["source_account_id"], "sacct_zotero_existing")
        self.assertEqual(payload["processing"], "sync")
        self.assertEqual(payload["source_account"]["account_label"], "Cortex Zotero")
        self.assertEqual(payload["records"][0]["source_url"], "zotero://select/library/items/ZTITEM1")
        self.assertEqual(
            self.fake_store.zotero_sync_calls,
            [
                {
                    "complete_snapshot": False,
                    "user_id": "local",
                    "token": "zotero_test",
                    "library_type": "user",
                    "library_id": "0",
                    "source_account_id": "sacct_zotero_existing",
                    "account_label": "Cortex Zotero",
                    "account_identifier": "user:0",
                    "since": "41",
                    "cursor": "10",
                    "processing": "sync",
                    "max_records": 50,
                    "cursor_name": "items",
                    "include_attachments": False,
                    "api_base_url": "http://localhost:23119/api",
                }
            ],
        )

        with self.assertRaises(error.HTTPError) as context:
            self.post_json(
                "/v1/connectors/zotero/sync",
                {
                    "max_records": 501,
                },
            )
        self.assertEqual(context.exception.code, 422)
        self.assertEqual(len(self.fake_store.zotero_sync_calls), 1)

    def test_linear_connector_route_forwards_to_store(self) -> None:
        with self.post_json(
            "/v1/connectors/linear/sync",
            {
                "token": "lin_api_test",
                "source_account_id": "sacct_linear_existing",
                "account_label": "Cortex Linear",
                "account_identifier": "doppl-linear",
                "since": "2026-01-01T00:00:00Z",
                "cursor": "cursor-1",
                "processing": "sync",
                "max_records": 50,
                "cursor_name": "issues",
                "api_url": "https://linear-api.test/graphql",
            },
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["source"], "linear")
        self.assertEqual(payload["source_account_id"], "sacct_linear_existing")
        self.assertEqual(payload["processing"], "sync")
        self.assertEqual(payload["source_account"]["account_label"], "Cortex Linear")
        self.assertEqual(payload["records"][0]["source_url"], "https://linear.app/doppl/issue/COR-42/test")
        self.assertEqual(
            self.fake_store.linear_sync_calls,
            [
                {
                    "complete_snapshot": False,
                    "user_id": "local",
                    "token": "lin_api_test",
                    "source_account_id": "sacct_linear_existing",
                    "account_label": "Cortex Linear",
                    "account_identifier": "doppl-linear",
                    "since": "2026-01-01T00:00:00Z",
                    "cursor": "cursor-1",
                    "processing": "sync",
                    "max_records": 50,
                    "cursor_name": "issues",
                    "api_url": "https://linear-api.test/graphql",
                }
            ],
        )

        with self.assertRaises(error.HTTPError) as context:
            self.post_json(
                "/v1/connectors/linear/sync",
                {
                    "token": "lin_api_test",
                    "max_records": 501,
                },
            )
        self.assertEqual(context.exception.code, 422)
        self.assertEqual(len(self.fake_store.linear_sync_calls), 1)

    def test_jira_connector_route_forwards_to_store(self) -> None:
        with self.post_json(
            "/v1/connectors/jira/sync",
            {
                "email": "sarp@example.com",
                "api_token": "jira_api_test",
                "site_url": "https://doppl.atlassian.net",
                "source_account_id": "sacct_jira_existing",
                "account_label": "Cortex Jira",
                "account_identifier": "doppl.atlassian.net",
                "jql": "project = COR ORDER BY updated DESC",
                "since": "2026-01-01T00:00:00Z",
                "page_token": "page-1",
                "processing": "sync",
                "max_records": 50,
                "cursor_name": "issues",
            },
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["source"], "jira")
        self.assertEqual(payload["source_account_id"], "sacct_jira_existing")
        self.assertEqual(payload["processing"], "sync")
        self.assertEqual(payload["source_account"]["account_label"], "Cortex Jira")
        self.assertEqual(payload["records"][0]["source_url"], "https://doppl.atlassian.net/browse/COR-42")
        self.assertNotIn("jira_api_test", json.dumps(payload))
        self.assertEqual(
            self.fake_store.jira_sync_calls,
            [
                {
                    "complete_snapshot": False,
                    "user_id": "local",
                    "email": "sarp@example.com",
                    "api_token": "jira_api_test",
                    "site_url": "https://doppl.atlassian.net",
                    "source_account_id": "sacct_jira_existing",
                    "account_label": "Cortex Jira",
                    "account_identifier": "doppl.atlassian.net",
                    "jql": "project = COR ORDER BY updated DESC",
                    "since": "2026-01-01T00:00:00Z",
                    "page_token": "page-1",
                    "processing": "sync",
                    "max_records": 50,
                    "cursor_name": "issues",
                }
            ],
        )

        with self.assertRaises(error.HTTPError) as context:
            self.post_json(
                "/v1/connectors/jira/sync",
                {
                    "email": "sarp@example.com",
                    "api_token": "jira_api_test",
                    "site_url": "https://doppl.atlassian.net",
                    "max_records": 501,
                },
            )
        self.assertEqual(context.exception.code, 422)
        self.assertEqual(len(self.fake_store.jira_sync_calls), 1)

    def test_notion_connector_route_forwards_to_store(self) -> None:
        with self.post_json(
            "/v1/connectors/notion/sync",
            {
                "token": "notion_test",
                "source_account_id": "sacct_notion_existing",
                "account_label": "Cortex Notion",
                "account_identifier": "doppl-notion",
                "since": "2026-01-01T00:00:00Z",
                "cursor": "cursor-1",
                "processing": "sync",
                "max_records": 50,
                "cursor_name": "pages",
                "include_content": False,
                "api_base_url": "https://notion-api.test/v1",
                "notion_version": "2026-03-11",
            },
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["source"], "notion")
        self.assertEqual(payload["source_account_id"], "sacct_notion_existing")
        self.assertEqual(payload["processing"], "sync")
        self.assertEqual(payload["source_account"]["account_label"], "Cortex Notion")
        self.assertEqual(payload["records"][0]["source_url"], "https://www.notion.so/doppl/page-1")
        self.assertEqual(
            self.fake_store.notion_sync_calls,
            [
                {
                    "complete_snapshot": False,
                    "user_id": "local",
                    "token": "notion_test",
                    "source_account_id": "sacct_notion_existing",
                    "account_label": "Cortex Notion",
                    "account_identifier": "doppl-notion",
                    "since": "2026-01-01T00:00:00Z",
                    "cursor": "cursor-1",
                    "processing": "sync",
                    "max_records": 50,
                    "cursor_name": "pages",
                    "include_content": False,
                    "api_base_url": "https://notion-api.test/v1",
                    "notion_version": "2026-03-11",
                }
            ],
        )

        with self.assertRaises(error.HTTPError) as context:
            self.post_json(
                "/v1/connectors/notion/sync",
                {
                    "token": "notion_test",
                    "max_records": 201,
                },
            )
        self.assertEqual(context.exception.code, 422)
        self.assertEqual(len(self.fake_store.notion_sync_calls), 1)

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
        self.assertIn("structuredContent", payload["result"])
        self.assertEqual(payload["result"]["structuredContent"]["results"][0]["id"], "memory-1")
        self.assertTrue(payload["result"]["structuredContent"]["retrieval"]["diagnostics_unavailable"])
        self.assertEqual(json.loads(payload["result"]["content"][0]["text"]), payload["result"]["structuredContent"])
        self.assertEqual(self.fake_store.search_calls[-1]["query"], "voice")
        self.assertEqual(self.fake_store.search_calls[-1]["limit"], 8)
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
        self.assertEqual(self.fake_store.search_calls[-1]["user_id"], "alice")

        # A token without export scope is still denied the raw bulk dump (distilled reads like
        # context-pack are now allowed with read scope).
        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(
                request.Request(
                    self.base_url + "/v1/export.json",
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

    def test_scoped_api_tokens_obey_trust_controls(self) -> None:
        standalone_server.settings = Settings(
            vault_path=Path(self.tmp.name) / "vault",
            db_path=Path(self.tmp.name) / "index.sqlite",
            api_key="test-token",
            public_base_url="http://127.0.0.1:8766",
            require_scoped_api_tokens=True,
        )
        scoped_headers = {"Authorization": "Bearer cxa-standalone-token", "X-Cortex-User": "alice"}
        self.fake_store.api_token_scopes = ["read", "write", "export", "maintenance", "destructive"]

        self.fake_store.denied_agent_access = {"read"}
        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(request.Request(self.base_url + "/v1/search?query=voice", headers=scoped_headers), timeout=5)
        self.assertEqual(context.exception.code, 403)
        self.assertIn("read actions are disabled", context.exception.read().decode("utf-8"))
        self.assertEqual(self.fake_store.search_calls, [])

        # The raw bulk dump stays export-gated (the distilled context-pack is now a read).
        self.fake_store.denied_agent_access = {"export"}
        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(request.Request(self.base_url + "/v1/export.json", headers=scoped_headers), timeout=5)
        self.assertEqual(context.exception.code, 403)
        self.assertIn("export actions are disabled", context.exception.read().decode("utf-8"))

        self.fake_store.denied_agent_access = {"write"}
        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(self.base_url + "/capture?token=cxa-standalone-token&content=Remember", timeout=5)
        self.assertEqual(context.exception.code, 403)
        self.assertIn("write actions are disabled", context.exception.read().decode("utf-8"))

        self.fake_store.api_token_scopes = ["read", "write"]
        self.fake_store.denied_agent_access = set()
        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(
                request.Request(
                    self.base_url + "/v1/source-accounts",
                    data=json.dumps({"source": "gmail"}).encode("utf-8"),
                    headers={**scoped_headers, "Content-Type": "application/json"},
                    method="POST",
                ),
                timeout=5,
            )
        self.assertEqual(context.exception.code, 403)
        self.assertIn("maintenance scope", context.exception.read().decode("utf-8"))
        self.assertEqual(self.fake_store.source_account_calls, [])

        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(
                request.Request(
                    self.base_url + "/v1/connectors/google/oauth/start",
                    data=json.dumps({"source": "gmail", "client_id": "google-client-id"}).encode("utf-8"),
                    headers={**scoped_headers, "Content-Type": "application/json"},
                    method="POST",
                ),
                timeout=5,
            )
        self.assertEqual(context.exception.code, 403)
        self.assertIn("maintenance scope", context.exception.read().decode("utf-8"))
        self.assertEqual(self.fake_store.google_oauth_start_calls, [])

        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(
                request.Request(
                    self.base_url + "/v1/connectors/oauth/start",
                    data=json.dumps({"source": "notion", "client_id": "notion-client-id"}).encode("utf-8"),
                    headers={**scoped_headers, "Content-Type": "application/json"},
                    method="POST",
                ),
                timeout=5,
            )
        self.assertEqual(context.exception.code, 403)
        self.assertIn("maintenance scope", context.exception.read().decode("utf-8"))
        self.assertEqual(self.fake_store.managed_oauth_start_calls, [])

        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(
                request.Request(
                    self.base_url + "/v1/source-accounts/sacct_test/disconnect",
                    data=b"{}",
                    headers={**scoped_headers, "Content-Type": "application/json"},
                    method="POST",
                ),
                timeout=5,
            )
        self.assertEqual(context.exception.code, 403)
        self.assertIn("maintenance scope", context.exception.read().decode("utf-8"))
        self.assertFalse(self.fake_store.source_account_disconnected)

        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(
                request.Request(
                    self.base_url + "/v1/sync-cursors",
                    data=json.dumps({"source": "gmail", "cursor_name": "messages"}).encode("utf-8"),
                    headers={**scoped_headers, "Content-Type": "application/json"},
                    method="POST",
                ),
                timeout=5,
            )
        self.assertEqual(context.exception.code, 403)
        self.assertIn("maintenance scope", context.exception.read().decode("utf-8"))
        self.assertEqual(self.fake_store.sync_cursor_calls, [])

        self.fake_store.api_token_scopes = ["read", "maintenance"]
        self.fake_store.denied_agent_access = {"maintenance"}
        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(
                request.Request(
                    self.base_url + "/v1/source-accounts",
                    data=json.dumps({"source": "gmail"}).encode("utf-8"),
                    headers={**scoped_headers, "Content-Type": "application/json"},
                    method="POST",
                ),
                timeout=5,
            )
        self.assertEqual(context.exception.code, 403)
        self.assertIn("maintenance actions are disabled", context.exception.read().decode("utf-8"))
        self.assertEqual(self.fake_store.source_account_calls, [])

        self.fake_store.api_token_scopes = ["read", "destructive"]
        self.fake_store.denied_agent_access = {"destructive"}
        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(
                request.Request(
                    self.base_url + "/v1/user-data?include_backups=false",
                    headers=scoped_headers,
                    method="DELETE",
                ),
                timeout=5,
            )
        self.assertEqual(context.exception.code, 403)
        self.assertIn("destructive actions are disabled", context.exception.read().decode("utf-8"))
        self.assertEqual(self.fake_store.delete_user_data_calls, [])

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

    def test_context_engine_routes_on_shipping_server(self) -> None:
        # GET /v1/context reaches assemble_context with the parsed params.
        self.fake_store.assemble_context_calls.clear()
        with request.urlopen(
            request.Request(
                self.base_url + "/v1/context?task=atlas+decision&token_budget=1500&surface=cursor",
                headers={"Authorization": "Bearer test-token"},
            ),
            timeout=5,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(payload["coverage"]["status"], "usable")
        self.assertEqual(len(self.fake_store.assemble_context_calls), 1)
        call = self.fake_store.assemble_context_calls[0]
        self.assertEqual(call["task"], "atlas decision")
        self.assertEqual(call["token_budget"], 1500)
        self.assertEqual(call["surface"], "cursor")
        # Admin bearer sees the identity layer.
        self.assertIs(call["include_identity"], True)

        # POST parity + markdown content type.
        with request.urlopen(
            request.Request(
                self.base_url + "/v1/context",
                data=json.dumps({"task": "atlas decision", "format": "markdown"}).encode("utf-8"),
                headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"},
                method="POST",
            ),
            timeout=5,
        ) as response:
            self.assertIn("text/markdown", response.headers.get("Content-Type", ""))
            self.assertIn("# Cortex Context Pack", response.read().decode("utf-8"))

        # Bad format is a 422, not a 500.
        with self.assertRaises(error.HTTPError) as context:
            self.post_json("/v1/context", {"task": "x", "format": "yaml"})
        self.assertEqual(context.exception.code, 422)

    def test_context_pack_verify_route_on_shipping_server(self) -> None:
        # Phase 2b: POST /v1/context/packs/{sha}/verify reaches store.verify_context_pack,
        # is maintenance-scoped for API tokens, and maps ValueError -> 404.
        self.fake_store.verify_context_pack_calls.clear()
        sha = "a" * 64
        with request.urlopen(
            request.Request(
                self.base_url + f"/v1/context/packs/{sha}/verify",
                headers={"Authorization": "Bearer test-token"},
                method="POST",
            ),
            timeout=5,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(payload["status"], "match")
        self.assertEqual(self.fake_store.verify_context_pack_calls, [("local", sha)])

        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(
                request.Request(
                    self.base_url + "/v1/context/packs/" + "missing" * 8 + "/verify",
                    headers={"Authorization": "Bearer test-token"},
                    method="POST",
                ),
                timeout=5,
            )
        self.assertEqual(context.exception.code, 404)

        # Maintenance scope discipline for scoped API tokens (parity with the MCP tool).
        self.fake_store.api_token_scopes = ["read", "write"]
        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(
                request.Request(
                    self.base_url + f"/v1/context/packs/{sha}/verify",
                    headers={"Authorization": "Bearer cxa-standalone-token"},
                    method="POST",
                ),
                timeout=5,
            )
        self.assertEqual(context.exception.code, 403)
        self.assertIn("maintenance scope", context.exception.read().decode("utf-8"))

        self.fake_store.api_token_scopes = ["read", "maintenance"]
        with request.urlopen(
            request.Request(
                self.base_url + f"/v1/context/packs/{sha}/verify",
                headers={"Authorization": "Bearer cxa-standalone-token"},
                method="POST",
            ),
            timeout=5,
        ) as response:
            self.assertEqual(json.loads(response.read().decode("utf-8"))["status"], "match")

        # The user-level maintenance trust gate blocks even a correctly-scoped token.
        self.fake_store.denied_agent_access = {"maintenance"}
        try:
            with self.assertRaises(error.HTTPError) as context:
                request.urlopen(
                    request.Request(
                        self.base_url + f"/v1/context/packs/{sha}/verify",
                        headers={"Authorization": "Bearer cxa-standalone-token"},
                        method="POST",
                    ),
                    timeout=5,
                )
            self.assertEqual(context.exception.code, 403)
            self.assertIn("maintenance actions are disabled", context.exception.read().decode("utf-8"))
        finally:
            self.fake_store.denied_agent_access = set()
            self.fake_store.api_token_scopes = ["read"]

    def test_mcp_tools_list_filters_read_only_scoped_token(self) -> None:
        scoped_request = request.Request(
            self.base_url + "/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": "read-tools", "method": "tools/list", "params": {}}).encode("utf-8"),
            headers={"Authorization": "Bearer cxm-standalone-token", "Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(scoped_request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))

        # A read-only scoped token is advertised the curated CORE surface (the tool collapse):
        # a handful of well-chosen read tools. The legacy long tail stays callable but hidden.
        tool_names = {tool["name"] for tool in payload["result"]["tools"]}
        self.assertEqual(
            tool_names,
            {"use_cortex", "get_context", "ask_memory", "search_memory", "get_entity_context", "get_person_map", "list_capabilities"},
        )
        self.assertNotIn("connect_source_account", tool_names)
        self.assertNotIn("sync_source_records", tool_names)
        self.assertNotIn("sync_connected_sources", tool_names)
        self.assertNotIn("approve_memory_capture", tool_names)
        self.assertNotIn("delete_all_user_data", tool_names)

        # Hidden-but-scoped tools still dispatch: hiding is never authorization.
        hidden_call = request.Request(
            self.base_url + "/mcp",
            data=json.dumps({
                "jsonrpc": "2.0", "id": "hidden-call", "method": "tools/call",
                "params": {"name": "get_memory_quality_report", "arguments": {}},
            }).encode("utf-8"),
            headers={"Authorization": "Bearer cxm-standalone-token", "Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(hidden_call, timeout=5) as response:
            hidden_payload = json.loads(response.read().decode("utf-8"))
        self.assertNotIn("error", hidden_payload)

        admin_request = request.Request(
            self.base_url + "/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": "admin-tools", "method": "tools/list", "params": {}}).encode("utf-8"),
            headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(admin_request, timeout=5) as response:
            admin_payload = json.loads(response.read().decode("utf-8"))

        admin_tool_names = {tool["name"] for tool in admin_payload["result"]["tools"]}
        self.assertIn("connect_source_account", admin_tool_names)
        self.assertIn("sync_source_records", admin_tool_names)
        self.assertIn("sync_connected_sources", admin_tool_names)
        self.assertIn("approve_memory_capture", admin_tool_names)

    def post_mcp(self, message):
        # POST a raw JSON-RPC payload to /mcp without raising on 4xx/5xx: returns (status, body).
        data = json.dumps(message).encode("utf-8")
        req = request.Request(
            self.base_url + "/mcp",
            data=data,
            headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=5) as response:
                return response.status, response.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            exc.close()
            return exc.code, body

    def test_mcp_initialize_echoes_supported_protocol_version(self) -> None:
        # Remote clients (ChatGPT web, Claude web) negotiate the protocol revision on initialize:
        # echo theirs when we can serve it, otherwise offer our newest.
        for requested in standalone_server.MCP_PROTOCOL_VERSIONS:
            status, body = self.post_mcp({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": requested, "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
            })
            payload = json.loads(body)
            self.assertEqual(status, 200)
            self.assertEqual(payload["result"]["protocolVersion"], requested)
            self.assertEqual(payload["result"]["serverInfo"]["name"], "cortex")
            self.assertEqual(payload["result"]["serverInfo"]["version"], standalone_server.BACKEND_VERSION)
            self.assertEqual(
                payload["result"]["capabilities"],
                {"tools": {"listChanged": False}, "resources": {"listChanged": False, "subscribe": False}, "prompts": {"listChanged": False}},
            )

        for params in ({"protocolVersion": "1999-01-01"}, {}, None):
            status, body = self.post_mcp({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": params})
            payload = json.loads(body)
            self.assertEqual(status, 200)
            self.assertEqual(payload["result"]["protocolVersion"], standalone_server.MCP_PROTOCOL_VERSIONS[0])

    def test_mcp_notifications_are_accepted_without_response_body(self) -> None:
        # Id-less JSON-RPC messages are notifications: never an error, no response body (202).
        for message in (
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 9}},
        ):
            status, body = self.post_mcp(message)
            self.assertEqual(status, 202)
            self.assertEqual(body, "")

    def test_mcp_tools_list_tolerates_cursor_param(self) -> None:
        status, body = self.post_mcp({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {"cursor": "opaque-cursor"}})
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertNotIn("error", payload)
        self.assertTrue(payload["result"]["tools"])
        self.assertNotIn("nextCursor", payload["result"])

    def test_mcp_ping_returns_empty_result(self) -> None:
        status, body = self.post_mcp({"jsonrpc": "2.0", "id": "ping-1", "method": "ping"})
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["result"], {})
        self.assertEqual(payload["id"], "ping-1")

    def test_mcp_batch_requests_rejected_with_clean_jsonrpc_error(self) -> None:
        status, body = self.post_mcp([
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["error"]["code"], -32600)
        self.assertIn("batch not supported", payload["error"]["message"])

    def test_mcp_unknown_method_returns_method_not_found(self) -> None:
        # resources/list, prompts/list, etc. are implemented now; use a genuinely unknown method.
        status, body = self.post_mcp({"jsonrpc": "2.0", "id": 4, "method": "sampling/createMessage", "params": {}})
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertNotIn("result", payload)
        self.assertEqual(payload["error"]["code"], -32601)
        self.assertIn("sampling/createMessage", payload["error"]["message"])

    def test_mcp_resources_and_prompts_are_served(self) -> None:
        # Routing-level checks against the FakeStore (which stubs only stats/agent_payload). Deep
        # resource/prompt behavior against a real CortexStore lives in test_mcp_resources_prompts.py.
        status, body = self.post_mcp({"jsonrpc": "2.0", "id": 40, "method": "resources/list"})
        payload = json.loads(body)
        self.assertEqual(status, 200)
        uris = {r["uri"] for r in payload["result"]["resources"]}
        self.assertIn("cortex://profile/person-map", uris)
        self.assertIn("cortex://schema/capabilities", uris)
        self.assertTrue(payload["result"]["resourceTemplates"])

        # schema/capabilities resolves from stats (which FakeStore provides) -> valid JSON contents.
        status, body = self.post_mcp({
            "jsonrpc": "2.0", "id": 41, "method": "resources/read",
            "params": {"uri": "cortex://schema/capabilities"},
        })
        payload = json.loads(body)
        self.assertEqual(status, 200)
        content = payload["result"]["contents"][0]
        self.assertEqual(content["mimeType"], "application/json")
        self.assertIn("stats", json.loads(content["text"]))

        # An unknown resource routes to a clean JSON-RPC error (not a crash).
        status, body = self.post_mcp({
            "jsonrpc": "2.0", "id": 44, "method": "resources/read", "params": {"uri": "cortex://nope/x"},
        })
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIn("error", payload)

        status, body = self.post_mcp({"jsonrpc": "2.0", "id": 42, "method": "prompts/list"})
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIn("brief_me_on", {p["name"] for p in payload["result"]["prompts"]})

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

    def test_rate_limit_burst_returns_429_with_retry_after(self) -> None:
        # A tiny bucket (burst 3, refill 1/s) must reject a rapid burst from one bearer token.
        with mock.patch.dict(os.environ, {"CORTEX_RATE_LIMIT_RPS": "1", "CORTEX_RATE_LIMIT_BURST": "3"}):
            standalone_server.REQUEST_GUARDS = standalone_server._RequestGuards()

        results = [self.get_raw("/v1/stats") for _ in range(6)]
        statuses = [status for status, _, _ in results]
        # The burst allowance is honored before throttling kicks in.
        self.assertEqual(statuses[:3], [200, 200, 200])
        self.assertIn(429, statuses)
        _, headers, body = next(result for result in results if result[0] == 429)
        self.assertEqual(headers.get("Retry-After"), "1")
        self.assertEqual(json.loads(body), {"detail": "Too many requests; slow down and retry."})

    def test_rate_limit_defaults_leave_sequential_requests_unaffected(self) -> None:
        # setUp installs default guards (20 rps / burst 60): normal app traffic never sees 429.
        for _ in range(5):
            with self.get("/v1/stats") as response:
                self.assertEqual(response.status, 200)

    def test_health_and_ready_bypass_saturated_guards(self) -> None:
        # Health endpoints never pass through either guard layer, so the app can supervise a
        # saturated backend.
        with mock.patch.object(standalone_server.REQUEST_GUARDS, "allow_request", return_value=False), \
                mock.patch.object(standalone_server.REQUEST_GUARDS, "acquire_slot", return_value=False):
            status, _, _ = self.get_raw("/v1/stats")
            self.assertEqual(status, 429)
            with self.get("/health") as response:
                self.assertEqual(response.status, 200)
            with self.get("/ready") as response:
                self.assertEqual(response.status, 200)

    def test_rate_limit_disabled_when_rps_zero(self) -> None:
        with mock.patch.dict(os.environ, {"CORTEX_RATE_LIMIT_RPS": "0", "CORTEX_RATE_LIMIT_BURST": "3"}):
            standalone_server.REQUEST_GUARDS = standalone_server._RequestGuards()

        statuses = [self.get_raw("/v1/stats")[0] for _ in range(10)]
        self.assertEqual(statuses, [200] * 10)

    def test_concurrency_gate_saturation_returns_503_but_health_still_serves(self) -> None:
        with mock.patch.object(standalone_server.REQUEST_GUARDS, "acquire_slot", return_value=False):
            status, headers, body = self.get_raw("/v1/stats")
            self.assertEqual(status, 503)
            self.assertEqual(headers.get("Retry-After"), "1")
            self.assertEqual(json.loads(body), {"detail": "Cortex is busy handling other requests; retry shortly."})
            with self.get("/health") as response:
                self.assertEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()
