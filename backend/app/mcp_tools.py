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
        "description": "Search Cortex memory across saved context and return results with retrieval diagnostics.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "default": 8},
                "kind": {"type": "string"},
                "layer": {"type": "string", "enum": ["semantic", "episodic", "style", "decision", "preference", "negative", "procedural"]},
                "sector": {"type": "string"},
            },
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
        "description": "Return the Cortex model-building loop state: signal, review, access, and return.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_personal_profile",
        "description": "Return a cited Cortex personal adaptation profile grouped by memory layer, coverage, sources, and limitations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 6},
                "include_pending": {"type": "boolean", "default": False},
                "format": {"type": "string", "default": "json", "enum": ["json", "markdown"]},
                "sector": {"type": "string"},
            },
        },
    },
    {
        "name": "get_agent_adaptation",
        "description": "Return cited operating instructions that adapt an AI assistant to the user's preferences, style, decisions, limits, and current memory coverage.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "default": ""},
                "target": {"type": "string", "default": "assistant"},
                "limit": {"type": "integer", "default": 8},
                "include_pending": {"type": "boolean", "default": False},
                "format": {"type": "string", "default": "json", "enum": ["json", "markdown"]},
                "sector": {"type": "string"},
            },
        },
    },
    {
        "name": "get_style_profile",
        "description": "Return focused writing style, preference, and negative guidance with citations for matching the user's communication.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "default": "writing style"},
                "limit": {"type": "integer", "default": 6},
                "format": {"type": "string", "default": "json", "enum": ["json", "markdown"]},
                "sector": {"type": "string"},
            },
        },
    },
    {
        "name": "get_project_context",
        "description": "Return cited Cortex memory for a project, workspace, person, or topic.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "query": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 8},
                "sector": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_procedure",
        "description": "Return cited procedural memory for how the user performs a workflow, setup, release, or recurring task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 6},
                "format": {"type": "string", "default": "json", "enum": ["json", "markdown"]},
                "sector": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_supported_import_sources",
        "description": "List source exports Cortex can import, including chat, email, notes, docs, work tools, and knowledge-base formats.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_source_connectors",
        "description": "List Cortex source connectors, account health, and sync state so connected tools can feed the user's memory layer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_accounts": {"type": "boolean", "default": True},
                "include_readiness": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "connect_source_account",
        "description": "Register or refresh a connected source account that Cortex should model, such as Gmail, Notion, Slack, Drive, GitHub, local notes, or an AI chat tool.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "account_label": {"type": "string"},
                "account_identifier": {"type": "string"},
                "connection_type": {"type": "string", "default": "mcp"},
                "status": {"type": "string", "default": "connected"},
                "auth_state": {"type": "string", "default": "healthy"},
                "policy": {"type": "object"},
                "metadata": {"type": "object"},
                "last_error": {"type": "string"},
                "account_id": {"type": "string"},
            },
            "required": ["source"],
        },
    },
    {
        "name": "sync_source_records",
        "description": "Send records from a connected source account into Cortex with durable citations and cursor state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_account_id": {"type": "string"},
                "records": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "title": {"type": "string"},
                            "external_id": {"type": "string"},
                            "source_url": {"type": "string"},
                            "captured_at": {"type": "string"},
                        },
                        "required": ["content"],
                    },
                },
                "cursor_name": {"type": "string", "default": "default"},
                "cursor_value": {"type": "string"},
                "high_water_mark": {"type": "string"},
                "state": {"type": "object"},
                "processing": {"type": "string", "default": "sync", "enum": ["sync", "async"]},
                "archive_missing": {"type": "boolean", "default": False},
                "complete_snapshot": {"type": "boolean", "default": False},
            },
            "required": ["source_account_id", "records"],
        },
    },
    {
        "name": "sync_connected_sources",
        "description": "Run due sync jobs for already connected Cortex sources using locally stored source configuration and credentials.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "sync_github",
        "description": "Fetch GitHub issues and pull requests with a read-only token, then sync them into Cortex with stable citations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": {"type": "string"},
                "repositories": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 25},
                "source_account_id": {"type": "string"},
                "account_label": {"type": "string"},
                "account_identifier": {"type": "string"},
                "since": {"type": "string"},
                "processing": {"type": "string", "default": "sync", "enum": ["sync", "async"]},
                "max_records": {"type": "integer", "default": 100},
                "include_comments": {"type": "boolean", "default": True},
                "max_comments_per_item": {"type": "integer", "default": 10, "minimum": 0, "maximum": 50},
                "cursor_name": {"type": "string", "default": "issues"},
                "api_base_url": {"type": "string"},
            },
            "required": ["token", "repositories"],
        },
    },
    {
        "name": "sync_slack",
        "description": "Fetch recent Slack channel messages with a read-only token, then sync them into Cortex with stable citations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": {"type": "string"},
                "channels": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20},
                "source_account_id": {"type": "string"},
                "account_label": {"type": "string"},
                "account_identifier": {"type": "string"},
                "since": {"type": "string"},
                "processing": {"type": "string", "default": "sync", "enum": ["sync", "async"]},
                "max_records": {"type": "integer", "default": 100},
                "cursor_name": {"type": "string", "default": "messages"},
                "workspace_url": {"type": "string"},
                "api_base_url": {"type": "string"},
            },
            "required": ["token", "channels"],
        },
    },
    {
        "name": "sync_readwise",
        "description": "Fetch Readwise highlights with a read-only token, then sync them into Cortex with stable citations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": {"type": "string"},
                "source_account_id": {"type": "string"},
                "account_label": {"type": "string"},
                "account_identifier": {"type": "string"},
                "since": {"type": "string"},
                "page_cursor": {"type": "string"},
                "processing": {"type": "string", "default": "sync", "enum": ["sync", "async"]},
                "max_records": {"type": "integer", "default": 100},
                "cursor_name": {"type": "string", "default": "highlights"},
                "api_base_url": {"type": "string"},
            },
            "required": ["token"],
        },
    },
    {
        "name": "sync_calendar",
        "description": "Sync Calendar events from a local ICS file or calendar feed URL without exposing local paths or private feed URLs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ics_path": {"type": "string"},
                "feed_url": {"type": "string"},
                "source_account_id": {"type": "string"},
                "account_label": {"type": "string"},
                "account_identifier": {"type": "string"},
                "since": {"type": "string"},
                "processing": {"type": "string", "default": "sync", "enum": ["sync", "async"]},
                "max_records": {"type": "integer", "default": 100},
                "cursor_name": {"type": "string", "default": "events"},
            },
        },
    },
    {
        "name": "sync_raindrop",
        "description": "Fetch Raindrop bookmarks and highlights with a read-only token, then sync them into Cortex with stable citations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": {"type": "string"},
                "collection_id": {"type": "string", "default": "0"},
                "source_account_id": {"type": "string"},
                "account_label": {"type": "string"},
                "account_identifier": {"type": "string"},
                "since": {"type": "string"},
                "page": {"type": "string"},
                "processing": {"type": "string", "default": "sync", "enum": ["sync", "async"]},
                "max_records": {"type": "integer", "default": 100},
                "cursor_name": {"type": "string", "default": "raindrops"},
                "include_highlights": {"type": "boolean", "default": True},
                "api_base_url": {"type": "string"},
            },
            "required": ["token"],
        },
    },
    {
        "name": "sync_zotero",
        "description": "Fetch Zotero items, notes, and annotations from the local desktop API by default, then sync them into Cortex with stable citations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": {"type": "string"},
                "library_type": {"type": "string", "default": "user", "enum": ["user", "group"]},
                "library_id": {"type": "string", "default": "0"},
                "source_account_id": {"type": "string"},
                "account_label": {"type": "string"},
                "account_identifier": {"type": "string"},
                "since": {"type": "string"},
                "cursor": {"type": "string"},
                "processing": {"type": "string", "default": "sync", "enum": ["sync", "async"]},
                "max_records": {"type": "integer", "default": 100},
                "cursor_name": {"type": "string", "default": "items"},
                "include_attachments": {"type": "boolean", "default": False},
                "api_base_url": {"type": "string", "default": "http://localhost:23119/api"},
            },
        },
    },
    {
        "name": "sync_linear",
        "description": "Fetch Linear issues with a personal API key, then sync them into Cortex with stable citations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": {"type": "string"},
                "source_account_id": {"type": "string"},
                "account_label": {"type": "string"},
                "account_identifier": {"type": "string"},
                "since": {"type": "string"},
                "cursor": {"type": "string"},
                "processing": {"type": "string", "default": "sync", "enum": ["sync", "async"]},
                "max_records": {"type": "integer", "default": 100},
                "cursor_name": {"type": "string", "default": "issues"},
                "api_url": {"type": "string"},
            },
            "required": ["token"],
        },
    },
    {
        "name": "sync_jira",
        "description": "Fetch Jira Cloud issues with an Atlassian account email and API token, then sync them into Cortex with stable /browse issue citations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
                "api_token": {"type": "string"},
                "site_url": {"type": "string"},
                "source_account_id": {"type": "string"},
                "account_label": {"type": "string"},
                "account_identifier": {"type": "string"},
                "jql": {"type": "string"},
                "since": {"type": "string"},
                "page_token": {"type": "string"},
                "processing": {"type": "string", "default": "sync", "enum": ["sync", "async"]},
                "max_records": {"type": "integer", "default": 100},
                "cursor_name": {"type": "string", "default": "issues"},
            },
            "required": ["email", "api_token", "site_url"],
        },
    },
    {
        "name": "sync_notion",
        "description": "Fetch Notion pages shared with an integration token, then sync them into Cortex with stable citations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": {"type": "string"},
                "source_account_id": {"type": "string"},
                "account_label": {"type": "string"},
                "account_identifier": {"type": "string"},
                "since": {"type": "string"},
                "cursor": {"type": "string"},
                "processing": {"type": "string", "default": "sync", "enum": ["sync", "async"]},
                "max_records": {"type": "integer", "default": 50},
                "cursor_name": {"type": "string", "default": "pages"},
                "include_content": {"type": "boolean", "default": True},
                "api_base_url": {"type": "string"},
                "notion_version": {"type": "string"},
            },
            "required": ["token"],
        },
    },
    {
        "name": "build_context_pack",
        "description": "Build a scoped Cortex memory view for ChatGPT, Claude, Cursor, or another assistant.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "default": ""}, "limit": {"type": "integer", "default": 12}, "target": {"type": "string", "default": "mcp-agent"}, "sector": {"type": "string"}}},
    },
    {
        "name": "get_decisions",
        "description": "Search decisions in Cortex memory.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "default": "decision"}, "sector": {"type": "string"}}},
    },
    {
        "name": "get_open_questions",
        "description": "Return open questions and action items.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_memory_topics",
        "description": "List active memory topics ranked by frequency and recency.",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 30}, "sector": {"type": "string"}}},
    },
    {
        "name": "list_memory_entities",
        "description": "List people, projects, organizations, and topics found in active memory.",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 30}, "sector": {"type": "string"}}},
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
        "name": "delete_memory_backups",
        "description": "Delete local Cortex backup archives so older deleted data is not retained there.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "restore_latest_memory_backup",
        "description": "Restore Cortex vault records from the latest local backup archive and rebuild the local search index.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "delete_all_user_data",
        "description": "Delete the current user's Cortex data from the local vault and index. Includes backup archives by default.",
        "inputSchema": {"type": "object", "properties": {"include_backups": {"type": "boolean", "default": True}}},
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
        "description": "Permanently delete one memory by id from the active index and local vault records.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    },
    {
        "name": "delete_memory_capture",
        "description": "Permanently delete one captured source and its derived memories/tasks from the active index and local vault records.",
        "inputSchema": {"type": "object", "properties": {"capture_id": {"type": "string"}}, "required": ["capture_id"]},
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
    "get_product_loop",
    "get_style_profile",
    "get_project_context",
    "get_procedure",
    "list_supported_import_sources",
    "list_source_connectors",
    "get_decisions",
    "get_open_questions",
    "list_memory_topics",
    "list_memory_entities",
    "get_about_person",
    "get_about_entity",
    "get_memory_stats",
    "get_support_bundle",
    "get_trust_summary",
    "get_audit_log",
}
REVIEW_TOOLS = {
    "get_daily_review",
    "get_memory_inbox",
}
WRITE_TOOLS = {
    "remember_this",
    "connect_source_account",
    "sync_source_records",
    "sync_github",
    "sync_slack",
    "sync_readwise",
    "sync_calendar",
    "sync_raindrop",
    "sync_zotero",
    "sync_linear",
    "sync_jira",
    "sync_notion",
    "approve_memory_capture",
    "archive_memory_capture",
    "forget_memory",
    "delete_memory_capture",
}
EXPORT_TOOLS = {"build_context_pack", "get_personal_profile", "get_agent_adaptation", "export_memory"}
MAINTENANCE_TOOLS = {
    "create_memory_backup",
    "sync_connected_sources",
    "get_memory_diagnostics",
    "get_reliability_report",
    "repair_memory_storage",
    "rebuild_memory_search",
    "rebuild_index_from_vault",
}
DESTRUCTIVE_TOOLS = {"forget_memory", "delete_memory_capture", "delete_memory_backups", "restore_latest_memory_backup", "delete_all_user_data"}


