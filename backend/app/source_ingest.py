from __future__ import annotations

import csv
import email
import html
import json
import mailbox
import re
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from email import policy
from email.utils import parseaddr
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote


MAX_TEXT_BYTES = 12_000_000
MAX_RECORD_CHARS = 185_000

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
        "id": "knowledge-base",
        "name": "Knowledge bases",
        "formats": ["Obsidian", "Roam", "Logseq", "Readwise, Pocket, Instapaper CSV/JSON"],
        "status": "generic",
    },
]


@dataclass
class SourceAsset:
    name: str
    display_path: str
    data: bytes | None = None
    filesystem_path: Path | None = None

    @property
    def suffix(self) -> str:
        return Path(self.name).suffix.lower()

    def read_bytes(self) -> bytes:
        if self.data is not None:
            return self.data
        if self.filesystem_path is None:
            return b""
        if self.filesystem_path.stat().st_size > MAX_TEXT_BYTES:
            return b""
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
            content=self.content[:MAX_RECORD_CHARS] + "\n\n[Truncated by Cortex importer.]",
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


def import_source_records(paths: Iterable[str], source_hint: str = "", max_records: int = 1000) -> list[SourceRecord]:
    assets = _collect_assets(paths)
    hint = _normalize_source(source_hint)
    records: list[SourceRecord] = []

    for parser in (
        _parse_chatgpt,
        _parse_claude,
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
        if len(records) >= max_records:
            break
        if asset.display_path in consumed:
            continue
        parsed = _parse_single_asset(asset, hint)
        if parsed:
            records.extend(parsed)

    bounded = [record.bounded() for record in records if record.content.strip()]
    return bounded[:max_records]


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


def _assets_from_zip(path: Path) -> list[SourceAsset]:
    assets: list[SourceAsset] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir() or info.file_size > MAX_TEXT_BYTES:
                    continue
                name = info.filename
                if Path(name).name.startswith("."):
                    continue
                if Path(name).suffix.lower() == ".zip":
                    continue
                try:
                    data = archive.read(info)
                except (KeyError, RuntimeError, zipfile.BadZipFile):
                    continue
                assets.append(SourceAsset(name=name, display_path=f"{path}::{name}", data=data))
    except zipfile.BadZipFile:
        return [SourceAsset(name=path.name, display_path=str(path), filesystem_path=path)]
    return assets


def _parse_chatgpt(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for asset in assets:
        if Path(asset.name).name != "conversations.json":
            continue
        try:
            payload = json.loads(asset.read_text())
        except json.JSONDecodeError:
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
        role = ((message.get("author") or {}).get("role") or "unknown").strip()
        if role == "system":
            continue
        content = _chatgpt_content(message.get("content") or {})
        if not content:
            continue
        created = float(message.get("create_time") or 0)
        messages.append((created, f"{role}: {content}"))
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


def _parse_claude(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for asset in assets:
        if Path(asset.name).name not in {"conversations.json", "chats.json"}:
            continue
        try:
            payload = json.loads(asset.read_text())
        except json.JSONDecodeError:
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
                    lines.append(f"{sender}: {text}")
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


def _parse_slack(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    slack_assets = [asset for asset in assets if asset.suffix == ".json" and _looks_like_slack_path(asset.name)]
    if not slack_assets and hint != "slack":
        return []
    users = _slack_users(assets)
    records: list[SourceRecord] = []
    for asset in slack_assets:
        try:
            payload = json.loads(asset.read_text())
        except json.JSONDecodeError:
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
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, list):
            return {}
        return {str(item.get("id")): str(item.get("real_name") or item.get("name") or item.get("id")) for item in payload if isinstance(item, dict)}
    return {}


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
        text = asset.read_text()
        rows = list(csv.DictReader(text.splitlines()))
        if not rows or not any("Contents" in row or "content" in row for row in rows):
            continue
        channel = _path_parts(asset.name)[-2] if len(_path_parts(asset.name)) > 1 else "Discord"
        lines = [f"Source: Discord", f"Channel: {channel}", f"File: {asset.name}", "", "--- Messages ---"]
        for row in rows:
            timestamp = row.get("Timestamp") or row.get("timestamp") or ""
            content = row.get("Contents") or row.get("content") or ""
            attachments = row.get("Attachments") or row.get("attachments") or ""
            content = content.strip()
            if attachments.strip():
                content = f"{content}\nAttachments: {attachments}".strip()
            if content:
                lines.append(f"{timestamp}: {content}".strip())
        if len(lines) > 5:
            records.append(SourceRecord("discord", f"Discord {channel}", "\n".join(lines), source_url=asset.display_path, metadata={"asset": asset.display_path, "service": "Discord", "channel": channel}))
    return records


def _parse_telegram(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for asset in assets:
        if Path(asset.name).name != "result.json" and hint != "telegram":
            continue
        try:
            payload = json.loads(asset.read_text())
        except json.JSONDecodeError:
            continue
        chats = ((payload.get("chats") or {}).get("list") if isinstance(payload, dict) else None) or []
        if not isinstance(chats, list):
            continue
        for chat in chats:
            if not isinstance(chat, dict) or "messages" not in chat:
                continue
            title = str(chat.get("name") or chat.get("title") or chat.get("id") or "Telegram chat")
            lines = [f"Source: Telegram", f"Chat: {title}", "", "--- Messages ---"]
            for message in chat.get("messages") or []:
                if not isinstance(message, dict):
                    continue
                text = _telegram_text(message.get("text"))
                if text:
                    sender = message.get("from") or message.get("actor") or "unknown"
                    date = message.get("date") or ""
                    lines.append(f"{date} {sender}: {text}".strip())
            if len(lines) > 4:
                records.append(SourceRecord("telegram", f"Telegram {title}", "\n".join(lines), source_url=asset.display_path, metadata={"asset": asset.display_path, "service": "Telegram"}))
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
        except json.JSONDecodeError:
            continue
        messages = _message_list_from_payload(payload)
        if not messages:
            continue
        title = _path_parts(asset.name)[-2] if len(_path_parts(asset.name)) > 1 else "Google Chat"
        lines = ["Source: Google Chat", f"Conversation: {title}", f"File: {asset.name}", "", "--- Messages ---"]
        for message in messages[:1500]:
            if not isinstance(message, dict):
                continue
            text = _message_text_value(message, ("text", "text_body", "message", "body", "content"))
            if not text:
                continue
            sender = _person_name(message.get("creator") or message.get("sender") or message.get("from")) or str(message.get("sender_name") or "unknown")
            created = str(message.get("created_date") or message.get("createdDate") or message.get("create_time") or message.get("timestamp") or "").strip()
            lines.append(f"{created} {sender}: {text}".strip())
        if len(lines) > 5:
            records.append(SourceRecord("google-chat", f"Google Chat {title}", "\n".join(lines), source_url=asset.display_path, metadata={"asset": asset.display_path, "service": "Google Chat"}))
    return records


def _parse_teams(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for asset in assets:
        path_hint = asset.display_path.lower()
        if hint not in {"", "teams", "microsoft-teams"} and "teams" not in path_hint:
            continue
        if "teams" not in path_hint and hint not in {"teams", "microsoft-teams"}:
            continue
        record: SourceRecord | None = None
        if asset.suffix == ".json":
            record = _teams_json_record(asset)
        elif asset.suffix == ".csv":
            record = _teams_csv_record(asset)
        if record:
            records.append(record)
    return records


def _teams_json_record(asset: SourceAsset) -> SourceRecord | None:
    try:
        payload = json.loads(asset.read_text())
    except json.JSONDecodeError:
        return None
    messages = _message_list_from_payload(payload)
    if not messages:
        return None
    title = _path_parts(asset.name)[-2] if len(_path_parts(asset.name)) > 1 else Path(asset.name).stem or "Teams"
    lines = ["Source: Microsoft Teams", f"Conversation: {title}", f"File: {asset.name}", "", "--- Messages ---"]
    for message in messages[:1500]:
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
        lines.append(f"{created} {sender}: {_html_to_text(text)}".strip())
    if len(lines) <= 5:
        return None
    return SourceRecord("teams", f"Teams {title}", "\n".join(lines), source_url=asset.display_path, metadata={"asset": asset.display_path, "service": "Microsoft Teams"})


def _teams_csv_record(asset: SourceAsset) -> SourceRecord | None:
    rows = _csv_rows(asset)
    if not rows:
        return None
    normalized_headers = {_normalize_header(key) for key in rows[0].keys()}
    if not normalized_headers & {"content", "message", "text", "body"}:
        return None
    title = _path_parts(asset.name)[-2] if len(_path_parts(asset.name)) > 1 else Path(asset.name).stem or "Teams"
    lines = ["Source: Microsoft Teams", f"Conversation: {title}", f"File: {asset.name}", "", "--- Messages ---"]
    for row in rows[:1500]:
        normalized = {_normalize_header(key): str(value or "").strip() for key, value in row.items()}
        text = normalized.get("content") or normalized.get("message") or normalized.get("text") or normalized.get("body") or ""
        if not text:
            continue
        sender = normalized.get("from") or normalized.get("sender") or normalized.get("user") or normalized.get("user_display_name") or "unknown"
        created = normalized.get("created_date_time") or normalized.get("created_at") or normalized.get("date") or normalized.get("timestamp") or ""
        lines.append(f"{created} {sender}: {_html_to_text(text)}".strip())
    if len(lines) <= 5:
        return None
    return SourceRecord("teams", f"Teams {title}", "\n".join(lines), source_url=asset.display_path, metadata={"asset": asset.display_path, "service": "Microsoft Teams"})


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
        records.append(SourceRecord("zoom", title, content, source_url=asset.display_path, metadata={"asset": asset.display_path, "service": "Zoom"}))
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
                records.append(SourceRecord("google-keep", title, f"Source: Google Keep\nTitle: {title}\n\n{text}", source_url=asset.display_path, metadata={"asset": asset.display_path, "service": "Google Keep"}))
            continue
        try:
            payload = json.loads(asset.read_text())
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or not any(key in payload for key in ("textContent", "listContent", "title")):
            continue
        title = str(payload.get("title") or "Google Keep note").strip()
        lines = ["Source: Google Keep", f"Title: {title}"]
        for key in ("createdTimestampUsec", "userEditedTimestampUsec"):
            if payload.get(key):
                lines.append(f"{key}: {payload[key]}")
        text = str(payload.get("textContent") or "").strip()
        if text:
            lines.extend(["", text])
        list_items = payload.get("listContent") or []
        if isinstance(list_items, list) and list_items:
            lines.extend(["", "--- List ---"])
            for item in list_items:
                if isinstance(item, dict):
                    lines.append(f"- {item.get('text') or ''}".strip())
        records.append(SourceRecord("google-keep", title, "\n".join(lines), source_url=asset.display_path, metadata={"asset": asset.display_path, "service": "Google Keep"}))
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
            record = _twitter_dm_record(asset, payload)
        else:
            record = _twitter_tweet_record(asset, payload)
        if record:
            records.append(record)
    return records


def _twitter_tweet_record(asset: SourceAsset, payload: list[Any]) -> SourceRecord | None:
    lines = [f"Source: Twitter/X", f"Archive file: {asset.name}", "", "--- Tweets ---"]
    for item in payload[:1000]:
        tweet = item.get("tweet") if isinstance(item, dict) else None
        if not isinstance(tweet, dict):
            continue
        text = str(tweet.get("full_text") or tweet.get("text") or "").strip()
        if not text:
            continue
        created = str(tweet.get("created_at") or "").strip()
        favorite_count = str(tweet.get("favorite_count") or "").strip()
        retweet_count = str(tweet.get("retweet_count") or "").strip()
        metrics = " ".join(part for part in (f"likes={favorite_count}" if favorite_count else "", f"retweets={retweet_count}" if retweet_count else "") if part)
        lines.append(f"{created} {metrics}: {text}".strip())
    if len(lines) <= 4:
        return None
    return SourceRecord("twitter-x", "Twitter/X tweets", "\n".join(lines), source_url=asset.display_path, metadata={"asset": asset.display_path, "service": "Twitter/X"})


def _twitter_dm_record(asset: SourceAsset, payload: list[Any]) -> SourceRecord | None:
    lines = [f"Source: Twitter/X", f"Archive file: {asset.name}", "", "--- Direct Messages ---"]
    for item in payload[:300]:
        conversation = item.get("dmConversation") if isinstance(item, dict) else None
        if not isinstance(conversation, dict):
            continue
        conversation_id = str(conversation.get("conversationId") or "").strip()
        messages = conversation.get("messages") or []
        for message in messages[:500]:
            create = message.get("messageCreate") if isinstance(message, dict) else None
            if not isinstance(create, dict):
                continue
            text = str(create.get("text") or "").strip()
            if not text:
                continue
            sender = str(create.get("senderId") or "").strip()
            created = str(create.get("createdAt") or "").strip()
            lines.append(f"{created} {conversation_id} {sender}: {text}".strip())
    if len(lines) <= 4:
        return None
    return SourceRecord("twitter-x", "Twitter/X direct messages", "\n".join(lines), source_url=asset.display_path, metadata={"asset": asset.display_path, "service": "Twitter/X"})


def _parse_linkedin(assets: list[SourceAsset], hint: str) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for asset in assets:
        if asset.suffix != ".csv":
            continue
        lowered = asset.name.lower()
        if hint not in {"", "linkedin"} and "linkedin" not in asset.display_path.lower():
            continue
        if "message" in lowered:
            record = _linkedin_messages_record(asset)
        elif "connection" in lowered:
            record = _linkedin_connections_record(asset)
        else:
            record = None
        if record:
            records.append(record)
    return records


def _linkedin_messages_record(asset: SourceAsset) -> SourceRecord | None:
    rows = _csv_rows(asset)
    if not rows:
        return None
    headers = {key.lower().replace(" ", "_") for key in rows[0].keys()}
    if "content" not in headers and "message" not in headers:
        return None
    lines = [f"Source: LinkedIn", f"File: {asset.name}", "", "--- Messages ---"]
    for row in rows[:1000]:
        normalized = {_normalize_header(key): value for key, value in row.items()}
        content = str(normalized.get("content") or normalized.get("message") or "").strip()
        if not content:
            continue
        date = str(normalized.get("date") or normalized.get("created_at") or "").strip()
        sender = str(normalized.get("from") or normalized.get("sender") or "").strip()
        title = str(normalized.get("conversation_title") or normalized.get("conversation_id") or "").strip()
        lines.append(f"{date} {title} {sender}: {content}".strip())
    if len(lines) <= 4:
        return None
    return SourceRecord("linkedin", "LinkedIn messages", "\n".join(lines), source_url=asset.display_path, metadata={"asset": asset.display_path, "service": "LinkedIn"})


def _linkedin_connections_record(asset: SourceAsset) -> SourceRecord | None:
    rows = _csv_rows(asset)
    if not rows:
        return None
    lines = [f"Source: LinkedIn", f"File: {asset.name}", "", "--- Connections ---"]
    for row in rows[:1500]:
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
            detail = " | ".join(part for part in (position, company, email_address, connected) if part)
            lines.append(f"{name}: {detail}".strip(": "))
    if len(lines) <= 4:
        return None
    return SourceRecord("linkedin", "LinkedIn connections", "\n".join(lines), source_url=asset.display_path, metadata={"asset": asset.display_path, "service": "LinkedIn"})


def _parse_single_asset(asset: SourceAsset, hint: str) -> list[SourceRecord]:
    suffix = asset.suffix
    if _is_slack_metadata_asset(asset):
        return []
    browser_json = _parse_browser_bookmarks_json_asset(asset, hint)
    if browser_json:
        return [browser_json]
    browser_history = _parse_browser_history_asset(asset, hint)
    if browser_history:
        return [browser_history]
    if suffix == ".mbox":
        return _parse_mbox(asset, hint)
    if Path(asset.name).name == "chat.db" and asset.filesystem_path:
        return _parse_imessage_db(asset, hint)
    if suffix in {".eml", ".emlx"}:
        record = _parse_email_asset(asset, hint)
        return [record] if record else []
    if suffix == ".ics":
        record = _parse_calendar_asset(asset, hint)
        return [record] if record else []
    if suffix == ".vcf":
        record = _parse_contacts_asset(asset, hint)
        return [record] if record else []
    if suffix in {".html", ".htm"} and _looks_like_bookmarks(asset.read_text()):
        record = _parse_bookmarks_asset(asset, hint)
        return [record] if record else []
    if suffix == ".docx":
        text = _extract_docx(asset)
        return [_generic_record(asset, hint or "docs", text)] if text.strip() else []
    if suffix == ".pdf":
        text = _extract_pdf(asset)
        return [_generic_record(asset, hint or "docs", text)] if text.strip() else []
    text = asset.read_text() if suffix in TEXT_EXTENSIONS or hint else ""
    if not text.strip():
        return []
    if _looks_like_whatsapp(asset.name, text):
        return [_generic_record(asset, "whatsapp", _format_whatsapp(asset, text))]
    source = _infer_generic_source(asset, hint)
    if suffix == ".csv" and source == "contacts":
        return [_generic_record(asset, "contacts", _format_contacts_csv(asset, text))]
    if suffix == ".csv" and source in {"notion", "linear", "jira", "asana", "trello", "github", "gitlab", "readwise", "pocket", "instapaper", "raindrop"}:
        text = _format_csv_export(asset, text, source)
    return [_generic_record(asset, source, text)]


def _parse_calendar_asset(asset: SourceAsset, hint: str) -> SourceRecord | None:
    text = asset.read_text()
    if "BEGIN:VEVENT" not in text:
        return None
    lines = [f"Source: Calendar", f"File: {asset.name}", "", "--- Events ---"]
    for event in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", _unfold_ical(text), flags=re.DOTALL)[:1000]:
        fields = _ical_fields(event)
        summary = fields.get("SUMMARY", "Untitled event").strip()
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
        return None
    return SourceRecord(hint or "calendar", Path(asset.name).stem or "Calendar export", "\n\n".join(lines), source_url=asset.display_path, metadata={"asset": asset.display_path, "service": "Calendar"})


def _parse_contacts_asset(asset: SourceAsset, hint: str) -> SourceRecord | None:
    text = asset.read_text()
    if "BEGIN:VCARD" not in text.upper():
        return None
    lines = [f"Source: Contacts", f"File: {asset.name}", "", "--- Contacts ---"]
    for card in re.findall(r"BEGIN:VCARD(.*?)END:VCARD", _unfold_ical(text), flags=re.DOTALL | re.IGNORECASE)[:2000]:
        fields = _ical_fields(card)
        name = fields.get("FN") or fields.get("N") or "Contact"
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
        return None
    return SourceRecord(hint or "contacts", Path(asset.name).stem or "Contacts export", "\n\n".join(lines), source_url=asset.display_path, metadata={"asset": asset.display_path, "service": "Contacts"})


def _parse_bookmarks_asset(asset: SourceAsset, hint: str) -> SourceRecord | None:
    text = asset.read_text()
    entries = re.findall(r"<A\s+[^>]*HREF=[\"']?([^\"'\s>]+)[^>]*>(.*?)</A>", text, flags=re.IGNORECASE | re.DOTALL)
    if not entries:
        return None
    lines = [f"Source: Browser Bookmarks", f"File: {asset.name}", "", "--- Bookmarks ---"]
    for url, raw_title in entries[:3000]:
        title = _html_to_text(raw_title).strip() or url
        lines.append(f"{title} - {html.unescape(url)}")
    return SourceRecord(hint or "browser-bookmarks", Path(asset.name).stem or "Browser bookmarks", "\n".join(lines), source_url=asset.display_path, metadata={"asset": asset.display_path, "service": "Browser Bookmarks"})


def _parse_browser_bookmarks_json_asset(asset: SourceAsset, hint: str) -> SourceRecord | None:
    basename = Path(asset.name).name.lower()
    if basename != "bookmarks" and not (asset.suffix == ".json" and "bookmarks" in asset.display_path.lower()):
        return None
    try:
        payload = json.loads(asset.read_text())
    except json.JSONDecodeError:
        return None
    roots = payload.get("roots") if isinstance(payload, dict) else None
    if not isinstance(roots, dict):
        return None
    entries: list[tuple[str, str, str]] = []
    for root_name, root in roots.items():
        _collect_browser_bookmark_entries(str(root_name), root, entries)
    if not entries:
        return None
    lines = [f"Source: Browser Bookmarks", f"File: {asset.name}", "", "--- Bookmarks ---"]
    for folder, title, url in entries[:3000]:
        prefix = f"{folder}: " if folder else ""
        lines.append(f"{prefix}{title} - {url}")
    return SourceRecord(hint or "browser-bookmarks", Path(asset.name).stem or "Browser bookmarks", "\n".join(lines), source_url=asset.display_path, metadata={"asset": asset.display_path, "service": "Browser Bookmarks"})


def _collect_browser_bookmark_entries(folder: str, node: Any, entries: list[tuple[str, str, str]]) -> None:
    if not isinstance(node, dict):
        return
    node_type = str(node.get("type") or "")
    name = str(node.get("name") or "").strip()
    if node_type == "url":
        url = str(node.get("url") or "").strip()
        if url:
            entries.append((folder, name or url, url))
        return
    child_folder = " / ".join(part for part in (folder, name) if part)
    for child in node.get("children") or []:
        _collect_browser_bookmark_entries(child_folder, child, entries)


def _parse_browser_history_asset(asset: SourceAsset, hint: str) -> SourceRecord | None:
    basename = Path(asset.name).name.lower()
    path_hint = asset.display_path.lower()
    if basename not in {"history", "places.sqlite"} and not (asset.suffix in {".sqlite", ".sqlite3", ".db"} and ("history" in path_hint or "places" in path_hint or "browser" in path_hint)):
        return None
    rows = _browser_history_rows(asset)
    if not rows:
        return None
    lines = [f"Source: Browser History", f"File: {asset.name}", "", "--- Recent Visits ---"]
    for title, url, visits in rows[:1000]:
        visit_text = f" ({visits} visits)" if visits else ""
        lines.append(f"{title or url} - {url}{visit_text}")
    return SourceRecord(hint or "browser-history", Path(asset.name).stem or "Browser history", "\n".join(lines), source_url=asset.display_path, metadata={"asset": asset.display_path, "service": "Browser History"})


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
                LIMIT 1000
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
                LIMIT 1000
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


def _format_csv_export(asset: SourceAsset, text: str, source: str) -> str:
    rows = _csv_rows(asset)
    if not rows:
        return text
    lines = [f"Source: {source}", f"Structured CSV export: {asset.name}", "", "--- Rows ---"]
    for index, row in enumerate(rows[:1000], start=1):
        parts = []
        for key, value in row.items():
            cleaned = str(value or "").strip()
            if cleaned:
                parts.append(f"{key}: {cleaned}")
        if parts:
            lines.append(f"Row {index}\n" + "\n".join(parts[:20]))
    return "\n\n".join(lines)


def _format_contacts_csv(asset: SourceAsset, text: str) -> str:
    rows = _csv_rows(asset)
    if not rows:
        return text
    lines = [f"Source: Contacts", f"Structured contacts export: {asset.name}", "", "--- Contacts ---"]
    for row in rows[:2000]:
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
    return "\n\n".join(lines) if len(lines) > 4 else text


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
        except json.JSONDecodeError:
            return None
    marker = "="
    if marker in stripped:
        stripped = stripped.split(marker, 1)[1].strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
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
            if index >= 500:
                break
            record = _email_record(message, f"{asset.display_path}#{index}", hint)
            if record:
                records.append(record)
        if records:
            return records
    text = asset.read_text()
    chunks = re.split(r"(?m)^From .*$", text)
    for index, chunk in enumerate(chunks):
        if index >= 500:
            break
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
            ORDER BY message.date DESC
            LIMIT 800
            """
        ).fetchall()
    except sqlite3.Error:
        return records
    finally:
        try:
            conn.close()
        except Exception:
            pass
    grouped: dict[str, list[str]] = {}
    for row in rows:
        chat = row["chat_name"] or row["handle_id"] or "Messages"
        sender = "me" if row["is_from_me"] else (row["handle_id"] or "them")
        date = _apple_message_time(row["date"])
        grouped.setdefault(chat, []).append(f"{date} {sender}: {row['text']}".strip())
    for chat, lines in grouped.items():
        ordered = list(reversed(lines))
        content = f"Source: Messages\nChat: {chat}\nFile: {asset.display_path}\n\n--- Messages ---\n" + "\n".join(ordered)
        records.append(SourceRecord("messages", f"Messages {chat}", content, source_url=asset.display_path, metadata={"asset": asset.display_path, "service": "Messages", "chat": chat}))
    return records[:40]


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
        return "\n\n".join(deduped)
    try:
        payload = message.get_content()
    except Exception:
        return ""
    if message.get_content_type() == "text/html":
        return _html_to_text(str(payload))
    return str(payload)


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


def _generic_record(asset: SourceAsset, source: str, text: str) -> SourceRecord:
    title = Path(asset.name).stem or Path(asset.name).name
    cleaned = _clean_generic_text(asset, text)
    return SourceRecord(source, title, cleaned, source_url=asset.display_path, metadata={"asset": asset.display_path, "service": source})


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
