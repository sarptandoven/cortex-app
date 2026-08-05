"""
cortex/redis_store.py
---------------------
Embed context items and store in Redis Stack for semantic + metadata search.

Uses Voyage AI (voyage-3, 1024-dim) for embeddings.

Index fields:
  content        — TEXT (full-text search)
  source         — TEXT
  kind           — TAG  (claim, decision, event, action, question, person, summary)
  status         — TAG  (active, resolved, archived)
  confidence     — TAG  (confirmed, reported, inferred)
  topics         — TAG  (comma-separated)
  entity_ids     — TAG  (comma-separated stable IDs)
  importance     — NUMERIC (1-5)
  timestamp_unix — NUMERIC
  embedding      — VECTOR (FLAT, COSINE, 1024-dim)
"""

import json
import os
import struct
from datetime import datetime
from typing import Optional
from opentelemetry import trace

_tracer = trace.get_tracer("cortex.redis")

import redis
from redis.commands.search.field import TextField, VectorField, TagField, NumericField
from redis.commands.search.indexDefinition import IndexDefinition, IndexType
from redis.commands.search.query import Query

import voyageai

VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
INDEX_NAME = "cortex_idx"
VECTOR_DIM = 1024
DOC_PREFIX = "cortex:note:"

_redis_client = None
_voyage_client = None


def _redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(REDIS_URL, decode_responses=False)
    return _redis_client


def _embed(text: str) -> list[float]:
    global _voyage_client
    if _voyage_client is None:
        _voyage_client = voyageai.Client(api_key=VOYAGE_API_KEY)
    result = _voyage_client.embed([text[:32000]], model="voyage-3")
    return result.embeddings[0]


def setup_index(drop_existing: bool = False):
    """Create (or recreate) the Redis vector search index."""
    r = _redis()

    if drop_existing:
        try:
            r.ft(INDEX_NAME).dropindex()
            print(f"Dropped existing index '{INDEX_NAME}'")
        except Exception:
            pass
    else:
        try:
            r.ft(INDEX_NAME).info()
            print(f"Index '{INDEX_NAME}' already exists.")
            return
        except Exception:
            pass

    schema = (
        TextField("content"),
        TextField("source"),
        TagField("kind"),
        TagField("status"),
        TagField("confidence"),
        TagField("topics"),
        TagField("entity_ids"),
        NumericField("importance"),
        NumericField("timestamp_unix"),
        VectorField(
            "embedding",
            "FLAT",
            {
                "TYPE": "FLOAT32",
                "DIM": VECTOR_DIM,
                "DISTANCE_METRIC": "COSINE",
            }
        )
    )

    r.ft(INDEX_NAME).create_index(
        schema,
        definition=IndexDefinition(prefix=[DOC_PREFIX], index_type=IndexType.HASH)
    )
    print(f"✅ Created Redis index '{INDEX_NAME}'")


def _pack_embedding(embedding: list[float]) -> bytes:
    return struct.pack(f"{len(embedding)}f", *embedding)


def _store_item(
    r: redis.Redis,
    key: str,
    content: str,
    source: str,
    kind: str,
    timestamp_unix: int,
    embedding: list[float],
    status: str = "active",
    confidence: str = "confirmed",
    importance: int = 3,
    topics: list[str] = None,
    entity_ids: list[str] = None,
):
    mapping = {
        b"content": content.encode("utf-8"),
        b"source": source.encode("utf-8"),
        b"kind": kind.encode("utf-8"),
        b"status": status.encode("utf-8"),
        b"confidence": confidence.encode("utf-8"),
        b"importance": str(importance).encode("utf-8"),
        b"topics": (",".join(topics or [])).encode("utf-8"),
        b"entity_ids": (",".join(entity_ids or [])).encode("utf-8"),
        b"timestamp_unix": str(timestamp_unix).encode("utf-8"),
        b"embedding": _pack_embedding(embedding),
        # Legacy compat
        b"type": kind.encode("utf-8"),
        b"tags": (",".join([kind, source] + (topics or []))).encode("utf-8"),
    }
    r.hset(key, mapping=mapping)