def tool_required_capabilities(name: str) -> list[str]:
    capabilities: list[str] = []
    if name in READ_TOOLS:
        capabilities.append("read")
    if name in REVIEW_TOOLS:
        capabilities.extend(["read", "write"])
    if name in WRITE_TOOLS:
        capabilities.append("write")
    if name in EXPORT_TOOLS:
        capabilities.extend(["read", "export"])
    if name in MAINTENANCE_TOOLS:
        capabilities.append("maintenance")
    if name in DESTRUCTIVE_TOOLS:
        capabilities.append("destructive")
    return list(dict.fromkeys(capabilities))


def tools_for_scopes(token_scopes: list[str] | None = None) -> list[dict[str, Any]]:
    if token_scopes is None:
        return TOOLS
    scope_set = set(token_scopes)
    return [
        tool
        for tool in TOOLS
        if all(capability in scope_set for capability in tool_required_capabilities(str(tool.get("name") or "")))
    ]


def _require_tool_access(store: CortexStore, user_id: str, name: str, token_scopes: list[str] | None = None) -> None:
    for capability in tool_required_capabilities(name):
        if token_scopes is not None and capability not in token_scopes:
            raise PermissionError(f"MCP token is not scoped for {capability} actions.")
        store.require_agent_access(user_id, capability)


