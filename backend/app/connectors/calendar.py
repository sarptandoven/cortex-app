from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from ._redaction import connector_error_payload


CALENDAR_SOURCE = "calendar"
CONNECTOR_VERSION = "2026-07-01"
MAX_RECORDS = 500


ReadText = Callable[[Path], str]
RequestText = Callable[[str], str]


@dataclass(frozen=True)
class CalendarSyncRecord:
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
class CalendarSync:
    records: list[CalendarSyncRecord]
    records_found: int
    records_returned: int
    high_water_mark: str | None
    cursor_value: str | None
    errors: list[dict[str, Any]]
    input_type: str
    source_label: str

    def to_summary(self) -> dict[str, Any]:
        return {
            "connector": CALENDAR_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "records_found": self.records_found,
            "records_returned": self.records_returned,
            "high_water_mark": self.high_water_mark,
            "cursor_value": self.cursor_value,
            "errors": self.errors,
            "input_type": self.input_type,
            "source_label": self.source_label,
        }


def fetch_calendar_records(
    *,
    ics_path: str | None = None,
    feed_url: str | None = None,
    since: str | None = None,
    max_records: int = 100,
    read_text: ReadText | None = None,
    request_text: RequestText | None = None,
) -> CalendarSync:
    path_text = str(ics_path or "").strip()
    url_text = str(feed_url or "").strip()
    if bool(path_text) == bool(url_text):
        raise ValueError("Provide exactly one of ics_path or feed_url")
    capped_max = max(1, min(int(max_records or 100), MAX_RECORDS))
    errors: list[dict[str, Any]] = []
    input_type = "feed" if url_text else "local_file"
    source_label = _source_label(url_text, path_text)
    try:
        raw_text = _fetch_feed(url_text, request_text) if url_text else _read_path(path_text, read_text)
    except Exception as exc:
        errors.append({"scope": "read", **_safe_error(exc, url_text or path_text)})
        return CalendarSync(
            records=[],
            records_found=0,
            records_returned=0,
            high_water_mark=None,
            cursor_value=since,
            errors=errors,
            input_type=input_type,
            source_label=source_label,
        )
    records: list[CalendarSyncRecord] = []
    records_found = 0
    high_water_mark: str | None = None
    since_text = str(since or "").strip() or None
    for event in _event_blocks(raw_text):
        records_found += 1
        record, event_errors = _record_from_event(event)
        errors.extend(event_errors)
        if record is None:
            continue
        if since_text and record.captured_at and record.captured_at <= since_text:
            continue
        high_water_mark = _max_text(high_water_mark, record.captured_at)
        records.append(record)
        if len(records) >= capped_max:
            break

    return CalendarSync(
        records=records,
        records_found=records_found,
        records_returned=len(records),
        high_water_mark=high_water_mark,
        cursor_value=high_water_mark or since_text,
        errors=errors,
        input_type=input_type,
        source_label=source_label,
    )


def _read_path(path_text: str, read_text: ReadText | None) -> str:
    path = Path(path_text).expanduser()
    reader = read_text or (lambda value: value.read_text(encoding="utf-8", errors="replace"))
    return reader(path)


def _fetch_feed(url_text: str, request_text: RequestText | None) -> str:
    normalized = _normalize_feed_url(url_text)
    if request_text:
        return request_text(normalized)
    request = Request(normalized, headers={"Accept": "text/calendar,*/*", "User-Agent": "Cortex-local-connector"}, method="GET")
    with urlopen(request, timeout=30) as response:  # noqa: S310 - user-provided calendar feed URL.
        return response.read().decode("utf-8", errors="replace")


def _normalize_feed_url(url_text: str) -> str:
    text = str(url_text or "").strip()
    if text.startswith("webcal://"):
        text = "https://" + text[len("webcal://") :]
    split = urlsplit(text)
    if split.scheme not in {"http", "https"} or not split.netloc:
        raise ValueError("feed_url must be http, https, or webcal")
    return urlunsplit((split.scheme, split.netloc, split.path, split.query, split.fragment))


def _source_label(feed_url: str, ics_path: str) -> str:
    if feed_url:
        normalized = _normalize_feed_url(feed_url)
        split = urlsplit(normalized)
        return split.netloc or "calendar-feed"
    return Path(ics_path).name or "local-calendar"


def _event_blocks(text: str) -> list[list[str]]:
    events: list[list[str]] = []
    current: list[str] | None = None
    for line in _unfold_ical(text):
        name, value = _property_name_and_value(line)
        upper_name = name.upper()
        upper_value = value.upper()
        if upper_name == "BEGIN" and upper_value == "VEVENT":
            current = []
            continue
        if upper_name == "END" and upper_value == "VEVENT":
            if current is not None:
                events.append(current)
            current = None
            continue
        if current is not None:
            current.append(line)
    return events


