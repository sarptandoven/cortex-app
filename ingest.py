"""
cortex/ingest.py
----------------
Core extraction pipeline. Takes raw text from any source,
calls Claude to extract structured context, returns a dict.

Schema v2: Each item has a stable ID, kind, confidence, importance,
entity references, and topic tags.
"""

import hashlib
import json
import re
import os
from datetime import datetime

# Initialize Arize tracing BEFORE creating Anthropic client
# so auto-instrumentation can wrap the client at import time
from instrumentation import setup_tracing, get_tracer
setup_tracing(project_name="cortex")

from anthropic import Anthropic

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
_tracer = get_tracer()


EXTRACTION_SYSTEM_PROMPT = """You are a context extraction engine for Cortex, a personal second brain.

Given any text (conversation, document, message, transcript), extract structured, atomic context items.

Return a single JSON object with these fields:

"records": list of atomic claims, decisions, or observations — each with:
  - "id": stable 8-char hex id prefixed "mem_" (hash of content)
  - "kind": "claim" | "decision" | "event" | "preference" | "observation"
  - "content": the atomic statement, 1-2 sentences, self-contained
  - "confidence": "confirmed" (stated as fact) | "reported" (someone said) | "inferred" (implied)
  - "importance": integer 1-5 (5=life-changing decision, 4=significant, 3=notable, 2=useful detail, 1=minor)
  - "entity_ids": list of stable entity IDs involved, e.g. ["person_vamika-singhal", "project_cortex"]
  - "topics": list of 1-3 topic tags, e.g. ["architecture", "redis", "memory"]
  - "occurred_at": ISO datetime string if a time is mentioned, else null

"tasks": list of action items and open questions — each with:
  - "id": "task_" + 8 hex chars
  - "kind": "action" | "question" | "decision-pending"
  - "content": the task or question
  - "status": "open"
  - "importance": 1-5
  - "entity_ids": list
  - "topics": list

"entities": people, projects, orgs, and topics mentioned — each with:
  - "id": stable slug: "person_first-last" | "project_name" | "org_name"
  - "kind": "person" | "project" | "org"
  - "name": canonical full name
  - "aliases": other names or spellings seen in this text
  - "context": 1 sentence describing who/what this is

"summary": 2-3 sentence summary of the overall content

Rules:
- Atomic claims: one fact per record, no compound statements
- Stable IDs: always use the same slug for the same entity (person_vamika-singhal not person_vamika)
- Only extract signal, not noise — empty lists are fine
- Return ONLY valid JSON, no markdown fences, no commentary"""


def make_id(prefix: str, content: str) -> str:
    """Generate a stable short ID from content hash."""
    return prefix + hashlib.sha256(content.encode()).hexdigest()[:8]


def extract_context(raw_text: str, source: str = "unknown") -> dict:
    """
    Extract structured context from raw text using Claude.

    Returns dict with keys: records, tasks, entities, summary,
    plus metadata: _source, _source_id, _timestamp, _raw_length
    """
    if not raw_text or not raw_text.strip():
        return _empty_extraction(source)

    MAX_INPUT_CHARS = 40_000
    truncated = raw_text[:MAX_INPUT_CHARS]
    if len(raw_text) > MAX_INPUT_CHARS:
        truncated += f"\n\n[... truncated {len(raw_text) - MAX_INPUT_CHARS} chars ...]"

    now = datetime.now().isoformat()
    source_id = make_id("src_", source + now[:16])

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=3000,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"Source: {source}\n"
                f"Source ID: {source_id}\n"
                f"Captured: {now}\n\n"
                f"---\n\n{truncated}\n\n---\n\n"
                f"Extract context as JSON:"
            )
        }]
    )

    raw_output = response.content[0].text.strip()
    extracted = _parse_json_response(raw_output)

    # Ensure required fields
    extracted.setdefault("records", [])
    extracted.setdefault("tasks", [])
    extracted.setdefault("entities", [])
    extracted.setdefault("summary", "")

    # Handle old-format responses (Claude sometimes uses old field names)
    _migrate_old_format(extracted)

    # Attach metadata
    extracted["_source"] = source
    extracted["_source_id"] = source_id
    extracted["_timestamp"] = now
    extracted["_raw_length"] = len(raw_text)

    return extracted


