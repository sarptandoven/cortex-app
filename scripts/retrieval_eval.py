from __future__ import annotations

import argparse
from email.message import EmailMessage
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.database import init_db
from backend.app.storage import BASELINE_10K_CONNECTOR_IDS, CortexStore, MEMORY_LAYERS


USER_ID = "retrieval-quality"
SEED_TIMESTAMP = "2026-01-01T00:00:00Z"
METRIC_K_VALUES = (1, 3)
NOISY_IMPORT_SOURCES = {"chatgpt", "claude", "slack", "email", "docs", "notion", "cloud-docs", "calendar", "github"}
DIRECT_CONNECTOR_SOURCES = {
    "calendar",
    "gmail",
    "outlook",
    "google-drive",
    "github",
    "jira",
    "linear",
    "notion",
    "obsidian",
    "raindrop",
    "readwise",
    "slack",
    "zotero",
}
DIRECT_CONNECTOR_SOURCE_URL_TOKEN = "direct-connector"
NOISY_SOURCE_URL_FRAGMENTS: dict[str, tuple[str, ...]] = {
    "chatgpt": ("service=chatgpt", "conversation=", "line=", "message=", "excerpt="),
    "claude": ("service=claude", "conversation=", "line=", "message=", "excerpt="),
    "slack": ("service=slack", "channel=", "line=", "message=", "excerpt="),
    "email": ("service=email", "subject=", "line=", "excerpt="),
    "docs": ("line=", "excerpt="),
    "notion": ("service=notion", "page=", "line=", "excerpt="),
    "cloud-docs": ("service=cloud-docs", "provider=", "document=", "line=", "excerpt="),
    "calendar": ("service=calendar", "line=", "event=", "excerpt="),
    "github": ("service=github", "file=", "line=", "row=", "excerpt="),
}
PENDING_LEAK_PHRASE = "Pending-only retrieval memory must not leak into search"
ARCHIVED_REJECTED_LEAK_PHRASE = "Archived rejected retrieval memory must not leak into search"
LOCAL_FILE_SOURCE_URL = "/Users/sarptandoven/Documents/Cortex Beta/Local Citation Plan.md#line=9&excerpt=local-file-citation"
LOCAL_FILE_DUPLICATE_SOURCE_URL = "/Users/sarptandoven/Archive/Cortex Beta/Local Citation Plan.md#line=17&excerpt=duplicate-local-file-citation"
LOCAL_FILE_SAFE_SOURCE_URL_PREFIX = "local-file://Local%20Citation%20Plan.md#line=9&excerpt=local-file-citation"
LOCAL_FILE_DUPLICATE_SAFE_SOURCE_URL_PREFIX = "local-file://Local%20Citation%20Plan.md#line=17&excerpt=duplicate-local-file-citation"
LOCAL_FILE_RAW_FRAGMENTS = ("/Users/sarptandoven", "Documents/Cortex Beta", "Archive/Cortex Beta")
LOCAL_FILE_CITATION_CONTENT = (
    "Local file citation fixture prefers sanitized source locators in shared answer and context outputs."
)
LOCAL_FILE_DUPLICATE_CITATION_CONTENT = (
    "Duplicate local file citation fixture also prefers sanitized source locators in shared answer and context outputs."
)
SECTOR_SCOPE_SOURCE_URL = "cortex-eval://retrieval/sector-scope"
TEMPORAL_VALIDITY_SOURCE_URL = "cortex-eval://retrieval/temporal-validity"
RELATED_MEMORY_SOURCE_URL = "cortex-eval://retrieval/related-memory"
SECTOR_SCOPE_ATLAS_ID = "rq_sector_atlas_release_current"
SECTOR_SCOPE_BOREAL_ID = "rq_sector_boreal_release_current"
TEMPORAL_VALIDITY_CURRENT_ID = "rq_validity_current_support_policy"
TEMPORAL_VALIDITY_EXCLUDED_IDS = (
    "rq_validity_expired_support_policy",
    "rq_validity_future_support_policy",
    "rq_validity_superseded_support_policy",
)
RELATED_MEMORY_PRIMARY_ID = "rq_related_memory_eval_decision"
RELATED_MEMORY_COMPANION_ID = "rq_related_memory_eval_procedure"
RELATED_MEMORY_SATURATION_PRIMARY_ID = "rq_related_memory_saturation_decision"
RELATED_MEMORY_SATURATION_COMPANION_ID = "rq_related_memory_saturation_procedure"
RELATED_MEMORY_SATURATION_DISTRACTOR_ID = "rq_related_memory_saturation_distractor"
EXTERNAL_SPEAKER_PERSONAL_SIGNAL_GUARDS: tuple[dict[str, str], ...] = (
    {"source": "email", "layer": "preference", "phrase": "long onboarding checklists"},
    {"source": "email", "layer": "style", "phrase": "verbose and salesy"},
    {"source": "slack", "layer": "preference", "phrase": "long-form consensus memos"},
)

DIRECT_CONNECTOR_FIXTURES: tuple[dict[str, Any], ...] = (
    {
        "source": "obsidian",
        "project": "Project Vaultline",
        "query": "Project Vaultline Obsidian direct connector local notes",
        "content": "Decision: Project Vaultline Obsidian direct connector keeps local notes as the canonical reviewed memory source.",
        "expected_layer": "decision",
        "expected_phrase": "canonical reviewed memory source",
    },
    {
        "source": "calendar",
        "project": "Project Chronos",
        "query": "Project Chronos calendar direct sync schedule memory",
        "content": "On July 1, 2026, Project Chronos calendar direct sync confirmed the schedule memory for first users.",
        "expected_layer": "episodic",
        "expected_phrase": "schedule memory",
        "occurred_at": "2026-07-01",
    },
    {
        "source": "gmail",
        "project": "Project Mailbox",
        "query": "Project Mailbox Gmail direct sync cited message memory",
        "content": "We decided Project Mailbox Gmail direct sync preserves cited message memory from email threads.",
        "expected_layer": "decision",
        "expected_phrase": "cited message memory",
    },
    {
        "source": "outlook",
        "project": "Project Exchange",
        "query": "Project Exchange Outlook direct sync cited message memory",
        "content": "We decided Project Exchange Outlook direct sync preserves cited message memory from Microsoft Graph mail.",
        "expected_layer": "decision",
        "expected_phrase": "Microsoft Graph mail",
    },
    {
        "source": "google-drive",
        "project": "Project Drive",
        "query": "Project Drive Google Drive exported docs cited memory",
        "content": "We decided Project Drive Google Drive direct sync preserves exported docs as cited memory.",
        "expected_layer": "decision",
        "expected_phrase": "exported docs as cited memory",
    },
    {
        "source": "zotero",
        "project": "Project Papertrail",
        "query": "Project Papertrail Zotero bibliography citation paths",
        "content": "Decision: Project Papertrail Zotero direct sync preserves bibliography citation paths for research memory.",
        "expected_layer": "decision",
        "expected_phrase": "bibliography citation paths",
    },
    {
        "source": "readwise",
        "project": "Project Highlight",
        "query": "Project Highlight source highlight first retrieval answers",
        "content": "I prefer Project Highlight retrieval answers to quote the source highlight before summary text.",
        "expected_layer": "preference",
        "expected_phrase": "source highlight before summary",
    },
    {
        "source": "raindrop",
        "project": "Project Bookmark",
        "query": "Project Bookmark Raindrop bookmark highlights cited memory",
        "content": "Decision: Project Bookmark Raindrop direct sync stores bookmark highlights as cited memory.",
        "expected_layer": "decision",
        "expected_phrase": "bookmark highlights",
    },
    {
        "source": "linear",
        "project": "Project Sprint",
        "query": "Project Sprint Linear triage owner status procedure",
        "content": "Procedure: Project Sprint Linear sync review requires triage, owner check, and status update before planning.",
        "expected_layer": "procedural",
        "expected_phrase": "owner check",
    },
    {
        "source": "jira",
        "project": "Project Ticket",
        "query": "Project Ticket Jira issue URLs before summaries",
        "content": "Decision: Project Ticket Jira direct sync cites issue URLs before generated summaries.",
        "expected_layer": "decision",
        "expected_phrase": "issue URLs",
    },
    {
        "source": "slack",
        "project": "Project Signal",
        "query": "Project Signal Slack compact update bullets source messages",
        "content": "Decision: Project Signal Slack direct sync preserves compact update bullets from source messages.",
        "expected_layer": "semantic",
        "expected_phrase": "compact update bullets",
    },
    {
        "source": "github",
        "project": "Project PullRequest",
        "query": "Project PullRequest GitHub PR source URLs",
        "content": "Decision: Project PullRequest GitHub direct sync preserves pull request source URLs.",
        "expected_layer": "decision",
        "expected_phrase": "pull request source URLs",
    },
    {
        "source": "notion",
        "project": "Project Wiki",
        "query": "Project Wiki Notion pages cited source memory",
        "content": "Decision: Project Wiki Notion direct sync maps pages to cited source memory.",
        "expected_layer": "decision",
        "expected_phrase": "cited source memory",
    },
)


@dataclass(frozen=True)
class SeedMemory:
    id: str
    kind: str
    layer: str
    content: str
    summary: str
    topics: tuple[str, ...]
    occurred_at: str | None = None


@dataclass(frozen=True)
class RetrievalCase:
    name: str
    query: str
    expected_id: str
    expected_layer: str
    expected_phrase: str
    category: str = "focused"
    sector: str | None = None
    include_related: bool = False
    disallowed_ids: tuple[str, ...] = ()
    expected_related_id: str | None = None
    expected_related_layer: str | None = None
    expected_related_phrase: str | None = None
    expected_relationship_kind: str | None = None
    source_url_contains: tuple[str, ...] = ()
    expected_occurred_at: str | None = None


SEED_MEMORIES: tuple[SeedMemory, ...] = (
    SeedMemory(
        id="rq_semantic_project_helio",
        kind="claim",
        layer="semantic",
        content="Project Helio uses a local SQLite vault as the canonical storage contract and source of truth for Cortex.",
        summary="Helio storage contract uses the durable local SQLite database vault.",
        topics=("helio", "storage", "sqlite", "database"),
    ),
    SeedMemory(
        id="rq_episodic_taipei_importer",
        kind="event",
        layer="episodic",
        content="On 2026-04-18, Vamika met Riley to debug the Taipei importer retry path.",
        summary="Taipei importer retry debug session with Vamika and Riley.",
        topics=("taipei", "importer", "meeting"),
        occurred_at="2026-04-18",
    ),
    SeedMemory(
        id="rq_style_short_notes",
        kind="style",
        layer="style",
        content="Writing style: use crisp implementation notes with concise short paragraphs, a spare tone, and no hype.",
        summary="Use concise short implementation notes with a spare tone.",
        topics=("writing", "style"),
    ),
    SeedMemory(
        id="rq_decision_stdlib_eval",
        kind="decision",
        layer="decision",
        content="Decision: keep retrieval evaluation in Python standard library unittest so CI stays dependency-light and lightweight.",
        summary="Retrieval evaluation should stay lightweight in stdlib unittest.",
        topics=("retrieval", "testing", "ci"),
        occurred_at="2026-06-29",
    ),
    SeedMemory(
        id="rq_preference_tradeoffs",
        kind="preference",
        layer="preference",
        content="Preference: when presenting tradeoffs, lead with the recommended option first and then list risks.",
        summary="Put the recommendation first before caveats and risks.",
        topics=("preference", "tradeoffs"),
    ),
    SeedMemory(
        id="rq_negative_fluffy_copy",
        kind="negative",
        layer="negative",
        content="Do not use broad metaphorical intros, marketing prose, or fluffy launch copy in engineering summaries.",
        summary="Avoid fluffy launch copy and marketing prose in engineering summaries.",
        topics=("negative", "writing", "avoid"),
    ),
    SeedMemory(
        id="rq_procedural_release_check",
        kind="procedure",
        layer="procedural",
        content="Procedure: before releasing Cortex, run the backend unittest suite, run ./macos/build.sh, then verify codesign.",
        summary="Release procedure requires backend tests, macOS build, and codesign verification.",
        topics=("procedure", "release", "build"),
    ),
)

DISTRACTOR_MEMORIES: tuple[SeedMemory, ...] = (
    SeedMemory(
        id="rq_distractor_helio_analytics",
        kind="claim",
        layer="semantic",
        content="Project Helio has an analytics mirror for dashboard experiments, but that mirror is not the canonical Cortex storage contract.",
        summary="Helio analytics mirror is experimental and not canonical storage.",
        topics=("helio", "storage", "analytics", "database"),
    ),
    SeedMemory(
        id="rq_distractor_taipei_catering",
        kind="event",
        layer="episodic",
        content="On 2026-04-18, Vamika met Riley to discuss Taipei catering and travel logistics.",
        summary="Taipei logistics discussion with Vamika and Riley.",
        topics=("taipei", "meeting", "logistics"),
        occurred_at="2026-04-18",
    ),
    SeedMemory(
        id="rq_distractor_verbose_style",
        kind="style",
        layer="style",
        content="Writing style draft: a rejected launch page used extended essays, hype-heavy framing, and long paragraphs.",
        summary="Rejected verbose launch-page writing style.",
        topics=("writing", "style", "launch"),
    ),
    SeedMemory(
        id="rq_distractor_eval_demo",
        kind="decision",
        layer="decision",
        content="Decision: keep retrieval demos in a JavaScript web harness for UI smoke tests, separate from the production evaluation suite.",
        summary="Retrieval demos can use a JavaScript UI harness.",
        topics=("retrieval", "testing", "demo"),
        occurred_at="2025-06-29",
    ),
    SeedMemory(
        id="rq_distractor_risk_first",
        kind="preference",
        layer="preference",
        content="Preference draft that was rejected: when presenting launch options, list every risk before the recommendation.",
        summary="Rejected risk-first launch-option preference.",
        topics=("preference", "tradeoffs", "risks"),
    ),
    SeedMemory(
        id="rq_distractor_cartoon_copy",
        kind="negative",
        layer="negative",
        content="Do not use cartoon mascot jokes in release notes for infrastructure migration summaries.",
        summary="Avoid cartoon mascot jokes in migration summaries.",
        topics=("negative", "writing", "avoid"),
    ),
    SeedMemory(
        id="rq_distractor_release_briefing",
        kind="claim",
        layer="semantic",
        content="The release briefing mentions backend tests, macOS builds, and codesign, but it is not the operating procedure.",
        summary="Release briefing is descriptive rather than procedural.",
        topics=("release", "build"),
    ),
)


