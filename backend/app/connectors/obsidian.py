from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
from typing import Any


OBSIDIAN_SOURCE = "obsidian"
CONNECTOR_VERSION = "2026-06-30"
ALLOWED_EXTENSIONS = {"md", "markdown", "txt"}
SKIPPED_DIRECTORIES = {".git", ".obsidian", ".trash", "node_modules"}
MAX_NOTE_BYTES = 200_000
MAX_RECORD_CHARS = 160_000

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(?P<body>.*?)\n---[ \t]*(?:\n|$)", re.DOTALL)
_FENCED_BLOCK_RE = re.compile(r"```(?P<language>[^\n`]*)\n(?P<body>.*?)```", re.DOTALL)
_WIKILINK_RE = re.compile(r"!?\[\[(?P<target>[^\]|#]+)(?:#[^\]|]+)?(?:\|(?P<alias>[^\]]+))?\]\]")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_TAG_RE = re.compile(r"(?<![\w/])#([A-Za-z0-9_][A-Za-z0-9_/-]*)")


@dataclass(frozen=True)
class ObsidianVaultRecord:
    content: str
    title: str
    source_url: str
    external_id: str
    captured_at: str
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
class ObsidianVaultScan:
    vault_path: str
    vault_name: str
    vault_id: str
    records: list[ObsidianVaultRecord]
    high_water_mark: str | None
    cursor_value: str
    extensions: list[str]
    records_found: int
    records_returned: int
    files_seen: int
    skipped: int
    errors: list[dict[str, Any]]
    manifest_hash: str
    truncated: bool

    def to_summary(self) -> dict[str, Any]:
        return {
            "connector": OBSIDIAN_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "vault_path": self.vault_path,
            "vault_name": self.vault_name,
            "vault_id": self.vault_id,
            "records_found": self.records_found,
            "records_returned": self.records_returned,
            "files_seen": self.files_seen,
            "skipped": self.skipped,
            "truncated": self.truncated,
            "errors": self.errors,
            "manifest_hash": self.manifest_hash,
            "high_water_mark": self.high_water_mark,
            "cursor_value": self.cursor_value,
            "extensions": self.extensions,
        }

    def summary(self) -> dict[str, Any]:
        return self.to_summary()


@dataclass(frozen=True)
class ParsedObsidianNote:
    content: str
    title: str
    frontmatter: dict[str, Any]
    tags: list[str]
    wikilinks: list[dict[str, str]]
    callouts: list[dict[str, str]]
    removed_blocks: dict[str, Any]


@dataclass(frozen=True)
class _MarkdownSection:
    title: str
    slug: str
    ordinal: int
    line_start: int
    line_end: int
    raw: str


def scan_obsidian_vault(vault_path: str | Path, *, limit: int = 200) -> ObsidianVaultScan:
    root = Path(vault_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError("Obsidian vault path must be a readable folder")

    candidates: list[tuple[float, Path, int]] = []
    seen_extensions: set[str] = set()
    files_seen = 0
    skipped = 0
    errors: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            dirname for dirname in dirnames
            if dirname.lower() not in SKIPPED_DIRECTORIES and not dirname.startswith(".")
        ]
        for filename in filenames:
            path = Path(dirpath) / filename
            suffix = path.suffix.lower().lstrip(".")
            if suffix not in ALLOWED_EXTENSIONS:
                continue
            files_seen += 1
            try:
                stat = path.stat()
            except OSError as exc:
                errors.append({"path": _safe_relative(root, path), "error": str(exc)})
                skipped += 1
                continue
            if stat.st_size <= 0 or stat.st_size > MAX_NOTE_BYTES:
                skipped += 1
                continue
            seen_extensions.add(suffix)
            candidates.append((stat.st_mtime, path, stat.st_size))

    capped_limit = max(1, min(limit, 5_000))
    ordered_candidates = sorted(candidates, key=lambda item: (-item[0], _relative_external_id(root, item[1])))
    selected = ordered_candidates[:capped_limit]
    records: list[ObsidianVaultRecord] = []
    high_water: float | None = None
    manifest_parts = [
        f"{_relative_external_id(root, path)}:{size}:{int(modified)}"
        for modified, path, size in ordered_candidates
    ]
    for modified, path, size in selected:
        try:
            raw = _read_text(path)
        except OSError as exc:
            errors.append({"path": _safe_relative(root, path), "error": str(exc)})
            skipped += 1
            continue
        note_records = _records_for_note(root, path, raw, modified=modified, size=size)
        if not note_records:
            skipped += 1
            continue
        high_water = max(high_water or modified, modified)
        records.extend(note_records)

    high_water_mark = _iso_from_timestamp(high_water) if high_water is not None else None
    cursor_value = f"{len(records)}:{high_water_mark or 'none'}"
    return ObsidianVaultScan(
        vault_path=str(root),
        vault_name=root.name or "Obsidian vault",
        vault_id=_stable_scan_id(str(root)),
        records=records,
        high_water_mark=high_water_mark,
        cursor_value=cursor_value,
        extensions=sorted(seen_extensions),
        records_found=len(ordered_candidates),
        records_returned=len(records),
        files_seen=files_seen,
        skipped=skipped,
        errors=errors[:25],
        manifest_hash=_stable_scan_id("\n".join(sorted(manifest_parts))),
        truncated=len(ordered_candidates) > len(selected),
    )


