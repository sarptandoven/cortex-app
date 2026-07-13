"""CLAUDE.md compiler — "Sync to CLAUDE.md".

The ICP (developers using Claude/Cursor/other AI coding tools) already hand-maintains a
CLAUDE.md / AGENTS.md / .cursorrules file as a manual memory workaround: a plain-text
dump of "who I am, how I work, what I'm building" that they paste at the top of every
new agent session. This module meets that workaround exactly where it lives instead of
asking the user to adopt something new: it renders the user's cited profile (from
:mod:`backend.app.profile` / ``store.build_profile``) into a small, human-readable
Markdown block, and writes ONLY that block into a fenced, Cortex-managed region of a
file the user already has (or is about to create) — never touching anything else in
the file.

Two responsibilities, kept strictly separate:

1. :func:`render_context_block` — pure, deterministic, stdlib-only. Turns a
   ``build_profile``-shaped dict into a compact fenced Markdown block. Cited-or-abstain:
   a section with nothing well-supported is simply omitted (never fabricated).
2. :func:`sync_context_file` — the managed-block WRITER. Reads the target file (if any),
   replaces only the content between the ``CORTEX:BEGIN`` / ``CORTEX:END`` HTML-comment
   markers, and atomically writes the result. Byte-preserving outside the markers — this
   reuses the exact discipline :mod:`backend.app.vault_markdown` already proved out for
   the Obsidian generated-links block (structural marker matching, not "first occurrence
   of the string", so a marker that appears inside the user's own prose is never treated
   as the managed region).

Both stdlib-only; no DB access, no network, no LLM. ``render_context_block`` accepts an
already-materialized profile dict (or a store + user_id, in which case it calls
``store.build_profile`` itself) so tests can feed a fixed profile and assert byte-exact,
deterministic output.
"""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import APP_BRAND


# --- Style catalog ------------------------------------------------------------------

# Every style renders the identical block today (the same cited facts read the same way
# regardless of which tool consumes them); the parameter exists so a future style can
# diverge (e.g. a leaner "cursorrules" style) without changing the call signature that
# routes/tests/Swift already depend on.
SUPPORTED_STYLES: tuple[str, ...] = ("claude", "agents", "cursorrules", "gemini")

DEFAULT_STYLE = "claude"

# Known context filenames a caller may target without a ".md" extension.
KNOWN_CONTEXT_FILENAMES: frozenset[str] = frozenset(
    {"CLAUDE.md", "AGENTS.md", ".cursorrules", "GEMINI.md"}
)

# Line budget for the rendered block (soft cap; abstention keeps real output well under
# this in practice, but a corpus with unusually many well-supported sections is still
# hard-capped here so the file a coding agent loads at session start stays cheap to read).
MAX_BLOCK_LINES = 150

# Managed-block markers. Structurally identical in spirit to vault_markdown's
# _GENERATED_MARKER discipline: the OPENER is only recognized when paired with the
# expected closer further down the file, so a marker-lookalike string that appears
# inside the user's own prose is never mistaken for the managed region.
BEGIN_MARKER = "<!-- CORTEX:BEGIN managed context -->"
END_MARKER = "<!-- CORTEX:END -->"


# --- Section catalog for the rendered block -----------------------------------------

# id -> heading used in the rendered block. Order is fixed so output is deterministic
# and stable across runs (new sections never reshuffle old ones). "facts" is rendered as
# "Who I am" (the profile's cited claims about the user's world, in their own words) —
# there is no separate identity pull, so nothing is ever duplicated across two headings.
_SECTION_HEADINGS: dict[str, str] = {
    "facts": "Who I am",
    "how_you_work": "How I work",
    "voice_style": "Voice & style",
    "preferences": "Preferences",
    "dislikes": "What to avoid",
    "decisions": "Key decisions",
    "recent_timeline": "Recent activity",
    "focus": "Focus areas",
    "people_projects": "People & projects",
    "open_loops": "Open loops",
}

