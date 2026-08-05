"""
cortex/ui.py
------------
Streamlit chat UI for Cortex — your universal second brain.

Run: streamlit run ui.py
"""

import os
import streamlit as st
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from instrumentation import setup_tracing
setup_tracing(project_name="cortex")

from anthropic import Anthropic
from redis_store import search_context, get_recent_context

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Cortex",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Styles ────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    .cortex-header { font-size: 2rem; font-weight: 700; margin-bottom: 0; }
    .cortex-sub { color: #888; font-size: 0.95rem; margin-top: 0; margin-bottom: 2rem; }
    .source-card {
        background: #1e1e2e;
        border: 1px solid #313147;
        border-radius: 8px;
        padding: 10px 14px;
        margin: 6px 0;
        font-size: 0.85rem;
    }
    .source-tag {
        display: inline-block;
        background: #313147;
        border-radius: 4px;
        padding: 2px 8px;
        font-size: 0.75rem;
        margin-right: 6px;
        color: #a0a0c0;
    }
    .source-type {
        color: #7c7cff;
        font-weight: 600;
    }
    .recent-item {
        border-left: 2px solid #7c7cff;
        padding-left: 10px;
        margin: 8px 0;
        font-size: 0.82rem;
        color: #ccc;
    }
</style>
""", unsafe_allow_html=True)


# ── Source emoji map ──────────────────────────────────────────────────────────

SOURCE_EMOJI = {
    "claude": "⚡",
    "claude-chat": "⚡",
    "chatgpt": "🤖",
    "gemini": "✨",
    "slack": "💬",
    "imessage": "💬",
    "notion": "📝",
    "chrome": "🌐",
    "safari": "🧭",
    "arc": "🌐",
    "omi": "🎙️",
    "email": "📧",
    "apple-notes": "📓",
    "unknown": "📌",
}

TYPE_COLOR = {
    "insight": "#7c7cff",
    "decision": "#ff7c7c",
    "open-question": "#ffb07c",
    "action": "#7cffb0",
    "summary": "#c0c0c0",
    "person": "#ff7ce0",
}

def source_emoji(source: str) -> str:
    for key, emoji in SOURCE_EMOJI.items():
        if key in source.lower():
            return emoji
    return "📌"

def type_color(note_type: str) -> str:
    return TYPE_COLOR.get(note_type, "#888")


# ── Answer with context ───────────────────────────────────────────────────────

def ask_cortex(question: str, context_chunks: list[dict]) -> str:
    """Ask Claude to answer using retrieved Cortex context."""
    if not context_chunks:
        return "I couldn't find anything relevant in your Cortex. Try capturing more context first using Cmd+C → Cmd+Shift+V."

    # Format context for Claude
    context_text = "\n\n".join([
        f"[{c.get('type', 'note')} from {c.get('source', '?')} on {c.get('timestamp', '')[:10]}]\n{c.get('content', '')}"
        for c in context_chunks
    ])

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1000,
        system="""You are Cortex — a personal AI that knows everything about the user based on their captured context.

You have access to the user's second brain: notes, decisions, insights, and memories captured from their AI chats, Slack, iMessage, and other apps.

Answer questions directly and personally, as if you are their most knowledgeable assistant.
- Reference specific details from the context (dates, sources, exact decisions)
- Be concise but complete
- If the context is partial, say so and answer with what you have
- Never say "based on the provided context" — just answer naturally""",
        messages=[{
            "role": "user",
            "content": f"Context from my second brain:\n\n{context_text}\n\n---\n\nQuestion: {question}"
        }]
    )
    return response.content[0].text


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### 🧠 Cortex")
    st.markdown("*Your universal second brain*")
    st.divider()

    # Recent captures
    st.markdown("**Recent captures**")
    try:
        recent = get_recent_context(since=datetime.now().replace(hour=0, minute=0, second=0), top_k=10)
        if recent:
            for item in recent[:8]:
                ts = item.get("timestamp", "")[:16].replace("T", " ")
                src = source_emoji(item.get("source", ""))
                content_preview = item.get("content", "")[:80]
                st.markdown(f"""<div class="recent-item">{src} <b>{ts}</b><br>{content_preview}...</div>""", unsafe_allow_html=True)
        else:
            st.caption("No captures today yet. Select text anywhere and press Cmd+C → Cmd+Shift+V.")
    except Exception:
        st.caption("Redis not connected — start with: docker start cortex-redis")

    st.divider()

    # Stats
    repo = os.environ.get("CORTEX_REPO", "")
    if repo:
        st.markdown(f"**[📁 GitHub repo](https://github.com/{repo})**")

    st.markdown("**Hotkey:** Select text → `Cmd+C` → `Cmd+Shift+V`")

    # Top K slider
    st.divider()
    top_k = st.slider("Context depth", min_value=3, max_value=15, value=6,
                      help="How many memory chunks to retrieve per question")


# ── Main chat area ────────────────────────────────────────────────────────────

st.markdown('<p class="cortex-header">🧠 Cortex</p>', unsafe_allow_html=True)
st.markdown('<p class="cortex-sub">Ask anything about your life, work, decisions, and conversations.</p>', unsafe_allow_html=True)

# Init chat history
if "messages" not in st.session_state:
    st.session_state.messages = []

# Render chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar="🧠" if msg["role"] == "assistant" else "👤"):
        st.markdown(msg["content"])
        # Show sources if available
        if msg.get("sources"):
            with st.expander(f"📎 {len(msg['sources'])} sources", expanded=False):
                for src in msg["sources"]:
                    note_type = src.get("type", "note")
                    source = src.get("source", "?")
                    ts = src.get("timestamp", "")[:10]
                    content = src.get("content", "")[:150]
                    color = type_color(note_type)
                    emoji = source_emoji(source)
                    st.markdown(f"""<div class="source-card">
                        <span class="source-type" style="color:{color}">● {note_type}</span>
                        <span class="source-tag">{emoji} {source}</span>
                        <span class="source-tag">📅 {ts}</span>
                        <br><br>{content}{"..." if len(src.get("content","")) > 150 else ""}
                    </div>""", unsafe_allow_html=True)

# Chat input
if question := st.chat_input("What do you know about..."):
    # Add user message
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user", avatar="👤"):
        st.markdown(question)

    # Search Cortex + generate answer
    with st.chat_message("assistant", avatar="🧠"):
        with st.spinner("Searching your second brain..."):
            try:
                context_chunks = search_context(question, top_k=top_k)
            except Exception as e:
                context_chunks = []
                st.warning(f"Redis search failed: {e}")

        with st.spinner("Synthesizing answer..."):
            answer = ask_cortex(question, context_chunks)

        st.markdown(answer)

        # Show sources
        if context_chunks:
            with st.expander(f"📎 {len(context_chunks)} sources", expanded=False):
                for src in context_chunks:
                    note_type = src.get("type", "note")
                    source = src.get("source", "?")
                    ts = src.get("timestamp", "")[:10]
                    content = src.get("content", "")[:150]
                    color = type_color(note_type)
                    emoji = source_emoji(source)
                    st.markdown(f"""<div class="source-card">
                        <span class="source-type" style="color:{color}">● {note_type}</span>
                        <span class="source-tag">{emoji} {source}</span>
                        <span class="source-tag">📅 {ts}</span>
                        <br><br>{content}{"..." if len(src.get("content","")) > 150 else ""}
                    </div>""", unsafe_allow_html=True)

    # Save to history
    st.session_state.messages.append({
        "role": "assistant",
        "content": answer,
        "sources": context_chunks,
    })
