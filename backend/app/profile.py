"""Personal Profile — the deterministic SORT/ORGANIZE + cited-assembly core.

This module regroups the output of ``MemoryStore.personal_profile(...)`` into a
small, fixed set of *cited* Sections that a downstream layer (``condense.py`` /
the store) can turn into user-facing statements. It follows the same discipline
as :mod:`backend.app.mirror`:

1. **Pure.** No DB access, no LLM, no network, no randomness. It accepts the
   already-materialized profile dict and returns plain data structures.
2. **Deterministic.** The same input always produces byte-identical output. All
   ordering is fully specified with stable, content-derived tie-breaks so
   results never flap.
3. **Cited.** Every surfaced :class:`Element` names a concrete ``source`` (or a
   ``source_url``), a support ``count``, and the exact ``memory_ids`` behind it,
   plus a short verbatim ``text`` drawn from the user's own content.
4. **Cited-or-abstain.** A section that has nothing well-supported and cited to
   say is *omitted* entirely. A wrong or uncited claim is strictly worse than
   silence, so silence is the safe default.

Because :mod:`backend.app.mirror` is self-contained, we reuse its deterministic
text/formatting helpers by importing them directly (one-way import; we never
mutate mirror).

Data contract (matched exactly — ``condense.py`` and the store depend on it)::

    Section = {
        "id": str,
        "title": str,
        "confidence": "high" | "medium" | "low",
        "elements": [Element],
    }
    Element = {
        "text": str,
        "source": str,
        "count": int,
        "memory_ids": [str],
        "source_url": str | None,
    }

We deliberately do NOT add ``"statement"``/``"method"`` to sections — those are
attached later by ``condense_section`` in the store.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

from .mirror import (
    _as_int,
    _evidence_source,
    _norm,
    _pick_example,
    _shorten,
    _text,
)


# --- Section catalog --------------------------------------------------------
#
# Fixed order. Each behavioral section draws its candidate memory items from one
# or more layers of the profile's per-layer ``sections`` list. ``focus`` and
# ``people_projects`` are derived from the profile's ``topics`` / ``entities``.

# id -> (title, [source layers])
_BEHAVIORAL_SECTIONS: list[tuple[str, str, tuple[str, ...]]] = [
    ("how_you_work", "How you work", ("style", "procedural")),
    ("preferences", "Your preferences", ("preference",)),
    ("dislikes", "What you avoid", ("negative",)),
    ("decisions", "Key decisions", ("decision",)),
]

_FOCUS_SECTION_ID = "focus"
_FOCUS_SECTION_TITLE = "Focus areas"
_PEOPLE_SECTION_ID = "people_projects"
_PEOPLE_SECTION_TITLE = "People & projects"

# Fixed catalog order used to sort the returned sections.
_SECTION_ORDER: list[str] = [sid for sid, _, _ in _BEHAVIORAL_SECTIONS] + [
    _FOCUS_SECTION_ID,
    _PEOPLE_SECTION_ID,
]


# --- Tunable, defensible thresholds ----------------------------------------

# Near-duplicate collapse: two items merge when their representative cosine
# similarity clears this floor (only when a real embedder is available).
_SIM_THRESHOLD = 0.86

# Token-signature fallback (no embedder / "hash" provider). A gentler Jaccard
# floor than the cosine one: token overlap is a coarse, keyword-free stand-in for
# semantics, so we merge on strong lexical overlap rather than near-equality.
_SIG_JACCARD_THRESHOLD = 0.6

# A behavioral element only surfaces if it repeats OR is individually important.
_MIN_ELEMENT_SUPPORT = 2
_IMPORTANT_FLOOR = 4

# An entity (people_projects) only surfaces if it is linked from at least this
# many memories.
_MIN_ENTITY_LINKS = 2

# A focus topic only surfaces if it repeats at least this many times.
_MIN_TOPIC_COUNT = 2

# Confidence bands (support = cluster size, distinct = #distinct sources).
_HIGH_SUPPORT = 5
_HIGH_DISTINCT_SOURCES = 2
_MEDIUM_SUPPORT = 3

# Keep at most this many elements per section (ranked).
_MAX_ELEMENTS = 3

# Source-quality signal used inline in the ranking score. Structured/first-party
# sources read as slightly more trustworthy than free-form dumps. Absolute scale
# does not matter, only relative ordering.
_SOURCE_QUALITY: dict[str, float] = {
    "calendar": 1.0,
    "github": 1.0,
    "linear": 1.0,
    "jira": 1.0,
    "obsidian": 0.8,
    "notion": 0.8,
    "apple-notes": 0.8,
    "email": 0.6,
    "gmail": 0.6,
    "messages": 0.5,
    "imessage": 0.5,
    "slack": 0.5,
}

_CONFIDENCE_STR = "confidence"

# Recency scoring window (folded-magnitude bounds, see _parse_ts). A parsed
# timestamp is mapped linearly across this fixed calendar span into [0, 1], so
# newer memories score higher. Fixed constants keep the signal pure/deterministic
# while remaining relative (newer > older) across the modern range.
def _folded(year: int) -> float:
    return ((((year * 12 + 1) * 31 + 1) * 24) * 60) * 60.0


_RECENCY_MIN_TS = _folded(2015)
_RECENCY_MAX_TS = _folded(2035)


# --- Public API -------------------------------------------------------------


def build_profile_sections(
    profile: dict,
    *,
    embed_fn: Optional[Callable[[str], list[float]]] = None,
    provider: str = "hash",
) -> list[dict]:
    """Regroup a personal profile into a small fixed set of cited Sections.

    Args:
        profile: the dict returned by ``MemoryStore.personal_profile(...)``.
        embed_fn: optional ``callable(text) -> list[float]`` used for semantic
            near-duplicate collapse. ``None`` falls back to token-signature dedup.
        provider: the value of ``embedding_status()["provider"]``. When
            ``"hash"`` we ignore ``embed_fn`` for merging (hash embeddings are
            keyword plumbing, not semantics) and use token-signature dedup.

    Returns:
        A list of Section dicts in the fixed catalog order (possibly empty).
        Pure and deterministic: identical input yields identical output.
    """
    if not isinstance(profile, dict):
        return []

    # Only use semantic merging when we have a *real* embedder. A "hash" provider
    # is keyword plumbing, so we fall back to deterministic token signatures.
    use_embeddings = embed_fn is not None and _norm(provider) != "hash"
    merger = _ClusterMerger(embed_fn if use_embeddings else None)

    layer_items = _items_by_layer(profile)

    sections: list[dict] = []
    for section_id, title, layers in _BEHAVIORAL_SECTIONS:
        candidates: list[dict] = []
        for layer in layers:
            candidates.extend(layer_items.get(layer, []))
        section = _build_behavioral_section(section_id, title, candidates, merger)
        if section is not None:
            sections.append(section)

    focus = _build_focus_section(profile)
    if focus is not None:
        sections.append(focus)

    people = _build_people_section(profile)
    if people is not None:
        sections.append(people)

    order = {sid: i for i, sid in enumerate(_SECTION_ORDER)}
    sections.sort(key=lambda s: order.get(s["id"], len(order)))
    return sections


# --- Behavioral sections ----------------------------------------------------


def _build_behavioral_section(
    section_id: str,
    title: str,
    candidates: list[dict],
    merger: "_ClusterMerger",
) -> Optional[dict]:
    """Cluster near-duplicate memories, then keep the top cited elements.

    Returns ``None`` when nothing survives cited-or-abstain, so the caller can
    omit the section entirely.
    """
    # Deterministic ordering before greedy clustering: strongest signal first so
    # the representative of each cluster is the best available member.
    ordered = sorted(candidates, key=_candidate_sort_key)

    clusters = merger.cluster(ordered)

    elements: list[dict] = []
    for cluster in clusters:
        element = _cluster_to_element(cluster)
        if element is None:
            continue  # uncited -> abstain
        support = element["count"]
        importance = _max_importance(cluster)
        # Behavioral abstention: surface only if it repeats OR is important.
        if support < _MIN_ELEMENT_SUPPORT and importance < _IMPORTANT_FLOOR:
            continue
        distinct_sources = _distinct_sources(cluster)
        elements.append(
            {
                "element": element,
                "support": support,
                "distinct_sources": distinct_sources,
                "importance": importance,
                "sort_key": _element_sort_key(element, cluster),
            }
        )

    if not elements:
        return None

    elements.sort(key=lambda e: e["sort_key"])
    kept = elements[:_MAX_ELEMENTS]

    confidence = _section_confidence(kept)
    return {
        "id": section_id,
        "title": title,
        _CONFIDENCE_STR: confidence,
        "elements": [e["element"] for e in kept],
    }


def _cluster_to_element(cluster: list[dict]) -> Optional[dict]:
    """Fold a cluster of near-duplicate items into a single cited Element.

    The cluster's *representative* is its first (best-ranked) member. Returns
    ``None`` when the representative carries no citation at all (cited-or-abstain).
    """
    if not cluster:
        return None
    rep = cluster[0]
    source = _text(rep.get("source"))
    source_url = _clean_source_url(rep.get("source_url"))
    if not source and not source_url:
        return None  # nothing to cite -> abstain

    text = _pick_example([_example_view(item) for item in cluster])
    if not text:
        # Fall back to a shortened raw snippet so we never emit an empty element
        # for an otherwise cited, supported cluster.
        text = _shorten(_item_text(rep))
    if not text:
        return None

    memory_ids = sorted({str(item.get("id")) for item in cluster if item.get("id") is not None})
    if not memory_ids:
        return None

    return {
        "text": text,
        "source": source,
        "count": len(cluster),
        "memory_ids": memory_ids,
        "source_url": source_url,
    }


# --- Focus section (from profile topics) ------------------------------------


def _build_focus_section(profile: dict) -> Optional[dict]:
    """Surface the user's recurring focus areas from profile topics.

    Topics carry no per-item citation of their own, so a topic can only surface
    when it clears the repetition floor (``count``). Focus is descriptive rather
    than behavioral, so its confidence is capped at ``medium``.
    """
    topics = profile.get("topics")
    if not isinstance(topics, list):
        return None

    ranked: list[dict] = []
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        name = _text(topic.get("topic"))
        count = _as_int(topic.get("count"), default=0)
        if not name or count < _MIN_TOPIC_COUNT:
            continue
        ranked.append(
            {
                "text": name,
                "source": "topics",
                "count": count,
                "memory_ids": [],
                "source_url": None,
                "_last_seen": _text(topic.get("last_seen")),
            }
        )

    if not ranked:
        return None

    # Deterministic: most-supported topic first, then most-recent, then name.
    ranked.sort(key=lambda t: (-t["count"], _neg_ts(t["_last_seen"]), t["text"]))
    kept = ranked[:_MAX_ELEMENTS]

    top_count = kept[0]["count"]
    confidence = "medium" if top_count >= _MEDIUM_SUPPORT else "low"

    elements = [
        {
            "text": t["text"],
            "source": t["source"],
            "count": t["count"],
            "memory_ids": t["memory_ids"],
            "source_url": t["source_url"],
        }
        for t in kept
    ]
    return {
        "id": _FOCUS_SECTION_ID,
        "title": _FOCUS_SECTION_TITLE,
        _CONFIDENCE_STR: confidence,
        "elements": elements,
    }


# --- People & projects section (from profile entities) ----------------------


def _build_people_section(profile: dict) -> Optional[dict]:
    """Surface the people and projects the user works with most.

    An entity only surfaces when it is linked from at least ``_MIN_ENTITY_LINKS``
    memories (a single mention is not a relationship). Confidence is capped at
    ``medium`` because an entity link is weaker evidence than a repeated,
    same-source behavioral pattern.
    """
    entities = profile.get("entities")
    if not isinstance(entities, list):
        return None

    ranked: list[dict] = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        name = _text(entity.get("name"))
        links = _as_int(entity.get("memory_count"), default=0)
        if not name or links < _MIN_ENTITY_LINKS:
            continue
        ranked.append(
            {
                "name": name,
                "kind": _text(entity.get("kind")),
                "links": links,
                "last_seen": _text(entity.get("last_seen")),
                "id": _text(entity.get("id")),
            }
        )

    if not ranked:
        return None

    # Deterministic: most-linked first, then most-recent, then name, then id.
    ranked.sort(key=lambda e: (-e["links"], _neg_ts(e["last_seen"]), e["name"], e["id"]))
    kept = ranked[:_MAX_ELEMENTS]

    elements = [
        {
            "text": _entity_text(e["name"], e["kind"]),
            "source": "entities",
            "count": e["links"],
            "memory_ids": [],
            "source_url": None,
        }
        for e in kept
    ]
    # Entity links are capped at medium confidence by design.
    confidence = "medium" if kept[0]["links"] >= _MEDIUM_SUPPORT else "low"
    return {
        "id": _PEOPLE_SECTION_ID,
        "title": _PEOPLE_SECTION_TITLE,
        _CONFIDENCE_STR: confidence,
        "elements": elements,
    }


def _entity_text(name: str, kind: str) -> str:
    if kind:
        return f"{name} ({kind})"
    return name


# --- Greedy near-duplicate clustering ---------------------------------------


class _ClusterMerger:
    """Greedy O(m^2) near-duplicate clustering. ``m`` is small (per-section).

    With a real embedder, two items merge when their representative cosine
    similarity clears ``_SIM_THRESHOLD`` (rounded to 6dp for determinism).
    Without one, we merge on a normalized token-signature match, which is a
    deterministic, keyword-free equivalence and the correct fallback for the
    "hash" provider.
    """

    def __init__(self, embed_fn: Optional[Callable[[str], list[float]]]) -> None:
        self._embed_fn = embed_fn

    def cluster(self, ordered_items: list[dict]) -> list[list[dict]]:
        clusters: list[list[dict]] = []
        # Cache the representative vector/signature per cluster so the greedy
        # pass stays O(m^2) rather than recomputing embeddings each comparison.
        rep_vecs: list[Optional[list[float]]] = []
        rep_sigs: list[frozenset[str]] = []

        for item in ordered_items:
            placed = False
            if self._embed_fn is not None:
                vec = self._embed(item)
                for idx, rep_vec in enumerate(rep_vecs):
                    if rep_vec is None or vec is None:
                        continue
                    sim = round(_cosine(rep_vec, vec), 6)
                    if sim >= _SIM_THRESHOLD:
                        clusters[idx].append(item)
                        placed = True
                        break
                if not placed:
                    clusters.append([item])
                    rep_vecs.append(vec)
            else:
                sig = _token_signature(item)
                for idx, rep_sig in enumerate(rep_sigs):
                    if _signature_match(rep_sig, sig):
                        clusters[idx].append(item)
                        placed = True
                        break
                if not placed:
                    clusters.append([item])
                    rep_sigs.append(sig)
        return clusters

    def _embed(self, item: dict) -> Optional[list[float]]:
        text = _item_text(item)
        if not text:
            return None
        try:
            vec = self._embed_fn(text)  # type: ignore[misc]
        except Exception:
            return None
        if not vec:
            return None
        try:
            return [float(x) for x in vec]
        except (TypeError, ValueError):
            return None


def _cosine(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(n):
        av = a[i]
        bv = b[i]
        dot += av * bv
        na += av * av
        nb += bv * bv
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
        "has", "have", "i", "in", "is", "it", "its", "of", "on", "or", "that",
        "the", "their", "them", "they", "this", "to", "was", "were", "with",
        "you", "your", "we", "our", "us",
    }
)


def _token_signature(item: dict) -> frozenset[str]:
    text = _item_text(item)
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return frozenset(t for t in tokens if t not in _STOPWORDS and len(t) > 1)


def _signature_match(a: frozenset[str], b: frozenset[str]) -> bool:
    """Deterministic near-dup test on normalized token signatures.

    Empty signatures never match (nothing to compare on). Otherwise we treat two
    items as near-duplicates when their Jaccard overlap clears a high floor — the
    keyword-free stand-in for cosine similarity used when no embedder is present.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    inter = len(a & b)
    if inter == 0:
        return False
    union = len(a | b)
    return (inter / union) >= _SIG_JACCARD_THRESHOLD


