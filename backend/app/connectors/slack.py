from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
import json
import re
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ._redaction import connector_error_payload


SLACK_SOURCE = "slack"
CONNECTOR_VERSION = "2026-07-01"
DEFAULT_API_BASE_URL = "https://slack.com/api"
MAX_CHANNELS = 20
DEFAULT_PAGE_LIMIT = 15


RequestJSON = Callable[[str, dict[str, str]], Any]


@dataclass(frozen=True)
class SlackChannelSpec:
    channel_id: str
    name: str | None = None


@dataclass(frozen=True)
class SlackSyncRecord:
    content: str
    title: str
    source_url: str
    external_id: str
    captured_at: str | None
    metadata: dict[str, Any]

    def to_source_account_record(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "title": self.title,
            "source_url": self.source_url,
            "external_id": self.external_id,
            "captured_at": self.captured_at,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class SlackSync:
    channels: list[dict[str, str | None]]
    records: list[SlackSyncRecord]
    records_found: int
    records_returned: int
    high_water_mark: str | None
    cursor_value: str | None
    next_cursors: dict[str, str]
    errors: list[dict[str, Any]]
    api_base_url: str
    auth_identity: dict[str, str]

    def to_summary(self) -> dict[str, Any]:
        return {
            "connector": SLACK_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "channels": self.channels,
            "records_found": self.records_found,
            "records_returned": self.records_returned,
            "high_water_mark": self.high_water_mark,
            "cursor_value": self.cursor_value,
            "next_cursors": self.next_cursors,
            "errors": self.errors,
            "api_base_url": self.api_base_url,
            "auth_identity": self.auth_identity,
        }


def fetch_slack_records(
    *,
    token: str,
    channels: list[str],
    since: str | None = None,
    page_cursors: dict[str, str] | None = None,
    max_records: int = 100,
    include_threads: bool = True,
    workspace_url: str | None = None,
    api_base_url: str = DEFAULT_API_BASE_URL,
    request_json: RequestJSON | None = None,
) -> SlackSync:
    cleaned_token = str(token or "").strip()
    if not cleaned_token:
        raise ValueError("Slack token is required")
    channel_specs = _normalize_channels(channels)
    if not channel_specs:
        raise ValueError("At least one Slack channel ID is required")
    if len(channel_specs) > MAX_CHANNELS:
        raise ValueError(f"At most {MAX_CHANNELS} Slack channels can be synced at once")
    capped_max = max(1, min(int(max_records or 100), 200))
    requester = request_json or _request_json
    base_url = str(api_base_url or DEFAULT_API_BASE_URL).rstrip("/")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {cleaned_token}",
        "User-Agent": "Cortex-local-connector",
    }
    oldest = _slack_oldest_from_since(since)
    auth_identity = _fetch_auth_identity(requester, base_url=base_url, headers=headers)

    records: list[SlackSyncRecord] = []
    records_found = 0
    errors: list[dict[str, Any]] = []
    high_water_mark: str | None = None
    next_cursors: dict[str, str] = {}
    initial_cursors = {str(key): str(value).strip() for key, value in (page_cursors or {}).items() if str(value).strip()}

    for channel in channel_specs:
        cursor: str | None = initial_cursors.get(channel.channel_id)
        while len(records) < capped_max:
            per_page = min(DEFAULT_PAGE_LIMIT, capped_max - len(records))
            query = {"channel": channel.channel_id, "limit": str(per_page)}
            if oldest:
                query["oldest"] = oldest
            if cursor:
                query["cursor"] = cursor
            url = f"{base_url}/conversations.history?{urlencode(query)}"
            try:
                payload = requester(url, headers)
            except Exception as exc:
                errors.append(
                    {
                        "channel": channel.channel_id,
                        **connector_error_payload(exc, [cleaned_token, headers.get("Authorization")]),
                    }
                )
                break
            if not isinstance(payload, dict):
                errors.append({"channel": channel.channel_id, "error": "Slack history response was not an object"})
                break
            if not payload.get("ok", False):
                errors.append({"channel": channel.channel_id, **connector_error_payload(payload.get("error") or "Slack API error")})
                break
            messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
            records_found += len(messages)
            for message in messages:
                if not isinstance(message, dict):
                    continue
                record = _record_from_message(channel, message, workspace_url=workspace_url)
                if record is None:
                    continue
                high_water_mark = _max_iso(high_water_mark, record.captured_at)
                records.append(record)
                if len(records) >= capped_max:
                    break
                if include_threads and _message_has_replies(message):
                    replies = _fetch_thread_reply_records(
                        requester,
                        base_url=base_url,
                        headers=headers,
                        token=cleaned_token,
                        channel=channel,
                        parent=message,
                        remaining=capped_max - len(records),
                        workspace_url=workspace_url,
                        errors=errors,
                    )
                    for reply_record in replies:
                        high_water_mark = _max_iso(high_water_mark, reply_record.captured_at)
                        records.append(reply_record)
                        records_found += 1
                        if len(records) >= capped_max:
                            break
            cursor = str((payload.get("response_metadata") or {}).get("next_cursor") or "").strip() or None
            if cursor:
                next_cursors[channel.channel_id] = cursor
            if not cursor or len(records) >= capped_max:
                break

    channel_payloads = [{"id": item.channel_id, "name": item.name} for item in channel_specs]
    return SlackSync(
        channels=channel_payloads,
        records=records,
        records_found=records_found,
        records_returned=len(records),
        high_water_mark=high_water_mark,
        cursor_value=high_water_mark or since,
        next_cursors=next_cursors,
        errors=errors,
        api_base_url=base_url,
        auth_identity=auth_identity,
    )


