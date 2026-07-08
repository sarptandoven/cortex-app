"""Phase 0 provenance substrate: authorship classification and base trust scoring.

Every memory carries an `author_class` — WHO asserted this fact:

  user       the user deliberately authored it (remember_this, a typed note, the
             capture box) or it is a personal-layer memory that passed the
             extractor's authorship gate (which only admits user-attributed
             personal statements in the first place).
  connector  synced from an external account the user connected (Gmail, Notion,
             ...); trustworthy but machine-imported.
  agent      written back by an AI agent (conversation-extracted claims from
             claude/chatgpt/mcp sources, future agent episodes/checkpoints).
  unknown    pre-Phase-0 rows or sources we cannot classify; scored neutrally.

Classification is DETERMINISTIC and derived only from durable record fields
(source, source_url, provenance, layer), so the backfill, the live save path,
and a vault rebuild all converge on the same answer for the same record. That
property is load-bearing: rebuild_index_from_vault must not flip authorship.

`trust_score` is a derived, recomputable ranking signal (NOT user-editable and
NOT vault frontmatter). Phase 0 ships the deterministic base score; Phase 3
layers corroboration/citation adjustments on top via a rescore job.
"""

from __future__ import annotations

from typing import Any

AUTHOR_CLASSES = ("user", "connector", "agent", "unknown")

# Personal layers only ever contain user-attributed statements (enforced by the
# extractor's authorship gate), so their author is the user regardless of the
# transport source label an agent passed.
_PERSONAL_LAYERS = {"preference", "style", "negative"}

# Transport/source labels that mean "an AI agent wrote this into Cortex".
_AGENT_SOURCES = {
    "ai-chat",
    "agent",
    "mcp",
    "claude",
    "chatgpt",
    "claude-code",
    "claude-desktop",
    "cursor",
    "codex",
    "copilot",
    "gemini",
    "openai",
}

# Labels that mean the user deliberately typed/saved the content themselves.
_USER_SOURCES = {
    "capture",
    "note",
    "notes",
    "manual",
    "user",
    "vault",
    "obsidian",
    "remember",
    "journal",
}

BASE_TRUST = {
    "user": 1.0,
    "connector": 0.8,
    "agent": 0.5,
    "unknown": 0.5,
}


def normalize_author_class(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in AUTHOR_CLASSES else "unknown"


def classify_author(
    *,
    source: Any = None,
    source_url: Any = None,
    layer: Any = None,
    provenance: dict[str, Any] | None = None,
    source_account_id: Any = None,
    self_authored: bool = False,
) -> str:
    """Deterministically classify who asserted a memory.

    Precedence (each rule is a strictly stronger signal than the ones below it):
      1. explicit self-authorship (remember_this / capture box)
      2. connector sync (a source account is attached)
      3. deliberate capture provenance (cortex-capture:// self-citation)
      4. personal layer (the authorship gate already proved user attribution)
      5. source label buckets (agent transports vs. user-authored surfaces)
    """
    if self_authored:
        return "user"
    prov = provenance if isinstance(provenance, dict) else {}
    if source_account_id or prov.get("source_account_id"):
        return "connector"
    normalized_url = str(source_url or "").strip().lower()
    # Phase 1 continuity episodes cite their agent session (cortex-session://<id>) the same way
    # deliberate captures cite themselves. The session URL means "an agent checkpointed this",
    # so it classifies as agent BEFORE the capture-URL rule can ever apply.
    if normalized_url.startswith("cortex-session://"):
        return "agent"
    if normalized_url.startswith("cortex-capture://"):
        return "user"
    if str(layer or "").strip().lower() in _PERSONAL_LAYERS:
        return "user"
    label = str(source or "").strip().lower()
    if label in _USER_SOURCES:
        return "user"
    if label in _AGENT_SOURCES:
        return "agent"
    return "unknown"


def base_trust_score(author_class: Any) -> float:
    return BASE_TRUST[normalize_author_class(author_class)]


def normalize_trust_score(value: Any, author_class: Any = "unknown") -> float:
    """Clamp a stored/parsed trust score to [0, 1]; fall back to the class base."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return base_trust_score(author_class)
    if score != score:  # NaN
        return base_trust_score(author_class)
    return min(1.0, max(0.0, score))
