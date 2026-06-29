from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any


def stable_id(prefix: str, content: str) -> str:
    return prefix + hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


MEMORY_LAYERS = {"semantic", "episodic", "style", "decision", "preference", "negative"}


def extract_context(raw_text: str, source: str = "unknown") -> dict[str, Any]:
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _extract_with_claude(raw_text, source)
        except Exception:
            pass
    return _extract_locally(raw_text, source)


def _extract_with_claude(raw_text: str, source: str) -> dict[str, Any]:
    from anthropic import Anthropic

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = """Extract Cortex memory as strict JSON with keys records, tasks, entities, summary.
records: list of {id, kind, layer, content, confidence, importance, entity_ids, topics, occurred_at}
tasks: list of {id, kind, content, status, importance, entity_ids, topics}
entities: list of {id, kind, name, aliases, context}
Kinds: claim, decision, event, preference, observation, style, negative. Layers: semantic, episodic, style, decision, preference, negative. Task kinds: action, question, decision-pending.
Use stable IDs and keep each memory atomic. Return JSON only."""
    response = client.messages.create(
        model=os.environ.get("CORTEX_EXTRACTION_MODEL", "claude-opus-4-5"),
        max_tokens=2500,
        system=prompt,
        messages=[{"role": "user", "content": f"Source: {source}\n\n{raw_text[:40000]}"}],
    )
    text = response.content[0].text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    return _normalize_extraction(data, raw_text, source)


def _extract_locally(raw_text: str, source: str) -> dict[str, Any]:
    cleaned = re.sub(r"\s+", " ", raw_text).strip()
    sentences = _sentences(cleaned)
    summary = _summarize(sentences, cleaned)
    records: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []

    for sentence in sentences[:24]:
        lower = sentence.lower()
        if _looks_like_task(sentence):
            tasks.append(_task(sentence))
        elif _looks_like_negative(lower):
            records.append(_record("negative", sentence, importance=4))
        elif _looks_like_decision(lower):
            records.append(_record("decision", sentence, importance=4))
        elif _looks_like_style(lower):
            records.append(_record("style", sentence, importance=3))
        elif _looks_like_preference(lower):
            records.append(_record("preference", sentence, importance=3))
        elif _looks_like_event(lower):
            records.append(_record("event", sentence, importance=3))
        elif len(sentence.split()) >= 5:
            records.append(_record("claim", sentence, importance=2))

    if summary and not any(r["kind"] == "summary" for r in records):
        records.insert(0, _record("summary", summary, importance=3))

    entities = _entities(cleaned)
    entity_ids = [entity["id"] for entity in entities]
    topics = _topics(cleaned)
    for item in records:
        item["entity_ids"] = entity_ids[:12]
        item["topics"] = topics[:4]
    for item in tasks:
        item["entity_ids"] = entity_ids[:12]
        item["topics"] = topics[:4]

    return _normalize_extraction({"records": records, "tasks": tasks, "entities": entities, "summary": summary}, raw_text, source)


def _normalize_extraction(data: dict[str, Any], raw_text: str, source: str) -> dict[str, Any]:
    timestamp = now_iso()
    data.setdefault("records", [])
    data.setdefault("tasks", [])
    data.setdefault("entities", [])
    data.setdefault("summary", "")
    data["_source"] = source
    data["_source_id"] = stable_id("src_", source + timestamp + raw_text[:80])
    data["_timestamp"] = timestamp
    data["_raw_length"] = len(raw_text)
    for record in data["records"]:
        content = str(record.get("content", "")).strip()
        record["id"] = record.get("id") or stable_id("mem_", content)
        record["kind"] = record.get("kind") or "observation"
        record["layer"] = _normalize_layer(record.get("layer"), record["kind"], content)
        record["confidence"] = record.get("confidence") or "confirmed"
        record["importance"] = int(record.get("importance") or 3)
        record["entity_ids"] = list(record.get("entity_ids") or [])
        record["topics"] = list(record.get("topics") or [])
        record["occurred_at"] = record.get("occurred_at")
    for task in data["tasks"]:
        content = str(task.get("content", "")).strip()
        task["id"] = task.get("id") or stable_id("task_", content)
        task["kind"] = task.get("kind") or ("question" if content.endswith("?") else "action")
        task["status"] = task.get("status") or "open"
        task["importance"] = int(task.get("importance") or 3)
        task["entity_ids"] = list(task.get("entity_ids") or [])
        task["topics"] = list(task.get("topics") or [])
    return data


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip(" -•\t") for p in parts if len(p.strip()) > 12]