def _fetch_auth_identity(
    requester: RequestJSON,
    *,
    base_url: str,
    headers: dict[str, str],
) -> dict[str, str]:
    try:
        payload = requester(f"{base_url}/auth.test", headers)
    except Exception:
        return {}
    if not isinstance(payload, dict) or not payload.get("ok", False):
        return {}
    identity: dict[str, str] = {}
    for key in ("user_id", "user", "team_id", "team", "url"):
        value = str(payload.get(key) or "").strip()
        if value:
            identity[key] = value[:240]
    return identity


def _message_has_replies(message: dict[str, Any]) -> bool:
    try:
        reply_count = int(message.get("reply_count") or 0)
    except (TypeError, ValueError):
        reply_count = 0
    thread_ts = str(message.get("thread_ts") or "").strip()
    ts = str(message.get("ts") or "").strip()
    return reply_count > 0 or bool(thread_ts and ts and thread_ts == ts and message.get("latest_reply"))


def _fetch_thread_reply_records(
    requester: RequestJSON,
    *,
    base_url: str,
    headers: dict[str, str],
    token: str,
    channel: SlackChannelSpec,
    parent: dict[str, Any],
    remaining: int,
    workspace_url: str | None,
    errors: list[dict[str, Any]],
) -> list[SlackSyncRecord]:
    parent_ts = str(parent.get("ts") or "").strip()
    if not parent_ts or remaining <= 0:
        return []
    query = {"channel": channel.channel_id, "ts": parent_ts, "limit": str(min(max(remaining + 1, 1), 100))}
    url = f"{base_url}/conversations.replies?{urlencode(query)}"
    try:
        payload = requester(url, headers)
    except Exception as exc:
        errors.append(
            {
                "channel": channel.channel_id,
                "thread_ts": parent_ts,
                **connector_error_payload(exc, [token, headers.get("Authorization")]),
            }
        )
        return []
    if not isinstance(payload, dict):
        errors.append({"channel": channel.channel_id, "thread_ts": parent_ts, "error": "Slack replies response was not an object"})
        return []
    if not payload.get("ok", False):
        errors.append({"channel": channel.channel_id, "thread_ts": parent_ts, **connector_error_payload(payload.get("error") or "Slack replies API error")})
        return []
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    records: list[SlackSyncRecord] = []
    thread_parent = parent
    for reply in messages:
        if not isinstance(reply, dict):
            continue
        if str(reply.get("ts") or "").strip() == parent_ts:
            if _clean_slack_text(reply.get("text")):
                thread_parent = reply
            continue
        record = _record_from_message(channel, reply, workspace_url=workspace_url, thread_parent=thread_parent)
        if record is not None:
            records.append(record)
        if len(records) >= remaining:
            break
    return records


def _request_json(url: str, headers: dict[str, str]) -> Any:
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=30) as response:  # noqa: S310 - user-provided token, trusted Slack API URL by default.
        return json.loads(response.read().decode("utf-8"))


def _normalize_channels(values: list[str]) -> list[SlackChannelSpec]:
    channels: list[SlackChannelSpec] = []
    seen: set[str] = set()
    for value in values or []:
        spec = _normalize_channel(value)
        if spec is None or spec.channel_id.lower() in seen:
            continue
        seen.add(spec.channel_id.lower())
        channels.append(spec)
    return channels


def _normalize_channel(value: str) -> SlackChannelSpec | None:
    text = str(value or "").strip()
    if not text:
        return None
    channel_id = text
    name: str | None = None
    if "|" in text:
        left, right = text.split("|", 1)
        channel_id = left.strip()
        name = _clean_channel_name(right)
    elif ":" in text:
        left, right = text.split(":", 1)
        if re.fullmatch(r"[A-Z][A-Z0-9]{2,}", left.strip()):
            channel_id = left.strip()
            name = _clean_channel_name(right)
    if channel_id.startswith("#"):
        return None
    if not re.fullmatch(r"[A-Z][A-Z0-9]{2,}", channel_id):
        return None
    return SlackChannelSpec(channel_id=channel_id, name=name)


