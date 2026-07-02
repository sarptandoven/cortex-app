from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from email.utils import parseaddr
from collections.abc import Iterable
from typing import Any


def stable_id(prefix: str, content: str) -> str:
    return prefix + hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


MEMORY_LAYERS = {"semantic", "episodic", "style", "decision", "preference", "negative", "procedural"}
USER_AUTHORED_ROLES = {"user", "human", "me", "self"}
ASSISTANT_ROLES = {"assistant", "model", "bot", "tool", "system", "chatgpt", "claude"}
NAMED_SPEAKER_ROLE = "speaker"
KNOWN_TURN_ROLES = USER_AUTHORED_ROLES | ASSISTANT_ROLES | {NAMED_SPEAKER_ROLE}
PERSONAL_MEMORY_KINDS = {"preference", "style", "negative"}
# Deterministic extraction keeps only the highest-priority candidates per capture so a
# small note or a single conversation turn-set yields a focused memory set. Connector and
# file imports pre-chunk structured exports (via chunk markers), so each chunk gets its own
# budget. A large *flat* free-form capture (a long pasted note, or an un-chunked import doc
# with no chunk markers) is a single unit, so a fixed cap would silently drop everything past
# the top BASE_EXTRACTION_CANDIDATE_LIMIT candidates. For such non-conversational text we
# scale the budget with the content (up to a safety ceiling) so nothing is lost; conversational
# text keeps the focused base cap because its personal-memory gating is turn-sensitive and
# large multi-turn exports are meant to be chunked upstream.
BASE_EXTRACTION_CANDIDATE_LIMIT = 40
MAX_EXTRACTION_CANDIDATE_LIMIT = 2000
# The LLM extraction path sends the capture text to the model in one request. A single
# request both truncates the input and caps the output, so a large capture would silently
# lose everything past this many characters. We window large captures into <= this size and
# extract each window, so nothing is dropped (mirroring the deterministic path's scaling).
CLAUDE_EXTRACTION_WINDOW_CHARS = 40000
# Cap the number of windows so a pathologically large capture can't fan out into unbounded
# model requests; matches the deterministic ceiling in spirit (very large but bounded).
MAX_CLAUDE_EXTRACTION_WINDOWS = 12
CONVERSATION_SOURCES = {
    "chatgpt",
    "claude",
    "copilot",
    "discord",
    "email",
    "gemini",
    "gmail",
    "google-chat",
    "grok",
    "linkedin",
    "messages",
    "notebooklm",
    "perplexity",
    "poe",
    "slack",
    "teams",
    "telegram",
    "twitter-x",
    "whatsapp",
    "zoom",
}
SELF_AUTHORED_UNATTRIBUTED_SOURCES = {
    "apple-notes",
    "docs",
    "file",
    "google-keep",
    "knowledge-base",
    "local",
    "logseq",
    "manual",
    "note",
    "notes",
    "notion",
    "obsidian",
    "quick-note",
    "readwise",
    "roam",
    "writing",
}
STRICT_UNATTRIBUTED_PERSONAL_SOURCES = CONVERSATION_SOURCES | {
    "asana",
    "browser-bookmarks",
    "browser-capture",
    "browser-history",
    "calendar",
    "cloud-docs",
    "contacts",
    "github",
    "gitlab",
    "instapaper",
    "jira",
    "linear",
    "pocket",
    "raindrop",
    "structured-export",
    "trello",
    "work-tools",
}
BOILERPLATE_PREFIXES = {
    "aliases",
    "archive file",
    "assignee",
    "author",
    "channel",
    "chat",
    "conversation",
    "created",
    "created at",
    "cssclasses",
    "date",
    "export",
    "file",
    "folder",
    "from",
    "html url",
    "id",
    "identifier",
    "key",
    "last updated",
    "labels",
    "modified",
    "modified at",
    "number",
    "path",
    "repo",
    "repository",
    "reporter",
    "source",
    "source file",
    "state",
    "status",
    "structured csv export",
    "structured contacts export",
    "subject",
    "tags",
    "title",
    "to",
    "updated",
    "url",
}
CONTENT_BEARING_LABELS = {
    "body",
    "comment",
    "content",
    "description",
    "excerpt",
    "highlight",
    "note",
    "notes",
    "summary",
    "text",
    "title",
}
SERVICE_DATE_HEADERS = {"created", "created at", "date", "edited", "updated", "user edited"}
NON_SPEAKER_LABELS = BOILERPLATE_PREFIXES | {
    "action item",
    "attendees",
    "description",
    "email",
    "location",
    "note",
    "org",
    "organizer",
    "phone",
    "summary",
    "thread note",
    "url",
}
MONTHS = {
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
MONTH_NAME_PATTERN = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)


def extract_context(
    raw_text: str,
    source: str = "unknown",
    author_aliases: Iterable[str] | None = None,
    extraction_mode: str | None = None,
) -> dict[str, Any]:
    mode = (extraction_mode or os.environ.get("CORTEX_EXTRACTION_MODE") or "auto").strip().lower()
    if mode in {"local", "deterministic", "connector"}:
        return _extract_locally(raw_text, source, author_aliases=author_aliases)
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _extract_with_claude_windowed(raw_text, source, author_aliases=author_aliases)
        except Exception:
            pass
    return _extract_locally(raw_text, source, author_aliases=author_aliases)


