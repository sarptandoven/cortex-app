from __future__ import annotations

import json
from typing import Any

from .extractor import extract_context
from .storage import CortexStore


TOOLS = [
    {
        "name": "remember_this",
        "description": "Save text into Cortex memory with extraction, source metadata, and graph links.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "source": {"type": "string", "default": "ai-chat"},
                "title": {"type": "string"},
                "source_url": {"type": "string"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "search_memory",
        "description": "Search Cortex memory across saved context.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "top_k": {"type": "integer", "default": 8}},
            "required": ["query"],
        },
    },
    {
        "name": "get_recent_context",
        "description": "Get recently captured Cortex memories.",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 10}}},
    },
    {
        "name": "get_memory_graph",
        "description": "Return the active Cortex graph of sources, memories, tasks, entities, and relationships.",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 150}}},
    },
    {
        "name": "get_daily_review",
        "description": "Return today's Cortex review: pending captures, open loops, decisions, topics, and recommended actions.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_product_loop",
        "description": "Return the simple Cortex product loop state: capture, review, reuse, and return.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "build_context_pack",
        "description": "Build a copy-ready context pack for ChatGPT, Claude, Cursor, or another assistant.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "default": ""}, "limit": {"type": "integer", "default": 12}, "target": {"type": "string", "default": "mcp-agent"}}},
    },
    {
        "name": "get_decisions",
        "description": "Search decisions in Cortex memory.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "default": "decision"}}},
    },
    {
        "name": "get_open_questions",
        "description": "Return open questions and action items.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_memory_topics",
        "description": "List active memory topics ranked by frequency and recency.",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 30}}},
    },
    {
        "name": "list_memory_entities",
        "description": "List people, projects, organizations, and topics found in active memory.",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 30}}},
    },
    {
        "name": "get_about_person",
        "description": "Retrieve memories involving a person.",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
    {
        "name": "get_about_entity",
        "description": "Retrieve memories involving any named person, project, organization, or topic.",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "limit": {"type": "integer", "default": 12}}, "required": ["name"]},
    },
    {
        "name": "get_memory_stats",
        "description": "Return Cortex memory counts, top topics, and top entities.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_memory_inbox",
        "description": "Return pending captured sources awaiting user review.",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 10}}},
    },
    {
        "name": "approve_memory_capture",
        "description": "Approve a pending captured source so it remains trusted active context.",
        "inputSchema": {"type": "object", "properties": {"capture_id": {"type": "string"}}, "required": ["capture_id"]},
    },
    {
        "name": "archive_memory_capture",
        "description": "Archive a captured source and remove its memories and tasks from active retrieval.",
        "inputSchema": {"type": "object", "properties": {"capture_id": {"type": "string"}}, "required": ["capture_id"]},
    },
    {
        "name": "get_memory_diagnostics",
        "description": "Return local Cortex storage health and maintenance diagnostics.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_reliability_report",
        "description": "Return a user-readable Cortex reliability report with health checks, backup state, and recommended recovery actions.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_support_bundle",
        "description": "Return a sanitized Cortex support bundle with operational health, counts, trust state, and safe event metadata. It omits captured text and memory content.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "create_memory_backup",
        "description": "Create a local backup of the Cortex database.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "repair_memory_storage",
        "description": "Back up Cortex, remove stale local index rows, and rebuild search for active memories.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "rebuild_memory_search",
        "description": "Rebuild the Cortex full-text search index for active memories.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "rebuild_index_from_vault",
        "description": "Rebuild the local Cortex SQLite search index from user-owned vault files on disk.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "export_memory",
        "description": "Export Cortex memory as JSON data or Markdown text.",
        "inputSchema": {"type": "object", "properties": {"format": {"type": "string", "default": "markdown"}}},
    },
    {
        "name": "forget_memory",
        "description": "Archive one memory by id.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    },
    {
        "name": "get_trust_summary",
        "description": "Return Cortex trust settings, risk flags, source counts, and recent agent activity.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_audit_log",
        "description": "Return recent Cortex audit events for captures, approvals, archives, settings, backups, and agent tool calls.",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 30}}},
    },
]


READ_TOOLS = {
    "search_memory",
    "get_recent_context",
    "get_memory_graph",
    "get_daily_review",
    "get_product_loop",
    "get_decisions",
    "get_open_questions",
    "list_memory_topics",
    "list_memory_entities",
    "get_about_person",
    "get_about_entity",
    "get_memory_stats",
    "get_memory_inbox",
    "get_memory_diagnostics",
    "get_reliability_report",
    "get_support_bundle",
    "get_trust_summary",
    "get_audit_log",
}
WRITE_TOOLS = {"remember_this", "approve_memory_capture", "archive_memory_capture", "forget_memory"}
EXPORT_TOOLS = {"build_context_pack", "export_memory"}
MAINTENANCE_TOOLS = {"create_memory_backup", "repair_memory_storage", "rebuild_memory_search", "rebuild_index_from_vault"}