def _bool_arg(args: dict[str, Any], key: str, default: bool = False) -> bool:
    value = args.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _markdown_memory_list(title: str, items: list[dict[str, Any]]) -> str:
    lines = [f"# {title}", ""]
    if not items:
        lines.append("- No matching approved memory found.")
        return "\n".join(lines)
    for item in items:
        citation = item.get("source_url") or item.get("source") or "unknown source"
        sector = f" Sector: {item['sector']}." if item.get("sector") else ""
        lines.append(f"- [{item['id']}] ({item.get('layer')}/{item.get('kind')}) Source: {citation}.{sector} {item.get('content')}")
    return "\n".join(lines)


def call_tool(store: CortexStore, user_id: str, name: str, args: dict[str, Any], token_scopes: list[str] | None = None) -> Any:
    _require_tool_access(store, user_id, name, token_scopes)
    if name == "remember_this":
        content = args.get("content", "")
        source = args.get("source", "ai-chat")
        extracted = extract_context(content, source, author_aliases=store.settings(user_id).get("identity_aliases"))
        return store.agent_payload(user_id, store.save_capture(
            user_id=user_id,
            content=content,
            source=source,
            source_url=args.get("source_url"),
            title=args.get("title"),
            extracted=extracted,
        ))
    if name == "search_memory":
        query = args.get("query", "")
        limit = int(args.get("top_k", 8))
        if hasattr(store, "public_search_payload"):
            return store.public_search_payload(user_id, query, limit, kind=args.get("kind"), layer=args.get("layer"), sector=args.get("sector"))
        return {
            "query": query,
            "sector": args.get("sector"),
            "results": store.agent_payload(user_id, store.search(user_id, query, limit, kind=args.get("kind"), layer=args.get("layer"), sector=args.get("sector"))),
            "retrieval": {"diagnostics_unavailable": True},
        }
    if name == "get_recent_context":
        return store.agent_payload(user_id, store.recent(user_id, int(args.get("limit", 10))))
    if name == "get_memory_graph":
        return store.agent_payload(user_id, store.graph(user_id, int(args.get("limit", 150))))
    if name == "get_daily_review":
        return store.agent_payload(user_id, store.daily_review(user_id))
    if name == "get_product_loop":
        return store.product_loop(user_id)
    if name == "get_personal_profile":
        profile = store.personal_profile(
            user_id,
            query=args.get("query", ""),
            limit=int(args.get("limit", 6)),
            include_pending=_bool_arg(args, "include_pending"),
            sector=args.get("sector"),
        )
        store.record_context_reuse(user_id, surface="mcp", query=args.get("query", ""), target="personal-profile")
        if args.get("format", "json") == "markdown":
            return profile["markdown"]
        return store.agent_payload(user_id, profile)
    if name == "get_agent_adaptation":
        adaptation = store.agent_adaptation(
            user_id,
            query=args.get("query", ""),
            target=args.get("target", "assistant"),
            limit=int(args.get("limit", 8)),
            include_pending=_bool_arg(args, "include_pending"),
            sector=args.get("sector"),
        )
        store.record_context_reuse(user_id, surface="mcp", query=args.get("query", ""), target=args.get("target", "agent-adaptation"))
        if args.get("format", "json") == "markdown":
            return adaptation["markdown"]
        return store.agent_payload(user_id, adaptation)
    if name == "get_style_profile":
        query = args.get("query", "writing style")
        limit = int(args.get("limit", 6))
        sector = args.get("sector")
        style = store.search(user_id, query, limit=limit, layer="style", sector=sector)
        preferences = store.search(user_id, query, limit=max(2, limit // 2), layer="preference", sector=sector)
        negatives = store.search(user_id, query, limit=max(2, limit // 2), layer="negative", sector=sector)
        result = {
            "query": query,
            "style": style,
            "preferences": preferences,
            "negative_constraints": negatives,
        }
        store.record_context_reuse(user_id, surface="mcp", query=query, target="style-profile")
        if args.get("format", "json") == "markdown":
            return "\n\n".join([
                _markdown_memory_list("Cortex Style Profile", style),
                _markdown_memory_list("Relevant Preferences", preferences),
                _markdown_memory_list("Constraints To Avoid", negatives),
            ])
        return store.agent_payload(user_id, result)
    if name == "get_project_context":
        name_arg = str(args.get("name") or "").strip()
        query = str(args.get("query") or "").strip()
        combined_query = " ".join(value for value in [name_arg, query] if value).strip()
        limit = int(args.get("limit", 8))
        sector = str(args.get("sector") or "").strip() or None
        if sector:
            memories = store.search(user_id, combined_query or query or name_arg, limit=limit, sector=sector, include_related=True)
        else:
            memories = []
            if name_arg:
                memories = store.search(user_id, combined_query or name_arg, limit=limit, sector=name_arg, include_related=True)
            if not memories:
                memories = store.search(user_id, combined_query or name_arg, limit=limit, include_related=True) if (combined_query or name_arg) else []
        if not sector and name_arg and len(memories) < limit:
            seen = {item["id"] for item in memories}
            memories.extend(item for item in store.about_entity(user_id, name_arg, limit=limit) if item["id"] not in seen)
            memories = memories[:limit]
        result = {"name": name_arg, "query": query, "memories": memories}
        store.record_context_reuse(user_id, surface="mcp", query=combined_query, target="project-context")
        return store.agent_payload(user_id, result)
    if name == "get_procedure":
        query = args.get("query", "")
        procedures = store.search(user_id, query, limit=int(args.get("limit", 6)), layer="procedural", sector=args.get("sector"))
        result = {"query": query, "procedures": procedures}
        store.record_context_reuse(user_id, surface="mcp", query=query, target="procedure")
        if args.get("format", "json") == "markdown":
            return _markdown_memory_list("Cortex Procedure", procedures)
        return store.agent_payload(user_id, result)
    if name == "list_supported_import_sources":
        return {"results": store.supported_import_sources()}
    if name == "list_source_connectors":
        result: dict[str, Any] = {"results": store.source_connector_catalog()}
        if _bool_arg(args, "include_accounts", True):
            result["accounts"] = store.list_source_accounts(user_id)
            result["sync_cursors"] = store.list_sync_cursors(user_id)
        if _bool_arg(args, "include_readiness", True):
            result["readiness"] = store.source_readiness_report(user_id)
        return store.agent_payload(user_id, result)
    if name == "connect_source_account":
        account = store.upsert_source_account(
            user_id,
            source=args.get("source", ""),
            account_label=args.get("account_label", ""),
            account_identifier=args.get("account_identifier"),
            connection_type=args.get("connection_type", "mcp"),
            status=args.get("status", "connected"),
            auth_state=args.get("auth_state", "healthy"),
            policy=args.get("policy") if isinstance(args.get("policy"), dict) else None,
            metadata=args.get("metadata") if isinstance(args.get("metadata"), dict) else None,
            last_error=args.get("last_error"),
            account_id=args.get("account_id"),
        )
        return store.agent_payload(user_id, {"account": account})
    if name == "sync_source_records":
        result = store.sync_source_account_records(
            user_id,
            args.get("source_account_id", ""),
            records=args.get("records") or [],
            cursor_name=args.get("cursor_name", "default"),
            cursor_value=args.get("cursor_value"),
            high_water_mark=args.get("high_water_mark"),
            state=args.get("state") if isinstance(args.get("state"), dict) else None,
            processing=args.get("processing", "sync"),
            archive_missing=_bool_arg(args, "archive_missing"),
            complete_snapshot=_bool_arg(args, "complete_snapshot"),
        )
        return store.agent_payload(user_id, result)
    if name == "sync_connected_sources":
        try:
            limit = int(args.get("limit", 10))
        except (TypeError, ValueError):
            limit = 10
        result = store.run_due_source_sync_jobs(
            user_id,
            limit=max(1, min(limit, 100)),
            worker_id="mcp-source-sync",
        )
        return store.agent_payload(user_id, result)
    if name == "sync_github":
        result = store.sync_github_account(
            user_id,
            token=args.get("token", ""),
            repositories=args.get("repositories") or [],
            source_account_id=args.get("source_account_id"),
            account_label=args.get("account_label"),
            account_identifier=args.get("account_identifier"),
            since=args.get("since"),
            processing=args.get("processing", "sync"),
            max_records=int(args.get("max_records", 100)),
            include_comments=_bool_arg(args, "include_comments", default=True),
            max_comments_per_item=int(args.get("max_comments_per_item", 10)),
            cursor_name=args.get("cursor_name", "issues"),
            api_base_url=args.get("api_base_url"),
        )
        return store.agent_payload(user_id, result)
    if name == "sync_slack":
        result = store.sync_slack_account(
            user_id,
            token=args.get("token", ""),
            channels=args.get("channels") or [],
            source_account_id=args.get("source_account_id"),
            account_label=args.get("account_label"),
            account_identifier=args.get("account_identifier"),
            since=args.get("since"),
            processing=args.get("processing", "sync"),
            max_records=int(args.get("max_records", 100)),
            cursor_name=args.get("cursor_name", "messages"),
            workspace_url=args.get("workspace_url"),
            api_base_url=args.get("api_base_url"),
        )
        return store.agent_payload(user_id, result)
    if name == "sync_readwise":
        result = store.sync_readwise_account(
            user_id,
            token=args.get("token", ""),
            source_account_id=args.get("source_account_id"),
            account_label=args.get("account_label"),
            account_identifier=args.get("account_identifier"),
            since=args.get("since"),
            page_cursor=args.get("page_cursor"),
            processing=args.get("processing", "sync"),
            max_records=int(args.get("max_records", 100)),
            cursor_name=args.get("cursor_name", "highlights"),
            api_base_url=args.get("api_base_url"),
        )
        return store.agent_payload(user_id, result)
    if name == "sync_calendar":
        result = store.sync_calendar_account(
            user_id,
            ics_path=args.get("ics_path"),
            feed_url=args.get("feed_url"),
            source_account_id=args.get("source_account_id"),
            account_label=args.get("account_label"),
            account_identifier=args.get("account_identifier"),
            since=args.get("since"),
            processing=args.get("processing", "sync"),
            max_records=int(args.get("max_records", 100)),
            cursor_name=args.get("cursor_name", "events"),
        )
        return store.agent_payload(user_id, result)
    if name == "sync_raindrop":
        result = store.sync_raindrop_account(
            user_id,
            token=args.get("token", ""),
            collection_id=args.get("collection_id", "0"),
            source_account_id=args.get("source_account_id"),
            account_label=args.get("account_label"),
            account_identifier=args.get("account_identifier"),
            since=args.get("since"),
            page=args.get("page"),
            processing=args.get("processing", "sync"),
            max_records=int(args.get("max_records", 100)),
            cursor_name=args.get("cursor_name", "raindrops"),
            include_highlights=_bool_arg(args, "include_highlights", True),
            api_base_url=args.get("api_base_url"),
        )
        return store.agent_payload(user_id, result)
    if name == "sync_zotero":
        result = store.sync_zotero_account(
            user_id,
            token=args.get("token"),
            library_type=args.get("library_type", "user"),
            library_id=args.get("library_id", "0"),
            source_account_id=args.get("source_account_id"),
            account_label=args.get("account_label"),
            account_identifier=args.get("account_identifier"),
            since=args.get("since"),
            cursor=args.get("cursor"),
            processing=args.get("processing", "sync"),
            max_records=int(args.get("max_records", 100)),
            cursor_name=args.get("cursor_name", "items"),
            include_attachments=_bool_arg(args, "include_attachments", False),
            api_base_url=args.get("api_base_url"),
        )
        return store.agent_payload(user_id, result)
    if name == "sync_linear":
        result = store.sync_linear_account(
            user_id,
            token=args.get("token", ""),
            source_account_id=args.get("source_account_id"),
            account_label=args.get("account_label"),
            account_identifier=args.get("account_identifier"),
            since=args.get("since"),
            cursor=args.get("cursor"),
            processing=args.get("processing", "sync"),
            max_records=int(args.get("max_records", 100)),
            cursor_name=args.get("cursor_name", "issues"),
            api_url=args.get("api_url"),
        )
        return store.agent_payload(user_id, result)
    if name == "sync_jira":
        result = store.sync_jira_account(
            user_id,
            email=args.get("email", ""),
            api_token=args.get("api_token", ""),
            site_url=args.get("site_url", ""),
            source_account_id=args.get("source_account_id"),
            account_label=args.get("account_label"),
            account_identifier=args.get("account_identifier"),
            jql=args.get("jql"),
            since=args.get("since"),
            page_token=args.get("page_token"),
            processing=args.get("processing", "sync"),
            max_records=int(args.get("max_records", 100)),
            cursor_name=args.get("cursor_name", "issues"),
        )
        return store.agent_payload(user_id, result)
    if name == "sync_notion":
        result = store.sync_notion_account(
            user_id,
            token=args.get("token", ""),
            source_account_id=args.get("source_account_id"),
            account_label=args.get("account_label"),
            account_identifier=args.get("account_identifier"),
            since=args.get("since"),
            cursor=args.get("cursor"),
            processing=args.get("processing", "sync"),
            max_records=int(args.get("max_records", 50)),
            cursor_name=args.get("cursor_name", "pages"),
            include_content=_bool_arg(args, "include_content", True),
            api_base_url=args.get("api_base_url"),
            notion_version=args.get("notion_version"),
        )
        return store.agent_payload(user_id, result)
    if name == "build_context_pack":
        query = args.get("query", "")
        value = store.context_pack(user_id, query, int(args.get("limit", 12)), sector=args.get("sector"))
        store.record_context_reuse(user_id, surface="mcp", query=query, target=args.get("target", "mcp-agent"))
        return value
    if name == "get_decisions":
        return store.agent_payload(user_id, store.search(user_id, args.get("query", "decision"), int(args.get("top_k", 10)), kind="decision", sector=args.get("sector")))
    if name == "get_open_questions":
        return store.agent_payload(user_id, store.open_tasks(user_id, int(args.get("limit", 20))))
    if name == "list_memory_topics":
        return store.list_topics(user_id, int(args.get("limit", 30)), sector=args.get("sector"))
    if name == "list_memory_entities":
        return store.agent_payload(user_id, store.list_entities(user_id, int(args.get("limit", 30)), sector=args.get("sector")))
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
    if name == "delete_memory_backups":
        return store.delete_backups(user_id)
    if name == "restore_latest_memory_backup":
        return store.restore_latest_backup(user_id)
    if name == "delete_all_user_data":
        return store.delete_user_data(user_id, include_backups=bool(args.get("include_backups", True)))
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
    if name == "delete_memory_capture":
        return {"deleted": store.delete_capture(user_id, args["capture_id"])}
    if name == "get_trust_summary":
        return store.trust_summary(user_id)
    if name == "get_audit_log":
        return store.audit_log(user_id, int(args.get("limit", 30)))
    raise ValueError(f"Unknown Cortex tool: {name}")


def tool_result_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def tool_call_result(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "content": [
            {
                "type": "text",
                "text": tool_result_text(value),
            }
        ]
    }
    if isinstance(value, (dict, list)):
        result["structuredContent"] = value
    return result
