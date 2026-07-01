from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from ._redaction import redact_error_message


GITHUB_SOURCE = "github"
CONNECTOR_VERSION = "2026-07-01"
DEFAULT_API_BASE_URL = "https://api.github.com"
MAX_REPOSITORIES = 25


RequestJSON = Callable[[str, dict[str, str]], Any]


@dataclass(frozen=True)
class GitHubSyncRecord:
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
class GitHubSync:
    repositories: list[str]
    records: list[GitHubSyncRecord]
    records_found: int
    records_returned: int
    high_water_mark: str | None
    cursor_value: str | None
    errors: list[dict[str, Any]]
    api_base_url: str

    def to_summary(self) -> dict[str, Any]:
        return {
            "connector": GITHUB_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "repositories": self.repositories,
            "records_found": self.records_found,
            "records_returned": self.records_returned,
            "high_water_mark": self.high_water_mark,
            "cursor_value": self.cursor_value,
            "errors": self.errors,
            "api_base_url": self.api_base_url,
        }


def fetch_github_records(
    *,
    token: str,
    repositories: list[str],
    since: str | None = None,
    max_records: int = 100,
    api_base_url: str = DEFAULT_API_BASE_URL,
    request_json: RequestJSON | None = None,
) -> GitHubSync:
    cleaned_token = str(token or "").strip()
    if not cleaned_token:
        raise ValueError("GitHub token is required")
    normalized_repositories = _normalize_repositories(repositories)
    if not normalized_repositories:
        raise ValueError("At least one GitHub repository is required")
    if len(normalized_repositories) > MAX_REPOSITORIES:
        raise ValueError(f"At most {MAX_REPOSITORIES} repositories can be synced at once")
    capped_max = max(1, min(int(max_records or 100), 500))
    requester = request_json or _request_json
    base_url = str(api_base_url or DEFAULT_API_BASE_URL).rstrip("/")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {cleaned_token}",
        "User-Agent": "Cortex-local-connector",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    records: list[GitHubSyncRecord] = []
    records_found = 0
    errors: list[dict[str, Any]] = []
    high_water_mark: str | None = None

    for repository in normalized_repositories:
        page = 1
        while len(records) < capped_max:
            per_page = min(100, capped_max - len(records))
            query = {
                "state": "all",
                "sort": "updated",
                "direction": "desc",
                "per_page": str(per_page),
                "page": str(page),
            }
            if since:
                query["since"] = str(since)
            url = f"{base_url}/repos/{repository}/issues?{urlencode(query)}"
            try:
                payload = requester(url, headers)
            except Exception as exc:
                errors.append(
                    {
                        "repository": repository,
                        "error": redact_error_message(exc, [cleaned_token, headers.get("Authorization")]),
                    }
                )
                break
            if not isinstance(payload, list):
                errors.append({"repository": repository, "error": "GitHub issues response was not a list"})
                break
            records_found += len(payload)
            for item in payload:
                if not isinstance(item, dict):
                    continue
                record = _record_from_issue(repository, item)
                if record is None:
                    continue
                high_water_mark = _max_iso(high_water_mark, record.captured_at)
                records.append(record)
                if len(records) >= capped_max:
                    break
            if len(payload) < per_page or len(records) >= capped_max:
                break
            page += 1

    return GitHubSync(
        repositories=normalized_repositories,
        records=records,
        records_found=records_found,
        records_returned=len(records),
        high_water_mark=high_water_mark,
        cursor_value=high_water_mark or since,
        errors=errors,
        api_base_url=base_url,
    )


def _request_json(url: str, headers: dict[str, str]) -> Any:
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=30) as response:  # noqa: S310 - user-provided token, trusted GitHub API URL by default.
        return json.loads(response.read().decode("utf-8"))


def _normalize_repositories(values: list[str]) -> list[str]:
    repositories: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        repository = _normalize_repository(value)
        if not repository or repository.lower() in seen:
            continue
        seen.add(repository.lower())
        repositories.append(repository)
    return repositories


def _normalize_repository(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("http://") or text.startswith("https://"):
        parsed = urlparse(text)
        text = parsed.path.strip("/")
    text = text.removeprefix("github.com/").strip("/")
    parts = [part for part in text.split("/") if part]
    if len(parts) < 2:
        return ""
    owner, repo = parts[0], parts[1]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        return ""
    return f"{owner}/{repo}"


def _record_from_issue(repository: str, item: dict[str, Any]) -> GitHubSyncRecord | None:
    number = item.get("number")
    title = str(item.get("title") or "").strip()
    if not number or not title:
        return None
    is_pull_request = isinstance(item.get("pull_request"), dict)
    record_scope = "pull_request" if is_pull_request else "issue"
    type_label = "Pull request" if is_pull_request else "Issue"
    html_url = str(item.get("html_url") or "").strip()
    source_url = html_url or f"https://github.com/{repository}/{'pull' if is_pull_request else 'issues'}/{number}"
    author = _login(item.get("user"))
    labels = _labels(item.get("labels"))
    assignees = [_login(value) for value in item.get("assignees") or [] if _login(value)]
    milestone = item.get("milestone") if isinstance(item.get("milestone"), dict) else {}
    created_at = _text(item.get("created_at"))
    updated_at = _text(item.get("updated_at"))
    closed_at = _text(item.get("closed_at"))
    body = _clean_body(item.get("body"))

    lines = [
        "Source: GitHub",
        f"Repository: {repository}",
        f"Type: {type_label}",
        f"Number: #{number}",
        f"State: {_text(item.get('state')) or 'unknown'}",
        f"Title: {title}",
    ]
    if author:
        lines.append(f"Author: {author}")
    if labels:
        lines.append(f"Labels: {', '.join(labels)}")
    if assignees:
        lines.append(f"Assignees: {', '.join(assignees)}")
    if isinstance(milestone, dict) and milestone.get("title"):
        lines.append(f"Milestone: {milestone['title']}")
    if created_at:
        lines.append(f"Created: {created_at}")
    if updated_at:
        lines.append(f"Updated: {updated_at}")
    if closed_at:
        lines.append(f"Closed: {closed_at}")
    lines.append(f"URL: {source_url}")
    if body:
        lines.extend(["", "Body:", body])

    return GitHubSyncRecord(
        content="\n".join(lines).strip(),
        title=f"{repository} {type_label} #{number}: {title}"[:200],
        source_url=source_url[:500],
        external_id=f"github:{repository}:{record_scope}:{number}"[:240],
        captured_at=updated_at or created_at,
        metadata={
            "connector": GITHUB_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "repository": repository,
            "record_scope": record_scope,
            "github_number": str(number),
            "issue": str(number),
            "author": author,
            "state": _text(item.get("state")),
            "labels": labels,
            "url": source_url,
            "source_type": "github_pull_request" if is_pull_request else "github_issue",
            "source_quality": "canonical",
        },
    )


def _login(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("login") or "").strip()
    return ""


def _labels(value: Any) -> list[str]:
    labels: list[str] = []
    for item in value or []:
        if isinstance(item, dict):
            label = str(item.get("name") or "").strip()
        else:
            label = str(item or "").strip()
        if label:
            labels.append(label)
    return labels[:12]


def _clean_body(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return text[:20_000]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _max_iso(left: str | None, right: str | None) -> str | None:
    if not left:
        return right
    if not right:
        return left
    return max(left, right)