def scan_vault(vault_path: str | Path, *, max_records: int = 200) -> ObsidianVaultScan:
    return scan_obsidian_vault(vault_path, limit=max_records)


def parse_note(raw: str, *, fallback_title: str = "Untitled") -> ParsedObsidianNote:
    cleaned, frontmatter, tags, wikilinks, callouts, removed_blocks = _parse_obsidian_markdown(raw)
    return ParsedObsidianNote(
        content=cleaned,
        title=_note_title(Path(fallback_title), cleaned, frontmatter),
        frontmatter=frontmatter,
        tags=sorted(tags),
        wikilinks=wikilinks,
        callouts=callouts,
        removed_blocks=removed_blocks,
    )


def stable_external_id(root: str | Path, path: str | Path) -> str:
    relative_id = _relative_external_id(Path(root).expanduser().resolve(), Path(path).expanduser().resolve())
    if len(relative_id) <= 240:
        return relative_id
    digest = _stable_scan_id(relative_id).removeprefix("obs_")[:16]
    return f"{relative_id[:221]}#{digest}"


def clean_obsidian_markdown(raw: str) -> tuple[str, dict[str, Any], set[str]]:
    cleaned, frontmatter, tags, _wikilinks, _callouts, _removed_blocks = _parse_obsidian_markdown(raw)
    return cleaned, frontmatter, tags


def _records_for_note(root: Path, path: Path, raw: str, *, modified: float, size: int) -> list[ObsidianVaultRecord]:
    cleaned, frontmatter, tags, wikilinks, callouts, removed_blocks = _parse_obsidian_markdown(raw)
    if not cleaned:
        return []

    relative_id = _relative_external_id(root, path)
    note_title = _note_title(path, cleaned, frontmatter)
    base_metadata = {
        "connector": "obsidian",
        "connector_version": CONNECTOR_VERSION,
        "vault_name": root.name,
        "vault_id": _stable_scan_id(str(root)),
        "relative_path": relative_id,
        "extension": path.suffix.lower().lstrip("."),
        "size_bytes": size,
        "tags": sorted(tags),
        "frontmatter": frontmatter,
        "wikilinks": wikilinks,
        "callouts": callouts,
        "removed_blocks": removed_blocks,
    }
    captured_at = _iso_from_timestamp(modified)
    source_url = path.resolve().as_uri()[:500]

    sections = _markdown_sections(raw)
    if not sections:
        return [
            ObsidianVaultRecord(
                content=cleaned[:MAX_RECORD_CHARS],
                title=note_title,
                external_id=stable_external_id(root, path),
                source_url=source_url,
                captured_at=captured_at,
                metadata={**base_metadata, "record_scope": "note", "line_start": 1, "line_end": _line_count(raw)},
            )
        ]

    records: list[ObsidianVaultRecord] = []
    for section in sections:
        section_cleaned, _section_frontmatter, section_tags, section_wikilinks, section_callouts, section_removed = _parse_obsidian_markdown(section.raw)
        if not section_cleaned:
            continue
        record_title = note_title if section.title.casefold() == note_title.casefold() else f"{note_title} / {section.title}"[:200]
        records.append(
            ObsidianVaultRecord(
                content=section_cleaned[:MAX_RECORD_CHARS],
                title=record_title,
                external_id=stable_section_external_id(root, path, section.slug, section.ordinal),
                source_url=source_url,
                captured_at=captured_at,
                metadata={
                    **base_metadata,
                    "record_scope": "section",
                    "note_external_id": stable_external_id(root, path),
                    "section_title": section.title,
                    "section_slug": section.slug,
                    "section_index": section.ordinal,
                    "line_start": section.line_start,
                    "line_end": section.line_end,
                    "section_tags": sorted(section_tags),
                    "section_wikilinks": section_wikilinks,
                    "section_callouts": section_callouts,
                    "section_removed_blocks": section_removed,
                },
            )
        )
    if not records:
        return [
            ObsidianVaultRecord(
                content=cleaned[:MAX_RECORD_CHARS],
                title=note_title,
                external_id=stable_external_id(root, path),
                source_url=source_url,
                captured_at=captured_at,
                metadata={**base_metadata, "record_scope": "note", "line_start": 1, "line_end": _line_count(raw)},
            )
        ]
    return records


