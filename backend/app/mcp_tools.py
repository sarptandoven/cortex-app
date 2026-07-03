from __future__ import annotations

import json
import re
from typing import Any

from .extractor import extract_context
from .storage import CortexStore


MCP_QUERY_MAX_CHARS = 500
MCP_NAME_MAX_CHARS = 160
MCP_READ_LIMIT_MAX = 50
MCP_GRAPH_LIMIT_MAX = 300
MCP_SYNC_RECORD_MAX = 500
MCP_SYNC_COMMENT_MAX = 50

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
                "source": {"type": "string"},
                "source_account_id": {"type": "string"},
                "as_of": {"type": "string"},
                "repository": {"type": "string"},
                "channel": {"type": "string"},
                "record_scope": {"type": "string"},
                "state": {"type": "string"},
                "project": {"type": "string"},
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
        "name": "get_memory_quality_report",
        "description": "Return Cortex memory quality coverage for citations, dates, review state, memory layers, and source health.",
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
        "name": "get_person_map",
        "description": "Return the full cited picture of the user in one call: the model-of-you profile (how they work, preferences, dislikes, decisions, focus areas, key people/projects) PLUS the knowledge-graph map — the hubs they orbit, the communities/areas of their world, and the bridges connecting them. Load this to understand the whole person before acting.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_pending": {"type": "boolean", "default": False},
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
        "name": "prepare_action_brief",
        "description": "Return a cited working brief for an AI agent before it performs a task: ranked action plan, open actions, context, procedures, decisions, preferences, constraints, risk flags, and coverage.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "limit": {"type": "integer", "default": 8},
                "sector": {"type": "string"},
                "as_of": {"type": "string"},
                "format": {"type": "string", "default": "json", "enum": ["json", "markdown"]},
            },
            "required": ["task"],
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
                "draft": {"type": "string", "description": "Optional draft to check against cited Cortex style memory."},
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
                "resume_disconnected": {"type": "boolean", "default": False},
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
                "complete_snapshot": {"type": "boolean", "default": False},
                "api_base_url": {"type": "string"},
            },
            "required": ["token", "repositories"],
        },
    },
    {
        "name": "sync_gmail",
        "description": "Fetch Gmail messages with a read-only OAuth access token, then sync them into Cortex with stable message citations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "access_token": {"type": "string"},
                "source_account_id": {"type": "string"},
                "account_label": {"type": "string"},
                "account_identifier": {"type": "string"},
                "query": {"type": "string"},
                "label_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                "since": {"type": "string"},
                "page_token": {"type": "string"},
                "processing": {"type": "string", "default": "sync", "enum": ["sync", "async"]},
                "max_records": {"type": "integer", "default": 50},
                "cursor_name": {"type": "string", "default": "messages"},
                "include_body": {"type": "boolean", "default": True},
                "complete_snapshot": {"type": "boolean", "default": False},
                "api_base_url": {"type": "string"},
            },
            "required": ["access_token"],
        },
    },
    {
        "name": "sync_google_drive",
        "description": "Fetch Google Drive docs and text files with a read-only OAuth access token, then sync them into Cortex with stable Drive citations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "access_token": {"type": "string"},
                "source_account_id": {"type": "string"},
                "account_label": {"type": "string"},
                "account_identifier": {"type": "string"},
                "query": {"type": "string"},
                "mime_types": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                "since": {"type": "string"},
                "page_token": {"type": "string"},
                "processing": {"type": "string", "default": "sync", "enum": ["sync", "async"]},
                "max_records": {"type": "integer", "default": 50},
                "cursor_name": {"type": "string", "default": "files"},
                "include_content": {"type": "boolean", "default": True},
                "complete_snapshot": {"type": "boolean", "default": False},
                "api_base_url": {"type": "string"},
            },
            "required": ["access_token"],
        },
    },
    {
        "name": "sync_outlook",
        "description": "Fetch Outlook messages with a read-only Microsoft Graph access token, then sync them into Cortex with stable message citations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "access_token": {"type": "string"},
                "source_account_id": {"type": "string"},
                "account_label": {"type": "string"},
                "account_identifier": {"type": "string"},
                "query": {"type": "string"},
                "since": {"type": "string"},
                "page_token": {"type": "string"},
                "processing": {"type": "string", "default": "sync", "enum": ["sync", "async"]},
                "max_records": {"type": "integer", "default": 50},
                "cursor_name": {"type": "string", "default": "messages"},
                "include_body": {"type": "boolean", "default": True},
                "complete_snapshot": {"type": "boolean", "default": False},
                "api_base_url": {"type": "string"},
            },
            "required": ["access_token"],
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
                "complete_snapshot": {"type": "boolean", "default": False},
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
                "complete_snapshot": {"type": "boolean", "default": False},
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
                "complete_snapshot": {"type": "boolean", "default": False},
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
                "complete_snapshot": {"type": "boolean", "default": False},
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
                "complete_snapshot": {"type": "boolean", "default": False},
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
                "complete_snapshot": {"type": "boolean", "default": False},
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
                "complete_snapshot": {"type": "boolean", "default": False},
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
                "complete_snapshot": {"type": "boolean", "default": False},
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
        "name": "get_decision_history",
        "description": "Return a cited decision ledger with current decisions, superseded decisions, timeline order, sectors, and topics.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 12},
                "sector": {"type": "string"},
                "include_superseded": {"type": "boolean", "default": True},
                "as_of": {"type": "string"},
            },
        },
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
        "name": "get_relationship_context",
        "description": "Return a cited relationship briefing for a person: open commitments, decisions, recent context, and topics.",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "limit": {"type": "integer", "default": 8}}, "required": ["name"]},
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
    "get_memory_quality_report",
    "get_style_profile",
    "get_project_context",
    "get_procedure",
    "prepare_action_brief",
    "list_supported_import_sources",
    "list_source_connectors",
    "get_decisions",
    "get_decision_history",
    "get_open_questions",
    "list_memory_topics",
    "list_memory_entities",
    "get_about_person",
    "get_relationship_context",
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
    "sync_gmail",
    "sync_google_drive",
    "sync_outlook",
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
EXPORT_TOOLS = {"build_context_pack", "get_personal_profile", "get_person_map", "get_agent_adaptation", "export_memory"}
MAINTENANCE_TOOLS = {
    "create_memory_backup",
    "sync_connected_sources",
    "get_memory_diagnostics",
    "get_reliability_report",
    "repair_memory_storage",
    "rebuild_memory_search",
    "rebuild_index_from_vault",
}
SCOPED_MCP_MAINTENANCE_REVIEW_TOOLS = {"approve_memory_capture", "archive_memory_capture"}
DESTRUCTIVE_TOOLS = {"forget_memory", "delete_memory_capture", "delete_memory_backups", "restore_latest_memory_backup", "delete_all_user_data"}
DIRECT_CONNECTOR_SYNC_TOOLS = {
    "sync_github",
    "sync_gmail",
    "sync_google_drive",
    "sync_outlook",
    "sync_slack",
    "sync_readwise",
    "sync_calendar",
    "sync_raindrop",
    "sync_zotero",
    "sync_linear",
    "sync_jira",
    "sync_notion",
}


