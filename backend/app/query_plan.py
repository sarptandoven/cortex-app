"""Query understanding & planning (Phase 4 of the outbound retrieval plan).

Turns a raw task/query into a QueryPlan — intent, soft layer hints, entities, and (for compound
questions) sub-queries — so retrieval can be task-aware instead of keyword-only. Self-contained and
stdlib-only: intent classification uses fast keyword rules plus an optional model2vec
nearest-prototype pass (no-op under the hash embedder), and decomposition/entity extraction are
regex heuristics. This module never imports storage (avoids a cycle); storage imports it and threads
the plan through search/answer_query/assemble_context behind the CORTEX_QUERY_PLAN flag.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .embeddings import embed_text, embedding_status

INTENTS = ("answer", "act", "draft", "plan", "recall")

# Keyword fast-path — mirrors storage._CONTEXT_INTENT_RULES so the plan's intent matches today's
# behavior when the semantic pass is unavailable (hash provider), keeping CI deterministic.
_KEYWORD_RULES = (
    ("draft", re.compile(r"\b(write|draft|reply|respond|email|message|compose|post)\b")),
    ("act", re.compile(r"\b(fix|implement|build|run|deploy|schedule|book|create|configure|refactor|ship|migrate|set\s?up)\b")),
    ("plan", re.compile(r"\b(plan|prioriti[sz]e|roadmap|organi[sz]e|next\s+steps|strategy)\b")),
)
_RECALL_RE = re.compile(r"\b(recall|remember|remind|last\s+time|history|previously|used\s+to)\b")
_QUESTION_RE = re.compile(r"^(who|whom|whose|what|when|where|why|how|which|did|do|does|is|are|was|were|can|could|should|would)\b")

# Prototype phrases per intent for the semantic nearest-prototype classifier (catches phrasings the
# keyword rules miss). Only consulted when a real embedder is active; embeddings are memoized.
_INTENT_PROTOTYPES: dict[str, tuple[str, ...]] = {
    "answer": ("what is the answer to this question", "give me a fact from memory", "what did I say about this topic"),
    "act": ("help me do this task now", "implement or configure this", "execute this action for me"),
    "draft": ("write this message for me", "compose a reply in my voice", "draft an email or post"),
    "plan": ("plan the next steps", "prioritize my roadmap", "organize a strategy"),
    "recall": ("what happened recently", "remind me of the history", "recall the timeline of events"),
}
_SEMANTIC_OVERRIDE_FLOOR = 0.50  # only override the keyword intent when the semantic match is confident

# Soft layer routing: which of the 7 memory layers a query leans toward (a hint, not a hard filter).
_LAYER_KEYWORDS: dict[str, re.Pattern[str]] = {
    "style": re.compile(r"\b(tone|voice|style|copy|wording|phrasing|write|draft)\b"),
    "decision": re.compile(r"\b(decide|decision|chose|choose|rationale|tradeoff|why\s+did\s+we)\b"),
    "preference": re.compile(r"\b(prefer|favou?rite|likes?|dislikes?|default)\b"),
    "negative": re.compile(r"\b(avoid|never|do\s*n['o]t|constraint|not\s+allowed|forbidden)\b"),
    "procedural": re.compile(r"\b(how\s+(to|do)|process|workflow|steps|setup|deploy|runbook)\b"),
    "episodic": re.compile(r"\b(when|happened|timeline|met|last\s+time|recently|event)\b"),
}

_CONJUNCTION_SPLIT_RE = re.compile(r"\s+(?:and\s+also|as\s+well\s+as|and|also|plus|then|vs\.?|versus|compared\s+to)\s+", re.IGNORECASE)
_CONJUNCTION_MARK_RE = re.compile(r"\b(and|also|plus|as\s+well\s+as|then|vs\.?|versus|compared\s+to)\b", re.IGNORECASE)

# Capitalized tokens that are sentence-initial noise, not entities.
_CAP_STOPWORDS = {
    "the", "a", "an", "what", "who", "when", "where", "why", "how", "which", "did", "do", "does",
    "is", "are", "was", "were", "can", "could", "should", "would", "tell", "give", "show", "find",
    "help", "please", "i", "my", "me", "we", "our", "list",
}

_PROTOTYPE_CACHE: dict[str, list[tuple[str, list[float], float]]] = {}


def _prototype_vectors(provider: str) -> list[tuple[str, list[float], float]]:
    """Embed the intent prototypes once per provider (memoized). Returns (intent, vector, norm)."""
    cached = _PROTOTYPE_CACHE.get(provider)
    if cached is not None:
        return cached
    vectors: list[tuple[str, list[float], float]] = []
    for intent, phrases in _INTENT_PROTOTYPES.items():
        for phrase in phrases:
            try:
                vector = embed_text(phrase)
            except Exception:
                continue
            norm = sum(value * value for value in vector) ** 0.5
            if norm:
                vectors.append((intent, vector, norm))
    _PROTOTYPE_CACHE[provider] = vectors
    return vectors


def _classify_intent_keyword(lowered: str) -> str:
    for name, pattern in _KEYWORD_RULES:
        if pattern.search(lowered):
            return name
    if _RECALL_RE.search(lowered):
        return "recall"
    return "answer"


def _classify_intent_semantic(query: str, keyword_intent: str) -> str:
    provider = str(embedding_status().get("provider") or "hash")
    if provider == "hash":
        return keyword_intent
    try:
        query_vector = embed_text(query)
    except Exception:
        return keyword_intent
    query_norm = sum(value * value for value in query_vector) ** 0.5
    if not query_norm:
        return keyword_intent
    best_intent, best_sim = keyword_intent, -1.0
    for intent, vector, norm in _prototype_vectors(provider):
        sim = sum(a * b for a, b in zip(query_vector, vector)) / (query_norm * norm)
        if sim > best_sim:
            best_sim, best_intent = sim, intent
    return best_intent if best_sim >= _SEMANTIC_OVERRIDE_FLOOR else keyword_intent


def _extract_entities(query: str) -> tuple[str, ...]:
    entities: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"\b([A-Z][A-Za-z0-9._-]+(?:\s+[A-Z][A-Za-z0-9._-]+)*)\b", query):
        token = match.group(1).strip()
        if len(token) < 2:
            continue
        # Drop a sentence-initial capitalized stopword (e.g. "What", "Tell"); keep genuine names.
        if token.lower() in _CAP_STOPWORDS:
            continue
        key = token.lower()
        if key not in seen:
            seen.add(key)
            entities.append(token)
    return tuple(entities[:5])


def _decompose(query: str) -> tuple[str, ...]:
    if not _CONJUNCTION_MARK_RE.search(query):
        return ()
    parts = [part.strip(" ?.,;") for part in _CONJUNCTION_SPLIT_RE.split(query)]
    parts = [part for part in parts if len(part) >= 4]
    if len(parts) < 2:
        return ()
    return tuple(parts[:3])


@dataclass(frozen=True)
class QueryPlan:
    query: str
    intent: str
    layers: tuple[str, ...]
    entities: tuple[str, ...]
    sub_queries: tuple[str, ...]
    is_question: bool


def build_query_plan(query: str, intent_hint: str | None = None) -> QueryPlan:
    """Build a QueryPlan for a task/query. `intent_hint` (if a valid intent) is authoritative;
    otherwise intent is classified by keyword rules with an optional semantic override."""
    text = (query or "").strip()
    lowered = text.lower()
    hint = str(intent_hint or "").strip().lower()
    if hint in INTENTS:
        intent = hint
    elif not text:
        intent = "recall"
    else:
        intent = _classify_intent_semantic(text, _classify_intent_keyword(lowered))
    layers = tuple(layer for layer, pattern in _LAYER_KEYWORDS.items() if pattern.search(lowered))
    return QueryPlan(
        query=text,
        intent=intent,
        layers=layers,
        entities=_extract_entities(text),
        sub_queries=_decompose(text),
        is_question=bool(_QUESTION_RE.match(lowered)),
    )


def query_plan_enabled() -> bool:
    """Whether query planning is active (CORTEX_QUERY_PLAN truthy). Default off — parity with today."""
    import os

    return os.environ.get("CORTEX_QUERY_PLAN", "").strip().lower() in {"1", "true", "on", "yes"}