RETRIEVAL_CASES: tuple[RetrievalCase, ...] = (
    RetrievalCase(
        name="semantic_project_fact",
        query="canonical storage contract Helio",
        expected_id="rq_semantic_project_helio",
        expected_layer="semantic",
        expected_phrase="local SQLite vault",
    ),
    RetrievalCase(
        name="episodic_meeting",
        query="Taipei importer retry meeting",
        expected_id="rq_episodic_taipei_importer",
        expected_layer="episodic",
        expected_phrase="met Riley",
    ),
    RetrievalCase(
        name="style_guidance",
        query="writing style short paragraphs hype",
        expected_id="rq_style_short_notes",
        expected_layer="style",
        expected_phrase="crisp implementation notes",
        category="style_recall",
    ),
    RetrievalCase(
        name="decision_record",
        query="retrieval evaluation stdlib unittest decision",
        expected_id="rq_decision_stdlib_eval",
        expected_layer="decision",
        expected_phrase="dependency-light",
    ),
    RetrievalCase(
        name="preference_tradeoffs",
        query="presenting tradeoffs recommended option risks",
        expected_id="rq_preference_tradeoffs",
        expected_layer="preference",
        expected_phrase="lead with the recommended option",
    ),
    RetrievalCase(
        name="negative_constraint",
        query="fluffy launch copy engineering summaries",
        expected_id="rq_negative_fluffy_copy",
        expected_layer="negative",
        expected_phrase="Do not use",
        category="negative_recall",
    ),
    RetrievalCase(
        name="procedural_release_check",
        query="how release Cortex backend unittest macos build codesign",
        expected_id="rq_procedural_release_check",
        expected_layer="procedural",
        expected_phrase="before releasing Cortex",
        category="procedural_recall",
    ),
    RetrievalCase(
        name="semantic_paraphrase_vault_source",
        query="Helio source truth database vault",
        expected_id="rq_semantic_project_helio",
        expected_layer="semantic",
        expected_phrase="source of truth",
        category="paraphrase",
    ),
    RetrievalCase(
        name="episodic_paraphrase_debug_session",
        query="Taipei importer retry debug session Riley",
        expected_id="rq_episodic_taipei_importer",
        expected_layer="episodic",
        expected_phrase="met Riley",
        category="paraphrase",
    ),
    RetrievalCase(
        name="decision_paraphrase_lightweight_eval",
        query="lightweight evaluation Python standard library CI",
        expected_id="rq_decision_stdlib_eval",
        expected_layer="decision",
        expected_phrase="dependency-light",
        category="paraphrase",
    ),
    RetrievalCase(
        name="temporal_month_decision",
        query="retrieval evaluation June 2026 decision",
        expected_id="rq_decision_stdlib_eval",
        expected_layer="decision",
        expected_phrase="standard library unittest",
        category="temporal_recall",
    ),
    RetrievalCase(
        name="temporal_day_decision",
        query="what happened on June 29 2026 retrieval evaluation",
        expected_id="rq_decision_stdlib_eval",
        expected_layer="decision",
        expected_phrase="dependency-light",
        category="temporal_recall",
    ),
    RetrievalCase(
        name="preference_paraphrase_caveats",
        query="recommendation first caveats tradeoffs risks",
        expected_id="rq_preference_tradeoffs",
        expected_layer="preference",
        expected_phrase="recommended option first",
        category="paraphrase",
    ),
    RetrievalCase(
        name="style_recall_concise_spare_tone",
        query="concise implementation notes spare tone",
        expected_id="rq_style_short_notes",
        expected_layer="style",
        expected_phrase="spare tone",
        category="style_recall",
    ),
    RetrievalCase(
        name="negative_recall_marketing_prose",
        query="avoid marketing prose engineering summaries",
        expected_id="rq_negative_fluffy_copy",
        expected_layer="negative",
        expected_phrase="marketing prose",
        category="negative_recall",
    ),
    RetrievalCase(
        name="negative_recall_metaphorical_intros",
        query="metaphorical intros launch copy",
        expected_id="rq_negative_fluffy_copy",
        expected_layer="negative",
        expected_phrase="metaphorical intros",
        category="negative_recall",
    ),
    RetrievalCase(
        name="sector_scope_atlas_release",
        query="sector eval release checklist codesign cohort",
        expected_id=SECTOR_SCOPE_ATLAS_ID,
        expected_layer="procedural",
        expected_phrase="Project Atlas uses signed Mac build",
        category="sector_scoping",
        sector="Project Atlas",
        disallowed_ids=(SECTOR_SCOPE_BOREAL_ID,),
        source_url_contains=("cortex-eval://retrieval/sector-scope",),
    ),
    RetrievalCase(
        name="sector_scope_boreal_release",
        query="sector eval release checklist web smoke CDN purge",
        expected_id=SECTOR_SCOPE_BOREAL_ID,
        expected_layer="procedural",
        expected_phrase="Project Boreal uses web smoke tests",
        category="sector_scoping",
        sector="Project Boreal",
        disallowed_ids=(SECTOR_SCOPE_ATLAS_ID,),
        source_url_contains=("cortex-eval://retrieval/sector-scope",),
    ),
    RetrievalCase(
        name="temporal_validity_current_policy",
        query="validity eval support policy",
        expected_id=TEMPORAL_VALIDITY_CURRENT_ID,
        expected_layer="decision",
        expected_phrase="Current validity eval support policy",
        category="temporal_validity",
        disallowed_ids=TEMPORAL_VALIDITY_EXCLUDED_IDS,
        source_url_contains=("cortex-eval://retrieval/temporal-validity",),
    ),
    RetrievalCase(
        name="related_memory_companion_surfaces",
        query="related memory eval risk ledger local-first beta path",
        expected_id=RELATED_MEMORY_PRIMARY_ID,
        expected_layer="decision",
        expected_phrase="risk ledger is tight",
        category="related_memory",
        sector="Project Atlas",
        include_related=True,
        expected_related_id=RELATED_MEMORY_COMPANION_ID,
        expected_related_layer="procedural",
        expected_related_phrase="verify update manifest",
        expected_relationship_kind="shared_entity",
        source_url_contains=("cortex-eval://retrieval/related-memory",),
    ),
    RetrievalCase(
        name="related_memory_companion_surfaces_under_saturation",
        query="related memory saturation founder-only launch decision path",
        expected_id=RELATED_MEMORY_SATURATION_PRIMARY_ID,
        expected_layer="decision",
        expected_phrase="founder-only launch decision path",
        category="related_memory",
        sector="Project Atlas",
        include_related=True,
        expected_related_id=RELATED_MEMORY_SATURATION_COMPANION_ID,
        expected_related_layer="procedural",
        expected_related_phrase="checksum verification",
        expected_relationship_kind="shared_entity",
        source_url_contains=("cortex-eval://retrieval/related-memory",),
    ),
)

def seed_representative_memories(store: CortexStore, user_id: str = USER_ID) -> list[dict[str, Any]]:
    timestamp = SEED_TIMESTAMP
    content = "\n".join(memory.content for memory in SEED_MEMORIES)
    store.update_settings(user_id, {"review_new_captures": False, "allow_pending_in_context": True})
    result = store.save_capture(
        user_id=user_id,
        content=content,
        source="retrieval-eval",
        source_url=None,
        title="Retrieval quality seed",
        extracted={
            "_timestamp": timestamp,
            "summary": "Representative memory-layer retrieval quality seed.",
            "records": [
                {
                    "id": memory.id,
                    "kind": memory.kind,
                    "layer": memory.layer,
                    "content": memory.content,
                    "summary": memory.summary,
                    "confidence": "confirmed",
                    "importance": 5,
                    "topics": list(memory.topics),
                    "entity_ids": [],
                    "occurred_at": memory.occurred_at,
                }
                for memory in SEED_MEMORIES
            ],
            "tasks": [],
            "entities": [],
        },
    )
    return result["memories"]


def seed_distractor_memories(store: CortexStore, user_id: str = USER_ID) -> list[dict[str, Any]]:
    timestamp = SEED_TIMESTAMP
    content = "\n".join(memory.content for memory in DISTRACTOR_MEMORIES)
    result = store.save_capture(
        user_id=user_id,
        content=content,
        source="retrieval-eval-distractors",
        source_url=None,
        title="Retrieval quality distractors",
        extracted={
            "_timestamp": timestamp,
            "summary": "Similar but wrong retrieval distractors.",
            "records": [
                {
                    "id": memory.id,
                    "kind": memory.kind,
                    "layer": memory.layer,
                    "content": memory.content,
                    "summary": memory.summary,
                    "confidence": "confirmed",
                    "importance": 2,
                    "topics": list(memory.topics),
                    "entity_ids": [],
                    "occurred_at": memory.occurred_at,
                }
                for memory in DISTRACTOR_MEMORIES
            ],
            "tasks": [],
            "entities": [],
        },
    )
    return result["memories"]


def seed_focused_retrieval_memories(store: CortexStore, user_id: str = USER_ID) -> list[dict[str, Any]]:
    store.update_settings(user_id, {"review_new_captures": False, "allow_pending_in_context": True})
    sector = store.save_capture(
        user_id=user_id,
        content="Sector scoped retrieval eval release checklist fixture.",
        source="retrieval-eval-sector",
        source_url=SECTOR_SCOPE_SOURCE_URL,
        title="Sector scoped retrieval eval",
        extracted={
            "_timestamp": "2026-06-30T09:00:00Z",
            "summary": "Sector scoped retrieval eval release checklist fixture.",
            "records": [
                {
                    "id": SECTOR_SCOPE_ATLAS_ID,
                    "kind": "procedure",
                    "layer": "procedural",
                    "content": "Sector eval release checklist: Project Atlas uses signed Mac build, backend smoke, and cohort guardrail.",
                    "summary": "Project Atlas sector eval release checklist uses Mac build, backend smoke, and cohort guardrail.",
                    "confidence": "confirmed",
                    "importance": 4,
                    "sector": "Project Atlas",
                    "topics": ["sector-eval", "release", "Project Atlas"],
                    "entity_ids": [],
                },
                {
                    "id": SECTOR_SCOPE_BOREAL_ID,
                    "kind": "procedure",
                    "layer": "procedural",
                    "content": "Sector eval release checklist: Project Boreal uses web smoke tests, CDN purge, and metrics review.",
                    "summary": "Project Boreal sector eval release checklist uses web smoke tests and CDN purge.",
                    "confidence": "confirmed",
                    "importance": 4,
                    "sector": "Project Boreal",
                    "topics": ["sector-eval", "release", "Project Boreal"],
                    "entity_ids": [],
                },
            ],
            "tasks": [],
            "entities": [],
        },
    )

    validity = store.save_capture(
        user_id=user_id,
        content="Temporal validity retrieval eval support policy fixture.",
        source="retrieval-eval-validity",
        source_url=TEMPORAL_VALIDITY_SOURCE_URL,
        title="Temporal validity retrieval eval",
        extracted={
            "_timestamp": "2026-06-30T09:01:00Z",
            "summary": "Temporal validity retrieval eval support policy fixture.",
            "records": [
                {
                    "id": TEMPORAL_VALIDITY_CURRENT_ID,
                    "kind": "decision",
                    "layer": "decision",
                    "content": "Current validity eval support policy: First-100 pilots use manual approval and capped beta invites.",
                    "summary": "Current validity eval support policy uses manual approval and capped beta invites.",
                    "confidence": "confirmed",
                    "importance": 3,
                    "valid_from": "2000-01-01T00:00:00+00:00",
                    "topics": ["validity-eval", "support-policy", "first-100"],
                    "entity_ids": [],
                },
                {
                    "id": TEMPORAL_VALIDITY_EXCLUDED_IDS[0],
                    "kind": "decision",
                    "layer": "decision",
                    "content": "Expired validity eval support policy: First-100 pilots use spreadsheet triage only.",
                    "summary": "Expired validity eval support policy uses spreadsheet triage.",
                    "confidence": "confirmed",
                    "importance": 5,
                    "valid_to": "2020-01-01T00:00:00+00:00",
                    "topics": ["validity-eval", "support-policy", "first-100"],
                    "entity_ids": [],
                },
                {
                    "id": TEMPORAL_VALIDITY_EXCLUDED_IDS[1],
                    "kind": "decision",
                    "layer": "decision",
                    "content": "Future validity eval support policy: First-100 pilots use autonomous routing.",
                    "summary": "Future validity eval support policy uses autonomous routing.",
                    "confidence": "confirmed",
                    "importance": 5,
                    "valid_from": "2999-01-01T00:00:00+00:00",
                    "topics": ["validity-eval", "support-policy", "first-100"],
                    "entity_ids": [],
                },
                {
                    "id": TEMPORAL_VALIDITY_EXCLUDED_IDS[2],
                    "kind": "decision",
                    "layer": "decision",
                    "content": "Superseded validity eval support policy: First-100 pilots skip manual approval.",
                    "summary": "Superseded validity eval support policy skips manual approval.",
                    "confidence": "confirmed",
                    "importance": 5,
                    "superseded_by": TEMPORAL_VALIDITY_CURRENT_ID,
                    "topics": ["validity-eval", "support-policy", "first-100"],
                    "entity_ids": [],
                },
            ],
            "tasks": [],
            "entities": [],
        },
    )

    related = store.save_capture(
        user_id=user_id,
        content="Related-memory retrieval eval Project Atlas beta fixture.",
        source="retrieval-eval-related",
        source_url=RELATED_MEMORY_SOURCE_URL,
        title="Related-memory retrieval eval",
        extracted={
            "_timestamp": "2026-06-30T09:02:00Z",
            "summary": "Related-memory retrieval eval Project Atlas beta fixture.",
            "records": [
                {
                    "id": RELATED_MEMORY_PRIMARY_ID,
                    "kind": "decision",
                    "layer": "decision",
                    "content": "Related-memory eval decision: Project Atlas keeps the local-first beta path because the risk ledger is tight.",
                    "summary": "Project Atlas related-memory eval decision keeps local-first beta because risk ledger is tight.",
                    "confidence": "confirmed",
                    "importance": 5,
                    "sector": "Project Atlas",
                    "topics": ["related-memory-eval", "Project Atlas", "beta"],
                    "entity_ids": ["project_atlas_related_eval"],
                },
                {
                    "id": RELATED_MEMORY_COMPANION_ID,
                    "kind": "procedure",
                    "layer": "procedural",
                    "content": "Related-memory eval companion procedure: before Project Atlas beta release, run backend smoke, build the signed app, and verify update manifest.",
                    "summary": "Project Atlas related-memory eval companion procedure covers backend smoke, signed app build, and update manifest.",
                    "confidence": "confirmed",
                    "importance": 3,
                    "sector": "Project Atlas",
                    "topics": ["related-memory-eval", "Project Atlas", "beta"],
                    "entity_ids": ["project_atlas_related_eval"],
                },
                {
                    "id": RELATED_MEMORY_SATURATION_PRIMARY_ID,
                    "kind": "decision",
                    "layer": "decision",
                    "content": "Related-memory saturation founder-only launch decision path keeps Project Atlas local-first until support checks are done.",
                    "summary": "Project Atlas related-memory saturation decision keeps launch local-first.",
                    "confidence": "confirmed",
                    "importance": 5,
                    "sector": "Project Atlas",
                    "topics": ["related-memory-saturation", "Project Atlas", "launch"],
                    "entity_ids": ["project_atlas_related_saturation_eval"],
                },
                {
                    "id": RELATED_MEMORY_SATURATION_COMPANION_ID,
                    "kind": "procedure",
                    "layer": "procedural",
                    "content": "Related-memory saturation companion procedure: run checksum verification, backup creation, support bundle export, and rollback replacement before launch.",
                    "summary": "Project Atlas related-memory saturation companion procedure covers checksum, backup, support bundle, and rollback checks.",
                    "confidence": "confirmed",
                    "importance": 3,
                    "sector": "Project Atlas",
                    "topics": ["related-memory-saturation", "Project Atlas", "launch"],
                    "entity_ids": ["project_atlas_related_saturation_eval"],
                },
                {
                    "id": RELATED_MEMORY_SATURATION_DISTRACTOR_ID,
                    "kind": "claim",
                    "layer": "semantic",
                    "content": "Related-memory saturation founder-only launch decision path appears in a generic duplicate note.",
                    "summary": "Generic duplicate related-memory saturation note.",
                    "confidence": "confirmed",
                    "importance": 4,
                    "sector": "Project Atlas",
                    "topics": ["related-memory-saturation", "Project Atlas", "launch"],
                    "entity_ids": [],
                },
            ],
            "tasks": [],
            "entities": [
                {
                    "id": "project_atlas_related_eval",
                    "kind": "project",
                    "name": "Project Atlas",
                    "aliases": ["Atlas"],
                    "context": "Related-memory retrieval eval fixture.",
                },
                {
                    "id": "project_atlas_related_saturation_eval",
                    "kind": "project",
                    "name": "Project Atlas Related Saturation",
                    "aliases": ["Atlas saturation"],
                    "context": "Related-memory saturation retrieval eval fixture.",
                }
            ],
        },
    )

    return [*sector["memories"], *validity["memories"], *related["memories"]]