# Per-section element cap inside the rendered block (independent of profile.py's own
# per-section cap; kept here so the renderer's budget is self-contained and legible).
_MAX_ELEMENTS_PER_SECTION = 5
_MAX_OPEN_LOOP_ELEMENTS = 8


def _utc_date_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _clean_line(text: str) -> str:
    """Collapse whitespace/newlines so one cited fact never breaks the block into
    multiple Markdown lines (which would desync from its citation footnote)."""
    text = str(text or "").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return " ".join(text.split())


def _citation_tag(memory_ids: list[Any]) -> str:
    """Render memory ids as a trailing HTML-comment citation, e.g. ``<!-- mem:a, mem:b -->``.

    Comments keep the block fully human-readable prose while still citing sources —
    the content itself never carries a bracket/footnote marker that would clutter the
    file a person reads directly.
    """
    ids = [_text(mid) for mid in (memory_ids or []) if _text(mid)]
    if not ids:
        return ""
    tags = ", ".join(f"mem:{mid}" for mid in ids)
    return f" <!-- {tags} -->"


def _render_element_line(element: dict[str, Any]) -> Optional[str]:
    if not isinstance(element, dict):
        return None
    text = _clean_line(element.get("text"))
    if not text:
        return None
    memory_ids = element.get("memory_ids")
    citation = _citation_tag(memory_ids if isinstance(memory_ids, list) else [])
    return f"- {text}{citation}"


def _render_section(section: dict[str, Any], *, max_elements: int) -> list[str]:
    """One cited section as a list of Markdown lines (heading + bullets). Returns an
    empty list when the section has nothing well-supported to say (cite-or-abstain)."""
    if not isinstance(section, dict):
        return []
    section_id = _text(section.get("id"))
    heading = _SECTION_HEADINGS.get(section_id)
    if not heading:
        return []
    elements = section.get("elements")
    if not isinstance(elements, list):
        return []
    lines: list[str] = []
    for element in elements[:max_elements]:
        rendered = _render_element_line(element)
        if rendered:
            lines.append(rendered)
    if not lines:
        return []  # nothing cited survived -> abstain on the whole section
    return [f"## {heading}", "", *lines, ""]


def render_context_block(
    store: Any = None,
    user_id: str = "",
    *,
    style: str = DEFAULT_STYLE,
    profile: Optional[dict[str, Any]] = None,
) -> str:
    """Render the Cortex-managed context block: who the user is, how they work, active
    projects + recent decisions + open loops — each cited to memory ids, compact,
    deterministic given the same store state.

    Two calling conventions, both supported so tests can feed a fixed profile directly:
      render_context_block(store, user_id, style=...)   # calls store.build_profile
      render_context_block(profile=already_built_dict, style=...)

    Cited-or-abstain throughout: a section with nothing well-supported is omitted
    entirely. Deterministic: relies only on ``build_profile``'s own deterministic
    ordering (no randomness, no wall-clock content besides the footer date, which is
    UTC-derived and thus reproducible for a fixed instant).
    """
    style = _text(style).lower() or DEFAULT_STYLE
    if style not in SUPPORTED_STYLES:
        style = DEFAULT_STYLE

    if profile is None:
        if store is None or not _text(user_id):
            raise ValueError("render_context_block requires either profile=... or (store, user_id)")
        profile = store.build_profile(user_id)
    if not isinstance(profile, dict):
        profile = {}

    lines: list[str] = []

    sections = [s for s in (profile.get("sections") or []) if isinstance(s, dict)]
    by_id = {_text(s.get("id")): s for s in sections}

    # Fixed, deterministic order: who-you-are first, then work style, then project state
    # (decisions/timeline/focus/people), then the commitment queue last.
    order = [
        "facts",
        "how_you_work",
        "voice_style",
        "preferences",
        "dislikes",
        "decisions",
        "recent_timeline",
        "focus",
        "people_projects",
    ]
    for section_id in order:
        section = by_id.get(section_id)
        if section is None:
            continue
        lines.extend(_render_section(section, max_elements=_MAX_ELEMENTS_PER_SECTION))

    open_loops = by_id.get("open_loops")
    if open_loops is not None:
        lines.extend(_render_section(open_loops, max_elements=_MAX_OPEN_LOOP_ELEMENTS))

    # Trim a trailing blank line before the footer so spacing is stable regardless of
    # which section rendered last.
    while lines and lines[-1] == "":
        lines.pop()

    if not lines:
        # Nothing well-supported anywhere -> abstain on the whole body, but the block
        # (and its markers) is still written so the file gets a clear, honest state
        # instead of silently doing nothing.
        lines = [
            f"_{APP_BRAND} has no well-supported memory yet for this profile. Nothing was cited, so"
            " nothing is written here. Sync again once you have more memory._"
        ]

    # Hard line budget: keep the earliest (highest-priority) sections, drop the tail.
    if len(lines) > MAX_BLOCK_LINES - 4:  # reserve room for the footer block below
        lines = lines[: MAX_BLOCK_LINES - 4]
        while lines and lines[-1] == "":
            lines.pop()

    footer_date = _utc_date_iso()
    footer = [
        "",
        f"_Compiled by {APP_BRAND} on {footer_date} — edits inside this block are overwritten;"
        " keep your own notes outside the markers._",
    ]
    lines.extend(footer)

    return "\n".join(lines).rstrip("\n") + "\n"


