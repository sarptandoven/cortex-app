"""Human-readable Markdown mirror for the Cortex native vault.

Each memory is rendered as a Markdown note with YAML frontmatter, so a user can open their
Cortex memory folder in Obsidian (or any editor), read it, grep it, back it up, and own it —
even if Cortex itself goes away ("file over app"). The SQLite index stays the query engine;
these files are the durable, portable, user-facing source of truth.

Stdlib-only on purpose: the shipping backend runs under `python3 -S` (no site-packages), so no
PyYAML. We exploit the fact that **YAML is a superset of JSON**: every frontmatter value is
emitted as its compact JSON form (`key: <json>`), which is valid YAML that Obsidian parses AND
round-trips exactly via `json.loads`. A lenient fallback parser tolerates hand-edited
frontmatter (bare scalars, unquoted lists) so Phase-3 two-way editing stays forgiving.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

# Frontmatter fields, in a stable, human-friendly order. Everything else that a memory record
# carries is still emitted (after these) so the round-trip is lossless; `content` is the body.
MEMORY_FRONTMATTER_FIELDS: tuple[str, ...] = (
    "id",
    "kind",
    "layer",
    "confidence",
    "importance",
    "status",
    "sector",
    "source",
    "source_url",
    "occurred_at",
    "valid_from",
    "valid_to",
    "superseded_by",
    "captured_at",
    "updated_at",
    "capture_id",
    "user_id",
    "topics",
    "entity_ids",
    "summary",
    "raw_excerpt",
    "provenance",
)

_FENCE = "---"

# Generated-section markers. render_memory_markdown appends a "## Links" and (optionally)
# "## Backlinks" block AFTER the body, fenced by _GENERATED_MARKER at BOTH ends;
# parse_memory_markdown STRIPS exactly that fenced region so a render -> hand-edit -> parse
# round-trip never absorbs generated wikilinks into `content` (which is the vault's rebuild
# source of truth). Text before AND after the fence survives, so editing the note in Obsidian
# is safe. The block itself is regenerated from entity_ids/topics on every write — editing it in
# place is a no-op on the index.
_LINKS_HEADING = "## Links"
_BACKLINKS_HEADING = "## Backlinks"
_GENERATED_MARKER = "<!-- cortex:generated-links -->"

# Record keys that feed the generated sections. They are consumed by render and NEVER emitted to
# frontmatter or body. Any other underscore-prefixed helper key is likewise kept out of the note.
_GENERATED_KEYS = ("_link_names", "_backlinks")


def _wikilink(name: str) -> str:
    """Render an Obsidian [[target]] link, sanitizing the characters that would break the link
    syntax (]/[ close/open, | alias, # heading-ref, ^ block-ref). Empty -> "" (caller skips)."""
    text = str(name or "")
    for bad in ("]", "[", "|", "#", "^"):
        text = text.replace(bad, " ")
    text = " ".join(text.split())
    return f"[[{text}]]" if text else ""


def _dump_value(value: Any) -> str:
    # Compact JSON is valid YAML for scalars/flow-collections/mappings and round-trips exactly.
    return json.dumps(value, ensure_ascii=False)


def _parse_value(raw: str) -> Any:
    raw = raw.strip()
    if raw == "":
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Lenient fallback for hand-edited YAML frontmatter.
    lowered = raw.lower()
    if lowered in ("null", "~", "none"):
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    stripped = raw.strip()
    try:
        if stripped.lstrip("-").isdigit():
            return int(stripped)
        return float(stripped)
    except (TypeError, ValueError):
        pass
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip('"').strip("'") for item in inner.split(",") if item.strip()]
    return raw.strip('"').strip("'")


def render_memory_markdown(record: dict[str, Any]) -> str:
    """Render a memory record as a frontmatter+body Markdown note."""
    lines: list[str] = [_FENCE]
    emitted: set[str] = set()
    for field in MEMORY_FRONTMATTER_FIELDS:
        if field in record and record[field] is not None:
            lines.append(f"{field}: {_dump_value(record[field])}")
            emitted.add(field)
    # Preserve any additional structured fields (except the body) so nothing is silently lost.
    # Underscore-prefixed keys (e.g. _link_names/_backlinks) are internal render inputs, never
    # frontmatter — skipping them here is what stops the generated-link data leaking into YAML.
    for key in sorted(record.keys()):
        if key in emitted or key == "content" or key.startswith("_"):
            continue
        value = record[key]
        if value is None:
            continue
        lines.append(f"{key}: {_dump_value(value)}")
    lines.append(_FENCE)
    lines.append("")
    lines.append(str(record.get("content") or "").rstrip("\n"))

    generated = _render_generated_sections(record)
    if generated:
        lines.append("")
        lines.append(generated)
    lines.append("")
    return "\n".join(lines)


