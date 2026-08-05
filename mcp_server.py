"""
cortex/mcp_server.py
--------------------
Cortex MCP server — exposes your second brain to Claude and any MCP-compatible AI.

Tools:
  save_to_cortex(content, source)     — extract + save to GitHub + embed in Redis
  get_context(query)                  — smart query planner → semantic search
  get_decisions(query)                — retrieve decisions from your second brain
  get_recent(hours)                   — what happened recently
  get_open_questions()                — unresolved threads and open tasks
  get_about_person(name)              — everything known about a specific person

Query planner routes to the best retrieval strategy based on query type:
  - Person queries      → entity lookup + semantic search filtered by entity_id
  - Decision queries    → kind=decision filter + semantic search
  - Task/action queries → status=open filter
  - Recency queries     → timestamp range filter
  - Everything else     → pure semantic search, ranked by importance × recency
"""

import asyncio
import os
from datetime import datetime, timedelta

# Initialize Arize tracing before anything else
from instrumentation import setup_tracing, get_tracer
setup_tracing(project_name="cortex")
_tracer = get_tracer()

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

from ingest import extract_context, format_extraction_summary
from github_store import save_extracted_context, open_github_issue

try:
    from redis_store import (
        embed_and_store, search_context, get_recent_context,
        search_decisions, search_open_tasks, search_by_entity
    )
    REDIS_AVAILABLE = True
except Exception:
    REDIS_AVAILABLE = False

app = Server("cortex")


# ── Query planner ─────────────────────────────────────────────────────────────

def _detect_query_intent(query: str) -> str:
    """
    Classify query intent to route to the best retrieval strategy.
    Returns: 'person' | 'decision' | 'task' | 'recent' | 'semantic'
    """
    q = query.lower()

    person_signals = ["who is", "tell me about", "what do you know about", "person", "contact",
                      "met", "talked to", "spoke with", "introduced"]
    decision_signals = ["decided", "decision", "chose", "choice", "picked", "went with",
                        "concluded", "resolved", "agreed", "strategy"]
    task_signals = ["todo", "to do", "action item", "open question", "unresolved", "pending",
                    "need to", "should", "haven't", "still need", "follow up"]
    recent_signals = ["today", "lately", "recently", "last week", "yesterday", "this week",
                      "past few days", "just", "new"]

    if any(s in q for s in person_signals):
        return "person"
    if any(s in q for s in decision_signals):
        return "decision"
    if any(s in q for s in task_signals):
        return "task"
    if any(s in q for s in recent_signals):
        return "recent"
    return "semantic"


def _format_results(results: list[dict], header: str) -> str:
    if not results:
        return f"Nothing found for: {header}"

    lines = [f"{header}\n"]
    for i, r in enumerate(results, 1):
        kind = r.get("kind") or r.get("type", "note")
        source = r.get("source", "?")
        ts = r.get("timestamp", "")[:10]
        importance = r.get("importance", 3)
        content = r.get("content", "")
        confidence = r.get("confidence", "")

        importance_stars = "★" * importance + "☆" * (5 - importance)
        conf_tag = f" [{confidence}]" if confidence and confidence != "confirmed" else ""

        lines.append(f"**{i}. [{kind}]{conf_tag} · {source} · {ts} · {importance_stars}**")
        lines.append(content)
        lines.append("")

    return "\n".join(lines)