def _record_from_event(lines: list[str]) -> tuple[CalendarSyncRecord | None, list[dict[str, Any]]]:
    fields: dict[str, list[str]] = {}
    nested_depth = 0
    for line in lines:
        name, value = _property_name_and_value(line)
        upper_name = name.upper()
        if upper_name == "BEGIN":
            nested_depth += 1
            continue
        if upper_name == "END":
            nested_depth = max(0, nested_depth - 1)
            continue
        if nested_depth > 0:
            continue
        if not upper_name:
            continue
        fields.setdefault(upper_name, []).append(_unescape_ical(value))

    uid = _first(fields, "UID")
    if not uid:
        return None, [{"error": "Calendar event missing UID", "scope": "event"}]
    summary = _first(fields, "SUMMARY") or "Untitled calendar event"
    description = _first(fields, "DESCRIPTION")
    location = _first(fields, "LOCATION")
    dtstart = _first(fields, "DTSTART")
    dtend = _first(fields, "DTEND")
    organizer = _first(fields, "ORGANIZER")
    attendees = fields.get("ATTENDEE") or []
    updated = _first(fields, "LAST-MODIFIED") or _first(fields, "DTSTAMP") or dtstart
    external_id = _external_id(uid)
    source_url = _event_source_url(uid, external_id)

    lines_out = [
        "Source: Calendar",
        f"Event: {summary}",
        f"UID: {uid}",
    ]
    if dtstart:
        lines_out.append(f"Start: {dtstart}")
    if dtend:
        lines_out.append(f"End: {dtend}")
    if location:
        lines_out.append(f"Location: {location}")
    if organizer:
        lines_out.append(f"Organizer: {organizer}")
    if attendees:
        lines_out.append(f"Attendees: {', '.join(attendees[:20])}")
    if updated:
        lines_out.append(f"Updated: {updated}")
    if description:
        lines_out.extend(["", "Description:", description])

    return (
        CalendarSyncRecord(
            content="\n".join(lines_out).strip(),
            title=f"Calendar: {summary}"[:200],
            source_url=source_url,
            external_id=external_id,
            captured_at=updated,
            metadata={
                "connector": CALENDAR_SOURCE,
                "connector_version": CONNECTOR_VERSION,
                "uid": uid,
                "summary": summary,
                "dtstart": dtstart,
                "dtend": dtend,
                "location": location,
                "organizer": organizer,
                "attendees": attendees[:50],
                "url": source_url,
                "source_type": "calendar_event",
                "source_quality": "canonical",
            },
        ),
        [],
    )


def _unfold_ical(text: str) -> list[str]:
    unfolded: list[str] = []
    for raw_line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw_line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += raw_line[1:]
        else:
            unfolded.append(raw_line)
    return unfolded


def _property_name_and_value(line: str) -> tuple[str, str]:
    if ":" not in line:
        return "", ""
    raw_name, value = line.split(":", 1)
    name = raw_name.split(";", 1)[0].strip()
    return name, value.strip()


def _unescape_ical(value: str) -> str:
    text = str(value or "")
    text = text.replace("\\n", "\n").replace("\\N", "\n")
    text = text.replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def _first(fields: dict[str, list[str]], name: str) -> str:
    values = fields.get(name) or []
    return str(values[0]).strip() if values else ""


def _external_id(uid: str) -> str:
    base = f"calendar:event:{uid.strip()}"
    if len(base) <= 240:
        return base
    digest = hashlib.sha256(uid.encode("utf-8")).hexdigest()[:24]
    return f"calendar:event:{digest}"


def _event_source_url(uid: str, external_id: str) -> str:
    quoted_uid = quote(uid.strip(), safe="")
    url = f"calendar://event/{quoted_uid}"
    if len(url) <= 500:
        return url
    return f"calendar://event/{external_id.removeprefix('calendar:event:')}"


def _max_text(left: str | None, right: str | None) -> str | None:
    if not left:
        return right
    if not right:
        return left
    return max(left, right)


def _safe_error(exc: Exception, secret: str) -> dict[str, Any]:
    secrets: list[str] = []
    if secret:
        secrets.append(secret)
        if str(secret).strip().startswith("webcal://"):
            try:
                secrets.append(_normalize_feed_url(secret))
            except ValueError:
                pass
    return connector_error_payload(exc, secrets)
