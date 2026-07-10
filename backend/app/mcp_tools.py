from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote

from .config import load_settings
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
        "name": "sync_agent_sessions",
        "description": "Harvest the user's own messages from local coding-agent session logs (Claude Code, Codex, Cursor) into the review queue. User words only — agent replies are never ingested.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agents": {"type": "array", "items": {"type": "string", "enum": ["claude", "codex", "cursor"]}},
                "source_account_id": {"type": "string"},
                "account_label": {"type": "string"},
                "processing": {"type": "string", "default": "sync", "enum": ["sync", "async"]},
                "max_records": {"type": "integer", "default": 200},
                "per_session_limit": {"type": "integer", "default": 25},
                "cursor_name": {"type": "string", "default": "agent-sessions"},
                "review_required": {"type": "boolean", "default": True},
            },
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
        "name": "record_working_canvas_node",
        "description": "Offload a verbose tool result into a compact symbolic working-memory canvas node backed by content-addressed raw evidence and an append-only receipt.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "node_id": {"type": "string"},
                "label": {"type": "string"},
                "summary": {"type": "string"},
                "raw_text": {"type": "string"},
                "predecessor_node_id": {"type": "string"},
            },
            "required": ["session_id", "node_id", "raw_text"],
        },
    },
    {
        "name": "get_working_canvas",
        "description": "Return the compact Mermaid working-memory canvas for an agent session, with node receipts for verifiable drill-down. max_chars caps the rendered canvas (oldest nodes elide first; they remain drill-downable).",
        "inputSchema": {"type": "object", "properties": {"session_id": {"type": "string"}, "limit": {"type": "integer", "default": 80}, "max_chars": {"type": "integer", "default": 0}}, "required": ["session_id"]},
    },
    {
        "name": "get_working_canvas_node",
        "description": "Recover and verify the raw evidence behind one symbolic working-memory canvas node.",
        "inputSchema": {"type": "object", "properties": {"session_id": {"type": "string"}, "node_id": {"type": "string"}, "include_raw": {"type": "boolean", "default": True}}, "required": ["session_id", "node_id"]},
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
        "name": "get_belief_timeline",
        "description": "Return the revision history of a belief: for each memory matching the topic, the current version plus every superseded revision, with authorship, trust, citations, and an optional as_of slice where valid-time and known-time are the same point. Use get_belief_proof when those times differ. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Belief or topic to trace, e.g. 'preferred database'."},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 50},
                "as_of": {"type": "string", "description": "Optional ISO-8601 shorthand applied to both valid time and transaction time. Use get_belief_proof when those points differ."},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "get_belief_proof",
        "description": "Reconstruct what Cortex believed at valid-time X as known at transaction-time Y, with chain-sealed snapshot receipts, an anchorable hash-chain segment, and Merkle inclusion paths for normal-sized histories. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Belief or topic to prove."},
                "valid_at": {"type": "string", "description": "Optional ISO-8601 real-world time being asked about."},
                "known_at": {"type": "string", "description": "Optional ISO-8601 transaction-time cutoff for what Cortex had recorded."},
                "expected_head": {"type": "string", "description": "Optional externally pinned integrity-chain head for the known_at prefix."},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 50},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "verify_belief_proof",
        "description": "Purely verify receipt inclusion for a self-contained Proof-of-Belief envelope without reading or trusting the local store. Supply expected_head separately for anchored verification. Search completeness is reported separately and is not claimed by this proof. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "proof": {"type": "object"},
                "expected_head": {"type": "string", "description": "Optional trusted chain head supplied separately from the untrusted proof."},
            },
            "required": ["proof"],
        },
    },
    {
        "name": "get_tool_scorecard",
        "description": "Return a per-host scorecard of how connected agents use Cortex memory: memory-usage rate, retrieval coverage mix, conflict exposure, and answer-grading faithfulness. Read-only, computed from the local audit log.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 7, "minimum": 1, "maximum": 90},
                "token_id": {"type": "string", "description": "Optional token id to filter to one connected host."},
            },
        },
    },
    {
        "name": "get_source_reputation",
        "description": "Return per-source approve/reject reputation from the review ledger: how often you approve vs reject what each source proposes, with a promote/demote recommendation for trusting a source. Read-only; never changes a trust setting on its own.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 90, "minimum": 1, "maximum": 365},
            },
        },
    },
    {
        "name": "submit_answer_for_grading",
        "description": "Submit an answer produced with Cortex context for deterministic faithfulness grading: each factual claim is checked against memory and returned as consistent, contradicted, or unsupported, with citations. Results accrue to the host scorecard.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "answer_text": {"type": "string", "description": "The answer text to grade against memory."},
                "session_id": {"type": "string", "description": "Optional agent session id the answer belongs to."},
                "pack_sha": {"type": "string", "description": "Optional sha256 of the context pack the answer was produced from."},
            },
            "required": ["answer_text"],
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
        "name": "get_memory_integrity",
        "description": "Return a tamper-evident digest of the whole memory history: a hash-chain head over the append-only event log, plus the counts it attests. Pin the head now and recompute it later to prove nothing in the past was silently edited. Read-only, computed from the local event log.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "verify_memory_integrity",
        "description": "Recompute the memory's event hash-chain and compare it to a head you pinned earlier. Returns whether the history is byte-identical (matches) or has changed since. Read-only continuity proof; new events legitimately advance the head.",
        "inputSchema": {
            "type": "object",
            "properties": {"expected_head": {"type": "string", "description": "The chain_head from an earlier get_memory_integrity call."}},
            "required": ["expected_head"],
        },
    },
    {
        "name": "export_memory_bundle",
        "description": "Export the whole memory as one self-verifying, restorable bundle: the full export payload wrapped in an integrity manifest (chain head + payload sha256 + record counts). Hand it to another Cortex instance or keep it as a cold archive; it can be verified byte-for-byte before restore. Carries the full corpus out of Cortex custody.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "verify_memory_bundle",
        "description": "Verify a portable memory bundle WITHOUT trusting its source: recompute the payload hash from the embedded payload and check it against the manifest. Returns whether the bundle is intact and safe to restore. Pure check of the bundle you pass in.",
        "inputSchema": {
            "type": "object",
            "properties": {"bundle": {"type": "object", "description": "A bundle produced by export_memory_bundle."}},
            "required": ["bundle"],
        },
    },
    {
        "name": "write_obsidian_pages",
        "description": "Write/refresh the distilled, cited Cortex pages (Profile, People) inside the connected Obsidian vault's Cortex/ folder.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "vault_path": {"type": "string", "description": "Optional vault folder; defaults to the connected Obsidian source account's vault."},
                "people_limit": {"type": "integer", "default": 10, "minimum": 0, "maximum": 50},
            },
        },
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
    {
        "name": "get_context",
        "description": (
            "Build the working context pack for a task: a token-budgeted, cited selection of the "
            "user's constraints, decisions, facts, entities, procedures, identity, open loops, and "
            "recent memory. Call this FIRST before doing work for the user. Every item carries a "
            "memory_id + source; dropped items are counted, never silent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "What you are about to do for the user."},
                "surface": {"type": "string", "default": "agent", "description": "Which tool you are (cursor, claude, chatgpt, ...)."},
                "token_budget": {"type": "integer", "default": 2000, "minimum": 300, "maximum": 6000},
                "intent": {"type": "string", "enum": ["answer", "act", "draft", "plan", "recall"]},
                "sector": {"type": "string"},
                "project": {"type": "string", "description": "Entity/project name to center the pack on."},
                "as_of": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"], "default": "json"},
                "pin": {"type": "boolean", "default": False, "description": "Persist this pack as an immutable, sha256-addressed audit artifact you can replay later via get_context_pack."},
                "session_id": {"type": "string", "description": "Agent continuity session (asess_...) to link a pinned pack to."},
            },
        },
    },
    {
        "name": "ask_memory",
        "description": (
            "Ask a question against the user's memory and get a cited answer or an explicit "
            "abstention (never an uncited guess). Use for a specific fact; use get_context for "
            "broad working context."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "default": 8},
                "sector": {"type": "string"},
                "as_of": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_entity_context",
        "description": (
            "Everything known about one person, project, org, or topic: cited memories plus the "
            "entity's graph neighborhood (who/what it is connected to and the shared evidence)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Entity name or id (person, project, org, topic)."},
                "limit": {"type": "integer", "default": 8},
            },
            "required": ["name"],
        },
    },
    {
        "name": "expand_context",
        "description": (
            "Expand ONE cited hop around one or more entities: the graph neighborhood plus the cited "
            "commitments, decisions, and recent context for each. Use to recover relational context "
            "when a direct ask abstained or returned low confidence. Cited-or-abstain: returns "
            "nothing for entities with no cited memory."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Entity names or ids (people, projects, orgs, topics).",
                },
                "name": {"type": "string", "description": "Single entity name (alternative to names)."},
                "limit": {"type": "integer", "default": 8},
            },
        },
    },
    {
        "name": "list_capabilities",
        "description": (
            "Discover this Cortex: memory counts, the scopes your token holds, which tool surface "
            "is active, and the full tool catalog with required scopes."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "use_cortex",
        "description": (
            "One entry point for the user's memory. Describe the task in natural language and Cortex "
            "routes it to the right retrieval — a cited answer, a working context pack, an entity/"
            "person briefing, or a keyword search — and returns cited results. Call this when unsure "
            "which specific tool to use."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "What you need from the user's memory, in natural language."},
                "intent": {"type": "string", "enum": ["answer", "act", "draft", "plan", "recall"], "description": "Optional hint about what you're doing."},
                "token_budget": {"type": "integer", "default": 2000, "minimum": 300, "maximum": 6000},
            },
            "required": ["task"],
        },
    },
    {
        "name": "start_agent_session",
        "description": (
            "Open a continuity session for a goal you are working on. Returns a session_id to pass "
            "to checkpoint_agent_session as you work and to resume_agent_session in a future "
            "conversation, so work survives context loss, compaction, and tool switches."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "What this session is trying to accomplish."},
                "host_label": {"type": "string", "description": "Where the agent runs (e.g. 'claude-desktop', 'cursor')."},
                "parent_session_id": {"type": "string", "description": "Optional previous session this continues."},
            },
            "required": ["goal"],
        },
    },
    {
        "name": "checkpoint_agent_session",
        "description": (
            "Save a durable progress checkpoint (what you did, learned, and plan next) into the "
            "user's memory as a cited episode. Auto-approved, episodic-only: checkpoints never "
            "touch the user's personal preference/style layers."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "summary": {"type": "string", "description": "One-paragraph state of the work right now."},
                "details": {"type": "string", "description": "Optional longer notes: decisions, findings, dead ends."},
                "next_steps": {"type": "array", "items": {"type": "string"}, "description": "Concrete next actions for whoever resumes."},
                "status": {"type": "string", "enum": ["active", "paused", "blocked"], "default": "active"},
            },
            "required": ["session_id", "summary"],
        },
    },
    {
        "name": "resume_agent_session",
        "description": (
            "Pick up where a previous agent session left off: returns the session goal/status, its "
            "recent checkpoints (newest first, cited), and the parent session chain."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "checkpoint_limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "list_agent_sessions",
        "description": "List recent agent continuity sessions (optionally filtered by status) to find one to resume.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["active", "paused", "blocked", "closed"]},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "close_agent_session",
        "description": "Mark an agent continuity session finished. Its checkpoints remain in memory as episodes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "outcome": {"type": "string", "description": "Optional final outcome summary."},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "get_context_pack",
        "description": (
            "Replay a pinned context pack by sha: returns the EXACT context that was served at pin "
            "time, with sha256 integrity verified. Use to audit or reproduce what a past agent saw."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pack_sha": {"type": "string", "description": "64-char hex sha256 from get_context pin or list_context_packs."},
            },
            "required": ["pack_sha"],
        },
    },
    {
        "name": "list_context_packs",
        "description": "List pinned context packs (metadata only, newest first), optionally filtered to one agent session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Only packs linked to this agent session (asess_...)."},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "verify_context_pack",
        "description": (
            "Diagnostic recompute-verify of a pinned context pack: re-run the context engine with "
            "the pack's stored inputs (as_of pinned to pin time) and diff against the stored bytes. "
            "Reports match, drift (with per-layer memory-id drift), or engine_mismatch when the "
            "engine version changed. Storage integrity (sha256) is always verified first. Drift is "
            "expected as the corpus evolves; it is not tampering."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pack_sha": {"type": "string", "description": "64-char hex sha256 from get_context pin or list_context_packs."},
            },
            "required": ["pack_sha"],
        },
    },
    {
        "name": "would_i",
        "description": "Predict what the user would decide or prefer, from cited memory evidence only (preferences, style, decisions, and negative-layer vetoes). Returns likely_yes/likely_no/mixed with supporting and opposing citations, or insufficient_evidence when memory cannot answer honestly. Never invents a preference.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The decision or preference question, e.g. 'Would I use tailwind for this project?'"},
                "limit": {"type": "integer", "default": 8, "minimum": 1, "maximum": 20},
            },
            "required": ["question"],
        },
    },
    {
        "name": "draft_as_me",
        "description": "Compile a cited voice pack for drafting in the user's voice: style evidence, relevant preferences, user-authored hard constraints (vetoes), and relevant context. The calling agent writes the draft; Cortex supplies the compiled persona.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What is being drafted, e.g. 'reply to the investor intro email'."},
                "medium": {"type": "string", "description": "Optional medium, e.g. 'email', 'slack', 'blog post'."},
                "limit": {"type": "integer", "default": 8, "minimum": 1, "maximum": 20},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "grade_twin_prediction",
        "description": (
            "Record how a would_i prediction turned out. outcome (correct/incorrect/unclear) "
            "measures prediction accuracy; answerability (answerable/unknown/unclear) separately "
            "labels whether Cortex had enough memory evidence for calibration."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prediction_id": {"type": "string", "description": "The twin_... id returned by would_i."},
                "outcome": {"type": "string", "enum": ["correct", "incorrect", "unclear"]},
                "actual": {"type": "string", "description": "Optional: what the user actually decided."},
                "answerability": {
                    "type": "string",
                    "enum": ["answerable", "unknown", "unclear"],
                    "description": "Optional M6 label: did memory contain enough evidence to answer? Independent of correctness.",
                },
            },
            "required": ["prediction_id", "outcome"],
        },
    },
    {
        "name": "get_twin_scorecard",
        "description": "Return the twin's prediction accuracy scorecard: prediction volume, verdict mix, graded accuracy, and the ungraded backlog awaiting user grading. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 90, "minimum": 1, "maximum": 365},
            },
        },
    },
    {
        "name": "get_twin_calibration",
        "description": (
            "Return M6 calibrated-metacognition metrics from graded twin predictions: expected "
            "calibration error (ECE), Brier score, abstention precision/recall, coverage, and the "
            "confident-wrong rate. Read-only and honest when no outcomes exist."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 90, "minimum": 1, "maximum": 365},
                "prediction_ids": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 120},
                    "maxItems": 100,
                    "description": "Optional exact prediction cohort, for isolated evaluation without historical contamination.",
                },
            },
        },
    },
    {
        "name": "get_proactive_alerts",
        "description": "Return proactive alerts Cortex raised (e.g. a new capture contradicting a high-trust memory). Budget-capped per day and always dismissible. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["pending", "delivered", "dismissed", "accepted", "all"], "default": "delivered"},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "resolve_proactive_alert",
        "description": "Accept or dismiss a proactive alert. Every resolution is recorded as a training label for alert precision.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "alert_id": {"type": "string", "description": "The alrt_... id from a warnings[] block or get_proactive_alerts."},
                "resolution": {"type": "string", "enum": ["accepted", "dismissed"]},
            },
            "required": ["alert_id", "resolution"],
        },
    },
]

