#!/usr/bin/env python3
"""Fail when a repository Markdown file points at a missing local path."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "build",
    "dist",
    "dist-test",
    "node_modules",
    "release-artifacts",
}
INLINE_LINK = re.compile(
    r"!?\[[^\]]*]\(\s*(?P<target><[^>]+>|[^)\s]+)(?:\s+['\"][^)]*)?\)"
)
REFERENCE_LINK = re.compile(r"^\s*\[[^\]]+]:\s*(?P<target><[^>]+>|\S+)", re.MULTILINE)


def _markdown_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.md")
        if not any(part in EXCLUDED_PARTS for part in path.relative_to(ROOT).parts)
    )


def _local_target(raw: str) -> str | None:
    target = raw.strip().strip("<>")
    if not target or target.startswith("#"):
        return None
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return None
    return unquote(parsed.path)


def main() -> int:
    errors: list[str] = []
    checked = 0
    for document in _markdown_files():
        text = document.read_text(encoding="utf-8")
        matches = [*INLINE_LINK.finditer(text), *REFERENCE_LINK.finditer(text)]
        for match in matches:
            raw = match.group("target")
            target = _local_target(raw)
            if target is None:
                continue
            checked += 1
            destination = (
                ROOT / target.lstrip("/")
                if target.startswith("/")
                else document.parent / target
            )
            if not destination.exists():
                line = text.count("\n", 0, match.start()) + 1
                errors.append(
                    f"{document.relative_to(ROOT)}:{line}: missing local link target {raw!r}"
                )
    if errors:
        print("\n".join(errors))
        return 1
    print(f"markdown links: {checked} local targets passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