def seed_state_leakage_memories(store: CortexStore, user_id: str = USER_ID) -> dict[str, str]:
    store.update_settings(user_id, {"review_new_captures": True, "allow_pending_in_context": False})
    pending = store.save_capture(
        user_id=user_id,
        content=PENDING_LEAK_PHRASE,
        source="retrieval-eval-pending",
        source_url="cortex-eval://retrieval/pending-leak",
        title="Pending retrieval leakage seed",
        extracted={
            "_timestamp": "2026-06-29T10:10:00Z",
            "summary": PENDING_LEAK_PHRASE,
            "records": [
                {
                    "id": "rq_pending_should_not_leak",
                    "kind": "preference",
                    "layer": "preference",
                    "content": PENDING_LEAK_PHRASE,
                    "summary": PENDING_LEAK_PHRASE,
                    "confidence": "unverified",
                    "importance": 5,
                    "topics": ["pending", "retrieval"],
                    "entity_ids": [],
                }
            ],
            "tasks": [],
            "entities": [],
        },
    )

    store.update_settings(user_id, {"review_new_captures": False, "allow_pending_in_context": False})
    archived = store.save_capture(
        user_id=user_id,
        content=ARCHIVED_REJECTED_LEAK_PHRASE,
        source="retrieval-eval-archived",
        source_url="cortex-eval://retrieval/archived-rejected-leak",
        title="Archived rejected retrieval leakage seed",
        extracted={
            "_timestamp": "2026-06-29T10:11:00Z",
            "summary": ARCHIVED_REJECTED_LEAK_PHRASE,
            "records": [
                {
                    "id": "rq_archived_rejected_should_not_leak",
                    "kind": "negative",
                    "layer": "negative",
                    "content": ARCHIVED_REJECTED_LEAK_PHRASE,
                    "summary": ARCHIVED_REJECTED_LEAK_PHRASE,
                    "confidence": "rejected",
                    "importance": 5,
                    "topics": ["archived", "rejected", "retrieval"],
                    "entity_ids": [],
                }
            ],
            "tasks": [],
            "entities": [],
        },
    )
    if not store.archive_capture(user_id, archived["capture_id"]):
        raise AssertionError("Failed to archive rejected retrieval leakage seed")
    return {
        "pending_id": pending["memories"][0]["id"],
        "pending_capture_id": pending["capture_id"],
        "archived_id": archived["memories"][0]["id"],
        "archived_capture_id": archived["capture_id"],
    }


def assert_state_leakage_excluded(store: CortexStore, user_id: str = USER_ID) -> dict[str, Any]:
    seeded = seed_state_leakage_memories(store, user_id)
    leaks: dict[str, list[str]] = {}
    for name, phrase, memory_id in (
        ("pending", PENDING_LEAK_PHRASE, seeded["pending_id"]),
        ("archived_rejected", ARCHIVED_REJECTED_LEAK_PHRASE, seeded["archived_id"]),
    ):
        results = store.search(user_id, phrase, limit=5)
        leaked = [item["id"] for item in results if item["id"] == memory_id or phrase in item["content"]]
        if leaked:
            leaks[name] = leaked
    if leaks:
        raise AssertionError(f"Retrieval leaked pending or archived/rejected memories: {leaks}")
    return seeded


def seed_noisy_import_memories(store: CortexStore, user_id: str = USER_ID) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="cortex-retrieval-import-") as tmp:
        root = Path(tmp)
        _write_eval_chatgpt_export(root / "chatgpt")
        _write_eval_claude_export(root / "claude")
        _write_eval_slack_export(root / "slack")
        _write_eval_external_email(root / "mail")
        _write_eval_docs_export(root / "docs")
        _write_eval_notion_export(root / "Notion Export")
        _write_eval_cloud_docs_export(root / "Google Drive" / "Docs")
        _write_eval_calendar_export(root / "calendar")
        _write_eval_github_export(root / "GitHub" / "Project Quarry")
        store.update_settings(user_id, {"identity_aliases": ["sarpt", "retrieval@example.com"]})
        import_paths = [
            root / "chatgpt",
            root / "claude",
            root / "slack",
            root / "mail",
            root / "docs",
            root / "Notion Export",
            root / "Google Drive",
            root / "calendar",
            root / "GitHub",
        ]
        result = store.import_sources(
            user_id=user_id,
            paths=[str(path) for path in import_paths],
            processing="async",
            max_records=40,
        )
        if result["failed"]:
            raise AssertionError(f"Noisy import eval failed to import records: {result['errors']}")
        if result["queued"]:
            jobs = store.run_due_jobs(user_id, limit=max(50, result["queued"] * 2))
            if jobs["failed"]:
                raise AssertionError(f"Noisy import eval failed queued jobs: {jobs['jobs']}")
    memories = [memory for memory in store.recent(user_id, limit=120) if memory["source"] in NOISY_IMPORT_SOURCES]
    joined = "\n".join(memory["content"] for memory in memories)
    for boilerplate in ("Source:", "Conversation:", "Created:", "--- Messages ---"):
        if boilerplate in joined:
            raise AssertionError(f"Noisy import leaked boilerplate into memory content: {boilerplate}")
    if "splashy launch pages" in joined:
        raise AssertionError("Noisy import treated assistant preference as user memory")
    if "long onboarding checklists" in joined or "verbose and salesy" in joined:
        raise AssertionError("Noisy import treated external email sender preference/style as user memory")
    if "long-form consensus memos" in joined or "glossy launch copy" in joined:
        raise AssertionError("Noisy import treated external Slack/Claude text as user preference or style")
    assert_noisy_import_citations(memories)
    assert_no_duplicate_noisy_import_memories(memories)
    assert_external_speaker_personal_signals_excluded(memories)
    return memories


def assert_noisy_import_citations(memories: list[dict[str, Any]]) -> None:
    missing = [memory["id"] for memory in memories if not memory.get("source_url")]
    if missing:
        raise AssertionError(f"Noisy import memories did not preserve source_url citations: {missing}")

    incomplete: list[dict[str, str]] = []
    for memory in memories:
        source = str(memory.get("source") or "")
        fragments = NOISY_SOURCE_URL_FRAGMENTS.get(source, ())
        source_url = str(memory.get("source_url") or "")
        missing_fragments = [fragment for fragment in fragments if fragment not in source_url]
        if missing_fragments:
            incomplete.append({"id": memory["id"], "source": source, "source_url": source_url, "missing": ",".join(missing_fragments)})
    if incomplete:
        raise AssertionError(f"Noisy import source_url citations are incomplete: {incomplete}")


def assert_no_duplicate_noisy_import_memories(memories: list[dict[str, Any]]) -> None:
    seen: dict[tuple[str, str, str], str] = {}
    duplicates: list[dict[str, str]] = []
    for memory in memories:
        normalized_content = " ".join(str(memory.get("content") or "").casefold().split())
        if not normalized_content:
            continue
        key = (str(memory.get("source") or ""), str(memory.get("layer") or ""), normalized_content)
        existing = seen.get(key)
        if existing:
            duplicates.append({"first_id": existing, "duplicate_id": memory["id"], "source": key[0], "layer": key[1]})
        else:
            seen[key] = memory["id"]
    if duplicates:
        raise AssertionError(f"Noisy import produced obvious duplicate memories: {duplicates}")


def assert_external_speaker_personal_signals_excluded(memories: list[dict[str, Any]]) -> None:
    leaked: list[dict[str, str]] = []
    for guard in EXTERNAL_SPEAKER_PERSONAL_SIGNAL_GUARDS:
        for memory in memories:
            if (
                memory.get("source") == guard["source"]
                and memory.get("layer") == guard["layer"]
                and guard["phrase"] in str(memory.get("content") or "")
            ):
                leaked.append({"id": memory["id"], **guard})
    if leaked:
        raise AssertionError(f"Noisy import treated external speaker text as user personal signals: {leaked}")


def seed_direct_connector_memories(store: CortexStore, user_id: str = USER_ID) -> list[dict[str, Any]]:
    if DIRECT_CONNECTOR_SOURCES != set(BASELINE_10K_CONNECTOR_IDS):
        raise AssertionError(
            "Direct connector retrieval fixtures must match BASELINE_10K_CONNECTOR_IDS: "
            f"fixtures={sorted(DIRECT_CONNECTOR_SOURCES)} baseline={sorted(BASELINE_10K_CONNECTOR_IDS)}"
        )
    store.update_settings(user_id, {"review_new_captures": False, "allow_pending_in_context": True})
    for index, fixture in enumerate(DIRECT_CONNECTOR_FIXTURES, start=1):
        source = str(fixture["source"])
        connection_type = "api-token"
        if source == "obsidian":
            connection_type = "local-folder"
        elif source == "calendar":
            connection_type = "local-file"
        elif source == "gmail":
            connection_type = "oauth-token"
        elif source == "outlook":
            connection_type = "oauth-token"
        elif source == "google-drive":
            connection_type = "oauth-token"
        elif source == "zotero":
            connection_type = "local-api"
        account = store.upsert_source_account(
            user_id,
            source=source,
            account_label=f"{fixture['project']} {source.title()}",
            account_identifier=f"{source}-direct-eval",
            connection_type=connection_type,
            status="connected",
            auth_state="connected",
            policy={"review_required": False, "allow_ai_context": True},
            metadata={"retrieval_eval": True, "direct_connector": True},
        )
        source_url = (
            f"cortex-source://{source}#service={source}"
            f"&record={DIRECT_CONNECTOR_SOURCE_URL_TOKEN}-{index}"
            f"&line=1&excerpt={source}-direct-eval"
        )
        result = store.sync_source_account_records(
            user_id,
            account["id"],
            records=[
                {
                    "external_id": f"{source}-direct-eval-{index}",
                    "title": f"{fixture['project']} direct connector eval",
                    "content": str(fixture["content"]),
                    "source_url": source_url,
                    "captured_at": "2026-07-01T12:00:00Z",
                    "metadata": {
                        "retrieval_eval": True,
                        "source_quality": "canonical",
                        "fixture": DIRECT_CONNECTOR_SOURCE_URL_TOKEN,
                    },
                }
            ],
            processing="sync",
            cursor_name="direct-connector-eval",
            cursor_value=str(index),
            high_water_mark="2026-07-01T12:00:00Z",
            state={"retrieval_eval": True},
        )
        if result["failed"] or result["saved"] != 1:
            raise AssertionError(f"Direct connector eval failed for {source}: {result}")

    memories = [
        memory
        for memory in store.recent(user_id, limit=240)
        if memory["source"] in DIRECT_CONNECTOR_SOURCES
        and DIRECT_CONNECTOR_SOURCE_URL_TOKEN in str(memory.get("source_url") or "")
    ]
    if len(memories) < len(DIRECT_CONNECTOR_FIXTURES):
        raise AssertionError(f"Direct connector eval seeded too few memories: {len(memories)}")

    missing_source_urls = [memory["id"] for memory in memories if not memory.get("source_url")]
    if missing_source_urls:
        raise AssertionError(f"Direct connector memories missed source URLs: {missing_source_urls}")
    return memories


def seed_local_file_citation_memory(store: CortexStore, user_id: str = USER_ID) -> list[dict[str, Any]]:
    store.update_settings(user_id, {"review_new_captures": False, "allow_pending_in_context": True})
    saved = store.save_capture(
        user_id=user_id,
        content=f"{LOCAL_FILE_CITATION_CONTENT}\n{LOCAL_FILE_DUPLICATE_CITATION_CONTENT}",
        source="docs",
        source_url=LOCAL_FILE_SOURCE_URL,
        title="Local citation sanitization fixture",
        extracted={
            "_timestamp": "2026-06-29T10:12:00Z",
            "summary": "Local citation sanitization fixture.",
            "records": [
                {
                    "id": "rq_local_file_citation_sanitized",
                    "kind": "preference",
                    "layer": "preference",
                    "content": LOCAL_FILE_CITATION_CONTENT,
                    "summary": LOCAL_FILE_CITATION_CONTENT,
                    "confidence": "confirmed",
                    "importance": 5,
                    "topics": ["citations", "local-files", "context"],
                    "entity_ids": [],
                }
            ],
            "tasks": [],
            "entities": [],
        },
    )
    duplicate_saved = store.save_capture(
        user_id=user_id,
        content=LOCAL_FILE_DUPLICATE_CITATION_CONTENT,
        source="docs",
        source_url=LOCAL_FILE_DUPLICATE_SOURCE_URL,
        title="Duplicate local citation sanitization fixture",
        extracted={
            "_timestamp": "2026-06-29T10:13:00Z",
            "summary": LOCAL_FILE_DUPLICATE_CITATION_CONTENT,
            "records": [
                {
                    "id": "rq_local_file_citation_duplicate_sanitized",
                    "kind": "preference",
                    "layer": "preference",
                    "content": LOCAL_FILE_DUPLICATE_CITATION_CONTENT,
                    "summary": LOCAL_FILE_DUPLICATE_CITATION_CONTENT,
                    "confidence": "confirmed",
                    "importance": 5,
                    "topics": ["citations", "local-files", "context"],
                    "entity_ids": [],
                }
            ],
            "tasks": [],
            "entities": [],
        },
    )
    return [saved["memories"][0], duplicate_saved["memories"][0]]