# --- Scoring & ranking ------------------------------------------------------


def _candidate_sort_key(item: dict) -> tuple:
    """Order candidates before greedy clustering. Lower tuple sorts first.

    Fully specified per the design spec:
        (-score, -support-so-far, -importance, captured_at desc, id)
    'support-so-far' is not known pre-clustering, so we use ``0`` for every
    single item (the term is preserved for spec fidelity and future use); the
    remaining, content-derived fields make the order stable.
    """
    score = _item_score(item)
    importance = _as_int(item.get("importance"), default=3)
    captured_at = _text(item.get("captured_at"))
    item_id = _text(item.get("id"))
    return (
        -round(score, 6),
        0,  # support-so-far (unknown pre-clustering; single item == 0)
        -importance,
        _neg_ts(captured_at),
        item_id,
    )


def _item_score(item: dict) -> float:
    """Inline, bounded relevance score for a single memory item.

    Combines recency, importance, self-reported confidence, and source quality.
    No query term is involved (the profile is query-agnostic here). Absolute
    scale does not matter — only relative ordering — so the weights are chosen to
    keep every component comparable and bounded to roughly ``[0, 1]``.
    """
    recency = _recency_signal(item)
    importance = _clamp01(_as_int(item.get("importance"), default=3) / 5.0)
    confidence = _confidence_signal(item)
    quality = _SOURCE_QUALITY.get(_norm(item.get("source")), 0.4)
    return recency + importance + confidence + quality


