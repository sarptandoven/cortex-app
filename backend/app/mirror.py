"""The "Mirror Moment" (strategy P0-B): surface exactly ONE thing Cortex has
learned about the user, in plain user-facing words, WITH its source and
supporting evidence.

Design goals (in priority order):

1. **Deterministic.** No LLM, no randomness, no network. The same corpus always
   produces the same insight (or the same abstention). All ordering is fully
   specified with stable tie-breaks so results never flap.
2. **Cited.** Every insight names a concrete source (e.g. "Calendar", "Obsidian",
   "GitHub"), a count of supporting memories, the exact ``memory_ids`` behind it,
   and a short verbatim example drawn from the user's own content.
3. **High-confidence.** We only speak when there is real *repetition* — several
   memories from the *same source* that agree — because repetition is what makes
   "it actually knows me" credible rather than a lucky guess.
4. **Honest abstention.** When the corpus is too thin, or nothing clears the
   support floor, we return ``None``. A wrong or generic insight is strictly worse
   than saying nothing, so silence is the safe default.

This module is intentionally self-contained: it never opens its own database and
never mutates anything. It accepts an ``sqlite3.Connection`` and reads only the
existing tables (``memories``, ``memory_topics``). Wiring it into the store / MCP
tools / macOS app happens elsewhere.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional


# --- Tunable, defensible thresholds ----------------------------------------

# We need *some* corpus before we can honestly claim to know the user at all.
# Below this, the Mirror Moment abstains outright.
MIN_TOTAL_MEMORIES = 5

# The number of agreeing memories (from the same source) required before a
# pattern is trustworthy enough to surface. Three independent signals pointing the
# same way is the smallest count that reads as "a pattern" rather than "a fact".
MIN_SUPPORT = 3

# A pattern at this many supporting memories reads as unmistakable, so we mark it
# high-confidence; between the floor and here it is medium-confidence.
HIGH_CONFIDENCE_SUPPORT = 5

# The "this-knows-me" layers, ranked by how strongly they read as self-knowledge.
# Preferences and decisions are the most personal; raw semantic facts the least.
# The weight is used only for cross-candidate comparison (never surfaced), so its
# absolute scale does not matter — only the relative ordering does.
_LAYER_WEIGHT: dict[str, float] = {
    "preference": 5.0,
    "negative": 4.5,  # "you avoid / dislike X" — a strong preference signal
    "decision": 4.0,
    "style": 3.5,
    "procedural": 3.0,
    "episodic": 2.0,
    "semantic": 1.0,
}

# Layers that read as "knowing me". A topic-only signal (which has no personal
# layer) is scored below all of these so a genuine preference/decision/style
# pattern always wins a tie against a bare topic cluster.
_PERSONAL_LAYERS = frozenset({"preference", "negative", "decision", "style", "procedural"})
_TOPIC_LAYER_WEIGHT = 2.5  # between episodic and procedural

# Human-facing display names for known source ids. Anything not listed falls back
# to a title-cased version of the id, so the headline is always readable.
_SOURCE_DISPLAY: dict[str, str] = {
    "calendar": "your calendar",
    "obsidian": "your Obsidian notes",
    "github": "GitHub",
    "email": "your email",
    "gmail": "your email",
    "messages": "your messages",
    "imessage": "your messages",
    "slack": "Slack",
    "linear": "Linear",
    "jira": "Jira",
    "notion": "Notion",
    "apple-notes": "your Apple Notes",
    "readwise": "Readwise",
    "zotero": "Zotero",
    "browser-history": "your browsing history",
    "browser-bookmarks": "your bookmarks",
    "contacts": "your contacts",
    "chatgpt": "your ChatGPT history",
    "claude": "your Claude history",
}

# For evidence.source we want the bare, machine-friendly source id/name (e.g.
# "calendar"), matching the connector catalog, not the "your ..." framing.
_SOURCE_NAME: dict[str, str] = {
    "calendar": "calendar",
    "obsidian": "obsidian",
    "github": "github",
    "email": "email",
    "gmail": "email",
}


def compute_mirror_insight(conn, user_id: str) -> Optional[dict]:
    """Return the single strongest thing Cortex has learned about ``user_id``.

    Reads only ACTIVE memories for the user via the supplied sqlite3 connection.
    Returns a citation-bearing dict, or ``None`` when the corpus is too thin or no
    pattern clears the support floor (honest abstention).

    Shape on success::

        {
          "headline": "You tend to decline meetings before 10am — from your calendar.",
          "evidence": {"source": "calendar", "count": 6,
                       "memory_ids": [...], "example": "<short quote>"},
          "layer": "preference",
          "confidence": "high",
        }
    """
    if not user_id:
        return None

    rows = _load_active_memories(conn, user_id)
    # Too little to say anything honestly.
    if len(rows) < MIN_TOTAL_MEMORIES:
        return None

    topic_rows = _load_topic_links(conn, user_id)

    candidates: list[_Candidate] = []
    candidates.extend(_layer_candidates(rows))
    candidates.extend(_topic_candidates(rows, topic_rows))

    # Keep only patterns with real repetition.
    candidates = [c for c in candidates if c.count >= MIN_SUPPORT]
    if not candidates:
        return None

    # Deterministic ranking: strongest signal first. The sort key is fully
    # specified — every field is a stable, content-derived value — so equal
    # corpora always yield the same winner.
    best = sorted(candidates, key=_candidate_sort_key)[0]
    return best.to_insight()


# --- Candidate model --------------------------------------------------------


class _Candidate:
    """One repeated pattern discovered in the corpus, with everything needed to
    both rank it and turn it into a cited, user-facing insight."""

    __slots__ = (
        "kind",  # "layer" | "topic" — how the pattern was found
        "layer",  # the memory layer this insight is attributed to
        "source",  # canonical source id (e.g. "calendar")
        "count",  # number of supporting active memories
        "memory_ids",  # sorted, deduped supporting memory ids
        "example",  # short verbatim snippet from the user's content
        "topic",  # populated for topic candidates, else ""
        "_weight",  # ranking weight (layer strength); never surfaced
    )

    def __init__(
        self,
        *,
        kind: str,
        layer: str,
        source: str,
        memory_ids: list[str],
        example: str,
        weight: float,
        topic: str = "",
    ) -> None:
        self.kind = kind
        self.layer = layer
        self.source = source
        self.memory_ids = sorted(set(memory_ids))
        self.count = len(self.memory_ids)
        self.example = example
        self.topic = topic
        self._weight = weight

    def to_insight(self) -> dict:
        confidence = "high" if self.count >= HIGH_CONFIDENCE_SUPPORT else "medium"
        return {
            "headline": _build_headline(self),
            "evidence": {
                "source": _evidence_source(self.source),
                "count": self.count,
                "memory_ids": list(self.memory_ids),
                "example": self.example,
            },
            "layer": self.layer,
            "confidence": confidence,
        }


def _candidate_sort_key(c: "_Candidate") -> tuple:
    """Rank candidates. Lower tuple sorts first (strongest insight).

    Priority:
      1. Higher layer weight (preference/decision/style beat topics/facts).
      2. More supporting memories (repetition = credibility).
      3. Deterministic, content-derived tie-breaks so ties never flap:
         layer name, source id, topic, then the first supporting memory id.
    """
    return (
        -c._weight,
        -c.count,
        c.layer,
        c.source,
        c.topic,
        c.memory_ids[0] if c.memory_ids else "",
    )


# --- Candidate discovery ----------------------------------------------------


def _layer_candidates(rows: list[dict]) -> list["_Candidate"]:
    """Repetition of a personal layer *within a single source*.

    e.g. six ``preference`` memories all from ``calendar`` -> "you tend to ...".
    Grouping by (source, layer) keeps the citation honest: every supporting
    memory genuinely came from the named source.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        layer = row["layer"]
        if layer not in _PERSONAL_LAYERS:
            continue
        source = row["source"]
        if not source:
            continue
        groups.setdefault((source, layer), []).append(row)

    out: list[_Candidate] = []
    for (source, layer), members in groups.items():
        example = _pick_example(members)
        out.append(
            _Candidate(
                kind="layer",
                layer=layer,
                source=source,
                memory_ids=[m["id"] for m in members],
                example=example,
                weight=_LAYER_WEIGHT.get(layer, 1.0),
            )
        )
    return out