def _claude_extraction_windows(raw_text: str) -> list[str]:
    """Split a large capture into <= CLAUDE_EXTRACTION_WINDOW_CHARS windows on line
    boundaries (never mid-line, so role-prefixed turns stay intact). A capture that fits
    in a single window returns [raw_text] unchanged."""
    if len(raw_text) <= CLAUDE_EXTRACTION_WINDOW_CHARS:
        return [raw_text]
    windows: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in raw_text.splitlines(keepends=True):
        # A single line longer than the window is hard-split as a last resort.
        while len(line) > CLAUDE_EXTRACTION_WINDOW_CHARS:
            if current:
                windows.append("".join(current))
                current, current_len = [], 0
            windows.append(line[:CLAUDE_EXTRACTION_WINDOW_CHARS])
            line = line[CLAUDE_EXTRACTION_WINDOW_CHARS:]
        if current and current_len + len(line) > CLAUDE_EXTRACTION_WINDOW_CHARS:
            windows.append("".join(current))
            current, current_len = [], 0
        current.append(line)
        current_len += len(line)
    if current:
        windows.append("".join(current))
    return windows[:MAX_CLAUDE_EXTRACTION_WINDOWS]


def _merge_extractions(parts: list[dict[str, Any]], raw_text: str, source: str) -> dict[str, Any]:
    """Merge per-window extraction results into one, de-duplicating records/tasks by id
    (which is content-derived) and unioning entities, then re-normalizing over the full
    capture text so top-level metadata reflects the whole capture."""
    records: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    entities_by_id: dict[str, dict[str, Any]] = {}
    summaries: list[str] = []
    seen_records: set[str] = set()
    seen_tasks: set[str] = set()
    for part in parts:
        for record in part.get("records", []):
            key = str(record.get("id") or record.get("content") or "")
            if key and key not in seen_records:
                seen_records.add(key)
                records.append(record)
        for task in part.get("tasks", []):
            key = str(task.get("id") or task.get("content") or "")
            if key and key not in seen_tasks:
                seen_tasks.add(key)
                tasks.append(task)
        for entity in part.get("entities", []):
            entity_id = str(entity.get("id") or "")
            if entity_id:
                entities_by_id.setdefault(entity_id, entity)
        summary = str(part.get("summary") or "").strip()
        if summary:
            summaries.append(summary)
    merged = {
        "records": records,
        "tasks": tasks,
        "entities": list(entities_by_id.values()),
        "summary": " ".join(summaries)[:2000],
    }
    return _normalize_extraction(merged, raw_text, source)


def _extract_with_claude_windowed(
    raw_text: str, source: str, author_aliases: Iterable[str] | None = None
) -> dict[str, Any]:
    windows = _claude_extraction_windows(raw_text)
    if len(windows) <= 1:
        return _extract_with_claude(raw_text, source, author_aliases=author_aliases)
    parts = [_extract_with_claude(window, source, author_aliases=author_aliases) for window in windows]
    return _merge_extractions(parts, raw_text, source)


def _extract_with_claude(raw_text: str, source: str, author_aliases: Iterable[str] | None = None) -> dict[str, Any]:
    from anthropic import Anthropic

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    aliases = ", ".join(sorted(_identity_alias_tokens(author_aliases))[:12])
    alias_instruction = f"\nUser-authored aliases in speaker-based imports: {aliases}. Only treat preference, style, and negative records as user memory when authored by those aliases or an explicit user/human/me role." if aliases else ""
    prompt = """Extract Cortex memory as strict JSON with keys records, tasks, entities, summary.
records: list of {id, kind, layer, content, confidence, importance, entity_ids, topics, occurred_at, valid_from, valid_to, sector}
tasks: list of {id, kind, content, status, importance, entity_ids, topics}
entities: list of {id, kind, name, aliases, context}
Kinds: claim, decision, event, preference, observation, style, negative, procedure. Layers: semantic, episodic, style, decision, preference, negative, procedural. Task kinds: action, question, decision-pending.
Use stable IDs and keep each memory atomic. Return JSON only.""" + alias_instruction
    response = client.messages.create(
        model=os.environ.get("CORTEX_EXTRACTION_MODEL", "claude-opus-4-5"),
        max_tokens=2500,
        system=prompt,
        messages=[{"role": "user", "content": f"Source: {source}\n\n{raw_text[:CLAUDE_EXTRACTION_WINDOW_CHARS]}"}],
    )
    text = response.content[0].text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    normalized = _normalize_extraction(data, raw_text, source)
    return _filter_disallowed_personal_records(normalized, raw_text, source, author_aliases)