def _markdown_sections(raw: str) -> list[_MarkdownSection]:
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    match = _FRONTMATTER_RE.match(text)
    frontmatter_lines = 0
    if match:
        frontmatter_lines = _line_count(text[:match.end()])
        text = text[match.end():]

    lines = text.split("\n")
    headings: list[tuple[int, str, str]] = []
    in_fence = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        title = _clean_heading_title(match.group(2))
        if title:
            headings.append((index, title, _slugify(title)))
    if not headings:
        return []

    slug_counts: dict[str, int] = {}
    sections: list[_MarkdownSection] = []
    for position, (start_index, title, slug) in enumerate(headings):
        end_index = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        raw_section = "\n".join(lines[start_index:end_index]).strip()
        if not raw_section:
            continue
        ordinal = slug_counts.get(slug, 0) + 1
        slug_counts[slug] = ordinal
        sections.append(
            _MarkdownSection(
                title=title,
                slug=slug,
                ordinal=ordinal,
                line_start=frontmatter_lines + start_index + 1,
                line_end=frontmatter_lines + end_index,
                raw=raw_section,
            )
        )
    return sections


def stable_section_external_id(root: str | Path, path: str | Path, section_slug: str, section_ordinal: int = 1) -> str:
    note_id = stable_external_id(root, path)
    suffix = f"#heading={section_slug}"
    if section_ordinal > 1:
        suffix = f"{suffix}~{section_ordinal}"
    external_id = f"{note_id}{suffix}"
    if len(external_id) <= 240:
        return external_id
    digest = _stable_scan_id(external_id).removeprefix("obs_")[:16]
    return f"{note_id[: max(1, 240 - len(suffix) - 17)]}#{digest}{suffix}"[:240]


def _parse_obsidian_markdown(raw: str) -> tuple[str, dict[str, Any], set[str], list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    frontmatter: dict[str, Any] = {}
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    match = _FRONTMATTER_RE.match(text)
    if match:
        frontmatter = _parse_frontmatter(match.group("body"))
        text = text[match.end():]

    wikilinks = _extract_wikilinks(text)
    callouts = _extract_callouts(text)
    removed_blocks = _removed_fenced_blocks(text)
    tags = {_normalize_tag(tag) for tag in _TAG_RE.findall(text)}
    tags.update(_frontmatter_tags(frontmatter))
    text = _FENCED_BLOCK_RE.sub(_clean_fenced_block, text)
    text = _WIKILINK_RE.sub(_replace_wikilink, text)
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)

    cleaned_lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append("")
            continue
        if re.match(r"^>\s*\[![A-Z]+[^\]]*\]", stripped, flags=re.IGNORECASE):
            continue
        stripped = re.sub(r"^>\s?", "", stripped)
        stripped = re.sub(r"^[-*]\s+\[[ xX]\]\s+", "- ", stripped)
        stripped = _TAG_RE.sub(lambda match: _normalize_tag(match.group(1)).replace("/", " "), stripped)
        cleaned_lines.append(stripped)

    collapsed = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned_lines)).strip()
    return collapsed, frontmatter, {tag for tag in tags if tag}, wikilinks, callouts, removed_blocks


