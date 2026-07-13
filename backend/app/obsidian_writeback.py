"""Obsidian write-back — the vault becomes the white-box face of Cortex.

Renders a small set of DISTILLED, CITED pages into the user's own Obsidian vault under a
single machine-owned folder (``Cortex/``):

    Cortex/README.md            what this folder is, and that Cortex owns these files
    Cortex/Profile.md           the cited personal profile (build_profile sections)
    Cortex/People/<page>.md     one page per key person (person_context: commitments,
                                decisions, recent context — each line cited)

Design discipline (matches mirror.py / profile.py / vault_markdown.py):

1. **Pure.** This module renders already-materialized dicts to Markdown strings. No DB, no
   LLM, no network, no filesystem. The store decides what to write and where.
2. **Deterministic.** The same input produces byte-identical output — there are NO
   timestamps in the rendered bytes. Idempotency is therefore plain byte-equality: the
   store skips the write when the rendered page equals the file on disk.
3. **Cited or silent.** Every bullet carries its memory id and source locator. Sections
   with nothing cited are omitted. An uncited claim in the user's own vault would be the
   worst kind of lie — silence is the safe default.
4. **Machine-owned, human-safe.** Every generated page carries ``cortex_generated: true``
   frontmatter. The store only overwrites/prunes files that carry the marker, so a user
   file dropped into ``Cortex/`` is never touched. The Obsidian *connector* skips marked
   files on ingestion, so Cortex never eats its own output.

Frontmatter values are compact JSON (valid YAML — same trick as vault_markdown.py), so
Obsidian renders them and tests can round-trip them exactly.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .config import APP_BRAND

WRITEBACK_DIRNAME = APP_BRAND
PEOPLE_DIRNAME = "People"
README_FILENAME = "README.md"
PROFILE_FILENAME = "Profile.md"
GENERATED_MARKER_KEY = "cortex_generated"
PAGE_KIND_KEY = "cortex_page"

_FENCE = "---"


def _dump_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


def _frontmatter(fields: list[tuple[str, Any]]) -> list[str]:
    lines = [_FENCE]
    for key, value in fields:
        if value is None:
            continue
        lines.append(f"{key}: {_dump_value(value)}")
    lines.append(_FENCE)
    return lines


def safe_page_stem(name: str, fallback: str = "page") -> str:
    """Filesystem-safe filename stem from a human label. Path separators and traversal are
    impossible by construction (only [A-Za-z0-9_.-] survives, dots collapse)."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(name or "")).strip("-_")
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    return cleaned[:60] or fallback


def person_page_filename(entity_id: str, name: str) -> str:
    """``<slug>--<hash12>.md`` — slug for human browsing, hash so the page survives label
    edits/collisions (same scheme as the native vault's entity MOC pages)."""
    short = hashlib.sha1(str(entity_id or "").encode("utf-8")).hexdigest()[:12]
    return f"{safe_page_stem(name, 'person')}--{short}.md"


def _citation_suffix(memory_id: str | None, source: str | None, source_url: str | None) -> str:
    parts: list[str] = []
    if source:
        parts.append(str(source))
    if source_url:
        parts.append(str(source_url))
    if memory_id:
        parts.append(f"`{memory_id}`")
    return f" — {' · '.join(parts)}" if parts else ""


def render_readme() -> str:
    lines = _frontmatter([
        (GENERATED_MARKER_KEY, True),
        (PAGE_KIND_KEY, "readme"),
        ("title", f"{APP_BRAND} — generated pages"),
    ])
    lines.extend([
        "",
        f"# {APP_BRAND} — generated pages",
        "",
        f"{APP_BRAND} maintains this folder. Every page here is **generated from your cited memory**",
        f"and is rewritten whenever {APP_BRAND} refreshes it — edits to generated pages will be lost.",
        "",
        "- Every bullet cites the memory id and source it came from.",
        "- Files marked `cortex_generated: true` are machine-owned; your own notes in this",
        "  folder (without that marker) are never modified, and are ingested like any note.",
        f"- Generated pages are **excluded from {APP_BRAND} ingestion**, so {APP_BRAND} never re-reads",
        "  its own output as new evidence.",
        "",
        "Pages:",
        "",
        f"- [[{PROFILE_FILENAME[:-3]}]] — your cited personal profile",
        f"- `{PEOPLE_DIRNAME}/` — one page per key person in your world",
        "",
    ])
    return "\n".join(lines).rstrip("\n") + "\n"