# The curated CORE surface an agent sees by default: one tool per job (working context,
# cited answer, keyword search, entity lookup, whole-person map, save a learning, discovery).
# Everything else stays callable — collapsing is advertisement-only, never authorization.
CORE_TOOL_NAMES = frozenset(
    {
        "use_cortex",
        "get_context",
        "ask_memory",
        "search_memory",
        "get_entity_context",
        "get_person_map",
        "remember_this",
        "list_capabilities",
    }
)


# Named tool surfaces (advertisement presets). A token is minted with a surface so each client sees
# a stable, cap-appropriate list (Cursor caps at 40, ChatGPT at 128) that never churns mid-session —
# task-awareness happens inside use_cortex, not by swapping the advertised list. "full" is handled
# specially in tools_for_scopes (every scope-visible tool). Unknown names fall back to "core".
MCP_TOOL_SURFACES: dict[str, frozenset[str]] = {
    "core": CORE_TOOL_NAMES,
    "coding": frozenset(
        {
            "use_cortex",
            "get_context",
            "search_memory",
            "get_entity_context",
            "get_project_context",
            "get_procedure",
            "get_decisions",
            "remember_this",
            "list_capabilities",
            "start_agent_session",
            "checkpoint_agent_session",
            "resume_agent_session",
            "list_agent_sessions",
            "close_agent_session",
            "get_context_pack",
            "list_context_packs",
        }
    ),
    "chat": frozenset(
        {
            "use_cortex",
            "ask_memory",
            "get_context",
            "search_memory",
            "get_person_map",
            "get_entity_context",
            "expand_context",
            "remember_this",
            "list_capabilities",
            "would_i",
            "draft_as_me",
            "grade_twin_prediction",
        }
    ),
}