# --- Managed-block writer ------------------------------------------------------------


def _marker_matches(raw: str, marker: str) -> bool:
    """True iff this line IS a managed marker. We strip only the trailing newline/CR and
    trailing whitespace — NEVER leading whitespace — so an INDENTED marker-lookalike that
    the user wrote inside their own prose (a fenced code block, an indented list, a
    blockquote documenting Cortex's markers) is NOT mistaken for the real region. Cortex
    always writes its markers at column 0, so a real marker matches while "    <!-- ... -->"
    (indented) does not, protecting the user's illustrative content from being overwritten."""
    return raw.rstrip("\r\n").rstrip() == marker


def _find_managed_region(original_lines: list[str]) -> Optional[tuple[int, int]]:
    """Locate the (begin_index, end_index) of the FIRST structurally-valid managed
    region: a column-0 opener line equal to BEGIN_MARKER, followed (anywhere later) by a
    column-0 line equal to END_MARKER. Mirrors vault_markdown's structural-match
    discipline: a bare opener with no matching closer is NOT treated as a region (nothing
    is truncated to end-of-file on a half-deleted marker). Lines may or may not retain
    their trailing newline — `_marker_matches` tolerates either."""
    begin_idx = None
    for idx, raw in enumerate(original_lines):
        if _marker_matches(raw, BEGIN_MARKER):
            begin_idx = idx
            break
    if begin_idx is None:
        return None
    for idx in range(begin_idx + 1, len(original_lines)):
        if _marker_matches(original_lines[idx], END_MARKER):
            return (begin_idx, idx)
    return None  # opener with no closer -> not a valid region, leave the file untouched


def _split_keep_ends(text: str) -> list[str]:
    """Split into lines each RETAINING its own trailing "\\n" or "\\r\\n" (the final line
    has no ending if the text didn't end in a newline). Only "\\n"/"\\r\\n" are treated as
    breaks — never the exotic separators str.splitlines honors (a lone \\r, U+2028, ...) —
    and ``"".join(_split_keep_ends(t)) == t`` for any t. Keeping each line's own ending is
    what lets the writer preserve untouched regions BYTE-FOR-BYTE even when the file mixes
    LF and CRLF endings: only the regenerated managed block is re-encoded."""
    lines: list[str] = []
    start = 0
    for idx, ch in enumerate(text):
        if ch == "\n":
            lines.append(text[start : idx + 1])
            start = idx + 1
    if start < len(text):
        lines.append(text[start:])
    return lines