def _topic_candidates(rows: list[dict], topic_rows: list[dict]) -> list["_Candidate"]:
    """Repetition of a topic *within a single source*.

    A strongly-supported topic cluster is the fallback signal when no personal
    layer clears the floor — it still reads as "it knows what I'm about".
    """
    by_id = {row["id"]: row for row in rows}

    groups: dict[tuple[str, str], list[dict]] = {}
    for link in topic_rows:
        row = by_id.get(link["memory_id"])
        if row is None:  # topic points at a non-active/other-user memory
            continue
        topic = _clean_topic(link["topic"])
        if not topic:
            continue
        source = row["source"]
        if not source:
            continue
        groups.setdefault((source, topic), []).append(row)

    out: list[_Candidate] = []
    for (source, topic), members in groups.items():
        # Attribute to the most personal layer present in the cluster so the
        # surfaced `layer` field is as meaningful as possible.
        layer = _dominant_layer(members)
        example = _pick_example(members)
        out.append(
            _Candidate(
                kind="topic",
                layer=layer,
                source=source,
                memory_ids=[m["id"] for m in members],
                example=example,
                weight=_TOPIC_LAYER_WEIGHT,
                topic=topic,
            )
        )
    return out


# --- Headline construction --------------------------------------------------


def _build_headline(c: "_Candidate") -> str:
    """Phrase the pattern in the user's own frame ("You ...", "Your ...").

    We derive the sentence from the user's real content (summary/content of the
    strongest example) rather than templating a generic claim, so the headline
    reads as something Cortex actually observed.
    """
    source_label = _SOURCE_DISPLAY.get(c.source, _titleize(c.source))
    example = _first_sentence(c.example) if c.example else ""

    if c.kind == "topic":
        topic = c.topic
        lead = f"You keep coming back to {topic}"
        if example:
            return f"{lead} — from {source_label}, e.g. “{example}”."
        return f"{lead} — from {source_label} ({c.count} times)."

    # Layer candidates: lead with a layer-appropriate framing, grounded in the
    # user's own words when we have them.
    if example:
        body = _to_second_person(example)
        return f"{body} — from {source_label} ({c.count} times)."

    lead = _LAYER_LEAD.get(c.layer, "You have a consistent pattern")
    return f"{lead} — from {source_label} ({c.count} times)."