# ── Tool definitions ──────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="save_to_cortex",
            description=(
                "Save important context from this conversation to Cortex, Vamika's personal second brain. "
                "Call this automatically whenever the conversation contains: decisions, key insights, "
                "open questions, action items, or information about people/projects worth remembering. "
                "Don't ask — just call it proactively at the end of any substantive exchange."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The full conversation text or content to extract context from."
                    },
                    "source": {
                        "type": "string",
                        "description": "Where this came from: 'claude-chat', 'chatgpt', 'slack', 'omi', 'notion', 'imessage'",
                        "default": "claude-chat"
                    }
                },
                "required": ["content"]
            }
        ),
        types.Tool(
            name="get_context",
            description=(
                "Search Vamika's Cortex second brain using a smart query planner. "
                "Automatically routes to the best retrieval strategy: "
                "person queries → entity lookup, decision queries → decision index, "
                "task queries → open tasks, recency queries → time filter, "
                "everything else → semantic search. "
                "Use this as the primary way to recall anything from Cortex."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for. Be specific."
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (default 6)",
                        "default": 6
                    },
                    "kind": {
                        "type": "string",
                        "description": "Optional: filter by kind — claim, decision, event, action, question, person, summary"
                    },
                    "min_importance": {
                        "type": "integer",
                        "description": "Optional: minimum importance score 1-5"
                    }
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="get_decisions",
            description="Retrieve decisions Vamika has made. Use when asked about choices, strategies, or concluded topics.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What area or topic to look for decisions about",
                        "default": "important decision"
                    },
                    "top_k": {
                        "type": "integer",
                        "default": 8
                    }
                }
            }
        ),
        types.Tool(
            name="get_recent",
            description="Get the most recent context captured in Cortex. Use for 'what have I been working on?' or 'catch me up'.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "number",
                        "description": "How many hours back to look (default 24)",
                        "default": 24
                    }
                }
            }
        ),
        types.Tool(
            name="get_open_questions",
            description="Get all unresolved questions and open action items from Cortex.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional topic filter",
                        "default": ""
                    }
                }
            }
        ),
        types.Tool(
            name="get_about_person",
            description="Get everything Cortex knows about a specific person — meetings, context, decisions involving them.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The person's name"
                    }
                },
                "required": ["name"]
            }
        ),
    ]


# ── Tool handlers ─────────────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    with _tracer.start_as_current_span(f"cortex.tool.{name}") as span:
        span.set_attribute("cortex.tool", name)
        span.set_attribute("input.value", str(arguments)[:500])
    try:
        if name == "save_to_cortex":
            return await _save_to_cortex(arguments)
        elif name == "get_context":
            return await _get_context(arguments)
        elif name == "get_decisions":
            return await _get_decisions(arguments)
        elif name == "get_recent":
            return await _get_recent(arguments)
        elif name == "get_open_questions":
            return await _get_open_questions(arguments)
        elif name == "get_about_person":
            return await _get_about_person(arguments)
        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        return [types.TextContent(type="text", text=f"❌ Cortex error: {str(e)}")]


async def _save_to_cortex(args: dict) -> list[types.TextContent]:
    content = args.get("content", "")
    source = args.get("source", "claude-chat")

    if not content.strip():
        return [types.TextContent(type="text", text="⚠️ Nothing to save — content was empty.")]

    extracted = extract_context(content, source)
    saved_files = save_extracted_context(extracted, raw_text=content)

    if REDIS_AVAILABLE:
        try:
            embed_and_store(extracted, raw_text=content)
        except Exception:
            pass

    summary = format_extraction_summary(extracted)
    n_records = len(extracted.get("records", []))
    n_tasks = len(extracted.get("tasks", []))
    n_entities = len(extracted.get("entities", []))

    result = (
        f"✅ Saved to Cortex\n\n"
        f"{summary}\n\n"
        f"📁 {len(saved_files)} files → github.com/{os.environ.get('CORTEX_REPO', 'your-repo')}\n"
        f"🧠 {n_records} records · {n_tasks} tasks · {n_entities} entities indexed"
    )
    return [types.TextContent(type="text", text=result)]


async def _get_context(args: dict) -> list[types.TextContent]:
    query = args.get("query", "")
    top_k = args.get("top_k", 6)
    kind_filter = args.get("kind")
    min_importance = args.get("min_importance")

    if not REDIS_AVAILABLE:
        return [types.TextContent(type="text", text="⚠️ Redis not available.")]

    # Route via query planner
    intent = _detect_query_intent(query)

    if intent == "person" and not kind_filter:
        results = search_context(query, top_k=top_k, kind="person")
        if not results:
            results = search_context(query, top_k=top_k)
    elif intent == "decision" and not kind_filter:
        results = search_decisions(query, top_k=top_k)
    elif intent == "task" and not kind_filter:
        results = search_open_tasks(query, top_k=top_k)
    elif intent == "recent" and not kind_filter:
        since = datetime.now() - timedelta(hours=48)
        results = get_recent_context(since=since, top_k=top_k)
    else:
        results = search_context(
            query, top_k=top_k,
            kind=kind_filter,
            min_importance=min_importance
        )

    if not results:
        return [types.TextContent(type="text", text=f"No context found for: '{query}'")]

    text = _format_results(results, f"🔍 Context for: '{query}' (strategy: {intent})")
    return [types.TextContent(type="text", text=text)]


