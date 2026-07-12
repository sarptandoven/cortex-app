"""IMPORT-DIFF compare engine — "what the AIs think of you", side by side.

A user drops in the SHORT memory export a vendor (ChatGPT / Claude / Gemini)
lets them download — the bulleted "saved memories" list, NOT a full chat export
(that lives in source_ingest's transcript parsers). This module compares each
atomic vendor fact against Cortex's OWN cited memory and classifies it:

    confirmed    Cortex holds a matching, cited memory      -> include the citation
    conflicting  Cortex holds a contradicting memory         -> include both, cited
    stale        Cortex holds a NEWER version (superseded)    -> include both, cited
    missing      Cortex doesn't know it                       -> a candidate to import

Plus `cortex_only`: high-signal things Cortex's Mirror knows that the vendor's
export never mentions — the "what your export missed" column.

Design invariants (match the storage.py cite-or-abstain convention):
  * DETERMINISTIC: fixed thresholds, sorted iteration, no wall-clock in the
    classification. No network, no LLM, stdlib-only.
  * CITE-OR-ABSTAIN: a `confirmed`/`conflicting`/`stale` verdict MUST carry a
    real Cortex memory id (and its source citation). We never assert a match
    without a memory to point at; when in doubt we fall to `missing`.
  * READ-ONLY: nothing here mutates the store. This is a preview — importing the
    `missing` facts is the caller's job via the existing capture path.
  * GRACEFUL DEGRADATION: under the hash embedder (keyword plumbing, not real
    semantics) we fall back to deterministic keyword overlap so the diff still
    works offline — it just labels itself lower-confidence.
"""

from __future__ import annotations

import math
import re
from typing import Any

from .embeddings import (
    embed_text,
    embedding_source_text,
    embedding_status,
)
from .source_ingest import (
    normalize_vendor_facts,
    parse_vendor_memory_export,
    vendor_memory_label,
)


# How many Cortex memories we retrieve as match candidates for one vendor fact.
# The fact's own text is the query; we only need the closest handful.
_CANDIDATES_PER_FACT = 6

# Cosine-similarity gates (real embedding provider). A confirmed match must clear
# CONFIRM; between RELATED and CONFIRM the topic is shared but not close enough to
# assert a match, so it stays `missing` unless a contradiction fires.
_CONFIRM_COSINE = 0.72
_RELATED_COSINE = 0.45

# Keyword-overlap gates (hash embedder / offline fallback). Jaccard over content
# word sets. Higher bar than cosine because word overlap is a coarser signal.
_CONFIRM_KEYWORD = 0.55
_RELATED_KEYWORD = 0.28

# A short list of high-signal Mirror layers whose memories are worth surfacing as
# `cortex_only` (durable model-of-user signal), in priority order.
_CORTEX_ONLY_LAYERS = ("preference", "decision", "negative", "style", "semantic")
_MAX_CORTEX_ONLY = 12

# Negation / polarity markers used to catch a contradiction between a vendor fact
# and an otherwise-similar Cortex memory ("prefers tabs" vs "prefers spaces").
_NEGATIONS = {"not", "no", "never", "don't", "dont", "cannot", "can't", "cant", "without", "avoid", "stopped", "quit"}

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'\-]*")
# Function words carry no discriminating signal for keyword overlap.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "being", "as", "at", "by", "it",
    "this", "that", "these", "those", "i", "you", "your", "yours", "me", "my",
    "he", "she", "they", "them", "his", "her", "their", "we", "us", "our",
    "has", "have", "had", "do", "does", "did", "will", "would", "can", "could",
    "should", "about", "from", "into", "than", "then", "so", "if", "when",
    "user", "users", "person", "prefers", "prefer", "likes", "uses", "using",
    "wants", "user's", "who", "which", "what",
}


def _tokens(text: str) -> set[str]:
    return {
        word
        for word in _WORD_RE.findall(str(text or "").lower())
        if word not in _STOPWORDS and len(word) > 1
    }


