from __future__ import annotations

import json
import platform
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import connect, sqlite_vec_status
from .embeddings import VECTOR_MODEL, embed_text, embedding_hash, embedding_json, embedding_source_text
from .extractor import now_iso, stable_id
from .vault import CortexVault


BACKEND_VERSION = "0.1.0"
HEALTH_CONTRACT = 3
BACKEND_FEATURES = (
    "local-vault",
    "capture-surfaces",
    "trust-controls",
    "installer-updates",
    "reliability-hardening",
    "simple-product-loop",
    "operational-readiness",
)
SUPPORT_BUNDLE_SCHEMA = 1


DEFAULT_USER_SETTINGS: dict[str, Any] = {
    "review_new_captures": True,
    "allow_pending_in_context": True,
    "context_pack_limit": 12,
    "allow_agent_reads": True,
    "allow_agent_writes": True,
    "allow_agent_exports": True,
    "redact_sensitive_context": True,
}


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
            for key in ("allow_agent_reads", "allow_agent_writes", "allow_agent_exports", "redact_sensitive_context"):
                if key in updates:
                    merged[key] = bool(updates[key])
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
                INSERT OR REPLACE INTO captures
                (id, user_id, source, source_url, title, raw_text, raw_hash, summary, review_status, approved_at, captured_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (capture_id, user_id, source, source_url, title, content, raw_hash, summary, review_status, approved_at, captured_at),
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

            self.vault.write_settings(user_id, user_settings_snapshot)
            self.vault.write_capture_bundle(
                capture={
                    "id": capture_id,
                    "user_id": user_id,
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

    def search(self, user_id: str, query: str, limit: int = 10, kind: str | None = None) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return self.recent(user_id, limit)

        fts_query = self._fts_query(query)

        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            filters, params = self._memory_filters(user_id, user_settings, alias="m", kind=kind)
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
                    [fts_query, *params, limit * 2],
                ).fetchall()
            vector_rows = self._vector_search(conn, user_id, query, limit * 2, kind, user_settings)
            rows = self._fuse_search_rows(fts_rows, vector_rows, limit)
            if not rows:
                like = f"%{query}%"
                rows = conn.execute(
                    f"""
                    SELECT * FROM memories m
                    WHERE {where} AND (m.content LIKE ? OR m.summary LIKE ? OR m.source LIKE ?)
                    ORDER BY m.importance DESC, m.captured_at DESC
                    LIMIT ?
                    """,
                    [*params, like, like, like, limit],
                ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def open_tasks(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            filters = ["t.user_id = ?", "t.status = 'open'"]
            params: list[Any] = [user_id]
            if not user_settings["allow_pending_in_context"]:
                filters.append(
                    "(t.capture_id IS NULL OR EXISTS (SELECT 1 FROM captures c WHERE c.id = t.capture_id AND c.user_id = t.user_id AND c.review_status = 'approved'))"
                )
            where = " AND ".join(filters)
            rows = conn.execute(
                f"SELECT * FROM tasks t WHERE {where} ORDER BY t.importance DESC, t.captured_at DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def list_topics(self, user_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            review_filter = ""
            if not user_settings["allow_pending_in_context"]:
                review_filter = "AND (m.capture_id IS NULL OR EXISTS (SELECT 1 FROM captures c WHERE c.id = m.capture_id AND c.user_id = m.user_id AND c.review_status = 'approved'))"
            rows = conn.execute(
                f"""
                SELECT mt.topic, COUNT(*) AS count, MAX(m.captured_at) AS last_seen
                FROM memory_topics mt
                JOIN memories m ON m.id = mt.memory_id AND m.user_id = mt.user_id
                WHERE mt.user_id = ? AND m.status = 'active'
                  {review_filter}
                GROUP BY mt.topic
                ORDER BY count DESC, last_seen DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_entities(self, user_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            review_join_filter = ""
            if not user_settings["allow_pending_in_context"]:
                review_join_filter = "AND (m.capture_id IS NULL OR EXISTS (SELECT 1 FROM captures c WHERE c.id = m.capture_id AND c.user_id = m.user_id AND c.review_status = 'approved'))"
            rows = conn.execute(
                f"""
                SELECT e.id, e.name, e.kind, e.context, e.first_seen, e.last_seen, COUNT(m.id) AS memory_count
                FROM entities e
                LEFT JOIN memory_entities me ON me.entity_id = e.id AND me.user_id = e.user_id
                LEFT JOIN memories m ON m.id = me.memory_id AND m.user_id = me.user_id AND m.status = 'active' {review_join_filter}
                WHERE e.user_id = ?
                GROUP BY e.id
                HAVING memory_count > 0
                ORDER BY memory_count DESC, e.last_seen DESC
                LIMIT ?
                """,
                (user_id, limit),
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

    def delete_memory(self, user_id: str, memory_id: str) -> bool:
        timestamp = now_iso()
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT id FROM memories WHERE user_id = ? AND id = ?", (user_id, memory_id)).fetchone()
            if not row:
                return False
            conn.execute("UPDATE memories SET status = 'archived', updated_at = ? WHERE user_id = ? AND id = ?", (timestamp, user_id, memory_id))
            conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
            self._delete_memory_vector(conn, memory_id)
            self._event(conn, user_id, memory_id, "memory", "archived", {})
            self.vault.patch_memory(memory_id, {"status": "archived", "updated_at": timestamp})
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
        return {**counts, "by_kind": by_kind, "top_topics": top_topics, "top_entities": top_entities}

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
                "label": "Save Clipboard",
                "title": "Save one useful thing",
                "detail": "Start with a decision, preference, project detail, or open loop.",
            }
        elif stats["pending_captures"] > 0:
            primary = {
                "action": "review",
                "label": "Review Inbox",
                "title": "Review new saves",
                "detail": f"{stats['pending_captures']} capture{'s' if stats['pending_captures'] != 1 else ''} need a quick approve/archive pass.",
            }
        elif not reuse_done:
            primary = {
                "action": "reuse",
                "label": "Copy Context",
                "title": "Use memory in an AI chat",
                "detail": "Copy a context pack before your next ChatGPT, Claude, Cursor, or MCP session.",
            }
        else:
            primary = {
                "action": "done",
                "label": "Loop Complete",
                "title": "Loop complete today",
                "detail": "You saved or reviewed context and reused memory. Keep Cortex nearby as work changes.",
            }

        def step(key: str, title: str, done: bool, detail: str) -> dict[str, Any]:
            status = "done" if done else "current" if primary["action"] == key else "waiting"
            return {"key": key, "title": title, "status": status, "detail": detail}

        steps = [
            step("capture", "Capture", capture_done, f"{captured_today} saved today, {stats['captures']} total."),
            step("review", "Review", review_done, f"{stats['pending_captures']} waiting in the inbox."),
            step("reuse", "Reuse", reuse_done, f"{reused_today} context pack{'s' if reused_today != 1 else ''} copied today."),
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
            "# Cortex Context Pack",
            "",
            f"Generated: {now_iso()}",
        ]
        if query:
            lines.append(f"Focus: {query}")
        lines.extend([
            "",
            "Use this as durable user context. Prefer cited memory IDs and sources when answering.",
            "",
            "## Suggested Assistant Instruction",
            "",
            "Use the Cortex context below as long-term memory for this conversation. When a memory is relevant, ground the answer in it and mention the memory ID if useful. If the context conflicts with the user's newest message, follow the newest message and note the mismatch.",
            "",
            "## Relevant Memories",
            "",
        ])
        if memories:
            for item in memories:
                date = item.get("captured_at") or ""
                topics_text = ", ".join(item.get("topics") or [])
                suffix = f" Topics: {topics_text}." if topics_text else ""
                lines.append(f"- [{item['id']}] ({item['kind']}, {item['source']}, {date}) {self._redact_text(item['content'], redact)}{suffix}")
        else:
            lines.append("- No active memories matched this focus.")
        lines.extend(["", "## Decisions", ""])
        if decisions:
            for item in decisions:
                lines.append(f"- [{item['id']}] {self._redact_text(item['content'], redact)}")
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
            memories = [self._memory_from_row(row) for row in conn.execute("SELECT * FROM memories WHERE user_id = ? ORDER BY captured_at DESC", (user_id,)).fetchall()]
            tasks = [self._task_from_row(row) for row in conn.execute("SELECT * FROM tasks WHERE user_id = ? ORDER BY captured_at DESC", (user_id,)).fetchall()]
            entities = [self._entity_from_row(row) for row in conn.execute("SELECT * FROM entities WHERE user_id = ? ORDER BY last_seen DESC", (user_id,)).fetchall()]
            edges = [dict(row) for row in conn.execute("SELECT * FROM graph_edges WHERE user_id = ? ORDER BY created_at DESC", (user_id,)).fetchall()]
        payload = {
            "exported_at": now_iso(),
            "user_id": user_id,
            "stats": self.stats(user_id),
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
                "vector_embeddings": self._vector_count(conn, user_id),
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
                    "This bundle is designed for support triage and omits captured text, memory bodies, context packs, and exported user data.",
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
            key=lambda path: path.stat().st_mtime,
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
        timestamp = now_iso().replace(":", "-")
        backup_dir = self.vault.backups_dir
        backup_dir.mkdir(parents=True, exist_ok=True)
        sqlite_backup_path = backup_dir / f"index-{timestamp}.sqlite"
        with sqlite3.connect(self.db_path) as source:
            with sqlite3.connect(sqlite_backup_path) as target:
                source.backup(target)
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
        return {
            "backup_path": str(backup_path),
            "size_bytes": backup_path.stat().st_size,
            "created_at": now_iso(),
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
                SELECT id, content, summary, source, topics_json
                FROM memories
                WHERE user_id = ? AND status = 'active'
                """,
                (user_id,),
            ).fetchall()
            for row in rows:
                topics = " ".join(json.loads(row["topics_json"] or "[]"))
                conn.execute(
                    "INSERT INTO memory_fts(memory_id, content, summary, source, topics) VALUES (?, ?, ?, ?, ?)",
                    (row["id"], row["content"], row["summary"], row["source"], topics),
                )
                self._index_memory_vector(
                    conn,
                    memory_id=row["id"],
                    user_id=user_id,
                    content=row["content"],
                    summary=row["summary"],
                    source=row["source"],
                    topics=json.loads(row["topics_json"] or "[]"),
                    captured_at=now_iso(),
                )
            vector_count = self._vector_count(conn, user_id)
            vector_available = self._vector_ready(conn)
            self._event(conn, user_id, "memory_fts", "maintenance", "rebuilt_search_index", {"indexed_memories": len(rows), "vector_indexed_memories": vector_count})
        return {
            "indexed_memories": len(rows),
            "rebuilt_at": now_iso(),
            "vector_available": vector_available,
            "vector_indexed_memories": vector_count,
            "vector_model": VECTOR_MODEL,
        }

    def rebuild_index_from_vault(self, user_id: str) -> dict[str, Any]:
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
            conn.execute("DELETE FROM captures WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM memory_events WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))

            for key, value in settings.items():
                if key in DEFAULT_USER_SETTINGS:
                    conn.execute(
                        "INSERT OR REPLACE INTO user_settings(user_id, key, value_json, updated_at) VALUES (?, ?, ?, ?)",
                        (user_id, key, json.dumps(value), timestamp),
                    )

            for capture in sorted(captures, key=lambda item: item.get("captured_at") or ""):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO captures
                    (id, user_id, source, source_url, title, raw_text, raw_hash, summary, review_status, approved_at, archived_at, captured_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        capture["id"],
                        user_id,
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
                    (id, capture_id, user_id, kind, content, summary, source, source_url, confidence, importance, status, topics_json, entity_ids_json, occurred_at, captured_at, updated_at, raw_excerpt)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory["id"],
                        memory.get("capture_id"),
                        user_id,
                        memory.get("kind", "observation"),
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
                    self._index_memory_vector(
                        conn,
                        memory_id=memory["id"],
                        user_id=user_id,
                        content=memory.get("content", ""),
                        summary=memory.get("summary", ""),
                        source=memory.get("source", "vault"),
                        topics=topics,
                        captured_at=captured_at,
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
        }

    def export_markdown(self, user_id: str) -> str:
        data = self.export_json(user_id)
        lines = ["# Cortex Export", "", f"Exported: {data['exported_at']}", "", "## Stats", ""]
        for key, value in data["stats"].items():
            if not isinstance(value, list):
                lines.append(f"- {key}: {value}")
        lines.extend(["", "## Memories", ""])
        for memory in data["memories"]:
            lines.append(f"### {memory['kind'].title()} - {memory['source']} - {memory['captured_at']}")
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
            risk_flags.append("Connected agents can export or build context packs.")
        if not user_settings["redact_sensitive_context"]:
            risk_flags.append("Sensitive-pattern redaction is off for shared context.")
        if diagnostics["status"] != "ok":
            risk_flags.append("Storage health needs maintenance.")

        score = 100
        score -= 16 if user_settings["allow_pending_in_context"] else 0
        score -= 12 if not user_settings["review_new_captures"] else 0
        score -= 12 if user_settings["allow_agent_writes"] else 0
        score -= 10 if user_settings["allow_agent_exports"] else 0
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

    def _support_event_summary(self, event: dict[str, Any]) -> dict[str, Any]:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        safe_metadata: dict[str, Any] = {}
        for key in ("tool", "success", "content_chars", "memory_count", "size_bytes", "format", "indexed_memories", "vector_indexed_memories"):
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
        }
        setting_by_capability = {
            "read": "allow_agent_reads",
            "write": "allow_agent_writes",
            "export": "allow_agent_exports",
            "maintenance": "allow_agent_writes",
        }
        key = setting_by_capability.get(capability)
        if key and not user_settings[key]:
            raise PermissionError(labels.get(capability, "Agent action is disabled in Cortex Trust controls."))

    def agent_payload(self, user_id: str, value: Any) -> Any:
        return self._redact_payload(value) if self.settings(user_id)["redact_sensitive_context"] else value

    def record_agent_event(self, user_id: str, tool_name: str, args: dict[str, Any], *, success: bool, error: str | None = None) -> None:
        metadata: dict[str, Any] = {
            "tool": tool_name,
            "success": success,
            "arg_keys": sorted(args.keys()),
        }
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

    def _save_memory(self, conn, capture_id: str, user_id: str, record: dict[str, Any], source: str, source_url: str | None, captured_at: str, raw_text: str) -> dict[str, Any]:
        memory_id = record["id"]
        topics = record.get("topics", [])
        entity_ids = record.get("entity_ids", [])
        conn.execute(
            """
            INSERT OR REPLACE INTO memories
            (id, capture_id, user_id, kind, content, summary, source, source_url, confidence, importance, status, topics_json, entity_ids_json, occurred_at, captured_at, updated_at, raw_excerpt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                capture_id,
                user_id,
                record.get("kind", "observation"),
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
        self._index_memory_vector(
            conn,
            memory_id=memory_id,
            user_id=user_id,
            content=record.get("content", ""),
            summary=record.get("summary", ""),
            source=source,
            topics=topics,
            captured_at=captured_at,
        )
        return {
            "id": memory_id,
            "capture_id": capture_id,
            "user_id": user_id,
            "kind": record.get("kind", "observation"),
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
        status = sqlite_vec_status(conn)
        if not status["available"]:
            return False
        try:
            conn.execute("SELECT 1 FROM memory_vec LIMIT 1")
            return True
        except sqlite3.Error:
            return False

    def _vector_search(self, conn, user_id: str, query: str, limit: int, kind: str | None, user_settings: dict[str, Any]) -> list[Any]:
        if not self._vector_ready(conn):
            return []
        filters, params = self._memory_filters(user_id, user_settings, alias="m", kind=kind)
        where = " AND ".join(filters)
        vector = embedding_json(embed_text(query))
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

    def _fuse_search_rows(self, fts_rows: list[Any], vector_rows: list[Any], limit: int) -> list[Any]:
        ranked: dict[str, dict[str, Any]] = {}
        for index, row in enumerate(fts_rows):
            entry = ranked.setdefault(row["id"], {"row": row, "score": 0.0})
            entry["score"] += 0.6 / (60 + index)
        for index, row in enumerate(vector_rows):
            entry = ranked.setdefault(row["id"], {"row": row, "score": 0.0})
            entry["score"] += 0.4 / (60 + index)
        return [item["row"] for item in sorted(ranked.values(), key=lambda item: item["score"], reverse=True)[:limit]]

    def _index_memory_vector(self, conn, *, memory_id: str, user_id: str, content: str, summary: str | None, source: str, topics: list[str], captured_at: str) -> bool:
        if not self._vector_ready(conn):
            return False
        text = embedding_source_text(content, summary, source, " ".join(topics))
        if not text:
            return False
        text_hash = embedding_hash(text)
        vector = embedding_json(embed_text(text))
        try:
            existing = conn.execute("SELECT vec_rowid FROM memory_vec_map WHERE memory_id = ?", (memory_id,)).fetchone()
            if existing:
                vec_rowid = existing["vec_rowid"]
                conn.execute("DELETE FROM memory_vec WHERE rowid = ?", (vec_rowid,))
                conn.execute(
                    "UPDATE memory_vec_map SET user_id = ?, embedding_model = ?, text_hash = ?, updated_at = ? WHERE vec_rowid = ?",
                    (user_id, VECTOR_MODEL, text_hash, captured_at, vec_rowid),
                )
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO memory_vec_map(memory_id, user_id, embedding_model, text_hash, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (memory_id, user_id, VECTOR_MODEL, text_hash, captured_at, captured_at),
                )
                vec_rowid = cursor.lastrowid
            conn.execute("INSERT INTO memory_vec(rowid, embedding) VALUES (?, ?)", (vec_rowid, vector))
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
        settings["redact_sensitive_context"] = bool(settings["redact_sensitive_context"])
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

    def _memory_filters(self, user_id: str, user_settings: dict[str, Any], *, alias: str = "m", kind: str | None = None) -> tuple[list[str], list[Any]]:
        filters = [f"{alias}.user_id = ?", f"{alias}.status = 'active'"]
        params: list[Any] = [user_id]
        if kind:
            filters.append(f"{alias}.kind = ?")
            params.append(kind)
        if not user_settings["allow_pending_in_context"]:
            filters.append(
                f"({alias}.capture_id IS NULL OR EXISTS (SELECT 1 FROM captures c WHERE c.id = {alias}.capture_id AND c.user_id = {alias}.user_id AND c.review_status = 'approved'))"
            )
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

    def _memory_from_row(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "kind": row["kind"],
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
            actions.append("Save one useful note today so your assistants have fresh context.")
        if top_topics:
            actions.append(f"Copy a context pack for #{top_topics[0]['topic']} before your next AI session.")
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