def _extraction_candidate_limit(memory_candidate_count: int, has_known_turns: bool) -> int:
    """How many prioritized candidates one capture keeps as memories.

    Conversational captures keep the focused base budget (each turn-set is meant to yield a
    bounded memory set, its personal-memory gating is turn-sensitive, and large multi-turn
    exports are chunked upstream). Flat free-form captures have no chunk boundaries, so we
    scale the budget with the content — up to MAX_EXTRACTION_CANDIDATE_LIMIT — instead of
    silently dropping everything past the base cap.
    """
    if has_known_turns:
        return BASE_EXTRACTION_CANDIDATE_LIMIT
    return max(
        BASE_EXTRACTION_CANDIDATE_LIMIT,
        min(memory_candidate_count, MAX_EXTRACTION_CANDIDATE_LIMIT),
    )


def _extract_locally(raw_text: str, source: str, author_aliases: Iterable[str] | None = None) -> dict[str, Any]:
    candidates = _sentence_candidates(raw_text, source, author_aliases=author_aliases)
    has_known_turns = any(candidate.get("role") in KNOWN_TURN_ROLES for candidate in candidates)
    allow_unattributed_personal_memory = _allow_unattributed_personal_memory(raw_text, source)
    memory_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("role") not in ASSISTANT_ROLES
        and not _is_disallowed_user_preference_candidate(candidate, has_known_turns, allow_unattributed_personal_memory)
    ]
    memory_candidates = _dedupe_candidates(memory_candidates)
    sentences = [candidate["text"] for candidate in memory_candidates]
    cleaned = " ".join(sentences)
    summary = _summarize(sentences, cleaned)
    records: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []

    candidate_limit = _extraction_candidate_limit(len(memory_candidates), has_known_turns)
    for candidate in _prioritized_extraction_candidates(memory_candidates, limit=candidate_limit):
        sentence = candidate["text"]
        lower = sentence.lower()
        personal_allowed = _allows_user_authored_memory(candidate, has_known_turns, allow_unattributed_personal_memory)
        if _looks_like_task(sentence):
            tasks.append(_task(sentence))
        elif _looks_like_negative(lower):
            if not personal_allowed:
                continue
            records.append(_record("negative", sentence, importance=4, occurred_at=candidate.get("occurred_at")))
        elif _looks_like_procedure(lower):
            records.append(_record("procedure", sentence, importance=4, occurred_at=candidate.get("occurred_at")))
        elif _looks_like_decision(lower):
            records.append(_record("decision", sentence, importance=4, occurred_at=candidate.get("occurred_at")))
        elif _looks_like_style(lower):
            if not personal_allowed:
                continue
            records.append(_record("style", sentence, importance=3, occurred_at=candidate.get("occurred_at")))
        elif _looks_like_preference(lower):
            if not personal_allowed:
                continue
            records.append(_record("preference", sentence, importance=3, occurred_at=candidate.get("occurred_at")))
        elif _looks_like_event(lower):
            records.append(_record("event", sentence, importance=3, occurred_at=candidate.get("occurred_at")))
        elif len(sentence.split()) >= 5:
            records.append(_record("claim", sentence, importance=2, occurred_at=candidate.get("occurred_at")))

    if summary and not records:
        records.insert(0, _record("summary", summary, importance=3))

    entities = _entities(cleaned)
    entity_ids = [entity["id"] for entity in entities]
    topics = _topics(cleaned)
    for item in records:
        item["entity_ids"] = entity_ids[:12]
        item["topics"] = topics[:4]
    for item in tasks:
        item["entity_ids"] = entity_ids[:12]
        item["topics"] = topics[:4]

    return _normalize_extraction({"records": records, "tasks": tasks, "entities": entities, "summary": summary}, raw_text, source)


def _normalize_extraction(data: dict[str, Any], raw_text: str, source: str) -> dict[str, Any]:
    timestamp = now_iso()
    data.setdefault("records", [])
    data.setdefault("tasks", [])
    data.setdefault("entities", [])
    data.setdefault("summary", "")
    data["_source"] = source
    data["_source_id"] = stable_id("src_", source + timestamp + raw_text[:80])
    data["_timestamp"] = timestamp
    data["_raw_length"] = len(raw_text)
    for record in data["records"]:
        content = str(record.get("content", "")).strip()
        record["id"] = record.get("id") or stable_id("mem_", content)
        record["kind"] = record.get("kind") or "observation"
        record["layer"] = _normalize_layer(record.get("layer"), record["kind"], content)
        record["confidence"] = record.get("confidence") or "confirmed"
        record["importance"] = int(record.get("importance") or 3)
        record["entity_ids"] = list(record.get("entity_ids") or [])
        record["topics"] = list(record.get("topics") or [])
        record["content"] = content
        record["raw_excerpt"] = str(record.get("raw_excerpt") or content).strip()[:500]
        record["occurred_at"] = record.get("occurred_at") or _extract_absolute_date(content)
        record["valid_from"] = record.get("valid_from") or None
        record["valid_to"] = record.get("valid_to") or None
        record["sector"] = str(record.get("sector") or "").strip()[:120]
    for task in data["tasks"]:
        content = str(task.get("content", "")).strip()
        task["id"] = task.get("id") or stable_id("task_", content)
        task["kind"] = task.get("kind") or ("question" if content.endswith("?") else "action")
        task["status"] = task.get("status") or "open"
        task["importance"] = int(task.get("importance") or 3)
        task["entity_ids"] = list(task.get("entity_ids") or [])
        task["topics"] = list(task.get("topics") or [])
    return data