def _recency_signal(item: dict) -> float:
    """Newer timestamps score higher, on a pure, deterministic scale.

    We map a parsed timestamp linearly across a fixed calendar window
    (``_RECENCY_MIN_TS`` .. ``_RECENCY_MAX_TS``, i.e. 2015..2035 in the folded
    magnitude used by :func:`_parse_ts`) into ``[0, 1]``. Because the window and
    mapping are fixed constants (no wall clock), identical input yields identical
    scores; because they are relative, a genuinely newer memory always scores
    above an older one. Unknown timestamps get a neutral ``0.5``.
    """
    ts = _parse_ts(_text(item.get("captured_at")))
    if ts is None:
        return 0.5
    if ts <= _RECENCY_MIN_TS:
        return 0.0
    if ts >= _RECENCY_MAX_TS:
        return 1.0
    return _clamp01((ts - _RECENCY_MIN_TS) / (_RECENCY_MAX_TS - _RECENCY_MIN_TS))


def _confidence_signal(item: dict) -> float:
    raw = item.get("confidence")
    if raw is None:
        return 0.5
    # Numeric confidences (0..1 or 0..100) and string bands are both possible.
    if isinstance(raw, (int, float)):
        val = float(raw)
        if val > 1.0:
            val = val / 100.0
        return _clamp01(val)
    label = _norm(raw)
    return {"high": 1.0, "medium": 0.6, "low": 0.3}.get(label, 0.5)