def _render_generated_sections(record: dict[str, Any]) -> str:
    """Build the fenced "## Links" (+ optional "## Backlinks") block from entity_ids/topics.

    Reads two OPTIONAL render inputs the record may carry:
      record["_link_names"]: {entity_id: canonical display name}; missing ids fall back to the id.
      record["_backlinks"]: [{"id": memory_id, "label": short text}] co-mention neighbours.
    Returns "" when there is nothing to link (so a note with no entities/topics/backlinks is
    byte-identical to today's output). Fenced by _GENERATED_MARKER at both ends so parse can strip
    it deterministically."""
    id_to_name = record.get("_link_names") or {}
    entity_links: list[str] = []
    seen: set[str] = set()
    for entity_id in (record.get("entity_ids") or []):
        eid = str(entity_id or "").strip()
        if not eid or eid in seen:
            continue
        seen.add(eid)
        link = _wikilink(str(id_to_name.get(eid) or eid).strip())
        if link:
            entity_links.append(link)
    topic_links: list[str] = []
    tseen: set[str] = set()
    for topic in (record.get("topics") or []):
        text = str(topic or "").strip()
        if not text or text.lower() in tseen:
            continue
        tseen.add(text.lower())
        link = _wikilink(text)
        if link:
            topic_links.append(link)

    backlinks = record.get("_backlinks") or []
    if not entity_links and not topic_links and not backlinks:
        return ""

    out: list[str] = [_GENERATED_MARKER, _LINKS_HEADING, ""]
    if entity_links:
        out.append("People & things: " + " ".join(entity_links))
    if topic_links:
        out.append("Topics: " + " ".join(topic_links))
    if backlinks:
        out.append("")
        out.append(_BACKLINKS_HEADING)
        out.append("")
        for backlink in backlinks:
            if not isinstance(backlink, dict):
                continue
            bid = str(backlink.get("id") or "").strip()
            if not bid:
                continue
            label = str(backlink.get("label") or bid).strip().replace("\n", " ")
            # Note-to-note wikilink by memory id (each memory note filename is <id>.md).
            out.append(f"- [[{bid}]]" + (f" — {label}" if label and label != bid else ""))
    out.append(_GENERATED_MARKER)
    return "\n".join(out)


def _strip_generated_sections(body_lines: list[str]) -> list[str]:
    """Inverse of _render_generated_sections: remove the cortex-generated block (the region fenced
    by _GENERATED_MARKER at both ends) so `content` never absorbs generated wikilinks — which would
    compound on every re-render and corrupt the rebuild source of truth. Text before AND after the
    fence is preserved (a user may hand-edit anywhere). A stray opening marker with no closing
    marker drops to end-of-note (the block is always emitted last, so this only fires on corruption)."""
    open_idx = None
    for index, raw in enumerate(body_lines):
        if raw.strip() == _GENERATED_MARKER:
            open_idx = index
            break
    if open_idx is None:
        return body_lines
    for index in range(open_idx + 1, len(body_lines)):
        if body_lines[index].strip() == _GENERATED_MARKER:
            return body_lines[:open_idx] + body_lines[index + 1:]
    return body_lines[:open_idx]


def parse_memory_markdown(text: str) -> dict[str, Any]:
    """Inverse of render_memory_markdown: frontmatter -> typed fields, body -> content."""
    record: dict[str, Any] = {}
    lines = text.split("\n")
    body_start = 0
    if lines and lines[0].strip() == _FENCE:
        closing = None
        for index in range(1, len(lines)):
            if lines[index].strip() == _FENCE:
                closing = index
                break
        if closing is not None:
            for frontmatter_line in lines[1:closing]:
                stripped = frontmatter_line.strip()
                if not stripped or stripped.startswith("#") or ":" not in frontmatter_line:
                    continue
                key, _, rest = frontmatter_line.partition(":")
                key = key.strip()
                if key:
                    record[key] = _parse_value(rest)
            body_start = closing + 1
    body_lines = _strip_generated_sections(lines[body_start:])
    record["content"] = "\n".join(body_lines).strip("\n")
    return record