def tool_required_capabilities(name: str, *, scoped: bool = False) -> list[str]:
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
    if scoped and name in SCOPED_MCP_MAINTENANCE_REVIEW_TOOLS:
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
        if all(capability in scope_set for capability in tool_required_capabilities(str(tool.get("name") or ""), scoped=True))
    ]


def _require_tool_access(store: CortexStore, user_id: str, name: str, token_scopes: list[str] | None = None) -> None:
    for capability in tool_required_capabilities(name, scoped=token_scopes is not None):
        if token_scopes is not None and capability not in token_scopes:
            raise PermissionError(f"MCP token is not scoped for {capability} actions.")
        store.require_agent_access(user_id, capability)


def _connector_detail_capability(token_scopes: list[str] | None) -> str | None:
    if token_scopes is None:
        return "read"
    scope_set = set(token_scopes)
    if "maintenance" in scope_set:
        return "maintenance"
    if "export" in scope_set:
        return "export"
    return None


def _bool_arg(args: dict[str, Any], key: str, default: bool = False) -> bool:
    value = args.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _bounded_int_arg(args: dict[str, Any], key: str, default: int, *, minimum: int = 1, maximum: int = MCP_READ_LIMIT_MAX) -> int:
    try:
        value = int(args.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _text_arg(args: dict[str, Any], key: str, default: str = "", *, max_chars: int = MCP_QUERY_MAX_CHARS) -> str:
    value = str(args.get(key, default) or "").strip()
    if len(value) > max_chars:
        raise ValueError(f"MCP argument '{key}' exceeds {max_chars} characters.")
    return value


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


def _style_signal(item: dict[str, Any], prefix: str) -> dict[str, Any]:
    return {
        "memory_id": item.get("id"),
        "instruction": f"{prefix}: {item.get('content') or item.get('summary') or ''}",
        "source": item.get("source"),
        "source_url": item.get("source_url"),
        "sector": item.get("sector") or "",
        "captured_at": item.get("captured_at"),
    }


def _style_example_citation(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "memory_id": item.get("id"),
        "source": item.get("source"),
        "source_url": item.get("source_url"),
        "sector": item.get("sector") or "",
    }


def _style_rule(item: dict[str, Any], rule_type: str, instruction: str) -> dict[str, Any]:
    return {
        "memory_id": item.get("id"),
        "rule_type": rule_type,
        "instruction": instruction,
        "source": item.get("source"),
        "source_url": item.get("source_url"),
        "sector": item.get("sector") or "",
        "captured_at": item.get("captured_at"),
    }


def _style_rules_applied(style: list[dict[str, Any]], preferences: list[dict[str, Any]], negatives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    if preferences:
        rules.append(_style_rule(preferences[0], "preference", "Lead with the recommended path or practical default before caveats."))
    if style:
        rules.append(_style_rule(style[0], "style", "Use the cited writing style signal for sentence shape, pacing, and emphasis."))
    if negatives:
        rules.append(_style_rule(negatives[0], "negative", "Remove rejected language or structure before polishing the draft."))
    return rules


def _style_rewrite_examples(query: str, style: list[dict[str, Any]], preferences: list[dict[str, Any]], negatives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    topic = str(query or "this response").strip() or "this response"
    examples: list[dict[str, Any]] = []
    style_item = style[0] if style else None
    preference_item = preferences[0] if preferences else None
    negative_item = negatives[0] if negatives else None

    if style_item or preference_item:
        citations = [_style_example_citation(item) for item in (preference_item, style_item) if item]
        examples.append(
            {
                "name": "recommended_path_first",
                "before": f"Here are some thoughts about {topic}. There are many exciting possibilities, and we can explore all of them.",
                "after": f"Recommended path: {topic}.\n\nTradeoff: name the main constraint before expanding scope.\n\nNext: use the cited evidence and keep the answer concise.",
                "why": "Shows the recommended path first, then keeps caveats and next steps direct.",
                "citations": citations,
                "rules_applied": _style_rules_applied([item for item in [style_item] if item], [item for item in [preference_item] if item], []),
                "quality_checks": [
                    "Opens with a clear recommendation.",
                    "Separates the tradeoff from the next action.",
                    "Keeps the cited style memory attached to the rewrite decision.",
                ],
            }
        )

    if negative_item:
        citations = [_style_example_citation(item) for item in (negative_item, style_item) if item]
        examples.append(
            {
                "name": "remove_rejected_pattern",
                "before": f"This is a game-changing update for {topic}, and it is incredibly powerful.",
                "after": f"{topic}: state what changed, why it matters, and what action should happen next. Keep the language plain.",
                "why": "Removes a known rejected pattern before applying style polish.",
                "citations": citations,
                "rules_applied": _style_rules_applied([item for item in [style_item] if item], [], [negative_item]),
                "quality_checks": [
                    "Removes hype before improving flow.",
                    "Keeps the rewritten sentence inspectable and plain.",
                    "Does not invent extra user preferences beyond the cited negative memory.",
                ],
            }
        )

    return examples


def _style_contract(query: str, style: list[dict[str, Any]], preferences: list[dict[str, Any]], negatives: list[dict[str, Any]]) -> dict[str, Any]:
    rules_applied = _style_rules_applied(style, preferences, negatives)
    response_shape = [
        "Start with the user's newest instruction.",
        "State the recommended path or practical answer first when preference memory supports it.",
        "Name the main tradeoff or constraint before broad exploration.",
        "End with the next action only when it helps the user move.",
    ]
    avoid = [
        "Do not imitate style without cited style or preference memory.",
        "Do not use rejected language patterns from negative memory.",
        "Do not use unreviewed or cross-account memory as style evidence.",
    ]
    if not preferences:
        response_shape[1] = "Use a direct answer first; ask before assuming stronger ordering preferences."
    if not negatives:
        avoid.append("No focused negative memory matched; avoid generic hype and keep wording plain.")
    return {
        "topic": query or "writing style",
        "confidence": "strong" if len(rules_applied) >= 3 else "usable" if rules_applied else "limited",
        "response_shape": response_shape,
        "must_do": [rule for rule in rules_applied if rule["rule_type"] in {"style", "preference"}],
        "must_avoid": [rule for rule in rules_applied if rule["rule_type"] == "negative"],
        "quality_gate": [
            "Does the draft follow the cited response shape?",
            "Did every material style choice come from a cited memory ID?",
            "Did the draft remove known rejected patterns?",
            "Is the final answer still governed by the user's newest message?",
        ],
        "avoid": avoid,
    }


def _style_draft_review(draft: str, query: str, style: list[dict[str, Any]], preferences: list[dict[str, Any]], negatives: list[dict[str, Any]]) -> dict[str, Any]:
    draft = str(draft or "").strip()
    if not draft:
        return {"status": "not_provided", "findings": [], "recommended_opening": None}
    lower = draft.casefold()
    findings: list[dict[str, Any]] = []
    preference_item = preferences[0] if preferences else None
    negative_item = negatives[0] if negatives else None
    style_item = style[0] if style else None

    if preference_item and not re.match(r"^\s*(recommended path|recommendation|answer|short answer|bottom line)\b", lower):
        findings.append(
            {
                "severity": "revise",
                "message": "Draft does not lead with a recommendation or direct answer.",
                "memory_id": preference_item.get("id"),
                "source_url": preference_item.get("source_url"),
            }
        )
    hype_terms = ["game-changing", "incredibly", "revolutionary", "powerful", "amazing", "transformative"]
    matched_hype = [term for term in hype_terms if term in lower]
    if negative_item and matched_hype:
        findings.append(
            {
                "severity": "revise",
                "message": "Draft uses language that looks like a rejected hype pattern: " + ", ".join(matched_hype[:4]) + ".",
                "memory_id": negative_item.get("id"),
                "source_url": negative_item.get("source_url"),
            }
        )
    sentences = [part.strip() for part in re.split(r"[.!?]+", draft) if part.strip()]
    if style_item and sentences:
        average_words = sum(len(sentence.split()) for sentence in sentences) / max(1, len(sentences))
        if average_words > 28:
            findings.append(
                {
                    "severity": "tighten",
                    "message": "Average sentence length is high for the cited concise/direct style signal.",
                    "memory_id": style_item.get("id"),
                    "source_url": style_item.get("source_url"),
                }
            )
    return {
        "status": "aligned" if not findings else "needs_revision",
        "findings": findings,
        "recommended_opening": f"Recommended path: {query or 'answer the user directly'}." if preference_item else None,
    }


def _style_profile_payload(query: str, style: list[dict[str, Any]], preferences: list[dict[str, Any]], negatives: list[dict[str, Any]], draft: str = "") -> dict[str, Any]:
    style_directives = [_style_signal(item, "Match this writing style signal") for item in style]
    preference_directives = [_style_signal(item, "Use this communication preference as a default") for item in preferences]
    negative_directives = [_style_signal(item, "Avoid this rejected or disliked pattern") for item in negatives]
    all_items = [*style, *preferences, *negatives]
    source_mix: dict[str, int] = {}
    uncited: list[str] = []
    for item in all_items:
        source = str(item.get("source") or "unknown")
        source_mix[source] = source_mix.get(source, 0) + 1
        if not item.get("source_url"):
            uncited.append(str(item.get("id") or ""))
    rewrite_checklist = [
        "Start from the newest user instruction, then apply the cited style signals.",
        "Use preference memories for ordering, defaults, and emphasis.",
        "Check negative constraints before finalizing the draft.",
        "Keep memory IDs and source URLs attached when the style guidance materially affects output.",
    ]
    if not style_directives:
        rewrite_checklist.append("No focused style memory matched; use neutral, concise wording and ask before imitating voice.")
    return {
        "query": query,
        "status": "strong" if len(all_items) >= 3 and not uncited else "usable" if all_items else "limited",
        "summary": {
            "style_signals": len(style),
            "preferences": len(preferences),
            "negative_constraints": len(negatives),
            "source_mix": [{"source": source, "count": count} for source, count in sorted(source_mix.items())],
            "uncited_memory_ids": [item for item in uncited if item],
        },
        "guidance": {
            "style_directives": style_directives,
            "preference_directives": preference_directives,
            "negative_directives": negative_directives,
        },
        "rewrite_examples": _style_rewrite_examples(query, style, preferences, negatives),
        "style_contract": _style_contract(query, style, preferences, negatives),
        "draft_review": _style_draft_review(draft, query, style, preferences, negatives),
        "rewrite_checklist": rewrite_checklist,
        "style": style,
        "preferences": preferences,
        "negative_constraints": negatives,
    }


def _style_profile_markdown(profile: dict[str, Any]) -> str:
    lines = [
        "# Cortex Style Profile",
        "",
        f"Query: {profile.get('query') or 'writing style'}",
        f"Status: {profile.get('status') or 'limited'}",
        "",
        "## Rewrite Checklist",
        "",
    ]
    for item in profile.get("rewrite_checklist") or []:
        lines.append(f"- {item}")
    contract = profile.get("style_contract") if isinstance(profile.get("style_contract"), dict) else {}
    lines.extend(["", "## Style Contract", ""])
    if contract:
        lines.append(f"Confidence: {contract.get('confidence') or 'limited'}")
        lines.extend(["", "Response shape:"])
        for step in contract.get("response_shape") or []:
            lines.append(f"- {step}")
        lines.extend(["", "Quality gate:"])
        for check in contract.get("quality_gate") or []:
            lines.append(f"- {check}")
    draft_review = profile.get("draft_review") if isinstance(profile.get("draft_review"), dict) else {}
    if draft_review and draft_review.get("status") != "not_provided":
        lines.extend(["", "## Draft Review", ""])
        lines.append(f"Status: {draft_review.get('status')}")
        for finding in draft_review.get("findings") or []:
            lines.append(f"- [{finding.get('severity')}] {finding.get('message')} Source: {finding.get('source_url') or finding.get('memory_id')}.")
        if draft_review.get("recommended_opening"):
            lines.append(f"- Recommended opening: {draft_review.get('recommended_opening')}")
    guidance = profile.get("guidance") if isinstance(profile.get("guidance"), dict) else {}
    for key, title in (
        ("style_directives", "Style Directives"),
        ("preference_directives", "Preference Directives"),
        ("negative_directives", "Negative Constraints"),
    ):
        lines.extend(["", f"## {title}", ""])
        directives = guidance.get(key) if isinstance(guidance.get(key), list) else []
        if not directives:
            lines.append("- No matching approved memory found.")
            continue
        for directive in directives:
            source = directive.get("source_url") or directive.get("source") or "unknown source"
            lines.append(f"- [{directive.get('memory_id')}] {directive.get('instruction')} Source: {source}.")
    examples = profile.get("rewrite_examples") if isinstance(profile.get("rewrite_examples"), list) else []
    lines.extend(["", "## Rewrite Examples", ""])
    if not examples:
        lines.append("- No rewrite examples available from the current cited style profile.")
    for example in examples:
        lines.append(f"### {example.get('name') or 'example'}")
        lines.append("")
        lines.append(f"- Before: {example.get('before') or ''}")
        lines.append(f"- After: {example.get('after') or ''}")
        lines.append(f"- Why: {example.get('why') or ''}")
        citations = example.get("citations") if isinstance(example.get("citations"), list) else []
        if citations:
            citation_text = ", ".join(
                str(citation.get("source_url") or citation.get("source") or citation.get("memory_id") or "unknown source")
                for citation in citations
                if isinstance(citation, dict)
            )
            lines.append(f"- Sources: {citation_text}")
    return "\n".join(lines)


def _context_citation(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "memory_id": item.get("id"),
        "kind": item.get("kind"),
        "layer": item.get("layer"),
        "source": item.get("source"),
        "source_url": item.get("source_url"),
        "sector": item.get("sector") or "",
    }


def _project_context_sections(memories: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    sections = {
        "current_decisions": [],
        "procedures": [],
        "preferences": [],
        "negative_constraints": [],
        "style_signals": [],
        "facts": [],
    }
    for item in memories:
        layer = str(item.get("layer") or "")
        kind = str(item.get("kind") or "")
        target: str | None = None
        if layer == "decision" or kind == "decision":
            target = "current_decisions"
        elif layer == "procedural" or kind == "procedure":
            target = "procedures"
        elif layer == "preference":
            target = "preferences"
        elif layer == "negative":
            target = "negative_constraints"
        elif layer == "style":
            target = "style_signals"
        elif layer == "semantic":
            target = "facts"
        if target:
            sections[target].append(
                {
                    "memory_id": item.get("id"),
                    "content": item.get("content") or item.get("summary") or "",
                    "source": item.get("source"),
                    "source_url": item.get("source_url"),
                    "captured_at": item.get("captured_at"),
                }
            )
    return {key: value[:4] for key, value in sections.items() if value}


def _project_agent_brief(name: str, query: str, memories: list[dict[str, Any]]) -> dict[str, Any]:
    sections = _project_context_sections(memories)
    next_actions = [
        "Use the cited project memories before using generic assumptions.",
        "Prefer current decisions and procedures over older semantic facts.",
    ]
    if sections.get("procedures"):
        next_actions.append("Follow the cited procedure before improvising execution steps.")
    if sections.get("negative_constraints"):
        next_actions.append("Check negative constraints before drafting or acting.")
    if not sections.get("current_decisions"):
        next_actions.append("Ask before making a decision if no cited decision memory applies.")
    return {
        "status": "strong" if len(sections) >= 3 else "usable" if memories else "limited",
        "project": name,
        "query": query,
        "evidence_count": len(memories),
        "sections": sections,
        "next_actions": next_actions,
        "citation_requirements": [
            "Keep memory IDs attached to project claims that affect the answer or action.",
            "Cite source_url when communicating a project decision, procedure, or constraint.",
            "Do not use missing project memory as permission to invent context.",
        ],
        "citations": [_context_citation(item) for item in memories[:8]],
    }


def _procedure_steps(text: str) -> list[str]:
    cleaned = re.sub(r"(?i)^\s*(?:procedure|runbook|checklist|workflow)\s*:\s*", "", str(text or "")).strip()
    if not cleaned:
        return []
    normalized = re.sub(r"\s+", " ", cleaned)
    normalized = re.sub(r"(?i)\b(?:then|before|after that|next)\b", "\n", normalized)
    normalized = re.sub(r"\s*(?:;|, and | and then )\s*", "\n", normalized)
    parts = []
    for part in re.split(r"\n|(?:^|\s)(?:\d+[\.)]|[-*])\s+", normalized):
        step = part.strip(" .;-")
        if len(step) >= 8:
            parts.append(step)
    return parts[:8]


def _procedure_payload(query: str, procedures: list[dict[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": query, "procedures": procedures}
    if not procedures:
        return payload
    checklist: list[dict[str, Any]] = []
    for procedure in procedures[:4]:
        steps = _procedure_steps(procedure.get("content") or procedure.get("summary") or "")
        if not steps:
            steps = [procedure.get("content") or procedure.get("summary") or ""]
        for step in steps[:5]:
            checklist.append(
                {
                    "step": step,
                    "memory_id": procedure.get("id"),
                    "source": procedure.get("source"),
                    "source_url": procedure.get("source_url"),
                }
            )
            if len(checklist) >= 12:
                break
        if len(checklist) >= 12:
            break
    payload["execution_checklist"] = checklist
    payload["procedure_contract"] = {
        "status": "strong" if checklist else "usable",
        "query": query,
        "procedure_count": len(procedures),
        "quality_gate": [
            "Follow cited steps in order when the task matches the procedure.",
            "Keep memory IDs attached to procedure-derived actions.",
            "Ask before taking irreversible actions when the procedure is missing a step.",
            "Verify completion against the cited source before reporting done.",
        ],
        "citations": [_context_citation(item) for item in procedures[:8]],
    }
    return payload


def _procedure_markdown(payload: dict[str, Any]) -> str:
    lines = ["# Cortex Procedure", ""]
    checklist = payload.get("execution_checklist") if isinstance(payload.get("execution_checklist"), list) else []
    if checklist:
        lines.extend(["## Execution Checklist", ""])
        for index, item in enumerate(checklist, start=1):
            source = item.get("source_url") or item.get("source") or item.get("memory_id") or "unknown source"
            lines.append(f"{index}. {item.get('step') or ''} Source: {source}.")
        lines.append("")
    lines.append("## Cited Procedures")
    lines.append("")
    procedures = payload.get("procedures") if isinstance(payload.get("procedures"), list) else []
    if not procedures:
        lines.append("- No matching approved memory found.")
    else:
        for item in procedures:
            source = item.get("source_url") or item.get("source") or "unknown source"
            lines.append(f"- [{item.get('id')}] {item.get('content') or item.get('summary') or ''} Source: {source}.")
    return "\n".join(lines)


def call_tool(store: CortexStore, user_id: str, name: str, args: dict[str, Any], token_scopes: list[str] | None = None) -> Any:
    _require_tool_access(store, user_id, name, token_scopes)
    if name in DIRECT_CONNECTOR_SYNC_TOOLS and _bool_arg(args, "complete_snapshot"):
        if token_scopes is not None and "maintenance" not in token_scopes:
            raise PermissionError("MCP token is not scoped for maintenance actions.")
        store.require_agent_access(user_id, "maintenance")
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
        query = _text_arg(args, "query")
        limit = _bounded_int_arg(args, "top_k", 8)
        metadata_filters = {
            "repository": args.get("repository"),
            "channel": args.get("channel"),
            "record_scope": args.get("record_scope"),
            "state": args.get("state"),
            "project": args.get("project"),
        }
        if hasattr(store, "public_search_payload"):
            return store.public_search_payload(
                user_id,
                query,
                limit,
                kind=args.get("kind"),
                layer=args.get("layer"),
                sector=args.get("sector"),
                source=args.get("source"),
                source_account_id=args.get("source_account_id"),
                as_of=args.get("as_of"),
                metadata_filters=metadata_filters,
            )
        return {
            "query": query,
            "sector": args.get("sector"),
            "filters": {},
            "results": store.agent_payload(
                user_id,
                store.search(
                    user_id,
                    query,
                    limit,
                    kind=args.get("kind"),
                    layer=args.get("layer"),
                    sector=args.get("sector"),
                    source=args.get("source"),
                    source_account_id=args.get("source_account_id"),
                    as_of=args.get("as_of"),
                    metadata_filters=metadata_filters,
                ),
            ),
            "retrieval": {"diagnostics_unavailable": True},
        }
    if name == "get_recent_context":
        return store.agent_payload(user_id, store.recent(user_id, _bounded_int_arg(args, "limit", 10)))
    if name == "get_memory_graph":
        return store.agent_payload(user_id, store.graph(user_id, _bounded_int_arg(args, "limit", 150, maximum=MCP_GRAPH_LIMIT_MAX)))
    if name == "get_daily_review":
        return store.agent_payload(user_id, store.daily_review(user_id))
    if name == "get_product_loop":
        return store.product_loop(user_id)
    if name == "get_memory_quality_report":
        return store.memory_quality_report(user_id)
    if name == "get_personal_profile":
        query = _text_arg(args, "query")
        profile = store.personal_profile(
            user_id,
            query=query,
            limit=_bounded_int_arg(args, "limit", 6),
            include_pending=_bool_arg(args, "include_pending"),
            sector=args.get("sector"),
        )
        store.record_context_reuse(user_id, surface="mcp", query=query, target="personal-profile")
        if args.get("format", "json") == "markdown":
            return profile["markdown"]
        return store.agent_payload(user_id, profile)
    if name == "get_person_map":
        result = store.person_map(user_id, include_pending=_bool_arg(args, "include_pending"), sector=args.get("sector"))
        store.record_context_reuse(user_id, surface="mcp", query="", target="person-map")
        return store.agent_payload(user_id, result)
    if name == "get_agent_adaptation":
        query = _text_arg(args, "query")
        target = _text_arg(args, "target", "assistant", max_chars=MCP_NAME_MAX_CHARS)
        adaptation = store.agent_adaptation(
            user_id,
            query=query,
            target=target,
            limit=_bounded_int_arg(args, "limit", 8),
            include_pending=_bool_arg(args, "include_pending"),
            sector=args.get("sector"),
        )
        store.record_context_reuse(user_id, surface="mcp", query=query, target=target)
        if args.get("format", "json") == "markdown":
            return adaptation["markdown"]
        return store.agent_payload(user_id, adaptation)
    if name == "prepare_action_brief":
        task = _text_arg(args, "task")
        brief = store.action_brief(
            user_id,
            task,
            limit=_bounded_int_arg(args, "limit", 8, maximum=20),
            sector=args.get("sector"),
            as_of=args.get("as_of"),
        )
        store.record_context_reuse(user_id, surface="mcp", query=task, target="action-brief")
        if args.get("format", "json") == "markdown":
            return brief["markdown"]
        return store.agent_payload(user_id, brief)
    if name == "get_style_profile":
        query = _text_arg(args, "query", "writing style")
        limit = _bounded_int_arg(args, "limit", 6)
        sector = args.get("sector")
        draft = _text_arg(args, "draft", "", max_chars=4000)
        style = store.search(user_id, query, limit=limit, layer="style", sector=sector)
        preferences = store.search(user_id, query, limit=max(2, limit // 2), layer="preference", sector=sector)
        negatives = store.search(user_id, query, limit=max(2, limit // 2), layer="negative", sector=sector)
        result = _style_profile_payload(query, style, preferences, negatives, draft=draft)
        store.record_context_reuse(user_id, surface="mcp", query=query, target="style-profile")
        if args.get("format", "json") == "markdown":
            return _style_profile_markdown(result)
        return store.agent_payload(user_id, result)
    if name == "get_project_context":
        name_arg = _text_arg(args, "name", max_chars=MCP_NAME_MAX_CHARS)
        query = _text_arg(args, "query")
        combined_query = " ".join(value for value in [name_arg, query] if value).strip()
        limit = _bounded_int_arg(args, "limit", 8)
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
        if memories:
            result["agent_brief"] = _project_agent_brief(name_arg, query, memories)
        store.record_context_reuse(user_id, surface="mcp", query=combined_query, target="project-context")
        return store.agent_payload(user_id, result)
    if name == "get_procedure":
        query = _text_arg(args, "query")
        procedures = store.search(user_id, query, limit=_bounded_int_arg(args, "limit", 6), layer="procedural", sector=args.get("sector"))
        result = _procedure_payload(query, procedures)
        store.record_context_reuse(user_id, surface="mcp", query=query, target="procedure")
        if args.get("format", "json") == "markdown":
            return _procedure_markdown(result)
        return store.agent_payload(user_id, result)
    if name == "list_supported_import_sources":
        return {"results": store.supported_import_sources()}
    if name == "list_source_connectors":
        result: dict[str, Any] = {"results": store.source_connector_catalog()}
        include_account_details = _bool_arg(args, "include_accounts", token_scopes is None)
        if include_account_details:
            detail_capability = _connector_detail_capability(token_scopes)
            if detail_capability:
                store.require_agent_access(user_id, detail_capability)
                result["accounts"] = store.list_source_accounts(user_id)
                result["sync_cursors"] = store.list_sync_cursors(user_id)
            else:
                result["details_omitted"] = {
                    "reason": "account and cursor details require export or maintenance scope for scoped MCP tokens",
                    "required_scopes": ["export", "maintenance"],
                }
        if _bool_arg(args, "include_readiness", True):
            result["readiness"] = store.source_readiness_report(user_id)
        return store.agent_payload(user_id, result)
    if name == "connect_source_account":
        resume_disconnected = _bool_arg(args, "resume_disconnected")
        policy = args.get("policy") if isinstance(args.get("policy"), dict) else None
        if policy:
            if token_scopes is not None and "maintenance" not in token_scopes:
                raise PermissionError("MCP token is not scoped for maintenance actions.")
            store.require_agent_access(user_id, "maintenance")
        if resume_disconnected:
            if token_scopes is not None and "maintenance" not in token_scopes:
                raise PermissionError("MCP token is not scoped for maintenance actions.")
            store.require_agent_access(user_id, "maintenance")
        account = store.upsert_source_account(
            user_id,
            source=args.get("source", ""),
            account_label=args.get("account_label", ""),
            account_identifier=args.get("account_identifier"),
            connection_type=args.get("connection_type", "mcp"),
            status=args.get("status", "connected"),
            auth_state=args.get("auth_state", "healthy"),
            policy=policy,
            metadata=args.get("metadata") if isinstance(args.get("metadata"), dict) else None,
            last_error=args.get("last_error"),
            account_id=args.get("account_id"),
            resume_disconnected=resume_disconnected,
        )
        return store.agent_payload(user_id, {"account": account})
    if name == "sync_source_records":
        archive_missing = _bool_arg(args, "archive_missing")
        if archive_missing:
            if token_scopes is not None and "maintenance" not in token_scopes:
                raise PermissionError("MCP token is not scoped for maintenance actions.")
            store.require_agent_access(user_id, "maintenance")
        result = store.sync_source_account_records(
            user_id,
            args.get("source_account_id", ""),
            records=args.get("records") or [],
            cursor_name=args.get("cursor_name", "default"),
            cursor_value=args.get("cursor_value"),
            high_water_mark=args.get("high_water_mark"),
            state=args.get("state") if isinstance(args.get("state"), dict) else None,
            processing=args.get("processing", "sync"),
            archive_missing=archive_missing,
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
            max_records=_bounded_int_arg(args, "max_records", 100, maximum=MCP_SYNC_RECORD_MAX),
            include_comments=_bool_arg(args, "include_comments", default=True),
            max_comments_per_item=_bounded_int_arg(args, "max_comments_per_item", 10, minimum=0, maximum=MCP_SYNC_COMMENT_MAX),
            cursor_name=args.get("cursor_name", "issues"),
            complete_snapshot=_bool_arg(args, "complete_snapshot"),
            api_base_url=args.get("api_base_url"),
        )
        return store.agent_payload(user_id, result)
    if name == "sync_gmail":
        result = store.sync_gmail_account(
            user_id,
            access_token=args.get("access_token", ""),
            source_account_id=args.get("source_account_id"),
            account_label=args.get("account_label"),
            account_identifier=args.get("account_identifier"),
            query=args.get("query"),
            label_ids=args.get("label_ids") or [],
            since=args.get("since"),
            page_token=args.get("page_token"),
            processing=args.get("processing", "sync"),
            max_records=_bounded_int_arg(args, "max_records", 50, maximum=MCP_SYNC_RECORD_MAX),
            cursor_name=args.get("cursor_name", "messages"),
            include_body=_bool_arg(args, "include_body", True),
            complete_snapshot=_bool_arg(args, "complete_snapshot"),
            api_base_url=args.get("api_base_url"),
        )
        return store.agent_payload(user_id, result)
    if name == "sync_google_drive":
        result = store.sync_google_drive_account(
            user_id,
            access_token=args.get("access_token", ""),
            source_account_id=args.get("source_account_id"),
            account_label=args.get("account_label"),
            account_identifier=args.get("account_identifier"),
            query=args.get("query"),
            mime_types=args.get("mime_types") or [],
            since=args.get("since"),
            page_token=args.get("page_token"),
            processing=args.get("processing", "sync"),
            max_records=_bounded_int_arg(args, "max_records", 50, maximum=MCP_SYNC_RECORD_MAX),
            cursor_name=args.get("cursor_name", "files"),
            include_content=_bool_arg(args, "include_content", True),
            complete_snapshot=_bool_arg(args, "complete_snapshot"),
            api_base_url=args.get("api_base_url"),
        )
        return store.agent_payload(user_id, result)
    if name == "sync_outlook":
        result = store.sync_outlook_account(
            user_id,
            access_token=args.get("access_token", ""),
            source_account_id=args.get("source_account_id"),
            account_label=args.get("account_label"),
            account_identifier=args.get("account_identifier"),
            query=args.get("query"),
            since=args.get("since"),
            page_token=args.get("page_token"),
            processing=args.get("processing", "sync"),
            max_records=_bounded_int_arg(args, "max_records", 50, maximum=MCP_SYNC_RECORD_MAX),
            cursor_name=args.get("cursor_name", "messages"),
            include_body=_bool_arg(args, "include_body", True),
            complete_snapshot=_bool_arg(args, "complete_snapshot"),
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
            max_records=_bounded_int_arg(args, "max_records", 100, maximum=MCP_SYNC_RECORD_MAX),
            cursor_name=args.get("cursor_name", "messages"),
            workspace_url=args.get("workspace_url"),
            complete_snapshot=_bool_arg(args, "complete_snapshot"),
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
            max_records=_bounded_int_arg(args, "max_records", 100, maximum=MCP_SYNC_RECORD_MAX),
            cursor_name=args.get("cursor_name", "highlights"),
            complete_snapshot=_bool_arg(args, "complete_snapshot"),
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
            max_records=_bounded_int_arg(args, "max_records", 100, maximum=MCP_SYNC_RECORD_MAX),
            cursor_name=args.get("cursor_name", "events"),
            complete_snapshot=_bool_arg(args, "complete_snapshot"),
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
            max_records=_bounded_int_arg(args, "max_records", 100, maximum=MCP_SYNC_RECORD_MAX),
            cursor_name=args.get("cursor_name", "raindrops"),
            include_highlights=_bool_arg(args, "include_highlights", True),
            complete_snapshot=_bool_arg(args, "complete_snapshot"),
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
            max_records=_bounded_int_arg(args, "max_records", 100, maximum=MCP_SYNC_RECORD_MAX),
            cursor_name=args.get("cursor_name", "items"),
            include_attachments=_bool_arg(args, "include_attachments", False),
            complete_snapshot=_bool_arg(args, "complete_snapshot"),
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
            max_records=_bounded_int_arg(args, "max_records", 100, maximum=MCP_SYNC_RECORD_MAX),
            cursor_name=args.get("cursor_name", "issues"),
            complete_snapshot=_bool_arg(args, "complete_snapshot"),
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
            max_records=_bounded_int_arg(args, "max_records", 100, maximum=MCP_SYNC_RECORD_MAX),
            cursor_name=args.get("cursor_name", "issues"),
            complete_snapshot=_bool_arg(args, "complete_snapshot"),
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
            max_records=_bounded_int_arg(args, "max_records", 50, maximum=MCP_SYNC_RECORD_MAX),
            cursor_name=args.get("cursor_name", "pages"),
            include_content=_bool_arg(args, "include_content", True),
            complete_snapshot=_bool_arg(args, "complete_snapshot"),
            api_base_url=args.get("api_base_url"),
            notion_version=args.get("notion_version"),
        )
        return store.agent_payload(user_id, result)
    if name == "build_context_pack":
        query = _text_arg(args, "query")
        value = store.context_pack(user_id, query, _bounded_int_arg(args, "limit", 12), sector=args.get("sector"))
        store.record_context_reuse(user_id, surface="mcp", query=query, target=_text_arg(args, "target", "mcp-agent", max_chars=MCP_NAME_MAX_CHARS))
        return value
    if name == "get_decisions":
        return store.agent_payload(user_id, store.search(user_id, _text_arg(args, "query", "decision"), _bounded_int_arg(args, "top_k", 10), kind="decision", sector=args.get("sector")))
    if name == "get_decision_history":
        query = _text_arg(args, "query", "")
        result = store.decision_history(
            user_id,
            query,
            limit=_bounded_int_arg(args, "limit", 12, maximum=30),
            sector=args.get("sector"),
            include_superseded=_bool_arg(args, "include_superseded", True),
            as_of=args.get("as_of"),
        )
        store.record_context_reuse(user_id, surface="mcp", query=query, target="decision-history")
        return store.agent_payload(user_id, result)
    if name == "get_open_questions":
        return store.agent_payload(user_id, store.open_tasks(user_id, _bounded_int_arg(args, "limit", 20)))
    if name == "list_memory_topics":
        return store.list_topics(user_id, _bounded_int_arg(args, "limit", 30), sector=args.get("sector"))
    if name == "list_memory_entities":
        return store.agent_payload(user_id, store.list_entities(user_id, _bounded_int_arg(args, "limit", 30), sector=args.get("sector")))
    if name == "get_about_person":
        return store.agent_payload(user_id, store.about_person(user_id, _text_arg(args, "name", max_chars=MCP_NAME_MAX_CHARS), _bounded_int_arg(args, "limit", 12)))
    if name == "get_relationship_context":
        person_name = _text_arg(args, "name", max_chars=MCP_NAME_MAX_CHARS)
        result = store.person_context(user_id, person_name, limit=_bounded_int_arg(args, "limit", 8, maximum=20))
        store.record_context_reuse(user_id, surface="mcp", query=person_name, target="relationship-context")
        return store.agent_payload(user_id, result)
    if name == "get_about_entity":
        return store.agent_payload(user_id, store.about_entity(user_id, _text_arg(args, "name", max_chars=MCP_NAME_MAX_CHARS), _bounded_int_arg(args, "limit", 12)))
    if name == "get_memory_stats":
        return store.stats(user_id)
    if name == "get_memory_inbox":
        return store.agent_payload(user_id, store.inbox(user_id, _bounded_int_arg(args, "limit", 10)))
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
        return store.audit_log(user_id, _bounded_int_arg(args, "limit", 30))
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