def _dominant_newline(text: str) -> str:
    """The file's dominant line ending, used only for the newly-generated managed block
    (untouched regions keep their own endings). CRLF wins only if it is the majority."""
    crlf = text.count("\r\n")
    lf_only = text.count("\n") - crlf
    return "\r\n" if crlf > lf_only else "\n"


def render_managed_block(block_body: str) -> str:
    """Wrap a rendered block body in the BEGIN/END markers (no surrounding blank-line
    assumptions beyond what's needed to look intentional in a Markdown file)."""
    body = block_body.rstrip("\n")
    return f"{BEGIN_MARKER}\n{body}\n{END_MARKER}"


def sync_context_file(
    store: Any,
    user_id: str,
    path: str,
    *,
    style: str = DEFAULT_STYLE,
) -> dict[str, Any]:
    """Render the context block and write it into ``path``'s Cortex-managed region.

    Behavior:
      - Target file exists, markers present: replace ONLY the text between the markers.
        Everything before the opener and after the closer is preserved byte-for-byte,
        including any CRLF line endings and trailing-newline state of those regions.
      - Target file exists, markers absent: append the managed block at the end (after
        exactly one blank-line separator if the file has trailing content).
      - Target file missing: create it (parent directory must already exist — routes
        validate this before calling in) containing only the managed block.
      - Atomic: written via temp-file + os.replace, so a crash mid-write can never leave
        a half-written file at ``path``.

    Returns ``{"path": str, "bytes_written": int, "block_lines": int, "created": bool}``.
    """
    style = _text(style).lower() or DEFAULT_STYLE
    target = Path(path).expanduser()

    block_body = render_context_block(store, user_id, style=style)
    managed_block = render_managed_block(block_body)
    block_lines = managed_block.count("\n") + 1

    created = not target.exists()
    if created:
        new_text = managed_block + "\n"
    else:
        # newline="" disables Python's universal-newline translation on read: without it,
        # Path.read_text()/open() SILENTLY converts every "\r\n" to "\n" before this code
        # ever sees it, so we could never preserve a file's real byte-level endings.
        with target.open("r", encoding="utf-8", newline="") as handle:
            original_text = handle.read()

        # Byte-preservation: split into lines that each KEEP their own ending, so every
        # untouched line survives verbatim regardless of whether the file is LF, CRLF, or
        # a mix. Only the regenerated managed block is (re-)encoded, using the file's
        # dominant ending. This fixes the prior "one stray CRLF rewrites the whole file"
        # break, where any single CRLF flipped every LF line ending in the file.
        newline = _dominant_newline(original_text)
        block_out = managed_block.replace("\n", newline) if newline != "\n" else managed_block

        lines = _split_keep_ends(original_text)
        region = _find_managed_region(lines)
        if region is not None:
            begin_idx, end_idx = region
            before = "".join(lines[:begin_idx])          # byte-exact prefix
            after = "".join(lines[end_idx + 1 :])         # byte-exact suffix
            end_had_newline = lines[end_idx].endswith("\n")
            # Terminate the block with a newline when content follows it, or when the old
            # closing-marker line was itself newline-terminated (preserve EOF-newline state).
            trailer = newline if (after or end_had_newline) else ""
            new_text = before + block_out + trailer + after
        else:
            # No valid managed region -> append at end. Preserve existing content
            # byte-for-byte except its trailing newlines, then add exactly one blank-line
            # separator (in the file's dominant ending) before the block.
            if original_text.strip("\r\n").strip() == "":
                new_text = block_out + newline
            else:
                stripped = original_text.rstrip("\r\n")
                new_text = stripped + newline + newline + block_out + newline

    _atomic_write(target, new_text)
    data = new_text.encode("utf-8")
    return {
        "path": str(target),
        "bytes_written": len(data),
        "block_lines": block_lines,
        "created": created,
    }