def _element_sort_key(element: dict, cluster: list[dict]) -> tuple:
    """Rank surviving elements within a section. Lower tuple sorts first.

    Prefer bigger support, then more distinct sources, then higher importance,
    then a fully-cited element, then the representative's score, then id — every
    field content-derived so ties never flap.
    """
    support = element["count"]
    distinct = _distinct_sources(cluster)
    importance = _max_importance(cluster)
    has_url = 1 if element.get("source_url") else 0
    rep = cluster[0] if cluster else {}
    score = _item_score(rep) if rep else 0.0
    first_id = element["memory_ids"][0] if element["memory_ids"] else ""
    return (
        -support,
        -distinct,
        -importance,
        -has_url,
        -round(score, 6),
        first_id,
    )


def _section_confidence(kept_elements: list[dict]) -> str:
    """Section/element confidence from the design spec.

    - "high" iff a top element has support >= 5 AND distinct_sources >= 2
    - "medium" iff a top element has support >= 3
    - else "low"
    """
    if not kept_elements:
        return "low"
    high = any(
        e["support"] >= _HIGH_SUPPORT and e["distinct_sources"] >= _HIGH_DISTINCT_SOURCES
        for e in kept_elements
    )
    if high:
        return "high"
    medium = any(e["support"] >= _MEDIUM_SUPPORT for e in kept_elements)
    if medium:
        return "medium"
    return "low"