def _summarize(sentences: list[str], text: str) -> str:
    if not text:
        return ""
    selected = sentences[:2] if sentences else [text[:240]]
    return " ".join(selected)[:500]


def _looks_like_decision(lower: str) -> bool:
    signals = ["decided", "we will", "we'll", "going with", "chose", "choice is", "let's use", "agreed"]
    return any(signal in lower for signal in signals)


def _looks_like_preference(lower: str) -> bool:
    signals = ["i prefer", "i like", "i don't like", "i want", "i need", "preference"]
    return any(signal in lower for signal in signals)


def _looks_like_negative(lower: str) -> bool:
    signals = [
        "i don't like",
        "i dislike",
        "i hate",
        "avoid ",
        "rejected",
        "do not ",
        "don't ",
        "never use",
        "not helpful",
        "bad fit",
    ]
    return any(signal in lower for signal in signals)


def _looks_like_style(lower: str) -> bool:
    signals = [
        "my writing",
        "writing style",
        "tone",
        "phrasing",
        "voice",
        "sentence length",
        "formatting",
        "i usually write",
        "i say",
    ]
    return any(signal in lower for signal in signals)


def _looks_like_event(lower: str) -> bool:
    signals = [
        "yesterday",
        "today",
        "last week",
        "last month",
        "met with",
        "talked to",
        "emailed",
        "shipped",
        "launched",
    ]
    return any(signal in lower for signal in signals)


def _looks_like_task(sentence: str) -> bool:
    lower = sentence.lower()
    return sentence.endswith("?") or any(signal in lower for signal in ["todo", "to do", "need to", "follow up", "we should", "i should", "next step", "open question"])


def _record(kind: str, content: str, importance: int) -> dict[str, Any]:
    return {
        "id": stable_id("mem_", kind + content),
        "kind": kind,
        "layer": _normalize_layer(None, kind, content),
        "content": content,
        "confidence": "confirmed",
        "importance": importance,
        "entity_ids": [],
        "topics": [],
        "occurred_at": None,
    }


def _normalize_layer(value: Any, kind: str, content: str = "") -> str:
    layer = str(value or "").strip().lower()
    if layer in MEMORY_LAYERS:
        return layer
    kind = str(kind or "").strip().lower()
    if kind in {"decision"}:
        return "decision"
    if kind in {"preference"}:
        return "preference"
    if kind in {"event"}:
        return "episodic"
    if kind in {"style"}:
        return "style"
    if kind in {"negative"}:
        return "negative"
    lower = content.lower()
    if _looks_like_negative(lower):
        return "negative"
    if _looks_like_style(lower):
        return "style"
    if _looks_like_event(lower):
        return "episodic"
    return "semantic"


def _task(content: str) -> dict[str, Any]:
    kind = "question" if content.endswith("?") else "action"
    return {
        "id": stable_id("task_", kind + content),
        "kind": kind,
        "content": content,
        "status": "open",
        "importance": 3,
        "entity_ids": [],
        "topics": [],
    }


def _entities(text: str) -> list[dict[str, Any]]:
    candidates = set(re.findall(r"\b[A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+){0,3}\b", text))
    stop = {"I", "The", "This", "That", "We", "You", "Next", "Open"}
    org_like = {
        "ChatGPT",
        "Claude",
        "GitHub",
        "Google",
        "Notion",
        "OpenAI",
        "Redis",
        "SQLite",
        "Supabase",
    }
    project_like = {"Cortex"}
    topic_like = {"MCP", "MVP"}
    entities = []
    for name in sorted(candidates):
        if name in stop or len(name) < 3:
            continue
        if name in org_like:
            kind = "org"
        elif name in project_like or any(word in name.lower() for word in ["app", "project", "cortex"]):
            kind = "project"
        elif name in topic_like or (name.isupper() and len(name) <= 5):
            kind = "topic"
        else:
            kind = "person"
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        prefix = {"project": "project_", "org": "org_", "topic": "topic_"}.get(kind, "person_")
        entities.append({
            "id": prefix + slug,
            "kind": kind,
            "name": name,
            "aliases": [],
            "context": f"Mentioned in context captured from {text[:40].strip()}...",
        })
    return entities[:12]


def _topics(text: str) -> list[str]:
    words = re.findall(r"\b[a-z][a-z0-9-]{3,}\b", text.lower())
    banned = {"this", "that", "with", "from", "have", "will", "about", "there", "their", "what", "when", "where", "your"}
    counts: dict[str, int] = {}
    for word in words:
        if word not in banned:
            counts[word] = counts.get(word, 0) + 1
    return [word for word, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:8]]
