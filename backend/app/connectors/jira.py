from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from ._redaction import classify_error_message, connector_error_payload


JIRA_SOURCE = "jira"
CONNECTOR_VERSION = "2026-07-01"
MAX_RECORDS = 500
DEFAULT_JQL = "ORDER BY updated DESC"
DEFAULT_FIELDS = [
    "summary",
    "description",
    "status",
    "issuetype",
    "project",
    "assignee",
    "reporter",
    "creator",
    "priority",
    "labels",
    "components",
    "created",
    "updated",
    "resolutiondate",
    "duedate",
    "parent",
    "fixVersions",
    "versions",
]


RequestJSON = Callable[[str, dict[str, str], dict[str, Any]], Any]


@dataclass(frozen=True)
class JiraSyncRecord:
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
class JiraSync:
    records: list[JiraSyncRecord]
    records_found: int
    records_returned: int
    high_water_mark: str | None
    cursor_value: str | None
    next_page_token: str | None
    errors: list[dict[str, Any]]
    site_url: str
    jql: str

    def to_summary(self) -> dict[str, Any]:
        return {
            "connector": JIRA_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "records_found": self.records_found,
            "records_returned": self.records_returned,
            "high_water_mark": self.high_water_mark,
            "cursor_value": self.cursor_value,
            "next_page_token": self.next_page_token,
            "errors": self.errors,
            "site_url": self.site_url,
            "jql": self.jql,
        }


def fetch_jira_records(
    *,
    email: str,
    api_token: str,
    site_url: str,
    jql: str | None = None,
    since: str | None = None,
    page_token: str | None = None,
    max_records: int = 100,
    request_json: RequestJSON | None = None,
) -> JiraSync:
    cleaned_email = str(email or "").strip()
    cleaned_token = str(api_token or "").strip()
    if not cleaned_email:
        raise ValueError("Jira email is required")
    if not cleaned_token:
        raise ValueError("Jira API token is required")
    base_url = _normalize_site_url(site_url)
    capped_max = max(1, min(int(max_records or 100), MAX_RECORDS))
    query = str(jql or DEFAULT_JQL).strip() or DEFAULT_JQL
    requester = request_json or _request_json
    basic_auth = _basic_auth(cleaned_email, cleaned_token)
    headers = {
        "Accept": "application/json",
        "Authorization": f"Basic {basic_auth}",
        "Content-Type": "application/json",
        "User-Agent": "Cortex-local-connector",
    }
    endpoint = f"{base_url}/rest/api/3/search/jql"

    records: list[JiraSyncRecord] = []
    records_found = 0
    errors: list[dict[str, Any]] = []
    high_water_mark: str | None = None
    next_page_token = str(page_token or "").strip() or None
    seen_page_tokens: set[str] = set()
    since_normalized = _iso_timestamp(since)

    while len(records) < capped_max:
        body: dict[str, Any] = {
            "jql": query,
            "maxResults": min(100, capped_max - len(records)),
            "fields": DEFAULT_FIELDS,
        }
        if next_page_token:
            if next_page_token in seen_page_tokens:
                errors.append({"error": "Jira search returned a repeated nextPageToken"})
                break
            seen_page_tokens.add(next_page_token)
            body["nextPageToken"] = next_page_token
        try:
            payload = requester(endpoint, headers, body)
        except Exception as exc:
            errors.append(_safe_error(exc, cleaned_token, basic_auth, headers.get("Authorization")))
            break
        if not isinstance(payload, dict):
            errors.append({"error": "Jira search response was not an object"})
            break
        api_errors = _jira_errors(payload)
        if api_errors:
            errors.extend(api_errors)
            break
        issues = payload.get("issues") if isinstance(payload.get("issues"), list) else []
        records_found += len(issues)
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            record = _record_from_issue(issue, base_url)
            if record is None:
                continue
            if since_normalized and record.captured_at and record.captured_at < since_normalized:
                continue
            high_water_mark = _max_iso(high_water_mark, record.captured_at)
            records.append(record)
            if len(records) >= capped_max:
                break

        next_page_token = str(payload.get("nextPageToken") or "").strip() or None
        if payload.get("isLast") is True or not next_page_token or not issues or len(records) >= capped_max:
            break

    return JiraSync(
        records=records,
        records_found=records_found,
        records_returned=len(records),
        high_water_mark=high_water_mark,
        cursor_value=next_page_token or high_water_mark or since_normalized or since,
        next_page_token=next_page_token,
        errors=errors,
        site_url=base_url,
        jql=query,
    )