READ_TOOLS = {
    "use_cortex",
    "get_context",
    # Continuity reads: finding/resuming a session pulls only agent-authored episodes.
    "resume_agent_session",
    "list_agent_sessions",
    # Pack replay/listing are pure reads of machine-owned audit artifacts.
    "get_context_pack",
    "list_context_packs",
    "ask_memory",
    "get_entity_context",
    "expand_context",
    "list_capabilities",
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
    "get_belief_timeline",
    "get_belief_proof",
    "verify_belief_proof",
    "get_tool_scorecard",
    "get_source_reputation",
    # Phase D integrity/portability reads: digest + verify expose only hashes/counts (no content),
    # and verify_memory_bundle is a pure check of a bundle the caller passes in. The bundle EXPORT
    # itself carries content, so it lives in EXPORT_TOOLS, not here.
    "get_memory_integrity",
    "verify_memory_integrity",
    "verify_memory_bundle",
    # Twin reads: would_i / draft_as_me only retrieve and compile cited evidence — the
    # twin_prediction event they log is audit trail, same as record_context_reuse.
    "would_i",
    "draft_as_me",
    "get_twin_scorecard",
    "get_twin_calibration",
    "get_proactive_alerts",
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
    # Reading your own DISTILLED profile is a read, not a bulk export — these are the tools an
    # external agent (Claude/ChatGPT) uses to pull a holistic, cited picture of the user. Only the
    # raw bulk dump (export_memory) stays gated behind the export scope + trust toggle.
    "get_personal_profile",
    "get_person_map",
    "get_agent_adaptation",
    "build_context_pack",
    "get_working_canvas",
    "get_working_canvas_node",
}
REVIEW_TOOLS = {
    "get_daily_review",
    "get_memory_inbox",
}
WRITE_TOOLS = {
    "remember_this",
    "record_working_canvas_node",
    # Grading writes an answer_graded audit event (roadmap: submit_answer_for_grading is write scope).
    "submit_answer_for_grading",
    # Recording a prediction outcome is a durable write to the accuracy ledger (Phase 5.4).
    "grade_twin_prediction",
    # Resolving an alert mutates its status and records a precision training label (Phase 6.3).
    "resolve_proactive_alert",
    # Continuity writes: sessions + checkpoint episodes are agent write-back.
    "start_agent_session",
    "checkpoint_agent_session",
    "close_agent_session",
    "connect_source_account",
    "sync_source_records",
    "sync_github",
    "sync_gmail",
    "sync_google_drive",
    "sync_outlook",
    "sync_slack",
    "sync_agent_sessions",
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
EXPORT_TOOLS = {
    "export_memory",
    # Phase D: the portable bundle wraps the FULL export payload (all captures/memories/tasks/
    # entities/edges) in an integrity manifest. It carries the whole corpus out of Cortex custody,
    # so it is export-scoped exactly like export_memory — not a plain read.
    "export_memory_bundle",
    # Obsidian write-back persists distilled memory (profile/people pages) OUTSIDE Cortex custody,
    # into the user's vault files (which then sync via git/iCloud). That is egress of personal
    # memory — gated exactly like a bulk export, NOT like an in-store write.
    "write_obsidian_pages",
}
MAINTENANCE_TOOLS = {
    "create_memory_backup",
    "sync_connected_sources",
    "get_memory_diagnostics",
    "get_reliability_report",
    "repair_memory_storage",
    "rebuild_memory_search",
    "rebuild_index_from_vault",
    # Phase 2b: recompute-verify is a diagnostic (reads the corpus, emits an audit event) —
    # deliberately maintenance-scoped like the other diagnostics, not part of the core surface.
    "verify_context_pack",
}
SCOPED_MCP_MAINTENANCE_REVIEW_TOOLS = {"approve_memory_capture", "archive_memory_capture"}
DESTRUCTIVE_TOOLS = {"forget_memory", "delete_memory_capture", "delete_memory_backups", "restore_latest_memory_backup", "delete_all_user_data"}
DIRECT_CONNECTOR_SYNC_TOOLS = {
    "sync_agent_sessions",
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


# --- Tool metadata: MCP annotations + titles + output schemas -------------------------------
# Single-sourced from the scope sets above so a hint can never drift from a tool's real behavior.
# Annotations are advisory hints (a client may auto-approve readOnly tools, confirm destructive
# ones) — never authorization; call_tool still enforces every scope.

# Tools that reach OUTSIDE the local memory store (network / external services) → open world.
_OPEN_WORLD_TOOLS = DIRECT_CONNECTOR_SYNC_TOOLS | {
    "connect_source_account",
    "sync_source_records",
    "sync_connected_sources",
}

_TOOL_TITLE_OVERRIDES: dict[str, str] = {
    "get_context": "Get Working Context",
    "ask_memory": "Ask Memory (cited)",
    "search_memory": "Search Memory",
    "get_entity_context": "Get Entity Context",
    "expand_context": "Expand Cited Context",
    "get_person_map": "Whole-Person Map",
    "remember_this": "Remember This",
    "list_capabilities": "List Cortex Capabilities",
    "start_agent_session": "Start Agent Session",
    "checkpoint_agent_session": "Checkpoint Agent Session",
    "resume_agent_session": "Resume Agent Session",
    "list_agent_sessions": "List Agent Sessions",
    "close_agent_session": "Close Agent Session",
    "get_belief_timeline": "Trace Belief Timeline",
    "get_belief_proof": "Prove Belief at a Point in Time",
    "verify_belief_proof": "Verify Belief Proof",
    "get_tool_scorecard": "Review Tool Scorecard",
    "get_source_reputation": "Source Reputation",
    "get_memory_integrity": "Memory Integrity Digest",
    "verify_memory_integrity": "Verify Memory Integrity",
    "export_memory_bundle": "Export Portable Memory Bundle",
    "verify_memory_bundle": "Verify Memory Bundle",
    "submit_answer_for_grading": "Grade Answer Against Memory",
    "get_context_pack": "Replay Pinned Context Pack",
    "list_context_packs": "List Pinned Context Packs",
    "verify_context_pack": "Recompute-Verify Context Pack",
    "would_i": "Would I? (Cited Twin Prediction)",
    "draft_as_me": "Draft As Me (Voice Pack)",
    "grade_twin_prediction": "Grade Twin Prediction",
    "get_twin_scorecard": "Twin Accuracy Scorecard",
    "get_twin_calibration": "Twin Calibration Scorecard",
    "get_proactive_alerts": "Proactive Alerts",
    "resolve_proactive_alert": "Resolve Proactive Alert",
    "write_obsidian_pages": "Write Obsidian Pages",
    "record_working_canvas_node": "Record Working Canvas Node",
    "get_working_canvas": "Get Working Canvas",
    "get_working_canvas_node": "Get Working Canvas Node",
}


def _tool_title(name: str) -> str:
    override = _TOOL_TITLE_OVERRIDES.get(name)
    if override:
        return override
    return name.replace("_", " ").title()


def _tool_annotations(name: str) -> dict[str, Any]:
    is_read = name in READ_TOOLS
    is_export = name in EXPORT_TOOLS
    is_write = name in WRITE_TOOLS
    is_maintenance = name in MAINTENANCE_TOOLS
    is_destructive = name in DESTRUCTIVE_TOOLS
    # Export-scoped tools that WRITE files outside Cortex custody (vault write-back). Scope-wise
    # they are exports (memory egress), but advertising them readOnly would let clients
    # auto-approve a filesystem mutation.
    writes_external_files = name in {"write_obsidian_pages"}
    read_only = (is_read or is_export) and not (is_write or is_maintenance or is_destructive or writes_external_files)
    idempotent = (is_read or is_export) and not is_destructive
    return {
        "title": _tool_title(name),
        "readOnlyHint": read_only,
        "destructiveHint": is_destructive,
        "idempotentHint": idempotent,
        "openWorldHint": name in _OPEN_WORLD_TOOLS,
    }


# Permissive output schemas for the high-value tools an external agent leans on. Kept
# additionalProperties-open so structuredContent always validates against the declared schema
# (a strict client rejects a mismatch) while still documenting the shape for parsers.
_OUTPUT_OBJECT = {"type": "object", "additionalProperties": True}
OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_memory": {
        "type": "object",
        "properties": {
            "results": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            "diagnostics": {"type": "object", "additionalProperties": True},
        },
        "additionalProperties": True,
    },
    "ask_memory": {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "known_unknown": {"type": "boolean"},
            "confidence_detail": {"type": "object", "additionalProperties": True},
            "knowledge_gap": {"type": ["object", "null"], "additionalProperties": True},
            "answer": {"type": "string"},
            "citations": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        },
        "additionalProperties": True,
    },
    "get_context": {
        "type": "object",
        "properties": {
            "task": {"type": "string"},
            "intent": {"type": "string"},
            "layers": {"type": "object", "additionalProperties": True},
            "total_tokens_estimated": {"type": "integer"},
        },
        "additionalProperties": True,
    },
    "get_entity_context": dict(_OUTPUT_OBJECT),
    "get_person_map": dict(_OUTPUT_OBJECT),
    "remember_this": dict(_OUTPUT_OBJECT),
    "list_capabilities": dict(_OUTPUT_OBJECT),
}


