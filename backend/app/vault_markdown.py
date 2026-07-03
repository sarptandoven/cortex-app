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
    for key in sorted(record.keys()):
        if key in emitted or key == "content":
            continue
        value = record[key]
        if value is None:
            continue
        lines.append(f"{key}: {_dump_value(value)}")
    lines.append(_FENCE)
    lines.append("")
    lines.append(str(record.get("content") or "").rstrip("\n"))
    lines.append("")
    return "\n".join(lines)


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
    record["content"] = "\n".join(lines[body_start:]).strip("\n")
    return record


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