def _clean_fenced_block(match: re.Match[str]) -> str:
    return "\n"


def _replace_wikilink(match: re.Match[str]) -> str:
    if match.group(0).startswith("!"):
        return ""
    alias = (match.group("alias") or "").strip()
    target = (match.group("target") or "").strip()
    if alias:
        return alias
    return target.rsplit("/", 1)[-1]


def _extract_wikilinks(text: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for match in _WIKILINK_RE.finditer(text):
        target = (match.group("target") or "").strip()
        alias = (match.group("alias") or "").strip()
        display = alias or target.rsplit("/", 1)[-1]
        embedded = "true" if match.group(0).startswith("!") else "false"
        key = (target, display, embedded)
        if target and key not in seen:
            links.append({"target": target, "display": display, "embedded": embedded})
            seen.add(key)
    return links


def _extract_callouts(text: str) -> list[dict[str, str]]:
    callouts: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in text.split("\n"):
        match = re.match(r"^\s*>\s*\[!([A-Za-z0-9_-]+)\][+-]?\s*(.*?)\s*$", line)
        if not match:
            continue
        key = (match.group(1).lower(), match.group(2).strip())
        if key in seen:
            continue
        callouts.append({"type": key[0], "title": key[1]})
        seen.add(key)
    return callouts


def _removed_fenced_blocks(text: str) -> dict[str, Any]:
    languages: set[str] = set()
    count = 0
    for match in _FENCED_BLOCK_RE.finditer(text):
        count += 1
        language = match.group("language").strip().lower()
        if language:
            languages.add(language)
    return {"code_blocks": count, "code_languages": sorted(languages)}


def _parse_frontmatter(raw: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    current_key = ""
    for line in raw.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        list_item = re.match(r"^-\s*(.+?)\s*$", stripped)
        if list_item and current_key:
            existing = parsed.setdefault(current_key, [])
            if not isinstance(existing, list):
                existing = [existing]
                parsed[current_key] = existing
            existing.append(_parse_yaml_scalar(list_item.group(1)))
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            continue
        current_key = key
        value = value.strip()
        parsed[key] = [] if value == "" else _parse_yaml_value(value)
    return parsed


def _frontmatter_tags(frontmatter: dict[str, Any]) -> set[str]:
    values: list[Any] = []
    for key in ("tag", "tags"):
        value = frontmatter.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif isinstance(value, str):
            values.extend(re.split(r"[, ]+", value))
        elif value:
            values.append(value)
    return {tag for tag in (_normalize_tag(value) for value in values) if tag}


def _parse_yaml_value(value: str) -> Any:
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_yaml_scalar(part.strip()) for part in inner.split(",")]
    return _parse_yaml_scalar(value)


def _parse_yaml_scalar(value: str) -> Any:
    stripped = str(value).strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        stripped = stripped[1:-1]
    lowered = stripped.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return stripped


def _normalize_tag(value: Any) -> str:
    tag = str(value or "").strip().lstrip("#")
    tag = re.sub(r"\s+", "-", tag)
    return re.sub(r"[^A-Za-z0-9_/-]+", "", tag).strip("/-")


def _note_title(path: Path, cleaned: str, frontmatter: dict[str, Any]) -> str:
    frontmatter_title = str(frontmatter.get("title") or "").strip()
    if frontmatter_title:
        return frontmatter_title[:200]
    for line in cleaned.split("\n")[:20]:
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title[:200]
    return path.stem[:200]


def _clean_heading_title(value: str) -> str:
    title = _WIKILINK_RE.sub(_replace_wikilink, value)
    title = _MARKDOWN_LINK_RE.sub(r"\1", title)
    title = _TAG_RE.sub("", title)
    return re.sub(r"\s+", " ", title).strip()[:200]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "section"


def _line_count(value: str) -> int:
    if not value:
        return 0
    return value.replace("\r\n", "\n").replace("\r", "\n").count("\n") + 1


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    return data.decode("utf-8", errors="replace")


def _relative_external_id(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.name


def _safe_relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except Exception:
        return str(path)


def _stable_scan_id(value: str) -> str:
    import hashlib

    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"obs_{digest}"


def _iso_from_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