def assert_shared_local_file_citations_sanitized(store: CortexStore, user_id: str = USER_ID) -> dict[str, Any]:
    memories = seed_local_file_citation_memory(store, user_id)
    query = "sanitized source locators shared answer context outputs"
    answer = store.answer_query(user_id, query, limit=5)
    context = store.context_pack(user_id, query=query, limit=5)
    serialized_answer = json.dumps(answer, sort_keys=True)
    combined = f"{serialized_answer}\n{context}"

    for raw_source_url in (LOCAL_FILE_SOURCE_URL, LOCAL_FILE_DUPLICATE_SOURCE_URL):
        if raw_source_url in combined:
            raise AssertionError("Shared answer/context output leaked the raw local source_url")
    for fragment in LOCAL_FILE_RAW_FRAGMENTS:
        if fragment in combined:
            raise AssertionError(f"Shared answer/context output leaked local path fragment: {fragment}")
    for expected_prefix in (LOCAL_FILE_SAFE_SOURCE_URL_PREFIX, LOCAL_FILE_DUPLICATE_SAFE_SOURCE_URL_PREFIX):
        if expected_prefix not in serialized_answer:
            raise AssertionError(f"Shared answer output missed sanitized local citation {expected_prefix!r}")
        if expected_prefix not in context:
            raise AssertionError(f"Context pack missed sanitized local citation {expected_prefix!r}")

    citations = {item.get("id"): item for item in answer.get("citations") or []}
    safe_source_urls: list[str] = []
    for memory in memories:
        citation = citations.get(memory["id"])
        if not citation:
            raise AssertionError(f"Shared answer citations missed the local-file citation fixture {memory['id']}")
        source_url = str(citation.get("source_url") or "")
        if not source_url.startswith("local-file://Local%20Citation%20Plan.md#"):
            raise AssertionError(f"Shared answer citation was not sanitized: {citation}")
        if "line=" not in source_url or "excerpt=" not in source_url or "path_hash=" not in source_url:
            raise AssertionError(f"Shared answer citation missed line/excerpt/path hash metadata: {citation}")
        safe_source_urls.append(source_url)
    if len(set(safe_source_urls)) != len(safe_source_urls):
        raise AssertionError(f"Sanitized duplicate local filenames were not disambiguated: {safe_source_urls}")

    return {
        "memory_ids": [memory["id"] for memory in memories],
        "raw_source_urls": [LOCAL_FILE_SOURCE_URL, LOCAL_FILE_DUPLICATE_SOURCE_URL],
        "safe_source_urls": safe_source_urls,
    }


