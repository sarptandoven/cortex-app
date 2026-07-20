from __future__ import annotations

import csv
import email
import html
import io
import json
import mailbox
import os
import re
import tempfile
import threading
import time
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email import policy
from email.utils import parseaddr
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from .config import APP_BRAND
from .sqlite_runtime import sqlite3


MAX_TEXT_BYTES = 12_000_000
MAX_RECORD_CHARS = 185_000
MAX_RECORD_CHUNK_CHARS = 45_000
MAX_RECORD_CHUNK_LINES = 18
# When a record is split into adjacent chunks (item #22), prepend a bounded tail
# of the previous chunk to the next one. A statement that spans a chunk boundary
# — its opening lands at the end of chunk i and its continuation at the start of
# chunk i+1 — is otherwise split mid-fact and neither chunk carries it whole.
# The overlap re-attaches the trailing complete sentence(s)/line(s) of chunk i to
# the head of chunk i+1 so that at least one chunk contains the boundary fact in
# full. It is small relative to MAX_RECORD_CHUNK_CHARS, so the stitched chunk
# stays far under MAX_RECORD_CHARS and `.bounded()` never truncates — no data is
# dropped; the tail is only duplicated (downstream near-duplicate memory reuse
# collapses the twin extractions).
MAX_RECORD_CHUNK_OVERLAP_CHARS = 600
# Cap on how deep we descend into nested JSON / bookmark trees. Real exports are
# only a handful of levels deep, so 200 never trips on genuine data but stops a
# crafted, deeply-nested import file from stack-overflowing the parser. When the
# cap is hit we stop descending and keep whatever was collected (degrade, never
# raise).
MAX_PARSE_NESTING_DEPTH = 200
CHUNK_MARKERS = {
    "--- messages ---",
    "--- rows ---",
    "--- events ---",
    "--- contacts ---",
    "--- recent visits ---",
}

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".html",
    ".htm",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".xml",
    ".yaml",
    ".yml",
    ".log",
    ".srt",
    ".vtt",
    ".ics",
    ".vcf",
    ".rtf",
}

STRUCTURED_CSV_EXPORT_SOURCES = {
    "notion",
    "linear",
    "jira",
    "asana",
    "trello",
    "github",
    "gitlab",
    "readwise",
    "pocket",
    "instapaper",
    "raindrop",
    "work-tools",
}
STRUCTURED_JSON_EXPORT_SOURCES = {"github", "jira", "linear", "work-tools"}
STRUCTURED_JSON_COLLECTION_KEYS = (
    "issues",
    "pull_requests",
    "pullRequests",
    "pulls",
    "prs",
    "items",
    "nodes",
    "edges",
    "values",
    "results",
    "data",
)
STRUCTURED_ROW_KEY_ORDER = (
    "key",
    "number",
    "identifier",
    "id",
    "title",
    "summary",
    "body",
    "description",
    "text",
    "content",
    "message",
    "state",
    "status",
    "type",
    "url",
    "html_url",
    "web_url",
    "created_at",
    "createdAt",
    "created",
    "updated_at",
    "updatedAt",
    "updated",
    "closed_at",
    "closedAt",
    "merged_at",
    "mergedAt",
    "author",
    "user",
    "creator",
    "reporter",
    "assignee",
    "assignees",
    "labels",
)
STRUCTURED_DISPLAY_KEYS = (
    "name",
    "displayName",
    "display_name",
    "login",
    "username",
    "emailAddress",
    "email",
    "key",
    "id",
    "value",
    "title",
    "summary",
    "text",
)
CONSUMER_AI_TRANSCRIPT_SOURCES: dict[str, str] = {
    "gemini": "Gemini",
    "perplexity": "Perplexity",
    "copilot": "Microsoft Copilot",
    "grok": "Grok",
    "poe": "Poe",
    "notebooklm": "NotebookLM",
}

# Limitless (formerly Rewind) exports its recorded days as "lifelogs": a
# timestamped transcript split into speaker-labelled segments. The realistic
# export shapes we handle:
#   * the Developer-API envelope   {"data": {"lifelogs": [...]}, "meta": {...}}
#   * a bare list of lifelog objects              [ {lifelog}, {lifelog}, ... ]
#   * a single lifelog object                     { "id": ..., "contents": [...] }
#   * JSONL, one lifelog per line (bulk export)
#   * a markdown / text transcript dump (Limitless plugins + legacy Rewind)
# any of the above, optionally inside a .zip. A lifelog node looks like:
#   {"type": "blockquote", "content": "...", "speakerName": "Sarp",
#    "speakerIdentifier": "user"|null, "startTime": "2026-07-01T09:15:00Z", ...}
LIMITLESS_LIFELOG_NODE_TYPES = {"heading1", "heading2", "heading3", "blockquote", "paragraph"}
LIMITLESS_MARKDOWN_MARKERS = ("limitless", "lifelog", "rewind")


SUPPORTED_SOURCES: list[dict[str, Any]] = [
    {
        "id": "chatgpt",
        "name": "ChatGPT",
        "formats": ["OpenAI data export zip", "conversations.json"],
        "status": "native",
    },
    {
        "id": "claude",
        "name": "Claude",
        "formats": ["Claude export zip", "conversations.json"],
        "status": "native",
    },
    {
        "id": "gemini",
        "name": "Gemini",
        "formats": ["transcript.json", "JSON/JSONL/TXT/Markdown transcript"],
        "status": "generic",
    },
    {
        "id": "perplexity",
        "name": "Perplexity",
        "formats": ["transcript.json", "JSON/JSONL/TXT/Markdown transcript"],
        "status": "generic",
    },
    {
        "id": "copilot",
        "name": "Microsoft Copilot",
        "formats": ["transcript.json", "JSON/JSONL/TXT/Markdown transcript"],
        "status": "generic",
    },
    {
        "id": "grok",
        "name": "Grok",
        "formats": ["transcript.json", "JSON/JSONL/TXT/Markdown transcript"],
        "status": "generic",
    },
    {
        "id": "poe",
        "name": "Poe",
        "formats": ["transcript.json", "JSON/JSONL/TXT/Markdown transcript"],
        "status": "generic",
    },
    {
        "id": "notebooklm",
        "name": "NotebookLM",
        "formats": ["transcript.json", "JSON/JSONL/TXT/Markdown transcript"],
        "status": "generic",
    },
    {
        "id": "limitless",
        "name": "Limitless / Rewind",
        "formats": [
            "Limitless lifelogs export (lifelogs.json / .jsonl / API envelope)",
            "Limitless or Rewind markdown/text transcript dump",
            "Export .zip containing any of the above",
        ],
        "status": "native",
    },
    {
        "id": "notion",
        "name": "Notion",
        "formats": ["Markdown + CSV export", "HTML export"],
        "status": "native",
    },
    {
        "id": "email",
        "name": "Email",
        "formats": ["mbox", "eml", "emlx"],
        "status": "native",
    },
    {
        "id": "slack",
        "name": "Slack",
        "formats": ["Workspace export zip"],
        "status": "native",
    },
    {
        "id": "discord",
        "name": "Discord",
        "formats": ["Data package messages.csv"],
        "status": "native",
    },
    {
        "id": "telegram",
        "name": "Telegram",
        "formats": ["Telegram Desktop result.json"],
        "status": "native",
    },
    {
        "id": "google-keep",
        "name": "Google Keep",
        "formats": ["Google Takeout Keep JSON/HTML"],
        "status": "native",
    },
    {
        "id": "google-chat",
        "name": "Google Chat and Hangouts",
        "formats": ["Google Takeout Chat/Hangouts messages.json"],
        "status": "native",
    },
    {
        "id": "teams",
        "name": "Microsoft Teams",
        "formats": ["Teams JSON or CSV message exports"],
        "status": "native",
    },
    {
        "id": "zoom",
        "name": "Zoom transcripts",
        "formats": ["Zoom .vtt and .srt transcripts"],
        "status": "native",
    },
    {
        "id": "messages",
        "name": "Messages",
        "formats": ["iMessage chat.db copy", "WhatsApp text export"],
        "status": "native",
    },
    {
        "id": "docs",
        "name": "Docs and writing",
        "formats": ["Markdown", "text", "HTML", "DOCX", "RTF", "PDF when pypdf is installed"],
        "status": "native",
    },
    {
        "id": "browser-bookmarks",
        "name": "Browser bookmarks and research",
        "formats": ["Chrome/Safari/Firefox Netscape HTML bookmarks", "Chrome/Edge Bookmarks JSON", "Chrome/Firefox browser history SQLite", "Raindrop/Readwise/Pocket/Instapaper CSV or JSON exports"],
        "status": "native",
    },
    {
        "id": "calendar",
        "name": "Calendar",
        "formats": ["Google Calendar, Apple Calendar, Outlook .ics exports"],
        "status": "native",
    },
    {
        "id": "contacts",
        "name": "Contacts",
        "formats": ["Apple Contacts, Google Contacts, Outlook vCard .vcf exports"],
        "status": "native",
    },
    {
        "id": "twitter-x",
        "name": "Twitter/X",
        "formats": ["Twitter/X archive tweets.js and direct-messages.js"],
        "status": "native",
    },
    {
        "id": "linkedin",
        "name": "LinkedIn",
        "formats": ["LinkedIn data export Messages.csv and Connections.csv"],
        "status": "native",
    },
    {
        "id": "apple-notes",
        "name": "Apple Notes and writing apps",
        "formats": ["Apple Notes HTML/RTF/PDF/text exports", "Bear, Craft, Ulysses, iA Writer Markdown or text exports"],
        "status": "generic",
    },
    {
        "id": "cloud-docs",
        "name": "Cloud docs",
        "formats": ["Google Drive/Docs Takeout", "Microsoft 365/OneDrive/Outlook exports", "Dropbox Paper exports"],
        "status": "generic",
    },
    {
        "id": "work-tools",
        "name": "Work tools",
        "formats": ["Jira/Linear/GitHub CSV or JSON exports"],
        "status": "generic",
    },
    {
        "id": "github",
        "name": "GitHub",
        "formats": ["GitHub issue, pull request, project, CSV, JSON, Markdown, and text exports"],
        "status": "generic",
    },
    {
        "id": "linear",
        "name": "Linear",
        "formats": ["Linear CSV and JSON exports"],
        "status": "generic",
    },
    {
        "id": "jira",
        "name": "Jira",
        "formats": ["Jira CSV and JSON exports"],
        "status": "generic",
    },
    {
        "id": "knowledge-base",
        "name": "Knowledge bases",
        "formats": ["Markdown notes", "Roam", "Logseq", "Readwise, Raindrop, Zotero, Pocket, Instapaper CSV/JSON"],
        "status": "generic",
    },
]