def _filter_disallowed_personal_records(
    data: dict[str, Any],
    raw_text: str,
    source: str,
    author_aliases: Iterable[str] | None = None,
) -> dict[str, Any]:
    candidates = _sentence_candidates(raw_text, source, author_aliases=author_aliases)
    has_known_turns = any(candidate.get("role") in KNOWN_TURN_ROLES for candidate in candidates)
    allow_unattributed_personal_memory = _allow_unattributed_personal_memory(raw_text, source)
    filtered: list[dict[str, Any]] = []
    for record in data.get("records", []):
        kind = str(record.get("kind") or "").strip().lower()
        layer = str(record.get("layer") or "").strip().lower()
        if kind not in PERSONAL_MEMORY_KINDS and layer not in PERSONAL_MEMORY_KINDS:
            filtered.append(record)
            continue
        if _record_has_allowed_personal_evidence(
            record,
            candidates,
            has_known_turns,
            allow_unattributed_personal_memory,
        ):
            filtered.append(record)
    data["records"] = filtered
    return data


def _record_has_allowed_personal_evidence(
    record: dict[str, Any],
    candidates: list[dict[str, Any]],
    has_known_turns: bool,
    allow_unattributed_personal_memory: bool,
) -> bool:
    if allow_unattributed_personal_memory and not has_known_turns:
        return True
    content = str(record.get("content") or record.get("summary") or "").strip()
    for candidate in candidates:
        if not _texts_overlap(content, candidate.get("text", "")):
            continue
        if _allows_user_authored_memory(candidate, has_known_turns, allow_unattributed_personal_memory):
            return True
    return False


def _texts_overlap(left: str, right: str) -> bool:
    left_key = re.sub(r"\s+", " ", str(left or "").strip().lower())
    right_key = re.sub(r"\s+", " ", str(right or "").strip().lower())
    if not left_key or not right_key:
        return False
    return left_key in right_key or right_key in left_key