def _apply_tool_metadata() -> None:
    """Attach title + annotations (+ outputSchema where declared) to every tool, once at import."""
    for tool in TOOLS:
        name = str(tool.get("name") or "")
        tool.setdefault("title", _tool_title(name))
        tool["annotations"] = _tool_annotations(name)
        schema = OUTPUT_SCHEMAS.get(name)
        if schema is not None:
            tool["outputSchema"] = schema


_apply_tool_metadata()


def tools_for_scopes(token_scopes: list[str] | None = None, *, surface: str = "core") -> list[dict[str, Any]]:
    """Which tools a caller is SHOWN. Admin auth (token_scopes is None — the app's own path)
    always sees everything. Scoped tokens default to the curated core surface so agents face a
    handful of well-chosen tools instead of ~60; tokens minted with maintenance/destructive
    scopes still see those tools (they were deliberately granted), and the "advertise_full"
    marker scope or surface="full" restores the legacy full list. Hiding is never authorization:
    call_tool enforces scopes for every tool regardless of advertisement."""
    if token_scopes is None:
        return TOOLS
    scope_set = set(token_scopes)
    if "advertise_full" in scope_set:
        surface = "full"
    scope_visible = [
        tool
        for tool in TOOLS
        if all(capability in scope_set for capability in tool_required_capabilities(str(tool.get("name") or ""), scoped=True))
    ]
    if surface == "full":
        return scope_visible
    visible_names = set(MCP_TOOL_SURFACES.get(surface, CORE_TOOL_NAMES))
    if "maintenance" in scope_set:
        visible_names |= MAINTENANCE_TOOLS
    if "destructive" in scope_set:
        visible_names |= DESTRUCTIVE_TOOLS
    return [tool for tool in scope_visible if str(tool.get("name") or "") in visible_names]


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