def _migrate_old_format(extracted: dict):
    """Migrate old KEY_INSIGHTS / DECISIONS / etc. fields to new schema."""
    for insight in extracted.pop("KEY_INSIGHTS", []):
        extracted["records"].append({
            "id": make_id("mem_", insight),
            "kind": "claim",
            "content": insight,
            "confidence": "confirmed",
            "importance": 3,
            "entity_ids": [],
            "topics": [],
            "occurred_at": None,
        })

    for decision in extracted.pop("DECISIONS", []):
        extracted["records"].append({
            "id": make_id("mem_", decision),
            "kind": "decision",
            "content": decision,
            "confidence": "confirmed",
            "importance": 4,
            "entity_ids": [],
            "topics": [],
            "occurred_at": None,
        })

    for question in extracted.pop("OPEN_QUESTIONS", []):
        extracted["tasks"].append({
            "id": make_id("task_", question),
            "kind": "question",
            "content": question,
            "status": "open",
            "importance": 3,
            "entity_ids": [],
            "topics": [],
        })

    for action in extracted.pop("ACTION_ITEMS", []):
        extracted["tasks"].append({
            "id": make_id("task_", action),
            "kind": "action",
            "content": action,
            "status": "open",
            "importance": 3,
            "entity_ids": [],
            "topics": [],
        })

    for person in extracted.pop("PEOPLE", []):
        if isinstance(person, dict):
            name = person.get("name", "")
            ctx = person.get("context", "")
        else:
            name = str(person)
            ctx = ""
        if name:
            slug = re.sub(r"[^\w]+", "-", name.lower()).strip("-")
            extracted["entities"].append({
                "id": f"person_{slug}",
                "kind": "person",
                "name": name,
                "aliases": [],
                "context": ctx,
            })

    # Remove old summary key if new one not present
    old_summary = extracted.pop("SUMMARY", None)
    if old_summary and not extracted.get("summary"):
        extracted["summary"] = old_summary

    # Remove unused old fields
    extracted.pop("PROJECTS", None)


def _parse_json_response(text: str) -> dict:
    """Robustly parse JSON from Claude's response."""
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

    return {
        "records": [],
        "tasks": [],
        "entities": [],
        "summary": text[:500]
    }


def _empty_extraction(source: str) -> dict:
    return {
        "records": [],
        "tasks": [],
        "entities": [],
        "summary": "",
        "_source": source,
        "_source_id": make_id("src_", source),
        "_timestamp": datetime.now().isoformat(),
        "_raw_length": 0,
    }


def format_extraction_summary(extracted: dict) -> str:
    """Human-readable summary of what was extracted."""
    lines = [f"📥 Captured from {extracted.get('_source', 'unknown')}"]

    records = extracted.get("records", [])
    tasks = extracted.get("tasks", [])
    entities = extracted.get("entities", [])

    # Count by kind
    by_kind: dict[str, int] = {}
    for r in records:
        k = r.get("kind", "claim")
        by_kind[k] = by_kind.get(k, 0) + 1

    parts = [f"{v} {k}s" for k, v in by_kind.items()]

    questions = sum(1 for t in tasks if t.get("kind") == "question")
    actions = sum(1 for t in tasks if t.get("kind") == "action")
    if questions:
        parts.append(f"{questions} questions")
    if actions:
        parts.append(f"{actions} actions")
    if entities:
        parts.append(f"{len(entities)} entities")

    if parts:
        lines.append("→ " + ", ".join(parts))

    if extracted.get("summary"):
        lines.append(f'"{extracted["summary"][:120]}..."')

    return "\n".join(lines)


if __name__ == "__main__":
    sample = """
    Vamika: I'm thinking we should use GitHub instead of Obsidian for Cortex.
    The main reason is that GitHub gives us an API, version history, and it's shareable for the demo.

    Claude: That makes a lot of sense. GitHub Actions could also trigger your ingestion pipeline automatically.
    You could use GitHub Issues to track open questions.

    Vamika: Yes exactly. And judges can just click the repo link. Let's go with GitHub.
    One thing I'm still not sure about is whether we need a graph visualization or if the file tree is enough.

    Claude: For the hackathon demo, I'd ship a simple D3.js graph in the chat UI.

    Vamika: Good call. Let's plan to do that in Phase 5.
    """
    result = extract_context(sample, source="claude-chat")
    print(json.dumps(result, indent=2))
    print("\n" + format_extraction_summary(result))