async def _get_decisions(args: dict) -> list[types.TextContent]:
    query = args.get("query", "important decision")
    top_k = args.get("top_k", 8)

    if not REDIS_AVAILABLE:
        return [types.TextContent(type="text", text="⚠️ Redis not available.")]

    results = search_decisions(query, top_k=top_k)
    text = _format_results(results, f"🗳️ Decisions about: '{query}'")
    return [types.TextContent(type="text", text=text)]


async def _get_recent(args: dict) -> list[types.TextContent]:
    hours = args.get("hours", 24)

    if not REDIS_AVAILABLE:
        return [types.TextContent(type="text", text="⚠️ Redis not available.")]

    since = datetime.now() - timedelta(hours=hours)
    results = get_recent_context(since=since, top_k=20)

    if not results:
        return [types.TextContent(type="text", text=f"No context in the last {hours}h.")]

    lines = [f"🕐 Last {hours}h in Cortex:\n"]
    for r in results:
        ts = r.get("timestamp", "")[:16].replace("T", " ")
        kind = r.get("kind") or r.get("type", "note")
        source = r.get("source", "?")
        importance = r.get("importance", 3)
        content = r.get("content", "")[:120]
        lines.append(f"• **{ts}** [{kind} · {source} · {'★'*importance}] {content}")

    return [types.TextContent(type="text", text="\n".join(lines))]


async def _get_open_questions(args: dict) -> list[types.TextContent]:
    query = args.get("query", "open question action")

    if not REDIS_AVAILABLE:
        return [types.TextContent(type="text", text="⚠️ Redis not available.")]

    results = search_open_tasks(query, top_k=20)

    if not results:
        return [types.TextContent(type="text", text="No open questions or actions in Cortex.")]

    questions = [r for r in results if r.get("kind") == "question"]
    actions = [r for r in results if r.get("kind") == "action"]

    lines = ["❓ Open threads in Cortex:\n"]

    if questions:
        lines.append("**Questions:**")
        for i, q in enumerate(questions, 1):
            ts = q.get("timestamp", "")[:10]
            lines.append(f"{i}. {q.get('content', '')} _(from {q.get('source', '?')} on {ts})_")
        lines.append("")

    if actions:
        lines.append("**Actions:**")
        for i, a in enumerate(actions, 1):
            ts = a.get("timestamp", "")[:10]
            lines.append(f"{i}. ☐ {a.get('content', '')} _(from {a.get('source', '?')} on {ts})_")

    return [types.TextContent(type="text", text="\n".join(lines))]


async def _get_about_person(args: dict) -> list[types.TextContent]:
    name = args.get("name", "")
    if not name:
        return [types.TextContent(type="text", text="Please provide a name.")]

    if not REDIS_AVAILABLE:
        return [types.TextContent(type="text", text="⚠️ Redis not available.")]

    # Try entity ID first, then fall back to name search
    import re
    slug = re.sub(r"[^\w]+", "-", name.lower()).strip("-")
    entity_id = f"person_{slug}"

    results = search_by_entity(entity_id, top_k=10)
    if not results:
        # Fall back to name-in-content search
        results = search_context(f"person {name}", top_k=10)
        results = [r for r in results if name.lower() in r.get("content", "").lower()]

    if not results:
        return [types.TextContent(type="text", text=f"Nothing in Cortex about {name} yet.")]

    text = _format_results(results, f"👤 Everything about {name}:")
    return [types.TextContent(type="text", text=text)]


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