def embed_and_store(extracted: dict, raw_text: str = "") -> list[str]:
    """
    Embed all extracted context items and store in Redis.
    Accepts both v2 (records/tasks/entities) and legacy (KEY_INSIGHTS etc.) formats.
    Returns list of Redis keys stored.
    """
    r = _redis()
    source = extracted.get("_source", "unknown")
    timestamp = extracted.get("_timestamp", datetime.now().isoformat())

    try:
        timestamp_unix = int(datetime.fromisoformat(timestamp).timestamp())
    except Exception:
        timestamp_unix = int(datetime.now().timestamp())

    stored_keys = []
    key_counter = [0]

    def _next_key(kind: str, item_id: str = "") -> str:
        k = f"{DOC_PREFIX}{kind}:{source}:{timestamp_unix}:{item_id or key_counter[0]}"
        key_counter[0] += 1
        return k

    def _embed_store(
        content: str,
        kind: str,
        item_id: str = "",
        status: str = "active",
        confidence: str = "confirmed",
        importance: int = 3,
        topics: list = None,
        entity_ids: list = None,
    ):
        if not content.strip():
            return
        try:
            embedding = _embed(content)
            key = _next_key(kind, item_id)
            _store_item(
                r, key, content, source, kind, timestamp_unix, embedding,
                status=status, confidence=confidence, importance=importance,
                topics=topics or [], entity_ids=entity_ids or []
            )
            stored_keys.append(key)
        except Exception as e:
            print(f"  ⚠️ Failed to embed {kind}: {e}", flush=True)

    # ── v2 format ──────────────────────────────────────────────────────────────
    for record in extracted.get("records", []):
        _embed_store(
            content=record.get("content", ""),
            kind=record.get("kind", "claim"),
            item_id=record.get("id", ""),
            confidence=record.get("confidence", "confirmed"),
            importance=record.get("importance", 3),
            topics=record.get("topics", []),
            entity_ids=record.get("entity_ids", []),
        )

    for task in extracted.get("tasks", []):
        _embed_store(
            content=task.get("content", ""),
            kind=task.get("kind", "action"),
            item_id=task.get("id", ""),
            status=task.get("status", "open"),
            importance=task.get("importance", 3),
            topics=task.get("topics", []),
            entity_ids=task.get("entity_ids", []),
        )

    for entity in extracted.get("entities", []):
        if isinstance(entity, dict):
            content = f"{entity.get('name', '')}: {entity.get('context', '')}"
            eid = entity.get("id", "")
        else:
            content = str(entity)
            eid = ""
        _embed_store(
            content=content,
            kind="person" if "person_" in eid else entity.get("kind", "entity") if isinstance(entity, dict) else "entity",
            item_id=eid,
            entity_ids=[eid] if eid else [],
        )

    if extracted.get("summary"):
        _embed_store(extracted["summary"], "summary")

    # ── legacy format (KEY_INSIGHTS etc.) ────────────────────────────────────
    for insight in extracted.get("KEY_INSIGHTS", []):
        _embed_store(insight, "claim", confidence="confirmed", importance=3)

    for decision in extracted.get("DECISIONS", []):
        _embed_store(decision, "decision", confidence="confirmed", importance=4)

    for question in extracted.get("OPEN_QUESTIONS", []):
        _embed_store(question, "question", status="open", importance=3)

    for action in extracted.get("ACTION_ITEMS", []):
        _embed_store(action, "action", status="open", importance=3)

    for person in extracted.get("PEOPLE", []):
        if isinstance(person, dict):
            content = f"{person.get('name', '')}: {person.get('context', '')}"
        else:
            content = str(person)
        _embed_store(content, "person")

    return stored_keys


def search_context(
    query: str,
    top_k: int = 5,
    kind: str = None,
    status: str = None,
    min_importance: int = None,
    topics: list[str] = None,
    entity_id: str = None,
    _span_name: str = "cortex.retrieval",
) -> list[dict]:
    """
    Multi-strategy semantic search with optional metadata filters.

    Args:
        query:          Natural language query
        top_k:          Number of results
        kind:           Filter by kind (claim, decision, action, question, person, summary)
        status:         Filter by status (active, open, resolved)
        min_importance: Minimum importance score (1-5)
        topics:         Filter by topic tags
        entity_id:      Filter by entity ID
    """
    with _tracer.start_as_current_span(_span_name) as span:
        span.set_attribute("input.value", query)
        span.set_attribute("cortex.top_k", top_k)
        if kind:
            span.set_attribute("cortex.filter.kind", kind)
        if min_importance:
            span.set_attribute("cortex.filter.min_importance", min_importance)

        results = _search_context_inner(
            query=query, top_k=top_k, kind=kind, status=status,
            min_importance=min_importance, topics=topics, entity_id=entity_id,
        )

        span.set_attribute("cortex.results_count", len(results))
        if results:
            span.set_attribute("output.value", " | ".join(r.get("content", "")[:80] for r in results[:3]))
        return results