def _sentence_candidates(text: str, source: str = "unknown", author_aliases: Iterable[str] | None = None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    current_role: str | None = None
    aliases = _identity_alias_tokens(author_aliases)
    normalized_source = _normalize_source_label(source)
    email_source = normalized_source in {"email", "gmail"}
    allow_named_speakers = _allows_named_speaker_labels(normalized_source)
    saw_email_header = False
    saw_email_from = False
    email_from_is_user = False
    current_date: str | None = None
    frontmatter_possible = True
    in_frontmatter = False
    in_fenced_block = False
    in_callout_block = False
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        raw_stripped = raw_line.strip()
        if in_frontmatter:
            if raw_stripped in {"---", "..."}:
                in_frontmatter = False
            continue
        if frontmatter_possible:
            if not raw_stripped:
                continue
            if raw_stripped == "---":
                in_frontmatter = True
                frontmatter_possible = False
                continue
            frontmatter_possible = False
        if re.match(r"^(```|~~~)", raw_stripped):
            in_fenced_block = not in_fenced_block
            continue
        if in_fenced_block:
            continue
        if in_callout_block:
            if raw_stripped.startswith(">"):
                continue
            in_callout_block = False
        if re.match(r"^>\s*\[![A-Za-z0-9_-]+\]", raw_stripped):
            in_callout_block = True
            continue

        line = _clean_import_line(raw_line).strip(" -•\t")
        if not line:
            if email_source and saw_email_header and saw_email_from:
                current_role = "user" if email_from_is_user else NAMED_SPEAKER_ROLE
            continue
        line_date = _extract_absolute_date(line)
        email_header = re.match(r"^(?P<label>[A-Za-z][A-Za-z0-9 _-]{0,40})\s*:\s*(?P<text>.*)$", line)
        if email_header:
            header_label = email_header.group("label").strip().lower()
            header_text = email_header.group("text").strip().lower()
            if header_label == "source" and header_text == "email":
                email_source = True
                saw_email_header = True
            elif email_source and header_label in {"subject", "from", "to", "date", "cc", "bcc", "reply-to"}:
                saw_email_header = True
                if header_label == "from" and header_text:
                    saw_email_from = True
                    email_from_is_user = _matches_identity_alias(header_text, aliases)
                if header_label == "date":
                    current_date = _extract_absolute_date(header_text) or line_date or current_date
            elif header_label in SERVICE_DATE_HEADERS and (line_date or _extract_absolute_date(header_text)):
                current_date = _extract_absolute_date(header_text) or line_date or current_date
        content_payload = _content_label_payload(line, normalized_source)
        if content_payload:
            line = content_payload
        elif _is_boilerplate_line(line):
            if line_date and email_source:
                current_date = line_date
            continue
        role, payload, speaker_present = _parse_role_line(line, aliases, allow_named_speakers=allow_named_speakers)
        if email_from_is_user and role == NAMED_SPEAKER_ROLE:
            role = "user"
        if role:
            current_role = role
            line = payload
        elif speaker_present:
            current_role = None
        elif current_role:
            role = current_role
        for segment_text, segment_role, segment_speaker_present in _inline_speaker_segments(
            line,
            role,
            speaker_present,
            aliases,
            allow_named_speakers=allow_named_speakers,
        ):
            for sentence in _sentences(segment_text):
                if _is_boilerplate_line(sentence):
                    continue
                sentence_date = _extract_absolute_date(sentence) or line_date or current_date
                candidates.append({"text": sentence, "role": segment_role, "speaker_present": segment_speaker_present, "occurred_at": sentence_date})
    return candidates


def _clean_import_line(raw_line: str) -> str:
    line = str(raw_line or "").strip()
    if not line:
        return ""
    if re.match(r"^>\s*\[![A-Za-z0-9_-]+\]", line):
        return ""
    line = re.sub(r"^>\s?", "", line)
    line = re.sub(r"<!--.*?-->", "", line)
    line = re.sub(r"!\[\[[^\]]+\]\]", "", line)
    line = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", line)
    line = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", lambda match: match.group(2).strip(), line)
    line = re.sub(r"\[\[([^\]]+)\]\]", lambda match: _clean_wikilink_target(match.group(1)), line)
    line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
    line = re.sub(r"(?<!\w)#([A-Za-z][A-Za-z0-9_/-]*)", lambda match: _clean_tag_token(match.group(1)), line)
    line = re.sub(r"^\s*[-*+]\s+\[[ xX]\]\s+", "", line)
    line = re.sub(r"^\s*[-*+]\s+", "", line)
    return re.sub(r"\s+", " ", line).strip()


def _clean_wikilink_target(value: str) -> str:
    target = str(value or "").split("#", 1)[0].strip()
    target = target.rsplit("/", 1)[-1].strip()
    return target


def _clean_tag_token(value: str) -> str:
    token = str(value or "").rsplit("/", 1)[-1].replace("_", " ").strip()
    return token


def _content_label_payload(line: str, source: str) -> str:
    if _normalize_source_label(source) in CONVERSATION_SOURCES:
        return ""
    match = re.match(r"^(?P<label>[A-Za-z][A-Za-z0-9 _-]{0,40})\s*:\s*(?P<text>.+)$", line)
    if not match:
        return ""
    label = match.group("label").strip().lower()
    if label not in CONTENT_BEARING_LABELS:
        return ""
    payload = match.group("text").strip()
    if _is_boilerplate_line(payload):
        return ""
    return payload


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for candidate in candidates:
        key = re.sub(r"\s+", " ", candidate.get("text", "").strip().lower())
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _prioritized_extraction_candidates(candidates: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    if len(candidates) <= limit:
        return candidates

    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, candidate in enumerate(candidates):
        scored.append((_candidate_memory_priority(candidate), index, candidate))
    selected = sorted(scored, key=lambda item: (item[0], item[1]))[:limit]
    return [candidate for _priority, _index, candidate in sorted(selected, key=lambda item: item[1])]


def _candidate_memory_priority(candidate: dict[str, Any]) -> int:
    lower = str(candidate.get("text") or "").lower()
    if _looks_like_decision(lower):
        return 0
    if _looks_like_procedure(lower):
        return 1
    if _looks_like_task(str(candidate.get("text") or "")):
        return 2
    if _looks_like_negative(lower) or _looks_like_style(lower) or _looks_like_preference(lower):
        return 3
    if _looks_like_event(lower):
        return 4
    return 5


def _parse_role_line(
    line: str,
    identity_aliases: set[str] | None = None,
    *,
    allow_named_speakers: bool = True,
) -> tuple[str | None, str, bool]:
    timestamped = re.match(
        r"^(?:\[[^\]]+\]|[0-9T:.,+/\- ]{8,40})\s+(?P<label>[A-Za-z][A-Za-z0-9 _.'-]{0,40})\s*:\s*(?P<text>.+)$",
        line,
        flags=re.IGNORECASE,
    )
    if timestamped:
        label = timestamped.group("label").strip()
        role = _normalize_role(label, identity_aliases)
        if role:
            return role, timestamped.group("text").strip(), True
        if _looks_like_named_speaker(label, allow_lowercase=True):
            return NAMED_SPEAKER_ROLE, timestamped.group("text").strip(), True
        return None, line, False

    match = re.match(r"^(?P<label>[A-Za-z][A-Za-z0-9 _.'-]{0,40})\s*:\s*(?P<text>.+)$", line)
    if not match:
        return None, line, False

    label = match.group("label").strip()
    role = _normalize_role(label, identity_aliases)
    if role:
        return role, match.group("text").strip(), True
    if allow_named_speakers and _looks_like_named_speaker(label, allow_lowercase=True):
        return NAMED_SPEAKER_ROLE, match.group("text").strip(), True
    return None, line, False


def _inline_speaker_segments(
    text: str,
    current_role: str | None,
    current_speaker_present: bool,
    identity_aliases: set[str],
    *,
    allow_named_speakers: bool,
) -> list[tuple[str, str | None, bool]]:
    if not allow_named_speakers or ":" not in text:
        return [(text, current_role, current_speaker_present)]

    matches: list[tuple[re.Match[str], str | None, bool]] = []
    for match in re.finditer(r"(?:(?<=^)|(?<=[.!?])\s+)(?P<label>[A-Za-z][A-Za-z0-9 _.'-]{0,40})\s*:\s*", text):
        label = match.group("label").strip()
        role = _normalize_role(label, identity_aliases)
        speaker = False
        if role:
            speaker = True
        elif _looks_like_named_speaker(label, allow_lowercase=True):
            role = NAMED_SPEAKER_ROLE
            speaker = True
        if speaker:
            matches.append((match, role, True))
    if not matches:
        return [(text, current_role, current_speaker_present)]

    segments: list[tuple[str, str | None, bool]] = []
    first_match = matches[0][0]
    prefix = text[: first_match.start()].strip()
    if prefix:
        segments.append((prefix, current_role, current_speaker_present))
    for index, (match, role, speaker_present) in enumerate(matches):
        start = match.end()
        end = matches[index + 1][0].start() if index + 1 < len(matches) else len(text)
        payload = text[start:end].strip()
        if payload:
            segments.append((payload, role, speaker_present))
    return segments


def _normalize_role(value: str, identity_aliases: set[str] | None = None) -> str | None:
    role = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    aliases = {
        "human": "human",
        "user": "user",
        "me": "me",
        "self": "self",
        "assistant": "assistant",
        "model": "model",
        "bot": "bot",
        "tool": "tool",
        "system": "system",
        "chatgpt": "chatgpt",
        "claude": "claude",
    }
    known = aliases.get(role)
    if known:
        return known
    if _matches_identity_alias(value, identity_aliases or set()):
        return "user"
    return None


def _identity_alias_tokens(values: Iterable[str] | None) -> set[str]:
    tokens: set[str] = set()
    if not values:
        return tokens
    for value in values:
        tokens.update(_identity_terms(str(value or "")))
    return {token for token in tokens if token}


def _identity_terms(value: str) -> set[str]:
    terms: set[str] = set()
    raw = str(value or "").strip()
    if not raw:
        return terms
    normalized = _normalize_identity_value(raw)
    if normalized:
        terms.add(normalized)
    name, address = parseaddr(raw)
    for part in (name, address):
        normalized_part = _normalize_identity_value(part)
        if normalized_part:
            terms.add(normalized_part)
    for email in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", raw):
        normalized_email = _normalize_identity_value(email)
        if normalized_email:
            terms.add(normalized_email)
        local = email.split("@", 1)[0]
        normalized_local = _normalize_identity_value(local)
        if normalized_local:
            terms.add(normalized_local)
    for normalized_term in list(terms):
        if "@" in normalized_term:
            continue
        for token in normalized_term.split():
            if len(token) >= 4:
                terms.add(token)
    return terms


def _normalize_identity_value(value: str) -> str:
    cleaned = str(value or "").strip().strip("@").lower()
    if not cleaned:
        return ""
    return re.sub(r"[^a-z0-9@._+-]+", " ", cleaned).strip()


def _matches_identity_alias(value: str, identity_aliases: set[str]) -> bool:
    if not identity_aliases:
        return False
    return bool(_identity_terms(value) & identity_aliases)


def _looks_like_named_speaker(label: str, allow_lowercase: bool = False) -> bool:
    cleaned = label.strip()
    lowered = cleaned.lower()
    if lowered in NON_SPEAKER_LABELS:
        return False
    if len(cleaned) > 40 or any(char.isdigit() for char in cleaned):
        return False
    words = cleaned.split()
    if not words or len(words) > 4:
        return False
    if allow_lowercase:
        return all(re.match(r"^[A-Za-z][A-Za-z0-9_.'-]*$", word) for word in words)
    return cleaned[:1].isupper()


def _normalize_source_label(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", str(value or "").strip().lower()).strip("-")


def _allows_named_speaker_labels(source: str) -> bool:
    return _normalize_source_label(source) in CONVERSATION_SOURCES


def _allow_unattributed_personal_memory(raw_text: str, source: str) -> bool:
    normalized_source = _normalize_source_label(source)
    if normalized_source in SELF_AUTHORED_UNATTRIBUTED_SOURCES:
        return True
    if normalized_source == "twitter-x":
        return "--- Tweets ---" in raw_text and "--- Direct Messages ---" not in raw_text
    if normalized_source in STRICT_UNATTRIBUTED_PERSONAL_SOURCES:
        return False
    if normalized_source.startswith("browser"):
        return False
    return True


def _is_boilerplate_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    if re.fullmatch(r"-{2,}\s*[^-]*\s*-{2,}", stripped):
        return True
    if re.match(r"^#{1,6}\s+\S", stripped):
        return True
    if re.fullmatch(r"!?\[\[[^\]]+\]\]", stripped):
        return True
    if re.fullmatch(r"row\s+\d+", lowered):
        return True
    if lowered in {"table of contents", "synced block placeholder"}:
        return True
    if "manage notification preferences" in lowered:
        return True
    if lowered.startswith(("[bot] source:", "google docs footer", "last edited by ", "suggested edit:")):
        return True
    if lowered in {"messages", "events", "contacts", "bookmarks", "recent visits", "rows", "tweets", "direct messages"}:
        return True
    header = re.match(r"^([a-z][a-z0-9 _-]{0,40})\s*:", lowered)
    if header and header.group(1).strip() in BOILERPLATE_PREFIXES:
        return True
    if lowered.startswith("[truncated by cortex importer"):
        return True
    return False


def _allows_user_authored_memory(
    candidate: dict[str, Any],
    has_known_turns: bool,
    allow_unattributed_personal_memory: bool,
) -> bool:
    role = candidate.get("role")
    if role in USER_AUTHORED_ROLES:
        return True
    if role in ASSISTANT_ROLES:
        return False
    if role == NAMED_SPEAKER_ROLE:
        return False
    if candidate.get("speaker_present"):
        return False
    return allow_unattributed_personal_memory and not has_known_turns


def _is_disallowed_user_preference_candidate(
    candidate: dict[str, Any],
    has_known_turns: bool,
    allow_unattributed_personal_memory: bool,
) -> bool:
    return _looks_like_user_preference_memory(candidate["text"].lower()) and not _allows_user_authored_memory(
        candidate, has_known_turns, allow_unattributed_personal_memory
    )


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip(" -•\t") for p in parts if len(p.strip()) > 12]


def _summarize(sentences: list[str], text: str) -> str:
    if not text:
        return ""
    selected = sentences[:2] if sentences else [text[:240]]
    return " ".join(selected)[:500]


def _looks_like_decision(lower: str) -> bool:
    signals = ["decision:", "decided", "we will", "we'll", "going with", "chose", "choice is", "let's use", "agreed"]
    return any(signal in lower for signal in signals)


def _looks_like_preference(lower: str) -> bool:
    signals = ["i prefer", "i like", "i don't like", "i want", "i need", "preference"]
    return any(signal in lower for signal in signals)


def _looks_like_user_preference_memory(lower: str) -> bool:
    return _looks_like_preference(lower) or _looks_like_style(lower) or any(
        signal in lower
        for signal in [
            "i don't like",
            "i dislike",
            "i hate",
            "avoid ",
            "rejected",
            "do not ",
            "don't ",
            "never use",
            "not helpful",
            "bad fit",
        ]
    )


def _looks_like_negative(lower: str) -> bool:
    signals = [
        "i don't like",
        "i dislike",
        "i hate",
        "avoid ",
        "rejected",
        "do not ",
        "don't ",
        "never use",
        "not helpful",
        "bad fit",
    ]
    return any(signal in lower for signal in signals)


def _looks_like_style(lower: str) -> bool:
    signals = [
        "my writing",
        "writing style",
        "tone",
        "phrasing",
        "voice",
        "sentence length",
        "formatting",
        "i usually write",
        "i say",
    ]
    return any(signal in lower for signal in signals)


def _looks_like_event(lower: str) -> bool:
    signals = [
        "yesterday",
        "today",
        "last week",
        "last month",
        "met with",
        "talked to",
        "emailed",
        "shipped",
        "launched",
    ]
    return any(signal in lower for signal in signals) or _extract_absolute_date(lower) is not None


def _looks_like_procedure(lower: str) -> bool:
    signals = [
        "process:",
        "procedure:",
        "runbook",
        "workflow",
        "when i ",
        "when we ",
        "the way i ",
        "the way we ",
        "always start by",
        "start by",
        "before shipping",
        "before deploying",
        "to deploy",
        "to release",
        "steps:",
        "step 1",
        "first,",
        "then,",
    ]
    return any(signal in lower for signal in signals)


def _looks_like_task(sentence: str) -> bool:
    lower = sentence.lower()
    return sentence.endswith("?") or any(signal in lower for signal in ["todo", "to do", "need to", "follow up", "we should", "i should", "next step", "open question"])


def _record(kind: str, content: str, importance: int, occurred_at: str | None = None) -> dict[str, Any]:
    return {
        "id": stable_id("mem_", kind + content),
        "kind": kind,
        "layer": _normalize_layer(None, kind, content),
        "content": content,
        "raw_excerpt": content[:500],
        "confidence": "confirmed",
        "importance": importance,
        "entity_ids": [],
        "topics": [],
        "occurred_at": _extract_absolute_date(content) or occurred_at,
    }


def _normalize_layer(value: Any, kind: str, content: str = "") -> str:
    layer = str(value or "").strip().lower()
    if layer in MEMORY_LAYERS:
        return layer
    kind = str(kind or "").strip().lower()
    if kind in {"decision"}:
        return "decision"
    if kind in {"preference"}:
        return "preference"
    if kind in {"event"}:
        return "episodic"
    if kind in {"style"}:
        return "style"
    if kind in {"negative"}:
        return "negative"
    if kind in {"procedure"}:
        return "procedural"
    lower = content.lower()
    if _looks_like_negative(lower):
        return "negative"
    if _looks_like_style(lower):
        return "style"
    if _looks_like_event(lower):
        return "episodic"
    if _looks_like_procedure(lower):
        return "procedural"
    return "semantic"


def _task(content: str) -> dict[str, Any]:
    kind = "question" if content.endswith("?") else "action"
    return {
        "id": stable_id("task_", kind + content),
        "kind": kind,
        "content": content,
        "status": "open",
        "importance": 3,
        "entity_ids": [],
        "topics": [],
    }


def _entities(text: str) -> list[dict[str, Any]]:
    candidates = set(re.findall(r"\b[A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+){0,3}\b", text))
    stop = {"I", "The", "This", "That", "We", "You", "Next", "Open"}
    org_like = {
        "ChatGPT",
        "Claude",
        "GitHub",
        "Google",
        "Notion",
        "OpenAI",
        "Redis",
        "SQLite",
        "Supabase",
    }
    project_like = {"Cortex"}
    topic_like = {"MCP", "MVP"}
    entities = []
    for name in sorted(candidates):
        if name in stop or len(name) < 3:
            continue
        if name in org_like:
            kind = "org"
        elif name in project_like or any(word in name.lower() for word in ["app", "project", "cortex"]):
            kind = "project"
        elif name in topic_like or (name.isupper() and len(name) <= 5):
            kind = "topic"
        else:
            kind = "person"
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        prefix = {"project": "project_", "org": "org_", "topic": "topic_"}.get(kind, "person_")
        entities.append({
            "id": prefix + slug,
            "kind": kind,
            "name": name,
            "aliases": [],
            "context": f"Mentioned in context captured from {text[:40].strip()}...",
        })
    return entities[:12]


def _topics(text: str) -> list[str]:
    words = re.findall(r"\b[a-z][a-z0-9-]{3,}\b", text.lower())
    banned = {"this", "that", "with", "from", "have", "will", "about", "there", "their", "what", "when", "where", "your"}
    counts: dict[str, int] = {}
    for word in words:
        if word not in banned:
            counts[word] = counts.get(word, 0) + 1
    return [word for word, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:8]]


def _extract_absolute_date(text: str) -> str | None:
    if not text:
        return None
    iso_match = re.search(r"\b((?:19|20)\d{2})[-/](\d{1,2})[-/](\d{1,2})(?=\D|$)", text)
    if iso_match:
        return _iso_date(iso_match.group(1), iso_match.group(2), iso_match.group(3))

    compact_match = re.search(r"\b((?:19|20)\d{2})(\d{2})(\d{2})(?:T\d{6}Z?)?\b", text)
    if compact_match:
        return _iso_date(compact_match.group(1), compact_match.group(2), compact_match.group(3))

    slash_match = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\b", text)
    if slash_match:
        year = int(slash_match.group(3))
        if year < 100:
            year += 2000 if year < 70 else 1900
        return _iso_date(str(year), slash_match.group(1), slash_match.group(2))

    month_first = re.search(
        rf"\b({MONTH_NAME_PATTERN})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+((?:19|20)\d{{2}})\b",
        text,
        flags=re.IGNORECASE,
    )
    if month_first:
        month = _month_number(month_first.group(1))
        if month:
            return _iso_date(month_first.group(3), str(month), month_first.group(2))

    day_first = re.search(
        rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTH_NAME_PATTERN})\.?[,]?\s+((?:19|20)\d{{2}})\b",
        text,
        flags=re.IGNORECASE,
    )
    if day_first:
        month = _month_number(day_first.group(2))
        if month:
            return _iso_date(day_first.group(3), str(month), day_first.group(1))

    rfc_email = re.search(
        rf"\b(?:mon|tue|wed|thu|fri|sat|sun),?\s+(\d{{1,2}})\s+({MONTH_NAME_PATTERN})\.?\s+((?:19|20)\d{{2}})\b",
        text,
        flags=re.IGNORECASE,
    )
    if rfc_email:
        month = _month_number(rfc_email.group(2))
        if month:
            return _iso_date(rfc_email.group(3), str(month), rfc_email.group(1))

    service_archive = re.search(
        rf"\b(?:mon|tue|wed|thu|fri|sat|sun),?\s+({MONTH_NAME_PATTERN})\.?\s+(\d{{1,2}})\s+\d{{1,2}}:\d{{2}}:\d{{2}}\s+(?:[+-]\d{{4}}\s+)?((?:19|20)\d{{2}})\b",
        text,
        flags=re.IGNORECASE,
    )
    if service_archive:
        month = _month_number(service_archive.group(1))
        if month:
            return _iso_date(service_archive.group(3), str(month), service_archive.group(2))
    return None


def _month_number(value: str) -> int | None:
    return MONTHS.get(value.lower().rstrip("."))


def _iso_date(year: str, month: str, day: str) -> str | None:
    try:
        return datetime(int(year), int(month), int(day)).date().isoformat()
    except ValueError:
        return None