def _atomic_write(path: Path, text: str) -> None:
    """Crash-safe write: unique temp file -> fsync -> os.replace. Mirrors
    vault_markdown.atomic_write_text's discipline (pid+thread+random temp name so
    concurrent writers to the same file never collide); duplicated in full here (rather
    than imported) so this module has no dependency on the vault subsystem — a context
    file lives OUTSIDE the vault by design."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.{os.urandom(4).hex()}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8", newline="") as handle:
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
        # Directory fsync is best-effort (unsupported on some platforms).
        pass


# --- Path validation ------------------------------------------------------------------

_SYSTEM_DIR_PREFIXES: tuple[str, ...] = (
    "/etc",
    "/usr",
    "/system",
    "/bin",
    "/sbin",
    "/private/etc",
    "/var/db",
    "/library/apple",
)


def validate_context_file_path(raw_path: str, *, vault_root: Optional[str] = None) -> Path:
    """Validate a caller-supplied context-file path. Raises ``ValueError`` with a
    user-facing message on any violation; returns the resolved, expanded ``Path`` on
    success. Callers (routes) still need to check the parent directory exists (a
    filesystem check, not a pure validation rule) before calling ``sync_context_file``.

    Rules:
      - Must be non-empty.
      - ``~`` is expanded.
      - Must be an ABSOLUTE path once expanded (no relative paths — a relative path is
        ambiguous about "relative to what" across a GUI app + backend process pair).
      - Filename must end in ``.md`` OR be one of the known dotfile context filenames
        (``.cursorrules`` has no extension).
      - Must NOT resolve inside the Cortex vault itself (the vault is Cortex's own
        managed storage; a context file is a file the USER already owns elsewhere).
      - Must NOT resolve inside a system directory (``/etc``, ``/usr``, ``/System``, ...).
    """
    text = _text(raw_path)
    if not text:
        raise ValueError("path is required")

    expanded = Path(text).expanduser()
    if not expanded.is_absolute():
        raise ValueError("path must be absolute")

    def _ext_ok(name: str) -> bool:
        return name in KNOWN_CONTEXT_FILENAMES or name.lower().endswith(".md")

    if not _ext_ok(expanded.name):
        raise ValueError(
            "path must end in .md or be a known context filename "
            "(CLAUDE.md, AGENTS.md, .cursorrules, GEMINI.md)"
        )

    # SECURITY: refuse to write THROUGH a symlink at the final component. A symlink named
    # CLAUDE.md could point at ~/.zshrc, ~/.ssh/authorized_keys, or ~/.aws/credentials —
    # following it would let the extension allowlist be bypassed and turn a "sync my
    # context file" action into an arbitrary write (and, for a shell rc, code execution).
    # We never manage a symlinked context file; the user points us at a real file.
    if expanded.is_symlink():
        raise ValueError("path must not be a symlink")

    # Resolve WITHOUT requiring the path to exist (a new CLAUDE.md is a common case),
    # but still collapse '..'/symlinks in the existing ancestry so the vault/system-dir
    # checks below cannot be bypassed with a path like "<vault>/../CLAUDE.md" or a
    # symlink hop.
    resolved = _resolve_best_effort(expanded)

    # SECURITY (defence in depth): the allowlist above was checked on the pre-resolve
    # name; re-check the RESOLVED name so a symlink/'..' hop can never redirect the write
    # to a file whose real name is outside the allowlist (e.g. an ancestor-symlink case).
    if not _ext_ok(resolved.name):
        raise ValueError(
            "resolved path must end in .md or be a known context filename"
        )

    if vault_root:
        vault_resolved = _resolve_best_effort(Path(vault_root).expanduser())
        if resolved == vault_resolved or _is_relative_to(resolved, vault_resolved):
            raise ValueError(f"path must not be inside the {APP_BRAND} vault")

    resolved_lower = str(resolved).lower()
    for prefix in _SYSTEM_DIR_PREFIXES:
        if resolved_lower == prefix or resolved_lower.startswith(prefix + "/"):
            raise ValueError("path must not be inside a system directory")

    parent = resolved.parent
    if not parent.is_dir():
        raise ValueError(f"parent directory does not exist: {parent}")

    return resolved


def _resolve_best_effort(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return path


def _is_relative_to(path: Path, other: Path) -> bool:
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False