def _cosine(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


def _keyword_overlap(fact_tokens: set[str], memory_tokens: set[str]) -> float:
    """Jaccard overlap of the discriminating word sets (deterministic, offline)."""
    if not fact_tokens or not memory_tokens:
        return 0.0
    intersection = fact_tokens & memory_tokens
    if not intersection:
        return 0.0
    union = fact_tokens | memory_tokens
    return len(intersection) / len(union)


def _memory_text(memory: dict[str, Any]) -> str:
    return embedding_source_text(memory.get("summary"), memory.get("content"))


def _memory_citation(memory: dict[str, Any]) -> dict[str, Any]:
    """The cited, presentation-ready projection of one Cortex memory.

    Always carries the memory `id` (the cite-or-abstain anchor) and a source
    label; `source_url` is the precise locator when the memory has one.
    """
    content = str(memory.get("summary") or memory.get("content") or "").strip()
    if len(content) > 400:
        content = content[:397].rstrip() + "..."
    return {
        "memory_id": str(memory.get("id") or ""),
        "content": content,
        "source": str(memory.get("source") or "") or "cortex",
        "source_url": memory.get("source_url") or None,
        "layer": memory.get("layer") or "",
        "kind": memory.get("kind") or "",
        "captured_at": memory.get("captured_at") or "",
        "occurred_at": memory.get("occurred_at") or "",
    }


def _is_stale(memory: dict[str, Any]) -> bool:
    """A memory Cortex has already superseded/closed is a NEWER-version signal."""
    if str(memory.get("status") or "active") not in {"active", ""}:
        return True
    if memory.get("superseded_by") or memory.get("superseded_at"):
        return True
    if memory.get("valid_to"):
        return True
    return False


def _polarity_conflict(fact_tokens: set[str], memory: dict[str, Any], fact_text: str) -> bool:
    """True when fact and memory share the same subject but DISAGREE on polarity.

    Cheap deterministic heuristic: the two texts overlap on discriminating
    subject words, but exactly one of them is negated. That flags e.g.
    "You are vegetarian" (vendor) vs "You are not vegetarian" (Cortex), or
    "prefers dark mode" vs "does not use dark mode". Not a general NLI — it only
    fires when there is real subject overlap, so it can't manufacture a conflict
    out of unrelated memories.
    """
    memory_text = _memory_text(memory)
    memory_words = set(_WORD_RE.findall(memory_text.lower()))
    fact_words = set(_WORD_RE.findall(str(fact_text or "").lower()))
    fact_negated = bool(fact_words & _NEGATIONS)
    memory_negated = bool(memory_words & _NEGATIONS)
    if fact_negated == memory_negated:
        return False
    # Require genuine subject overlap so the polarity flip is about the SAME thing.
    # A short fact may only carry one discriminating subject word ("vegetarian"),
    # so require min(2, |fact subject words|) — never zero, so unrelated memories
    # (no shared subject) can't be manufactured into a conflict.
    subject_overlap = fact_tokens & _tokens(memory_text)
    required = min(2, len(fact_tokens)) or 1
    return len(subject_overlap) >= required


class _Matcher:
    """Similarity backend chosen once per compare so classification is uniform.

    Uses real embeddings when the provider is not the hash fallback; otherwise
    keyword overlap. Memory vectors/tokens are cached across facts.
    """

    def __init__(self) -> None:
        status = embedding_status()
        self.provider = str(status.get("provider") or "hash")
        self.semantic = self.provider != "hash"
        self.method = "embedding" if self.semantic else "keyword"
        self._vec_cache: dict[str, list[float]] = {}
        self._tok_cache: dict[str, set[str]] = {}

    def _fact_vector(self, text: str) -> list[float]:
        try:
            return embed_text(text)
        except Exception:
            return []

    def _memory_vector(self, memory: dict[str, Any]) -> list[float]:
        key = str(memory.get("id") or "")
        text = _memory_text(memory)
        if not text:
            return []
        if key and key in self._vec_cache:
            return self._vec_cache[key]
        try:
            vector = embed_text(text)
        except Exception:
            vector = []
        if key:
            self._vec_cache[key] = vector
        return vector

    def _memory_tokens(self, memory: dict[str, Any]) -> set[str]:
        key = str(memory.get("id") or "")
        if key and key in self._tok_cache:
            return self._tok_cache[key]
        tokens = _tokens(_memory_text(memory))
        if key:
            self._tok_cache[key] = tokens
        return tokens

    def similarity(self, fact_text: str, fact_tokens: set[str], fact_vector: list[float], memory: dict[str, Any]) -> float:
        if self.semantic:
            return _cosine(fact_vector, self._memory_vector(memory))
        return _keyword_overlap(fact_tokens, self._memory_tokens(memory))

    @property
    def confirm_threshold(self) -> float:
        return _CONFIRM_COSINE if self.semantic else _CONFIRM_KEYWORD

    @property
    def related_threshold(self) -> float:
        return _RELATED_COSINE if self.semantic else _RELATED_KEYWORD


def _rank_candidates(
    matcher: _Matcher,
    fact_text: str,
    fact_tokens: set[str],
    fact_vector: list[float],
    candidates: list[dict[str, Any]],
) -> list[tuple[float, dict[str, Any]]]:
    """Score + sort candidate memories for one fact (best first, deterministic).

    Ties break on memory id so the ordering is stable regardless of retrieval
    order — required for a deterministic diff.
    """
    scored: list[tuple[float, str, dict[str, Any]]] = []
    for memory in candidates:
        memory_id = str(memory.get("id") or "")
        if not memory_id:
            continue  # a candidate without an id can never be a cite-or-abstain anchor
        score = matcher.similarity(fact_text, fact_tokens, fact_vector, memory)
        scored.append((score, memory_id, memory))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [(score, memory) for score, _, memory in scored]


def _classify_fact(
    matcher: _Matcher,
    fact: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Classify one vendor fact against its retrieved Cortex candidates."""
    fact_text = str(fact.get("text") or "").strip()
    vendor = str(fact.get("vendor") or "")
    base: dict[str, Any] = {
        "text": fact_text,
        "vendor": vendor,
        "vendor_label": vendor_memory_label(vendor) if vendor else "",
    }
    if fact.get("captured_at"):
        base["captured_at"] = fact["captured_at"]

    fact_tokens = _tokens(fact_text)
    fact_vector = matcher._fact_vector(fact_text) if matcher.semantic else []
    ranked = _rank_candidates(matcher, fact_text, fact_tokens, fact_vector, candidates)

    if not ranked:
        return {**base, "status": "missing", "score": 0.0, "match": None}

    top_score, top_memory = ranked[0]

    # Contradiction check runs on the top candidates even below the related gate —
    # a polarity flip ("vegetarian" vs "not vegetarian") is a real conflict at low
    # numeric overlap, and _polarity_conflict's own subject-overlap requirement is
    # the guard against manufacturing one. Bound to the few best so it stays cheap.
    for score, memory in ranked[:3]:
        if score <= 0.0:
            break
        if _polarity_conflict(fact_tokens, memory, fact_text):
            status = "stale" if _is_stale(memory) else "conflicting"
            return {
                **base,
                "status": status,
                "score": round(float(score), 4),
                "match": _memory_citation(memory),
                "note": (
                    "Cortex holds a newer version that contradicts this."
                    if status == "stale"
                    else "Cortex holds a memory that contradicts this."
                ),
            }

    if top_score >= matcher.confirm_threshold:
        # A close match that Cortex has already superseded is stale, not confirmed.
        if _is_stale(top_memory):
            return {
                **base,
                "status": "stale",
                "score": round(float(top_score), 4),
                "match": _memory_citation(top_memory),
                "note": "Cortex holds a newer version of this.",
            }
        return {
            **base,
            "status": "confirmed",
            "score": round(float(top_score), 4),
            "match": _memory_citation(top_memory),
        }

    # Related-but-not-confirmed: surface the nearest memory as context, but the
    # verdict is still `missing` (cite-or-abstain: we did not assert a match).
    match = _memory_citation(top_memory) if top_score >= matcher.related_threshold else None
    return {
        **base,
        "status": "missing",
        "score": round(float(top_score), 4),
        "match": match,
    }


def _cortex_only(
    matcher: _Matcher,
    store: Any,
    user_id: str,
    vendor_facts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """High-signal Mirror memories the vendor's export never mentions.

    Pulls the durable model-of-user layers from personal_profile (each item is
    already cited: id + source + source_url), then drops any that a vendor fact
    matched (>= related), so what remains is genuinely vendor-missed. Best-effort:
    a profile failure yields [] rather than breaking the diff.
    """
    try:
        profile = store.personal_profile(user_id, limit=8, include_pending=False)
    except Exception:
        return []
    sections = {str(section.get("layer") or ""): section for section in (profile.get("sections") or [])}

    fact_token_sets = [_tokens(str(fact.get("text") or "")) for fact in vendor_facts]
    fact_vectors = (
        [matcher._fact_vector(str(fact.get("text") or "")) for fact in vendor_facts]
        if matcher.semantic
        else [[] for _ in vendor_facts]
    )

    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for layer in _CORTEX_ONLY_LAYERS:
        section = sections.get(layer) or {}
        for item in section.get("items") or []:
            memory_id = str(item.get("id") or "")
            if not memory_id or memory_id in seen_ids:
                continue
            # Skip memories any vendor fact already covers — those aren't "only in Cortex".
            covered = False
            for fact_tokens, fact_vector in zip(fact_token_sets, fact_vectors):
                if matcher.similarity("", fact_tokens, fact_vector, item) >= matcher.related_threshold:
                    covered = True
                    break
            if covered:
                continue
            seen_ids.add(memory_id)
            out.append(_memory_citation(item))
            if len(out) >= _MAX_CORTEX_ONLY:
                return out
    return out


def compare_vendor_memory(
    store: Any,
    user_id: str,
    vendor_facts: list[dict[str, Any]],
    *,
    vendor: str = "",
) -> dict[str, Any]:
    """Compare a vendor's memory export against Cortex's cited memory.

    `vendor_facts` is a list of {text, vendor, captured_at?} (from
    source_ingest.parse_vendor_memory_export / normalize_vendor_facts). Returns a
    structured, presentation-ready diff:

        {
          "method": "embedding" | "keyword",
          "provider": <embedding provider>,
          "vendor": <resolved vendor id>,
          "vendor_label": <display label>,
          "summary": {"total", "confirmed", "conflicting", "stale", "missing", "cortex_only"},
          "facts": [ {text, vendor, status, score, match?, note?}, ... ],
          "cortex_only": [ <cited memory>, ... ],
          "caveats": [...],
        }

    Read-only: nothing is written. `confirmed`/`conflicting`/`stale` always carry
    a real Cortex memory id in `match` (cite-or-abstain).
    """
    matcher = _Matcher()
    facts = [fact for fact in (vendor_facts or []) if isinstance(fact, dict) and str(fact.get("text") or "").strip()]

    # Resolve the export's vendor: explicit arg, else the first fact that names one.
    resolved_vendor = str(vendor or "").strip()
    if not resolved_vendor:
        for fact in facts:
            if fact.get("vendor"):
                resolved_vendor = str(fact["vendor"])
                break

    classified: list[dict[str, Any]] = []
    for fact in facts:
        query = str(fact.get("text") or "").strip()
        try:
            candidates = store.search(user_id, query, limit=_CANDIDATES_PER_FACT)
        except Exception:
            candidates = []
        candidates = [memory for memory in candidates if isinstance(memory, dict)]
        classified.append(_classify_fact(matcher, fact, candidates))

    cortex_only = _cortex_only(matcher, store, user_id, facts)

    counts = {"confirmed": 0, "conflicting": 0, "stale": 0, "missing": 0}
    for entry in classified:
        status = entry.get("status")
        if status in counts:
            counts[status] += 1

    caveats = [
        "This is a read-only preview: nothing was imported. Import the 'missing' facts through the normal capture path.",
        "A confirmed / conflicting / stale verdict always cites a real Cortex memory id; unmatched facts stay 'missing' rather than guess a match.",
    ]
    if not matcher.semantic:
        caveats.append(
            "Matching used deterministic keyword overlap because a semantic embedding provider is not configured; "
            "close paraphrases may be reported as 'missing'."
        )

    return {
        "method": matcher.method,
        "provider": matcher.provider,
        "vendor": resolved_vendor,
        "vendor_label": vendor_memory_label(resolved_vendor) if resolved_vendor else "AI assistant",
        "summary": {
            "total": len(classified),
            "confirmed": counts["confirmed"],
            "conflicting": counts["conflicting"],
            "stale": counts["stale"],
            "missing": counts["missing"],
            "cortex_only": len(cortex_only),
        },
        "facts": classified,
        "cortex_only": cortex_only,
        "caveats": caveats,
    }


def compare_vendor_export(
    store: Any,
    user_id: str,
    *,
    raw_export: Any = None,
    vendor_facts: Any = None,
    vendor: str = "",
) -> dict[str, Any]:
    """Endpoint-facing entry point: parse (if needed) then compare.

    Accepts EITHER `raw_export` (the pasted/uploaded vendor memory text or JSON)
    OR a pre-parsed `vendor_facts` list, plus an optional `vendor` hint. Returns
    the compare diff, with a `parsed` block describing what was extracted so the
    UI can show "we read N facts from your ChatGPT export".
    """
    if vendor_facts is not None:
        facts = normalize_vendor_facts(vendor_facts, vendor_hint=vendor)
        resolved_vendor = vendor or (facts[0].get("vendor") if facts else "")
        parsed = {
            "vendor": resolved_vendor or "",
            "vendor_label": vendor_memory_label(resolved_vendor) if resolved_vendor else "AI assistant",
            "count": len(facts),
            "source": "pre_parsed",
        }
    else:
        parsed = parse_vendor_memory_export(raw_export, vendor_hint=vendor)
        facts = parsed.get("facts") or []
        parsed = {
            "vendor": parsed.get("vendor") or "",
            "vendor_label": parsed.get("vendor_label") or "AI assistant",
            "count": parsed.get("count") or 0,
            "source": "parsed_export",
        }

    diff = compare_vendor_memory(store, user_id, facts, vendor=vendor or parsed.get("vendor") or "")
    diff["parsed"] = parsed
    return diff