def _record_from_message(
    channel: SlackChannelSpec,
    message: dict[str, Any],
    *,
    workspace_url: str | None = None,
    thread_parent: dict[str, Any] | None = None,
) -> SlackSyncRecord | None:
    ts = str(message.get("ts") or "").strip()
    text = _clean_slack_text(message.get("text"))
    if not ts or not text:
        return None
    author = str(message.get("user") or message.get("username") or message.get("bot_id") or "unknown").strip()
    message_type = str(message.get("type") or "message").strip() or "message"
    subtype = str(message.get("subtype") or "").strip()
    captured_at = _iso_from_slack_ts(ts)
    channel_label = f"#{channel.name}" if channel.name else channel.channel_id
    source_url = _message_source_url(channel.channel_id, ts, workspace_url=workspace_url)
    title = f"Slack {channel_label} {captured_at or ts}"[:200]
    lines = [
        "Source: Slack",
        f"Channel: {channel_label}",
        f"Channel ID: {channel.channel_id}",
        f"Message TS: {ts}",
        f"Type: {message_type}",
    ]
    if subtype:
        lines.append(f"Subtype: {subtype}")
    if captured_at:
        lines.append(f"Date: {captured_at}")
    thread_ts = str(message.get("thread_ts") or "").strip()
    if thread_ts and thread_ts != ts:
        lines.insert(-1, f"Thread TS: {thread_ts}")
    parent_context = _thread_parent_context(thread_parent, thread_ts=thread_ts, reply_ts=ts)
    if parent_context:
        lines.extend(
            [
                "",
                f"Thread parent: {parent_context['thread_parent_author']}: {parent_context['thread_parent_text_excerpt']}",
                f"{author}: {text} Thread context: {parent_context['thread_parent_text_excerpt']}",
            ]
        )
    else:
        lines.extend(["", f"{author}: {text}"])

    return SlackSyncRecord(
        content="\n".join(lines).strip(),
        title=title,
        source_url=source_url,
        external_id=f"slack:{channel.channel_id}:{ts}"[:240],
        captured_at=captured_at,
        metadata={
            "connector": SLACK_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "channel": channel.name or channel.channel_id,
            "channel_id": channel.channel_id,
            "message": ts,
            "message_ts": ts,
            "thread_ts": thread_ts,
            "author": author,
            "message_type": message_type,
            "subtype": subtype,
            **parent_context,
            "url": source_url,
            "source_type": "slack_message",
            "source_quality": "canonical",
        },
    )


def _thread_parent_context(parent: dict[str, Any] | None, *, thread_ts: str, reply_ts: str) -> dict[str, str]:
    if not isinstance(parent, dict) or not thread_ts or thread_ts == reply_ts:
        return {}
    parent_ts = str(parent.get("ts") or "").strip()
    if parent_ts and parent_ts != thread_ts:
        return {}
    parent_text = _clean_slack_text(parent.get("text"))[:500]
    if not parent_text:
        return {}
    author = str(parent.get("user") or parent.get("username") or parent.get("bot_id") or "unknown").strip()
    return {
        "thread_parent_ts": thread_ts,
        "thread_parent_author": author,
        "thread_parent_text_excerpt": parent_text,
    }


def _message_source_url(channel_id: str, ts: str, *, workspace_url: str | None = None) -> str:
    base = str(workspace_url or "").strip().rstrip("/")
    encoded_ts = ts.replace(".", "")
    if base:
        if not base.startswith("http://") and not base.startswith("https://"):
            base = f"https://{base}"
        return f"{base}/archives/{channel_id}/p{encoded_ts}"[:500]
    return f"slack://channel/{channel_id}/message/{encoded_ts}"[:500]


def _clean_slack_text(value: Any) -> str:
    text = unescape(str(value or "").replace("\r\n", "\n").replace("\r", "\n")).strip()
    text = re.sub(r"<@([A-Z0-9]+)>", r"@\1", text)
    text = re.sub(r"<#([A-Z0-9]+)\|([^>]+)>", r"#\2", text)
    text = re.sub(r"<(https?://[^>|]+)\|([^>]+)>", r"\2 (\1)", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()[:20_000]


def _clean_channel_name(value: str) -> str | None:
    cleaned = str(value or "").strip().lstrip("#")
    if not cleaned:
        return None
    return re.sub(r"[^A-Za-z0-9_-]+", "-", cleaned).strip("-")[:80] or None


def _iso_from_slack_ts(value: str) -> str | None:
    try:
        seconds = float(str(value or ""))
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slack_oldest_from_since(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return text
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return f"{parsed.timestamp():.6f}"


def _max_iso(left: str | None, right: str | None) -> str | None:
    if not left:
        return right
    if not right:
        return left
    return max(left, right)
