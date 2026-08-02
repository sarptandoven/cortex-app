"""Agent-session harvesting connector.

The user already explains themselves to coding agents every day: every Claude Code / Codex /
Cursor session starts with the user stating intent, constraints, and preferences in their own
words. Those transcripts sit on local disk and evaporate from memory. This connector harvests
the USER'S OWN MESSAGES (never the agent's replies) from local session logs so they can flow
through the normal review pipeline into memory.

Hard rules:
  - User words only. Agent output is derived text; ingesting it would launder model prose into
    first-party evidence (same principle as excluding Cortex's own Obsidian write-back pages).
  - Local, read-only scanning. Session files are never modified; Cursor's sqlite is opened
    read-only and a locked database is an error entry, not a crash.
  - Noise-gated. Command wrappers, environment context, tool results, sidechain/subagent
    traffic, and sub-40-char steering ("continue", "yes") are skipped. Harvested records are
    review-gated by default at the storage layer.
  - Incremental. Files are processed oldest-first with an mtime high-water-mark cursor;
    re-scans only touch new/changed files, and stable external ids make re-emission a dedupe
    no-op.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any, Iterator

AGENT_SESSIONS_SOURCE = "agent-sessions"
CONNECTOR_VERSION = "2026-07-09"
KNOWN_AGENTS = ("claude", "codex", "cursor")

MAX_RECORDS = 500
DEFAULT_PER_SESSION_LIMIT = 25
# A typed user prompt is never megabytes; giant lines are embedded tool dumps / attachments.
MAX_LINE_BYTES = 1_000_000
# Keep one pasted wall-of-text from bloating a capture; the extractor summarizes anyway.
MAX_MESSAGE_CHARS = 8_000
# Below this a message is steering noise ("continue", "yes", "do it"), not self-explanation.
MIN_MESSAGE_CHARS = 40


@dataclass(frozen=True)
class AgentSessionRecord:
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
class AgentSessionScan:
    records: list[AgentSessionRecord]
    records_found: int
    records_returned: int
    files_seen: int
    files_scanned: int
    skipped_files: int
    truncated: bool
    high_water_mark: str | None
    cursor_value: str | None
    errors: list[dict[str, Any]]
    roots: dict[str, str]

    def to_summary(self) -> dict[str, Any]:
        return {
            "connector": AGENT_SESSIONS_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "records_found": self.records_found,
            "records_returned": self.records_returned,
            "files_seen": self.files_seen,
            "files_scanned": self.files_scanned,
            "skipped_files": self.skipped_files,
            "truncated": self.truncated,
            "high_water_mark": self.high_water_mark,
            "cursor_value": self.cursor_value,
            "errors": self.errors,
            "roots": self.roots,
        }


def default_roots() -> dict[str, Path]:
    home = Path.home()
    return {
        "claude": home / ".claude" / "projects",
        "codex": home / ".codex" / "sessions",
        "cursor": home / "Library" / "Application Support" / "Cursor" / "User" / "workspaceStorage",
    }


def scan_agent_sessions(
    *,
    claude_dir: str | Path | None = None,
    codex_dir: str | Path | None = None,
    cursor_dir: str | Path | None = None,
    agents: list[str] | None = None,
    max_records: int = 200,
    per_session_limit: int = DEFAULT_PER_SESSION_LIMIT,
    cursor_value: str | None = None,
) -> AgentSessionScan:
    """Scan local agent-session logs for the user's own messages.

    Files are processed oldest-mtime-first. ``cursor_value`` (``hwm=<iso>``) skips files not
    modified since the last completed scan. When ``max_records`` truncates the scan, the
    high-water mark only advances past FULLY processed files, so the partial file re-scans
    next time (stable external ids make the repeats dedupe no-ops downstream).
    """
    requested = _normalize_agents(agents)
    defaults = default_roots()
    roots: dict[str, Path] = {}
    overrides = {"claude": claude_dir, "codex": codex_dir, "cursor": cursor_dir}
    for agent in requested:
        override = overrides.get(agent)
        root = Path(override).expanduser() if override else defaults[agent]
        if override and not root.is_dir():
            raise ValueError(f"{agent} sessions folder is not a readable folder: {root}")
        roots[agent] = root

    capped_max = max(1, min(int(max_records or 200), MAX_RECORDS))
    session_cap = max(1, min(int(per_session_limit or DEFAULT_PER_SESSION_LIMIT), 200))
    previous_hwm = _cursor_high_water_mark(cursor_value)

    candidates: list[tuple[float, str, Path]] = []
    files_seen = 0
    errors: list[dict[str, Any]] = []
    for agent, root in roots.items():
        for path in _candidate_files(agent, root):
            try:
                mtime = path.stat().st_mtime
            except OSError as exc:
                errors.append({"agent": agent, "file": str(path), "error": str(exc), "category": "filesystem"})
                continue
            files_seen += 1
            candidates.append((mtime, agent, path))

    candidates.sort(key=lambda item: (item[0], str(item[2])))
    eligible = [item for item in candidates if _mtime_iso(item[0]) > previous_hwm] if previous_hwm else candidates
    skipped_files = len(candidates) - len(eligible)

    records: list[AgentSessionRecord] = []
    records_found = 0
    files_scanned = 0
    truncated = False
    high_water_mark = previous_hwm or None
    for mtime, agent, path in eligible:
        if len(records) >= capped_max:
            truncated = True
            break
        try:
            file_records = _records_for_file(agent, roots[agent], path, mtime=mtime, session_cap=session_cap)
        except Exception as exc:  # noqa: BLE001 — one bad session file must not sink the scan
            errors.append({"agent": agent, "file": str(path), "error": str(exc), "category": "parse"})
            files_scanned += 1
            high_water_mark = _max_iso(high_water_mark, _mtime_iso(mtime))
            continue
        files_scanned += 1
        records_found += len(file_records)
        remaining = capped_max - len(records)
        if len(file_records) > remaining:
            records.extend(file_records[:remaining])
            truncated = True
            # Do NOT advance the high-water mark past a partially emitted file.
            break
        records.extend(file_records)
        high_water_mark = _max_iso(high_water_mark, _mtime_iso(mtime))

    return AgentSessionScan(
        records=records,
        records_found=records_found if not truncated else max(records_found, len(records)),
        records_returned=len(records),
        files_seen=files_seen,
        files_scanned=files_scanned,
        skipped_files=skipped_files,
        truncated=truncated,
        high_water_mark=high_water_mark,
        cursor_value=f"hwm={high_water_mark}" if high_water_mark else None,
        errors=errors,
        roots={agent: str(root) for agent, root in roots.items()},
    )


# --- discovery -------------------------------------------------------------------------------


def _normalize_agents(agents: list[str] | None) -> list[str]:
    if not agents:
        return list(KNOWN_AGENTS)
    normalized: list[str] = []
    for agent in agents:
        cleaned = str(agent or "").strip().lower()
        if not cleaned:
            continue
        if cleaned not in KNOWN_AGENTS:
            raise ValueError(f"Unknown agent source: {cleaned!r} (expected one of {', '.join(KNOWN_AGENTS)})")
        if cleaned not in normalized:
            normalized.append(cleaned)
    if not normalized:
        return list(KNOWN_AGENTS)
    return normalized


def _candidate_files(agent: str, root: Path) -> Iterator[Path]:
    if not root.is_dir():
        return
    if agent == "claude":
        yield from (p for p in root.rglob("*.jsonl") if p.is_file())
    elif agent == "codex":
        yield from (p for p in root.rglob("*.jsonl") if p.is_file())
    elif agent == "cursor":
        yield from (p for p in root.glob("*/state.vscdb") if p.is_file())


def _records_for_file(agent: str, root: Path, path: Path, *, mtime: float, session_cap: int) -> list[AgentSessionRecord]:
    if agent == "claude":
        return _claude_records(root, path, session_cap=session_cap)
    if agent == "codex":
        return _codex_records(root, path, session_cap=session_cap)
    if agent == "cursor":
        return _cursor_records(root, path, mtime=mtime, session_cap=session_cap)
    return []


# --- Claude Code (~/.claude/projects/<project>/<session>.jsonl) -------------------------------


def _claude_records(root: Path, path: Path, *, session_cap: int) -> list[AgentSessionRecord]:
    session_id = path.stem
    records: list[AgentSessionRecord] = []
    seen_texts: set[str] = set()
    for line_no, obj in _iter_jsonl(path):
        if len(records) >= session_cap:
            break
        if obj.get("type") != "user" or obj.get("isMeta") or obj.get("isSidechain"):
            continue
        message = obj.get("message") or {}
        if message.get("role") != "user":
            continue
        text = _claude_message_text(message.get("content"))
        cleaned = _clean_user_text(text)
        if not cleaned:
            continue
        fingerprint = sha1(cleaned.encode("utf-8"), usedforsecurity=False).hexdigest()
        if fingerprint in seen_texts:
            continue
        seen_texts.add(fingerprint)
        uuid = str(obj.get("uuid") or f"line{line_no}")
        records.append(
            _record(
                agent="claude",
                root=root,
                path=path,
                anchor=uuid,
                content=cleaned,
                captured_at=str(obj.get("timestamp") or "") or None,
                session_id=session_id,
                extra={
                    "cwd": str(obj.get("cwd") or "") or None,
                    "git_branch": str(obj.get("gitBranch") or "") or None,
                },
            )
        )
    return records


def _claude_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = str(block.get("text") or "")
                if text.strip():
                    parts.append(text)
        return "\n\n".join(parts)
    return ""


# --- Codex (~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl) -------------------------------------


def _codex_records(root: Path, path: Path, *, session_cap: int) -> list[AgentSessionRecord]:
    records: list[AgentSessionRecord] = []
    seen_texts: set[str] = set()
    session_id = path.stem
    session_cwd: str | None = None
    for line_no, obj in _iter_jsonl(path):
        if len(records) >= session_cap:
            break
        payload = obj.get("payload") or {}
        if obj.get("type") == "session_meta":
            source = payload.get("source")
            if isinstance(source, dict) and "subagent" in source:
                # Guardian/subagent rollouts are agent-to-agent traffic, not the user's words.
                return []
            session_id = str(payload.get("id") or session_id)
            session_cwd = str(payload.get("cwd") or "") or None
            continue
        if obj.get("type") != "event_msg" or payload.get("type") != "user_message":
            continue
        cleaned = _clean_user_text(str(payload.get("message") or ""))
        if not cleaned:
            continue
        fingerprint = sha1(cleaned.encode("utf-8"), usedforsecurity=False).hexdigest()
        if fingerprint in seen_texts:
            continue
        seen_texts.add(fingerprint)
        records.append(
            _record(
                agent="codex",
                root=root,
                path=path,
                anchor=f"line{line_no}",
                content=cleaned,
                captured_at=str(obj.get("timestamp") or "") or None,
                session_id=session_id,
                extra={"cwd": session_cwd},
            )
        )
    return records


# --- Cursor (workspaceStorage/<hash>/state.vscdb, ItemTable key aiService.prompts) -------------


def _cursor_records(root: Path, path: Path, *, mtime: float, session_cap: int) -> list[AgentSessionRecord]:
    workspace_hash = path.parent.name
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=0", uri=True)
        try:
            connection.execute("PRAGMA query_only = 1")
            row = connection.execute(
                "SELECT value FROM ItemTable WHERE key = 'aiService.prompts'"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise RuntimeError(f"Cursor state database unreadable: {exc}") from exc
    if not row or not row[0]:
        return []
    try:
        prompts = json.loads(row[0])
    except (TypeError, ValueError):
        return []
    if not isinstance(prompts, list):
        return []
    captured_at = _mtime_iso(mtime)
    records: list[AgentSessionRecord] = []
    seen_texts: set[str] = set()
    for index, prompt in enumerate(prompts):
        if len(records) >= session_cap:
            break
        if not isinstance(prompt, dict):
            continue
        cleaned = _clean_user_text(str(prompt.get("text") or ""))
        if not cleaned:
            continue
        fingerprint = sha1(cleaned.encode("utf-8"), usedforsecurity=False).hexdigest()
        if fingerprint in seen_texts:
            continue
        seen_texts.add(fingerprint)
        records.append(
            _record(
                agent="cursor",
                root=root,
                path=path,
                anchor=f"prompt{index}",
                content=cleaned,
                captured_at=captured_at,
                session_id=workspace_hash,
                extra=None,
            )
        )
    return records


# --- shared -----------------------------------------------------------------------------------


def _iter_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle):
            if len(line) > MAX_LINE_BYTES:
                continue  # embedded tool dumps / attachments, never a typed prompt
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield line_no, obj


def _clean_user_text(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    if cleaned.startswith("<"):
        # Command wrappers, caveats, environment context, pasted tool output — harness plumbing,
        # not the user explaining themselves.
        return ""
    if len(cleaned) < MIN_MESSAGE_CHARS:
        return ""
    if len(cleaned) > MAX_MESSAGE_CHARS:
        cleaned = cleaned[:MAX_MESSAGE_CHARS].rstrip() + " …"
    return cleaned


def _record(
    *,
    agent: str,
    root: Path,
    path: Path,
    anchor: str,
    content: str,
    captured_at: str | None,
    session_id: str,
    extra: dict[str, Any] | None,
) -> AgentSessionRecord:
    try:
        relative_path = str(path.relative_to(root))
    except ValueError:
        relative_path = path.name
    external_id = _stable_external_id(agent, session_id, anchor)
    title = content.splitlines()[0][:120]
    metadata: dict[str, Any] = {
        "connector": AGENT_SESSIONS_SOURCE,
        "connector_version": CONNECTOR_VERSION,
        "agent": agent,
        "session_id": session_id,
        "relative_path": relative_path,
        "anchor": anchor,
    }
    for key, value in (extra or {}).items():
        if value:
            metadata[key] = value
    return AgentSessionRecord(
        content=content,
        title=title,
        source_url=f"{path.resolve().as_uri()}#{anchor}"[:500],
        external_id=external_id,
        captured_at=captured_at,
        metadata=metadata,
    )


def _stable_external_id(agent: str, session_id: str, anchor: str) -> str:
    raw = f"{agent}:{session_id}:{anchor}"
    if len(raw) <= 240:
        return raw
    digest = sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return f"{raw[:220]}#{digest}"


def _mtime_iso(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


def _max_iso(current: str | None, candidate: str) -> str:
    if not current:
        return candidate
    return candidate if candidate > current else current


def _cursor_high_water_mark(cursor_value: str | None) -> str:
    if not cursor_value:
        return ""
    for piece in str(cursor_value).split(";"):
        if "=" not in piece:
            continue
        key, value = piece.split("=", 1)
        if key.strip() == "hwm":
            return value.strip()
    return ""