def render_profile_page(profile: dict[str, Any]) -> str:
    """Render build_profile output. Sections arrive already cited-or-omitted; each element
    line carries memory ids + source. Readiness and limitations are kept — honesty over
    polish: the page says what it does not know."""
    sections = [s for s in (profile.get("sections") or []) if isinstance(s, dict)]
    lines = _frontmatter([
        (GENERATED_MARKER_KEY, True),
        (PAGE_KIND_KEY, "profile"),
        ("title", "Profile"),
        ("readiness", int(profile.get("readiness") or 0)),
        ("section_count", len(sections)),
    ])
    lines.extend(["", "# Profile", ""])
    lines.append(f"*Cited personal profile — readiness {int(profile.get('readiness') or 0)}/100.*")
    lines.append("")
    if not sections:
        lines.extend([
            f"{APP_BRAND} does not yet have enough cited signal to say anything here honestly.",
            "",
        ])
    for section in sections:
        title = str(section.get("title") or section.get("id") or "Section").strip()
        statement = str(section.get("statement") or "").strip()
        confidence = str(section.get("confidence") or "").strip()
        lines.append(f"## {title}")
        lines.append("")
        if statement:
            suffix = f" *(confidence: {confidence})*" if confidence else ""
            lines.append(f"{statement}{suffix}")
            lines.append("")
        for element in section.get("elements") or []:
            if not isinstance(element, dict):
                continue
            text = str(element.get("text") or "").strip()
            if not text:
                continue
            memory_ids = [str(m) for m in (element.get("memory_ids") or []) if m]
            cite = _citation_suffix(
                memory_ids[0] if memory_ids else None,
                str(element.get("source") or "").strip() or None,
                str(element.get("source_url") or "").strip() or None,
            )
            count = int(element.get("count") or 0)
            count_note = f" (×{count})" if count > 1 else ""
            lines.append(f"- {text}{count_note}{cite}")
        lines.append("")
    limitations = [str(item).strip() for item in (profile.get("limitations") or []) if str(item).strip()]
    if limitations:
        lines.extend(["## Limitations", ""])
        for item in limitations:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _person_item_line(item: dict[str, Any]) -> str | None:
    text = str(item.get("content") or item.get("summary") or "").strip()
    if not text:
        return None
    text = " ".join(text.split())
    if len(text) > 300:
        text = text[:297].rstrip() + "..."
    citation = item.get("citation") if isinstance(item.get("citation"), dict) else {}
    cite = _citation_suffix(
        str(citation.get("id") or item.get("id") or "") or None,
        str(citation.get("source") or item.get("source") or "").strip() or None,
        str(citation.get("source_url") or "").strip() or None,
    )
    return f"- {text}{cite}"


def render_person_page(context: dict[str, Any]) -> str:
    """Render person_context output for one person. Cited-or-omitted per section."""
    person = context.get("person") if isinstance(context.get("person"), dict) else {}
    name = str(person.get("name") or "Person").strip() or "Person"
    entity_id = str(person.get("id") or "").strip()
    lines = _frontmatter([
        (GENERATED_MARKER_KEY, True),
        (PAGE_KIND_KEY, "person"),
        ("title", name),
        ("cortex_entity_id", entity_id or None),
        ("aliases", [a for a in (person.get("aliases") or []) if a] or None),
    ])
    lines.extend(["", f"# {name}", ""])
    last = str(context.get("last_interaction") or "").strip()
    if last:
        lines.append(f"*Last interaction: {last}*")
        lines.append("")
    section_specs = [
        ("Open commitments", context.get("open_commitments") or []),
        ("Decisions", context.get("decisions") or []),
        ("Recent context", context.get("recent_context") or []),
    ]
    wrote_any = False
    for title, items in section_specs:
        rendered = [
            line
            for line in (_person_item_line(item) for item in items if isinstance(item, dict))
            if line
        ]
        if not rendered:
            continue
        wrote_any = True
        lines.append(f"## {title}")
        lines.append("")
        lines.extend(rendered)
        lines.append("")
    topics = [str(t.get("topic") or "").strip() for t in (context.get("top_topics") or []) if isinstance(t, dict)]
    topics = [t for t in topics if t]
    if topics:
        lines.extend(["## Topics", "", ", ".join(f"`{t}`" for t in topics[:8]), ""])
    connections = [c for c in (context.get("connections") or []) if isinstance(c, dict) and c.get("label")]
    if connections:
        lines.extend(["## Connected", ""])
        for conn_item in connections[:8]:
            label = str(conn_item.get("label") or "").strip()
            relation = str(conn_item.get("relation") or "related").strip()
            example = " ".join(str(conn_item.get("example") or "").split())
            if len(example) > 160:
                example = example[:157].rstrip() + "..."
            suffix = f" — {example}" if example else ""
            lines.append(f"- **{label}** ({relation}){suffix}")
        lines.append("")
    if not wrote_any and not topics and not connections:
        lines.extend([f"{APP_BRAND} has no cited evidence about this person yet.", ""])
    return "\n".join(lines).rstrip("\n") + "\n"


def parse_generated_marker(text: str) -> dict[str, Any]:
    """Read the frontmatter of a (possibly hand-edited) page and return the Cortex marker
    fields: {generated: bool, page: str, entity_id: str}. Lenient by design — anything
    unparseable simply reads as not-generated, which fails SAFE (we refuse to touch it)."""
    result = {"generated": False, "page": "", "entity_id": ""}
    if not text.startswith(_FENCE):
        return result
    end = text.find(f"\n{_FENCE}", len(_FENCE))
    if end < 0:
        return result
    for line in text[len(_FENCE):end].splitlines():
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        raw = raw.strip()
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            value = raw
        if key == GENERATED_MARKER_KEY:
            result["generated"] = value is True or str(value).strip().lower() == "true"
        elif key == PAGE_KIND_KEY:
            result["page"] = str(value)
        elif key == "cortex_entity_id":
            result["entity_id"] = str(value)
    return result