# --- Small deterministic helpers -------------------------------------------


def _items_by_layer(profile: dict) -> dict[str, list[dict]]:
    """Index the profile's per-layer ``sections`` -> {layer: [items]}."""
    out: dict[str, list[dict]] = {}
    sections = profile.get("sections")
    if not isinstance(sections, list):
        return out
    for section in sections:
        if not isinstance(section, dict):
            continue
        layer = _text(section.get("layer"))
        items = section.get("items")
        if not layer or not isinstance(items, list):
            continue
        out.setdefault(layer, [])
        for item in items:
            if isinstance(item, dict):
                out[layer].append(item)
    return out


def _example_view(item: dict) -> dict:
    """Adapt a profile item into the shape ``mirror._pick_example`` expects.

    ``_pick_example`` reads ``summary``, ``content``, ``importance`` and ``id``.
    Profile items always carry ``summary``/``content``/``id`` but may drop
    ``importance`` (the store's projection does), so we default it.
    """
    return {
        "summary": _text(item.get("summary")),
        "content": _text(item.get("content")),
        "importance": _as_int(item.get("importance"), default=3),
        "id": _text(item.get("id")),
    }


def _item_text(item: dict) -> str:
    return _text(item.get("summary")) or _text(item.get("content"))


def _clean_source_url(value: Any) -> Optional[str]:
    text = _text(value)
    return text or None


