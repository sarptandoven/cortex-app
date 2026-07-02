from __future__ import annotations

import hashlib
import hmac
import base64
import json
import math
import os
import platform
import re
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .connectors._redaction import redact_error_message
from .database import connect, sqlite_vec_status
from .embeddings import VECTOR_DIMENSIONS, embed_text, embed_text_result, embedding_hash, embedding_json, embedding_source_text, embedding_status
from .extractor import extract_context, now_iso, stable_id
from .sqlite_runtime import SQLITE_RUNTIME, sqlite3
from .source_ingest import SourceRecord, analyze_sources, import_source_records_page, supported_sources
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
    "source-account-sync",
    "google-managed-oauth",
    "service-managed-oauth",
    "obsidian-connector",
    "github-token-connector",
    "slack-token-connector",
    "readwise-token-connector",
    "raindrop-token-connector",
    "calendar-ics-connector",
    "linear-token-connector",
    "jira-token-connector",
    "notion-token-connector",
    "sync-device-manifests",
    "sync-receipts",
)
SUPPORT_BUNDLE_SCHEMA = 1
DEFAULT_BACKUP_RETENTION_COUNT = 20
DEFAULT_BACKUP_RETENTION_DAYS = 0
DEFAULT_MCP_TOKEN_SCOPES = ("read",)
MCP_TOKEN_SCOPES = {"read", "write", "export", "maintenance", "destructive"}

MEMORY_LAYERS = {"semantic", "episodic", "style", "decision", "preference", "negative", "procedural"}
MEMORY_LAYER_BY_KIND = {
    "claim": "semantic",
    "observation": "semantic",
    "summary": "semantic",
    "procedure": "procedural",
    "event": "episodic",
    "decision": "decision",
    "preference": "preference",
    "style": "style",
    "negative": "negative",
}
_PERSON_QUERY_INTENT_RE = re.compile(
    r"(?i)\b(?:talk(?:ing)?\s+to|meet(?:ing)?\s+with|brief\s+me|briefing\s+(?:on|for)|call\s+with|"
    r"catch(?:ing)?\s+up\s+with|follow(?:ing)?\s+up\s+with|1:1\s+with|one[- ]on[- ]one\s+with|"
    r"email(?:ing)?\s+to|message\s+to|promised?\s+(?:to\s+)?|prepare\s+(?:me\s+)?for\s+(?:my\s+)?(?:meeting|call|chat))\b"
)

LAYER_RETRIEVAL_BOOST = 0.02
TEMPORAL_RETRIEVAL_BOOST = 0.025
# Near-tie boosts: must stay below LAYER_RETRIEVAL_BOOST and the temporal
# boosts so recency/importance break ties without overriding relevance.
RECENCY_RETRIEVAL_BOOST_STEPS: tuple[tuple[float, float], ...] = (
    (2.0, 0.004),
    (7.0, 0.003),
    (30.0, 0.002),
    (90.0, 0.001),
    (365.0, 0.0005),
)
IMPORTANCE_RETRIEVAL_BOOST_STEP = 0.0008
IMPORTANCE_RETRIEVAL_BOOST_MAX = 0.0024
# Confidence tie-breaker for retrieval ranking. Most memories are "confirmed" (the extractor
# default), so this is uniform and inert for them; it only moves the ranking when extraction
# assigns weaker confidence (e.g. inferred/low), so a shaky memory loses a near-tie to a
# trusted one instead of confidence being ignored entirely. Kept small so it breaks ties
# without overriding lexical/vector relevance.
CONFIDENCE_RETRIEVAL_BOOSTS: dict[str, float] = {
    "confirmed": 0.006,
    "verified": 0.006,
    "high": 0.006,
    "probable": 0.0,
    "medium": 0.0,
    "inferred": -0.004,
    "unverified": -0.008,
    "weak": -0.012,
    "low": -0.016,
}
# A claimed job leases its lock for this long. If a worker dies mid-job (OOM/SIGKILL/deploy
# restart/hang), the lease expires and the job is reclaimed instead of being stuck in 'running'
# forever. Matches job_health's stale_after default so "stale_running" and reclaim agree.
MEMORY_JOB_LEASE_SECONDS = 15 * 60
SOURCE_QUALITY_RETRIEVAL_BOOST_MAX = 0.012
SAME_CAPTURE_RELATION_FULL_PAIR_LIMIT = 80
SAME_CAPTURE_RELATION_PER_MEMORY_LIMIT = 6
SAME_CAPTURE_RELATION_TOTAL_LIMIT = 2000
SAME_CAPTURE_RELATION_TOPIC_BUCKET_LIMIT = 40
SAME_CAPTURE_RELATION_BUCKET_SCAN_LIMIT = 24
QUERY_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
QUERY_MONTH_PATTERN = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
QUERY_TEMPORAL_STOPWORDS = {
    "about",
    "after",
    "before",
    "during",
    "event",
    "events",
    "find",
    "for",
    "from",
    "happen",
    "happened",
    "history",
    "memory",
    "memories",
    "month",
    "on",
    "past",
    "record",
    "records",
    "show",
    "that",
    "the",
    "then",
    "this",
    "timeline",
    "what",
    "when",
    "where",
    "which",
    "year",
}
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
            "decisions",
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
            "preferences",
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
            "changed",
            "recent",
            "recently",
        },
    ),
    (
        {"procedural"},
        {
            "checklist",
            "deploy",
            "do",
            "how",
            "operate",
            "operating",
            "playbook",
            "process",
            "procedure",
            "release",
            "releasing",
            "rollback",
            "rollout",
            "runbook",
            "setup",
            "ship",
            "shipping",
            "step",
            "steps",
            "workflow",
        },
    ),
)
QUERY_FTS_STOPWORDS = QUERY_TEMPORAL_STOPWORDS | {
    "am",
    "and",
    "are",
    "be",
    "been",
    "can",
    "could",
    "did",
    "do",
    "does",
    "had",
    "has",
    "have",
    "how",
    "i",
    "me",
    "my",
    "or",
    "our",
    "please",
    "should",
    "stated",
    "to",
    "usually",
    "we",
    "why",
    "you",
    "your",
}
ANSWER_REASON_TERMS = {"why", "reason", "reasons", "rationale", "because"}
ANSWER_SUPPORT_QUERY_EXCLUDE = ANSWER_REASON_TERMS | {
    "decide",
    "decided",
    "decision",
    "decisions",
    "did",
}
ANSWER_DATE_CLAIM_RE = re.compile(
    rf"\b(?:{QUERY_MONTH_PATTERN})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+(?:19|20)\d{{2}})?\b|\b\d{{4}}-\d{{2}}-\d{{2}}\b",
    re.IGNORECASE,
)
ANSWER_CLAIM_FIELDS = (
    "backup owner",
    "database",
    "default connector",
    "handoff owner",
    "incident owner",
    "launch channel",
    "owner",
    "primary connector",
    "primary source",
    "release channel",
    "retrieval engine",
    "storage backend",
    "support owner",
)
ANSWER_CLAIM_RE = re.compile(
    rf"\b(?P<field>{'|'.join(re.escape(field) for field in ANSWER_CLAIM_FIELDS)})\b\s+"
    r"(?:(?:is|was|uses?|choose|chooses|chosen as|should be|should use|will be|will use|"
    r"changed to|moved to|switched to|now is|now uses|replaces)\s+)"
    r"(?P<value>[^.;\n]+)",
    re.IGNORECASE,
)
ANSWER_FIELD_REQUIREMENTS: tuple[tuple[str, set[str], set[str]], ...] = (
    ("owner", {"owner"}, {"owner", "owns", "responsible"}),
    ("launch channel", {"launch", "channel"}, {"channel"}),
    ("release channel", {"release", "channel"}, {"channel"}),
    ("storage backend", {"storage", "backend"}, {"backend", "database", "sqlite", "postgres", "pgvector"}),
    ("retrieval engine", {"retrieval", "engine"}, {"engine", "fts", "vector", "pgvector", "sqlite", "hybrid"}),
    ("primary connector", {"primary", "connector"}, {"connector"}),
    ("default connector", {"default", "connector"}, {"connector"}),
    ("date", {"date"}, {"date", "deadline", "timing", "schedule"}),
    ("deadline", {"deadline"}, {"date", "deadline", "timing", "schedule"}),
    ("budget", {"budget"}, {"budget", "cost", "price", "spend"}),
    ("cost", {"cost"}, {"budget", "cost", "price", "spend"}),
)
QUERY_LEXICAL_FALLBACK_STOPWORDS = QUERY_FTS_STOPWORDS | {
    "a",
    "an",
    "and",
    "any",
    "anything",
    "around",
    "as",
    "at",
    "concerning",
    "dont",
    "in",
    "is",
    "it",
    "its",
    "look",
    "looking",
    "mention",
    "mentioned",
    "note",
    "notes",
    "of",
    "regarding",
    "related",
    "remember",
    "remind",
    "said",
    "say",
    "something",
    "stuff",
    "tell",
    "thing",
    "told",
    "was",
    "were",
    "with",
    "you",
    "your",
}
TASK_QUERY_STOPWORDS = {
    "action",
    "actions",
    "agenda",
    "ask",
    "asks",
    "blocker",
    "blockers",
    "close",
    "due",
    "follow",
    "followup",
    "followups",
    "item",
    "items",
    "loop",
    "loops",
    "need",
    "next",
    "open",
    "pending",
    "question",
    "questions",
    "step",
    "steps",
    "task",
    "tasks",
    "todo",
    "todos",
    "unresolved",
    "waiting",
    "work",
}
TASK_QUERY_PHRASES = (
    "action item",
    "action items",
    "anything pending",
    "follow up",
    "follow-up",
    "followup",
    "loose end",
    "loose ends",
    "need to do",
    "next step",
    "next steps",
    "open loop",
    "open loops",
    "open question",
    "open questions",
    "still need",
    "to-do",
    "todo",
    "unresolved",
    "waiting on",
)
TASK_QUERY_TERMS = {
    "action",
    "blocker",
    "blockers",
    "followup",
    "followups",
    "pending",
    "task",
    "tasks",
    "todo",
    "todos",
    "unresolved",
}


DEFAULT_USER_SETTINGS: dict[str, Any] = {
    "review_new_captures": True,
    "allow_pending_in_context": False,
    "context_pack_limit": 12,
    "allow_agent_reads": True,
    "allow_agent_writes": True,
    "allow_agent_exports": False,
    "allow_agent_maintenance": False,
    "allow_agent_destructive_actions": False,
    "redact_sensitive_context": True,
    "source_policies": {},
    "identity_aliases": [],
}


SOURCE_CONNECTOR_CATALOG: tuple[dict[str, Any], ...] = (
    {"id": "chatgpt", "name": "ChatGPT", "category": "AI chats", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "Direct ChatGPT account connector is required before this can be a primary source."},
    {"id": "claude", "name": "Claude", "category": "AI chats", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "Direct Claude account connector is required before this can be a primary source."},
    {"id": "gemini", "name": "Gemini", "category": "AI chats", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "Direct Gemini account connector is required before this can be a primary source."},
    {"id": "perplexity", "name": "Perplexity", "category": "AI chats", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "Direct Perplexity account connector is required before this can be a primary source."},
    {"id": "copilot", "name": "Microsoft Copilot", "category": "AI chats", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "Direct Copilot account connector is required before this can be a primary source."},
    {"id": "grok", "name": "Grok", "category": "AI chats", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "Direct Grok account connector is required before this can be a primary source."},
    {"id": "poe", "name": "Poe", "category": "AI chats", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "Direct Poe account connector is required before this can be a primary source."},
    {"id": "notebooklm", "name": "NotebookLM", "category": "AI chats", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "Direct NotebookLM account connector is required before this can be a primary source."},
    {"id": "gmail", "name": "Gmail", "category": "Email", "auth": "oauth", "live_status": "api_token", "scopes": ["gmail.readonly"], "notes": "Read-only Gmail sync works through browser Google sign-in when this build has OAuth credentials configured."},
    {"id": "apple-mail", "name": "Apple Mail", "category": "Email", "auth": "local_file", "live_status": "import_ready", "scopes": [], "notes": "Local Mail integration requires explicit app data access."},
    {"id": "outlook", "name": "Outlook", "category": "Email", "auth": "oauth", "live_status": "api_token", "scopes": ["Mail.Read", "User.Read"], "notes": "Read-only Outlook mail sync works through browser Microsoft sign-in when this build has OAuth credentials configured."},
    {"id": "email", "name": "Email", "category": "Email", "auth": "file", "live_status": "import_ready", "scopes": [], "notes": "Email connector coverage for local and account-backed mail sources."},
    {"id": "docs", "name": "Docs and writing", "category": "Docs", "auth": "file", "live_status": "import_ready", "scopes": [], "notes": "Document connector coverage for notes, drafts, and writing."},
    {"id": "pdfs", "name": "PDFs", "category": "Docs", "auth": "file", "live_status": "import_ready", "scopes": [], "notes": "PDF connector coverage for readable documents."},
    {"id": "cloud-docs", "name": "Cloud docs", "category": "Docs", "auth": "export", "live_status": "import_ready", "scopes": [], "notes": "Cloud document connector coverage for Drive, OneDrive, and Dropbox Paper account sources."},
    {"id": "notion", "name": "Notion", "category": "Docs", "auth": "oauth", "live_status": "api_token", "scopes": ["read_content"], "notes": "Read-only Notion page sync works through browser Notion sign-in when this build has OAuth credentials configured."},
    {"id": "google-drive", "name": "Google Drive", "category": "Docs", "auth": "oauth", "live_status": "api_token", "scopes": ["drive.readonly"], "notes": "Read-only Drive sync works through browser Google sign-in when this build has OAuth credentials configured."},
    {"id": "google-docs", "name": "Google Docs", "category": "Docs", "auth": "oauth", "live_status": "planned", "scopes": ["drive.readonly", "documents.readonly"], "notes": "Drive and Docs account sync is the intended connector path."},
    {"id": "google-keep", "name": "Google Keep", "category": "Notes", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "Direct Google Keep account connector is required before this can be a primary source."},
    {"id": "microsoft-365", "name": "Microsoft 365", "category": "Docs", "auth": "oauth", "live_status": "planned", "scopes": ["Files.Read", "Mail.Read", "Calendars.Read"], "notes": "Microsoft 365 account sync is the intended connector path."},
    {"id": "slack", "name": "Slack", "category": "Work chat", "auth": "api_token", "live_status": "api_token", "scopes": ["channels:history", "groups:history", "channels:read", "groups:read"], "notes": "Read-only Slack channel sync works with a bot or user token for selected channels."},
    {"id": "google-chat", "name": "Google Chat", "category": "Work chat", "auth": "oauth", "live_status": "planned", "scopes": ["chat.messages.readonly"], "notes": "Google Chat account sync is the intended connector path."},
    {"id": "teams", "name": "Microsoft Teams", "category": "Work chat", "auth": "oauth", "live_status": "planned", "scopes": ["ChannelMessage.Read.All"], "notes": "Teams account sync is the intended connector path."},
    {"id": "discord", "name": "Discord", "category": "Messages", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "Direct Discord account connector is required before this can be a primary source."},
    {"id": "telegram", "name": "Telegram", "category": "Messages", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "Direct Telegram connector is required before this can be a primary source."},
    {"id": "messages", "name": "Messages", "category": "Messages", "auth": "local_file", "live_status": "local_only", "scopes": [], "notes": "Local Messages integration requires explicit app data access."},
    {"id": "imessage", "name": "iMessage", "category": "Messages", "auth": "local_file", "live_status": "local_only", "scopes": [], "notes": "Local iMessage integration requires explicit Messages data access."},
    {"id": "whatsapp", "name": "WhatsApp", "category": "Messages", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "Direct WhatsApp connector is required before this can be a primary source."},
    {"id": "calendar", "name": "Calendar", "category": "Calendar", "auth": "local_file", "live_status": "local_only", "scopes": [], "notes": "Read-only Calendar ICS file/feed sync works locally; Google/Microsoft OAuth is still planned."},
    {"id": "contacts", "name": "Contacts", "category": "People", "auth": "oauth", "live_status": "planned", "scopes": ["contacts.readonly"], "notes": "Contacts account sync is the intended connector path."},
    {"id": "work-tools", "name": "Work tools", "category": "Work tools", "auth": "file", "live_status": "import_ready", "scopes": [], "notes": "Work tool connector coverage for issues, pull requests, tasks, and projects."},
    {"id": "github", "name": "GitHub", "category": "Work tools", "auth": "api_token", "live_status": "api_token", "scopes": ["repo:read"], "notes": "Read-only GitHub issue and pull request sync works with a fine-grained personal access token."},
    {"id": "linkedin", "name": "LinkedIn", "category": "Work tools", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "Direct LinkedIn account connector is required before this can be a primary source."},
    {"id": "linear", "name": "Linear", "category": "Work tools", "auth": "api_token", "live_status": "api_token", "scopes": ["read"], "notes": "Read-only Linear issue sync works with a personal API key."},
    {"id": "jira", "name": "Jira", "category": "Work tools", "auth": "api_token", "live_status": "api_token", "scopes": ["read:jira-work"], "notes": "Read-only Jira issue sync works with a Jira Cloud site URL, Atlassian account email, and API token."},
    {"id": "zoom", "name": "Zoom", "category": "Meetings", "auth": "oauth", "live_status": "planned", "scopes": ["recording:read"], "notes": "Zoom account sync is the intended connector path."},
    {"id": "browser-bookmarks", "name": "Browser bookmarks", "category": "Research", "auth": "local_file", "live_status": "import_ready", "scopes": [], "notes": "Local browser integration covers bookmarks and history with explicit app data access."},
    {"id": "browser-history", "name": "Browser history", "category": "Research", "auth": "local_file", "live_status": "import_ready", "scopes": [], "notes": "Local browser history integration requires explicit app data access; no background browser collection."},
    {"id": "readwise", "name": "Readwise", "category": "Research", "auth": "api_token", "live_status": "api_token", "scopes": ["read"], "notes": "Read-only Readwise highlight export sync works with a user access token."},
    {"id": "raindrop", "name": "Raindrop", "category": "Research", "auth": "api_token", "live_status": "api_token", "scopes": ["read"], "notes": "Read-only Raindrop bookmark and highlight sync works with a user API token."},
    {"id": "zotero", "name": "Zotero", "category": "Research", "auth": "local_api", "live_status": "local_api", "scopes": ["read"], "notes": "Read-only Zotero item, note, and annotation sync works through the local desktop API by default; Web API tokens are optional."},
    {"id": "knowledge-base", "name": "Knowledge base", "category": "Research", "auth": "file", "live_status": "import_ready", "scopes": [], "notes": "Knowledge base connector coverage for local notes and read-later services."},
    {"id": "twitter-x", "name": "Twitter/X", "category": "Social", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "Direct Twitter/X account connector is required before this can be a primary source."},
    {"id": "apple-notes", "name": "Apple Notes", "category": "Notes", "auth": "export", "live_status": "export_only", "scopes": [], "notes": "Local Apple Notes integration is the intended source path."},
    {"id": "obsidian", "name": "Obsidian", "category": "Notes", "auth": "local_folder", "live_status": "import_ready", "scopes": [], "notes": "Local Obsidian vault integration is the intended connector path."},
)


SOURCE_CONNECTOR_IMPORT_METADATA: dict[str, dict[str, Any]] = {
    "chatgpt": {"source_ids": ["chatgpt"], "export_status": "native", "import_label": "ChatGPT account connector records"},
    "claude": {"source_ids": ["claude"], "export_status": "native", "import_label": "Claude account connector records"},
    "gemini": {"source_ids": ["gemini"], "export_status": "generic", "import_status": "generic", "import_label": "Gemini connector records"},
    "perplexity": {"source_ids": ["perplexity"], "export_status": "generic", "import_status": "generic", "import_label": "Perplexity connector records"},
    "copilot": {"source_ids": ["copilot"], "export_status": "generic", "import_status": "generic", "import_label": "Microsoft Copilot connector records"},
    "grok": {"source_ids": ["grok"], "export_status": "generic", "import_status": "generic", "import_label": "Grok connector records"},
    "poe": {"source_ids": ["poe"], "export_status": "generic", "import_status": "generic", "import_label": "Poe connector records"},
    "notebooklm": {"source_ids": ["notebooklm"], "export_status": "generic", "import_status": "generic", "import_label": "NotebookLM connector records"},
    "gmail": {"source_ids": ["email"], "source_aliases": ["gmail", "google-mail"], "export_status": "native_via_email", "import_status": "native", "import_label": "Gmail account records map to Email"},
    "apple-mail": {"source_ids": ["email"], "source_aliases": ["mail", "mail-app"], "export_status": "native_via_email", "import_status": "native", "import_label": "Apple Mail local records map to Email"},
    "outlook": {"source_ids": ["email", "calendar", "contacts", "cloud-docs"], "source_aliases": ["microsoft-outlook", "office-mail"], "export_status": "generic", "import_status": "generic", "import_label": "Outlook account records map to Email, Calendar, Contacts, and Cloud docs"},
    "email": {"source_ids": ["email"], "export_status": "native", "import_label": "Email connector records map directly"},
    "docs": {"source_ids": ["docs"], "export_status": "native", "import_label": "Docs and writing connector records map directly"},
    "pdfs": {"source_ids": ["docs"], "source_aliases": ["pdf", "pdf-documents"], "export_status": "native_via_docs", "import_status": "native", "import_label": "PDF connector records map to Docs and writing"},
    "cloud-docs": {"source_ids": ["cloud-docs", "docs"], "export_status": "generic", "import_status": "generic", "import_label": "Cloud document account records map to Cloud docs"},
    "notion": {"source_ids": ["notion"], "export_status": "native", "import_label": "Native Notion account connector records"},
    "google-drive": {"source_ids": ["cloud-docs", "docs"], "export_status": "generic", "import_status": "generic", "import_label": "Google Drive account records map to Cloud docs"},
    "google-docs": {"source_ids": ["cloud-docs", "docs"], "source_aliases": ["google-documents"], "export_status": "generic", "import_status": "generic", "import_label": "Google Docs account records map to Cloud docs"},
    "google-keep": {"source_ids": ["google-keep"], "export_status": "native", "import_label": "Native Google Keep connector records"},
    "microsoft-365": {"source_ids": ["cloud-docs", "email", "calendar", "contacts"], "export_status": "generic", "import_status": "generic", "import_label": "Microsoft 365 account records map to Cloud docs, Email, Calendar, and Contacts"},
    "slack": {"source_ids": ["slack"], "export_status": "native", "import_label": "Native Slack account connector records"},
    "google-chat": {"source_ids": ["google-chat"], "export_status": "native", "import_label": "Native Google Chat connector records"},
    "teams": {"source_ids": ["teams"], "export_status": "native", "import_label": "Native Teams account connector records"},
    "discord": {"source_ids": ["discord"], "export_status": "native", "import_label": "Native Discord connector records"},
    "telegram": {"source_ids": ["telegram"], "export_status": "native", "import_label": "Native Telegram connector records"},
    "messages": {"source_ids": ["messages"], "export_status": "native", "import_label": "Messages local records"},
    "imessage": {"source_ids": ["messages"], "source_aliases": ["ios-messages", "apple-messages"], "export_status": "native_via_messages", "import_status": "native", "import_label": "iMessage local records map to Messages"},
    "whatsapp": {"source_ids": ["whatsapp", "messages"], "export_status": "generic", "import_status": "generic", "import_label": "WhatsApp connector records map to Messages"},
    "calendar": {"source_ids": ["calendar"], "export_status": "native", "import_label": "Calendar account records"},
    "contacts": {"source_ids": ["contacts"], "export_status": "native", "import_label": "Contacts account records"},
    "work-tools": {"source_ids": ["work-tools", "github", "linear", "jira"], "export_status": "generic", "import_status": "generic", "import_label": "Work tool connector records map to Work tools"},
    "github": {"source_ids": ["github", "work-tools"], "export_status": "generic", "import_status": "generic", "import_label": "GitHub account and project records map to Work tools"},
    "linkedin": {"source_ids": ["linkedin"], "export_status": "native", "import_label": "Native LinkedIn account connector records"},
    "linear": {"source_ids": ["linear", "work-tools"], "export_status": "generic", "import_status": "generic", "import_label": "Linear account records map to Work tools"},
    "jira": {"source_ids": ["jira", "work-tools"], "export_status": "generic", "import_status": "generic", "import_label": "Jira account records map to Work tools"},
    "zoom": {"source_ids": ["zoom"], "export_status": "native", "import_label": "Native Zoom account records"},
    "browser-bookmarks": {"source_ids": ["browser-bookmarks", "browser-history"], "export_status": "native", "import_label": "Browser bookmarks and history local records"},
    "browser-history": {"source_ids": ["browser-bookmarks", "browser-history"], "source_aliases": ["chrome-history", "firefox-history"], "export_status": "native", "import_label": "Browser history local records"},
    "readwise": {"source_ids": ["readwise", "knowledge-base"], "export_status": "generic", "import_status": "generic", "import_label": "Readwise account records map to Knowledge bases"},
    "raindrop": {"source_ids": ["raindrop", "knowledge-base"], "export_status": "generic", "import_status": "generic", "import_label": "Raindrop account records map to Knowledge bases"},
    "zotero": {"source_ids": ["zotero", "knowledge-base"], "export_status": "generic", "import_status": "generic", "import_label": "Zotero account records map to Knowledge bases"},
    "knowledge-base": {"source_ids": ["knowledge-base", "obsidian", "logseq", "roam", "readwise", "zotero", "pocket", "instapaper", "raindrop"], "export_status": "generic", "import_status": "generic", "import_label": "Knowledge base local records"},
    "twitter-x": {"source_ids": ["twitter-x"], "export_status": "native", "import_label": "Native Twitter/X account connector records"},
    "apple-notes": {"source_ids": ["apple-notes", "docs"], "export_status": "generic", "import_status": "generic", "import_label": "Apple Notes local records map to Notes and writing"},
    "obsidian": {"source_ids": ["obsidian", "knowledge-base"], "export_status": "generic", "import_status": "generic", "import_label": "Obsidian vault local records map to Knowledge bases"},
}

PRIMARY_BETA_CONNECTOR_IDS: frozenset[str] = frozenset({"obsidian"})
BASELINE_10K_CONNECTOR_IDS: frozenset[str] = frozenset(
    {
        "obsidian",
        "gmail",
        "outlook",
        "google-drive",
        "slack",
        "github",
        "readwise",
        "linear",
        "notion",
        "jira",
        "raindrop",
        "calendar",
        "zotero",
    }
)
COMMON_CONNECTOR_SETUP_FIELDS: tuple[dict[str, Any], ...] = (
    {"name": "account_label", "label": "Account label", "kind": "text", "required": False, "secret": False, "max_length": 160},
    {"name": "account_identifier", "label": "Account identifier", "kind": "text", "required": False, "secret": False, "max_length": 240},
)

GOOGLE_OAUTH_REFRESH_SETUP_FIELDS: tuple[dict[str, Any], ...] = (
    {"name": "refresh_token", "label": "OAuth refresh token", "kind": "secret", "required": False, "secret": True, "max_length": 4000},
    {"name": "access_token_expires_at", "label": "Access token expires at", "kind": "timestamp", "required": False, "secret": False, "max_length": 80},
    {"name": "client_id", "label": "OAuth client ID", "kind": "secret", "required": False, "secret": True, "max_length": 4000},
    {"name": "client_secret", "label": "OAuth client secret", "kind": "secret", "required": False, "secret": True, "max_length": 4000},
    {"name": "token_endpoint", "label": "OAuth token endpoint", "kind": "url", "required": False, "secret": False, "default": "https://oauth2.googleapis.com/token", "max_length": 500},
    {"name": "scope", "label": "OAuth scope", "kind": "text", "required": False, "secret": False, "max_length": 1000},
)

MICROSOFT_OAUTH_REFRESH_SETUP_FIELDS: tuple[dict[str, Any], ...] = (
    {"name": "refresh_token", "label": "OAuth refresh token", "kind": "secret", "required": False, "secret": True, "max_length": 4000},
    {"name": "access_token_expires_at", "label": "Access token expires at", "kind": "timestamp", "required": False, "secret": False, "max_length": 80},
    {"name": "client_id", "label": "OAuth client ID", "kind": "secret", "required": False, "secret": True, "max_length": 4000},
    {"name": "client_secret", "label": "OAuth client secret", "kind": "secret", "required": False, "secret": True, "max_length": 4000},
    {"name": "token_endpoint", "label": "OAuth token endpoint", "kind": "url", "required": False, "secret": False, "default": "https://login.microsoftonline.com/common/oauth2/v2.0/token", "max_length": 500},
    {"name": "scope", "label": "OAuth scope", "kind": "text", "required": False, "secret": False, "max_length": 1000},
)

CONNECTOR_SETUP_BLUEPRINTS: dict[str, dict[str, Any]] = {
    "obsidian": {
        "mode": "native-local-connector",
        "endpoint": "/v1/connectors/obsidian/sync",
        "default_cursor_name": "local-folder",
        "default_max_records": 1000,
        "max_records_limit": 5000,
        "credential_fields": [],
        "configuration_fields": [
            {"name": "vault_path", "label": "Vault folder", "kind": "local_folder", "required": True, "secret": False, "local_path": True, "max_length": 2000},
        ],
    },
    "github": {
        "mode": "native-token-connector",
        "endpoint": "/v1/connectors/github/sync",
        "discovery_endpoint": "/v1/connectors/github/discover",
        "discovery_target_field": "repositories",
        "default_cursor_name": "issues",
        "default_max_records": 100,
        "max_records_limit": 500,
        "credential_fields": [{"name": "token", "label": "GitHub token", "kind": "secret", "required": True, "secret": True, "max_length": 4000}],
        "configuration_fields": [
            {"name": "repositories", "label": "Repositories", "kind": "string_list", "required": True, "secret": False, "max_items": 25, "options_endpoint": "/v1/connectors/github/discover", "option_label_key": "label", "option_value_key": "sync_value"},
            {"name": "include_comments", "label": "Include comments and reviews", "kind": "boolean", "required": False, "default": True, "secret": False},
            {"name": "max_comments_per_item", "label": "Max comments per item", "kind": "integer", "required": False, "default": 10, "minimum": 0, "maximum": 50, "secret": False},
            {"name": "since", "label": "Sync after", "kind": "timestamp", "required": False, "secret": False, "max_length": 80},
            {"name": "api_base_url", "label": "API base URL", "kind": "url", "required": False, "secret": False, "max_length": 500},
        ],
    },
    "gmail": {
        "mode": "native-token-connector",
        "endpoint": "/v1/connectors/gmail/sync",
        "managed_oauth_shipped": True,
        "oauth_provider": "google",
        "oauth_start_endpoint": "/v1/connectors/google/oauth/start",
        "oauth_complete_endpoint": "/v1/connectors/google/oauth/complete",
        "default_cursor_name": "messages",
        "default_max_records": 50,
        "max_records_limit": 200,
        "credential_fields": [
            {"name": "access_token", "label": "Gmail access token", "kind": "secret", "required": True, "secret": True, "max_length": 4000},
            *GOOGLE_OAUTH_REFRESH_SETUP_FIELDS,
        ],
        "configuration_fields": [
            {"name": "query", "label": "Gmail query", "kind": "text", "required": False, "secret": False, "max_length": 500},
            {"name": "label_ids", "label": "Label IDs", "kind": "string_list", "required": False, "secret": False, "max_items": 20},
            {"name": "include_body", "label": "Include message body", "kind": "boolean", "required": False, "default": True, "secret": False},
            {"name": "since", "label": "Sync after", "kind": "timestamp", "required": False, "secret": False, "max_length": 80},
            {"name": "api_base_url", "label": "API base URL", "kind": "url", "required": False, "secret": False, "max_length": 500},
        ],
    },
    "google-drive": {
        "mode": "native-token-connector",
        "endpoint": "/v1/connectors/google-drive/sync",
        "managed_oauth_shipped": True,
        "oauth_provider": "google",
        "oauth_start_endpoint": "/v1/connectors/google/oauth/start",
        "oauth_complete_endpoint": "/v1/connectors/google/oauth/complete",
        "default_cursor_name": "files",
        "default_max_records": 50,
        "max_records_limit": 200,
        "credential_fields": [
            {"name": "access_token", "label": "Google Drive access token", "kind": "secret", "required": True, "secret": True, "max_length": 4000},
            *GOOGLE_OAUTH_REFRESH_SETUP_FIELDS,
        ],
        "configuration_fields": [
            {"name": "query", "label": "Drive query", "kind": "text", "required": False, "secret": False, "max_length": 500},
            {"name": "mime_types", "label": "MIME types", "kind": "string_list", "required": False, "secret": False, "max_items": 20},
            {"name": "include_content", "label": "Include readable file content", "kind": "boolean", "required": False, "default": True, "secret": False},
            {"name": "since", "label": "Sync after", "kind": "timestamp", "required": False, "secret": False, "max_length": 80},
            {"name": "api_base_url", "label": "API base URL", "kind": "url", "required": False, "secret": False, "max_length": 500},
        ],
    },
    "outlook": {
        "mode": "native-token-connector",
        "endpoint": "/v1/connectors/outlook/sync",
        "managed_oauth_shipped": True,
        "oauth_provider": "microsoft",
        "oauth_start_endpoint": "/v1/connectors/oauth/start",
        "oauth_complete_endpoint": "/v1/connectors/oauth/complete",
        "default_cursor_name": "messages",
        "default_max_records": 50,
        "max_records_limit": 200,
        "credential_fields": [
            {"name": "access_token", "label": "Microsoft Graph access token", "kind": "secret", "required": True, "secret": True, "max_length": 4000},
            *MICROSOFT_OAUTH_REFRESH_SETUP_FIELDS,
        ],
        "configuration_fields": [
            {"name": "query", "label": "Outlook query", "kind": "text", "required": False, "secret": False, "max_length": 1000},
            {"name": "include_body", "label": "Include message body", "kind": "boolean", "required": False, "default": True, "secret": False},
            {"name": "since", "label": "Sync after", "kind": "timestamp", "required": False, "secret": False, "max_length": 80},
            {"name": "api_base_url", "label": "API base URL", "kind": "url", "required": False, "secret": False, "max_length": 500},
        ],
    },
    "slack": {
        "mode": "native-token-connector",
        "endpoint": "/v1/connectors/slack/sync",
        "discovery_endpoint": "/v1/connectors/slack/discover",
        "discovery_target_field": "channels",
        "default_cursor_name": "messages",
        "default_max_records": 100,
        "max_records_limit": 200,
        "credential_fields": [{"name": "token", "label": "Slack token", "kind": "secret", "required": True, "secret": True, "max_length": 4000}],
        "configuration_fields": [
            {"name": "channels", "label": "Channels", "kind": "string_list", "required": True, "secret": False, "max_items": 20, "options_endpoint": "/v1/connectors/slack/discover", "option_label_key": "label", "option_value_key": "sync_value"},
            {"name": "workspace_url", "label": "Workspace URL", "kind": "url", "required": False, "secret": False, "max_length": 500},
            {"name": "since", "label": "Sync after", "kind": "timestamp", "required": False, "secret": False, "max_length": 80},
            {"name": "api_base_url", "label": "API base URL", "kind": "url", "required": False, "secret": False, "max_length": 500},
        ],
    },
    "readwise": {
        "mode": "native-token-connector",
        "endpoint": "/v1/connectors/readwise/sync",
        "default_cursor_name": "highlights",
        "default_max_records": 100,
        "max_records_limit": 500,
        "credential_fields": [{"name": "token", "label": "Readwise token", "kind": "secret", "required": True, "secret": True, "max_length": 4000}],
        "configuration_fields": [
            {"name": "since", "label": "Sync after", "kind": "timestamp", "required": False, "secret": False, "max_length": 80},
            {"name": "page_cursor", "label": "Page cursor", "kind": "text", "required": False, "secret": False, "max_length": 2000},
            {"name": "api_base_url", "label": "API base URL", "kind": "url", "required": False, "secret": False, "max_length": 500},
        ],
    },
    "calendar": {
        "mode": "native-local-connector",
        "endpoint": "/v1/connectors/calendar/sync",
        "default_cursor_name": "events",
        "default_max_records": 100,
        "max_records_limit": 500,
        "require_one_of": ["ics_path", "feed_url"],
        "credential_fields": [],
        "configuration_fields": [
            {"name": "ics_path", "label": "ICS file", "kind": "local_file", "required": False, "secret": False, "local_path": True, "max_length": 1000},
            {"name": "feed_url", "label": "ICS feed URL", "kind": "url", "required": False, "secret": True, "max_length": 1000},
            {"name": "since", "label": "Sync after", "kind": "timestamp", "required": False, "secret": False, "max_length": 80},
        ],
    },
    "raindrop": {
        "mode": "native-token-connector",
        "endpoint": "/v1/connectors/raindrop/sync",
        "default_cursor_name": "raindrops",
        "default_max_records": 100,
        "max_records_limit": 500,
        "credential_fields": [{"name": "token", "label": "Raindrop token", "kind": "secret", "required": True, "secret": True, "max_length": 4000}],
        "configuration_fields": [
            {"name": "collection_id", "label": "Collection ID", "kind": "text", "required": False, "default": "0", "secret": False, "max_length": 120},
            {"name": "include_highlights", "label": "Include highlights", "kind": "boolean", "required": False, "default": True, "secret": False},
            {"name": "since", "label": "Sync after", "kind": "timestamp", "required": False, "secret": False, "max_length": 80},
            {"name": "api_base_url", "label": "API base URL", "kind": "url", "required": False, "secret": False, "max_length": 500},
        ],
    },
    "zotero": {
        "mode": "native-local-connector",
        "endpoint": "/v1/connectors/zotero/sync",
        "default_cursor_name": "items",
        "default_max_records": 100,
        "max_records_limit": 500,
        "credential_fields": [{"name": "token", "label": "Optional Zotero Web API token", "kind": "secret", "required": False, "secret": True, "max_length": 4000}],
        "configuration_fields": [
            {"name": "library_type", "label": "Library type", "kind": "select", "required": False, "default": "user", "options": ["user", "group"], "secret": False},
            {"name": "library_id", "label": "Library ID", "kind": "text", "required": False, "default": "0", "secret": False, "max_length": 120},
            {"name": "include_attachments", "label": "Include attachments", "kind": "boolean", "required": False, "default": False, "secret": False},
            {"name": "since", "label": "Sync after", "kind": "timestamp", "required": False, "secret": False, "max_length": 80},
            {"name": "api_base_url", "label": "API base URL", "kind": "url", "required": False, "secret": False, "max_length": 500},
        ],
    },
    "linear": {
        "mode": "native-token-connector",
        "endpoint": "/v1/connectors/linear/sync",
        "default_cursor_name": "issues",
        "default_max_records": 100,
        "max_records_limit": 500,
        "credential_fields": [{"name": "token", "label": "Linear API key", "kind": "secret", "required": True, "secret": True, "max_length": 4000}],
        "configuration_fields": [
            {"name": "since", "label": "Sync after", "kind": "timestamp", "required": False, "secret": False, "max_length": 80},
            {"name": "api_url", "label": "API URL", "kind": "url", "required": False, "secret": False, "max_length": 500},
        ],
    },
    "jira": {
        "mode": "native-token-connector",
        "endpoint": "/v1/connectors/jira/sync",
        "default_cursor_name": "issues",
        "default_max_records": 100,
        "max_records_limit": 500,
        "credential_fields": [
            {"name": "email", "label": "Atlassian account email", "kind": "email", "required": True, "secret": False, "max_length": 320},
            {"name": "api_token", "label": "Jira API token", "kind": "secret", "required": True, "secret": True, "max_length": 4000},
            {"name": "site_url", "label": "Jira site URL", "kind": "url", "required": True, "secret": False, "max_length": 500},
        ],
        "configuration_fields": [
            {"name": "jql", "label": "JQL filter", "kind": "text", "required": False, "secret": False, "max_length": 2000},
            {"name": "since", "label": "Sync after", "kind": "timestamp", "required": False, "secret": False, "max_length": 80},
        ],
    },
    "notion": {
        "mode": "native-token-connector",
        "endpoint": "/v1/connectors/notion/sync",
        "managed_oauth_shipped": True,
        "oauth_provider": "notion",
        "oauth_start_endpoint": "/v1/connectors/oauth/start",
        "oauth_complete_endpoint": "/v1/connectors/oauth/complete",
        "default_cursor_name": "pages",
        "default_max_records": 50,
        "max_records_limit": 200,
        "credential_fields": [{"name": "token", "label": "Notion integration token", "kind": "secret", "required": True, "secret": True, "max_length": 4000}],
        "configuration_fields": [
            {"name": "include_content", "label": "Include page content", "kind": "boolean", "required": False, "default": True, "secret": False},
            {"name": "since", "label": "Sync after", "kind": "timestamp", "required": False, "secret": False, "max_length": 80},
            {"name": "api_base_url", "label": "API base URL", "kind": "url", "required": False, "secret": False, "max_length": 500},
            {"name": "notion_version", "label": "Notion API version", "kind": "text", "required": False, "secret": False, "max_length": 80},
        ],
    },
}
SERVICE_MEMORY_SOURCE_KEYS: frozenset[str] = frozenset(
    {
        "calendar",
        "chatgpt",
        "claude",
        "cloud-docs",
        "contacts",
        "copilot",
        "discord",
        "email",
        "gemini",
        "github",
        "gmail",
        "google-chat",
        "google-docs",
        "google-drive",
        "google-keep",
        "grok",
        "jira",
        "linear",
        "linkedin",
        "microsoft-365",
        "notebooklm",
        "notion",
        "outlook",
        "perplexity",
        "poe",
        "readwise",
        "slack",
        "teams",
        "telegram",
        "twitter-x",
        "whatsapp",
        "work-tools",
        "zoom",
    }
)
LOCAL_FILE_MEMORY_SOURCE_KEYS: frozenset[str] = frozenset(
    {
        "apple-mail",
        "apple-notes",
        "browser-bookmarks",
        "browser-history",
        "docs",
        "imessage",
        "knowledge-base",
        "messages",
        "obsidian",
        "pdfs",
    }
)


SENSITIVE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\b(?:xox[baprs]-[A-Za-z0-9-]{16,})\b"), "[REDACTED_SLACK_TOKEN]"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|client[_-]?id|client[_-]?secret|secret|password|passwd|pwd)\s*[:=]\s*['\"]?[^'\"\s,;]{8,}"
        ),
        r"\1=[REDACTED_SECRET]",
    ),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\d[ -]*?){13,16}\b"), "[REDACTED_NUMBER]"),
)
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)^(?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|client[_-]?id|client[_-]?secret|secret|password|passwd|pwd)$"
)
SOURCE_ACCOUNT_METADATA_SAFE_SECRET_LIKE_KEYS = frozenset(
    {
        "api_token_configured",
        "credential_ref",
        "refresh_token_configured",
        "token_configured",
    }
)
SOURCE_ACCOUNT_METADATA_SECRET_KEY_PATTERN = re.compile(
    r"(?i)(?:^|[_-])(?:api[_-]?key|api[_-]?token|access[_-]?token|auth[_-]?token|refresh[_-]?token|client[_-]?id|client[_-]?secret|private[_-]?key|secret|password|passwd|pwd|token|credential|credentials)(?:$|[_-])"
)
SOURCE_ACCOUNT_METADATA_SECRET_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\b(?:xox[baprs]-[A-Za-z0-9-]{16,})\b"), "[REDACTED_SLACK_TOKEN]"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|client[_-]?id|client[_-]?secret|secret|password|passwd|pwd)\s*[:=]\s*['\"]?[^'\"\s,;]{8,}"
        ),
        r"\1=[REDACTED_SECRET]",
    ),
)

LOCAL_PATH_PATTERN = re.compile(
    r"(?<![\w:/])(?:file://)?/(?:Applications|Library|Users|Volumes|private|tmp|var|home)/[^\s)>\]\"']+"
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

LOCAL_PATH_VALUE_KEYS = SUPPORT_PATH_KEYS | {"paths", "source_url"}


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


def _normalize_account_state_key(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")


RETRIEVAL_METADATA_FILTER_KEYS: dict[str, tuple[str, ...]] = {
    "channel": ("channel", "channel_id"),
    "channel_id": ("channel_id",),
    "repository": ("repository",),
    "record_scope": ("record_scope",),
    "state": ("state", "status"),
    "project": ("project", "project_key"),
    "project_key": ("project_key",),
}


def _normalize_retrieval_filter_value(value: Any, *, max_length: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:max_length]


def _normalize_retrieval_metadata_filters(value: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    filters: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = _normalize_source_key(str(raw_key or "")).replace("-", "_")
        if key not in RETRIEVAL_METADATA_FILTER_KEYS:
            continue
        normalized = _normalize_retrieval_filter_value(raw_value)
        if normalized:
            filters[key] = normalized
    return filters


def _normalize_as_of_filter(value: Any) -> str | None:
    text = str(value or "").strip()[:80]
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return f"{text}T23:59:59+00:00"
    return text


def _retrieval_filter_payload(
    *,
    source: str | None = None,
    source_account_id: str | None = None,
    metadata_filters: dict[str, Any] | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    normalized_source = _normalize_source_key(source or "")
    if normalized_source:
        payload["source"] = normalized_source
    normalized_account_id = _normalize_retrieval_filter_value(source_account_id, max_length=120)
    if normalized_account_id:
        payload["source_account_id"] = normalized_account_id
    normalized_metadata = _normalize_retrieval_metadata_filters(metadata_filters)
    if normalized_metadata:
        payload["metadata"] = normalized_metadata
    normalized_as_of = _normalize_as_of_filter(as_of)
    if normalized_as_of:
        payload["as_of"] = normalized_as_of
    return payload


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


def _normalize_source_account_policy(value: Any) -> dict[str, Any]:
    raw_policy = dict(value) if isinstance(value, dict) else {}
    mode = _normalize_source_key(str(raw_policy.get("mode") or "default")) or "default"
    if mode not in {"default", "trusted", "review", "excluded"}:
        mode = "default"
    policy = dict(raw_policy)
    policy["allow_ai_context"] = bool(policy.get("allow_ai_context", mode != "excluded"))
    policy["review_required"] = bool(policy.get("review_required", True))
    if mode == "excluded":
        policy["allow_ai_context"] = False
        policy["review_required"] = True
    return policy


def _source_policy_sources(source_policies: dict[str, dict[str, Any]], predicate) -> list[str]:
    sources: set[str] = set()
    for source, policy in source_policies.items():
        if not predicate(policy):
            continue
        aliases = _source_account_alias_sources(source)
        sources.update(aliases or {_normalize_source_key(source)})
    return sorted(source for source in sources if source)


def _unique_catalog_strings(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _default_import_label(import_status: str, source_name: str) -> str:
    status = str(import_status or "").lower()
    if status == "native":
        return f"{source_name} connector can process source records directly"
    if status in {"generic", "import_ready"}:
        return f"{source_name} connector is available for local source records"
    if status == "export_only":
        return f"{source_name} needs a direct connector before becoming a primary source"
    return "Connect this source when an account or direct integration is available"


def _connector_service_baseline(item: dict[str, Any], source_ids: list[str], *, supports_import: bool) -> dict[str, Any]:
    connector_id = _normalize_source_key(item.get("id"))
    live_status = str(item.get("live_status") or "").lower()
    primary_beta = _connector_primary_beta(item)
    local_app_autosync = bool(primary_beta or live_status in {"api_token", "local_api", "local_only"})
    manual_direct_sync = bool(supports_import and not local_app_autosync and live_status != "planned")
    hosted_managed_sync = bool(item.get("hosted_managed_sync"))
    if primary_beta:
        path = "native-local-sync"
    elif live_status == "api_token":
        path = "native-token-sync"
    elif live_status in {"local_api", "local_only"}:
        path = "native-local-sync"
    elif live_status == "planned":
        path = "normalized-record-sync-now-account-sign-in-planned"
    elif supports_import:
        path = "connector-records-supported"
    else:
        path = "direct-connector-needed"
    return {
        "included": connector_id in BASELINE_10K_CONNECTOR_IDS,
        "records_supported": bool(supports_import),
        "live_sync": local_app_autosync,
        "manual_direct_sync": manual_direct_sync,
        "local_app_autosync": local_app_autosync,
        "hosted_managed_sync": hosted_managed_sync,
        "primary_ui": primary_beta,
        "path": path,
        "source_ids": source_ids,
    }


def _copy_setup_fields(fields: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    for field in fields:
        next_field = dict(field)
        if isinstance(next_field.get("options"), list):
            next_field["options"] = list(next_field["options"])
        copied.append(next_field)
    return copied


def _connector_connection_setup(item: dict[str, Any], service_baseline: dict[str, Any]) -> dict[str, Any]:
    connector_id = _normalize_source_key(item.get("id"))
    blueprint = CONNECTOR_SETUP_BLUEPRINTS.get(connector_id)
    if not blueprint:
        if str(item.get("live_status") or "").lower() == "planned":
            mode = "account-sign-in-planned"
            unavailable_reason = "managed_sign_in_not_shipped"
        elif _connector_readiness_status(item) == "export-only":
            mode = "direct-connector-needed"
            unavailable_reason = "direct_connector_needed"
        else:
            mode = _connector_primary_beta_path(item)
            unavailable_reason = "advanced_fallback_only"
        return {
            "available": False,
            "mode": mode,
            "method": None,
            "endpoint": None,
            "unavailable_reason": unavailable_reason,
            "managed_oauth_shipped": False,
            "credential_storage": "none",
            "credential_retained_on_disconnect": False,
            "disconnect_behavior": "no_live_sync_configuration",
            "common_fields": [],
            "credential_fields": [],
            "configuration_fields": [],
            "require_one_of": [],
        }

    mode = str(blueprint.get("mode") or _connector_primary_beta_path(item))
    credential_fields = _copy_setup_fields(blueprint.get("credential_fields") or [])
    configuration_fields = _copy_setup_fields(blueprint.get("configuration_fields") or [])
    managed_oauth_shipped = bool(blueprint.get("managed_oauth_shipped"))
    return {
        "available": bool(service_baseline.get("live_sync")),
        "mode": mode,
        "method": "POST",
        "endpoint": blueprint.get("endpoint"),
        "discovery_endpoint": blueprint.get("discovery_endpoint"),
        "discovery_target_field": blueprint.get("discovery_target_field"),
        "unavailable_reason": None,
        "managed_oauth_shipped": managed_oauth_shipped,
        "oauth_provider": blueprint.get("oauth_provider") if managed_oauth_shipped else None,
        "oauth_start_endpoint": blueprint.get("oauth_start_endpoint") if managed_oauth_shipped else None,
        "oauth_complete_endpoint": blueprint.get("oauth_complete_endpoint") if managed_oauth_shipped else None,
        "credential_storage": "local_vault_credentials" if service_baseline.get("live_sync") else "none",
        "credential_retained_on_disconnect": bool(service_baseline.get("live_sync")),
        "disconnect_behavior": "pause_sync_keep_local_data_and_credentials" if service_baseline.get("live_sync") else "no_live_sync_configuration",
        "default_processing": "sync",
        "default_cursor_name": blueprint.get("default_cursor_name"),
        "default_max_records": blueprint.get("default_max_records"),
        "max_records_limit": blueprint.get("max_records_limit"),
        "common_fields": _copy_setup_fields(COMMON_CONNECTOR_SETUP_FIELDS),
        "credential_fields": credential_fields,
        "configuration_fields": configuration_fields,
        "require_one_of": list(blueprint.get("require_one_of") or []),
    }


def _latest_nonempty_iso(values: Iterable[Any]) -> str | None:
    normalized = sorted(str(value or "").strip() for value in values if str(value or "").strip())
    return normalized[-1] if normalized else None


def _earliest_nonempty_iso(values: Iterable[Any]) -> str | None:
    normalized = [str(value or "").strip() for value in values if str(value or "").strip()]
    parsed_values = [
        parsed
        for parsed in (_parse_iso_timestamp(value) for value in normalized)
        if parsed is not None
    ]
    if parsed_values:
        return _isoformat_z(min(parsed_values))
    return sorted(normalized)[0] if normalized else None


def _isoformat_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _bounded_int(value: Any, *, minimum: int, maximum: int) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < minimum or parsed > maximum:
        return None
    return parsed


def _request_oauth_token(token_endpoint: str, form: dict[str, str]) -> dict[str, Any]:
    request = Request(
        token_endpoint,
        data=urlencode(form).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - caller supplies trusted OAuth token endpoint.
        return json.loads(response.read().decode("utf-8"))


def _request_basic_json_oauth_token(
    token_endpoint: str,
    payload: dict[str, Any],
    *,
    client_id: str,
    client_secret: str,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    credentials = f"{client_id}:{client_secret}".encode("utf-8")
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Basic {base64.b64encode(credentials).decode('ascii')}",
    }
    request_headers.update(headers or {})
    request = Request(
        token_endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - caller supplies trusted OAuth token endpoint.
        return json.loads(response.read().decode("utf-8"))


def _request_oauth_token_refresh(token_endpoint: str, form: dict[str, str]) -> dict[str, Any]:
    return _request_oauth_token(token_endpoint, form)


def _request_notion_oauth_token_refresh(
    token_endpoint: str,
    *,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    notion_version: str = "",
) -> dict[str, Any]:
    """Notion's token endpoint authenticates the client with HTTP Basic auth and a JSON
    body (not the form-encoded body the generic OAuth providers use), so refresh mirrors
    the authorization-code exchange in complete_managed_oauth."""
    return _request_basic_json_oauth_token(
        token_endpoint,
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
        client_id=client_id,
        client_secret=client_secret,
        headers={"Notion-Version": notion_version} if notion_version else None,
    )


def _credential_access_token_expired(payload: dict[str, Any], *, now: datetime | None = None, skew_seconds: int = 60) -> bool:
    expires_at = _parse_iso_timestamp(
        str(payload.get("access_token_expires_at") or payload.get("expires_at") or "").strip()
    )
    if not expires_at:
        return False
    return expires_at <= ((now or datetime.now(timezone.utc)) + timedelta(seconds=skew_seconds))


def _oauth_refresh_expires_at(payload: dict[str, Any], *, now: datetime) -> str | None:
    raw_expires_in = payload.get("expires_in")
    try:
        expires_in = int(raw_expires_in)
    except (TypeError, ValueError):
        expires_in = 0
    if expires_in > 0:
        return (now + timedelta(seconds=max(60, expires_in))).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    expires_at = _parse_iso_timestamp(str(payload.get("expires_at") or "").strip())
    if expires_at:
        return expires_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return None


def _oauth_refresh_credential_fields(payload: dict[str, Any]) -> dict[str, Any]:
    field_names = (
        "refresh_token",
        "token_endpoint",
        "client_id",
        "client_secret",
        "access_token_expires_at",
        "scope",
        "oauth_refreshed_at",
    )
    fields: dict[str, Any] = {}
    for key in field_names:
        value = payload.get(key)
        if value not in (None, "", []):
            fields[key] = value
    return fields


SCHEDULED_CREDENTIAL_SYNC_SOURCES = {"github", "gmail", "outlook", "google-drive", "readwise", "raindrop", "zotero", "linear", "notion", "slack", "calendar", "jira"}
OAUTH_REFRESH_CREDENTIAL_SOURCES = {"gmail", "google-drive", "outlook", "notion"}
GOOGLE_OAUTH_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_OAUTH_DEFAULT_REDIRECT_URI = "http://127.0.0.1:8766/v1/connectors/google/oauth/callback"
GOOGLE_OAUTH_CONNECTOR_SCOPES: dict[str, tuple[str, ...]] = {
    "gmail": ("https://www.googleapis.com/auth/gmail.readonly",),
    "google-drive": ("https://www.googleapis.com/auth/drive.readonly",),
}
GOOGLE_OAUTH_CONNECTOR_DEFAULTS: dict[str, dict[str, Any]] = {
    "gmail": {
        "account_label": "Gmail",
        "account_identifier": "google-gmail",
        "api_base_url": "https://gmail.googleapis.com/gmail/v1",
        "cursor_name": "messages",
    },
    "google-drive": {
        "account_label": "Google Drive",
        "account_identifier": "google-drive",
        "api_base_url": "https://www.googleapis.com/drive/v3",
        "cursor_name": "files",
    },
}
MANAGED_OAUTH_AUTHORIZATION_ENDPOINTS: dict[str, str] = {
    "notion": "https://api.notion.com/v1/oauth/authorize",
    "outlook": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
}
MANAGED_OAUTH_TOKEN_ENDPOINTS: dict[str, str] = {
    "notion": "https://api.notion.com/v1/oauth/token",
    "outlook": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
}
MANAGED_OAUTH_DEFAULT_REDIRECT_URI = "http://127.0.0.1:8766/v1/connectors/oauth/callback"
MANAGED_OAUTH_CONNECTOR_SCOPES: dict[str, tuple[str, ...]] = {
    "notion": (),
    "outlook": ("offline_access", "User.Read", "Mail.Read"),
}
MANAGED_OAUTH_CONNECTOR_DEFAULTS: dict[str, dict[str, Any]] = {
    "notion": {
        "provider": "notion",
        "account_label": "Notion",
        "account_identifier": "notion-workspace",
        "api_base_url": "https://api.notion.com/v1",
        "cursor_name": "pages",
        "notion_version": "2026-03-11",
    },
    "outlook": {
        "provider": "microsoft",
        "account_label": "Outlook",
        "account_identifier": "microsoft-outlook",
        "api_base_url": "https://graph.microsoft.com/v1.0",
        "cursor_name": "messages",
    },
}
SOURCE_SYNC_CURSOR_NAMES = {
    "obsidian": "local-folder",
    "gmail": "messages",
    "outlook": "messages",
    "google-drive": "files",
    "github": "issues",
    "slack": "messages",
    "readwise": "highlights",
    "calendar": "events",
    "raindrop": "raindrops",
    "zotero": "items",
    "linear": "issues",
    "jira": "issues",
    "notion": "pages",
}


def _default_source_sync_cursor_name(source: str) -> str:
    return SOURCE_SYNC_CURSOR_NAMES.get(_normalize_source_key(source), "default")


def _google_oauth_source(source: str) -> str:
    normalized = _normalize_source_key(source)
    if normalized in {"drive", "google-docs"}:
        normalized = "google-drive"
    if normalized not in GOOGLE_OAUTH_CONNECTOR_SCOPES:
        raise ValueError("Google OAuth is only supported for gmail and google-drive")
    return normalized


def _google_oauth_env_prefix(source: str) -> str:
    return "CORTEX_GMAIL" if source == "gmail" else "CORTEX_GOOGLE_DRIVE"


def _google_oauth_config_value(source: str, name: str, explicit: str | None = None) -> str:
    value = str(explicit or "").strip()
    if value:
        return value
    specific = os.environ.get(f"{_google_oauth_env_prefix(source)}_OAUTH_{name}", "")
    if specific.strip():
        return specific.strip()
    return os.environ.get(f"CORTEX_GOOGLE_OAUTH_{name}", "").strip()


def _google_oauth_redirect_uri(source: str, explicit: str | None = None) -> str:
    return _google_oauth_config_value(source, "REDIRECT_URI", explicit) or GOOGLE_OAUTH_DEFAULT_REDIRECT_URI


def _google_oauth_scopes(source: str, scopes: list[str] | None = None) -> list[str]:
    allowed = set(GOOGLE_OAUTH_CONNECTOR_SCOPES[source])
    requested = [str(scope or "").strip() for scope in (scopes or []) if str(scope or "").strip()]
    if not requested:
        return list(GOOGLE_OAUTH_CONNECTOR_SCOPES[source])
    disallowed = sorted(scope for scope in requested if scope not in allowed)
    if disallowed:
        raise ValueError(f"Unsupported Google OAuth scope for {source}: {', '.join(disallowed)}")
    return list(dict.fromkeys(requested))


def _managed_oauth_source(source: str) -> str:
    normalized = _normalize_source_key(source)
    if normalized not in MANAGED_OAUTH_CONNECTOR_DEFAULTS:
        raise ValueError("Managed OAuth is only supported for notion and outlook")
    return normalized


def _managed_oauth_env_prefix(source: str) -> str:
    return f"CORTEX_{source.replace('-', '_').upper()}"


def _managed_oauth_config_value(source: str, name: str, explicit: str | None = None) -> str:
    value = str(explicit or "").strip()
    if value:
        return value
    prefixes = [_managed_oauth_env_prefix(source)]
    provider = str((MANAGED_OAUTH_CONNECTOR_DEFAULTS.get(source) or {}).get("provider") or "").strip()
    if provider:
        provider_prefix = f"CORTEX_{provider.replace('-', '_').upper()}"
        if provider_prefix not in prefixes:
            prefixes.append(provider_prefix)
    for prefix in prefixes:
        specific = os.environ.get(f"{prefix}_OAUTH_{name}", "")
        if specific.strip():
            return specific.strip()
    return os.environ.get(f"CORTEX_MANAGED_OAUTH_{name}", "").strip()


def _managed_oauth_redirect_uri(source: str, explicit: str | None = None) -> str:
    return _managed_oauth_config_value(source, "REDIRECT_URI", explicit) or MANAGED_OAUTH_DEFAULT_REDIRECT_URI


def _managed_oauth_scopes(source: str, scopes: list[str] | None = None) -> list[str]:
    allowed = set(MANAGED_OAUTH_CONNECTOR_SCOPES[source])
    requested = [str(scope or "").strip() for scope in (scopes or []) if str(scope or "").strip()]
    if not requested:
        return list(MANAGED_OAUTH_CONNECTOR_SCOPES[source])
    if allowed:
        disallowed = sorted(scope for scope in requested if scope not in allowed)
        if disallowed:
            raise ValueError(f"Unsupported managed OAuth scope for {source}: {', '.join(disallowed)}")
    return list(dict.fromkeys(requested))


def _decode_oauth_jwt_payload(token: str) -> dict[str, Any]:
    pieces = str(token or "").split(".")
    if len(pieces) < 2:
        return {}
    payload = pieces[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{payload}{padding}".encode("ascii"))
        parsed = json.loads(decoded.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _microsoft_oauth_claims(payload: dict[str, Any]) -> dict[str, Any]:
    claims = payload.get("claims") if isinstance(payload.get("claims"), dict) else {}
    if claims:
        return claims
    id_token_claims = _decode_oauth_jwt_payload(str(payload.get("id_token") or ""))
    return id_token_claims if id_token_claims else {}


def _microsoft_oauth_account_identifier(payload: dict[str, Any]) -> str:
    claims = _microsoft_oauth_claims(payload)
    for key in ("preferred_username", "email", "upn", "unique_name"):
        value = str(claims.get(key) or payload.get(key) or "").strip()
        if value:
            return value[:240]
    oid = str(claims.get("oid") or claims.get("sub") or payload.get("account_identifier") or "").strip()
    return oid[:240]


def _microsoft_oauth_account_label(payload: dict[str, Any]) -> str:
    claims = _microsoft_oauth_claims(payload)
    name = str(claims.get("name") or payload.get("name") or "").strip()
    identifier = _microsoft_oauth_account_identifier(payload)
    if name and identifier:
        return f"Outlook: {name}"[:160]
    if identifier:
        return f"Outlook: {identifier}"[:160]
    return ""


def _source_sync_interval_seconds(
    active_accounts: list[dict[str, Any]],
    source_cursors: list[dict[str, Any]],
    *,
    default: int = 1800,
) -> int:
    candidates: list[int] = []
    for account in active_accounts:
        metadata = account.get("metadata") or {}
        seconds = _bounded_int(metadata.get("sync_interval_seconds"), minimum=60, maximum=86_400)
        if seconds is not None:
            candidates.append(seconds)
        minutes = _bounded_int(metadata.get("sync_interval_minutes"), minimum=1, maximum=1_440)
        if minutes is not None:
            candidates.append(minutes * 60)
    for cursor in source_cursors:
        state = cursor.get("state") or {}
        seconds = _bounded_int(state.get("sync_interval_seconds"), minimum=60, maximum=86_400)
        if seconds is not None:
            candidates.append(seconds)
        minutes = _bounded_int(state.get("sync_interval_minutes"), minimum=1, maximum=1_440)
        if minutes is not None:
            candidates.append(minutes * 60)
    return min(candidates) if candidates else default


def _source_account_sync_scheduler_supported(account: dict[str, Any]) -> bool:
    source = _normalize_source_key(str(account.get("source") or ""))
    metadata = account.get("metadata") if isinstance(account.get("metadata"), dict) else {}
    if source == "obsidian" and str(metadata.get("vault_path") or "").strip():
        return True
    if source in SCHEDULED_CREDENTIAL_SYNC_SOURCES and str(metadata.get("credential_ref") or "").startswith("source_credential:"):
        return True
    if source == "zotero" and str(metadata.get("api_base_url") or "").strip():
        return True
    return False


def _source_sync_scheduler_supported(active_accounts: list[dict[str, Any]]) -> bool:
    return any(_source_account_sync_scheduler_supported(account) for account in active_accounts)


def _source_credential_ref(account_id: str) -> str:
    return f"source_credential:{account_id}"


def _with_source_credential_ref(metadata: dict[str, Any], account_id: str, enabled: bool = True) -> dict[str, Any]:
    if not enabled:
        return metadata
    return {**metadata, "credential_ref": _source_credential_ref(account_id)}


def _source_account_metadata_secret_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key or "").strip().lower()).strip("_")
    if not normalized or normalized in SOURCE_ACCOUNT_METADATA_SAFE_SECRET_LIKE_KEYS:
        return False
    return bool(SOURCE_ACCOUNT_METADATA_SECRET_KEY_PATTERN.search(normalized))


def _sanitize_source_account_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key or "").strip()
            if not key_text or _source_account_metadata_secret_key(key_text):
                continue
            sanitized[key_text] = _sanitize_source_account_metadata(child)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_source_account_metadata(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_source_account_metadata(item) for item in value]
    if isinstance(value, str):
        redacted = value
        for pattern, replacement in SOURCE_ACCOUNT_METADATA_SECRET_VALUE_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        return redacted
    return value


def _connector_failure_summary(errors: list[dict[str, Any]] | None, *, now: datetime | None = None) -> dict[str, Any] | None:
    normalized_errors = [error for error in (errors or []) if isinstance(error, dict)]
    if not normalized_errors:
        return None
    now = now or datetime.now(timezone.utc)
    first = normalized_errors[0]
    category = str(first.get("category") or "").strip() or "network"
    summary: dict[str, Any] = {
        "category": category,
        "error": str(first.get("error") or "Connector sync failed").strip()[:500],
        "error_count": len(normalized_errors),
    }
    status_code = _bounded_int(first.get("status_code"), minimum=100, maximum=599)
    if status_code is not None:
        summary["status_code"] = status_code
    retry_after = _earliest_nonempty_iso(
        [error.get("retry_after") for error in normalized_errors if isinstance(error, dict)]
    )
    if not retry_after and category in {"rate_limited", "server", "timeout", "network"}:
        retry_after = _isoformat_z(now + timedelta(seconds=900))
    if retry_after:
        summary["retry_after"] = retry_after
    return summary


def _with_connector_failure_metadata(metadata: dict[str, Any], errors: list[dict[str, Any]] | None, *, now: datetime | None = None) -> dict[str, Any]:
    updated = dict(metadata or {})
    summary = _connector_failure_summary(errors, now=now)
    if summary:
        updated["last_connector_failure"] = summary
        if summary.get("retry_after"):
            updated["retry_after"] = summary["retry_after"]
    else:
        updated.pop("last_connector_failure", None)
        updated.pop("retry_after", None)
    return updated


def _source_sync_due_at(last_completed_at: str | None, *, interval_seconds: int) -> str | None:
    completed = _parse_iso_timestamp(last_completed_at)
    if completed is None:
        return None
    return _isoformat_z(completed + timedelta(seconds=interval_seconds))


def _timestamp_due(value: str | None, *, now: datetime) -> bool:
    parsed = _parse_iso_timestamp(value)
    return bool(parsed and parsed <= now)


def _timestamp_in_future(value: str | None, *, now: datetime) -> bool:
    parsed = _parse_iso_timestamp(value)
    return bool(parsed and parsed > now)


def _source_readiness_sync_plan(
    *,
    service_baseline: dict[str, Any],
    live_status: str,
    active_accounts: list[dict[str, Any]],
    source_cursors: list[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if service_baseline.get("hosted_managed_sync"):
        mode = "hosted_managed_sync"
    elif service_baseline.get("local_app_autosync"):
        mode = "local_app_autosync"
    elif live_status == "planned":
        mode = "planned_account_sync"
    elif service_baseline.get("manual_direct_sync"):
        mode = "manual_direct_sync"
    else:
        mode = "direct_connector_needed"

    first_account = active_accounts[0] if active_accounts else None
    account_errors = [account.get("last_error") for account in active_accounts if account.get("last_error")]
    cursor_errors = [cursor.get("last_error") for cursor in source_cursors if cursor.get("last_error")]
    last_attempt_at = _latest_nonempty_iso(
        [cursor.get("last_started_at") for cursor in source_cursors]
        + [cursor.get("updated_at") for cursor in source_cursors]
        + [account.get("updated_at") for account in active_accounts]
    )
    last_completed_at = _latest_nonempty_iso(
        [cursor.get("last_completed_at") for cursor in source_cursors]
        + [account.get("last_sync_at") for account in active_accounts]
    )
    interval_seconds = _source_sync_interval_seconds(active_accounts, source_cursors)
    explicit_next_sync_due_at = _earliest_nonempty_iso(
        [(account.get("metadata") or {}).get("next_sync_due_at") for account in active_accounts]
        + [(cursor.get("state") or {}).get("next_sync_due_at") for cursor in source_cursors]
    )
    next_sync_due_at = explicit_next_sync_due_at
    if not next_sync_due_at and active_accounts and mode in {"hosted_managed_sync", "local_app_autosync"}:
        next_sync_due_at = _source_sync_due_at(last_completed_at, interval_seconds=interval_seconds)
    retry_after = _latest_nonempty_iso(
        [(cursor.get("state") or {}).get("retry_after") for cursor in source_cursors]
        + [(account.get("metadata") or {}).get("retry_after") for account in active_accounts]
    )
    backing_off = _timestamp_in_future(retry_after, now=now)
    scheduler_supported = _source_sync_scheduler_supported(active_accounts)
    due_now = (
        bool(active_accounts)
        and mode in {"hosted_managed_sync", "local_app_autosync"}
        and scheduler_supported
        and not backing_off
        and (_timestamp_due(next_sync_due_at, now=now) if next_sync_due_at else not bool(last_completed_at))
    )

    if backing_off:
        managed_sync_status = "backing_off"
    elif account_errors or cursor_errors:
        managed_sync_status = "needs_attention"
    elif due_now:
        managed_sync_status = "due"
    elif mode == "planned_account_sync":
        managed_sync_status = "planned"
    elif last_completed_at:
        managed_sync_status = "healthy"
    elif active_accounts and mode in {"hosted_managed_sync", "local_app_autosync"}:
        managed_sync_status = "waiting_for_first_sync"
    elif mode == "manual_direct_sync":
        managed_sync_status = "available_advanced"
    elif mode == "direct_connector_needed":
        managed_sync_status = "connector_needed"
    else:
        managed_sync_status = "not_configured"

    return {
        "mode": mode,
        "credential_ref": f"source_account:{first_account['id']}" if first_account else None,
        "hosted_credential_ref": None,
        "managed_sync_status": managed_sync_status,
        "next_sync_due_at": next_sync_due_at,
        "sync_interval_seconds": interval_seconds,
        "due_now": due_now,
        "scheduler_supported": scheduler_supported,
        "blocked_reason": None if scheduler_supported or not active_accounts else "stored_sync_configuration_required",
        "last_attempt_at": last_attempt_at,
        "last_completed_at": last_completed_at,
        "retry_after": retry_after,
    }


def _connector_readiness_status(item: dict[str, Any]) -> str:
    live_status = str(item.get("live_status") or "").lower()
    if live_status == "planned":
        return "live-planned"
    if live_status == "api_token":
        return "token-ready"
    if live_status in {"import_ready", "local_api", "local_only", "imported"}:
        return "import-ready"
    return "export-only"


def _connector_permission_requirements(item: dict[str, Any]) -> list[str]:
    explicit = _unique_catalog_strings(item.get("permissions_required") or [])
    if explicit:
        return explicit

    auth = str(item.get("auth") or "").lower()
    scopes = _unique_catalog_strings(item.get("scopes") or [])
    live_status = str(item.get("live_status") or "").lower()
    requirements: list[str]
    if auth == "local_folder":
        requirements = ["First-100: local app access when explicitly connected."]
    elif auth == "local_file":
        requirements = ["First-100: local app data access when explicitly connected."]
    elif auth == "file":
        requirements = ["First-100: local app connection or recovery intake."]
    elif auth == "export":
        requirements = ["First-100: direct connector required for the primary path."]
    elif auth == "api_token":
        requirements = ["First-100: read-only account token required for direct sync."]
    elif auth == "oauth":
        blueprint = CONNECTOR_SETUP_BLUEPRINTS.get(_normalize_source_key(item.get("id"))) or {}
        if blueprint.get("managed_oauth_shipped"):
            requirements = ["First-100: browser account sign-in for read-only sync."]
        else:
            requirements = ["First-100: account sign-in planned."]
    else:
        requirements = ["First-100: connected source data."]

    if live_status == "planned" and scopes:
        requirements.append(f"Live-planned: account consent for {', '.join(scopes)}.")
    return requirements


def _connector_first_100_note(item: dict[str, Any]) -> str:
    explicit = str(item.get("first_100_note") or "").strip()
    if explicit:
        return explicit

    note = str(item.get("notes") or "").strip()
    connector_id = _normalize_source_key(item.get("id"))
    live_status = str(item.get("live_status") or "").lower()
    readiness_status = _connector_readiness_status(item)
    managed_oauth_shipped = bool((CONNECTOR_SETUP_BLUEPRINTS.get(connector_id) or {}).get("managed_oauth_shipped"))
    if _connector_primary_beta(item):
        prefix = "First-100: native local sync is the primary source path."
    elif connector_id in BASELINE_10K_CONNECTOR_IDS and live_status in {"local_api", "local_only"}:
        prefix = "First-100: native local sync is available from connector settings; not primary UI."
    elif managed_oauth_shipped:
        prefix = "First-100: browser sign-in sync is available from Connections & Privacy; not primary UI."
    elif readiness_status == "live-planned":
        prefix = "First-100: account sign-in is the intended source path; recovery intake is not primary."
    elif readiness_status == "token-ready":
        prefix = "First-100: read-only token sync is available from advanced connector settings; not primary UI."
    elif readiness_status == "import-ready":
        prefix = "First-100: connect through a direct local integration when available."
    else:
        prefix = "First-100: needs a direct connector before becoming a primary source."
    return f"{prefix} {note}".strip()


def _connector_primary_beta(item: dict[str, Any]) -> bool:
    return str(item.get("id") or "").strip().lower() in PRIMARY_BETA_CONNECTOR_IDS


def _connector_primary_beta_path(item: dict[str, Any]) -> str:
    if _connector_primary_beta(item):
        return "native-local-connector"
    connector_id = _normalize_source_key(item.get("id"))
    live_status = str(item.get("live_status") or "").lower()
    if connector_id in BASELINE_10K_CONNECTOR_IDS and live_status in {"local_api", "local_only"}:
        return "native-local-connector"
    readiness_status = _connector_readiness_status(item)
    if readiness_status == "live-planned":
        return "account-sign-in-planned"
    if readiness_status == "token-ready":
        return "native-token-connector"
    if readiness_status == "import-ready":
        return "advanced-fallback-only"
    return "direct-connector-needed"


def _connector_beta_status(item: dict[str, Any]) -> str:
    if _connector_primary_beta(item):
        return "ready"
    connector_id = _normalize_source_key(item.get("id"))
    live_status = str(item.get("live_status") or "").lower()
    if connector_id in BASELINE_10K_CONNECTOR_IDS and live_status in {"local_api", "local_only"}:
        return "ready"
    readiness_status = _connector_readiness_status(item)
    if readiness_status == "token-ready":
        return "ready"
    if readiness_status == "live-planned":
        return "planned"
    if readiness_status == "import-ready":
        return "advanced-fallback"
    return "needs-connector"


def _planned_connector_without_native_sync(item: dict[str, Any] | None) -> bool:
    if not item or _connector_primary_beta(item):
        return False
    return _connector_readiness_status(item) == "live-planned"


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


COMMUNICATION_IDENTITY_SOURCES = {
    "email",
    "gmail",
    "microsoft-365",
    "slack",
    "google-chat",
    "teams",
    "discord",
    "telegram",
    "messages",
    "whatsapp",
    "linkedin",
    "twitter-x",
}


def _source_account_alias_sources(source: str) -> set[str]:
    normalized = _normalize_source_key(source)
    if not normalized:
        return set()
    matches = {normalized}
    for connector, metadata in SOURCE_CONNECTOR_IMPORT_METADATA.items():
        source_ids = {_normalize_source_key(item) for item in metadata.get("source_ids") or []}
        source_aliases = {_normalize_source_key(item) for item in metadata.get("source_aliases") or []}
        if normalized == connector or normalized in source_ids or normalized in source_aliases:
            matches.add(_normalize_source_key(connector))
            matches.update(item for item in source_ids if item)
            matches.update(item for item in source_aliases if item)
    return {item for item in matches if item}


def _retrieval_source_filter_sources(source: str) -> set[str]:
    normalized = _normalize_source_key(source)
    if not normalized:
        return set()
    matches = {normalized}
    direct_metadata = SOURCE_CONNECTOR_IMPORT_METADATA.get(normalized) or {}
    matches.update(
        _normalize_source_key(item)
        for item in direct_metadata.get("source_aliases") or []
        if _normalize_source_key(item)
    )
    for connector, metadata in SOURCE_CONNECTOR_IMPORT_METADATA.items():
        source_ids = {_normalize_source_key(item) for item in metadata.get("source_ids") or []}
        source_aliases = {_normalize_source_key(item) for item in metadata.get("source_aliases") or []}
        if normalized in source_ids or normalized in source_aliases:
            matches.add(_normalize_source_key(connector))
    return {item for item in matches if item}


def _source_account_identity_values(account: dict[str, Any], source: str) -> list[str]:
    values: list[Any] = [account.get("account_identifier")]
    metadata = account.get("metadata") if isinstance(account.get("metadata"), dict) else {}
    for key in (
        "user_id",
        "service_user_id",
        "slack_user_id",
        "email",
        "email_address",
        "account_email",
        "user_email",
        "username",
        "user_name",
        "handle",
        "screen_name",
        "real_name",
        "display_name",
        "name",
    ):
        value = metadata.get(key)
        if isinstance(value, (list, tuple, set)):
            values.extend(value)
        else:
            values.append(value)
    if source in COMMUNICATION_IDENTITY_SOURCES:
        label = str(account.get("account_label") or "").strip()
        connector_name = next((item["name"] for item in SOURCE_CONNECTOR_CATALOG if item["id"] == source), "")
        generic_labels = {
            source.lower(),
            connector_name.lower(),
            "email",
            "email files",
            "slack",
            "gmail",
            "microsoft 365",
        }
        if label and label.lower() not in generic_labels:
            values.append(label)
    return _normalize_identity_aliases(values)


def normalize_token_scopes(scopes: list[str] | tuple[str, ...] | str | None) -> list[str]:
    if scopes is None:
        values = list(DEFAULT_MCP_TOKEN_SCOPES)
    elif isinstance(scopes, str):
        values = [item.strip().lower() for item in scopes.split(",")]
    else:
        values = [str(item).strip().lower() for item in scopes]
    return sorted({scope for scope in values if scope in MCP_TOKEN_SCOPES})


def _sync_cursor_offset(value: Any) -> int | None:
    match = re.search(r"(?:^|;)offset=(\d+);", str(value or ""))
    return int(match.group(1)) if match else None


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_seconds(value: str | None, *, now: datetime) -> int | None:
    parsed = _parse_iso_timestamp(value)
    if parsed is None:
        return None
    return max(0, int((now - parsed).total_seconds()))


def _duration_seconds(start: str | None, end: str | None) -> float | None:
    started = _parse_iso_timestamp(start)
    ended = _parse_iso_timestamp(end)
    if started is None or ended is None:
        return None
    return max(0.0, (ended - started).total_seconds())


def _nearest_rank_percentile(ordered: list[float], pct: float) -> float:
    if not ordered:
        return 0.0
    rank = max(1, int(math.ceil((pct / 100.0) * len(ordered))))
    return ordered[min(rank, len(ordered)) - 1]


def _percentile_summary(values: list[float]) -> dict[str, float | int]:
    """p50/p95/p99/max nearest-rank summary of a list of seconds (0 when empty)."""
    ordered = sorted(value for value in values if value is not None)
    if not ordered:
        return {"count": 0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    return {
        "count": len(ordered),
        "p50": round(_nearest_rank_percentile(ordered, 50), 3),
        "p95": round(_nearest_rank_percentile(ordered, 95), 3),
        "p99": round(_nearest_rank_percentile(ordered, 99), 3),
        "max": round(ordered[-1], 3),
    }


def memory_layer(kind: str | None, value: str | None = None) -> str:
    explicit = (value or "").strip().lower()
    if explicit in MEMORY_LAYERS:
        return explicit
    return MEMORY_LAYER_BY_KIND.get((kind or "").strip().lower(), "semantic")


def _memory_raw_excerpt(record: dict[str, Any], raw_text: str) -> str:
    explicit = str(record.get("raw_excerpt") or record.get("source_excerpt") or "").strip()
    if explicit:
        return explicit[:500]
    content = str(record.get("content") or "").strip()
    if content:
        line = _matching_source_line(raw_text, content)
        return (line or content)[:500]
    return raw_text[:500]


def _memory_sector(record: dict[str, Any], source_url: str | None, source_account: dict[str, Any] | None = None) -> str:
    explicit = str(record.get("sector") or record.get("workspace") or "").strip()
    if explicit:
        return explicit[:120]
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    for key in ("sector", "workspace", "project", "vault_name"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value[:120]
    if source_account:
        account_metadata = source_account.get("metadata") if isinstance(source_account.get("metadata"), dict) else {}
        for key in ("workspace", "project", "vault_name"):
            value = str(account_metadata.get(key) or "").strip()
            if value:
                return value[:120]
        label = str(source_account.get("account_label") or "").strip()
        if label and label.lower() not in {"obsidian", "gmail", "slack", "notion"}:
            return label[:120]
    if source_url:
        split = urlsplit(source_url)
        query = split.query or ""
        for key in ("workspace", "project", "vault", "team"):
            match = re.search(rf"(?:^|&){key}=([^&]+)", query)
            if match:
                return unquote(match.group(1))[:120]
        if split.scheme == "file":
            parts = [part for part in unquote(split.path).split("/") if part]
            if len(parts) >= 2:
                return parts[-2][:120]
    return ""


def _normalize_sector_filter(value: str | None) -> str:
    return str(value or "").strip()[:120]


def _memory_source_type(source: str, source_url: str | None) -> str:
    if source_url:
        scheme = urlsplit(source_url).scheme.lower()
        if scheme in {"file", "local-file"}:
            return "local_file"
        if scheme:
            return "service"
        if str(source_url).startswith("/"):
            return "local_file"
    normalized = _normalize_source_key(source)
    if normalized in LOCAL_FILE_MEMORY_SOURCE_KEYS:
        return "local_file"
    if normalized in SERVICE_MEMORY_SOURCE_KEYS:
        return "service"
    return "manual" if normalized in {"macos", "unit-test"} else "connector"


def _memory_provenance(
    record: dict[str, Any],
    *,
    source: str,
    source_url: str | None,
    capture_id: str,
    source_account: dict[str, Any] | None,
    external_id: str | None,
) -> dict[str, Any]:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    provenance = {
        "source": source,
        "source_url": source_url,
        "capture_id": capture_id,
        "source_account_id": (source_account or {}).get("id"),
        "external_id": external_id,
        "record_metadata": metadata,
    }
    source_account_policy = (source_account or {}).get("policy") if isinstance((source_account or {}).get("policy"), dict) else {}
    if source_account_policy:
        provenance["source_account_policy"] = {
            key: source_account_policy[key]
            for key in ("mode", "allow_ai_context", "review_required")
            if key in source_account_policy
        }
    return {key: value for key, value in provenance.items() if value not in (None, "", {}, [])}


def _apply_source_record_metadata(extracted: dict[str, Any], metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return extracted
    source_topics = _source_record_metadata_topics(metadata)
    line_offset = _source_record_line_offset(metadata)
    for record in extracted.get("records", []) or []:
        existing_metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        record["metadata"] = {**metadata, **existing_metadata}
        if line_offset:
            record["source_line_offset"] = line_offset
        if source_topics:
            existing_topics = [str(topic) for topic in (record.get("topics") or []) if str(topic).strip()]
            record["topics"] = _unique_preserving_order([*existing_topics, *source_topics])[:12]
    return extracted


SOURCE_RECORD_REFRESH_METADATA_KEYS = {
    "relative_path",
    "record_scope",
    "note_external_id",
    "block_id",
    "section_title",
    "section_slug",
    "tags",
    "wikilinks",
    "workspace",
    "project",
    "vault_name",
    "team",
    "page",
    "document",
    "message",
    "row",
    "event",
    "subject",
    "conversation",
    "channel",
    "repository",
    "issue",
    "url",
    "canvas_node_id",
    "canvas_node_type",
    "canvas_node_edges",
}


def _source_record_refresh_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    projected: dict[str, Any] = {}
    for key in sorted(SOURCE_RECORD_REFRESH_METADATA_KEYS):
        value = metadata.get(key)
        if value not in (None, "", [], {}):
            projected[key] = value
    return projected


def _source_record_line_offset(metadata: dict[str, Any]) -> int:
    try:
        line_start = int(metadata.get("line_start") or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, line_start - 1)


def _source_record_metadata_topics(metadata: dict[str, Any]) -> list[str]:
    topics: list[str] = []
    for tag in metadata.get("tags") or []:
        topics.append(str(tag).replace("/", " ").replace("-", " ").strip())
    relative_path = str(metadata.get("relative_path") or "").strip()
    if relative_path:
        topics.append(Path(relative_path).stem)
        parent = str(Path(relative_path).parent)
        if parent and parent != ".":
            topics.append(parent.replace("/", " "))
    for link in metadata.get("wikilinks") or []:
        if not isinstance(link, dict):
            continue
        for key in ("display", "target"):
            value = str(link.get(key) or "").strip()
            if value:
                topics.append(value.rsplit("/", 1)[-1])
    for key in ("workspace", "project", "vault_name", "team", "repository", "channel", "page", "document"):
        value = str(metadata.get(key) or "").strip()
        if value:
            topics.append(value)
            if key == "repository" and "/" in value:
                topics.extend(part.strip() for part in value.split("/") if part.strip())
    return _unique_preserving_order(topic for topic in topics if topic)[:12]


def _normalize_obsidian_wikilink_target(value: str) -> str:
    target = str(value or "").strip().replace("\\", "/")
    target = target.split("#", 1)[0].strip()
    while target.startswith("./"):
        target = target[2:]
    return target.strip("/")


def _obsidian_wikilink_candidate_paths(value: str) -> list[str]:
    target = _normalize_obsidian_wikilink_target(value)
    if not target:
        return []
    candidates = [target]
    suffix = Path(target).suffix.lower()
    if not suffix:
        candidates.extend(f"{target}.{extension}" for extension in ("md", "markdown", "txt"))
    return _unique_preserving_order(candidate.lower() for candidate in candidates if candidate)


def _obsidian_wikilink_target_basename(value: str) -> str:
    target = _normalize_obsidian_wikilink_target(value)
    if not target:
        return ""
    return Path(target).stem.strip()


def _unique_preserving_order(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "").strip())
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        unique.append(text)
    return unique


def _memory_duplicate_key(value: str) -> str:
    text = re.sub(r"https?://\S+|www\.\S+", " ", value.casefold())
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.\w+\b", " ", text)
    text = re.sub(r"^\s*(?:decision|summary|preference|procedure|process|note)\s*:\s*", " ", text)
    text = re.sub(r"^\s*(?:we|i)\s+(?:decided|chose|agreed)\s+(?:that\s+)?", " ", text)
    text = re.sub(r"^\s*(?:we|i)\s+(?:prefer|like|need|want)\s+(?:that\s+)?", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    words = [word for word in text.split() if word]
    if len(words) < 6:
        return ""
    return " ".join(words)


def _granular_memory_source_url(source_url: str | None, raw_text: str, excerpt: str, *, enabled: bool, line_offset: int = 0) -> str | None:
    if not enabled or not source_url:
        return source_url
    locator = _source_locator_for_excerpt(raw_text, excerpt)
    if not locator:
        return source_url
    if line_offset and str(locator.get("line") or "").isdigit():
        locator["line"] = str(int(locator["line"]) + max(0, line_offset))
    return _append_source_locator(source_url, locator)


def _matching_source_line(raw_text: str, excerpt: str) -> str:
    normalized_excerpt = _locator_text_key(excerpt)
    if not normalized_excerpt:
        return ""
    best_line = ""
    best_score = 0
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        normalized_line = _locator_text_key(stripped)
        if normalized_excerpt in normalized_line:
            return stripped
        if normalized_line in normalized_excerpt and len(normalized_line) > best_score:
            best_line = stripped
            best_score = len(normalized_line)
    return best_line


def _source_locator_for_excerpt(raw_text: str, excerpt: str) -> dict[str, str]:
    normalized_excerpt = _locator_text_key(excerpt)
    if not normalized_excerpt:
        return {}
    lines = raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    matched_index = -1
    best_score = 0
    for index, line in enumerate(lines):
        normalized_line = _locator_text_key(line)
        if not normalized_line:
            continue
        if normalized_excerpt in normalized_line:
            matched_index = index
            break
        if normalized_line in normalized_excerpt and len(normalized_line) > best_score:
            matched_index = index
            best_score = len(normalized_line)
    if matched_index < 0:
        return {}

    locator: dict[str, str] = {"line": str(matched_index + 1)}
    line = lines[matched_index].strip()
    block = _nearest_source_block(lines, matched_index)
    if block:
        locator.update(block)
    timestamp = _line_timestamp(line)
    if timestamp and "message" in locator:
        locator["message_at"] = timestamp[:40]
    locator["excerpt"] = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()[:10]
    return locator


def _nearest_source_block(lines: list[str], matched_index: int) -> dict[str, str]:
    marker_index = -1
    marker = ""
    for index in range(matched_index, -1, -1):
        stripped = lines[index].strip().lower()
        if re.fullmatch(r"-{2,}\s*[^-]+\s*-{2,}", stripped):
            marker_index = index
            marker = stripped.strip("- ").lower()
            break

    for index in range(matched_index, max(marker_index, matched_index - 12), -1):
        row_match = re.match(r"^row\s+(\d+)\b", lines[index].strip(), flags=re.IGNORECASE)
        if row_match:
            return {"row": row_match.group(1)}

    if marker_index < 0:
        return {}
    key = {
        "messages": "message",
        "direct messages": "message",
        "tweets": "tweet",
        "events": "event",
        "contacts": "contact",
        "bookmarks": "bookmark",
        "recent visits": "visit",
    }.get(marker)
    if not key:
        return {}
    single_line_items = key in {"message", "tweet", "bookmark", "visit"}
    index = _block_item_index(lines, marker_index + 1, matched_index, single_line_items=single_line_items)
    return {key: str(index)} if index else {}


def _block_item_index(lines: list[str], start: int, matched_index: int, *, single_line_items: bool) -> int:
    if single_line_items:
        return sum(1 for line in lines[start : matched_index + 1] if line.strip())
    count = 0
    in_item = False
    for line in lines[start : matched_index + 1]:
        stripped = line.strip()
        if not stripped:
            in_item = False
            continue
        if not in_item:
            count += 1
            in_item = True
    return count


def _line_timestamp(line: str) -> str:
    timestamp = re.match(r"^(\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?)\b", line)
    if timestamp:
        return timestamp.group(1)
    slack_ts = re.match(r"^(\d{10}(?:\.\d+)?)\b", line)
    if slack_ts:
        return slack_ts.group(1)
    bracketed = re.match(r"^\[([^\]]{6,60})\]", line)
    if bracketed:
        return bracketed.group(1)
    return ""


def _append_source_locator(source_url: str, locator: dict[str, str]) -> str:
    if re.search(r"(?:[#?&])line=", source_url):
        return source_url
    fragment = "&".join(
        f"{quote(str(key), safe='')}={quote(str(value), safe='')}"
        for key, value in locator.items()
        if str(value).strip()
    )
    if not fragment:
        return source_url
    separator = "&" if ("#" in source_url or "?" in source_url) else "#"
    return f"{source_url}{separator}{fragment}"


def _locator_text_key(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


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


def query_temporal_prefixes(query: str) -> list[str]:
    lowered = str(query or "").lower()
    prefixes: list[str] = []
    seen: set[str] = set()

    def add(year: str | int, month: str | int | None = None, day: str | int | None = None) -> None:
        try:
            year_value = int(year)
        except (TypeError, ValueError):
            return
        if year_value < 1900 or year_value > 2099:
            return
        try:
            month_value = int(month) if month is not None else None
            day_value = int(day) if day is not None else None
        except (TypeError, ValueError):
            return
        if month_value is None:
            prefix = f"{year_value:04d}"
        elif day_value is None:
            if month_value < 1 or month_value > 12:
                return
            prefix = f"{year_value:04d}-{month_value:02d}"
        else:
            try:
                datetime(year_value, month_value, day_value)
            except ValueError:
                return
            prefix = f"{year_value:04d}-{month_value:02d}-{day_value:02d}"
        if prefix not in seen:
            prefixes.append(prefix)
            seen.add(prefix)

    for match in re.finditer(r"\b((?:19|20)\d{2})[-/](0?[1-9]|1[0-2])(?:[-/](0?[1-9]|[12]\d|3[01]))?\b", lowered):
        add(match.group(1), match.group(2), match.group(3))
    for match in re.finditer(rf"\b({QUERY_MONTH_PATTERN})\.?\s+([0-3]?\d)(?:st|nd|rd|th)?[,]?\s+((?:19|20)\d{{2}})\b", lowered):
        add(match.group(3), QUERY_MONTHS.get(match.group(1)), match.group(2))
    for match in re.finditer(rf"\b([0-3]?\d)(?:st|nd|rd|th)?\s+({QUERY_MONTH_PATTERN})\.?[,]?\s+((?:19|20)\d{{2}})\b", lowered):
        add(match.group(3), QUERY_MONTHS.get(match.group(2)), match.group(1))
    for match in re.finditer(rf"\b({QUERY_MONTH_PATTERN})\.?\s+((?:19|20)\d{{2}})\b", lowered):
        add(match.group(2), QUERY_MONTHS.get(match.group(1)))
    for match in re.finditer(r"\b((?:19|20)\d{2})\b", lowered):
        add(match.group(1))
    return sorted(prefixes, key=lambda value: (-len(value), value))


def query_non_temporal_terms(query: str, *, limit: int = 6) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[a-z0-9_]+", str(query or "").lower()):
        if token in QUERY_TEMPORAL_STOPWORDS or token in QUERY_MONTHS:
            continue
        if re.fullmatch(r"(?:19|20)\d{2}", token):
            continue
        if len(token) < 3:
            continue
        if token not in seen:
            terms.append(token)
            seen.add(token)
        if len(terms) >= limit:
            break
    return terms


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
                        "source_account_id": row["source_account_id"] if "source_account_id" in row.keys() else None,
                        "external_id": row["external_id"] if "external_id" in row.keys() else None,
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

    def authenticate_api_token(self, token: str, user_id: str | None = None) -> dict[str, Any] | None:
        return self._authenticate_token(token, audience="api", user_id=user_id)

    def authenticate_mcp_token(self, token: str, user_id: str | None = None) -> dict[str, Any] | None:
        return self._authenticate_token(token, audience="mcp", user_id=user_id)

    def _authenticate_token(self, token: str, *, audience: str, user_id: str | None = None) -> dict[str, Any] | None:
        normalized = token.strip()
        if not normalized:
            return None
        timestamp = now_iso()
        filters = ["audience = ?", "revoked_at IS NULL"]
        values: list[Any] = [audience]
        if user_id:
            filters.append("user_id = ?")
            values.append(user_id)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT token_id, user_id, label, audience, token_salt, token_hash, scopes_json, created_at, last_used_at
                FROM api_tokens
                WHERE {" AND ".join(filters)}
                ORDER BY created_at DESC
                """,
                tuple(values),
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
        captured_at: str | None = None,
        source_account_id: str | None = None,
        external_id: str | None = None,
        record_metadata: dict[str, Any] | None = None,
        refresh_key: str | None = None,
        capture_id_override: str | None = None,
    ) -> dict[str, Any]:
        content = content.strip()
        if not content:
            raise ValueError("content is required")
        if len(content) > 200_000:
            raise ValueError("content is too large")
        captured_at = (captured_at or "").strip() or now_iso()
        normalized_source = (source or "macos")[:80]
        normalized_source_account_id = (source_account_id or "").strip() or None
        normalized_external_id = (external_id or "").strip()[:240] or None
        override_id = str(capture_id_override or "").strip()
        if override_id.startswith("cap_"):
            capture_id = override_id[:80]
        elif normalized_source_account_id and normalized_external_id:
            capture_id = stable_id("cap_", f"{user_id}:{normalized_source_account_id}:{normalized_external_id}")
        else:
            capture_id = stable_id("cap_", user_id + normalized_source + captured_at + content[:120])
        raw_hash = stable_id("", content)
        summary = "Queued for memory extraction."
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            review_status = "pending" if user_settings["review_new_captures"] else "approved"
            approved_at = None if review_status == "pending" else captured_at
            stable_source_record = bool(normalized_source_account_id and normalized_external_id)
            existing_capture = conn.execute(
                "SELECT id, raw_hash, review_status, approved_at FROM captures WHERE user_id = ? AND id = ?",
                (user_id, capture_id),
            ).fetchone()
            if existing_capture and existing_capture["raw_hash"] != raw_hash:
                purged = (
                    self._archive_capture_derivatives_in_conn(conn, user_id, capture_id, timestamp=captured_at)
                    if stable_source_record
                    else self._purge_capture_derivatives_in_conn(conn, user_id, capture_id)
                )
                self._event(
                    conn,
                    user_id,
                    capture_id,
                    "capture",
                    "replaced",
                    {
                        "source": normalized_source,
                        "source_account_id": normalized_source_account_id,
                        "external_id": normalized_external_id,
                        "memory_count": purged["memory_count"],
                        "task_count": purged["task_count"],
                        "edge_count": purged["edge_count"],
                    },
                )
            elif existing_capture and stable_source_record and existing_capture["review_status"] != "archived":
                review_status = existing_capture["review_status"]
                approved_at = existing_capture["approved_at"]
            normalized_refresh_key = str(refresh_key or "").strip()[:120]
            unique_key = f"extract_capture:{capture_id}:{raw_hash}"
            if normalized_refresh_key:
                unique_key = f"{unique_key}:refresh:{normalized_refresh_key}"
            conn.execute(
                """
                INSERT INTO captures
                (id, user_id, import_id, source, source_url, source_account_id, external_id, title, raw_text, raw_hash, summary, review_status, approved_at, captured_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  user_id = excluded.user_id,
                  import_id = COALESCE(captures.import_id, excluded.import_id),
                  source = excluded.source,
                  source_url = excluded.source_url,
                  source_account_id = excluded.source_account_id,
                  external_id = excluded.external_id,
                  title = excluded.title,
                  raw_text = excluded.raw_text,
                  raw_hash = excluded.raw_hash,
                  summary = excluded.summary,
                  review_status = excluded.review_status,
                  approved_at = excluded.approved_at,
                  captured_at = excluded.captured_at
                """,
                (
                    capture_id,
                    user_id,
                    import_id,
                    normalized_source,
                    source_url,
                    normalized_source_account_id,
                    normalized_external_id,
                    title,
                    content,
                    raw_hash,
                    summary,
                    review_status,
                    approved_at,
                    captured_at,
                ),
            )
            job = self._enqueue_job(
                conn,
                user_id=user_id,
                job_type="extract_capture",
                object_type="capture",
                object_id=capture_id,
                unique_key=unique_key,
                payload={
                    "capture_id": capture_id,
                    "source": normalized_source,
                    "source_url": source_url,
                    "source_account_id": normalized_source_account_id,
                    "external_id": normalized_external_id,
                    "record_metadata": record_metadata if isinstance(record_metadata, dict) else {},
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
                    "source_account_id": normalized_source_account_id,
                    "external_id": normalized_external_id,
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
            import_metadata = SOURCE_CONNECTOR_IMPORT_METADATA.get(source_id, {})
            source_ids = _unique_catalog_strings(import_metadata.get("source_ids") or [source_id])
            source_aliases = _unique_catalog_strings(import_metadata.get("source_aliases") or [])
            import_infos = [import_sources[source] for source in source_ids if source in import_sources]
            import_info = import_sources.get(source_id) or (import_infos[0] if import_infos else None)
            import_status = str(import_metadata.get("import_status") or (import_info or {}).get("status") or "")
            if not import_status:
                import_status = "generic" if item["live_status"] in {"planned", "import_ready"} else "export_only"
            formats = _unique_catalog_strings(format for info in import_infos for format in info.get("formats", []))
            if import_info and not formats:
                formats = _unique_catalog_strings(import_info.get("formats", []))
            export_status = str(import_metadata.get("export_status") or import_status)
            supports_import = import_status in {"native", "generic", "import_ready"} or bool(formats)
            service_baseline = _connector_service_baseline(item, source_ids, supports_import=supports_import)
            catalog.append(
                {
                    **item,
                    "readiness_status": _connector_readiness_status(item),
                    "permissions_required": _connector_permission_requirements(item),
                    "first_100_note": _connector_first_100_note(item),
                    "primary_beta": _connector_primary_beta(item),
                    "beta_status": _connector_beta_status(item),
                    "primary_beta_path": _connector_primary_beta_path(item),
                    "show_in_primary_ui": _connector_primary_beta(item),
                    "import_status": import_status,
                    "export_status": export_status,
                    "source_ids": source_ids,
                    "source_aliases": source_aliases,
                    "import_label": import_metadata.get("import_label") or _default_import_label(import_status, item.get("name") or source_id),
                    "supports_import": supports_import,
                    "formats": formats,
                    "baseline_10k": service_baseline["included"],
                    "service_baseline": service_baseline,
                    "connection_setup": _connector_connection_setup(item, service_baseline),
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
                  SUM(CASE WHEN c.review_status != 'archived' THEN 1 ELSE 0 END) AS current_captures,
                  COUNT(DISTINCT CASE WHEN m.status = 'active' THEN m.id END) AS active_memories,
                  COUNT(DISTINCT CASE WHEN m.status = 'active' AND COALESCE(m.source_url, '') != '' THEN m.id END) AS cited_memories,
                  COUNT(DISTINCT CASE WHEN cps.extraction_status IN ('queued', 'running') THEN c.id END) AS processing,
                  COUNT(DISTINCT CASE WHEN cps.extraction_status = 'failed' THEN c.id END) AS processing_failed,
                  MAX(c.captured_at) AS last_imported_at
                FROM captures c
                LEFT JOIN memories m ON m.capture_id = c.id AND m.user_id = c.user_id
                LEFT JOIN capture_processing_state cps ON cps.capture_id = c.id AND cps.user_id = c.user_id
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
        for item in [*catalog, *({"id": source, "name": source, "category": "Connected source", "auth": "direct", "live_status": "imported", "scopes": [], "notes": "", "readiness_status": "import-ready", "primary_beta": False, "beta_status": "advanced-fallback", "primary_beta_path": "advanced-fallback-only", "show_in_primary_ui": False, "import_status": "native", "export_status": "imported", "source_ids": [source], "source_aliases": [], "import_label": "Connected source data", "supports_import": True, "formats": []} for source in extra_sources)]:
            source = item["id"]
            source_accounts = accounts_by_source.get(source, [])
            active_accounts = [account for account in source_accounts if not account.get("disconnected_at")]
            source_cursors = cursors_by_source.get(source, [])
            stats = captures_by_source.get(source, {})
            pending = int(stats.get("pending") or 0)
            approved = int(stats.get("approved") or 0)
            archived = int(stats.get("archived") or 0)
            captures = int(stats.get("captures") or 0)
            current_captures = int(stats.get("current_captures") or 0)
            active_memories = int(stats.get("active_memories") or 0)
            cited_memories = int(stats.get("cited_memories") or 0)
            processing = int(stats.get("processing") or 0)
            processing_failed = int(stats.get("processing_failed") or 0)
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
            processing_errors = [f"{processing_failed} source record{'s' if processing_failed != 1 else ''} failed processing"] if processing_failed else []
            revoked_or_disconnected = any(
                account.get("disconnected_at")
                or str(account.get("auth_state") or "").lower() in {"revoked", "expired", "error"}
                or str(account.get("status") or "").lower() in {"error", "failed", "disconnected"}
                for account in source_accounts
            )
            has_attention = bool(account_errors or cursor_errors or processing_errors or revoked_or_disconnected)
            import_status = str(item.get("import_status") or "")
            supports_import = bool(item.get("supports_import")) or import_status in {"native", "generic", "import_ready"} or bool(item.get("formats"))
            service_baseline = item.get("service_baseline") or _connector_service_baseline(
                item,
                _unique_catalog_strings(item.get("source_ids") or [source]),
                supports_import=supports_import,
            )
            live_status = str(item.get("live_status") or "")
            readiness_status = str(item.get("readiness_status") or _connector_readiness_status(item))
            catalog_primary_beta = bool(item.get("primary_beta"))
            planned_connector = _planned_connector_without_native_sync(item)
            has_completed_sync = any(cursor.get("last_completed_at") for cursor in source_cursors) or any(account.get("last_sync_at") for account in active_accounts)
            sync_incomplete = False
            if source == "obsidian":
                sync_incomplete = any(
                    bool((cursor.get("state") or {}).get("truncated"))
                    and (_sync_cursor_offset(cursor.get("cursor_value")) or 0) > 0
                    for cursor in source_cursors
                )
            obsidian_empty_sync = False
            if source == "obsidian" and has_completed_sync and not current_captures and not active_memories:
                sync_states = [cursor.get("state") or {} for cursor in source_cursors]
                account_states = [account.get("metadata") or {} for account in active_accounts]
                obsidian_empty_sync = any(
                    int((state or {}).get("records_returned") or 0) == 0
                    and not (state or {}).get("truncated")
                    and not (state or {}).get("scan_errors")
                    for state in [*sync_states, *account_states]
                )
            has_synced_data = (has_completed_sync and not obsidian_empty_sync and not sync_incomplete) or current_captures or active_memories
            if has_synced_data and catalog_primary_beta:
                beta_status = "active"
                primary_beta_path = "connected-source-account"
                primary_beta = True
                show_in_primary_ui = True
            else:
                beta_status = str(item.get("beta_status") or _connector_beta_status(item))
                primary_beta_path = str(item.get("primary_beta_path") or _connector_primary_beta_path(item))
                primary_beta = catalog_primary_beta
                show_in_primary_ui = bool(item.get("show_in_primary_ui"))
            if has_attention:
                status = "needs_attention"
                attention_messages = account_errors + cursor_errors + processing_errors
                next_action = attention_messages[0] if attention_messages else "Resume sync or review this source account."
            elif processing:
                status = "syncing"
                next_action = f"Processing {processing} source record{'s' if processing != 1 else ''} before Review and Ask use them."
            elif pending:
                status = "needs_review"
                next_action = f"Review {pending} pending capture{'s' if pending != 1 else ''}."
            elif obsidian_empty_sync:
                status = "empty"
                next_action = "No Markdown notes were found in this Obsidian vault. Choose a vault with notes before Cortex can build memory."
            elif sync_incomplete:
                status = "syncing"
                next_action = "Cortex is still scanning this Obsidian vault in batches."
            elif planned_connector and (current_captures or active_memories):
                status = "imported"
                next_action = "Local records are available; direct account sign-in sync is still planned for this source."
            elif planned_connector:
                status = "planned"
                next_action = "Account sign-in sync is planned for this source."
            elif has_completed_sync:
                status = "synced"
                next_action = "Source sync has completed; review new memories as they arrive."
            elif active_accounts and not planned_connector:
                status = "connected"
                next_action = "Connection is registered; waiting for the first completed sync."
            elif captures or active_memories:
                status = "imported"
                next_action = "Connected source data is available for retrieval."
            elif catalog_primary_beta:
                status = "import_ready"
                next_action = "Connect this source through the native beta connector."
            elif live_status == "planned":
                status = "planned"
                next_action = "Account sign-in sync is planned for this source."
            elif readiness_status == "token-ready":
                status = "available"
                next_action = "Connect this source with a read-only token to start sync."
            elif source in BASELINE_10K_CONNECTOR_IDS and live_status in {"local_api", "local_only"}:
                status = "available"
                next_action = "Connect this source through the native local connector to start sync."
            elif readiness_status == "export-only":
                status = "connector_needed"
                next_action = "Needs a direct connector before becoming a primary source."
            elif readiness_status == "import-ready" or supports_import:
                status = "advanced_fallback"
                next_action = "Available only through Advanced/Fallback support tooling until a direct connector exists."
            else:
                status = "available"
                next_action = "Add this source when it contains useful personal context."

            sync_plan = _source_readiness_sync_plan(
                service_baseline=service_baseline,
                live_status=live_status,
                active_accounts=active_accounts,
                source_cursors=source_cursors,
            )
            sync_plan = self._apply_source_scheduler_support(user_id, sync_plan, active_accounts)
            last_seen = stats.get("last_imported_at") or next((account.get("last_sync_at") for account in active_accounts if account.get("last_sync_at")), None)
            rows.append(
                {
                    "source": source,
                    "name": item.get("name") or source,
                    "category": item.get("category") or "Other",
                    "status": status,
                    "next_action": next_action,
                    "import_status": import_status,
                    "export_status": item.get("export_status") or import_status,
                    "source_ids": item.get("source_ids") or [source],
                    "source_aliases": item.get("source_aliases") or [],
                    "import_label": item.get("import_label") or "",
                    "supports_import": supports_import,
                    "live_status": live_status,
                    "readiness_status": readiness_status,
                    "permissions_required": item.get("permissions_required") or _connector_permission_requirements(item),
                    "first_100_note": item.get("first_100_note") or _connector_first_100_note(item),
                    "baseline_10k": bool(item.get("baseline_10k") or service_baseline.get("included")),
                    "service_baseline": service_baseline,
                    "sync_plan": sync_plan,
                    "primary_beta": primary_beta,
                    "beta_status": beta_status,
                    "primary_beta_path": primary_beta_path,
                    "show_in_primary_ui": show_in_primary_ui,
                    "auth": item.get("auth"),
                    "scopes": item.get("scopes") or [],
                    "formats": item.get("formats") or [],
                    "accounts": len(active_accounts),
                    "cursors": len(source_cursors),
                    "captures": captures,
                    "current_captures": current_captures,
                    "pending": pending,
                    "approved": approved,
                    "archived": archived,
                    "processing": processing,
                    "processing_failed": processing_failed,
                    "active_memories": active_memories,
                    "citation_coverage": _ratio(cited_memories, active_memories),
                    "last_seen_at": last_seen,
                    "policy": policies.get(source) or {"mode": "default", "allow_ai_context": True, "review_required": False},
                    "warnings": [value for value in [*account_errors, *cursor_errors, *processing_errors] if value],
                }
            )

        status_rank = {
            "needs_attention": 0,
            "empty": 1,
            "syncing": 2,
            "needs_review": 3,
            "import_ready": 4,
            "connected": 5,
            "synced": 6,
            "imported": 7,
            "planned": 8,
            "advanced_fallback": 9,
            "connector_needed": 10,
            "available": 11,
        }
        rows.sort(key=lambda row: (status_rank.get(row["status"], 9), -int(row["active_memories"]), row["name"]))
        summary = {
            "sources_total": len(rows),
            "import_ready": sum(1 for row in rows if row["status"] == "import_ready"),
            "planned_live": sum(1 for row in rows if row["live_status"] == "planned"),
            "primary_beta_ready": sum(1 for row in rows if row["beta_status"] == "ready"),
            "primary_beta_active": sum(1 for row in rows if row["beta_status"] == "active"),
            "planned_connectors": sum(1 for row in rows if row["beta_status"] == "planned"),
            "advanced_fallback_only": sum(1 for row in rows if row["beta_status"] == "advanced-fallback"),
            "connector_needed": sum(1 for row in rows if row["beta_status"] == "needs-connector"),
            "connected": sum(
                int(row["accounts"])
                for row in rows
                if row["status"] in {"connected", "syncing", "needs_review", "synced", "imported"}
            ),
            "synced": sum(1 for row in rows if row["status"] == "synced"),
            "empty": sum(1 for row in rows if row["status"] == "empty"),
            "syncing": sum(1 for row in rows if row["status"] == "syncing"),
            "processing": sum(int(row["processing"]) for row in rows),
            "processing_failed": sum(int(row["processing_failed"]) for row in rows),
            "sources_with_data": sum(1 for row in rows if row["captures"] or row["active_memories"]),
            "baseline_10k_services": sum(1 for row in rows if row["baseline_10k"]),
            "baseline_10k_records_supported": sum(
                1 for row in rows if row["baseline_10k"] and (row["service_baseline"] or {}).get("records_supported")
            ),
            "baseline_10k_live_sync": sum(1 for row in rows if row["baseline_10k"] and (row["service_baseline"] or {}).get("live_sync")),
            "baseline_10k_manual_direct_sync": sum(
                1 for row in rows if row["baseline_10k"] and (row["service_baseline"] or {}).get("manual_direct_sync")
            ),
            "baseline_10k_local_app_autosync": sum(
                1 for row in rows if row["baseline_10k"] and (row["service_baseline"] or {}).get("local_app_autosync")
            ),
            "baseline_10k_hosted_managed_sync": sum(
                1 for row in rows if row["baseline_10k"] and (row["service_baseline"] or {}).get("hosted_managed_sync")
            ),
            "needs_review": sum(1 for row in rows if row["status"] == "needs_review"),
            "needs_attention": sum(1 for row in rows if row["status"] == "needs_attention"),
            "active_memories": sum(int(row["active_memories"]) for row in rows),
        }
        baseline_rows = [row for row in rows if row["baseline_10k"]]
        summary.update(
            {
                "baseline_10k_ready_for_beta": all(
                    row["beta_status"] == "ready"
                    and row["sync_plan"]["mode"] in {"local_app_autosync", "hosted_managed_sync"}
                    and row["sync_plan"]["mode"] != "planned_account_sync"
                    for row in baseline_rows
                ),
                "baseline_10k_service_ids": sorted(row["source"] for row in baseline_rows),
                "baseline_10k_primary_ui_ids": sorted(row["source"] for row in baseline_rows if row["show_in_primary_ui"]),
                "baseline_10k_advanced_sync_ids": sorted(row["source"] for row in baseline_rows if not row["show_in_primary_ui"]),
                "baseline_10k_token_sync_ids": sorted(
                    row["source"]
                    for row in baseline_rows
                    if row["primary_beta_path"] == "native-token-connector"
                ),
                "baseline_10k_local_sync_ids": sorted(
                    row["source"]
                    for row in baseline_rows
                    if row["primary_beta_path"] == "native-local-connector"
                ),
            }
        )
        recommendations: list[str] = []
        if summary["needs_attention"]:
            recommendations.append("Resolve source account or sync errors before relying on those memories.")
        if summary["empty"]:
            recommendations.append("Choose an Obsidian vault with Markdown notes before Cortex can build memory from it.")
        if summary["syncing"]:
            recommendations.append("Wait for source processing to finish before judging Review and Ask coverage.")
        if summary["needs_review"]:
            recommendations.append("Review pending source captures so they can become trusted model memory.")
        if not summary["sources_with_data"]:
            recommendations.append("Connect Obsidian notes or local AI tools so Cortex can start building reviewed memory.")
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

    def enqueue_due_source_syncs(self, user_id: str, *, limit: int = 20) -> dict[str, Any]:
        scheduled: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        accounts = self.list_source_accounts(user_id)
        cursors_by_account: dict[str, list[dict[str, Any]]] = {}
        for cursor in self.list_sync_cursors(user_id):
            account_id = str(cursor.get("source_account_id") or "").strip()
            if account_id:
                cursors_by_account.setdefault(account_id, []).append(cursor)
        catalog_by_source = {item["id"]: item for item in self.source_connector_catalog()}
        for account in accounts:
            if len(scheduled) >= max(0, min(int(limit or 0), 100)):
                break
            source = account["source"]
            sync_plan = self._source_account_readiness_sync_plan(
                account,
                cursors_by_account.get(account["id"], []),
                catalog_by_source=catalog_by_source,
            )
            if not sync_plan.get("due_now"):
                continue
            scheduler_supported, blocked_reason = self._source_account_scheduler_support(user_id, account)
            if not scheduler_supported:
                skipped.append({
                    "source_account_id": account["id"],
                    "source": source,
                    "reason": blocked_reason or "stored_sync_configuration_required",
                })
                continue
            try:
                scheduled.append(
                    self.enqueue_source_account_sync(
                        user_id,
                        account["id"],
                        schedule_token=sync_plan.get("next_sync_due_at") or now_iso(),
                    )
                )
            except ValueError as exc:
                skipped.append({
                    "source_account_id": account["id"],
                    "source": source,
                    "reason": str(exc),
                })
        return {
            "checked_at": now_iso(),
            "scheduled": len(scheduled),
            "jobs": scheduled,
            "skipped": skipped,
        }

    def _source_account_readiness_sync_plan(
        self,
        account: dict[str, Any],
        source_cursors: list[dict[str, Any]],
        *,
        catalog_by_source: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        source = _normalize_source_key(str(account.get("source") or ""))
        catalog = catalog_by_source if catalog_by_source is not None else {item["id"]: item for item in self.source_connector_catalog()}
        item = catalog.get(source) or {
            "id": source,
            "live_status": "local_only",
            "source_ids": [source],
            "supports_import": True,
        }
        supports_import = bool(item.get("supports_import")) or bool(item.get("formats"))
        service_baseline = item.get("service_baseline") or _connector_service_baseline(
            item,
            _unique_catalog_strings(item.get("source_ids") or [source]),
            supports_import=supports_import,
        )
        return _source_readiness_sync_plan(
            service_baseline=service_baseline,
            live_status=str(item.get("live_status") or ""),
            active_accounts=[account],
            source_cursors=source_cursors,
        )

    def _source_account_scheduler_support(self, user_id: str, account: dict[str, Any]) -> tuple[bool, str | None]:
        if not _source_account_sync_scheduler_supported(account):
            return False, "stored_sync_configuration_required"
        metadata = account.get("metadata") if isinstance(account.get("metadata"), dict) else {}
        credential_ref = str(metadata.get("credential_ref") or "").strip()
        if not credential_ref.startswith("source_credential:"):
            return True, None
        source = _normalize_source_key(str(account.get("source") or ""))
        if source == "zotero" and str(metadata.get("api_base_url") or "").strip():
            return True, None
        credential = self.vault.read_source_credential(
            user_id=user_id,
            source_account_id=str(account.get("id") or ""),
        )
        payload = credential.get("payload") if isinstance(credential, dict) else None
        if isinstance(payload, dict) and payload:
            return True, None
        return False, "stored_credential_missing"

    def _apply_source_scheduler_support(
        self,
        user_id: str,
        sync_plan: dict[str, Any],
        active_accounts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not active_accounts:
            return sync_plan
        support_checks = [self._source_account_scheduler_support(user_id, account) for account in active_accounts]
        if any(supported for supported, _reason in support_checks):
            return sync_plan
        reasons = [reason for _supported, reason in support_checks if reason]
        if "stored_credential_missing" not in reasons:
            return sync_plan
        plan = dict(sync_plan)
        plan["scheduler_supported"] = False
        plan["due_now"] = False
        plan["blocked_reason"] = reasons[0] if reasons else "stored_sync_configuration_required"
        if plan.get("managed_sync_status") in {"due", "waiting_for_first_sync", "healthy", "not_configured"}:
            plan["managed_sync_status"] = "needs_attention"
        return plan

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
    ) -> dict[str, Any]:
        normalized_source = _google_oauth_source(source)
        resolved_client_id = _google_oauth_config_value(normalized_source, "CLIENT_ID", client_id)
        if not resolved_client_id:
            raise ValueError("Google OAuth client ID is not configured")
        resolved_redirect_uri = _google_oauth_redirect_uri(normalized_source, redirect_uri)
        if not resolved_redirect_uri:
            raise ValueError("Google OAuth redirect URI is not configured")
        resolved_state = str(state or "").strip() or secrets.token_urlsafe(24)
        resolved_scopes = _google_oauth_scopes(normalized_source, scopes)
        normalized_code_challenge = str(code_challenge or "").strip()
        normalized_challenge_method = str(code_challenge_method or "").strip() or ("S256" if normalized_code_challenge else "")
        if normalized_code_challenge:
            if normalized_challenge_method not in {"S256", "plain"}:
                raise ValueError("Google OAuth code challenge method must be S256 or plain")
        query = {
            "client_id": resolved_client_id,
            "redirect_uri": resolved_redirect_uri,
            "response_type": "code",
            "scope": " ".join(resolved_scopes),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": resolved_state,
        }
        if normalized_code_challenge:
            query["code_challenge"] = normalized_code_challenge
            query["code_challenge_method"] = normalized_challenge_method
        return {
            "source": normalized_source,
            "provider": "google",
            "authorization_url": f"{GOOGLE_OAUTH_AUTHORIZATION_ENDPOINT}?{urlencode(query)}",
            "authorization_endpoint": GOOGLE_OAUTH_AUTHORIZATION_ENDPOINT,
            "token_endpoint": GOOGLE_OAUTH_TOKEN_ENDPOINT,
            "redirect_uri": resolved_redirect_uri,
            "state": resolved_state,
            "scopes": resolved_scopes,
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
        request_token: Any | None = None,
    ) -> dict[str, Any]:
        normalized_source = _google_oauth_source(source)
        normalized_state = str(state or "").strip()
        normalized_expected_state = str(expected_state or "").strip()
        if normalized_expected_state and normalized_state != normalized_expected_state:
            raise ValueError("Google OAuth state did not match")
        normalized_code = str(code or "").strip()
        if not normalized_code:
            raise ValueError("Google OAuth authorization code is required")
        resolved_client_id = _google_oauth_config_value(normalized_source, "CLIENT_ID", client_id)
        resolved_client_secret = _google_oauth_config_value(normalized_source, "CLIENT_SECRET", client_secret)
        resolved_redirect_uri = _google_oauth_redirect_uri(normalized_source, redirect_uri)
        resolved_token_endpoint = str(token_endpoint or "").strip() or _google_oauth_config_value(normalized_source, "TOKEN_ENDPOINT") or GOOGLE_OAUTH_TOKEN_ENDPOINT
        if not resolved_client_id:
            raise ValueError("Google OAuth client ID is not configured")
        normalized_code_verifier = str(code_verifier or "").strip()
        if not resolved_client_secret and not normalized_code_verifier:
            raise ValueError("Google OAuth client secret or PKCE verifier is required")
        defaults = GOOGLE_OAUTH_CONNECTOR_DEFAULTS[normalized_source]
        identifier = (account_identifier or defaults["account_identifier"]).strip()[:240]
        resolved_account_id = (source_account_id or "").strip() or stable_id("sacct_", f"{user_id}:{normalized_source}:{identifier}")
        existing_payload = self._read_source_account_credential_payload(user_id, resolved_account_id)
        form = {
            "grant_type": "authorization_code",
            "code": normalized_code,
            "client_id": resolved_client_id,
            "redirect_uri": resolved_redirect_uri,
        }
        if resolved_client_secret:
            form["client_secret"] = resolved_client_secret
        if normalized_code_verifier:
            form["code_verifier"] = normalized_code_verifier
        requester = request_token or _request_oauth_token
        try:
            token_payload = requester(resolved_token_endpoint, form)
        except Exception as exc:
            raise ValueError(redact_error_message(exc, [normalized_code, resolved_client_id, resolved_client_secret, normalized_code_verifier])) from exc
        if not isinstance(token_payload, dict):
            raise ValueError("Google OAuth token response was not an object")
        access_token = str(token_payload.get("access_token") or "").strip()
        if not access_token:
            raise ValueError("Google OAuth token response did not include an access token")
        refresh_token = str(token_payload.get("refresh_token") or existing_payload.get("refresh_token") or "").strip()
        if not refresh_token:
            raise ValueError("Google OAuth token response did not include a refresh token; reconnect with consent")
        refreshed_at = datetime.now(timezone.utc).replace(microsecond=0)
        expires_at = _oauth_refresh_expires_at(token_payload, now=refreshed_at)
        token_scope = str(token_payload.get("scope") or " ".join(GOOGLE_OAUTH_CONNECTOR_SCOPES[normalized_source])).strip()
        label = (account_label or defaults["account_label"]).strip()[:160]
        metadata: dict[str, Any] = _with_source_credential_ref(
            {
                "connector": normalized_source,
                "oauth_provider": "google",
                "managed_oauth": True,
                "token_configured": True,
                "refresh_token_configured": True,
                "scope": token_scope,
                "scopes": token_scope.split(),
                "token_endpoint": resolved_token_endpoint,
                "api_base_url": defaults["api_base_url"],
                "content_sync_enabled": bool(include_body if normalized_source == "gmail" else include_content),
                "oauth_connected_at": refreshed_at.isoformat().replace("+00:00", "Z"),
            },
            resolved_account_id,
            True,
        )
        if normalized_source == "gmail":
            normalized_label_ids = [str(label_id).strip() for label_id in (label_ids or []) if str(label_id).strip()]
            if query:
                metadata["query"] = str(query).strip()
                metadata["query_configured"] = True
            if normalized_label_ids:
                metadata["label_ids"] = normalized_label_ids
        else:
            normalized_mime_types = [str(mime).strip() for mime in (mime_types or []) if str(mime).strip()]
            if query:
                metadata["query"] = str(query).strip()
                metadata["query_configured"] = True
            if normalized_mime_types:
                metadata["mime_types"] = normalized_mime_types
        account = self.upsert_source_account(
            user_id,
            source=normalized_source,
            account_label=label,
            account_identifier=identifier,
            connection_type="oauth_token",
            status="connected",
            auth_state="healthy",
            policy={"review_required": True, "allow_ai_context": True},
            metadata=metadata,
            account_id=resolved_account_id,
        )
        credential_payload: dict[str, Any] = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_endpoint": resolved_token_endpoint,
            "client_id": resolved_client_id,
            "scope": token_scope,
            "oauth_refreshed_at": refreshed_at.isoformat().replace("+00:00", "Z"),
            "api_base_url": defaults["api_base_url"],
        }
        if resolved_client_secret:
            credential_payload["client_secret"] = resolved_client_secret
        if expires_at:
            credential_payload["access_token_expires_at"] = expires_at
        if normalized_source == "gmail":
            credential_payload.update({
                "query": str(query or ""),
                "label_ids": metadata.get("label_ids") or [],
                "include_body": bool(include_body),
            })
        else:
            credential_payload.update({
                "query": str(query or ""),
                "mime_types": metadata.get("mime_types") or [],
                "include_content": bool(include_content),
            })
        credential = self.store_source_account_credential(
            user_id,
            account["id"],
            source=normalized_source,
            payload=credential_payload,
        )
        return {
            "source": normalized_source,
            "provider": "google",
            "source_account": self._source_account_by_id(user_id, account["id"]) or account,
            "credential_ref": credential["credential_ref"],
            "scope": token_scope,
            "scopes": token_scope.split(),
            "access_token_expires_at": expires_at,
            "sync_plan": self._source_account_readiness_sync_plan(
                self._source_account_by_id(user_id, account["id"]) or account,
                self.list_sync_cursors(user_id, source_account_id=account["id"]),
            ),
        }

    def start_managed_oauth(
        self,
        source: str,
        *,
        redirect_uri: str | None = None,
        state: str | None = None,
        client_id: str | None = None,
        scopes: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_source = _managed_oauth_source(source)
        defaults = MANAGED_OAUTH_CONNECTOR_DEFAULTS[normalized_source]
        provider = defaults["provider"]
        resolved_client_id = _managed_oauth_config_value(normalized_source, "CLIENT_ID", client_id)
        if not resolved_client_id:
            raise ValueError(f"{provider.title()} OAuth client ID is not configured")
        resolved_redirect_uri = _managed_oauth_redirect_uri(normalized_source, redirect_uri)
        if not resolved_redirect_uri:
            raise ValueError(f"{provider.title()} OAuth redirect URI is not configured")
        resolved_state = str(state or "").strip() or secrets.token_urlsafe(24)
        resolved_scopes = _managed_oauth_scopes(normalized_source, scopes)
        query: dict[str, str] = {
            "client_id": resolved_client_id,
            "redirect_uri": resolved_redirect_uri,
            "response_type": "code",
            "state": resolved_state,
        }
        if normalized_source == "notion":
            query["owner"] = "user"
        elif provider == "microsoft":
            query["response_mode"] = "query"
        if resolved_scopes:
            query["scope"] = " ".join(resolved_scopes)
        authorization_endpoint = MANAGED_OAUTH_AUTHORIZATION_ENDPOINTS[normalized_source]
        return {
            "source": normalized_source,
            "provider": provider,
            "authorization_url": f"{authorization_endpoint}?{urlencode(query)}",
            "authorization_endpoint": authorization_endpoint,
            "token_endpoint": MANAGED_OAUTH_TOKEN_ENDPOINTS[normalized_source],
            "redirect_uri": resolved_redirect_uri,
            "state": resolved_state,
            "scopes": resolved_scopes,
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
        request_token: Any | None = None,
    ) -> dict[str, Any]:
        normalized_source = _managed_oauth_source(source)
        defaults = MANAGED_OAUTH_CONNECTOR_DEFAULTS[normalized_source]
        provider = str(defaults["provider"])
        normalized_state = str(state or "").strip()
        normalized_expected_state = str(expected_state or "").strip()
        if normalized_expected_state and normalized_state != normalized_expected_state:
            raise ValueError(f"{provider.title()} OAuth state did not match")
        normalized_code = str(code or "").strip()
        if not normalized_code:
            raise ValueError(f"{provider.title()} OAuth authorization code is required")
        resolved_client_id = _managed_oauth_config_value(normalized_source, "CLIENT_ID", client_id)
        resolved_client_secret = _managed_oauth_config_value(normalized_source, "CLIENT_SECRET", client_secret)
        resolved_redirect_uri = _managed_oauth_redirect_uri(normalized_source, redirect_uri)
        resolved_token_endpoint = str(token_endpoint or "").strip() or _managed_oauth_config_value(normalized_source, "TOKEN_ENDPOINT") or MANAGED_OAUTH_TOKEN_ENDPOINTS[normalized_source]
        if not resolved_client_id:
            raise ValueError(f"{provider.title()} OAuth client ID is not configured")
        if normalized_source == "notion" and not resolved_client_secret:
            raise ValueError(f"{provider.title()} OAuth client secret is not configured")

        token_request = {
            "grant_type": "authorization_code",
            "code": normalized_code,
        }
        if resolved_redirect_uri:
            token_request["redirect_uri"] = resolved_redirect_uri
        if provider == "microsoft":
            token_request["client_id"] = resolved_client_id
            if resolved_client_secret:
                token_request["client_secret"] = resolved_client_secret
            token_request["scope"] = " ".join(MANAGED_OAUTH_CONNECTOR_SCOPES[normalized_source])
        resolved_notion_version = str(notion_version or defaults.get("notion_version") or "2026-03-11").strip()
        token_headers = {"Notion-Version": resolved_notion_version} if normalized_source == "notion" else {}
        try:
            if normalized_source == "notion":
                requester = request_token or _request_basic_json_oauth_token
                token_payload = requester(
                    resolved_token_endpoint,
                    token_request,
                    client_id=resolved_client_id,
                    client_secret=resolved_client_secret,
                    headers=token_headers,
                )
            else:
                requester = request_token or _request_oauth_token
                token_payload = requester(resolved_token_endpoint, token_request)
        except Exception as exc:
            raise ValueError(
                redact_error_message(
                    exc,
                    [normalized_code, resolved_client_id, resolved_client_secret],
                )
            ) from exc
        if not isinstance(token_payload, dict):
            raise ValueError(f"{provider.title()} OAuth token response was not an object")
        access_token = str(token_payload.get("access_token") or "").strip()
        if not access_token:
            raise ValueError(f"{provider.title()} OAuth token response did not include an access token")
        refresh_token = str(token_payload.get("refresh_token") or "").strip()
        if provider == "microsoft" and not refresh_token:
            raise ValueError("Microsoft OAuth token response did not include a refresh token; reconnect with offline access")
        connected_at_dt = datetime.now(timezone.utc).replace(microsecond=0)
        expires_at = _oauth_refresh_expires_at(token_payload, now=connected_at_dt)
        token_scope = str(token_payload.get("scope") or " ".join(MANAGED_OAUTH_CONNECTOR_SCOPES[normalized_source])).strip()
        workspace_name = str(token_payload.get("workspace_name") or "").strip()
        workspace_id = str(token_payload.get("workspace_id") or "").strip()
        bot_id = str(token_payload.get("bot_id") or "").strip()
        identifier = (account_identifier or workspace_id or _microsoft_oauth_account_identifier(token_payload) or defaults["account_identifier"]).strip()[:240]
        resolved_account_id = (source_account_id or "").strip() or stable_id("sacct_", f"{user_id}:{normalized_source}:{identifier}")
        label = (account_label or workspace_name or _microsoft_oauth_account_label(token_payload) or defaults["account_label"]).strip()[:160]
        resolved_api_base_url = str(api_base_url or defaults["api_base_url"]).strip()
        connected_at = connected_at_dt.isoformat().replace("+00:00", "Z")
        metadata_payload = {
            "connector": normalized_source,
            "oauth_provider": provider,
            "managed_oauth": True,
            "token_configured": True,
            "refresh_token_configured": bool(refresh_token),
            "api_base_url": resolved_api_base_url,
            "content_sync_enabled": bool(include_content),
            "oauth_connected_at": connected_at,
            "scope": token_scope,
            "scopes": token_scope.split(),
            "token_endpoint": resolved_token_endpoint,
        }
        if normalized_source == "notion":
            metadata_payload.update({
                "workspace_name": workspace_name,
                "workspace_id": workspace_id,
                "bot_id": bot_id,
                "notion_version": resolved_notion_version,
            })
        elif normalized_source == "outlook":
            metadata_payload.update({
                "email": _microsoft_oauth_account_identifier(token_payload),
                "user_email": _microsoft_oauth_account_identifier(token_payload),
                "query_configured": False,
            })
        metadata = _with_source_credential_ref(metadata_payload, resolved_account_id, True)
        account = self.upsert_source_account(
            user_id,
            source=normalized_source,
            account_label=label,
            account_identifier=identifier,
            connection_type="oauth_token",
            status="connected",
            auth_state="healthy",
            policy={"review_required": True, "allow_ai_context": True},
            metadata=metadata,
            account_id=resolved_account_id,
        )
        if normalized_source == "notion":
            credential_payload: dict[str, Any] = {
                "token": access_token,
                # access_token mirrors token so the shared OAuth-refresh path (which keys off
                # access_token / access_token_expires_at) can refresh Notion the same way it
                # refreshes Gmail/Outlook; the notion sync dispatch continues to read "token".
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_endpoint": resolved_token_endpoint,
                "client_id": resolved_client_id,
                "scope": token_scope,
                "api_base_url": resolved_api_base_url,
                "include_content": bool(include_content),
                "notion_version": resolved_notion_version,
                "oauth_refreshed_at": connected_at,
            }
            if resolved_client_secret:
                credential_payload["client_secret"] = resolved_client_secret
            if expires_at:
                credential_payload["access_token_expires_at"] = expires_at
        else:
            credential_payload = {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_endpoint": resolved_token_endpoint,
                "client_id": resolved_client_id,
                "scope": token_scope,
                "api_base_url": resolved_api_base_url,
                "include_body": bool(include_content),
                "query": "",
                "oauth_refreshed_at": connected_at,
            }
            if resolved_client_secret:
                credential_payload["client_secret"] = resolved_client_secret
            if expires_at:
                credential_payload["access_token_expires_at"] = expires_at
        credential = self.store_source_account_credential(
            user_id,
            account["id"],
            source=normalized_source,
            payload=credential_payload,
        )
        return {
            "source": normalized_source,
            "provider": provider,
            "source_account": self._source_account_by_id(user_id, account["id"]) or account,
            "credential_ref": credential["credential_ref"],
            "scope": token_scope,
            "scopes": token_scope.split(),
            "access_token_expires_at": expires_at,
            "sync_plan": self._source_account_readiness_sync_plan(
                self._source_account_by_id(user_id, account["id"]) or account,
                self.list_sync_cursors(user_id, source_account_id=account["id"]),
            ),
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
    ) -> dict[str, Any]:
        if processing not in {"sync", "async"}:
            raise ValueError("processing must be sync or async")
        account = self._source_account_by_id(user_id, account_id)
        if not account:
            raise ValueError("source account not found")
        if account.get("disconnected_at"):
            raise ValueError("source account is disconnected")
        scheduler_supported, blocked_reason = self._source_account_scheduler_support(user_id, account)
        if not scheduler_supported:
            if blocked_reason == "stored_credential_missing":
                raise ValueError("source account stored credential is missing")
            raise ValueError("source account needs stored sync configuration before it can be scheduled")
        try:
            capped_max_records = int(max_records or 200)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_records must be an integer") from exc
        if capped_max_records < 1 or capped_max_records > 500:
            raise ValueError("max_records must be between 1 and 500")
        normalized_cursor_name = (cursor_name or _default_source_sync_cursor_name(account["source"])).strip() or "default"
        normalized_schedule_token = str(schedule_token or run_at or now_iso()).strip()[:120]
        unique_key = f"source_account_sync:{account['id']}:{normalized_cursor_name}:{normalized_schedule_token}"
        timestamp = now_iso()
        with connect(self.db_path) as conn:
            existing = conn.execute(
                """
                SELECT *
                FROM memory_jobs
                WHERE user_id = ?
                  AND job_type = 'source_account_sync'
                  AND object_type = 'source_account'
                  AND object_id = ?
                  AND status IN ('queued', 'running')
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (user_id, account["id"]),
            ).fetchone()
            if existing:
                return self._job_from_row(existing)
            job = self._enqueue_job(
                conn,
                user_id=user_id,
                job_type="source_account_sync",
                object_type="source_account",
                object_id=account["id"],
                unique_key=unique_key,
                payload={
                    "source_account_id": account["id"],
                    "source": account["source"],
                    "processing": processing,
                    "cursor_name": normalized_cursor_name,
                    "max_records": capped_max_records,
                },
                priority=30,
                run_at=run_at,
                max_attempts=3,
            )
            self._event(
                conn,
                user_id,
                account["id"],
                "source_account",
                "sync_enqueued",
                {"job_id": job["id"], "source": account["source"], "run_at": run_at or timestamp},
            )
        return job

    def store_source_account_credential(self, user_id: str, account_id: str, *, source: str, payload: dict[str, Any]) -> dict[str, Any]:
        cleaned_payload = {
            str(key): value
            for key, value in (payload or {}).items()
            if str(key or "").strip() and value is not None
        }
        if not cleaned_payload:
            raise ValueError("credential payload is required")
        return self.vault.write_source_credential(
            user_id=user_id,
            source_account_id=account_id,
            source=source,
            payload=cleaned_payload,
        )

    def _read_source_account_credential_payload(self, user_id: str, account_id: str) -> dict[str, Any]:
        credential = self.vault.read_source_credential(user_id=user_id, source_account_id=account_id)
        if not credential:
            return {}
        payload = credential.get("payload")
        return payload if isinstance(payload, dict) else {}

    def _source_credential_payload_with_fresh_oauth_token(
        self,
        user_id: str,
        account: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        source = _normalize_source_key(str(account.get("source") or ""))
        if source not in OAUTH_REFRESH_CREDENTIAL_SOURCES or not _credential_access_token_expired(payload):
            return payload

        refresh_token = str(payload.get("refresh_token") or "").strip()
        token_endpoint = str(payload.get("token_endpoint") or "").strip()
        client_id = str(payload.get("client_id") or "").strip()
        client_secret = str(payload.get("client_secret") or "").strip()
        # Notion stores its access token under "token" and authenticates refresh with Basic auth.
        current_access_token = str(payload.get("access_token") or payload.get("token") or "").strip()
        if not refresh_token or not token_endpoint or not client_id:
            raise ValueError(f"{source} access token expired and refresh configuration is missing")
        if source == "notion" and not client_secret:
            raise ValueError(f"{source} access token expired and refresh configuration is missing")

        scope = str(payload.get("scope") or "").strip()
        secrets_to_redact = [
            current_access_token,
            refresh_token,
            client_id,
            client_secret,
        ]
        try:
            if source == "notion":
                token_payload = _request_notion_oauth_token_refresh(
                    token_endpoint,
                    refresh_token=refresh_token,
                    client_id=client_id,
                    client_secret=client_secret,
                    notion_version=str(payload.get("notion_version") or "").strip(),
                )
            else:
                form = {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                }
                if client_secret:
                    form["client_secret"] = client_secret
                if scope:
                    form["scope"] = scope
                token_payload = _request_oauth_token_refresh(token_endpoint, form)
        except Exception as exc:
            raise ValueError(redact_error_message(exc, secrets_to_redact)) from exc
        if not isinstance(token_payload, dict):
            raise ValueError(f"{source} token refresh response was not an object")
        access_token = str(token_payload.get("access_token") or "").strip()
        if not access_token:
            raise ValueError(f"{source} token refresh response did not include an access token")

        refreshed = dict(payload)
        refreshed["access_token"] = access_token
        # Keep the "token" alias in sync so the Notion sync dispatch (which reads "token")
        # picks up the refreshed credential instead of the expired one.
        if "token" in payload:
            refreshed["token"] = access_token
        if str(token_payload.get("refresh_token") or "").strip():
            refreshed["refresh_token"] = str(token_payload.get("refresh_token") or "").strip()
        refreshed_at = datetime.now(timezone.utc).replace(microsecond=0)
        refreshed["oauth_refreshed_at"] = refreshed_at.isoformat().replace("+00:00", "Z")
        expires_at = _oauth_refresh_expires_at(token_payload, now=refreshed_at)
        if expires_at:
            refreshed["access_token_expires_at"] = expires_at
        if str(token_payload.get("scope") or "").strip():
            refreshed["scope"] = str(token_payload.get("scope") or "").strip()
        self.store_source_account_credential(
            user_id,
            account["id"],
            source=source,
            payload=refreshed,
        )
        return refreshed

    def _identity_aliases_for_source(
        self,
        user_id: str,
        source: str,
        *,
        base_aliases: Any | None = None,
        accounts: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        aliases: list[Any] = list(_normalize_identity_aliases(base_aliases if base_aliases is not None else self.settings(user_id).get("identity_aliases")))
        source_matches = _source_account_alias_sources(source)
        if source_matches:
            active_accounts = accounts if accounts is not None else self.list_source_accounts(user_id)
            for account in active_accounts:
                account_source = _normalize_source_key(str(account.get("source") or ""))
                if account_source in source_matches:
                    aliases.extend(_source_account_identity_values(account, account_source))
        return _normalize_identity_aliases(aliases)

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
        resume_disconnected: bool = True,
    ) -> dict[str, Any]:
        normalized_source = _normalize_source_key(source)
        if not normalized_source:
            raise ValueError("source is required")
        catalog_entry = next((item for item in SOURCE_CONNECTOR_CATALOG if item["id"] == normalized_source), None)
        label = (account_label or (catalog_entry or {}).get("name") or normalized_source).strip()[:160]
        identifier = (account_identifier or "").strip()[:240] or None
        connection = _normalize_source_key(connection_type or "manual") or "manual"
        account_status = _normalize_account_state_key(status or "available") or "available"
        auth = _normalize_account_state_key(auth_state or "not_configured") or "not_configured"
        timestamp = now_iso()
        resolved_id = account_id or stable_id("sacct_", f"{user_id}:{normalized_source}:{identifier or label}")
        existing_policy: dict[str, Any] | None = None
        with connect(self.db_path) as conn:
            existing_for_policy = conn.execute(
                "SELECT policy_json FROM source_accounts WHERE user_id = ? AND id = ?",
                (user_id, resolved_id),
            ).fetchone()
        if policy is None and existing_for_policy:
            existing_policy = self._json_or_empty(existing_for_policy["policy_json"])
        resolved_policy = _normalize_source_account_policy(existing_policy if existing_policy is not None else policy)
        resolved_metadata = _sanitize_source_account_metadata(metadata if isinstance(metadata, dict) else {})
        if _planned_connector_without_native_sync(catalog_entry):
            requested_status = account_status
            requested_auth = auth
            resolved_metadata = {
                **resolved_metadata,
                "requested_status": requested_status,
                "requested_auth_state": requested_auth,
                "connector_state": "planned_until_records_sync",
            }
            account_status = "planned"
            auth = "not_configured"
        with connect(self.db_path) as conn:
            existing = conn.execute("SELECT disconnected_at FROM source_accounts WHERE user_id = ? AND id = ?", (user_id, resolved_id)).fetchone()
            if existing and existing["disconnected_at"] and not resume_disconnected:
                raise ValueError("source account is disconnected; resume before reconnecting")
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

    def sync_source_account_records(
        self,
        user_id: str,
        account_id: str,
        *,
        records: list[dict[str, Any]],
        cursor_name: str = "default",
        cursor_value: str | None = None,
        high_water_mark: str | None = None,
        state: dict[str, Any] | None = None,
        processing: str = "async",
        archive_missing: bool = False,
        complete_snapshot: bool = False,
        archive_external_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        account_id = (account_id or "").strip()
        if not account_id:
            raise ValueError("source account is required")
        if processing not in {"sync", "async"}:
            raise ValueError("processing must be sync or async")
        if not records and not (archive_missing and complete_snapshot):
            raise ValueError("records are required")
        if len(records) > 500:
            raise ValueError("too many records")
        if archive_missing and not complete_snapshot and archive_external_ids is None:
            raise ValueError("archive_missing requires complete_snapshot=true so partial sync pages cannot archive existing memory")

        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM source_accounts WHERE user_id = ? AND id = ?",
                (user_id, account_id),
            ).fetchone()
        if not row:
            raise ValueError("source account not found")
        account = self._source_account_from_row(row)
        if account.get("disconnected_at"):
            raise ValueError("source account is disconnected")

        source = account["source"]
        queued = 0
        saved = 0
        skipped = 0
        failed = 0
        capture_ids: list[str] = []
        record_results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        active_external_ids = (
            {str(value).strip()[:240] for value in archive_external_ids if str(value).strip()}
            if archive_external_ids is not None
            else {str(record.get("external_id") or "").strip()[:240] for record in records if str(record.get("external_id") or "").strip()}
        )
        base_identity_aliases = self.settings(user_id).get("identity_aliases")
        source_accounts = self.list_source_accounts(user_id)

        for ordinal, raw_record in enumerate(records):
            content = str(raw_record.get("content") or "").strip()
            title = str(raw_record.get("title") or "").strip()[:200] or None
            external_id = str(raw_record.get("external_id") or "").strip()[:240] or None
            captured_at = str(raw_record.get("captured_at") or "").strip()[:80] or None
            record_metadata = raw_record.get("metadata") if isinstance(raw_record.get("metadata"), dict) else {}
            source_url = self._source_account_record_url(
                source=source,
                account_id=account_id,
                explicit_url=raw_record.get("source_url"),
                external_id=external_id,
                ordinal=ordinal,
            )
            if not content:
                skipped += 1
                record_results.append({"status": "skipped", "source": source, "source_url": source_url, "title": title, "reason": "empty_content"})
                continue

            try:
                content_hash = stable_id("", content)
                duplicate = None
                updated_existing_record = False
                capture_id_override = None
                with connect(self.db_path) as conn:
                    metadata_refresh_required = False
                    if external_id:
                        existing_record = conn.execute(
                            """
                            SELECT id, raw_hash, review_status, source_url, title, captured_at
                            FROM captures
                            WHERE user_id = ?
                              AND source_account_id = ?
                              AND external_id = ?
                            ORDER BY captured_at DESC
                            LIMIT 1
                            """,
                            (user_id, account_id, external_id),
                        ).fetchone()
                        if existing_record:
                            if existing_record["raw_hash"] == content_hash and existing_record["review_status"] != "archived":
                                metadata_refresh_required = self._source_record_metadata_refresh_required(
                                    conn,
                                    user_id,
                                    existing_record["id"],
                                    source_url=source_url,
                                    title=title,
                                    captured_at=captured_at,
                                    record_metadata=record_metadata,
                                )
                                if metadata_refresh_required:
                                    updated_existing_record = True
                                else:
                                    duplicate = existing_record
                            else:
                                updated_existing_record = True
                                capture_id_override = existing_record["id"]
                        else:
                            moved_record = self._source_account_record_by_content_hash(
                                conn,
                                user_id,
                                account_id,
                                content_hash,
                                exclude_external_ids=active_external_ids,
                            )
                            if moved_record:
                                updated_existing_record = True
                                metadata_refresh_required = True
                                capture_id_override = moved_record["id"]
                    else:
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
                            (user_id, content_hash, source),
                        ).fetchone()
                if duplicate:
                    skipped += 1
                    record_results.append({
                        "capture_id": duplicate["id"],
                        "status": "duplicate",
                        "source": source,
                        "source_url": source_url,
                        "title": title,
                    })
                    continue

                if processing == "sync":
                    identity_aliases = self._identity_aliases_for_source(
                        user_id,
                        source,
                        base_aliases=base_identity_aliases,
                        accounts=[account],
                    )
                    extracted = extract_context(
                        content,
                        source,
                        author_aliases=identity_aliases,
                        extraction_mode="connector",
                    )
                    extracted = _apply_source_record_metadata(extracted, record_metadata)
                    if captured_at:
                        extracted["_timestamp"] = captured_at
                    result = self.save_capture(
                        user_id=user_id,
                        content=content,
                        source=source,
                        source_url=source_url,
                        title=title,
                        extracted=extracted,
                        source_account_id=account_id,
                        external_id=external_id,
                        capture_id_override=capture_id_override,
                    )
                    saved += 1
                    record_results.append({
                        "capture_id": result["capture_id"],
                        "status": "updated" if updated_existing_record else "saved",
                        "source": source,
                        "source_url": source_url,
                        "title": title,
                        "memories": len(result.get("memories") or []),
                    })
                else:
                    result = self.enqueue_capture(
                        user_id=user_id,
                        content=content,
                        source=source,
                        source_url=source_url,
                        title=title,
                        captured_at=captured_at,
                        source_account_id=account_id,
                        external_id=external_id,
                        record_metadata=record_metadata,
                        refresh_key=(
                            stable_id("", json.dumps({"source_url": source_url, "title": title, "metadata": _source_record_refresh_metadata(record_metadata)}, sort_keys=True))
                            if metadata_refresh_required
                            else None
                        ),
                        capture_id_override=capture_id_override,
                    )
                    queued += 1
                    record_results.append({
                        "capture_id": result["capture_id"],
                        "status": "updated" if updated_existing_record else "queued",
                        "source": source,
                        "source_url": source_url,
                        "title": title,
                        "jobs": result.get("jobs") or [],
                    })
                capture_ids.append(result["capture_id"])
            except Exception as exc:
                failed += 1
                error = {"source": source, "source_url": source_url, "title": title, "error": str(exc)}
                errors.append(error)
                record_results.append({**error, "status": "failed"})

        cursor_state = dict(state or {})
        sync_errors = [error for error in (cursor_state.get("sync_errors") or []) if isinstance(error, dict)]
        sync_error_message = str(sync_errors[0].get("error") or "")[:500] if sync_errors else None
        sync_had_errors = bool(sync_error_message)
        cursor_state = _with_connector_failure_metadata(cursor_state, sync_errors)
        cursor_state.update({
            "last_batch_received": len(records),
            "last_batch_saved": saved,
            "last_batch_queued": queued,
            "last_batch_skipped": skipped,
            "last_batch_failed": failed,
        })
        archived_missing = 0
        if archive_missing:
            archived_missing = self._archive_missing_source_account_records(user_id, account_id, active_external_ids)
            cursor_state["last_batch_archived_missing"] = archived_missing
        cursor = self.upsert_sync_cursor(
            user_id,
            source=source,
            source_account_id=account_id,
            cursor_name=cursor_name,
            cursor_value=cursor_value,
            high_water_mark=high_water_mark,
            state=cursor_state,
            last_error=errors[0]["error"] if errors else sync_error_message,
            completed=failed == 0 and not sync_had_errors,
        )
        timestamp = now_iso()
        account_metadata = _with_connector_failure_metadata(account.get("metadata") or {}, [*sync_errors, *errors])
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE source_accounts
                SET status = ?,
                    auth_state = ?,
                    metadata_json = ?,
                    updated_at = ?,
                    last_error = ?
                WHERE user_id = ? AND id = ?
                """,
                (
                    "needs_attention" if failed or sync_had_errors else "connected",
                    "error" if failed or sync_had_errors else ("healthy" if account.get("auth_state") in {"", "not_configured", "available"} else account.get("auth_state")),
                    json.dumps(account_metadata),
                    timestamp,
                    errors[0]["error"] if errors else sync_error_message,
                    user_id,
                    account_id,
                ),
            )
            updated = conn.execute("SELECT * FROM source_accounts WHERE user_id = ? AND id = ?", (user_id, account_id)).fetchone()
        if updated:
            self.vault.write_source_account(self._source_account_from_row(updated))

        status = "complete" if failed == 0 else "partial"
        if queued == 0 and saved == 0 and skipped == 0 and failed == 0:
            status = "empty"
        return {
            "source_account_id": account_id,
            "source": source,
            "status": status,
            "processing": processing,
            "received": len(records),
            "queued": queued,
            "saved": saved,
            "skipped": skipped,
            "failed": failed,
            "archived_missing": archived_missing,
            "capture_ids": capture_ids,
            "records": record_results,
            "errors": errors,
            "cursor": cursor,
        }

    def _source_account_record_by_content_hash(
        self,
        conn,
        user_id: str,
        account_id: str,
        content_hash: str,
        *,
        exclude_external_ids: set[str] | None = None,
    ):
        excluded = {str(value or "").strip()[:240] for value in (exclude_external_ids or set()) if str(value or "").strip()}
        filters = [
            "user_id = ?",
            "source_account_id = ?",
            "raw_hash = ?",
            "review_status != 'archived'",
        ]
        params: list[Any] = [user_id, account_id, content_hash]
        if excluded:
            filters.append(f"(external_id IS NULL OR external_id NOT IN ({','.join('?' for _ in excluded)}))")
            params.extend(sorted(excluded))
        return conn.execute(
            f"""
            SELECT id, raw_hash, review_status, source_url, title, captured_at, external_id
            FROM captures
            WHERE {' AND '.join(filters)}
            ORDER BY
              CASE WHEN review_status = 'approved' THEN 0 ELSE 1 END,
              captured_at DESC
            LIMIT 1
            """,
            params,
        ).fetchone()

    def _archive_missing_source_account_records(self, user_id: str, account_id: str, active_external_ids: set[str]) -> int:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, external_id
                FROM captures
                WHERE user_id = ?
                  AND source_account_id = ?
                  AND external_id IS NOT NULL
                  AND review_status != 'archived'
                """,
                (user_id, account_id),
            ).fetchall()
        archived = 0
        for row in rows:
            external_id = str(row["external_id"] or "").strip()
            if external_id in active_external_ids:
                continue
            if self.archive_capture(user_id, row["id"]):
                archived += 1
        return archived

    def sync_obsidian_vault(
        self,
        user_id: str,
        *,
        vault_path: str,
        source_account_id: str | None = None,
        account_label: str | None = None,
        account_identifier: str | None = None,
        processing: str = "sync",
        max_records: int = 200,
        cursor_name: str = "local-folder",
    ) -> dict[str, Any]:
        from .connectors.obsidian import OBSIDIAN_SOURCE, scan_vault, vault_identity

        if processing not in {"sync", "async"}:
            raise ValueError("processing must be sync or async")
        identity = vault_identity(vault_path)
        resolved_account_id = (source_account_id or "").strip() or stable_id("sacct_", f"{user_id}:{OBSIDIAN_SOURCE}:{identity.vault_id}")
        self._ensure_source_account_can_sync(user_id, resolved_account_id, expected_source=OBSIDIAN_SOURCE)
        previous_cursor = self._latest_sync_cursor_value(user_id, resolved_account_id, cursor_name)
        scan = scan_vault(vault_path, max_records=max_records, cursor_value=previous_cursor)
        label = (account_label or f"Obsidian: {scan.vault_name}").strip()[:160]
        identifier = (account_identifier or scan.vault_id).strip()[:240]
        metadata = {
            "connector": OBSIDIAN_SOURCE,
            "connector_version": scan.to_summary()["connector_version"],
            "vault_name": scan.vault_name,
            "vault_path": scan.vault_path,
            "vault_id": scan.vault_id,
            "extensions": scan.extensions,
            "manifest_hash": scan.manifest_hash,
            "records_found": scan.records_found,
            "records_returned": scan.records_returned,
            "truncated": scan.truncated,
            "files_seen": scan.files_seen,
            "skipped": scan.skipped,
        }
        empty_complete_scan = not scan.records and not scan.errors and not scan.truncated
        account = self.upsert_source_account(
            user_id,
            source=OBSIDIAN_SOURCE,
            account_label=label,
            account_identifier=identifier,
            connection_type="local_folder",
            status="empty" if empty_complete_scan else "connected",
            auth_state="needs_content" if empty_complete_scan else "healthy",
            policy={"review_required": True, "allow_ai_context": True},
            metadata=metadata,
            last_error=scan.errors[0]["error"] if scan.errors else None,
            account_id=resolved_account_id,
        )
        scan_summary = scan.to_summary()
        state = {
            "connector": OBSIDIAN_SOURCE,
            "connector_version": scan_summary["connector_version"],
            "vault_name": scan.vault_name,
            "vault_id": scan.vault_id,
            "vault_path": scan.vault_path,
            "records_found": scan.records_found,
            "records_returned": scan.records_returned,
            "truncated": scan.truncated,
            "files_seen": scan.files_seen,
            "skipped": scan.skipped,
            "extensions": scan.extensions,
            "manifest_hash": scan.manifest_hash,
            "scan_errors": scan.errors,
        }
        complete_record_set = not scan.truncated and not scan.errors
        if not scan.records:
            archived_missing = 0
            if complete_record_set and scan.files_seen == 0:
                archived_missing = self._archive_missing_source_account_records(user_id, account["id"], set())
                state["last_batch_archived_missing"] = archived_missing
            cursor = self.upsert_sync_cursor(
                user_id,
                source=OBSIDIAN_SOURCE,
                source_account_id=account["id"],
                cursor_name=cursor_name,
                cursor_value=scan.cursor_value,
                high_water_mark=scan.high_water_mark,
                state=state,
                last_error=scan.errors[0]["error"] if scan.errors else None,
                completed=not scan.errors,
            )
            updated_account = self._source_account_by_id(user_id, account["id"]) or account
            return {
                "source_account_id": account["id"],
                "source": OBSIDIAN_SOURCE,
                "status": "partial" if scan.errors or scan.truncated else "empty",
                "processing": processing,
                "received": 0,
                "queued": 0,
                "saved": 0,
                "skipped": scan.skipped,
                "failed": len(scan.errors),
                "archived_missing": archived_missing,
                "capture_ids": [],
                "records": [],
                "errors": scan.errors,
                "cursor": cursor,
                "source_account": updated_account,
                "scan": scan_summary,
            }

        source_records = [record.to_source_account_record() for record in scan.records]
        active_external_ids = {str(record.get("external_id") or "").strip()[:240] for record in source_records if str(record.get("external_id") or "").strip()}
        if len(source_records) <= 500:
            result = self.sync_source_account_records(
                user_id,
                account["id"],
                records=source_records,
                cursor_name=cursor_name,
                cursor_value=scan.cursor_value,
                high_water_mark=scan.high_water_mark,
                state=state,
                processing=processing,
                archive_missing=complete_record_set,
                archive_external_ids=active_external_ids if complete_record_set else None,
            )
        else:
            batches = [source_records[index:index + 500] for index in range(0, len(source_records), 500)]
            result = {
                "source_account_id": account["id"],
                "source": OBSIDIAN_SOURCE,
                "status": "complete",
                "processing": processing,
                "received": 0,
                "queued": 0,
                "saved": 0,
                "skipped": 0,
                "failed": 0,
                "archived_missing": 0,
                "capture_ids": [],
                "records": [],
                "errors": [],
                "cursor": None,
            }
            for index, batch in enumerate(batches):
                is_last_batch = index == len(batches) - 1
                batch_state = dict(state)
                batch_state.update({
                    "batch_index": index + 1,
                    "batch_count": len(batches),
                    "batch_size": len(batch),
                    "records_returned": len(source_records),
                })
                batch_result = self.sync_source_account_records(
                    user_id,
                    account["id"],
                    records=batch,
                    cursor_name=cursor_name,
                    cursor_value=scan.cursor_value,
                    high_water_mark=scan.high_water_mark,
                    state=batch_state,
                    processing=processing,
                    archive_missing=complete_record_set and is_last_batch,
                    archive_external_ids=active_external_ids if complete_record_set and is_last_batch else None,
                )
                result["received"] += int(batch_result.get("received") or 0)
                result["queued"] += int(batch_result.get("queued") or 0)
                result["saved"] += int(batch_result.get("saved") or 0)
                result["skipped"] += int(batch_result.get("skipped") or 0)
                result["failed"] += int(batch_result.get("failed") or 0)
                result["archived_missing"] += int(batch_result.get("archived_missing") or 0)
                result["capture_ids"].extend(batch_result.get("capture_ids") or [])
                result["records"].extend(batch_result.get("records") or [])
                result["errors"].extend(batch_result.get("errors") or [])
                result["cursor"] = batch_result.get("cursor")
            if result["failed"] > 0:
                result["status"] = "partial"
        if scan.errors:
            result["errors"] = [*(result.get("errors") or []), *scan.errors]
            result["failed"] = int(result.get("failed") or 0) + len(scan.errors)
            if result.get("status") == "complete":
                result["status"] = "partial"
        if scan.truncated and result.get("status") == "complete":
            result["status"] = "partial"
        result["source_account"] = self._source_account_by_id(user_id, account["id"]) or account
        result["scan"] = scan_summary
        return result

    def _direct_connector_can_archive_missing(
        self,
        *,
        complete_snapshot: bool,
        sync_summary: dict[str, Any],
        sync_errors: list[dict[str, Any]] | None = None,
        max_records: int | None = None,
    ) -> bool:
        return bool(
            self._direct_connector_archive_missing_decision(
                complete_snapshot=complete_snapshot,
                sync_summary=sync_summary,
                sync_errors=sync_errors,
                max_records=max_records,
            )["allowed"]
        )

    def _direct_connector_archive_missing_decision(
        self,
        *,
        complete_snapshot: bool,
        sync_summary: dict[str, Any],
        sync_errors: list[dict[str, Any]] | None = None,
        max_records: int | None = None,
    ) -> dict[str, Any]:
        requested = bool(complete_snapshot)
        decision: dict[str, Any] = {
            "requested": requested,
            "allowed": False,
            "reason": "not_requested" if not requested else None,
        }
        if not complete_snapshot:
            return decision
        if sync_errors:
            decision["reason"] = "connector_errors"
            decision["error_count"] = len(sync_errors)
            return decision
        summary = sync_summary if isinstance(sync_summary, dict) else {}
        if summary.get("truncated"):
            decision["reason"] = "truncated"
            return decision
        for key in ("next_page_token", "next_page_cursor", "next_cursor", "next_page", "pending_high_water_mark"):
            if summary.get(key):
                decision["reason"] = "pagination_incomplete"
                decision["pagination_field"] = key
                return decision
        next_cursors = summary.get("next_cursors")
        if isinstance(next_cursors, dict) and any(str(value or "").strip() for value in next_cursors.values()):
            decision["reason"] = "pagination_incomplete"
            decision["pagination_field"] = "next_cursors"
            return decision
        if max_records is not None:
            try:
                returned = int(summary.get("records_returned") or 0)
                capped = int(max_records)
            except (TypeError, ValueError):
                returned = 0
                capped = 0
            if capped > 0 and returned >= capped:
                decision["reason"] = "record_cap_reached"
                decision["records_returned"] = returned
                decision["max_records"] = capped
                return decision
        decision["allowed"] = True
        decision["reason"] = "complete_snapshot"
        return decision

    def _with_direct_connector_archive_decision(
        self,
        result: dict[str, Any],
        *,
        complete_snapshot: bool,
        sync_summary: dict[str, Any],
        sync_errors: list[dict[str, Any]] | None = None,
        max_records: int | None = None,
    ) -> dict[str, Any]:
        decision = self._direct_connector_archive_missing_decision(
            complete_snapshot=complete_snapshot,
            sync_summary=sync_summary,
            sync_errors=sync_errors,
            max_records=max_records,
        )
        result["archive_missing_decision"] = decision
        if decision["requested"] and not decision["allowed"]:
            result["archive_missing_suppressed"] = True
        return result

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
        complete_snapshot: bool = False,
        api_base_url: str | None = None,
        request_json: Any | None = None,
    ) -> dict[str, Any]:
        from .connectors.github import GITHUB_SOURCE, fetch_github_records

        if processing not in {"sync", "async"}:
            raise ValueError("processing must be sync or async")
        try:
            capped_max_records = int(max_records or 100)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_records must be an integer") from exc
        if capped_max_records < 1 or capped_max_records > 500:
            raise ValueError("max_records must be between 1 and 500")
        bounded_max_comments = _bounded_int(max_comments_per_item, minimum=0, maximum=50)
        if bounded_max_comments is None:
            raise ValueError("max_comments_per_item must be between 0 and 50")
        self._ensure_source_account_can_sync(user_id, source_account_id, expected_source=GITHUB_SOURCE)

        sync = fetch_github_records(
            token=token,
            repositories=repositories,
            since=since,
            include_comments=bool(include_comments),
            max_comments_per_item=bounded_max_comments,
            max_records=capped_max_records,
            api_base_url=api_base_url or "https://api.github.com",
            request_json=request_json,
        )
        repository_key = ",".join(sync.repositories)
        resolved_account_id = (source_account_id or "").strip() or stable_id("sacct_", f"{user_id}:{GITHUB_SOURCE}:{repository_key}")
        label = (account_label or f"GitHub: {repository_key}").strip()[:160]
        identifier = (account_identifier or repository_key or "github").strip()[:240]
        sync_summary = sync.to_summary()
        error_message = sync.errors[0]["error"] if sync.errors else None
        metadata = {
            "connector": GITHUB_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "repositories": sync.repositories,
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "comments_found": sync.comments_found,
            "comments_returned": sync.comments_returned,
            "reviews_found": sync.reviews_found,
            "reviews_returned": sync.reviews_returned,
            "review_comments_found": sync.review_comments_found,
            "review_comments_returned": sync.review_comments_returned,
            "include_comments": bool(include_comments),
            "max_comments_per_item": bounded_max_comments,
            "token_configured": True,
            "api_base_url": sync.api_base_url,
        }
        metadata = _with_source_credential_ref(metadata, resolved_account_id, bool(str(token or "").strip()))
        metadata = _with_connector_failure_metadata(metadata, sync.errors)
        account = self.upsert_source_account(
            user_id,
            source=GITHUB_SOURCE,
            account_label=label,
            account_identifier=identifier,
            connection_type="api_token",
            status="needs_attention" if error_message else "connected",
            auth_state="error" if error_message else "healthy",
            policy={"review_required": True, "allow_ai_context": True},
            metadata=metadata,
            last_error=error_message,
            account_id=resolved_account_id,
        )
        if str(token or "").strip():
            self.store_source_account_credential(
                user_id,
                account["id"],
                source=GITHUB_SOURCE,
                payload={
                    "token": token,
                    "repositories": sync.repositories,
                    "include_comments": bool(include_comments),
                    "max_comments_per_item": bounded_max_comments,
                    "api_base_url": sync.api_base_url,
                },
            )
        state = {
            "connector": GITHUB_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "repositories": sync.repositories,
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "comments_found": sync.comments_found,
            "comments_returned": sync.comments_returned,
            "reviews_found": sync.reviews_found,
            "reviews_returned": sync.reviews_returned,
            "review_comments_found": sync.review_comments_found,
            "review_comments_returned": sync.review_comments_returned,
            "include_comments": bool(include_comments),
            "max_comments_per_item": bounded_max_comments,
            "sync_errors": sync.errors,
            "api_base_url": sync.api_base_url,
        }
        records = [record.to_source_account_record() for record in sync.records]
        archive_decision = self._direct_connector_archive_missing_decision(
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        empty_archived_missing = 0
        if not records and archive_decision["allowed"]:
            empty_archived_missing = self._archive_missing_source_account_records(user_id, account["id"], set())
        if not records:
            cursor = self.upsert_sync_cursor(
                user_id,
                source=GITHUB_SOURCE,
                source_account_id=account["id"],
                cursor_name=cursor_name,
                cursor_value=sync.cursor_value,
                high_water_mark=sync.high_water_mark,
                state={
                    **state,
                    "last_batch_received": 0,
                    "last_batch_saved": 0,
                    "last_batch_queued": 0,
                    "last_batch_skipped": 0,
                    "last_batch_failed": 0,
                    "last_batch_archived_missing": empty_archived_missing,
                },
                last_error=error_message,
                completed=not bool(error_message),
            )
            updated_account = self._source_account_by_id(user_id, account["id"]) or account
            return {
                "source_account_id": account["id"],
                "source": GITHUB_SOURCE,
                "status": "partial" if error_message else "empty",
                "processing": processing,
                "received": 0,
                "queued": 0,
                "saved": 0,
                "skipped": 0,
                "failed": len(sync.errors),
                "archived_missing": empty_archived_missing,
                "archive_missing_decision": archive_decision,
                "archive_missing_suppressed": bool(archive_decision["requested"] and not archive_decision["allowed"]),
                "capture_ids": [],
                "records": [],
                "errors": sync.errors,
                "cursor": cursor,
                "source_account": updated_account,
                "sync": sync_summary,
            }

        result = self.sync_source_account_records(
            user_id,
            account["id"],
            records=records,
            cursor_name=cursor_name,
            cursor_value=sync.cursor_value,
            high_water_mark=sync.high_water_mark,
            state=state,
            processing=processing,
            archive_missing=self._direct_connector_can_archive_missing(
                complete_snapshot=bool(complete_snapshot),
                sync_summary=sync_summary,
                sync_errors=sync.errors,
                max_records=capped_max_records,
            ),
            complete_snapshot=bool(complete_snapshot),
        )
        if sync.errors:
            result["errors"] = [*(result.get("errors") or []), *sync.errors]
            result["failed"] = int(result.get("failed") or 0) + len(sync.errors)
            if result.get("status") == "complete":
                result["status"] = "partial"
        result["source_account"] = self._source_account_by_id(user_id, account["id"]) or account
        result["sync"] = sync_summary
        result = self._with_direct_connector_archive_decision(
            result,
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        return result

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
        complete_snapshot: bool = False,
        api_base_url: str | None = None,
        request_json: Any | None = None,
    ) -> dict[str, Any]:
        from .connectors.gmail import GMAIL_SOURCE, fetch_gmail_records

        if processing not in {"sync", "async"}:
            raise ValueError("processing must be sync or async")
        try:
            capped_max_records = int(max_records or 50)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_records must be an integer") from exc
        if capped_max_records < 1 or capped_max_records > 200:
            raise ValueError("max_records must be between 1 and 200")
        self._ensure_source_account_can_sync(user_id, source_account_id, expected_source=GMAIL_SOURCE)

        sync = fetch_gmail_records(
            access_token=access_token,
            query=query,
            label_ids=label_ids or [],
            since=since,
            page_token=page_token,
            max_records=capped_max_records,
            include_body=bool(include_body),
            api_base_url=api_base_url or "https://gmail.googleapis.com/gmail/v1",
            request_json=request_json,
        )
        identifier = (account_identifier or sync.user_email or "gmail").strip()[:240]
        resolved_account_id = (source_account_id or "").strip() or stable_id("sacct_", f"{user_id}:{GMAIL_SOURCE}:{identifier}")
        label = (account_label or f"Gmail: {identifier}").strip()[:160]
        sync_summary = sync.to_summary()
        error_message = sync.errors[0]["error"] if sync.errors else None
        metadata = {
            "connector": GMAIL_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "token_configured": True,
            "content_sync_enabled": bool(include_body),
            "query_configured": bool(str(query or "").strip()),
            "label_ids": sync.label_ids,
            "api_base_url": sync.api_base_url,
        }
        if sync.user_email:
            metadata["email"] = sync.user_email
            metadata["user_email"] = sync.user_email
        if sync.query:
            metadata["query"] = sync.query
        metadata = _with_source_credential_ref(metadata, resolved_account_id, bool(str(access_token or "").strip()))
        metadata = _with_connector_failure_metadata(metadata, sync.errors)
        account = self.upsert_source_account(
            user_id,
            source=GMAIL_SOURCE,
            account_label=label,
            account_identifier=identifier,
            connection_type="oauth_token",
            status="needs_attention" if error_message else "connected",
            auth_state="error" if error_message else "healthy",
            policy={"review_required": True, "allow_ai_context": True},
            metadata=metadata,
            last_error=error_message,
            account_id=resolved_account_id,
        )
        if str(access_token or "").strip():
            self.store_source_account_credential(
                user_id,
                account["id"],
                source=GMAIL_SOURCE,
                payload={
                    "access_token": access_token,
                    "query": query or "",
                    "label_ids": sync.label_ids,
                    "include_body": bool(include_body),
                    "api_base_url": sync.api_base_url,
                    **_oauth_refresh_credential_fields(self._read_source_account_credential_payload(user_id, resolved_account_id)),
                },
            )
        state = {
            "connector": GMAIL_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "sync_errors": sync.errors,
            "next_page_token": sync.next_page_token,
            "query": sync.query,
            "label_ids": sync.label_ids,
            "api_base_url": sync.api_base_url,
        }
        records = [record.to_source_account_record() for record in sync.records]
        archive_decision = self._direct_connector_archive_missing_decision(
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        empty_archived_missing = 0
        if not records and archive_decision["allowed"]:
            empty_archived_missing = self._archive_missing_source_account_records(user_id, account["id"], set())
        if not records:
            cursor = self.upsert_sync_cursor(
                user_id,
                source=GMAIL_SOURCE,
                source_account_id=account["id"],
                cursor_name=cursor_name,
                cursor_value=sync.cursor_value,
                high_water_mark=sync.high_water_mark,
                state={
                    **state,
                    "last_batch_received": 0,
                    "last_batch_saved": 0,
                    "last_batch_queued": 0,
                    "last_batch_skipped": 0,
                    "last_batch_failed": 0,
                    "last_batch_archived_missing": empty_archived_missing,
                },
                last_error=error_message,
                completed=not bool(error_message),
            )
            updated_account = self._source_account_by_id(user_id, account["id"]) or account
            return {
                "source_account_id": account["id"],
                "source": GMAIL_SOURCE,
                "status": "partial" if error_message else "empty",
                "processing": processing,
                "received": 0,
                "queued": 0,
                "saved": 0,
                "skipped": 0,
                "failed": len(sync.errors),
                "archived_missing": empty_archived_missing,
                "archive_missing_decision": archive_decision,
                "archive_missing_suppressed": bool(archive_decision["requested"] and not archive_decision["allowed"]),
                "capture_ids": [],
                "records": [],
                "errors": sync.errors,
                "cursor": cursor,
                "source_account": updated_account,
                "sync": sync_summary,
            }

        result = self.sync_source_account_records(
            user_id,
            account["id"],
            records=records,
            cursor_name=cursor_name,
            cursor_value=sync.cursor_value,
            high_water_mark=sync.high_water_mark,
            state=state,
            processing=processing,
            archive_missing=self._direct_connector_can_archive_missing(
                complete_snapshot=bool(complete_snapshot),
                sync_summary=sync_summary,
                sync_errors=sync.errors,
                max_records=capped_max_records,
            ),
            complete_snapshot=bool(complete_snapshot),
        )
        if sync.errors:
            result["errors"] = [*(result.get("errors") or []), *sync.errors]
            result["failed"] = int(result.get("failed") or 0) + len(sync.errors)
            if result.get("status") == "complete":
                result["status"] = "partial"
        result["source_account"] = self._source_account_by_id(user_id, account["id"]) or account
        result["sync"] = sync_summary
        result = self._with_direct_connector_archive_decision(
            result,
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        return result

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
        complete_snapshot: bool = False,
        api_base_url: str | None = None,
        request_value: Any | None = None,
    ) -> dict[str, Any]:
        from .connectors.google_drive import GOOGLE_DRIVE_SOURCE, fetch_google_drive_records

        if processing not in {"sync", "async"}:
            raise ValueError("processing must be sync or async")
        try:
            capped_max_records = int(max_records or 50)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_records must be an integer") from exc
        if capped_max_records < 1 or capped_max_records > 200:
            raise ValueError("max_records must be between 1 and 200")
        self._ensure_source_account_can_sync(user_id, source_account_id, expected_source=GOOGLE_DRIVE_SOURCE)

        sync = fetch_google_drive_records(
            access_token=access_token,
            query=query,
            mime_types=mime_types or [],
            since=since,
            page_token=page_token,
            max_records=capped_max_records,
            include_content=bool(include_content),
            api_base_url=api_base_url or "https://www.googleapis.com/drive/v3",
            request_value=request_value,
        )
        identifier = (account_identifier or sync.user_email or "google-drive").strip()[:240]
        resolved_account_id = (source_account_id or "").strip() or stable_id("sacct_", f"{user_id}:{GOOGLE_DRIVE_SOURCE}:{identifier}")
        label = (account_label or f"Google Drive: {identifier}").strip()[:160]
        sync_summary = sync.to_summary()
        error_message = sync.errors[0]["error"] if sync.errors else None
        metadata = {
            "connector": GOOGLE_DRIVE_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "skipped_unsupported": sync.skipped_unsupported,
            "token_configured": True,
            "content_sync_enabled": bool(include_content),
            "query_configured": bool(str(query or "").strip()),
            "mime_types": sync.mime_types,
            "api_base_url": sync.api_base_url,
        }
        if sync.user_email:
            metadata["email"] = sync.user_email
            metadata["user_email"] = sync.user_email
        if sync.query:
            metadata["query"] = sync.query
        metadata = _with_source_credential_ref(metadata, resolved_account_id, bool(str(access_token or "").strip()))
        metadata = _with_connector_failure_metadata(metadata, sync.errors)
        account = self.upsert_source_account(
            user_id,
            source=GOOGLE_DRIVE_SOURCE,
            account_label=label,
            account_identifier=identifier,
            connection_type="oauth_token",
            status="needs_attention" if error_message else "connected",
            auth_state="error" if error_message else "healthy",
            policy={"review_required": True, "allow_ai_context": True},
            metadata=metadata,
            last_error=error_message,
            account_id=resolved_account_id,
        )
        if str(access_token or "").strip():
            self.store_source_account_credential(
                user_id,
                account["id"],
                source=GOOGLE_DRIVE_SOURCE,
                payload={
                    "access_token": access_token,
                    "query": query or "",
                    "mime_types": sync.mime_types,
                    "include_content": bool(include_content),
                    "api_base_url": sync.api_base_url,
                    **_oauth_refresh_credential_fields(self._read_source_account_credential_payload(user_id, resolved_account_id)),
                },
            )
        state = {
            "connector": GOOGLE_DRIVE_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "skipped_unsupported": sync.skipped_unsupported,
            "sync_errors": sync.errors,
            "next_page_token": sync.next_page_token,
            "query": sync.query,
            "mime_types": sync.mime_types,
            "api_base_url": sync.api_base_url,
        }
        records = [record.to_source_account_record() for record in sync.records]
        archive_decision = self._direct_connector_archive_missing_decision(
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        empty_archived_missing = 0
        if not records and archive_decision["allowed"]:
            empty_archived_missing = self._archive_missing_source_account_records(user_id, account["id"], set())
        if not records:
            cursor = self.upsert_sync_cursor(
                user_id,
                source=GOOGLE_DRIVE_SOURCE,
                source_account_id=account["id"],
                cursor_name=cursor_name,
                cursor_value=sync.cursor_value,
                high_water_mark=sync.high_water_mark,
                state={
                    **state,
                    "last_batch_received": 0,
                    "last_batch_saved": 0,
                    "last_batch_queued": 0,
                    "last_batch_skipped": sync.skipped_unsupported,
                    "last_batch_failed": 0,
                    "last_batch_archived_missing": empty_archived_missing,
                },
                last_error=error_message,
                completed=not bool(error_message),
            )
            updated_account = self._source_account_by_id(user_id, account["id"]) or account
            return {
                "source_account_id": account["id"],
                "source": GOOGLE_DRIVE_SOURCE,
                "status": "partial" if error_message else "empty",
                "processing": processing,
                "received": 0,
                "queued": 0,
                "saved": 0,
                "skipped": sync.skipped_unsupported,
                "failed": len(sync.errors),
                "archived_missing": empty_archived_missing,
                "archive_missing_decision": archive_decision,
                "archive_missing_suppressed": bool(archive_decision["requested"] and not archive_decision["allowed"]),
                "capture_ids": [],
                "records": [],
                "errors": sync.errors,
                "cursor": cursor,
                "source_account": updated_account,
                "sync": sync_summary,
            }

        result = self.sync_source_account_records(
            user_id,
            account["id"],
            records=records,
            cursor_name=cursor_name,
            cursor_value=sync.cursor_value,
            high_water_mark=sync.high_water_mark,
            state=state,
            processing=processing,
            archive_missing=self._direct_connector_can_archive_missing(
                complete_snapshot=bool(complete_snapshot),
                sync_summary=sync_summary,
                sync_errors=sync.errors,
                max_records=capped_max_records,
            ),
            complete_snapshot=bool(complete_snapshot),
        )
        if sync.errors:
            result["errors"] = [*(result.get("errors") or []), *sync.errors]
            result["failed"] = int(result.get("failed") or 0) + len(sync.errors)
            if result.get("status") == "complete":
                result["status"] = "partial"
        result["skipped"] = int(result.get("skipped") or 0) + sync.skipped_unsupported
        result["source_account"] = self._source_account_by_id(user_id, account["id"]) or account
        result["sync"] = sync_summary
        result = self._with_direct_connector_archive_decision(
            result,
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        return result

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
        complete_snapshot: bool = False,
        api_base_url: str | None = None,
        request_json: Any | None = None,
    ) -> dict[str, Any]:
        from .connectors.outlook import OUTLOOK_SOURCE, fetch_outlook_records

        if processing not in {"sync", "async"}:
            raise ValueError("processing must be sync or async")
        try:
            capped_max_records = int(max_records or 50)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_records must be an integer") from exc
        if capped_max_records < 1 or capped_max_records > 200:
            raise ValueError("max_records must be between 1 and 200")
        self._ensure_source_account_can_sync(user_id, source_account_id, expected_source=OUTLOOK_SOURCE)

        sync = fetch_outlook_records(
            access_token=access_token,
            query=query,
            since=since,
            page_token=page_token,
            max_records=capped_max_records,
            include_body=bool(include_body),
            api_base_url=api_base_url or "https://graph.microsoft.com/v1.0",
            request_json=request_json,
        )
        identifier = (account_identifier or sync.user_email or "outlook").strip()[:240]
        resolved_account_id = (source_account_id or "").strip() or stable_id("sacct_", f"{user_id}:{OUTLOOK_SOURCE}:{identifier}")
        label = (account_label or f"Outlook: {identifier}").strip()[:160]
        sync_summary = sync.to_summary()
        error_message = sync.errors[0]["error"] if sync.errors else None
        metadata = {
            "connector": OUTLOOK_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "token_configured": True,
            "content_sync_enabled": bool(include_body),
            "query_configured": bool(str(query or "").strip()),
            "api_base_url": sync.api_base_url,
        }
        if sync.user_email:
            metadata["email"] = sync.user_email
            metadata["user_email"] = sync.user_email
        if sync.query:
            metadata["query"] = sync.query
        metadata = _with_source_credential_ref(metadata, resolved_account_id, bool(str(access_token or "").strip()))
        metadata = _with_connector_failure_metadata(metadata, sync.errors)
        account = self.upsert_source_account(
            user_id,
            source=OUTLOOK_SOURCE,
            account_label=label,
            account_identifier=identifier,
            connection_type="oauth_token",
            status="needs_attention" if error_message else "connected",
            auth_state="error" if error_message else "healthy",
            policy={"review_required": True, "allow_ai_context": True},
            metadata=metadata,
            last_error=error_message,
            account_id=resolved_account_id,
        )
        if str(access_token or "").strip():
            self.store_source_account_credential(
                user_id,
                account["id"],
                source=OUTLOOK_SOURCE,
                payload={
                    "access_token": access_token,
                    "query": query or "",
                    "include_body": bool(include_body),
                    "api_base_url": sync.api_base_url,
                    **_oauth_refresh_credential_fields(self._read_source_account_credential_payload(user_id, resolved_account_id)),
                },
            )
        state = {
            "connector": OUTLOOK_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "sync_errors": sync.errors,
            "next_page_token": sync.next_page_token,
            "query": sync.query,
            "api_base_url": sync.api_base_url,
        }
        records = [record.to_source_account_record() for record in sync.records]
        archive_decision = self._direct_connector_archive_missing_decision(
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        empty_archived_missing = 0
        if not records and archive_decision["allowed"]:
            empty_archived_missing = self._archive_missing_source_account_records(user_id, account["id"], set())
        if not records:
            cursor = self.upsert_sync_cursor(
                user_id,
                source=OUTLOOK_SOURCE,
                source_account_id=account["id"],
                cursor_name=cursor_name,
                cursor_value=sync.cursor_value,
                high_water_mark=sync.high_water_mark,
                state={
                    **state,
                    "last_batch_received": 0,
                    "last_batch_saved": 0,
                    "last_batch_queued": 0,
                    "last_batch_skipped": 0,
                    "last_batch_failed": len(sync.errors),
                    "last_batch_archived_missing": empty_archived_missing,
                },
                last_error=error_message,
                completed=not bool(error_message),
            )
            updated_account = self._source_account_by_id(user_id, account["id"]) or account
            return {
                "source_account_id": account["id"],
                "source": OUTLOOK_SOURCE,
                "status": "partial" if error_message else "empty",
                "processing": processing,
                "received": 0,
                "queued": 0,
                "saved": 0,
                "skipped": 0,
                "failed": len(sync.errors),
                "archived_missing": empty_archived_missing,
                "archive_missing_decision": archive_decision,
                "archive_missing_suppressed": bool(archive_decision["requested"] and not archive_decision["allowed"]),
                "capture_ids": [],
                "records": [],
                "errors": sync.errors,
                "cursor": cursor,
                "source_account": updated_account,
                "sync": sync_summary,
            }

        result = self.sync_source_account_records(
            user_id,
            account["id"],
            records=records,
            cursor_name=cursor_name,
            cursor_value=sync.cursor_value,
            high_water_mark=sync.high_water_mark,
            state=state,
            processing=processing,
            archive_missing=self._direct_connector_can_archive_missing(
                complete_snapshot=bool(complete_snapshot),
                sync_summary=sync_summary,
                sync_errors=sync.errors,
                max_records=capped_max_records,
            ),
            complete_snapshot=bool(complete_snapshot),
        )
        if sync.errors:
            result["errors"] = [*(result.get("errors") or []), *sync.errors]
            result["failed"] = int(result.get("failed") or 0) + len(sync.errors)
            if result.get("status") == "complete":
                result["status"] = "partial"
        result["source_account"] = self._source_account_by_id(user_id, account["id"]) or account
        result["sync"] = sync_summary
        result = self._with_direct_connector_archive_decision(
            result,
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        return result

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
        page_cursors: dict[str, str] | None = None,
        processing: str = "sync",
        max_records: int = 100,
        cursor_name: str = "messages",
        workspace_url: str | None = None,
        complete_snapshot: bool = False,
        api_base_url: str | None = None,
        request_json: Any | None = None,
    ) -> dict[str, Any]:
        from .connectors.slack import SLACK_SOURCE, fetch_slack_records

        if processing not in {"sync", "async"}:
            raise ValueError("processing must be sync or async")
        try:
            capped_max_records = int(max_records or 100)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_records must be an integer") from exc
        if capped_max_records < 1 or capped_max_records > 200:
            raise ValueError("max_records must be between 1 and 200")
        self._ensure_source_account_can_sync(user_id, source_account_id, expected_source=SLACK_SOURCE)

        sync = fetch_slack_records(
            token=token,
            channels=channels,
            since=since,
            page_cursors=page_cursors,
            max_records=capped_max_records,
            workspace_url=workspace_url,
            api_base_url=api_base_url or "https://slack.com/api",
            request_json=request_json,
        )
        channel_key = ",".join(str(item.get("name") or item.get("id") or "") for item in sync.channels if item.get("id") or item.get("name"))
        auth_identity = sync.auth_identity if isinstance(sync.auth_identity, dict) else {}
        auth_user_id = str(auth_identity.get("user_id") or "").strip()
        auth_user_name = str(auth_identity.get("user") or "").strip()
        resolved_account_id = (source_account_id or "").strip() or stable_id("sacct_", f"{user_id}:{SLACK_SOURCE}:{channel_key}")
        label = (account_label or f"Slack: {auth_user_name or channel_key}").strip()[:160]
        identifier = (account_identifier or auth_user_id or channel_key or "slack").strip()[:240]
        sync_summary = sync.to_summary()
        error_message = sync.errors[0]["error"] if sync.errors else None
        metadata = {
            "connector": SLACK_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "channels": sync.channels,
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "token_configured": True,
            "workspace_url_configured": bool(str(workspace_url or "").strip()),
            "api_base_url": sync.api_base_url,
        }
        if auth_user_id:
            metadata["user_id"] = auth_user_id
            metadata["slack_user_id"] = auth_user_id
        if auth_user_name:
            metadata["user_name"] = auth_user_name
        if auth_identity.get("team_id"):
            metadata["team_id"] = auth_identity["team_id"]
        if auth_identity.get("team"):
            metadata["team"] = auth_identity["team"]
        metadata = _with_source_credential_ref(metadata, resolved_account_id, bool(str(token or "").strip()))
        metadata = _with_connector_failure_metadata(metadata, sync.errors)
        account = self.upsert_source_account(
            user_id,
            source=SLACK_SOURCE,
            account_label=label,
            account_identifier=identifier,
            connection_type="api_token",
            status="needs_attention" if error_message else "connected",
            auth_state="error" if error_message else "healthy",
            policy={"review_required": True, "allow_ai_context": True},
            metadata=metadata,
            last_error=error_message,
            account_id=resolved_account_id,
        )
        if str(token or "").strip():
            self.store_source_account_credential(
                user_id,
                account["id"],
                source=SLACK_SOURCE,
                payload={
                    "token": token,
                    "channels": channels,
                    "workspace_url": workspace_url or "",
                    "api_base_url": sync.api_base_url,
                },
            )
        state = {
            "connector": SLACK_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "channels": sync.channels,
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "sync_errors": sync.errors,
            "next_cursors": sync_summary.get("next_cursors") or {},
            "api_base_url": sync.api_base_url,
        }
        records = [record.to_source_account_record() for record in sync.records]
        archive_decision = self._direct_connector_archive_missing_decision(
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        empty_archived_missing = 0
        if not records and archive_decision["allowed"]:
            empty_archived_missing = self._archive_missing_source_account_records(user_id, account["id"], set())
        if not records:
            cursor = self.upsert_sync_cursor(
                user_id,
                source=SLACK_SOURCE,
                source_account_id=account["id"],
                cursor_name=cursor_name,
                cursor_value=sync.cursor_value,
                high_water_mark=sync.high_water_mark,
                state={
                    **state,
                    "last_batch_received": 0,
                    "last_batch_saved": 0,
                    "last_batch_queued": 0,
                    "last_batch_skipped": 0,
                    "last_batch_failed": 0,
                    "last_batch_archived_missing": empty_archived_missing,
                },
                last_error=error_message,
                completed=not bool(error_message),
            )
            updated_account = self._source_account_by_id(user_id, account["id"]) or account
            return {
                "source_account_id": account["id"],
                "source": SLACK_SOURCE,
                "status": "partial" if error_message else "empty",
                "processing": processing,
                "received": 0,
                "queued": 0,
                "saved": 0,
                "skipped": 0,
                "failed": len(sync.errors),
                "archived_missing": empty_archived_missing,
                "archive_missing_decision": archive_decision,
                "archive_missing_suppressed": bool(archive_decision["requested"] and not archive_decision["allowed"]),
                "capture_ids": [],
                "records": [],
                "errors": sync.errors,
                "cursor": cursor,
                "source_account": updated_account,
                "sync": sync_summary,
            }

        result = self.sync_source_account_records(
            user_id,
            account["id"],
            records=records,
            cursor_name=cursor_name,
            cursor_value=sync.cursor_value,
            high_water_mark=sync.high_water_mark,
            state=state,
            processing=processing,
            archive_missing=self._direct_connector_can_archive_missing(
                complete_snapshot=bool(complete_snapshot),
                sync_summary=sync_summary,
                sync_errors=sync.errors,
                max_records=capped_max_records,
            ),
            complete_snapshot=bool(complete_snapshot),
        )
        if sync.errors:
            result["errors"] = [*(result.get("errors") or []), *sync.errors]
            result["failed"] = int(result.get("failed") or 0) + len(sync.errors)
            if result.get("status") == "complete":
                result["status"] = "partial"
        result["source_account"] = self._source_account_by_id(user_id, account["id"]) or account
        result["sync"] = sync_summary
        result = self._with_direct_connector_archive_decision(
            result,
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        return result

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
        complete_snapshot: bool = False,
        api_base_url: str | None = None,
        request_json: Any | None = None,
    ) -> dict[str, Any]:
        from .connectors.readwise import READWISE_SOURCE, fetch_readwise_records

        if processing not in {"sync", "async"}:
            raise ValueError("processing must be sync or async")
        try:
            capped_max_records = int(max_records or 100)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_records must be an integer") from exc
        if capped_max_records < 1 or capped_max_records > 500:
            raise ValueError("max_records must be between 1 and 500")
        self._ensure_source_account_can_sync(user_id, source_account_id, expected_source=READWISE_SOURCE)

        sync = fetch_readwise_records(
            token=token,
            since=since,
            page_cursor=page_cursor,
            max_records=capped_max_records,
            api_base_url=api_base_url or "https://readwise.io/api/v2",
            request_json=request_json,
        )
        identifier = (account_identifier or "readwise").strip()[:240]
        resolved_account_id = (source_account_id or "").strip() or stable_id("sacct_", f"{user_id}:{READWISE_SOURCE}:{identifier}")
        label = (account_label or "Readwise Highlights").strip()[:160]
        sync_summary = sync.to_summary()
        error_message = sync.errors[0]["error"] if sync.errors else None
        metadata = {
            "connector": READWISE_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "token_configured": True,
            "api_base_url": sync.api_base_url,
        }
        metadata = _with_source_credential_ref(metadata, resolved_account_id, bool(str(token or "").strip()))
        metadata = _with_connector_failure_metadata(metadata, sync.errors)
        account = self.upsert_source_account(
            user_id,
            source=READWISE_SOURCE,
            account_label=label,
            account_identifier=identifier,
            connection_type="api_token",
            status="needs_attention" if error_message else "connected",
            auth_state="error" if error_message else "healthy",
            policy={"review_required": True, "allow_ai_context": True},
            metadata=metadata,
            last_error=error_message,
            account_id=resolved_account_id,
        )
        if str(token or "").strip():
            self.store_source_account_credential(
                user_id,
                account["id"],
                source=READWISE_SOURCE,
                payload={
                    "token": token,
                    "api_base_url": sync.api_base_url,
                },
            )
        state = {
            "connector": READWISE_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "sync_errors": sync.errors,
            "next_page_cursor": sync.next_page_cursor,
            "api_base_url": sync.api_base_url,
        }
        records = [record.to_source_account_record() for record in sync.records]
        archive_decision = self._direct_connector_archive_missing_decision(
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        empty_archived_missing = 0
        if not records and archive_decision["allowed"]:
            empty_archived_missing = self._archive_missing_source_account_records(user_id, account["id"], set())
        if not records:
            cursor = self.upsert_sync_cursor(
                user_id,
                source=READWISE_SOURCE,
                source_account_id=account["id"],
                cursor_name=cursor_name,
                cursor_value=sync.cursor_value,
                high_water_mark=sync.high_water_mark,
                state={
                    **state,
                    "last_batch_received": 0,
                    "last_batch_saved": 0,
                    "last_batch_queued": 0,
                    "last_batch_skipped": 0,
                    "last_batch_failed": 0,
                    "last_batch_archived_missing": empty_archived_missing,
                },
                last_error=error_message,
                completed=not bool(error_message),
            )
            updated_account = self._source_account_by_id(user_id, account["id"]) or account
            return {
                "source_account_id": account["id"],
                "source": READWISE_SOURCE,
                "status": "partial" if error_message else "empty",
                "processing": processing,
                "received": 0,
                "queued": 0,
                "saved": 0,
                "skipped": 0,
                "failed": len(sync.errors),
                "archived_missing": empty_archived_missing,
                "archive_missing_decision": archive_decision,
                "archive_missing_suppressed": bool(archive_decision["requested"] and not archive_decision["allowed"]),
                "capture_ids": [],
                "records": [],
                "errors": sync.errors,
                "cursor": cursor,
                "source_account": updated_account,
                "sync": sync_summary,
            }

        result = self.sync_source_account_records(
            user_id,
            account["id"],
            records=records,
            cursor_name=cursor_name,
            cursor_value=sync.cursor_value,
            high_water_mark=sync.high_water_mark,
            state=state,
            processing=processing,
            archive_missing=self._direct_connector_can_archive_missing(
                complete_snapshot=bool(complete_snapshot),
                sync_summary=sync_summary,
                sync_errors=sync.errors,
                max_records=capped_max_records,
            ),
            complete_snapshot=bool(complete_snapshot),
        )
        if sync.errors:
            result["errors"] = [*(result.get("errors") or []), *sync.errors]
            result["failed"] = int(result.get("failed") or 0) + len(sync.errors)
            if result.get("status") == "complete":
                result["status"] = "partial"
        result["source_account"] = self._source_account_by_id(user_id, account["id"]) or account
        result["sync"] = sync_summary
        result = self._with_direct_connector_archive_decision(
            result,
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        return result

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
        complete_snapshot: bool = False,
        read_text: Any | None = None,
        request_text: Any | None = None,
    ) -> dict[str, Any]:
        from .connectors.calendar import CALENDAR_SOURCE, fetch_calendar_records

        if processing not in {"sync", "async"}:
            raise ValueError("processing must be sync or async")
        try:
            capped_max_records = int(max_records or 100)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_records must be an integer") from exc
        if capped_max_records < 1 or capped_max_records > 500:
            raise ValueError("max_records must be between 1 and 500")
        self._ensure_source_account_can_sync(user_id, source_account_id, expected_source=CALENDAR_SOURCE)

        sync = fetch_calendar_records(
            ics_path=ics_path,
            feed_url=feed_url,
            since=since,
            max_records=capped_max_records,
            read_text=read_text,
            request_text=request_text,
        )
        input_type = sync.input_type
        identifier = (account_identifier or f"{input_type}:{sync.source_label}").strip()[:240]
        resolved_account_id = (source_account_id or "").strip() or stable_id("sacct_", f"{user_id}:{CALENDAR_SOURCE}:{identifier}")
        label = (account_label or f"Calendar: {sync.source_label}").strip()[:160]
        sync_summary = sync.to_summary()
        error_message = sync.errors[0]["error"] if sync.errors else None
        metadata = {
            "connector": CALENDAR_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "input_type": input_type,
            "source_label": sync.source_label,
            "path_redacted": True,
            "feed_url_redacted": True,
        }
        metadata = _with_source_credential_ref(metadata, resolved_account_id, bool(str(ics_path or feed_url or "").strip()))
        metadata = _with_connector_failure_metadata(metadata, sync.errors)
        account = self.upsert_source_account(
            user_id,
            source=CALENDAR_SOURCE,
            account_label=label,
            account_identifier=identifier,
            connection_type="calendar_feed" if input_type == "feed" else "local_file",
            status="needs_attention" if error_message else "connected",
            auth_state="error" if error_message else "healthy",
            policy={"review_required": True, "allow_ai_context": True},
            metadata=metadata,
            last_error=error_message,
            account_id=resolved_account_id,
        )
        if str(ics_path or feed_url or "").strip():
            self.store_source_account_credential(
                user_id,
                account["id"],
                source=CALENDAR_SOURCE,
                payload={
                    "ics_path": ics_path or "",
                    "feed_url": feed_url or "",
                    "input_type": input_type,
                    "source_label": sync.source_label,
                },
            )
        state = {
            "connector": CALENDAR_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "sync_errors": sync.errors,
            "input_type": input_type,
            "source_label": sync.source_label,
            "path_redacted": True,
            "feed_url_redacted": True,
        }
        records = [record.to_source_account_record() for record in sync.records]
        archive_decision = self._direct_connector_archive_missing_decision(
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        empty_archived_missing = 0
        if not records and archive_decision["allowed"]:
            empty_archived_missing = self._archive_missing_source_account_records(user_id, account["id"], set())
        if not records:
            cursor_payload = self.upsert_sync_cursor(
                user_id,
                source=CALENDAR_SOURCE,
                source_account_id=account["id"],
                cursor_name=cursor_name,
                cursor_value=sync.cursor_value,
                high_water_mark=sync.high_water_mark,
                state={
                    **state,
                    "last_batch_received": 0,
                    "last_batch_saved": 0,
                    "last_batch_queued": 0,
                    "last_batch_skipped": 0,
                    "last_batch_failed": 0,
                    "last_batch_archived_missing": empty_archived_missing,
                },
                last_error=error_message,
                completed=not bool(error_message),
            )
            updated_account = self._source_account_by_id(user_id, account["id"]) or account
            return {
                "source_account_id": account["id"],
                "source": CALENDAR_SOURCE,
                "status": "partial" if error_message else "empty",
                "processing": processing,
                "received": 0,
                "queued": 0,
                "saved": 0,
                "skipped": 0,
                "failed": len(sync.errors),
                "archived_missing": empty_archived_missing,
                "archive_missing_decision": archive_decision,
                "archive_missing_suppressed": bool(archive_decision["requested"] and not archive_decision["allowed"]),
                "capture_ids": [],
                "records": [],
                "errors": sync.errors,
                "cursor": cursor_payload,
                "source_account": updated_account,
                "sync": sync_summary,
            }

        result = self.sync_source_account_records(
            user_id,
            account["id"],
            records=records,
            cursor_name=cursor_name,
            cursor_value=sync.cursor_value,
            high_water_mark=sync.high_water_mark,
            state=state,
            processing=processing,
            archive_missing=self._direct_connector_can_archive_missing(
                complete_snapshot=bool(complete_snapshot),
                sync_summary=sync_summary,
                sync_errors=sync.errors,
                max_records=capped_max_records,
            ),
            complete_snapshot=bool(complete_snapshot),
        )
        if sync.errors:
            result["errors"] = [*(result.get("errors") or []), *sync.errors]
            result["failed"] = int(result.get("failed") or 0) + len(sync.errors)
            if result.get("status") == "complete":
                result["status"] = "partial"
        result["source_account"] = self._source_account_by_id(user_id, account["id"]) or account
        result["sync"] = sync_summary
        result = self._with_direct_connector_archive_decision(
            result,
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        return result

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
        pending_high_water_mark: str | None = None,
        processing: str = "sync",
        max_records: int = 100,
        cursor_name: str = "raindrops",
        include_highlights: bool = True,
        complete_snapshot: bool = False,
        api_base_url: str | None = None,
        request_json: Any | None = None,
    ) -> dict[str, Any]:
        from .connectors.raindrop import RAINDROP_SOURCE, fetch_raindrop_records

        if processing not in {"sync", "async"}:
            raise ValueError("processing must be sync or async")
        try:
            capped_max_records = int(max_records or 100)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_records must be an integer") from exc
        if capped_max_records < 1 or capped_max_records > 500:
            raise ValueError("max_records must be between 1 and 500")
        self._ensure_source_account_can_sync(user_id, source_account_id, expected_source=RAINDROP_SOURCE)

        sync = fetch_raindrop_records(
            token=token,
            collection_id=collection_id,
            since=since,
            page=page,
            pending_high_water_mark=pending_high_water_mark,
            max_records=capped_max_records,
            include_highlights=bool(include_highlights),
            api_base_url=api_base_url or "https://api.raindrop.io/rest/v1",
            request_json=request_json,
        )
        identifier = (account_identifier or f"collection:{sync.collection_id}").strip()[:240]
        resolved_account_id = (source_account_id or "").strip() or stable_id("sacct_", f"{user_id}:{RAINDROP_SOURCE}:{identifier}")
        label = (account_label or "Raindrop Bookmarks").strip()[:160]
        sync_summary = sync.to_summary()
        error_message = sync.errors[0]["error"] if sync.errors else None
        metadata = {
            "connector": RAINDROP_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "collection_id": sync.collection_id,
            "token_configured": True,
            "api_base_url": sync.api_base_url,
            "include_highlights": bool(include_highlights),
        }
        metadata = _with_source_credential_ref(metadata, resolved_account_id, bool(str(token or "").strip()))
        metadata = _with_connector_failure_metadata(metadata, sync.errors)
        account = self.upsert_source_account(
            user_id,
            source=RAINDROP_SOURCE,
            account_label=label,
            account_identifier=identifier,
            connection_type="api_token",
            status="needs_attention" if error_message else "connected",
            auth_state="error" if error_message else "healthy",
            policy={"review_required": True, "allow_ai_context": True},
            metadata=metadata,
            last_error=error_message,
            account_id=resolved_account_id,
        )
        if str(token or "").strip():
            self.store_source_account_credential(
                user_id,
                account["id"],
                source=RAINDROP_SOURCE,
                payload={
                    "token": token,
                    "collection_id": sync.collection_id,
                    "api_base_url": sync.api_base_url,
                    "include_highlights": bool(include_highlights),
                },
            )
        state = {
            "connector": RAINDROP_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "sync_errors": sync.errors,
            "next_page": sync.next_page,
            "pending_high_water_mark": sync.pending_high_water_mark,
            "collection_id": sync.collection_id,
            "api_base_url": sync.api_base_url,
            "include_highlights": bool(include_highlights),
        }
        records = [record.to_source_account_record() for record in sync.records]
        archive_decision = self._direct_connector_archive_missing_decision(
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        empty_archived_missing = 0
        if not records and archive_decision["allowed"]:
            empty_archived_missing = self._archive_missing_source_account_records(user_id, account["id"], set())
        if not records:
            cursor_payload = self.upsert_sync_cursor(
                user_id,
                source=RAINDROP_SOURCE,
                source_account_id=account["id"],
                cursor_name=cursor_name,
                cursor_value=sync.cursor_value,
                high_water_mark=sync.high_water_mark,
                state={
                    **state,
                    "last_batch_received": 0,
                    "last_batch_saved": 0,
                    "last_batch_queued": 0,
                    "last_batch_skipped": 0,
                    "last_batch_failed": 0,
                    "last_batch_archived_missing": empty_archived_missing,
                },
                last_error=error_message,
                completed=not bool(error_message),
            )
            updated_account = self._source_account_by_id(user_id, account["id"]) or account
            return {
                "source_account_id": account["id"],
                "source": RAINDROP_SOURCE,
                "status": "partial" if error_message else "empty",
                "processing": processing,
                "received": 0,
                "queued": 0,
                "saved": 0,
                "skipped": 0,
                "failed": len(sync.errors),
                "archived_missing": empty_archived_missing,
                "archive_missing_decision": archive_decision,
                "archive_missing_suppressed": bool(archive_decision["requested"] and not archive_decision["allowed"]),
                "capture_ids": [],
                "records": [],
                "errors": sync.errors,
                "cursor": cursor_payload,
                "source_account": updated_account,
                "sync": sync_summary,
            }

        result = self.sync_source_account_records(
            user_id,
            account["id"],
            records=records,
            cursor_name=cursor_name,
            cursor_value=sync.cursor_value,
            high_water_mark=sync.high_water_mark,
            state=state,
            processing=processing,
            archive_missing=self._direct_connector_can_archive_missing(
                complete_snapshot=bool(complete_snapshot),
                sync_summary=sync_summary,
                sync_errors=sync.errors,
                max_records=capped_max_records,
            ),
            complete_snapshot=bool(complete_snapshot),
        )
        if sync.errors:
            result["errors"] = [*(result.get("errors") or []), *sync.errors]
            result["failed"] = int(result.get("failed") or 0) + len(sync.errors)
            if result.get("status") == "complete":
                result["status"] = "partial"
        result["source_account"] = self._source_account_by_id(user_id, account["id"]) or account
        result["sync"] = sync_summary
        result = self._with_direct_connector_archive_decision(
            result,
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        return result

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
        complete_snapshot: bool = False,
        api_base_url: str | None = None,
        request_json: Any | None = None,
    ) -> dict[str, Any]:
        from .connectors.zotero import ZOTERO_SOURCE, fetch_zotero_records

        if processing not in {"sync", "async"}:
            raise ValueError("processing must be sync or async")
        try:
            capped_max_records = int(max_records or 100)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_records must be an integer") from exc
        if capped_max_records < 1 or capped_max_records > 500:
            raise ValueError("max_records must be between 1 and 500")
        self._ensure_source_account_can_sync(user_id, source_account_id, expected_source=ZOTERO_SOURCE)

        sync = fetch_zotero_records(
            token=token,
            library_type=library_type,
            library_id=library_id,
            since=since,
            cursor=cursor,
            max_records=capped_max_records,
            include_attachments=bool(include_attachments),
            api_base_url=api_base_url or "http://localhost:23119/api",
            request_json=request_json,
        )
        identifier = (account_identifier or f"{sync.library_type}:{sync.library_id}").strip()[:240]
        resolved_account_id = (source_account_id or "").strip() or stable_id("sacct_", f"{user_id}:{ZOTERO_SOURCE}:{identifier}")
        default_label = "Zotero Library" if sync.library_type == "user" else f"Zotero Group {sync.library_id}"
        label = (account_label or default_label).strip()[:160]
        sync_summary = sync.to_summary()
        error_message = sync.errors[0]["error"] if sync.errors else None
        token_configured = bool(str(token or "").strip())
        metadata = {
            "connector": ZOTERO_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "library_type": sync.library_type,
            "library_id": sync.library_id,
            "token_configured": token_configured,
            "local_api_default": sync.api_base_url.rstrip("/") == "http://localhost:23119/api",
            "api_base_url": sync.api_base_url,
            "include_attachments": bool(include_attachments),
            "attachment_content_imported": False,
        }
        metadata = _with_source_credential_ref(metadata, resolved_account_id, token_configured or bool(sync.api_base_url))
        metadata = _with_connector_failure_metadata(metadata, sync.errors)
        account = self.upsert_source_account(
            user_id,
            source=ZOTERO_SOURCE,
            account_label=label,
            account_identifier=identifier,
            connection_type="api_token" if token_configured else "local_api",
            status="needs_attention" if error_message else "connected",
            auth_state="error" if error_message else "healthy",
            policy={"review_required": True, "allow_ai_context": True},
            metadata=metadata,
            last_error=error_message,
            account_id=resolved_account_id,
        )
        if token_configured or sync.api_base_url:
            self.store_source_account_credential(
                user_id,
                account["id"],
                source=ZOTERO_SOURCE,
                payload={
                    "token": token or "",
                    "library_type": sync.library_type,
                    "library_id": sync.library_id,
                    "api_base_url": sync.api_base_url,
                    "include_attachments": bool(include_attachments),
                },
            )
        state = {
            "connector": ZOTERO_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "sync_errors": sync.errors,
            "next_cursor": sync.next_cursor,
            "library_type": sync.library_type,
            "library_id": sync.library_id,
            "api_base_url": sync.api_base_url,
            "include_attachments": bool(include_attachments),
            "attachment_content_imported": False,
        }
        records = [record.to_source_account_record() for record in sync.records]
        archive_decision = self._direct_connector_archive_missing_decision(
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        empty_archived_missing = 0
        if not records and archive_decision["allowed"]:
            empty_archived_missing = self._archive_missing_source_account_records(user_id, account["id"], set())
        if not records:
            cursor_payload = self.upsert_sync_cursor(
                user_id,
                source=ZOTERO_SOURCE,
                source_account_id=account["id"],
                cursor_name=cursor_name,
                cursor_value=sync.cursor_value,
                high_water_mark=sync.high_water_mark,
                state={
                    **state,
                    "last_batch_received": 0,
                    "last_batch_saved": 0,
                    "last_batch_queued": 0,
                    "last_batch_skipped": 0,
                    "last_batch_failed": 0,
                    "last_batch_archived_missing": empty_archived_missing,
                },
                last_error=error_message,
                completed=not bool(error_message),
            )
            updated_account = self._source_account_by_id(user_id, account["id"]) or account
            return {
                "source_account_id": account["id"],
                "source": ZOTERO_SOURCE,
                "status": "partial" if error_message else "empty",
                "processing": processing,
                "received": 0,
                "queued": 0,
                "saved": 0,
                "skipped": 0,
                "failed": len(sync.errors),
                "archived_missing": empty_archived_missing,
                "archive_missing_decision": archive_decision,
                "archive_missing_suppressed": bool(archive_decision["requested"] and not archive_decision["allowed"]),
                "capture_ids": [],
                "records": [],
                "errors": sync.errors,
                "cursor": cursor_payload,
                "source_account": updated_account,
                "sync": sync_summary,
            }

        result = self.sync_source_account_records(
            user_id,
            account["id"],
            records=records,
            cursor_name=cursor_name,
            cursor_value=sync.cursor_value,
            high_water_mark=sync.high_water_mark,
            state=state,
            processing=processing,
            archive_missing=self._direct_connector_can_archive_missing(
                complete_snapshot=bool(complete_snapshot),
                sync_summary=sync_summary,
                sync_errors=sync.errors,
                max_records=capped_max_records,
            ),
            complete_snapshot=bool(complete_snapshot),
        )
        if sync.errors:
            result["errors"] = [*(result.get("errors") or []), *sync.errors]
            result["failed"] = int(result.get("failed") or 0) + len(sync.errors)
            if result.get("status") == "complete":
                result["status"] = "partial"
        result["source_account"] = self._source_account_by_id(user_id, account["id"]) or account
        result["sync"] = sync_summary
        result = self._with_direct_connector_archive_decision(
            result,
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        return result

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
        complete_snapshot: bool = False,
        api_url: str | None = None,
        request_json: Any | None = None,
    ) -> dict[str, Any]:
        from .connectors.linear import LINEAR_SOURCE, fetch_linear_records

        if processing not in {"sync", "async"}:
            raise ValueError("processing must be sync or async")
        try:
            capped_max_records = int(max_records or 100)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_records must be an integer") from exc
        if capped_max_records < 1 or capped_max_records > 500:
            raise ValueError("max_records must be between 1 and 500")
        self._ensure_source_account_can_sync(user_id, source_account_id, expected_source=LINEAR_SOURCE)

        sync = fetch_linear_records(
            token=token,
            since=since,
            cursor=cursor,
            max_records=capped_max_records,
            api_url=api_url or "https://api.linear.app/graphql",
            request_json=request_json,
        )
        identifier = (account_identifier or "linear").strip()[:240]
        resolved_account_id = (source_account_id or "").strip() or stable_id("sacct_", f"{user_id}:{LINEAR_SOURCE}:{identifier}")
        label = (account_label or "Linear Issues").strip()[:160]
        sync_summary = sync.to_summary()
        error_message = sync.errors[0]["error"] if sync.errors else None
        metadata = {
            "connector": LINEAR_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "token_configured": True,
            "api_url_configured": bool(str(api_url or "").strip()),
        }
        metadata = _with_source_credential_ref(metadata, resolved_account_id, bool(str(token or "").strip()))
        metadata = _with_connector_failure_metadata(metadata, sync.errors)
        account = self.upsert_source_account(
            user_id,
            source=LINEAR_SOURCE,
            account_label=label,
            account_identifier=identifier,
            connection_type="api_token",
            status="needs_attention" if error_message else "connected",
            auth_state="error" if error_message else "healthy",
            policy={"review_required": True, "allow_ai_context": True},
            metadata=metadata,
            last_error=error_message,
            account_id=resolved_account_id,
        )
        if str(token or "").strip():
            self.store_source_account_credential(
                user_id,
                account["id"],
                source=LINEAR_SOURCE,
                payload={
                    "token": token,
                    "api_url": api_url or "https://api.linear.app/graphql",
                },
            )
        state = {
            "connector": LINEAR_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "sync_errors": sync.errors,
            "next_cursor": sync.next_cursor,
        }
        records = [record.to_source_account_record() for record in sync.records]
        archive_decision = self._direct_connector_archive_missing_decision(
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        empty_archived_missing = 0
        if not records and archive_decision["allowed"]:
            empty_archived_missing = self._archive_missing_source_account_records(user_id, account["id"], set())
        if not records:
            cursor_payload = self.upsert_sync_cursor(
                user_id,
                source=LINEAR_SOURCE,
                source_account_id=account["id"],
                cursor_name=cursor_name,
                cursor_value=sync.cursor_value,
                high_water_mark=sync.high_water_mark,
                state={
                    **state,
                    "last_batch_received": 0,
                    "last_batch_saved": 0,
                    "last_batch_queued": 0,
                    "last_batch_skipped": 0,
                    "last_batch_failed": 0,
                    "last_batch_archived_missing": empty_archived_missing,
                },
                last_error=error_message,
                completed=not bool(error_message),
            )
            updated_account = self._source_account_by_id(user_id, account["id"]) or account
            return {
                "source_account_id": account["id"],
                "source": LINEAR_SOURCE,
                "status": "partial" if error_message else "empty",
                "processing": processing,
                "received": 0,
                "queued": 0,
                "saved": 0,
                "skipped": 0,
                "failed": len(sync.errors),
                "archived_missing": empty_archived_missing,
                "archive_missing_decision": archive_decision,
                "archive_missing_suppressed": bool(archive_decision["requested"] and not archive_decision["allowed"]),
                "capture_ids": [],
                "records": [],
                "errors": sync.errors,
                "cursor": cursor_payload,
                "source_account": updated_account,
                "sync": sync_summary,
            }

        result = self.sync_source_account_records(
            user_id,
            account["id"],
            records=records,
            cursor_name=cursor_name,
            cursor_value=sync.cursor_value,
            high_water_mark=sync.high_water_mark,
            state=state,
            processing=processing,
            archive_missing=self._direct_connector_can_archive_missing(
                complete_snapshot=bool(complete_snapshot),
                sync_summary=sync_summary,
                sync_errors=sync.errors,
                max_records=capped_max_records,
            ),
            complete_snapshot=bool(complete_snapshot),
        )
        if sync.errors:
            result["errors"] = [*(result.get("errors") or []), *sync.errors]
            result["failed"] = int(result.get("failed") or 0) + len(sync.errors)
            if result.get("status") == "complete":
                result["status"] = "partial"
        result["source_account"] = self._source_account_by_id(user_id, account["id"]) or account
        result["sync"] = sync_summary
        result = self._with_direct_connector_archive_decision(
            result,
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        return result

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
        complete_snapshot: bool = False,
        request_json: Any | None = None,
    ) -> dict[str, Any]:
        from .connectors.jira import JIRA_SOURCE, fetch_jira_records

        if processing not in {"sync", "async"}:
            raise ValueError("processing must be sync or async")
        try:
            capped_max_records = int(max_records or 100)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_records must be an integer") from exc
        if capped_max_records < 1 or capped_max_records > 500:
            raise ValueError("max_records must be between 1 and 500")
        self._ensure_source_account_can_sync(user_id, source_account_id, expected_source=JIRA_SOURCE)

        sync = fetch_jira_records(
            email=email,
            api_token=api_token,
            site_url=site_url,
            jql=jql,
            since=since,
            page_token=page_token,
            max_records=capped_max_records,
            request_json=request_json,
        )
        site_key = urlsplit(sync.site_url).netloc or sync.site_url
        identifier = (account_identifier or site_key or "jira").strip()[:240]
        resolved_account_id = (source_account_id or "").strip() or stable_id("sacct_", f"{user_id}:{JIRA_SOURCE}:{identifier}")
        label = (account_label or f"Jira: {identifier}").strip()[:160]
        sync_summary = sync.to_summary()
        error_message = sync.errors[0]["error"] if sync.errors else None
        metadata = {
            "connector": JIRA_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "site_url": sync.site_url,
            "email_configured": True,
            "api_token_configured": True,
        }
        metadata = _with_source_credential_ref(metadata, resolved_account_id, bool(str(email or "").strip() and str(api_token or "").strip() and str(site_url or "").strip()))
        metadata = _with_connector_failure_metadata(metadata, sync.errors)
        account = self.upsert_source_account(
            user_id,
            source=JIRA_SOURCE,
            account_label=label,
            account_identifier=identifier,
            connection_type="api_token",
            status="needs_attention" if error_message else "connected",
            auth_state="error" if error_message else "healthy",
            policy={"review_required": True, "allow_ai_context": True},
            metadata=metadata,
            last_error=error_message,
            account_id=resolved_account_id,
        )
        if str(email or "").strip() and str(api_token or "").strip() and str(site_url or "").strip():
            self.store_source_account_credential(
                user_id,
                account["id"],
                source=JIRA_SOURCE,
                payload={
                    "email": email,
                    "api_token": api_token,
                    "site_url": sync.site_url,
                    "jql": sync.jql,
                },
            )
        state = {
            "connector": JIRA_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "sync_errors": sync.errors,
            "next_page_token": sync.next_page_token,
            "site_url": sync.site_url,
            "jql": sync.jql,
        }
        records = [record.to_source_account_record() for record in sync.records]
        archive_decision = self._direct_connector_archive_missing_decision(
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        empty_archived_missing = 0
        if not records and archive_decision["allowed"]:
            empty_archived_missing = self._archive_missing_source_account_records(user_id, account["id"], set())
        if not records:
            cursor_payload = self.upsert_sync_cursor(
                user_id,
                source=JIRA_SOURCE,
                source_account_id=account["id"],
                cursor_name=cursor_name,
                cursor_value=sync.cursor_value,
                high_water_mark=sync.high_water_mark,
                state={
                    **state,
                    "last_batch_received": 0,
                    "last_batch_saved": 0,
                    "last_batch_queued": 0,
                    "last_batch_skipped": 0,
                    "last_batch_failed": 0,
                    "last_batch_archived_missing": empty_archived_missing,
                },
                last_error=error_message,
                completed=not bool(error_message),
            )
            updated_account = self._source_account_by_id(user_id, account["id"]) or account
            return {
                "source_account_id": account["id"],
                "source": JIRA_SOURCE,
                "status": "partial" if error_message else "empty",
                "processing": processing,
                "received": 0,
                "queued": 0,
                "saved": 0,
                "skipped": 0,
                "failed": len(sync.errors),
                "archived_missing": empty_archived_missing,
                "archive_missing_decision": archive_decision,
                "archive_missing_suppressed": bool(archive_decision["requested"] and not archive_decision["allowed"]),
                "capture_ids": [],
                "records": [],
                "errors": sync.errors,
                "cursor": cursor_payload,
                "source_account": updated_account,
                "sync": sync_summary,
            }

        result = self.sync_source_account_records(
            user_id,
            account["id"],
            records=records,
            cursor_name=cursor_name,
            cursor_value=sync.cursor_value,
            high_water_mark=sync.high_water_mark,
            state=state,
            processing=processing,
            archive_missing=self._direct_connector_can_archive_missing(
                complete_snapshot=bool(complete_snapshot),
                sync_summary=sync_summary,
                sync_errors=sync.errors,
                max_records=capped_max_records,
            ),
            complete_snapshot=bool(complete_snapshot),
        )
        if sync.errors:
            result["errors"] = [*(result.get("errors") or []), *sync.errors]
            result["failed"] = int(result.get("failed") or 0) + len(sync.errors)
            if result.get("status") == "complete":
                result["status"] = "partial"
        result["source_account"] = self._source_account_by_id(user_id, account["id"]) or account
        result["sync"] = sync_summary
        result = self._with_direct_connector_archive_decision(
            result,
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        return result

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
        complete_snapshot: bool = False,
        api_base_url: str | None = None,
        notion_version: str | None = None,
        request_json: Any | None = None,
    ) -> dict[str, Any]:
        from .connectors.notion import NOTION_SOURCE, fetch_notion_records

        if processing not in {"sync", "async"}:
            raise ValueError("processing must be sync or async")
        try:
            capped_max_records = int(max_records or 50)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_records must be an integer") from exc
        if capped_max_records < 1 or capped_max_records > 200:
            raise ValueError("max_records must be between 1 and 200")
        self._ensure_source_account_can_sync(user_id, source_account_id, expected_source=NOTION_SOURCE)

        sync = fetch_notion_records(
            token=token,
            since=since,
            cursor=cursor,
            max_records=capped_max_records,
            include_content=bool(include_content),
            api_base_url=api_base_url or "https://api.notion.com/v1",
            notion_version=notion_version or "2026-03-11",
            request_json=request_json,
        )
        identifier = (account_identifier or "notion").strip()[:240]
        resolved_account_id = (source_account_id or "").strip() or stable_id("sacct_", f"{user_id}:{NOTION_SOURCE}:{identifier}")
        label = (account_label or "Notion Pages").strip()[:160]
        sync_summary = sync.to_summary()
        error_message = sync.errors[0]["error"] if sync.errors else None
        metadata = {
            "connector": NOTION_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "token_configured": True,
            "content_sync_enabled": bool(include_content),
            "notion_version": sync.notion_version,
        }
        metadata = _with_source_credential_ref(metadata, resolved_account_id, bool(str(token or "").strip()))
        metadata = _with_connector_failure_metadata(metadata, sync.errors)
        account = self.upsert_source_account(
            user_id,
            source=NOTION_SOURCE,
            account_label=label,
            account_identifier=identifier,
            connection_type="api_token",
            status="needs_attention" if error_message else "connected",
            auth_state="error" if error_message else "healthy",
            policy={"review_required": True, "allow_ai_context": True},
            metadata=metadata,
            last_error=error_message,
            account_id=resolved_account_id,
        )
        if str(token or "").strip():
            self.store_source_account_credential(
                user_id,
                account["id"],
                source=NOTION_SOURCE,
                payload={
                    "token": token,
                    "include_content": bool(include_content),
                    "api_base_url": api_base_url or "https://api.notion.com/v1",
                    "notion_version": sync.notion_version,
                    # Preserve OAuth refresh material (refresh_token, token_endpoint, client
                    # id/secret, expiry) across syncs so managed-OAuth Notion can refresh again;
                    # otherwise a sync would wipe it and lock the account out at next expiry.
                    **_oauth_refresh_credential_fields(self._read_source_account_credential_payload(user_id, resolved_account_id)),
                },
            )
        state = {
            "connector": NOTION_SOURCE,
            "connector_version": sync_summary["connector_version"],
            "records_found": sync.records_found,
            "records_returned": sync.records_returned,
            "sync_errors": sync.errors,
            "next_cursor": sync.next_cursor,
            "notion_version": sync.notion_version,
        }
        records = [record.to_source_account_record() for record in sync.records]
        archive_decision = self._direct_connector_archive_missing_decision(
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        empty_archived_missing = 0
        if not records and archive_decision["allowed"]:
            empty_archived_missing = self._archive_missing_source_account_records(user_id, account["id"], set())
        if not records:
            cursor_payload = self.upsert_sync_cursor(
                user_id,
                source=NOTION_SOURCE,
                source_account_id=account["id"],
                cursor_name=cursor_name,
                cursor_value=sync.cursor_value,
                high_water_mark=sync.high_water_mark,
                state={
                    **state,
                    "last_batch_received": 0,
                    "last_batch_saved": 0,
                    "last_batch_queued": 0,
                    "last_batch_skipped": 0,
                    "last_batch_failed": 0,
                    "last_batch_archived_missing": empty_archived_missing,
                },
                last_error=error_message,
                completed=not bool(error_message),
            )
            updated_account = self._source_account_by_id(user_id, account["id"]) or account
            return {
                "source_account_id": account["id"],
                "source": NOTION_SOURCE,
                "status": "partial" if error_message else "empty",
                "processing": processing,
                "received": 0,
                "queued": 0,
                "saved": 0,
                "skipped": 0,
                "failed": len(sync.errors),
                "archived_missing": empty_archived_missing,
                "archive_missing_decision": archive_decision,
                "archive_missing_suppressed": bool(archive_decision["requested"] and not archive_decision["allowed"]),
                "capture_ids": [],
                "records": [],
                "errors": sync.errors,
                "cursor": cursor_payload,
                "source_account": updated_account,
                "sync": sync_summary,
            }

        result = self.sync_source_account_records(
            user_id,
            account["id"],
            records=records,
            cursor_name=cursor_name,
            cursor_value=sync.cursor_value,
            high_water_mark=sync.high_water_mark,
            state=state,
            processing=processing,
            archive_missing=self._direct_connector_can_archive_missing(
                complete_snapshot=bool(complete_snapshot),
                sync_summary=sync_summary,
                sync_errors=sync.errors,
                max_records=capped_max_records,
            ),
            complete_snapshot=bool(complete_snapshot),
        )
        if sync.errors:
            result["errors"] = [*(result.get("errors") or []), *sync.errors]
            result["failed"] = int(result.get("failed") or 0) + len(sync.errors)
            if result.get("status") == "complete":
                result["status"] = "partial"
        result["source_account"] = self._source_account_by_id(user_id, account["id"]) or account
        result["sync"] = sync_summary
        result = self._with_direct_connector_archive_decision(
            result,
            complete_snapshot=bool(complete_snapshot),
            sync_summary=sync_summary,
            sync_errors=sync.errors,
            max_records=capped_max_records,
        )
        return result

    def _latest_sync_cursor(self, user_id: str, account_id: str, cursor_name: str) -> dict[str, Any] | None:
        normalized_name = (cursor_name or "default").strip() or "default"
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM sync_cursors
                WHERE user_id = ?
                  AND source_account_id = ?
                  AND cursor_name = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (user_id, account_id, normalized_name),
            ).fetchone()
        if not row:
            return None
        return self._sync_cursor_from_row(row)

    def _latest_sync_cursor_value(self, user_id: str, account_id: str, cursor_name: str) -> str | None:
        row = self._latest_sync_cursor(user_id, account_id, cursor_name)
        if not row:
            return None
        return str(row.get("cursor_value") or "").strip() or None

    def _source_account_by_id(self, user_id: str, account_id: str) -> dict[str, Any] | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM source_accounts WHERE user_id = ? AND id = ?",
                (user_id, account_id),
            ).fetchone()
        return self._source_account_from_row(row) if row else None

    def _ensure_source_account_can_sync(self, user_id: str, account_id: str | None, *, expected_source: str | None = None) -> None:
        resolved_id = str(account_id or "").strip()
        if not resolved_id:
            return
        account = self._source_account_by_id(user_id, resolved_id)
        if not account:
            return
        source = _normalize_source_key(str(account.get("source") or ""))
        expected = _normalize_source_key(str(expected_source or ""))
        if expected and source != expected:
            raise ValueError(f"source account belongs to {source or 'another source'}, not {expected}")
        if account.get("disconnected_at"):
            raise ValueError("source account is disconnected; resume before syncing")

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

    def resume_source_account(self, user_id: str, account_id: str) -> dict[str, Any] | None:
        timestamp = now_iso()
        with connect(self.db_path) as conn:
            existing = conn.execute("SELECT * FROM source_accounts WHERE user_id = ? AND id = ?", (user_id, account_id)).fetchone()
            if not existing:
                return None
            credential = self.vault.read_source_credential(user_id=user_id, source_account_id=account_id)
            status = "connected" if credential else "available"
            auth_state = "healthy" if credential else "not_configured"
            conn.execute(
                """
                UPDATE source_accounts
                SET status = ?,
                    auth_state = ?,
                    last_error = NULL,
                    updated_at = ?,
                    disconnected_at = NULL
                WHERE user_id = ? AND id = ?
                """,
                (status, auth_state, timestamp, user_id, account_id),
            )
            self._event(
                conn,
                user_id,
                account_id,
                "source_account",
                "resumed",
                {"source": existing["source"], "credential_retained": bool(credential)},
            )
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

    def remember_oauth_pending(
        self,
        *,
        state: str,
        user_id: str,
        flow: str,
        payload: dict[str, Any],
        ttl_seconds: int = 600,
    ) -> None:
        """Persist in-flight OAuth setup state so a backend restart between the
        authorize redirect and the callback does not lose it. Keyed by the random
        `state` token and scoped by `flow` ("google" or "managed"). Expires after
        `ttl_seconds`; expired rows are pruned opportunistically on write/read."""
        normalized_state = str(state or "").strip()[:500]
        normalized_flow = str(flow or "").strip()[:40]
        if not normalized_state or not normalized_flow:
            return
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=max(1, int(ttl_seconds or 600)))
        stored = {
            key: value
            for key, value in (payload or {}).items()
            if key not in {"created_at", "expires_at", "user_id", "flow", "state"}
        }
        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM oauth_pending WHERE expires_at < ?", (_isoformat_z(now),))
            conn.execute(
                """
                INSERT INTO oauth_pending (state, user_id, flow, payload_json, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(state) DO UPDATE SET
                  user_id = excluded.user_id,
                  flow = excluded.flow,
                  payload_json = excluded.payload_json,
                  created_at = excluded.created_at,
                  expires_at = excluded.expires_at
                """,
                (
                    normalized_state,
                    str(user_id or ""),
                    normalized_flow,
                    json.dumps(stored),
                    _isoformat_z(now),
                    _isoformat_z(expires),
                ),
            )

    def pop_oauth_pending(self, state: str, *, flow: str) -> dict[str, Any] | None:
        """Atomically consume and return the persisted OAuth pending state for a
        `(state, flow)` pair, or None if it is missing or expired. Single-use:
        the row is deleted whether or not it had expired."""
        normalized_state = str(state or "").strip()
        normalized_flow = str(flow or "").strip()
        if not normalized_state or not normalized_flow:
            return None
        now_text = _isoformat_z(datetime.now(timezone.utc))
        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM oauth_pending WHERE expires_at < ?", (now_text,))
            row = conn.execute(
                "SELECT * FROM oauth_pending WHERE state = ? AND flow = ?",
                (normalized_state, normalized_flow),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "DELETE FROM oauth_pending WHERE state = ? AND flow = ?",
                (normalized_state, normalized_flow),
            )
        payload = self._json_or_empty(row["payload_json"])
        payload["user_id"] = row["user_id"]
        payload["created_at"] = row["created_at"]
        return payload

    def _source_account_record_url(
        self,
        *,
        source: str,
        account_id: str,
        explicit_url: Any,
        external_id: str | None,
        ordinal: int,
    ) -> str:
        explicit = str(explicit_url or "").strip()
        if explicit:
            return explicit[:500]
        record_id = external_id or f"record-{ordinal + 1}"
        return f"source-account://{quote(source)}/{quote(account_id)}/{quote(record_id)}"

    def _source_record_metadata_refresh_required(
        self,
        conn,
        user_id: str,
        capture_id: str,
        *,
        source_url: str | None,
        title: str | None,
        captured_at: str | None,
        record_metadata: dict[str, Any] | None,
    ) -> bool:
        capture = conn.execute(
            "SELECT source_url, title, captured_at, raw_hash FROM captures WHERE user_id = ? AND id = ?",
            (user_id, capture_id),
        ).fetchone()
        if not capture:
            return True
        if str(capture["source_url"] or "") != str(source_url or ""):
            return True
        if str(capture["title"] or "") != str(title or ""):
            return True
        normalized_metadata = _source_record_refresh_metadata(record_metadata)
        if not normalized_metadata:
            return False
        rows = conn.execute(
            """
            SELECT provenance_json
            FROM memories
            WHERE user_id = ?
              AND capture_id = ?
              AND status = 'active'
            LIMIT 20
            """,
            (user_id, capture_id),
        ).fetchall()
        if not rows:
            if self._pending_source_record_metadata_matches(
                conn,
                user_id,
                capture_id,
                source_url=source_url,
                title=title,
                raw_hash=str(capture["raw_hash"] or ""),
                record_metadata=record_metadata,
            ):
                return False
            return True
        for row in rows:
            provenance = self._json_or_empty(row["provenance_json"])
            existing_metadata = provenance.get("record_metadata") if isinstance(provenance.get("record_metadata"), dict) else {}
            if _source_record_refresh_metadata(existing_metadata) != normalized_metadata:
                return True
        return False

    def _pending_source_record_metadata_matches(
        self,
        conn,
        user_id: str,
        capture_id: str,
        *,
        source_url: str | None,
        title: str | None,
        raw_hash: str | None,
        record_metadata: dict[str, Any] | None,
    ) -> bool:
        normalized_metadata = _source_record_refresh_metadata(record_metadata)
        rows = conn.execute(
            """
            SELECT payload_json
            FROM memory_jobs
            WHERE user_id = ?
              AND object_type = 'capture'
              AND object_id = ?
              AND job_type = 'extract_capture'
              AND status IN ('queued', 'running')
            ORDER BY updated_at DESC
            LIMIT 10
            """,
            (user_id, capture_id),
        ).fetchall()
        for row in rows:
            payload = self._json_or_empty(row["payload_json"])
            if str(payload.get("raw_hash") or "") != str(raw_hash or ""):
                continue
            if str(payload.get("source_url") or "") != str(source_url or ""):
                continue
            if str(payload.get("title") or "") != str(title or ""):
                continue
            pending_metadata = payload.get("record_metadata") if isinstance(payload.get("record_metadata"), dict) else {}
            if _source_record_refresh_metadata(pending_metadata) == normalized_metadata:
                return True
        return False

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
        offset: int = 0,
    ) -> dict[str, Any]:
        cleaned_paths = [str(path).strip() for path in paths if str(path).strip()]
        if not cleaned_paths:
            raise ValueError("paths must include at least one local file or folder")
        if processing not in {"sync", "async"}:
            raise ValueError("processing must be sync or async")
        max_records = min(max(int(max_records), 1), 5000)
        offset = max(0, int(offset or 0))
        started_at = now_iso()
        # Paginated so a huge export imports fully across calls instead of silently
        # dropping everything past the cap. `has_more`/`next_offset` tell the caller
        # to run again; content-hash dedup keeps overlapping windows safe.
        page = import_source_records_page(cleaned_paths, source_hint=source_hint, max_records=max_records, offset=offset)
        records = page["records"]
        records_available = int(page["total"])
        has_more = bool(page["has_more"])
        next_offset = page["next_offset"]
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
        base_identity_aliases = self.settings(user_id).get("identity_aliases")
        source_accounts = self.list_source_accounts(user_id)
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
                    identity_aliases = self._identity_aliases_for_source(
                        user_id,
                        record.source,
                        base_aliases=base_identity_aliases,
                        accounts=source_accounts,
                    )
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
                "records_available": records_available,
                "offset": offset,
                "has_more": has_more,
                "next_offset": next_offset,
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
            "records_available": records_available,
            "offset": offset,
            "has_more": has_more,
            "next_offset": next_offset,
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

    def job_health(
        self,
        user_id: str,
        *,
        failed_limit: int = 10,
        stale_after_seconds: int = 15 * 60,
    ) -> dict[str, Any]:
        timestamp = now_iso()
        now = datetime.now(timezone.utc)
        stale_after = max(60, min(int(stale_after_seconds), 24 * 60 * 60))
        failed_limit = max(0, min(int(failed_limit), 50))
        with connect(self.db_path) as conn:
            count_rows = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM memory_jobs
                WHERE user_id = ?
                GROUP BY status
                """,
                (user_id,),
            ).fetchall()
            type_rows = conn.execute(
                """
                SELECT job_type, status, COUNT(*) AS count
                FROM memory_jobs
                WHERE user_id = ?
                GROUP BY job_type, status
                ORDER BY job_type, status
                """,
                (user_id,),
            ).fetchall()
            oldest_queued_at = conn.execute(
                "SELECT MIN(created_at) FROM memory_jobs WHERE user_id = ? AND status = 'queued'",
                (user_id,),
            ).fetchone()[0]
            due_queued = conn.execute(
                """
                SELECT COUNT(*)
                FROM memory_jobs
                WHERE user_id = ?
                  AND status = 'queued'
                  AND run_at <= ?
                """,
                (user_id, timestamp),
            ).fetchone()[0]
            failed_rows = conn.execute(
                """
                SELECT *
                FROM memory_jobs
                WHERE user_id = ?
                  AND status = 'failed'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, failed_limit),
            ).fetchall()
            running_rows = conn.execute(
                """
                SELECT *
                FROM memory_jobs
                WHERE user_id = ?
                  AND status = 'running'
                ORDER BY updated_at ASC
                LIMIT 100
                """,
                (user_id,),
            ).fetchall()
            latency_rows = conn.execute(
                """
                SELECT job_type, created_at, completed_at
                FROM memory_jobs
                WHERE user_id = ?
                  AND status = 'succeeded'
                  AND completed_at IS NOT NULL
                ORDER BY completed_at DESC
                LIMIT 2000
                """,
                (user_id,),
            ).fetchall()
            queued_created_rows = conn.execute(
                "SELECT created_at FROM memory_jobs WHERE user_id = ? AND status = 'queued'",
                (user_id,),
            ).fetchall()

        counts = {"queued": 0, "running": 0, "succeeded": 0, "failed": 0}
        for row in count_rows:
            counts[str(row["status"])] = int(row["count"] or 0)
        by_type: dict[str, dict[str, int]] = {}
        for row in type_rows:
            job_type = str(row["job_type"])
            by_type.setdefault(job_type, {})
            by_type[job_type][str(row["status"])] = int(row["count"] or 0)

        # End-to-end latency (enqueue -> completed) percentiles, overall and per job type, plus
        # queue-age percentiles for currently-queued work — capture-to-indexed SLA visibility.
        latencies_overall: list[float] = []
        latencies_by_type: dict[str, list[float]] = {}
        for row in latency_rows:
            duration = _duration_seconds(row["created_at"], row["completed_at"])
            if duration is None:
                continue
            latencies_overall.append(duration)
            latencies_by_type.setdefault(str(row["job_type"]), []).append(duration)
        queue_ages = [
            age
            for age in (_age_seconds(row["created_at"], now=now) for row in queued_created_rows)
            if age is not None
        ]
        latency_seconds = {
            "overall": _percentile_summary(latencies_overall),
            "by_type": {job_type: _percentile_summary(values) for job_type, values in sorted(latencies_by_type.items())},
        }
        queue_age_seconds = _percentile_summary([float(age) for age in queue_ages])

        recent_failures = [self._job_health_item(self._job_from_row(row), now=now) for row in failed_rows]
        stale_running: list[dict[str, Any]] = []
        for row in running_rows:
            job = self._job_from_row(row)
            age = _age_seconds(job.get("updated_at"), now=now)
            if age is not None and age >= stale_after:
                stale_running.append(self._job_health_item(job, now=now))

        if counts["failed"] or stale_running:
            status = "blocked"
        elif counts["queued"] or counts["running"]:
            status = "attention"
        else:
            status = "ok"
        return {
            "status": status,
            "checked_at": timestamp,
            "user_id": user_id,
            "counts": counts,
            "by_type": by_type,
            "due_queued": int(due_queued or 0),
            "oldest_queued_at": oldest_queued_at,
            "oldest_queued_age_seconds": _age_seconds(oldest_queued_at, now=now),
            "stale_after_seconds": stale_after,
            "stale_running": stale_running,
            "recent_failures": recent_failures,
            "latency_seconds": latency_seconds,
            "queue_age_seconds": queue_age_seconds,
        }

    def run_due_jobs(
        self,
        user_id: str,
        *,
        limit: int = 10,
        worker_id: str = "local-worker",
        schedule_source_syncs: bool = True,
    ) -> dict[str, Any]:
        scheduled_sources = self.enqueue_due_source_syncs(user_id, limit=limit) if schedule_source_syncs else None
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
            "scheduled_source_syncs": scheduled_sources,
            "pending": len(self.list_jobs(user_id, status="queued", limit=100)),
            "failed": len(self.list_jobs(user_id, status="failed", limit=100)),
        }

    def run_due_source_sync_jobs(
        self,
        user_id: str,
        *,
        limit: int = 10,
        worker_id: str = "source-sync-worker",
    ) -> dict[str, Any]:
        scheduled_sources = self.enqueue_due_source_syncs(user_id, limit=limit)
        processed: list[dict[str, Any]] = []
        for _ in range(max(0, min(limit, 100))):
            job = self._claim_next_job(user_id, worker_id, job_type="source_account_sync")
            if not job:
                break
            processed.append(self._run_job(job, worker_id))
        return {
            "ran_at": now_iso(),
            "processed": len(processed),
            "jobs": processed,
            "scheduled_source_syncs": scheduled_sources,
            "pending": len(self.list_jobs(user_id, status="queued", job_type="source_account_sync", limit=100)),
            "failed": len(self.list_jobs(user_id, status="failed", job_type="source_account_sync", limit=100)),
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
        source_account_id: str | None = None,
        external_id: str | None = None,
        capture_id_override: str | None = None,
    ) -> dict[str, Any]:
        captured_at = extracted.get("_timestamp") or now_iso()
        normalized_source_account_id = (source_account_id or "").strip() or None
        normalized_external_id = (external_id or "").strip()[:240] or None
        override_id = str(capture_id_override or "").strip()
        if override_id.startswith("cap_"):
            capture_id = override_id[:80]
        elif normalized_source_account_id and normalized_external_id:
            capture_id = stable_id("cap_", f"{user_id}:{normalized_source_account_id}:{normalized_external_id}")
        else:
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
            source_account_snapshot = None
            if normalized_source_account_id:
                source_account_row = conn.execute(
                    "SELECT * FROM source_accounts WHERE user_id = ? AND id = ?",
                    (user_id, normalized_source_account_id),
                ).fetchone()
                if source_account_row:
                    source_account_snapshot = self._source_account_from_row(source_account_row)
            source_account_policy = source_account_snapshot.get("policy") if source_account_snapshot else {}
            if normalized_source_account_id and not (isinstance(source_account_policy, dict) and source_account_policy.get("review_required") is False):
                review_status = "pending"
                approved_at = None
            stable_source_record = bool(normalized_source_account_id and normalized_external_id)
            existing_capture = conn.execute(
                "SELECT id, raw_hash, review_status, approved_at FROM captures WHERE user_id = ? AND id = ?",
                (user_id, capture_id),
            ).fetchone()
            if existing_capture and existing_capture["raw_hash"] != raw_hash:
                purged = (
                    self._archive_capture_derivatives_in_conn(conn, user_id, capture_id, timestamp=captured_at)
                    if stable_source_record
                    else self._purge_capture_derivatives_in_conn(conn, user_id, capture_id)
                )
                self._event(
                    conn,
                    user_id,
                    capture_id,
                    "capture",
                    "replaced",
                    {
                        "source": source,
                        "source_account_id": normalized_source_account_id,
                        "external_id": normalized_external_id,
                        "memory_count": purged["memory_count"],
                        "task_count": purged["task_count"],
                        "edge_count": purged["edge_count"],
                    },
                )
            elif existing_capture and stable_source_record and existing_capture["review_status"] != "archived":
                review_status = existing_capture["review_status"]
                approved_at = existing_capture["approved_at"]
            conn.execute(
                """
                INSERT INTO captures
                (id, user_id, import_id, source, source_url, source_account_id, external_id, title, raw_text, raw_hash, summary, review_status, approved_at, captured_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  user_id = excluded.user_id,
                  import_id = COALESCE(captures.import_id, excluded.import_id),
                  source = excluded.source,
                  source_url = excluded.source_url,
                  source_account_id = excluded.source_account_id,
                  external_id = excluded.external_id,
                  title = excluded.title,
                  raw_text = excluded.raw_text,
                  raw_hash = excluded.raw_hash,
                  summary = excluded.summary,
                  review_status = excluded.review_status,
                  approved_at = excluded.approved_at,
                  captured_at = excluded.captured_at
                """,
                (
                    capture_id,
                    user_id,
                    import_id,
                    source,
                    source_url,
                    normalized_source_account_id,
                    normalized_external_id,
                    title,
                    content,
                    raw_hash,
                    summary,
                    review_status,
                    approved_at,
                    captured_at,
                ),
            )
            self._event(conn, user_id, capture_id, "capture", "created", {"source": source, "title": title})

            for record in extracted.get("records", []):
                memory = self._save_memory(
                    conn,
                    capture_id,
                    user_id,
                    record,
                    source,
                    source_url,
                    captured_at,
                    content,
                    granular_source_url=import_id is not None or source_account_snapshot is not None,
                    source_account=source_account_snapshot,
                    external_id=normalized_external_id,
                )
                memories.append(memory)
                edges.append(self._edge(conn, user_id, capture_id, memory["id"], "contains", memory["id"], captured_at))

            self._save_memory_relations_for_capture(conn, user_id, capture_id, memories, captured_at)
            self._save_cross_capture_memory_relations(conn, user_id, capture_id, memories, captured_at)
            self._save_obsidian_canvas_memory_relations(conn, user_id, capture_id, memories, captured_at)
            self._save_obsidian_wikilink_memory_relations(conn, user_id, capture_id, memories, captured_at)

            if stable_source_record and memories:
                self._link_superseded_capture_memories_in_conn(
                    conn,
                    user_id,
                    capture_id,
                    memories,
                    timestamp=captured_at,
                )

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
                INSERT INTO capture_processing_state
                (capture_id, user_id, ingest_status, extraction_status, embedding_status, memory_count, task_count, entity_count, last_job_id, last_error, queued_at, started_at, completed_at, updated_at)
                VALUES (?, ?, 'materialized', 'succeeded', ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)
                ON CONFLICT(capture_id) DO UPDATE SET
                  ingest_status = 'materialized',
                  extraction_status = 'succeeded',
                  embedding_status = excluded.embedding_status,
                  memory_count = excluded.memory_count,
                  task_count = excluded.task_count,
                  entity_count = excluded.entity_count,
                  last_job_id = NULL,
                  last_error = NULL,
                  started_at = excluded.started_at,
                  completed_at = excluded.completed_at,
                  updated_at = excluded.updated_at
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
                    "source_account_id": normalized_source_account_id,
                    "external_id": normalized_external_id,
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
            return [self._capture_from_row(row, conn=conn, include_review_preview=True, redact_source_urls=True) for row in rows]

    def recent(
        self,
        user_id: str,
        limit: int = 20,
        *,
        kind: str | None = None,
        layer: str | None = None,
        sector: str | None = None,
        source: str | None = None,
        source_account_id: str | None = None,
        metadata_filters: dict[str, Any] | None = None,
        as_of: str | None = None,
    ) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            filters, params = self._memory_filters(
                user_id,
                user_settings,
                alias="m",
                kind=kind,
                layer=layer,
                sector=sector,
                source=source,
                source_account_id=source_account_id,
                metadata_filters=metadata_filters,
                as_of=as_of,
            )
            where = " AND ".join(filters)
            rows = conn.execute(
                f"SELECT * FROM memories m WHERE {where} ORDER BY m.captured_at DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def public_recent(
        self,
        user_id: str,
        limit: int = 20,
        *,
        kind: str | None = None,
        layer: str | None = None,
        sector: str | None = None,
        source: str | None = None,
        source_account_id: str | None = None,
        metadata_filters: dict[str, Any] | None = None,
        as_of: str | None = None,
    ) -> list[dict[str, Any]]:
        results = self.recent(
            user_id,
            limit=limit,
            kind=kind,
            layer=layer,
            sector=sector,
            source=source,
            source_account_id=source_account_id,
            metadata_filters=metadata_filters,
            as_of=as_of,
        )
        return self._shared_payload(results, redact_sensitive=bool(self.settings(user_id)["redact_sensitive_context"]))

    def search(
        self,
        user_id: str,
        query: str,
        limit: int = 10,
        kind: str | None = None,
        layer: str | None = None,
        *,
        sector: str | None = None,
        source: str | None = None,
        source_account_id: str | None = None,
        metadata_filters: dict[str, Any] | None = None,
        include_related: bool = False,
        as_of: str | None = None,
        _diagnostics: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            results = self.recent(
                user_id,
                limit,
                kind=kind,
                layer=layer,
                sector=sector,
                source=source,
                source_account_id=source_account_id,
                metadata_filters=metadata_filters,
                as_of=as_of,
            )
            if _diagnostics is not None:
                _diagnostics.update(self._search_diagnostics_snapshot(user_id, limit=limit, returned=len(results), query_empty=True))
            return results

        fts_query = self._fts_query(query)
        candidate_limit = max(limit * 8, 50)
        ranked_limit = max(limit * 4, min(candidate_limit, 80))
        task_intent = self._query_has_task_intent(query)
        task_rows = []
        mode_counts: dict[str, int] = {
            "fts": 0,
            "vector": 0,
            "temporal": 0,
            "intent": 0,
            "fallback_like": 0,
            "lexical_fallback": 0,
            "related": 0,
            "task": 0,
        }
        vector_available = False
        vector_count = 0
        active_memory_count = 0

        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            filters, params = self._memory_filters(
                user_id,
                user_settings,
                alias="m",
                kind=kind,
                layer=layer,
                sector=sector,
                source=source,
                source_account_id=source_account_id,
                metadata_filters=metadata_filters,
                as_of=as_of,
            )
            where = " AND ".join(filters)
            active_memory_count = conn.execute("SELECT COUNT(*) FROM memories WHERE user_id = ? AND status = 'active'", (user_id,)).fetchone()[0]
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
            vector_available = self._vector_ready(conn)
            vector_count = self._vector_count(conn, user_id)
            vector_rows = self._vector_search(
                conn,
                user_id,
                query,
                candidate_limit,
                kind,
                layer,
                user_settings,
                sector=sector,
                source=source,
                source_account_id=source_account_id,
                metadata_filters=metadata_filters,
                as_of=as_of,
            )
            temporal_rows = self._temporal_search(
                conn,
                user_id,
                query,
                candidate_limit,
                kind,
                layer,
                user_settings,
                sector=sector,
                source=source,
                source_account_id=source_account_id,
                metadata_filters=metadata_filters,
                as_of=as_of,
            )
            mode_counts["fts"] = len(fts_rows)
            mode_counts["vector"] = len(vector_rows)
            mode_counts["temporal"] = len(temporal_rows)
            intent_rows = []
            if not fts_rows and not temporal_rows:
                intent_rows = self._intent_search(
                    conn,
                    user_id,
                    query,
                    candidate_limit,
                    kind,
                    layer,
                    user_settings,
                    sector=sector,
                    source=source,
                    source_account_id=source_account_id,
                    metadata_filters=metadata_filters,
                    as_of=as_of,
                )
                mode_counts["intent"] = len(intent_rows)
            rows = self._fuse_search_rows(
                query,
                fts_rows,
                vector_rows,
                temporal_rows,
                intent_rows,
                ranked_limit,
                user_settings=user_settings,
            )
            scoped_to_memory = bool(source or source_account_id or _normalize_retrieval_metadata_filters(metadata_filters))
            if rows:
                rows = self._diversify_memory_rows(rows, limit, scoped_to_source=scoped_to_memory)
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
                mode_counts["fallback_like"] = len(fallback_rows)
                rows = self._rank_rows_with_layer_boosts(query, fallback_rows, ranked_limit, user_settings=user_settings)
                rows = self._diversify_memory_rows(rows, limit, scoped_to_source=scoped_to_memory)
            if not rows:
                existing_ids = {row["id"] for row in rows}
                lexical_rows = self._lexical_fallback_search(
                    conn,
                    user_id,
                    query,
                    candidate_limit,
                    kind,
                    layer,
                    user_settings,
                    sector=sector,
                    source=source,
                    source_account_id=source_account_id,
                    metadata_filters=metadata_filters,
                    as_of=as_of,
                )
                mode_counts["lexical_fallback"] = len(lexical_rows)
                rows.extend(row for row in lexical_rows if row["id"] not in existing_ids)
                rows = self._diversify_memory_rows(rows, limit, scoped_to_source=scoped_to_memory)
            if layer is None and not scoped_to_memory and (task_intent or not rows):
                # Tasks are sector-fenced inside _task_search_rows via their
                # capture's memory sector, so a project-scoped Ask surfaces that
                # project's open tasks (previously dropped entirely) without
                # leaking other projects' tasks.
                task_rows = self._task_search_rows(
                    conn, user_id, query, candidate_limit, kind, user_settings, sector=sector
                )
                mode_counts["task"] = len(task_rows)

        memory_results = [self._memory_from_row(row) for row in rows]
        if include_related and memory_results and limit > 1:
            related_slot_count = min(2, max(1, limit // 4), limit - 1)
            anchor_results = memory_results[: max(1, limit - related_slot_count)]
            with connect(self.db_path) as conn:
                user_settings = self._settings(conn, user_id)
                related_rows = self._related_memory_rows(
                    conn,
                    user_id,
                    anchor_results,
                    related_slot_count,
                    kind=kind,
                    layer=layer,
                    sector=sector,
                    source=source,
                    source_account_id=source_account_id,
                    metadata_filters=metadata_filters,
                    as_of=as_of,
                    user_settings=user_settings,
                )
            mode_counts["related"] = len(related_rows)
            seen_memory_ids = {item["id"] for item in anchor_results}
            related_results: list[dict[str, Any]] = []
            for row in related_rows:
                item = self._memory_from_row(row)
                item["relationship"] = {
                    "kind": row["relation_kind"],
                    "weight": row["relation_weight"],
                    "related_to_id": row["related_to_id"],
                }
                if item["id"] in seen_memory_ids:
                    continue
                related_results.append(item)
                seen_memory_ids.add(item["id"])
                if len(related_results) >= related_slot_count:
                    break
            if related_results:
                combined_results = [*anchor_results, *related_results]
                for item in memory_results:
                    if item["id"] in seen_memory_ids:
                        continue
                    combined_results.append(item)
                    seen_memory_ids.add(item["id"])
                    if len(combined_results) >= limit:
                        break
                memory_results = combined_results[:limit]
        task_results = [self._task_search_result_from_row(row) for row in task_rows]
        if task_intent:
            task_ids = {item["id"] for item in task_results}
            final_results = [*task_results, *(item for item in memory_results if item["id"] not in task_ids)][:limit]
        elif not memory_results:
            final_results = task_results[:limit]
        else:
            final_results = memory_results
        if _diagnostics is not None:
            _diagnostics.update(
                self._search_diagnostics_payload(
                    limit=limit,
                    candidate_limit=candidate_limit,
                    returned=len(final_results),
                    mode_counts=mode_counts,
                    vector_available=vector_available,
                    vector_indexed_memories=vector_count,
                    active_memories=active_memory_count,
                    query_empty=False,
                )
            )
        return final_results

    def public_search(
        self,
        user_id: str,
        query: str,
        limit: int = 10,
        kind: str | None = None,
        layer: str | None = None,
        *,
        sector: str | None = None,
        source: str | None = None,
        source_account_id: str | None = None,
        metadata_filters: dict[str, Any] | None = None,
        include_related: bool = False,
        as_of: str | None = None,
    ) -> list[dict[str, Any]]:
        results = self.search(
            user_id,
            query,
            limit,
            kind,
            layer,
            sector=sector,
            source=source,
            source_account_id=source_account_id,
            metadata_filters=metadata_filters,
            include_related=include_related,
            as_of=as_of,
        )
        return self._shared_payload(results, redact_sensitive=bool(self.settings(user_id)["redact_sensitive_context"]))

    def public_search_payload(
        self,
        user_id: str,
        query: str,
        limit: int = 10,
        kind: str | None = None,
        layer: str | None = None,
        *,
        sector: str | None = None,
        source: str | None = None,
        source_account_id: str | None = None,
        metadata_filters: dict[str, Any] | None = None,
        include_related: bool = False,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        diagnostics: dict[str, Any] = {}
        results = self.search(
            user_id,
            query,
            limit,
            kind,
            layer,
            sector=sector,
            source=source,
            source_account_id=source_account_id,
            metadata_filters=metadata_filters,
            include_related=include_related,
            as_of=as_of,
            _diagnostics=diagnostics,
        )
        return {
            "query": query,
            "sector": sector,
            "filters": _retrieval_filter_payload(source=source, source_account_id=source_account_id, metadata_filters=metadata_filters, as_of=as_of),
            "results": self._shared_payload(results, redact_sensitive=bool(self.settings(user_id)["redact_sensitive_context"])),
            "retrieval": diagnostics,
        }

    def _search_diagnostics_snapshot(self, user_id: str, *, limit: int, returned: int, query_empty: bool) -> dict[str, Any]:
        with connect(self.db_path) as conn:
            return self._search_diagnostics_payload(
                limit=limit,
                candidate_limit=limit,
                returned=returned,
                mode_counts={"recent": returned},
                vector_available=self._vector_ready(conn),
                vector_indexed_memories=self._vector_count(conn, user_id),
                active_memories=conn.execute("SELECT COUNT(*) FROM memories WHERE user_id = ? AND status = 'active'", (user_id,)).fetchone()[0],
                query_empty=query_empty,
            )

    def _search_diagnostics_payload(
        self,
        *,
        limit: int,
        candidate_limit: int,
        returned: int,
        mode_counts: dict[str, int],
        vector_available: bool,
        vector_indexed_memories: int,
        active_memories: int,
        query_empty: bool,
    ) -> dict[str, Any]:
        embedding = embedding_status()
        degraded_reasons: list[str] = []
        if active_memories and not vector_available:
            degraded_reasons.append("vector_index_unavailable")
        elif active_memories and vector_available and vector_indexed_memories == 0:
            degraded_reasons.append("no_vector_embeddings_indexed")
        if active_memories and embedding.get("provider") == "hash":
            degraded_reasons.append("hash_embedding_provider")
        fallback_modes = [
            mode
            for mode in ("fallback_like", "lexical_fallback", "recent")
            if int(mode_counts.get(mode) or 0) > 0
        ]
        return {
            "query_empty": query_empty,
            "limit": limit,
            "candidate_limit": candidate_limit,
            "returned": returned,
            "used_modes": [mode for mode, count in mode_counts.items() if int(count or 0) > 0],
            "mode_counts": {mode: int(count or 0) for mode, count in mode_counts.items()},
            "fallback_modes": fallback_modes,
            "degraded": bool(degraded_reasons),
            "degraded_reasons": degraded_reasons,
            "vector_available": vector_available,
            "vector_indexed_memories": int(vector_indexed_memories or 0),
            "active_memories": int(active_memories or 0),
            "embedding_provider": embedding.get("provider"),
            "embedding_model": embedding.get("model"),
            "embedding_dimensions": embedding.get("dimensions"),
        }

    def answer_query(
        self,
        user_id: str,
        query: str,
        limit: int = 8,
        *,
        sector: str | None = None,
        source: str | None = None,
        source_account_id: str | None = None,
        metadata_filters: dict[str, Any] | None = None,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        query = query.strip()
        limit = max(1, min(20, int(limit)))
        sector = _normalize_sector_filter(sector) or None
        redact_sensitive = bool(self.settings(user_id)["redact_sensitive_context"])
        search_limit = max(limit * 3, 12)
        candidates = self.search(
            user_id,
            query,
            limit=search_limit,
            sector=sector,
            source=source,
            source_account_id=source_account_id,
            metadata_filters=metadata_filters,
            include_related=True,
            as_of=as_of,
        )
        person_injected_ids: set[str] = set()
        person_entity = self._query_person_entity(user_id, query)
        if person_entity:
            person_memories = self._person_linked_memories(
                user_id,
                person_entity["id"],
                limit=search_limit,
                sector=sector,
                source=source,
                source_account_id=source_account_id,
                metadata_filters=metadata_filters,
                as_of=as_of,
            )
            if person_memories:
                person_injected_ids = {str(item.get("id") or "") for item in person_memories}
                candidates = [*person_memories, *(item for item in candidates if str(item.get("id") or "") not in person_injected_ids)][:search_limit]
        support_query = self._answer_support_query(query)
        if support_query:
            support_candidates = self.search(
                user_id,
                support_query,
                limit=search_limit,
                sector=sector,
                source=source,
                source_account_id=source_account_id,
                metadata_filters=metadata_filters,
                include_related=True,
                as_of=as_of,
            )
            if support_candidates:
                candidate_ids = {str(item.get("id") or "") for item in candidates}
                candidates = [
                    *candidates,
                    *(item for item in support_candidates if str(item.get("id") or "") not in candidate_ids),
                ][:search_limit]
        primary_candidates = [item for item in candidates if not self._is_related_result(item)]
        primary_by_id = {str(item.get("id") or ""): item for item in primary_candidates}
        query_terms = self._lexical_fallback_terms(query, limit=12)
        query_has_layer_intent = bool(query_layer_boosts(query))
        primary_source_backed = [
            item
            for item in primary_candidates
            if self._has_source_citation(item)
            and self._primary_citation_is_relevant(
                item,
                query_terms,
                exempt=query_has_layer_intent or str(item.get("id") or "") in person_injected_ids,
            )
        ]
        related_source_backed = [
            item
            for item in candidates
            if self._is_related_result(item)
            and self._has_source_citation(item)
            and self._should_include_related_citation(query, item, primary_by_id)
        ]
        source_backed = [*primary_source_backed, *related_source_backed]
        uncited = [item for item in candidates if not self._has_source_citation(item)]
        results = [*source_backed, *uncited][:limit]
        cited_results = source_backed[:limit]
        citations: list[dict[str, Any]] = []
        for index, item in enumerate(cited_results, start=1):
            result_type = item.get("result_type") or "memory"
            source_url = item.get("source_url")
            excerpt = self._shared_text(item.get("content") or item.get("summary") or "", redact_sensitive=redact_sensitive)
            provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
            citation_metadata = self._citation_metadata(item, provenance)
            citations.append(
                {
                    "index": index,
                    "id": item["id"],
                    "result_type": result_type,
                    "kind": item["kind"],
                    "layer": item.get("layer") or result_type,
                    "status": item.get("status"),
                    "source": item["source"],
                    "source_url": self._safe_source_locator(source_url, force_local=True),
                    **citation_metadata,
                    "captured_at": item.get("captured_at"),
                    "occurred_at": item.get("occurred_at"),
                    "excerpt": self._answer_excerpt(excerpt),
                    "topics": item.get("topics") or [],
                    "relationship": item.get("relationship"),
                }
            )
        conflicts = self._answer_conflicts(query, cited_results, citations)
        evidence = self._answer_evidence_quality(query, cited_results)
        if citations:
            lines = [f"Cortex found {len(citations)} cited item{'s' if len(citations) != 1 else ''} for this question:"]
            for citation in citations[:5]:
                citation_source = citation["source_url"] or citation["source"]
                lines.append(f"[{citation['index']}] {citation['excerpt']} ({citation_source})")
            if evidence.get("status") == "low_confidence":
                missing_fields = ", ".join(evidence.get("missing_fields") or [])
                lines.append("")
                lines.append(
                    f"Coverage check: Cortex found related citations, but not enough evidence for {missing_fields}. "
                    "Use these citations as context and verify before acting."
                )
            if conflicts:
                lines.append("")
                for conflict in conflicts[:2]:
                    primary_index = conflict.get("primary_index")
                    primary_text = f"[{primary_index}]" if primary_index else str(conflict.get("primary_id") or "the newest cited item")
                    if conflict.get("type") == "date_conflict":
                        date_values = ", ".join(conflict.get("date_claims") or [])
                        lines.append(
                            f"Conflict check: cited sources mention different dates ({date_values}); "
                            f"prefer {primary_text} as the newest/current evidence and verify before acting."
                        )
                    else:
                        claim_key = conflict.get("claim_key") or "this claim"
                        lines.append(
                            f"Conflict check: cited sources disagree on {claim_key}; "
                            f"prefer {primary_text} as the newest/current evidence and verify before acting."
                        )
            answer = "\n".join(lines)
            with connect(self.db_path) as conn:
                self._event(
                    conn,
                    user_id,
                    "ask",
                    "loop",
                    "cited_answer_used",
                    {
                        "surface": "ask",
                        "query": query[:160],
                        "citation_count": len(citations),
                        "result_ids": [citation["id"] for citation in citations[:10]],
                    },
                )
        else:
            answer = "Cortex did not find a cited item for this question yet. Import or approve more source material, then ask again."
        if conflicts:
            answer_status = "conflicted"
        elif citations and evidence.get("status") == "low_confidence":
            answer_status = "low_confidence"
        elif citations:
            answer_status = "cited"
        else:
            answer_status = "no_cited_evidence"
        return {
            "query": query,
            "status": answer_status,
            "filters": _retrieval_filter_payload(source=source, source_account_id=source_account_id, metadata_filters=metadata_filters, as_of=as_of),
            "answer": answer,
            "citations": citations,
            "conflicts": conflicts,
            "evidence": evidence,
            "results": self._shared_payload(results, redact_sensitive=redact_sensitive),
        }

    def _answer_evidence_quality(self, query: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        if not items:
            return {"status": "no_cited_evidence", "checked_fields": [], "missing_fields": []}
        query_terms = set(self._lexical_fallback_terms(query, limit=20))
        checked_fields: list[str] = []
        missing_fields: list[str] = []
        for field, required_terms, evidence_terms in ANSWER_FIELD_REQUIREMENTS:
            if not required_terms.issubset(query_terms):
                continue
            checked_fields.append(field)
            if not any(self._answer_item_supports_field(item, field=field, evidence_terms=evidence_terms) for item in items):
                missing_fields.append(field)
        status = "low_confidence" if missing_fields else "cited"
        return {
            "status": status,
            "checked_fields": checked_fields,
            "missing_fields": missing_fields,
        }

    def _answer_item_supports_field(self, item: dict[str, Any], *, field: str, evidence_terms: set[str]) -> bool:
        if field in self._answer_field_claims(item):
            return True
        if field in {"date", "deadline"} and self._answer_date_claims(item):
            return True
        item_terms = self._answer_item_terms(item)
        if item_terms & evidence_terms:
            return True
        if field == "owner" and item_terms & {"owns", "owner", "responsible", "assignee", "assigned"}:
            return True
        return False

    def _answer_item_terms(self, item: dict[str, Any]) -> set[str]:
        topics = item.get("topics") if isinstance(item.get("topics"), list) else []
        text = " ".join(
            str(value or "")
            for value in (
                item.get("content"),
                item.get("summary"),
                item.get("raw_excerpt"),
                " ".join(str(topic or "") for topic in topics),
            )
        )
        terms: set[str] = set()
        for token in re.findall(r"[a-z0-9_]+", text.casefold().replace("'", "")):
            clean = token.strip("_")
            if not clean:
                continue
            if len(clean) > 3 and clean.endswith("s") and not clean.endswith(("ss", "ies")):
                clean = clean[:-1]
            terms.add(clean)
        return terms

    def _answer_conflicts(self, query: str, items: list[dict[str, Any]], citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(items) < 2:
            return []
        query_terms = set(self._lexical_fallback_terms(query, limit=12))
        citation_by_id = {str(citation.get("id") or ""): citation for citation in citations}
        conflicts: list[dict[str, Any]] = []
        seen_pairs: set[tuple[str, str]] = set()
        for left_index, left in enumerate(items):
            for right in items[left_index + 1 :]:
                left_id = str(left.get("id") or "")
                right_id = str(right.get("id") or "")
                if not left_id or not right_id or left_id == right_id:
                    continue
                pair = tuple(sorted((left_id, right_id)))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                if not self._answer_items_share_scope(query_terms, left, right):
                    continue
                left_dates = self._answer_date_claims(left)
                right_dates = self._answer_date_claims(right)
                if left_dates and right_dates and left_dates != right_dates:
                    primary, conflicting, reason = self._answer_current_item(left, right)
                    primary_id = str(primary.get("id") or "")
                    conflicting_id = str(conflicting.get("id") or "")
                    primary_citation = citation_by_id.get(primary_id, {})
                    conflicting_citation = citation_by_id.get(conflicting_id, {})
                    date_claims = sorted(left_dates | right_dates)
                    conflicts.append(
                        {
                            "type": "date_conflict",
                            "message": "Cited sources mention different dates for the same answer scope.",
                            "primary_id": primary_id,
                            "primary_index": primary_citation.get("index"),
                            "primary_source": primary_citation.get("source") or primary.get("source"),
                            "primary_source_url": primary_citation.get("source_url") or primary.get("source_url"),
                            "primary_dates": sorted(self._answer_date_claims(primary)),
                            "conflicting_id": conflicting_id,
                            "conflicting_index": conflicting_citation.get("index"),
                            "conflicting_source": conflicting_citation.get("source") or conflicting.get("source"),
                            "conflicting_source_url": conflicting_citation.get("source_url") or conflicting.get("source_url"),
                            "conflicting_dates": sorted(self._answer_date_claims(conflicting)),
                            "date_claims": date_claims,
                            "reason": reason,
                        }
                    )
                    if len(conflicts) >= 3:
                        return conflicts
                    continue
                claim_conflict = self._answer_claim_conflict(left, right, citation_by_id)
                if claim_conflict:
                    conflicts.append(claim_conflict)
                if len(conflicts) >= 3:
                    return conflicts
        return conflicts

    def _answer_items_share_scope(self, query_terms: set[str], left: dict[str, Any], right: dict[str, Any]) -> bool:
        left_sector = str(left.get("sector") or "").strip().casefold()
        right_sector = str(right.get("sector") or "").strip().casefold()
        if left_sector and right_sector and left_sector == right_sector:
            return True

        left_entities = {str(value) for value in left.get("entity_ids") or [] if str(value)}
        right_entities = {str(value) for value in right.get("entity_ids") or [] if str(value)}
        if left_entities & right_entities:
            return True

        left_topics = {str(value).casefold() for value in left.get("topics") or [] if str(value)}
        right_topics = {str(value).casefold() for value in right.get("topics") or [] if str(value)}
        if len(left_topics & right_topics) >= 2:
            return True

        if query_terms:
            left_matches = self._item_matched_query_terms(left, list(query_terms))
            right_matches = self._item_matched_query_terms(right, list(query_terms))
            return len(left_matches & right_matches) >= min(3, max(2, len(query_terms) // 2))
        return False

    def _answer_date_claims(self, item: dict[str, Any]) -> set[str]:
        text = " ".join(str(item.get(key) or "") for key in ("content", "summary", "raw_excerpt"))
        dates: set[str] = set()
        for match in ANSWER_DATE_CLAIM_RE.finditer(text):
            value = match.group(0).casefold()
            value = re.sub(r"\b(\d{1,2})(?:st|nd|rd|th)\b", r"\1", value)
            value = re.sub(r",\s*", " ", value)
            dates.add(re.sub(r"\s+", " ", value).strip())
        return dates

    def _answer_claim_conflict(
        self,
        left: dict[str, Any],
        right: dict[str, Any],
        citation_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        left_claims = self._answer_field_claims(left)
        right_claims = self._answer_field_claims(right)
        for claim_key, left_value in left_claims.items():
            right_value = right_claims.get(claim_key)
            if not right_value or left_value == right_value:
                continue
            primary, conflicting, reason = self._answer_current_item(left, right)
            primary_id = str(primary.get("id") or "")
            conflicting_id = str(conflicting.get("id") or "")
            primary_citation = citation_by_id.get(primary_id, {})
            conflicting_citation = citation_by_id.get(conflicting_id, {})
            primary_claims = self._answer_field_claims(primary)
            conflicting_claims = self._answer_field_claims(conflicting)
            return {
                "type": "claim_conflict",
                "message": "Cited sources disagree on the same decision claim.",
                "claim_key": claim_key,
                "primary_id": primary_id,
                "primary_index": primary_citation.get("index"),
                "primary_source": primary_citation.get("source") or primary.get("source"),
                "primary_source_url": primary_citation.get("source_url") or primary.get("source_url"),
                "primary_claim": primary_claims.get(claim_key),
                "conflicting_id": conflicting_id,
                "conflicting_index": conflicting_citation.get("index"),
                "conflicting_source": conflicting_citation.get("source") or conflicting.get("source"),
                "conflicting_source_url": conflicting_citation.get("source_url") or conflicting.get("source_url"),
                "conflicting_claim": conflicting_claims.get(claim_key),
                "reason": reason,
            }
        return None

    def _answer_field_claims(self, item: dict[str, Any]) -> dict[str, str]:
        text = " ".join(str(item.get(key) or "") for key in ("content", "summary", "raw_excerpt"))
        claims: dict[str, str] = {}
        for match in ANSWER_CLAIM_RE.finditer(text):
            field = re.sub(r"\s+", " ", match.group("field").casefold()).strip()
            value = self._normalize_answer_claim_value(match.group("value"))
            if field and value:
                claims[field] = value
        return claims

    def _normalize_answer_claim_value(self, value: str) -> str:
        normalized = re.sub(r"\s+", " ", str(value or "").casefold()).strip(" .,:;")
        normalized = re.sub(r"^(?:the|a|an)\s+", "", normalized)
        normalized = re.split(
            r"\b(?:according to|because|before|after|for now|from|in the|instead of|rather than|until|with)\b",
            normalized,
            maxsplit=1,
        )[0].strip(" .,:;")
        return normalized[:120]

    def _answer_current_item(self, left: dict[str, Any], right: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
        left_authority = self._answer_authority_score(left)
        right_authority = self._answer_authority_score(right)
        if abs(left_authority - right_authority) >= 3:
            if left_authority > right_authority:
                return left, right, "trusted_canonical_source"
            return right, left, "trusted_canonical_source"

        left_time = _parse_iso_timestamp(str(left.get("occurred_at") or left.get("captured_at") or ""))
        right_time = _parse_iso_timestamp(str(right.get("occurred_at") or right.get("captured_at") or ""))
        if left_time and right_time and left_time != right_time:
            if left_time > right_time:
                return left, right, "newer_timestamp"
            return right, left, "newer_timestamp"

        left_current = self._answer_current_language_score(left)
        right_current = self._answer_current_language_score(right)
        if left_current != right_current:
            if left_current > right_current:
                return left, right, "current_language"
            return right, left, "current_language"

        return left, right, "ranking_order"

    def _answer_authority_score(self, item: dict[str, Any]) -> int:
        provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
        account_policy = provenance.get("source_account_policy") if isinstance(provenance.get("source_account_policy"), dict) else {}
        metadata = provenance.get("record_metadata") if isinstance(provenance.get("record_metadata"), dict) else {}
        score = 0
        if _normalize_source_key(str(account_policy.get("mode") or "")) == "trusted":
            score += 3

        quality_values = {
            str(metadata.get(key) or "").strip().lower()
            for key in ("source_quality", "quality", "trust", "trust_level", "verification")
        }
        if any(value in {"trusted", "high", "canonical", "verified"} for value in quality_values):
            score += 2
        if any(bool(metadata.get(key)) for key in ("trusted_source", "verified", "canonical")):
            score += 2
        if any(value in {"stale", "draft", "deprecated", "outdated", "noisy", "low"} for value in quality_values):
            score -= 2
        return score

    def _answer_current_language_score(self, item: dict[str, Any]) -> int:
        text = str(item.get("content") or item.get("summary") or "").casefold()
        score = 0
        for marker in ("now", "current", "currently", "updated", "moved", "changed", "instead", "supersedes", "replaces"):
            if marker in text:
                score += 1
        return score

    def _answer_support_query(self, query: str) -> str:
        tokens = [token.strip("_") for token in re.findall(r"[a-z0-9_]+", query.lower().replace("'", ""))]
        if not any(token in ANSWER_REASON_TERMS for token in tokens):
            return ""
        normalized: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            if (
                not token
                or token in QUERY_FTS_STOPWORDS
                or token in ANSWER_SUPPORT_QUERY_EXCLUDE
                or len(token) < 3
            ):
                continue
            if token not in seen:
                seen.add(token)
                normalized.append(token)
        if len(normalized) < 2:
            return ""
        return " ".join(normalized[:8])

    def _has_source_citation(self, item: dict[str, Any]) -> bool:
        source_url = str(item.get("source_url") or "").strip()
        return bool(source_url)

    def _is_related_result(self, item: dict[str, Any]) -> bool:
        relationship = item.get("relationship")
        return isinstance(relationship, dict) and bool(relationship)

    def _should_include_related_citation(
        self,
        query: str,
        item: dict[str, Any],
        primary_by_id: dict[str, dict[str, Any]],
    ) -> bool:
        relationship = item.get("relationship") if isinstance(item.get("relationship"), dict) else {}
        related_to_id = str(relationship.get("related_to_id") or "").strip()
        primary = primary_by_id.get(related_to_id)
        if not primary:
            return False
        relation_kind = str(relationship.get("kind") or "").strip()
        if relation_kind == "canvas_edge":
            return True
        if relation_kind == "obsidian_link":
            return True

        item_sector = str(item.get("sector") or "").strip().casefold()
        primary_sector = str(primary.get("sector") or "").strip().casefold()
        if item_sector and primary_sector and item_sector == primary_sector:
            return True

        terms = self._lexical_fallback_terms(query, limit=12)
        if not terms:
            return False
        matched = self._item_matched_query_terms(item, terms)
        minimum = 2 if len(terms) <= 4 else max(3, len(terms) // 2)
        return len(matched) >= minimum

    def _primary_citation_is_relevant(self, item: dict[str, Any], query_terms: list[str], *, exempt: bool = False) -> bool:
        # Ask must cite or abstain. Primary candidates are the highest-ranked search
        # hits, but on a small corpus the lexical/LIKE fallback returns a best-available
        # memory even when nothing truly matches (e.g. "capital of Mongolia" surfacing a
        # pricing note). Require at least one meaningful query term to actually appear in
        # the memory before it can be cited, so unrelated fallback hits produce an honest
        # abstention instead of a misleading citation. Exemptions preserve legitimate
        # non-keyword retrieval: layer-intent queries ("how do I usually write?", "what
        # changed recently?") and person-briefing hits are relevant by construction, not
        # by surface term overlap. When the query has no extractable terms, keep prior
        # behavior and let ranking decide rather than risk a wrong abstention.
        if exempt or not query_terms:
            return True
        return bool(self._item_matched_query_terms(item, query_terms))

    def _item_matched_query_terms(self, item: dict[str, Any], terms: list[str]) -> set[str]:
        topics = item.get("topics") if isinstance(item.get("topics"), list) else []
        text = " ".join(
            str(value or "")
            for value in (
                item.get("content"),
                item.get("summary"),
                item.get("source"),
                item.get("sector"),
                item.get("kind"),
                item.get("layer"),
                " ".join(str(topic or "") for topic in topics),
            )
        )
        item_terms: set[str] = set()
        for token in re.findall(r"[a-z0-9_]+", text.lower().replace("'", "")):
            clean = token.strip("_")
            if not clean:
                continue
            if len(clean) > 3 and clean.endswith("s") and not clean.endswith(("ss", "ies")):
                clean = clean[:-1]
            item_terms.add(clean)
        return {term for term in terms if any(candidate.startswith(term) for candidate in item_terms)}

    def _citation_metadata(self, item: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
        record_metadata = provenance.get("record_metadata") if isinstance(provenance.get("record_metadata"), dict) else {}
        external_id = str(provenance.get("external_id") or "").strip()
        citation_path = self._safe_relative_citation_path(record_metadata.get("relative_path"))
        metadata = {
            "source_account_id": provenance.get("source_account_id"),
            "external_id": external_id or None,
            "source_record_id": external_id or item.get("capture_id") or item.get("id"),
            "sector": item.get("sector") or None,
            "source_type": item.get("source_type") or None,
            "citation_path": citation_path or None,
        }
        for key in ("line_start", "line_end", "record_scope", "section_title", "block_id"):
            value = record_metadata.get(key)
            if value not in (None, "", [], {}):
                metadata[key] = value
        for key, value in self._source_url_citation_metadata(str(item.get("source_url") or "")).items():
            if metadata.get(key) in (None, "", [], {}):
                metadata[key] = value
        return {key: value for key, value in metadata.items() if value not in (None, "", [], {})}

    def _source_url_citation_metadata(self, source_url: str) -> dict[str, str]:
        source_url = str(source_url or "").strip()
        if not source_url:
            return {}
        allowed = {
            "service",
            "repository",
            "file",
            "line",
            "line_start",
            "line_end",
            "row",
            "message",
            "event",
            "subject",
            "page",
            "document",
            "channel",
            "record",
            "excerpt",
        }
        split = urlsplit(source_url)
        values: dict[str, str] = {}
        for part in (split.query, split.fragment):
            for key, value in parse_qsl(part, keep_blank_values=False):
                normalized_key = str(key or "").strip().lower()
                if normalized_key not in allowed:
                    continue
                cleaned = re.sub(r"\s+", " ", unquote(str(value or ""))).strip()
                if not cleaned:
                    continue
                output_key = "source_excerpt" if normalized_key == "excerpt" else normalized_key
                values.setdefault(output_key, cleaned[:240])
        if "line" in values and "line_start" not in values:
            values["line_start"] = values["line"]
        return values

    def _safe_relative_citation_path(self, value: Any) -> str:
        text = str(value or "").strip().replace("\\", "/")
        if not text or text.startswith("/") or "://" in text or "\x00" in text:
            return ""
        parts: list[str] = []
        for raw_part in text.split("/"):
            part = re.sub(r"[\r\n\t]+", " ", unquote(raw_part)).strip()
            if not part or part in {".", ".."}:
                continue
            parts.append(part[:160])
        return "/".join(parts)[:500]

    def _answer_excerpt(self, text: str, limit: int = 220) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: max(0, limit - 1)].rstrip() + "..."

    def open_tasks(self, user_id: str, limit: int = 20, *, include_pending: bool | None = None, sector: str | None = None) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            if include_pending is False:
                user_settings = {**user_settings, "allow_pending_in_context": False}
            filters, params = self._task_filters(user_id, user_settings, alias="t", capture_alias="c", sector=sector)
            where = " AND ".join(filters)
            rows = conn.execute(
                f"""
                SELECT
                  t.*,
                  c.source AS capture_source,
                  c.source_url AS capture_source_url,
                  c.title AS capture_title,
                  c.source_account_id AS capture_source_account_id,
                  c.external_id AS capture_external_id,
                  c.raw_text AS capture_raw_text
                FROM tasks t
                LEFT JOIN captures c ON c.id = t.capture_id AND c.user_id = t.user_id
                WHERE {where}
                ORDER BY t.importance DESC, t.captured_at DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def list_topics(
        self,
        user_id: str,
        limit: int = 30,
        *,
        include_pending: bool | None = None,
        sector: str | None = None,
    ) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            if include_pending is False:
                user_settings = {**user_settings, "allow_pending_in_context": False}
            filters, params = self._memory_filters(user_id, user_settings, alias="m", sector=sector)
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

    def list_entities(
        self,
        user_id: str,
        limit: int = 30,
        *,
        include_pending: bool | None = None,
        sector: str | None = None,
    ) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            if include_pending is False:
                user_settings = {**user_settings, "allow_pending_in_context": False}
            filters, params = self._memory_filters(user_id, user_settings, alias="m", sector=sector)
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

    def person_context(self, user_id: str, name: str, *, limit: int = 8) -> dict[str, Any]:
        name = str(name or "").strip()
        limit = max(1, min(20, int(limit)))
        slug = "person_" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        redact_sensitive = bool(self.settings(user_id)["redact_sensitive_context"])
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            entity_row = self._person_entity_row(conn, user_id, name, slug)
            entity = self._entity_from_row(entity_row) if entity_row else None
            open_commitments: list[dict[str, Any]] = []
            if entity:
                task_filters, task_params = self._task_filters(user_id, user_settings, alias="t", capture_alias="c")
                task_where = " AND ".join(task_filters)
                task_rows = conn.execute(
                    f"""
                    SELECT
                      t.*,
                      c.source AS capture_source,
                      c.source_url AS capture_source_url,
                      c.title AS capture_title,
                      c.source_account_id AS capture_source_account_id,
                      c.external_id AS capture_external_id,
                      c.raw_text AS capture_raw_text
                    FROM tasks t
                    JOIN task_entities te ON te.task_id = t.id AND te.user_id = t.user_id
                    LEFT JOIN captures c ON c.id = t.capture_id AND c.user_id = t.user_id
                    WHERE te.entity_id = ? AND {task_where}
                    ORDER BY t.importance DESC, t.captured_at DESC
                    LIMIT ?
                    """,
                    [entity["id"], *task_params, limit],
                ).fetchall()
                open_commitments = [self._task_from_row(row) for row in task_rows]
        memories: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        linked_memories = self._person_linked_memories(user_id, entity["id"], limit=limit * 2) if entity else []
        fallback_memories = self.about_person(user_id, name, limit=limit) if name else []
        for item in [*linked_memories, *fallback_memories]:
            memory_id = str(item.get("id") or "")
            if memory_id in seen_ids:
                continue
            seen_ids.add(memory_id)
            memories.append(item)
        resolved = bool(entity) or bool(memories)
        decisions = [item for item in memories if item.get("kind") == "decision"][:limit]
        decision_ids = {str(item.get("id") or "") for item in decisions}
        recent_context = [item for item in memories if str(item.get("id") or "") not in decision_ids][:limit]
        interaction_times = [
            str(item.get("occurred_at") or item.get("captured_at") or "")
            for item in memories
            if item.get("layer") == "episodic" or item.get("kind") == "event"
        ]
        interaction_times = [value for value in interaction_times if value]
        topic_counts: dict[str, int] = {}
        for item in memories:
            for topic in item.get("topics") or []:
                topic_counts[str(topic)] = topic_counts.get(str(topic), 0) + 1
        top_topics = [
            {"topic": topic, "count": count}
            for topic, count in sorted(topic_counts.items(), key=lambda entry: (-entry[1], entry[0]))[:8]
        ]
        citation_index = 0
        cited_commitments: list[dict[str, Any]] = []
        for item in open_commitments:
            citation_index += 1
            cited_commitments.append({**item, "citation": self._person_context_citation(item, citation_index, redact_sensitive=redact_sensitive)})
        cited_decisions: list[dict[str, Any]] = []
        for item in decisions:
            citation_index += 1
            cited_decisions.append({**item, "citation": self._person_context_citation(item, citation_index, redact_sensitive=redact_sensitive)})
        cited_context: list[dict[str, Any]] = []
        for item in recent_context:
            citation_index += 1
            cited_context.append({**item, "citation": self._person_context_citation(item, citation_index, redact_sensitive=redact_sensitive)})
        with connect(self.db_path) as conn:
            self._event(
                conn,
                user_id,
                entity["id"] if entity else slug,
                "entity",
                "person_context",
                {
                    "name": name[:80],
                    "resolved": resolved,
                    "memory_count": len(memories),
                    "open_commitment_count": len(open_commitments),
                    "decision_count": len(decisions),
                },
            )
        payload = {
            "person": {
                "id": entity["id"] if entity else None,
                "name": entity["name"] if entity else name,
                "aliases": entity["aliases"] if entity else [],
                "last_seen": entity["last_seen"] if entity else None,
            },
            "resolved": resolved,
            "last_interaction": max(interaction_times) if interaction_times else None,
            "open_commitments": cited_commitments,
            "decisions": cited_decisions,
            "recent_context": cited_context,
            "top_topics": top_topics,
        }
        return self._shared_payload(payload, redact_sensitive=redact_sensitive)

    def _person_entity_row(self, conn, user_id: str, name: str, slug: str):
        target = name.strip().casefold()
        if not target:
            return None
        alias_row = None
        for row in conn.execute(
            "SELECT * FROM entities WHERE user_id = ? AND kind = 'person' ORDER BY last_seen DESC, id",
            (user_id,),
        ).fetchall():
            if row["id"] == slug or str(row["name"] or "").strip().casefold() == target:
                return row
            if alias_row is None and any(str(alias or "").strip().casefold() == target for alias in json.loads(row["aliases_json"] or "[]")):
                alias_row = row
        return alias_row

    def _person_linked_memories(
        self,
        user_id: str,
        entity_id: str,
        *,
        limit: int,
        sector: str | None = None,
        source: str | None = None,
        source_account_id: str | None = None,
        metadata_filters: dict[str, Any] | None = None,
        as_of: str | None = None,
    ) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            filters, params = self._memory_filters(
                user_id,
                user_settings,
                alias="m",
                sector=sector,
                source=source,
                source_account_id=source_account_id,
                metadata_filters=metadata_filters,
                as_of=as_of,
            )
            where = " AND ".join(filters)
            rows = conn.execute(
                f"""
                SELECT m.*
                FROM memories m
                JOIN memory_entities me ON me.memory_id = m.id AND me.user_id = m.user_id
                WHERE me.entity_id = ? AND {where}
                ORDER BY m.captured_at DESC
                LIMIT ?
                """,
                [entity_id, *params, limit],
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def _person_context_citation(self, item: dict[str, Any], index: int, *, redact_sensitive: bool) -> dict[str, Any]:
        result_type = item.get("result_type") or "memory"
        provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
        excerpt = self._shared_text(item.get("content") or item.get("summary") or "", redact_sensitive=redact_sensitive)
        return {
            "index": index,
            "id": item["id"],
            "result_type": result_type,
            "kind": item["kind"],
            "layer": item.get("layer") or result_type,
            "status": item.get("status"),
            "source": item["source"],
            "source_url": self._safe_source_locator(item.get("source_url"), force_local=True),
            **self._citation_metadata(item, provenance),
            "captured_at": item.get("captured_at"),
            "occurred_at": item.get("occurred_at"),
            "excerpt": self._answer_excerpt(excerpt),
            "topics": item.get("topics") or [],
            "relationship": item.get("relationship"),
        }

    def _query_person_entity(self, user_id: str, query: str) -> dict[str, Any] | None:
        # The implicit Ask hook only fires for person-briefing intent. Generic
        # queries routinely contain capitalized tokens (projects, products)
        # that the extractor may have classified as person entities; merging
        # person context into those would reorder unrelated answers.
        if not _PERSON_QUERY_INTENT_RE.search(str(query or "")):
            return None
        stop = {"I", "The", "This", "That", "We", "You", "Next", "Open"}
        candidates: list[str] = []
        for match in re.findall(r"\b[A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+){0,3}\b", str(query or "")):
            for candidate in (match, *match.split()):
                if candidate not in stop and len(candidate) >= 3 and candidate not in candidates:
                    candidates.append(candidate)
        if not candidates:
            return None
        with connect(self.db_path) as conn:
            for candidate in candidates:
                slug = "person_" + re.sub(r"[^a-z0-9]+", "-", candidate.lower()).strip("-")
                row = self._person_entity_row(conn, user_id, candidate, slug)
                if row:
                    return self._entity_from_row(row)
        return None

    def decision_history(
        self,
        user_id: str,
        query: str = "",
        *,
        limit: int = 12,
        sector: str | None = None,
        include_superseded: bool = True,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        query = str(query or "").strip()
        limit = max(1, min(30, int(limit)))
        sector = _normalize_sector_filter(sector) or None
        redact_sensitive = bool(self.settings(user_id)["redact_sensitive_context"])
        current = self._decision_history_memories(
            user_id,
            query=query,
            limit=limit,
            sector=sector,
            superseded_only=False,
            include_superseded=False,
            as_of=as_of,
        )
        superseded = (
            self._decision_history_memories(
                user_id,
                query=query,
                limit=limit,
                sector=sector,
                superseded_only=True,
                include_superseded=True,
                as_of=as_of,
            )
            if include_superseded
            else []
        )
        seen: set[str] = set()
        timeline = []
        for item in sorted(
            [*current, *superseded],
            key=lambda memory: str(memory.get("occurred_at") or memory.get("captured_at") or ""),
            reverse=True,
        ):
            memory_id = str(item.get("id") or "")
            if memory_id in seen:
                continue
            seen.add(memory_id)
            timeline.append(item)
        topic_counts: dict[str, int] = {}
        for item in timeline:
            for topic in item.get("topics") or []:
                topic_counts[str(topic)] = topic_counts.get(str(topic), 0) + 1
        citation_index = 0

        def cite(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal citation_index
            cited: list[dict[str, Any]] = []
            for item in items:
                citation_index += 1
                cited.append({**item, "citation": self._person_context_citation(item, citation_index, redact_sensitive=redact_sensitive)})
            return cited

        cited_current = cite(current)
        cited_superseded = cite(superseded)
        timeline_by_id = {item["id"]: item for item in [*cited_current, *cited_superseded]}
        cited_timeline = [timeline_by_id[item["id"]] for item in timeline if item["id"] in timeline_by_id]
        with connect(self.db_path) as conn:
            self._event(
                conn,
                user_id,
                "decision_history",
                "memory",
                "decision_history",
                {
                    "query": query[:160],
                    "sector": sector,
                    "current_count": len(current),
                    "superseded_count": len(superseded),
                },
            )
        return self._shared_payload(
            {
                "query": query,
                "sector": sector,
                "generated_at": now_iso(),
                "current_decisions": cited_current,
                "superseded_decisions": cited_superseded,
                "timeline": cited_timeline,
                "top_topics": [
                    {"topic": topic, "count": count}
                    for topic, count in sorted(topic_counts.items(), key=lambda entry: (-entry[1], entry[0]))[:10]
                ],
                "counts": {
                    "current": len(current),
                    "superseded": len(superseded),
                    "timeline": len(cited_timeline),
                },
            },
            redact_sensitive=redact_sensitive,
        )

    def _decision_history_memories(
        self,
        user_id: str,
        *,
        query: str,
        limit: int,
        sector: str | None,
        superseded_only: bool,
        include_superseded: bool,
        as_of: str | None,
    ) -> list[dict[str, Any]]:
        if query and not superseded_only and not include_superseded:
            return self.search(user_id, query, limit=limit, kind="decision", sector=sector, as_of=as_of)
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            filters, params = self._memory_filters(user_id, user_settings, alias="m", kind="decision", sector=sector, as_of=as_of)
            if include_superseded:
                filters = [item for item in filters if item != "(m.superseded_by IS NULL OR m.superseded_by = '')"]
            if superseded_only:
                filters.append("COALESCE(m.superseded_by, '') != ''")
            elif not include_superseded:
                filters.append("COALESCE(m.superseded_by, '') = ''")
            if query:
                terms = [term for term in self._lexical_fallback_terms(query, limit=6) if len(term) >= 3]
                if terms:
                    like_clauses = []
                    for term in terms:
                        like_clauses.append("(lower(m.content) LIKE ? OR lower(m.summary) LIKE ? OR lower(m.source) LIKE ? OR lower(COALESCE(m.sector, '')) LIKE ?)")
                        value = f"%{term.lower()}%"
                        params.extend([value, value, value, value])
                    filters.append(f"({' OR '.join(like_clauses)})")
            where = " AND ".join(filters)
            rows = conn.execute(
                f"""
                SELECT m.*
                FROM memories m
                LEFT JOIN captures c ON c.id = m.capture_id AND c.user_id = m.user_id
                WHERE {where}
                ORDER BY COALESCE(m.occurred_at, m.captured_at) DESC, m.importance DESC, m.captured_at DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def action_brief(
        self,
        user_id: str,
        task: str,
        *,
        limit: int = 8,
        sector: str | None = None,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        task = str(task or "").strip()
        limit = max(1, min(20, int(limit)))
        sector = _normalize_sector_filter(sector) or None
        redact_sensitive = bool(self.settings(user_id)["redact_sensitive_context"])
        primary = self.search(user_id, task, limit=limit, sector=sector, include_related=True, as_of=as_of) if task else []

        def section_search(
            *,
            layer: str | None = None,
            kind: str | None = None,
            fallback_query: str,
            section_limit: int,
        ) -> list[dict[str, Any]]:
            items = self.search(user_id, task, limit=section_limit, kind=kind, layer=layer, sector=sector, as_of=as_of) if task else []
            if not items:
                items = self.search(user_id, fallback_query, limit=section_limit, kind=kind, layer=layer, sector=sector, as_of=as_of)
            if not items:
                items = self.search(user_id, "", limit=section_limit, kind=kind, layer=layer, sector=sector, as_of=as_of)
            return items

        procedures = section_search(layer="procedural", fallback_query="procedure checklist workflow", section_limit=min(limit, 6))
        decisions = section_search(kind="decision", fallback_query="decision", section_limit=min(limit, 6))
        preferences = section_search(layer="preference", fallback_query="preference prefer default", section_limit=min(limit, 6))
        negative_constraints = section_search(layer="negative", fallback_query="avoid reject dislike constraint", section_limit=min(limit, 6))
        style = section_search(layer="style", fallback_query="writing style tone phrasing", section_limit=min(limit, 4))
        with connect(self.db_path) as conn:
            task_rows = self._task_search_rows(
                conn,
                user_id,
                f"{task} next action todo follow up",
                min(limit, 6),
                "action",
                self._settings(conn, user_id),
            )
        open_actions = [self._task_search_result_from_row(row) for row in task_rows]

        citation_index = 0

        def cite_section(items: list[dict[str, Any]], section: str, max_items: int) -> list[dict[str, Any]]:
            nonlocal citation_index
            cited: list[dict[str, Any]] = []
            seen: set[str] = set()
            for item in items:
                memory_id = str(item.get("id") or "")
                if not memory_id or memory_id in seen:
                    continue
                seen.add(memory_id)
                citation_index += 1
                cited.append(
                    {
                        **item,
                        "brief_section": section,
                        "citation": self._person_context_citation(item, citation_index, redact_sensitive=redact_sensitive),
                    }
                )
                if len(cited) >= max_items:
                    break
            return cited

        cited_primary = cite_section(primary, "primary_context", limit)
        cited_procedures = cite_section(procedures, "procedures", min(limit, 6))
        cited_decisions = cite_section(decisions, "current_decisions", min(limit, 6))
        cited_preferences = cite_section(preferences, "preferences", min(limit, 6))
        cited_negative = cite_section(negative_constraints, "negative_constraints", min(limit, 6))
        cited_style = cite_section(style, "style_signals", min(limit, 4))
        cited_open_actions = cite_section(open_actions, "open_actions", min(limit, 6))
        sections = {
            "primary_context": cited_primary,
            "open_actions": cited_open_actions,
            "procedures": cited_procedures,
            "current_decisions": cited_decisions,
            "preferences": cited_preferences,
            "negative_constraints": cited_negative,
            "style_signals": cited_style,
        }
        all_items = [item for values in sections.values() for item in values]
        unique_items = {str(item.get("id") or ""): item for item in all_items if str(item.get("id") or "")}
        cited_count = sum(1 for item in unique_items.values() if self._has_source_citation(item))
        source_counts: dict[str, int] = {}
        for item in unique_items.values():
            source = str(item.get("source") or "unknown")
            source_counts[source] = source_counts.get(source, 0) + 1
        risk_flags: list[dict[str, str]] = []
        if not cited_primary:
            risk_flags.append({"code": "no_primary_context", "message": "No directly relevant approved memory was found for this task."})
        if not cited_procedures:
            risk_flags.append({"code": "no_known_procedure", "message": "No known procedure was found; ask before taking irreversible steps."})
        if not cited_decisions:
            risk_flags.append({"code": "no_prior_decision", "message": "No prior decision was found for this task or sector."})
        if cited_negative:
            risk_flags.append({"code": "constraints_present", "message": "Negative memory exists; avoid the listed rejected patterns."})
        if unique_items and cited_count < len(unique_items):
            risk_flags.append({"code": "citation_gap", "message": "Some retrieved memories do not have precise source URLs."})
        status = "strong" if cited_count >= 4 and (cited_procedures or cited_decisions) else "usable" if cited_count >= 2 else "limited"
        next_actions: list[str] = []
        if cited_negative:
            next_actions.append("Start by checking the negative constraints so the action avoids known rejected patterns.")
        if cited_decisions:
            next_actions.append("Use the current decisions as the policy boundary for the task.")
        if cited_procedures:
            next_actions.append("Follow the cited procedure before improvising a new workflow.")
        if cited_open_actions:
            next_actions.append("Work through the cited open actions in priority order before adding new tasks.")
        if cited_preferences or cited_style:
            next_actions.append("Shape the output using the cited preferences and style signals.")
        if status == "limited":
            next_actions.append("Ask the user for confirmation before taking action because Cortex has limited cited context.")
        if not next_actions:
            next_actions.append("Ask for more context before acting.")
        instructions = [
            "Use this brief as cited working context for the requested action.",
            "Prefer current decisions and procedures over generic memories.",
            "Honor negative constraints before style or preference signals.",
            "Treat open actions as the concrete queue, then use procedures and decisions to execute them.",
            "If the brief is limited or lacks a known procedure, ask for confirmation before acting.",
            "Keep citation IDs attached to any action rationale passed to another tool or AI surface.",
        ]
        action_plan = self._action_brief_plan(
            open_actions=cited_open_actions,
            procedures=cited_procedures,
            decisions=cited_decisions,
            negative_constraints=cited_negative,
            preferences=cited_preferences,
            style=cited_style,
        )
        execution_checklist = self._action_brief_execution_checklist(
            open_actions=cited_open_actions,
            procedures=cited_procedures,
            decisions=cited_decisions,
            negative_constraints=cited_negative,
            action_plan=action_plan,
        )
        payload: dict[str, Any] = {
            "generated_at": now_iso(),
            "task": task,
            "sector": sector,
            "status": status,
            "instructions": instructions,
            "next_actions": next_actions,
            "coverage": {
                "unique_memories": len(unique_items),
                "cited_memories": cited_count,
                "source_mix": [{"source": source, "count": count} for source, count in sorted(source_counts.items(), key=lambda entry: (-entry[1], entry[0]))],
                "sections": {name: len(items) for name, items in sections.items()},
            },
            "risk_flags": risk_flags,
            "action_plan": action_plan,
            "execution_checklist": execution_checklist,
            **sections,
        }
        payload["markdown"] = self._action_brief_markdown(payload)
        with connect(self.db_path) as conn:
            self._event(
                conn,
                user_id,
                "action_brief",
                "memory",
                "action_brief",
                {
                    "task": task[:160],
                    "sector": sector,
                    "status": status,
                    "cited_memories": cited_count,
                    "risk_flags": [item["code"] for item in risk_flags],
                },
            )
        return self._shared_payload(payload, redact_sensitive=redact_sensitive)

    def _action_brief_plan(
        self,
        *,
        open_actions: list[dict[str, Any]],
        procedures: list[dict[str, Any]],
        decisions: list[dict[str, Any]],
        negative_constraints: list[dict[str, Any]],
        preferences: list[dict[str, Any]],
        style: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        plan: list[dict[str, Any]] = []

        def add_item(section: str, action: str, item: dict[str, Any], reason: str, priority: int) -> None:
            if not action.strip():
                return
            citation = item.get("citation") if isinstance(item.get("citation"), dict) else {}
            plan.append(
                {
                    "rank": 0,
                    "section": section,
                    "action": action.strip(),
                    "reason": reason,
                    "priority": priority,
                    "evidence_id": item.get("id"),
                    "citation": citation,
                }
            )

        for item in negative_constraints[:2]:
            add_item(
                "negative_constraints",
                f"Check constraint before acting: {item.get('content') or item.get('summary') or ''}",
                item,
                "Avoids a known rejected pattern before work begins.",
                100 + int(item.get("importance") or 3),
            )
        for item in open_actions:
            add_item(
                "open_actions",
                item.get("content") or item.get("summary") or "",
                item,
                "Open task captured from source data.",
                90 + int(item.get("importance") or 3),
            )
        for item in procedures[:2]:
            add_item(
                "procedures",
                f"Follow procedure: {item.get('content') or item.get('summary') or ''}",
                item,
                "Known workflow for executing this kind of task.",
                80 + int(item.get("importance") or 3),
            )
        for item in decisions[:2]:
            add_item(
                "current_decisions",
                f"Apply decision boundary: {item.get('content') or item.get('summary') or ''}",
                item,
                "Prior decision constrains the action.",
                70 + int(item.get("importance") or 3),
            )
        for item in preferences[:1]:
            add_item(
                "preferences",
                f"Apply preference: {item.get('content') or item.get('summary') or ''}",
                item,
                "User preference affects how the action should be shaped.",
                60 + int(item.get("importance") or 3),
            )
        for item in style[:1]:
            add_item(
                "style_signals",
                f"Use style signal: {item.get('content') or item.get('summary') or ''}",
                item,
                "Writing style affects the output surface.",
                50 + int(item.get("importance") or 3),
            )
        plan.sort(key=lambda item: (-int(item["priority"]), str(item["action"])))
        for index, item in enumerate(plan[:8], start=1):
            item["rank"] = index
        return plan[:8]

    def _action_brief_execution_checklist(
        self,
        *,
        open_actions: list[dict[str, Any]],
        procedures: list[dict[str, Any]],
        decisions: list[dict[str, Any]],
        negative_constraints: list[dict[str, Any]],
        action_plan: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        checklist: list[dict[str, Any]] = []
        seen_steps: set[str] = set()

        def add_step(phase: str, step: str, item: dict[str, Any] | None, reason: str, required: bool = True) -> None:
            clean = self._clean_action_brief_step(step)
            if not clean:
                return
            signature = re.sub(r"[^a-z0-9]+", " ", clean.casefold()).strip()
            if not signature or signature in seen_steps:
                return
            seen_steps.add(signature)
            citation = item.get("citation") if isinstance(item, dict) and isinstance(item.get("citation"), dict) else {}
            checklist.append(
                {
                    "rank": 0,
                    "phase": phase,
                    "step": clean,
                    "reason": reason,
                    "required": required,
                    "evidence_id": item.get("id") if isinstance(item, dict) else None,
                    "citation": citation,
                }
            )

        for item in negative_constraints[:2]:
            add_step(
                "guardrails",
                f"Confirm this constraint is not violated: {item.get('content') or item.get('summary') or ''}",
                item,
                "Known rejected patterns should block or reshape the work before execution.",
            )
        for item in decisions[:2]:
            add_step(
                "decision_boundary",
                f"Apply this decision boundary: {item.get('content') or item.get('summary') or ''}",
                item,
                "Prior decisions define what the agent should not reopen without approval.",
            )
        for item in procedures[:3]:
            procedure_steps = self._action_brief_procedure_steps(item.get("content") or item.get("summary") or "")
            for step in procedure_steps[:5]:
                add_step(
                    "procedure",
                    step,
                    item,
                    "Cited procedure step for executing this task.",
                )
        for item in open_actions[:4]:
            add_step(
                "open_action",
                f"Complete open action: {item.get('content') or item.get('summary') or ''}",
                item,
                "Concrete open task from connected source data.",
            )
        verification_source = next((item for item in [*procedures, *open_actions, *action_plan] if isinstance(item, dict)), None)
        if checklist:
            add_step(
                "verification",
                "Before handoff, cite what changed, what remains open, and which checklist steps were completed.",
                verification_source,
                "Keeps downstream AI/tool use grounded in auditable evidence.",
            )
        for index, item in enumerate(checklist[:12], start=1):
            item["rank"] = index
        return checklist[:12]

    def _action_brief_procedure_steps(self, text: str) -> list[str]:
        text = str(text or "").strip()
        text = re.sub(r"(?i)^\s*(?:procedure|runbook|checklist|workflow)\s*:\s*", "", text).strip()
        if not text:
            return []
        lines = [line.strip(" \t-*0123456789.)") for line in text.splitlines() if line.strip()]
        candidates: list[str] = []
        for line in lines or [text]:
            normalized = re.sub(r"(?i)\b(?:and then|then)\b", ";", line)
            normalized = re.sub(
                r",\s*(?=(?:approve|ask|back up|backup|build|check|confirm|connect|create|export|invite|review|run|send|sync|test|verify)\b)",
                "; ",
                normalized,
            )
            parts = [part.strip(" .,:;") for part in normalized.split(";") if part.strip(" .,:;")]
            candidates.extend(parts or [line.strip(" .,:;")])
        steps: list[str] = []
        for candidate in candidates:
            clean = self._clean_action_brief_step(candidate)
            if not clean:
                continue
            if len(clean) > 180:
                clean = clean[:177].rstrip() + "..."
            if clean not in steps:
                steps.append(clean)
        return steps[:8]

    def _clean_action_brief_step(self, value: str) -> str:
        clean = re.sub(r"\s+", " ", str(value or "")).strip(" .,:;")
        clean = re.sub(r"(?i)^before\s+[^,]{3,120},\s*", "", clean).strip(" .,:;")
        if not clean:
            return ""
        return clean[:220]

    def _action_brief_markdown(self, brief: dict[str, Any]) -> str:
        lines = [
            "# Cortex Action Brief",
            "",
            f"Generated: {brief['generated_at']}",
            f"Task: {brief.get('task') or 'unspecified'}",
            f"Status: {brief['status']}",
        ]
        if brief.get("sector"):
            lines.append(f"Sector: {brief['sector']}")
        lines.extend(["", "## Instructions", ""])
        for instruction in brief.get("instructions") or []:
            lines.append(f"- {instruction}")
        if brief.get("next_actions"):
            lines.extend(["", "## Next Actions", ""])
            for action in brief["next_actions"]:
                lines.append(f"- {action}")
        if brief.get("risk_flags"):
            lines.extend(["", "## Risk Flags", ""])
            for flag in brief["risk_flags"]:
                lines.append(f"- {flag['code']}: {flag['message']}")
        if brief.get("action_plan"):
            lines.extend(["", "## Action Plan", ""])
            for item in brief["action_plan"]:
                citation = item.get("citation") if isinstance(item.get("citation"), dict) else {}
                source = citation.get("source_url") or "unknown source"
                lines.append(f"- {item['rank']}. {item['action']} Reason: {item['reason']} Source: {source}.")
        if brief.get("execution_checklist"):
            lines.extend(["", "## Execution Checklist", ""])
            for item in brief["execution_checklist"]:
                citation = item.get("citation") if isinstance(item.get("citation"), dict) else {}
                source = citation.get("source_url") or "unknown source"
                required = "required" if item.get("required", True) else "optional"
                lines.append(f"- {item['rank']}. [{item['phase']}, {required}] {item['step']} Source: {source}.")
        for key, title in (
            ("primary_context", "Primary Context"),
            ("open_actions", "Open Actions"),
            ("procedures", "Procedures"),
            ("current_decisions", "Current Decisions"),
            ("preferences", "Preferences"),
            ("negative_constraints", "Negative Constraints"),
            ("style_signals", "Style Signals"),
        ):
            lines.extend(["", f"## {title}", ""])
            items = brief.get(key) or []
            if not items:
                lines.append("- No cited memory found.")
                continue
            for item in items:
                citation = item.get("citation") if isinstance(item.get("citation"), dict) else {}
                source = citation.get("source_url") or item.get("source_url") or item.get("source") or "unknown source"
                lines.append(f"- [{item['id']}] {item.get('content') or item.get('summary') or ''} Source: {source}.")
        lines.extend(["", "## Coverage", ""])
        coverage = brief.get("coverage") or {}
        lines.append(f"- Unique memories: {coverage.get('unique_memories', 0)}")
        lines.append(f"- Cited memories: {coverage.get('cited_memories', 0)}")
        if coverage.get("source_mix"):
            lines.append("- Sources: " + ", ".join(f"{item['source']} ({item['count']})" for item in coverage["source_mix"]))
        return "\n".join(lines)

    def archive_memory(self, user_id: str, memory_id: str) -> bool:
        timestamp = now_iso()
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT id FROM memories WHERE user_id = ? AND id = ?", (user_id, memory_id)).fetchone()
            if not row:
                return False
            conn.execute("UPDATE memories SET status = 'archived', updated_at = ? WHERE user_id = ? AND id = ?", (timestamp, user_id, memory_id))
            conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
            conn.execute(
                "DELETE FROM memory_relations WHERE user_id = ? AND (source_memory_id = ? OR target_memory_id = ?)",
                (user_id, memory_id, memory_id),
            )
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

    def _purge_capture_derivatives_in_conn(self, conn, user_id: str, capture_id: str) -> dict[str, Any]:
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
        for memory_id in memory_ids:
            self.vault.delete_memory(memory_id)
        for task_id in task_ids:
            self.vault.delete_task(task_id)
        for edge_id in edge_ids:
            self.vault.delete_edge(edge_id)
        return {
            "memory_ids": memory_ids,
            "task_ids": task_ids,
            "edge_ids": edge_ids,
            "memory_count": len(memory_ids),
            "task_count": len(task_ids),
            "edge_count": len(edge_ids),
        }

    def _archive_capture_derivatives_in_conn(self, conn, user_id: str, capture_id: str, *, timestamp: str) -> dict[str, Any]:
        memory_rows = conn.execute(
            """
            SELECT id, kind, layer
            FROM memories
            WHERE user_id = ?
              AND capture_id = ?
              AND status != 'archived'
            """,
            (user_id, capture_id),
        ).fetchall()
        task_rows = conn.execute(
            """
            SELECT id
            FROM tasks
            WHERE user_id = ?
              AND capture_id = ?
              AND status != 'archived'
            """,
            (user_id, capture_id),
        ).fetchall()
        memory_ids = [row["id"] for row in memory_rows]
        task_ids = [row["id"] for row in task_rows]
        edge_ids = self._purge_edges_for_objects(conn, user_id, [capture_id, *memory_ids, *task_ids])
        if memory_ids:
            placeholders = ",".join("?" for _ in memory_ids)
            conn.execute(
                f"""
                UPDATE memories
                SET status = 'archived',
                    valid_to = CASE WHEN valid_to IS NULL OR valid_to = '' THEN ? ELSE valid_to END,
                    updated_at = ?
                WHERE user_id = ? AND id IN ({placeholders})
                """,
                [timestamp, timestamp, user_id, *memory_ids],
            )
            conn.execute(
                f"DELETE FROM memory_relations WHERE user_id = ? AND (source_memory_id IN ({placeholders}) OR target_memory_id IN ({placeholders}))",
                [user_id, *memory_ids, *memory_ids],
            )
            conn.execute(f"DELETE FROM memory_fts WHERE memory_id IN ({placeholders})", memory_ids)
            conn.execute(
                f"DELETE FROM memory_jobs WHERE user_id = ? AND object_type = 'memory' AND object_id IN ({placeholders})",
                [user_id, *memory_ids],
            )
            for memory_id in memory_ids:
                self._delete_memory_vector(conn, memory_id)
                self.vault.patch_memory(memory_id, {"status": "archived", "valid_to": timestamp, "updated_at": timestamp})
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)
            conn.execute(
                f"UPDATE tasks SET status = 'archived' WHERE user_id = ? AND id IN ({placeholders})",
                [user_id, *task_ids],
            )
            for task_id in task_ids:
                self.vault.patch_task(task_id, {"status": "archived"})
        conn.execute(
            "DELETE FROM memory_jobs WHERE user_id = ? AND object_type = 'capture' AND object_id = ?",
            (user_id, capture_id),
        )
        for edge_id in edge_ids:
            self.vault.delete_edge(edge_id)
        return {
            "memory_ids": memory_ids,
            "task_ids": task_ids,
            "edge_ids": edge_ids,
            "memory_count": len(memory_ids),
            "task_count": len(task_ids),
            "edge_count": len(edge_ids),
        }

    def _link_superseded_capture_memories_in_conn(
        self,
        conn,
        user_id: str,
        capture_id: str,
        replacements: list[dict[str, Any]],
        *,
        timestamp: str,
    ) -> int:
        replacement_ids = [str(memory.get("id") or "") for memory in replacements if str(memory.get("id") or "")]
        if not replacement_ids:
            return 0
        replacement_by_kind_layer: dict[tuple[str, str], str] = {}
        for memory in replacements:
            memory_id = str(memory.get("id") or "")
            if not memory_id:
                continue
            replacement_by_kind_layer.setdefault((str(memory.get("kind") or ""), str(memory.get("layer") or "")), memory_id)
        replacement_set = set(replacement_ids)
        rows = conn.execute(
            """
            SELECT id, kind, layer
            FROM memories
            WHERE user_id = ?
              AND capture_id = ?
              AND status = 'archived'
              AND (superseded_by IS NULL OR superseded_by = '')
            """,
            (user_id, capture_id),
        ).fetchall()
        linked = 0
        for row in rows:
            memory_id = row["id"]
            if memory_id in replacement_set:
                continue
            replacement_id = replacement_by_kind_layer.get((row["kind"], row["layer"])) or replacement_ids[0]
            conn.execute(
                "UPDATE memories SET superseded_by = ?, updated_at = ? WHERE user_id = ? AND id = ?",
                (replacement_id, timestamp, user_id, memory_id),
            )
            self.vault.patch_memory(memory_id, {"superseded_by": replacement_id, "updated_at": timestamp})
            linked += 1
        return linked

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
            row = conn.execute("SELECT id, review_status FROM captures WHERE user_id = ? AND id = ?", (user_id, capture_id)).fetchone()
            if not row:
                return False
            if row["review_status"] == "archived":
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
            if memory_ids:
                placeholders = ",".join("?" for _ in memory_ids)
                conn.execute(
                    f"DELETE FROM memory_relations WHERE user_id = ? AND (source_memory_id IN ({placeholders}) OR target_memory_id IN ({placeholders}))",
                    [user_id, *memory_ids, *memory_ids],
                )
            self._event(conn, user_id, capture_id, "capture", "archived", {"memory_count": len(memory_ids)})
            self.vault.patch_capture(capture_id, {"review_status": "archived", "archived_at": timestamp})
            for memory_id in memory_ids:
                self.vault.patch_memory(memory_id, {"status": "archived", "updated_at": timestamp})
            for task_id in task_ids:
                self.vault.patch_task(task_id, {"status": "archived"})
        return True

    def active_memory_count(self, user_id: str) -> int:
        """Cheap count of a user's active memories, for per-user quota checks."""
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE user_id = ? AND status = 'active'",
                (user_id,),
            ).fetchone()
        return int(row[0] or 0)

    def stats(self, user_id: str) -> dict[str, Any]:
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            stats_settings = {**user_settings, "allow_pending_in_context": False}
            memory_filters, memory_params = self._memory_filters(user_id, stats_settings, alias="m")
            memory_where = " AND ".join(memory_filters)
            decision_filters, decision_params = self._memory_filters(user_id, stats_settings, alias="m", kind="decision")
            decision_where = " AND ".join(decision_filters)
            task_filters, task_params = self._task_filters(user_id, stats_settings, alias="t", capture_alias="c")
            task_where = " AND ".join(task_filters)
            counts = {
                "captures": conn.execute("SELECT COUNT(*) FROM captures WHERE user_id = ?", (user_id,)).fetchone()[0],
                "pending_captures": conn.execute("SELECT COUNT(*) FROM captures WHERE user_id = ? AND review_status = 'pending'", (user_id,)).fetchone()[0],
                "memories": conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM memories m
                    WHERE {memory_where}
                    """,
                    memory_params,
                ).fetchone()[0],
                "decisions": conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM memories m
                    WHERE {decision_where}
                    """,
                    decision_params,
                ).fetchone()[0],
                "tasks": conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM tasks t
                    LEFT JOIN captures c ON c.id = t.capture_id AND c.user_id = t.user_id
                    WHERE {task_where}
                    """,
                    task_params,
                ).fetchone()[0],
                "entities": conn.execute(
                    f"""
                    SELECT COUNT(DISTINCT e.id)
                    FROM entities e
                    JOIN memory_entities me ON me.entity_id = e.id AND me.user_id = e.user_id
                    JOIN memories m ON m.id = me.memory_id AND m.user_id = me.user_id AND m.status = 'active'
                    WHERE e.user_id = ? AND {memory_where}
                    """,
                    [user_id, *memory_params],
                ).fetchone()[0],
                "edges": conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM graph_edges ge
                    LEFT JOIN captures c ON c.id = ge.evidence_id AND c.user_id = ge.user_id
                    LEFT JOIN source_accounts csa ON csa.id = c.source_account_id AND csa.user_id = c.user_id
                    LEFT JOIN memories m ON m.id = ge.evidence_id AND m.user_id = ge.user_id
                    LEFT JOIN captures mc ON mc.id = m.capture_id AND mc.user_id = m.user_id
                    LEFT JOIN source_accounts mcsa ON mcsa.id = mc.source_account_id AND mcsa.user_id = mc.user_id
                    LEFT JOIN tasks t ON t.id = ge.evidence_id AND t.user_id = ge.user_id
                    LEFT JOIN captures tc ON tc.id = t.capture_id AND tc.user_id = t.user_id
                    LEFT JOIN source_accounts tcsa ON tcsa.id = tc.source_account_id AND tcsa.user_id = tc.user_id
                    WHERE ge.user_id = ?
                      AND (
                        ge.evidence_id IS NULL
                        OR (
                          c.id IS NOT NULL
                          AND (c.review_status = 'approved' OR COALESCE(json_extract(csa.policy_json, '$.review_required'), 1) = 0)
                          AND COALESCE(json_extract(csa.policy_json, '$.allow_ai_context'), 1) != 0
                        )
                        OR (
                          m.status = 'active'
                          AND (
                            m.capture_id IS NULL
                            OR (
                              mc.id IS NOT NULL
                              AND (mc.review_status = 'approved' OR COALESCE(json_extract(mcsa.policy_json, '$.review_required'), 1) = 0)
                              AND COALESCE(json_extract(mcsa.policy_json, '$.allow_ai_context'), 1) != 0
                            )
                          )
                        )
                        OR (
                          t.status = 'open'
                          AND (
                            t.capture_id IS NULL
                            OR (
                              tc.id IS NOT NULL
                              AND (tc.review_status = 'approved' OR COALESCE(json_extract(tcsa.policy_json, '$.review_required'), 1) = 0)
                              AND COALESCE(json_extract(tcsa.policy_json, '$.allow_ai_context'), 1) != 0
                            )
                          )
                        )
                      )
                    """,
                    (user_id,),
                ).fetchone()[0],
            }
            by_kind = [
                {"kind": row["kind"], "count": row["count"]}
                for row in conn.execute(
                    f"""
                    SELECT m.kind, COUNT(*) AS count
                    FROM memories m
                    WHERE {memory_where}
                    GROUP BY m.kind
                    ORDER BY count DESC
                    """,
                    memory_params,
                ).fetchall()
            ]
            by_layer = [
                {"layer": row["layer"], "count": row["count"]}
                for row in conn.execute(
                    f"""
                    SELECT m.layer, COUNT(*) AS count
                    FROM memories m
                    WHERE {memory_where}
                    GROUP BY m.layer
                    ORDER BY count DESC
                    """,
                    memory_params,
                ).fetchall()
            ]
            top_topics = [
                {"topic": row["topic"], "count": row["count"]}
                for row in conn.execute(
                    f"""
                    SELECT mt.topic, COUNT(*) AS count
                    FROM memory_topics mt
                    JOIN memories m ON m.id = mt.memory_id AND m.user_id = mt.user_id
                    WHERE mt.user_id = ? AND {memory_where}
                    GROUP BY mt.topic
                    ORDER BY count DESC, mt.topic
                    LIMIT 12
                    """,
                    [user_id, *memory_params],
                ).fetchall()
            ]
            top_entities = [
                {"id": row["id"], "name": row["name"], "kind": row["kind"], "count": row["count"]}
                for row in conn.execute(
                    f"""
                    SELECT e.id, e.name, e.kind, COUNT(m.id) AS count
                    FROM entities e
                    JOIN memory_entities me ON me.entity_id = e.id AND me.user_id = e.user_id
                    JOIN memories m ON m.id = me.memory_id AND m.user_id = me.user_id AND m.status = 'active'
                    WHERE e.user_id = ? AND {memory_where}
                    GROUP BY e.id
                    ORDER BY count DESC, e.last_seen DESC
                    LIMIT 12
                    """,
                    [user_id, *memory_params],
                ).fetchall()
            ]
        return {**counts, "by_kind": by_kind, "by_layer": by_layer, "top_topics": top_topics, "top_entities": top_entities}

    def memory_quality_report(self, user_id: str) -> dict[str, Any]:
        expected_layers = {"semantic", "episodic", "style", "decision", "preference", "negative", "procedural"}
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
                "dated_memories": conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE user_id = ? AND status = 'active' AND COALESCE(occurred_at, '') != ''",
                    (user_id,),
                ).fetchone()[0],
                "temporal_memories": conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE user_id = ? AND status = 'active' AND layer IN ('episodic', 'decision')",
                    (user_id,),
                ).fetchone()[0],
                "dated_temporal_memories": conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE user_id = ? AND status = 'active' AND layer IN ('episodic', 'decision') AND COALESCE(occurred_at, '') != ''",
                    (user_id,),
                ).fetchone()[0],
                "sector_memories": conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE user_id = ? AND status = 'active' AND COALESCE(sector, '') != ''",
                    (user_id,),
                ).fetchone()[0],
                "source_type_memories": conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE user_id = ? AND status = 'active' AND COALESCE(source_type, '') != ''",
                    (user_id,),
                ).fetchone()[0],
                "provenance_memories": conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM memories
                    WHERE user_id = ?
                      AND status = 'active'
                      AND COALESCE(provenance_json, '') NOT IN ('', '{}')
                      AND COALESCE(json_extract(provenance_json, '$.source'), '') != ''
                    """,
                    (user_id,),
                ).fetchone()[0],
                "provenance_complete_memories": conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM memories
                    WHERE user_id = ?
                      AND status = 'active'
                      AND COALESCE(source_url, '') != ''
                      AND COALESCE(json_extract(provenance_json, '$.source'), '') != ''
                      AND (
                        COALESCE(raw_excerpt, '') != ''
                        OR COALESCE(json_extract(provenance_json, '$.external_id'), '') != ''
                        OR json_type(provenance_json, '$.record_metadata') = 'object'
                      )
                    """,
                    (user_id,),
                ).fetchone()[0],
            }
            vector_ready = self._vector_ready(conn)
            active_vector_memories = (
                conn.execute(
                    """
                    SELECT COUNT(DISTINCT m.id)
                    FROM memories m
                    JOIN memory_vec_map map
                      ON map.memory_id = m.id
                     AND map.user_id = m.user_id
                    WHERE m.user_id = ?
                      AND m.status = 'active'
                    """,
                    (user_id,),
                ).fetchone()[0]
                if vector_ready
                else 0
            )
            relation_rows = conn.execute(
                "SELECT COUNT(*) FROM memory_relations WHERE user_id = ?",
                (user_id,),
            ).fetchone()[0]
            active_relation_rows = conn.execute(
                """
                SELECT COUNT(*)
                FROM memory_relations mr
                JOIN memories source_memory
                  ON source_memory.id = mr.source_memory_id
                 AND source_memory.user_id = mr.user_id
                 AND source_memory.status = 'active'
                JOIN memories target_memory
                  ON target_memory.id = mr.target_memory_id
                 AND target_memory.user_id = mr.user_id
                 AND target_memory.status = 'active'
                WHERE mr.user_id = ?
                """,
                (user_id,),
            ).fetchone()[0]
            related_memories = conn.execute(
                """
                SELECT COUNT(DISTINCT memory_id)
                FROM (
                  SELECT mr.source_memory_id AS memory_id
                  FROM memory_relations mr
                  JOIN memories source_memory
                    ON source_memory.id = mr.source_memory_id
                   AND source_memory.user_id = mr.user_id
                   AND source_memory.status = 'active'
                  JOIN memories target_memory
                    ON target_memory.id = mr.target_memory_id
                   AND target_memory.user_id = mr.user_id
                   AND target_memory.status = 'active'
                  WHERE mr.user_id = ?
                  UNION
                  SELECT mr.target_memory_id AS memory_id
                  FROM memory_relations mr
                  JOIN memories source_memory
                    ON source_memory.id = mr.source_memory_id
                   AND source_memory.user_id = mr.user_id
                   AND source_memory.status = 'active'
                  JOIN memories target_memory
                    ON target_memory.id = mr.target_memory_id
                   AND target_memory.user_id = mr.user_id
                   AND target_memory.status = 'active'
                  WHERE mr.user_id = ?
                )
                """,
                (user_id, user_id),
            ).fetchone()[0]
            totals["uncited_memories"] = max(0, totals["active_memories"] - totals["cited_memories"])
            totals["undated_temporal_memories"] = max(0, totals["temporal_memories"] - totals["dated_temporal_memories"])
            totals["unsectored_memories"] = max(0, totals["active_memories"] - totals["sector_memories"])
            totals["missing_source_type_memories"] = max(0, totals["active_memories"] - totals["source_type_memories"])
            totals["weak_provenance_memories"] = max(0, totals["active_memories"] - totals["provenance_complete_memories"])
            totals["vector_memories"] = active_vector_memories
            totals["missing_vector_memories"] = max(0, totals["active_memories"] - active_vector_memories)
            totals["memory_relations"] = relation_rows
            totals["active_memory_relations"] = active_relation_rows
            totals["orphaned_memory_relations"] = max(0, relation_rows - active_relation_rows)
            totals["related_memories"] = related_memories
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
                  SUM(CASE WHEN m.id IS NOT NULL AND COALESCE(m.occurred_at, '') != '' THEN 1 ELSE 0 END) AS dated_memories,
                  SUM(CASE WHEN m.id IS NOT NULL AND COALESCE(m.sector, '') != '' THEN 1 ELSE 0 END) AS sector_memories,
                  SUM(CASE WHEN m.id IS NOT NULL AND COALESCE(m.source_type, '') != '' THEN 1 ELSE 0 END) AS source_type_memories,
                  SUM(CASE
                    WHEN m.id IS NOT NULL
                     AND COALESCE(m.source_url, '') != ''
                     AND COALESCE(json_extract(m.provenance_json, '$.source'), '') != ''
                     AND (
                       COALESCE(m.raw_excerpt, '') != ''
                       OR COALESCE(json_extract(m.provenance_json, '$.external_id'), '') != ''
                       OR json_type(m.provenance_json, '$.record_metadata') = 'object'
                     )
                    THEN 1 ELSE 0 END) AS provenance_complete_memories,
                  SUM(CASE WHEN m.id IS NOT NULL AND m.layer IN ('episodic', 'decision') THEN 1 ELSE 0 END) AS temporal_memories,
                  SUM(CASE WHEN m.id IS NOT NULL AND m.layer IN ('episodic', 'decision') AND COALESCE(m.occurred_at, '') != '' THEN 1 ELSE 0 END) AS dated_temporal_memories,
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
        date_coverage = 0.0 if totals["active_memories"] == 0 else (1.0 if totals["temporal_memories"] == 0 else _ratio(totals["dated_temporal_memories"], totals["temporal_memories"]))
        review_coverage = _ratio(totals["captures"] - totals["pending_captures"], totals["captures"])
        layer_coverage = _ratio(len(layers_present & expected_layers), len(expected_layers))
        sector_coverage = _ratio(totals["sector_memories"], totals["active_memories"])
        source_type_coverage = _ratio(totals["source_type_memories"], totals["active_memories"])
        provenance_coverage = _ratio(totals["provenance_complete_memories"], totals["active_memories"])
        vector_coverage = _ratio(totals["vector_memories"], totals["active_memories"])
        relation_coverage = _ratio(totals["related_memories"], totals["active_memories"])
        relation_integrity = 1.0 if totals["memory_relations"] == 0 else _ratio(totals["active_memory_relations"], totals["memory_relations"])
        volume_score = _ratio(min(totals["active_memories"], 50), 50)
        base_score = citation_coverage * 30 + date_coverage * 15 + review_coverage * 20 + layer_coverage * 20 + volume_score * 15
        score = round(
            base_score * 0.82
            + provenance_coverage * 8
            + source_type_coverage * 4
            + sector_coverage * 3
            + relation_integrity * 3
        )
        score = max(0, min(100, score))
        status = "strong" if score >= 80 else "usable" if score >= 55 else "needs_sources" if totals["active_memories"] == 0 else "needs_review"

        warnings: list[str] = []
        recommendations: list[str] = []
        if totals["active_memories"] == 0:
            warnings.append("No active memories are available yet.")
            recommendations.append("Connect local AI tools or Obsidian notes and approve useful memory before relying on Ask.")
        if totals["active_memories"] > 0 and citation_coverage < 0.8:
            warnings.append("Some active memories are missing source citations.")
            recommendations.append("Prefer connected-source sync and cited captures so retrieved memory has citations.")
        if totals["temporal_memories"] > 0 and date_coverage < 0.6:
            warnings.append("Many decision and event memories are missing dates.")
            recommendations.append("Sync sources with timestamps or include dates in decisions and events.")
        if totals["pending_captures"] > 0 and _ratio(totals["pending_captures"], totals["captures"]) > 0.25:
            warnings.append("A large share of captured data is still pending review.")
            recommendations.append("Review or archive pending captures to improve model reliability.")
        if totals["active_memories"] > 0 and layer_coverage < 0.5:
            warnings.append("Memory coverage is concentrated in too few layers.")
            recommendations.append("Add decisions, writing samples, preferences, and rejected approaches for better adaptation.")
        if totals["active_memories"] > 0 and provenance_coverage < 0.8:
            warnings.append("Some active memories have weak provenance metadata.")
            recommendations.append("Prefer connected-source sync with source URLs, excerpts, and stable external IDs.")
        if totals["active_memories"] > 0 and sector_coverage < 0.5:
            warnings.append("Many memories are not assigned to a sector or workspace.")
            recommendations.append("Use source metadata such as vault, workspace, project, repository, or team to improve scoped retrieval.")
        if totals["orphaned_memory_relations"] > 0:
            warnings.append("Some memory relationship rows no longer point at active memories.")
            recommendations.append("Run storage repair before relying on related-memory expansion.")
        if totals["active_memories"] > 0 and vector_ready and vector_coverage < 0.8:
            warnings.append("Vector retrieval coverage is still catching up.")
            recommendations.append("Run the local worker so queued embedding jobs can finish.")

        source_health = []
        for row in source_rows:
            active_memories = int(row["active_memories"] or 0)
            cited_memories = int(row["cited_memories"] or 0)
            dated_memories = int(row["dated_memories"] or 0)
            sector_memories = int(row["sector_memories"] or 0)
            source_type_memories = int(row["source_type_memories"] or 0)
            provenance_complete_memories = int(row["provenance_complete_memories"] or 0)
            temporal_memories = int(row["temporal_memories"] or 0)
            dated_temporal_memories = int(row["dated_temporal_memories"] or 0)
            pending = int(row["pending"] or 0)
            captures = int(row["captures"] or 0)
            source_date_coverage = 1.0 if active_memories and temporal_memories == 0 else _ratio(dated_temporal_memories, temporal_memories)
            source_sector_coverage = _ratio(sector_memories, active_memories)
            source_type_coverage_value = _ratio(source_type_memories, active_memories)
            source_provenance_coverage = _ratio(provenance_complete_memories, active_memories)
            source_warnings: list[str] = []
            if active_memories and cited_memories < active_memories:
                source_warnings.append("missing citations")
            if temporal_memories and source_date_coverage < 0.6:
                source_warnings.append("missing dates")
            if captures and pending / captures > 0.5:
                source_warnings.append("mostly pending")
            if active_memories and source_provenance_coverage < 0.8:
                source_warnings.append("weak provenance")
            if active_memories and source_sector_coverage < 0.5:
                source_warnings.append("missing sectors")
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
                    "dated_memories": dated_memories,
                    "temporal_memories": temporal_memories,
                    "dated_temporal_memories": dated_temporal_memories,
                    "undated_temporal_memories": max(0, temporal_memories - dated_temporal_memories),
                    "sector_memories": sector_memories,
                    "unsectored_memories": max(0, active_memories - sector_memories),
                    "source_type_memories": source_type_memories,
                    "missing_source_type_memories": max(0, active_memories - source_type_memories),
                    "provenance_complete_memories": provenance_complete_memories,
                    "weak_provenance_memories": max(0, active_memories - provenance_complete_memories),
                    "citation_coverage": _ratio(cited_memories, active_memories),
                    "date_coverage": source_date_coverage,
                    "sector_coverage": source_sector_coverage,
                    "source_type_coverage": source_type_coverage_value,
                    "provenance_coverage": source_provenance_coverage,
                    "last_seen": row["last_seen"],
                    "status": source_status,
                    "warnings": source_warnings,
                }
            )

        relation_health = {
            "status": "ok" if totals["orphaned_memory_relations"] == 0 else "needs_repair",
            "relations": totals["memory_relations"],
            "active_relations": totals["active_memory_relations"],
            "orphaned_relations": totals["orphaned_memory_relations"],
            "related_memories": totals["related_memories"],
            "relation_coverage": relation_coverage,
            "relation_integrity": relation_integrity,
        }
        vector_health = {
            "status": "ok" if totals["active_memories"] == 0 or vector_coverage >= 0.8 else "catching_up" if vector_ready else "not_available",
            "available": vector_ready,
            "provider": embedding_status()["provider"],
            "model": embedding_status()["model"],
            "indexed_memories": totals["vector_memories"],
            "missing_memories": totals["missing_vector_memories"],
            "coverage": vector_coverage,
        }

        return {
            "generated_at": now_iso(),
            "score": score,
            "status": status,
            "citation_coverage": citation_coverage,
            "date_coverage": date_coverage,
            "review_coverage": review_coverage,
            "layer_coverage": layer_coverage,
            "sector_coverage": sector_coverage,
            "source_type_coverage": source_type_coverage,
            "provenance_coverage": provenance_coverage,
            "vector_coverage": vector_coverage,
            "relation_coverage": relation_coverage,
            "relation_integrity": relation_integrity,
            "layers_present": sorted(layers_present),
            "totals": totals,
            "relation_health": relation_health,
            "vector_health": vector_health,
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
            used_today = conn.execute(
                """
                SELECT COUNT(*)
                FROM memory_events
                WHERE user_id = ?
                  AND object_type = 'loop'
                  AND event_type IN ('context_reused', 'cited_answer_used')
                  AND substr(created_at, 1, 10) = date('now')
                """,
                (user_id,),
            ).fetchone()[0]
            last_used_at = conn.execute(
                """
                SELECT MAX(created_at)
                FROM memory_events
                WHERE user_id = ?
                  AND object_type = 'loop'
                  AND event_type IN ('context_reused', 'cited_answer_used')
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
                        AND event_type IN ('context_reused', 'cited_answer_used')
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
        reuse_done = used_today > 0
        return_done = streak_days >= 2

        if not capture_done:
            primary = {
                "action": "capture",
                "label": "Connect Source",
                "title": "Start memory sync",
                "detail": "Connect local AI tools or Obsidian notes so Cortex can sync memory into Review.",
            }
        elif stats["pending_captures"] > 0:
            primary = {
                "action": "review",
                "label": "Review Memory",
                "title": "Review new signals",
                "detail": f"{stats['pending_captures']} synced signal{'s' if stats['pending_captures'] != 1 else ''} need approval before they strengthen the model.",
            }
        elif not reuse_done:
            primary = {
                "action": "reuse",
                "label": "Ask Cortex",
                "title": "Use your personal model",
                "detail": "Ask a cited question or let connected AI tools read approved memory.",
            }
        else:
            primary = {
                "action": "done",
                "label": "Loop Complete",
                "title": "Loop complete today",
                "detail": "You connected, reviewed, and used cited memory. Keep Cortex nearby as work changes.",
            }

        def step(key: str, title: str, done: bool, detail: str) -> dict[str, Any]:
            status = "done" if done else "current" if primary["action"] == key else "waiting"
            return {"key": key, "title": title, "status": status, "detail": detail}

        steps = [
            step("capture", "Sync", capture_done, f"{captured_today} synced today, {stats['captures']} total."),
            step("review", "Review", review_done, f"{stats['pending_captures']} waiting for review."),
            step("reuse", "Use", reuse_done, f"{used_today} cited Cortex use{'s' if used_today != 1 else ''} today."),
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
                "reused_today": used_today,
                "used_today": used_today,
                "active_days_30d": len(active_day_set),
                "streak_days": streak_days,
            },
            "last_reused_at": last_used_at,
            "last_used_at": last_used_at,
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

    def context_pack(self, user_id: str, query: str = "", limit: int | None = None, *, sector: str | None = None) -> str:
        query = query.strip()
        sector = _normalize_sector_filter(sector) or None
        user_settings = self.settings(user_id)
        if limit is None:
            limit = int(user_settings["context_pack_limit"])
        memories = self.search(user_id, query, limit=limit, sector=sector, include_related=True) if query else self.recent(user_id, limit=limit, sector=sector)
        decisions = self._memories_by_kind(user_id, "decision", limit=5, sector=sector)
        tasks = self.open_tasks(user_id, limit=8, sector=sector)
        topics = self.list_topics(user_id, limit=8, sector=sector)
        entities = self.list_entities(user_id, limit=8, sector=sector)
        redact = bool(user_settings["redact_sensitive_context"])

        lines = [
            "# Cortex Memory View",
            "",
            f"Generated: {now_iso()}",
        ]
        if query:
            lines.append(f"Focus: {query}")
        if sector:
            lines.append(f"Sector: {sector}")
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
                "procedural": "Procedural Memory",
                "episodic": "Episodic Memory",
                "semantic": "Semantic Memory",
            }
            layer_order = ["decision", "preference", "style", "negative", "procedural", "episodic", "semantic"]
            for layer in layer_order:
                grouped = [item for item in memories if memory_layer(item.get("kind"), item.get("layer")) == layer]
                if not grouped:
                    continue
                lines.extend([f"### {layer_titles[layer]}", ""])
                for item in grouped:
                    date = item.get("captured_at") or ""
                    topics_text = ", ".join(item.get("topics") or [])
                    suffix = f" Topics: {topics_text}." if topics_text else ""
                    relationship = item.get("relationship") if isinstance(item.get("relationship"), dict) else {}
                    if relationship:
                        relation_kind = str(relationship.get("kind") or "related")
                        related_to = str(relationship.get("related_to_id") or "").strip()
                        suffix += f" Related: {relation_kind}{f' to {related_to}' if related_to else ''}."
                    citation = self._memory_citation(item)
                    content = self._shared_text(item["content"], redact_sensitive=redact)
                    lines.append(f"- [{item['id']}] ({item['kind']}, {item['source']}, {date}) Source: {citation}. {content}{suffix}")
                lines.append("")
        else:
            lines.append("- No active memories matched this focus.")
        lines.extend(["", "## Decisions", ""])
        if decisions:
            for item in decisions:
                content = self._shared_text(item["content"], redact_sensitive=redact)
                lines.append(f"- [{item['id']}] Source: {self._memory_citation(item)}. {content}")
        else:
            lines.append("- No active decisions yet.")
        lines.extend(["", "## Open Loops", ""])
        if tasks:
            for task in tasks:
                lines.append(f"- [{task['id']}] ({task['kind']}) {self._shared_text(task['content'], redact_sensitive=redact)}")
        else:
            lines.append("- No open tasks or questions.")
        lines.extend(["", "## Useful Topics", ""])
        lines.append(", ".join(f"#{item['topic']}" for item in topics) if topics else "No active topics yet.")
        lines.extend(["", "## Useful Entities", ""])
        lines.append(", ".join(f"{item['name']} ({item['kind']})" for item in entities) if entities else "No active entities yet.")
        return "\n".join(lines)

    def personal_profile(self, user_id: str, query: str = "", limit: int = 6, include_pending: bool = False, *, sector: str | None = None) -> dict[str, Any]:
        query = query.strip()
        sector = _normalize_sector_filter(sector) or None
        limit = max(1, min(20, int(limit)))
        user_settings = self.settings(user_id)
        profile_settings = {**user_settings}
        if not include_pending:
            profile_settings["allow_pending_in_context"] = False
        redact = bool(user_settings["redact_sensitive_context"])
        stats = self._profile_stats(user_id, profile_settings, sector=sector)
        layer_counts = {item["layer"]: int(item["count"]) for item in stats["by_layer"]}
        layer_order = [
            ("preference", "Preference memory", "Durable likes, dislikes, defaults, and working preferences."),
            ("negative", "Negative memory", "Rejected approaches, disliked outputs, and constraints to avoid."),
            ("style", "Style memory", "Writing voice, phrasing, structure, and communication patterns."),
            ("decision", "Decision memory", "Past choices, reasons, constraints, and settled direction."),
            ("procedural", "Procedural memory", "How the user performs recurring workflows, checks, and operational steps."),
            ("episodic", "Episodic memory", "Specific conversations, events, project moments, and recent context."),
            ("semantic", "Semantic memory", "Facts about people, projects, goals, systems, and durable context."),
        ]
        sections: list[dict[str, Any]] = []
        for layer, title, description in layer_order:
            memories = self._memories_by_layer(user_id, layer, limit=limit, include_pending=include_pending, sector=sector)
            sections.append(
                {
                    "layer": layer,
                    "title": title,
                    "description": description,
                    "count": layer_counts.get(layer, 0),
                    "items": [self._profile_memory_item(item, redact=redact) for item in memories],
                }
            )

        focus_memories = self.search(user_id, query, limit=limit, sector=sector, include_related=True) if query else []
        focus_memories = self._approved_profile_memories(user_id, focus_memories, include_pending=include_pending)
        open_loops = self.open_tasks(user_id, limit=limit, include_pending=include_pending, sector=sector)
        topics = self.list_topics(user_id, limit=8, include_pending=include_pending, sector=sector)
        entities = self.list_entities(user_id, limit=8, include_pending=include_pending, sector=sector)
        sources = self._source_freshness(user_id, limit=8, user_settings=profile_settings)
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
            "sector": sector,
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
                    "content": self._shared_text(task["content"], redact_sensitive=redact),
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

    def _profile_stats(self, user_id: str, user_settings: dict[str, Any], *, sector: str | None = None) -> dict[str, Any]:
        with connect(self.db_path) as conn:
            memory_filters, memory_params = self._memory_filters(user_id, user_settings, alias="m", sector=sector)
            memory_where = " AND ".join(memory_filters)
            task_filters, task_params = self._task_filters(user_id, user_settings, alias="t", capture_alias="c")
            task_where = " AND ".join(task_filters)
            memory_totals = conn.execute(
                f"""
                SELECT
                  COUNT(*) AS memories,
                  SUM(CASE WHEN m.kind = 'decision' THEN 1 ELSE 0 END) AS decisions
                FROM memories m
                WHERE {memory_where}
                """,
                memory_params,
            ).fetchone()
            by_layer = [
                {"layer": row["layer"], "count": row["count"]}
                for row in conn.execute(
                    f"""
                    SELECT m.layer, COUNT(*) AS count
                    FROM memories m
                    WHERE {memory_where}
                    GROUP BY m.layer
                    ORDER BY count DESC
                    """,
                    memory_params,
                ).fetchall()
            ]
            task_count = 0
            if not sector:
                task_count = conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM tasks t
                    LEFT JOIN captures c ON c.id = t.capture_id AND c.user_id = t.user_id
                    WHERE {task_where}
                    """,
                    task_params,
                ).fetchone()[0]
            entity_count = conn.execute(
                f"""
                SELECT COUNT(DISTINCT e.id)
                FROM entities e
                JOIN memory_entities me ON me.entity_id = e.id AND me.user_id = e.user_id
                JOIN memories m ON m.id = me.memory_id AND m.user_id = me.user_id
                WHERE e.user_id = ? AND {memory_where}
                """,
                [user_id, *memory_params],
            ).fetchone()[0]
            source_policies = _normalize_source_policies(user_settings.get("source_policies"))
            excluded_sources = _source_policy_sources(source_policies, lambda policy: not policy.get("allow_ai_context", True))
            capture_filters = ["user_id = ?", "review_status = 'pending'"]
            capture_params: list[Any] = [user_id]
            if excluded_sources:
                capture_filters.append(f"source NOT IN ({','.join('?' for _ in excluded_sources)})")
                capture_params.extend(excluded_sources)
            pending_count = conn.execute(
                f"SELECT COUNT(*) FROM captures WHERE {' AND '.join(capture_filters)}",
                capture_params,
            ).fetchone()[0]
        return {
            "memories": int(memory_totals["memories"] or 0),
            "decisions": int(memory_totals["decisions"] or 0),
            "tasks": int(task_count or 0),
            "entities": int(entity_count or 0),
            "pending_captures": int(pending_count or 0),
            "by_layer": by_layer,
        }

    def agent_adaptation(self, user_id: str, query: str = "", target: str = "assistant", limit: int = 8, include_pending: bool = False, *, sector: str | None = None) -> dict[str, Any]:
        target = (target or "assistant").strip()[:80] or "assistant"
        profile = self.personal_profile(user_id, query=query, limit=limit, include_pending=include_pending, sector=sector)
        layer_priority = ["preference", "negative", "style", "decision", "procedural", "episodic", "semantic"]
        section_by_layer = {section["layer"]: section for section in profile["sections"]}
        rule_templates = {
            "preference": "Honor this user preference",
            "negative": "Avoid this rejected or disliked pattern",
            "style": "Match this communication style signal",
            "decision": "Respect this prior decision and its constraints",
            "procedural": "Follow this known workflow when relevant",
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
        lines.extend(["", "## Follow-ups", ""])
        if artifact["open_loops"]:
            for task in artifact["open_loops"][:8]:
                lines.append(f"- [{task['id']}] ({task['kind']}) {task['content']}")
        else:
            lines.append("- No active follow-ups.")
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
        lines.extend(["", "## Follow-ups", ""])
        if profile["open_loops"]:
            for task in profile["open_loops"]:
                lines.append(f"- [{task['id']}] ({task['kind']}) {task['content']}")
        else:
            lines.append("- No active follow-ups.")
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
        relationship = item.get("relationship") if isinstance(item.get("relationship"), dict) else {}
        relation_text = ""
        if relationship:
            relation_kind = str(relationship.get("kind") or "related")
            related_to = str(relationship.get("related_to_id") or "").strip()
            relation_text = f" Related: {relation_kind}{f' to {related_to}' if related_to else ''}."
        return f"- [{item['id']}] ({item['kind']}, {item['source']}, {date}) Source: {self._memory_citation(item)}. {item['content']}{topics_text}{relation_text}"

    def _memory_citation(self, item: dict[str, Any], *, redact_paths: bool = True) -> str:
        source_url = str(item.get("source_url") or "").strip()
        if source_url:
            return self._safe_source_locator(source_url, force_local=True) if redact_paths else source_url
        return str(item.get("source") or "unknown source")

    def _profile_memory_item(self, item: dict[str, Any], *, redact: bool) -> dict[str, Any]:
        profile_item = {
            "id": item["id"],
            "kind": item["kind"],
            "layer": item["layer"],
            "content": self._shared_text(item["content"], redact_sensitive=redact),
            "summary": self._shared_text(item.get("summary") or "", redact_sensitive=redact),
            "source": item["source"],
            "source_url": self._safe_source_locator(item.get("source_url"), force_local=True),
            "sector": item.get("sector") or "",
            "source_type": item.get("source_type") or "",
            "captured_at": item["captured_at"],
            "occurred_at": item.get("occurred_at"),
            "valid_from": item.get("valid_from"),
            "valid_to": item.get("valid_to"),
            "topics": item.get("topics") or [],
            "entity_ids": item.get("entity_ids") or [],
        }
        relationship = item.get("relationship") if isinstance(item.get("relationship"), dict) else {}
        if relationship:
            profile_item["relationship"] = {
                "kind": relationship.get("kind"),
                "weight": relationship.get("weight"),
                "related_to_id": relationship.get("related_to_id"),
            }
        return profile_item

    def _source_freshness(self, user_id: str, limit: int = 8, *, user_settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        source_policies = _normalize_source_policies((user_settings or {}).get("source_policies"))
        excluded_sources = _source_policy_sources(source_policies, lambda policy: not policy.get("allow_ai_context", True))
        filters = ["c.user_id = ?", "COALESCE(json_extract(sa.policy_json, '$.allow_ai_context'), 1) != 0"]
        params: list[Any] = [user_id]
        if user_settings and not user_settings.get("allow_pending_in_context", True):
            filters.append("(c.review_status = 'approved' OR COALESCE(json_extract(sa.policy_json, '$.review_required'), 1) = 0)")
        if excluded_sources:
            filters.append(f"c.source NOT IN ({','.join('?' for _ in excluded_sources)})")
            params.extend(excluded_sources)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT
                  c.source,
                  COUNT(*) AS total,
                  SUM(CASE WHEN c.review_status = 'approved' OR COALESCE(json_extract(sa.policy_json, '$.review_required'), 1) = 0 THEN 1 ELSE 0 END) AS approved,
                  SUM(CASE WHEN c.review_status = 'pending' AND COALESCE(json_extract(sa.policy_json, '$.review_required'), 1) != 0 THEN 1 ELSE 0 END) AS pending,
                  MAX(c.captured_at) AS last_seen
                FROM captures c
                LEFT JOIN source_accounts sa ON sa.id = c.source_account_id AND sa.user_id = c.user_id
                WHERE {' AND '.join(filters)}
                GROUP BY c.source
                ORDER BY total DESC, last_seen DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        return [dict(row) for row in rows]

    def _approved_profile_memories(self, user_id: str, memories: list[dict[str, Any]], *, include_pending: bool) -> list[dict[str, Any]]:
        if include_pending or not memories:
            return memories
        ids = [item["id"] for item in memories]
        placeholders = ",".join("?" for _ in ids)
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            profile_settings = {**user_settings, "allow_pending_in_context": False}
            filters, params = self._memory_filters(user_id, profile_settings, alias="m")
            filters.append(f"m.id IN ({placeholders})")
            rows = conn.execute(
                f"""
                SELECT m.id
                FROM memories m
                WHERE {' AND '.join(filters)}
                """,
                [*params, *ids],
            ).fetchall()
        approved_ids = {row["id"] for row in rows}
        return [item for item in memories if item["id"] in approved_ids]

    def graph(self, user_id: str, limit: int = 150) -> dict[str, Any]:
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            source_policies = _normalize_source_policies(user_settings.get("source_policies"))
            excluded_sources = _source_policy_sources(source_policies, lambda policy: not policy.get("allow_ai_context", True))
            capture_filters = ["c.user_id = ?", "COALESCE(json_extract(sa.policy_json, '$.allow_ai_context'), 1) != 0"]
            capture_params: list[Any] = [user_id]
            if not user_settings["allow_pending_in_context"]:
                capture_filters.append("(c.review_status = 'approved' OR COALESCE(json_extract(sa.policy_json, '$.review_required'), 1) = 0)")
            else:
                capture_filters.append("c.review_status IN ('pending', 'approved')")
            if excluded_sources:
                capture_filters.append(f"c.source NOT IN ({','.join('?' for _ in excluded_sources)})")
                capture_params.extend(excluded_sources)
            for row in conn.execute(
                f"""
                SELECT c.id, c.source, c.title, c.summary, c.captured_at, c.review_status
                FROM captures c
                LEFT JOIN source_accounts sa ON sa.id = c.source_account_id AND sa.user_id = c.user_id
                WHERE {' AND '.join(capture_filters)}
                ORDER BY c.captured_at DESC
                LIMIT ?
                """,
                [*capture_params, limit // 3],
            ).fetchall():
                nodes[row["id"]] = {
                    "id": row["id"],
                    "type": "source",
                    "label": row["title"] or row["source"],
                    "detail": row["summary"] or "",
                    "status": row["review_status"],
                    "created_at": row["captured_at"],
                }
            memory_filters, memory_params = self._memory_filters(user_id, user_settings, alias="m")
            for row in conn.execute(
                f"""
                SELECT m.id, m.kind, m.content, m.importance, m.captured_at
                FROM memories m
                WHERE {' AND '.join(memory_filters)}
                ORDER BY m.captured_at DESC
                LIMIT ?
                """,
                [*memory_params, limit],
            ).fetchall():
                nodes[row["id"]] = {"id": row["id"], "type": row["kind"], "label": row["content"][:72], "importance": row["importance"], "created_at": row["captured_at"]}
            task_filters, task_params = self._task_filters(user_id, user_settings, alias="t", capture_alias="c")
            task_where = " AND ".join(task_filters)
            memory_where = " AND ".join(memory_filters)
            for row in conn.execute(
                f"""
                SELECT DISTINCT e.id, e.kind, e.name, e.context, e.last_seen
                FROM entities e
                WHERE e.user_id = ?
                  AND (
                    EXISTS (
                      SELECT 1
                      FROM memory_entities me
                      JOIN memories m ON m.id = me.memory_id AND m.user_id = me.user_id
                      WHERE me.user_id = e.user_id AND me.entity_id = e.id AND {memory_where}
                    )
                    OR EXISTS (
                      SELECT 1
                      FROM task_entities te
                      JOIN tasks t ON t.id = te.task_id AND t.user_id = te.user_id
                      LEFT JOIN captures c ON c.id = t.capture_id AND c.user_id = t.user_id
                      WHERE te.user_id = e.user_id AND te.entity_id = e.id AND {task_where}
                    )
                  )
                ORDER BY e.last_seen DESC
                LIMIT ?
                """,
                [user_id, *memory_params, *task_params, limit],
            ).fetchall():
                nodes[row["id"]] = {"id": row["id"], "type": row["kind"], "label": row["name"], "detail": row["context"] or ""}
            for row in conn.execute(
                f"""
                SELECT t.id, t.kind, t.content, t.status
                FROM tasks t
                LEFT JOIN captures c ON c.id = t.capture_id AND c.user_id = t.user_id
                WHERE {task_where}
                ORDER BY t.captured_at DESC
                LIMIT ?
                """,
                [*task_params, limit // 2],
            ).fetchall():
                nodes[row["id"]] = {"id": row["id"], "type": row["kind"], "label": row["content"][:72], "status": row["status"]}
            for row in conn.execute(
                """
                SELECT ge.*
                FROM graph_edges ge
                WHERE ge.user_id = ?
                ORDER BY ge.created_at DESC
                LIMIT ?
                """,
                (user_id, limit * 2),
            ).fetchall():
                evidence_id = row["evidence_id"]
                if row["source_id"] in nodes and row["target_id"] in nodes and (not evidence_id or evidence_id in nodes):
                    edges.append(dict(row))
        return self._shared_payload(
            {"nodes": list(nodes.values()), "edges": edges},
            redact_sensitive=bool(user_settings["redact_sensitive_context"]),
        )

    def export_json(self, user_id: str) -> dict[str, Any]:
        user_settings = self.settings(user_id)
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
        payload = self._filter_export_by_source_policy(payload, user_settings)
        return self._shared_payload(payload, redact_sensitive=bool(user_settings["redact_sensitive_context"]))

    def _filter_export_by_source_policy(self, payload: dict[str, Any], user_settings: dict[str, Any]) -> dict[str, Any]:
        source_policies = _normalize_source_policies(user_settings.get("source_policies"))
        excluded_sources = set(_source_policy_sources(source_policies, lambda policy: not policy.get("allow_ai_context", True)))
        blocked_account_capture_ids: set[str] = set()
        user_id = str(payload.get("user_id") or "").strip()
        if user_id:
            with connect(self.db_path) as conn:
                blocked_account_capture_ids = {
                    row["id"]
                    for row in conn.execute(
                        """
                        SELECT c.id
                        FROM captures c
                        JOIN source_accounts sa ON sa.id = c.source_account_id AND sa.user_id = c.user_id
                        WHERE c.user_id = ?
                          AND COALESCE(json_extract(sa.policy_json, '$.allow_ai_context'), 1) = 0
                        """,
                        (user_id,),
                    ).fetchall()
                }
        if not excluded_sources and not blocked_account_capture_ids:
            return payload

        captures = list(payload.get("captures") or [])
        all_capture_sources = {capture.get("id"): capture.get("source") for capture in captures}

        def source_blocked(source: Any) -> bool:
            return _normalize_source_key(source) in excluded_sources

        def capture_blocked(capture_id: Any, source: Any) -> bool:
            return str(capture_id or "") in blocked_account_capture_ids or source_blocked(source)

        filtered_captures = [capture for capture in captures if not capture_blocked(capture.get("id"), capture.get("source"))]
        allowed_capture_ids = {capture.get("id") for capture in filtered_captures if capture.get("id")}

        def memory_allowed(memory: dict[str, Any]) -> bool:
            if source_blocked(memory.get("source")):
                return False
            capture_id = memory.get("capture_id")
            return not capture_id or not capture_blocked(capture_id, all_capture_sources.get(capture_id))

        filtered_memories = [memory for memory in payload.get("memories") or [] if memory_allowed(memory)]
        allowed_memory_ids = {memory.get("id") for memory in filtered_memories if memory.get("id")}

        def task_allowed(task: dict[str, Any]) -> bool:
            capture_id = task.get("capture_id")
            return not capture_id or not capture_blocked(capture_id, all_capture_sources.get(capture_id))

        filtered_tasks = [task for task in payload.get("tasks") or [] if task_allowed(task)]
        allowed_task_ids = {task.get("id") for task in filtered_tasks if task.get("id")}

        allowed_entity_ids = {
            entity_id
            for item in [*filtered_memories, *filtered_tasks]
            for entity_id in item.get("entity_ids") or []
        }
        filtered_entities = [entity for entity in payload.get("entities") or [] if entity.get("id") in allowed_entity_ids]
        allowed_node_ids = allowed_capture_ids | allowed_memory_ids | allowed_task_ids | allowed_entity_ids
        filtered_edges = [
            edge
            for edge in payload.get("edges") or []
            if edge.get("source_id") in allowed_node_ids
            and edge.get("target_id") in allowed_node_ids
            and (not edge.get("evidence_id") or edge.get("evidence_id") in allowed_node_ids)
        ]

        filtered_imports: list[dict[str, Any]] = []
        for item in payload.get("imports") or []:
            filtered = self._filter_export_import_by_source_policy(item, excluded_sources, allowed_capture_ids)
            if filtered is not None:
                filtered_imports.append(filtered)

        return {
            **payload,
            "stats": self._export_stats_from_payload(filtered_captures, filtered_memories, filtered_tasks, filtered_entities, filtered_edges),
            "imports": filtered_imports,
            "captures": filtered_captures,
            "memories": filtered_memories,
            "tasks": filtered_tasks,
            "entities": filtered_entities,
            "edges": filtered_edges,
        }

    def _export_stats_from_payload(
        self,
        captures: list[dict[str, Any]],
        memories: list[dict[str, Any]],
        tasks: list[dict[str, Any]],
        entities: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> dict[str, Any]:
        active_memories = [memory for memory in memories if memory.get("status") == "active"]
        open_tasks = [task for task in tasks if task.get("status") == "open"]

        def counts_by(items: list[dict[str, Any]], key: str, label: str) -> list[dict[str, Any]]:
            counts: dict[str, int] = {}
            for item in items:
                value = str(item.get(key) or "").strip()
                if value:
                    counts[value] = counts.get(value, 0) + 1
            return [
                {label: value, "count": count}
                for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            ]

        topic_counts: dict[str, int] = {}
        entity_counts: dict[str, int] = {}
        for memory in active_memories:
            for topic in memory.get("topics") or []:
                topic = str(topic or "").strip()
                if topic:
                    topic_counts[topic] = topic_counts.get(topic, 0) + 1
            for entity_id in memory.get("entity_ids") or []:
                entity_id = str(entity_id or "").strip()
                if entity_id:
                    entity_counts[entity_id] = entity_counts.get(entity_id, 0) + 1

        entities_by_id = {entity.get("id"): entity for entity in entities}
        top_entities = []
        for entity_id, count in sorted(entity_counts.items(), key=lambda item: (-item[1], item[0]))[:12]:
            entity = entities_by_id.get(entity_id)
            if not entity:
                continue
            top_entities.append(
                {
                    "id": entity.get("id"),
                    "name": entity.get("name"),
                    "kind": entity.get("kind"),
                    "count": count,
                }
            )

        return {
            "captures": len(captures),
            "pending_captures": sum(1 for capture in captures if capture.get("review_status") == "pending"),
            "memories": len(active_memories),
            "decisions": sum(1 for memory in active_memories if memory.get("kind") == "decision"),
            "tasks": len(open_tasks),
            "entities": len(entities),
            "edges": len(edges),
            "by_kind": counts_by(active_memories, "kind", "kind"),
            "by_layer": counts_by(active_memories, "layer", "layer"),
            "top_topics": [
                {"topic": topic, "count": count}
                for topic, count in sorted(topic_counts.items(), key=lambda item: (-item[1], item[0]))[:12]
            ],
            "top_entities": top_entities,
        }

    def _filter_export_import_by_source_policy(
        self,
        item: dict[str, Any],
        excluded_sources: set[str],
        allowed_capture_ids: set[str],
    ) -> dict[str, Any] | None:
        def source_id(entry: Any) -> str:
            if isinstance(entry, dict):
                return _normalize_source_key(entry.get("source") or "")
            return _normalize_source_key(entry or "")

        sources = list(item.get("sources") or [])
        filtered_sources = [source for source in sources if source_id(source) not in excluded_sources]
        capture_ids = [capture_id for capture_id in item.get("capture_ids") or [] if capture_id in allowed_capture_ids]
        if sources and not filtered_sources:
            return None
        if item.get("capture_ids") and not capture_ids and any(source_id(source) in excluded_sources for source in sources):
            return None
        filtered = {**item, "sources": filtered_sources, "capture_ids": capture_ids}
        if len(filtered_sources) != len(sources):
            filtered["paths"] = []
            filtered["errors"] = [error for error in item.get("errors") or [] if source_id(error) not in excluded_sources]
        return filtered

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
                  (SELECT COUNT(*) FROM task_topics tt LEFT JOIN tasks t ON t.id = tt.task_id WHERE t.id IS NULL) +
                  (
                    SELECT COUNT(*)
                    FROM memory_relations mr
                    LEFT JOIN memories source_memory
                      ON source_memory.id = mr.source_memory_id
                     AND source_memory.user_id = mr.user_id
                     AND source_memory.status = 'active'
                    LEFT JOIN memories target_memory
                      ON target_memory.id = mr.target_memory_id
                     AND target_memory.user_id = mr.user_id
                     AND target_memory.status = 'active'
                    WHERE source_memory.id IS NULL OR target_memory.id IS NULL
                  )
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

    def runtime_storage_status(self) -> dict[str, Any]:
        with connect(self.db_path) as conn:
            vector_status = sqlite_vec_status(conn)
        vector_available = bool(vector_status.get("available"))
        return {
            "database_backend": "sqlite",
            "database_live": self.db_path.exists(),
            "vector_backend": "sqlite-vec" if vector_available else "none",
            "vector_live": vector_available,
            "vector_available": vector_available,
            "vector_reason": vector_status.get("reason"),
            "embedding": embedding_status(),
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
        source_readiness = self.source_readiness_report(user_id)
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
                "sqlite_runtime": SQLITE_RUNTIME,
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
            "source_readiness": self._support_safe_payload(source_readiness),
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
                    "remove_memory_relation_orphans",
                    """
                    DELETE FROM memory_relations
                    WHERE user_id = ?
                      AND (
                        source_memory_id NOT IN (SELECT id FROM memories WHERE user_id = ? AND status = 'active')
                        OR target_memory_id NOT IN (SELECT id FROM memories WHERE user_id = ? AND status = 'active')
                      )
                    """,
                    (user_id, user_id, user_id),
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
            conn.execute("DELETE FROM memory_relations WHERE user_id = ?", (user_id,))
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
                    (id, user_id, import_id, source, source_url, source_account_id, external_id, title, raw_text, raw_hash, summary, review_status, approved_at, archived_at, captured_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        capture["id"],
                        user_id,
                        capture.get("import_id"),
                        capture.get("source", "vault"),
                        capture.get("source_url"),
                        capture.get("source_account_id"),
                        capture.get("external_id"),
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
                provenance = memory.get("provenance") if isinstance(memory.get("provenance"), dict) else {}
                conn.execute(
                    """
                    INSERT OR REPLACE INTO memories
                    (id, capture_id, user_id, kind, layer, content, summary, source, source_url, confidence, importance, status, sector, source_type, provenance_json, topics_json, entity_ids_json, occurred_at, valid_from, valid_to, superseded_by, captured_at, updated_at, raw_excerpt)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        memory.get("sector", ""),
                        memory.get("source_type", ""),
                        json.dumps(provenance),
                        json.dumps(topics),
                        json.dumps(entity_ids),
                        memory.get("occurred_at"),
                        memory.get("valid_from"),
                        memory.get("valid_to"),
                        memory.get("superseded_by"),
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
            if memory.get("source_url"):
                lines.append(f"Source: {memory['source_url']}")
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
                    "credentials",
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
            safe_metadata = self._support_safe_payload(metadata)
            events.append(
                {
                    "id": row["id"],
                    "object_id": row["object_id"],
                    "object_type": row["object_type"],
                    "event_type": row["event_type"],
                    "metadata": safe_metadata,
                    "metadata_text": self._metadata_summary(safe_metadata),
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
        return self._shared_payload(value, redact_sensitive=bool(self.settings(user_id)["redact_sensitive_context"]))

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

    def _reap_expired_jobs(self, conn: sqlite3.Connection, user_id: str, timestamp: str) -> None:
        """Recover jobs orphaned in 'running' by a dead worker (expired lease). Jobs with
        attempts left are requeued for reclaim; jobs out of attempts fail terminally so they
        can't loop forever. A live worker holds a future lease, so healthy jobs are untouched."""
        conn.execute(
            """
            UPDATE memory_jobs
            SET status = 'failed',
                locked_by = NULL,
                locked_until = NULL,
                updated_at = ?,
                completed_at = ?,
                last_error = COALESCE(NULLIF(last_error, ''), 'worker lease expired')
            WHERE user_id = ?
              AND status = 'running'
              AND locked_until IS NOT NULL
              AND locked_until < ?
              AND attempts >= max_attempts
            """,
            (timestamp, timestamp, user_id, timestamp),
        )
        conn.execute(
            """
            UPDATE memory_jobs
            SET status = 'queued',
                locked_by = NULL,
                locked_until = NULL,
                updated_at = ?,
                run_at = ?
            WHERE user_id = ?
              AND status = 'running'
              AND locked_until IS NOT NULL
              AND locked_until < ?
              AND attempts < max_attempts
            """,
            (timestamp, timestamp, user_id, timestamp),
        )

    def _claim_next_job(self, user_id: str, worker_id: str, *, job_type: str | None = None) -> dict[str, Any] | None:
        timestamp = now_iso()
        parsed_now = _parse_iso_timestamp(timestamp) or datetime.now(timezone.utc)
        lease_until = (parsed_now + timedelta(seconds=MEMORY_JOB_LEASE_SECONDS)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        normalized_job_type = str(job_type or "").strip()
        job_type_filter = "AND job_type = ?" if normalized_job_type else ""
        params: list[Any] = [user_id, timestamp]
        if normalized_job_type:
            params.append(normalized_job_type)
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._reap_expired_jobs(conn, user_id, timestamp)
            row = conn.execute(
                f"""
                SELECT *
                FROM memory_jobs
                WHERE user_id = ?
                  AND status = 'queued'
                  AND run_at <= ?
                  {job_type_filter}
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
                """,
                params,
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
                (worker_id, lease_until, timestamp, row["id"]),
            )
            claimed = conn.execute("SELECT * FROM memory_jobs WHERE id = ?", (row["id"],)).fetchone()
        return self._job_from_row(claimed)

    def _run_job(self, job: dict[str, Any], worker_id: str) -> dict[str, Any]:
        try:
            if job["job_type"] == "extract_capture":
                result = self._process_extract_capture_job(job)
            elif job["job_type"] == "embed_memory":
                result = self._process_embed_memory_job(job)
            elif job["job_type"] == "source_account_sync":
                result = self._process_source_account_sync_job(job)
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

    def _process_source_account_sync_job(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload") or {}
        user_id = job["user_id"]
        account_id = str(payload.get("source_account_id") or job["object_id"] or "").strip()
        if not account_id:
            raise ValueError("source account sync job missing source_account_id")
        account = self._source_account_by_id(user_id, account_id)
        if not account:
            return {
                "source_account_id": account_id,
                "skipped": True,
                "reason": "source_account_missing_or_deleted",
                "completed_at": now_iso(),
            }
        if account.get("disconnected_at"):
            return {
                "source_account_id": account_id,
                "source": account.get("source"),
                "skipped": True,
                "reason": "source_account_disconnected",
                "completed_at": now_iso(),
            }
        processing = str(payload.get("processing") or "async").strip().lower()
        if processing not in {"sync", "async"}:
            raise ValueError("processing must be sync or async")
        cursor_name = str(payload.get("cursor_name") or "default").strip() or "default"
        max_records = _bounded_int(payload.get("max_records"), minimum=1, maximum=500) or 200
        metadata = account.get("metadata") if isinstance(account.get("metadata"), dict) else {}
        source = _normalize_source_key(str(account.get("source") or ""))
        if cursor_name == "default":
            cursor_name = _default_source_sync_cursor_name(source)
        cursor_record = self._latest_sync_cursor(user_id, account_id, cursor_name) or {}
        cursor_state = cursor_record.get("state") if isinstance(cursor_record.get("state"), dict) else {}
        cursor_value = str(cursor_record.get("cursor_value") or "").strip() or None
        high_water_mark = str(cursor_record.get("high_water_mark") or "").strip() or None

        def cursor_state_value(name: str) -> str | None:
            return str(cursor_state.get(name) or "").strip() or None

        credential_payload = self._source_credential_payload_with_fresh_oauth_token(
            user_id,
            account,
            self._read_source_account_credential_payload(user_id, account_id),
        )
        account_label = account.get("account_label")
        account_identifier = account.get("account_identifier")
        if source == "obsidian":
            vault_path = str(metadata.get("vault_path") or "").strip()
            if not vault_path:
                raise ValueError("obsidian source account is missing vault_path")
            result = self.sync_obsidian_vault(
                user_id,
                vault_path=vault_path,
                source_account_id=account_id,
                account_label=account.get("account_label"),
                account_identifier=account.get("account_identifier"),
                processing=processing,
                max_records=max_records,
                cursor_name=cursor_name,
            )
        elif source == "gmail":
            access_token = str(credential_payload.get("access_token") or "").strip()
            if not access_token:
                raise ValueError("gmail source account is missing stored access token")
            next_page_token = cursor_state_value("next_page_token")
            label_ids = credential_payload.get("label_ids")
            if not isinstance(label_ids, list):
                label_ids = metadata.get("label_ids") if isinstance(metadata.get("label_ids"), list) else []
            result = self.sync_gmail_account(
                user_id,
                access_token=access_token,
                source_account_id=account_id,
                account_label=account_label,
                account_identifier=account_identifier,
                query=str(credential_payload.get("query") or metadata.get("query") or "").strip() or None,
                label_ids=[str(label).strip() for label in label_ids if str(label).strip()],
                since=None if next_page_token else (high_water_mark or cursor_value),
                page_token=next_page_token,
                processing=processing,
                max_records=min(max_records, 200),
                cursor_name=cursor_name,
                include_body=bool(credential_payload.get("include_body", metadata.get("content_sync_enabled", True))),
                api_base_url=str(credential_payload.get("api_base_url") or metadata.get("api_base_url") or "https://gmail.googleapis.com/gmail/v1"),
            )
        elif source == "outlook":
            access_token = str(credential_payload.get("access_token") or "").strip()
            if not access_token:
                raise ValueError("outlook source account is missing stored access token")
            next_page_token = cursor_state_value("next_page_token")
            result = self.sync_outlook_account(
                user_id,
                access_token=access_token,
                source_account_id=account_id,
                account_label=account_label,
                account_identifier=account_identifier,
                query=str(credential_payload.get("query") or metadata.get("query") or "").strip() or None,
                since=None if next_page_token else (high_water_mark or cursor_value),
                page_token=next_page_token,
                processing=processing,
                max_records=min(max_records, 200),
                cursor_name=cursor_name,
                include_body=bool(credential_payload.get("include_body", metadata.get("content_sync_enabled", True))),
                api_base_url=str(credential_payload.get("api_base_url") or metadata.get("api_base_url") or "https://graph.microsoft.com/v1.0"),
            )
        elif source == "google-drive":
            access_token = str(credential_payload.get("access_token") or "").strip()
            if not access_token:
                raise ValueError("google-drive source account is missing stored access token")
            next_page_token = cursor_state_value("next_page_token")
            mime_types = credential_payload.get("mime_types")
            if not isinstance(mime_types, list):
                mime_types = metadata.get("mime_types") if isinstance(metadata.get("mime_types"), list) else []
            result = self.sync_google_drive_account(
                user_id,
                access_token=access_token,
                source_account_id=account_id,
                account_label=account_label,
                account_identifier=account_identifier,
                query=str(credential_payload.get("query") or metadata.get("query") or "").strip() or None,
                mime_types=[str(mime).strip() for mime in mime_types if str(mime).strip()],
                since=None if next_page_token else (high_water_mark or cursor_value),
                page_token=next_page_token,
                processing=processing,
                max_records=min(max_records, 200),
                cursor_name=cursor_name,
                include_content=bool(credential_payload.get("include_content", metadata.get("content_sync_enabled", True))),
                api_base_url=str(credential_payload.get("api_base_url") or metadata.get("api_base_url") or "https://www.googleapis.com/drive/v3"),
            )
        elif source == "github":
            token = str(credential_payload.get("token") or "").strip()
            repositories = credential_payload.get("repositories")
            if not isinstance(repositories, list):
                repositories = metadata.get("repositories") if isinstance(metadata.get("repositories"), list) else []
            repositories = [str(repo).strip() for repo in repositories if str(repo).strip()]
            if not token or not repositories:
                raise ValueError("github source account is missing stored token or repositories")
            scheduled_max_comments_per_item = _bounded_int(
                credential_payload.get("max_comments_per_item", metadata.get("max_comments_per_item", 10)),
                minimum=0,
                maximum=50,
            )
            if scheduled_max_comments_per_item is None:
                scheduled_max_comments_per_item = 10
            result = self.sync_github_account(
                user_id,
                token=token,
                repositories=repositories,
                source_account_id=account_id,
                account_label=account_label,
                account_identifier=account_identifier,
                since=cursor_value or high_water_mark or account.get("last_sync_at"),
                processing=processing,
                max_records=max_records,
                include_comments=bool(credential_payload.get("include_comments", metadata.get("include_comments", True))),
                max_comments_per_item=scheduled_max_comments_per_item,
                cursor_name=cursor_name,
                api_base_url=str(credential_payload.get("api_base_url") or metadata.get("api_base_url") or "https://api.github.com"),
            )
        elif source == "slack":
            token = str(credential_payload.get("token") or "").strip()
            channels = credential_payload.get("channels")
            if not isinstance(channels, list):
                channels = []
                for item in metadata.get("channels") if isinstance(metadata.get("channels"), list) else []:
                    if not isinstance(item, dict):
                        continue
                    channel_id = str(item.get("id") or "").strip()
                    channel_name = str(item.get("name") or "").strip()
                    if channel_id and channel_name:
                        channels.append(f"{channel_id}|{channel_name}")
                    elif channel_id:
                        channels.append(channel_id)
            channels = [str(channel).strip() for channel in channels if str(channel).strip()]
            if not token or not channels:
                raise ValueError("slack source account is missing stored token or channels")
            result = self.sync_slack_account(
                user_id,
                token=token,
                channels=channels,
                source_account_id=account_id,
                account_label=account_label,
                account_identifier=account_identifier,
                since=high_water_mark or cursor_value,
                page_cursors=cursor_state.get("next_cursors") if isinstance(cursor_state.get("next_cursors"), dict) else None,
                processing=processing,
                max_records=min(max_records, 200),
                cursor_name=cursor_name,
                workspace_url=str(credential_payload.get("workspace_url") or "").strip() or None,
                api_base_url=str(credential_payload.get("api_base_url") or metadata.get("api_base_url") or "https://slack.com/api"),
            )
        elif source == "readwise":
            token = str(credential_payload.get("token") or "").strip()
            if not token:
                raise ValueError("readwise source account is missing stored token")
            next_page_cursor = cursor_state_value("next_page_cursor")
            result = self.sync_readwise_account(
                user_id,
                token=token,
                source_account_id=account_id,
                account_label=account_label,
                account_identifier=account_identifier,
                since=None if next_page_cursor else (high_water_mark or cursor_value),
                page_cursor=next_page_cursor,
                processing=processing,
                max_records=max_records,
                cursor_name=cursor_name,
                api_base_url=str(credential_payload.get("api_base_url") or metadata.get("api_base_url") or "https://readwise.io/api/v2"),
            )
        elif source == "calendar":
            ics_path = str(credential_payload.get("ics_path") or "").strip() or None
            feed_url = str(credential_payload.get("feed_url") or "").strip() or None
            if bool(ics_path) == bool(feed_url):
                raise ValueError("calendar source account is missing one stored calendar source")
            result = self.sync_calendar_account(
                user_id,
                ics_path=ics_path,
                feed_url=feed_url,
                source_account_id=account_id,
                account_label=account_label,
                account_identifier=account_identifier,
                since=high_water_mark or cursor_value,
                processing=processing,
                max_records=max_records,
                cursor_name=cursor_name,
            )
        elif source == "raindrop":
            token = str(credential_payload.get("token") or "").strip()
            if not token:
                raise ValueError("raindrop source account is missing stored token")
            next_page = cursor_state_value("next_page")
            result = self.sync_raindrop_account(
                user_id,
                token=token,
                collection_id=str(credential_payload.get("collection_id") or metadata.get("collection_id") or "0"),
                source_account_id=account_id,
                account_label=account_label,
                account_identifier=account_identifier,
                since=None if next_page else (high_water_mark or cursor_value),
                page=next_page,
                pending_high_water_mark=cursor_state_value("pending_high_water_mark"),
                processing=processing,
                max_records=max_records,
                cursor_name=cursor_name,
                include_highlights=bool(credential_payload.get("include_highlights", metadata.get("include_highlights", True))),
                api_base_url=str(credential_payload.get("api_base_url") or metadata.get("api_base_url") or "https://api.raindrop.io/rest/v1"),
            )
        elif source == "zotero":
            next_cursor = cursor_state_value("next_cursor")
            zotero_payload = credential_payload or metadata
            result = self.sync_zotero_account(
                user_id,
                token=str(zotero_payload.get("token") or "").strip() or None,
                library_type=str(zotero_payload.get("library_type") or metadata.get("library_type") or "user"),
                library_id=str(zotero_payload.get("library_id") or metadata.get("library_id") or "0"),
                source_account_id=account_id,
                account_label=account_label,
                account_identifier=account_identifier,
                since=None if next_cursor else (high_water_mark or cursor_value),
                cursor=next_cursor,
                processing=processing,
                max_records=max_records,
                cursor_name=cursor_name,
                include_attachments=bool(zotero_payload.get("include_attachments", metadata.get("include_attachments", False))),
                api_base_url=str(zotero_payload.get("api_base_url") or metadata.get("api_base_url") or "http://localhost:23119/api"),
            )
        elif source == "linear":
            token = str(credential_payload.get("token") or "").strip()
            if not token:
                raise ValueError("linear source account is missing stored token")
            next_cursor = cursor_state_value("next_cursor")
            result = self.sync_linear_account(
                user_id,
                token=token,
                source_account_id=account_id,
                account_label=account_label,
                account_identifier=account_identifier,
                since=None if next_cursor else (high_water_mark or cursor_value),
                cursor=next_cursor,
                processing=processing,
                max_records=max_records,
                cursor_name=cursor_name,
                api_url=str(credential_payload.get("api_url") or "https://api.linear.app/graphql"),
            )
        elif source == "jira":
            email = str(credential_payload.get("email") or "").strip()
            api_token = str(credential_payload.get("api_token") or "").strip()
            site_url = str(credential_payload.get("site_url") or metadata.get("site_url") or "").strip()
            if not email or not api_token or not site_url:
                raise ValueError("jira source account is missing stored email, token, or site URL")
            next_page_token = cursor_state_value("next_page_token")
            result = self.sync_jira_account(
                user_id,
                email=email,
                api_token=api_token,
                site_url=site_url,
                source_account_id=account_id,
                account_label=account_label,
                account_identifier=account_identifier,
                jql=str(credential_payload.get("jql") or "").strip() or None,
                since=None if next_page_token else (high_water_mark or cursor_value),
                page_token=next_page_token,
                processing=processing,
                max_records=max_records,
                cursor_name=cursor_name,
            )
        elif source == "notion":
            token = str(credential_payload.get("token") or "").strip()
            if not token:
                raise ValueError("notion source account is missing stored token")
            next_cursor = cursor_state_value("next_cursor")
            result = self.sync_notion_account(
                user_id,
                token=token,
                source_account_id=account_id,
                account_label=account_label,
                account_identifier=account_identifier,
                since=None if next_cursor else (high_water_mark or cursor_value),
                cursor=next_cursor,
                processing=processing,
                max_records=min(max_records, 200),
                cursor_name=cursor_name,
                include_content=bool(credential_payload.get("include_content", metadata.get("content_sync_enabled", True))),
                api_base_url=str(credential_payload.get("api_base_url") or "https://api.notion.com/v1"),
                notion_version=str(credential_payload.get("notion_version") or metadata.get("notion_version") or "2026-03-11"),
            )
        else:
            raise ValueError("source account needs stored sync configuration before it can be scheduled")
        self._mark_source_account_sync_job_finished(
            user_id,
            account_id,
            job_id=job["id"],
            status=str(result.get("status") or ""),
            result=result,
        )
        return {
            "source_account_id": account_id,
            "source": source,
            "sync_status": result.get("status"),
            "processing": result.get("processing"),
            "received": result.get("received"),
            "queued": result.get("queued"),
            "saved": result.get("saved"),
            "skipped": result.get("skipped"),
            "failed": result.get("failed"),
            "archived_missing": result.get("archived_missing"),
            "capture_ids": result.get("capture_ids") or [],
            "cursor": result.get("cursor"),
            "completed_at": now_iso(),
        }

    def _mark_source_account_sync_job_finished(self, user_id: str, account_id: str, *, job_id: str, status: str, result: dict[str, Any] | None = None) -> None:
        timestamp = now_iso()
        result = result or {}
        errors = [error for error in (result.get("errors") or []) if isinstance(error, dict)]
        failed = int(result.get("failed") or 0)
        clean_success = not errors and failed == 0 and status not in {"partial", "failed", "error"}
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM source_accounts WHERE user_id = ? AND id = ?", (user_id, account_id)).fetchone()
            if not row:
                return
            account = self._source_account_from_row(row)
            metadata = dict(account.get("metadata") or {})
            metadata.pop("next_sync_due_at", None)
            if clean_success:
                metadata.pop("retry_after", None)
                metadata.pop("last_connector_failure", None)
            else:
                metadata = _with_connector_failure_metadata(metadata, errors)
            metadata["last_scheduler_job_id"] = job_id
            metadata["last_scheduler_sync_at"] = timestamp
            metadata["last_scheduler_status"] = status[:80]
            conn.execute(
                """
                UPDATE source_accounts
                SET metadata_json = ?,
                    status = ?,
                    auth_state = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE user_id = ? AND id = ?
                """,
                (
                    json.dumps(metadata),
                    "connected" if clean_success else "needs_attention",
                    (
                        "healthy"
                        if clean_success and account.get("auth_state") in {"", "error", "not_configured", "available"}
                        else ("error" if not clean_success else account.get("auth_state"))
                    ),
                    None if clean_success else (errors[0].get("error") if errors else account.get("last_error")),
                    timestamp,
                    user_id,
                    account_id,
                ),
            )
            updated = conn.execute("SELECT * FROM source_accounts WHERE user_id = ? AND id = ?", (user_id, account_id)).fetchone()
        if updated:
            self.vault.write_source_account(self._source_account_from_row(updated))

    def _mark_source_account_sync_job_failed(self, job: dict[str, Any], error: str) -> None:
        user_id = job.get("user_id")
        account_id = job.get("object_id")
        if not user_id or not account_id:
            return
        timestamp_dt = datetime.now(timezone.utc)
        timestamp = _isoformat_z(timestamp_dt)
        retry_after = _isoformat_z(timestamp_dt + timedelta(seconds=min(3600, 300 * max(1, int(job.get("attempts") or 1)))))
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM source_accounts WHERE user_id = ? AND id = ?", (user_id, account_id)).fetchone()
            if not row:
                return
            account = self._source_account_from_row(row)
            metadata = dict(account.get("metadata") or {})
            metadata["retry_after"] = retry_after
            metadata["last_scheduler_job_id"] = job.get("id")
            metadata["last_scheduler_error_at"] = timestamp
            conn.execute(
                """
                UPDATE source_accounts
                SET status = 'needs_attention',
                    auth_state = CASE
                      WHEN auth_state IN ('healthy', 'authorized', 'available', 'not_configured') THEN 'error'
                      ELSE auth_state
                    END,
                    metadata_json = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE user_id = ? AND id = ?
                """,
                (json.dumps(metadata), error[:500], timestamp, user_id, account_id),
            )
            if job.get("id"):
                conn.execute(
                    """
                    UPDATE memory_jobs
                    SET run_at = ?,
                        updated_at = ?
                    WHERE user_id = ? AND id = ?
                    """,
                    (retry_after, timestamp, user_id, job["id"]),
                )
            updated = conn.execute("SELECT * FROM source_accounts WHERE user_id = ? AND id = ?", (user_id, account_id)).fetchone()
        if updated:
            self.vault.write_source_account(self._source_account_from_row(updated))

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
        source_account_id = capture["source_account_id"] if "source_account_id" in capture.keys() else None
        external_id = capture["external_id"] if "external_id" in capture.keys() else None
        extraction_mode = "connector" if source_account_id else ("local" if import_id else None)
        extracted = extract_context(
            content,
            source,
            author_aliases=self._identity_aliases_for_source(user_id, source),
            extraction_mode=extraction_mode,
        )
        extracted = _apply_source_record_metadata(
            extracted,
            payload.get("record_metadata") if isinstance(payload.get("record_metadata"), dict) else {},
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
            source_account_id=source_account_id,
            external_id=external_id,
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
        attempts = int(job.get("attempts", 0))
        final_status = "failed" if attempts >= int(job.get("max_attempts", 3)) else "queued"
        # Exponential backoff on requeue so a transient failure (e.g. an embeddings 429) isn't
        # re-claimed in the same tick and hammered — _claim_next_job gates on run_at <= now.
        backoff_seconds = min(300, 30 * (2 ** max(0, attempts - 1)))
        parsed_now = _parse_iso_timestamp(timestamp) or datetime.now(timezone.utc)
        run_at = (parsed_now + timedelta(seconds=backoff_seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE memory_jobs
                SET status = ?,
                    last_error = ?,
                    locked_by = NULL,
                    locked_until = NULL,
                    updated_at = ?,
                    run_at = ?
                WHERE id = ?
                """,
                (final_status, error[:500], timestamp, run_at, job["id"]),
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
        elif job.get("job_type") == "source_account_sync":
            self._mark_source_account_sync_job_failed(job, error)
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

    def _job_health_item(self, job: dict[str, Any], *, now: datetime) -> dict[str, Any]:
        return {
            "id": job.get("id"),
            "job_type": job.get("job_type"),
            "object_type": job.get("object_type"),
            "object_id": job.get("object_id"),
            "status": job.get("status"),
            "attempts": job.get("attempts"),
            "max_attempts": job.get("max_attempts"),
            "locked_by": job.get("locked_by"),
            "locked_until": job.get("locked_until"),
            "last_error": job.get("last_error"),
            "updated_at": job.get("updated_at"),
            "age_seconds": _age_seconds(job.get("updated_at"), now=now),
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

    def _save_memory(
        self,
        conn,
        capture_id: str,
        user_id: str,
        record: dict[str, Any],
        source: str,
        source_url: str | None,
        captured_at: str,
        raw_text: str,
        *,
        granular_source_url: bool = False,
        source_account: dict[str, Any] | None = None,
        external_id: str | None = None,
    ) -> dict[str, Any]:
        base_memory_id = str(record["id"])
        memory_id = base_memory_id
        if source_account and external_id:
            memory_id = stable_id("mem_", f"{user_id}:{capture_id}:{base_memory_id}")
        kind = record.get("kind", "observation")
        layer = memory_layer(kind, record.get("layer"))
        topics = record.get("topics", [])
        entity_ids = record.get("entity_ids", [])
        raw_excerpt = _memory_raw_excerpt(record, raw_text)
        try:
            line_offset = int(record.get("source_line_offset") or 0)
        except (TypeError, ValueError):
            line_offset = 0
        memory_source_url = _granular_memory_source_url(
            source_url,
            raw_text,
            raw_excerpt,
            enabled=granular_source_url,
            line_offset=max(0, line_offset),
        )
        sector = _memory_sector(record, memory_source_url, source_account)
        source_type = str(record.get("source_type") or _memory_source_type(source, memory_source_url)).strip()[:80]
        provenance = _memory_provenance(
            record,
            source=source,
            source_url=memory_source_url,
            capture_id=capture_id,
            source_account=source_account,
            external_id=external_id,
        )
        occurred_at = record.get("occurred_at")
        valid_from = record.get("valid_from")
        valid_to = record.get("valid_to")
        superseded_by = str(record.get("superseded_by") or "").strip()[:80] or None
        duplicate = self._find_duplicate_memory(
            conn,
            user_id,
            capture_id,
            memory_id,
            kind,
            layer,
            record.get("content", ""),
            same_capture_only=bool(source_account and external_id),
        )
        if duplicate:
            if source_account and external_id:
                return self._refresh_duplicate_source_memory(
                    conn,
                    duplicate,
                    user_id=user_id,
                    record=record,
                    source=source,
                    memory_source_url=memory_source_url,
                    captured_at=captured_at,
                    source_type=source_type,
                    provenance=provenance,
                    topics=topics,
                    entity_ids=entity_ids,
                    sector=sector,
                    layer=layer,
                    occurred_at=occurred_at,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    superseded_by=superseded_by,
                )
            return self._memory_from_row(duplicate)
        conn.execute(
            """
            INSERT OR REPLACE INTO memories
            (id, capture_id, user_id, kind, layer, content, summary, source, source_url, confidence, importance, status, sector, source_type, provenance_json, topics_json, entity_ids_json, occurred_at, valid_from, valid_to, superseded_by, captured_at, updated_at, raw_excerpt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                memory_source_url,
                record.get("confidence", "confirmed"),
                int(record.get("importance", 3)),
                sector,
                source_type,
                json.dumps(provenance),
                json.dumps(topics),
                json.dumps(entity_ids),
                occurred_at,
                valid_from,
                valid_to,
                superseded_by,
                captured_at,
                captured_at,
                raw_excerpt,
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
            "source_url": memory_source_url,
            "confidence": record.get("confidence", "confirmed"),
            "importance": int(record.get("importance", 3)),
            "status": "active",
            "sector": sector,
            "source_type": source_type,
            "provenance": provenance,
            "topics": topics,
            "entity_ids": entity_ids,
            "occurred_at": occurred_at,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "superseded_by": superseded_by,
            "captured_at": captured_at,
            "updated_at": captured_at,
            "raw_excerpt": raw_excerpt,
        }

    def _refresh_duplicate_source_memory(
        self,
        conn,
        duplicate,
        *,
        user_id: str,
        record: dict[str, Any],
        source: str,
        memory_source_url: str | None,
        captured_at: str,
        source_type: str,
        provenance: dict[str, Any],
        topics: list[str],
        entity_ids: list[str],
        sector: str,
        layer: str,
        occurred_at: str | None,
        valid_from: str | None,
        valid_to: str | None,
        superseded_by: str | None,
    ) -> dict[str, Any]:
        memory_id = duplicate["id"]
        conn.execute(
            """
            UPDATE memories
            SET source_url = ?,
                sector = ?,
                source_type = ?,
                provenance_json = ?,
                topics_json = ?,
                entity_ids_json = ?,
                occurred_at = ?,
                valid_from = ?,
                valid_to = ?,
                superseded_by = ?,
                updated_at = ?
            WHERE user_id = ? AND id = ?
            """,
            (
                memory_source_url,
                sector,
                source_type,
                json.dumps(provenance),
                json.dumps(topics),
                json.dumps(entity_ids),
                occurred_at,
                valid_from,
                valid_to,
                superseded_by,
                captured_at,
                user_id,
                memory_id,
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
            capture_id=duplicate["capture_id"],
            user_id=user_id,
            content=record.get("content", ""),
            summary=record.get("summary", ""),
            source=source,
            layer=layer,
            topics=topics,
            captured_at=captured_at,
        )
        row = conn.execute(
            "SELECT * FROM memories WHERE user_id = ? AND id = ?",
            (user_id, memory_id),
        ).fetchone()
        return self._memory_from_row(row)

    def _save_memory_relations_for_capture(
        self,
        conn,
        user_id: str,
        capture_id: str,
        memories: list[dict[str, Any]],
        captured_at: str,
    ) -> list[dict[str, Any]]:
        active = [memory for memory in memories if memory.get("id") and memory.get("status", "active") == "active"]
        if len(active) < 2:
            return []
        memory_ids = [memory["id"] for memory in active]
        placeholders = ",".join("?" for _ in memory_ids)
        conn.execute(
            f"""
            DELETE FROM memory_relations
            WHERE user_id = ?
              AND source_memory_id IN ({placeholders})
              AND target_memory_id IN ({placeholders})
            """,
            [user_id, *memory_ids, *memory_ids],
        )

        relations: list[dict[str, Any]] = []
        if len(active) > SAME_CAPTURE_RELATION_FULL_PAIR_LIMIT:
            return self._save_bounded_memory_relations_for_capture(conn, user_id, capture_id, active, captured_at)
        for left_index, left in enumerate(active):
            left_entities = {str(value) for value in left.get("entity_ids") or [] if str(value)}
            left_topics = {str(value).lower() for value in left.get("topics") or [] if str(value).strip()}
            for right in active[left_index + 1 :]:
                right_entities = {str(value) for value in right.get("entity_ids") or [] if str(value)}
                right_topics = {str(value).lower() for value in right.get("topics") or [] if str(value).strip()}
                shared_entities = sorted(left_entities & right_entities)
                shared_topics = sorted(left_topics & right_topics)
                if not shared_entities and not shared_topics:
                    continue
                relation_kind = "shared_entity" if shared_entities else "shared_topic"
                weight = min(1.0, 0.55 + (0.25 * len(shared_entities)) + (0.08 * len(shared_topics)))
                source_id, target_id = sorted([left["id"], right["id"]])
                metadata = {
                    "capture_id": capture_id,
                    "shared_entities": shared_entities[:12],
                    "shared_topics": shared_topics[:12],
                    "source": left.get("source") or right.get("source"),
                }
                relation_id = stable_id("rel_", f"{user_id}:{source_id}:{target_id}:{relation_kind}:{','.join(shared_entities)}:{','.join(shared_topics)}")
                relation = {
                    "id": relation_id,
                    "user_id": user_id,
                    "source_memory_id": source_id,
                    "target_memory_id": target_id,
                    "kind": relation_kind,
                    "weight": round(weight, 3),
                    "metadata": metadata,
                    "created_at": captured_at,
                }
                conn.execute(
                    """
                    INSERT OR REPLACE INTO memory_relations
                    (id, user_id, source_memory_id, target_memory_id, kind, weight, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        relation["id"],
                        user_id,
                        source_id,
                        target_id,
                        relation_kind,
                        relation["weight"],
                        json.dumps(metadata),
                        captured_at,
                    ),
                )
                relations.append(relation)
        return relations

    def _save_bounded_memory_relations_for_capture(
        self,
        conn,
        user_id: str,
        capture_id: str,
        active: list[dict[str, Any]],
        captured_at: str,
    ) -> list[dict[str, Any]]:
        entity_index: dict[str, list[int]] = {}
        topic_index: dict[str, list[int]] = {}
        normalized: list[dict[str, Any]] = []
        for index, memory in enumerate(active):
            entities = {str(value) for value in memory.get("entity_ids") or [] if str(value)}
            topics = {str(value).lower() for value in memory.get("topics") or [] if str(value).strip()}
            normalized.append({"entities": entities, "topics": topics})
            for entity_id in entities:
                entity_index.setdefault(entity_id, []).append(index)
            for topic in topics:
                topic_index.setdefault(topic, []).append(index)

        relations: list[dict[str, Any]] = []
        seen_pairs: set[tuple[str, str, str]] = set()
        for left_index, left in enumerate(active):
            left_terms = normalized[left_index]
            candidates: set[int] = set()
            for entity_id in left_terms["entities"]:
                candidates.update(self._bounded_relation_candidate_indexes(entity_index.get(entity_id, []), left_index))
            for topic in left_terms["topics"]:
                topic_matches = topic_index.get(topic, [])
                if len(topic_matches) <= SAME_CAPTURE_RELATION_TOPIC_BUCKET_LIMIT:
                    candidates.update(self._bounded_relation_candidate_indexes(topic_matches, left_index))
            if not candidates:
                continue
            ranked_candidates: list[tuple[float, int, list[str], list[str]]] = []
            for right_index in candidates:
                right_terms = normalized[right_index]
                shared_entities = sorted(left_terms["entities"] & right_terms["entities"])
                shared_topics = sorted(left_terms["topics"] & right_terms["topics"])
                if not shared_entities and not shared_topics:
                    continue
                right = active[right_index]
                score = (
                    (1.0 if shared_entities else 0.0)
                    + (0.2 * len(shared_entities))
                    + (0.03 * len(shared_topics))
                    + (0.001 * int(right.get("importance") or 0))
                )
                ranked_candidates.append((score, right_index, shared_entities, shared_topics))
            added_for_left = 0
            for _, right_index, shared_entities, shared_topics in sorted(ranked_candidates, key=lambda item: (-item[0], item[1])):
                right = active[right_index]
                relation_kind = "shared_entity" if shared_entities else "shared_topic"
                source_id, target_id = sorted([left["id"], right["id"]])
                pair_key = (source_id, target_id, relation_kind)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                weight = min(1.0, 0.55 + (0.25 * len(shared_entities)) + (0.08 * len(shared_topics)))
                metadata = {
                    "capture_id": capture_id,
                    "shared_entities": shared_entities[:12],
                    "shared_topics": shared_topics[:12],
                    "source": left.get("source") or right.get("source"),
                    "bounded": True,
                    "candidate_count": len(active),
                }
                relation_id = stable_id("rel_", f"{user_id}:{source_id}:{target_id}:{relation_kind}:{','.join(shared_entities)}:{','.join(shared_topics)}")
                relation = {
                    "id": relation_id,
                    "user_id": user_id,
                    "source_memory_id": source_id,
                    "target_memory_id": target_id,
                    "kind": relation_kind,
                    "weight": round(weight, 3),
                    "metadata": metadata,
                    "created_at": captured_at,
                }
                conn.execute(
                    """
                    INSERT OR REPLACE INTO memory_relations
                    (id, user_id, source_memory_id, target_memory_id, kind, weight, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        relation["id"],
                        user_id,
                        source_id,
                        target_id,
                        relation_kind,
                        relation["weight"],
                        json.dumps(metadata),
                        captured_at,
                    ),
                )
                relations.append(relation)
                added_for_left += 1
                if added_for_left >= SAME_CAPTURE_RELATION_PER_MEMORY_LIMIT or len(relations) >= SAME_CAPTURE_RELATION_TOTAL_LIMIT:
                    break
            if len(relations) >= SAME_CAPTURE_RELATION_TOTAL_LIMIT:
                break
        return relations

    def _bounded_relation_candidate_indexes(self, indexes: list[int], left_index: int) -> list[int]:
        if not indexes:
            return []
        return [index for index in indexes if index > left_index][:SAME_CAPTURE_RELATION_BUCKET_SCAN_LIMIT]

    def _save_cross_capture_memory_relations(
        self,
        conn,
        user_id: str,
        capture_id: str,
        memories: list[dict[str, Any]],
        captured_at: str,
        *,
        per_memory_limit: int = 8,
    ) -> list[dict[str, Any]]:
        active = [memory for memory in memories if memory.get("id") and memory.get("status", "active") == "active"]
        if not active:
            return []
        memory_ids = [str(memory["id"]) for memory in active]
        memory_placeholders = ",".join("?" for _ in memory_ids)
        conn.execute(
            f"""
            DELETE FROM memory_relations
            WHERE user_id = ?
              AND kind IN ('shared_entity', 'shared_topic')
              AND (source_memory_id IN ({memory_placeholders}) OR target_memory_id IN ({memory_placeholders}))
              AND NOT (source_memory_id IN ({memory_placeholders}) AND target_memory_id IN ({memory_placeholders}))
            """,
            [user_id, *memory_ids, *memory_ids, *memory_ids, *memory_ids],
        )

        relations: list[dict[str, Any]] = []
        now = now_iso()
        for memory in active:
            left_id = str(memory["id"])
            left_entities = {str(value) for value in memory.get("entity_ids") or [] if str(value)}
            if not left_entities:
                continue
            entity_placeholders = ",".join("?" for _ in left_entities)
            rows = conn.execute(
                f"""
                SELECT DISTINCT m.*
                FROM memory_entities me
                JOIN memories m ON m.id = me.memory_id AND m.user_id = me.user_id
                WHERE me.user_id = ?
                  AND me.entity_id IN ({entity_placeholders})
                  AND m.id NOT IN ({memory_placeholders})
                  AND COALESCE(m.capture_id, '') != ?
                  AND m.status = 'active'
                  AND (m.valid_from IS NULL OR m.valid_from = '' OR m.valid_from <= ?)
                  AND (m.valid_to IS NULL OR m.valid_to = '' OR m.valid_to > ?)
                  AND (m.superseded_by IS NULL OR m.superseded_by = '')
                ORDER BY m.importance DESC, COALESCE(m.occurred_at, m.captured_at) DESC, m.captured_at DESC
                LIMIT ?
                """,
                [user_id, *sorted(left_entities), *memory_ids, capture_id, now, now, max(1, per_memory_limit * 4)],
            ).fetchall()
            left_topics = {str(value).lower() for value in memory.get("topics") or [] if str(value).strip()}
            added = 0
            for row in rows:
                right = self._memory_from_row(row)
                right_entities = {str(value) for value in right.get("entity_ids") or [] if str(value)}
                shared_entities = sorted(left_entities & right_entities)
                if not shared_entities:
                    continue
                right_topics = {str(value).lower() for value in right.get("topics") or [] if str(value).strip()}
                shared_topics = sorted(left_topics & right_topics)
                source_id, target_id = sorted([left_id, right["id"]])
                metadata = {
                    "capture_id": capture_id,
                    "related_capture_id": right.get("capture_id"),
                    "shared_entities": shared_entities[:12],
                    "shared_topics": shared_topics[:12],
                    "source": memory.get("source") or right.get("source"),
                }
                relation_id = stable_id("rel_", f"{user_id}:{source_id}:{target_id}:shared_entity:{','.join(shared_entities)}:{','.join(shared_topics)}")
                relation = {
                    "id": relation_id,
                    "user_id": user_id,
                    "source_memory_id": source_id,
                    "target_memory_id": target_id,
                    "kind": "shared_entity",
                    "weight": round(min(1.0, 0.5 + (0.25 * len(shared_entities)) + (0.05 * len(shared_topics))), 3),
                    "metadata": metadata,
                    "created_at": captured_at,
                }
                conn.execute(
                    """
                    INSERT OR REPLACE INTO memory_relations
                    (id, user_id, source_memory_id, target_memory_id, kind, weight, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        relation["id"],
                        user_id,
                        source_id,
                        target_id,
                        relation["kind"],
                        relation["weight"],
                        json.dumps(metadata),
                        captured_at,
                    ),
                )
                relations.append(relation)
                added += 1
                if added >= per_memory_limit:
                    break
        return relations

    def _save_obsidian_canvas_memory_relations(
        self,
        conn,
        user_id: str,
        capture_id: str,
        memories: list[dict[str, Any]],
        captured_at: str,
        *,
        per_memory_limit: int = 12,
    ) -> list[dict[str, Any]]:
        active = [memory for memory in memories if memory.get("id") and memory.get("status", "active") == "active"]
        canvas_memories: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for memory in active:
            metadata = self._obsidian_canvas_memory_metadata(memory)
            if metadata:
                canvas_memories.append((memory, metadata))
        if not canvas_memories:
            return []

        memory_ids = [str(memory["id"]) for memory, _metadata in canvas_memories]
        placeholders = ",".join("?" for _ in memory_ids)
        conn.execute(
            f"""
            DELETE FROM memory_relations
            WHERE user_id = ?
              AND kind = 'canvas_edge'
              AND (source_memory_id IN ({placeholders}) OR target_memory_id IN ({placeholders}))
            """,
            [user_id, *memory_ids, *memory_ids],
        )

        relations: list[dict[str, Any]] = []
        seen_pairs: set[tuple[str, str, str]] = set()
        for memory, metadata in canvas_memories:
            edge_rows = metadata.get("canvas_node_edges") if isinstance(metadata.get("canvas_node_edges"), list) else []
            node_id = str(metadata.get("canvas_node_id") or "").strip()
            if not node_id or not edge_rows:
                continue
            related_node_ids = sorted(
                {
                    other
                    for edge in edge_rows
                    if isinstance(edge, dict)
                    for other in [self._canvas_edge_other_node(edge, node_id)]
                    if other
                }
            )
            if not related_node_ids:
                continue
            candidate_memories = self._obsidian_canvas_related_memory_candidates(
                conn,
                user_id,
                metadata,
                related_node_ids,
                limit=max(per_memory_limit * 4, len(related_node_ids) * 4),
            )
            candidates_by_node: dict[str, list[dict[str, Any]]] = {}
            for candidate in candidate_memories:
                candidate_metadata = self._obsidian_canvas_memory_metadata(candidate)
                candidate_node_id = str((candidate_metadata or {}).get("canvas_node_id") or "").strip()
                if not candidate_node_id or candidate_node_id == node_id:
                    continue
                candidates_by_node.setdefault(candidate_node_id, []).append(candidate)

            added_for_memory = 0
            for edge in edge_rows:
                if not isinstance(edge, dict):
                    continue
                other_node = self._canvas_edge_other_node(edge, node_id)
                if not other_node:
                    continue
                edge_id = str(edge.get("id") or "").strip()
                edge_label = str(edge.get("label") or "").strip()
                for related in candidates_by_node.get(other_node, []):
                    source_id, target_id = sorted([str(memory["id"]), str(related["id"])])
                    pair_key = (source_id, target_id, edge_id or f"{node_id}->{other_node}")
                    if source_id == target_id or pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)
                    metadata_payload = {
                        "capture_id": capture_id,
                        "related_capture_id": related.get("capture_id"),
                        "source": "obsidian",
                        "relation_source": "obsidian_canvas",
                        "canvas_relative_path": metadata.get("relative_path"),
                        "canvas_vault_id": metadata.get("vault_id"),
                        "canvas_edge_id": edge_id,
                        "canvas_edge_label": edge_label,
                        "canvas_from_node": str(edge.get("from") or "").strip(),
                        "canvas_to_node": str(edge.get("to") or "").strip(),
                        "canvas_anchor_node": node_id,
                        "canvas_related_node": other_node,
                    }
                    relation_id = stable_id(
                        "rel_",
                        json.dumps(
                            {
                                "user_id": user_id,
                                "source_id": source_id,
                                "target_id": target_id,
                                "kind": "canvas_edge",
                                "edge_id": edge_id,
                                "from": metadata_payload["canvas_from_node"],
                                "to": metadata_payload["canvas_to_node"],
                            },
                            sort_keys=True,
                        ),
                    )
                    weight = 0.92 if edge_label else 0.86
                    relation = {
                        "id": relation_id,
                        "user_id": user_id,
                        "source_memory_id": source_id,
                        "target_memory_id": target_id,
                        "kind": "canvas_edge",
                        "weight": weight,
                        "metadata": metadata_payload,
                        "created_at": captured_at,
                    }
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO memory_relations
                        (id, user_id, source_memory_id, target_memory_id, kind, weight, metadata_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            relation_id,
                            user_id,
                            source_id,
                            target_id,
                            "canvas_edge",
                            weight,
                            json.dumps(metadata_payload),
                            captured_at,
                        ),
                    )
                    relations.append(relation)
                    added_for_memory += 1
                    if added_for_memory >= per_memory_limit:
                        break
                if added_for_memory >= per_memory_limit:
                    break
        return relations

    def _obsidian_canvas_memory_metadata(self, memory: dict[str, Any]) -> dict[str, Any] | None:
        provenance = memory.get("provenance") if isinstance(memory.get("provenance"), dict) else {}
        if _normalize_source_key(str(provenance.get("source") or memory.get("source") or "")) != "obsidian":
            return None
        metadata = provenance.get("record_metadata") if isinstance(provenance.get("record_metadata"), dict) else {}
        if str(metadata.get("record_scope") or "").strip() != "canvas_node":
            return None
        node_id = str(metadata.get("canvas_node_id") or "").strip()
        relative_path = str(metadata.get("relative_path") or "").strip()
        if not node_id or not relative_path:
            return None
        return metadata

    def _canvas_edge_other_node(self, edge: dict[str, Any], node_id: str) -> str | None:
        from_node = str(edge.get("from") or "").strip()
        to_node = str(edge.get("to") or "").strip()
        if from_node == node_id and to_node:
            return to_node
        if to_node == node_id and from_node:
            return from_node
        return None

    def _obsidian_canvas_related_memory_candidates(
        self,
        conn,
        user_id: str,
        metadata: dict[str, Any],
        node_ids: list[str],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        normalized_node_ids = [str(node_id).strip() for node_id in node_ids if str(node_id).strip()]
        if not normalized_node_ids:
            return []
        placeholders = ",".join("?" for _ in normalized_node_ids)
        relative_path = str(metadata.get("relative_path") or "").strip()
        vault_id = str(metadata.get("vault_id") or "").strip()
        params: list[Any] = [user_id, relative_path, *normalized_node_ids]
        vault_filter = ""
        if vault_id:
            vault_filter = "AND COALESCE(json_extract(provenance_json, '$.record_metadata.vault_id'), '') = ?"
            params.append(vault_id)
        params.append(max(1, min(200, limit)))
        rows = conn.execute(
            f"""
            SELECT *
            FROM memories
            WHERE user_id = ?
              AND source = 'obsidian'
              AND status = 'active'
              AND COALESCE(json_extract(provenance_json, '$.record_metadata.record_scope'), '') = 'canvas_node'
              AND COALESCE(json_extract(provenance_json, '$.record_metadata.relative_path'), '') = ?
              AND COALESCE(json_extract(provenance_json, '$.record_metadata.canvas_node_id'), '') IN ({placeholders})
              {vault_filter}
              AND (valid_from IS NULL OR valid_from = '' OR valid_from <= ?)
              AND (valid_to IS NULL OR valid_to = '' OR valid_to > ?)
              AND (superseded_by IS NULL OR superseded_by = '')
            ORDER BY importance DESC, COALESCE(occurred_at, captured_at) DESC, captured_at DESC
            LIMIT ?
            """,
            [*params[:-1], now_iso(), now_iso(), params[-1]],
        ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def _save_obsidian_wikilink_memory_relations(
        self,
        conn,
        user_id: str,
        capture_id: str,
        memories: list[dict[str, Any]],
        captured_at: str,
        *,
        per_memory_limit: int = 16,
    ) -> list[dict[str, Any]]:
        active = [memory for memory in memories if memory.get("id") and memory.get("status", "active") == "active"]
        obsidian_memories: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for memory in active:
            metadata = self._obsidian_note_memory_metadata(memory)
            if metadata:
                obsidian_memories.append((memory, metadata))
        if not obsidian_memories:
            return []

        memory_ids = [str(memory["id"]) for memory, _metadata in obsidian_memories]
        placeholders = ",".join("?" for _ in memory_ids)
        conn.execute(
            f"""
            DELETE FROM memory_relations
            WHERE user_id = ?
              AND kind = 'obsidian_link'
              AND (source_memory_id IN ({placeholders}) OR target_memory_id IN ({placeholders}))
            """,
            [user_id, *memory_ids, *memory_ids],
        )

        relations: list[dict[str, Any]] = []
        seen_pairs: set[tuple[str, str, str]] = set()
        for memory, metadata in obsidian_memories:
            wikilinks = metadata.get("wikilinks") if isinstance(metadata.get("wikilinks"), list) else []
            if not wikilinks:
                continue
            source_relative_path = str(metadata.get("relative_path") or "").strip()
            if not source_relative_path:
                continue
            added_for_memory = 0
            for link in wikilinks:
                if not isinstance(link, dict):
                    continue
                target = str(link.get("target") or "").strip()
                if not target:
                    continue
                candidates = self._obsidian_wikilink_related_memory_candidates(
                    conn,
                    user_id,
                    metadata,
                    target,
                    limit=per_memory_limit * 4,
                )
                for related in candidates:
                    related_metadata = self._obsidian_note_memory_metadata(related)
                    if not related_metadata:
                        continue
                    target_relative_path = str(related_metadata.get("relative_path") or "").strip()
                    if not target_relative_path or target_relative_path == source_relative_path:
                        continue
                    source_id, target_id = sorted([str(memory["id"]), str(related["id"])])
                    pair_key = (source_id, target_id, _normalize_obsidian_wikilink_target(target))
                    if source_id == target_id or pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)
                    display = str(link.get("display") or "").strip()
                    metadata_payload = {
                        "capture_id": capture_id,
                        "related_capture_id": related.get("capture_id"),
                        "source": "obsidian",
                        "relation_source": "obsidian_wikilink",
                        "source_relative_path": source_relative_path,
                        "target_relative_path": target_relative_path,
                        "vault_id": metadata.get("vault_id"),
                        "link_target": target,
                        "link_display": display,
                        "embedded": str(link.get("embedded") or "").strip(),
                    }
                    relation_id = stable_id(
                        "rel_",
                        json.dumps(
                            {
                                "user_id": user_id,
                                "source_id": source_id,
                                "target_id": target_id,
                                "kind": "obsidian_link",
                                "link_target": _normalize_obsidian_wikilink_target(target),
                            },
                            sort_keys=True,
                        ),
                    )
                    relation = {
                        "id": relation_id,
                        "user_id": user_id,
                        "source_memory_id": source_id,
                        "target_memory_id": target_id,
                        "kind": "obsidian_link",
                        "weight": 0.84,
                        "metadata": metadata_payload,
                        "created_at": captured_at,
                    }
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO memory_relations
                        (id, user_id, source_memory_id, target_memory_id, kind, weight, metadata_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            relation_id,
                            user_id,
                            source_id,
                            target_id,
                            "obsidian_link",
                            relation["weight"],
                            json.dumps(metadata_payload),
                            captured_at,
                        ),
                    )
                    relations.append(relation)
                    added_for_memory += 1
                    if added_for_memory >= per_memory_limit:
                        break
                if added_for_memory >= per_memory_limit:
                    break
        return relations

    def _obsidian_note_memory_metadata(self, memory: dict[str, Any]) -> dict[str, Any] | None:
        provenance = memory.get("provenance") if isinstance(memory.get("provenance"), dict) else {}
        if _normalize_source_key(str(provenance.get("source") or memory.get("source") or "")) != "obsidian":
            return None
        metadata = provenance.get("record_metadata") if isinstance(provenance.get("record_metadata"), dict) else {}
        relative_path = str(metadata.get("relative_path") or "").strip()
        extension = str(metadata.get("extension") or "").strip().lower()
        if not relative_path or extension == "canvas":
            return None
        return metadata

    def _obsidian_wikilink_related_memory_candidates(
        self,
        conn,
        user_id: str,
        metadata: dict[str, Any],
        target: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        vault_id = str(metadata.get("vault_id") or "").strip()
        target_paths = _obsidian_wikilink_candidate_paths(target)
        if not target_paths:
            return []
        exact_matches = self._obsidian_wikilink_candidate_rows(
            conn,
            user_id,
            vault_id=vault_id,
            target_paths=target_paths,
            limit=limit,
        )
        if exact_matches:
            return exact_matches

        basename = _obsidian_wikilink_target_basename(target)
        if not basename:
            return []
        fallback_rows = self._obsidian_vault_memory_rows(conn, user_id, vault_id=vault_id, limit=max(200, limit))
        by_relative_path: dict[str, list[dict[str, Any]]] = {}
        for row in fallback_rows:
            candidate = self._memory_from_row(row)
            candidate_metadata = self._obsidian_note_memory_metadata(candidate)
            candidate_path = str((candidate_metadata or {}).get("relative_path") or "").strip()
            if not candidate_path:
                continue
            if Path(candidate_path).stem.casefold() != basename.casefold():
                continue
            by_relative_path.setdefault(candidate_path, []).append(candidate)
        if len(by_relative_path) != 1:
            return []
        return list(by_relative_path.values())[0][:limit]

    def _obsidian_wikilink_candidate_rows(
        self,
        conn,
        user_id: str,
        *,
        vault_id: str,
        target_paths: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in target_paths)
        params: list[Any] = [user_id, *target_paths]
        vault_filter = ""
        if vault_id:
            vault_filter = "AND COALESCE(json_extract(provenance_json, '$.record_metadata.vault_id'), '') = ?"
            params.append(vault_id)
        params.append(max(1, min(200, limit)))
        rows = conn.execute(
            f"""
            SELECT *
            FROM memories
            WHERE user_id = ?
              AND source = 'obsidian'
              AND status = 'active'
              AND lower(COALESCE(json_extract(provenance_json, '$.record_metadata.relative_path'), '')) IN ({placeholders})
              {vault_filter}
              AND COALESCE(json_extract(provenance_json, '$.record_metadata.extension'), '') != 'canvas'
              AND (valid_from IS NULL OR valid_from = '' OR valid_from <= ?)
              AND (valid_to IS NULL OR valid_to = '' OR valid_to > ?)
              AND (superseded_by IS NULL OR superseded_by = '')
            ORDER BY importance DESC, COALESCE(occurred_at, captured_at) DESC, captured_at DESC
            LIMIT ?
            """,
            [*params[:-1], now_iso(), now_iso(), params[-1]],
        ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def _obsidian_vault_memory_rows(
        self,
        conn,
        user_id: str,
        *,
        vault_id: str,
        limit: int,
    ) -> list[Any]:
        params: list[Any] = [user_id]
        vault_filter = ""
        if vault_id:
            vault_filter = "AND COALESCE(json_extract(provenance_json, '$.record_metadata.vault_id'), '') = ?"
            params.append(vault_id)
        params.append(max(1, min(500, limit)))
        return conn.execute(
            f"""
            SELECT *
            FROM memories
            WHERE user_id = ?
              AND source = 'obsidian'
              AND status = 'active'
              {vault_filter}
              AND COALESCE(json_extract(provenance_json, '$.record_metadata.extension'), '') != 'canvas'
              AND (valid_from IS NULL OR valid_from = '' OR valid_from <= ?)
              AND (valid_to IS NULL OR valid_to = '' OR valid_to > ?)
              AND (superseded_by IS NULL OR superseded_by = '')
            ORDER BY importance DESC, COALESCE(occurred_at, captured_at) DESC, captured_at DESC
            LIMIT ?
            """,
            [*params[:-1], now_iso(), now_iso(), params[-1]],
        ).fetchall()

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

    def _vector_search(
        self,
        conn,
        user_id: str,
        query: str,
        limit: int,
        kind: str | None,
        layer: str | None,
        user_settings: dict[str, Any],
        *,
        sector: str | None = None,
        source: str | None = None,
        source_account_id: str | None = None,
        metadata_filters: dict[str, Any] | None = None,
        as_of: str | None = None,
    ) -> list[Any]:
        if not self._vector_ready(conn):
            return []
        filters, params = self._memory_filters(
            user_id,
            user_settings,
            alias="m",
            kind=kind,
            layer=layer,
            sector=sector,
            source=source,
            source_account_id=source_account_id,
            metadata_filters=metadata_filters,
            as_of=as_of,
        )
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

    def _temporal_search(
        self,
        conn,
        user_id: str,
        query: str,
        limit: int,
        kind: str | None,
        layer: str | None,
        user_settings: dict[str, Any],
        *,
        sector: str | None = None,
        source: str | None = None,
        source_account_id: str | None = None,
        metadata_filters: dict[str, Any] | None = None,
        as_of: str | None = None,
    ) -> list[Any]:
        prefixes = query_temporal_prefixes(query)
        if not prefixes:
            return []
        most_specific_length = len(prefixes[0])
        effective_prefixes = [prefix for prefix in prefixes if len(prefix) == most_specific_length]
        filters, params = self._memory_filters(
            user_id,
            user_settings,
            alias="m",
            kind=kind,
            layer=layer,
            sector=sector,
            source=source,
            source_account_id=source_account_id,
            metadata_filters=metadata_filters,
            as_of=as_of,
        )
        date_filters = []
        date_params: list[Any] = []
        for prefix in effective_prefixes:
            date_filters.append("m.occurred_at LIKE ?")
            date_params.append(f"{prefix}%")
        filters.append(f"({' OR '.join(date_filters)})")

        term_filters = []
        term_params: list[Any] = []
        for term in query_non_temporal_terms(query):
            like = f"%{term}%"
            term_filters.append(
                "("
                "lower(COALESCE(m.content, '')) LIKE ? OR "
                "lower(COALESCE(m.summary, '')) LIKE ? OR "
                "lower(COALESCE(m.source, '')) LIKE ? OR "
                "lower(COALESCE(m.topics_json, '')) LIKE ?"
                ")"
            )
            term_params.extend([like, like, like, like])
        if term_filters:
            filters.append(f"({' OR '.join(term_filters)})")

        where = " AND ".join(filters)
        return conn.execute(
            f"""
            SELECT m.*
            FROM memories m
            WHERE {where}
            ORDER BY m.importance DESC, m.occurred_at DESC, m.captured_at DESC
            LIMIT ?
            """,
            [*params, *date_params, *term_params, limit],
        ).fetchall()

    def _intent_search(
        self,
        conn,
        user_id: str,
        query: str,
        limit: int,
        kind: str | None,
        layer: str | None,
        user_settings: dict[str, Any],
        *,
        sector: str | None = None,
        source: str | None = None,
        source_account_id: str | None = None,
        metadata_filters: dict[str, Any] | None = None,
        as_of: str | None = None,
    ) -> list[Any]:
        boosts = query_layer_boosts(query)
        if not boosts:
            return []
        intent_layers = sorted(boosts, key=lambda value: (-boosts[value], value))
        if layer:
            requested_layer = memory_layer(kind, layer)
            intent_layers = [value for value in intent_layers if value == requested_layer]
        if not intent_layers:
            return []
        filters, params = self._memory_filters(
            user_id,
            user_settings,
            alias="m",
            kind=kind,
            layer=None,
            sector=sector,
            source=source,
            source_account_id=source_account_id,
            metadata_filters=metadata_filters,
            as_of=as_of,
        )
        placeholders = ", ".join("?" for _ in intent_layers)
        where = " AND ".join(filters)
        return conn.execute(
            f"""
            SELECT m.*
            FROM memories m
            WHERE {where}
              AND m.layer IN ({placeholders})
            ORDER BY m.importance DESC, COALESCE(m.occurred_at, m.captured_at) DESC, m.captured_at DESC
            LIMIT ?
            """,
            [*params, *intent_layers, limit],
        ).fetchall()

    def _lexical_fallback_search(
        self,
        conn,
        user_id: str,
        query: str,
        limit: int,
        kind: str | None,
        layer: str | None,
        user_settings: dict[str, Any],
        *,
        sector: str | None = None,
        source: str | None = None,
        source_account_id: str | None = None,
        metadata_filters: dict[str, Any] | None = None,
        as_of: str | None = None,
    ) -> list[Any]:
        terms = self._lexical_fallback_terms(query)
        if not terms:
            return []
        match_query = " OR ".join(f"{term}*" for term in terms)
        filters, params = self._memory_filters(
            user_id,
            user_settings,
            alias="m",
            kind=kind,
            layer=layer,
            sector=sector,
            source=source,
            source_account_id=source_account_id,
            metadata_filters=metadata_filters,
            as_of=as_of,
        )
        where = " AND ".join(filters)
        rows = conn.execute(
            f"""
            SELECT m.*, bm25(memory_fts) AS rank
            FROM memory_fts
            JOIN memories m ON m.id = memory_fts.memory_id
            WHERE memory_fts MATCH ? AND {where}
            ORDER BY rank ASC, m.importance DESC, m.captured_at DESC
            LIMIT ?
            """,
            [match_query, *params, limit],
        ).fetchall()
        if not rows:
            return []

        min_matches = 2 if len(terms) <= 3 else max(3, (len(terms) + 1) // 2)
        ranked: list[dict[str, Any]] = []
        layer_boosts = query_layer_boosts(query)
        temporal_prefixes = query_temporal_prefixes(query)
        source_policies = _normalize_source_policies(user_settings.get("source_policies"))
        now = datetime.now(timezone.utc)
        for index, row in enumerate(rows):
            matched = self._lexical_matched_terms(row, terms)
            if len(matched) < min_matches:
                continue
            ranked.append(
                {
                    "row": row,
                    "score": (len(matched) / len(terms))
                    + (0.05 / (60 + index))
                    + self._layer_boost(row, layer_boosts)
                    + self._temporal_boost(row, temporal_prefixes)
                    + self._source_quality_boost(row, source_policies)
                    + self._recency_boost(row, now=now)
                    + self._importance_boost(row)
                    + self._confidence_boost(row),
                }
            )
        return [item["row"] for item in sorted(ranked, key=lambda item: item["score"], reverse=True)]

    def _fuse_search_rows(
        self,
        query: str,
        fts_rows: list[Any],
        vector_rows: list[Any],
        temporal_rows: list[Any],
        intent_rows: list[Any],
        limit: int,
        *,
        user_settings: dict[str, Any] | None = None,
    ) -> list[Any]:
        ranked: dict[str, dict[str, Any]] = {}
        for index, row in enumerate(fts_rows):
            entry = ranked.setdefault(row["id"], {"row": row, "score": 0.0})
            entry["score"] += 0.6 / (60 + index)
        for index, row in enumerate(vector_rows):
            entry = ranked.setdefault(row["id"], {"row": row, "score": 0.0})
            entry["score"] += 0.4 / (60 + index)
        for index, row in enumerate(temporal_rows):
            entry = ranked.setdefault(row["id"], {"row": row, "score": 0.0})
            entry["score"] += 0.5 / (60 + index)
        for index, row in enumerate(intent_rows):
            entry = ranked.setdefault(row["id"], {"row": row, "score": 0.0})
            entry["score"] += 0.35 / (60 + index)
        layer_boosts = query_layer_boosts(query)
        temporal_prefixes = query_temporal_prefixes(query)
        source_policies = _normalize_source_policies((user_settings or {}).get("source_policies"))
        now = datetime.now(timezone.utc)
        for entry in ranked.values():
            entry["score"] += self._layer_boost(entry["row"], layer_boosts)
            entry["score"] += self._temporal_boost(entry["row"], temporal_prefixes)
            entry["score"] += self._source_quality_boost(entry["row"], source_policies)
            entry["score"] += self._recency_boost(entry["row"], now=now)
            entry["score"] += self._importance_boost(entry["row"])
            entry["score"] += self._confidence_boost(entry["row"])
        return [item["row"] for item in sorted(ranked.values(), key=lambda item: item["score"], reverse=True)[:limit]]

    def _rank_rows_with_layer_boosts(
        self,
        query: str,
        rows: list[Any],
        limit: int,
        *,
        user_settings: dict[str, Any] | None = None,
    ) -> list[Any]:
        layer_boosts = query_layer_boosts(query)
        temporal_prefixes = query_temporal_prefixes(query)
        source_policies = _normalize_source_policies((user_settings or {}).get("source_policies"))
        now = datetime.now(timezone.utc)
        ranked = [
            {
                "row": row,
                "score": (0.2 / (60 + index))
                + self._layer_boost(row, layer_boosts)
                + self._temporal_boost(row, temporal_prefixes)
                + self._source_quality_boost(row, source_policies)
                + self._recency_boost(row, now=now)
                + self._importance_boost(row)
                + self._confidence_boost(row),
            }
            for index, row in enumerate(rows)
        ]
        return [item["row"] for item in sorted(ranked, key=lambda item: item["score"], reverse=True)[:limit]]

    def _diversify_memory_rows(self, rows: list[Any], limit: int, *, scoped_to_source: bool) -> list[Any]:
        if limit <= 0 or len(rows) <= 1:
            return rows[: max(0, limit)]

        selected: list[Any] = []
        seen_ids: set[str] = set()
        seen_signatures: set[str] = set()
        bucket_counts: dict[tuple[str, str], int] = {}

        def can_select(row: Any, *, strict: bool) -> bool:
            memory_id = str(self._row_value(row, "id") or "").strip()
            if not memory_id or memory_id in seen_ids:
                return False
            signature = self._row_content_signature(row)
            if signature and signature in seen_signatures:
                return False
            if not strict:
                return True
            for bucket in self._row_evidence_buckets(row, scoped_to_source=scoped_to_source):
                cap = 2
                if bucket_counts.get(bucket, 0) >= cap:
                    return False
            return True

        def select(row: Any) -> None:
            selected.append(row)
            seen_ids.add(str(self._row_value(row, "id") or ""))
            signature = self._row_content_signature(row)
            if signature:
                seen_signatures.add(signature)
            for bucket in self._row_evidence_buckets(row, scoped_to_source=scoped_to_source):
                bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

        for row in rows:
            if can_select(row, strict=True):
                select(row)
                if len(selected) >= limit:
                    return selected[:limit]

        for row in rows:
            if can_select(row, strict=False):
                select(row)
                if len(selected) >= limit:
                    return selected[:limit]

        return selected[:limit]

    def _row_content_signature(self, row: Any) -> str:
        text = " ".join(
            str(self._row_value(row, key) or "")
            for key in ("kind", "layer", "content", "summary")
        )
        normalized = re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
        if not normalized:
            return ""
        tokens = [token for token in normalized.split() if not token.isdigit()]
        if len(tokens) < 8:
            return ""
        return " ".join(tokens[:80])

    def _row_evidence_buckets(self, row: Any, *, scoped_to_source: bool) -> list[tuple[str, str]]:
        buckets: list[tuple[str, str]] = []
        capture_id = str(self._row_value(row, "capture_id") or "").strip()
        if capture_id:
            buckets.append(("capture", capture_id))

        provenance = self._json_or_empty(str(self._row_value(row, "provenance_json") or "{}"))
        metadata = provenance.get("record_metadata") if isinstance(provenance.get("record_metadata"), dict) else {}
        if not scoped_to_source:
            source_account_id = str(provenance.get("source_account_id") or "").strip()
            if source_account_id:
                buckets.append(("source_account", source_account_id))
            source = str(self._row_value(row, "source") or provenance.get("source") or "").strip()
            external_id = str(provenance.get("external_id") or "").strip()
            if source and external_id:
                buckets.append(("source_record", f"{source}:{external_id}"))
            for key in (
                "conversation",
                "thread",
                "thread_id",
                "channel",
                "repository",
                "issue",
                "subject",
                "page",
                "document",
                "relative_path",
            ):
                value = str(metadata.get(key) or "").strip()
                if value:
                    buckets.append((key, value.casefold()))
        return buckets

    def _related_memory_rows(
        self,
        conn,
        user_id: str,
        primary_results: list[dict[str, Any]],
        limit: int,
        *,
        kind: str | None,
        layer: str | None,
        sector: str | None,
        source: str | None = None,
        source_account_id: str | None = None,
        metadata_filters: dict[str, Any] | None = None,
        as_of: str | None = None,
        user_settings: dict[str, Any],
    ) -> list[Any]:
        primary_ids = [str(item.get("id") or "") for item in primary_results if item.get("result_type", "memory") == "memory"]
        primary_ids = [value for value in dict.fromkeys(primary_ids) if value]
        if not primary_ids or limit <= 0:
            return []
        primary_placeholders = ",".join("?" for _ in primary_ids)
        filters, params = self._memory_filters(
            user_id,
            user_settings,
            alias="m",
            kind=kind,
            layer=layer,
            sector=sector,
            source=source,
            source_account_id=source_account_id,
            metadata_filters=metadata_filters,
            as_of=as_of,
        )
        filters.append(f"m.id NOT IN ({primary_placeholders})")
        where = " AND ".join(filters)
        return conn.execute(
            f"""
            SELECT
              m.*,
              r.kind AS relation_kind,
              r.weight AS relation_weight,
              CASE
                WHEN r.source_memory_id IN ({primary_placeholders}) THEN r.source_memory_id
                ELSE r.target_memory_id
              END AS related_to_id
            FROM memory_relations r
            JOIN memories m
              ON m.user_id = r.user_id
             AND m.id = CASE
               WHEN r.source_memory_id IN ({primary_placeholders}) THEN r.target_memory_id
               ELSE r.source_memory_id
             END
            WHERE r.user_id = ?
              AND (r.source_memory_id IN ({primary_placeholders}) OR r.target_memory_id IN ({primary_placeholders}))
              AND {where}
            ORDER BY r.weight DESC, m.importance DESC, COALESCE(m.occurred_at, m.captured_at) DESC, m.captured_at DESC
            LIMIT ?
            """,
            [
                *primary_ids,
                *primary_ids,
                user_id,
                *primary_ids,
                *primary_ids,
                *params,
                *primary_ids,
                limit,
            ],
        ).fetchall()

    def _layer_boost(self, row: Any, layer_boosts: dict[str, float]) -> float:
        if not layer_boosts:
            return 0.0
        keys = set(row.keys())
        layer = memory_layer(row["kind"], row["layer"] if "layer" in keys else None)
        return layer_boosts.get(layer, 0.0)

    def _temporal_boost(self, row: Any, prefixes: list[str]) -> float:
        if not prefixes:
            return 0.0
        keys = set(row.keys())
        occurred_at = str(row["occurred_at"] or "") if "occurred_at" in keys else ""
        if not occurred_at:
            return 0.0
        most_specific_length = len(prefixes[0])
        for prefix in (value for value in prefixes if len(value) == most_specific_length):
            if occurred_at.startswith(prefix):
                if len(prefix) >= 10:
                    return 0.055
                if len(prefix) >= 7:
                    return 0.045
                return TEMPORAL_RETRIEVAL_BOOST + 0.004
        return 0.0

    def _source_quality_boost(self, row: Any, source_policies: dict[str, dict[str, Any]]) -> float:
        score = (
            self._citation_quality_boost(row)
            + self._source_type_boost(row)
            + self._trusted_source_boost(row, source_policies)
        )
        return min(SOURCE_QUALITY_RETRIEVAL_BOOST_MAX, score)

    def _recency_boost(self, row: Any, *, now: datetime) -> float:
        age_seconds = _age_seconds(str(self._row_value(row, "captured_at") or ""), now=now)
        if age_seconds is None:
            return 0.0
        age_days = age_seconds / 86400.0
        for max_days, boost in RECENCY_RETRIEVAL_BOOST_STEPS:
            if age_days <= max_days:
                return boost
        return 0.0

    def _importance_boost(self, row: Any) -> float:
        try:
            importance = int(self._row_value(row, "importance") or 0)
        except (TypeError, ValueError):
            return 0.0
        return min(IMPORTANCE_RETRIEVAL_BOOST_MAX, max(0, importance - 2) * IMPORTANCE_RETRIEVAL_BOOST_STEP)

    def _confidence_boost(self, row: Any) -> float:
        confidence = str(self._row_value(row, "confidence") or "").strip().lower()
        return CONFIDENCE_RETRIEVAL_BOOSTS.get(confidence, 0.0)

    def _citation_quality_boost(self, row: Any) -> float:
        source_url = str(self._row_value(row, "source_url") or "").strip()
        if not source_url:
            return 0.0
        score = 0.0015
        lowered = source_url.lower()
        if any(marker in lowered for marker in ("line=", "excerpt=", "message=", "row=", "event=", "subject=", "page=", "document=")):
            score += 0.001
        return score

    def _source_type_boost(self, row: Any) -> float:
        source = str(self._row_value(row, "source") or "")
        source_url = str(self._row_value(row, "source_url") or "").strip() or None
        source_type = str(self._row_value(row, "source_type") or _memory_source_type(source, source_url)).strip().lower()
        return 0.001 if source_type in {"service", "local_file"} else 0.0

    def _trusted_source_boost(self, row: Any, source_policies: dict[str, dict[str, Any]]) -> float:
        score = 0.0
        source = _normalize_source_key(str(self._row_value(row, "source") or ""))
        if source:
            matched_sources = _source_account_alias_sources(source) or {source}
            if any((source_policies.get(candidate) or {}).get("mode") == "trusted" for candidate in matched_sources):
                score += 0.003

        provenance = self._json_or_empty(str(self._row_value(row, "provenance_json") or "{}"))
        account_policy = provenance.get("source_account_policy") if isinstance(provenance.get("source_account_policy"), dict) else {}
        if _normalize_source_key(str(account_policy.get("mode") or "")) == "trusted":
            score += 0.003
        metadata = provenance.get("record_metadata") if isinstance(provenance.get("record_metadata"), dict) else {}
        trusted_values = {
            str(metadata.get(key) or "").strip().lower()
            for key in ("source_quality", "quality", "trust", "trust_level", "verification")
        }
        if any(value in {"trusted", "high", "canonical", "verified"} for value in trusted_values):
            score += 0.002
        if any(bool(metadata.get(key)) for key in ("trusted_source", "verified", "canonical")):
            score += 0.002
        return score

    def _row_value(self, row: Any, key: str, default: Any = None) -> Any:
        try:
            return row[key] if key in row.keys() else default
        except (AttributeError, KeyError, IndexError, TypeError):
            if isinstance(row, dict):
                return row.get(key, default)
            return default

    def _lexical_fallback_terms(self, query: str, *, limit: int = 8) -> list[str]:
        terms: list[str] = []
        seen: set[str] = set()
        for token in re.findall(r"[a-z0-9_]+", str(query or "").lower().replace("'", "")):
            clean = token.strip("_")
            if not clean or clean in QUERY_LEXICAL_FALLBACK_STOPWORDS:
                continue
            normalized_token = clean
            if len(clean) > 3 and clean.endswith("s") and not clean.endswith(("ss", "ies")):
                normalized_token = clean[:-1]
            if len(normalized_token) < 3 or normalized_token in seen:
                continue
            seen.add(normalized_token)
            terms.append(normalized_token)
            if len(terms) >= limit:
                break
        return terms

    def _lexical_matched_terms(self, row: Any, terms: list[str]) -> set[str]:
        text = " ".join(
            str(value or "")
            for value in (
                row["content"],
                row["summary"],
                row["source"],
                row["topics_json"],
            )
        )
        row_terms: set[str] = set()
        for token in re.findall(r"[a-z0-9_]+", text.lower().replace("'", "")):
            clean = token.strip("_")
            if not clean:
                continue
            if len(clean) > 3 and clean.endswith("s") and not clean.endswith(("ss", "ies")):
                clean = clean[:-1]
            row_terms.add(clean)
        return {term for term in terms if any(candidate.startswith(term) for candidate in row_terms)}

    def _query_has_task_intent(self, query: str) -> bool:
        lowered = re.sub(r"\s+", " ", str(query or "").strip().lower())
        if any(phrase in lowered for phrase in TASK_QUERY_PHRASES):
            return True
        tokens = set(re.findall(r"[a-z0-9_]+", lowered.replace("-", "")))
        if tokens & TASK_QUERY_TERMS:
            return True
        if {"what", "do"} <= tokens and ({"need", "next", "pending"} & tokens):
            return True
        return False

    def _task_search_rows(
        self,
        conn,
        user_id: str,
        query: str,
        limit: int,
        kind: str | None,
        user_settings: dict[str, Any],
        *,
        sector: str | None = None,
    ) -> list[Any]:
        task_intent = self._query_has_task_intent(query)
        terms = self._lexical_fallback_terms(query, limit=14)
        meaningful_terms = [term for term in terms if term not in TASK_QUERY_STOPWORDS]
        if kind and kind not in {"action", "question"}:
            return []
        if not task_intent and not meaningful_terms:
            return []

        filters, params = self._task_filters(user_id, user_settings, alias="t", capture_alias="c", kind=kind, sector=sector)
        term_params: list[Any] = []
        if meaningful_terms:
            term_filters = []
            for term in meaningful_terms:
                like = f"%{term}%"
                term_filters.append(
                    "("
                    "lower(COALESCE(t.content, '')) LIKE ? OR "
                    "lower(COALESCE(t.topics_json, '')) LIKE ? OR "
                    "lower(COALESCE(t.entity_ids_json, '')) LIKE ? OR "
                    "lower(COALESCE(c.title, '')) LIKE ? OR "
                    "lower(COALESCE(c.source, '')) LIKE ? OR "
                    "lower(COALESCE(c.raw_text, '')) LIKE ?"
                    ")"
                )
                term_params.extend([like, like, like, like, like, like])
            filters.append(f"({' OR '.join(term_filters)})")
        where = " AND ".join(filters)
        candidate_limit = max(limit * 4, 20)
        rows = conn.execute(
            f"""
            SELECT
              t.*,
              c.source AS capture_source,
              c.source_url AS capture_source_url,
              c.title AS capture_title,
              c.source_account_id AS capture_source_account_id,
              c.external_id AS capture_external_id,
              c.raw_text AS capture_raw_text
            FROM tasks t
            LEFT JOIN captures c ON c.id = t.capture_id AND c.user_id = t.user_id
            WHERE {where}
            ORDER BY t.importance DESC, t.captured_at DESC
            LIMIT ?
            """,
            [*params, *term_params, candidate_limit],
        ).fetchall()
        if not meaningful_terms:
            return rows[:limit]

        ranked: list[dict[str, Any]] = []
        min_matches = 1 if task_intent else min(2, len(meaningful_terms))
        for index, row in enumerate(rows):
            matched = self._task_matched_terms(row, meaningful_terms)
            if len(matched) < min_matches:
                continue
            ranked.append(
                {
                    "row": row,
                    "score": (len(matched) / len(meaningful_terms))
                    + ((row["importance"] or 3) * 0.01)
                    + (0.05 / (60 + index)),
                }
            )
        return [item["row"] for item in sorted(ranked, key=lambda item: item["score"], reverse=True)[:limit]]

    def _task_matched_terms(self, row: Any, terms: list[str]) -> set[str]:
        keys = set(row.keys())
        text = " ".join(
            str(value or "")
            for value in (
                row["content"],
                row["topics_json"],
                row["entity_ids_json"],
                row["kind"],
                row["capture_source"] if "capture_source" in keys else "",
                row["capture_title"] if "capture_title" in keys else "",
                row["capture_raw_text"] if "capture_raw_text" in keys else "",
            )
        )
        row_terms: set[str] = set()
        for token in re.findall(r"[a-z0-9_]+", text.lower().replace("'", "")):
            clean = token.strip("_")
            if not clean:
                continue
            if len(clean) > 3 and clean.endswith("s") and not clean.endswith(("ss", "ies")):
                clean = clean[:-1]
            row_terms.add(clean)
        return {term for term in terms if any(candidate.startswith(term) for candidate in row_terms)}

    def _task_search_result_from_row(self, row: Any) -> dict[str, Any]:
        keys = set(row.keys())
        source = row["capture_source"] if "capture_source" in keys and row["capture_source"] else "task"
        source_url = self._task_source_url_from_row(row)
        provenance = self._task_provenance_from_row(row)
        topics = json.loads(row["topics_json"] or "[]")
        entity_ids = json.loads(row["entity_ids_json"] or "[]")
        return {
            "id": row["id"],
            "capture_id": row["capture_id"] if "capture_id" in keys else None,
            "user_id": row["user_id"] if "user_id" in keys else None,
            "result_type": "task",
            "kind": row["kind"],
            "layer": "task",
            "content": row["content"],
            "summary": row["content"],
            "source": source,
            "source_url": source_url,
            "provenance": provenance,
            "confidence": "confirmed",
            "importance": row["importance"],
            "status": row["status"],
            "topics": topics,
            "entity_ids": entity_ids,
            "occurred_at": None,
            "captured_at": row["captured_at"],
            "updated_at": row["captured_at"],
            "raw_excerpt": row["content"],
        }

    def _task_source_url_from_row(self, row: Any) -> str | None:
        keys = set(row.keys())
        source_url = row["capture_source_url"] if "capture_source_url" in keys else None
        if source_url and "capture_raw_text" in keys:
            return _granular_memory_source_url(
                source_url,
                row["capture_raw_text"] or "",
                row["content"] or "",
                enabled=True,
            )
        return source_url

    def _task_provenance_from_row(self, row: Any) -> dict[str, Any]:
        keys = set(row.keys())
        return {
            key: value
            for key, value in {
                "source_account_id": row["capture_source_account_id"] if "capture_source_account_id" in keys else None,
                "external_id": row["capture_external_id"] if "capture_external_id" in keys else None,
            }.items()
            if value not in (None, "", [], {})
        }

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
        conn.execute(
            f"DELETE FROM memory_relations WHERE user_id = ? AND (source_memory_id IN ({placeholders}) OR target_memory_id IN ({placeholders}))",
            [user_id, *ids, *ids],
        )
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
        return self._redact_local_paths(redacted)

    def _redact_payload(self, value: Any, key: str = "") -> Any:
        if isinstance(value, str):
            if SENSITIVE_KEY_PATTERN.match(str(key or "")):
                return "[REDACTED_SECRET]"
            redacted = value
            for pattern, replacement in SENSITIVE_PATTERNS:
                redacted = pattern.sub(replacement, redacted)
            return self._redact_local_paths(redacted, force_locator=self._is_local_path_value_key(key))
        if isinstance(value, list):
            return [self._redact_payload(item, key) for item in value]
        if isinstance(value, tuple):
            return [self._redact_payload(item, key) for item in value]
        if isinstance(value, dict):
            return {child_key: self._redact_payload(item, str(child_key)) for child_key, item in value.items()}
        return value

    def _shared_payload(self, value: Any, *, redact_sensitive: bool) -> Any:
        if redact_sensitive:
            return self._redact_payload(value)
        return self._redact_local_path_payload(value)

    def public_payload(self, user_id: str, value: Any) -> Any:
        return self._shared_payload(value, redact_sensitive=bool(self.settings(user_id)["redact_sensitive_context"]))

    def _redact_local_path_payload(self, value: Any, key: str = "") -> Any:
        if isinstance(value, str):
            return self._redact_local_paths(value, force_locator=self._is_local_path_value_key(key))
        if isinstance(value, list):
            return [self._redact_local_path_payload(item, key) for item in value]
        if isinstance(value, tuple):
            return [self._redact_local_path_payload(item, key) for item in value]
        if isinstance(value, dict):
            return {child_key: self._redact_local_path_payload(item, str(child_key)) for child_key, item in value.items()}
        return value

    def _shared_text(self, value: str, *, redact_sensitive: bool) -> str:
        if redact_sensitive:
            return self._redact_text(value)
        return self._redact_local_paths(value)

    def _is_local_path_value_key(self, key: str) -> bool:
        normalized = str(key or "").strip().lower()
        return normalized in LOCAL_PATH_VALUE_KEYS or normalized.endswith("_path")

    def _redact_local_paths(self, value: str, *, force_locator: bool = False) -> str:
        if not value:
            return value
        if self._looks_like_local_locator(value, force_local=force_locator):
            return self._safe_source_locator(value, force_local=force_locator)
        if self._looks_like_service_locator(value):
            return self._sanitize_locator_path_components(value)
        return LOCAL_PATH_PATTERN.sub(lambda match: self._safe_source_locator(match.group(0)), value)

    def _looks_like_local_locator(self, value: Any, *, force_local: bool = False) -> bool:
        text = str(value or "").strip()
        if text.startswith("file://") or bool(LOCAL_PATH_PATTERN.match(text)):
            return True
        return force_local and text.startswith("/")

    def _looks_like_service_locator(self, value: Any) -> bool:
        text = str(value or "").strip()
        if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", text):
            return False
        locator_head = re.split(r"[?#]", text, maxsplit=1)[0]
        return not bool(re.search(r"\s", locator_head))

    def _safe_source_locator(self, value: Any, *, force_local: bool = False) -> str:
        text = str(value or "").strip()
        if not text:
            return text
        if not self._looks_like_local_locator(text, force_local=force_local):
            return self._sanitize_locator_path_components(text) if self._looks_like_service_locator(text) else text

        path_text = text
        query = ""
        fragment = ""
        if text.startswith("file://"):
            parts = urlsplit(text)
            path_text = unquote(parts.path or parts.netloc or "")
            query = parts.query
            fragment = parts.fragment
        else:
            path_text, _, fragment = text.partition("#")

        basename = Path(unquote(path_text)).name or "local-source"
        path_hash = hashlib.sha256(os.path.normpath(unquote(path_text)).encode("utf-8")).hexdigest()[:12]
        safe = f"local-file://{quote(basename)}"
        if query:
            safe = f"{safe}?{self._sanitize_locator_parameter_component(query)}"
        if fragment:
            safe_fragment = self._sanitize_locator_parameter_component(fragment)
            safe = f"{safe}#{self._append_locator_parameter(safe_fragment, f'path_hash={path_hash}')}"
        elif query:
            safe = f"{safe}&path_hash={path_hash}"
        else:
            safe = f"{safe}?path_hash={path_hash}"
        return safe

    def _append_locator_parameter(self, value: str, parameter: str) -> str:
        return f"{value}&{parameter}" if value else parameter

    def _sanitize_locator_path_components(self, value: str) -> str:
        parts = urlsplit(value)
        query = self._sanitize_locator_parameter_component(parts.query)
        fragment = self._sanitize_locator_parameter_component(parts.fragment)
        if query == parts.query and fragment == parts.fragment:
            return value
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, fragment))

    def _sanitize_locator_parameter_component(self, value: str) -> str:
        if not value:
            return value
        parts = re.split(r"([&;])", value)
        changed = False
        sanitized_parts: list[str] = []
        for part in parts:
            if part in {"&", ";"}:
                sanitized_parts.append(part)
                continue
            key, separator, raw_value = part.partition("=")
            if separator:
                sanitized_value = self._sanitize_locator_parameter_value(raw_value)
                changed = changed or sanitized_value != raw_value
                sanitized_parts.append(f"{key}{separator}{sanitized_value}")
                continue
            sanitized_value = self._sanitize_locator_parameter_value(part)
            changed = changed or sanitized_value != part
            sanitized_parts.append(sanitized_value)
        return "".join(sanitized_parts) if changed else value

    def _sanitize_locator_parameter_value(self, value: str) -> str:
        if not value:
            return value
        decoded = unquote(value)
        if self._looks_like_local_locator(decoded, force_local=True):
            sanitized = self._safe_source_locator(decoded, force_local=True)
        elif self._looks_like_service_locator(decoded):
            sanitized = self._sanitize_locator_path_components(decoded)
        else:
            sanitized = LOCAL_PATH_PATTERN.sub(lambda match: self._safe_source_locator(match.group(0)), decoded)
        if sanitized == decoded:
            return value
        return quote(sanitized, safe="/:%@")

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

    def _memory_filters(
        self,
        user_id: str,
        user_settings: dict[str, Any],
        *,
        alias: str = "m",
        kind: str | None = None,
        layer: str | None = None,
        sector: str | None = None,
        source: str | None = None,
        source_account_id: str | None = None,
        metadata_filters: dict[str, Any] | None = None,
        as_of: str | None = None,
    ) -> tuple[list[str], list[Any]]:
        filters = [f"{alias}.user_id = ?", f"{alias}.status = 'active'"]
        params: list[Any] = [user_id]
        if kind:
            filters.append(f"{alias}.kind = ?")
            params.append(kind)
        if layer:
            filters.append(f"{alias}.layer = ?")
            params.append(memory_layer(None, layer))
        normalized_sector = _normalize_sector_filter(sector)
        if normalized_sector:
            filters.append(f"lower(COALESCE({alias}.sector, '')) = ?")
            params.append(normalized_sector.lower())
        normalized_source = _normalize_source_key(source or "")
        if normalized_source:
            source_matches = sorted(_retrieval_source_filter_sources(normalized_source))
            filters.append(f"lower(COALESCE({alias}.source, '')) IN ({','.join('?' for _ in source_matches)})")
            params.extend(source_matches)
        normalized_source_account_id = _normalize_retrieval_filter_value(source_account_id, max_length=120)
        if normalized_source_account_id:
            filters.append(
                f"""(
                  EXISTS (
                    SELECT 1
                    FROM captures c_source_scope
                    WHERE c_source_scope.id = {alias}.capture_id
                      AND c_source_scope.user_id = {alias}.user_id
                      AND c_source_scope.source_account_id = ?
                  )
                  OR COALESCE(json_extract({alias}.provenance_json, '$.source_account_id'), '') = ?
                )"""
            )
            params.extend([normalized_source_account_id, normalized_source_account_id])
        for key, value in _normalize_retrieval_metadata_filters(metadata_filters).items():
            paths = RETRIEVAL_METADATA_FILTER_KEYS[key]
            clauses = [f"lower(COALESCE(json_extract({alias}.provenance_json, '$.record_metadata.{path}'), '')) = ?" for path in paths]
            filters.append(f"({' OR '.join(clauses)})")
            params.extend([value.lower()] * len(paths))
        now = _normalize_as_of_filter(as_of) or now_iso()
        filters.append(f"({alias}.valid_from IS NULL OR {alias}.valid_from = '' OR {alias}.valid_from <= ?)")
        params.append(now)
        filters.append(f"({alias}.valid_to IS NULL OR {alias}.valid_to = '' OR {alias}.valid_to > ?)")
        params.append(now)
        filters.append(f"({alias}.superseded_by IS NULL OR {alias}.superseded_by = '')")
        filters.append(
            f"""(
              {alias}.capture_id IS NULL
              OR NOT EXISTS (
                    SELECT 1
                    FROM captures c_review_required
                    JOIN source_accounts sa_review_required
                      ON sa_review_required.id = c_review_required.source_account_id
                     AND sa_review_required.user_id = c_review_required.user_id
                    WHERE c_review_required.id = {alias}.capture_id
                      AND c_review_required.user_id = {alias}.user_id
                      AND COALESCE(json_extract(sa_review_required.policy_json, '$.review_required'), 1) != 0
                  )
                  OR EXISTS (
                    SELECT 1
                    FROM captures c_review_approved
                WHERE c_review_approved.id = {alias}.capture_id
                  AND c_review_approved.user_id = {alias}.user_id
                  AND c_review_approved.review_status = 'approved'
              )
            )"""
        )
        filters.append(
            f"""(
              {alias}.capture_id IS NULL
              OR NOT EXISTS (
                SELECT 1
                FROM captures c_ai_block
                    JOIN source_accounts sa_ai_block
                      ON sa_ai_block.id = c_ai_block.source_account_id
                     AND sa_ai_block.user_id = c_ai_block.user_id
                    WHERE c_ai_block.id = {alias}.capture_id
                      AND c_ai_block.user_id = {alias}.user_id
                      AND COALESCE(json_extract(sa_ai_block.policy_json, '$.allow_ai_context'), 1) = 0
                  )
            )"""
        )
        if not user_settings["allow_pending_in_context"]:
            filters.append(
                f"""(
                  {alias}.capture_id IS NULL
                  OR EXISTS (
                    SELECT 1
                    FROM captures c
                    WHERE c.id = {alias}.capture_id
                      AND c.user_id = {alias}.user_id
                      AND c.review_status = 'approved'
                  )
                  OR EXISTS (
                    SELECT 1
                    FROM captures c_account_review
                    JOIN source_accounts sa_account_review
                      ON sa_account_review.id = c_account_review.source_account_id
                     AND sa_account_review.user_id = c_account_review.user_id
                    WHERE c_account_review.id = {alias}.capture_id
                      AND c_account_review.user_id = {alias}.user_id
                      AND COALESCE(json_extract(sa_account_review.policy_json, '$.review_required'), 1) = 0
                  )
                )"""
            )
        source_policies = _normalize_source_policies(user_settings.get("source_policies"))
        excluded_sources = _source_policy_sources(source_policies, lambda policy: not policy.get("allow_ai_context", True))
        if excluded_sources:
            filters.append(f"{alias}.source NOT IN ({','.join('?' for _ in excluded_sources)})")
            params.extend(excluded_sources)
        review_sources = [
            source
            for source in _source_policy_sources(source_policies, lambda policy: policy.get("review_required"))
            if source not in excluded_sources
        ]
        if review_sources:
            filters.append(
                f"({alias}.source NOT IN ({','.join('?' for _ in review_sources)}) OR {alias}.capture_id IS NULL OR EXISTS (SELECT 1 FROM captures c WHERE c.id = {alias}.capture_id AND c.user_id = {alias}.user_id AND c.review_status = 'approved'))"
            )
            params.extend(review_sources)
        return filters, params

    def _task_filters(
        self,
        user_id: str,
        user_settings: dict[str, Any],
        *,
        alias: str = "t",
        capture_alias: str = "c",
        kind: str | None = None,
        sector: str | None = None,
    ) -> tuple[list[str], list[Any]]:
        filters = [f"{alias}.user_id = ?", f"{alias}.status = 'open'"]
        params: list[Any] = [user_id]
        if kind:
            filters.append(f"{alias}.kind = ?")
            params.append(kind)
        normalized_sector = _normalize_sector_filter(sector)
        if normalized_sector:
            # Tasks have no sector column, so attribute them to a project via the
            # sector of any memory extracted from the same capture. This lets a
            # project-scoped Ask surface that project's open tasks without leaking
            # tasks that belong to other projects.
            filters.append(
                f"""EXISTS (
                  SELECT 1 FROM memories m_sector
                  WHERE m_sector.capture_id = {alias}.capture_id
                    AND m_sector.user_id = {alias}.user_id
                    AND lower(COALESCE(m_sector.sector, '')) = ?
                )"""
            )
            params.append(normalized_sector.lower())
        capture_missing = f"({alias}.capture_id IS NULL OR {capture_alias}.id IS NULL)"
        filters.append(
            f"""(
              {capture_missing}
              OR NOT EXISTS (
                SELECT 1
                FROM source_accounts sa_review_required
                WHERE sa_review_required.id = {capture_alias}.source_account_id
                  AND sa_review_required.user_id = {capture_alias}.user_id
                  AND COALESCE(json_extract(sa_review_required.policy_json, '$.review_required'), 1) != 0
              )
              OR {capture_alias}.review_status = 'approved'
            )"""
        )
        filters.append(
            f"""(
              {capture_missing}
              OR NOT EXISTS (
                SELECT 1
                FROM source_accounts sa_ai_block
                WHERE sa_ai_block.id = {capture_alias}.source_account_id
                  AND sa_ai_block.user_id = {capture_alias}.user_id
                  AND COALESCE(json_extract(sa_ai_block.policy_json, '$.allow_ai_context'), 1) = 0
              )
            )"""
        )
        if not user_settings["allow_pending_in_context"]:
            filters.append(
                f"""(
                  {capture_missing}
                  OR {capture_alias}.review_status = 'approved'
                  OR EXISTS (
                    SELECT 1
                    FROM source_accounts sa_account_review
                    WHERE sa_account_review.id = {capture_alias}.source_account_id
                      AND sa_account_review.user_id = {capture_alias}.user_id
                      AND COALESCE(json_extract(sa_account_review.policy_json, '$.review_required'), 1) = 0
                  )
                )"""
            )
        source_policies = _normalize_source_policies(user_settings.get("source_policies"))
        excluded_sources = _source_policy_sources(source_policies, lambda policy: not policy.get("allow_ai_context", True))
        if excluded_sources:
            filters.append(
                f"({capture_missing} OR {capture_alias}.source NOT IN ({','.join('?' for _ in excluded_sources)}))"
            )
            params.extend(excluded_sources)
        review_sources = [
            source
            for source in _source_policy_sources(source_policies, lambda policy: policy.get("review_required"))
            if source not in excluded_sources
        ]
        if review_sources:
            filters.append(
                f"({capture_missing} OR {capture_alias}.source NOT IN ({','.join('?' for _ in review_sources)}) OR {capture_alias}.review_status = 'approved')"
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

    def _capture_from_row(
        self,
        row,
        conn=None,
        *,
        include_review_preview: bool = False,
        redact_source_urls: bool = False,
    ) -> dict[str, Any]:
        keys = set(row.keys())
        capture = {
            "id": row["id"],
            "import_id": row["import_id"] if "import_id" in keys else None,
            "source_account_id": row["source_account_id"] if "source_account_id" in keys else None,
            "external_id": row["external_id"] if "external_id" in keys else None,
            "source": row["source"],
            "source_url": self._safe_source_locator(row["source_url"], force_local=True) if redact_source_urls else row["source_url"],
            "title": row["title"],
            "summary": row["summary"],
            "review_status": row["review_status"],
            "approved_at": row["approved_at"],
            "archived_at": row["archived_at"],
            "captured_at": row["captured_at"],
            "memory_count": row["memory_count"] if "memory_count" in keys else None,
            "task_count": row["task_count"] if "task_count" in keys else None,
        }
        if include_review_preview and conn is not None:
            capture["preview_memories"] = self._review_memory_preview(conn, row["user_id"], row["id"], redact_source_urls=redact_source_urls)
            capture["preview_tasks"] = self._review_task_preview(conn, row["user_id"], row["id"])
        return capture

    def _review_memory_preview(
        self,
        conn,
        user_id: str,
        capture_id: str,
        limit: int = 5,
        *,
        redact_source_urls: bool = False,
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT id, kind, layer, content, source, source_url, confidence, importance, status, topics_json, entity_ids_json, captured_at
            FROM memories
            WHERE user_id = ? AND capture_id = ? AND status = 'active'
            ORDER BY importance DESC, captured_at DESC, id ASC
            LIMIT ?
            """,
            (user_id, capture_id, limit),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "capture_id": capture_id,
                "result_type": "memory",
                "kind": row["kind"],
                "layer": memory_layer(row["kind"], row["layer"]),
                "content": row["content"],
                "source": row["source"],
                "source_url": self._safe_source_locator(row["source_url"], force_local=True) if redact_source_urls else row["source_url"],
                "confidence": row["confidence"],
                "importance": row["importance"],
                "status": row["status"],
                "topics": json.loads(row["topics_json"] or "[]"),
                "entity_ids": json.loads(row["entity_ids_json"] or "[]"),
                "captured_at": row["captured_at"],
            }
            for row in rows
        ]

    def _review_task_preview(self, conn, user_id: str, capture_id: str, limit: int = 3) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT id, kind, content, status, importance
            FROM tasks
            WHERE user_id = ? AND capture_id = ? AND status = 'open'
            ORDER BY importance DESC, captured_at DESC, id ASC
            LIMIT ?
            """,
            (user_id, capture_id, limit),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "kind": row["kind"],
                "content": row["content"],
                "status": row["status"],
                "importance": row["importance"],
            }
            for row in rows
        ]

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
            "status": _normalize_account_state_key(row["status"]) or "available",
            "auth_state": _normalize_account_state_key(row["auth_state"]) or "not_configured",
            "policy": self._json_or_empty(row["policy_json"]),
            "metadata": self._json_or_empty(row["metadata_json"]),
            "last_sync_at": row["last_sync_at"],
            "last_error": row["last_error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "disconnected_at": row["disconnected_at"],
            "retention": {
                "disconnect_action": "pause_sync",
                "disconnect_retains": ["source_account", "captures", "memories", "sync_cursors", "local_credentials"],
                "disconnect_stops": ["scheduled_sync", "new_remote_reads"],
                "resume_action": "resume_sync",
                "resume_endpoint": f"/v1/source-accounts/{row['id']}/resume",
                "delete_action": "delete_user_data",
                "delete_endpoint": "/v1/user-data?include_backups=true",
            },
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
            "capture_id": row["capture_id"] if "capture_id" in keys else None,
            "user_id": row["user_id"] if "user_id" in keys else None,
            "result_type": "memory",
            "kind": kind,
            "layer": memory_layer(kind, row["layer"] if "layer" in keys else None),
            "content": row["content"],
            "summary": row["summary"],
            "source": row["source"],
            "source_url": row["source_url"],
            "confidence": row["confidence"],
            "importance": row["importance"],
            "status": row["status"],
            "sector": row["sector"] if "sector" in keys else "",
            "source_type": row["source_type"] if "source_type" in keys else "",
            "provenance": self._json_or_empty(row["provenance_json"] if "provenance_json" in keys else "{}"),
            "topics": json.loads(row["topics_json"] or "[]"),
            "entity_ids": json.loads(row["entity_ids_json"] or "[]"),
            "occurred_at": row["occurred_at"],
            "valid_from": row["valid_from"] if "valid_from" in keys else None,
            "valid_to": row["valid_to"] if "valid_to" in keys else None,
            "superseded_by": row["superseded_by"] if "superseded_by" in keys else None,
            "captured_at": row["captured_at"],
            "updated_at": row["updated_at"] if "updated_at" in keys else row["captured_at"],
            "raw_excerpt": row["raw_excerpt"],
        }

    def _find_duplicate_memory(
        self,
        conn,
        user_id: str,
        capture_id: str,
        memory_id: str,
        kind: str,
        layer: str,
        content: str,
        *,
        same_capture_only: bool = False,
    ):
        key = _memory_duplicate_key(content)
        if len(key) < 48:
            return None
        capture_scope = (
            "m.capture_id = ?"
            if same_capture_only
            else """
              (
                m.capture_id = ?
                OR m.capture_id IS NULL
                OR c.review_status = 'approved'
              )
            """
        )
        rows = conn.execute(
            f"""
            SELECT m.*
            FROM memories m
            LEFT JOIN captures c ON c.id = m.capture_id AND c.user_id = m.user_id
            WHERE m.user_id = ?
              AND m.status = 'active'
              AND m.kind = ?
              AND m.layer = ?
              AND m.id != ?
              AND {capture_scope}
            ORDER BY m.captured_at DESC
            LIMIT 250
            """,
            (user_id, kind, layer, memory_id, capture_id),
        ).fetchall()
        for row in rows:
            if _memory_duplicate_key(row["content"]) == key:
                return row
        return None

    def _task_from_row(self, row) -> dict[str, Any]:
        keys = set(row.keys())
        return {
            "id": row["id"],
            "capture_id": row["capture_id"] if "capture_id" in keys else None,
            "result_type": "task",
            "kind": row["kind"],
            "layer": "task",
            "content": row["content"],
            "status": row["status"],
            "importance": row["importance"],
            "topics": json.loads(row["topics_json"] or "[]"),
            "entity_ids": json.loads(row["entity_ids_json"] or "[]"),
            "source": row["capture_source"] if "capture_source" in keys and row["capture_source"] else "task",
            "source_url": self._task_source_url_from_row(row),
            "provenance": self._task_provenance_from_row(row),
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

    def _memories_by_kind(self, user_id: str, kind: str, limit: int, *, sector: str | None = None) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            filters, params = self._memory_filters(user_id, user_settings, alias="m", kind=kind, sector=sector)
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

    def _memories_by_layer(self, user_id: str, layer: str, limit: int, *, include_pending: bool = False, sector: str | None = None) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            user_settings = self._settings(conn, user_id)
            if not include_pending:
                user_settings = {**user_settings, "allow_pending_in_context": False}
            filters, params = self._memory_filters(user_id, user_settings, alias="m", layer=layer, sector=sector)
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
            actions.append(f"Review {pending_count} pending item{'s' if pending_count != 1 else ''}.")
        if open_task_count:
            actions.append(f"Clear or update {open_task_count} follow-up{'s' if open_task_count != 1 else ''}.")
        if captured_today == 0:
            actions.append("Connect one useful source so Cortex has fresh memory to review.")
        if top_topics:
            actions.append(f"Use Cortex's #{top_topics[0]['topic']} memory before your next AI session.")
        if not recent_decisions:
            actions.append("Approve the next important decision so it is easy to retrieve later.")
        return actions[:5]

    def _fts_query(self, query: str) -> str:
        tokens = re.findall(r"[a-z0-9_]+", query.lower().replace("'", ""))
        normalized: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            clean = token.strip("_")
            if not clean or clean in QUERY_FTS_STOPWORDS:
                continue
            normalized_token = clean
            if len(clean) > 3 and clean.endswith("s") and not clean.endswith(("ss", "ies")):
                normalized_token = clean[:-1]
            if normalized_token not in seen:
                seen.add(normalized_token)
                normalized.append(normalized_token + "*")
        return " ".join(normalized)
