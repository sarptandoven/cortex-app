from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import connect, sqlite_vec_status
from .embeddings import VECTOR_DIMENSIONS, embed_text, embed_text_result, embedding_hash, embedding_json, embedding_source_text, embedding_status
from .extractor import extract_context, now_iso, stable_id
from .source_ingest import SourceRecord, analyze_sources, import_source_records, supported_sources
from .vault import CortexVault


BACKEND_VERSION = "0.1.0"
HEALTH_CONTRACT = 3
BACKEND_FEATURES = (
    "local-vault",
    "capture-surfaces",
    "layered-memory",
    "trust-controls",
    "installer-updates",
    "reliability-hardening",
    "simple-product-loop",
    "operational-readiness",
    "source-imports",
    "source-account-registry",
    "sync-device-manifests",
    "sync-receipts",
)
SUPPORT_BUNDLE_SCHEMA = 1
DEFAULT_BACKUP_RETENTION_COUNT = 20
DEFAULT_BACKUP_RETENTION_DAYS = 0
DEFAULT_MCP_TOKEN_SCOPES = ("read", "write", "export", "maintenance")
MCP_TOKEN_SCOPES = {"read", "write", "export", "maintenance", "destructive"}

MEMORY_LAYERS = {"semantic", "episodic", "style", "decision", "preference", "negative"}
MEMORY_LAYER_BY_KIND = {
    "claim": "semantic",
    "observation": "semantic",
    "summary": "semantic",
    "event": "episodic",
    "decision": "decision",
    "preference": "preference",
    "style": "style",
    "negative": "negative",
}
LAYER_RETRIEVAL_BOOST = 0.02
LAYER_QUERY_INTENTS: tuple[tuple[set[str], set[str]], ...] = (
    (
        {"style", "negative"},
        {
            "copy",
            "draft",
            "language",
            "paragraph",
            "paragraphs",
            "prose",
            "style",
            "tone",
            "voice",
            "wording",
            "write",
            "writing",
        },
    ),
    (
        {"decision"},
        {
            "approach",
            "choose",
            "decide",
            "decided",
            "decision",
            "plan",
            "planning",
            "prioritize",
            "roadmap",
        },
    ),
    (
        {"preference"},
        {
            "default",
            "dislike",
            "favorite",
            "prefer",
            "preference",
            "preferred",
            "rather",
        },
    ),
    (
        {"episodic"},
        {
            "event",
            "happen",
            "happened",
            "history",
            "meeting",
            "met",
            "timeline",
            "when",
        },
    ),
)


DEFAULT_USER_SETTINGS: dict[str, Any] = {
    "review_new_captures": True,
    "allow_pending_in_context": True,
    "context_pack_limit": 12,
    "allow_agent_reads": True,
    "allow_agent_writes": False,
    "allow_agent_exports": False,
    "allow_agent_maintenance": False,
    "allow_agent_destructive_actions": False,
    "redact_sensitive_context": True,
    "source_policies": {},
    "identity_aliases": [],
}


SOURCE_CONNECTOR_CATALOG: tuple[dict[str, Any], ...] = (
    {"id": "chatgpt", "name": "ChatGPT", "category": "AI chats", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "OpenAI data export zip or conversations.json."},
    {"id": "claude", "name": "Claude", "category": "AI chats", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "Claude export conversations.json or chats.json."},
    {"id": "gmail", "name": "Gmail", "category": "Email", "auth": "oauth", "live_status": "planned", "scopes": ["gmail.readonly"], "notes": "Use Gmail Takeout mbox today; OAuth sync later."},
    {"id": "email", "name": "Email files", "category": "Email", "auth": "file", "live_status": "import_ready", "scopes": [], "notes": "mbox, eml, and emlx imports."},
    {"id": "notion", "name": "Notion", "category": "Docs", "auth": "oauth", "live_status": "planned", "scopes": ["read_content"], "notes": "Markdown, CSV, and HTML exports today."},
    {"id": "google-drive", "name": "Google Drive", "category": "Docs", "auth": "oauth", "live_status": "planned", "scopes": ["drive.readonly"], "notes": "Drive/Docs Takeout exports today."},
    {"id": "microsoft-365", "name": "Microsoft 365", "category": "Docs", "auth": "oauth", "live_status": "planned", "scopes": ["Files.Read", "Mail.Read", "Calendars.Read"], "notes": "OneDrive, Outlook, and Office exports today."},
    {"id": "slack", "name": "Slack", "category": "Work chat", "auth": "oauth", "live_status": "planned", "scopes": ["channels:history", "groups:history", "im:history"], "notes": "Workspace export folders or zips today."},
    {"id": "google-chat", "name": "Google Chat", "category": "Work chat", "auth": "oauth", "live_status": "planned", "scopes": ["chat.messages.readonly"], "notes": "Google Takeout Chat/Hangouts exports today."},
    {"id": "teams", "name": "Microsoft Teams", "category": "Work chat", "auth": "oauth", "live_status": "planned", "scopes": ["ChannelMessage.Read.All"], "notes": "Teams JSON/CSV exports today."},
    {"id": "discord", "name": "Discord", "category": "Messages", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "Discord data package messages.csv."},
    {"id": "telegram", "name": "Telegram", "category": "Messages", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "Telegram Desktop result.json."},
    {"id": "messages", "name": "Messages", "category": "Messages", "auth": "local_file", "live_status": "local_only", "scopes": [], "notes": "User-selected copy of iMessage chat.db."},
    {"id": "whatsapp", "name": "WhatsApp", "category": "Messages", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "Text chat exports."},
    {"id": "calendar", "name": "Calendar", "category": "Calendar", "auth": "oauth", "live_status": "planned", "scopes": ["calendar.readonly"], "notes": "ICS exports today."},
    {"id": "contacts", "name": "Contacts", "category": "People", "auth": "oauth", "live_status": "planned", "scopes": ["contacts.readonly"], "notes": "VCF and contacts CSV exports today."},
    {"id": "github", "name": "GitHub", "category": "Work tools", "auth": "oauth", "live_status": "planned", "scopes": ["repo:read", "read:org"], "notes": "Issue/PR exports and project files today."},
    {"id": "linear", "name": "Linear", "category": "Work tools", "auth": "oauth", "live_status": "planned", "scopes": ["read"], "notes": "CSV/JSON exports today."},
    {"id": "jira", "name": "Jira", "category": "Work tools", "auth": "oauth", "live_status": "planned", "scopes": ["read:jira-work"], "notes": "CSV exports today."},
    {"id": "zoom", "name": "Zoom", "category": "Meetings", "auth": "oauth", "live_status": "planned", "scopes": ["recording:read"], "notes": "VTT and SRT transcript imports today."},
    {"id": "browser-bookmarks", "name": "Browser bookmarks", "category": "Research", "auth": "local_file", "live_status": "import_ready", "scopes": [], "notes": "Bookmarks HTML/JSON and browser history SQLite."},
    {"id": "readwise", "name": "Readwise", "category": "Research", "auth": "api_token", "live_status": "planned", "scopes": ["export"], "notes": "CSV/JSON exports today."},
    {"id": "apple-notes", "name": "Apple Notes", "category": "Notes", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "HTML, RTF, PDF, Markdown, or text exports."},
    {"id": "obsidian", "name": "Obsidian", "category": "Notes", "auth": "local_folder", "live_status": "planned", "scopes": [], "notes": "Markdown vault imports today."},
)


SENSITIVE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\b(?:xox[baprs]-[A-Za-z0-9-]{16,})\b"), "[REDACTED_SLACK_TOKEN]"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|passwd|pwd)\s*[:=]\s*['\"]?[^'\"\s,;]{8,}"
        ),
        r"\1=[REDACTED_SECRET]",
    ),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\d[ -]*?){13,16}\b"), "[REDACTED_NUMBER]"),
)

SUPPORT_OMITTED_KEYS = {
    "raw_text",
    "content",
    "context_pack",
    "captures",
    "memories",
    "tasks",
    "entities",
    "edges",
}

SUPPORT_PATH_KEYS = {
    "backup_path",
    "db_path",
    "events_path",
    "index_path",
    "manifest_path",
    "path",
    "settings_path",
    "vault_path",
}


def _env_int(name: str, default: int, *, minimum: int = 0, maximum: int = 3650) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return min(maximum, max(minimum, parsed))


def backup_retention_policy() -> dict[str, int]:
    return {
        "keep_latest": _env_int("CORTEX_BACKUP_RETENTION_COUNT", DEFAULT_BACKUP_RETENTION_COUNT, minimum=0, maximum=500),
        "max_age_days": _env_int("CORTEX_BACKUP_RETENTION_DAYS", DEFAULT_BACKUP_RETENTION_DAYS, minimum=0, maximum=3650),
    }