# --- Universal schema exporters ------------------------------------------------------------
# The SAME TOOLS list projects to MCP, OpenAI/Anthropic function-calling, and OpenAPI, so any
# function-calling app can drive Cortex from one definition. Scope filtering reuses
# tools_for_scopes, so a read-only token's exported surface is exactly its allowed tools.

def _tool_input_schema(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("inputSchema")
    if isinstance(schema, dict):
        return schema
    return {"type": "object", "properties": {}}


def export_openai_tools(token_scopes: list[str] | None = None, *, surface: str = "full") -> list[dict[str, Any]]:
    """OpenAI Chat Completions / Responses `tools` array (function-calling)."""
    return [
        {
            "type": "function",
            "function": {
                "name": str(tool.get("name") or ""),
                "description": str(tool.get("description") or ""),
                "parameters": _tool_input_schema(tool),
            },
        }
        for tool in tools_for_scopes(token_scopes, surface=surface)
    ]


def export_anthropic_tools(token_scopes: list[str] | None = None, *, surface: str = "full") -> list[dict[str, Any]]:
    """Anthropic Messages API `tools` array."""
    return [
        {
            "name": str(tool.get("name") or ""),
            "description": str(tool.get("description") or ""),
            "input_schema": _tool_input_schema(tool),
        }
        for tool in tools_for_scopes(token_scopes, surface=surface)
    ]


def export_openapi(
    base_url: str = "http://127.0.0.1:8766",
    token_scopes: list[str] | None = None,
    *,
    surface: str = "full",
    version: str = "0.2.0",
) -> dict[str, Any]:
    """OpenAPI 3.1 document exposing one POST path per tool (operationId == tool name), suitable
    for custom-GPT actions / Zapier-style connectors once a reachable endpoint is enabled."""
    paths: dict[str, Any] = {}
    for tool in tools_for_scopes(token_scopes, surface=surface):
        name = str(tool.get("name") or "")
        if not name:
            continue
        input_schema = _tool_input_schema(tool)
        paths[f"/v1/tools/{name}"] = {
            "post": {
                "operationId": name,
                "summary": str(tool.get("title") or name),
                "description": str(tool.get("description") or ""),
                "requestBody": {
                    "required": bool(input_schema.get("required")),
                    "content": {"application/json": {"schema": input_schema}},
                },
                "responses": {
                    "200": {
                        "description": "Tool result",
                        "content": {
                            "application/json": {
                                "schema": tool.get("outputSchema") or {"type": "object", "additionalProperties": True}
                            }
                        },
                    }
                },
            }
        }
    return {
        "openapi": "3.1.0",
        "info": {"title": "Cortex Memory", "version": version, "description": "Cited personal-memory context for AI agents."},
        "servers": [{"url": base_url.rstrip("/")}],
        "paths": paths,
    }


def export_tool_schema(fmt: str, token_scopes: list[str] | None = None, *, surface: str = "full", base_url: str = "http://127.0.0.1:8766") -> Any:
    """Dispatch a `format` string to the right exporter. Used by the /v1/tools/schema endpoint."""
    normalized = str(fmt or "").strip().lower()
    if normalized in {"openai", "openai-tools", "functions"}:
        return export_openai_tools(token_scopes, surface=surface)
    if normalized in {"anthropic", "claude"}:
        return export_anthropic_tools(token_scopes, surface=surface)
    if normalized in {"openapi", "openapi-3.1", "actions"}:
        return export_openapi(base_url, token_scopes, surface=surface)
    if normalized in {"mcp", "", "raw"}:
        return tools_for_scopes(token_scopes, surface=surface)
    raise ValueError(f"Unknown tool-schema format: {fmt!r}")


# --- MCP resources + prompts (Phase 5) -----------------------------------------------------
# Resources are app-driven, cacheable snapshots of the user's distilled memory, addressed by
# cortex:// URIs. Prompts are user-driven templates that embed cited context so downstream
# generations stay grounded. Both are READ-scoped and run the same trust gate + redaction path as
# the read tools — a resource read is never a scope bypass.

CORTEX_RESOURCES: list[dict[str, Any]] = [
    {"uri": "cortex://profile/person-map", "name": "Whole-person map", "description": "Cited profile + knowledge-graph picture of the user.", "mimeType": "application/json"},
    {"uri": "cortex://profile/personal", "name": "Personal profile", "description": "Distilled, cited profile grouped by memory layer.", "mimeType": "application/json"},
    {"uri": "cortex://profile/adaptation", "name": "Agent adaptation guide", "description": "Cited operating instructions for an AI assistant acting for the user.", "mimeType": "application/json"},
    {"uri": "cortex://schema/capabilities", "name": "Cortex capabilities", "description": "Memory counts + the tool catalog.", "mimeType": "application/json"},
    {"uri": "cortex://review/daily", "name": "Daily review", "description": "Today's pending captures, open loops, decisions, and topics.", "mimeType": "application/json"},
]
CORTEX_RESOURCE_TEMPLATES: list[dict[str, Any]] = [
    {"uriTemplate": "cortex://entity/{name}", "name": "Entity context", "description": "Cited memory + graph neighborhood for one person/project/org/topic.", "mimeType": "application/json"},
]
CORTEX_PROMPTS: list[dict[str, Any]] = [
    {"name": "summarize_recent_decisions", "description": "Summarize the user's recent decisions from cited memory.", "arguments": [{"name": "since", "description": "Optional ISO date to summarize from.", "required": False}]},
    {"name": "extract_action_items", "description": "Extract open action items / commitments from the user's memory.", "arguments": [{"name": "topic", "description": "Optional topic to focus on.", "required": False}]},
    {"name": "brief_me_on", "description": "Produce a cited briefing on a person, project, or topic.", "arguments": [{"name": "subject", "description": "Person/project/topic to brief on.", "required": True}]},
]


def _read_gate(store: CortexStore, user_id: str, token_scopes: list[str] | None) -> None:
    """Read scope + trust gate shared by resources and prompts (same discipline as read tools)."""
    if token_scopes is not None and "read" not in token_scopes:
        raise PermissionError("MCP token is not scoped for read actions.")
    store.require_agent_access(user_id, "read")


def list_resources() -> list[dict[str, Any]]:
    return [dict(resource) for resource in CORTEX_RESOURCES]


def list_resource_templates() -> list[dict[str, Any]]:
    return [dict(template) for template in CORTEX_RESOURCE_TEMPLATES]


def read_resource(store: CortexStore, user_id: str, uri: str, token_scopes: list[str] | None = None) -> dict[str, Any]:
    _read_gate(store, user_id, token_scopes)
    target = str(uri or "").strip()
    if target == "cortex://profile/person-map":
        payload: Any = store.person_map(user_id)
    elif target == "cortex://profile/personal":
        payload = store.personal_profile(user_id)
    elif target == "cortex://profile/adaptation":
        payload = store.agent_adaptation(user_id)
    elif target == "cortex://schema/capabilities":
        payload = {
            "stats": store.stats(user_id),
            "tools": [
                {"name": str(tool.get("name") or ""), "purpose": str(tool.get("description") or "")[:160]}
                for tool in TOOLS
            ],
        }
    elif target == "cortex://review/daily":
        payload = store.daily_review(user_id)
    elif target.startswith("cortex://entity/"):
        name = unquote(target[len("cortex://entity/"):]).strip()
        if not name:
            raise ValueError("entity name is required in the resource URI")
        payload = {
            "entity": name,
            "context": store.person_context(user_id, name),
            "neighborhood": store.entity_neighborhood(user_id, name),
        }
    else:
        raise ValueError(f"Unknown Cortex resource: {uri}")
    payload = store.agent_payload(user_id, payload)
    return {"contents": [{"uri": target, "mimeType": "application/json", "text": json.dumps(payload, ensure_ascii=False)}]}


def list_prompts() -> list[dict[str, Any]]:
    return [dict(prompt) for prompt in CORTEX_PROMPTS]


def get_prompt(store: CortexStore, user_id: str, name: str, args: dict[str, Any] | None = None, token_scopes: list[str] | None = None) -> dict[str, Any]:
    _read_gate(store, user_id, token_scopes)
    args = args or {}

    def _message(text: str) -> dict[str, Any]:
        return {"messages": [{"role": "user", "content": {"type": "text", "text": text}}]}

    def _cited(payload: Any) -> str:
        return json.dumps(store.agent_payload(user_id, payload), ensure_ascii=False, indent=2)

    if name == "summarize_recent_decisions":
        history = store.decision_history(user_id, "", limit=12, include_superseded=False)
        return _message(
            "Summarize the user's recent decisions using ONLY the cited memory below. Keep each "
            "memory_id/source attached to every claim, and note any superseded or conflicting "
            "decisions. Do not invent decisions that are not present.\n\n" + _cited(history)
        )
    if name == "extract_action_items":
        topic = _text_arg(args, "topic")
        tasks = store.open_tasks(user_id, limit=20, sector=None)
        header = f"Extract the open action items / commitments{f' about {topic}' if topic else ''} from the cited memory below. "
        return _message(
            header + "Return a checklist; keep the memory_id/source for each. Only include items "
            "actually present.\n\n" + _cited(tasks)
        )
    if name == "brief_me_on":
        subject = _text_arg(args, "subject", max_chars=MCP_NAME_MAX_CHARS)
        if not subject:
            raise ValueError("the 'subject' argument is required")
        payload = {
            "subject": subject,
            "context": store.person_context(user_id, subject),
            "neighborhood": store.entity_neighborhood(user_id, subject),
        }
        return _message(
            f"Brief me on {subject} using ONLY the cited memory below — who/what it is, key "
            "decisions, commitments, and open loops. Keep memory_id/source on every point; if "
            "coverage is thin, say so.\n\n" + _cited(payload)
        )
    raise ValueError(f"Unknown Cortex prompt: {name}")


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


def _text_list_arg(
    args: dict[str, Any],
    key: str,
    *,
    max_items: int = 100,
    max_chars: int = 120,
) -> list[str] | None:
    raw = args.get(key)
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError(f"MCP argument '{key}' must be an array.")
    values: list[str] = []
    seen: set[str] = set()
    for item in raw[:max_items]:
        value = str(item or "").strip()
        if len(value) > max_chars:
            raise ValueError(f"MCP argument '{key}' items exceed {max_chars} characters.")
        if value and value not in seen:
            seen.add(value)
            values.append(value)
    return values


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


_ROUTER_QUESTION_PREFIXES = (
    "who ", "what ", "when ", "where ", "why ", "how ", "which ", "whose ",
    "is ", "are ", "was ", "were ", "does ", "did ", "do ", "can ", "should ", "could ", "will ",
)
_ROUTER_ENTITY_MARKERS = (
    "brief me on ", "tell me about ", "everything about ", "everything on ",
    "context on ", "context about ", "about ", "who is ", "who's ",
)
_ROUTER_SEARCH_MARKERS = ("search ", "find ", "look up ", "search:", "grep ")


def _route_use_cortex(task: str, intent: str | None, args: dict[str, Any]) -> tuple[str, dict[str, Any], list[str]]:
    """Deterministic task router for the use_cortex tool: pick the retrieval tool that best fits the
    request. Entity briefings, direct questions, and explicit searches route to their specialist
    tools; everything else gets a working context pack. All targets are read-only, so the router
    can never escalate scope."""
    text = (task or "").strip()
    lowered = text.lower()
    budget = _bounded_int_arg(args, "token_budget", 2000, minimum=300, maximum=6000)
    alternatives = ["get_context", "ask_memory", "search_memory", "get_entity_context"]

    def others(chosen: str) -> list[str]:
        return [tool for tool in alternatives if tool != chosen]

    for marker in _ROUTER_ENTITY_MARKERS:
        if lowered.startswith(marker):
            entity = text[len(marker):].strip(" ?.\t").strip()
            if entity:
                return "get_entity_context", {"name": entity[:MCP_NAME_MAX_CHARS], "limit": 8}, others("get_entity_context")
    if lowered.endswith("?") or lowered.startswith(_ROUTER_QUESTION_PREFIXES):
        return "ask_memory", {"query": text, "top_k": 8}, others("ask_memory")
    for marker in _ROUTER_SEARCH_MARKERS:
        if lowered.startswith(marker):
            query = text[len(marker):].strip() or text
            return "search_memory", {"query": query, "top_k": 8}, others("search_memory")
    context_args: dict[str, Any] = {"task": text, "token_budget": budget}
    if intent:
        context_args["intent"] = intent
    return "get_context", context_args, others("get_context")


def call_tool(store: CortexStore, user_id: str, name: str, args: dict[str, Any], token_scopes: list[str] | None = None) -> Any:
    _require_tool_access(store, user_id, name, token_scopes)
    if name in DIRECT_CONNECTOR_SYNC_TOOLS and _bool_arg(args, "complete_snapshot"):
        if token_scopes is not None and "maintenance" not in token_scopes:
            raise PermissionError("MCP token is not scoped for maintenance actions.")
        store.require_agent_access(user_id, "maintenance")
    if name == "remember_this":
        content = args.get("content", "")
        source = args.get("source", "ai-chat")
        # remember_this is an explicit "save this about me" instruction, so the content is the
        # user's own — trust personal memories (preference/style/dislike) as theirs even when the
        # agent labels the source "claude"/"chatgpt" (otherwise those get silently dropped).
        extracted = extract_context(
            content,
            source,
            author_aliases=store.settings(user_id).get("identity_aliases"),
            self_authored=True,
        )
        return store.agent_payload(user_id, store.save_capture(
            user_id=user_id,
            content=content,
            source=source,
            source_url=args.get("source_url"),
            title=args.get("title"),
            extracted=extracted,
            cite_capture_provenance=True,
            auto_approve=load_settings().auto_approve_captures,
        ))
    if name == "start_agent_session":
        return store.agent_payload(
            user_id,
            store.begin_agent_session(
                user_id,
                goal=_text_arg(args, "goal", max_chars=500),
                host_label=_text_arg(args, "host_label", max_chars=120),
                parent_session_id=_text_arg(args, "parent_session_id", max_chars=80) or None,
            ),
        )
    if name == "checkpoint_agent_session":
        next_steps_arg = args.get("next_steps")
        next_steps = [str(step) for step in next_steps_arg] if isinstance(next_steps_arg, list) else []
        return store.agent_payload(
            user_id,
            store.checkpoint_agent_session(
                user_id,
                _text_arg(args, "session_id", max_chars=80),
                summary=_text_arg(args, "summary", max_chars=4000),
                details=_text_arg(args, "details", max_chars=20000),
                next_steps=next_steps,
                status=_text_arg(args, "status", "active", max_chars=12) or "active",
            ),
        )
    if name == "resume_agent_session":
        return store.agent_payload(
            user_id,
            store.resume_agent_session(
                user_id,
                _text_arg(args, "session_id", max_chars=80),
                checkpoint_limit=_bounded_int_arg(args, "checkpoint_limit", 5, minimum=1, maximum=20),
            ),
        )
    if name == "list_agent_sessions":
        return store.agent_payload(
            user_id,
            store.list_agent_sessions(
                user_id,
                status=_text_arg(args, "status", max_chars=12) or None,
                limit=_bounded_int_arg(args, "limit", 20, minimum=1, maximum=100),
            ),
        )
    if name == "close_agent_session":
        return store.agent_payload(
            user_id,
            store.close_agent_session(
                user_id,
                _text_arg(args, "session_id", max_chars=80),
                outcome=_text_arg(args, "outcome", max_chars=2000),
            ),
        )
    if name == "use_cortex":
        task = _text_arg(args, "task")
        intent = _text_arg(args, "intent", max_chars=16) or None
        target, target_args, alternatives = _route_use_cortex(task, intent, args)
        # Dispatch through call_tool so the target tool's own scope enforcement + payload shaping
        # run unchanged (all targets are read-only, so a read token suffices).
        result = call_tool(store, user_id, target, target_args, token_scopes)
        return {"routed_to": target, "task": task, "alternatives": alternatives, "result": result}
    if name == "get_context":
        # The identity/persona layer is distilled, cited context about the user — a read, like the
        # rest of the picture. Any read-scoped agent gets it; a token with neither read nor export
        # (write/maintenance-only) still sees a visible omission record instead of a hard error.
        include_identity = token_scopes is None or bool({"read", "export"} & set(token_scopes))
        return store.agent_payload(
            user_id,
            store.assemble_context(
                user_id,
                _text_arg(args, "task"),
                surface=_text_arg(args, "surface", "agent", max_chars=40) or "agent",
                token_budget=_bounded_int_arg(args, "token_budget", 2000, minimum=300, maximum=6000),
                sector=_text_arg(args, "sector") or None,
                project=_text_arg(args, "project", max_chars=MCP_NAME_MAX_CHARS) or None,
                as_of=_text_arg(args, "as_of", max_chars=40) or None,
                intent=_text_arg(args, "intent", max_chars=16) or None,
                include_identity=include_identity,
                format=_text_arg(args, "format", "json", max_chars=12) or "json",
                pin=bool(args.get("pin")),
                session_id=_text_arg(args, "session_id", max_chars=80) or None,
            ),
        )
    if name == "get_context_pack":
        return store.agent_payload(
            user_id,
            store.get_context_pack(user_id, _text_arg(args, "pack_sha", max_chars=64)),
        )
    if name == "list_context_packs":
        return store.agent_payload(
            user_id,
            store.list_context_packs(
                user_id,
                session_id=_text_arg(args, "session_id", max_chars=80) or None,
                limit=_bounded_int_arg(args, "limit", 20, minimum=1, maximum=100),
            ),
        )
    if name == "verify_context_pack":
        return store.agent_payload(
            user_id,
            store.verify_context_pack(user_id, _text_arg(args, "pack_sha", max_chars=64)),
        )
    if name == "ask_memory":
        return store.agent_payload(
            user_id,
            store.answer_query(
                user_id,
                _text_arg(args, "query"),
                _bounded_int_arg(args, "top_k", 8),
                sector=_text_arg(args, "sector") or None,
                as_of=_text_arg(args, "as_of", max_chars=40) or None,
            ),
        )
    if name == "get_entity_context":
        entity_name = _text_arg(args, "name", max_chars=MCP_NAME_MAX_CHARS)
        limit = _bounded_int_arg(args, "limit", 8)
        neighborhood = store.entity_neighborhood(user_id, entity_name, limit=limit)
        context = store.person_context(user_id, entity_name, limit=limit)
        return store.agent_payload(user_id, {"entity": entity_name, "context": context, "neighborhood": neighborhood})
    if name == "expand_context":
        names_arg = args.get("names")
        names_list = [str(n) for n in names_arg if str(n or "").strip()] if isinstance(names_arg, list) else []
        single = _text_arg(args, "name", max_chars=MCP_NAME_MAX_CHARS)
        if single and single not in names_list:
            names_list.append(single)
        limit = _bounded_int_arg(args, "limit", 8)
        return store.agent_payload(user_id, store.expand_context(user_id, names_list, limit=limit))
    if name == "list_capabilities":
        scope_list = sorted(set(token_scopes)) if token_scopes is not None else ["admin"]
        catalog = [
            {
                "name": str(tool.get("name") or ""),
                "purpose": str(tool.get("description") or "")[:160],
                "required_scopes": tool_required_capabilities(str(tool.get("name") or ""), scoped=True),
                "advertised": str(tool.get("name") or "") in CORE_TOOL_NAMES,
            }
            for tool in TOOLS
        ]
        return {
            "stats": store.stats(user_id),
            "token_scopes": scope_list,
            "surface": "full" if token_scopes is None or "advertise_full" in set(token_scopes or []) else "core",
            "tools": catalog,
            "expand_surface": "Mint a token with the advertise_full scope (or set CORTEX_MCP_TOOL_SURFACE=full) to advertise every tool; unadvertised tools remain callable when your scopes allow.",
        }
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
    if name == "sync_agent_sessions":
        # Local log harvesting: agent list only, no directory override args — an MCP client must
        # not be able to point the scanner at arbitrary filesystem paths.
        raw_agents = args.get("agents")
        agent_list = [str(item) for item in raw_agents] if isinstance(raw_agents, list) else None
        result = store.sync_agent_sessions(
            user_id,
            agents=agent_list,
            source_account_id=args.get("source_account_id"),
            account_label=args.get("account_label"),
            processing=args.get("processing", "sync"),
            max_records=_bounded_int_arg(args, "max_records", 200, maximum=MCP_SYNC_RECORD_MAX),
            per_session_limit=_bounded_int_arg(args, "per_session_limit", 25, maximum=200),
            cursor_name=args.get("cursor_name", "agent-sessions"),
            review_required=_bool_arg(args, "review_required", True),
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
    if name == "record_working_canvas_node":
        # raw_text deliberately bypasses _text_arg: stripping whitespace would silently
        # mutate the offloaded evidence, and the whole M3 contract is byte-exact recovery
        # (the hash is computed over exactly what the agent handed us).
        raw_text = str(args.get("raw_text") or "")
        if len(raw_text) > 200000:
            raise ValueError("MCP argument 'raw_text' exceeds 200000 characters.")
        result = store.record_working_canvas_node(
            user_id,
            session_id=_text_arg(args, "session_id", "", max_chars=120),
            node_id=_text_arg(args, "node_id", "", max_chars=120),
            label=_text_arg(args, "label", "", max_chars=120),
            summary=_text_arg(args, "summary", "", max_chars=500),
            raw_text=raw_text,
            predecessor_node_id=_text_arg(args, "predecessor_node_id", "", max_chars=120) or None,
        )
        return store.agent_payload(user_id, result)
    if name == "get_working_canvas":
        result = store.get_working_canvas(
            user_id,
            session_id=_text_arg(args, "session_id", "", max_chars=120),
            limit=_bounded_int_arg(args, "limit", 80, maximum=200),
            max_chars=_bounded_int_arg(args, "max_chars", 0, minimum=0, maximum=200000),
        )
        store.record_context_reuse(user_id, surface="mcp", query=result["session_id"], target="working-canvas")
        return store.agent_payload(user_id, result)
    if name == "get_working_canvas_node":
        result = store.get_working_canvas_node(
            user_id,
            session_id=_text_arg(args, "session_id", "", max_chars=120),
            node_id=_text_arg(args, "node_id", "", max_chars=120),
            include_raw=_bool_arg(args, "include_raw", True),
        )
        return store.agent_payload(user_id, result)
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
    if name == "get_belief_timeline":
        topic = _text_arg(args, "topic", "", max_chars=240)
        result = store.get_belief_timeline(
            user_id,
            topic,
            limit=_bounded_int_arg(args, "limit", 20, maximum=50),
            as_of=args.get("as_of"),
        )
        store.record_context_reuse(user_id, surface="mcp", query=topic, target="belief-timeline")
        return store.agent_payload(user_id, result)
    if name == "get_belief_proof":
        topic = _text_arg(args, "topic", "", max_chars=240)
        result = store.get_belief_proof(
            user_id,
            topic,
            valid_at=args.get("valid_at"),
            known_at=args.get("known_at"),
            expected_head=args.get("expected_head"),
            limit=_bounded_int_arg(args, "limit", 20, maximum=50),
        )
        store.record_context_reuse(user_id, surface="mcp", query=topic, target="belief-proof")
        return store.agent_payload(user_id, result)
    if name == "verify_belief_proof":
        proof = args.get("proof")
        if not isinstance(proof, dict):
            raise ValueError("proof must be an object")
        return store.agent_payload(
            user_id,
            store.verify_belief_proof(
                proof,
                expected_head=_text_arg(args, "expected_head", "", max_chars=128) or None,
            ),
        )
    if name == "get_tool_scorecard":
        result = store.get_tool_scorecard(
            user_id,
            days=_bounded_int_arg(args, "days", 7, maximum=90),
            token_id=str(args.get("token_id") or "").strip() or None,
        )
        return store.agent_payload(user_id, result)
    if name == "get_source_reputation":
        result = store.source_reputation(
            user_id,
            days=_bounded_int_arg(args, "days", 90, maximum=365),
        )
        return store.agent_payload(user_id, result)
    if name == "would_i":
        question = _text_arg(args, "question", "", max_chars=500)
        result = store.would_i(user_id, question, limit=_bounded_int_arg(args, "limit", 8, maximum=20))
        store.record_context_reuse(user_id, surface="mcp", query=question, target="twin-prediction")
        return store.agent_payload(user_id, result)
    if name == "draft_as_me":
        prompt = _text_arg(args, "prompt", "", max_chars=2000)
        result = store.draft_as_me(
            user_id,
            prompt,
            medium=_text_arg(args, "medium", "", max_chars=60),
            limit=_bounded_int_arg(args, "limit", 8, maximum=20),
        )
        store.record_context_reuse(user_id, surface="mcp", query=prompt, target="voice-pack")
        return store.agent_payload(user_id, result)
    if name == "grade_twin_prediction":
        result = store.grade_twin_prediction(
            user_id,
            _text_arg(args, "prediction_id", "", max_chars=120),
            _text_arg(args, "outcome", "", max_chars=20),
            actual=_text_arg(args, "actual", "", max_chars=500),
            answerability=_text_arg(args, "answerability", "", max_chars=20) or None,
        )
        return store.agent_payload(user_id, result)
    if name == "get_twin_scorecard":
        result = store.get_twin_scorecard(user_id, days=_bounded_int_arg(args, "days", 90, maximum=365))
        return store.agent_payload(user_id, result)
    if name == "get_twin_calibration":
        result = store.get_twin_calibration(
            user_id,
            days=_bounded_int_arg(args, "days", 90, maximum=365),
            prediction_ids=_text_list_arg(args, "prediction_ids"),
        )
        return store.agent_payload(user_id, result)
    if name == "get_proactive_alerts":
        status = str(args.get("status") or "delivered").strip().lower()
        result = store.list_proactive_alerts(
            user_id,
            status=None if status == "all" else status,
            limit=_bounded_int_arg(args, "limit", 20, maximum=100),
        )
        return store.agent_payload(user_id, {"alerts": result})
    if name == "resolve_proactive_alert":
        result = store.resolve_proactive_alert(
            user_id,
            _text_arg(args, "alert_id", "", max_chars=120),
            _text_arg(args, "resolution", "", max_chars=20),
        )
        return store.agent_payload(user_id, result)
    if name == "submit_answer_for_grading":
        result = store.grade_answer(
            user_id,
            _text_arg(args, "answer_text", "", max_chars=20000),
            session_id=str(args.get("session_id") or "").strip() or None,
            pack_sha=str(args.get("pack_sha") or "").strip() or None,
        )
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
    if name == "get_memory_integrity":
        return store.integrity_digest(user_id)
    if name == "verify_memory_integrity":
        expected = _text_arg(args, "expected_head", max_chars=128)
        if not expected:
            raise ValueError("expected_head is required")
        return store.verify_integrity(user_id, expected)
    if name == "export_memory_bundle":
        return store.export_portable_bundle(user_id)
    if name == "verify_memory_bundle":
        bundle = args.get("bundle")
        if not isinstance(bundle, dict):
            raise ValueError("bundle must be a JSON object")
        return store.verify_portable_bundle(bundle)
    if name == "write_obsidian_pages":
        return store.agent_payload(
            user_id,
            store.write_obsidian_pages(
                user_id,
                vault_path=_text_arg(args, "vault_path", max_chars=2000) or None,
                people_limit=_bounded_int_arg(args, "people_limit", 10, minimum=0, maximum=50),
            ),
        )
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