def _write_eval_chatgpt_export(folder: Path) -> None:
    folder.mkdir(parents=True)
    payload = [
        {
            "title": "Project Atlas memory UI",
            "create_time": 1_782_739_200,
            "mapping": {
                "assistant_pref": {
                    "message": {
                        "author": {"role": "assistant"},
                        "create_time": 1_782_739_201,
                        "content": {"parts": ["I prefer splashy launch pages with lots of marketing copy."]},
                    }
                },
                "decision": {
                    "message": {
                        "author": {"role": "user"},
                        "create_time": 1_782_739_202,
                        "content": {"parts": ["We decided Project Atlas should keep the memory UI to Home, Review, Ask, and Connections & Privacy."]},
                    }
                },
                "event": {
                    "message": {
                        "author": {"role": "user"},
                        "create_time": 1_782_739_203,
                        "content": {"parts": ["On June 29, 2026, Project Atlas passed the noisy import retrieval eval."]},
                    }
                },
            },
        }
    ]
    (folder / "conversations.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_eval_claude_export(folder: Path) -> None:
    folder.mkdir(parents=True)
    payload = [
        {
            "name": "Project Lumen retrieval habits",
            "created_at": "2026-06-29T11:00:00Z",
            "chat_messages": [
                {"sender": "human", "text": "I prefer source-trace answers with citation gutters for Project Lumen."},
                {"sender": "assistant", "text": "I prefer glossy launch copy when explaining Project Lumen."},
            ],
        }
    ]
    (folder / "conversations.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_eval_slack_export(folder: Path) -> None:
    channel = folder / "general"
    channel.mkdir(parents=True)
    (folder / "users.json").write_text(
        json.dumps([{"id": "U1", "name": "sarpt"}, {"id": "U2", "name": "dana"}]),
        encoding="utf-8",
    )
    messages = [
        {"type": "message", "user": "U1", "text": "My writing style uses terse Lumen bullets with direct paragraphs.", "ts": "1782739200.0001"},
        {"type": "message", "user": "U1", "text": "Never use ceremonial launch intros for Project Lumen reviews.", "ts": "1782739201.0001"},
        {"type": "message", "user": "U2", "text": "I prefer long-form consensus memos for Project Lumen.", "ts": "1782739202.0001"},
    ]
    (channel / "2026-06-29.json").write_text(json.dumps(messages), encoding="utf-8")


def _write_eval_external_email(folder: Path) -> None:
    folder.mkdir(parents=True)
    message = EmailMessage()
    message["Subject"] = "External advice"
    message["From"] = "Alex Advisor <alex@example.com>"
    message["To"] = "cortex@example.com"
    message["Date"] = "Mon, 29 Jun 2026 10:00:00 +0000"
    message.set_content(
        "I prefer long onboarding checklists for you.\n"
        "My writing style is verbose and salesy.\n"
        "We decided Project Atlas should preserve external email citations."
    )
    (folder / "external.eml").write_bytes(message.as_bytes())


def _write_eval_docs_export(folder: Path) -> None:
    folder.mkdir(parents=True)
    (folder / "Project Lumen Retrieval.md").write_text(
        "# Project Lumen Retrieval\n\nProject Lumen document fixture requires docs retrieval coverage with cited source paths.",
        encoding="utf-8",
    )


def _write_eval_notion_export(folder: Path) -> None:
    folder.mkdir(parents=True)
    (folder / "Project Orion.md").write_text(
        "# Project Orion\n\nWe decided Project Orion Notion imports must show canonical source-id mapping during source review.",
        encoding="utf-8",
    )


def _write_eval_cloud_docs_export(folder: Path) -> None:
    folder.mkdir(parents=True)
    (folder / "Project Nebula.md").write_text(
        "# Project Nebula\n\nProject Nebula cloud docs retrieval coverage requires export-ready source health with cited paths.",
        encoding="utf-8",
    )


def _write_eval_calendar_export(folder: Path) -> None:
    folder.mkdir(parents=True)
    ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:Project Meridian calendar review
DTSTART:20260629T170000Z
DTEND:20260629T173000Z
DESCRIPTION:On June 29, 2026, Project Meridian calendar import review verified cited schedule memory.
END:VEVENT
END:VCALENDAR
"""
    (folder / "project-meridian.ics").write_text(ics, encoding="utf-8")


def _write_eval_github_export(folder: Path) -> None:
    folder.mkdir(parents=True)
    (folder / "issues.csv").write_text(
        "Title,Body\n"
        "Project Quarry source paths,We decided Project Quarry GitHub imports should preserve issue source paths.\n",
        encoding="utf-8",
    )


def _metrics_for_results(expected_id: str, result_ids: list[str], k_values: tuple[int, ...]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for k in k_values:
        top_k = result_ids[:k]
        hits = 1 if expected_id in top_k else 0
        metrics[f"recall@{k}"] = float(hits)
        metrics[f"precision@{k}"] = hits / len(top_k) if top_k else 0.0
    return metrics


def _summarize_metrics(checks: list[dict[str, Any]], k_values: tuple[int, ...]) -> dict[str, Any]:
    def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(items)
        if total == 0:
            return {"case_count": 0, "top1_accuracy": 0.0}

        summary: dict[str, Any] = {
            "case_count": total,
            "top1_accuracy": round(sum(1 for item in items if item["expected_rank"] == 1) / total, 3),
        }
        for k in k_values:
            summary[f"recall@{k}"] = round(sum(item["metrics"][f"recall@{k}"] for item in items) / total, 3)
            summary[f"precision@{k}"] = round(sum(item["metrics"][f"precision@{k}"] for item in items) / total, 3)
        return summary

    categories = sorted({item["category"] for item in checks})
    return {
        "overall": summarize(checks),
        "by_category": {
            category: summarize([item for item in checks if item["category"] == category])
            for category in categories
        },
    }


def _evaluate_case(store: CortexStore, user_id: str, case: RetrievalCase, limit: int) -> dict[str, Any]:
    results = store.search(user_id, case.query, limit=limit, sector=case.sector, include_related=case.include_related)
    if not results:
        raise AssertionError(f"{case.name}: query returned no results: {case.query!r}")

    top = results[0]
    if top["id"] != case.expected_id:
        raise AssertionError(
            f"{case.name}: expected top result {case.expected_id}, got {top['id']} from {[item['id'] for item in results]}"
        )
    if top["layer"] != case.expected_layer:
        raise AssertionError(f"{case.name}: expected layer {case.expected_layer}, got {top['layer']}")
    if case.expected_phrase not in top["content"]:
        raise AssertionError(f"{case.name}: expected phrase {case.expected_phrase!r} in {top['content']!r}")
    if case.sector and top.get("sector") != case.sector:
        raise AssertionError(f"{case.name}: expected sector {case.sector}, got {top.get('sector')}")
    source_url = str(top.get("source_url") or "")
    for expected_fragment in case.source_url_contains:
        if expected_fragment not in source_url:
            raise AssertionError(f"{case.name}: expected source_url fragment {expected_fragment!r} in {source_url!r}")
    if case.expected_occurred_at and top.get("occurred_at") != case.expected_occurred_at:
        raise AssertionError(f"{case.name}: expected occurred_at {case.expected_occurred_at}, got {top.get('occurred_at')}")

    result_ids = [item["id"] for item in results]
    leaked_ids = [memory_id for memory_id in case.disallowed_ids if memory_id in result_ids]
    if leaked_ids:
        raise AssertionError(f"{case.name}: disallowed memories appeared in retrieval results: {leaked_ids}")

    related_check: dict[str, Any] | None = None
    if case.expected_related_id:
        related = next((item for item in results if item["id"] == case.expected_related_id), None)
        if not related:
            raise AssertionError(f"{case.name}: expected related result {case.expected_related_id}, got {result_ids}")
        if case.expected_related_layer and related.get("layer") != case.expected_related_layer:
            raise AssertionError(f"{case.name}: expected related layer {case.expected_related_layer}, got {related.get('layer')}")
        if case.expected_related_phrase and case.expected_related_phrase not in str(related.get("content") or ""):
            raise AssertionError(f"{case.name}: expected related phrase {case.expected_related_phrase!r} in {related.get('content')!r}")
        relationship = related.get("relationship") if isinstance(related.get("relationship"), dict) else {}
        if case.expected_relationship_kind and relationship.get("kind") != case.expected_relationship_kind:
            raise AssertionError(f"{case.name}: expected relationship kind {case.expected_relationship_kind}, got {relationship}")
        if relationship and relationship.get("related_to_id") != case.expected_id:
            raise AssertionError(f"{case.name}: expected related_to_id {case.expected_id}, got {relationship}")
        related_check = {
            "id": related["id"],
            "layer": related.get("layer"),
            "rank": result_ids.index(case.expected_related_id) + 1,
            "relationship": relationship,
        }

    layer_results = store.search(user_id, case.query, limit=limit, layer=case.expected_layer, sector=case.sector)
    layer_ids = [item["id"] for item in layer_results]
    if case.expected_id not in layer_ids:
        raise AssertionError(f"{case.name}: layer-filtered search missed {case.expected_id}: {layer_ids}")

    expected_rank = result_ids.index(case.expected_id) + 1 if case.expected_id in result_ids else None
    return {
        "name": case.name,
        "category": case.category,
        "query": case.query,
        "sector": case.sector,
        "include_related": case.include_related,
        "expected_layer": case.expected_layer,
        "expected_id": case.expected_id,
        "expected_rank": expected_rank,
        "top_result": top["id"],
        "top_layer": top["layer"],
        "top_sector": top.get("sector"),
        "top_source_url": top.get("source_url"),
        "top_occurred_at": top.get("occurred_at"),
        "result_ids": result_ids,
        "disallowed_ids": list(case.disallowed_ids),
        "related_result": related_check,
        "layer_filtered_results": layer_ids,
        "metrics": _metrics_for_results(case.expected_id, result_ids, METRIC_K_VALUES),
    }


def assert_focused_answer_contracts(store: CortexStore, user_id: str = USER_ID) -> dict[str, Any]:
    sector_answer = store.answer_query(
        user_id,
        "sector eval release checklist codesign cohort",
        limit=3,
        sector="Project Atlas",
    )
    sector_ids = [citation["id"] for citation in sector_answer.get("citations") or []]
    if not sector_ids or sector_ids[0] != SECTOR_SCOPE_ATLAS_ID:
        raise AssertionError(f"Sector-scoped Ask returned unexpected citations: {sector_ids}")
    if SECTOR_SCOPE_BOREAL_ID in sector_ids:
        raise AssertionError(f"Sector-scoped Ask leaked the Project Boreal citation: {sector_ids}")
    sector_citation = sector_answer["citations"][0]
    if sector_citation.get("sector") != "Project Atlas":
        raise AssertionError(f"Sector-scoped Ask missed sector metadata: {sector_citation}")
    wrong_sector_citations = [
        citation["id"]
        for citation in sector_answer.get("citations") or []
        if citation.get("sector") != "Project Atlas"
    ]
    if wrong_sector_citations:
        raise AssertionError(f"Sector-scoped Ask returned out-of-sector citations: {wrong_sector_citations}")
    if SECTOR_SCOPE_SOURCE_URL not in str(sector_citation.get("source_url") or ""):
        raise AssertionError(f"Sector-scoped Ask missed source citation: {sector_citation}")
    if "Project Boreal uses web smoke tests" in json.dumps(sector_answer, sort_keys=True):
        raise AssertionError("Sector-scoped Ask leaked the Project Boreal memory into the Project Atlas answer")

    validity_answer = store.answer_query(user_id, "validity eval support policy", limit=4)
    validity_ids = [citation["id"] for citation in validity_answer.get("citations") or []]
    if not validity_ids or validity_ids[0] != TEMPORAL_VALIDITY_CURRENT_ID:
        raise AssertionError(f"Temporal-validity Ask missed the current support policy: {validity_ids}")
    leaked_validity_ids = [memory_id for memory_id in TEMPORAL_VALIDITY_EXCLUDED_IDS if memory_id in validity_ids]
    if leaked_validity_ids:
        raise AssertionError(f"Temporal-validity Ask leaked expired/future/superseded memories: {leaked_validity_ids}")
    current_citation = next(citation for citation in validity_answer["citations"] if citation["id"] == TEMPORAL_VALIDITY_CURRENT_ID)
    if TEMPORAL_VALIDITY_SOURCE_URL not in str(current_citation.get("source_url") or ""):
        raise AssertionError(f"Temporal-validity Ask missed source citation: {current_citation}")

    related_answer = store.answer_query(
        user_id,
        "related memory eval risk ledger local-first beta path",
        limit=2,
        sector="Project Atlas",
    )
    related_ids = [citation["id"] for citation in related_answer.get("citations") or []]
    if not related_ids or related_ids[0] != RELATED_MEMORY_PRIMARY_ID:
        raise AssertionError(f"Related-memory Ask missed the primary decision: {related_ids}")
    if RELATED_MEMORY_COMPANION_ID not in related_ids:
        raise AssertionError(f"Related-memory Ask missed the companion memory: {related_ids}")
    companion = next(citation for citation in related_answer["citations"] if citation["id"] == RELATED_MEMORY_COMPANION_ID)
    relationship = companion.get("relationship") if isinstance(companion.get("relationship"), dict) else {}
    if relationship.get("kind") != "shared_entity" or relationship.get("related_to_id") != RELATED_MEMORY_PRIMARY_ID:
        raise AssertionError(f"Related-memory Ask missed relationship metadata: {relationship}")
    if RELATED_MEMORY_SOURCE_URL not in str(companion.get("source_url") or ""):
        raise AssertionError(f"Related-memory Ask missed source citation: {companion}")

    store.update_settings(user_id, {"review_new_captures": False, "allow_pending_in_context": True})
    store.save_capture(
        user_id=user_id,
        content="Ask eval source-backed ranking points at a generic uncited memo.",
        source="eval-manual",
        source_url=None,
        title="Uncited Ask eval distractor",
        extracted={
            "_timestamp": "2026-07-01T10:00:00Z",
            "summary": "Uncited Ask eval distractor.",
            "records": [
                {
                    "id": "rq_ask_source_backed_uncited",
                    "kind": "claim",
                    "layer": "semantic",
                    "content": "Ask eval source-backed ranking points at a generic uncited memo.",
                    "summary": "Generic uncited Ask eval memo.",
                    "confidence": "confirmed",
                    "importance": 5,
                    "topics": ["ask", "source-backed", "ranking"],
                    "entity_ids": [],
                }
            ],
            "tasks": [],
            "entities": [],
        },
    )
    store.save_capture(
        user_id=user_id,
        content="Ask eval source-backed ranking points at the canonical connected source.",
        source="github",
        source_url="cortex-source://github#service=github&file=issues.json&line=34&excerpt=ask-source-backed",
        title="Cited Ask eval source",
        extracted={
            "_timestamp": "2026-07-01T10:00:00Z",
            "summary": "Cited Ask eval source.",
            "records": [
                {
                    "id": "rq_ask_source_backed_cited",
                    "kind": "claim",
                    "layer": "semantic",
                    "content": "Ask eval source-backed ranking points at the canonical connected source.",
                    "summary": "Canonical cited Ask eval source.",
                    "confidence": "confirmed",
                    "importance": 1,
                    "topics": ["ask", "source-backed", "ranking"],
                    "entity_ids": [],
                }
            ],
            "tasks": [],
            "entities": [],
        },
    )
    source_backed_answer = store.answer_query(user_id, "ask eval source-backed ranking", limit=2)
    source_backed_ids = [citation["id"] for citation in source_backed_answer.get("citations") or []]
    if not source_backed_ids or source_backed_ids[0] != "rq_ask_source_backed_cited":
        raise AssertionError(f"Ask source-backed rerank missed cited source: {source_backed_ids}")
    if "rq_ask_source_backed_uncited" in source_backed_ids:
        raise AssertionError(f"Ask source-backed rerank cited an uncited distractor: {source_backed_ids}")
    missing_source_urls = [citation["id"] for citation in source_backed_answer.get("citations") or [] if not citation.get("source_url")]
    if missing_source_urls:
        raise AssertionError(f"Ask source-backed rerank emitted uncited citations: {missing_source_urls}")

    claim_conflict_fixtures = [
        (
            "slack",
            "cortex-source://slack#service=slack&channel=C456&message=1782900000000200&line=1&excerpt=eval-icarus-email",
            "2026-06-02T00:00:00+00:00",
            "rq_ask_claim_conflict_original",
            "Project Icarus launch channel should use email according to the original GTM thread.",
        ),
        (
            "github",
            "cortex-source://github#service=github&repository=cortex&file=issues.json&line=45&excerpt=eval-icarus-slack",
            "2026-06-16T00:00:00+00:00",
            "rq_ask_claim_conflict_updated",
            "Project Icarus launch channel now uses Slack because tester replies need fast triage.",
        ),
    ]
    for source, source_url, timestamp, record_id, content in claim_conflict_fixtures:
        store.save_capture(
            user_id=user_id,
            content=content,
            source=source,
            source_url=source_url,
            title=f"{source} Project Icarus launch channel",
            extracted={
                "_timestamp": timestamp,
                "summary": content,
                "records": [
                    {
                        "id": record_id,
                        "kind": "decision",
                        "layer": "decision",
                        "content": content,
                        "summary": content,
                        "confidence": "confirmed",
                        "importance": 4,
                        "sector": "Project Icarus",
                        "topics": ["project-icarus", "launch", "channel"],
                        "entity_ids": ["project_icarus"],
                        "occurred_at": timestamp,
                    }
                ],
                "tasks": [],
                "entities": [
                    {
                        "id": "project_icarus",
                        "kind": "project",
                        "name": "Project Icarus",
                        "aliases": ["Icarus"],
                    }
                ],
            },
        )
    claim_conflict_answer = store.answer_query(user_id, "Project Icarus launch channel", limit=4, sector="Project Icarus")
    claim_conflicts = claim_conflict_answer.get("conflicts") or []
    if claim_conflict_answer.get("status") != "conflicted" or not claim_conflicts:
        raise AssertionError(f"Ask claim-conflict contract missed conflicted status: {claim_conflict_answer}")
    first_claim_conflict = claim_conflicts[0]
    if first_claim_conflict.get("type") != "claim_conflict":
        raise AssertionError(f"Ask claim-conflict contract returned wrong conflict type: {first_claim_conflict}")
    if first_claim_conflict.get("claim_key") != "launch channel":
        raise AssertionError(f"Ask claim-conflict contract missed claim key: {first_claim_conflict}")
    if first_claim_conflict.get("primary_id") != "rq_ask_claim_conflict_updated":
        raise AssertionError(f"Ask claim-conflict contract missed newer/current primary source: {first_claim_conflict}")
    if first_claim_conflict.get("primary_claim") != "slack" or first_claim_conflict.get("conflicting_claim") != "email":
        raise AssertionError(f"Ask claim-conflict contract missed disagreeing claims: {first_claim_conflict}")

    store.save_capture(
        user_id=user_id,
        content="Project Atlas payroll reimbursement policy is approved for local beta expenses.",
        source="github",
        source_url="cortex-source://github#service=github&file=issues.json&line=88&excerpt=atlas-payroll-policy",
        title="Atlas payroll reimbursement policy",
        extracted={
            "_timestamp": "2026-07-01T10:10:00Z",
            "summary": "Atlas payroll reimbursement policy.",
            "records": [
                {
                    "id": "rq_ask_low_confidence_related_cited",
                    "kind": "claim",
                    "layer": "semantic",
                    "content": "Project Atlas payroll reimbursement policy is approved for local beta expenses.",
                    "summary": "Atlas payroll reimbursement policy is approved.",
                    "confidence": "confirmed",
                    "importance": 4,
                    "sector": "Project Atlas",
                    "topics": ["project-atlas", "payroll", "reimbursement"],
                    "entity_ids": ["project_atlas"],
                }
            ],
            "tasks": [],
            "entities": [
                {
                    "id": "project_atlas",
                    "kind": "project",
                    "name": "Project Atlas",
                    "aliases": ["Atlas"],
                }
            ],
        },
    )
    low_confidence_answer = store.answer_query(
        user_id,
        "Project Atlas payroll reimbursement owner",
        limit=3,
        sector="Project Atlas",
    )
    low_confidence_evidence = low_confidence_answer.get("evidence") if isinstance(low_confidence_answer.get("evidence"), dict) else {}
    if low_confidence_answer.get("status") != "low_confidence":
        raise AssertionError(f"Ask low-confidence contract missed low_confidence status: {low_confidence_answer}")
    if low_confidence_evidence.get("missing_fields") != ["owner"]:
        raise AssertionError(f"Ask low-confidence contract missed owner evidence gap: {low_confidence_evidence}")
    low_confidence_ids = [citation["id"] for citation in low_confidence_answer.get("citations") or []]
    if not low_confidence_ids or low_confidence_ids[0] != "rq_ask_low_confidence_related_cited":
        raise AssertionError(f"Ask low-confidence contract missed related citation: {low_confidence_ids}")

    for label, answer in (
        ("sector_scoping", sector_answer),
        ("temporal_validity", validity_answer),
        ("related_memory", related_answer),
        ("source_backed_rerank", source_backed_answer),
        ("claim_conflict", claim_conflict_answer),
        ("low_confidence", low_confidence_answer),
    ):
        missing_citations = [citation["id"] for citation in answer.get("citations") or [] if not citation.get("source_url")]
        if missing_citations:
            raise AssertionError(f"{label}: Ask citations missed source_url values: {missing_citations}")

    return {
        "sector_scoping": {
            "sector": "Project Atlas",
            "citation_ids": sector_ids,
            "source_url": sector_citation.get("source_url"),
        },
        "temporal_validity": {
            "citation_ids": validity_ids,
            "excluded_ids": list(TEMPORAL_VALIDITY_EXCLUDED_IDS),
            "source_url": current_citation.get("source_url"),
        },
        "related_memory": {
            "citation_ids": related_ids,
            "related_id": RELATED_MEMORY_COMPANION_ID,
            "relationship": relationship,
            "source_url": companion.get("source_url"),
        },
        "source_backed_rerank": {
            "citation_ids": source_backed_ids,
        },
        "claim_conflict": {
            "status": claim_conflict_answer.get("status"),
            "citation_ids": [citation["id"] for citation in claim_conflict_answer.get("citations") or []],
            "conflict": first_claim_conflict,
        },
        "low_confidence": {
            "status": low_confidence_answer.get("status"),
            "citation_ids": low_confidence_ids,
            "evidence": low_confidence_evidence,
        },
    }


def assert_no_evidence_answer_abstains(store: CortexStore, user_id: str = USER_ID) -> dict[str, Any]:
    store.update_settings(user_id, {"review_new_captures": False, "allow_pending_in_context": True})
    store.save_capture(
        user_id=user_id,
        content="Project Atlas payroll reimbursement owner appears only in an uncited scratch note.",
        source="eval-manual",
        source_url=None,
        title="Uncited no-evidence eval source",
        extracted={
            "_timestamp": "2026-07-01T10:05:00Z",
            "summary": "Uncited no-evidence eval source.",
            "records": [
                {
                    "id": "rq_ask_no_evidence_uncited_only",
                    "kind": "claim",
                    "layer": "semantic",
                    "content": "Project Atlas payroll reimbursement owner appears only in an uncited scratch note.",
                    "summary": "Project Atlas payroll reimbursement owner uncited scratch note.",
                    "confidence": "confirmed",
                    "importance": 5,
                    "topics": ["Project Atlas", "payroll", "reimbursement"],
                    "entity_ids": [],
                }
            ],
            "tasks": [],
            "entities": [],
        },
    )
    answer = store.answer_query(user_id, "Project Atlas payroll reimbursement owner", limit=3)
    result_ids = [item["id"] for item in answer.get("results") or []]
    citation_ids = [citation["id"] for citation in answer.get("citations") or []]
    if "rq_ask_no_evidence_uncited_only" not in result_ids:
        raise AssertionError(f"No-evidence Ask regression did not surface the uncited match in results: {result_ids}")
    if citation_ids:
        raise AssertionError(f"No-evidence Ask returned uncited matches as citations: {citation_ids}")
    if "did not find a cited item" not in str(answer.get("answer") or ""):
        raise AssertionError(f"No-evidence Ask did not abstain with missing-citation copy: {answer}")
    return {
        "query": "Project Atlas payroll reimbursement owner",
        "uncited_result_ids": result_ids,
        "citation_ids": citation_ids,
        "abstained": True,
    }


def assert_direct_connector_answer_contracts(
    store: CortexStore,
    direct_connector_memories: list[dict[str, Any]],
    user_id: str = USER_ID,
) -> dict[str, Any]:
    memory_by_source: dict[str, dict[str, Any]] = {}
    for fixture in DIRECT_CONNECTOR_FIXTURES:
        source = str(fixture["source"])
        phrase = str(fixture["expected_phrase"])
        layer = str(fixture["expected_layer"])
        match = next(
            (
                memory
                for memory in direct_connector_memories
                if memory["source"] == source and memory["layer"] == layer and phrase in str(memory.get("content") or "")
            ),
            None,
        )
        if not match:
            raise AssertionError(f"Direct connector Ask contract missing seeded memory for {source}")
        memory_by_source[source] = match

    contracts: dict[str, Any] = {}
    for index, fixture in enumerate(DIRECT_CONNECTOR_FIXTURES, start=1):
        source = str(fixture["source"])
        expected = memory_by_source[source]
        expected_external_id = f"{source}-direct-eval-{index}"
        answer = store.answer_query(user_id, str(fixture["query"]), limit=3)
        citations = answer.get("citations") or []
        if not citations:
            raise AssertionError(f"Direct connector Ask returned no citations for {source}")
        citation_ids = [citation["id"] for citation in citations]
        if citations[0]["id"] != expected["id"]:
            raise AssertionError(f"Direct connector Ask for {source} expected top citation {expected['id']}, got {citation_ids}")
        citation = citations[0]
        source_url = str(citation.get("source_url") or "")
        if citation.get("source") != source:
            raise AssertionError(f"Direct connector Ask citation had wrong source for {source}: {citation}")
        if DIRECT_CONNECTOR_SOURCE_URL_TOKEN not in source_url or f"service={source}" not in source_url:
            raise AssertionError(f"Direct connector Ask citation missed source URL details for {source}: {citation}")
        if "line=1" not in source_url or "excerpt=" not in source_url:
            raise AssertionError(f"Direct connector Ask citation missed granular locator for {source}: {citation}")
        if str(citation.get("line_start") or "") != "1":
            raise AssertionError(f"Direct connector Ask citation missed structured line metadata for {source}: {citation}")
        if str(citation.get("source_excerpt") or "") != f"{source}-direct-eval":
            raise AssertionError(f"Direct connector Ask citation missed structured excerpt metadata for {source}: {citation}")
        if not citation.get("source_account_id"):
            raise AssertionError(f"Direct connector Ask citation missed source_account_id for {source}: {citation}")
        if citation.get("external_id") != expected_external_id or citation.get("source_record_id") != expected_external_id:
            raise AssertionError(f"Direct connector Ask citation missed source record id for {source}: {citation}")
        if citation.get("source_type") != "service":
            raise AssertionError(f"Direct connector Ask citation had wrong source_type for {source}: {citation}")
        contracts[source] = {
            "citation_ids": citation_ids,
            "source_account_id": citation.get("source_account_id"),
            "source_record_id": citation.get("source_record_id"),
            "source_url": source_url,
        }
    return {
        "sources": sorted(contracts),
        "source_count": len(contracts),
        "contracts": contracts,
    }


def assert_automatic_connector_account_scope_contract(store: CortexStore, user_id: str = USER_ID) -> dict[str, Any]:
    query = "Project Meridian automatic connector review bypass source ledger"
    expected_phrase = "automatic connector review bypass source ledger"
    trusted_external_id = "auto-account-scope-trusted"
    blocked_external_id = "auto-account-scope-blocked"
    trusted_content = (
        "Decision: Project Meridian automatic connector review bypass source ledger stays retrievable "
        "by cited Ask and search when global pending context is disabled."
    )
    blocked_content = (
        "Decision: Project Meridian automatic connector review bypass source ledger should not leak "
        "from the review-required Gmail account while pending."
    )

    store.update_settings(user_id, {"review_new_captures": True, "allow_pending_in_context": False})
    trusted_account = store.upsert_source_account(
        user_id,
        source="slack",
        account_id="sacct_retrieval_eval_auto_connector_trusted",
        account_label="Project Meridian Slack automatic eval",
        account_identifier="project-meridian-slack-auto-eval",
        connection_type="api-token",
        status="connected",
        auth_state="connected",
        policy={"review_required": False, "allow_ai_context": True},
        metadata={"retrieval_eval": True, "automatic_connector_scope": True},
    )
    blocked_account = store.upsert_source_account(
        user_id,
        source="gmail",
        account_id="sacct_retrieval_eval_auto_connector_review_required",
        account_label="Project Meridian Gmail review-required eval",
        account_identifier="project-meridian-gmail-review-required-eval",
        connection_type="oauth-token",
        status="connected",
        auth_state="connected",
        policy={"review_required": True, "allow_ai_context": True},
        metadata={"retrieval_eval": True, "automatic_connector_scope": True},
    )

    trusted_sync = store.sync_source_account_records(
        user_id,
        trusted_account["id"],
        records=[
            {
                "external_id": trusted_external_id,
                "title": "Project Meridian automatic connector account scope",
                "content": trusted_content,
                "source_url": (
                    "cortex-source://slack#service=slack&channel=CACCT"
                    "&message=1782920000000100&line=1&excerpt=auto-account-review-bypass"
                ),
                "captured_at": "2026-07-01T12:20:00Z",
                "metadata": {
                    "retrieval_eval": True,
                    "line_start": 1,
                    "record_scope": "message",
                    "source_quality": "canonical",
                },
            }
        ],
        processing="sync",
        cursor_name="automatic-connector-account-scope",
        cursor_value="trusted",
        high_water_mark="2026-07-01T12:20:00Z",
        state={"retrieval_eval": True, "automatic_connector_scope": True},
    )
    blocked_sync = store.sync_source_account_records(
        user_id,
        blocked_account["id"],
        records=[
            {
                "external_id": blocked_external_id,
                "title": "Project Meridian review-required account distractor",
                "content": blocked_content,
                "source_url": (
                    "cortex-source://gmail#service=gmail&subject=Project%20Meridian%20Review"
                    "&line=1&excerpt=auto-account-review-required"
                ),
                "captured_at": "2026-07-01T12:21:00Z",
                "metadata": {
                    "retrieval_eval": True,
                    "line_start": 1,
                    "record_scope": "message",
                    "source_quality": "review_required",
                },
            }
        ],
        processing="sync",
        cursor_name="automatic-connector-account-scope",
        cursor_value="review-required",
        high_water_mark="2026-07-01T12:21:00Z",
        state={"retrieval_eval": True, "automatic_connector_scope": True},
    )
    for label, sync_result in (("trusted", trusted_sync), ("review_required", blocked_sync)):
        if sync_result["failed"] or sync_result["saved"] != 1:
            raise AssertionError(f"Automatic connector account-scope sync failed for {label}: {sync_result}")
        record_counts = [int(record.get("memories") or 0) for record in sync_result.get("records") or []]
        if not record_counts or record_counts[0] < 1:
            raise AssertionError(f"Automatic connector account-scope sync produced no memories for {label}: {sync_result}")

    trusted_capture_id = trusted_sync["capture_ids"][0]
    blocked_capture_id = blocked_sync["capture_ids"][0]
    settings = store.settings(user_id)
    if settings.get("allow_pending_in_context") is not False:
        raise AssertionError(f"Automatic connector account-scope eval requires global pending context disabled: {settings}")
    pending_capture_ids = {capture["id"] for capture in store.inbox(user_id, limit=20)}
    for capture_id in (trusted_capture_id, blocked_capture_id):
        if capture_id not in pending_capture_ids:
            raise AssertionError(f"Automatic connector account-scope capture was not pending as expected: {capture_id}")

    scoped_results = store.search(user_id, query, limit=4, source_account_id=trusted_account["id"])
    if not scoped_results:
        raise AssertionError("Automatic connector account-scoped search returned no results")
    top = scoped_results[0]
    scoped_ids = [item["id"] for item in scoped_results]
    if expected_phrase not in str(top.get("content") or ""):
        raise AssertionError(f"Automatic connector account-scoped search missed expected content: {top}")
    provenance = top.get("provenance") if isinstance(top.get("provenance"), dict) else {}
    source_account_policy = provenance.get("source_account_policy") if isinstance(provenance.get("source_account_policy"), dict) else {}
    if provenance.get("source_account_id") != trusted_account["id"]:
        raise AssertionError(f"Automatic connector account-scoped search missed source account provenance: {top}")
    if provenance.get("external_id") != trusted_external_id:
        raise AssertionError(f"Automatic connector account-scoped search missed external_id provenance: {top}")
    if source_account_policy.get("review_required") is not False:
        raise AssertionError(f"Automatic connector account-scoped search missed review bypass policy: {top}")
    top_source_url = str(top.get("source_url") or "")
    if "line=1" not in top_source_url or "excerpt=auto-account-review-bypass" not in top_source_url:
        raise AssertionError(f"Automatic connector account-scoped search missed granular source URL: {top}")

    answer = store.answer_query(user_id, query, limit=4, source_account_id=trusted_account["id"])
    citations = answer.get("citations") or []
    citation_ids = [citation["id"] for citation in citations]
    if answer.get("status") != "cited" or not citations:
        raise AssertionError(f"Automatic connector account-scoped Ask did not return a cited answer: {answer}")
    citation = citations[0]
    if citation["id"] != top["id"]:
        raise AssertionError(f"Automatic connector account-scoped Ask disagreed with search top result: {citation_ids} vs {top['id']}")
    if citation.get("source_account_id") != trusted_account["id"]:
        raise AssertionError(f"Automatic connector account-scoped Ask missed source account citation metadata: {citation}")
    if citation.get("source_record_id") != trusted_external_id or citation.get("external_id") != trusted_external_id:
        raise AssertionError(f"Automatic connector account-scoped Ask missed source record metadata: {citation}")
    if citation.get("source_type") != "service" or not citation.get("source_url"):
        raise AssertionError(f"Automatic connector account-scoped Ask missed service citation details: {citation}")
    if str(citation.get("line_start") or "") != "1" or citation.get("record_scope") != "message":
        raise AssertionError(f"Automatic connector account-scoped Ask missed structured locator metadata: {citation}")

    blocked_results = store.search(user_id, query, limit=4, source_account_id=blocked_account["id"])
    if blocked_results:
        raise AssertionError(f"Review-required pending connector account leaked into search: {[item['id'] for item in blocked_results]}")
    blocked_answer = store.answer_query(user_id, query, limit=4, source_account_id=blocked_account["id"])
    blocked_citation_ids = [citation["id"] for citation in blocked_answer.get("citations") or []]
    if blocked_citation_ids:
        raise AssertionError(f"Review-required pending connector account leaked into Ask citations: {blocked_citation_ids}")

    broad_results = store.search(user_id, query, limit=6)
    broad_ids = [item["id"] for item in broad_results]
    if top["id"] not in broad_ids:
        raise AssertionError(f"Automatic connector review-bypass memory was not retrievable without source-account scope: {broad_ids}")
    if any(blocked_external_id == (item.get("provenance") or {}).get("external_id") for item in broad_results):
        raise AssertionError(f"Review-required pending connector account leaked into broad search: {broad_ids}")

    search_check = {
        "name": "automatic_connector_account_scope_review_bypass",
        "category": "direct_connector_account_policy",
        "query": query,
        "sector": None,
        "include_related": False,
        "expected_layer": top["layer"],
        "expected_id": top["id"],
        "expected_rank": 1,
        "top_result": top["id"],
        "top_layer": top["layer"],
        "top_sector": top.get("sector"),
        "top_source_url": top.get("source_url"),
        "top_occurred_at": top.get("occurred_at"),
        "result_ids": scoped_ids,
        "disallowed_ids": [],
        "related_result": None,
        "layer_filtered_results": scoped_ids,
        "metrics": _metrics_for_results(top["id"], scoped_ids, METRIC_K_VALUES),
    }
    return {
        "trusted_account_id": trusted_account["id"],
        "blocked_account_id": blocked_account["id"],
        "trusted_capture_id": trusted_capture_id,
        "blocked_capture_id": blocked_capture_id,
        "pending_capture_ids": sorted(pending_capture_ids),
        "search_result_ids": scoped_ids,
        "ask_status": answer.get("status"),
        "ask_citation_ids": citation_ids,
        "broad_result_ids": broad_ids,
        "blocked_search_result_ids": [item["id"] for item in blocked_results],
        "blocked_ask_status": blocked_answer.get("status"),
        "check": search_check,
    }


def assert_source_backed_lexical_fallback_ranking(
    store: CortexStore,
    user_id: str = USER_ID,
    limit: int = 3,
) -> dict[str, Any]:
    expected_id = "rq_source_backed_lexical_fallback_cited"
    noisy_id = "rq_source_backed_lexical_fallback_noisy"
    query = "Project Beacon citation summary fallbacktoken"
    store.update_settings(
        user_id,
        {
            "review_new_captures": False,
            "allow_pending_in_context": True,
            "source_policies": {"github": {"mode": "trusted"}},
        },
    )
    store.save_capture(
        user_id=user_id,
        content="Project Beacon citation summary noisy fallback seed.",
        source="chatgpt",
        source_url=None,
        title="Noisy generated summary fallback seed",
        extracted={
            "_timestamp": "2026-07-01T10:05:00Z",
            "summary": "Noisy generated summary fallback seed.",
            "records": [
                {
                    "id": noisy_id,
                    "kind": "claim",
                    "layer": "semantic",
                    "content": (
                        "Project Beacon citation summary repeats boilerplate. Project Beacon citation summary "
                        "appears again in a generated service digest without source evidence."
                    ),
                    "summary": "Project Beacon citation summary generated service digest.",
                    "confidence": "confirmed",
                    "importance": 5,
                    "topics": ["beacon", "citation", "summary"],
                    "entity_ids": [],
                }
            ],
            "tasks": [],
            "entities": [],
        },
    )
    store.save_capture(
        user_id=user_id,
        content="Project Beacon citation summary canonical fallback seed.",
        source="github",
        source_url="cortex-source://github#service=github&repository=cortex&file=issues.json&line=99&excerpt=beacon-source-truth",
        title="Canonical source fallback seed",
        extracted={
            "_timestamp": "2026-07-01T10:05:00Z",
            "summary": "Canonical source fallback seed.",
            "records": [
                {
                    "id": expected_id,
                    "kind": "claim",
                    "layer": "semantic",
                    "content": "Project Beacon citation summary comes from the signed GitHub issue source evidence.",
                    "summary": "Project Beacon citation summary GitHub issue.",
                    "confidence": "confirmed",
                    "importance": 1,
                    "topics": ["beacon", "citation", "summary"],
                    "entity_ids": [],
                    "metadata": {"source_quality": "canonical", "verified": True},
                }
            ],
            "tasks": [],
            "entities": [],
        },
    )

    diagnostics: dict[str, Any] = {}
    original_vector_ready = store._vector_ready
    store._vector_ready = lambda conn: False
    try:
        results = store.search(user_id, query, limit=limit, _diagnostics=diagnostics)
    finally:
        store._vector_ready = original_vector_ready

    if "lexical_fallback" not in diagnostics.get("used_modes", []):
        raise AssertionError(f"Source-backed fallback case did not exercise lexical fallback: {diagnostics}")
    if not results:
        raise AssertionError("Source-backed fallback case returned no results")
    top = results[0]
    result_ids = [item["id"] for item in results]
    if top["id"] != expected_id:
        raise AssertionError(f"Source-backed fallback expected {expected_id}, got {result_ids}")
    source_url = str(top.get("source_url") or "")
    if "line=99" not in source_url or "excerpt=beacon-source-truth" not in source_url:
        raise AssertionError(f"Source-backed fallback missed granular citation: {top}")
    if top.get("source_type") != "service":
        raise AssertionError(f"Source-backed fallback missed service source type: {top}")

    return {
        "name": "source_backed_lexical_fallback",
        "category": "source_backed_ranking",
        "query": query,
        "sector": None,
        "include_related": False,
        "expected_layer": "semantic",
        "expected_id": expected_id,
        "expected_rank": 1,
        "top_result": top["id"],
        "top_layer": top["layer"],
        "top_sector": top.get("sector"),
        "top_source_url": top.get("source_url"),
        "top_occurred_at": top.get("occurred_at"),
        "result_ids": result_ids,
        "noisy_result_id": noisy_id,
        "disallowed_ids": [],
        "related_result": None,
        "layer_filtered_results": result_ids,
        "retrieval_modes": diagnostics.get("used_modes", []),
        "metrics": _metrics_for_results(expected_id, result_ids, METRIC_K_VALUES),
    }


def assert_mixed_source_trusted_authority_ranking(
    store: CortexStore,
    user_id: str = USER_ID,
    limit: int = 5,
) -> dict[str, Any]:
    expected_id = "rq_mixed_source_authority_notion_canonical"
    query = "Project Solaris launch owner budget source of truth"
    store.update_settings(
        user_id,
        {
            "review_new_captures": False,
            "allow_pending_in_context": True,
            "source_policies": {"notion": {"mode": "trusted"}},
        },
    )

    distractors: tuple[tuple[str, str, str, str], ...] = (
        ("slack", "cortex-source://slack#service=slack&channel=CSOLARIS&line=11&excerpt=solaris-old-slack", "Marco", "Slack thread"),
        ("gmail", "cortex-source://gmail#service=gmail&subject=Project%20Solaris&line=3&excerpt=solaris-old-gmail", "Nina", "email recap"),
        ("google-drive", "cortex-source://google-drive#service=google-drive&document=solaris-plan&line=27&excerpt=solaris-old-drive", "Omar", "planning doc"),
        ("chatgpt", "", "Priya", "generated summary"),
        ("slack", "cortex-source://slack#service=slack&channel=CSOLARIS&line=18&excerpt=solaris-digest-slack", "Ren", "standup digest"),
        ("gmail", "cortex-source://gmail#service=gmail&subject=Solaris%20Followup&line=9&excerpt=solaris-followup", "Ava", "follow-up email"),
        ("google-drive", "cortex-source://google-drive#service=google-drive&document=solaris-budget&line=42&excerpt=solaris-budget-draft", "Theo", "budget draft"),
        ("chatgpt", "", "Lena", "meeting digest"),
    )
    for index, (source, source_url, owner, label) in enumerate(distractors, start=1):
        store.save_capture(
            user_id=user_id,
            content=f"Project Solaris launch owner budget source of truth noisy {label}.",
            source=source,
            source_url=source_url or None,
            title=f"Project Solaris noisy {label}",
            extracted={
                "_timestamp": f"2026-07-01T09:{index:02d}:00Z",
                "summary": f"Project Solaris noisy {label}.",
                "records": [
                    {
                        "id": f"rq_mixed_source_authority_noise_{index}",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": (
                            "Project Solaris launch owner budget source of truth says "
                            f"{owner} owns the launch budget according to a stale {label}."
                        ),
                        "summary": f"Project Solaris stale {label} owner is {owner}.",
                        "confidence": "confirmed",
                        "importance": 5,
                        "sector": "Project Solaris",
                        "topics": ["project-solaris", "launch", "owner", "budget"],
                        "entity_ids": ["project_solaris"],
                    }
                ],
                "tasks": [],
                "entities": [
                    {"id": "project_solaris", "kind": "project", "name": "Project Solaris", "aliases": ["Solaris"]}
                ],
            },
        )

    store.save_capture(
        user_id=user_id,
        content="Project Solaris canonical Notion source of truth.",
        source="notion",
        source_url="cortex-source://notion#service=notion&page=solaris-canonical&line=4&excerpt=solaris-source-truth",
        title="Project Solaris canonical launch source",
        extracted={
            "_timestamp": "2026-07-01T09:30:00Z",
            "summary": "Project Solaris canonical launch source.",
            "records": [
                {
                    "id": expected_id,
                    "kind": "claim",
                    "layer": "semantic",
                    "content": (
                        "Project Solaris launch owner budget source of truth says Mira owns the launch budget; "
                        "this Notion page is the verified canonical record."
                    ),
                    "summary": "Project Solaris canonical owner is Mira.",
                    "confidence": "confirmed",
                    "importance": 1,
                    "sector": "Project Solaris",
                    "topics": ["project-solaris", "launch", "owner", "budget", "canonical"],
                    "entity_ids": ["project_solaris"],
                    "metadata": {"source_quality": "canonical", "verified": True},
                }
            ],
            "tasks": [],
            "entities": [
                {"id": "project_solaris", "kind": "project", "name": "Project Solaris", "aliases": ["Solaris"]}
            ],
        },
    )

    results = store.search(user_id, query, limit=limit, sector="Project Solaris")
    if not results:
        raise AssertionError("Mixed-source authority ranking returned no results")
    result_ids = [item["id"] for item in results]
    top = results[0]
    if top["id"] != expected_id:
        raise AssertionError(f"Mixed-source authority ranking expected trusted canonical top result {expected_id}, got {result_ids}")
    if top.get("source") != "notion":
        raise AssertionError(f"Mixed-source authority ranking top result was not Notion: {top}")
    if "Mira owns the launch budget" not in str(top.get("content") or ""):
        raise AssertionError(f"Mixed-source authority ranking missed canonical content: {top}")
    if not str(top.get("source_url") or "").startswith("cortex-source://notion#"):
        raise AssertionError(f"Mixed-source authority ranking missed canonical citation: {top}")
    noisy_top3 = [item["id"] for item in results[:3] if item["id"].startswith("rq_mixed_source_authority_noise_")]
    if len(noisy_top3) >= 3:
        raise AssertionError(f"Mixed-source authority ranking let noisy duplicates dominate top results: {result_ids}")

    return {
        "name": "mixed_source_trusted_authority_ranking",
        "category": "mixed_source_authority",
        "query": query,
        "sector": "Project Solaris",
        "include_related": False,
        "expected_layer": "semantic",
        "expected_id": expected_id,
        "expected_rank": 1,
        "top_result": top["id"],
        "top_layer": top["layer"],
        "top_sector": top.get("sector"),
        "top_source_url": top.get("source_url"),
        "top_occurred_at": top.get("occurred_at"),
        "result_ids": result_ids,
        "disallowed_ids": [],
        "related_result": None,
        "layer_filtered_results": result_ids,
        "top_source": top.get("source"),
        "metrics": _metrics_for_results(expected_id, result_ids, METRIC_K_VALUES),
    }


def seed_mixed_source_project_memories(store: CortexStore, user_id: str = USER_ID) -> list[dict[str, Any]]:
    store.update_settings(user_id, {"review_new_captures": False, "allow_pending_in_context": True})
    fixtures: tuple[tuple[str, str, str, tuple[dict[str, Any], ...]], ...] = (
        (
            "obsidian",
            "local-file://Project%20Meridian/Launch.md#line=12&excerpt=meridian-beta",
            "Project Meridian launch note",
            (
                {
                    "id": "rq_mixed_meridian_decision",
                    "kind": "decision",
                    "layer": "decision",
                    "content": "Project Meridian beta outreach decision: invite only design partners first because support load is still unknown.",
                    "summary": "Project Meridian should start with design partners.",
                    "importance": 5,
                    "sector": "Project Meridian",
                    "topics": ["project-meridian", "beta", "outreach", "decision"],
                },
                {
                    "id": "rq_mixed_meridian_procedure",
                    "kind": "procedure",
                    "layer": "procedural",
                    "content": "Project Meridian beta outreach procedure: verify source sync health, approve at least one memory, ask a cited question, then send the invite.",
                    "summary": "Project Meridian beta outreach checklist.",
                    "importance": 5,
                    "sector": "Project Meridian",
                    "topics": ["project-meridian", "beta", "procedure"],
                },
            ),
        ),
        (
            "gmail",
            "https://mail.example.com/thread/meridian-beta-support",
            "Project Meridian support email",
            (
                {
                    "id": "rq_mixed_meridian_reason",
                    "kind": "claim",
                    "layer": "semantic",
                    "content": "Project Meridian beta outreach reason: support risk means early users need founder-reviewed replies until onboarding confusion is understood.",
                    "summary": "Founder-reviewed replies are needed during early Meridian onboarding.",
                    "importance": 4,
                    "sector": "Project Meridian",
                    "topics": ["project-meridian", "support", "onboarding"],
                },
                {
                    "id": "rq_mixed_meridian_person",
                    "kind": "event",
                    "layer": "episodic",
                    "content": "Alex Rivera asked for the Project Meridian beta invite to include exact data-retention language before Friday.",
                    "summary": "Alex needs data-retention language in the Meridian invite.",
                    "importance": 4,
                    "sector": "Project Meridian",
                    "topics": ["project-meridian", "alex", "deadline"],
                },
            ),
        ),
        (
            "slack",
            "https://slack.example.com/archives/C123/p1782912000",
            "Project Meridian product channel",
            (
                {
                    "id": "rq_mixed_meridian_negative",
                    "kind": "negative",
                    "layer": "negative",
                    "content": "Project Meridian beta outreach constraint: do not ask users to manually upload private data; use connected sources and local sync instead.",
                    "summary": "Do not ask Meridian users to manually upload private data.",
                    "importance": 5,
                    "sector": "Project Meridian",
                    "topics": ["project-meridian", "privacy", "negative"],
                },
                {
                    "id": "rq_mixed_meridian_open_loop",
                    "kind": "claim",
                    "layer": "semantic",
                    "content": "Project Meridian open loop: confirm whether the Obsidian plugin installed cleanly before inviting the next tester cohort.",
                    "summary": "Confirm Obsidian plugin install before inviting more Meridian testers.",
                    "importance": 3,
                    "sector": "Project Meridian",
                    "topics": ["project-meridian", "obsidian", "open-loop"],
                },
            ),
        ),
        (
            "notion",
            "https://notion.example.com/project-meridian-style",
            "Project Meridian messaging",
            (
                {
                    "id": "rq_mixed_meridian_preference",
                    "kind": "preference",
                    "layer": "preference",
                    "content": "I prefer Project Meridian beta invites that lead with privacy, then show exactly what Cortex can cite.",
                    "summary": "Lead Meridian beta invites with privacy and citations.",
                    "importance": 4,
                    "sector": "Project Meridian",
                    "topics": ["project-meridian", "preference", "invite"],
                },
                {
                    "id": "rq_mixed_meridian_style",
                    "kind": "style",
                    "layer": "style",
                    "content": "Project Meridian writing style: warm, direct, spare, and concrete, with short paragraphs and no hype.",
                    "summary": "Project Meridian style is warm, direct, spare, and concrete.",
                    "importance": 4,
                    "sector": "Project Meridian",
                    "topics": ["project-meridian", "style", "invite"],
                },
            ),
        ),
    )
    memories: list[dict[str, Any]] = []
    for source, source_url, title, records in fixtures:
        tasks = []
        if title == "Project Meridian product channel":
            tasks = [
                {
                    "id": "rq_mixed_meridian_task_plugin_check",
                    "kind": "action",
                    "content": "Confirm whether the Obsidian plugin installed cleanly before inviting the next Project Meridian tester cohort.",
                    "status": "open",
                    "importance": 5,
                    "topics": ["project-meridian", "obsidian", "open-loop"],
                }
            ]
        result = store.save_capture(
            user_id=user_id,
            content="\n".join([*(str(record["content"]) for record in records), *(str(task["content"]) for task in tasks)]),
            source=source,
            source_url=source_url,
            title=title,
            extracted={
                "_timestamp": "2026-07-01T12:00:00Z",
                "summary": title,
                "records": [
                    {
                        **record,
                        "confidence": "confirmed",
                        "entity_ids": [],
                    }
                    for record in records
                ],
                "tasks": tasks,
                "entities": [],
            },
        )
        memories.extend(result["memories"])
    return memories


def assert_mixed_source_project_contracts(store: CortexStore, user_id: str = USER_ID) -> dict[str, Any]:
    answer = store.answer_query(
        user_id,
        "what did we decide for Project Meridian beta outreach and why",
        limit=6,
        sector="Project Meridian",
    )
    citations = answer.get("citations") or []
    citation_ids = [citation["id"] for citation in citations]
    citation_sources = sorted({str(citation.get("source") or "") for citation in citations})
    for expected_id in ("rq_mixed_meridian_decision", "rq_mixed_meridian_reason"):
        if expected_id not in citation_ids:
            raise AssertionError(f"Mixed-source Ask missed {expected_id}: {citation_ids}")
    if len(citation_sources) < 3:
        raise AssertionError(f"Mixed-source Ask did not diversify source citations: {citation_sources}")
    missing_source_urls = [citation["id"] for citation in citations if not citation.get("source_url")]
    if missing_source_urls:
        raise AssertionError(f"Mixed-source Ask emitted uncited evidence: {missing_source_urls}")

    brief = store.action_brief(
        user_id,
        "prepare Project Meridian beta outreach invite for Alex",
        sector="Project Meridian",
        limit=8,
    )
    required_sections = ("current_decisions", "procedures", "preferences", "negative_constraints", "style_signals")
    empty_sections = [section for section in required_sections if not brief.get(section)]
    if empty_sections:
        raise AssertionError(f"Mixed-source Action Brief missed sections: {empty_sections}")
    if brief.get("status") != "strong":
        raise AssertionError(f"Mixed-source Action Brief should be strong, got {brief.get('status')}: {brief.get('coverage')}")
    if int((brief.get("coverage") or {}).get("cited_memories") or 0) < 6:
        raise AssertionError(f"Mixed-source Action Brief had weak citation coverage: {brief.get('coverage')}")
    source_mix = sorted({item["source"] for item in (brief.get("coverage") or {}).get("source_mix") or []})
    if len(source_mix) < 4:
        raise AssertionError(f"Mixed-source Action Brief missed source mix: {source_mix}")
    primary_ids = {item["id"] for item in brief.get("primary_context") or []}
    expected_primary_ids = {
        "rq_mixed_meridian_decision",
        "rq_mixed_meridian_person",
        "rq_mixed_meridian_negative",
        "rq_mixed_meridian_preference",
        "rq_mixed_meridian_style",
        "rq_mixed_meridian_open_loop",
    }
    missing_primary_ids = sorted(expected_primary_ids - primary_ids)
    if missing_primary_ids:
        raise AssertionError(f"Mixed-source Action Brief missed user-action evidence: {missing_primary_ids}")
    brief_markdown = str(brief.get("markdown") or "")
    for phrase in (
        "data-retention language",
        "do not ask users to manually upload private data",
        "warm, direct, spare, and concrete",
        "confirm whether the Obsidian plugin installed cleanly",
    ):
        if phrase not in brief_markdown:
            raise AssertionError(f"Mixed-source Action Brief missed required phrase {phrase!r}")
    next_actions = " ".join(str(item) for item in brief.get("next_actions") or [])
    for phrase in ("negative constraints", "current decisions", "cited procedure", "preferences and style"):
        if phrase not in next_actions:
            raise AssertionError(f"Mixed-source Action Brief missed next-action guidance {phrase!r}: {next_actions!r}")
    action_plan = brief.get("action_plan") or []
    if not action_plan:
        raise AssertionError("Mixed-source Action Brief missed ranked action_plan")
    action_plan_sections = {str(item.get("section") or "") for item in action_plan}
    for section in ("negative_constraints", "open_actions", "procedures", "current_decisions"):
        if section not in action_plan_sections:
            raise AssertionError(f"Mixed-source Action Brief action_plan missed {section}: {action_plan!r}")
    if not any("Obsidian plugin installed cleanly" in str(item.get("action") or "") for item in action_plan):
        raise AssertionError(f"Mixed-source Action Brief action_plan missed concrete Obsidian plugin task: {action_plan!r}")
    execution_checklist = brief.get("execution_checklist") or []
    if not execution_checklist:
        raise AssertionError("Mixed-source Action Brief missed execution_checklist")
    checklist_phases = {str(item.get("phase") or "") for item in execution_checklist}
    for phase in ("guardrails", "decision_boundary", "procedure", "open_action", "verification"):
        if phase not in checklist_phases:
            raise AssertionError(f"Mixed-source Action Brief execution_checklist missed {phase}: {execution_checklist!r}")
    if not any("Obsidian plugin installed cleanly" in str(item.get("step") or "") for item in execution_checklist):
        raise AssertionError(f"Mixed-source Action Brief execution_checklist missed concrete Obsidian plugin task: {execution_checklist!r}")
    checklist_uncited = [
        item.get("rank")
        for item in execution_checklist
        if item.get("phase") != "verification"
        and not (isinstance(item.get("citation"), dict) and item["citation"].get("source_url"))
    ]
    if checklist_uncited:
        raise AssertionError(f"Mixed-source Action Brief execution_checklist emitted uncited steps: {checklist_uncited!r}")

    adaptation = store.agent_adaptation(
        user_id,
        query="Project Meridian beta outreach invite for Alex",
        target="Claude",
        limit=8,
        sector="Project Meridian",
    )
    rule_layers = {rule["layer"] for rule in adaptation.get("rules") or []}
    expected_layers = {"decision", "procedural", "preference", "negative", "style", "episodic", "semantic"}
    if not expected_layers.issubset(rule_layers):
        raise AssertionError(f"Mixed-source adaptation missed rule layers: {sorted(expected_layers - rule_layers)}")
    rule_ids = {rule["memory_id"] for rule in adaptation.get("rules") or []}
    for expected_id in expected_primary_ids:
        if expected_id not in rule_ids:
            raise AssertionError(f"Mixed-source adaptation missed actionable rule evidence: {expected_id}")
    uncited_rules = [rule["memory_id"] for rule in adaptation.get("rules") or [] if not rule.get("source_url")]
    if uncited_rules:
        raise AssertionError(f"Mixed-source adaptation emitted uncited rules: {uncited_rules}")
    if adaptation.get("coverage_warnings"):
        raise AssertionError(f"Mixed-source adaptation should not warn on the complete fixture: {adaptation['coverage_warnings']}")
    adaptation_markdown = str(adaptation.get("markdown") or "")
    for phrase in ("data-retention language", "manually upload private data", "warm, direct, spare"):
        if phrase not in adaptation_markdown:
            raise AssertionError(f"Mixed-source adaptation markdown missed phrase {phrase!r}")

    return {
        "ask_citation_ids": citation_ids,
        "ask_sources": citation_sources,
        "brief_status": brief.get("status"),
        "brief_source_mix": source_mix,
        "brief_primary_ids": sorted(primary_ids),
        "brief_next_actions": brief.get("next_actions") or [],
        "brief_execution_checklist_phases": sorted(checklist_phases),
        "adaptation_rule_layers": sorted(rule_layers),
        "adaptation_rule_ids": sorted(rule_ids),
    }


def evaluate_retrieval(store: CortexStore, user_id: str = USER_ID, limit: int = 3) -> dict[str, Any]:
    seeded = seed_representative_memories(store, user_id)
    distractors = seed_distractor_memories(store, user_id)
    focused_memories = seed_focused_retrieval_memories(store, user_id)
    seeded_layers = {memory["layer"] for memory in seeded}
    if seeded_layers != MEMORY_LAYERS:
        missing = sorted(MEMORY_LAYERS - seeded_layers)
        extra = sorted(seeded_layers - MEMORY_LAYERS)
        raise AssertionError(f"Seeded layer mismatch: missing={missing}, extra={extra}")

    checks: list[dict[str, Any]] = []
    for case in RETRIEVAL_CASES:
        checks.append(_evaluate_case(store, user_id, case, limit))

    noisy_memories = seed_noisy_import_memories(store, user_id)
    def noisy_id(phrase: str, *, layer: str | None = None) -> str:
        for memory in noisy_memories:
            if phrase in memory["content"] and (layer is None or memory["layer"] == layer):
                return memory["id"]
        raise AssertionError(f"No noisy import memory contained {phrase!r} in layer {layer!r}: {[memory['content'] for memory in noisy_memories]}")

    noisy_cases = (
        RetrievalCase(
            name="noisy_import_decision_surfaces",
            query="Project Atlas Home Review Ask Connections Privacy",
            expected_id=noisy_id("Home, Review, Ask", layer="decision"),
            expected_layer="decision",
            expected_phrase="Home, Review, Ask",
            category="noisy_import",
            source_url_contains=("service=chatgpt", "conversation=Project%20Atlas%20memory%20UI", "line=", "message=", "excerpt="),
        ),
        RetrievalCase(
            name="noisy_import_event_eval",
            query="Project Atlas noisy import retrieval eval June 29",
            expected_id=noisy_id("noisy import retrieval eval", layer="episodic"),
            expected_layer="episodic",
            expected_phrase="noisy import retrieval eval",
            category="noisy_import",
            source_url_contains=("service=chatgpt", "conversation=Project%20Atlas%20memory%20UI", "line=", "message=", "excerpt="),
            expected_occurred_at="2026-06-29",
        ),
        RetrievalCase(
            name="noisy_import_claude_preference",
            query="Project Lumen source trace answers citation gutters",
            expected_id=noisy_id("source-trace answers", layer="preference"),
            expected_layer="preference",
            expected_phrase="citation gutters",
            category="noisy_import_preference",
            source_url_contains=("service=claude", "conversation=Project%20Lumen%20retrieval%20habits", "line=", "message=", "excerpt="),
        ),
        RetrievalCase(
            name="noisy_import_slack_style",
            query="terse Lumen bullets direct paragraphs",
            expected_id=noisy_id("terse Lumen bullets", layer="style"),
            expected_layer="style",
            expected_phrase="direct paragraphs",
            category="noisy_import_style",
            source_url_contains=("service=slack", "channel=general", "line=", "message=1", "excerpt="),
        ),
        RetrievalCase(
            name="noisy_import_slack_negative",
            query="Project Lumen ceremonial launch intros",
            expected_id=noisy_id("ceremonial launch intros", layer="negative"),
            expected_layer="negative",
            expected_phrase="Project Lumen reviews",
            category="noisy_import_negative",
            source_url_contains=("service=slack", "channel=general", "line=", "message=2", "excerpt="),
        ),
        RetrievalCase(
            name="noisy_import_email_decision",
            query="Project Atlas external email citations",
            expected_id=noisy_id("external email citations", layer="decision"),
            expected_layer="decision",
            expected_phrase="external email citations",
            category="noisy_import_email",
            source_url_contains=("service=email", "subject=External%20advice", "line=", "excerpt="),
        ),
        RetrievalCase(
            name="noisy_import_docs_semantic",
            query="Project Lumen docs retrieval coverage cited source paths",
            expected_id=noisy_id("document fixture requires docs retrieval coverage", layer="semantic"),
            expected_layer="semantic",
            expected_phrase="cited source paths",
            category="noisy_import_docs",
            source_url_contains=("Project Lumen Retrieval.md", "line=", "excerpt="),
        ),
        RetrievalCase(
            name="noisy_import_notion_decision",
            query="Project Orion Notion canonical source id mapping",
            expected_id=noisy_id("Project Orion Notion imports", layer="decision"),
            expected_layer="decision",
            expected_phrase="canonical source-id mapping",
            category="noisy_import_notion",
            source_url_contains=("service=notion", "page=Project%20Orion", "line=", "excerpt="),
        ),
        RetrievalCase(
            name="noisy_import_cloud_docs_semantic",
            query="Project Nebula cloud docs export ready source health",
            expected_id=noisy_id("cloud docs retrieval coverage", layer="semantic"),
            expected_layer="semantic",
            expected_phrase="export-ready source health",
            category="noisy_import_cloud_docs",
            source_url_contains=("service=cloud-docs", "provider=google-drive", "document=Project%20Nebula", "line=", "excerpt="),
        ),
        RetrievalCase(
            name="noisy_import_calendar_event",
            query="Project Meridian calendar import review schedule memory",
            expected_id=noisy_id("Project Meridian calendar import review", layer="episodic"),
            expected_layer="episodic",
            expected_phrase="cited schedule memory",
            category="noisy_import_calendar",
            source_url_contains=("service=calendar", "first_event=Project%20Meridian%20calendar%20review", "line=", "event=1", "excerpt="),
            expected_occurred_at="2026-06-29",
        ),
        RetrievalCase(
            name="noisy_import_github_decision",
            query="Project Quarry GitHub issue source paths",
            expected_id=noisy_id("Project Quarry GitHub imports", layer="decision"),
            expected_layer="decision",
            expected_phrase="preserve issue source paths",
            category="noisy_import_github",
            source_url_contains=("service=github", "repository=Project%20Quarry", "file=issues.csv", "line=", "row=1", "excerpt="),
        ),
    )
    for case in noisy_cases:
        checks.append(_evaluate_case(store, user_id, case, limit))

    direct_connector_memories = seed_direct_connector_memories(store, user_id)
    def direct_id(source: str, phrase: str, *, layer: str | None = None) -> str:
        for memory in direct_connector_memories:
            if (
                memory["source"] == source
                and phrase in memory["content"]
                and (layer is None or memory["layer"] == layer)
            ):
                return memory["id"]
        raise AssertionError(
            f"No direct connector memory matched source={source!r} phrase={phrase!r} layer={layer!r}: "
            f"{[(memory['source'], memory['layer'], memory['content']) for memory in direct_connector_memories]}"
        )

    direct_cases = tuple(
        RetrievalCase(
            name=f"direct_connector_{fixture['source']}",
            query=str(fixture["query"]),
            expected_id=direct_id(str(fixture["source"]), str(fixture["expected_phrase"]), layer=str(fixture["expected_layer"])),
            expected_layer=str(fixture["expected_layer"]),
            expected_phrase=str(fixture["expected_phrase"]),
            category="direct_connector",
            source_url_contains=(
                DIRECT_CONNECTOR_SOURCE_URL_TOKEN,
                f"service={fixture['source']}",
                "line=1",
                "excerpt=",
            ),
            expected_occurred_at=fixture.get("occurred_at"),
        )
        for fixture in DIRECT_CONNECTOR_FIXTURES
    )
    for case in direct_cases:
        checks.append(_evaluate_case(store, user_id, case, limit))

    mixed_source_project_memories = seed_mixed_source_project_memories(store, user_id)
    mixed_source_project_contracts = assert_mixed_source_project_contracts(store, user_id)
    mixed_source_authority = assert_mixed_source_trusted_authority_ranking(store, user_id)
    checks.append(mixed_source_authority)
    automatic_connector_account_scope = assert_automatic_connector_account_scope_contract(store, user_id)
    checks.append(automatic_connector_account_scope["check"])
    local_file_citation = assert_shared_local_file_citations_sanitized(store, user_id)
    state_leakage = assert_state_leakage_excluded(store, user_id)
    focused_answer_contracts = assert_focused_answer_contracts(store, user_id)
    no_evidence_answer = assert_no_evidence_answer_abstains(store, user_id)
    direct_connector_answer_contracts = assert_direct_connector_answer_contracts(store, direct_connector_memories, user_id)
    source_backed_fallback = assert_source_backed_lexical_fallback_ranking(store, user_id, limit)
    checks.append(source_backed_fallback)

    return {
        "status": "ok",
        "seeded_memories": len(seeded),
        "distractor_memories": len(distractors),
        "focused_retrieval_memories": len(focused_memories),
        "noisy_import_memories": len(noisy_memories),
        "direct_connector_memories": len(direct_connector_memories),
        "mixed_source_project_memories": len(mixed_source_project_memories),
        "mixed_source_project_contracts": mixed_source_project_contracts,
        "mixed_source_authority": mixed_source_authority,
        "automatic_connector_account_scope": automatic_connector_account_scope,
        "local_file_citation": local_file_citation,
        "state_leakage_seeded": state_leakage,
        "focused_answer_contracts": focused_answer_contracts,
        "no_evidence_answer": no_evidence_answer,
        "direct_connector_answer_contracts": direct_connector_answer_contracts,
        "source_backed_fallback": source_backed_fallback,
        "seeded_layers": sorted(seeded_layers),
        "metrics": _summarize_metrics(checks, METRIC_K_VALUES),
        "checks": checks,
    }


def run_retrieval_eval(db_path: Path, vault_path: Path | None = None, user_id: str = USER_ID) -> dict[str, Any]:
    init_db(db_path)
    store = CortexStore(db_path, vault_path)
    return evaluate_retrieval(store, user_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a lightweight Cortex retrieval quality harness.")
    parser.add_argument("--db-path", type=Path, help="Optional SQLite path. Defaults to a temporary database.")
    parser.add_argument("--vault-path", type=Path, help="Optional vault path. Defaults beside the SQLite database.")
    parser.add_argument("--user-id", default=USER_ID)
    args = parser.parse_args()

    if args.db_path:
        result = run_retrieval_eval(args.db_path.expanduser(), args.vault_path.expanduser() if args.vault_path else None, args.user_id)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_retrieval_eval(root / "retrieval-eval.sqlite", root / "Cortex.vault", args.user_id)

    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