def _require_tool_access(store: CortexStore, user_id: str, name: str) -> None:
    if name in READ_TOOLS:
        store.require_agent_access(user_id, "read")
    if name in WRITE_TOOLS:
        store.require_agent_access(user_id, "write")
    if name in EXPORT_TOOLS:
        store.require_agent_access(user_id, "read")
        store.require_agent_access(user_id, "export")
    if name in MAINTENANCE_TOOLS:
        store.require_agent_access(user_id, "maintenance")


def call_tool(store: CortexStore, user_id: str, name: str, args: dict[str, Any]) -> Any:
    _require_tool_access(store, user_id, name)
    if name == "remember_this":
        content = args.get("content", "")
        source = args.get("source", "ai-chat")
        extracted = extract_context(content, source)
        return store.agent_payload(user_id, store.save_capture(
            user_id=user_id,
            content=content,
            source=source,
            source_url=args.get("source_url"),
            title=args.get("title"),
            extracted=extracted,
        ))
    if name == "search_memory":
        return store.agent_payload(user_id, store.search(user_id, args.get("query", ""), int(args.get("top_k", 8))))
    if name == "get_recent_context":
        return store.agent_payload(user_id, store.recent(user_id, int(args.get("limit", 10))))
    if name == "get_memory_graph":
        return store.agent_payload(user_id, store.graph(user_id, int(args.get("limit", 150))))
    if name == "get_daily_review":
        return store.agent_payload(user_id, store.daily_review(user_id))
    if name == "get_product_loop":
        return store.product_loop(user_id)
    if name == "build_context_pack":
        query = args.get("query", "")
        value = store.context_pack(user_id, query, int(args.get("limit", 12)))
        store.record_context_reuse(user_id, surface="mcp", query=query, target=args.get("target", "mcp-agent"))
        return value
    if name == "get_decisions":
        return store.agent_payload(user_id, store.search(user_id, args.get("query", "decision"), int(args.get("top_k", 10)), kind="decision"))
    if name == "get_open_questions":
        return store.agent_payload(user_id, store.open_tasks(user_id, int(args.get("limit", 20))))
    if name == "list_memory_topics":
        return store.list_topics(user_id, int(args.get("limit", 30)))
    if name == "list_memory_entities":
        return store.agent_payload(user_id, store.list_entities(user_id, int(args.get("limit", 30))))
    if name == "get_about_person":
        return store.agent_payload(user_id, store.about_person(user_id, args.get("name", ""), int(args.get("limit", 12))))
    if name == "get_about_entity":
        return store.agent_payload(user_id, store.about_entity(user_id, args.get("name", ""), int(args.get("limit", 12))))
    if name == "get_memory_stats":
        return store.stats(user_id)
    if name == "get_memory_inbox":
        return store.agent_payload(user_id, store.inbox(user_id, int(args.get("limit", 10))))
    if name == "approve_memory_capture":
        return {"approved": store.approve_capture(user_id, args["capture_id"])}
    if name == "archive_memory_capture":
        return {"archived": store.archive_capture(user_id, args["capture_id"])}
    if name == "get_memory_diagnostics":
        return store.diagnostics(user_id)
    if name == "get_reliability_report":
        return store.reliability_report(user_id)
    if name == "get_support_bundle":
        return store.support_bundle(user_id)
    if name == "create_memory_backup":
        return store.create_backup(user_id)
    if name == "repair_memory_storage":
        return store.repair_storage(user_id)
    if name == "rebuild_memory_search":
        return store.rebuild_search_index(user_id)
    if name == "rebuild_index_from_vault":
        return store.rebuild_index_from_vault(user_id)
    if name == "export_memory":
        if args.get("format", "markdown") == "json":
            return store.export_json(user_id)
        return store.export_markdown(user_id)
    if name == "forget_memory":
        return {"deleted": store.delete_memory(user_id, args["id"])}
    if name == "get_trust_summary":
        return store.trust_summary(user_id)
    if name == "get_audit_log":
        return store.audit_log(user_id, int(args.get("limit", 30)))
    raise ValueError(f"Unknown Cortex tool: {name}")


def tool_result_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)