def _request_json(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
    request = Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    with urlopen(request, timeout=30) as response:  # noqa: S310 - trusted Jira site URL provided by the user.
        return json.loads(response.read().decode("utf-8"))


def _record_from_issue(issue: dict[str, Any], site_url: str) -> JiraSyncRecord | None:
    key = _text(issue.get("key"))
    issue_id = _text(issue.get("id")) or key
    if not key:
        return None
    fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
    summary = _clean_text(fields.get("summary")) or "Untitled Jira issue"
    created = _iso_timestamp(fields.get("created"))
    updated = _iso_timestamp(fields.get("updated"))
    resolved = _iso_timestamp(fields.get("resolutiondate"))
    due = _text(fields.get("duedate"))
    source_url = f"{site_url}/browse/{key}"
    project = fields.get("project") if isinstance(fields.get("project"), dict) else {}
    issue_type = fields.get("issuetype") if isinstance(fields.get("issuetype"), dict) else {}
    status = fields.get("status") if isinstance(fields.get("status"), dict) else {}
    priority = fields.get("priority") if isinstance(fields.get("priority"), dict) else {}
    assignee = fields.get("assignee") if isinstance(fields.get("assignee"), dict) else {}
    reporter = fields.get("reporter") if isinstance(fields.get("reporter"), dict) else {}
    creator = fields.get("creator") if isinstance(fields.get("creator"), dict) else {}
    parent = fields.get("parent") if isinstance(fields.get("parent"), dict) else {}
    labels = _string_list(fields.get("labels"))
    components = _named_list(fields.get("components"))
    fix_versions = _named_list(fields.get("fixVersions"))
    versions = _named_list(fields.get("versions"))
    description = _description_text(fields.get("description"))

    lines = [
        "Source: Jira",
        f"Issue: {key}",
        f"Title: {summary}",
    ]
    project_label = _project_label(project)
    if project_label:
        lines.append(f"Project: {project_label}")
    if issue_type:
        lines.append(f"Type: {_display_name(issue_type)}")
    if status:
        lines.append(f"Status: {_display_name(status)}")
    if priority:
        lines.append(f"Priority: {_display_name(priority)}")
    if assignee:
        lines.append(f"Assignee: {_display_name(assignee)}")
    if reporter:
        lines.append(f"Reporter: {_display_name(reporter)}")
    if creator:
        lines.append(f"Creator: {_display_name(creator)}")
    if parent:
        lines.append(f"Parent: {_text(parent.get('key')) or _text(parent.get('id'))}")
    if labels:
        lines.append(f"Labels: {', '.join(labels)}")
    if components:
        lines.append(f"Components: {', '.join(components)}")
    if fix_versions:
        lines.append(f"Fix versions: {', '.join(fix_versions)}")
    if versions:
        lines.append(f"Affects versions: {', '.join(versions)}")
    if created:
        lines.append(f"Created: {created}")
    if updated:
        lines.append(f"Updated: {updated}")
    if resolved:
        lines.append(f"Resolved: {resolved}")
    if due:
        lines.append(f"Due: {due}")
    lines.append(f"URL: {source_url}")
    if description:
        lines.extend(["", "Description:", description])

    project_name = _clean_text(project.get("name"))
    project_key = _text(project.get("key"))
    return JiraSyncRecord(
        content="\n".join(lines).strip(),
        title=f"Jira {key}: {summary}"[:200],
        source_url=source_url[:500],
        external_id=f"jira:issue:{issue_id}"[:240],
        captured_at=updated or created,
        metadata={
            "connector": JIRA_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "issue_id": issue_id,
            "issue_key": key,
            "key": key,
            "project": project_name,
            "project_key": project_key,
            "issue_type": _display_name(issue_type),
            "status": _display_name(status),
            "priority": _display_name(priority),
            "assignee": _display_name(assignee),
            "reporter": _display_name(reporter),
            "creator": _display_name(creator),
            "labels": labels,
            "components": components,
            "url": source_url,
            "source_type": "jira_issue",
            "source_quality": "canonical",
        },
    )


def _normalize_site_url(value: str) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        raise ValueError("Jira site_url is required")
    if not text.startswith("http://") and not text.startswith("https://"):
        text = f"https://{text}"
    split = urlsplit(text)
    if not split.scheme or not split.netloc:
        raise ValueError("Jira site_url must be a valid URL")
    return urlunsplit((split.scheme, split.netloc, split.path.rstrip("/"), "", ""))


def _basic_auth(email: str, api_token: str) -> str:
    return base64.b64encode(f"{email}:{api_token}".encode("utf-8")).decode("ascii")


def _safe_error(exc: Exception, *secrets: str | None) -> dict[str, Any]:
    return connector_error_payload(exc, secrets)


def _jira_errors(payload: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for item in payload.get("errorMessages") or []:
        text = _clean_text(item)
        if text:
            errors.append({"error": text, "category": classify_error_message(text, default="client")})
    raw_errors = payload.get("errors")
    if isinstance(raw_errors, dict):
        for key, value in raw_errors.items():
            message = _clean_text(value) or "Jira API error"
            errors.append({
                "field": _clean_text(key),
                "error": message,
                "category": classify_error_message(message, default="client"),
            })
    return errors


def _description_text(value: Any) -> str:
    if isinstance(value, str):
        return _clean_text(value)[:20_000]
    if isinstance(value, dict):
        return _clean_text(_adf_text(value))[:20_000]
    return ""


def _adf_text(value: Any) -> str:
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            parts.append(node)
            return
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, dict):
            return
        node_type = _text(node.get("type"))
        attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
        if node_type == "text":
            parts.append(_text(node.get("text")))
        elif node_type == "mention":
            parts.append(_text(attrs.get("text") or attrs.get("id")))
        elif node_type == "emoji":
            parts.append(_text(attrs.get("text") or attrs.get("shortName")))
        elif node_type == "hardBreak":
            parts.append("\n")
        walk(node.get("content"))
        if node_type in {"paragraph", "heading", "blockquote", "bulletList", "orderedList", "listItem", "codeBlock", "rule"}:
            parts.append("\n")

    walk(value)
    return " ".join(part for part in parts if part)


def _project_label(project: dict[str, Any]) -> str:
    name = _clean_text(project.get("name"))
    key = _text(project.get("key"))
    if name and key:
        return f"{name} ({key})"
    return name or key


def _display_name(value: dict[str, Any]) -> str:
    return _clean_text(value.get("displayName") or value.get("name") or value.get("key") or value.get("accountId"))


def _named_list(value: Any) -> list[str]:
    items: list[str] = []
    if not isinstance(value, list):
        return items
    for item in value:
        if isinstance(item, dict):
            text = _clean_text(item.get("name") or item.get("key") or item.get("id"))
        else:
            text = _clean_text(item)
        if text and text not in items:
            items.append(text)
    return items


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = _clean_text(item)
        if text and text not in items:
            items.append(text)
    return items


def _iso_timestamp(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    normalized = text
    if re.search(r"[+-]\d{4}$", normalized):
        normalized = f"{normalized[:-2]}:{normalized[-2:]}"
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc).replace(microsecond=0)
    return parsed.isoformat().replace("+00:00", "Z")


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", _text(value)).strip()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _max_iso(left: str | None, right: str | None) -> str | None:
    if not left:
        return right
    if not right:
        return left
    return max(left, right)