def _ratio(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(max(0.0, min(1.0, float(numerator) / float(denominator))), 4)


def _normalize_source_key(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", str(value or "").strip().lower()).strip("-")


def _normalize_source_policies(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    policies: dict[str, dict[str, Any]] = {}
    for raw_source, raw_policy in value.items():
        source = str(raw_source or "").strip()[:80]
        if not source or not isinstance(raw_policy, dict):
            continue
        mode = _normalize_source_key(str(raw_policy.get("mode") or "default")) or "default"
        if mode not in {"default", "trusted", "review", "excluded"}:
            mode = "default"
        if mode == "default":
            continue
        allow_ai_context = bool(raw_policy.get("allow_ai_context", mode != "excluded"))
        review_required = bool(raw_policy.get("review_required", mode in {"review", "excluded"}))
        if mode == "excluded":
            allow_ai_context = False
            review_required = True
        policies[source] = {
            "mode": mode,
            "allow_ai_context": allow_ai_context,
            "review_required": review_required,
        }
    return policies


def _normalize_identity_aliases(value: Any) -> list[str]:
    raw_values: list[Any]
    if isinstance(value, dict):
        raw_values = []
        for item in value.values():
            if isinstance(item, (list, tuple, set)):
                raw_values.extend(item)
            else:
                raw_values.append(item)
    elif isinstance(value, str):
        raw_values = re.split(r"[,;\n]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw_values = []
    aliases: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        alias = str(raw or "").strip()
        alias = re.sub(r"\s+", " ", alias)[:120]
        key = alias.lower()
        if alias and key not in seen:
            aliases.append(alias)
            seen.add(key)
        if len(aliases) >= 40:
            break
    return aliases


def normalize_token_scopes(scopes: list[str] | tuple[str, ...] | str | None) -> list[str]:
    if scopes is None:
        values = list(DEFAULT_MCP_TOKEN_SCOPES)
    elif isinstance(scopes, str):
        values = [item.strip().lower() for item in scopes.split(",")]
    else:
        values = [str(item).strip().lower() for item in scopes]
    return sorted({scope for scope in values if scope in MCP_TOKEN_SCOPES})


def memory_layer(kind: str | None, value: str | None = None) -> str:
    explicit = (value or "").strip().lower()
    if explicit in MEMORY_LAYERS:
        return explicit
    return MEMORY_LAYER_BY_KIND.get((kind or "").strip().lower(), "semantic")


def query_layer_boosts(query: str) -> dict[str, float]:
    tokens = set(re.findall(r"[a-z0-9_]+", query.lower()))
    boosts: dict[str, float] = {}
    for layers, keywords in LAYER_QUERY_INTENTS:
        if tokens & keywords:
            for layer in layers:
                boosts[layer] = max(boosts.get(layer, 0.0), LAYER_RETRIEVAL_BOOST)
    if "what happened" in query.lower():
        boosts["episodic"] = max(boosts.get("episodic", 0.0), LAYER_RETRIEVAL_BOOST)
    return boosts


def _path_event_summary(path: str) -> dict[str, Any]:
    candidate = Path(path).expanduser()
    kind = "directory" if candidate.is_dir() else "file" if candidate.is_file() else "missing"
    return {
        "name": candidate.name,
        "suffix": candidate.suffix.lower(),
        "kind": kind,
    }


class CortexStore:
    def __init__(self, db_path, vault_path: str | Path | None = None):
        self.db_path = Path(db_path)
        resolved_vault_path = Path(vault_path).expanduser() if vault_path else self.db_path.parent
        self.vault = CortexVault(resolved_vault_path, self.db_path)
        self.vault.ensure()

    def settings(self, user_id: str) -> dict[str, Any]:
        with connect(self.db_path) as conn:
            return self._settings(conn, user_id)

    def ensure_vault_backfilled(self, user_id: str) -> dict[str, Any]:
        vault_diagnostics = self.vault.diagnostics()
        existing_records = sum(
            vault_diagnostics["record_counts"].get(key, 0)
            for key in ("captures", "memories", "tasks", "entities", "graph_edges")
        )
        if existing_records:
            return {"backfilled": False, "reason": "vault already has records", "records": existing_records}

        with connect(self.db_path) as conn:
            capture_rows = conn.execute("SELECT * FROM captures WHERE user_id = ? ORDER BY captured_at", (user_id,)).fetchall()
            if not capture_rows:
                self.vault.write_settings(user_id, self._settings(conn, user_id))
                return {"backfilled": False, "reason": "index has no captures", "records": 0}

            memory_rows = conn.execute("SELECT * FROM memories WHERE user_id = ? ORDER BY captured_at", (user_id,)).fetchall()
            task_rows = conn.execute("SELECT * FROM tasks WHERE user_id = ? ORDER BY captured_at", (user_id,)).fetchall()
            entity_rows = conn.execute("SELECT * FROM entities WHERE user_id = ? ORDER BY last_seen", (user_id,)).fetchall()
            edge_rows = conn.execute("SELECT * FROM graph_edges WHERE user_id = ? ORDER BY created_at", (user_id,)).fetchall()
            event_rows = conn.execute("SELECT * FROM memory_events WHERE user_id = ? ORDER BY created_at", (user_id,)).fetchall()
            self.vault.write_settings(user_id, self._settings(conn, user_id))

            for row in capture_rows:
                self.vault.write_capture(
                    {
                        "id": row["id"],
                        "user_id": row["user_id"],
                        "import_id": row["import_id"] if "import_id" in row.keys() else None,
                        "source": row["source"],
                        "source_url": row["source_url"],
                        "title": row["title"],
                        "raw_text": row["raw_text"],
                        "raw_hash": row["raw_hash"],
                        "summary": row["summary"],
                        "review_status": row["review_status"],
                        "approved_at": row["approved_at"],
                        "archived_at": row["archived_at"],
                        "captured_at": row["captured_at"],
                    }
                )
            for row in memory_rows:
                self.vault.write_memory(
                    {
                        "id": row["id"],
                        "capture_id": row["capture_id"],
                        "user_id": row["user_id"],
                        "kind": row["kind"],
                        "layer": memory_layer(row["kind"], row["layer"] if "layer" in row.keys() else None),
                        "content": row["content"],
                        "summary": row["summary"],
                        "source": row["source"],
                        "source_url": row["source_url"],
                        "confidence": row["confidence"],
                        "importance": row["importance"],
                        "status": row["status"],
                        "topics": json.loads(row["topics_json"] or "[]"),
                        "entity_ids": json.loads(row["entity_ids_json"] or "[]"),
                        "occurred_at": row["occurred_at"],
                        "captured_at": row["captured_at"],
                        "updated_at": row["updated_at"],
                        "raw_excerpt": row["raw_excerpt"],
                    }
                )
            for row in task_rows:
                self.vault.write_task(
                    {
                        "id": row["id"],
                        "capture_id": row["capture_id"],
                        "user_id": row["user_id"],
                        "kind": row["kind"],
                        "content": row["content"],
                        "status": row["status"],
                        "importance": row["importance"],
                        "topics": json.loads(row["topics_json"] or "[]"),
                        "entity_ids": json.loads(row["entity_ids_json"] or "[]"),
                        "captured_at": row["captured_at"],
                    }
                )
            for row in entity_rows:
                self.vault.write_entity(
                    {
                        "id": row["id"],
                        "user_id": row["user_id"],
                        "kind": row["kind"],
                        "name": row["name"],
                        "aliases": json.loads(row["aliases_json"] or "[]"),
                        "context": row["context"],
                        "first_seen": row["first_seen"],
                        "last_seen": row["last_seen"],
                    }
                )
            for row in edge_rows:
                self.vault.write_edge(dict(row))
            if vault_diagnostics["event_count"] == 0:
                for row in event_rows:
                    try:
                        metadata = json.loads(row["metadata_json"] or "{}")
                    except json.JSONDecodeError:
                        metadata = {}
                    self.vault.append_event(
                        {
                            "id": row["id"],
                            "user_id": row["user_id"],
                            "object_id": row["object_id"],
                            "object_type": row["object_type"],
                            "event_type": row["event_type"],
                            "metadata": metadata,
                            "created_at": row["created_at"],
                        }
                    )
        return {
            "backfilled": True,
            "captures": len(capture_rows),
            "memories": len(memory_rows),
            "tasks": len(task_rows),
            "entities": len(entity_rows),
            "edges": len(edge_rows),
            "events": len(event_rows),
        }

    def create_mcp_token(self, user_id: str, *, label: str = "MCP integration", scopes: list[str] | tuple[str, ...] | str | None = None) -> dict[str, Any]:
        token = "cxm_" + secrets.token_urlsafe(32).replace("-", "").replace("_", "")[:43]
        metadata = self.ensure_mcp_token(user_id, token, label=label, scopes=scopes)
        return {**metadata, "token": token}

    def create_api_token(self, user_id: str, *, label: str = "REST API client", scopes: list[str] | tuple[str, ...] | str | None = None) -> dict[str, Any]:
        token = "cxa_" + secrets.token_urlsafe(32).replace("-", "").replace("_", "")[:43]
        metadata = self.ensure_api_token(user_id, token, label=label, scopes=scopes)
        return {**metadata, "token": token}

    def ensure_api_token(
        self,
        user_id: str,
        token: str,
        *,
        label: str = "REST API client",
        scopes: list[str] | tuple[str, ...] | str | None = None,
        token_id: str | None = None,
    ) -> dict[str, Any]:
        return self._ensure_token(
            user_id,
            token,
            audience="api",
            label=label,
            scopes=scopes,
            token_id=token_id or stable_id("tok_", f"{user_id}:api:{label}"),
        )

    def ensure_mcp_token(
        self,
        user_id: str,
        token: str,
        *,
        label: str = "MCP integration",
        scopes: list[str] | tuple[str, ...] | str | None = None,
        token_id: str | None = None,
    ) -> dict[str, Any]:
        return self._ensure_token(
            user_id,
            token,
            audience="mcp",
            label=label,
            scopes=scopes,
            token_id=token_id or stable_id("tok_", f"{user_id}:mcp:{label}"),
        )

    def _ensure_token(
        self,
        user_id: str,
        token: str,
        *,
        audience: str,
        label: str,
        scopes: list[str] | tuple[str, ...] | str | None,
        token_id: str,
    ) -> dict[str, Any]:
        normalized = token.strip()
        if not normalized:
            raise ValueError(f"{audience.upper()} token is required")
        timestamp = now_iso()
        resolved_scopes = normalize_token_scopes(scopes)
        with connect(self.db_path) as conn:
            existing = conn.execute("SELECT token_salt FROM api_tokens WHERE token_id = ?", (token_id,)).fetchone()
            if existing:
                salt = existing["token_salt"]
            else:
                salt = secrets.token_hex(16)
            token_hash = self._token_hash(normalized, salt)
            conn.execute(
                """
                INSERT OR REPLACE INTO api_tokens
                (token_id, user_id, label, audience, token_salt, token_hash, scopes_json, created_at, updated_at, last_used_at, revoked_at)
                VALUES (
                  ?,
                  ?,
                  ?,
                  ?,
                  ?,
                  ?,
                  ?,
                  COALESCE((SELECT created_at FROM api_tokens WHERE token_id = ?), ?),
                  ?,
                  (SELECT last_used_at FROM api_tokens WHERE token_id = ?),
                  NULL
                )
                """,
                (
                    token_id,
                    user_id,
                    label[:120],
                    audience,
                    salt,
                    token_hash,
                    json.dumps(resolved_scopes),
                    token_id,
                    timestamp,
                    timestamp,
                    token_id,
                ),
            )
        return {
            "token_id": token_id,
            "user_id": user_id,
            "label": label[:120],
            "audience": audience,
            "scopes": resolved_scopes,
            "updated_at": timestamp,
        }

    def list_tokens(self, user_id: str, *, audience: str | None = None, include_revoked: bool = False) -> list[dict[str, Any]]:
        filters = ["user_id = ?"]
        values: list[Any] = [user_id]
        if audience:
            filters.append("audience = ?")
            values.append(audience)
        if not include_revoked:
            filters.append("revoked_at IS NULL")
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT token_id, user_id, label, audience, scopes_json, created_at, updated_at, last_used_at, revoked_at
                FROM api_tokens
                WHERE {" AND ".join(filters)}
                ORDER BY updated_at DESC
                """,
                tuple(values),
            ).fetchall()
        return [
            {
                "token_id": row["token_id"],
                "user_id": row["user_id"],
                "label": row["label"],
                "audience": row["audience"],
                "scopes": json.loads(row["scopes_json"] or "[]"),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "last_used_at": row["last_used_at"],
                "revoked_at": row["revoked_at"],
            }
            for row in rows
        ]

    def revoke_token(self, user_id: str, token_id: str) -> dict[str, Any] | None:
        timestamp = now_iso()
        with connect(self.db_path) as conn:
            existing = conn.execute(
                """
                SELECT token_id, user_id, label, audience, scopes_json, created_at, updated_at, last_used_at, revoked_at
                FROM api_tokens
                WHERE user_id = ? AND token_id = ?
                """,
                (user_id, token_id),
            ).fetchone()
            if not existing:
                return None
            revoked_at = existing["revoked_at"] or timestamp
            if not existing["revoked_at"]:
                conn.execute("UPDATE api_tokens SET revoked_at = ?, updated_at = ? WHERE user_id = ? AND token_id = ?", (revoked_at, timestamp, user_id, token_id))
        return {
            "token_id": existing["token_id"],
            "user_id": existing["user_id"],
            "label": existing["label"],
            "audience": existing["audience"],
            "scopes": json.loads(existing["scopes_json"] or "[]"),
            "created_at": existing["created_at"],
            "updated_at": timestamp,
            "last_used_at": existing["last_used_at"],
            "revoked_at": revoked_at,
            "revoked": True,
        }

    def authenticate_api_token(self, token: str) -> dict[str, Any] | None:
        return self._authenticate_token(token, audience="api")

    def authenticate_mcp_token(self, token: str) -> dict[str, Any] | None:
        return self._authenticate_token(token, audience="mcp")

    def _authenticate_token(self, token: str, *, audience: str) -> dict[str, Any] | None:
        normalized = token.strip()
        if not normalized:
            return None
        timestamp = now_iso()
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT token_id, user_id, label, audience, token_salt, token_hash, scopes_json, created_at, last_used_at
                FROM api_tokens
                WHERE audience = ? AND revoked_at IS NULL
                ORDER BY created_at DESC
                """,
                (audience,),
            ).fetchall()
            for row in rows:
                candidate = self._token_hash(normalized, row["token_salt"])
                if not secrets.compare_digest(candidate, row["token_hash"]):
                    continue
                conn.execute("UPDATE api_tokens SET last_used_at = ? WHERE token_id = ?", (timestamp, row["token_id"]))
                return {
                    "token_id": row["token_id"],
                    "user_id": row["user_id"],
                    "label": row["label"],
                    "audience": row["audience"],
                    "scopes": self._json_list(row["scopes_json"]),
                    "created_at": row["created_at"],
                    "last_used_at": timestamp,
                    "admin": False,
                }
        return None

    def enqueue_capture(
        self,
        *,
        user_id: str,
        content: str,
        source: str,
        source_url: str | None,
        title: str | None,
        import_id: str | None = None,
    ) -> dict[str, Any]:
        content = content.strip()
        if not content:
            raise ValueError("content is required")
        if len(content) > 200_000:
            raise ValueError("content is too large")
        captured_at = now_iso()
        normalized_source = (source or "macos")[:80]
        capture_id = stable_id("cap_", user_id + normalized_source + captured_at + content[:120])
        raw_hash = stable_id("", content)
        summary = "Queued for memory extraction."
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            review_status = "pending" if user_settings["review_new_captures"] else "approved"
            approved_at = None if review_status == "pending" else captured_at
            conn.execute(
                """
                INSERT INTO captures
                (id, user_id, import_id, source, source_url, title, raw_text, raw_hash, summary, review_status, approved_at, captured_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  user_id = excluded.user_id,
                  import_id = COALESCE(captures.import_id, excluded.import_id),
                  source = excluded.source,
                  source_url = excluded.source_url,
                  title = excluded.title,
                  raw_text = excluded.raw_text,
                  raw_hash = excluded.raw_hash,
                  summary = excluded.summary,
                  review_status = excluded.review_status,
                  approved_at = excluded.approved_at,
                  captured_at = excluded.captured_at
                """,
                (capture_id, user_id, import_id, normalized_source, source_url, title, content, raw_hash, summary, review_status, approved_at, captured_at),
            )
            job = self._enqueue_job(
                conn,
                user_id=user_id,
                job_type="extract_capture",
                object_type="capture",
                object_id=capture_id,
                unique_key=f"extract_capture:{capture_id}:{raw_hash}",
                payload={
                    "capture_id": capture_id,
                    "source": normalized_source,
                    "source_url": source_url,
                    "title": title,
                    "captured_at": captured_at,
                    "raw_hash": raw_hash,
                },
                priority=50,
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO capture_processing_state
                (capture_id, user_id, ingest_status, extraction_status, embedding_status, memory_count, task_count, entity_count, last_job_id, last_error, queued_at, updated_at)
                VALUES (?, ?, 'accepted', 'queued', 'pending', 0, 0, 0, ?, NULL, ?, ?)
                """,
                (capture_id, user_id, job["id"], captured_at, captured_at),
            )
            self._event(conn, user_id, capture_id, "capture", "queued", {"source": normalized_source, "job_id": job["id"]})
            self.vault.write_settings(user_id, user_settings)
            self.vault.write_capture(
                {
                    "id": capture_id,
                    "user_id": user_id,
                    "import_id": import_id,
                    "source": normalized_source,
                    "source_url": source_url,
                    "title": title,
                    "raw_text": content,
                    "raw_hash": raw_hash,
                    "summary": summary,
                    "review_status": review_status,
                    "approved_at": approved_at,
                    "archived_at": None,
                    "captured_at": captured_at,
                    "processing": {"ingest_status": "accepted", "extraction_status": "queued", "job_id": job["id"]},
                }
            )
        return {
            "capture_id": capture_id,
            "status": "queued",
            "summary": summary,
            "jobs": [job],
            "processing": self.capture_status(user_id, capture_id),
        }

    def capture_status(self, user_id: str, capture_id: str) -> dict[str, Any]:
        with connect(self.db_path) as conn:
            capture = conn.execute(
                "SELECT id, source, title, review_status, captured_at FROM captures WHERE user_id = ? AND id = ?",
                (user_id, capture_id),
            ).fetchone()
            if not capture:
                raise FileNotFoundError("Capture not found")
            state = conn.execute(
                "SELECT * FROM capture_processing_state WHERE user_id = ? AND capture_id = ?",
                (user_id, capture_id),
            ).fetchone()
            jobs = conn.execute(
                """
                SELECT *
                FROM memory_jobs
                WHERE user_id = ?
                  AND (
                    (object_type = 'capture' AND object_id = ?)
                    OR (
                      object_type = 'memory'
                      AND object_id IN (
                        SELECT id FROM memories WHERE user_id = ? AND capture_id = ?
                      )
                    )
                  )
                ORDER BY created_at DESC
                """,
                (user_id, capture_id, user_id, capture_id),
            ).fetchall()
        state_payload = dict(state) if state else {
            "capture_id": capture_id,
            "user_id": user_id,
            "ingest_status": "materialized",
            "extraction_status": "succeeded",
            "embedding_status": "available",
            "memory_count": None,
            "task_count": None,
            "entity_count": None,
            "last_job_id": None,
            "last_error": None,
            "queued_at": capture["captured_at"],
            "started_at": None,
            "completed_at": capture["captured_at"],
            "updated_at": capture["captured_at"],
        }
        return {
            "capture": dict(capture),
            "processing": state_payload,
            "jobs": [self._job_from_row(row) for row in jobs],
        }

    def supported_import_sources(self) -> list[dict[str, Any]]:
        return supported_sources()

    def source_connector_catalog(self) -> list[dict[str, Any]]:
        import_sources = {item["id"]: item for item in supported_sources()}
        catalog: list[dict[str, Any]] = []
        for item in SOURCE_CONNECTOR_CATALOG:
            source_id = item["id"]
            import_info = import_sources.get(source_id)
            catalog.append(
                {
                    **item,
                    "import_status": import_info["status"] if import_info else "generic" if item["live_status"] in {"planned", "import_ready"} else "export_only",
                    "formats": import_info["formats"] if import_info else [],
                }
            )
        return catalog

    def source_readiness_report(self, user_id: str) -> dict[str, Any]:
        catalog = self.source_connector_catalog()
        accounts = self.list_source_accounts(user_id, include_disconnected=True)
        cursors = self.list_sync_cursors(user_id)
        policies = self.settings(user_id).get("source_policies", {})
        with connect(self.db_path) as conn:
            capture_rows = conn.execute(
                """
                SELECT
                  c.source,
                  COUNT(*) AS captures,
                  SUM(CASE WHEN c.review_status = 'pending' THEN 1 ELSE 0 END) AS pending,
                  SUM(CASE WHEN c.review_status = 'approved' THEN 1 ELSE 0 END) AS approved,
                  SUM(CASE WHEN c.review_status = 'archived' THEN 1 ELSE 0 END) AS archived,
                  COUNT(DISTINCT CASE WHEN m.status = 'active' THEN m.id END) AS active_memories,
                  COUNT(DISTINCT CASE WHEN m.status = 'active' AND COALESCE(m.source_url, '') != '' THEN m.id END) AS cited_memories,
                  MAX(c.captured_at) AS last_imported_at
                FROM captures c
                LEFT JOIN memories m ON m.capture_id = c.id AND m.user_id = c.user_id
                WHERE c.user_id = ?
                GROUP BY c.source
                """,
                (user_id,),
            ).fetchall()
        captures_by_source = {row["source"]: dict(row) for row in capture_rows}
        accounts_by_source: dict[str, list[dict[str, Any]]] = {}
        for account in accounts:
            accounts_by_source.setdefault(account["source"], []).append(account)
        cursors_by_source: dict[str, list[dict[str, Any]]] = {}
        for cursor in cursors:
            cursors_by_source.setdefault(cursor["source"], []).append(cursor)

        rows: list[dict[str, Any]] = []
        catalog_ids = {item["id"] for item in catalog}
        extra_sources = sorted(set(captures_by_source) - catalog_ids)
        for item in [*catalog, *({"id": source, "name": source, "category": "Imported", "auth": "import", "live_status": "imported", "scopes": [], "notes": "", "import_status": "native", "formats": []} for source in extra_sources)]:
            source = item["id"]
            source_accounts = accounts_by_source.get(source, [])
            active_accounts = [account for account in source_accounts if not account.get("disconnected_at")]
            source_cursors = cursors_by_source.get(source, [])
            stats = captures_by_source.get(source, {})
            pending = int(stats.get("pending") or 0)
            approved = int(stats.get("approved") or 0)
            archived = int(stats.get("archived") or 0)
            captures = int(stats.get("captures") or 0)
            active_memories = int(stats.get("active_memories") or 0)
            cited_memories = int(stats.get("cited_memories") or 0)
            account_errors = [
                account.get("last_error")
                for account in source_accounts
                if account.get("last_error")
            ]
            cursor_errors = [
                cursor.get("last_error")
                for cursor in source_cursors
                if cursor.get("last_error")
            ]
            revoked_or_disconnected = any(
                account.get("disconnected_at")
                or str(account.get("auth_state") or "").lower() in {"revoked", "expired", "error"}
                or str(account.get("status") or "").lower() in {"error", "failed", "disconnected"}
                for account in source_accounts
            )
            has_attention = bool(account_errors or cursor_errors or revoked_or_disconnected)
            import_status = str(item.get("import_status") or "")
            supports_import = import_status in {"native", "generic", "import_ready"} or bool(item.get("formats"))
            live_status = str(item.get("live_status") or "")
            has_completed_sync = any(cursor.get("last_completed_at") for cursor in source_cursors) or any(account.get("last_sync_at") for account in active_accounts)
            if has_attention:
                status = "needs_attention"
                next_action = (account_errors + cursor_errors)[0] if account_errors or cursor_errors else "Reconnect or review this source account."
            elif pending:
                status = "needs_review"
                next_action = f"Review {pending} pending capture{'s' if pending != 1 else ''}."
            elif has_completed_sync:
                status = "synced"
                next_action = "Source sync has completed; review new memories as they arrive."
            elif active_accounts:
                status = "connected"
                next_action = "Account is registered; run or wait for the next sync."
            elif captures or active_memories:
                status = "imported"
                next_action = "Imported data is available for retrieval."
            elif supports_import:
                status = "import_ready"
                next_action = "Import an export file or folder for this source."
            elif live_status == "planned":
                status = "planned"
                next_action = "Live OAuth is planned; use exports today."
            else:
                status = "available"
                next_action = "Add this source when it contains useful personal context."

            last_seen = stats.get("last_imported_at") or next((account.get("last_sync_at") for account in active_accounts if account.get("last_sync_at")), None)
            rows.append(
                {
                    "source": source,
                    "name": item.get("name") or source,
                    "category": item.get("category") or "Other",
                    "status": status,
                    "next_action": next_action,
                    "import_status": import_status,
                    "live_status": live_status,
                    "auth": item.get("auth"),
                    "formats": item.get("formats") or [],
                    "accounts": len(active_accounts),
                    "cursors": len(source_cursors),
                    "captures": captures,
                    "pending": pending,
                    "approved": approved,
                    "archived": archived,
                    "active_memories": active_memories,
                    "citation_coverage": _ratio(cited_memories, active_memories),
                    "last_seen_at": last_seen,
                    "policy": policies.get(source) or {"mode": "default", "allow_ai_context": True, "review_required": False},
                    "warnings": [value for value in [*account_errors, *cursor_errors] if value],
                }
            )

        status_rank = {
            "needs_attention": 0,
            "needs_review": 1,
            "import_ready": 2,
            "connected": 3,
            "synced": 4,
            "imported": 5,
            "planned": 6,
            "available": 7,
        }
        rows.sort(key=lambda row: (status_rank.get(row["status"], 9), -int(row["active_memories"]), row["name"]))
        summary = {
            "sources_total": len(rows),
            "import_ready": sum(1 for row in rows if row["status"] == "import_ready"),
            "planned_live": sum(1 for row in rows if row["live_status"] == "planned"),
            "connected": sum(int(row["accounts"]) for row in rows),
            "synced": sum(1 for row in rows if row["status"] == "synced"),
            "sources_with_data": sum(1 for row in rows if row["captures"] or row["active_memories"]),
            "needs_review": sum(1 for row in rows if row["status"] == "needs_review"),
            "needs_attention": sum(1 for row in rows if row["status"] == "needs_attention"),
            "active_memories": sum(int(row["active_memories"]) for row in rows),
        }
        recommendations: list[str] = []
        if summary["needs_attention"]:
            recommendations.append("Resolve source account or sync errors before relying on those memories.")
        if summary["needs_review"]:
            recommendations.append("Review pending source captures so they can become trusted model memory.")
        if not summary["sources_with_data"]:
            recommendations.append("Import one high-signal source such as ChatGPT, Claude, Gmail, Notion, Slack, or notes.")
        if not recommendations:
            recommendations.append("Source readiness is healthy for local beta use.")
        return {
            "generated_at": now_iso(),
            "summary": summary,
            "sources": rows,
            "recommendations": recommendations,
        }

    def list_source_accounts(self, user_id: str, *, include_disconnected: bool = False) -> list[dict[str, Any]]:
        filters = ["user_id = ?"]
        values: list[Any] = [user_id]
        if not include_disconnected:
            filters.append("disconnected_at IS NULL")
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM source_accounts
                WHERE {" AND ".join(filters)}
                ORDER BY updated_at DESC, source, account_label
                """,
                tuple(values),
            ).fetchall()
        return [self._source_account_from_row(row) for row in rows]

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
        policy: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        last_error: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_source = _normalize_source_key(source)
        if not normalized_source:
            raise ValueError("source is required")
        catalog_entry = next((item for item in SOURCE_CONNECTOR_CATALOG if item["id"] == normalized_source), None)
        label = (account_label or (catalog_entry or {}).get("name") or normalized_source).strip()[:160]
        identifier = (account_identifier or "").strip()[:240] or None
        connection = _normalize_source_key(connection_type or "manual") or "manual"
        account_status = _normalize_source_key(status or "available") or "available"
        auth = _normalize_source_key(auth_state or "not_configured") or "not_configured"
        timestamp = now_iso()
        resolved_id = account_id or stable_id("sacct_", f"{user_id}:{normalized_source}:{identifier or label}")
        resolved_policy = policy or {}
        resolved_metadata = metadata or {}
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO source_accounts
                (id, user_id, source, account_label, account_identifier, connection_type, status, auth_state, policy_json, metadata_json, last_sync_at, last_error, created_at, updated_at, disconnected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL)
                ON CONFLICT(id) DO UPDATE SET
                  source = excluded.source,
                  account_label = excluded.account_label,
                  account_identifier = excluded.account_identifier,
                  connection_type = excluded.connection_type,
                  status = excluded.status,
                  auth_state = excluded.auth_state,
                  policy_json = excluded.policy_json,
                  metadata_json = excluded.metadata_json,
                  last_error = excluded.last_error,
                  updated_at = excluded.updated_at,
                  disconnected_at = NULL
                """,
                (
                    resolved_id,
                    user_id,
                    normalized_source,
                    label,
                    identifier,
                    connection,
                    account_status,
                    auth,
                    json.dumps(resolved_policy),
                    json.dumps(resolved_metadata),
                    last_error,
                    timestamp,
                    timestamp,
                ),
            )
            self._event(
                conn,
                user_id,
                resolved_id,
                "source_account",
                "upserted",
                {"source": normalized_source, "connection_type": connection, "status": account_status, "auth_state": auth},
            )
            row = conn.execute("SELECT * FROM source_accounts WHERE user_id = ? AND id = ?", (user_id, resolved_id)).fetchone()
        account = self._source_account_from_row(row)
        self.vault.write_source_account(account)
        return account

    def disconnect_source_account(self, user_id: str, account_id: str) -> dict[str, Any] | None:
        timestamp = now_iso()
        with connect(self.db_path) as conn:
            existing = conn.execute("SELECT * FROM source_accounts WHERE user_id = ? AND id = ?", (user_id, account_id)).fetchone()
            if not existing:
                return None
            conn.execute(
                """
                UPDATE source_accounts
                SET status = 'disconnected',
                    auth_state = 'revoked',
                    updated_at = ?,
                    disconnected_at = ?
                WHERE user_id = ? AND id = ?
                """,
                (timestamp, timestamp, user_id, account_id),
            )
            self._event(conn, user_id, account_id, "source_account", "disconnected", {"source": existing["source"]})
            row = conn.execute("SELECT * FROM source_accounts WHERE user_id = ? AND id = ?", (user_id, account_id)).fetchone()
        account = self._source_account_from_row(row)
        self.vault.write_source_account(account)
        return account

    def list_sync_cursors(self, user_id: str, *, source_account_id: str | None = None) -> list[dict[str, Any]]:
        filters = ["user_id = ?"]
        values: list[Any] = [user_id]
        if source_account_id:
            filters.append("source_account_id = ?")
            values.append(source_account_id)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM sync_cursors
                WHERE {" AND ".join(filters)}
                ORDER BY updated_at DESC, source, cursor_name
                """,
                tuple(values),
            ).fetchall()
        return [self._sync_cursor_from_row(row) for row in rows]

    def upsert_sync_cursor(
        self,
        user_id: str,
        *,
        source: str,
        cursor_name: str,
        cursor_value: str | None = None,
        high_water_mark: str | None = None,
        state: dict[str, Any] | None = None,
        source_account_id: str | None = None,
        last_error: str | None = None,
        completed: bool = True,
    ) -> dict[str, Any]:
        normalized_source = _normalize_source_key(source)
        normalized_name = _normalize_source_key(cursor_name)
        if not normalized_source or not normalized_name:
            raise ValueError("source and cursor_name are required")
        timestamp = now_iso()
        account_id = (source_account_id or "").strip() or None
        if account_id:
            with connect(self.db_path) as conn:
                account = conn.execute("SELECT * FROM source_accounts WHERE user_id = ? AND id = ?", (user_id, account_id)).fetchone()
            if not account:
                raise ValueError("source account not found")
            normalized_source = account["source"]
        cursor_id = stable_id("sync_", f"{user_id}:{account_id or normalized_source}:{normalized_name}")
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO sync_cursors
                (id, user_id, source_account_id, source, cursor_name, cursor_value, high_water_mark, state_json, last_started_at, last_completed_at, last_error, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  cursor_value = excluded.cursor_value,
                  high_water_mark = excluded.high_water_mark,
                  state_json = excluded.state_json,
                  last_started_at = excluded.last_started_at,
                  last_completed_at = excluded.last_completed_at,
                  last_error = excluded.last_error,
                  updated_at = excluded.updated_at
                """,
                (
                    cursor_id,
                    user_id,
                    account_id,
                    normalized_source,
                    normalized_name,
                    cursor_value,
                    high_water_mark,
                    json.dumps(state or {}),
                    timestamp,
                    timestamp if completed and not last_error else None,
                    last_error,
                    timestamp,
                    timestamp,
                ),
            )
            if account_id and completed and not last_error:
                conn.execute("UPDATE source_accounts SET last_sync_at = ?, updated_at = ?, last_error = NULL WHERE user_id = ? AND id = ?", (timestamp, timestamp, user_id, account_id))
            elif account_id and last_error:
                conn.execute("UPDATE source_accounts SET last_error = ?, updated_at = ? WHERE user_id = ? AND id = ?", (last_error, timestamp, user_id, account_id))
            self._event(
                conn,
                user_id,
                cursor_id,
                "sync_cursor",
                "updated",
                {"source": normalized_source, "source_account_id": account_id, "cursor_name": normalized_name, "completed": completed, "success": not bool(last_error)},
            )
            row = conn.execute("SELECT * FROM sync_cursors WHERE user_id = ? AND id = ?", (user_id, cursor_id)).fetchone()
            account_row = conn.execute("SELECT * FROM source_accounts WHERE user_id = ? AND id = ?", (user_id, account_id)).fetchone() if account_id else None
        cursor = self._sync_cursor_from_row(row)
        self.vault.write_sync_cursor(cursor)
        if account_row:
            self.vault.write_source_account(self._source_account_from_row(account_row))
        return cursor

    def register_sync_device(
        self,
        user_id: str,
        *,
        device_name: str,
        platform: str = "unknown",
        device_key: str | None = None,
        public_key: str | None = None,
        capabilities: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        name = (device_name or "").strip()[:160]
        if not name:
            raise ValueError("device_name is required")
        normalized_platform = _normalize_source_key(platform or "unknown") or "unknown"
        generated_key = ""
        key_material = (device_key or public_key or "").strip()
        if not key_material:
            generated_key = "csd_" + secrets.token_urlsafe(32).replace("-", "").replace("_", "")[:43]
            key_material = generated_key
        device_key_hash = hashlib.sha256(key_material.encode("utf-8")).hexdigest()
        device_id = stable_id("sdev_", f"{user_id}:{device_key_hash}")
        timestamp = now_iso()
        capability_values = sorted({str(value).strip()[:80] for value in (capabilities or []) if str(value).strip()})
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO sync_devices
                (id, user_id, device_name, platform, device_key_hash, public_key, capabilities_json, first_cursor, last_cursor, last_seen_at, created_at, updated_at, revoked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, NULL)
                ON CONFLICT(id) DO UPDATE SET
                  device_name = excluded.device_name,
                  platform = excluded.platform,
                  public_key = excluded.public_key,
                  capabilities_json = excluded.capabilities_json,
                  updated_at = excluded.updated_at,
                  revoked_at = NULL
                """,
                (
                    device_id,
                    user_id,
                    name,
                    normalized_platform,
                    device_key_hash,
                    (public_key or "").strip()[:2000] or None,
                    json.dumps(capability_values),
                    timestamp,
                    timestamp,
                ),
            )
            self._event(conn, user_id, device_id, "sync_device", "registered", {"platform": normalized_platform, "capabilities": capability_values})
            row = conn.execute("SELECT * FROM sync_devices WHERE user_id = ? AND id = ?", (user_id, device_id)).fetchone()
        device = self._sync_device_from_row(row)
        self.vault.write_sync_device(self._sync_device_record_from_row(row))
        if generated_key:
            return {**device, "device_key": generated_key}
        return device

    def list_sync_devices(self, user_id: str, *, include_revoked: bool = False) -> list[dict[str, Any]]:
        filters = ["user_id = ?"]
        values: list[Any] = [user_id]
        if not include_revoked:
            filters.append("revoked_at IS NULL")
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM sync_devices
                WHERE {" AND ".join(filters)}
                ORDER BY updated_at DESC, device_name
                """,
                tuple(values),
            ).fetchall()
        return [self._sync_device_from_row(row) for row in rows]

    def revoke_sync_device(self, user_id: str, device_id: str) -> dict[str, Any] | None:
        timestamp = now_iso()
        with connect(self.db_path) as conn:
            existing = conn.execute("SELECT * FROM sync_devices WHERE user_id = ? AND id = ?", (user_id, device_id)).fetchone()
            if not existing:
                return None
            conn.execute(
                """
                UPDATE sync_devices
                SET updated_at = ?,
                    revoked_at = ?
                WHERE user_id = ? AND id = ?
                """,
                (timestamp, timestamp, user_id, device_id),
            )
            self._event(conn, user_id, device_id, "sync_device", "revoked", {"platform": existing["platform"]})
            row = conn.execute("SELECT * FROM sync_devices WHERE user_id = ? AND id = ?", (user_id, device_id)).fetchone()
        device = self._sync_device_from_row(row)
        self.vault.write_sync_device(self._sync_device_record_from_row(row))
        return device

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
        stats: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_device_id = (device_id or "").strip()
        normalized_cursor = (cursor or "").strip()[:160]
        if not normalized_device_id:
            raise ValueError("device_id is required")
        if not normalized_cursor:
            raise ValueError("cursor is required")
        normalized_status = (status or "accepted").strip().lower()
        if normalized_status not in {"accepted", "uploaded", "failed"}:
            raise ValueError("status must be accepted, uploaded, or failed")
        timestamp = now_iso()
        receipt_id = stable_id("srec_", f"{user_id}:{normalized_device_id}:{normalized_cursor}")
        stats_payload = stats if isinstance(stats, dict) else {}
        with connect(self.db_path) as conn:
            device = conn.execute("SELECT * FROM sync_devices WHERE user_id = ? AND id = ?", (user_id, normalized_device_id)).fetchone()
            if not device:
                raise ValueError("sync device not found")
            if device["revoked_at"]:
                raise ValueError("sync device is revoked")
            conn.execute(
                """
                INSERT INTO sync_receipts
                (id, user_id, device_id, cursor, status, manifest_hash, remote_ref, error, stats_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, device_id, cursor) DO UPDATE SET
                  status = excluded.status,
                  manifest_hash = excluded.manifest_hash,
                  remote_ref = excluded.remote_ref,
                  error = excluded.error,
                  stats_json = excluded.stats_json,
                  updated_at = excluded.updated_at
                """,
                (
                    receipt_id,
                    user_id,
                    normalized_device_id,
                    normalized_cursor,
                    normalized_status,
                    (manifest_hash or "").strip()[:256] or None,
                    (remote_ref or "").strip()[:500] or None,
                    (error or "").strip()[:500] or None,
                    json.dumps(stats_payload),
                    timestamp,
                    timestamp,
                ),
            )
            self._event(
                conn,
                user_id,
                receipt_id,
                "sync_receipt",
                normalized_status,
                {"device_id": normalized_device_id, "cursor": normalized_cursor, "manifest_hash": (manifest_hash or "")[:80]},
            )
            row = conn.execute("SELECT * FROM sync_receipts WHERE user_id = ? AND id = ?", (user_id, receipt_id)).fetchone()
        receipt = self._sync_receipt_from_row(row)
        self.vault.write_sync_receipt(receipt)
        return receipt

    def list_sync_receipts(self, user_id: str, device_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        normalized_device_id = (device_id or "").strip()
        limit = max(1, min(200, int(limit)))
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM sync_receipts
                WHERE user_id = ? AND device_id = ?
                ORDER BY updated_at DESC, cursor DESC
                LIMIT ?
                """,
                (user_id, normalized_device_id, limit),
            ).fetchall()
        return [self._sync_receipt_from_row(row) for row in rows]

    def analyze_import_sources(self, paths: list[str], source_hint: str = "", max_records: int = 500) -> dict[str, Any]:
        return analyze_sources(paths, source_hint=source_hint, max_records=max_records)

    def import_sources(
        self,
        *,
        user_id: str,
        paths: list[str],
        source_hint: str = "",
        processing: str = "async",
        max_records: int = 1000,
    ) -> dict[str, Any]:
        cleaned_paths = [str(path).strip() for path in paths if str(path).strip()]
        if not cleaned_paths:
            raise ValueError("paths must include at least one local file or folder")
        if processing not in {"sync", "async"}:
            raise ValueError("processing must be sync or async")
        max_records = min(max(int(max_records), 1), 5000)
        started_at = now_iso()
        records = import_source_records(cleaned_paths, source_hint=source_hint, max_records=max_records)
        import_id = stable_id("imp_", user_id + "|".join(cleaned_paths) + started_at + secrets.token_hex(8))
        queued = 0
        saved = 0
        skipped = 0
        errors: list[dict[str, Any]] = []
        record_results: list[dict[str, Any]] = []
        source_counts: dict[str, int] = {}
        source_summary: list[dict[str, Any]] = []
        path_summaries = [_path_event_summary(path) for path in cleaned_paths[:20]]
        with connect(self.db_path) as conn:
            for record in records:
                source_counts[record.source] = source_counts.get(record.source, 0) + 1
            source_summary = [{"source": source, "count": count} for source, count in sorted(source_counts.items())]
            self._upsert_import_session(
                conn,
                {
                    "id": import_id,
                    "user_id": user_id,
                    "status": "running",
                    "source_hint": source_hint,
                    "processing": processing,
                    "paths": path_summaries,
                    "sources": source_summary,
                    "records_found": len(records),
                    "queued": 0,
                    "saved": 0,
                    "failed": 0,
                    "skipped": 0,
                    "capture_ids": [],
                    "errors": [],
                    "records": [],
                    "created_at": started_at,
                    "updated_at": started_at,
                    "completed_at": None,
                    "deleted_at": None,
                },
            )
            for ordinal, record in enumerate(records):
                self._upsert_import_record(
                    conn,
                    import_id=import_id,
                    user_id=user_id,
                    ordinal=ordinal,
                    record=record,
                    status="pending",
                    created_at=started_at,
                    updated_at=started_at,
                )

        capture_ids: list[str] = []
        identity_aliases = self.settings(user_id).get("identity_aliases")
        for ordinal, record in enumerate(records):
            record_id = stable_id("irec_", import_id + str(ordinal) + record.source + record.title)
            try:
                content_hash = stable_id("", record.content)
                with connect(self.db_path) as conn:
                    duplicate = conn.execute(
                        """
                        SELECT id
                        FROM captures
                        WHERE user_id = ?
                          AND raw_hash = ?
                          AND source = ?
                        ORDER BY captured_at DESC
                        LIMIT 1
                        """,
                        (user_id, content_hash, record.source),
                    ).fetchone()
                if duplicate:
                    skipped += 1
                    record_results.append({
                        "capture_id": None,
                        "status": "duplicate",
                        "source": record.source,
                        "source_url": record.source_url,
                        "title": record.title,
                    })
                    with connect(self.db_path) as conn:
                        conn.execute(
                            """
                            UPDATE import_records
                            SET status = 'duplicate',
                                error = ?,
                                updated_at = ?
                            WHERE user_id = ? AND id = ?
                            """,
                            (f"Duplicate of existing capture {duplicate['id']}", now_iso(), user_id, record_id),
                        )
                    continue
                if processing == "sync":
                    extracted = extract_context(
                        record.content,
                        record.source,
                        author_aliases=identity_aliases,
                        extraction_mode="local",
                    )
                    result = self.save_capture(
                        user_id=user_id,
                        content=record.content,
                        source=record.source,
                        source_url=record.source_url,
                        title=record.title,
                        extracted=extracted,
                        import_id=import_id,
                    )
                    saved += 1
                    capture_ids.append(result["capture_id"])
                    record_results.append({
                        "capture_id": result["capture_id"],
                        "status": "saved",
                        "source": record.source,
                        "source_url": record.source_url,
                        "title": record.title,
                        "memories": len(result.get("memories") or []),
                    })
                    with connect(self.db_path) as conn:
                        conn.execute(
                            "UPDATE import_records SET status = 'saved', capture_id = ?, updated_at = ? WHERE user_id = ? AND id = ?",
                            (result["capture_id"], now_iso(), user_id, record_id),
                        )
                else:
                    result = self.enqueue_capture(
                        user_id=user_id,
                        content=record.content,
                        source=record.source,
                        source_url=record.source_url,
                        title=record.title,
                        import_id=import_id,
                    )
                    queued += 1
                    capture_ids.append(result["capture_id"])
                    jobs = result.get("jobs", [])
                    job_id = jobs[0]["id"] if jobs else None
                    record_results.append({
                        "capture_id": result["capture_id"],
                        "status": "queued",
                        "source": record.source,
                        "source_url": record.source_url,
                        "title": record.title,
                        "jobs": jobs,
                    })
                    with connect(self.db_path) as conn:
                        conn.execute(
                            "UPDATE import_records SET status = 'queued', capture_id = ?, job_id = ?, updated_at = ? WHERE user_id = ? AND id = ?",
                            (result["capture_id"], job_id, now_iso(), user_id, record_id),
                        )
            except Exception as exc:
                error = {"source": record.source, "source_url": record.source_url, "title": record.title, "error": str(exc)}
                errors.append(error)
                with connect(self.db_path) as conn:
                    conn.execute(
                        "UPDATE import_records SET status = 'failed', error = ?, updated_at = ? WHERE user_id = ? AND id = ?",
                        (str(exc), now_iso(), user_id, record_id),
                    )
        completed_at = now_iso()
        status = "empty" if not records else "complete" if not errors else "partial"
        with connect(self.db_path) as conn:
            summary = {
                "id": import_id,
                "user_id": user_id,
                "status": status,
                "source_hint": source_hint,
                "processing": processing,
                "paths": path_summaries,
                "sources": source_summary,
                "records_found": len(records),
                "queued": queued,
                "saved": saved,
                "failed": len(errors),
                "skipped": skipped,
                "capture_ids": capture_ids,
                "errors": errors,
                "records": record_results[:100],
                "created_at": started_at,
                "updated_at": completed_at,
                "completed_at": completed_at,
                "deleted_at": None,
            }
            self._upsert_import_session(conn, summary)
            self._event(
                conn,
                user_id,
                import_id,
                "import",
                "created",
                {
                    "paths": path_summaries,
                    "source_hint": source_hint,
                    "processing": processing,
                    "records_found": len(records),
                    "queued": queued,
                    "saved": saved,
                    "failed": len(errors),
                    "skipped": skipped,
                    "capture_count": len(capture_ids),
                },
            )
        return {
            "import_id": import_id,
            "status": status,
            "records_found": len(records),
            "queued": queued,
            "saved": saved,
            "failed": len(errors),
            "skipped": skipped,
            "sources": source_summary,
            "records": record_results[:100],
            "errors": errors,
        }

    def get_job(self, user_id: str, job_id: str) -> dict[str, Any] | None:
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM memory_jobs WHERE user_id = ? AND id = ?", (user_id, job_id)).fetchone()
        return self._job_from_row(row) if row else None

    def list_imports(self, user_id: str, limit: int = 50, *, include_deleted: bool = True) -> list[dict[str, Any]]:
        filters = ["i.user_id = ?"]
        params: list[Any] = [user_id]
        if not include_deleted:
            filters.append("i.deleted_at IS NULL")
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT
                  i.*,
                  COUNT(DISTINCT c.id) AS remaining_captures,
                  COUNT(DISTINCT m.id) AS remaining_memories,
                  COUNT(DISTINCT t.id) AS remaining_tasks
                FROM import_sessions i
                LEFT JOIN captures c ON c.user_id = i.user_id AND c.import_id = i.id
                LEFT JOIN memories m ON m.user_id = i.user_id AND m.capture_id = c.id AND m.status = 'active'
                LEFT JOIN tasks t ON t.user_id = i.user_id AND t.capture_id = c.id AND t.status = 'open'
                WHERE {' AND '.join(filters)}
                GROUP BY i.id
                ORDER BY i.created_at DESC
                LIMIT ?
                """,
                [*params, max(1, min(limit, 100))],
            ).fetchall()
        return [self._import_session_from_row(row) for row in rows]

    def get_import(self, user_id: str, import_id: str) -> dict[str, Any] | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT
                  i.*,
                  COUNT(DISTINCT c.id) AS remaining_captures,
                  COUNT(DISTINCT m.id) AS remaining_memories,
                  COUNT(DISTINCT t.id) AS remaining_tasks
                FROM import_sessions i
                LEFT JOIN captures c ON c.user_id = i.user_id AND c.import_id = i.id
                LEFT JOIN memories m ON m.user_id = i.user_id AND m.capture_id = c.id AND m.status = 'active'
                LEFT JOIN tasks t ON t.user_id = i.user_id AND t.capture_id = c.id AND t.status = 'open'
                WHERE i.user_id = ? AND i.id = ?
                GROUP BY i.id
                """,
                (user_id, import_id),
            ).fetchone()
            if not row:
                return None
            records = conn.execute(
                """
                SELECT *
                FROM import_records
                WHERE user_id = ? AND import_id = ?
                ORDER BY ordinal
                LIMIT 500
                """,
                (user_id, import_id),
            ).fetchall()
            captures = conn.execute(
                """
                SELECT
                  c.*,
                  COUNT(DISTINCT m.id) AS memory_count,
                  COUNT(DISTINCT t.id) AS task_count
                FROM captures c
                LEFT JOIN memories m ON m.capture_id = c.id AND m.status = 'active'
                LEFT JOIN tasks t ON t.capture_id = c.id AND t.status = 'open'
                WHERE c.user_id = ? AND c.import_id = ?
                GROUP BY c.id
                ORDER BY c.captured_at DESC
                LIMIT 500
                """,
                (user_id, import_id),
            ).fetchall()
        detail = self._import_session_from_row(row)
        detail["records"] = [self._import_record_from_row(record) for record in records]
        detail["captures"] = [self._capture_from_row(capture) for capture in captures]
        return detail

    def delete_import(self, user_id: str, import_id: str) -> dict[str, Any]:
        timestamp = now_iso()
        with connect(self.db_path) as conn:
            session = conn.execute("SELECT * FROM import_sessions WHERE user_id = ? AND id = ?", (user_id, import_id)).fetchone()
            if not session:
                raise FileNotFoundError("Import not found")
            if session["deleted_at"]:
                return {
                    "import_id": import_id,
                    "deleted": False,
                    "status": "already_deleted",
                    "deleted_captures": 0,
                    "deleted_memories": 0,
                    "deleted_tasks": 0,
                    "deleted_edges": 0,
                }
            capture_ids = [
                row["id"]
                for row in conn.execute(
                    """
                    SELECT id FROM captures WHERE user_id = ? AND import_id = ?
                    UNION
                    SELECT capture_id AS id FROM import_records WHERE user_id = ? AND import_id = ? AND capture_id IS NOT NULL
                    """,
                    (user_id, import_id, user_id, import_id),
                ).fetchall()
                if row["id"]
            ]
            deleted: list[dict[str, Any]] = []
            for capture_id in sorted(set(capture_ids)):
                result = self._delete_capture_in_conn(
                    conn,
                    user_id,
                    capture_id,
                    timestamp=timestamp,
                    reason="import_deleted",
                    event_metadata={"import_id": import_id},
                )
                if result:
                    deleted.append(result)
            deleted_capture_ids = [item["capture_id"] for item in deleted]
            memory_ids = [memory_id for item in deleted for memory_id in item["memory_ids"]]
            task_ids = [task_id for item in deleted for task_id in item["task_ids"]]
            edge_ids = [edge_id for item in deleted for edge_id in item["edge_ids"]]
            conn.execute(
                """
                UPDATE import_sessions
                SET status = 'deleted', deleted_at = ?, updated_at = ?
                WHERE user_id = ? AND id = ?
                """,
                (timestamp, timestamp, user_id, import_id),
            )
            conn.execute(
                """
                UPDATE import_records
                SET status = CASE WHEN capture_id IS NULL THEN status ELSE 'deleted' END,
                    updated_at = ?
                WHERE user_id = ? AND import_id = ?
                """,
                (timestamp, user_id, import_id),
            )
            self._event(
                conn,
                user_id,
                import_id,
                "import",
                "deleted",
                {
                    "capture_count": len(deleted_capture_ids),
                    "memory_count": len(memory_ids),
                    "task_count": len(task_ids),
                    "edge_count": len(edge_ids),
                },
            )
            self.vault.patch_import(import_id, {"status": "deleted", "deleted_at": timestamp, "updated_at": timestamp})
            self.vault.write_tombstone(
                user_id=user_id,
                object_type="import",
                object_id=import_id,
                deleted_at=timestamp,
                reason="import_deleted",
                related_ids=[*deleted_capture_ids, *memory_ids, *task_ids, *edge_ids],
                metadata={
                    "capture_count": len(deleted_capture_ids),
                    "memory_count": len(memory_ids),
                    "task_count": len(task_ids),
                    "edge_count": len(edge_ids),
                },
            )
        return {
            "import_id": import_id,
            "deleted": True,
            "status": "deleted",
            "deleted_captures": len(deleted_capture_ids),
            "deleted_memories": len(memory_ids),
            "deleted_tasks": len(task_ids),
            "deleted_edges": len(edge_ids),
        }

    def list_jobs(self, user_id: str, *, status: str | None = None, job_type: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        filters = ["user_id = ?"]
        params: list[Any] = [user_id]
        if status:
            filters.append("status = ?")
            params.append(status)
        if job_type:
            filters.append("job_type = ?")
            params.append(job_type)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM memory_jobs
                WHERE {' AND '.join(filters)}
                ORDER BY
                  CASE status WHEN 'failed' THEN 0 WHEN 'running' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END,
                  updated_at DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        return [self._job_from_row(row) for row in rows]

    def run_due_jobs(self, user_id: str, *, limit: int = 10, worker_id: str = "local-worker") -> dict[str, Any]:
        processed: list[dict[str, Any]] = []
        for _ in range(max(0, min(limit, 100))):
            job = self._claim_next_job(user_id, worker_id)
            if not job:
                break
            processed.append(self._run_job(job, worker_id))
        return {
            "ran_at": now_iso(),
            "processed": len(processed),
            "jobs": processed,
            "pending": len(self.list_jobs(user_id, status="queued", limit=100)),
            "failed": len(self.list_jobs(user_id, status="failed", limit=100)),
        }

    def update_settings(self, user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        with connect(self.db_path) as conn:
            current = self._settings(conn, user_id)
            merged = {**current}
            if "review_new_captures" in updates:
                merged["review_new_captures"] = bool(updates["review_new_captures"])
            if "allow_pending_in_context" in updates:
                merged["allow_pending_in_context"] = bool(updates["allow_pending_in_context"])
            if "context_pack_limit" in updates:
                try:
                    value = int(updates["context_pack_limit"])
                except (TypeError, ValueError):
                    value = int(DEFAULT_USER_SETTINGS["context_pack_limit"])
                merged["context_pack_limit"] = min(50, max(4, value))
            for key in (
                "allow_agent_reads",
                "allow_agent_writes",
                "allow_agent_exports",
                "allow_agent_maintenance",
                "allow_agent_destructive_actions",
                "redact_sensitive_context",
            ):
                if key in updates:
                    merged[key] = bool(updates[key])
            if "source_policies" in updates:
                merged["source_policies"] = _normalize_source_policies(updates.get("source_policies"))
            if "identity_aliases" in updates:
                merged["identity_aliases"] = _normalize_identity_aliases(updates.get("identity_aliases"))
            timestamp = now_iso()
            for key, value in merged.items():
                conn.execute(
                    """
                    INSERT OR REPLACE INTO user_settings(user_id, key, value_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, key, json.dumps(value), timestamp),
                )
            self._event(conn, user_id, user_id, "settings", "updated", merged)
            self.vault.write_settings(user_id, merged)
            return merged

    def save_capture(
        self,
        *,
        user_id: str,
        content: str,
        source: str,
        source_url: str | None,
        title: str | None,
        extracted: dict[str, Any],
        import_id: str | None = None,
    ) -> dict[str, Any]:
        captured_at = extracted.get("_timestamp") or now_iso()
        capture_id = stable_id("cap_", user_id + source + captured_at + content[:120])
        raw_hash = stable_id("", content)
        summary = extracted.get("summary", "")
        user_settings_snapshot: dict[str, Any] = {}
        review_status = "pending"
        approved_at = None
        memories: list[dict[str, Any]] = []
        tasks: list[dict[str, Any]] = []
        entities: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            user_settings_snapshot = dict(user_settings)
            review_status = "pending" if user_settings["review_new_captures"] else "approved"
            approved_at = None if review_status == "pending" else captured_at
            conn.execute(
                """
                INSERT INTO captures
                (id, user_id, import_id, source, source_url, title, raw_text, raw_hash, summary, review_status, approved_at, captured_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  user_id = excluded.user_id,
                  import_id = COALESCE(captures.import_id, excluded.import_id),
                  source = excluded.source,
                  source_url = excluded.source_url,
                  title = excluded.title,
                  raw_text = excluded.raw_text,
                  raw_hash = excluded.raw_hash,
                  summary = excluded.summary,
                  review_status = excluded.review_status,
                  approved_at = excluded.approved_at,
                  captured_at = excluded.captured_at
                """,
                (capture_id, user_id, import_id, source, source_url, title, content, raw_hash, summary, review_status, approved_at, captured_at),
            )
            self._event(conn, user_id, capture_id, "capture", "created", {"source": source, "title": title})

            for record in extracted.get("records", []):
                memory = self._save_memory(conn, capture_id, user_id, record, source, source_url, captured_at, content)
                memories.append(memory)
                edges.append(self._edge(conn, user_id, capture_id, memory["id"], "contains", memory["id"], captured_at))

            for task in extracted.get("tasks", []):
                saved_task = self._save_task(conn, capture_id, user_id, task, captured_at)
                tasks.append(saved_task)
                edges.append(self._edge(conn, user_id, capture_id, saved_task["id"], "creates_task", saved_task["id"], captured_at))

            for entity in extracted.get("entities", []):
                entities.append(self._save_entity(conn, user_id, entity, captured_at))

            for memory in memories:
                for entity_id in memory.get("entity_ids", []):
                    edges.append(self._edge(conn, user_id, memory["id"], entity_id, "mentions", memory["id"], captured_at))

            for task in tasks:
                for entity_id in task.get("entity_ids", []):
                    edges.append(self._edge(conn, user_id, task["id"], entity_id, "involves", task["id"], captured_at))

            entity_ids = [entity["id"] for entity in entities]
            for index, left in enumerate(entity_ids):
                for right in entity_ids[index + 1:]:
                    edges.append(self._edge(conn, user_id, left, right, "co_occurs", capture_id, captured_at, weight=0.5))

            embedding_state = self._capture_embedding_status(conn, user_id, capture_id)
            conn.execute(
                """
                INSERT OR IGNORE INTO capture_processing_state
                (capture_id, user_id, ingest_status, extraction_status, embedding_status, memory_count, task_count, entity_count, last_job_id, last_error, queued_at, started_at, completed_at, updated_at)
                VALUES (?, ?, 'materialized', 'succeeded', ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)
                """,
                (
                    capture_id,
                    user_id,
                    embedding_state,
                    len(memories),
                    len(tasks),
                    len(entities),
                    captured_at,
                    captured_at,
                    captured_at,
                    captured_at,
                ),
            )

            self.vault.write_settings(user_id, user_settings_snapshot)
            self.vault.write_capture_bundle(
                capture={
                    "id": capture_id,
                    "user_id": user_id,
                    "import_id": import_id,
                    "source": source,
                    "source_url": source_url,
                    "title": title,
                    "raw_text": content,
                    "raw_hash": raw_hash,
                    "summary": summary,
                    "review_status": review_status,
                    "approved_at": approved_at,
                    "archived_at": None,
                    "captured_at": captured_at,
                },
                memories=memories,
                tasks=tasks,
                entities=entities,
                edges=edges,
            )

        return {
            "capture_id": capture_id,
            "summary": summary,
            "memories": memories,
            "tasks": tasks,
            "entities": entities,
            "graph": {"nodes": self.graph(user_id, limit=80)["nodes"], "edges": edges},
        }

    def inbox(self, user_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT
                  c.*,
                  COUNT(DISTINCT m.id) AS memory_count,
                  COUNT(DISTINCT t.id) AS task_count
                FROM captures c
                LEFT JOIN memories m ON m.capture_id = c.id AND m.status = 'active'
                LEFT JOIN tasks t ON t.capture_id = c.id AND t.status = 'open'
                WHERE c.user_id = ? AND c.review_status = 'pending'
                GROUP BY c.id
                ORDER BY c.captured_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [self._capture_from_row(row) for row in rows]

    def recent(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            filters, params = self._memory_filters(user_id, user_settings, alias="m")
            where = " AND ".join(filters)
            rows = conn.execute(
                f"SELECT * FROM memories m WHERE {where} ORDER BY m.captured_at DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def search(self, user_id: str, query: str, limit: int = 10, kind: str | None = None, layer: str | None = None) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return self.recent(user_id, limit)

        fts_query = self._fts_query(query)
        candidate_limit = max(limit * 4, 12)

        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            filters, params = self._memory_filters(user_id, user_settings, alias="m", kind=kind, layer=layer)
            where = " AND ".join(filters)
            rows = []
            fts_rows = []
            if fts_query:
                fts_rows = conn.execute(
                    f"""
                    SELECT m.*, bm25(memory_fts) AS rank
                    FROM memory_fts
                    JOIN memories m ON m.id = memory_fts.memory_id
                    WHERE memory_fts MATCH ? AND {where}
                    ORDER BY rank ASC, m.importance DESC, m.captured_at DESC
                    LIMIT ?
                    """,
                    [fts_query, *params, candidate_limit],
                ).fetchall()
            vector_rows = self._vector_search(conn, user_id, query, candidate_limit, kind, layer, user_settings)
            rows = self._fuse_search_rows(query, fts_rows, vector_rows, limit)
            if not rows:
                like = f"%{query}%"
                fallback_rows = conn.execute(
                    f"""
                    SELECT * FROM memories m
                    WHERE {where} AND (m.content LIKE ? OR m.summary LIKE ? OR m.source LIKE ?)
                    ORDER BY m.importance DESC, m.captured_at DESC
                    LIMIT ?
                    """,
                    [*params, like, like, like, candidate_limit],
                ).fetchall()
                rows = self._rank_rows_with_layer_boosts(query, fallback_rows, limit)
        return [self._memory_from_row(row) for row in rows]

    def answer_query(self, user_id: str, query: str, limit: int = 8) -> dict[str, Any]:
        query = query.strip()
        limit = max(1, min(20, int(limit)))
        results = self.search(user_id, query, limit=limit)
        citations: list[dict[str, Any]] = []
        for index, memory in enumerate(results, start=1):
            citations.append(
                {
                    "index": index,
                    "id": memory["id"],
                    "kind": memory["kind"],
                    "layer": memory["layer"],
                    "source": memory["source"],
                    "source_url": memory.get("source_url"),
                    "captured_at": memory.get("captured_at"),
                    "occurred_at": memory.get("occurred_at"),
                    "excerpt": self._answer_excerpt(memory.get("content") or memory.get("summary") or ""),
                    "topics": memory.get("topics") or [],
                }
            )
        if citations:
            lines = [f"Cortex found {len(citations)} cited memor{'y' if len(citations) == 1 else 'ies'} for this question:"]
            for citation in citations[:5]:
                source = citation["source_url"] or citation["source"]
                lines.append(f"[{citation['index']}] {citation['excerpt']} ({source})")
            answer = "\n".join(lines)
        else:
            answer = "Cortex did not find cited memory for this question yet. Import or approve more source material, then ask again."
        return {
            "query": query,
            "answer": answer,
            "citations": citations,
            "results": results,
        }

    def _answer_excerpt(self, text: str, limit: int = 220) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: max(0, limit - 1)].rstrip() + "..."

    def open_tasks(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            filters = ["t.user_id = ?", "t.status = 'open'"]
            params: list[Any] = [user_id]
            if not user_settings["allow_pending_in_context"]:
                filters.append(
                    "(t.capture_id IS NULL OR EXISTS (SELECT 1 FROM captures c WHERE c.id = t.capture_id AND c.user_id = t.user_id AND c.review_status = 'approved'))"
                )
            source_policies = _normalize_source_policies(user_settings.get("source_policies"))
            excluded_sources = [source for source, policy in source_policies.items() if not policy.get("allow_ai_context", True)]
            if excluded_sources:
                filters.append(f"(t.capture_id IS NULL OR NOT EXISTS (SELECT 1 FROM captures c WHERE c.id = t.capture_id AND c.user_id = t.user_id AND c.source IN ({','.join('?' for _ in excluded_sources)})))")
                params.extend(excluded_sources)
            review_sources = [source for source, policy in source_policies.items() if policy.get("review_required") and source not in excluded_sources]
            if review_sources:
                filters.append(f"(t.capture_id IS NULL OR NOT EXISTS (SELECT 1 FROM captures c WHERE c.id = t.capture_id AND c.user_id = t.user_id AND c.source IN ({','.join('?' for _ in review_sources)})) OR EXISTS (SELECT 1 FROM captures c WHERE c.id = t.capture_id AND c.user_id = t.user_id AND c.review_status = 'approved'))")
                params.extend(review_sources)
            where = " AND ".join(filters)
            rows = conn.execute(
                f"SELECT * FROM tasks t WHERE {where} ORDER BY t.importance DESC, t.captured_at DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def list_topics(self, user_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            filters, params = self._memory_filters(user_id, user_settings, alias="m")
            where = " AND ".join(filters)
            rows = conn.execute(
                f"""
                SELECT mt.topic, COUNT(*) AS count, MAX(m.captured_at) AS last_seen
                FROM memory_topics mt
                JOIN memories m ON m.id = mt.memory_id AND m.user_id = mt.user_id
                WHERE mt.user_id = ? AND {where}
                GROUP BY mt.topic
                ORDER BY count DESC, last_seen DESC
                LIMIT ?
                """,
                [user_id, *params, limit],
            ).fetchall()
        return [dict(row) for row in rows]

    def list_entities(self, user_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            filters, params = self._memory_filters(user_id, user_settings, alias="m")
            memory_filter = " AND ".join(filters)
            rows = conn.execute(
                f"""
                SELECT e.id, e.name, e.kind, e.context, e.first_seen, e.last_seen, COUNT(m.id) AS memory_count
                FROM entities e
                LEFT JOIN memory_entities me ON me.entity_id = e.id AND me.user_id = e.user_id
                LEFT JOIN memories m ON m.id = me.memory_id AND m.user_id = me.user_id AND {memory_filter}
                WHERE e.user_id = ?
                GROUP BY e.id
                HAVING memory_count > 0
                ORDER BY memory_count DESC, e.last_seen DESC
                LIMIT ?
                """,
                [*params, user_id, limit],
            ).fetchall()
        return [dict(row) for row in rows]

    def about_person(self, user_id: str, name: str, limit: int = 12) -> list[dict[str, Any]]:
        slug = "person_" + "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")
        results = self.search(user_id, name, limit=limit)
        return [item for item in results if slug in item.get("entity_ids", []) or name.lower() in item.get("content", "").lower()]

    def about_entity(self, user_id: str, name: str, limit: int = 12) -> list[dict[str, Any]]:
        slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")
        entity_suffix = "_" + slug
        results = self.search(user_id, name, limit=limit)
        return [
            item
            for item in results
            if any(entity_id.endswith(entity_suffix) for entity_id in item.get("entity_ids", []))
            or name.lower() in item.get("content", "").lower()
        ]

    def archive_memory(self, user_id: str, memory_id: str) -> bool:
        timestamp = now_iso()
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT id FROM memories WHERE user_id = ? AND id = ?", (user_id, memory_id)).fetchone()
            if not row:
                return False
            conn.execute("UPDATE memories SET status = 'archived', updated_at = ? WHERE user_id = ? AND id = ?", (timestamp, user_id, memory_id))
            conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
            self._delete_memory_vector(conn, memory_id)
            conn.execute(
                "DELETE FROM memory_jobs WHERE user_id = ? AND object_type = 'memory' AND object_id = ?",
                (user_id, memory_id),
            )
            self._event(conn, user_id, memory_id, "memory", "archived", {})
            self.vault.patch_memory(memory_id, {"status": "archived", "updated_at": timestamp})
        return True

    def delete_memory(self, user_id: str, memory_id: str) -> bool:
        timestamp = now_iso()
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT id FROM memories WHERE user_id = ? AND id = ?", (user_id, memory_id)).fetchone()
            if not row:
                return False
            edge_ids = self._purge_edges_for_objects(conn, user_id, [memory_id])
            self._purge_memory_rows(conn, user_id, [memory_id])
            self._event(conn, user_id, memory_id, "memory", "deleted", {"hard_delete": True, "edge_count": len(edge_ids)})
            self.vault.delete_memory(memory_id)
            for edge_id in edge_ids:
                self.vault.delete_edge(edge_id)
            self.vault.write_tombstone(
                user_id=user_id,
                object_type="memory",
                object_id=memory_id,
                deleted_at=timestamp,
                reason="memory_deleted",
                related_ids=edge_ids,
                metadata={"edge_count": len(edge_ids)},
            )
        return True

    def _delete_capture_in_conn(
        self,
        conn,
        user_id: str,
        capture_id: str,
        *,
        timestamp: str,
        reason: str,
        event_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        row = conn.execute("SELECT id FROM captures WHERE user_id = ? AND id = ?", (user_id, capture_id)).fetchone()
        if not row:
            return None
        memory_ids = [
            row["id"]
            for row in conn.execute("SELECT id FROM memories WHERE user_id = ? AND capture_id = ?", (user_id, capture_id)).fetchall()
        ]
        task_ids = [
            row["id"]
            for row in conn.execute("SELECT id FROM tasks WHERE user_id = ? AND capture_id = ?", (user_id, capture_id)).fetchall()
        ]
        edge_ids = self._purge_edges_for_objects(conn, user_id, [capture_id, *memory_ids, *task_ids])
        self._purge_memory_rows(conn, user_id, memory_ids)
        self._purge_task_rows(conn, user_id, task_ids)
        conn.execute(
            "DELETE FROM memory_jobs WHERE user_id = ? AND object_type = 'capture' AND object_id = ?",
            (user_id, capture_id),
        )
        conn.execute("DELETE FROM captures WHERE user_id = ? AND id = ?", (user_id, capture_id))
        metadata = {
            "hard_delete": True,
            "memory_count": len(memory_ids),
            "task_count": len(task_ids),
            "edge_count": len(edge_ids),
            **(event_metadata or {}),
        }
        self._event(conn, user_id, capture_id, "capture", "deleted", metadata)
        self.vault.delete_capture(capture_id)
        for memory_id in memory_ids:
            self.vault.delete_memory(memory_id)
        for task_id in task_ids:
            self.vault.delete_task(task_id)
        for edge_id in edge_ids:
            self.vault.delete_edge(edge_id)
        self.vault.write_tombstone(
            user_id=user_id,
            object_type="capture",
            object_id=capture_id,
            deleted_at=timestamp,
            reason=reason,
            related_ids=[*memory_ids, *task_ids, *edge_ids],
            metadata=metadata,
        )
        return {
            "capture_id": capture_id,
            "memory_ids": memory_ids,
            "task_ids": task_ids,
            "edge_ids": edge_ids,
            "memory_count": len(memory_ids),
            "task_count": len(task_ids),
            "edge_count": len(edge_ids),
        }

    def delete_capture(self, user_id: str, capture_id: str) -> bool:
        timestamp = now_iso()
        with connect(self.db_path) as conn:
            deleted = self._delete_capture_in_conn(conn, user_id, capture_id, timestamp=timestamp, reason="capture_deleted")
            if not deleted:
                return False
        return True

    def approve_capture(self, user_id: str, capture_id: str) -> bool:
        timestamp = now_iso()
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT id FROM captures WHERE user_id = ? AND id = ?", (user_id, capture_id)).fetchone()
            if not row:
                return False
            conn.execute(
                "UPDATE captures SET review_status = 'approved', approved_at = ?, archived_at = NULL WHERE user_id = ? AND id = ?",
                (timestamp, user_id, capture_id),
            )
            self._event(conn, user_id, capture_id, "capture", "approved", {})
            self.vault.patch_capture(capture_id, {"review_status": "approved", "approved_at": timestamp, "archived_at": None})
        return True

    def archive_capture(self, user_id: str, capture_id: str) -> bool:
        timestamp = now_iso()
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT id FROM captures WHERE user_id = ? AND id = ?", (user_id, capture_id)).fetchone()
            if not row:
                return False
            conn.execute(
                "UPDATE captures SET review_status = 'archived', archived_at = ? WHERE user_id = ? AND id = ?",
                (timestamp, user_id, capture_id),
            )
            memory_ids = [
                row["id"]
                for row in conn.execute("SELECT id FROM memories WHERE user_id = ? AND capture_id = ?", (user_id, capture_id)).fetchall()
            ]
            task_ids = [
                row["id"]
                for row in conn.execute("SELECT id FROM tasks WHERE user_id = ? AND capture_id = ?", (user_id, capture_id)).fetchall()
            ]
            conn.execute("UPDATE memories SET status = 'archived', updated_at = ? WHERE user_id = ? AND capture_id = ?", (timestamp, user_id, capture_id))
            conn.execute("UPDATE tasks SET status = 'archived' WHERE user_id = ? AND capture_id = ?", (user_id, capture_id))
            for memory_id in memory_ids:
                conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
                self._delete_memory_vector(conn, memory_id)
            self._event(conn, user_id, capture_id, "capture", "archived", {"memory_count": len(memory_ids)})
            self.vault.patch_capture(capture_id, {"review_status": "archived", "archived_at": timestamp})
            for memory_id in memory_ids:
                self.vault.patch_memory(memory_id, {"status": "archived", "updated_at": timestamp})
            for task_id in task_ids:
                self.vault.patch_task(task_id, {"status": "archived"})
        return True

    def stats(self, user_id: str) -> dict[str, Any]:
        with connect(self.db_path) as conn:
            counts = {
                "captures": conn.execute("SELECT COUNT(*) FROM captures WHERE user_id = ?", (user_id,)).fetchone()[0],
                "pending_captures": conn.execute("SELECT COUNT(*) FROM captures WHERE user_id = ? AND review_status = 'pending'", (user_id,)).fetchone()[0],
                "memories": conn.execute("SELECT COUNT(*) FROM memories WHERE user_id = ? AND status = 'active'", (user_id,)).fetchone()[0],
                "decisions": conn.execute("SELECT COUNT(*) FROM memories WHERE user_id = ? AND status = 'active' AND kind = 'decision'", (user_id,)).fetchone()[0],
                "tasks": conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'open'", (user_id,)).fetchone()[0],
                "entities": conn.execute("SELECT COUNT(*) FROM entities WHERE user_id = ?", (user_id,)).fetchone()[0],
                "edges": conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM graph_edges ge
                    LEFT JOIN captures c ON c.id = ge.evidence_id AND c.user_id = ge.user_id
                    LEFT JOIN memories m ON m.id = ge.evidence_id AND m.user_id = ge.user_id
                    LEFT JOIN tasks t ON t.id = ge.evidence_id AND t.user_id = ge.user_id
                    WHERE ge.user_id = ?
                      AND (
                        ge.evidence_id IS NULL
                        OR c.review_status IN ('pending', 'approved')
                        OR m.status = 'active'
                        OR t.status = 'open'
                      )
                    """,
                    (user_id,),
                ).fetchone()[0],
            }
            by_kind = [
                {"kind": row["kind"], "count": row["count"]}
                for row in conn.execute(
                    "SELECT kind, COUNT(*) AS count FROM memories WHERE user_id = ? AND status = 'active' GROUP BY kind ORDER BY count DESC",
                    (user_id,),
                ).fetchall()
            ]
            by_layer = [
                {"layer": row["layer"], "count": row["count"]}
                for row in conn.execute(
                    "SELECT layer, COUNT(*) AS count FROM memories WHERE user_id = ? AND status = 'active' GROUP BY layer ORDER BY count DESC",
                    (user_id,),
                ).fetchall()
            ]
            top_topics = [
                {"topic": row["topic"], "count": row["count"]}
                for row in conn.execute(
                    """
                    SELECT mt.topic, COUNT(*) AS count
                    FROM memory_topics mt
                    JOIN memories m ON m.id = mt.memory_id AND m.user_id = mt.user_id
                    WHERE mt.user_id = ? AND m.status = 'active'
                    GROUP BY mt.topic
                    ORDER BY count DESC, mt.topic
                    LIMIT 12
                    """,
                    (user_id,),
                ).fetchall()
            ]
            top_entities = [
                {"id": row["id"], "name": row["name"], "kind": row["kind"], "count": row["count"]}
                for row in conn.execute(
                    """
                    SELECT e.id, e.name, e.kind, COUNT(m.id) AS count
                    FROM entities e
                    JOIN memory_entities me ON me.entity_id = e.id AND me.user_id = e.user_id
                    JOIN memories m ON m.id = me.memory_id AND m.user_id = me.user_id AND m.status = 'active'
                    WHERE e.user_id = ?
                    GROUP BY e.id
                    ORDER BY count DESC, e.last_seen DESC
                    LIMIT 12
                    """,
                    (user_id,),
                ).fetchall()
            ]
        return {**counts, "by_kind": by_kind, "by_layer": by_layer, "top_topics": top_topics, "top_entities": top_entities}

    def memory_quality_report(self, user_id: str) -> dict[str, Any]:
        expected_layers = {"semantic", "episodic", "style", "decision", "preference", "negative"}
        with connect(self.db_path) as conn:
            totals = {
                "captures": conn.execute("SELECT COUNT(*) FROM captures WHERE user_id = ?", (user_id,)).fetchone()[0],
                "pending_captures": conn.execute("SELECT COUNT(*) FROM captures WHERE user_id = ? AND review_status = 'pending'", (user_id,)).fetchone()[0],
                "approved_captures": conn.execute("SELECT COUNT(*) FROM captures WHERE user_id = ? AND review_status = 'approved'", (user_id,)).fetchone()[0],
                "archived_captures": conn.execute("SELECT COUNT(*) FROM captures WHERE user_id = ? AND review_status = 'archived'", (user_id,)).fetchone()[0],
                "active_memories": conn.execute("SELECT COUNT(*) FROM memories WHERE user_id = ? AND status = 'active'", (user_id,)).fetchone()[0],
                "cited_memories": conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE user_id = ? AND status = 'active' AND COALESCE(source_url, '') != ''",
                    (user_id,),
                ).fetchone()[0],
            }
            totals["uncited_memories"] = max(0, totals["active_memories"] - totals["cited_memories"])
            layer_rows = conn.execute(
                """
                SELECT layer, COUNT(*) AS count
                FROM memories
                WHERE user_id = ? AND status = 'active'
                GROUP BY layer
                ORDER BY count DESC
                """,
                (user_id,),
            ).fetchall()
            layers_present = {row["layer"] for row in layer_rows if row["layer"]}
            source_rows = conn.execute(
                """
                SELECT
                  c.source,
                  COUNT(DISTINCT c.id) AS captures,
                  SUM(CASE WHEN c.review_status = 'pending' THEN 1 ELSE 0 END) AS pending,
                  SUM(CASE WHEN c.review_status = 'approved' THEN 1 ELSE 0 END) AS approved,
                  SUM(CASE WHEN c.review_status = 'archived' THEN 1 ELSE 0 END) AS archived,
                  COUNT(m.id) AS active_memories,
                  SUM(CASE WHEN m.id IS NOT NULL AND COALESCE(m.source_url, '') != '' THEN 1 ELSE 0 END) AS cited_memories,
                  MAX(c.captured_at) AS last_seen
                FROM captures c
                LEFT JOIN memories m ON m.capture_id = c.id AND m.user_id = c.user_id AND m.status = 'active'
                WHERE c.user_id = ?
                GROUP BY c.source
                ORDER BY captures DESC, last_seen DESC
                LIMIT 24
                """,
                (user_id,),
            ).fetchall()

        citation_coverage = _ratio(totals["cited_memories"], totals["active_memories"])
        review_coverage = _ratio(totals["captures"] - totals["pending_captures"], totals["captures"])
        layer_coverage = _ratio(len(layers_present & expected_layers), len(expected_layers))
        volume_score = _ratio(min(totals["active_memories"], 50), 50)
        score = round(citation_coverage * 35 + review_coverage * 25 + layer_coverage * 20 + volume_score * 20)
        score = max(0, min(100, score))
        status = "strong" if score >= 80 else "usable" if score >= 55 else "needs_sources" if totals["active_memories"] == 0 else "needs_review"

        warnings: list[str] = []
        recommendations: list[str] = []
        if totals["active_memories"] == 0:
            warnings.append("No active memories are available yet.")
            recommendations.append("Import a real source and approve useful memory before relying on Ask.")
        if totals["active_memories"] > 0 and citation_coverage < 0.8:
            warnings.append("Some active memories are missing source citations.")
            recommendations.append("Prefer source imports and URL/file captures so retrieved memory has citations.")
        if totals["pending_captures"] > 0 and _ratio(totals["pending_captures"], totals["captures"]) > 0.25:
            warnings.append("A large share of captured data is still pending review.")
            recommendations.append("Review or archive pending captures to improve model reliability.")
        if totals["active_memories"] > 0 and layer_coverage < 0.5:
            warnings.append("Memory coverage is concentrated in too few layers.")
            recommendations.append("Add decisions, writing samples, preferences, and rejected approaches for better adaptation.")

        source_health = []
        for row in source_rows:
            active_memories = int(row["active_memories"] or 0)
            cited_memories = int(row["cited_memories"] or 0)
            pending = int(row["pending"] or 0)
            captures = int(row["captures"] or 0)
            source_warnings: list[str] = []
            if active_memories and cited_memories < active_memories:
                source_warnings.append("missing citations")
            if captures and pending / captures > 0.5:
                source_warnings.append("mostly pending")
            source_status = "ok" if not source_warnings else "needs_attention"
            source_health.append(
                {
                    "source": row["source"],
                    "captures": captures,
                    "pending": pending,
                    "approved": int(row["approved"] or 0),
                    "archived": int(row["archived"] or 0),
                    "active_memories": active_memories,
                    "cited_memories": cited_memories,
                    "uncited_memories": max(0, active_memories - cited_memories),
                    "citation_coverage": _ratio(cited_memories, active_memories),
                    "last_seen": row["last_seen"],
                    "status": source_status,
                    "warnings": source_warnings,
                }
            )

        return {
            "generated_at": now_iso(),
            "score": score,
            "status": status,
            "citation_coverage": citation_coverage,
            "review_coverage": review_coverage,
            "layer_coverage": layer_coverage,
            "layers_present": sorted(layers_present),
            "totals": totals,
            "source_health": source_health,
            "warnings": warnings,
            "recommendations": recommendations,
        }

    def product_loop(self, user_id: str) -> dict[str, Any]:
        stats = self.stats(user_id)
        with connect(self.db_path) as conn:
            captured_today = conn.execute(
                "SELECT COUNT(*) FROM captures WHERE user_id = ? AND substr(captured_at, 1, 10) = date('now')",
                (user_id,),
            ).fetchone()[0]
            approved_today = conn.execute(
                "SELECT COUNT(*) FROM captures WHERE user_id = ? AND approved_at IS NOT NULL AND substr(approved_at, 1, 10) = date('now')",
                (user_id,),
            ).fetchone()[0]
            reused_today = conn.execute(
                """
                SELECT COUNT(*)
                FROM memory_events
                WHERE user_id = ?
                  AND object_type = 'loop'
                  AND event_type = 'context_reused'
                  AND substr(created_at, 1, 10) = date('now')
                """,
                (user_id,),
            ).fetchone()[0]
            last_reused_at = conn.execute(
                """
                SELECT MAX(created_at)
                FROM memory_events
                WHERE user_id = ?
                  AND object_type = 'loop'
                  AND event_type = 'context_reused'
                """,
                (user_id,),
            ).fetchone()[0]
            active_days = [
                row["day"]
                for row in conn.execute(
                    """
                    SELECT day
                    FROM (
                      SELECT substr(captured_at, 1, 10) AS day
                      FROM captures
                      WHERE user_id = ? AND captured_at >= datetime('now', '-30 days')
                      UNION
                      SELECT substr(created_at, 1, 10) AS day
                      FROM memory_events
                      WHERE user_id = ?
                        AND object_type = 'loop'
                        AND event_type = 'context_reused'
                        AND created_at >= datetime('now', '-30 days')
                    )
                    GROUP BY day
                    ORDER BY day DESC
                    """,
                    (user_id, user_id),
                ).fetchall()
            ]
            today = conn.execute("SELECT date('now')").fetchone()[0]

        active_day_set = set(active_days)
        streak_days = 0
        with connect(self.db_path) as conn:
            streak_rows = conn.execute(
                """
                WITH RECURSIVE days(offset, day) AS (
                  SELECT 0, date('now')
                  UNION ALL
                  SELECT offset + 1, date('now', '-' || (offset + 1) || ' days')
                  FROM days
                  WHERE offset < 29
                )
                SELECT day FROM days
                """,
            ).fetchall()
        for row in streak_rows:
            if row["day"] in active_day_set:
                streak_days += 1
            else:
                break

        capture_done = stats["captures"] > 0
        review_done = stats["captures"] > 0 and stats["pending_captures"] == 0
        reuse_done = reused_today > 0
        return_done = streak_days >= 2

        if not capture_done:
            primary = {
                "action": "capture",
                "label": "Add First Source",
                "title": "Start your personal model",
                "detail": "Add a decision, preference, writing sample, project detail, or open loop.",
            }
        elif stats["pending_captures"] > 0:
            primary = {
                "action": "review",
                "label": "Review Inbox",
                "title": "Review new signals",
                "detail": f"{stats['pending_captures']} capture{'s' if stats['pending_captures'] != 1 else ''} need approval before they strengthen the model.",
            }
        elif not reuse_done:
            primary = {
                "action": "reuse",
                "label": "Ask Cortex",
                "title": "Use your personal model",
                "detail": "Use Cortex memory in your next AI session and check model coverage afterward.",
            }
        else:
            primary = {
                "action": "done",
                "label": "Loop Complete",
                "title": "Loop complete today",
                "detail": "You added or reviewed signals and used approved memory. Keep Cortex nearby as work changes.",
            }

        def step(key: str, title: str, done: bool, detail: str) -> dict[str, Any]:
            status = "done" if done else "current" if primary["action"] == key else "waiting"
            return {"key": key, "title": title, "status": status, "detail": detail}

        steps = [
            step("capture", "Signal", capture_done, f"{captured_today} added today, {stats['captures']} total."),
            step("review", "Review", review_done, f"{stats['pending_captures']} waiting in the inbox."),
            step("reuse", "Access", reuse_done, f"{reused_today} approved memory handoff{'s' if reused_today != 1 else ''} prepared today."),
            step("done", "Return", return_done, f"{streak_days} day streak."),
        ]
        completion = round((sum(1 for item in steps if item["status"] == "done") / len(steps)) * 100)

        return {
            "generated_at": now_iso(),
            "status": "complete" if primary["action"] == "done" else "active",
            "completion": completion,
            "primary_action": primary,
            "steps": steps,
            "counts": {
                "captures_today": captured_today,
                "approved_today": approved_today,
                "pending_captures": stats["pending_captures"],
                "reused_today": reused_today,
                "active_days_30d": len(active_day_set),
                "streak_days": streak_days,
            },
            "last_reused_at": last_reused_at,
            "today": today,
        }

    def record_context_reuse(self, user_id: str, *, surface: str, query: str = "", target: str = "") -> dict[str, Any]:
        metadata = {
            "surface": (surface or "unknown")[:80],
            "query": (query or "")[:160],
            "target": (target or "")[:80],
        }
        with connect(self.db_path) as conn:
            event = self._event(conn, user_id, "context_pack", "loop", "context_reused", metadata)
        return {"recorded": True, "event": event, "product_loop": self.product_loop(user_id)}

    def daily_review(self, user_id: str) -> dict[str, Any]:
        stats = self.stats(user_id)
        pending = self.inbox(user_id, limit=6)
        recent_memories = self.recent(user_id, limit=8)
        open_tasks = self.open_tasks(user_id, limit=8)
        recent_decisions = self._memories_by_kind(user_id, "decision", limit=6)
        top_topics = self.list_topics(user_id, limit=8)
        top_entities = self.list_entities(user_id, limit=8)
        with connect(self.db_path) as conn:
            activity = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT substr(captured_at, 1, 10) AS day, COUNT(*) AS captures
                    FROM captures
                    WHERE user_id = ? AND substr(captured_at, 1, 10) >= date('now', '-6 days')
                    GROUP BY day
                    ORDER BY day DESC
                    """,
                    (user_id,),
                ).fetchall()
            ]
            captured_today = conn.execute(
                "SELECT COUNT(*) FROM captures WHERE user_id = ? AND substr(captured_at, 1, 10) = date('now')",
                (user_id,),
            ).fetchone()[0]
            approved_today = conn.execute(
                "SELECT COUNT(*) FROM captures WHERE user_id = ? AND approved_at IS NOT NULL AND substr(approved_at, 1, 10) = date('now')",
                (user_id,),
            ).fetchone()[0]
        recommended_actions = self._recommended_actions(
            pending_count=stats["pending_captures"],
            open_task_count=stats["tasks"],
            captured_today=captured_today,
            top_topics=top_topics,
            recent_decisions=recent_decisions,
        )
        momentum_score = min(
            100,
            max(
                0,
                captured_today * 12
                + approved_today * 8
                + len(recent_decisions) * 5
                + min(stats["memories"], 20)
                - min(stats["pending_captures"], 10) * 3,
            ),
        )
        focus_query = " ".join(item["topic"] for item in top_topics[:3]) or ""
        return {
            "generated_at": now_iso(),
            "momentum_score": momentum_score,
            "captured_today": captured_today,
            "approved_today": approved_today,
            "stats": stats,
            "pending": pending,
            "recent_memories": recent_memories,
            "recent_decisions": recent_decisions,
            "open_tasks": open_tasks,
            "top_topics": top_topics,
            "top_entities": top_entities,
            "capture_activity": activity,
            "recommended_actions": recommended_actions,
            "context_pack": self.context_pack(user_id, query=focus_query, limit=8),
            "product_loop": self.product_loop(user_id),
        }

    def context_pack(self, user_id: str, query: str = "", limit: int | None = None) -> str:
        query = query.strip()
        user_settings = self.settings(user_id)
        if limit is None:
            limit = int(user_settings["context_pack_limit"])
        memories = self.search(user_id, query, limit=limit) if query else self.recent(user_id, limit=limit)
        decisions = self._memories_by_kind(user_id, "decision", limit=5)
        tasks = self.open_tasks(user_id, limit=8)
        topics = self.list_topics(user_id, limit=8)
        entities = self.list_entities(user_id, limit=8)
        redact = bool(user_settings["redact_sensitive_context"])

        lines = [
            "# Cortex Memory View",
            "",
            f"Generated: {now_iso()}",
        ]
        if query:
            lines.append(f"Focus: {query}")
        lines.extend([
            "",
            "Use this Cortex context as partial, cited memory for this conversation. Treat it as coverage-limited, follow the user's newest message when there is conflict, and ask when coverage is missing.",
            "",
            "## Suggested Assistant Instruction",
            "",
            "Use the Cortex context below as scoped memory for this conversation. When a memory is relevant, ground the answer in it and mention the memory ID if useful. If the context conflicts with the user's newest message, follow the newest message and note the mismatch.",
            "",
            "## Relevant Memories",
            "",
        ])
        if memories:
            layer_titles = {
                "decision": "Decision Memory",
                "preference": "Preference Memory",
                "style": "Style Memory",
                "negative": "Negative Memory",
                "episodic": "Episodic Memory",
                "semantic": "Semantic Memory",
            }
            layer_order = ["decision", "preference", "style", "negative", "episodic", "semantic"]
            for layer in layer_order:
                grouped = [item for item in memories if memory_layer(item.get("kind"), item.get("layer")) == layer]
                if not grouped:
                    continue
                lines.extend([f"### {layer_titles[layer]}", ""])
                for item in grouped:
                    date = item.get("captured_at") or ""
                    topics_text = ", ".join(item.get("topics") or [])
                    suffix = f" Topics: {topics_text}." if topics_text else ""
                    citation = self._memory_citation(item)
                    lines.append(f"- [{item['id']}] ({item['kind']}, {item['source']}, {date}) Source: {citation}. {self._redact_text(item['content'], redact)}{suffix}")
                lines.append("")
        else:
            lines.append("- No active memories matched this focus.")
        lines.extend(["", "## Decisions", ""])
        if decisions:
            for item in decisions:
                lines.append(f"- [{item['id']}] Source: {self._memory_citation(item)}. {self._redact_text(item['content'], redact)}")
        else:
            lines.append("- No active decisions yet.")
        lines.extend(["", "## Open Loops", ""])
        if tasks:
            for task in tasks:
                lines.append(f"- [{task['id']}] ({task['kind']}) {self._redact_text(task['content'], redact)}")
        else:
            lines.append("- No open tasks or questions.")
        lines.extend(["", "## Useful Topics", ""])
        lines.append(", ".join(f"#{item['topic']}" for item in topics) if topics else "No active topics yet.")
        lines.extend(["", "## Useful Entities", ""])
        lines.append(", ".join(f"{item['name']} ({item['kind']})" for item in entities) if entities else "No active entities yet.")
        return "\n".join(lines)

    def personal_profile(self, user_id: str, query: str = "", limit: int = 6, include_pending: bool = False) -> dict[str, Any]:
        query = query.strip()
        limit = max(1, min(20, int(limit)))
        user_settings = self.settings(user_id)
        redact = bool(user_settings["redact_sensitive_context"])
        stats = self.stats(user_id)
        layer_counts = {item["layer"]: int(item["count"]) for item in stats["by_layer"]}
        layer_order = [
            ("preference", "Preference memory", "Durable likes, dislikes, defaults, and working preferences."),
            ("negative", "Negative memory", "Rejected approaches, disliked outputs, and constraints to avoid."),
            ("style", "Style memory", "Writing voice, phrasing, structure, and communication patterns."),
            ("decision", "Decision memory", "Past choices, reasons, constraints, and settled direction."),
            ("episodic", "Episodic memory", "Specific conversations, events, project moments, and recent context."),
            ("semantic", "Semantic memory", "Facts about people, projects, goals, systems, and durable context."),
        ]
        sections: list[dict[str, Any]] = []
        for layer, title, description in layer_order:
            memories = self._memories_by_layer(user_id, layer, limit=limit, include_pending=include_pending)
            sections.append(
                {
                    "layer": layer,
                    "title": title,
                    "description": description,
                    "count": layer_counts.get(layer, 0),
                    "items": [self._profile_memory_item(item, redact=redact) for item in memories],
                }
            )

        focus_memories = self.search(user_id, query, limit=limit) if query else []
        focus_memories = self._approved_profile_memories(user_id, focus_memories, include_pending=include_pending)
        open_loops = self.open_tasks(user_id, limit=limit)
        topics = self.list_topics(user_id, limit=8)
        entities = self.list_entities(user_id, limit=8)
        sources = self._source_freshness(user_id, limit=8)
        covered_layers = sum(1 for item in layer_order if layer_counts.get(item[0], 0) > 0)
        readiness = min(
            100,
            covered_layers * 12
            + min(stats["memories"], 20) * 2
            + min(stats["decisions"], 8) * 4
            + min(stats["entities"], 12) * 2
            - min(stats["pending_captures"], 8) * 2,
        )
        readiness = max(0, readiness)

        limitations: list[str] = []
        if stats["memories"] == 0:
            limitations.append("No approved memory signals exist yet, so this profile cannot adapt an assistant.")
        missing_layers = [title for layer, title, _ in layer_order if layer_counts.get(layer, 0) == 0]
        if missing_layers:
            limitations.append("Missing or weak layers: " + ", ".join(missing_layers[:4]) + ".")
        if stats["pending_captures"] and not include_pending:
            limitations.append(f"{stats['pending_captures']} pending capture(s) are excluded until approved.")
        limitations.append("This is cited retrieved memory, not a fine-tuned model or complete copy of the user.")

        profile: dict[str, Any] = {
            "generated_at": now_iso(),
            "name": "Cortex Personal Adaptation Profile",
            "query": query,
            "readiness": readiness,
            "include_pending": include_pending,
            "summary": {
                "memories": stats["memories"],
                "decisions": stats["decisions"],
                "open_loops": stats["tasks"],
                "entities": stats["entities"],
                "covered_layers": covered_layers,
                "total_layers": len(layer_order),
            },
            "coverage": {
                "by_layer": [
                    {
                        "layer": layer,
                        "title": title,
                        "count": layer_counts.get(layer, 0),
                        "status": "ready" if layer_counts.get(layer, 0) > 0 else "needs_signal",
                    }
                    for layer, title, _ in layer_order
                ],
                "sources": sources,
            },
            "focus": [self._profile_memory_item(item, redact=redact) for item in focus_memories],
            "sections": sections,
            "open_loops": [
                {
                    "id": task["id"],
                    "kind": task["kind"],
                    "content": self._redact_text(task["content"], redact),
                    "captured_at": task["captured_at"],
                    "topics": task.get("topics") or [],
                }
                for task in open_loops
            ],
            "topics": topics,
            "entities": entities,
            "limitations": limitations,
        }
        profile["markdown"] = self._personal_profile_markdown(profile)
        return profile

    def agent_adaptation(self, user_id: str, query: str = "", target: str = "assistant", limit: int = 8, include_pending: bool = False) -> dict[str, Any]:
        target = (target or "assistant").strip()[:80] or "assistant"
        profile = self.personal_profile(user_id, query=query, limit=limit, include_pending=include_pending)
        layer_priority = ["preference", "negative", "style", "decision", "episodic", "semantic"]
        section_by_layer = {section["layer"]: section for section in profile["sections"]}
        rule_templates = {
            "preference": "Honor this user preference",
            "negative": "Avoid this rejected or disliked pattern",
            "style": "Match this communication style signal",
            "decision": "Respect this prior decision and its constraints",
            "episodic": "Use this past event as situational context",
            "semantic": "Use this durable fact as background context",
        }
        rules: list[dict[str, Any]] = []
        evidence_by_id: dict[str, dict[str, Any]] = {}
        for layer in layer_priority:
            section = section_by_layer.get(layer) or {}
            for item in (section.get("items") or [])[:3]:
                evidence_by_id[item["id"]] = item
                rules.append(
                    {
                        "layer": layer,
                        "kind": item["kind"],
                        "instruction": f"{rule_templates[layer]}: {item['content']}",
                        "memory_id": item["id"],
                        "source": item["source"],
                        "source_url": item.get("source_url"),
                        "captured_at": item.get("captured_at"),
                    }
                )
        for item in profile.get("focus") or []:
            evidence_by_id[item["id"]] = item

        operating_principles = [
            f"Use this Cortex adaptation layer when acting as {target}.",
            "Do not claim to be the user or imply complete access to the user's mind.",
            "Follow the user's newest message over older memory when they conflict.",
            "Use cited memories as behavioral guidance, not as immutable facts.",
            "Ask a short clarifying question when coverage is missing or confidence is low.",
            "When a memory materially affects an answer or action, retain the memory ID internally and cite it when useful.",
        ]
        limitations = list(profile["limitations"])
        if profile["readiness"] < 70:
            limitations.append("Readiness is below production-grade adaptation; use cautious defaults and ask before high-impact actions.")
        if not rules:
            limitations.append("No adaptation rules were generated because the approved memory layers are sparse.")
        coverage_warnings: list[str] = []
        missing_layers = [
            layer["title"]
            for layer in profile["coverage"]["by_layer"]
            if int(layer.get("count") or 0) == 0
        ]
        if missing_layers:
            coverage_warnings.append("Missing memory layers: " + ", ".join(missing_layers[:4]) + ".")
        if any(not rule.get("source_url") for rule in rules):
            coverage_warnings.append("Some adaptation rules only have source labels, not precise source_url citations.")

        def policy_for(layer: str) -> list[dict[str, Any]]:
            return [
                {
                    "memory_id": rule["memory_id"],
                    "instruction": rule["instruction"],
                    "source": rule["source"],
                    "source_url": rule.get("source_url"),
                    "captured_at": rule.get("captured_at"),
                }
                for rule in rules
                if rule["layer"] == layer
            ]

        artifact: dict[str, Any] = {
            "generated_at": now_iso(),
            "name": "Cortex Agent Adaptation Layer",
            "target": target,
            "query": profile["query"],
            "readiness": profile["readiness"],
            "include_pending": profile["include_pending"],
            "operating_principles": operating_principles,
            "rules": rules,
            "evidence": list(evidence_by_id.values())[:20],
            "style_guide": policy_for("style"),
            "preference_policy": policy_for("preference"),
            "decision_policy": policy_for("decision"),
            "negative_constraints": policy_for("negative"),
            "citation_requirements": [
                "Every adaptation rule must keep its memory_id attached to the behavior it changes.",
                "Use source_url citations when explaining or applying a memory that materially affects an answer or action.",
                "Do not rely on pending memories unless include_pending is explicitly true.",
            ],
            "coverage_warnings": coverage_warnings,
            "coverage": profile["coverage"],
            "summary": profile["summary"],
            "open_loops": profile["open_loops"],
            "limitations": limitations,
        }
        artifact["markdown"] = self._agent_adaptation_markdown(artifact)
        return artifact

    def _agent_adaptation_markdown(self, artifact: dict[str, Any]) -> str:
        lines = [
            "# Cortex Agent Adaptation Layer",
            "",
            f"Generated: {artifact['generated_at']}",
            f"Target: {artifact['target']}",
            f"Readiness: {artifact['readiness']}/100",
            "",
            "Use this as a cited, coverage-limited adaptation layer. It is not a fine-tuned model and it must yield to the user's newest message.",
            "",
            "## Operating Principles",
            "",
        ]
        for principle in artifact["operating_principles"]:
            lines.append(f"- {principle}")
        policy_sections = [
            ("Preference Policy", artifact.get("preference_policy") or []),
            ("Negative Constraints", artifact.get("negative_constraints") or []),
            ("Style Guide", artifact.get("style_guide") or []),
            ("Decision Policy", artifact.get("decision_policy") or []),
        ]
        for title, items in policy_sections:
            lines.extend(["", f"## {title}", ""])
            if items:
                for item in items:
                    source = item.get("source_url") or item.get("source") or "unknown source"
                    lines.append(f"- [{item['memory_id']}] {item['instruction']} Source: {source}.")
            else:
                lines.append("- No approved signals yet.")
        lines.extend(["", "## Citation Requirements", ""])
        for requirement in artifact.get("citation_requirements") or []:
            lines.append(f"- {requirement}")
        if artifact.get("coverage_warnings"):
            lines.extend(["", "## Coverage Warnings", ""])
            for warning in artifact["coverage_warnings"]:
                lines.append(f"- {warning}")
        lines.extend(["", "## Adaptation Rules", ""])
        if artifact["rules"]:
            for rule in artifact["rules"]:
                source = rule.get("source_url") or rule.get("source") or "unknown source"
                lines.append(f"- [{rule['memory_id']}] {rule['instruction']} Source: {source}.")
        else:
            lines.append("- No adaptation rules generated yet.")
        lines.extend(["", "## Coverage", ""])
        for layer in artifact["coverage"]["by_layer"]:
            lines.append(f"- {layer['title']}: {layer['count']} signal{'s' if layer['count'] != 1 else ''} ({layer['status']})")
        lines.extend(["", "## Evidence", ""])
        if artifact["evidence"]:
            for item in artifact["evidence"][:12]:
                lines.append(self._profile_markdown_item(item))
        else:
            lines.append("- No cited evidence yet.")
        lines.extend(["", "## Open Loops", ""])
        if artifact["open_loops"]:
            for task in artifact["open_loops"][:8]:
                lines.append(f"- [{task['id']}] ({task['kind']}) {task['content']}")
        else:
            lines.append("- No active open loops.")
        lines.extend(["", "## Limits", ""])
        for limitation in artifact["limitations"]:
            lines.append(f"- {limitation}")
        return "\n".join(lines)

    def _personal_profile_markdown(self, profile: dict[str, Any]) -> str:
        lines = [
            "# Cortex Personal Adaptation Profile",
            "",
            f"Generated: {profile['generated_at']}",
            f"Readiness: {profile['readiness']}/100",
            "",
            "Use this as partial, cited memory for this conversation. It is coverage-limited and should yield to the user's newest message.",
            "",
            "## Coverage",
            "",
        ]
        for layer in profile["coverage"]["by_layer"]:
            lines.append(f"- {layer['title']}: {layer['count']} signal{'s' if layer['count'] != 1 else ''} ({layer['status']})")
        if profile["coverage"]["sources"]:
            lines.extend(["", "## Source Freshness", ""])
            for source in profile["coverage"]["sources"]:
                last_seen = source.get("last_seen") or "unknown"
                lines.append(f"- {source['source']}: {source['approved']} approved, {source['pending']} pending, last seen {last_seen}")
        if profile["focus"]:
            lines.extend(["", "## Focused Memory", ""])
            for item in profile["focus"]:
                lines.append(self._profile_markdown_item(item))
        for section in profile["sections"]:
            lines.extend(["", f"## {section['title']}", "", section["description"], ""])
            if section["items"]:
                for item in section["items"]:
                    lines.append(self._profile_markdown_item(item))
            else:
                lines.append("- No approved signals yet.")
        lines.extend(["", "## Open Loops", ""])
        if profile["open_loops"]:
            for task in profile["open_loops"]:
                lines.append(f"- [{task['id']}] ({task['kind']}) {task['content']}")
        else:
            lines.append("- No active open loops.")
        lines.extend(["", "## Topics", ""])
        lines.append(", ".join(f"#{item['topic']}" for item in profile["topics"]) if profile["topics"] else "No active topics yet.")
        lines.extend(["", "## People, Projects, And Entities", ""])
        lines.append(", ".join(f"{item['name']} ({item['kind']})" for item in profile["entities"]) if profile["entities"] else "No active entities yet.")
        lines.extend(["", "## Limitations", ""])
        for limitation in profile["limitations"]:
            lines.append(f"- {limitation}")
        return "\n".join(lines)

    def _profile_markdown_item(self, item: dict[str, Any]) -> str:
        date = item.get("captured_at") or "unknown date"
        topics = item.get("topics") or []
        topics_text = f" Topics: {', '.join(topics)}." if topics else ""
        return f"- [{item['id']}] ({item['kind']}, {item['source']}, {date}) Source: {self._memory_citation(item)}. {item['content']}{topics_text}"

    def _memory_citation(self, item: dict[str, Any]) -> str:
        source_url = str(item.get("source_url") or "").strip()
        if source_url:
            return source_url
        return str(item.get("source") or "unknown source")

    def _profile_memory_item(self, item: dict[str, Any], *, redact: bool) -> dict[str, Any]:
        return {
            "id": item["id"],
            "kind": item["kind"],
            "layer": item["layer"],
            "content": self._redact_text(item["content"], redact),
            "summary": self._redact_text(item.get("summary") or "", redact),
            "source": item["source"],
            "source_url": item.get("source_url"),
            "captured_at": item["captured_at"],
            "occurred_at": item.get("occurred_at"),
            "topics": item.get("topics") or [],
            "entity_ids": item.get("entity_ids") or [],
        }

    def _source_freshness(self, user_id: str, limit: int = 8) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT
                  source,
                  COUNT(*) AS total,
                  SUM(CASE WHEN review_status = 'approved' THEN 1 ELSE 0 END) AS approved,
                  SUM(CASE WHEN review_status = 'pending' THEN 1 ELSE 0 END) AS pending,
                  MAX(captured_at) AS last_seen
                FROM captures
                WHERE user_id = ?
                GROUP BY source
                ORDER BY total DESC, last_seen DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def _approved_profile_memories(self, user_id: str, memories: list[dict[str, Any]], *, include_pending: bool) -> list[dict[str, Any]]:
        if include_pending or not memories:
            return memories
        ids = [item["id"] for item in memories]
        placeholders = ",".join("?" for _ in ids)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT m.id
                FROM memories m
                LEFT JOIN captures c ON c.id = m.capture_id AND c.user_id = m.user_id
                WHERE m.user_id = ?
                  AND m.id IN ({placeholders})
                  AND (m.capture_id IS NULL OR c.review_status = 'approved')
                """,
                [user_id, *ids],
            ).fetchall()
        approved_ids = {row["id"] for row in rows}
        return [item for item in memories if item["id"] in approved_ids]

    def graph(self, user_id: str, limit: int = 150) -> dict[str, Any]:
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        with connect(self.db_path) as conn:
            for row in conn.execute(
                """
                SELECT id, source, title, summary, captured_at, review_status
                FROM captures
                WHERE user_id = ? AND review_status IN ('pending', 'approved')
                ORDER BY captured_at DESC
                LIMIT ?
                """,
                (user_id, limit // 3),
            ).fetchall():
                nodes[row["id"]] = {
                    "id": row["id"],
                    "type": "source",
                    "label": row["title"] or row["source"],
                    "detail": row["summary"] or "",
                    "status": row["review_status"],
                    "created_at": row["captured_at"],
                }
            for row in conn.execute(
                "SELECT id, kind, content, importance, captured_at FROM memories WHERE user_id = ? AND status = 'active' ORDER BY captured_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall():
                nodes[row["id"]] = {"id": row["id"], "type": row["kind"], "label": row["content"][:72], "importance": row["importance"], "created_at": row["captured_at"]}
            for row in conn.execute(
                """
                SELECT DISTINCT e.id, e.kind, e.name, e.context, e.last_seen
                FROM entities e
                WHERE e.user_id = ?
                  AND (
                    EXISTS (
                      SELECT 1
                      FROM memory_entities me
                      JOIN memories m ON m.id = me.memory_id AND m.user_id = me.user_id
                      WHERE me.user_id = e.user_id AND me.entity_id = e.id AND m.status = 'active'
                    )
                    OR EXISTS (
                      SELECT 1
                      FROM task_entities te
                      JOIN tasks t ON t.id = te.task_id AND t.user_id = te.user_id
                      WHERE te.user_id = e.user_id AND te.entity_id = e.id AND t.status = 'open'
                    )
                  )
                ORDER BY e.last_seen DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall():
                nodes[row["id"]] = {"id": row["id"], "type": row["kind"], "label": row["name"], "detail": row["context"] or ""}
            for row in conn.execute("SELECT id, kind, content, status FROM tasks WHERE user_id = ? AND status = 'open' ORDER BY captured_at DESC LIMIT ?", (user_id, limit // 2)).fetchall():
                nodes[row["id"]] = {"id": row["id"], "type": row["kind"], "label": row["content"][:72], "status": row["status"]}
            for row in conn.execute(
                """
                SELECT ge.*
                FROM graph_edges ge
                LEFT JOIN captures c ON c.id = ge.evidence_id AND c.user_id = ge.user_id
                LEFT JOIN memories m ON m.id = ge.evidence_id AND m.user_id = ge.user_id
                LEFT JOIN tasks t ON t.id = ge.evidence_id AND t.user_id = ge.user_id
                WHERE ge.user_id = ?
                  AND (
                    ge.evidence_id IS NULL
                    OR c.review_status IN ('pending', 'approved')
                    OR m.status = 'active'
                    OR t.status = 'open'
                  )
                ORDER BY ge.created_at DESC
                LIMIT ?
                """,
                (user_id, limit * 2),
            ).fetchall():
                if row["source_id"] in nodes and row["target_id"] in nodes:
                    edges.append(dict(row))
        return {"nodes": list(nodes.values()), "edges": edges}

    def export_json(self, user_id: str) -> dict[str, Any]:
        with connect(self.db_path) as conn:
            captures = [self._capture_from_row(row) for row in conn.execute("SELECT * FROM captures WHERE user_id = ? ORDER BY captured_at DESC", (user_id,)).fetchall()]
            imports = self.list_imports(user_id, limit=100)
            memories = [self._memory_from_row(row) for row in conn.execute("SELECT * FROM memories WHERE user_id = ? ORDER BY captured_at DESC", (user_id,)).fetchall()]
            tasks = [self._task_from_row(row) for row in conn.execute("SELECT * FROM tasks WHERE user_id = ? ORDER BY captured_at DESC", (user_id,)).fetchall()]
            entities = [self._entity_from_row(row) for row in conn.execute("SELECT * FROM entities WHERE user_id = ? ORDER BY last_seen DESC", (user_id,)).fetchall()]
            edges = [dict(row) for row in conn.execute("SELECT * FROM graph_edges WHERE user_id = ? ORDER BY created_at DESC", (user_id,)).fetchall()]
        payload = {
            "exported_at": now_iso(),
            "user_id": user_id,
            "stats": self.stats(user_id),
            "imports": imports,
            "captures": captures,
            "memories": memories,
            "tasks": tasks,
            "entities": entities,
            "edges": edges,
        }
        if self.settings(user_id)["redact_sensitive_context"]:
            return self._redact_payload(payload)
        return payload

    def diagnostics(self, user_id: str) -> dict[str, Any]:
        db_size = self.db_path.stat().st_size if self.db_path.exists() else 0
        wal_path = self.db_path.with_name(self.db_path.name + "-wal")
        wal_size = wal_path.stat().st_size if wal_path.exists() else 0
        with connect(self.db_path) as conn:
            quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
            schema_version = conn.execute("PRAGMA user_version").fetchone()[0]
            vector_status = sqlite_vec_status(conn)
            counts = {
                "captures": conn.execute("SELECT COUNT(*) FROM captures WHERE user_id = ?", (user_id,)).fetchone()[0],
                "active_memories": conn.execute("SELECT COUNT(*) FROM memories WHERE user_id = ? AND status = 'active'", (user_id,)).fetchone()[0],
                "archived_memories": conn.execute("SELECT COUNT(*) FROM memories WHERE user_id = ? AND status = 'archived'", (user_id,)).fetchone()[0],
                "open_tasks": conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'open'", (user_id,)).fetchone()[0],
                "events": conn.execute("SELECT COUNT(*) FROM memory_events WHERE user_id = ?", (user_id,)).fetchone()[0],
                "imports": conn.execute("SELECT COUNT(*) FROM import_sessions WHERE user_id = ?", (user_id,)).fetchone()[0],
                "source_accounts": conn.execute("SELECT COUNT(*) FROM source_accounts WHERE user_id = ?", (user_id,)).fetchone()[0],
                "sync_cursors": conn.execute("SELECT COUNT(*) FROM sync_cursors WHERE user_id = ?", (user_id,)).fetchone()[0],
                "sync_devices": conn.execute("SELECT COUNT(*) FROM sync_devices WHERE user_id = ?", (user_id,)).fetchone()[0],
                "sync_receipts": conn.execute("SELECT COUNT(*) FROM sync_receipts WHERE user_id = ?", (user_id,)).fetchone()[0],
                "vector_embeddings": self._vector_count(conn, user_id),
                "queued_jobs": conn.execute("SELECT COUNT(*) FROM memory_jobs WHERE user_id = ? AND status = 'queued'", (user_id,)).fetchone()[0],
                "running_jobs": conn.execute("SELECT COUNT(*) FROM memory_jobs WHERE user_id = ? AND status = 'running'", (user_id,)).fetchone()[0],
                "failed_jobs": conn.execute("SELECT COUNT(*) FROM memory_jobs WHERE user_id = ? AND status = 'failed'", (user_id,)).fetchone()[0],
            }
            fts_orphans = conn.execute(
                """
                SELECT COUNT(*)
                FROM memory_fts f
                LEFT JOIN memories m ON m.id = f.memory_id
                WHERE m.id IS NULL
                """
            ).fetchone()[0]
            inactive_fts_rows = conn.execute(
                """
                SELECT COUNT(*)
                FROM memory_fts f
                JOIN memories m ON m.id = f.memory_id
                WHERE m.status != 'active'
                """
            ).fetchone()[0]
            relation_orphans = conn.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM memory_entities me LEFT JOIN memories m ON m.id = me.memory_id WHERE m.id IS NULL) +
                  (SELECT COUNT(*) FROM memory_topics mt LEFT JOIN memories m ON m.id = mt.memory_id WHERE m.id IS NULL) +
                  (SELECT COUNT(*) FROM task_entities te LEFT JOIN tasks t ON t.id = te.task_id WHERE t.id IS NULL) +
                  (SELECT COUNT(*) FROM task_topics tt LEFT JOIN tasks t ON t.id = tt.task_id WHERE t.id IS NULL)
                """
            ).fetchone()[0]
            last_event_at = conn.execute("SELECT MAX(created_at) FROM memory_events WHERE user_id = ?", (user_id,)).fetchone()[0]
        issue_count = int(quick_check != "ok") + fts_orphans + inactive_fts_rows + relation_orphans
        vault_diagnostics = self.vault.diagnostics()
        issue_count += len(vault_diagnostics["missing_dirs"])
        return {
            "status": "ok" if issue_count == 0 else "needs_maintenance",
            "quick_check": quick_check,
            "schema_version": schema_version,
            "db_path": str(self.db_path),
            "db_size_bytes": db_size,
            "wal_size_bytes": wal_size,
            "counts": counts,
            "fts_orphans": fts_orphans,
            "inactive_fts_rows": inactive_fts_rows,
            "relation_orphans": relation_orphans,
            "last_event_at": last_event_at,
            "vector": vector_status,
            "embedding": embedding_status(),
            "vault": vault_diagnostics,
        }

    def health_payload(self, *, mode: str, auth: bool) -> dict[str, Any]:
        return {
            "status": "ok",
            "backend_version": BACKEND_VERSION,
            "health_contract": HEALTH_CONTRACT,
            "features": list(BACKEND_FEATURES),
            "mode": mode,
            "db_path": str(self.db_path),
            "vault_path": str(self.vault.root),
            "auth": auth,
        }

    def reliability_report(self, user_id: str) -> dict[str, Any]:
        diagnostics = self.diagnostics(user_id)
        latest_backup = self.latest_backup()
        checks: list[dict[str, Any]] = []

        def add_check(
            name: str,
            title: str,
            status: str,
            detail: str,
            action: str | None = None,
        ) -> None:
            checks.append(
                {
                    "name": name,
                    "title": title,
                    "status": status,
                    "detail": detail,
                    "action": action,
                }
            )

        add_check(
            "sqlite_quick_check",
            "SQLite integrity",
            "ok" if diagnostics["quick_check"] == "ok" else "critical",
            f"PRAGMA quick_check returned {diagnostics['quick_check']}.",
            None if diagnostics["quick_check"] == "ok" else "Restore from backup or export the vault before using this index.",
        )

        fts_issues = diagnostics["fts_orphans"] + diagnostics["inactive_fts_rows"]
        add_check(
            "search_index",
            "Search index",
            "ok" if fts_issues == 0 else "warn",
            f"{fts_issues} stale or orphaned full-text search rows.",
            None if fts_issues == 0 else "Run storage repair or rebuild search.",
        )

        add_check(
            "relationships",
            "Relationship tables",
            "ok" if diagnostics["relation_orphans"] == 0 else "warn",
            f"{diagnostics['relation_orphans']} orphaned relationship rows.",
            None if diagnostics["relation_orphans"] == 0 else "Run storage repair.",
        )

        missing_dirs = diagnostics.get("vault", {}).get("missing_dirs", []) if diagnostics.get("vault") else []
        add_check(
            "vault_layout",
            "Vault layout",
            "ok" if not missing_dirs else "warn",
            "Vault folders are present." if not missing_dirs else "Missing folders: " + ", ".join(missing_dirs),
            None if not missing_dirs else "Restart Cortex or create a backup to recreate missing folders.",
        )

        if latest_backup:
            age_days = latest_backup.get("age_days")
            backup_status = "ok" if isinstance(age_days, int) and age_days <= 7 else "warn"
            backup_detail = f"Latest backup is {age_days} day{'s' if age_days != 1 else ''} old."
        else:
            backup_status = "warn"
            backup_detail = "No local backup has been created yet."
        add_check(
            "backup_recency",
            "Backup recency",
            backup_status,
            backup_detail,
            None if backup_status == "ok" else "Create a backup before relying on this vault.",
        )

        vector = diagnostics.get("vector") or {}
        vector_status = "ok" if vector.get("available") else "warn"
        add_check(
            "vector_index",
            "Vector search",
            vector_status,
            "sqlite-vec is available." if vector.get("available") else str(vector.get("reason") or "sqlite-vec is not available; keyword search remains enabled."),
            None if vector_status == "ok" else "Install sqlite-vec when semantic search becomes required for this build.",
        )

        if any(check["status"] == "critical" for check in checks):
            status = "critical"
        elif any(check["status"] == "warn" for check in checks):
            status = "needs_attention"
        else:
            status = "ok"

        recommended_actions = [
            check["action"]
            for check in checks
            if check.get("action")
        ]
        if status == "ok":
            recommended_actions = ["No action needed. Keep using Cortex and keep periodic backups enabled."]

        return {
            "status": status,
            "generated_at": now_iso(),
            "backend_version": BACKEND_VERSION,
            "health_contract": HEALTH_CONTRACT,
            "features": list(BACKEND_FEATURES),
            "checks": checks,
            "recommended_actions": recommended_actions,
            "latest_backup": latest_backup,
            "diagnostics": diagnostics,
        }

    def support_bundle(self, user_id: str) -> dict[str, Any]:
        diagnostics = self.diagnostics(user_id)
        reliability = self.reliability_report(user_id)
        trust = self.trust_summary(user_id)
        stats = self.stats(user_id)
        loop = self.product_loop(user_id)
        recent_events = [self._support_event_summary(event) for event in self.audit_log(user_id, limit=30)]
        latest_backup = self.latest_backup()

        return {
            "bundle_schema": SUPPORT_BUNDLE_SCHEMA,
            "generated_at": now_iso(),
            "privacy": {
                "contains_raw_capture_text": False,
                "contains_memory_content": False,
                "contains_context_pack": False,
                "contains_user_files": False,
                "review_before_sharing": True,
                "notes": [
                    "This bundle is designed for support triage and omits captured text, memory bodies, memory views, and exported user data.",
                    "It may include local paths shortened to use ~, record counts, health checks, feature flags, and safe event metadata.",
                ],
            },
            "backend": {
                "version": BACKEND_VERSION,
                "health_contract": HEALTH_CONTRACT,
                "features": list(BACKEND_FEATURES),
                "sqlite_version": sqlite3.sqlite_version,
            },
            "runtime": {
                "python_version": sys.version.split()[0],
                "platform": platform.platform(),
            },
            "summary": {
                "status": reliability["status"],
                "trust_mode": trust["mode"],
                "trust_score": trust["trust_score"],
                "recommended_actions": reliability["recommended_actions"],
                "latest_backup_age_days": latest_backup.get("age_days") if latest_backup else None,
                "counts": {
                    "captures": stats["captures"],
                    "pending_captures": stats["pending_captures"],
                    "active_memories": stats["memories"],
                    "open_tasks": stats["tasks"],
                    "events": diagnostics["counts"].get("events", 0),
                    "vault_backups": diagnostics.get("vault", {}).get("record_counts", {}).get("backups", 0),
                },
            },
            "health": self._support_safe_payload(self.health_payload(mode="local", auth=True)),
            "diagnostics": self._support_safe_payload(diagnostics),
            "reliability": self._support_safe_payload(reliability),
            "trust": self._support_safe_payload(trust),
            "product_loop": self._support_safe_payload(
                {
                    "status": loop.get("status"),
                    "completion": loop.get("completion"),
                    "primary_action": loop.get("primary_action"),
                    "counts": loop.get("counts"),
                    "last_reused_at": loop.get("last_reused_at"),
                    "today": loop.get("today"),
                }
            ),
            "recent_events": recent_events,
        }

    def latest_backup(self) -> dict[str, Any] | None:
        backup_dir = self.vault.backups_dir
        if not backup_dir.exists():
            return None
        backups = sorted(
            (path for path in backup_dir.glob("*.zip") if path.is_file()),
            key=lambda path: (path.stat().st_mtime, path.name),
            reverse=True,
        )
        if not backups:
            return None
        path = backups[0]
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        age_seconds = max(0, (datetime.now(timezone.utc) - modified).total_seconds())
        return {
            "backup_path": str(path),
            "size_bytes": path.stat().st_size,
            "created_at": modified.isoformat().replace("+00:00", "Z"),
            "age_days": int(age_seconds // 86400),
        }

    def repair_storage(self, user_id: str) -> dict[str, Any]:
        before = self.diagnostics(user_id)
        backup = self.create_backup(user_id)
        actions: list[dict[str, Any]] = []

        def deleted_rows(cursor: sqlite3.Cursor) -> int:
            return cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else 0

        with connect(self.db_path) as conn:
            cleanup_statements = [
                (
                    "remove_fts_orphans",
                    """
                    DELETE FROM memory_fts
                    WHERE memory_id NOT IN (SELECT id FROM memories)
                    """,
                    (),
                ),
                (
                    "remove_inactive_fts",
                    """
                    DELETE FROM memory_fts
                    WHERE memory_id IN (
                      SELECT id FROM memories WHERE user_id = ? AND status != 'active'
                    )
                    """,
                    (user_id,),
                ),
                (
                    "remove_memory_entity_orphans",
                    """
                    DELETE FROM memory_entities
                    WHERE user_id = ? AND memory_id NOT IN (SELECT id FROM memories)
                    """,
                    (user_id,),
                ),
                (
                    "remove_memory_topic_orphans",
                    """
                    DELETE FROM memory_topics
                    WHERE user_id = ? AND memory_id NOT IN (SELECT id FROM memories)
                    """,
                    (user_id,),
                ),
                (
                    "remove_task_entity_orphans",
                    """
                    DELETE FROM task_entities
                    WHERE user_id = ? AND task_id NOT IN (SELECT id FROM tasks)
                    """,
                    (user_id,),
                ),
                (
                    "remove_task_topic_orphans",
                    """
                    DELETE FROM task_topics
                    WHERE user_id = ? AND task_id NOT IN (SELECT id FROM tasks)
                    """,
                    (user_id,),
                ),
                (
                    "remove_graph_edge_orphans",
                    """
                    DELETE FROM graph_edges
                    WHERE user_id = ?
                      AND evidence_id IS NOT NULL
                      AND evidence_id NOT IN (SELECT id FROM memories)
                      AND evidence_id NOT IN (SELECT id FROM tasks)
                      AND evidence_id NOT IN (SELECT id FROM captures)
                    """,
                    (user_id,),
                ),
            ]
            for name, statement, parameters in cleanup_statements:
                cursor = conn.execute(statement, parameters)
                actions.append({"name": name, "rows": deleted_rows(cursor)})
            conn.execute("PRAGMA optimize")
            self._event(
                conn,
                user_id,
                str(self.db_path),
                "maintenance",
                "storage_repair_cleanup",
                {"actions": actions, "backup_path": backup["backup_path"]},
            )

        rebuild = self.rebuild_search_index(user_id)
        actions.append({"name": "rebuild_search_index", "rows": rebuild["indexed_memories"]})
        after = self.diagnostics(user_id)
        return {
            "repaired_at": now_iso(),
            "backup_path": backup["backup_path"],
            "before": before,
            "after": after,
            "actions": actions,
        }

    def create_backup(self, user_id: str) -> dict[str, Any]:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")
        backup_dir = self.vault.backups_dir
        backup_dir.mkdir(parents=True, exist_ok=True)
        sqlite_backup_path = backup_dir / f"index-{timestamp}.sqlite"
        source = sqlite3.connect(self.db_path)
        target = sqlite3.connect(sqlite_backup_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        backup_path = self.vault.create_zip_backup(timestamp, sqlite_backup_path)
        try:
            sqlite_backup_path.unlink()
        except FileNotFoundError:
            pass
        with connect(self.db_path) as conn:
            self._event(
                conn,
                user_id,
                str(backup_path),
                "backup",
                "created",
                {"size_bytes": backup_path.stat().st_size, "format": "cortex-vault-zip"},
            )
        retention = backup_retention_policy()
        pruned = self.prune_backups(
            user_id,
            keep_latest=retention["keep_latest"],
            max_age_days=retention["max_age_days"],
        )
        return {
            "backup_path": str(backup_path),
            "size_bytes": backup_path.stat().st_size,
            "created_at": now_iso(),
            "retention": retention,
            "pruned_backups": pruned,
        }

    def delete_backups(self, user_id: str) -> dict[str, Any]:
        deleted_at = now_iso()
        result = self.vault.delete_backups()
        with connect(self.db_path) as conn:
            self._event(
                conn,
                user_id,
                str(self.vault.backups_dir),
                "backup",
                "deleted",
                {"deleted": result["deleted"], "bytes_deleted": result["bytes_deleted"]},
            )
        return {"deleted_at": deleted_at, **result}

    def prune_backups(self, user_id: str, *, keep_latest: int = 20, max_age_days: int = 0) -> dict[str, Any]:
        pruned_at = now_iso()
        result = self.vault.prune_backups(keep_latest=keep_latest, max_age_days=max_age_days)
        if result["deleted"]:
            with connect(self.db_path) as conn:
                self._event(
                    conn,
                    user_id,
                    str(self.vault.backups_dir),
                    "backup",
                    "pruned",
                    {
                        "deleted": result["deleted"],
                        "bytes_deleted": result["bytes_deleted"],
                        "retention": result["retention"],
                    },
                )
        return {"pruned_at": pruned_at, **result}

    def delete_user_data(self, user_id: str, *, include_backups: bool = True) -> dict[str, Any]:
        deleted_at = now_iso()
        with connect(self.db_path) as conn:
            counts = {
                "captures": conn.execute("SELECT COUNT(*) FROM captures WHERE user_id = ?", (user_id,)).fetchone()[0],
                "memories": conn.execute("SELECT COUNT(*) FROM memories WHERE user_id = ?", (user_id,)).fetchone()[0],
                "tasks": conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ?", (user_id,)).fetchone()[0],
                "entities": conn.execute("SELECT COUNT(*) FROM entities WHERE user_id = ?", (user_id,)).fetchone()[0],
                "graph_edges": conn.execute("SELECT COUNT(*) FROM graph_edges WHERE user_id = ?", (user_id,)).fetchone()[0],
                "events": conn.execute("SELECT COUNT(*) FROM memory_events WHERE user_id = ?", (user_id,)).fetchone()[0],
                "settings": conn.execute("SELECT COUNT(*) FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()[0],
                "api_tokens": conn.execute("SELECT COUNT(*) FROM api_tokens WHERE user_id = ?", (user_id,)).fetchone()[0],
                "imports": conn.execute("SELECT COUNT(*) FROM import_sessions WHERE user_id = ?", (user_id,)).fetchone()[0],
                "source_accounts": conn.execute("SELECT COUNT(*) FROM source_accounts WHERE user_id = ?", (user_id,)).fetchone()[0],
                "sync_cursors": conn.execute("SELECT COUNT(*) FROM sync_cursors WHERE user_id = ?", (user_id,)).fetchone()[0],
                "sync_devices": conn.execute("SELECT COUNT(*) FROM sync_devices WHERE user_id = ?", (user_id,)).fetchone()[0],
                "sync_receipts": conn.execute("SELECT COUNT(*) FROM sync_receipts WHERE user_id = ?", (user_id,)).fetchone()[0],
                "memory_jobs": conn.execute("SELECT COUNT(*) FROM memory_jobs WHERE user_id = ?", (user_id,)).fetchone()[0],
                "capture_processing_state": conn.execute("SELECT COUNT(*) FROM capture_processing_state WHERE user_id = ?", (user_id,)).fetchone()[0],
            }
            self._clear_user_vectors(conn, user_id)
            conn.execute("DELETE FROM memory_fts WHERE memory_id IN (SELECT id FROM memories WHERE user_id = ?)", (user_id,))
            conn.execute("DELETE FROM memory_entities WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM memory_topics WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM task_entities WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM task_topics WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM graph_edges WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM tasks WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM memories WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM entities WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM memory_jobs WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM sync_receipts WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM sync_devices WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM sync_cursors WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM source_accounts WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM import_records WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM import_sessions WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM capture_processing_state WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM captures WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM memory_events WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM api_tokens WHERE user_id = ?", (user_id,))
            vault_counts = self.vault.delete_user_records(user_id, include_backups=include_backups)
        return {
            "deleted_at": deleted_at,
            "include_backups": include_backups,
            "sqlite": counts,
            "vault": vault_counts,
        }

    def restore_latest_backup(self, user_id: str) -> dict[str, Any]:
        latest = self.latest_backup()
        if not latest:
            raise FileNotFoundError("No Cortex backup archives are available")
        preserved_tombstones = list(self.vault.iter_tombstones(user_id))
        restored = self.vault.restore_from_zip_backup(Path(latest["backup_path"]))
        for tombstone in preserved_tombstones:
            self.vault.write_tombstone_record(tombstone)
        rebuild = self.rebuild_index_from_vault(user_id)
        restored_at = now_iso()
        with connect(self.db_path) as conn:
            self._event(
                conn,
                user_id,
                latest["backup_path"],
                "backup",
                "restored",
                {
                    "captures": rebuild["captures"],
                    "memories": rebuild["memories"],
                    "tasks": rebuild["tasks"],
                    "tombstones": rebuild.get("tombstones", {}),
                },
            )
        return {
            "restored_at": restored_at,
            "backup_path": latest["backup_path"],
            "size_bytes": latest["size_bytes"],
            "vault": restored,
            "rebuild": rebuild,
            "tombstones": rebuild.get("tombstones", {}),
        }

    def rebuild_search_index(self, user_id: str) -> dict[str, Any]:
        with connect(self.db_path) as conn:
            conn.execute(
                """
                DELETE FROM memory_fts
                WHERE memory_id IN (SELECT id FROM memories WHERE user_id = ?)
                """,
                (user_id,),
            )
            self._clear_user_vectors(conn, user_id)
            rows = conn.execute(
                """
                SELECT id, capture_id, kind, layer, content, summary, source, topics_json, captured_at
                FROM memories
                WHERE user_id = ? AND status = 'active'
                """,
                (user_id,),
            ).fetchall()
            queued_vectors = 0
            for row in rows:
                topics = " ".join(json.loads(row["topics_json"] or "[]"))
                conn.execute(
                    "INSERT INTO memory_fts(memory_id, content, summary, source, topics) VALUES (?, ?, ?, ?, ?)",
                    (row["id"], row["content"], row["summary"], row["source"], topics),
                )
                job = self._enqueue_embed_memory_job(
                    conn,
                    memory_id=row["id"],
                    capture_id=row["capture_id"],
                    user_id=user_id,
                    content=row["content"],
                    summary=row["summary"],
                    source=row["source"],
                    layer=memory_layer(row["kind"], row["layer"]),
                    topics=json.loads(row["topics_json"] or "[]"),
                    captured_at=row["captured_at"] or now_iso(),
                    priority=90,
                )
                if job and job["status"] == "queued":
                    queued_vectors += 1
            vector_count = self._vector_count(conn, user_id)
            vector_available = self._vector_ready(conn)
            self._event(conn, user_id, "memory_fts", "maintenance", "rebuilt_search_index", {"indexed_memories": len(rows), "vector_indexed_memories": vector_count, "vector_queued_memories": queued_vectors})
        return {
            "indexed_memories": len(rows),
            "rebuilt_at": now_iso(),
            "vector_available": vector_available,
            "vector_indexed_memories": vector_count,
            "vector_queued_memories": queued_vectors,
            "vector_model": embedding_status()["model"],
            "embedding": embedding_status(),
        }

    def rebuild_vectors(self, user_id: str) -> dict[str, Any]:
        timestamp = now_iso()
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, capture_id, kind, layer, content, summary, source, topics_json, captured_at
                FROM memories
                WHERE user_id = ? AND status = 'active'
                """,
                (user_id,),
            ).fetchall()
            vector_available = self._vector_ready(conn)
            if not vector_available:
                return {
                    "queued": 0,
                    "skipped": len(rows),
                    "checked": len(rows),
                    "rebuilt_at": timestamp,
                    "vector_available": False,
                    "vector_indexed_memories": self._vector_count(conn, user_id),
                    "vector_model": embedding_status()["model"],
                    "embedding": embedding_status(),
                }
            queued = 0
            skipped = 0
            for row in rows:
                job = self._enqueue_embed_memory_job(
                    conn,
                    memory_id=row["id"],
                    capture_id=row["capture_id"],
                    user_id=user_id,
                    content=row["content"],
                    summary=row["summary"],
                    source=row["source"],
                    layer=memory_layer(row["kind"], row["layer"]),
                    topics=json.loads(row["topics_json"] or "[]"),
                    captured_at=row["captured_at"] or timestamp,
                    priority=90,
                )
                if job and job["status"] == "queued":
                    queued += 1
                else:
                    skipped += 1
            vector_count = self._vector_count(conn, user_id)
            self._event(
                conn,
                user_id,
                "memory_vec",
                "maintenance",
                "queued_vector_rebuild",
                {"checked": len(rows), "queued": queued, "skipped": skipped, "vector_indexed_memories": vector_count},
            )
        return {
            "queued": queued,
            "skipped": skipped,
            "checked": len(rows),
            "rebuilt_at": timestamp,
            "vector_available": vector_available,
            "vector_indexed_memories": vector_count,
            "vector_model": embedding_status()["model"],
            "embedding": embedding_status(),
        }

    def rebuild_index_from_vault(self, user_id: str) -> dict[str, Any]:
        tombstone_counts = self.vault.apply_tombstones(user_id)
        imports = list(self.vault.iter_records("imports", user_id))
        source_accounts = list(self.vault.iter_records("source_accounts", user_id))
        sync_cursors = list(self.vault.iter_records("sync_cursors", user_id))
        sync_devices = list(self.vault.iter_records("sync_devices", user_id))
        sync_receipts = list(self.vault.iter_records("sync_receipts", user_id))
        captures = list(self.vault.iter_records("captures", user_id))
        memories = list(self.vault.iter_records("memories", user_id))
        tasks = list(self.vault.iter_records("tasks", user_id))
        entities = list(self.vault.iter_records("entities", user_id))
        edges = list(self.vault.iter_records("graph_edges", user_id))
        events = list(self.vault.iter_events(user_id))
        settings = self.vault.read_settings(user_id) or dict(DEFAULT_USER_SETTINGS)
        timestamp = now_iso()

        with connect(self.db_path) as conn:
            self._clear_user_vectors(conn, user_id)
            conn.execute("DELETE FROM memory_fts WHERE memory_id IN (SELECT id FROM memories WHERE user_id = ?)", (user_id,))
            conn.execute("DELETE FROM memory_entities WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM memory_topics WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM task_entities WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM task_topics WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM graph_edges WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM tasks WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM memories WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM entities WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM sync_receipts WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM sync_devices WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM sync_cursors WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM source_accounts WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM import_records WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM import_sessions WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM captures WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM memory_events WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))

            for key, value in settings.items():
                if key in DEFAULT_USER_SETTINGS:
                    conn.execute(
                        "INSERT OR REPLACE INTO user_settings(user_id, key, value_json, updated_at) VALUES (?, ?, ?, ?)",
                        (user_id, key, json.dumps(value), timestamp),
                    )

            restored_account_ids: set[str] = set()
            for account in sorted(source_accounts, key=lambda item: item.get("updated_at") or item.get("created_at") or ""):
                account_id = account.get("id")
                if not account_id:
                    continue
                restored_account_ids.add(str(account_id))
                conn.execute(
                    """
                    INSERT OR REPLACE INTO source_accounts
                    (id, user_id, source, account_label, account_identifier, connection_type, status, auth_state, policy_json, metadata_json, last_sync_at, last_error, created_at, updated_at, disconnected_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(account_id),
                        user_id,
                        str(account.get("source") or "unknown"),
                        str(account.get("account_label") or account.get("source") or "Source account"),
                        account.get("account_identifier"),
                        str(account.get("connection_type") or "manual"),
                        str(account.get("status") or "available"),
                        str(account.get("auth_state") or "not_configured"),
                        json.dumps(account.get("policy") if isinstance(account.get("policy"), dict) else {}),
                        json.dumps(account.get("metadata") if isinstance(account.get("metadata"), dict) else {}),
                        account.get("last_sync_at"),
                        account.get("last_error"),
                        account.get("created_at") or timestamp,
                        account.get("updated_at") or account.get("created_at") or timestamp,
                        account.get("disconnected_at"),
                    ),
                )

            restored_cursor_count = 0
            for cursor in sorted(sync_cursors, key=lambda item: item.get("updated_at") or item.get("created_at") or ""):
                cursor_id = cursor.get("id")
                if not cursor_id:
                    continue
                account_id = cursor.get("source_account_id")
                if account_id and str(account_id) not in restored_account_ids:
                    account_id = None
                conn.execute(
                    """
                    INSERT OR REPLACE INTO sync_cursors
                    (id, user_id, source_account_id, source, cursor_name, cursor_value, high_water_mark, state_json, last_started_at, last_completed_at, last_error, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(cursor_id),
                        user_id,
                        account_id,
                        str(cursor.get("source") or "unknown"),
                        str(cursor.get("cursor_name") or "default"),
                        cursor.get("cursor_value"),
                        cursor.get("high_water_mark"),
                        json.dumps(cursor.get("state") if isinstance(cursor.get("state"), dict) else {}),
                        cursor.get("last_started_at"),
                        cursor.get("last_completed_at"),
                        cursor.get("last_error"),
                        cursor.get("created_at") or timestamp,
                        cursor.get("updated_at") or cursor.get("created_at") or timestamp,
                    ),
                )
                restored_cursor_count += 1

            restored_device_count = 0
            restored_device_ids: set[str] = set()
            for device in sorted(sync_devices, key=lambda item: item.get("updated_at") or item.get("created_at") or ""):
                device_id = device.get("id")
                if not device_id:
                    continue
                key_hash = str(device.get("device_key_hash") or "")
                if not key_hash and device.get("fingerprint"):
                    key_hash = str(device.get("fingerprint"))
                if not key_hash:
                    continue
                capabilities = device.get("capabilities") if isinstance(device.get("capabilities"), list) else []
                conn.execute(
                    """
                    INSERT OR REPLACE INTO sync_devices
                    (id, user_id, device_name, platform, device_key_hash, public_key, capabilities_json, first_cursor, last_cursor, last_seen_at, created_at, updated_at, revoked_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(device_id),
                        user_id,
                        str(device.get("device_name") or "Sync device"),
                        str(device.get("platform") or "unknown"),
                        key_hash,
                        device.get("public_key"),
                        json.dumps([str(item) for item in capabilities if str(item).strip()]),
                        device.get("first_cursor"),
                        device.get("last_cursor"),
                        device.get("last_seen_at"),
                        device.get("created_at") or timestamp,
                        device.get("updated_at") or device.get("created_at") or timestamp,
                        device.get("revoked_at"),
                    ),
                )
                restored_device_count += 1
                restored_device_ids.add(str(device_id))

            restored_receipt_count = 0
            for receipt in sorted(sync_receipts, key=lambda item: item.get("updated_at") or item.get("created_at") or ""):
                receipt_id = receipt.get("id")
                device_id = str(receipt.get("device_id") or "")
                cursor = str(receipt.get("cursor") or "")
                if not receipt_id or not device_id or not cursor or device_id not in restored_device_ids:
                    continue
                stats = receipt.get("stats") if isinstance(receipt.get("stats"), dict) else {}
                conn.execute(
                    """
                    INSERT OR REPLACE INTO sync_receipts
                    (id, user_id, device_id, cursor, status, manifest_hash, remote_ref, error, stats_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(receipt_id),
                        user_id,
                        device_id,
                        cursor,
                        str(receipt.get("status") or "accepted"),
                        receipt.get("manifest_hash"),
                        receipt.get("remote_ref"),
                        receipt.get("error"),
                        json.dumps(stats),
                        receipt.get("created_at") or timestamp,
                        receipt.get("updated_at") or receipt.get("created_at") or timestamp,
                    ),
                )
                restored_receipt_count += 1

            for import_record in sorted(imports, key=lambda item: item.get("created_at") or ""):
                session = {
                    "id": import_record.get("id") or import_record.get("import_id"),
                    "user_id": user_id,
                    "status": import_record.get("status", "complete"),
                    "source_hint": import_record.get("source_hint", ""),
                    "processing": import_record.get("processing", "async"),
                    "paths": import_record.get("paths", []),
                    "sources": import_record.get("sources", []),
                    "records_found": import_record.get("records_found", 0),
                    "queued": import_record.get("queued", 0),
                    "saved": import_record.get("saved", 0),
                    "failed": import_record.get("failed", 0),
                    "capture_ids": import_record.get("capture_ids", []),
                    "errors": import_record.get("errors", []),
                    "records": import_record.get("records", []),
                    "created_at": import_record.get("created_at") or timestamp,
                    "updated_at": import_record.get("updated_at") or import_record.get("created_at") or timestamp,
                    "completed_at": import_record.get("completed_at"),
                    "deleted_at": import_record.get("deleted_at"),
                }
                if not session["id"]:
                    continue
                self._upsert_import_session(conn, session)

            for capture in sorted(captures, key=lambda item: item.get("captured_at") or ""):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO captures
                    (id, user_id, import_id, source, source_url, title, raw_text, raw_hash, summary, review_status, approved_at, archived_at, captured_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        capture["id"],
                        user_id,
                        capture.get("import_id"),
                        capture.get("source", "vault"),
                        capture.get("source_url"),
                        capture.get("title"),
                        capture.get("raw_text", ""),
                        capture.get("raw_hash"),
                        capture.get("summary", ""),
                        capture.get("review_status", "pending"),
                        capture.get("approved_at"),
                        capture.get("archived_at"),
                        capture.get("captured_at") or timestamp,
                    ),
                )

            for import_record in sorted(imports, key=lambda item: item.get("created_at") or ""):
                import_id = import_record.get("id") or import_record.get("import_id")
                if not import_id:
                    continue
                created_at = import_record.get("created_at") or timestamp
                updated_at = import_record.get("updated_at") or created_at
                for ordinal, item in enumerate(import_record.get("records", []) or []):
                    if not isinstance(item, dict):
                        continue
                    record_id = stable_id("irec_", import_id + str(ordinal) + str(item.get("source") or "") + str(item.get("title") or ""))
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO import_records
                        (id, import_id, user_id, ordinal, source, title, source_url, content_hash, chars, metadata_json, status, capture_id, job_id, error, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record_id,
                            import_id,
                            user_id,
                            ordinal,
                            str(item.get("source") or "import"),
                            str(item.get("title") or "Imported source"),
                            item.get("source_url"),
                            str(item.get("content_hash") or ""),
                            int(item.get("chars") or 0),
                            json.dumps(item.get("metadata") or {}),
                            str(item.get("status") or "restored"),
                            item.get("capture_id"),
                            item.get("job_id"),
                            item.get("error"),
                            created_at,
                            updated_at,
                        ),
                    )

            for entity in sorted(entities, key=lambda item: item.get("id") or ""):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO entities
                    (id, user_id, kind, name, aliases_json, context, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entity["id"],
                        user_id,
                        entity.get("kind", "person"),
                        entity.get("name", entity["id"]),
                        json.dumps(entity.get("aliases", [])),
                        entity.get("context", ""),
                        entity.get("first_seen") or timestamp,
                        entity.get("last_seen") or timestamp,
                    ),
                )

            for memory in sorted(memories, key=lambda item: item.get("captured_at") or ""):
                topics = memory.get("topics", [])
                entity_ids = memory.get("entity_ids", [])
                captured_at = memory.get("captured_at") or timestamp
                conn.execute(
                    """
                    INSERT OR REPLACE INTO memories
                    (id, capture_id, user_id, kind, layer, content, summary, source, source_url, confidence, importance, status, topics_json, entity_ids_json, occurred_at, captured_at, updated_at, raw_excerpt)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory["id"],
                        memory.get("capture_id"),
                        user_id,
                        memory.get("kind", "observation"),
                        memory_layer(memory.get("kind", "observation"), memory.get("layer")),
                        memory.get("content", ""),
                        memory.get("summary", ""),
                        memory.get("source", "vault"),
                        memory.get("source_url"),
                        memory.get("confidence", "confirmed"),
                        int(memory.get("importance", 3)),
                        memory.get("status", "active"),
                        json.dumps(topics),
                        json.dumps(entity_ids),
                        memory.get("occurred_at"),
                        captured_at,
                        memory.get("updated_at") or captured_at,
                        memory.get("raw_excerpt"),
                    ),
                )
                if memory.get("status", "active") == "active":
                    conn.execute(
                        "INSERT INTO memory_fts(memory_id, content, summary, source, topics) VALUES (?, ?, ?, ?, ?)",
                        (memory["id"], memory.get("content", ""), memory.get("summary", ""), memory.get("source", "vault"), " ".join(topics)),
                    )
                    self._enqueue_embed_memory_job(
                        conn,
                        memory_id=memory["id"],
                        capture_id=memory.get("capture_id"),
                        user_id=user_id,
                        content=memory.get("content", ""),
                        summary=memory.get("summary", ""),
                        source=memory.get("source", "vault"),
                        layer=memory_layer(memory.get("kind", "observation"), memory.get("layer")),
                        topics=topics,
                        captured_at=captured_at,
                        priority=90,
                    )
                for entity_id in entity_ids:
                    conn.execute(
                        "INSERT OR REPLACE INTO memory_entities(memory_id, entity_id, user_id, created_at) VALUES (?, ?, ?, ?)",
                        (memory["id"], entity_id, user_id, captured_at),
                    )
                for topic in topics:
                    conn.execute(
                        "INSERT OR REPLACE INTO memory_topics(memory_id, topic, user_id, created_at) VALUES (?, ?, ?, ?)",
                        (memory["id"], topic, user_id, captured_at),
                    )

            for task in sorted(tasks, key=lambda item: item.get("captured_at") or ""):
                topics = task.get("topics", [])
                entity_ids = task.get("entity_ids", [])
                captured_at = task.get("captured_at") or timestamp
                conn.execute(
                    """
                    INSERT OR REPLACE INTO tasks
                    (id, capture_id, user_id, kind, content, status, importance, topics_json, entity_ids_json, captured_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task["id"],
                        task.get("capture_id"),
                        user_id,
                        task.get("kind", "action"),
                        task.get("content", ""),
                        task.get("status", "open"),
                        int(task.get("importance", 3)),
                        json.dumps(topics),
                        json.dumps(entity_ids),
                        captured_at,
                    ),
                )
                for entity_id in entity_ids:
                    conn.execute(
                        "INSERT OR REPLACE INTO task_entities(task_id, entity_id, user_id, created_at) VALUES (?, ?, ?, ?)",
                        (task["id"], entity_id, user_id, captured_at),
                    )
                for topic in topics:
                    conn.execute(
                        "INSERT OR REPLACE INTO task_topics(task_id, topic, user_id, created_at) VALUES (?, ?, ?, ?)",
                        (task["id"], topic, user_id, captured_at),
                    )

            for edge in edges:
                conn.execute(
                    "INSERT OR REPLACE INTO graph_edges(id, user_id, source_id, target_id, kind, weight, evidence_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        edge["id"],
                        user_id,
                        edge.get("source_id", ""),
                        edge.get("target_id", ""),
                        edge.get("kind", "related"),
                        float(edge.get("weight", 1.0)),
                        edge.get("evidence_id"),
                        edge.get("created_at") or timestamp,
                    ),
                )

            for event in events:
                conn.execute(
                    "INSERT OR REPLACE INTO memory_events(id, user_id, object_id, object_type, event_type, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        event["id"],
                        user_id,
                        event.get("object_id", ""),
                        event.get("object_type", "unknown"),
                        event.get("event_type", "unknown"),
                        json.dumps(event.get("metadata", {})),
                        event.get("created_at") or timestamp,
                    ),
                )

            self._event(
                conn,
                user_id,
                str(self.vault.root),
                "index",
                "rebuilt_from_vault",
                {
                    "captures": len(captures),
                    "memories": len(memories),
                    "tasks": len(tasks),
                    "entities": len(entities),
                    "edges": len(edges),
                    "events": len(events),
                    "imports": len(imports),
                    "source_accounts": len(restored_account_ids),
                    "sync_cursors": restored_cursor_count,
                    "sync_devices": restored_device_count,
                    "sync_receipts": restored_receipt_count,
                    "tombstones": tombstone_counts,
                },
            )

        return {
            "rebuilt_at": timestamp,
            "vault_path": str(self.vault.root),
            "index_path": str(self.db_path),
            "captures": len(captures),
            "memories": len(memories),
            "tasks": len(tasks),
            "entities": len(entities),
            "edges": len(edges),
            "events": len(events),
            "imports": len(imports),
            "source_accounts": len(restored_account_ids),
            "sync_cursors": restored_cursor_count,
            "sync_devices": restored_device_count,
            "sync_receipts": restored_receipt_count,
            "tombstones": tombstone_counts,
        }

    def export_markdown(self, user_id: str) -> str:
        data = self.export_json(user_id)
        lines = ["# Cortex Export", "", f"Exported: {data['exported_at']}", "", "## Stats", ""]
        for key, value in data["stats"].items():
            if not isinstance(value, list):
                lines.append(f"- {key}: {value}")
        lines.extend(["", "## Memories", ""])
        for memory in data["memories"]:
            layer = memory_layer(memory.get("kind"), memory.get("layer")).title()
            lines.append(f"### {layer} / {memory['kind'].title()} - {memory['source']} - {memory['captured_at']}")
            lines.append("")
            lines.append(memory["content"])
            topics = memory.get("topics") or []
            if topics:
                lines.append("")
                lines.append("Topics: " + ", ".join(topics))
            lines.append("")
        lines.extend(["## Open Tasks", ""])
        for task in data["tasks"]:
            if task.get("status") == "open":
                lines.append(f"- [{task['kind']}] {task['content']}")
        return "\n".join(lines)

    def trust_summary(self, user_id: str) -> dict[str, Any]:
        user_settings = self.settings(user_id)
        stats = self.stats(user_id)
        diagnostics = self.diagnostics(user_id)
        with connect(self.db_path) as conn:
            source_rows = conn.execute(
                """
                SELECT
                  source,
                  COUNT(*) AS total,
                  SUM(CASE WHEN review_status = 'pending' THEN 1 ELSE 0 END) AS pending,
                  SUM(CASE WHEN review_status = 'approved' THEN 1 ELSE 0 END) AS approved,
                  SUM(CASE WHEN review_status = 'archived' THEN 1 ELSE 0 END) AS archived,
                  MAX(captured_at) AS last_seen
                FROM captures
                WHERE user_id = ?
                GROUP BY source
                ORDER BY total DESC, last_seen DESC
                LIMIT 16
                """,
                (user_id,),
            ).fetchall()
            recent_agent_events = conn.execute(
                """
                SELECT COUNT(*)
                FROM memory_events
                WHERE user_id = ?
                  AND object_type = 'agent'
                  AND created_at >= datetime('now', '-7 days')
                """,
                (user_id,),
            ).fetchone()[0]
            last_agent_event = conn.execute(
                """
                SELECT MAX(created_at)
                FROM memory_events
                WHERE user_id = ? AND object_type = 'agent'
                """,
                (user_id,),
            ).fetchone()[0]

        risk_flags: list[str] = []
        if not user_settings["review_new_captures"]:
            risk_flags.append("New saves are approved automatically.")
        if user_settings["allow_pending_in_context"]:
            risk_flags.append("Pending saves can appear in assistant context.")
        if user_settings["allow_agent_writes"]:
            risk_flags.append("Connected agents can write to memory.")
        if user_settings["allow_agent_exports"]:
            risk_flags.append("Connected agents can export or prepare memory handoffs.")
        if user_settings["allow_agent_maintenance"]:
            risk_flags.append("Connected agents can run maintenance actions.")
        if user_settings["allow_agent_destructive_actions"]:
            risk_flags.append("Connected agents can run destructive actions.")
        if not user_settings["redact_sensitive_context"]:
            risk_flags.append("Sensitive-pattern redaction is off for shared context.")
        if diagnostics["status"] != "ok":
            risk_flags.append("Storage health needs maintenance.")

        score = 100
        score -= 16 if user_settings["allow_pending_in_context"] else 0
        score -= 12 if not user_settings["review_new_captures"] else 0
        score -= 12 if user_settings["allow_agent_writes"] else 0
        score -= 10 if user_settings["allow_agent_exports"] else 0
        score -= 10 if user_settings["allow_agent_maintenance"] else 0
        score -= 18 if user_settings["allow_agent_destructive_actions"] else 0
        score -= 18 if not user_settings["redact_sensitive_context"] else 0
        score -= min(20, stats["pending_captures"] * 2)
        score -= 12 if diagnostics["status"] != "ok" else 0
        score = max(0, min(100, score))
        if score >= 80:
            mode = "guarded"
        elif score >= 55:
            mode = "balanced"
        else:
            mode = "open"

        return {
            "generated_at": now_iso(),
            "trust_score": score,
            "mode": mode,
            "settings": user_settings,
            "counts": {
                "captures": stats["captures"],
                "pending_captures": stats["pending_captures"],
                "active_memories": stats["memories"],
                "open_tasks": stats["tasks"],
                "audit_events": diagnostics["counts"].get("events", 0),
                "agent_events_7d": recent_agent_events,
            },
            "risk_flags": risk_flags,
            "source_counts": [dict(row) for row in source_rows],
            "last_agent_event_at": last_agent_event,
            "redaction_labels": [
                "[REDACTED_OPENAI_KEY]",
                "[REDACTED_GITHUB_TOKEN]",
                "[REDACTED_SLACK_TOKEN]",
                "[REDACTED_SECRET]",
                "[REDACTED_EMAIL]",
                "[REDACTED_NUMBER]",
            ],
        }

    def data_lifecycle_report(self, user_id: str) -> dict[str, Any]:
        diagnostics = self.diagnostics(user_id)
        trust = self.trust_summary(user_id)
        reliability = self.reliability_report(user_id)
        latest_backup = self.latest_backup()
        user_settings = trust["settings"]
        vault_counts = (diagnostics.get("vault") or {}).get("record_counts", {})
        tombstones_count = int(vault_counts.get("deletion_tombstones") or 0)
        backup_count = int(vault_counts.get("backups") or 0)
        warnings: list[str] = []
        if diagnostics["status"] != "ok":
            warnings.append("Storage health needs maintenance before the lifecycle report is fully reliable.")
        if latest_backup is None:
            warnings.append("No backup has been created yet.")
        elif isinstance(latest_backup.get("age_days"), int) and latest_backup["age_days"] > 7:
            warnings.append("Latest backup is older than 7 days.")
        if not user_settings["redact_sensitive_context"]:
            warnings.append("Shared exports and context are not redacted.")
        if user_settings["allow_agent_destructive_actions"]:
            warnings.append("Connected agents can delete local data.")
        if not warnings:
            warnings.append("Lifecycle posture is ready for local beta use.")

        return {
            "generated_at": now_iso(),
            "status": "ok" if diagnostics["status"] == "ok" and latest_backup else "needs_attention",
            "storage": {
                "mode": "local_first",
                "database_path": str(self.db_path),
                "vault_path": str(self.vault.root),
                "database_bytes": diagnostics["db_size_bytes"],
                "wal_bytes": diagnostics["wal_size_bytes"],
                "vault_status": (diagnostics.get("vault") or {}).get("status", "unknown"),
            },
            "record_counts": {
                "captures": diagnostics["counts"].get("captures", 0),
                "active_memories": diagnostics["counts"].get("active_memories", 0),
                "archived_memories": diagnostics["counts"].get("archived_memories", 0),
                "open_tasks": diagnostics["counts"].get("open_tasks", 0),
                "imports": diagnostics["counts"].get("imports", 0),
                "source_accounts": vault_counts.get("source_accounts", 0),
                "sync_cursors": vault_counts.get("sync_cursors", 0),
                "sync_devices": vault_counts.get("sync_devices", 0),
                "sync_receipts": vault_counts.get("sync_receipts", 0),
                "audit_events": diagnostics["counts"].get("events", 0),
                "deletion_tombstones": tombstones_count,
            },
            "backups": {
                "count": backup_count,
                "latest_backup": latest_backup,
                "retention": backup_retention_policy(),
                "include_in_delete_default": True,
            },
            "export": {
                "json_endpoint": "/v1/export.json",
                "markdown_endpoint": "/v1/export.md",
                "redaction_enabled": bool(user_settings["redact_sensitive_context"]),
                "contains_raw_capture_text": True,
                "contains_memory_content": True,
            },
            "deletion": {
                "endpoint": "/v1/user-data?include_backups=true",
                "include_backups_default": True,
                "covered_sqlite": [
                    "captures",
                    "memories",
                    "tasks",
                    "entities",
                    "graph_edges",
                    "imports",
                    "source_accounts",
                    "sync_cursors",
                    "sync_devices",
                    "sync_receipts",
                    "jobs",
                    "events",
                    "settings",
                    "api_tokens",
                ],
                "covered_vault": [
                    "captures",
                    "memories",
                    "tasks",
                    "entities",
                    "graph_edges",
                    "imports",
                    "source_accounts",
                    "sync_cursors",
                    "sync_devices",
                    "sync_receipts",
                    "settings",
                    "events",
                    "attachments",
                    "backups when include_backups=true",
                ],
                "tombstones_count": tombstones_count,
                "tombstone_policy": "block_restore",
                "restore_preserves_tombstones": True,
            },
            "ai_access": {
                "mode": trust["mode"],
                "trust_score": trust["trust_score"],
                "allow_agent_reads": bool(user_settings["allow_agent_reads"]),
                "allow_agent_writes": bool(user_settings["allow_agent_writes"]),
                "allow_agent_exports": bool(user_settings["allow_agent_exports"]),
                "allow_agent_maintenance": bool(user_settings["allow_agent_maintenance"]),
                "allow_agent_destructive_actions": bool(user_settings["allow_agent_destructive_actions"]),
                "redaction_enabled": bool(user_settings["redact_sensitive_context"]),
                "risk_flags": trust["risk_flags"],
            },
            "audit": {
                "events": diagnostics["counts"].get("events", 0),
                "last_event_at": diagnostics.get("last_event_at"),
                "agent_events_7d": trust["counts"].get("agent_events_7d", 0),
            },
            "recommended_actions": list(dict.fromkeys(warnings + reliability["recommended_actions"]))[:8],
        }

    def audit_log(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM memory_events
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            metadata = self._json_or_empty(row["metadata_json"])
            events.append(
                {
                    "id": row["id"],
                    "object_id": row["object_id"],
                    "object_type": row["object_type"],
                    "event_type": row["event_type"],
                    "metadata": metadata,
                    "metadata_text": self._metadata_summary(metadata),
                    "created_at": row["created_at"],
                }
            )
        return events

    def sync_change_feed(
        self,
        user_id: str,
        *,
        after: str = "",
        limit: int = 100,
        device_id: str = "",
        signing_key: str = "",
        shard: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        limit = max(1, min(1000, int(limit)))
        after = (after or "").strip()
        device_id = (device_id or "").strip()
        warnings: list[str] = []
        device_row = None
        with connect(self.db_path) as conn:
            counts = {
                "captures": conn.execute("SELECT COUNT(*) FROM captures WHERE user_id = ?", (user_id,)).fetchone()[0],
                "memories": conn.execute("SELECT COUNT(*) FROM memories WHERE user_id = ? AND status = 'active'", (user_id,)).fetchone()[0],
                "tasks": conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ?", (user_id,)).fetchone()[0],
                "entities": conn.execute("SELECT COUNT(*) FROM entities WHERE user_id = ?", (user_id,)).fetchone()[0],
                "imports": conn.execute("SELECT COUNT(*) FROM import_sessions WHERE user_id = ? AND deleted_at IS NULL", (user_id,)).fetchone()[0],
                "source_accounts": conn.execute("SELECT COUNT(*) FROM source_accounts WHERE user_id = ? AND disconnected_at IS NULL", (user_id,)).fetchone()[0],
                "sync_cursors": conn.execute("SELECT COUNT(*) FROM sync_cursors WHERE user_id = ?", (user_id,)).fetchone()[0],
                "sync_devices": conn.execute("SELECT COUNT(*) FROM sync_devices WHERE user_id = ? AND revoked_at IS NULL", (user_id,)).fetchone()[0],
                "sync_receipts": conn.execute("SELECT COUNT(*) FROM sync_receipts WHERE user_id = ?", (user_id,)).fetchone()[0],
                "events": conn.execute("SELECT COUNT(*) FROM memory_events WHERE user_id = ?", (user_id,)).fetchone()[0],
            }
            if device_id:
                device_row = conn.execute("SELECT * FROM sync_devices WHERE user_id = ? AND id = ?", (user_id, device_id)).fetchone()
                if not device_row:
                    warnings.append("device_not_found")
                elif device_row["revoked_at"]:
                    warnings.append("device_revoked")
            start_created_at = ""
            start_id = ""
            if after:
                cursor_row = conn.execute(
                    "SELECT id, created_at FROM memory_events WHERE user_id = ? AND id = ?",
                    (user_id, after),
                ).fetchone()
                if not cursor_row:
                    warnings.append("cursor_not_found")
                    rows = []
                else:
                    start_created_at = cursor_row["created_at"]
                    start_id = cursor_row["id"]
                    rows = conn.execute(
                        """
                        SELECT *
                        FROM memory_events
                        WHERE user_id = ?
                          AND (created_at > ? OR (created_at = ? AND id > ?))
                        ORDER BY created_at ASC, id ASC
                        LIMIT ?
                        """,
                        (user_id, start_created_at, start_created_at, start_id, limit + 1),
                    ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM memory_events
                    WHERE user_id = ?
                    ORDER BY created_at ASC, id ASC
                    LIMIT ?
                    """,
                    (user_id, limit + 1),
                ).fetchall()

        selected = rows[:limit]
        events = [self._sync_event_from_row(row) for row in selected]
        next_cursor = events[-1]["id"] if events else after
        device = self._sync_device_from_row(device_row) if device_row else None
        if device and "device_revoked" not in warnings:
            seen_at = now_iso()
            with connect(self.db_path) as conn:
                conn.execute(
                    """
                    UPDATE sync_devices
                    SET first_cursor = COALESCE(first_cursor, ?),
                        last_cursor = ?,
                        last_seen_at = ?,
                        updated_at = ?
                    WHERE user_id = ? AND id = ?
                    """,
                    (next_cursor, next_cursor, seen_at, seen_at, user_id, device["id"]),
                )
                updated = conn.execute("SELECT * FROM sync_devices WHERE user_id = ? AND id = ?", (user_id, device["id"])).fetchone()
            device = self._sync_device_from_row(updated)
            self.vault.write_sync_device(self._sync_device_record_from_row(updated))

        payload = {
            "generated_at": now_iso(),
            "sync_contract": 1,
            "content_included": False,
            "cursor": after,
            "next_cursor": next_cursor,
            "has_more": len(rows) > limit,
            "high_watermark": {
                "event_id": next_cursor or None,
                "created_at": events[-1]["created_at"] if events else start_created_at or None,
            },
            "shard": shard,
            "counts": counts,
            "device": device,
            "changes": events,
            "warnings": warnings,
            "signature": None,
        }
        if device:
            if signing_key and "device_revoked" not in warnings:
                payload["signature"] = self._sync_feed_signature(payload, signing_key, device_id=device["id"])
            else:
                payload["signature"] = {
                    "algorithm": "hmac-sha256",
                    "configured": False,
                    "device_id": device["id"],
                }
        return payload

    def _sync_feed_signature(self, payload: dict[str, Any], signing_key: str, *, device_id: str) -> dict[str, Any]:
        unsigned = {key: value for key, value in payload.items() if key != "signature"}
        canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        canonical_bytes = canonical.encode("utf-8")
        payload_hash = hashlib.sha256(canonical_bytes).hexdigest()
        value = hmac.new(signing_key.encode("utf-8"), canonical_bytes, hashlib.sha256).hexdigest()
        return {
            "algorithm": "hmac-sha256",
            "configured": True,
            "key_id": "local-sync-signing-key",
            "device_id": device_id,
            "payload_hash": f"sha256:{payload_hash}",
            "value": f"hmac-sha256:{value}",
        }

    def _sync_event_from_row(self, row) -> dict[str, Any]:
        metadata = self._json_or_empty(row["metadata_json"])
        safe_summary = self._support_event_summary(
            {
                "id": row["id"],
                "object_type": row["object_type"],
                "event_type": row["event_type"],
                "metadata": metadata,
                "created_at": row["created_at"],
            }
        )
        object_id = str(row["object_id"] or "")
        safe_object_id = self._safe_sync_object_id(object_id)
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "object_type": row["object_type"],
            "event_type": row["event_type"],
            "object_id": safe_object_id,
            "object_id_hash": hashlib.sha256(object_id.encode("utf-8")).hexdigest()[:16] if object_id else "",
            "object_id_redacted": bool(object_id and not safe_object_id),
            "metadata_keys": safe_summary["metadata_keys"],
            "safe_metadata": safe_summary["safe_metadata"],
        }

    def _safe_sync_object_id(self, object_id: str) -> str | None:
        if re.match(r"^(cap|mem|task|ent|edge|evt|imp|irec|job|sacct|sdev|srec|sync|tok|backup|tomb|vec)_[A-Za-z0-9]+$", object_id or ""):
            return object_id
        return None

    def _support_event_summary(self, event: dict[str, Any]) -> dict[str, Any]:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        safe_metadata: dict[str, Any] = {}
        for key in (
            "tool",
            "success",
            "content_chars",
            "memory_count",
            "size_bytes",
            "format",
            "indexed_memories",
            "vector_indexed_memories",
            "token_id",
            "token_label",
            "token_audience",
            "token_admin",
            "token_scopes",
            "device_id",
            "cursor",
            "manifest_hash",
        ):
            if key in metadata:
                safe_metadata[key] = self._support_safe_payload(metadata[key], key)
        actions = metadata.get("actions")
        if isinstance(actions, list):
            safe_metadata["actions"] = [
                {
                    "name": item.get("name"),
                    "rows": item.get("rows"),
                }
                for item in actions
                if isinstance(item, dict)
            ][:12]
        return {
            "id": event.get("id"),
            "object_type": event.get("object_type"),
            "event_type": event.get("event_type"),
            "created_at": event.get("created_at"),
            "metadata_keys": sorted(metadata.keys()),
            "safe_metadata": safe_metadata,
        }

    def _support_safe_payload(self, value: Any, key: str = "") -> Any:
        if key in SUPPORT_OMITTED_KEYS:
            return "[omitted from support bundle]"
        if isinstance(value, dict):
            safe: dict[str, Any] = {}
            for child_key, child_value in value.items():
                if child_key in SUPPORT_OMITTED_KEYS:
                    safe[child_key] = "[omitted from support bundle]"
                else:
                    safe[child_key] = self._support_safe_payload(child_value, child_key)
            return safe
        if isinstance(value, list):
            return [self._support_safe_payload(item, key) for item in value[:200]]
        if isinstance(value, str):
            text = self._redact_text(value)
            if key in SUPPORT_PATH_KEYS or text.startswith("/Users/"):
                text = self._support_safe_path(text)
            if len(text) > 600:
                return text[:600] + "...[truncated]"
            return text
        return value

    def _support_safe_path(self, value: str) -> str:
        home = str(Path.home())
        if value == home:
            return "~"
        if value.startswith(home + "/"):
            return "~/" + value[len(home) + 1:]
        return value

    def require_agent_access(self, user_id: str, capability: str) -> None:
        user_settings = self.settings(user_id)
        labels = {
            "read": "Agent memory reads are disabled in Cortex Trust controls.",
            "write": "Agent memory writes are disabled in Cortex Trust controls.",
            "export": "Agent context exports are disabled in Cortex Trust controls.",
            "maintenance": "Agent maintenance actions are disabled in Cortex Trust controls.",
            "destructive": "Agent destructive actions are disabled in Cortex Trust controls.",
        }
        setting_by_capability = {
            "read": "allow_agent_reads",
            "write": "allow_agent_writes",
            "export": "allow_agent_exports",
            "maintenance": "allow_agent_maintenance",
            "destructive": "allow_agent_destructive_actions",
        }
        key = setting_by_capability.get(capability)
        if key and not user_settings[key]:
            raise PermissionError(labels.get(capability, "Agent action is disabled in Cortex Trust controls."))

    def agent_payload(self, user_id: str, value: Any) -> Any:
        return self._redact_payload(value) if self.settings(user_id)["redact_sensitive_context"] else value

    def record_agent_event(
        self,
        user_id: str,
        tool_name: str,
        args: dict[str, Any],
        *,
        success: bool,
        error: str | None = None,
        token: dict[str, Any] | None = None,
    ) -> None:
        metadata: dict[str, Any] = {
            "tool": tool_name,
            "success": success,
            "arg_keys": sorted(args.keys()),
        }
        if token:
            metadata["token_id"] = token.get("token_id")
            metadata["token_label"] = token.get("label")
            metadata["token_audience"] = token.get("audience", "mcp")
            metadata["token_admin"] = bool(token.get("admin"))
            metadata["token_scopes"] = token.get("scopes", [])
        content = args.get("content")
        if isinstance(content, str):
            metadata["content_chars"] = len(content)
        query = args.get("query")
        if isinstance(query, str):
            metadata["query"] = query[:120]
        if error:
            metadata["error"] = error[:240]
        with connect(self.db_path) as conn:
            self._event(conn, user_id, f"mcp:{tool_name}", "agent", "tool_call", metadata)

    def _enqueue_job(
        self,
        conn,
        *,
        user_id: str,
        job_type: str,
        object_type: str,
        object_id: str,
        unique_key: str,
        payload: dict[str, Any],
        priority: int = 100,
        run_at: str | None = None,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        timestamp = now_iso()
        job_id = stable_id("job_", unique_key)
        conn.execute(
            """
            INSERT OR IGNORE INTO memory_jobs
            (id, user_id, job_type, object_type, object_id, status, priority, run_at, attempts, max_attempts, unique_key, payload_json, result_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, 0, ?, ?, ?, '{}', ?, ?)
            """,
            (
                job_id,
                user_id,
                job_type,
                object_type,
                object_id,
                priority,
                run_at or timestamp,
                max_attempts,
                unique_key,
                json.dumps(payload),
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute("SELECT * FROM memory_jobs WHERE unique_key = ?", (unique_key,)).fetchone()
        return self._job_from_row(row)

    def _claim_next_job(self, user_id: str, worker_id: str) -> dict[str, Any] | None:
        timestamp = now_iso()
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT *
                FROM memory_jobs
                WHERE user_id = ?
                  AND status = 'queued'
                  AND run_at <= ?
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
                """,
                (user_id, timestamp),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                """
                UPDATE memory_jobs
                SET status = 'running',
                    attempts = attempts + 1,
                    locked_by = ?,
                    locked_until = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (worker_id, timestamp, timestamp, row["id"]),
            )
            claimed = conn.execute("SELECT * FROM memory_jobs WHERE id = ?", (row["id"],)).fetchone()
        return self._job_from_row(claimed)

    def _run_job(self, job: dict[str, Any], worker_id: str) -> dict[str, Any]:
        try:
            if job["job_type"] == "extract_capture":
                result = self._process_extract_capture_job(job)
            elif job["job_type"] == "embed_memory":
                result = self._process_embed_memory_job(job)
            else:
                raise ValueError(f"Unsupported memory job type: {job['job_type']}")
            completed = self._complete_job(job["id"], result)
            if job["job_type"] == "embed_memory" and result.get("capture_id"):
                self._refresh_capture_embedding_state(
                    job["user_id"],
                    str(result["capture_id"]),
                    last_job_id=job["id"],
                )
            return completed
        except Exception as exc:
            return self._fail_job(job, str(exc))

    def _process_extract_capture_job(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload") or {}
        capture_id = payload["capture_id"]
        user_id = job["user_id"]
        started_at = now_iso()
        with connect(self.db_path) as conn:
            capture = conn.execute(
                "SELECT * FROM captures WHERE user_id = ? AND id = ?",
                (user_id, capture_id),
            ).fetchone()
        if not capture:
            return {
                "capture_id": capture_id,
                "skipped": True,
                "reason": "capture_missing_or_deleted",
                "completed_at": started_at,
            }
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE capture_processing_state
                SET extraction_status = 'running',
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?,
                    last_job_id = ?
                WHERE user_id = ? AND capture_id = ?
                """,
                (started_at, started_at, job["id"], user_id, capture_id),
            )
        content = capture["raw_text"]
        source = capture["source"]
        import_id = capture["import_id"] if "import_id" in capture.keys() else None
        extraction_mode = "local" if import_id else None
        extracted = extract_context(
            content,
            source,
            author_aliases=self.settings(user_id).get("identity_aliases"),
            extraction_mode=extraction_mode,
        )
        extracted["_timestamp"] = capture["captured_at"] or payload.get("captured_at") or started_at
        saved = self.save_capture(
            user_id=user_id,
            content=content,
            source=source,
            source_url=capture["source_url"],
            title=capture["title"],
            extracted=extracted,
            import_id=import_id,
        )
        completed_at = now_iso()
        memory_count = len(saved.get("memories", []))
        task_count = len(saved.get("tasks", []))
        entity_count = len(saved.get("entities", []))
        with connect(self.db_path) as conn:
            embedding_state = self._capture_embedding_status(conn, user_id, capture_id)
            conn.execute(
                """
                INSERT OR REPLACE INTO capture_processing_state
                (capture_id, user_id, ingest_status, extraction_status, embedding_status, memory_count, task_count, entity_count, last_job_id, last_error, queued_at, started_at, completed_at, updated_at)
                VALUES (?, ?, 'materialized', 'succeeded', ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    capture_id,
                    user_id,
                    embedding_state,
                    memory_count,
                    task_count,
                    entity_count,
                    job["id"],
                    payload.get("captured_at") or started_at,
                    started_at,
                    completed_at,
                    completed_at,
                ),
            )
            self._event(
                conn,
                user_id,
                capture_id,
                "capture",
                "processed",
                {"job_id": job["id"], "memories": memory_count, "tasks": task_count, "entities": entity_count},
            )
        return {
            "capture_id": capture_id,
            "memories": memory_count,
            "tasks": task_count,
            "entities": entity_count,
            "completed_at": completed_at,
        }

    def _process_embed_memory_job(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload") or {}
        memory_id = str(payload.get("memory_id") or job["object_id"])
        user_id = job["user_id"]
        started_at = now_iso()
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT id, capture_id, user_id, kind, layer, content, summary, source, topics_json, status, captured_at
                FROM memories
                WHERE user_id = ? AND id = ?
                """,
                (user_id, memory_id),
            ).fetchone()
            if not row:
                return {
                    "memory_id": memory_id,
                    "capture_id": payload.get("capture_id"),
                    "skipped": True,
                    "reason": "memory_missing_or_deleted",
                    "completed_at": started_at,
                }
            capture_id = row["capture_id"]
            if row["status"] != "active":
                self._delete_memory_vector(conn, memory_id)
                return {
                    "memory_id": memory_id,
                    "capture_id": capture_id,
                    "skipped": True,
                    "reason": "memory_not_active",
                    "completed_at": started_at,
                }
            if not self._vector_ready(conn):
                return {
                    "memory_id": memory_id,
                    "capture_id": capture_id,
                    "skipped": True,
                    "reason": "vector_not_available",
                    "vector_available": False,
                    "embedding": embedding_status(),
                    "completed_at": started_at,
                }
            topics = self._json_list(row["topics_json"])
            layer = memory_layer(row["kind"], row["layer"])
            text = embedding_source_text(row["content"], row["summary"], row["source"], layer, " ".join(topics))
            if not text:
                return {
                    "memory_id": memory_id,
                    "capture_id": capture_id,
                    "skipped": True,
                    "reason": "empty_embedding_text",
                    "completed_at": started_at,
                }
            text_hash = embedding_hash(text)
            status = embedding_status()
            if self._memory_vector_current(conn, memory_id, status["model"], text_hash):
                return {
                    "memory_id": memory_id,
                    "capture_id": capture_id,
                    "skipped": True,
                    "reason": "vector_already_current",
                    "text_hash": text_hash,
                    "embedding_model": status["model"],
                    "completed_at": started_at,
                }

        embedding = embed_text_result(text)
        vector = embedding_json(embedding.vector)
        completed_at = now_iso()
        with connect(self.db_path) as conn:
            if not self._vector_ready(conn):
                return {
                    "memory_id": memory_id,
                    "capture_id": capture_id,
                    "skipped": True,
                    "reason": "vector_not_available",
                    "vector_available": False,
                    "embedding": embedding_status(),
                    "completed_at": completed_at,
                }
            still_active = conn.execute(
                "SELECT id FROM memories WHERE user_id = ? AND id = ? AND status = 'active'",
                (user_id, memory_id),
            ).fetchone()
            if not still_active:
                self._delete_memory_vector(conn, memory_id)
                return {
                    "memory_id": memory_id,
                    "capture_id": capture_id,
                    "skipped": True,
                    "reason": "memory_deleted_before_write",
                    "completed_at": completed_at,
                }
            self._write_memory_vector(
                conn,
                memory_id=memory_id,
                user_id=user_id,
                embedding_model=embedding.model,
                text_hash=text_hash,
                vector=vector,
                timestamp=completed_at,
            )
            self._event(
                conn,
                user_id,
                memory_id,
                "memory",
                "vector_indexed",
                {"capture_id": capture_id, "embedding_model": embedding.model, "provider": embedding.provider},
            )
        return {
            "memory_id": memory_id,
            "capture_id": capture_id,
            "text_hash": text_hash,
            "embedding_model": embedding.model,
            "provider": embedding.provider,
            "dimensions": embedding.dimensions,
            "completed_at": completed_at,
        }

    def _complete_job(self, job_id: str, result: dict[str, Any]) -> dict[str, Any]:
        timestamp = now_iso()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE memory_jobs
                SET status = 'succeeded',
                    result_json = ?,
                    last_error = NULL,
                    locked_by = NULL,
                    locked_until = NULL,
                    completed_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(result), timestamp, timestamp, job_id),
            )
            row = conn.execute("SELECT * FROM memory_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job_from_row(row)

    def _fail_job(self, job: dict[str, Any], error: str) -> dict[str, Any]:
        timestamp = now_iso()
        final_status = "failed" if int(job.get("attempts", 0)) >= int(job.get("max_attempts", 3)) else "queued"
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE memory_jobs
                SET status = ?,
                    last_error = ?,
                    locked_by = NULL,
                    locked_until = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (final_status, error[:500], timestamp, job["id"]),
            )
            if job.get("object_type") == "capture":
                conn.execute(
                    """
                    UPDATE capture_processing_state
                    SET extraction_status = ?,
                        last_error = ?,
                        updated_at = ?,
                        last_job_id = ?
                    WHERE user_id = ? AND capture_id = ?
                    """,
                    (final_status, error[:500], timestamp, job["id"], job["user_id"], job["object_id"]),
                )
            row = conn.execute("SELECT * FROM memory_jobs WHERE id = ?", (job["id"],)).fetchone()
        if job.get("job_type") == "embed_memory":
            payload = job.get("payload") or {}
            capture_id = payload.get("capture_id")
            if capture_id:
                self._refresh_capture_embedding_state(
                    job["user_id"],
                    str(capture_id),
                    last_job_id=job["id"],
                    last_error=error[:500],
                )
        return self._job_from_row(row)

    def _refresh_capture_embedding_state(
        self,
        user_id: str,
        capture_id: str,
        *,
        last_job_id: str | None = None,
        last_error: str | None = None,
    ) -> None:
        timestamp = now_iso()
        with connect(self.db_path) as conn:
            capture = conn.execute(
                "SELECT id, captured_at FROM captures WHERE user_id = ? AND id = ?",
                (user_id, capture_id),
            ).fetchone()
            if not capture:
                return
            memory_count = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE user_id = ? AND capture_id = ? AND status = 'active'",
                (user_id, capture_id),
            ).fetchone()[0]
            status = self._capture_embedding_status(conn, user_id, capture_id)
            conn.execute(
                """
                INSERT OR IGNORE INTO capture_processing_state
                (capture_id, user_id, ingest_status, extraction_status, embedding_status, memory_count, task_count, entity_count, last_job_id, last_error, queued_at, completed_at, updated_at)
                VALUES (?, ?, 'materialized', 'succeeded', ?, ?, 0, 0, ?, ?, ?, ?, ?)
                """,
                (
                    capture_id,
                    user_id,
                    status,
                    memory_count,
                    last_job_id,
                    last_error,
                    capture["captured_at"] or timestamp,
                    capture["captured_at"] or timestamp,
                    timestamp,
                ),
            )
            conn.execute(
                """
                UPDATE capture_processing_state
                SET embedding_status = ?,
                    memory_count = ?,
                    last_job_id = COALESCE(?, last_job_id),
                    last_error = ?,
                    updated_at = ?
                WHERE user_id = ? AND capture_id = ?
                """,
                (status, memory_count, last_job_id, last_error, timestamp, user_id, capture_id),
            )

    def _capture_embedding_status(self, conn, user_id: str, capture_id: str) -> str:
        active_count = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE user_id = ? AND capture_id = ? AND status = 'active'",
            (user_id, capture_id),
        ).fetchone()[0]
        if not active_count:
            return "not_needed"
        if not self._vector_ready(conn):
            return "not_available"
        vector_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM memory_vec_map map
            JOIN memories m ON m.id = map.memory_id
            WHERE m.user_id = ?
              AND m.capture_id = ?
              AND m.status = 'active'
            """,
            (user_id, capture_id),
        ).fetchone()[0]
        if vector_count >= active_count:
            return "available"
        job_counts = conn.execute(
            """
            SELECT
              SUM(CASE WHEN j.status IN ('queued', 'running') THEN 1 ELSE 0 END) AS pending,
              SUM(CASE WHEN j.status = 'failed' THEN 1 ELSE 0 END) AS failed
            FROM memory_jobs j
            JOIN memories m ON m.id = j.object_id
            WHERE j.user_id = ?
              AND j.job_type = 'embed_memory'
              AND j.object_type = 'memory'
              AND m.capture_id = ?
            """,
            (user_id, capture_id),
        ).fetchone()
        pending = int(job_counts["pending"] or 0)
        failed = int(job_counts["failed"] or 0)
        if pending:
            return "queued"
        if failed:
            return "failed"
        return "queued"

    def _job_from_row(self, row) -> dict[str, Any]:
        payload = self._json_or_empty(row["payload_json"])
        result = self._json_or_empty(row["result_json"])
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "job_type": row["job_type"],
            "object_type": row["object_type"],
            "object_id": row["object_id"],
            "status": row["status"],
            "priority": row["priority"],
            "run_at": row["run_at"],
            "attempts": row["attempts"],
            "max_attempts": row["max_attempts"],
            "locked_by": row["locked_by"],
            "locked_until": row["locked_until"],
            "unique_key": row["unique_key"],
            "payload": payload,
            "result": result,
            "last_error": row["last_error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
        }

    def _token_hash(self, token: str, salt: str) -> str:
        return hashlib.sha256(f"{salt}:{token}".encode("utf-8")).hexdigest()

    def _json_list(self, value: str | None) -> list[str]:
        try:
            parsed = json.loads(value or "[]")
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed]

    def _json_array(self, value: str | None) -> list[Any]:
        try:
            parsed = json.loads(value or "[]")
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []

    def _save_memory(self, conn, capture_id: str, user_id: str, record: dict[str, Any], source: str, source_url: str | None, captured_at: str, raw_text: str) -> dict[str, Any]:
        memory_id = record["id"]
        kind = record.get("kind", "observation")
        layer = memory_layer(kind, record.get("layer"))
        topics = record.get("topics", [])
        entity_ids = record.get("entity_ids", [])
        conn.execute(
            """
            INSERT OR REPLACE INTO memories
            (id, capture_id, user_id, kind, layer, content, summary, source, source_url, confidence, importance, status, topics_json, entity_ids_json, occurred_at, captured_at, updated_at, raw_excerpt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                capture_id,
                user_id,
                kind,
                layer,
                record.get("content", ""),
                record.get("summary", ""),
                source,
                source_url,
                record.get("confidence", "confirmed"),
                int(record.get("importance", 3)),
                json.dumps(topics),
                json.dumps(entity_ids),
                record.get("occurred_at"),
                captured_at,
                captured_at,
                raw_text[:500],
            ),
        )
        conn.execute("DELETE FROM memory_entities WHERE memory_id = ?", (memory_id,))
        conn.execute("DELETE FROM memory_topics WHERE memory_id = ?", (memory_id,))
        for entity_id in entity_ids:
            conn.execute(
                "INSERT OR REPLACE INTO memory_entities(memory_id, entity_id, user_id, created_at) VALUES (?, ?, ?, ?)",
                (memory_id, entity_id, user_id, captured_at),
            )
        for topic in topics:
            conn.execute(
                "INSERT OR REPLACE INTO memory_topics(memory_id, topic, user_id, created_at) VALUES (?, ?, ?, ?)",
                (memory_id, topic, user_id, captured_at),
            )
        conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
        conn.execute(
            "INSERT INTO memory_fts(memory_id, content, summary, source, topics) VALUES (?, ?, ?, ?, ?)",
            (memory_id, record.get("content", ""), record.get("summary", ""), source, " ".join(topics)),
        )
        self._enqueue_embed_memory_job(
            conn,
            memory_id=memory_id,
            capture_id=capture_id,
            user_id=user_id,
            content=record.get("content", ""),
            summary=record.get("summary", ""),
            source=source,
            layer=layer,
            topics=topics,
            captured_at=captured_at,
        )
        return {
            "id": memory_id,
            "capture_id": capture_id,
            "user_id": user_id,
            "kind": kind,
            "layer": layer,
            "content": record.get("content", ""),
            "summary": record.get("summary", ""),
            "source": source,
            "source_url": source_url,
            "confidence": record.get("confidence", "confirmed"),
            "importance": int(record.get("importance", 3)),
            "status": "active",
            "topics": topics,
            "entity_ids": entity_ids,
            "occurred_at": record.get("occurred_at"),
            "captured_at": captured_at,
            "updated_at": captured_at,
            "raw_excerpt": raw_text[:500],
        }

    def _vector_ready(self, conn) -> bool:
        if embedding_status()["dimensions"] != VECTOR_DIMENSIONS:
            return False
        status = sqlite_vec_status(conn)
        if not status["available"]:
            return False
        try:
            conn.execute("SELECT 1 FROM memory_vec LIMIT 1")
            return True
        except sqlite3.Error:
            return False

    def _vector_search(self, conn, user_id: str, query: str, limit: int, kind: str | None, layer: str | None, user_settings: dict[str, Any]) -> list[Any]:
        if not self._vector_ready(conn):
            return []
        filters, params = self._memory_filters(user_id, user_settings, alias="m", kind=kind, layer=layer)
        where = " AND ".join(filters)
        try:
            vector = embedding_json(embed_text(query))
        except Exception:
            return []
        try:
            return conn.execute(
                f"""
                SELECT m.*, memory_vec.distance AS vector_distance
                FROM memory_vec
                JOIN memory_vec_map map ON map.vec_rowid = memory_vec.rowid
                JOIN memories m ON m.id = map.memory_id
                WHERE memory_vec.embedding MATCH ? AND {where}
                ORDER BY memory_vec.distance ASC, m.importance DESC, m.captured_at DESC
                LIMIT ?
                """,
                [vector, *params, limit],
            ).fetchall()
        except sqlite3.Error:
            return []

    def _fuse_search_rows(self, query: str, fts_rows: list[Any], vector_rows: list[Any], limit: int) -> list[Any]:
        ranked: dict[str, dict[str, Any]] = {}
        for index, row in enumerate(fts_rows):
            entry = ranked.setdefault(row["id"], {"row": row, "score": 0.0})
            entry["score"] += 0.6 / (60 + index)
        for index, row in enumerate(vector_rows):
            entry = ranked.setdefault(row["id"], {"row": row, "score": 0.0})
            entry["score"] += 0.4 / (60 + index)
        layer_boosts = query_layer_boosts(query)
        for entry in ranked.values():
            entry["score"] += self._layer_boost(entry["row"], layer_boosts)
        return [item["row"] for item in sorted(ranked.values(), key=lambda item: item["score"], reverse=True)[:limit]]

    def _rank_rows_with_layer_boosts(self, query: str, rows: list[Any], limit: int) -> list[Any]:
        layer_boosts = query_layer_boosts(query)
        ranked = [
            {"row": row, "score": (0.2 / (60 + index)) + self._layer_boost(row, layer_boosts)}
            for index, row in enumerate(rows)
        ]
        return [item["row"] for item in sorted(ranked, key=lambda item: item["score"], reverse=True)[:limit]]

    def _layer_boost(self, row: Any, layer_boosts: dict[str, float]) -> float:
        if not layer_boosts:
            return 0.0
        keys = set(row.keys())
        layer = memory_layer(row["kind"], row["layer"] if "layer" in keys else None)
        return layer_boosts.get(layer, 0.0)

    def _enqueue_embed_memory_job(
        self,
        conn,
        *,
        memory_id: str,
        capture_id: str | None,
        user_id: str,
        content: str,
        summary: str | None,
        source: str,
        layer: str,
        topics: list[str],
        captured_at: str,
        priority: int = 80,
    ) -> dict[str, Any] | None:
        if not self._vector_ready(conn):
            return None
        text = embedding_source_text(content, summary, source, layer, " ".join(topics))
        if not text:
            return None
        text_hash = embedding_hash(text)
        status = embedding_status()
        if self._memory_vector_current(conn, memory_id, status["model"], text_hash):
            return None
        unique_key = f"embed_memory:{memory_id}:{status['model']}:{status['dimensions']}:{text_hash}"
        payload = {
            "memory_id": memory_id,
            "capture_id": capture_id,
            "text_hash": text_hash,
            "embedding_model": status["model"],
            "embedding_provider": status["provider"],
            "embedding_dimensions": status["dimensions"],
            "captured_at": captured_at,
        }
        job = self._enqueue_job(
            conn,
            user_id=user_id,
            job_type="embed_memory",
            object_type="memory",
            object_id=memory_id,
            unique_key=unique_key,
            payload=payload,
            priority=priority,
        )
        if job["status"] != "queued":
            timestamp = now_iso()
            conn.execute(
                """
                UPDATE memory_jobs
                SET status = 'queued',
                    attempts = 0,
                    run_at = ?,
                    locked_by = NULL,
                    locked_until = NULL,
                    payload_json = ?,
                    result_json = '{}',
                    last_error = NULL,
                    completed_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (timestamp, json.dumps(payload), timestamp, job["id"]),
            )
            row = conn.execute("SELECT * FROM memory_jobs WHERE id = ?", (job["id"],)).fetchone()
            job = self._job_from_row(row)
        return job

    def _memory_vector_current(self, conn, memory_id: str, embedding_model: str, text_hash: str) -> bool:
        try:
            row = conn.execute(
                "SELECT vec_rowid, embedding_model, text_hash FROM memory_vec_map WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
            if not row or row["embedding_model"] != embedding_model or row["text_hash"] != text_hash:
                return False
            return conn.execute("SELECT 1 FROM memory_vec WHERE rowid = ?", (row["vec_rowid"],)).fetchone() is not None
        except sqlite3.Error:
            return False

    def _write_memory_vector(
        self,
        conn,
        *,
        memory_id: str,
        user_id: str,
        embedding_model: str,
        text_hash: str,
        vector: str,
        timestamp: str,
    ) -> None:
        existing = conn.execute("SELECT vec_rowid FROM memory_vec_map WHERE memory_id = ?", (memory_id,)).fetchone()
        if existing:
            vec_rowid = existing["vec_rowid"]
            conn.execute("DELETE FROM memory_vec WHERE rowid = ?", (vec_rowid,))
            conn.execute(
                "UPDATE memory_vec_map SET user_id = ?, embedding_model = ?, text_hash = ?, updated_at = ? WHERE vec_rowid = ?",
                (user_id, embedding_model, text_hash, timestamp, vec_rowid),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO memory_vec_map(memory_id, user_id, embedding_model, text_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (memory_id, user_id, embedding_model, text_hash, timestamp, timestamp),
            )
            vec_rowid = cursor.lastrowid
        conn.execute("INSERT INTO memory_vec(rowid, embedding) VALUES (?, ?)", (vec_rowid, vector))

    def _index_memory_vector(self, conn, *, memory_id: str, user_id: str, content: str, summary: str | None, source: str, layer: str, topics: list[str], captured_at: str) -> bool:
        if not self._vector_ready(conn):
            return False
        text = embedding_source_text(content, summary, source, layer, " ".join(topics))
        if not text:
            return False
        text_hash = embedding_hash(text)
        embedding = embed_text_result(text)
        vector = embedding_json(embedding.vector)
        try:
            self._write_memory_vector(
                conn,
                memory_id=memory_id,
                user_id=user_id,
                embedding_model=embedding.model,
                text_hash=text_hash,
                vector=vector,
                timestamp=captured_at,
            )
            return True
        except sqlite3.Error:
            return False

    def _delete_memory_vector(self, conn, memory_id: str) -> None:
        if not self._vector_ready(conn):
            return
        try:
            row = conn.execute("SELECT vec_rowid FROM memory_vec_map WHERE memory_id = ?", (memory_id,)).fetchone()
            if row:
                conn.execute("DELETE FROM memory_vec WHERE rowid = ?", (row["vec_rowid"],))
                conn.execute("DELETE FROM memory_vec_map WHERE vec_rowid = ?", (row["vec_rowid"],))
        except sqlite3.Error:
            return

    def _purge_edges_for_objects(self, conn, user_id: str, object_ids: list[str]) -> list[str]:
        ids = [value for value in dict.fromkeys(object_ids) if value]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"""
            SELECT id
            FROM graph_edges
            WHERE user_id = ?
              AND (
                source_id IN ({placeholders})
                OR target_id IN ({placeholders})
                OR evidence_id IN ({placeholders})
              )
            """,
            [user_id, *ids, *ids, *ids],
        ).fetchall()
        edge_ids = [row["id"] for row in rows]
        if edge_ids:
            edge_placeholders = ",".join("?" for _ in edge_ids)
            conn.execute(f"DELETE FROM graph_edges WHERE user_id = ? AND id IN ({edge_placeholders})", [user_id, *edge_ids])
        return edge_ids

    def _purge_memory_rows(self, conn, user_id: str, memory_ids: list[str]) -> None:
        ids = [value for value in dict.fromkeys(memory_ids) if value]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        for memory_id in ids:
            self._delete_memory_vector(conn, memory_id)
        conn.execute(f"DELETE FROM memory_jobs WHERE user_id = ? AND object_type = 'memory' AND object_id IN ({placeholders})", [user_id, *ids])
        conn.execute(f"DELETE FROM memory_fts WHERE memory_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM memory_entities WHERE user_id = ? AND memory_id IN ({placeholders})", [user_id, *ids])
        conn.execute(f"DELETE FROM memory_topics WHERE user_id = ? AND memory_id IN ({placeholders})", [user_id, *ids])
        conn.execute(f"DELETE FROM memories WHERE user_id = ? AND id IN ({placeholders})", [user_id, *ids])

    def _purge_task_rows(self, conn, user_id: str, task_ids: list[str]) -> None:
        ids = [value for value in dict.fromkeys(task_ids) if value]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM task_entities WHERE user_id = ? AND task_id IN ({placeholders})", [user_id, *ids])
        conn.execute(f"DELETE FROM task_topics WHERE user_id = ? AND task_id IN ({placeholders})", [user_id, *ids])
        conn.execute(f"DELETE FROM tasks WHERE user_id = ? AND id IN ({placeholders})", [user_id, *ids])

    def _clear_user_vectors(self, conn, user_id: str) -> None:
        if not self._vector_ready(conn):
            return
        try:
            rows = conn.execute("SELECT vec_rowid FROM memory_vec_map WHERE user_id = ?", (user_id,)).fetchall()
            for row in rows:
                conn.execute("DELETE FROM memory_vec WHERE rowid = ?", (row["vec_rowid"],))
            conn.execute("DELETE FROM memory_vec_map WHERE user_id = ?", (user_id,))
        except sqlite3.Error:
            return

    def _vector_count(self, conn, user_id: str) -> int:
        if not self._vector_ready(conn):
            return 0
        try:
            return conn.execute("SELECT COUNT(*) FROM memory_vec_map WHERE user_id = ?", (user_id,)).fetchone()[0]
        except sqlite3.Error:
            return 0

    def _settings(self, conn, user_id: str) -> dict[str, Any]:
        settings = dict(DEFAULT_USER_SETTINGS)
        vault_settings = self.vault.read_settings(user_id)
        if vault_settings:
            settings.update({key: value for key, value in vault_settings.items() if key in settings})
        try:
            rows = conn.execute("SELECT key, value_json FROM user_settings WHERE user_id = ?", (user_id,)).fetchall()
        except sqlite3.Error:
            return settings
        for row in rows:
            if row["key"] not in settings:
                continue
            try:
                settings[row["key"]] = json.loads(row["value_json"])
            except json.JSONDecodeError:
                continue
        settings["review_new_captures"] = bool(settings["review_new_captures"])
        settings["allow_pending_in_context"] = bool(settings["allow_pending_in_context"])
        settings["allow_agent_reads"] = bool(settings["allow_agent_reads"])
        settings["allow_agent_writes"] = bool(settings["allow_agent_writes"])
        settings["allow_agent_exports"] = bool(settings["allow_agent_exports"])
        settings["allow_agent_maintenance"] = bool(settings["allow_agent_maintenance"])
        settings["allow_agent_destructive_actions"] = bool(settings["allow_agent_destructive_actions"])
        settings["redact_sensitive_context"] = bool(settings["redact_sensitive_context"])
        settings["source_policies"] = _normalize_source_policies(settings.get("source_policies"))
        settings["identity_aliases"] = _normalize_identity_aliases(settings.get("identity_aliases"))
        try:
            settings["context_pack_limit"] = min(50, max(4, int(settings["context_pack_limit"])))
        except (TypeError, ValueError):
            settings["context_pack_limit"] = int(DEFAULT_USER_SETTINGS["context_pack_limit"])
        return settings

    def _redact_text(self, value: str, enabled: bool = True) -> str:
        if not enabled:
            return value
        redacted = value
        for pattern, replacement in SENSITIVE_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        return redacted

    def _redact_payload(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redact_text(value)
        if isinstance(value, list):
            return [self._redact_payload(item) for item in value]
        if isinstance(value, tuple):
            return [self._redact_payload(item) for item in value]
        if isinstance(value, dict):
            return {key: self._redact_payload(item) for key, item in value.items()}
        return value

    def _json_or_empty(self, value: str | None) -> dict[str, Any]:
        try:
            payload = json.loads(value or "{}")
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _metadata_summary(self, metadata: dict[str, Any]) -> str:
        if not metadata:
            return ""
        pieces: list[str] = []
        if "tool" in metadata:
            pieces.append(f"tool={metadata['tool']}")
        if "source" in metadata:
            pieces.append(f"source={metadata['source']}")
        if "title" in metadata and metadata["title"]:
            pieces.append(f"title={metadata['title']}")
        if "query" in metadata and metadata["query"]:
            pieces.append(f"query={metadata['query']}")
        if "success" in metadata:
            pieces.append(f"success={metadata['success']}")
        if "content_chars" in metadata:
            pieces.append(f"chars={metadata['content_chars']}")
        if "memory_count" in metadata:
            pieces.append(f"memories={metadata['memory_count']}")
        if "error" in metadata:
            pieces.append(f"error={metadata['error']}")
        return ", ".join(str(piece) for piece in pieces[:6])

    def _memory_filters(self, user_id: str, user_settings: dict[str, Any], *, alias: str = "m", kind: str | None = None, layer: str | None = None) -> tuple[list[str], list[Any]]:
        filters = [f"{alias}.user_id = ?", f"{alias}.status = 'active'"]
        params: list[Any] = [user_id]
        if kind:
            filters.append(f"{alias}.kind = ?")
            params.append(kind)
        if layer:
            filters.append(f"{alias}.layer = ?")
            params.append(memory_layer(None, layer))
        if not user_settings["allow_pending_in_context"]:
            filters.append(
                f"({alias}.capture_id IS NULL OR EXISTS (SELECT 1 FROM captures c WHERE c.id = {alias}.capture_id AND c.user_id = {alias}.user_id AND c.review_status = 'approved'))"
            )
        source_policies = _normalize_source_policies(user_settings.get("source_policies"))
        excluded_sources = [source for source, policy in source_policies.items() if not policy.get("allow_ai_context", True)]
        if excluded_sources:
            filters.append(f"{alias}.source NOT IN ({','.join('?' for _ in excluded_sources)})")
            params.extend(excluded_sources)
        review_sources = [source for source, policy in source_policies.items() if policy.get("review_required") and source not in excluded_sources]
        if review_sources:
            filters.append(
                f"({alias}.source NOT IN ({','.join('?' for _ in review_sources)}) OR {alias}.capture_id IS NULL OR EXISTS (SELECT 1 FROM captures c WHERE c.id = {alias}.capture_id AND c.user_id = {alias}.user_id AND c.review_status = 'approved'))"
            )
            params.extend(review_sources)
        return filters, params

    def _save_task(self, conn, capture_id: str, user_id: str, task: dict[str, Any], captured_at: str) -> dict[str, Any]:
        task_id = task["id"]
        topics = task.get("topics", [])
        entity_ids = task.get("entity_ids", [])
        conn.execute(
            """
            INSERT OR REPLACE INTO tasks
            (id, capture_id, user_id, kind, content, status, importance, topics_json, entity_ids_json, captured_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, capture_id, user_id, task.get("kind", "action"), task.get("content", ""), task.get("status", "open"), int(task.get("importance", 3)), json.dumps(topics), json.dumps(entity_ids), captured_at),
        )
        conn.execute("DELETE FROM task_entities WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM task_topics WHERE task_id = ?", (task_id,))
        for entity_id in entity_ids:
            conn.execute(
                "INSERT OR REPLACE INTO task_entities(task_id, entity_id, user_id, created_at) VALUES (?, ?, ?, ?)",
                (task_id, entity_id, user_id, captured_at),
            )
        for topic in topics:
            conn.execute(
                "INSERT OR REPLACE INTO task_topics(task_id, topic, user_id, created_at) VALUES (?, ?, ?, ?)",
                (task_id, topic, user_id, captured_at),
            )
        return {
            "id": task_id,
            "capture_id": capture_id,
            "user_id": user_id,
            "kind": task.get("kind", "action"),
            "content": task.get("content", ""),
            "status": task.get("status", "open"),
            "importance": int(task.get("importance", 3)),
            "topics": topics,
            "entity_ids": entity_ids,
            "captured_at": captured_at,
        }

    def _save_entity(self, conn, user_id: str, entity: dict[str, Any], captured_at: str) -> dict[str, Any]:
        entity_id = entity["id"]
        existing = conn.execute("SELECT id FROM entities WHERE user_id = ? AND id = ?", (user_id, entity_id)).fetchone()
        if existing:
            conn.execute("UPDATE entities SET context = ?, last_seen = ? WHERE user_id = ? AND id = ?", (entity.get("context", ""), captured_at, user_id, entity_id))
        else:
            conn.execute(
                "INSERT INTO entities (id, user_id, kind, name, aliases_json, context, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (entity_id, user_id, entity.get("kind", "person"), entity.get("name", entity_id), json.dumps(entity.get("aliases", [])), entity.get("context", ""), captured_at, captured_at),
            )
        row = conn.execute("SELECT * FROM entities WHERE user_id = ? AND id = ?", (user_id, entity_id)).fetchone()
        return {
            "id": row["id"],
            "user_id": user_id,
            "kind": row["kind"],
            "name": row["name"],
            "aliases": json.loads(row["aliases_json"] or "[]"),
            "context": row["context"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
        }

    def _edge(self, conn, user_id: str, source_id: str, target_id: str, kind: str, evidence_id: str, created_at: str, weight: float = 1.0) -> dict[str, Any]:
        edge_id = stable_id("edge_", user_id + source_id + target_id + kind + evidence_id)
        conn.execute(
            "INSERT OR REPLACE INTO graph_edges (id, user_id, source_id, target_id, kind, weight, evidence_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (edge_id, user_id, source_id, target_id, kind, weight, evidence_id, created_at),
        )
        return {"id": edge_id, "user_id": user_id, "source_id": source_id, "target_id": target_id, "kind": kind, "weight": weight, "evidence_id": evidence_id, "created_at": created_at}

    def _event(self, conn, user_id: str, object_id: str, object_type: str, event_type: str, metadata: dict[str, Any]) -> dict[str, Any]:
        created_at = now_iso()
        event_id = stable_id("evt_", user_id + object_id + object_type + event_type + created_at)
        conn.execute(
            "INSERT OR REPLACE INTO memory_events(id, user_id, object_id, object_type, event_type, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, user_id, object_id, object_type, event_type, json.dumps(metadata), created_at),
        )
        event = {
            "id": event_id,
            "user_id": user_id,
            "object_id": object_id,
            "object_type": object_type,
            "event_type": event_type,
            "metadata": metadata,
            "created_at": created_at,
        }
        self.vault.append_event(event)
        return event

    def _capture_from_row(self, row) -> dict[str, Any]:
        keys = set(row.keys())
        return {
            "id": row["id"],
            "import_id": row["import_id"] if "import_id" in keys else None,
            "source": row["source"],
            "source_url": row["source_url"],
            "title": row["title"],
            "summary": row["summary"],
            "review_status": row["review_status"],
            "approved_at": row["approved_at"],
            "archived_at": row["archived_at"],
            "captured_at": row["captured_at"],
            "memory_count": row["memory_count"] if "memory_count" in keys else None,
            "task_count": row["task_count"] if "task_count" in keys else None,
        }

    def _import_session_from_row(self, row) -> dict[str, Any]:
        keys = set(row.keys())
        remaining_captures = row["remaining_captures"] if "remaining_captures" in keys else 0
        deleted_at = row["deleted_at"] if "deleted_at" in keys else None
        return {
            "import_id": row["id"],
            "id": row["id"],
            "status": row["status"],
            "source_hint": row["source_hint"],
            "processing": row["processing"],
            "paths": self._json_array(row["paths_json"]),
            "sources": self._json_array(row["source_counts_json"]),
            "records_found": row["records_found"],
            "queued": row["queued"],
            "saved": row["saved"],
            "failed": row["failed"],
            "skipped": row["skipped"] if "skipped" in keys else 0,
            "capture_ids": self._json_array(row["capture_ids_json"]),
            "errors": self._json_array(row["errors_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"] if "completed_at" in keys else None,
            "deleted_at": deleted_at,
            "remaining_captures": remaining_captures,
            "remaining_memories": row["remaining_memories"] if "remaining_memories" in keys else 0,
            "remaining_tasks": row["remaining_tasks"] if "remaining_tasks" in keys else 0,
            "can_delete": deleted_at is None and int(remaining_captures or 0) > 0,
        }

    def _import_record_from_row(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "import_id": row["import_id"],
            "ordinal": row["ordinal"],
            "source": row["source"],
            "title": row["title"],
            "source_url": row["source_url"],
            "content_hash": row["content_hash"],
            "chars": row["chars"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "status": row["status"],
            "capture_id": row["capture_id"],
            "job_id": row["job_id"],
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _source_account_from_row(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "source": row["source"],
            "account_label": row["account_label"],
            "account_identifier": row["account_identifier"],
            "connection_type": row["connection_type"],
            "status": row["status"],
            "auth_state": row["auth_state"],
            "policy": self._json_or_empty(row["policy_json"]),
            "metadata": self._json_or_empty(row["metadata_json"]),
            "last_sync_at": row["last_sync_at"],
            "last_error": row["last_error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "disconnected_at": row["disconnected_at"],
        }

    def _sync_cursor_from_row(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "source_account_id": row["source_account_id"],
            "source": row["source"],
            "cursor_name": row["cursor_name"],
            "cursor_value": row["cursor_value"],
            "high_water_mark": row["high_water_mark"],
            "state": self._json_or_empty(row["state_json"]),
            "last_started_at": row["last_started_at"],
            "last_completed_at": row["last_completed_at"],
            "last_error": row["last_error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _sync_device_from_row(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "device_name": row["device_name"],
            "platform": row["platform"],
            "fingerprint": str(row["device_key_hash"] or "")[:16],
            "public_key": row["public_key"],
            "capabilities": self._json_list(row["capabilities_json"]),
            "first_cursor": row["first_cursor"],
            "last_cursor": row["last_cursor"],
            "last_seen_at": row["last_seen_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "revoked_at": row["revoked_at"],
        }

    def _sync_device_record_from_row(self, row) -> dict[str, Any]:
        return {
            **self._sync_device_from_row(row),
            "device_key_hash": row["device_key_hash"],
        }

    def _sync_receipt_from_row(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "device_id": row["device_id"],
            "cursor": row["cursor"],
            "status": row["status"],
            "manifest_hash": row["manifest_hash"],
            "remote_ref": row["remote_ref"],
            "error": row["error"],
            "stats": self._json_or_empty(row["stats_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _upsert_import_session(self, conn, session: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO import_sessions
            (id, user_id, status, source_hint, processing, paths_json, source_counts_json, records_found, queued, saved, failed, skipped, capture_ids_json, errors_json, created_at, updated_at, completed_at, deleted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              status = excluded.status,
              source_hint = excluded.source_hint,
              processing = excluded.processing,
              paths_json = excluded.paths_json,
              source_counts_json = excluded.source_counts_json,
              records_found = excluded.records_found,
              queued = excluded.queued,
              saved = excluded.saved,
              failed = excluded.failed,
              skipped = excluded.skipped,
              capture_ids_json = excluded.capture_ids_json,
              errors_json = excluded.errors_json,
              updated_at = excluded.updated_at,
              completed_at = excluded.completed_at,
              deleted_at = excluded.deleted_at
            """,
            (
                session["id"],
                session["user_id"],
                session["status"],
                session.get("source_hint", ""),
                session.get("processing", "async"),
                json.dumps(session.get("paths") or []),
                json.dumps(session.get("sources") or []),
                int(session.get("records_found") or 0),
                int(session.get("queued") or 0),
                int(session.get("saved") or 0),
                int(session.get("failed") or 0),
                int(session.get("skipped") or 0),
                json.dumps(session.get("capture_ids") or []),
                json.dumps(session.get("errors") or []),
                session["created_at"],
                session["updated_at"],
                session.get("completed_at"),
                session.get("deleted_at"),
            ),
        )
        self.vault.write_import(session)

    def _upsert_import_record(
        self,
        conn,
        *,
        import_id: str,
        user_id: str,
        ordinal: int,
        record: SourceRecord,
        status: str,
        created_at: str,
        updated_at: str,
    ) -> None:
        record_id = stable_id("irec_", import_id + str(ordinal) + record.source + record.title)
        conn.execute(
            """
            INSERT OR REPLACE INTO import_records
            (id, import_id, user_id, ordinal, source, title, source_url, content_hash, chars, metadata_json, status, capture_id, job_id, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
            """,
            (
                record_id,
                import_id,
                user_id,
                ordinal,
                record.source,
                record.title,
                record.source_url,
                stable_id("", record.content),
                len(record.content),
                json.dumps(record.metadata),
                status,
                created_at,
                updated_at,
            ),
        )

    def _memory_from_row(self, row) -> dict[str, Any]:
        keys = set(row.keys())
        kind = row["kind"]
        return {
            "id": row["id"],
            "kind": kind,
            "layer": memory_layer(kind, row["layer"] if "layer" in keys else None),
            "content": row["content"],
            "summary": row["summary"],
            "source": row["source"],
            "source_url": row["source_url"],
            "confidence": row["confidence"],
            "importance": row["importance"],
            "status": row["status"],
            "topics": json.loads(row["topics_json"] or "[]"),
            "entity_ids": json.loads(row["entity_ids_json"] or "[]"),
            "occurred_at": row["occurred_at"],
            "captured_at": row["captured_at"],
            "raw_excerpt": row["raw_excerpt"],
        }

    def _task_from_row(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "content": row["content"],
            "status": row["status"],
            "importance": row["importance"],
            "topics": json.loads(row["topics_json"] or "[]"),
            "entity_ids": json.loads(row["entity_ids_json"] or "[]"),
            "captured_at": row["captured_at"],
        }

    def _entity_from_row(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "name": row["name"],
            "aliases": json.loads(row["aliases_json"] or "[]"),
            "context": row["context"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
        }

    def _memories_by_kind(self, user_id: str, kind: str, limit: int) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            filters, params = self._memory_filters(user_id, user_settings, alias="m", kind=kind)
            where = " AND ".join(filters)
            rows = conn.execute(
                f"""
                SELECT *
                FROM memories m
                WHERE {where}
                ORDER BY m.importance DESC, m.captured_at DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def _memories_by_layer(self, user_id: str, layer: str, limit: int, *, include_pending: bool = False) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            if not include_pending:
                user_settings = {**user_settings, "allow_pending_in_context": False}
            filters, params = self._memory_filters(user_id, user_settings, alias="m", layer=layer)
            where = " AND ".join(filters)
            rows = conn.execute(
                f"""
                SELECT *
                FROM memories m
                WHERE {where}
                ORDER BY m.importance DESC, m.captured_at DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def _recommended_actions(
        self,
        *,
        pending_count: int,
        open_task_count: int,
        captured_today: int,
        top_topics: list[dict[str, Any]],
        recent_decisions: list[dict[str, Any]],
    ) -> list[str]:
        actions: list[str] = []
        if pending_count:
            actions.append(f"Review {pending_count} pending capture{'s' if pending_count != 1 else ''}.")
        if open_task_count:
            actions.append(f"Clear or update {open_task_count} open loop{'s' if open_task_count != 1 else ''}.")
        if captured_today == 0:
            actions.append("Add one useful source today so your personal model has fresh signal.")
        if top_topics:
            actions.append(f"Use Cortex's #{top_topics[0]['topic']} memory before your next AI session.")
        if not recent_decisions:
            actions.append("Capture the next decision explicitly so it is easy to retrieve later.")
        return actions[:5]

    def _fts_query(self, query: str) -> str:
        tokens = [token for token in query.lower().replace("'", "").split() if token]
        normalized: list[str] = []
        for token in tokens:
            clean = "".join(ch for ch in token if ch.isalnum() or ch == "_").strip("_")
            if clean:
                normalized.append(clean + "*")
        return " ".join(normalized)
