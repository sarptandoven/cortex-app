"""Condensation layer for Cortex's Personal Profile (strategy P0-C).

Takes ONE profile ``Section`` — a title plus a ranked list of already-selected,
already-cited memory elements produced by ``profile.build_profile_sections`` —
and condenses it into ONE faithful, user-facing, second-person statement that is
ALWAYS tagged with the exact ``memory_ids`` it drew from.

Design goals (in priority order):

1. **Deterministic core.** The default path (``_condense_template``) uses no LLM,
   no randomness, and no network. The same section always yields the same
   statement. This is the only path the tests exercise, so the reproducible,
   tested core NEVER touches the model.
2. **Cited.** Every statement carries the exact ``memory_ids`` behind it (a subset
   of the union of the section's element ids), the contributing sources, and a
   short verbatim example drawn from the user's own content.
3. **LLM-optional, hard-validated.** The model is only consulted when the caller
   explicitly opts in AND an API key is present. Its output is validated against
   the section's real ids (hallucinated ids are dropped, first-person / "you are"
   phrasings are rejected, ``ABSTAIN`` is honoured). ANY failure — parse,
   validation, or exception — falls back to the deterministic template. The LLM
   can only ever change the *wording*; it can never invent a citation.
4. **Honest abstention.** A section with no usable elements yields ``None``.

This module never opens a database and never mutates anything. It reuses the
phrasing helpers from :mod:`mirror` verbatim (importing, never copying, them) so
the second-person framing stays consistent across the Mirror Moment and the
Profile.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

# Reuse mirror's deterministic phrasing helpers verbatim — do not reimplement.
from .mirror import (
    _SOURCE_NAME,
    _first_sentence,
    _shorten,
    _to_second_person,
)


# Per-section lead phrases, used only when the anchor text cannot be mechanically
# rewritten into the second person. Keyed by the Section ``id``. Anything not
# listed falls back to a neutral, still-honest lead.
_SECTION_LEAD: dict[str, str] = {
    "how_you_work": "You tend to",
    "voice_style": "Your voice:",
    "preferences": "You prefer",
    "dislikes": "You avoid",
    "decisions": "You decided",
    "focus": "You keep coming back to",
    "people_projects": "You work with",
}

_DEFAULT_LEAD = "You consistently"


def condense_section(
    section: dict, *, max_chars: int = 220, allow_llm: bool = False
) -> Optional[dict]:
    """Condense one profile ``Section`` into a single cited, second-person statement.

    Returns ``None`` when the section has no usable elements (honest abstention).
    Otherwise returns::

        {
          "section_id": str,
          "statement": str,       # one faithful second-person sentence
          "example": str,         # short verbatim snippet from the anchor element
          "memory_ids": [str],    # sorted, deduped; subset of the section's ids
          "sources": [str],       # sorted, deduped contributing sources
          "confidence": str,      # carried through from the section
          "method": "template" | "llm",
        }

    The deterministic template is the default and the fallback. The LLM is only
    consulted when ``allow_llm`` is true AND ``ANTHROPIC_API_KEY`` is set, and its
    output is hard-validated against the section's real ids before it is trusted.
    """
    if not section or not section.get("elements"):
        return None

    result = _condense_with_claude(section, max_chars) if allow_llm else None
    return result or _condense_template(section, max_chars)


# --- Deterministic core (the only path tests exercise) ----------------------


def _condense_template(section: dict, max_chars: int) -> Optional[dict]:
    """Build the statement deterministically from the ranked elements.

    Elements arrive ranked, so the anchor is ``elements[0]``. The statement is
    phrased in the second person from the anchor's own words when we can rewrite
    it mechanically, else with a section-appropriate lead. It always carries the
    union of the surfaced elements' ids as citations.
    """
    elements = _usable_elements(section)
    if not elements:
        return None

    anchor = elements[0]
    section_id = str(section.get("id") or "")

    body = _second_person_body(anchor, section_id)
    tail = _source_tail(anchor)
    statement = _shorten(f"{body} — {tail}", limit=max_chars)

    memory_ids = _union_memory_ids(elements)
    sources = sorted({str(e.get("source") or "").strip() for e in elements if e.get("source")})
    example = _anchor_example(anchor)

    return {
        "section_id": section_id,
        "statement": statement,
        "example": example,
        "memory_ids": memory_ids,
        "sources": sources,
        "confidence": str(section.get("confidence") or ""),
        "method": "template",
    }


def _second_person_body(anchor: dict, section_id: str) -> str:
    """Phrase the anchor in the second person, grounded in the user's own words.

    Runs mirror's mechanical rewrite over the anchor's first sentence. If that
    stays third-person (mirror returns it unchanged when it cannot confidently
    rewrite), fall back to a section-appropriate lead so we never surface a bare
    third-person claim as if it were about the user.
    """
    first = _first_sentence(str(anchor.get("text") or ""))
    rewritten = _to_second_person(first)
    if _is_second_person(rewritten):
        return rewritten

    lead = _SECTION_LEAD.get(section_id, _DEFAULT_LEAD)
    snippet = _lowered_snippet(first)
    if snippet:
        return f"{lead} {snippet}"
    return lead


def _source_tail(anchor: dict) -> str:
    """The " — from {source} ({n} signals)." tail, mirroring mirror's headline."""
    source = _source_label(str(anchor.get("source") or ""))
    n = _signal_count(anchor)
    unit = "signal" if n == 1 else "signals"
    return f"from {source} ({n} {unit})."