@dataclass
class SourceAsset:
    name: str
    display_path: str
    data: bytes | None = None
    filesystem_path: Path | None = None
    # Set when the underlying file was larger than MAX_TEXT_BYTES and only the
    # first window could be read. Surfaced on records as `source_file_truncated`.
    read_truncated: bool = False

    @property
    def suffix(self) -> str:
        return Path(self.name).suffix.lower()

    def read_bytes(self) -> bytes:
        if self.data is not None:
            return self.data
        if self.filesystem_path is None:
            return b""
        if self.filesystem_path.stat().st_size > MAX_TEXT_BYTES:
            # A file past the byte cap used to be treated as empty — silent
            # whole-file loss. Read the first MAX_TEXT_BYTES instead and mark
            # the asset truncated so downstream records can surface it.
            # (`read_text` decodes with errors-replace, so a cut multibyte
            # character at the boundary is safe.)
            self.read_truncated = True
            with self.filesystem_path.open("rb") as handle:
                return handle.read(MAX_TEXT_BYTES)
        return self.filesystem_path.read_bytes()

    def read_text(self) -> str:
        data = self.read_bytes()
        for encoding in ("utf-8", "utf-8-sig", "iso-8859-1"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")


@dataclass
class SourceRecord:
    source: str
    title: str
    content: str
    source_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def bounded(self) -> "SourceRecord":
        if len(self.content) <= MAX_RECORD_CHARS:
            return self
        return SourceRecord(
            source=self.source,
            title=self.title,
            content=self.content[:MAX_RECORD_CHARS] + f"\n\n[Truncated by {APP_BRAND} importer.]",
            source_url=self.source_url,
            metadata={**self.metadata, "truncated": True},
        )


def supported_sources() -> list[dict[str, Any]]:
    return SUPPORTED_SOURCES


def analyze_sources(paths: Iterable[str], source_hint: str = "", max_records: int = 500) -> dict[str, Any]:
    records = list(import_source_records(paths, source_hint=source_hint, max_records=max_records))
    counts: dict[str, int] = {}
    for record in records:
        counts[record.source] = counts.get(record.source, 0) + 1
    return {
        "records_found": len(records),
        "sources": [{"source": source, "count": count} for source, count in sorted(counts.items())],
        "sample": [
            {
                "source": record.source,
                "title": record.title,
                "chars": len(record.content),
                "metadata": record.metadata,
                "source_url": record.source_url,
            }
            for record in records[:12]
        ],
        "supported_sources": supported_sources(),
    }


# Filenames/patterns that mark a file as an AI-chat or app data export worth probing.
_EXPORT_JSON_NAMES = {"conversations.json", "conversations.jsonl", "result.json", "messages.json",
                      "lifelogs.json", "lifelogs.jsonl", "limitless.json"}
_EXPORT_ZIP_HINTS = ("chatgpt", "claude", "openai", "anthropic", "gemini", "notebooklm",
                     "export", "conversations", "takeout", "slack", "discord", "telegram",
                     "limitless", "lifelog", "rewind")
# Member basenames that mark a .zip as an AI-chat export even when the archive's
# filename is a hash with no hint token. Checked via namelist() (central
# directory only — no extraction), so a real ChatGPT/Claude export downloaded
# with an opaque name is still surfaced instead of silently skipped.
_EXPORT_ZIP_CONTENT_MEMBERS = {"conversations.json", "conversations.jsonl", "chats.json"}
# Cost guards for the content peek: never open an archive larger than this, and
# inspect at most this many hint-less .zip files per scan.
_EXPORT_ZIP_PEEK_MAX_BYTES = 600_000_000
_EXPORT_ZIP_PEEK_MAX_COUNT = 12


def scan_export_candidates(directories: Iterable[str], *, max_files: int = 60, max_records: int = 200) -> list[dict[str, Any]]:
    """Shallow-scan folders (e.g. ~/Downloads) for files that look like an AI-chat / app data
    export — an OpenAI/Claude export .zip, a `conversations.json`, or an unzipped export folder —
    run the importer's analysis on each, and return only the ones that actually parse into
    records: {path, filename, service, records_found, sources, kind}. So the app can say
    "found your ChatGPT export (142 conversations) in Downloads" instead of asking you to hunt
    for the file. Non-recursive beyond one nested level; skips unreadable entries."""
    candidates: list[Path] = []
    zip_peeks = 0
    for directory in directories:
        try:
            base = Path(directory).expanduser()
        except (OSError, ValueError):
            continue
        if not base.is_dir():
            continue
        try:
            entries = sorted(base.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for entry in entries:
            if len(candidates) >= max_files:
                break
            name = entry.name.lower()
            try:
                if entry.is_file():
                    if name in _EXPORT_JSON_NAMES:
                        candidates.append(entry)
                    elif name.endswith(".zip"):
                        if any(h in name for h in _EXPORT_ZIP_HINTS):
                            candidates.append(entry)
                        elif zip_peeks < _EXPORT_ZIP_PEEK_MAX_COUNT:
                            # Filename gave no hint — peek inside for a known export
                            # member so a hash-named ChatGPT/Claude export isn't
                            # silently skipped. Bounded in count + archive size.
                            zip_peeks += 1
                            if _zip_has_export_member(entry):
                                candidates.append(entry)
                elif entry.is_dir() and any(h in name for h in _EXPORT_ZIP_HINTS):
                    # an already-unzipped export folder — grab its conversations.json if present
                    for sub in entry.iterdir():
                        if sub.is_file() and sub.name.lower() in _EXPORT_JSON_NAMES:
                            candidates.append(sub)
                            break
            except OSError:
                continue

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            resolved = str(path.resolve())
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            analysis = analyze_sources([resolved], max_records=max_records)
        except Exception:
            continue
        found = int(analysis.get("records_found") or 0)
        if found <= 0:
            continue
        sources = analysis.get("sources") or []
        service = str(sources[0]["source"]) if sources else "unknown"
        results.append({
            "path": resolved,
            "filename": path.name,
            "service": service,
            "records_found": found,
            "sources": sources,
            "kind": "zip" if path.suffix.lower() == ".zip" else "file",
        })
    return results


def _zip_has_export_member(path: Path) -> bool:
    """Cheap content sniff: does this .zip hold a known AI-chat export member
    (conversations.json / conversations.jsonl / chats.json)? Reads only the
    archive's central directory via namelist() — never extracts — so it stays fast
    even on a large export. Oversized, unreadable, or corrupt archives are skipped
    silently (return False)."""
    try:
        if path.stat().st_size > _EXPORT_ZIP_PEEK_MAX_BYTES:
            return False
    except OSError:
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            for member in archive.namelist():
                if Path(member).name.lower() in _EXPORT_ZIP_CONTENT_MEMBERS:
                    return True
    except (OSError, zipfile.BadZipFile, RuntimeError):
        return False
    return False


def default_export_scan_dirs() -> list[str]:
    """The folders Cortex looks in for exports by default: the user's Downloads and Desktop, plus
    a dedicated ~/CortexImports drop folder the app can create and point users at."""
    home = Path.home()
    return [str(home / "Downloads"), str(home / "Desktop"), str(home / "CortexImports")]


def _parsed_source_records(paths: Iterable[str], source_hint: str = "") -> list[SourceRecord]:
    """Parse + chunk every source record from the given paths (no cap/order applied)."""
    assets = _collect_assets(paths)
    hint = _normalize_source(source_hint)
    records: list[SourceRecord] = []

    for parser in (
        _parse_chatgpt,
        _parse_gemini_takeout,
        _parse_consumer_ai_transcripts,
        _parse_limitless,
        _parse_claude,
        _parse_notion,
        _parse_slack,
        _parse_discord,
        _parse_telegram,
        _parse_google_chat,
        _parse_teams,
        _parse_zoom_transcripts,
        _parse_google_keep,
        _parse_twitter_archive,
        _parse_linkedin,
    ):
        parsed = parser(assets, hint)
        if parsed:
            records.extend(parsed)

    consumed = {_record_asset_key(record) for record in records if _record_asset_key(record)}
    for asset in assets:
        if asset.display_path in consumed:
            continue
        parsed = _parse_single_asset(asset, hint)
        if parsed:
            records.extend(parsed)

    # Surface truncated reads (files past MAX_TEXT_BYTES) on every record that
    # came from such an asset, so the loss is observable instead of silent.
    # `read_truncated` is set lazily by `read_bytes`, so check after parsing.
    truncated_paths = {asset.display_path for asset in assets if asset.read_truncated}
    if truncated_paths:
        # Records key their asset by display_path (see `_record_asset_key`);
        # per-item parsers like mbox suffix it with `#<index>`.
        truncated_prefixes = tuple(f"{path}#" for path in truncated_paths)
        for record in records:
            key = _record_asset_key(record)
            if key and (key in truncated_paths or key.startswith(truncated_prefixes)):
                record.metadata = {**record.metadata, "source_file_truncated": True}

    expanded: list[SourceRecord] = []
    for record in records:
        if not record.content.strip():
            continue
        for chunk in _chunk_source_record(record):
            _annotate_occurred_at(chunk)
            expanded.append(chunk)
    return expanded


def import_source_records(paths: Iterable[str], source_hint: str = "", max_records: int = 1000) -> list[SourceRecord]:
    record_limit = max(1, int(max_records or 1000))
    expanded = _parsed_source_records(paths, source_hint)
    return [record.bounded() for record in _source_fair_record_cap(expanded, record_limit)]


def _source_fair_order(records: list[SourceRecord]) -> list[SourceRecord]:
    """Stable, source-fair total order over all records (round-robin across
    sources). A single source keeps parse order. This ordering is consistent
    regardless of window, so paginating with an offset never skips or reorders
    records between pages."""
    buckets: dict[str, list[SourceRecord]] = {}
    for record in records:
        buckets.setdefault(record.source, []).append(record)
    if len(buckets) <= 1:
        return list(records)
    ordered: list[SourceRecord] = []
    bucket_lists = list(buckets.values())
    while any(bucket_lists):
        for bucket in bucket_lists:
            if bucket:
                ordered.append(bucket.pop(0))
    return ordered


# Parsing a large export is expensive (unzip + run every parser + chunk). The
# import layer paginates by calling `import_source_records_page` once per window,
# so a naive implementation re-parses the whole export on every page (O(pages^2)).
# We parse once and serve every window from a small, bounded, process-wide cache
# keyed on the resolved inputs so a huge export actually finishes.
_PARSE_CACHE_LOCK = threading.Lock()
_PARSE_CACHE: "OrderedDict[Any, tuple[float, list[SourceRecord]]]" = OrderedDict()
_PARSE_CACHE_MAX_ENTRIES = 2
_PARSE_CACHE_TTL_SECONDS = 300.0


def _parse_cache_key(paths: list[str], source_hint: str) -> Any:
    """Fingerprint the resolved inputs so a re-downloaded/edited export (changed
    mtime or size) invalidates automatically. Never key on path alone."""
    fingerprint: list[tuple[str, int | None, int | None]] = []
    for path in paths:
        try:
            st = os.stat(path)
        except OSError:
            # Missing/unreadable path: fold in the raw realpath so its later
            # appearance or disappearance still changes the key.
            fingerprint.append((os.path.realpath(path), None, None))
            continue
        fingerprint.append((os.path.realpath(path), st.st_mtime_ns, st.st_size))
    return (tuple(sorted(fingerprint)), _normalize_source(source_hint))


def _ordered_source_records_cached(paths: Iterable[str], source_hint: str) -> list[SourceRecord]:
    """Return the source-fair ordered record list, parsing once per import and
    serving repeated paginated calls from a bounded cache. The ordered list is
    independent of offset/max_records, so one cached list serves every page."""
    resolved = list(paths)
    key = _parse_cache_key(resolved, source_hint)
    with _PARSE_CACHE_LOCK:
        entry = _PARSE_CACHE.get(key)
        if entry is not None and time.monotonic() - entry[0] <= _PARSE_CACHE_TTL_SECONDS:
            _PARSE_CACHE.move_to_end(key)
            return entry[1]
    # Parse outside the lock so concurrent imports of different exports do not
    # serialize on one slow parse.
    ordered = _source_fair_order(_parsed_source_records(resolved, source_hint))
    stored_at = time.monotonic()
    with _PARSE_CACHE_LOCK:
        _PARSE_CACHE[key] = (stored_at, ordered)
        _PARSE_CACHE.move_to_end(key)
        # Drop entries that outlived the TTL, then bound the count with LRU
        # eviction so these (potentially very large) lists never accumulate.
        for stale_key in [k for k, (ts, _) in _PARSE_CACHE.items() if stored_at - ts > _PARSE_CACHE_TTL_SECONDS]:
            _PARSE_CACHE.pop(stale_key, None)
        while len(_PARSE_CACHE) > _PARSE_CACHE_MAX_ENTRIES:
            _PARSE_CACHE.popitem(last=False)
    return ordered


def import_source_records_page(
    paths: Iterable[str],
    source_hint: str = "",
    max_records: int = 1000,
    offset: int = 0,
) -> dict[str, Any]:
    """Import a resumable window of records so huge exports import fully across
    calls instead of silently dropping everything past the cap. Returns the
    windowed records plus `total`/`offset`/`returned`/`has_more`/`next_offset`."""
    record_limit = max(1, int(max_records or 1000))
    start = max(0, int(offset or 0))
    ordered = _ordered_source_records_cached(paths, source_hint)
    total = len(ordered)
    window = [record.bounded() for record in ordered[start : start + record_limit]]
    consumed = start + len(window)
    has_more = consumed < total
    return {
        "records": window,
        "total": total,
        "offset": start,
        "returned": len(window),
        "has_more": has_more,
        "next_offset": consumed if has_more else None,
    }


def _source_fair_record_cap(records: list[SourceRecord], max_records: int) -> list[SourceRecord]:
    if max_records <= 0:
        return []
    if len(records) <= max_records:
        return records

    buckets: dict[str, list[SourceRecord]] = {}
    for record in records:
        buckets.setdefault(record.source, []).append(record)
    if len(buckets) <= 1:
        return records[:max_records]

    selected: list[SourceRecord] = []
    while len(selected) < max_records:
        added = False
        for bucket in buckets.values():
            if not bucket:
                continue
            selected.append(bucket.pop(0))
            added = True
            if len(selected) >= max_records:
                break
        if not added:
            break
    return selected


def _chunk_source_record(record: SourceRecord) -> list[SourceRecord]:
    lines = record.content.splitlines()
    body_start = _chunk_body_start(lines)
    if body_start is None:
        # Free-form documents (a book, transcript, Notion page, Apple Note, .md /
        # .docx / .pdf) carry no chunk marker, so they would otherwise land as one
        # record and get hard-truncated at MAX_RECORD_CHARS by `.bounded()`. Split
        # a genuinely large one into `(part i/n)` records so the whole body is
        # indexed instead of dropped. Small docs stay byte-identical.
        if len(record.content) > MAX_RECORD_CHUNK_CHARS:
            return _chunk_plain_document(record)
        return [record]
    header = lines[:body_start]
    body = [line for line in lines[body_start:] if line.strip()]
    if len(body) <= MAX_RECORD_CHUNK_LINES and len(record.content) <= MAX_RECORD_CHUNK_CHARS:
        return [record]

    grouped = _group_chunk_body_lines(body)
    raw_chunks: list[list[str]] = []
    current: list[str] = []
    current_chars = 0
    for group in grouped:
        group_chars = sum(len(line) + 1 for line in group)
        if current and (len(current) + len(group) > MAX_RECORD_CHUNK_LINES or current_chars + group_chars > MAX_RECORD_CHUNK_CHARS):
            raw_chunks.append(current)
            current = []
            current_chars = 0
        current.extend(group)
        current_chars += group_chars
    if current:
        raw_chunks.append(current)
    if len(raw_chunks) <= 1:
        return [record]

    chunk_count = len(raw_chunks)
    bodies = _stitch_chunk_overlap(["\n".join(chunk_lines) for chunk_lines in raw_chunks])
    chunks: list[SourceRecord] = []
    for index, body in enumerate(bodies, start=1):
        metadata = {**record.metadata, "chunk_index": index, "chunk_count": chunk_count}
        if index > 1:
            metadata["chunk_overlap"] = True
        chunks.append(
            SourceRecord(
                source=record.source,
                title=f"{record.title} (part {index}/{chunk_count})",
                content="\n".join([*header, body]) if header else body,
                source_url=_source_url_with_chunk(record.source_url, index),
                metadata=metadata,
            )
        )
    return chunks


def _chunk_plain_document(record: SourceRecord) -> list[SourceRecord]:
    """Split a long marker-less document into `(part i/n)` records, each bounded to
    MAX_RECORD_CHUNK_CHARS so `.bounded()` never truncates a part. Output shape
    matches the marker-based chunk path so retrieval/citation behaves identically."""
    units = _split_document_units(record.content)
    parts: list[str] = []
    current: list[str] = []
    current_chars = 0
    for unit in units:
        unit_chars = len(unit) + 2  # account for the "\n\n" join separator
        if current and current_chars + unit_chars > MAX_RECORD_CHUNK_CHARS:
            parts.append("\n\n".join(current))
            current = []
            current_chars = 0
        current.append(unit)
        current_chars += unit_chars
    if current:
        parts.append("\n\n".join(current))
    if len(parts) <= 1:
        return [record]
    chunk_count = len(parts)
    parts = _stitch_chunk_overlap(parts)
    chunks: list[SourceRecord] = []
    for index, part_content in enumerate(parts, start=1):
        metadata = {**record.metadata, "chunk_index": index, "chunk_count": chunk_count}
        if index > 1:
            metadata["chunk_overlap"] = True
        chunks.append(
            SourceRecord(
                source=record.source,
                title=f"{record.title} (part {index}/{chunk_count})",
                content=part_content,
                source_url=_source_url_with_chunk(record.source_url, index),
                metadata=metadata,
            )
        )
    return chunks


def _split_document_units(content: str) -> list[str]:
    """Break a document into units no larger than MAX_RECORD_CHUNK_CHARS, splitting
    on paragraph boundaries first, then line boundaries, then fixed-size windows as
    a last resort so even a single pathological long line is never dropped."""
    units: list[str] = []
    for paragraph in re.split(r"\n[ \t]*\n", content):
        if not paragraph.strip():
            continue
        if len(paragraph) <= MAX_RECORD_CHUNK_CHARS:
            units.append(paragraph)
            continue
        for line in paragraph.splitlines():
            if not line.strip():
                continue
            if len(line) <= MAX_RECORD_CHUNK_CHARS:
                units.append(line)
                continue
            for start in range(0, len(line), MAX_RECORD_CHUNK_CHARS):
                units.append(line[start : start + MAX_RECORD_CHUNK_CHARS])
    return units


def _stitch_chunk_overlap(bodies: list[str]) -> list[str]:
    """Prepend a bounded tail of each chunk to the next (item #22).

    Given the ordered *body* strings of adjacent chunks, return new bodies where
    every chunk after the first is prefixed with the trailing complete
    sentence(s)/line(s) of its predecessor. A fact spanning the boundary — begun
    at the end of chunk i, continued at the start of chunk i+1 — then appears
    whole inside chunk i+1 instead of being split mid-fact. Single-chunk inputs
    are returned untouched so small documents stay byte-identical. Never drops
    text: the tail is duplicated, not moved."""
    if len(bodies) <= 1:
        return bodies
    stitched = [bodies[0]]
    for index in range(1, len(bodies)):
        tail = _overlap_tail(bodies[index - 1], MAX_RECORD_CHUNK_OVERLAP_CHARS)
        current = bodies[index]
        # Guard against re-duplicating a tail the next chunk already opens with
        # (e.g. a repeated boundary line), and never overlap an empty body.
        if tail and current.strip() and not current.startswith(tail):
            stitched.append(f"{tail}\n{current}")
        else:
            stitched.append(current)
    return stitched


def _overlap_tail(text: str, limit: int) -> str:
    """Return the trailing <=`limit` chars of `text`, cut back to a clean
    sentence/line boundary so the overlap carries whole trailing statements
    rather than a dangling fragment. Returns "" for blank input."""
    text = text.rstrip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    window = text[-limit:]
    # Prefer to start the window right after the earliest sentence terminator or
    # line break inside it, dropping the leading partial fragment so we keep only
    # complete trailing sentences/lines.
    boundary = re.search(r"[.!?](?=\s)|\n", window)
    if boundary:
        candidate = window[boundary.end():].lstrip()
        if candidate:
            return candidate
    # No interior boundary: fall back to a word boundary so we never split a word.
    space = window.find(" ")
    if 0 <= space < len(window) - 1:
        return window[space + 1:].lstrip()
    return window


# --- Item #19: relative / anchored date resolution ------------------------------
# Episodic records routinely say "yesterday", "last Tuesday", "three weeks ago"
# instead of a calendar date. Resolved against the record's own anchor timestamp
# (its capture / creation time), these become a concrete `occurred_at` so the
# memory lands at a real timeline position instead of only its ingest time. The
# resolver is deliberately conservative: it fires only on unambiguous phrases and
# returns None otherwise (never a wild guess). It is pure and deterministic —
# same text + same anchor always yields the same ISO date — so it does not
# perturb the hash-embedder path. captured_at itself is never modified here; we
# only derive an additional occurred_at.

_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12,
}
_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
    "saturday": 5, "sunday": 6,
}
_WEEKDAY_PATTERN = "|".join(_WEEKDAYS)
_NUMBER_PATTERN = r"\d+|" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
_UNIT_PATTERN = r"day|days|week|weeks|month|months|year|years|hour|hours|minute|minutes"
# Anchor timestamps live under a handful of well-known metadata keys emitted by
# the parsers above; the first that parses wins.
_ANCHOR_METADATA_KEYS = (
    "lifelog_start", "captured_at", "occurred_at", "created_at", "createdAt",
    "created", "timestamp", "date", "time",
)


def _days_in_month(year: int, month: int) -> int:
    first_next = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return (first_next - timedelta(days=1)).day


def _shift_months(anchor: datetime, months: int) -> datetime:
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    day = min(anchor.day, _days_in_month(year, month))
    return anchor.replace(year=year, month=month, day=day)


def _relative_number(token: str) -> int | None:
    token = token.strip().lower()
    if token.isdigit():
        value = int(token)
        return value if value > 0 else None
    return _NUMBER_WORDS.get(token)


def _coerce_anchor(anchor: Any) -> datetime | None:
    if isinstance(anchor, datetime):
        return anchor
    text = str(anchor or "").strip()
    if not text:
        return None
    candidate = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        pass
    # Fall back to the leading YYYY-MM-DD (handles "2026-07-01 09:12:00 UTC" etc.).
    match = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


def resolve_occurred_at(text: str, anchor: Any) -> str | None:
    """Resolve the first unambiguous relative/anchored date phrase in `text`
    against `anchor` (an ISO string or datetime — the record's captured/anchor
    time) into a concrete ``YYYY-MM-DD`` occurred_at. Returns None when nothing
    parses confidently; never guesses. Pure and deterministic."""
    base = _coerce_anchor(anchor)
    if not base or not text:
        return None
    lowered = text.lower()

    # (regex, handler) — every match is scored by its start position; the
    # earliest temporal reference in the text wins, so a leading "yesterday"
    # beats a later "last month". Longer phrases are searched too; because they
    # start earlier in the string than the shorter word nested inside them
    # ("the day before yesterday" vs "yesterday"), earliest-start naturally
    # prefers the more specific reading.
    def _delta_days(match: "re.Match[str]", sign: int) -> datetime | None:
        number = _relative_number(match.group("num"))
        if number is None:
            return None
        unit = match.group("unit").rstrip("s")
        if unit in ("day", "week", "hour", "minute"):
            if not (1 <= number <= 3650):
                return None
            kwargs = {
                "day": {"days": number},
                "week": {"weeks": number},
                "hour": {"hours": number},
                "minute": {"minutes": number},
            }[unit]
            return base + sign * timedelta(**kwargs)
        if unit == "month":
            if not (1 <= number <= 120):
                return None
            return _shift_months(base, sign * number)
        if unit == "year":
            if not (1 <= number <= 100):
                return None
            return _shift_months(base, sign * number * 12)
        return None

    handlers: list[tuple[str, Any]] = [
        (r"\bthe day before yesterday\b", lambda m: base - timedelta(days=2)),
        (r"\bthe day after tomorrow\b", lambda m: base + timedelta(days=2)),
        (r"\byesterday\b", lambda m: base - timedelta(days=1)),
        (r"\btomorrow\b", lambda m: base + timedelta(days=1)),
        (r"\b(?:today|this morning|this afternoon|this evening|tonight)\b", lambda m: base),
        (
            rf"\b(?P<num>{_NUMBER_PATTERN})\s+(?P<unit>{_UNIT_PATTERN})\s+ago\b",
            lambda m: _delta_days(m, -1),
        ),
        (
            rf"\b(?:in|after)\s+(?P<num>{_NUMBER_PATTERN})\s+(?P<unit>{_UNIT_PATTERN})\b",
            lambda m: _delta_days(m, 1),
        ),
        (
            rf"\b(?P<num>{_NUMBER_PATTERN})\s+(?P<unit>{_UNIT_PATTERN})\s+(?:from now|from today|later)\b",
            lambda m: _delta_days(m, 1),
        ),
        (r"\blast\s+week\b", lambda m: base - timedelta(weeks=1)),
        (r"\blast\s+month\b", lambda m: _shift_months(base, -1)),
        (r"\blast\s+year\b", lambda m: _shift_months(base, -12)),
        (r"\bnext\s+week\b", lambda m: base + timedelta(weeks=1)),
        (r"\bnext\s+month\b", lambda m: _shift_months(base, 1)),
        (r"\bnext\s+year\b", lambda m: _shift_months(base, 12)),
        (
            rf"\blast\s+(?P<wd>{_WEEKDAY_PATTERN})\b",
            lambda m: _relative_weekday(base, m.group("wd"), direction=-1),
        ),
        (
            rf"\bnext\s+(?P<wd>{_WEEKDAY_PATTERN})\b",
            lambda m: _relative_weekday(base, m.group("wd"), direction=1),
        ),
    ]

    best_start: int | None = None
    best_dt: datetime | None = None
    for pattern, handler in handlers:
        match = re.search(pattern, lowered)
        if not match:
            continue
        resolved = handler(match)
        if resolved is None:
            continue
        if best_start is None or match.start() < best_start:
            best_start = match.start()
            best_dt = resolved
    if best_dt is None:
        return None
    return best_dt.date().isoformat()


def _relative_weekday(anchor: datetime, weekday: str, direction: int) -> datetime | None:
    target = _WEEKDAYS.get(weekday.lower())
    if target is None:
        return None
    if direction < 0:
        delta = (anchor.weekday() - target) % 7 or 7
        return anchor - timedelta(days=delta)
    delta = (target - anchor.weekday()) % 7 or 7
    return anchor + timedelta(days=delta)