# Entity Map-of-Content (MOC) page frontmatter, stable order. cortex_generated:true marks the
# page machine-owned so it is never treated as user-authored (and it lives OUTSIDE memories/, so
# reconcile literally cannot see it). No generated timestamp -> an unchanged graph renders a
# byte-identical page, so a git/iCloud-synced vault gets no churny diffs.
ENTITY_MOC_FRONTMATTER_FIELDS: tuple[str, ...] = (
    "cortex_generated",
    "entity_id",
    "kind",
    "name",
    "aliases",
    "centrality",
    "community",
    "supporting_count",
)


def _moc_alias_link(target: str, display: str = "") -> str:
    """An Obsidian [[target|display]] link. target is a file stem (already path-safe); display is
    sanitized to not break the link. Falls back to [[target]] when display is empty/equal."""
    tgt = str(target or "").strip()
    if not tgt:
        return ""
    show = str(display or "").strip()
    for bad in ("]", "[", "|"):
        show = show.replace(bad, " ")
    show = " ".join(show.split())
    return f"[[{tgt}|{show}]]" if show and show != tgt else f"[[{tgt}]]"


def render_entity_moc_markdown(page: dict[str, Any]) -> str:
    """Render one entity Map-of-Content page: cortex_generated frontmatter + a body of [[wikilinks]]
    to co-mentioned entities and cited memories. Human-browsable; machine-owned; deterministic."""
    lines: list[str] = [_FENCE]
    for field in ENTITY_MOC_FRONTMATTER_FIELDS:
        if field in page and page[field] is not None:
            lines.append(f"{field}: {_dump_value(page[field])}")
    lines.append(_FENCE)
    lines.append("")
    lines.append(f"# {page.get('name') or page.get('entity_id') or 'Entity'}")
    lines.append("")

    aliases = [str(a).strip() for a in (page.get("aliases") or []) if str(a).strip()]
    if aliases:
        lines.append("**Also known as:** " + ", ".join(aliases))
        lines.append("")

    connections = page.get("connections") or []
    if connections:
        lines.append("## Connected")
        lines.append("")
        for conn_item in connections:
            if not isinstance(conn_item, dict):
                continue
            link = _moc_alias_link(conn_item.get("wikilink") or "", conn_item.get("name") or "")
            if not link:
                continue
            rel = str(conn_item.get("relation") or "related").strip()
            weight = conn_item.get("weight")
            suffix = f" — {rel}" if rel else ""
            try:
                if weight and float(weight) >= 1:
                    suffix += f" (×{int(float(weight))})"
            except (TypeError, ValueError):
                pass
            lines.append(f"- {link}{suffix}")
        lines.append("")

    memory_links = page.get("memory_links") or []
    if memory_links:
        lines.append("## Supporting memories")
        lines.append("")
        for mem in memory_links:
            if not isinstance(mem, dict):
                continue
            target = str(mem.get("wikilink") or "").strip()
            if not target:
                continue
            note = str(mem.get("note") or "").strip().replace("\n", " ")
            lines.append(f"- [[{target}]]" + (f" — {note}" if note else ""))
        lines.append("")

    peers = page.get("community_peers") or []
    if peers:
        lines.append("## Same area")
        lines.append("")
        chips = [
            _moc_alias_link(peer.get("wikilink") or "", peer.get("name") or "")
            for peer in peers
            if isinstance(peer, dict) and (peer.get("wikilink"))
        ]
        chips = [chip for chip in chips if chip]
        if chips:
            lines.append(", ".join(chips))
            lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def atomic_write_text(path: Path, text: str) -> None:
    """Crash-safe write: temp file -> fsync -> atomic replace -> fsync parent directory.

    The temp name is unique per writer (pid + thread id + randomness): the shipping server is
    multi-threaded under one PID, so a pid-only temp path lets two concurrent writes to the same
    note collide — one thread's os.replace moves the shared temp out from under the other,
    raising FileNotFoundError or installing a half-written note (and this note is the rebuild
    source of truth). The temp is unlinked on failure so a crash mid-write leaves no litter.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.{os.urandom(4).hex()}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except OSError:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    try:
        dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except (OSError, AttributeError):
        # Directory fsync is best-effort (not supported on every platform, e.g. some Windows).
        pass