def _anchor_example(anchor: dict) -> str:
    """A short verbatim snippet from the anchor, in the spirit of mirror's
    ``_pick_example`` (shortened to a punchy quote), but drawn from the Section
    element's ``text`` (elements carry no summary/content/importance fields)."""
    return _shorten(str(anchor.get("text") or ""))


# --- LLM path (gated, validated, never raises) ------------------------------


def _condense_with_claude(section: dict, max_chars: int) -> Optional[dict]:
    """LLM rewording of the statement, gated and hard-validated.

    Returns a condensed dict on success, or ``None`` to signal the caller to fall
    back to the deterministic template. NEVER raises: any parse error, validation
    failure, or client exception collapses to ``None`` (-> template).

    The LLM is closed-book and id-anchored: it sees only the section's numbered
    memories and may only cite ids it was given. Every ``used_id`` it returns is
    intersected with the section's real element ids before we trust the output, so
    the model can change the *wording* but never invent a *citation*.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    elements = _usable_elements(section)
    if not elements:
        return None

    try:
        import litellm

        allowed_ids = set(_union_memory_ids(elements))
        by_id = _elements_by_id(elements)

        model = os.environ.get(
            "CORTEX_CONDENSE_MODEL",
            os.environ.get("CORTEX_EXTRACTION_MODEL", "claude-opus-4-5"),
        )
        system = (
            "Condense into ONE second-person sentence using ONLY the numbered "
            "memories; add nothing not entailed by them; never say 'this is you' "
            "or 'you are'; if the memories do not support a single faithful "
            "statement output exactly ABSTAIN; return strict JSON "
            '{"statement": str, "used_ids": [str]} and nothing else.'
        )
        user = _build_user_prompt(section, elements)

        response = litellm.completion(
            model=model,
            max_tokens=300,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Drop provider-unsupported params so one config works across providers.
            drop_params=True,
        )

        text = (response.choices[0].message.content or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)

        statement = str(data.get("statement") or "").strip()
        if not statement or statement.upper() == "ABSTAIN":
            return None
        if _looks_first_person(statement) or _asserts_identity(statement):
            return None

        raw_used = data.get("used_ids") or []
        if not isinstance(raw_used, list):
            return None
        # Drop hallucinated ids: keep only ids the section actually contained.
        used_ids = sorted({str(i) for i in raw_used if str(i) in allowed_ids})
        if not used_ids:
            return None

        statement = _shorten(statement, limit=max_chars)
        sources = sorted(
            {
                str(by_id[i].get("source") or "").strip()
                for i in used_ids
                if by_id.get(i) and by_id[i].get("source")
            }
        )
        example = _anchor_example(by_id.get(used_ids[0]) or elements[0])

        return {
            "section_id": str(section.get("id") or ""),
            "statement": statement,
            "example": example,
            "memory_ids": used_ids,
            "sources": sources,
            "confidence": str(section.get("confidence") or ""),
            "method": "llm",
        }
    except Exception:
        # Any failure whatsoever -> deterministic template.
        return None


def _build_user_prompt(section: dict, elements: list[dict]) -> str:
    """List the section title and each element as ``[<memory_id>] <text>``.

    Each element may carry several memory_ids; we anchor every line on the
    element's first id (the citation the model should return for that memory).
    """
    lines = [f"Section: {section.get('title') or section.get('id') or ''}", "", "Memories:"]
    for element in elements:
        ids = _element_memory_ids(element)
        anchor_id = ids[0] if ids else ""
        text = str(element.get("text") or "").strip()
        lines.append(f"[{anchor_id}] {text}")
    return "\n".join(lines)


# --- Shared helpers ---------------------------------------------------------


def _usable_elements(section: dict) -> list[dict]:
    """Elements that have both text and at least one memory id (order preserved).

    An element with no ids cannot be cited, and one with no text cannot be
    phrased, so neither is usable — but a section is only fully abstained on when
    *no* element is usable.
    """
    out: list[dict] = []
    for element in section.get("elements") or []:
        if not isinstance(element, dict):
            continue
        if not str(element.get("text") or "").strip():
            continue
        # Cited-or-abstain: an element must carry SOME citation. Behavioral elements cite
        # memory_ids; entity/topic-derived elements (people & projects, focus areas) are cited by
        # their source + support count and legitimately have no memory_ids — accept those too.
        if not _element_memory_ids(element) and not str(element.get("source") or "").strip():
            continue
        out.append(element)
    return out


def _element_memory_ids(element: dict) -> list[str]:
    ids = element.get("memory_ids") or []
    if not isinstance(ids, (list, tuple)):
        return []
    return [str(i).strip() for i in ids if str(i).strip()]


def _union_memory_ids(elements: list[dict]) -> list[str]:
    seen: set[str] = set()
    for element in elements:
        seen.update(_element_memory_ids(element))
    return sorted(seen)


def _elements_by_id(elements: list[dict]) -> dict[str, dict]:
    """Map every memory id to the element that surfaced it (first wins)."""
    mapping: dict[str, dict] = {}
    for element in elements:
        for mid in _element_memory_ids(element):
            mapping.setdefault(mid, element)
    return mapping


def _signal_count(anchor: dict) -> int:
    """The supporting count for the anchor, floored at the number of ids we can
    actually cite so the surfaced count never over-claims."""
    ids = _element_memory_ids(anchor)
    try:
        count = int(anchor.get("count"))
    except (TypeError, ValueError):
        count = 0
    return max(count, len(ids), 1)


def _source_label(source: str) -> str:
    """Human-facing source label. Prefer mirror's canonical name map; otherwise a
    readable title-cased fallback so the tail always reads cleanly."""
    source = (source or "").strip()
    if not source:
        return "your data"
    mapped = _SOURCE_NAME.get(source.lower())
    if mapped:
        return mapped
    return source.replace("-", " ").replace("_", " ").strip() or "your data"


def _is_second_person(text: str) -> bool:
    low = (text or "").strip().lower()
    return low.startswith("you ") or low.startswith("your ") or low.startswith("you're")


def _lowered_snippet(text: str) -> str:
    """A tidy fragment to hang off a lead phrase (leading verb lower-cased)."""
    text = (text or "").strip()
    if not text:
        return ""
    # Lower-case only a leading all-alpha word so "Prefers X" -> "prefers X" reads
    # naturally after "You" without mangling proper nouns mid-sentence.
    match = re.match(r"^([A-Za-z]+)(\b.*)$", text)
    if match and match.group(1).isalpha():
        return match.group(1).lower() + match.group(2)
    return text


def _looks_first_person(text: str) -> bool:
    return bool(re.match(r"^\s*(i|i'm|i am|my|we|we're|we are|our)\b", text, re.IGNORECASE))


def _asserts_identity(text: str) -> bool:
    """Reject "this is you" / "you are ..." style identity assertions."""
    low = (text or "").strip().lower()
    return low.startswith("you are") or low.startswith("you're") or "this is you" in low


__all__ = ["condense_section"]