def _search_context_inner(
    query: str,
    top_k: int = 5,
    kind: str = None,
    status: str = None,
    min_importance: int = None,
    topics: list[str] = None,
    entity_id: str = None,
) -> list[dict]:
    r = _redis()
    embedding = _embed(query)
    embedding_bytes = _pack_embedding(embedding)

    # Build filter expression
    filters = []
    if kind:
        filters.append(f"@kind:{{{kind}}}")
    if status:
        filters.append(f"@status:{{{status}}}")
    if min_importance:
        filters.append(f"@importance:[{min_importance} +inf]")
    if topics:
        for topic in topics:
            filters.append(f"@topics:{{{topic}}}")
    if entity_id:
        filters.append(f"@entity_ids:{{{entity_id}}}")

    filter_expr = " ".join(filters) if filters else "*"
    knn_expr = f"{filter_expr}=>[KNN {top_k} @embedding $vec AS score]"

    q = (
        Query(knn_expr)
        .sort_by("score")
        .return_fields("content", "source", "kind", "type", "status",
                       "confidence", "importance", "topics", "entity_ids",
                       "timestamp_unix", "score")
        .dialect(2)
    )

    results = r.ft(INDEX_NAME).search(q, query_params={"vec": embedding_bytes})

    output = []
    for doc in results.docs:
        ts_unix = int(getattr(doc, "timestamp_unix", 0) or 0)
        kind_val = getattr(doc, "kind", "") or getattr(doc, "type", "")
        output.append({
            "content": getattr(doc, "content", ""),
            "source": getattr(doc, "source", ""),
            "type": kind_val,       # legacy compat
            "kind": kind_val,
            "status": getattr(doc, "status", "active"),
            "confidence": getattr(doc, "confidence", "confirmed"),
            "importance": int(getattr(doc, "importance", 3) or 3),
            "topics": [t for t in (getattr(doc, "topics", "") or "").split(",") if t],
            "entity_ids": [e for e in (getattr(doc, "entity_ids", "") or "").split(",") if e],
            "timestamp": datetime.fromtimestamp(ts_unix).isoformat() if ts_unix else "",
            "score": float(getattr(doc, "score", 1.0)),
        })

    return output


def get_recent_context(since: datetime, top_k: int = 20) -> list[dict]:
    """Get context items captured after a given datetime."""
    r = _redis()
    since_unix = int(since.timestamp())

    q = (
        Query(f"@timestamp_unix:[{since_unix} +inf]")
        .sort_by("timestamp_unix", asc=False)
        .return_fields("content", "source", "kind", "type", "status", "importance", "timestamp_unix")
        .paging(0, top_k)
    )

    results = r.ft(INDEX_NAME).search(q)
    output = []
    for doc in results.docs:
        ts_unix = int(getattr(doc, "timestamp_unix", 0) or 0)
        kind_val = getattr(doc, "kind", "") or getattr(doc, "type", "")
        output.append({
            "content": getattr(doc, "content", ""),
            "source": getattr(doc, "source", ""),
            "type": kind_val,
            "kind": kind_val,
            "importance": int(getattr(doc, "importance", 3) or 3),
            "timestamp": datetime.fromtimestamp(ts_unix).isoformat() if ts_unix else "",
        })

    return output


def search_by_entity(entity_id: str, top_k: int = 10) -> list[dict]:
    """Get all context items involving a specific entity."""
    return search_context(entity_id.replace("_", " "), top_k=top_k, entity_id=entity_id)


def search_decisions(query: str = "important decision", top_k: int = 10) -> list[dict]:
    """Get decisions, optionally filtered by query."""
    return search_context(query, top_k=top_k, kind="decision")


def search_open_tasks(query: str = "open action question", top_k: int = 20) -> list[dict]:
    """Get open tasks and questions."""
    return search_context(query, top_k=top_k, status="open")


if __name__ == "__main__":
    print("Setting up Cortex Redis index (v2)...")
    setup_index(drop_existing=True)
    print("Done.")