_LAYER_LEAD: dict[str, str] = {
    "preference": "You have a consistent preference",
    "negative": "There is something you consistently avoid",
    "decision": "You keep making the same kind of decision",
    "style": "You have a recognizable style",
    "procedural": "You follow a consistent way of working",
}


# --- Data loading -----------------------------------------------------------


def _load_active_memories(conn, user_id: str) -> list[dict]:
    cur = conn.execute(
        """
        SELECT id, layer, kind, content, summary, source, importance
        FROM memories
        WHERE user_id = ? AND status = 'active'
        ORDER BY id
        """,
        (user_id,),
    )
    return [
        {
            "id": str(r["id"]),
            "layer": _norm(r["layer"]) or "semantic",
            "kind": _norm(r["kind"]),
            "content": _text(r["content"]),
            "summary": _text(r["summary"]),
            "source": _norm(r["source"]),
            "importance": _as_int(r["importance"], default=3),
        }
        for r in cur.fetchall()
    ]


def _load_topic_links(conn, user_id: str) -> list[dict]:
    cur = conn.execute(
        """
        SELECT memory_id, topic
        FROM memory_topics
        WHERE user_id = ?
        ORDER BY memory_id, topic
        """,
        (user_id,),
    )
    return [{"memory_id": str(r["memory_id"]), "topic": _text(r["topic"])} for r in cur.fetchall()]


# --- Small deterministic helpers -------------------------------------------


def _pick_example(members: Iterable[dict]) -> str:
    """Choose one representative snippet, deterministically.

    Prefer the highest-importance member; tie-break on shortest-then-id so the
    quote is punchy and stable. Uses summary when present (it is already a tight
    paraphrase), else content.
    """
    best = None
    best_key: tuple = ()
    for m in members:
        text = m["summary"] or m["content"]
        text = _shorten(text)
        if not text:
            continue
        key = (-m["importance"], len(text), m["id"])
        if best is None or key < best_key:
            best = text
            best_key = key
    return best or ""


def _dominant_layer(members: list[dict]) -> str:
    """Pick the most personal layer present in a cluster (for attribution)."""
    best_layer = "semantic"
    best_weight = -1.0
    for m in members:
        layer = m["layer"]
        weight = _LAYER_WEIGHT.get(layer, 1.0)
        # Deterministic tie-break by layer name for equal weights.
        if weight > best_weight or (weight == best_weight and layer < best_layer):
            best_weight = weight
            best_layer = layer
    return best_layer


def _evidence_source(source: str) -> str:
    return _SOURCE_NAME.get(source, source)


def _clean_topic(topic: str) -> str:
    topic = _norm(topic).replace("_", " ").strip()
    return topic


def _to_second_person(text: str) -> str:
    """Best-effort re-frame of a third-person snippet into "You ...".

    Purely mechanical and deterministic. If the snippet already reads in the
    second person, or we cannot confidently rewrite it, we return it unchanged
    (still grounded in the user's own words) rather than fabricating.
    """
    stripped = text.strip()
    if not stripped:
        return stripped
    low = stripped.lower()
    if low.startswith("you ") or low.startswith("your "):
        return stripped
    # "prefers X" / "prefer X" phrasings -> "You prefer X"
    m = re.match(r"^(prefers|prefer|tends to|tend to|avoids|avoid|likes|like|dislikes|dislike)\b(.*)$", stripped, re.IGNORECASE)
    if m:
        verb = _second_person_verb(m.group(1).lower())
        return f"You {verb}{m.group(2)}"
    return stripped


def _second_person_verb(verb: str) -> str:
    mapping = {
        "prefers": "prefer",
        "prefer": "prefer",
        "tends to": "tend to",
        "tend to": "tend to",
        "avoids": "avoid",
        "avoid": "avoid",
        "likes": "like",
        "like": "like",
        "dislikes": "dislike",
        "dislike": "dislike",
    }
    return mapping.get(verb, verb)


def _first_sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    match = re.search(r"[.!?]", text)
    if match:
        text = text[: match.start()].strip()
    return _shorten(text)


def _shorten(text: str, limit: int = 140) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rstrip()
    return f"{cut}…"


def _titleize(source: str) -> str:
    if not source:
        return "your data"
    return source.replace("-", " ").replace("_", " ").title()


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