def _record_anchor_datetime(record: SourceRecord) -> datetime | None:
    """Best-effort anchor for resolving a record's relative dates: a concrete
    timestamp the record already carries (metadata first, then the first absolute
    date in its content). Returns None when nothing datable is present, so
    occurred_at is only ever set when there is a real anchor to resolve against."""
    for key in _ANCHOR_METADATA_KEYS:
        value = record.metadata.get(key)
        anchor = _coerce_anchor(value) if value else None
        if anchor:
            return anchor
    match = re.search(r"\b((?:19|20)\d{2})-(\d{1,2})-(\d{1,2})\b", record.content)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


def _annotate_occurred_at(record: SourceRecord) -> None:
    """When a record has a relative/anchored date phrase and a concrete anchor,
    record the resolved occurred_at in metadata. Non-destructive: content and
    captured_at are untouched, and an occurred_at already supplied by a parser is
    left in place."""
    if record.metadata.get("occurred_at"):
        return
    anchor = _record_anchor_datetime(record)
    if not anchor:
        return
    resolved = resolve_occurred_at(record.content, anchor)
    if resolved:
        record.metadata = {**record.metadata, "occurred_at": resolved}


def _chunk_body_start(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if line.strip().lower() in CHUNK_MARKERS:
            return index + 1
    return None


def _group_chunk_body_lines(lines: list[str]) -> list[list[str]]:
    groups: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if current and _looks_like_new_chunk_item(line):
            groups.append(current)
            current = []
        current.append(line)
    if current:
        groups.append(current)
    return groups


def _looks_like_new_chunk_item(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if re.match(r"^row\s+\d+\b", stripped, re.IGNORECASE):
        return True
    if re.match(r"^\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2})?", stripped):
        return True
    if re.match(r"^\d{10}(?:\.\d+)?\b", stripped):
        return True
    if re.match(r"^(user|assistant|human|system|unknown|bot)\s*:", stripped, re.IGNORECASE):
        return True
    return False


def _source_url_with_chunk(source_url: str | None, index: int) -> str | None:
    if not source_url:
        return source_url
    if source_url.startswith("cortex-source://") or "#" in source_url or "?" in source_url:
        separator = "&" if ("?" in source_url or "#" in source_url) else "?"
        return f"{source_url}{separator}chunk={index}"
    return f"{source_url}#chunk-{index}"


def _batched(items: list, batch_size: int) -> list[list]:
    if batch_size <= 0:
        return [items] if items else []
    return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]


def _label_record_parts(records: list[SourceRecord]) -> list[SourceRecord]:
    """Label sibling records built from batches of one oversized export.

    Parsers used to hard-cap item counts (e.g. tweets past 1000 were silently
    dropped); the cap is now a batch size and each batch becomes its own record.
    When there is more than one batch, each part is labeled `(part i/n)` with a
    per-part source locator and chunk metadata. A single record is returned
    untouched so small imports stay byte-identical to the pre-batching output.
    """
    if len(records) <= 1:
        return records
    count = len(records)
    return [
        SourceRecord(
            source=record.source,
            title=f"{record.title} (part {index}/{count})",
            content=record.content,
            source_url=_source_url_with_chunk(record.source_url, index),
            metadata={**record.metadata, "chunk_index": index, "chunk_count": count},
        )
        for index, record in enumerate(records, start=1)
    ]


def _collect_assets(paths: Iterable[str]) -> list[SourceAsset]:
    assets: list[SourceAsset] = []
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if not path.exists():
            continue
        if path.is_dir():
            for child in path.rglob("*"):
                relative = child.relative_to(path)
                if child.is_file() and not _is_hidden(relative):
                    assets.extend(_asset_from_path(child, name=str(relative)))
        elif path.is_file() and not _is_hidden(Path(path.name)):
            assets.extend(_asset_from_path(path))
    return assets


def _asset_from_path(path: Path, name: str | None = None) -> list[SourceAsset]:
    suffix = path.suffix.lower()
    if suffix == ".zip":
        return _assets_from_zip(path)
    return [SourceAsset(name=name or path.name, display_path=str(path), filesystem_path=path)]


# Notion (and some other multi-part exports) wrap the real files in an outer
# .zip that only contains one or more inner "Export-*.zip" / "Part-*.zip"
# archives. A .zip member is expanded one additional level so those inner files
# are imported instead of silently skipped. Bounded in nesting depth, total
# member count, and inner-archive size so a crafted (zip-bomb) import degrades
# instead of exhausting memory.
_MAX_INNER_ZIP_DEPTH = 2
_MAX_ZIP_MEMBER_COUNT = 20_000
_MAX_INNER_ZIP_BYTES = MAX_TEXT_BYTES * 8


def _assets_from_zip(path: Path) -> list[SourceAsset]:
    try:
        with zipfile.ZipFile(path) as archive:
            return _assets_from_open_zip(archive, str(path), depth=0, budget=[_MAX_ZIP_MEMBER_COUNT])
    except (zipfile.BadZipFile, OSError):
        return [SourceAsset(name=path.name, display_path=str(path), filesystem_path=path)]


def _assets_from_open_zip(
    archive: zipfile.ZipFile, display_root: str, *, depth: int, budget: list[int]
) -> list[SourceAsset]:
    assets: list[SourceAsset] = []
    for info in archive.infolist():
        if budget[0] <= 0:
            break
        if info.is_dir():
            continue
        name = info.filename
        if Path(name).name.startswith("."):
            continue
        if Path(name).suffix.lower() == ".zip":
            # Expand a nested export archive one more level (Notion multi-part
            # exports), guarding against deep nesting and oversized inner zips.
            if depth >= _MAX_INNER_ZIP_DEPTH or info.file_size > _MAX_INNER_ZIP_BYTES:
                continue
            budget[0] -= 1
            try:
                inner_bytes = archive.read(info)
                with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner:
                    assets.extend(
                        _assets_from_open_zip(
                            inner, f"{display_root}::{name}", depth=depth + 1, budget=budget
                        )
                    )
            except (KeyError, RuntimeError, zipfile.BadZipFile, OSError):
                continue
            continue
        budget[0] -= 1
        truncated = info.file_size > MAX_TEXT_BYTES
        try:
            if truncated:
                # Oversized members used to be skipped entirely — silent
                # whole-file loss. Stream the first MAX_TEXT_BYTES and
                # mark the asset truncated instead.
                with archive.open(info) as member:
                    data = member.read(MAX_TEXT_BYTES)
            else:
                data = archive.read(info)
        except (KeyError, RuntimeError, zipfile.BadZipFile):
            continue
        assets.append(SourceAsset(name=name, display_path=f"{display_root}::{name}", data=data, read_truncated=truncated))
    return assets


def _parse_chatgpt(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for asset in assets:
        if Path(asset.name).name != "conversations.json":
            continue
        try:
            payload = json.loads(asset.read_text())
        except (json.JSONDecodeError, RecursionError):
            continue
        if not isinstance(payload, list) or not payload:
            continue
        if not any(isinstance(item, dict) and "mapping" in item for item in payload):
            continue
        for conversation in payload:
            if not isinstance(conversation, dict):
                continue
            title = str(conversation.get("title") or "ChatGPT conversation").strip()
            lines = [f"Source: ChatGPT", f"Conversation: {title}"]
            created = _chatgpt_time(conversation.get("create_time"))
            updated = _chatgpt_time(conversation.get("update_time"))
            if created:
                lines.append(f"Created: {created}")
            if updated:
                lines.append(f"Updated: {updated}")
            messages = _chatgpt_messages(conversation)
            if not messages:
                continue
            lines.extend(["", "--- Messages ---", *messages])
            source_url = _source_locator(
                asset.display_path,
                service="chatgpt",
                conversation=title,
                conversation_id=conversation.get("id") or conversation.get("conversation_id"),
            )
            records.append(SourceRecord("chatgpt", title, "\n".join(lines), source_url=source_url, metadata={"asset": asset.display_path, "service": "ChatGPT"}))
    return records


def _chatgpt_messages(conversation: dict[str, Any]) -> list[str]:
    mapping = conversation.get("mapping") or {}
    messages: list[tuple[float, str]] = []
    for node in mapping.values():
        if not isinstance(node, dict):
            continue
        message = node.get("message") or {}
        if not isinstance(message, dict):
            continue
        author = message.get("author")
        role = ((author.get("role") if isinstance(author, dict) else None) or "unknown").strip()
        if role == "system":
            continue
        content = _chatgpt_content(message.get("content") or {})
        if not content:
            continue
        create_time = message.get("create_time")
        created = float(create_time) if isinstance(create_time, (int, float)) else 0.0
        created_label = _chatgpt_time(created)
        prefix = f"{created_label} {role}" if created_label else role
        messages.append((created, f"{prefix}: {content}"))
    return [text for _, text in sorted(messages, key=lambda item: item[0])]


def _chatgpt_content(content: dict[str, Any]) -> str:
    parts = content.get("parts")
    if isinstance(parts, list):
        values: list[str] = []
        for part in parts:
            if isinstance(part, str):
                values.append(part)
            elif isinstance(part, dict):
                values.append(json.dumps(part, ensure_ascii=False))
        return "\n".join(value.strip() for value in values if value and value.strip())
    text = content.get("text")
    if isinstance(text, str):
        return text.strip()
    return ""


def _chatgpt_time(value: Any) -> str:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    return _iso_from_unix(timestamp)


def _parse_consumer_ai_transcripts(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for asset in assets:
        if _looks_like_gemini_takeout(asset, hint):
            # Google Takeout "Gemini Apps" activity has its own shape — leave it
            # to _parse_gemini_takeout so we don't double-parse the same file.
            continue
        source = _consumer_ai_source_for_asset(asset, hint)
        if not source:
            continue
        if asset.suffix not in {".json", ".jsonl", ".txt", ".md", ".markdown", ".html", ".htm"}:
            continue
        provider = CONSUMER_AI_TRANSCRIPT_SOURCES[source]
        if asset.suffix == ".json":
            records.extend(_parse_consumer_ai_json_asset(asset, source, provider))
        elif asset.suffix == ".jsonl":
            records.extend(_parse_consumer_ai_jsonl_asset(asset, source, provider))
        else:
            record = _parse_consumer_ai_text_asset(asset, source, provider)
            if record:
                records.append(record)
    return records


def _consumer_ai_source_for_asset(asset: SourceAsset, hint: str) -> str:
    if hint in CONSUMER_AI_TRANSCRIPT_SOURCES:
        return hint
    components = [
        component
        for component in re.split(r"[\\/]+", asset.display_path)
        if component and component not in {".", ".."}
    ]
    nearby = [asset.name, *components[-3:]]
    for component in nearby:
        source = _consumer_ai_source_for_component(component)
        if source:
            return source
    return ""


def _consumer_ai_source_for_component(component: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", component.lower()).strip()
    compact = normalized.replace(" ", "")
    aliases = {
        "gemini": {"gemini", "google gemini", "googlegemini", "gemini apps", "geminiapps"},
        "perplexity": {"perplexity", "perplexity ai", "perplexityai"},
        "copilot": {"copilot", "microsoft copilot", "ms copilot", "microsoftcopilot", "mscopilot"},
        "grok": {"grok"},
        "poe": {"poe"},
        "notebooklm": {"notebooklm", "notebook lm", "notebook-lm"},
    }
    for source, values in aliases.items():
        if normalized in values or compact in values:
            return source
    tokens = normalized.split()
    transcript_terms = {"ai", "chat", "chats", "conversation", "conversations", "export", "exports", "takeout", "transcript", "transcripts"}
    for source in CONSUMER_AI_TRANSCRIPT_SOURCES:
        if source in tokens and (set(tokens) & transcript_terms):
            return source
    if "notebook" in tokens and "lm" in tokens:
        return "notebooklm"
    if "microsoft" in tokens and "copilot" in tokens:
        return "copilot"
    return ""


def _parse_consumer_ai_json_asset(asset: SourceAsset, source: str, provider: str) -> list[SourceRecord]:
    try:
        payload = json.loads(asset.read_text())
    except (json.JSONDecodeError, RecursionError):
        return []
    return [
        record
        for conversation in _consumer_ai_conversations(payload, fallback_title=Path(asset.name).stem)
        if (record := _consumer_ai_record(asset, source, provider, conversation))
    ]


def _parse_consumer_ai_jsonl_asset(asset: SourceAsset, source: str, provider: str) -> list[SourceRecord]:
    rows: list[dict[str, Any]] = []
    for line in asset.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, RecursionError):
            # JSONL lines are independent records; a single truncated/corrupt line
            # (common in large exports) must not discard every valid conversation
            # already parsed from the file. Skip the bad line instead of aborting.
            continue
        if isinstance(row, dict):
            rows.append(row)
    if not rows:
        return []
    if any(_consumer_ai_message_text(row) for row in rows):
        conversation = {"title": Path(asset.name).stem, "messages": rows}
        record = _consumer_ai_record(asset, source, provider, conversation)
        return [record] if record else []
    return [
        record
        for conversation in _consumer_ai_conversations(rows, fallback_title=Path(asset.name).stem)
        if (record := _consumer_ai_record(asset, source, provider, conversation))
    ]


def _parse_consumer_ai_text_asset(asset: SourceAsset, source: str, provider: str) -> SourceRecord | None:
    text = asset.read_text()
    if asset.suffix in {".html", ".htm"}:
        text = _html_to_text(text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    title = Path(asset.name).stem or f"{provider} transcript"
    messages: list[str] = []
    for line in lines:
        if re.match(r"^(?:\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2})?\s+)?(?:user|assistant|human|model|system|unknown)\s*:", line, flags=re.IGNORECASE):
            messages.append(line)
        else:
            messages.append(f"unknown: {line}")
    content = "\n".join([f"Source: {provider}", f"Conversation: {title}", "", "--- Messages ---", *messages])
    source_url = _source_locator(asset.display_path, service=source, conversation=title, file=Path(asset.name).name)
    return SourceRecord(source, title, content, source_url=source_url, metadata={"asset": asset.display_path, "service": provider})


def _consumer_ai_conversations(payload: Any, *, fallback_title: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("conversations", "chats", "threads", "items", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if isinstance(payload.get("messages") or payload.get("chat_messages"), list):
            return [payload]
        return []
    if isinstance(payload, list):
        if all(isinstance(item, dict) and _consumer_ai_message_text(item) for item in payload):
            return [{"title": fallback_title, "messages": payload}]
        return [item for item in payload if isinstance(item, dict)]
    return []


def _consumer_ai_record(asset: SourceAsset, source: str, provider: str, conversation: dict[str, Any]) -> SourceRecord | None:
    messages = conversation.get("messages") or conversation.get("chat_messages") or conversation.get("turns") or []
    if not isinstance(messages, list):
        return None
    title = str(
        conversation.get("title")
        or conversation.get("name")
        or conversation.get("subject")
        or conversation.get("id")
        or f"{provider} transcript"
    ).strip()
    lines = [f"Source: {provider}", f"Conversation: {title}"]
    created = str(conversation.get("created_at") or conversation.get("createdAt") or conversation.get("created") or "").strip()
    if created:
        lines.append(f"Created: {created}")
    message_lines = _consumer_ai_message_lines(messages)
    if not message_lines:
        return None
    lines.extend(["", "--- Messages ---", *message_lines])
    source_url = _source_locator(
        asset.display_path,
        service=source,
        conversation=title,
        conversation_id=conversation.get("id") or conversation.get("conversation_id") or conversation.get("uuid"),
        file=Path(asset.name).name,
    )
    return SourceRecord(source, title, "\n".join(lines), source_url=source_url, metadata={"asset": asset.display_path, "service": provider})


def _consumer_ai_message_lines(messages: list[Any]) -> list[str]:
    lines: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        text = _consumer_ai_message_text(message)
        if not text:
            continue
        role = _consumer_ai_role(message.get("role") or message.get("sender") or message.get("author") or message.get("from"))
        created = _consumer_ai_message_time(message)
        prefix = f"{created} {role}" if created else role
        lines.append(f"{prefix}: {text}")
    return lines


def _consumer_ai_message_text(message: dict[str, Any]) -> str:
    for key in ("text", "content", "body", "message", "answer"):
        value = message.get(key)
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(str(item.get("text") or item.get("content") or item.get("body") or ""))
            return "\n".join(part.strip() for part in parts if part.strip())
        if isinstance(value, dict):
            text = value.get("text") or value.get("content") or value.get("body")
            if isinstance(text, str):
                return text.strip()
    return ""


def _consumer_ai_role(value: Any) -> str:
    role = str(value or "unknown").strip().lower()
    role = re.sub(r"[^a-z0-9_-]+", "-", role).strip("-")
    if role in {"human", "user", "me", "self"}:
        return "user"
    if role in {"assistant", "ai", "model", "bot", "system", "tool"}:
        return "assistant" if role != "system" else "system"
    if role in CONSUMER_AI_TRANSCRIPT_SOURCES:
        return "assistant"
    return role or "unknown"


def _consumer_ai_message_time(message: dict[str, Any]) -> str:
    for key in ("created_at", "createdAt", "timestamp", "time", "date", "created", "create_time"):
        value = message.get(key)
        if value is None:
            continue
        if isinstance(value, (int, float)):
            return _chatgpt_time(value)
        text = str(value).strip()
        if text:
            return text
    return ""


def _google_keep_time(value: Any) -> str:
    try:
        raw = int(value)
    except (TypeError, ValueError):
        return ""
    if raw <= 0:
        return ""
    seconds = raw / 1_000_000 if raw > 10_000_000_000 else raw
    return _iso_from_unix(seconds)


# A Google Takeout Gemini export lives at
#   Takeout/My Activity/Gemini Apps/MyActivity.(html|json)
# Each activity entry is a prompt (and sometimes the linked response) plus a
# timestamp. These verbs prefix the prompt text on each entry ("Prompted …").
_GEMINI_ACTIVITY_VERBS = ("prompted", "asked", "searched for", "used gemini apps", "used")
_GEMINI_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def _parse_gemini_takeout(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    """Import a Google Takeout Gemini Apps activity export into transcript records.

    Real Takeout layout is ``Takeout/My Activity/Gemini Apps/MyActivity.html`` (or
    ``MyActivity.json``). Detection keys off the "Gemini Apps" path component, so a
    user can drop the unzipped Takeout folder (or its ``.zip``) and have it
    recognized without a hint. Extraction is best-effort — an unexpected shape
    yields no records for that file, never a crash.
    """
    records: list[SourceRecord] = []
    for asset in assets:
        if not _looks_like_gemini_takeout(asset, hint):
            continue
        if asset.suffix == ".json":
            records.extend(_parse_gemini_takeout_json(asset))
        elif asset.suffix in {".html", ".htm"}:
            records.extend(_parse_gemini_takeout_html(asset))
    return records


def _looks_like_gemini_takeout(asset: SourceAsset, hint: str) -> bool:
    if asset.suffix not in {".json", ".html", ".htm"}:
        return False
    path_words = re.sub(r"[^a-z0-9]+", " ", asset.display_path.lower())
    if "gemini apps" in path_words:
        return True
    # No folder marker: only claim a bare MyActivity file when the user hinted gemini.
    return hint == "gemini" and Path(asset.name).stem.lower() == "myactivity"


def _parse_gemini_takeout_json(asset: SourceAsset) -> list[SourceRecord]:
    try:
        payload = json.loads(asset.read_text())
    except (json.JSONDecodeError, RecursionError):
        return []
    if isinstance(payload, list):
        entries: Any = payload
    elif isinstance(payload, dict):
        entries = payload.get("activity") or payload.get("activities") or payload.get("items")
    else:
        entries = None
    if not isinstance(entries, list):
        return []
    items: list[tuple[str, str]] = []
    for entry in entries:
        if isinstance(entry, dict):
            item = _gemini_json_item(entry)
            if item:
                items.append(item)
    return _gemini_takeout_records(asset, items)


def _gemini_json_item(entry: dict[str, Any]) -> tuple[str, str] | None:
    text = _strip_gemini_verb(str(entry.get("title") or "").strip())
    if not text:
        # Some entries carry the prompt in the subtitles/details list instead.
        subtitles = entry.get("subtitles")
        if isinstance(subtitles, list):
            for sub in subtitles:
                if isinstance(sub, dict):
                    name = str(sub.get("name") or "").strip()
                    if name:
                        text = name
                        break
    if not text:
        return None
    return (str(entry.get("time") or "").strip(), text)


def _parse_gemini_takeout_html(asset: SourceAsset) -> list[SourceRecord]:
    parser = _GeminiActivityHTMLParser()
    try:
        parser.feed(asset.read_text())
    except Exception:
        return []
    items: list[tuple[str, str]] = []
    for cell in parser.cells:
        item = _gemini_html_item(cell)
        if item:
            items.append(item)
    return _gemini_takeout_records(asset, items)


def _gemini_html_item(lines: list[str]) -> tuple[str, str] | None:
    time_value = ""
    body: list[str] = []
    for line in lines:
        if not time_value and _looks_like_gemini_time(line):
            time_value = line
            continue
        stripped = _strip_gemini_verb(line)
        if stripped:
            body.append(stripped)
    text = " ".join(body).strip()
    if not text:
        return None
    return (time_value, text)


def _strip_gemini_verb(line: str) -> str:
    text = line.strip()
    lowered = text.lower()
    for verb in _GEMINI_ACTIVITY_VERBS:
        if lowered == verb:
            return ""
        if lowered.startswith(verb + " "):
            return text[len(verb):].strip()
    return text


def _looks_like_gemini_time(line: str) -> bool:
    lowered = line.lower()
    if re.search(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", lowered) and re.search(r"\b(am|pm)\b", lowered):
        return True
    if any(re.search(rf"\b{month}[a-z]*\s+\d{{1,2}}\b", lowered) for month in _GEMINI_MONTHS):
        return True
    return bool(re.search(r"\d{4}-\d{2}-\d{2}", line))


def _gemini_takeout_records(asset: SourceAsset, items: list[tuple[str, str]]) -> list[SourceRecord]:
    if not items:
        return []
    title = "Gemini Apps activity"
    file_name = Path(asset.name).name
    batch_records: list[SourceRecord] = []
    for batch in _batched(items, 1500):
        lines = ["Source: Gemini", f"Activity: {title}", f"File: {file_name}", "", "--- Messages ---"]
        first_time = ""
        for time_value, text in batch:
            if not first_time and time_value:
                first_time = time_value
            prefix = f"{time_value} user" if time_value else "user"
            lines.append(f"{prefix}: {text}")
        if len(lines) <= 5:
            continue
        source_url = _source_locator(
            asset.display_path, service="gemini", activity=title, file=file_name, first_time=first_time
        )
        batch_records.append(
            SourceRecord(
                "gemini",
                title,
                "\n".join(lines),
                source_url=source_url,
                metadata={"asset": asset.display_path, "service": "Gemini"},
            )
        )
    return _label_record_parts(batch_records)


class _GeminiActivityHTMLParser(HTMLParser):
    """Pull activity entries out of a Google Takeout MyActivity.html.

    Each entry lives in a ``content-cell`` div; the meaningful ones hold the
    prompt text (and sometimes the response) plus a timestamp, with ``<br>``
    separating the pieces. We capture each content cell's text as a list of lines
    and let the caller shape prompt/time. The MDL markup varies, so detection is
    class-substring based and tolerant of extra nesting; the caption/metadata cell
    (locations, product tags) is skipped.
    """

    def __init__(self) -> None:
        super().__init__()
        self.cells: list[list[str]] = []
        self._capture = False
        self._depth = 0
        self._current: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "div":
            classes = ""
            for key, value in attrs:
                if key == "class":
                    classes = value or ""
            if not self._capture:
                if "content-cell" in classes and "mdl-typography--caption" not in classes:
                    self._capture = True
                    self._depth = 0
                    self._current = []
            else:
                self._depth += 1
        elif tag == "br" and self._capture:
            self._current.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._capture:
            if self._depth == 0:
                self._capture = False
                self._flush()
            else:
                self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._capture and data.strip():
            self._current.append(data.strip())

    def _flush(self) -> None:
        text = " ".join(self._current)
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if lines:
            self.cells.append(lines)
        self._current = []


def _parse_limitless(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    """Import a Limitless (formerly Rewind) export into per-day transcript records.

    Detection is by filename/dir marker (``limitless``/``lifelog``/``rewind``) plus a
    content sniff, so a user can drop the raw export folder or ``.zip`` and have it
    recognized without a hint. Each lifelog (a recorded day/session) becomes one
    record whose content is the speaker-labelled transcript; the shared chunker then
    splits long days into atomic memories. Malformed entries are skipped, never
    fatal — a single corrupt lifelog can't discard a whole import.
    """
    records: list[SourceRecord] = []
    for asset in assets:
        if not _looks_like_limitless_asset(asset, hint):
            continue
        if asset.suffix == ".jsonl":
            records.extend(_parse_limitless_jsonl_asset(asset))
        elif asset.suffix == ".json":
            records.extend(_parse_limitless_json_asset(asset))
        elif asset.suffix in {".md", ".markdown", ".txt"}:
            record = _parse_limitless_text_asset(asset)
            if record:
                records.append(record)
    return records


def _looks_like_limitless_asset(asset: SourceAsset, hint: str) -> bool:
    if asset.suffix not in {".json", ".jsonl", ".md", ".markdown", ".txt"}:
        return False
    if hint in {"limitless", "rewind", "lifelog", "lifelogs"}:
        return True
    haystack = f"{asset.display_path}\n{asset.name}".lower()
    if any(marker in haystack for marker in LIMITLESS_MARKDOWN_MARKERS):
        return True
    # No filename hint — sniff the content so a generically-named `export.json`
    # from Limitless is still recognized (and, crucially, so an unrelated JSON
    # file is not misclaimed).
    if asset.suffix in {".json", ".jsonl"}:
        return _limitless_content_sniff(asset)
    return False


def _limitless_content_sniff(asset: SourceAsset) -> bool:
    """True only when the file's shape is unmistakably a Limitless lifelog export.

    Requires the lifelog-node fingerprint (`speakerName`/`speakerIdentifier`/
    `lifelogs`/`startOffsetMs`) so a plain chat-export JSON is never misclaimed.
    """
    head = asset.read_text()[:20_000].lower()
    if not head:
        return False
    if '"lifelogs"' in head:
        return True
    return ('"speakeridentifier"' in head or '"speakername"' in head or '"startoffsetms"' in head)


def _parse_limitless_json_asset(asset: SourceAsset) -> list[SourceRecord]:
    try:
        payload = json.loads(asset.read_text())
    except (json.JSONDecodeError, RecursionError):
        return []
    lifelogs = _limitless_lifelogs_from_payload(payload)
    return [
        record
        for lifelog in lifelogs
        if isinstance(lifelog, dict) and (record := _limitless_record(asset, lifelog))
    ]


def _parse_limitless_jsonl_asset(asset: SourceAsset) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for line in asset.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, RecursionError):
            # JSONL lines are independent lifelogs; one truncated/corrupt line
            # (common in large exports) must not discard the valid days already
            # parsed. Skip the bad line and keep going.
            continue
        for lifelog in _limitless_lifelogs_from_payload(row):
            if isinstance(lifelog, dict):
                record = _limitless_record(asset, lifelog)
                if record:
                    records.append(record)
    return records


def _limitless_lifelogs_from_payload(payload: Any) -> list[Any]:
    """Unwrap the several realistic shapes a lifelog export can arrive in.

    Handles the Developer-API envelope ``{"data": {"lifelogs": [...]}}``, a bare
    list of lifelogs, a single lifelog object, and a top-level ``{"lifelogs": [...]}``.
    """
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("lifelogs"), list):
        return data["lifelogs"]
    if isinstance(data, list):
        return data
    for key in ("lifelogs", "entries", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    # A single lifelog object (has the shape of a day: contents or markdown).
    if _looks_like_lifelog(payload):
        return [payload]
    return []


def _looks_like_lifelog(value: dict[str, Any]) -> bool:
    return bool(
        isinstance(value.get("contents"), list)
        or isinstance(value.get("markdown"), str)
        or ("title" in value and ("startTime" in value or "start_time" in value))
    )


def _limitless_record(asset: SourceAsset, lifelog: dict[str, Any]) -> SourceRecord | None:
    start = _limitless_time(lifelog.get("startTime") or lifelog.get("start_time"))
    end = _limitless_time(lifelog.get("endTime") or lifelog.get("end_time"))
    day = _limitless_day_label(start)
    base_title = str(lifelog.get("title") or "Lifelog").strip() or "Lifelog"
    title = f"{base_title} - {day}" if day else base_title
    transcript = _limitless_transcript_lines(lifelog)
    if not transcript:
        # Fall back to the pre-rendered markdown when the structured contents are
        # missing/empty (older/partial exports carry only `markdown`).
        markdown = str(lifelog.get("markdown") or "").strip()
        if markdown:
            transcript = [line.strip() for line in markdown.splitlines() if line.strip()]
    if not transcript:
        return None
    lines = ["Source: Limitless", f"Lifelog: {base_title}"]
    if start:
        lines.append(f"Start: {start}")
    if end:
        lines.append(f"End: {end}")
    lines.extend(["", "--- Transcript ---", *transcript])
    source_url = _source_locator(
        asset.display_path,
        service="limitless",
        lifelog=base_title,
        lifelog_id=lifelog.get("id") or lifelog.get("uuid"),
        file=Path(asset.name).name,
        start=start,
    )
    return SourceRecord(
        "limitless",
        title,
        "\n".join(lines),
        source_url=source_url,
        metadata={"asset": asset.display_path, "service": "Limitless", "lifelog_start": start},
    )


def _limitless_transcript_lines(lifelog: dict[str, Any]) -> list[str]:
    contents = lifelog.get("contents")
    if not isinstance(contents, list):
        return []
    lines: list[str] = []
    _collect_limitless_nodes(contents, lines)
    return lines


def _collect_limitless_nodes(nodes: Any, lines: list[str], depth: int = 0) -> None:
    if not isinstance(nodes, list) or depth >= MAX_PARSE_NESTING_DEPTH:
        return
    for node in nodes:
        if not isinstance(node, dict):
            continue
        text = str(node.get("content") or node.get("text") or "").strip()
        if text:
            node_type = str(node.get("type") or "").strip().lower()
            speaker = _limitless_speaker(node)
            when = _limitless_time(node.get("startTime") or node.get("start_time"))
            if node_type in {"heading1", "heading2", "heading3"} and not speaker:
                lines.append(f"# {text}" if node_type == "heading1" else f"## {text}")
            elif speaker:
                prefix = f"{when} {speaker}" if when else speaker
                lines.append(f"{prefix}: {text}")
            else:
                lines.append(text)
        children = node.get("children")
        if isinstance(children, list) and children:
            _collect_limitless_nodes(children, lines, depth + 1)


def _limitless_speaker(node: dict[str, Any]) -> str:
    name = str(node.get("speakerName") or node.get("speaker_name") or node.get("speaker") or "").strip()
    if name:
        return re.sub(r"\s+", " ", name)[:60]
    identifier = str(node.get("speakerIdentifier") or node.get("speaker_identifier") or "").strip().lower()
    if identifier == "user":
        return "You"
    return ""


def _limitless_day_label(start: str) -> str:
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", start or "")
    return match.group(1) if match else ""


def _limitless_time(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        # Milliseconds vs seconds: Limitless offsets are ms; epoch seconds are ~1e9.
        seconds = value / 1000 if value > 100_000_000_000 else float(value)
        return _iso_from_unix(seconds)
    text = str(value).strip()
    return text


def _parse_limitless_text_asset(asset: SourceAsset) -> SourceRecord | None:
    text = asset.read_text()
    lines = [line.rstrip() for line in text.splitlines()]
    body = [line.strip() for line in lines if line.strip()]
    if not body:
        return None
    day = _limitless_day_label_from_text(asset.name, text)
    base_title = Path(asset.name).stem or "Lifelog"
    title = f"{base_title} - {day}" if day and day not in base_title else base_title
    content = "\n".join(["Source: Limitless", f"Lifelog: {base_title}", "", "--- Transcript ---", *body])
    source_url = _source_locator(
        asset.display_path, service="limitless", lifelog=base_title, file=Path(asset.name).name, start=day
    )
    return SourceRecord(
        "limitless",
        title,
        content,
        source_url=source_url,
        metadata={"asset": asset.display_path, "service": "Limitless", "lifelog_start": day},
    )


def _limitless_day_label_from_text(name: str, text: str) -> str:
    match = re.search(r"\d{4}-\d{2}-\d{2}", name)
    if match:
        return match.group(0)
    match = re.search(r"\d{4}-\d{2}-\d{2}", text[:400])
    return match.group(0) if match else ""


def _parse_claude(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for asset in assets:
        inferred_consumer_source = _consumer_ai_source_for_asset(asset, hint)
        if inferred_consumer_source and inferred_consumer_source != "claude":
            continue
        if Path(asset.name).name not in {"conversations.json", "chats.json"}:
            continue
        try:
            payload = json.loads(asset.read_text())
        except (json.JSONDecodeError, RecursionError):
            continue
        conversations = payload.get("conversations") if isinstance(payload, dict) else payload
        if not isinstance(conversations, list):
            continue
        if not any(isinstance(item, dict) and ("chat_messages" in item or "messages" in item) for item in conversations):
            continue
        for conversation in conversations:
            if not isinstance(conversation, dict):
                continue
            title = str(conversation.get("name") or conversation.get("title") or "Claude conversation").strip()
            messages = conversation.get("chat_messages") or conversation.get("messages") or []
            lines = [f"Source: Claude", f"Conversation: {title}"]
            if conversation.get("created_at"):
                lines.append(f"Created: {conversation.get('created_at')}")
            lines.extend(["", "--- Messages ---"])
            for message in messages:
                if not isinstance(message, dict):
                    continue
                sender = str(message.get("sender") or message.get("role") or "unknown")
                text = _claude_message_text(message)
                if text:
                    created = str(message.get("created_at") or message.get("createdAt") or message.get("timestamp") or "").strip()
                    prefix = f"{created} {sender}" if created else sender
                    lines.append(f"{prefix}: {text}")
            if len(lines) > 4:
                source_url = _source_locator(
                    asset.display_path,
                    service="claude",
                    conversation=title,
                    conversation_id=conversation.get("uuid") or conversation.get("id"),
                )
                records.append(SourceRecord("claude", title, "\n".join(lines), source_url=source_url, metadata={"asset": asset.display_path, "service": "Claude"}))
    return records


def _claude_message_text(message: dict[str, Any]) -> str:
    for key in ("text", "content"):
        value = message.get(key)
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            parts = []
            for part in value:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    parts.append(str(part.get("text") or part.get("content") or ""))
            return "\n".join(part.strip() for part in parts if part.strip())
    return ""


# Notion appends a space + a 32-hex page id to every exported page filename (and
# to the parent folder names), e.g.
#   "My Great Page 0a1b2c3d4e5f60718293a4b5c6d7e8f9.md" -> "My Great Page".
_NOTION_ID_SUFFIX = re.compile(r"\s+[0-9a-fA-F]{32}$")


def _strip_notion_id(value: str) -> str:
    """Drop the trailing " <32 hex>" Notion export id from a page/folder name."""
    return _NOTION_ID_SUFFIX.sub("", value).strip()


def _parse_notion(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    """Import a Notion workspace export (Markdown / HTML / CSV assets).

    Notion exports one file per page, named "<Page title> <32-hex id>.md" (or
    .html), and database views as .csv. We claim these only when the user hinted
    "notion" — so a generic markdown drop is unaffected and the assets are not
    double-parsed by the generic fallback — strip the trailing page id from the
    title, and hand CSV views to the shared structured-CSV formatter. Best-effort:
    odd content or an empty page is skipped, never raised.
    """
    if hint != "notion":
        return []
    records: list[SourceRecord] = []
    for asset in assets:
        suffix = asset.suffix
        if suffix not in {".md", ".markdown", ".html", ".htm", ".csv"}:
            continue
        try:
            text = asset.read_text()
        except Exception:
            continue
        if suffix == ".csv":
            csv_title = _strip_notion_id(Path(asset.name).stem) or Path(asset.name).name
            records.extend(
                _label_record_parts(
                    [
                        _generic_record(asset, "notion", part, title=csv_title)
                        for part in _format_csv_export(asset, text, "notion")
                    ]
                )
            )
            continue
        if suffix in {".html", ".htm"}:
            text = _html_to_text(text)
        if not text.strip():
            continue
        title = _strip_notion_id(Path(asset.name).stem) or Path(asset.name).name
        source_url = _generic_source_locator(asset, "notion", title)
        records.append(
            SourceRecord(
                "notion",
                title,
                text,
                source_url=source_url,
                metadata={"asset": asset.display_path, "service": "Notion"},
            )
        )
    return records


def _parse_slack(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    slack_assets = [asset for asset in assets if asset.suffix == ".json" and _looks_like_slack_path(asset.name)]
    if not slack_assets and hint != "slack":
        return []
    users = _slack_users(assets)
    records: list[SourceRecord] = []
    for asset in slack_assets:
        try:
            payload = json.loads(asset.read_text())
        except (json.JSONDecodeError, RecursionError):
            continue
        if not isinstance(payload, list) or not any(isinstance(item, dict) and "ts" in item for item in payload):
            continue
        channel = _path_parts(asset.name)[-2] if len(_path_parts(asset.name)) > 1 else "Slack"
        lines = [f"Source: Slack", f"Channel: {channel}", f"File: {asset.name}", "", "--- Messages ---"]
        first_ts = ""
        for message in payload:
            if not isinstance(message, dict):
                continue
            text = _slack_message_text(message)
            if not text:
                continue
            if not first_ts and message.get("ts"):
                first_ts = str(message.get("ts"))
            user = users.get(str(message.get("user") or ""), str(message.get("username") or message.get("user") or "unknown"))
            ts = _slack_time(message.get("ts"))
            lines.append(f"{ts} {user}: {text}".strip())
        if len(lines) > 5:
            source_url = _source_locator(asset.display_path, service="slack", channel=channel, file=Path(asset.name).name, first_ts=first_ts)
            records.append(SourceRecord("slack", f"Slack #{channel} {Path(asset.name).stem}", "\n".join(lines), source_url=source_url, metadata={"asset": asset.display_path, "service": "Slack", "channel": channel}))
    return records


def _slack_users(assets: list[SourceAsset]) -> dict[str, str]:
    for asset in assets:
        if Path(asset.name).name != "users.json":
            continue
        try:
            payload = json.loads(asset.read_text())
        except (json.JSONDecodeError, RecursionError):
            return {}
        if not isinstance(payload, list):
            return {}
        return {str(item.get("id")): _slack_user_label(item) for item in payload if isinstance(item, dict)}
    return {}


def _slack_user_label(item: dict[str, Any]) -> str:
    label = str(item.get("real_name") or item.get("name") or item.get("id") or "unknown").strip()
    profile = item.get("profile") if isinstance(item.get("profile"), dict) else {}
    email = str(profile.get("email") or item.get("email") or "").strip()
    local = email.split("@", 1)[0].strip() if "@" in email else ""
    if local and local.lower() not in {part.lower() for part in label.split()}:
        combined = f"{label} {local}".strip()
        if len(combined) <= 40:
            return combined
    return label


def _slack_message_text(message: dict[str, Any]) -> str:
    parts: list[str] = []
    text = str(message.get("text") or "").strip()
    if text:
        parts.append(text)
    for attachment in message.get("attachments") or []:
        if not isinstance(attachment, dict):
            continue
        for key in ("pretext", "title", "text", "fallback"):
            value = str(attachment.get(key) or "").strip()
            if value:
                parts.append(value)
    for file_item in message.get("files") or []:
        if not isinstance(file_item, dict):
            continue
        value = str(file_item.get("title") or file_item.get("name") or "").strip()
        if value:
            parts.append(f"File: {value}")
    for block in message.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        text_obj = block.get("text")
        if isinstance(text_obj, dict):
            value = str(text_obj.get("text") or "").strip()
            if value:
                parts.append(value)
        for element in block.get("elements") or []:
            if isinstance(element, dict):
                value = str((element.get("text") or {}).get("text") if isinstance(element.get("text"), dict) else element.get("text") or "").strip()
                if value:
                    parts.append(value)
    seen: set[str] = set()
    cleaned: list[str] = []
    for part in parts:
        normalized = _clean_chat_text(part)
        key = re.sub(r"\s+", " ", normalized).strip().lower()
        if normalized and key not in seen:
            seen.add(key)
            cleaned.append(normalized)
    return "\n".join(cleaned)


def _looks_like_slack_path(name: str) -> bool:
    parts = _path_parts(name)
    return len(parts) >= 2 and re.match(r"\d{4}-\d{2}-\d{2}\.json$", parts[-1] or "") is not None


def _is_slack_metadata_asset(asset: SourceAsset) -> bool:
    return asset.suffix == ".json" and Path(asset.name).name.lower() in {"users.json", "channels.json", "groups.json", "dms.json", "mpims.json"}


def _parse_discord(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for asset in assets:
        if Path(asset.name).name.lower() != "messages.csv":
            continue
        rows = _csv_rows(asset)
        if not rows or not any("Contents" in row or "content" in row for row in rows):
            continue
        channel = _path_parts(asset.name)[-2] if len(_path_parts(asset.name)) > 1 else "Discord"
        lines = [f"Source: Discord", f"Channel: {channel}", f"File: {asset.name}", "", "--- Messages ---"]
        first_timestamp = ""
        for row in rows:
            timestamp = row.get("Timestamp") or row.get("timestamp") or ""
            content = row.get("Contents") or row.get("content") or ""
            attachments = row.get("Attachments") or row.get("attachments") or ""
            content = content.strip()
            if attachments.strip():
                content = f"{content}\nAttachments: {attachments}".strip()
            if content:
                if not first_timestamp and timestamp:
                    first_timestamp = str(timestamp)
                author = _discord_row_author(row)
                if timestamp and author:
                    lines.append(f"{timestamp} {author}: {content}".strip())
                elif author:
                    lines.append(f"{author}: {content}".strip())
                else:
                    lines.append(f"{timestamp}: {content}".strip())
        if len(lines) > 5:
            source_url = _source_locator(asset.display_path, service="discord", channel=channel, file=Path(asset.name).name, first_timestamp=first_timestamp)
            records.append(SourceRecord("discord", f"Discord {channel}", "\n".join(lines), source_url=source_url, metadata={"asset": asset.display_path, "service": "Discord", "channel": channel}))
    return records


def _discord_row_author(row: dict[str, str]) -> str:
    for key in ("Author", "author", "Username", "username", "User", "user", "AuthorID", "author_id", "User ID", "user_id"):
        value = str(row.get(key) or "").strip()
        if value:
            return re.sub(r"\s+", " ", value)[:40]
    return ""


def _parse_telegram(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for asset in assets:
        if Path(asset.name).name != "result.json" and hint != "telegram":
            continue
        try:
            payload = json.loads(asset.read_text())
        except (json.JSONDecodeError, RecursionError):
            continue
        chats = ((payload.get("chats") or {}).get("list") if isinstance(payload, dict) else None) or []
        if not isinstance(chats, list):
            continue
        for chat in chats:
            if not isinstance(chat, dict) or "messages" not in chat:
                continue
            title = str(chat.get("name") or chat.get("title") or chat.get("id") or "Telegram chat")
            lines = [f"Source: Telegram", f"Chat: {title}", "", "--- Messages ---"]
            first_message = ""
            first_date = ""
            for message in chat.get("messages") or []:
                if not isinstance(message, dict):
                    continue
                text = _telegram_text(message.get("text"))
                if text:
                    sender = message.get("from") or message.get("actor") or "unknown"
                    date = message.get("date") or ""
                    if not first_message:
                        first_message = str(message.get("id") or "")
                    if not first_date and date:
                        first_date = str(date)
                    lines.append(f"{date} {sender}: {text}".strip())
            if len(lines) > 4:
                source_url = _source_locator(asset.display_path, service="telegram", chat=title, file=Path(asset.name).name, first_message=first_message, first_date=first_date)
                records.append(SourceRecord("telegram", f"Telegram {title}", "\n".join(lines), source_url=source_url, metadata={"asset": asset.display_path, "service": "Telegram"}))
    return records


def _telegram_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        pieces = []
        for item in value:
            if isinstance(item, str):
                pieces.append(item)
            elif isinstance(item, dict):
                pieces.append(str(item.get("text") or ""))
        return "".join(pieces).strip()
    return ""


def _parse_google_chat(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for asset in assets:
        if asset.suffix != ".json":
            continue
        path_hint = asset.display_path.lower()
        if hint not in {"", "google-chat", "hangouts"} and not any(marker in path_hint for marker in ("google chat", "hangouts", "takeout/chat")):
            continue
        if Path(asset.name).name.lower() not in {"messages.json", "conversation.json"} and "messages" not in asset.name.lower():
            continue
        try:
            payload = json.loads(asset.read_text())
        except (json.JSONDecodeError, RecursionError):
            continue
        messages = _message_list_from_payload(payload)
        if not messages:
            continue
        title = _path_parts(asset.name)[-2] if len(_path_parts(asset.name)) > 1 else "Google Chat"
        batch_records: list[SourceRecord] = []
        for batch in _batched(messages, 1500):
            lines = ["Source: Google Chat", f"Conversation: {title}", f"File: {asset.name}", "", "--- Messages ---"]
            first_created = ""
            for message in batch:
                if not isinstance(message, dict):
                    continue
                text = _message_text_value(message, ("text", "text_body", "message", "body", "content"))
                if not text:
                    continue
                sender = _person_name(message.get("creator") or message.get("sender") or message.get("from")) or str(message.get("sender_name") or "unknown")
                created = str(message.get("created_date") or message.get("createdDate") or message.get("create_time") or message.get("timestamp") or "").strip()
                if not first_created and created:
                    first_created = created
                lines.append(f"{created} {sender}: {text}".strip())
            if len(lines) > 5:
                source_url = _source_locator(asset.display_path, service="google-chat", conversation=title, file=Path(asset.name).name, first_created=first_created)
                batch_records.append(SourceRecord("google-chat", f"Google Chat {title}", "\n".join(lines), source_url=source_url, metadata={"asset": asset.display_path, "service": "Google Chat"}))
        records.extend(_label_record_parts(batch_records))
    return records


def _parse_teams(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for asset in assets:
        path_hint = asset.display_path.lower()
        if hint not in {"", "teams", "microsoft-teams"} and "teams" not in path_hint:
            continue
        if "teams" not in path_hint and hint not in {"teams", "microsoft-teams"}:
            continue
        if asset.suffix == ".json":
            records.extend(_teams_json_records(asset))
        elif asset.suffix == ".csv":
            records.extend(_teams_csv_records(asset))
    return records


def _teams_json_records(asset: SourceAsset) -> list[SourceRecord]:
    try:
        payload = json.loads(asset.read_text())
    except (json.JSONDecodeError, RecursionError):
        return []
    messages = _message_list_from_payload(payload)
    if not messages:
        return []
    title = _path_parts(asset.name)[-2] if len(_path_parts(asset.name)) > 1 else Path(asset.name).stem or "Teams"
    batch_records: list[SourceRecord] = []
    for batch in _batched(messages, 1500):
        lines = ["Source: Microsoft Teams", f"Conversation: {title}", f"File: {asset.name}", "", "--- Messages ---"]
        first_created = ""
        for message in batch:
            if not isinstance(message, dict):
                continue
            body = message.get("body")
            text = ""
            if isinstance(body, dict):
                text = _message_text_value(body, ("content", "text", "body"))
            text = text or _message_text_value(message, ("content", "message", "text", "body"))
            if not text:
                continue
            sender = _person_name(message.get("from") or message.get("sender") or message.get("user")) or str(message.get("userDisplayName") or message.get("from") or "unknown")
            created = str(message.get("createdDateTime") or message.get("created_at") or message.get("date") or message.get("timestamp") or "").strip()
            if not first_created and created:
                first_created = created
            lines.append(f"{created} {sender}: {_html_to_text(text)}".strip())
        if len(lines) <= 5:
            continue
        source_url = _source_locator(asset.display_path, service="teams", conversation=title, file=Path(asset.name).name, first_created=first_created)
        batch_records.append(SourceRecord("teams", f"Teams {title}", "\n".join(lines), source_url=source_url, metadata={"asset": asset.display_path, "service": "Microsoft Teams"}))
    return _label_record_parts(batch_records)


def _teams_csv_records(asset: SourceAsset) -> list[SourceRecord]:
    rows = _csv_rows(asset)
    if not rows:
        return []
    normalized_headers = {_normalize_header(key) for key in rows[0].keys()}
    if not normalized_headers & {"content", "message", "text", "body"}:
        return []
    title = _path_parts(asset.name)[-2] if len(_path_parts(asset.name)) > 1 else Path(asset.name).stem or "Teams"
    batch_records: list[SourceRecord] = []
    for batch in _batched(rows, 1500):
        lines = ["Source: Microsoft Teams", f"Conversation: {title}", f"File: {asset.name}", "", "--- Messages ---"]
        first_created = ""
        for row in batch:
            normalized = {_normalize_header(key): str(value or "").strip() for key, value in row.items()}
            text = normalized.get("content") or normalized.get("message") or normalized.get("text") or normalized.get("body") or ""
            if not text:
                continue
            sender = normalized.get("from") or normalized.get("sender") or normalized.get("user") or normalized.get("user_display_name") or "unknown"
            created = normalized.get("created_date_time") or normalized.get("created_at") or normalized.get("date") or normalized.get("timestamp") or ""
            if not first_created and created:
                first_created = created
            lines.append(f"{created} {sender}: {_html_to_text(text)}".strip())
        if len(lines) <= 5:
            continue
        source_url = _source_locator(asset.display_path, service="teams", conversation=title, file=Path(asset.name).name, first_created=first_created)
        batch_records.append(SourceRecord("teams", f"Teams {title}", "\n".join(lines), source_url=source_url, metadata={"asset": asset.display_path, "service": "Microsoft Teams"}))
    return _label_record_parts(batch_records)


def _parse_zoom_transcripts(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for asset in assets:
        if asset.suffix not in {".vtt", ".srt", ".txt"}:
            continue
        path_hint = asset.display_path.lower()
        text = asset.read_text()
        if hint not in {"", "zoom"} and "zoom" not in path_hint:
            continue
        if "zoom" not in path_hint and "WEBVTT" not in text[:1000] and "-->" not in text[:1000]:
            continue
        transcript = _format_transcript_text(text)
        if not transcript.strip():
            continue
        title = Path(asset.name).stem or "Zoom transcript"
        content = f"Source: Zoom\nTranscript: {title}\nFile: {asset.name}\n\n--- Transcript ---\n{transcript}"
        source_url = _source_locator(asset.display_path, service="zoom", transcript=title, file=Path(asset.name).name)
        records.append(SourceRecord("zoom", title, content, source_url=source_url, metadata={"asset": asset.display_path, "service": "Zoom"}))
    return records


def _message_list_from_payload(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("messages", "value", "items", "events"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _message_text_value(message: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = _message_text_value(value, ("text", "content", "body", "value"))
            if nested:
                return nested
    return ""


def _person_name(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("displayName", "display_name", "name", "email", "userPrincipalName"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
        user = value.get("user")
        if isinstance(user, dict):
            return _person_name(user)
    if isinstance(value, str):
        return value.strip()
    return ""


def _format_transcript_text(text: str) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.upper() == "WEBVTT":
            continue
        if re.match(r"^\d+$", line):
            continue
        if "-->" in line:
            continue
        if line.startswith(("NOTE", "STYLE", "REGION")):
            continue
        key = re.sub(r"\s+", " ", line).lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return "\n".join(lines)


def _parse_google_keep(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for asset in assets:
        if asset.suffix not in {".json", ".html", ".htm"}:
            continue
        if hint not in {"", "google-keep", "keep"} and "keep" not in asset.name.lower() and "keep" not in asset.display_path.lower():
            continue
        if asset.suffix in {".html", ".htm"}:
            text = _html_to_text(asset.read_text()).strip()
            if text and ("Google Keep" in asset.read_text() or "keep" in asset.display_path.lower()):
                title = Path(asset.name).stem or "Google Keep note"
                source_url = _source_locator(asset.display_path, service="google-keep", note=title, file=Path(asset.name).name)
                records.append(SourceRecord("google-keep", title, f"Source: Google Keep\nTitle: {title}\n\n{text}", source_url=source_url, metadata={"asset": asset.display_path, "service": "Google Keep"}))
            continue
        try:
            payload = json.loads(asset.read_text())
        except (json.JSONDecodeError, RecursionError):
            continue
        if not isinstance(payload, dict) or not any(key in payload for key in ("textContent", "listContent", "title")):
            continue
        title = str(payload.get("title") or "Google Keep note").strip()
        lines = ["Source: Google Keep", f"Title: {title}"]
        created = _google_keep_time(payload.get("createdTimestampUsec"))
        updated = _google_keep_time(payload.get("userEditedTimestampUsec"))
        if created:
            lines.append(f"Created: {created}")
        if updated and updated != created:
            lines.append(f"Updated: {updated}")
        text = str(payload.get("textContent") or "").strip()
        if text:
            lines.extend(["", text])
        list_items = payload.get("listContent") or []
        if isinstance(list_items, list) and list_items:
            lines.extend(["", "--- List ---"])
            for item in list_items:
                if isinstance(item, dict):
                    lines.append(f"- {item.get('text') or ''}".strip())
        source_url = _source_locator(asset.display_path, service="google-keep", note=title, file=Path(asset.name).name, created=created)
        records.append(SourceRecord("google-keep", title, "\n".join(lines), source_url=source_url, metadata={"asset": asset.display_path, "service": "Google Keep"}))
    return records


def _parse_twitter_archive(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for asset in assets:
        lowered = asset.name.lower()
        if hint not in {"", "twitter", "x", "twitter-x"} and "twitter" not in asset.display_path.lower() and "tweet" not in lowered:
            continue
        if not any(token in lowered for token in ("tweet", "direct-message", "direct_messages", "dm")):
            continue
        payload = _json_payload_from_archive_js(asset.read_text())
        if not isinstance(payload, list):
            continue
        if "direct" in lowered or "dm" in lowered:
            batch_records = [
                record
                for batch in _batched(payload, 300)
                if (record := _twitter_dm_record(asset, batch)) is not None
            ]
        else:
            batch_records = [
                record
                for batch in _batched(payload, 1000)
                if (record := _twitter_tweet_record(asset, batch)) is not None
            ]
        records.extend(_label_record_parts(batch_records))
    return records


def _twitter_tweet_record(asset: SourceAsset, payload: list[Any]) -> SourceRecord | None:
    lines = [f"Source: Twitter/X", f"Archive file: {asset.name}", "", "--- Tweets ---"]
    first_created = ""
    for item in payload:
        tweet = item.get("tweet") if isinstance(item, dict) else None
        if not isinstance(tweet, dict):
            continue
        text = str(tweet.get("full_text") or tweet.get("text") or "").strip()
        if not text:
            continue
        created = str(tweet.get("created_at") or "").strip()
        if not first_created and created:
            first_created = created
        favorite_count = str(tweet.get("favorite_count") or "").strip()
        retweet_count = str(tweet.get("retweet_count") or "").strip()
        metrics = " ".join(part for part in (f"likes={favorite_count}" if favorite_count else "", f"retweets={retweet_count}" if retweet_count else "") if part)
        lines.append(f"{created} {metrics}: {text}".strip())
    if len(lines) <= 4:
        return None
    source_url = _source_locator(asset.display_path, service="twitter-x", archive="tweets", file=Path(asset.name).name, first_created=first_created)
    return SourceRecord("twitter-x", "Twitter/X tweets", "\n".join(lines), source_url=source_url, metadata={"asset": asset.display_path, "service": "Twitter/X"})


def _twitter_dm_record(asset: SourceAsset, payload: list[Any]) -> SourceRecord | None:
    lines = [f"Source: Twitter/X", f"Archive file: {asset.name}", "", "--- Direct Messages ---"]
    first_conversation = ""
    first_created = ""
    for item in payload:
        conversation = item.get("dmConversation") if isinstance(item, dict) else None
        if not isinstance(conversation, dict):
            continue
        conversation_id = str(conversation.get("conversationId") or "").strip()
        if not first_conversation and conversation_id:
            first_conversation = conversation_id
        messages = conversation.get("messages") or []
        for message in messages:
            create = message.get("messageCreate") if isinstance(message, dict) else None
            if not isinstance(create, dict):
                continue
            text = str(create.get("text") or "").strip()
            if not text:
                continue
            sender = str(create.get("senderId") or "").strip()
            created = str(create.get("createdAt") or "").strip()
            if not first_created and created:
                first_created = created
            lines.append(f"{created} {conversation_id} {sender}: {text}".strip())
    if len(lines) <= 4:
        return None
    source_url = _source_locator(asset.display_path, service="twitter-x", archive="direct-messages", file=Path(asset.name).name, first_conversation=first_conversation, first_created=first_created)
    return SourceRecord("twitter-x", "Twitter/X direct messages", "\n".join(lines), source_url=source_url, metadata={"asset": asset.display_path, "service": "Twitter/X"})


def _parse_linkedin(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for asset in assets:
        if asset.suffix != ".csv":
            continue
        lowered = asset.name.lower()
        if hint not in {"", "linkedin"} and "linkedin" not in asset.display_path.lower():
            continue
        if "message" in lowered:
            builder, batch_size = _linkedin_messages_record, 1000
        elif "connection" in lowered:
            builder, batch_size = _linkedin_connections_record, 1500
        else:
            continue
        batch_records = [
            record
            for batch in _batched(_csv_rows(asset), batch_size)
            if (record := builder(asset, batch)) is not None
        ]
        records.extend(_label_record_parts(batch_records))
    return records


def _linkedin_messages_record(asset: SourceAsset, rows: list[dict[str, str]]) -> SourceRecord | None:
    if not rows:
        return None
    headers = {key.lower().replace(" ", "_") for key in rows[0].keys()}
    if "content" not in headers and "message" not in headers:
        return None
    lines = [f"Source: LinkedIn", f"File: {asset.name}", "", "--- Messages ---"]
    first_date = ""
    for row in rows:
        normalized = {_normalize_header(key): value for key, value in row.items()}
        content = str(normalized.get("content") or normalized.get("message") or "").strip()
        if not content:
            continue
        date = str(normalized.get("date") or normalized.get("created_at") or "").strip()
        if not first_date and date:
            first_date = date
        sender = str(normalized.get("from") or normalized.get("sender") or "").strip()
        title = str(normalized.get("conversation_title") or normalized.get("conversation_id") or "").strip()
        lines.append(f"{date} {title} {sender}: {content}".strip())
    if len(lines) <= 4:
        return None
    source_url = _source_locator(asset.display_path, service="linkedin", export="messages", file=Path(asset.name).name, first_date=first_date)
    return SourceRecord("linkedin", "LinkedIn messages", "\n".join(lines), source_url=source_url, metadata={"asset": asset.display_path, "service": "LinkedIn"})


def _linkedin_connections_record(asset: SourceAsset, rows: list[dict[str, str]]) -> SourceRecord | None:
    if not rows:
        return None
    lines = [f"Source: LinkedIn", f"File: {asset.name}", "", "--- Connections ---"]
    first_connection = ""
    for row in rows:
        normalized = {_normalize_header(key): value for key, value in row.items()}
        name = " ".join(
            str(normalized.get(key) or "").strip()
            for key in ("first_name", "last_name")
            if str(normalized.get(key) or "").strip()
        ) or str(normalized.get("name") or "").strip()
        company = str(normalized.get("company") or "").strip()
        position = str(normalized.get("position") or normalized.get("title") or "").strip()
        connected = str(normalized.get("connected_on") or normalized.get("date") or "").strip()
        email_address = str(normalized.get("email_address") or normalized.get("email") or "").strip()
        if name or company or position:
            if not first_connection and connected:
                first_connection = connected
            detail = " | ".join(part for part in (position, company, email_address, connected) if part)
            lines.append(f"{name}: {detail}".strip(": "))
    if len(lines) <= 4:
        return None
    source_url = _source_locator(asset.display_path, service="linkedin", export="connections", file=Path(asset.name).name, first_connection=first_connection)
    return SourceRecord("linkedin", "LinkedIn connections", "\n".join(lines), source_url=source_url, metadata={"asset": asset.display_path, "service": "LinkedIn"})


def _parse_single_asset(asset: SourceAsset, hint: str) -> list[SourceRecord]:
    suffix = asset.suffix
    if _is_slack_metadata_asset(asset):
        return []
    browser_json = _parse_browser_bookmarks_json_asset(asset, hint)
    if browser_json:
        return browser_json
    browser_history = _parse_browser_history_asset(asset, hint)
    if browser_history:
        return browser_history
    if suffix == ".mbox":
        return _parse_mbox(asset, hint)
    if Path(asset.name).name == "chat.db" and asset.filesystem_path:
        return _parse_imessage_db(asset, hint)
    if suffix in {".eml", ".emlx"}:
        record = _parse_email_asset(asset, hint)
        return [record] if record else []
    if suffix == ".ics":
        return _parse_calendar_asset(asset, hint)
    if suffix == ".vcf":
        return _parse_contacts_asset(asset, hint)
    if suffix in {".html", ".htm"} and _looks_like_bookmarks(asset.read_text()):
        return _parse_bookmarks_asset(asset, hint)
    if suffix == ".docx":
        text = _extract_docx(asset)
        return [_generic_record(asset, _infer_generic_source(asset, hint), text)] if text.strip() else []
    if suffix == ".pdf":
        text = _extract_pdf(asset)
        return [_generic_record(asset, _infer_generic_source(asset, hint), text)] if text.strip() else []
    text = asset.read_text() if suffix in TEXT_EXTENSIONS or hint else ""
    if not text.strip():
        return []
    if _looks_like_whatsapp(asset.name, text):
        return [_generic_record(asset, "whatsapp", _format_whatsapp(asset, text))]
    source = _infer_generic_source(asset, hint)
    if suffix == ".csv" and source == "contacts":
        return _label_record_parts(
            [_generic_record(asset, "contacts", part) for part in _format_contacts_csv(asset, text)]
        )
    if suffix == ".csv" and source in STRUCTURED_CSV_EXPORT_SOURCES:
        return _label_record_parts(
            [_generic_record(asset, source, part) for part in _format_csv_export(asset, text, source)]
        )
    if suffix in {".json", ".jsonl"} and source in STRUCTURED_JSON_EXPORT_SOURCES:
        return _label_record_parts(
            [_generic_record(asset, source, part) for part in _format_json_export(asset, text, source)]
        )
    return [_generic_record(asset, source, text)]


def _parse_calendar_asset(asset: SourceAsset, hint: str) -> list[SourceRecord]:
    text = asset.read_text()
    if "BEGIN:VEVENT" not in text:
        return []
    events = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", _unfold_ical(text), flags=re.DOTALL)
    source = hint or "calendar"
    batch_records: list[SourceRecord] = []
    for batch in _batched(events, 1000):
        lines = [f"Source: Calendar", f"File: {asset.name}", "", "--- Events ---"]
        first_summary = ""
        for event in batch:
            fields = _ical_fields(event)
            summary = fields.get("SUMMARY", "Untitled event").strip()
            if not first_summary:
                first_summary = summary
            start = fields.get("DTSTART", "").strip()
            end = fields.get("DTEND", "").strip()
            location = fields.get("LOCATION", "").strip()
            description = fields.get("DESCRIPTION", "").strip()
            organizer = fields.get("ORGANIZER", "").strip()
            attendees = [value for key, value in fields.items() if key == "ATTENDEE"]
            detail = [f"{start} - {end}".strip(" - "), summary]
            if location:
                detail.append(f"Location: {location}")
            if organizer:
                detail.append(f"Organizer: {organizer}")
            if attendees:
                detail.append(f"Attendees: {', '.join(attendees[:8])}")
            if description:
                detail.append(description)
            lines.append("\n".join(part for part in detail if part))
        if len(lines) <= 4:
            continue
        source_url = _source_locator(asset.display_path, service=source, file=Path(asset.name).name, first_event=first_summary)
        batch_records.append(SourceRecord(source, Path(asset.name).stem or "Calendar export", "\n\n".join(lines), source_url=source_url, metadata={"asset": asset.display_path, "service": "Calendar"}))
    return _label_record_parts(batch_records)


def _parse_contacts_asset(asset: SourceAsset, hint: str) -> list[SourceRecord]:
    text = asset.read_text()
    if "BEGIN:VCARD" not in text.upper():
        return []
    cards = re.findall(r"BEGIN:VCARD(.*?)END:VCARD", _unfold_ical(text), flags=re.DOTALL | re.IGNORECASE)
    source = hint or "contacts"
    batch_records: list[SourceRecord] = []
    for batch in _batched(cards, 2000):
        lines = [f"Source: Contacts", f"File: {asset.name}", "", "--- Contacts ---"]
        first_contact = ""
        for card in batch:
            fields = _ical_fields(card)
            name = fields.get("FN") or fields.get("N") or "Contact"
            if not first_contact:
                first_contact = str(name).strip()
            org = fields.get("ORG", "")
            title = fields.get("TITLE", "")
            notes = fields.get("NOTE", "")
            emails = [value for key, value in fields.items() if key == "EMAIL"]
            phones = [value for key, value in fields.items() if key == "TEL"]
            urls = [value for key, value in fields.items() if key == "URL"]
            detail = [str(name).strip()]
            for label, values in (("Title", [title]), ("Org", [org]), ("Email", emails[:4]), ("Phone", phones[:4]), ("URL", urls[:4]), ("Note", [notes])):
                value = ", ".join(str(item).strip() for item in values if str(item).strip())
                if value:
                    detail.append(f"{label}: {value}")
            lines.append("\n".join(detail))
        if len(lines) <= 4:
            continue
        source_url = _source_locator(asset.display_path, service=source, file=Path(asset.name).name, first_contact=first_contact)
        batch_records.append(SourceRecord(source, Path(asset.name).stem or "Contacts export", "\n\n".join(lines), source_url=source_url, metadata={"asset": asset.display_path, "service": "Contacts"}))
    return _label_record_parts(batch_records)


def _parse_bookmarks_asset(asset: SourceAsset, hint: str) -> list[SourceRecord]:
    text = asset.read_text()
    entries = re.findall(r"<A\s+[^>]*HREF=[\"']?([^\"'\s>]+)[^>]*>(.*?)</A>", text, flags=re.IGNORECASE | re.DOTALL)
    if not entries:
        return []
    source = hint or "browser-bookmarks"
    batch_records: list[SourceRecord] = []
    for batch in _batched(entries, 3000):
        lines = [f"Source: Browser Bookmarks", f"File: {asset.name}", "", "--- Bookmarks ---"]
        for url, raw_title in batch:
            title = _html_to_text(raw_title).strip() or url
            lines.append(f"{title} - {html.unescape(url)}")
        source_url = _source_locator(asset.display_path, service=source, file=Path(asset.name).name)
        batch_records.append(SourceRecord(source, Path(asset.name).stem or "Browser bookmarks", "\n".join(lines), source_url=source_url, metadata={"asset": asset.display_path, "service": "Browser Bookmarks"}))
    return _label_record_parts(batch_records)


def _parse_browser_bookmarks_json_asset(asset: SourceAsset, hint: str) -> list[SourceRecord]:
    basename = Path(asset.name).name.lower()
    if basename != "bookmarks" and not (asset.suffix == ".json" and "bookmarks" in asset.display_path.lower()):
        return []
    try:
        payload = json.loads(asset.read_text())
    except (json.JSONDecodeError, RecursionError):
        return []
    roots = payload.get("roots") if isinstance(payload, dict) else None
    if not isinstance(roots, dict):
        return []
    entries: list[tuple[str, str, str]] = []
    for root_name, root in roots.items():
        try:
            _collect_browser_bookmark_entries(str(root_name), root, entries)
        except RecursionError:
            # The depth cap should prevent this, but skip a single pathological
            # root rather than losing the whole bookmarks file if it slips through.
            continue
    if not entries:
        return []
    source = hint or "browser-bookmarks"
    batch_records: list[SourceRecord] = []
    for batch in _batched(entries, 3000):
        lines = [f"Source: Browser Bookmarks", f"File: {asset.name}", "", "--- Bookmarks ---"]
        for folder, title, url in batch:
            prefix = f"{folder}: " if folder else ""
            lines.append(f"{prefix}{title} - {url}")
        source_url = _source_locator(asset.display_path, service=source, file=Path(asset.name).name)
        batch_records.append(SourceRecord(source, Path(asset.name).stem or "Browser bookmarks", "\n".join(lines), source_url=source_url, metadata={"asset": asset.display_path, "service": "Browser Bookmarks"}))
    return _label_record_parts(batch_records)


def _collect_browser_bookmark_entries(folder: str, node: Any, entries: list[tuple[str, str, str]], depth: int = 0) -> None:
    if not isinstance(node, dict):
        return
    node_type = str(node.get("type") or "")
    name = str(node.get("name") or "").strip()
    if node_type == "url":
        url = str(node.get("url") or "").strip()
        if url:
            entries.append((folder, name or url, url))
        return
    if depth >= MAX_PARSE_NESTING_DEPTH:
        # Runaway nesting: keep what we have and stop descending rather than
        # overflowing the stack on a crafted bookmark tree.
        return
    child_folder = " / ".join(part for part in (folder, name) if part)
    for child in node.get("children") or []:
        _collect_browser_bookmark_entries(child_folder, child, entries, depth + 1)


def _parse_browser_history_asset(asset: SourceAsset, hint: str) -> list[SourceRecord]:
    basename = Path(asset.name).name.lower()
    path_hint = asset.display_path.lower()
    if basename not in {"history", "places.sqlite"} and not (asset.suffix in {".sqlite", ".sqlite3", ".db"} and ("history" in path_hint or "places" in path_hint or "browser" in path_hint)):
        return []
    rows = _browser_history_rows(asset)
    if not rows:
        return []
    source = hint or "browser-history"
    batch_records: list[SourceRecord] = []
    for batch in _batched(rows, 1000):
        lines = [f"Source: Browser History", f"File: {asset.name}", "", "--- Recent Visits ---"]
        for title, url, visits in batch:
            visit_text = f" ({visits} visits)" if visits else ""
            lines.append(f"{title or url} - {url}{visit_text}")
        source_url = _source_locator(asset.display_path, service=source, file=Path(asset.name).name)
        batch_records.append(SourceRecord(source, Path(asset.name).stem or "Browser history", "\n".join(lines), source_url=source_url, metadata={"asset": asset.display_path, "service": "Browser History"}))
    return _label_record_parts(batch_records)


def _browser_history_rows(asset: SourceAsset) -> list[tuple[str, str, int]]:
    db_path = asset.filesystem_path
    temp_path: Path | None = None
    if db_path is None:
        data = asset.read_bytes()
        if not data:
            return []
        temp = tempfile.NamedTemporaryFile(prefix="cortex-history-", suffix=".sqlite", delete=False)
        try:
            temp.write(data)
            temp.close()
            temp_path = Path(temp.name)
            db_path = temp_path
        finally:
            try:
                temp.close()
            except Exception:
                pass
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            chrome_rows = conn.execute(
                """
                SELECT title, url, visit_count
                FROM urls
                WHERE url IS NOT NULL AND url != ''
                ORDER BY last_visit_time DESC
                """
            ).fetchall()
            if chrome_rows:
                return [(str(row[0] or ""), str(row[1] or ""), int(row[2] or 0)) for row in chrome_rows]
        except sqlite3.Error:
            pass
        try:
            firefox_rows = conn.execute(
                """
                SELECT title, url, visit_count
                FROM moz_places
                WHERE url IS NOT NULL AND url != ''
                ORDER BY last_visit_date DESC
                """
            ).fetchall()
            return [(str(row[0] or ""), str(row[1] or ""), int(row[2] or 0)) for row in firefox_rows]
        except sqlite3.Error:
            return []
    except sqlite3.Error:
        return []
    finally:
        if conn is not None:
            conn.close()
        if temp_path:
            try:
                temp_path.unlink()
            except OSError:
                pass


def _looks_like_bookmarks(text: str) -> bool:
    lowered = text[:8000].lower()
    if "netscape-bookmark-file" in lowered or "<h1>bookmarks" in lowered:
        return True
    return (
        "<dl" in lowered
        and "<dt" in lowered
        and bool(re.search(r"<a\s+[^>]*href=", lowered))
        and any(marker in lowered for marker in ("add_date", "icon_uri", "personal_toolbar_folder", "<h3"))
    )


def _format_csv_export(asset: SourceAsset, text: str, source: str) -> list[str]:
    """Format a structured CSV export as one text per batch of 1000 rows.

    Returns `[text]` untouched when the rows cannot be parsed. Row numbering is
    global across batches so `Row 1001` stays `Row 1001` in part 2.
    """
    rows = _csv_rows(asset)
    if not rows:
        return [text]
    texts: list[str] = []
    for batch in _batched(list(enumerate(rows, start=1)), 1000):
        lines = [f"Source: {source}", f"Structured CSV export: {asset.name}", "", "--- Rows ---"]
        for index, row in batch:
            parts = []
            for key, value in row.items():
                cleaned = str(value or "").strip()
                if cleaned:
                    parts.append(f"{key}: {cleaned}")
            if parts:
                lines.append(f"Row {index}\n" + "\n".join(parts[:20]))
        texts.append("\n\n".join(lines))
    return texts


def _format_json_export(asset: SourceAsset, text: str, source: str) -> list[str]:
    """Format a structured JSON export as one text per batch of 1000 rows.

    Returns `[text]` untouched when no structured rows can be extracted.
    """
    rows = _json_export_rows(asset, text, source)
    if not rows:
        return [text]
    texts: list[str] = []
    for batch in _batched(list(enumerate(rows, start=1)), 1000):
        lines = [f"Source: {source}", f"File: {asset.name}", "", "--- Rows ---"]
        for index, row in batch:
            try:
                parts = _structured_row_parts(row)
            except RecursionError:
                # The depth cap should prevent this, but skip a single pathological
                # row rather than losing the whole export if it slips through.
                continue
            if parts:
                lines.append(f"Row {index}\n" + "\n".join(parts[:24]))
        if len(lines) > 4:
            texts.append("\n\n".join(lines))
    return texts or [text]


def _json_export_rows(asset: SourceAsset, text: str, source: str) -> list[Any]:
    if asset.suffix == ".jsonl":
        return _jsonl_export_rows(text, source)
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        return _jsonl_export_rows(text, source)
    return _json_rows_from_payload(payload, source)


def _jsonl_export_rows(text: str, source: str) -> list[Any]:
    rows: list[Any] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, RecursionError):
            continue
        rows.extend(_json_rows_from_payload(payload, source))
    return rows


def _json_rows_from_payload(payload: Any, source: str, depth: int = 0) -> list[Any]:
    if isinstance(payload, list):
        return [_json_row_value(item) for item in payload]
    if not isinstance(payload, dict):
        return []
    if depth >= MAX_PARSE_NESTING_DEPTH:
        # Runaway nesting: stop descending into a crafted deeply-nested export
        # rather than overflowing the stack. Degrade to no rows for this branch.
        return []

    for key in STRUCTURED_JSON_COLLECTION_KEYS:
        if key not in payload:
            continue
        rows = _json_rows_from_collection(payload.get(key), source, depth + 1)
        if rows:
            return rows

    if _looks_like_structured_json_row(payload, source):
        return [_json_row_value(payload)]

    for value in payload.values():
        rows = _json_rows_from_collection(value, source, depth + 1)
        if rows:
            return rows

    return []


def _json_rows_from_collection(value: Any, source: str, depth: int = 0) -> list[Any]:
    if isinstance(value, list):
        return [_json_row_value(item) for item in value]
    if isinstance(value, dict):
        return _json_rows_from_payload(value, source, depth)
    return []


def _json_row_value(value: Any) -> Any:
    if isinstance(value, dict):
        node = value.get("node")
        if isinstance(node, dict):
            return node
        issue = value.get("issue")
        if isinstance(issue, dict) and len(value) <= 3:
            return issue
    return value


def _looks_like_structured_json_row(value: dict[str, Any], source: str) -> bool:
    keys = {_normalize_header(key) for key in value}
    if source == "jira" and {"key", "fields"} & keys:
        return True
    if source == "linear" and {"identifier", "title", "description", "state"} & keys:
        return True
    if source == "github" and {"number", "title", "body", "html_url", "pull_request"} & keys:
        return True
    return bool(keys & {"key", "identifier", "number", "title", "summary", "body", "description", "content", "text"})


def _structured_row_parts(row: Any) -> list[str]:
    if not isinstance(row, dict):
        value = _structured_value_text(row)
        return [f"value: {value}"] if value else []

    fields: list[tuple[str, Any]] = []
    seen: set[str] = set()

    def add(label: str, value: Any) -> None:
        key = str(label or "").strip()
        if not key:
            return
        normalized = _normalize_header(key)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        fields.append((key, value))

    for key in STRUCTURED_ROW_KEY_ORDER:
        if key in row:
            add(key, row[key])

    nested_fields = row.get("fields")
    if isinstance(nested_fields, dict):
        for key in STRUCTURED_ROW_KEY_ORDER:
            if key in nested_fields:
                add(key, nested_fields[key])

    for key, value in row.items():
        if key == "fields" or _normalize_header(key) in seen:
            continue
        if isinstance(value, dict) and not _structured_dict_display(value):
            for child_key, child_value in value.items():
                add(f"{key}.{child_key}", child_value)
        else:
            add(key, value)

    if isinstance(nested_fields, dict):
        for key, value in nested_fields.items():
            if _normalize_header(key) in seen:
                continue
            add(key, value)

    parts: list[str] = []
    for key, value in fields:
        cleaned = _structured_value_text(value)
        if cleaned:
            parts.append(f"{key}: {cleaned}")
    return parts


def _structured_value_text(value: Any, depth: int = 0) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _clean_structured_value(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if depth >= MAX_PARSE_NESTING_DEPTH:
        # Runaway nesting: stop descending into a crafted deeply-nested row
        # rather than overflowing the stack. Whatever we return here is dropped
        # into the parent join, so we simply degrade to empty for this branch.
        return ""
    if isinstance(value, list):
        parts = [_structured_value_text(item, depth + 1) for item in value]
        return "; ".join(part for part in parts if part)
    if isinstance(value, dict):
        display = _structured_dict_display(value)
        if display:
            return display
        text = _json_text_content(value, depth)
        if text:
            return _clean_structured_value(text)
        try:
            return _clean_structured_value(json.dumps(value, ensure_ascii=False, sort_keys=True))
        except (TypeError, ValueError):
            return ""
    return _clean_structured_value(str(value))


def _structured_dict_display(value: dict[str, Any]) -> str:
    for key in STRUCTURED_DISPLAY_KEYS:
        item = value.get(key)
        if isinstance(item, (str, int, float, bool)) and str(item).strip():
            return _structured_value_text(item)
    return ""


def _json_text_content(value: Any, depth: int = 0) -> str:
    parts: list[str] = []

    def collect(item: Any, level: int) -> None:
        if isinstance(item, str):
            if item.strip():
                parts.append(item.strip())
            return
        if level >= MAX_PARSE_NESTING_DEPTH:
            # Runaway nesting: keep the text gathered so far and stop descending
            # rather than overflowing the stack on a crafted 'content' chain.
            return
        if isinstance(item, list):
            for child in item:
                collect(child, level + 1)
            return
        if isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
            for key in ("content", "paragraphs", "blocks"):
                if key in item:
                    collect(item.get(key), level + 1)

    collect(value, depth)
    return " ".join(parts)


def _clean_structured_value(value: str) -> str:
    text = _html_to_text(value) if "<" in value and ">" in value else value
    return re.sub(r"\s+", " ", text).strip()


def _format_contacts_csv(asset: SourceAsset, text: str) -> list[str]:
    """Format a contacts CSV as one text per batch of 2000 rows.

    Returns `[text]` untouched when no contact rows can be extracted.
    """
    rows = _csv_rows(asset)
    if not rows:
        return [text]
    texts: list[str] = []
    for batch in _batched(rows, 2000):
        lines = [f"Source: Contacts", f"Structured contacts export: {asset.name}", "", "--- Contacts ---"]
        for row in batch:
            normalized = {_normalize_header(key): str(value or "").strip() for key, value in row.items()}
            name = normalized.get("name") or normalized.get("full_name") or " ".join(
                part for part in (normalized.get("first_name", ""), normalized.get("last_name", "")) if part
            )
            email_values = [
                value
                for key, value in normalized.items()
                if value and ("email" in key or "e_mail" in key)
            ][:4]
            phone_values = [value for key, value in normalized.items() if value and "phone" in key][:4]
            org = normalized.get("organization") or normalized.get("company") or normalized.get("org") or ""
            title = normalized.get("title") or normalized.get("job_title") or ""
            notes = normalized.get("notes") or normalized.get("note") or ""
            detail = [name.strip() or "Contact"]
            for label, values in (("Title", [title]), ("Org", [org]), ("Email", email_values), ("Phone", phone_values), ("Note", [notes])):
                value = ", ".join(item for item in values if item)
                if value:
                    detail.append(f"{label}: {value}")
            if len(detail) > 1:
                lines.append("\n".join(detail))
        if len(lines) > 4:
            texts.append("\n\n".join(lines))
    return texts or [text]


def _csv_rows(asset: SourceAsset) -> list[dict[str, str]]:
    try:
        return list(csv.DictReader(asset.read_text().splitlines()))
    except csv.Error:
        return []


def _normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _json_payload_from_archive_js(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except (json.JSONDecodeError, RecursionError):
            return None
    marker = "="
    if marker in stripped:
        stripped = stripped.split(marker, 1)[1].strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].strip()
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, RecursionError):
        return None


def _unfold_ical(text: str) -> str:
    return re.sub(r"\r?\n[ \t]", "", text)


def _ical_fields(block: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw_line in block.splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        key = key.split(";", 1)[0].upper()
        value = html.unescape(value.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";")).strip()
        if not value:
            continue
        if key in fields:
            fields[key] = f"{fields[key]}, {value}"
        else:
            fields[key] = value
    return fields


def _parse_mbox(asset: SourceAsset, hint: str) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    if asset.filesystem_path:
        try:
            box = mailbox.mbox(asset.filesystem_path)
        except (OSError, mailbox.Error):
            box = []
        for index, message in enumerate(box):
            record = _email_record(message, f"{asset.display_path}#{index}", hint)
            if record:
                records.append(record)
        if records:
            return records
    text = asset.read_text()
    chunks = re.split(r"(?m)^From .*$", text)
    for index, chunk in enumerate(chunks):
        if not chunk.strip() or "\nSubject:" not in chunk[:2000]:
            continue
        try:
            message = email.message_from_string(chunk.lstrip(), policy=policy.default)
        except Exception:
            continue
        record = _email_record(message, f"{asset.display_path}#{index}", hint)
        if record:
            records.append(record)
    return records


def _parse_email_asset(asset: SourceAsset, hint: str) -> SourceRecord | None:
    data = asset.read_bytes()
    if asset.suffix == ".emlx":
        data = b"\n".join(data.splitlines()[1:])
    try:
        message = email.message_from_bytes(data, policy=policy.default)
    except Exception:
        return None
    return _email_record(message, asset.display_path, hint)


def _parse_imessage_db(asset: SourceAsset, hint: str) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    if not asset.filesystem_path:
        return records
    try:
        conn = sqlite3.connect(f"file:{asset.filesystem_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
              message.text,
              message.date,
              message.is_from_me,
              handle.id AS handle_id,
              chat.display_name AS chat_name
            FROM message
            LEFT JOIN handle ON handle.ROWID = message.handle_id
            LEFT JOIN chat_message_join cmj ON cmj.message_id = message.ROWID
            LEFT JOIN chat ON chat.ROWID = cmj.chat_id
            WHERE message.text IS NOT NULL AND length(message.text) > 0
            ORDER BY message.date ASC
            """
        ).fetchall()
    except sqlite3.Error:
        return records
    finally:
        try:
            conn.close()
        except Exception:
            pass
    # Rows arrive chronologically (ASC), so each chat's line list is already in
    # order; no reversal needed.
    grouped: dict[str, list[str]] = {}
    for row in rows:
        chat = row["chat_name"] or row["handle_id"] or "Messages"
        sender = "me" if row["is_from_me"] else (row["handle_id"] or "them")
        date = _apple_message_time(row["date"])
        grouped.setdefault(chat, []).append(f"{date} {sender}: {row['text']}".strip())
    for chat, lines in grouped.items():
        # A single large conversation becomes multiple `(part i/n)` records instead
        # of being dropped; small chats stay a single unlabeled record.
        chat_records: list[SourceRecord] = []
        for batch in _batched(lines, 1500):
            content = f"Source: Messages\nChat: {chat}\nFile: {asset.display_path}\n\n--- Messages ---\n" + "\n".join(batch)
            first_message_at = batch[0].split(" ", 1)[0] if batch else ""
            source_url = _source_locator(asset.display_path, service="messages", chat=chat, file=Path(asset.name).name, first_message_at=first_message_at)
            chat_records.append(SourceRecord("messages", f"Messages {chat}", content, source_url=source_url, metadata={"asset": asset.display_path, "service": "Messages", "chat": chat}))
        records.extend(_label_record_parts(chat_records))
    return records


def _email_record(message: email.message.EmailMessage, display_path: str, hint: str) -> SourceRecord | None:
    subject = str(message.get("subject") or "Email").strip()
    sender = str(message.get("from") or "").strip()
    recipients = str(message.get("to") or "").strip()
    date = str(message.get("date") or "").strip()
    body = _email_body(message)
    if not body.strip():
        return None
    lines = ["Source: Email", f"Subject: {subject}"]
    if sender:
        lines.append(f"From: {sender}")
    if recipients:
        lines.append(f"To: {recipients}")
    if date:
        lines.append(f"Date: {date}")
    sender_label = _email_sender_label(sender)
    if sender_label:
        lines.extend(["", "--- Body ---"])
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if line:
                lines.append(f"{sender_label}: {line}")
            else:
                lines.append("")
    else:
        lines.extend(["", body])
    source = hint if hint and hint not in {"gmail", "email"} else "email"
    source_url = _source_locator(display_path, service=source, subject=subject, message_id=message.get("message-id"))
    return SourceRecord(source, subject, "\n".join(lines), source_url=source_url, metadata={"asset": display_path, "service": "Email"})


def _email_sender_label(sender: str) -> str:
    name, address = parseaddr(sender)
    label = name or address.split("@", 1)[0] or "Sender"
    label = re.sub(r"[^A-Za-z0-9 _.'-]+", " ", label).strip()
    if not label:
        return "Sender"
    if label[:1].islower():
        label = label[:1].upper() + label[1:]
    return label[:40]


def _email_body(message: email.message.EmailMessage) -> str:
    if message.is_multipart():
        parts = _email_body_parts(message)
        deduped: list[str] = []
        seen: set[str] = set()
        for part in parts:
            cleaned = part.strip()
            key = re.sub(r"\s+", " ", cleaned).lower()
            if cleaned and key not in seen:
                seen.add(key)
                deduped.append(cleaned)
        return _clean_email_body("\n\n".join(deduped))
    try:
        payload = message.get_content()
    except Exception:
        return ""
    if message.get_content_type() == "text/html":
        return _clean_email_body(_html_to_text(str(payload)))
    return _clean_email_body(str(payload))


def _clean_email_body(value: str) -> str:
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned: list[str] = []
    blank_pending = False
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if _is_email_quote_boundary(lines, index) or _is_email_footer_boundary(line):
            break
        if _is_email_quoted_line(line) or _is_email_noise_line(line):
            continue
        if not line:
            blank_pending = bool(cleaned)
            continue
        if blank_pending:
            cleaned.append("")
            blank_pending = False
        cleaned.append(line)
    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    return "\n".join(cleaned).strip()


def _is_email_quote_boundary(lines: list[str], index: int) -> bool:
    line = lines[index].strip()
    if not line:
        return False
    lowered = line.lower().strip()
    if lowered in {"-----original message-----", "----- forwarded message -----", "begin forwarded message:"}:
        return True
    if re.match(r"^on .{8,240}wrote:$", line, flags=re.IGNORECASE):
        return True
    if re.match(r"^from:\s+.+", line, flags=re.IGNORECASE) and _starts_email_header_block(lines[index + 1 : index + 7]):
        return True
    return False


def _starts_email_header_block(next_lines: list[str]) -> bool:
    header_count = 0
    for raw_line in next_lines:
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^(sent|date|to|cc|subject):\s+.+", line, flags=re.IGNORECASE):
            header_count += 1
            if header_count >= 1:
                return True
            continue
        if header_count:
            break
    return False


def _is_email_footer_boundary(line: str) -> bool:
    if not line:
        return False
    lowered = re.sub(r"\s+", " ", line.lower()).strip()
    footer_patterns = (
        r"^--\s*$",
        r"^sent from my (iphone|ipad|android|mobile)",
        r"^get outlook for ",
        r"^this email was sent to ",
        r"^you are receiving this email because ",
        r"^unsubscribe\b",
        r"\bunsubscribe from\b",
        r"\bmanage (your )?(email )?preferences\b",
        r"\bview this email in (your )?browser\b",
        r"^confidentiality notice\b",
        r"^this (email|message) and any attachments",
        r"^this communication is confidential",
        r"^please consider the environment before printing",
    )
    return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in footer_patterns)


def _is_email_quoted_line(line: str) -> bool:
    return bool(line and re.match(r"^>+", line))


def _is_email_noise_line(line: str) -> bool:
    lowered = line.lower().strip()
    if not lowered:
        return False
    return lowered in {"[image]", "[cid:image]", "[external email]"} or bool(re.match(r"^\[image:.*\]$", lowered))


def _email_body_parts(message: Any) -> list[str]:
    disposition = str(message.get_content_disposition() or "").lower()
    if disposition == "attachment":
        return []
    content_type = message.get_content_type()
    if content_type == "multipart/alternative":
        plain: list[str] = []
        html_parts: list[str] = []
        for part in _email_children(message):
            part_type = part.get_content_type()
            text = _email_single_part_text(part)
            if not text:
                continue
            if part_type == "text/plain":
                plain.append(text)
            elif part_type == "text/html":
                html_parts.append(text)
        return plain[:1] or html_parts[:1]
    if message.is_multipart():
        parts: list[str] = []
        for part in _email_children(message):
            parts.extend(_email_body_parts(part))
        return parts
    text = _email_single_part_text(message)
    return [text] if text else []


def _email_children(message: Any) -> list[Any]:
    try:
        return list(message.iter_parts())
    except AttributeError:
        payload = message.get_payload()
        return payload if isinstance(payload, list) else []


def _email_single_part_text(message: Any) -> str:
    if str(message.get_content_disposition() or "").lower() == "attachment":
        return ""
    content_type = message.get_content_type()
    if content_type not in {"text/plain", "text/html"}:
        return ""
    try:
        payload = message.get_content()
    except Exception:
        return ""
    return _html_to_text(str(payload)) if content_type == "text/html" else str(payload)


def _generic_record(asset: SourceAsset, source: str, text: str, title: str | None = None) -> SourceRecord:
    if title is None:
        title = Path(asset.name).stem or Path(asset.name).name
    cleaned = _clean_generic_text(asset, text)
    source_url = _generic_source_locator(asset, source, title)
    return SourceRecord(source, title, cleaned, source_url=source_url, metadata={"asset": asset.display_path, "service": source})


def _generic_source_locator(asset: SourceAsset, source: str, title: str) -> str:
    if source in {"docs", "file"}:
        return asset.display_path
    file_name = Path(asset.name).name
    if source == "notion":
        return _source_locator(asset.display_path, service=source, page=title, file=file_name)
    if source == "cloud-docs":
        provider = _source_provider(asset, {"google drive": "google-drive", "google docs": "google-drive", "onedrive": "onedrive", "dropbox paper": "dropbox-paper", "microsoft": "microsoft-365", "office 365": "microsoft-365", "takeout/drive": "google-drive", "takeout\\drive": "google-drive"})
        return _source_locator(asset.display_path, service=source, provider=provider, document=title, file=file_name)
    if source in {"github", "gitlab"}:
        repository = _source_container_after(asset, {source})
        return _source_locator(asset.display_path, service=source, repository=repository, file=file_name)
    if source in {"linear", "jira", "asana", "trello", "work-tools"}:
        workspace = _source_container_after(asset, {source})
        return _source_locator(asset.display_path, service=source, workspace=workspace, file=file_name)
    if source in {"apple-notes", "obsidian", "logseq", "roam", "knowledge-base", "readwise", "pocket", "instapaper", "raindrop", "structured-export", "whatsapp"}:
        return _source_locator(asset.display_path, service=source, file=file_name)
    return asset.display_path


def _clean_generic_text(asset: SourceAsset, text: str) -> str:
    suffix = asset.suffix
    if suffix in {".html", ".htm"}:
        text = _html_to_text(text)
    elif suffix == ".rtf":
        text = _strip_rtf(text)
    title = Path(asset.name).name
    return f"Source file: {title}\nPath: {asset.display_path}\n\n{text.strip()}"


def _infer_generic_source(asset: SourceAsset, hint: str) -> str:
    if hint:
        return hint
    lowered = asset.display_path.lower()
    name = asset.name.lower()
    if asset.suffix == ".ics" or "calendar" in lowered:
        return "calendar"
    if asset.suffix == ".vcf" or "contacts" in lowered or "address book" in lowered:
        return "contacts"
    if "linkedin" in lowered:
        return "linkedin"
    if "twitter" in lowered or "x archive" in lowered:
        return "twitter-x"
    if "chrome" in lowered or "safari" in lowered or "firefox" in lowered or "bookmarks" in lowered or "browser" in lowered:
        return "browser-bookmarks"
    if "notion" in lowered:
        return "notion"
    if "apple notes" in lowered or "notes export" in lowered or "bear" in lowered or "craft" in lowered or "ulysses" in lowered or "ia writer" in lowered:
        return "apple-notes"
    if "google drive" in lowered or "google docs" in lowered or "takeout/drive" in lowered or "takeout\\drive" in lowered:
        return "cloud-docs"
    if "microsoft" in lowered or "onedrive" in lowered or "office 365" in lowered or "outlook" in lowered or "dropbox paper" in lowered:
        return "cloud-docs"
    if "work tools" in lowered or "work-tools" in lowered or "worktools" in lowered:
        return "work-tools"
    if "obsidian" in lowered:
        return "obsidian"
    if "logseq" in lowered:
        return "logseq"
    if "roam" in lowered:
        return "roam"
    if "linear" in lowered or name.startswith("linear"):
        return "linear"
    if "jira" in lowered:
        return "jira"
    if "asana" in lowered:
        return "asana"
    if "trello" in lowered:
        return "trello"
    if "github" in lowered:
        return "github"
    if "gitlab" in lowered:
        return "gitlab"
    if "readwise" in lowered:
        return "readwise"
    if "pocket" in lowered:
        return "pocket"
    if "instapaper" in lowered:
        return "instapaper"
    if "raindrop" in lowered:
        return "raindrop"
    if asset.suffix in {".md", ".markdown", ".docx", ".rtf", ".pdf"}:
        return "docs"
    if asset.suffix in {".csv", ".json", ".jsonl"}:
        return "structured-export"
    return "file"


def _extract_docx(asset: SourceAsset) -> str:
    data = asset.read_bytes()
    if not data:
        return ""
    try:
        from io import BytesIO

        with zipfile.ZipFile(BytesIO(data)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
    except Exception:
        return ""
    text = re.sub(r"<[^>]+>", " ", xml)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def _extract_pdf(asset: SourceAsset) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
        from io import BytesIO
    except Exception:
        return ""
    try:
        reader = PdfReader(str(asset.filesystem_path)) if asset.filesystem_path else PdfReader(BytesIO(asset.read_bytes()))
        return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages)
    except Exception:
        return ""


def _looks_like_whatsapp(name: str, text: str) -> bool:
    lowered = name.lower()
    return "whatsapp" in lowered or bool(re.search(r"^\[?\d{1,2}[/-]\d{1,2}[/-]\d{2,4},?\s+\d{1,2}:\d{2}", text, re.MULTILINE))


def _format_whatsapp(asset: SourceAsset, text: str) -> str:
    return f"Source: WhatsApp\nExport: {asset.name}\n\n--- Messages ---\n{text.strip()}"


def _record_asset_key(record: SourceRecord) -> str:
    value = record.metadata.get("asset")
    return str(value) if value else ""


def _source_locator(display_path: str, **parts: Any) -> str:
    fragments = []
    for key, value in parts.items():
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        fragments.append(f"{quote(str(key), safe='')}={quote(text, safe='')}")
    if not fragments:
        return display_path
    return f"{display_path}#{'&'.join(fragments)}"


def _source_provider(asset: SourceAsset, markers: dict[str, str]) -> str:
    lowered = asset.display_path.lower().replace("::", "/")
    name = asset.name.lower().replace("::", "/")
    for marker, provider in markers.items():
        if marker in lowered or marker in name:
            return provider
    return ""


def _source_container_after(asset: SourceAsset, markers: set[str]) -> str:
    parts = _path_parts(asset.display_path.replace("::", "/"))
    lowered_markers = {marker.lower() for marker in markers}
    for index, part in enumerate(parts[:-1]):
        normalized = re.sub(r"[^a-z0-9]+", "-", part.lower()).strip("-")
        if normalized in lowered_markers:
            return parts[index + 1]
    return ""


def _normalize_source(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", value.strip().lower()).strip("-")


def _path_parts(name: str) -> list[str]:
    return [part for part in re.split(r"[\\/]+", name) if part]


def _is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def _clean_chat_text(value: str) -> str:
    value = re.sub(r"<@([A-Z0-9]+)>", r"@\1", value)
    value = re.sub(r"<#([A-Z0-9]+)\|([^>]+)>", r"#\2", value)
    value = re.sub(r"<([^|>]+)\|([^>]+)>", r"\2 (\1)", value)
    return html.unescape(value).strip()


def _slack_time(value: Any) -> str:
    try:
        timestamp = float(str(value).split(".")[0])
    except (TypeError, ValueError):
        return ""
    return _iso_from_unix(timestamp)


def _iso_from_unix(timestamp: float) -> str:
    try:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(timestamp, timezone.utc).replace(microsecond=0).isoformat()
    except Exception:
        return ""


def _apple_message_time(value: Any) -> str:
    try:
        raw = int(value)
    except (TypeError, ValueError):
        return ""
    if raw <= 0:
        return ""
    seconds = raw / 1_000_000_000 if raw > 10_000_000_000 else raw
    apple_epoch = 978_307_200
    return _iso_from_unix(apple_epoch + seconds)


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def _html_to_text(value: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(value)
    except Exception:
        return re.sub(r"<[^>]+>", " ", value)
    return html.unescape("\n".join(parser.parts))


def _strip_rtf(value: str) -> str:
    value = re.sub(r"\\'[0-9a-fA-F]{2}", " ", value)
    value = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", value)
    value = value.replace("{", " ").replace("}", " ")
    return re.sub(r"\s+", " ", value).strip()


# ===========================================================================
# Vendor MEMORY exports (the SHORT "what the AI remembers about you" lists)
# ---------------------------------------------------------------------------
# Distinct from the full conversation exports parsed above: these are the tiny
# bulleted fact lists a vendor surfaces as "Saved memories" / "Model set
# context" (ChatGPT), the Claude memory/profile summary, or Gemini's
# Personalization / "Things Gemini remembers" page. Users copy-paste them or
# download a small JSON/markdown/text file; each atomic fact becomes one
# VendorFact {text, vendor, captured_at?} that the import-diff engine compares
# against Cortex's own cited memory. Tolerant of the real shapes (JSON object,
# JSON list, markdown bullets, plain lines); a malformed blob yields [] and
# never raises.
# ===========================================================================

# Canonical vendor ids + display labels for the memory-list mode.
VENDOR_MEMORY_LABELS: dict[str, str] = {
    "chatgpt": "ChatGPT",
    "claude": "Claude",
    "gemini": "Gemini",
    "copilot": "Microsoft Copilot",
    "grok": "Grok",
    "perplexity": "Perplexity",
}

# Vendor aliases seen in filenames / copy-paste headers / hint strings.
_VENDOR_MEMORY_ALIASES: dict[str, str] = {
    "chatgpt": "chatgpt",
    "chat-gpt": "chatgpt",
    "openai": "chatgpt",
    "gpt": "chatgpt",
    "claude": "claude",
    "anthropic": "claude",
    "gemini": "gemini",
    "google-gemini": "gemini",
    "bard": "gemini",
    "copilot": "copilot",
    "microsoft-copilot": "copilot",
    "grok": "grok",
    "xai": "grok",
    "perplexity": "perplexity",
}

# A single atomic vendor fact is capped so a pasted paragraph can't smuggle a
# whole document in as one "fact"; the diff compares short sentence-like facts.
MAX_VENDOR_FACT_CHARS = 600
# Never build an unbounded diff from a giant paste — keep the top N facts in
# file order (dedup applied first) so the compare stays deterministic + bounded.
MAX_VENDOR_FACTS = 400

# Boilerplate the vendor pages wrap the actual list in — never a real fact.
_VENDOR_MEMORY_BOILERPLATE = (
    "saved memories",
    "saved memory",
    "model set context",
    "memory updated",
    "manage memory",
    "manage memories",
    "things gemini remembers",
    "what gemini remembers",
    "personalization",
    "here's what",
    "here is what",
    "what i remember",
    "what i know about you",
    "memory is on",
    "reference saved memories",
    "clear chatgpt's memory",
    "to forget",
    "no memories yet",
    "delete all",
)


def vendor_memory_label(vendor: str) -> str:
    """Display label for a vendor id, falling back to a title-cased id."""
    key = _normalize_vendor_hint(vendor)
    if key in VENDOR_MEMORY_LABELS:
        return VENDOR_MEMORY_LABELS[key]
    cleaned = re.sub(r"[^a-z0-9]+", " ", str(vendor or "").lower()).strip()
    return cleaned.title() if cleaned else "AI assistant"


def _normalize_vendor_hint(value: Any) -> str:
    """Map a free-form vendor hint / filename token to a canonical vendor id (or "")."""
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    if not normalized:
        return ""
    if normalized in _VENDOR_MEMORY_ALIASES:
        return _VENDOR_MEMORY_ALIASES[normalized]
    compact = normalized.replace("-", "")
    for alias, vendor in _VENDOR_MEMORY_ALIASES.items():
        if alias.replace("-", "") == compact:
            return vendor
    # A longer token like "chatgpt-memory-export" — match by contained vendor word.
    tokens = set(normalized.split("-"))
    for alias, vendor in _VENDOR_MEMORY_ALIASES.items():
        if alias in tokens:
            return vendor
    return ""


def _detect_vendor_from_text(text: str) -> str:
    """Best-effort vendor id from the export's own header/body (used when no hint)."""
    head = text[:2000].lower()
    for vendor in ("chatgpt", "claude", "gemini", "copilot", "grok", "perplexity"):
        if vendor in head:
            return vendor
    for alias, vendor in _VENDOR_MEMORY_ALIASES.items():
        if alias in head:
            return vendor
    return ""


def _is_vendor_memory_boilerplate(line: str) -> bool:
    lowered = line.strip().lower().rstrip(":.")
    if not lowered:
        return True
    if any(marker in lowered for marker in _VENDOR_MEMORY_BOILERPLATE):
        return True
    # A bare vendor name / page heading ("ChatGPT", "Memory") carries no fact.
    if lowered in VENDOR_MEMORY_LABELS or lowered in _VENDOR_MEMORY_ALIASES or lowered in {"memory", "memories", "context"}:
        return True
    return False


def _clean_vendor_fact_text(value: Any) -> str:
    """Normalize one candidate fact: strip bullet/number markers, collapse whitespace, bound length."""
    if isinstance(value, (dict, list)):
        return ""
    text = str(value or "")
    text = html.unescape(text).replace("•", " ").replace("–", "-").replace("—", "-")
    # Strip a leading list marker: "-", "*", "•", "1.", "1)", "[ ]"/"[x]" checkbox, "> " quote.
    text = re.sub(r"^\s*(?:[-*+•●▪]|\d{1,3}[.)]|\[[ xX]?\]|>)\s+", "", text)
    text = re.sub(r"\s+", " ", text).strip().strip("\"'")
    if len(text) > MAX_VENDOR_FACT_CHARS:
        text = text[:MAX_VENDOR_FACT_CHARS].rstrip() + "..."
    return text


def _vendor_fact(text: str, vendor: str, captured_at: str = "") -> dict[str, Any] | None:
    cleaned = _clean_vendor_fact_text(text)
    # A real memory fact is a short statement — a couple of words at minimum, not a stray token.
    if len(cleaned) < 4 or len(cleaned.split()) < 2:
        return None
    if _is_vendor_memory_boilerplate(cleaned):
        return None
    fact: dict[str, Any] = {"text": cleaned, "vendor": vendor}
    captured = str(captured_at or "").strip()
    if captured:
        fact["captured_at"] = captured[:64]
    return fact


def _vendor_facts_from_json(payload: Any, vendor: str, depth: int = 0) -> list[dict[str, Any]]:
    """Walk a vendor memory JSON payload, harvesting atomic facts.

    Handles the realistic shapes: a bare list of strings; a list of objects with
    a text/content/memory/value field (and an optional created/updated time); a
    top-level object whose "memories"/"saved_memories"/"facts"/"items"/"context"
    key holds the list; and, as a last resort, a scan of string leaves. Nesting
    is depth-capped so a crafted deep blob can't overflow the parser.
    """
    if depth >= MAX_PARSE_NESTING_DEPTH:
        return []
    facts: list[dict[str, Any]] = []
    if isinstance(payload, str):
        fact = _vendor_fact(payload, vendor)
        return [fact] if fact else []
    if isinstance(payload, list):
        for item in payload:
            facts.extend(_vendor_facts_from_json(item, vendor, depth + 1))
        return facts
    if isinstance(payload, dict):
        # Prefer an explicit collection key so we don't harvest metadata fields as facts.
        for key in (
            "memories", "saved_memories", "savedMemories", "memory", "facts",
            "items", "entries", "context", "model_set_context", "personalization",
            "data", "results",
        ):
            value = payload.get(key)
            if isinstance(value, (list, dict)):
                nested = _vendor_facts_from_json(value, vendor, depth + 1)
                if nested:
                    return nested
        # A single fact object: {text/content/memory/value, created_at?}.
        text = ""
        for key in ("text", "content", "memory", "fact", "value", "statement", "description", "summary"):
            candidate = payload.get(key)
            if isinstance(candidate, str) and candidate.strip():
                text = candidate
                break
        if text:
            captured = ""
            for key in ("captured_at", "created_at", "createdAt", "created", "updated_at", "updatedAt", "timestamp", "date"):
                stamp = payload.get(key)
                if isinstance(stamp, (str, int, float)) and str(stamp).strip():
                    captured = _iso_from_unix(float(stamp)) if isinstance(stamp, (int, float)) else str(stamp)
                    break
            fact = _vendor_fact(text, vendor, captured)
            if fact:
                facts.append(fact)
            return facts
        # No recognizable text key: scan string leaves so an unusual object still yields facts.
        for value in payload.values():
            if isinstance(value, str):
                fact = _vendor_fact(value, vendor)
                if fact:
                    facts.append(fact)
            elif isinstance(value, list):
                facts.extend(_vendor_facts_from_json(value, vendor, depth + 1))
    return facts


def _vendor_facts_from_text(text: str, vendor: str) -> list[dict[str, Any]]:
    """Parse a markdown / plain-text vendor memory dump into facts (one per bullet/line)."""
    facts: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Skip a markdown heading line entirely (## Saved memories) — it's a page label.
        if re.match(r"^#{1,6}\s", line):
            continue
        fact = _vendor_fact(line, vendor)
        if fact:
            facts.append(fact)
    return facts


def _dedupe_vendor_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop exact-duplicate facts (same normalized text), keeping first occurrence + its order."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for fact in facts:
        key = re.sub(r"\s+", " ", str(fact.get("text") or "").lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(fact)
    return unique[:MAX_VENDOR_FACTS]


def parse_vendor_memory_export(raw: Any, vendor_hint: str = "") -> dict[str, Any]:
    """Parse a SHORT vendor MEMORY export into atomic vendor facts.

    `raw` is the export as pasted/uploaded — a JSON string, an already-parsed
    JSON object/list, or plain/markdown text. `vendor_hint` names the vendor
    ("chatgpt"/"claude"/"gemini"/...) when known; otherwise it is sniffed from
    the content. Returns {"vendor", "vendor_label", "facts": [{text, vendor,
    captured_at?}], "count"}. Never raises: a malformed/empty blob yields an
    empty fact list. This is the "memory list" mode — deliberately separate from
    the full-conversation parsers, which expect message transcripts.
    """
    vendor = _normalize_vendor_hint(vendor_hint)
    facts: list[dict[str, Any]] = []

    payload: Any = raw
    text_form = ""
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = bytes(raw).decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        payload = raw
    if isinstance(raw, str):
        text_form = raw
        stripped = raw.strip()
        if stripped and stripped[0] in "[{":
            try:
                payload = json.loads(stripped)
            except (json.JSONDecodeError, RecursionError, ValueError):
                payload = raw  # not JSON after all — treat as text below
        else:
            payload = raw

    if not vendor:
        vendor = _detect_vendor_from_text(text_form) if text_form else ""

    try:
        if isinstance(payload, (dict, list)):
            facts = _vendor_facts_from_json(payload, vendor or "")
        elif isinstance(payload, str):
            facts = _vendor_facts_from_text(payload, vendor or "")
    except (RecursionError, ValueError, TypeError):
        facts = []

    facts = _dedupe_vendor_facts(facts)
    # Stamp the resolved vendor onto each fact so a mixed/unhinted export is still attributed.
    for fact in facts:
        if not fact.get("vendor"):
            fact["vendor"] = vendor or ""
    return {
        "vendor": vendor or "",
        "vendor_label": vendor_memory_label(vendor) if vendor else "AI assistant",
        "facts": facts,
        "count": len(facts),
    }


def normalize_vendor_facts(facts: Any, vendor_hint: str = "") -> list[dict[str, Any]]:
    """Coerce a client-supplied pre-parsed fact list into clean VendorFact dicts.

    The import-diff endpoint accepts EITHER raw export text OR a caller-parsed
    list of facts (strings or {text, vendor?, captured_at?} objects); this
    normalizes the latter through the same cleaning/bounding/dedup path so both
    entry points behave identically. Never raises.
    """
    vendor = _normalize_vendor_hint(vendor_hint)
    out: list[dict[str, Any]] = []
    if not isinstance(facts, list):
        return out
    for item in facts:
        if isinstance(item, str):
            fact = _vendor_fact(item, vendor or "")
        elif isinstance(item, dict):
            item_vendor = _normalize_vendor_hint(item.get("vendor")) or vendor or ""
            text = item.get("text") or item.get("content") or item.get("memory") or item.get("fact") or ""
            fact = _vendor_fact(str(text), item_vendor, str(item.get("captured_at") or item.get("created_at") or ""))
        else:
            fact = None
        if fact:
            out.append(fact)
    return _dedupe_vendor_facts(out)