def _distinct_sources(cluster: list[dict]) -> int:
    return len({_norm(item.get("source")) for item in cluster if _norm(item.get("source"))})


def _max_importance(cluster: list[dict]) -> int:
    best = 0
    for item in cluster:
        best = max(best, _as_int(item.get("importance"), default=3))
    return best


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


_TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def _parse_ts(value: str) -> Optional[float]:
    """Parse an ISO-8601-ish timestamp into a comparable ordinal-seconds float.

    Deterministic and dependency-free: we only need a monotonic ordering, not a
    true epoch, so we fold Y/M/D/H/M/S into a single sortable magnitude.
    """
    if not value:
        return None
    m = _TS_RE.match(value)
    if m:
        y, mo, d, h, mi, s = (int(g) for g in m.groups())
        return ((((y * 12 + mo) * 31 + d) * 24 + h) * 60 + mi) * 60 + s
    m = _DATE_RE.match(value)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        return ((((y * 12 + mo) * 31 + d) * 24) * 60) * 60.0
    return None


def _neg_ts(value: str) -> float:
    """Sort key component for 'most recent first'. Unknown timestamps sort last."""
    ts = _parse_ts(value)
    if ts is None:
        return float("inf")  # unknown -> oldest (sorts after known-recent)
    return -ts


# Re-exported for callers/tests that want the canonical source label.
evidence_source = _evidence_source
