from __future__ import annotations

import argparse
from email.message import EmailMessage
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.database import init_db
from backend.app.embeddings import embedding_status
from backend.app.storage import BASELINE_10K_CONNECTOR_IDS, CortexStore, MEMORY_LAYERS


USER_ID = "retrieval-quality"
SEED_TIMESTAMP = "2026-01-01T00:00:00Z"
METRIC_K_VALUES = (1, 3)

# Retrieval-quality regression gate. Floors are set just below current measured
# values (overall top1/recall@1/recall@3 = 1.0; every category top1/recall@3 = 1.0)
# so the gate catches real drops without being flaky. retrieval_eval.py exits
# non-zero when any floor is breached, so CI blocks merges on a quality regression.
# Current measured corpus: 163 checks (136 static RETRIEVAL_CASES + noisy-import,
# direct-connector, account-policy, mixed-source-authority, and source-backed
# fallback checks); min_case_count sits just below that, never below a previous
# floor.
RETRIEVAL_METRIC_THRESHOLDS: dict[str, Any] = {
    "min_case_count": 160,
    "overall": {"top1_accuracy": 0.95, "recall@1": 0.95, "recall@3": 0.97},
    "per_category": {"top1_accuracy": 0.9, "recall@3": 0.9},
    # Per-LAYER floors, set at/just below the current measured per-layer values so
    # the gate passes today but catches a real per-layer regression. Current
    # measured per-layer top1_accuracy/recall@1/recall@3 are all 1.0 for every
    # expected_layer (decision, episodic, negative, preference, procedural,
    # semantic, style); floors sit ~0.1 below that, never above current.
    "per_layer": {"top1_accuracy": 0.9, "recall@1": 0.9, "recall@3": 0.9},
    "required_layers": (
        "decision",
        "episodic",
        "negative",
        "preference",
        "procedural",
        "semantic",
        "style",
    ),
    "required_categories": (
        "style_recall",
        "negative_recall",
        "procedural_recall",
        "temporal_recall",
        "temporal_validity",
        "sector_scoping",
        "related_memory",
        "source_backed_ranking",
        "mixed_source_authority",
        "direct_connector",
        "paraphrase",
        "focused",
        "cross_project_no_leak",
        "entity_recall",
        "decision_recall",
        "source_scoped",
    ),
}

# Relevance-monotonicity gate. Every per-case check emits a `relevance` for each returned
# row (preferring a `relevance`/`relevance_basis` surfaced by the search layer's reranker
# work, falling back to a strictly rank-monotonic value derived from the emitted order under
# the deterministic hash embedder). This gate asserts that the emitted relevance is a faithful
# ranking key:
#   * pairwise_concordance — across every case, a graded-relevant row (the expected id, and its
#     expected related companion when the case requests one) outscores a non-relevant returned
#     row. A rank-correlation floor rather than strict all-pairs so an occasional companion/tail
#     inversion does not flake, while a scrambled relevance signal (concordance ~0.5) still trips.
#   * reordered_* — re-sorting each case's rows BY the emitted relevance reproduces the existing
#     top1/recall@1/recall@3 floors, i.e. ordering by relevance never regresses the ranking.
#   * allowed_bases — under the hash path relevance is rank/rrf-derived, never cosine; the cosine
#     basis only appears in the real-embedder rerank path exercised by rerank_eval.py.
# Floors sit just below the values measured under the deterministic hash embedder so the gate
# passes today (rank-derived relevance is trivially monotonic with the emitted order) and stays
# green when an additive real `relevance` signal lands, but a broken relevance key fails it.
RELEVANCE_MONOTONICITY_THRESHOLDS: dict[str, Any] = {
    "min_case_count": 120,
    "min_pair_count": 40,
    "pairwise_concordance": 0.95,
    "reordered_top1_accuracy": 0.95,
    "reordered_recall@1": 0.95,
    "reordered_recall@3": 0.97,
    "allowed_bases": ("rank", "rrf"),
}
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


# Expanded multi-source, multi-project eval corpus. These captures extend the
# original seven-layer seed with realistic fixtures across five projects
# (data-migration, hiring pipeline, personal health, offline mobile client,
# design system), ten named people, and six connected sources
# (gmail/slack/github/notion/calendar/obsidian) using the standard
# cortex-source://<service>#service=...&line=...&excerpt=... locator format.
# Record ids all start with "rq_x_" so import-focused assertions can keep
# scoping themselves to import-produced memories.
EXPANDED_VALIDITY_ALWAYS_VALID_FROM = "2000-01-01T00:00:00+00:00"
EXPANDED_VALIDITY_EXPIRED_AT = "2020-01-01T00:00:00+00:00"

EXPANDED_SEED_CAPTURES: tuple[dict[str, Any], ...] = (
    {
        "source": "gmail",
        "source_url": "cortex-source://gmail#service=gmail&subject=Project%20Granite%20migration&line=8&excerpt=granite-migration-thread",
        "title": "Project Granite migration thread",
        "timestamp": "2026-05-21T09:00:00Z",
        "records": (
            {
                "id": "rq_x_granite_shards",
                "kind": "claim",
                "layer": "semantic",
                "content": "Project Granite splits the legacy Postgres warehouse into per-tenant SQLite shards with a checksum manifest for every batch.",
                "summary": "Granite shards the legacy warehouse per tenant with checksum manifests.",
                "importance": 4,
                "sector": "Project Granite",
                "topics": ["project-granite", "migration", "shards"],
            },
            {
                "id": "rq_x_granite_vendor",
                "kind": "claim",
                "layer": "semantic",
                "content": "Saoirse Quinn confirmed the Granite vendor contract covers replication tooling through fiscal 2027.",
                "summary": "Saoirse confirmed the Granite vendor contract runs through fiscal 2027.",
                "importance": 3,
                "sector": "Project Granite",
                "topics": ["project-granite", "vendor", "replication"],
                "entity_ids": ["x_person_saoirse_quinn"],
            },
            {
                "id": "rq_x_granite_neg_dashboards",
                "kind": "negative",
                "layer": "negative",
                "content": "Never point analytics dashboards at the primary tenant shards during a cutover window.",
                "summary": "Keep analytics dashboards off the primary shards during cutovers.",
                "importance": 4,
                "sector": "Project Granite",
                "topics": ["project-granite", "negative", "dashboards"],
            },
            {
                "id": "rq_x_granite_dec_dualwrite",
                "kind": "decision",
                "layer": "decision",
                "content": "Decision: Project Granite dual-writes to the legacy and sharded stores until shadow reads match for thirty consecutive days.",
                "summary": "Granite dual-writes until shadow reads match for thirty days.",
                "importance": 5,
                "sector": "Project Granite",
                "topics": ["project-granite", "decision", "dual-write"],
            },
            {
                "id": "rq_x_granite_dec_differ",
                "kind": "decision",
                "layer": "decision",
                "content": "Decision: Project Granite builds the replay differ in-house instead of licensing the vendor comparison suite.",
                "summary": "Granite builds the replay differ in-house.",
                "importance": 4,
                "sector": "Project Granite",
                "topics": ["project-granite", "decision", "replay"],
            },
            {
                "id": "rq_x_granite_event_freeze",
                "kind": "decision",
                "layer": "decision",
                "content": "On 2026-05-21, Ingrid Halvorsen approved the Granite schema freeze after the dry-run diff came back clean.",
                "summary": "Ingrid approved the Granite schema freeze on 2026-05-21.",
                "importance": 4,
                "sector": "Project Granite",
                "topics": ["project-granite", "schema", "freeze"],
                "occurred_at": "2026-05-21",
                "entity_ids": ["x_person_ingrid_halvorsen"],
            },
            {
                "id": "rq_x_granite_marcus",
                "kind": "claim",
                "layer": "semantic",
                "content": "Marcus Bell sized the Granite replica fleet at twelve nodes with headroom for two more tenants.",
                "summary": "Marcus sized the Granite replica fleet at twelve nodes.",
                "importance": 3,
                "sector": "Project Granite",
                "topics": ["project-granite", "capacity", "replicas"],
                "entity_ids": ["x_person_marcus_bell"],
            },
        ),
        "entities": (
            {"id": "x_person_saoirse_quinn", "kind": "person", "name": "Saoirse Quinn", "aliases": ["Saoirse"], "context": "Granite vendor contact."},
            {"id": "x_person_ingrid_halvorsen", "kind": "person", "name": "Ingrid Halvorsen", "aliases": ["Ingrid"], "context": "Granite database administrator."},
            {"id": "x_person_marcus_bell", "kind": "person", "name": "Marcus Bell", "aliases": ["Marcus"], "context": "Granite infrastructure engineer."},
        ),
    },
    {
        "source": "slack",
        "source_url": "cortex-source://slack#service=slack&channel=CGRANITE&message=1772800000000100&line=1&excerpt=granite-migration-channel",
        "title": "Granite migration channel rules",
        "timestamp": "2026-03-05T14:00:00Z",
        "records": (
            {
                "id": "rq_x_granite_neg_backfill",
                "kind": "negative",
                "layer": "negative",
                "content": "Do not run destructive backfills against live tenant shards without a rehearsal snapshot.",
                "summary": "No destructive backfills without a rehearsal snapshot.",
                "importance": 5,
                "sector": "Project Granite",
                "topics": ["project-granite", "negative", "backfills"],
            },
            {
                "id": "rq_x_leak_granite_freeze",
                "kind": "decision",
                "layer": "decision",
                "content": "Deploy freeze rule: Project Granite blocks schema deploys during the replay verification week.",
                "summary": "Granite blocks schema deploys during replay verification week.",
                "importance": 4,
                "sector": "Project Granite",
                "topics": ["project-granite", "freeze", "deploys"],
            },
            {
                "id": "rq_x_leak_kestrel_freeze",
                "kind": "decision",
                "layer": "decision",
                "content": "Deploy freeze rule: Project Kestrel blocks app-store submissions during the field pilot week.",
                "summary": "Kestrel blocks app-store submissions during the field pilot week.",
                "importance": 4,
                "sector": "Project Kestrel",
                "topics": ["project-kestrel", "freeze", "submissions"],
            },
            {
                "id": "rq_x_sector_granite_escalation",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Escalation runbook: Project Granite pages the data on-call, freezes the replay queue, and posts a shard status thread.",
                "summary": "Granite escalation pages data on-call and freezes the replay queue.",
                "importance": 4,
                "sector": "Project Granite",
                "topics": ["project-granite", "escalation", "runbook"],
            },
            {
                "id": "rq_x_sector_kestrel_escalation",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Escalation runbook: Project Kestrel pages the mobile on-call, disables background sync, and posts a device status thread.",
                "summary": "Kestrel escalation pages mobile on-call and disables background sync.",
                "importance": 4,
                "sector": "Project Kestrel",
                "topics": ["project-kestrel", "escalation", "runbook"],
            },
        ),
        "entities": (),
    },
    {
        "source": "notion",
        "source_url": "cortex-source://notion#service=notion&page=Granite%20Runbook&line=6&excerpt=granite-runbook",
        "title": "Granite runbook page",
        "timestamp": "2026-02-10T10:00:00Z",
        "records": (
            {
                "id": "rq_x_granite_style_runbook",
                "kind": "style",
                "layer": "style",
                "content": "Migration runbook style for Granite: numbered steps, imperative voice, one command per step.",
                "summary": "Granite runbooks use numbered steps and imperative voice.",
                "importance": 3,
                "sector": "Project Granite",
                "topics": ["project-granite", "style", "runbook"],
            },
            {
                "id": "rq_x_granite_style_status",
                "kind": "style",
                "layer": "style",
                "content": "Granite migration status-note style: three-line digest with shard counts and a blocking-issues line.",
                "summary": "Granite status notes are a three-line digest with shard counts.",
                "importance": 3,
                "sector": "Project Granite",
                "topics": ["project-granite", "style", "status"],
            },
            {
                "id": "rq_x_granite_proc_cutover",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Procedure: run the Granite cutover by draining writers, replaying the tail journal, flipping reads, and watching the drift alerts for an hour.",
                "summary": "Granite cutover drains writers, replays the tail journal, then flips reads.",
                "importance": 5,
                "sector": "Project Granite",
                "topics": ["project-granite", "procedure", "cutover"],
            },
            {
                "id": "rq_x_granite_proc_tenant",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Procedure: onboard a Granite tenant by provisioning the shard, seeding fixtures, running checksums, and scheduling the first replay.",
                "summary": "Granite tenant onboarding provisions the shard and seeds fixtures.",
                "importance": 4,
                "sector": "Project Granite",
                "topics": ["project-granite", "procedure", "tenants"],
            },
            {
                "id": "rq_x_validity_granite_retention_current",
                "kind": "decision",
                "layer": "decision",
                "content": "Current Granite replay retention window: keep fourteen days of replay traffic on the staging shards.",
                "summary": "Granite keeps fourteen days of replay traffic.",
                "importance": 3,
                "sector": "Project Granite",
                "topics": ["project-granite", "retention", "replay"],
                "valid_from": EXPANDED_VALIDITY_ALWAYS_VALID_FROM,
            },
            {
                "id": "rq_x_validity_granite_retention_expired",
                "kind": "decision",
                "layer": "decision",
                "content": "Expired Granite replay retention window: keep sixty days of replay traffic on tape backups.",
                "summary": "Expired Granite retention kept sixty days on tape.",
                "importance": 5,
                "sector": "Project Granite",
                "topics": ["project-granite", "retention", "replay"],
                "valid_to": EXPANDED_VALIDITY_EXPIRED_AT,
            },
            {
                "id": "rq_x_validity_granite_retention_superseded",
                "kind": "decision",
                "layer": "decision",
                "content": "Superseded Granite replay retention window: keep thirty days of replay traffic uncompressed.",
                "summary": "Superseded Granite retention kept thirty days uncompressed.",
                "importance": 5,
                "sector": "Project Granite",
                "topics": ["project-granite", "retention", "replay"],
                "superseded_by": "rq_x_validity_granite_retention_current",
            },
            {
                "id": "rq_x_validity_granite_cutover_current",
                "kind": "decision",
                "layer": "decision",
                "content": "Current Granite cutover window: Sundays 02:00 to 05:00 UTC with a two-hour drain.",
                "summary": "Granite cutovers run Sundays with a two-hour drain.",
                "importance": 3,
                "sector": "Project Granite",
                "topics": ["project-granite", "cutover", "window"],
                "valid_from": EXPANDED_VALIDITY_ALWAYS_VALID_FROM,
            },
            {
                "id": "rq_x_validity_granite_cutover_expired",
                "kind": "decision",
                "layer": "decision",
                "content": "Expired Granite cutover window: Fridays 22:00 to 23:00 UTC with no drain buffer.",
                "summary": "Expired Granite cutovers ran Fridays with no drain buffer.",
                "importance": 5,
                "sector": "Project Granite",
                "topics": ["project-granite", "cutover", "window"],
                "valid_to": EXPANDED_VALIDITY_EXPIRED_AT,
            },
            {
                "id": "rq_x_validity_granite_cutover_superseded",
                "kind": "decision",
                "layer": "decision",
                "content": "Superseded Granite cutover window: Saturdays 01:00 to 03:00 UTC with a one-hour drain.",
                "summary": "Superseded Granite cutovers ran Saturdays with a one-hour drain.",
                "importance": 5,
                "sector": "Project Granite",
                "topics": ["project-granite", "cutover", "window"],
                "superseded_by": "rq_x_validity_granite_cutover_current",
            },
            {
                "id": "rq_x_validity_granite_alert_current",
                "kind": "decision",
                "layer": "decision",
                "content": "Current Granite drift alert threshold: page the on-call when replay drift exceeds 0.5 percent for ten minutes.",
                "summary": "Granite pages on-call at 0.5 percent replay drift.",
                "importance": 3,
                "sector": "Project Granite",
                "topics": ["project-granite", "alerts", "drift"],
                "valid_from": EXPANDED_VALIDITY_ALWAYS_VALID_FROM,
            },
            {
                "id": "rq_x_validity_granite_alert_expired",
                "kind": "decision",
                "layer": "decision",
                "content": "Expired Granite drift alert threshold: page the on-call when replay drift exceeds five percent for an hour.",
                "summary": "Expired Granite alert threshold was five percent for an hour.",
                "importance": 5,
                "sector": "Project Granite",
                "topics": ["project-granite", "alerts", "drift"],
                "valid_to": EXPANDED_VALIDITY_EXPIRED_AT,
            },
            {
                "id": "rq_x_validity_granite_alert_superseded",
                "kind": "decision",
                "layer": "decision",
                "content": "Superseded Granite drift alert threshold: page the on-call when replay drift exceeds one percent for thirty minutes.",
                "summary": "Superseded Granite alert threshold was one percent for thirty minutes.",
                "importance": 5,
                "sector": "Project Granite",
                "topics": ["project-granite", "alerts", "drift"],
                "superseded_by": "rq_x_validity_granite_alert_current",
            },
        ),
        "entities": (),
    },
    {
        "source": "calendar",
        "source_url": "cortex-source://calendar#service=calendar&event=1&first_event=Granite%20cutover%20rehearsal&line=1&excerpt=granite-calendar",
        "title": "Granite milestone events",
        "timestamp": "2026-06-11T18:00:00Z",
        "records": (
            {
                "id": "rq_x_granite_event_dryrun",
                "kind": "event",
                "layer": "episodic",
                "content": "On 2026-03-12, the Granite team completed the first full dry-run of the warehouse cutover in the staging enclave.",
                "summary": "First full Granite warehouse cutover dry-run on 2026-03-12.",
                "importance": 4,
                "sector": "Project Granite",
                "topics": ["project-granite", "dry-run", "cutover"],
                "occurred_at": "2026-03-12",
            },
            {
                "id": "rq_x_granite_event_rehearsal",
                "kind": "event",
                "layer": "episodic",
                "content": "On 2026-06-11, the second Granite cutover rehearsal replayed a week of production traffic without drift.",
                "summary": "Second Granite rehearsal replayed a week of traffic without drift.",
                "importance": 4,
                "sector": "Project Granite",
                "topics": ["project-granite", "rehearsal", "cutover"],
                "occurred_at": "2026-06-11",
            },
            {
                "id": "rq_x_leak_health_morning",
                "kind": "claim",
                "layer": "semantic",
                "content": "Morning block rule: Personal Health reserves 06:30 to 07:30 for training before any meetings.",
                "summary": "Personal Health reserves 06:30 to 07:30 for training.",
                "importance": 3,
                "sector": "Personal Health",
                "topics": ["personal-health", "training", "calendar"],
            },
            {
                "id": "rq_x_leak_granite_morning",
                "kind": "claim",
                "layer": "semantic",
                "content": "Morning block rule: Project Granite reserves 09:00 to 09:15 for the migration standup sync.",
                "summary": "Granite reserves 09:00 to 09:15 for the migration standup.",
                "importance": 3,
                "sector": "Project Granite",
                "topics": ["project-granite", "standup", "calendar"],
            },
        ),
        "entities": (),
    },
    {
        "source": "notion",
        "source_url": "cortex-source://notion#service=notion&page=Foundry%20Hiring%20Pipeline&line=3&excerpt=foundry-pipeline",
        "title": "Foundry hiring pipeline page",
        "timestamp": "2025-12-08T11:00:00Z",
        "records": (
            {
                "id": "rq_x_foundry_pipeline",
                "kind": "claim",
                "layer": "semantic",
                "content": "Project Foundry tracks every candidate through sourcing, recruiter screen, panel loop, and offer approval in one pipeline board.",
                "summary": "Foundry tracks candidates through four stages in one pipeline board.",
                "importance": 4,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "hiring", "pipeline"],
            },
            {
                "id": "rq_x_foundry_style_outreach",
                "kind": "style",
                "layer": "style",
                "content": "Hiring outreach style for Foundry: two plain sentences, one concrete role detail, and zero buzzwords.",
                "summary": "Foundry outreach uses two plain sentences and zero buzzwords.",
                "importance": 3,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "style", "outreach"],
            },
            {
                "id": "rq_x_foundry_beatriz",
                "kind": "claim",
                "layer": "semantic",
                "content": "Beatriz Sousa maintains the Foundry scorecard template and signs off every rubric change.",
                "summary": "Beatriz maintains the Foundry scorecard template.",
                "importance": 3,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "rubric", "scorecards"],
                "entity_ids": ["x_person_beatriz_sousa"],
            },
            {
                "id": "rq_x_foundry_dec_takehome",
                "kind": "decision",
                "layer": "decision",
                "content": "Decision: Project Foundry replaces take-home projects with a paired ninety-minute working session.",
                "summary": "Foundry replaces take-homes with a paired working session.",
                "importance": 4,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "decision", "interviews"],
            },
            {
                "id": "rq_x_validity_foundry_comp_current",
                "kind": "decision",
                "layer": "decision",
                "content": "Current Foundry compensation bands: platform roles span level three to level five with location factors.",
                "summary": "Foundry compensation bands span level three to five with location factors.",
                "importance": 3,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "compensation", "bands"],
                "valid_from": EXPANDED_VALIDITY_ALWAYS_VALID_FROM,
            },
            {
                "id": "rq_x_validity_foundry_comp_expired",
                "kind": "decision",
                "layer": "decision",
                "content": "Expired Foundry compensation bands: platform roles used a flat national rate sheet.",
                "summary": "Expired Foundry compensation used a flat national rate sheet.",
                "importance": 5,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "compensation", "bands"],
                "valid_to": EXPANDED_VALIDITY_EXPIRED_AT,
            },
            {
                "id": "rq_x_validity_foundry_comp_superseded",
                "kind": "decision",
                "layer": "decision",
                "content": "Superseded Foundry compensation bands: platform roles span level two to level four without location factors.",
                "summary": "Superseded Foundry compensation spanned level two to four.",
                "importance": 5,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "compensation", "bands"],
                "superseded_by": "rq_x_validity_foundry_comp_current",
            },
            {
                "id": "rq_x_validity_foundry_headcount_current",
                "kind": "decision",
                "layer": "decision",
                "content": "Current Foundry headcount plan: six platform hires and two design hires this fiscal year.",
                "summary": "Foundry plans six platform hires and two design hires.",
                "importance": 3,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "headcount", "plan"],
                "valid_from": EXPANDED_VALIDITY_ALWAYS_VALID_FROM,
            },
            {
                "id": "rq_x_validity_foundry_headcount_expired",
                "kind": "decision",
                "layer": "decision",
                "content": "Expired Foundry headcount plan: two contract hires only while the team was frozen.",
                "summary": "Expired Foundry headcount allowed two contract hires only.",
                "importance": 5,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "headcount", "plan"],
                "valid_to": EXPANDED_VALIDITY_EXPIRED_AT,
            },
            {
                "id": "rq_x_validity_foundry_headcount_superseded",
                "kind": "decision",
                "layer": "decision",
                "content": "Superseded Foundry headcount plan: four platform hires and one design hire this fiscal year.",
                "summary": "Superseded Foundry headcount planned four platform hires.",
                "importance": 5,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "headcount", "plan"],
                "superseded_by": "rq_x_validity_foundry_headcount_current",
            },
            {
                "id": "rq_x_sector_foundry_weekly",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Weekly review agenda: Project Foundry walks the pipeline board, ages every stalled candidate, and books next panels.",
                "summary": "Foundry weekly review walks the board and ages stalled candidates.",
                "importance": 3,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "weekly", "agenda"],
            },
            {
                "id": "rq_x_sector_larkspur_weekly",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Weekly review agenda: Project Larkspur walks the token backlog, triages component bugs, and books design critiques.",
                "summary": "Larkspur weekly review walks the token backlog and books critiques.",
                "importance": 3,
                "sector": "Project Larkspur",
                "topics": ["project-larkspur", "weekly", "agenda"],
            },
        ),
        "entities": (
            {"id": "x_person_beatriz_sousa", "kind": "person", "name": "Beatriz Sousa", "aliases": ["Beatriz"], "context": "Foundry hiring lead."},
        ),
    },
    {
        "source": "slack",
        "source_url": "cortex-source://slack#service=slack&channel=CFOUNDRY&message=1768500000000200&line=1&excerpt=foundry-hiring-channel",
        "title": "Foundry hiring channel",
        "timestamp": "2026-01-15T16:00:00Z",
        "records": (
            {
                "id": "rq_x_foundry_referral",
                "kind": "claim",
                "layer": "semantic",
                "content": "Project Foundry referral rewards pay out ninety days after the new hire starts.",
                "summary": "Foundry referral rewards pay out after ninety days.",
                "importance": 3,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "referrals", "rewards"],
            },
            {
                "id": "rq_x_foundry_pref_afternoon",
                "kind": "preference",
                "layer": "preference",
                "content": "Preference: schedule Foundry interviews in the afternoon block and never before ten.",
                "summary": "Keep Foundry interviews in the afternoon block.",
                "importance": 3,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "preference", "interviews"],
            },
            {
                "id": "rq_x_foundry_neg_comp",
                "kind": "negative",
                "layer": "negative",
                "content": "Do not quote compensation numbers during recruiter screens before the interview loop finishes.",
                "summary": "No compensation numbers during recruiter screens.",
                "importance": 4,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "negative", "compensation"],
            },
            {
                "id": "rq_x_foundry_style_feedback",
                "kind": "style",
                "layer": "style",
                "content": "Interview feedback style for Foundry: verdict first, then two observed behaviors with timestamps.",
                "summary": "Foundry feedback leads with the verdict and cites observed behaviors.",
                "importance": 3,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "style", "feedback"],
            },
            {
                "id": "rq_x_foundry_dec_ats",
                "kind": "decision",
                "layer": "decision",
                "content": "Decision: Project Foundry keeps the current applicant tracker and revisits alternatives after six quarterly cycles.",
                "summary": "Foundry keeps the applicant tracker for six quarterly cycles.",
                "importance": 4,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "decision", "tooling"],
            },
            {
                "id": "rq_x_foundry_proc_debrief",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Procedure: close a Foundry panel with scorecards submitted, a debrief within two days, and a decision logged in the pipeline.",
                "summary": "Foundry panels close with scorecards, a debrief, and a logged decision.",
                "importance": 4,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "procedure", "debrief"],
            },
            {
                "id": "rq_x_foundry_proc_reference",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Procedure: run Foundry reference calls with two former peers, one direct report, and a written summary per call.",
                "summary": "Foundry reference calls cover peers and a direct report.",
                "importance": 3,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "procedure", "references"],
            },
        ),
        "entities": (),
    },
    {
        "source": "calendar",
        "source_url": "cortex-source://calendar#service=calendar&event=1&first_event=Foundry%20panel%20loop&line=1&excerpt=foundry-calendar",
        "title": "Foundry interview events",
        "timestamp": "2025-11-14T09:00:00Z",
        "records": (
            {
                "id": "rq_x_foundry_event_kickoff",
                "kind": "event",
                "layer": "episodic",
                "content": "On 2025-11-14, Fatima Rahman kicked off the Foundry sourcing sprint for the platform role.",
                "summary": "Fatima kicked off the Foundry sourcing sprint on 2025-11-14.",
                "importance": 3,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "sourcing", "kickoff"],
                "occurred_at": "2025-11-14",
                "entity_ids": ["x_person_fatima_rahman"],
            },
            {
                "id": "rq_x_foundry_event_panel",
                "kind": "event",
                "layer": "episodic",
                "content": "On 2026-02-09, the Foundry panel loop interviewed the first three platform candidates back to back.",
                "summary": "Foundry panel loop interviewed three candidates on 2026-02-09.",
                "importance": 3,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "panel", "interviews"],
                "occurred_at": "2026-02-09",
            },
        ),
        "entities": (
            {"id": "x_person_fatima_rahman", "kind": "person", "name": "Fatima Rahman", "aliases": ["Fatima"], "context": "Foundry recruiter."},
        ),
    },
    {
        "source": "obsidian",
        "source_url": "cortex-source://obsidian#service=obsidian&note=Personal%20Health%20Log.md&line=14&excerpt=health-log",
        "title": "Personal health log",
        "timestamp": "2026-04-20T07:30:00Z",
        "records": (
            {
                "id": "rq_x_health_resting_hr",
                "kind": "claim",
                "layer": "semantic",
                "content": "Personal health baseline: morning resting heart rate averages 52 bpm when measured with the chest strap.",
                "summary": "Morning resting heart rate averages 52 bpm on the chest strap.",
                "importance": 3,
                "sector": "Personal Health",
                "topics": ["personal-health", "heart-rate", "baseline"],
            },
            {
                "id": "rq_x_health_sleep",
                "kind": "claim",
                "layer": "semantic",
                "content": "Personal health target: keep average nightly sleep above seven hours during training blocks.",
                "summary": "Keep nightly sleep above seven hours in training blocks.",
                "importance": 3,
                "sector": "Personal Health",
                "topics": ["personal-health", "sleep", "target"],
            },
            {
                "id": "rq_x_health_pref_metric",
                "kind": "preference",
                "layer": "preference",
                "content": "Preference: track workouts in metric units and kilometers, never miles.",
                "summary": "Track workouts in metric units and kilometers.",
                "importance": 3,
                "sector": "Personal Health",
                "topics": ["personal-health", "preference", "units"],
            },
            {
                "id": "rq_x_health_neg_hiit",
                "kind": "negative",
                "layer": "negative",
                "content": "Do not stack high-intensity interval sessions on consecutive recovery days.",
                "summary": "No high-intensity intervals on consecutive recovery days.",
                "importance": 4,
                "sector": "Personal Health",
                "topics": ["personal-health", "negative", "recovery"],
            },
            {
                "id": "rq_x_health_neg_freeform",
                "kind": "negative",
                "layer": "negative",
                "content": "Do not log medication doses as free-form text; use the structured dose tracker fields.",
                "summary": "Log medication doses in the structured tracker, not free text.",
                "importance": 4,
                "sector": "Personal Health",
                "topics": ["personal-health", "negative", "medication"],
            },
            {
                "id": "rq_x_health_style_journal",
                "kind": "style",
                "layer": "style",
                "content": "Health journal style: dated numeric entries with unit suffixes and no narrative filler.",
                "summary": "Health journal entries are dated, numeric, and filler-free.",
                "importance": 3,
                "sector": "Personal Health",
                "topics": ["personal-health", "style", "journal"],
            },
            {
                "id": "rq_x_health_style_coach",
                "kind": "style",
                "layer": "style",
                "content": "Coaching note style: record the physio cue verbatim, then one sentence about how the session felt.",
                "summary": "Coaching notes quote the physio cue verbatim.",
                "importance": 3,
                "sector": "Personal Health",
                "topics": ["personal-health", "style", "coaching"],
            },
            {
                "id": "rq_x_health_dec_zone2",
                "kind": "decision",
                "layer": "decision",
                "content": "Decision: base mileage stays in zone two heart rate until the autumn race block begins.",
                "summary": "Base mileage stays in zone two until the autumn race block.",
                "importance": 4,
                "sector": "Personal Health",
                "topics": ["personal-health", "decision", "training"],
            },
            {
                "id": "rq_x_health_dec_wearable",
                "kind": "decision",
                "layer": "decision",
                "content": "Decision: keep the current wearable through winter because accuracy beats the newer model battery gains.",
                "summary": "Keep the current wearable; accuracy beats battery gains.",
                "importance": 3,
                "sector": "Personal Health",
                "topics": ["personal-health", "decision", "wearable"],
            },
            {
                "id": "rq_x_health_proc_longrun",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Procedure: prepare the weekend long run with fueling every forty minutes, a charged watch, and a recovery meal planned.",
                "summary": "Weekend long runs need fueling, a charged watch, and a recovery meal.",
                "importance": 3,
                "sector": "Personal Health",
                "topics": ["personal-health", "procedure", "long-run"],
            },
            {
                "id": "rq_x_health_proc_flare",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Procedure: when the knee flares, drop mileage by half, ice twice daily, and book a physio check within a week.",
                "summary": "Knee flare protocol halves mileage and books a physio check.",
                "importance": 4,
                "sector": "Personal Health",
                "topics": ["personal-health", "procedure", "knee"],
            },
            {
                "id": "rq_x_validity_health_hydration_current",
                "kind": "decision",
                "layer": "decision",
                "content": "Current hydration target: three liters daily with electrolytes on training days.",
                "summary": "Hydration target is three liters daily with electrolytes.",
                "importance": 3,
                "sector": "Personal Health",
                "topics": ["personal-health", "hydration", "target"],
                "valid_from": EXPANDED_VALIDITY_ALWAYS_VALID_FROM,
            },
            {
                "id": "rq_x_validity_health_hydration_expired",
                "kind": "decision",
                "layer": "decision",
                "content": "Expired hydration target: two liters daily with no electrolyte plan.",
                "summary": "Expired hydration target was two liters daily.",
                "importance": 5,
                "sector": "Personal Health",
                "topics": ["personal-health", "hydration", "target"],
                "valid_to": EXPANDED_VALIDITY_EXPIRED_AT,
            },
            {
                "id": "rq_x_validity_health_hydration_superseded",
                "kind": "decision",
                "layer": "decision",
                "content": "Superseded hydration target: two and a half liters daily with electrolytes after workouts.",
                "summary": "Superseded hydration target was two and a half liters daily.",
                "importance": 5,
                "sector": "Personal Health",
                "topics": ["personal-health", "hydration", "target"],
                "superseded_by": "rq_x_validity_health_hydration_current",
            },
            {
                "id": "rq_x_validity_health_physio_current",
                "kind": "decision",
                "layer": "decision",
                "content": "Current physio cadence: one physio session every other week during the strength block.",
                "summary": "Physio cadence is one session every other week.",
                "importance": 3,
                "sector": "Personal Health",
                "topics": ["personal-health", "physio", "cadence"],
                "valid_from": EXPANDED_VALIDITY_ALWAYS_VALID_FROM,
            },
            {
                "id": "rq_x_validity_health_physio_expired",
                "kind": "decision",
                "layer": "decision",
                "content": "Expired physio cadence: weekly physio sessions during the acute phase.",
                "summary": "Expired physio cadence was weekly sessions.",
                "importance": 5,
                "sector": "Personal Health",
                "topics": ["personal-health", "physio", "cadence"],
                "valid_to": EXPANDED_VALIDITY_EXPIRED_AT,
            },
            {
                "id": "rq_x_validity_health_physio_superseded",
                "kind": "decision",
                "layer": "decision",
                "content": "Superseded physio cadence: one physio session monthly during maintenance.",
                "summary": "Superseded physio cadence was monthly sessions.",
                "importance": 5,
                "sector": "Personal Health",
                "topics": ["personal-health", "physio", "cadence"],
                "superseded_by": "rq_x_validity_health_physio_current",
            },
        ),
        "entities": (),
    },
    {
        "source": "calendar",
        "source_url": "cortex-source://calendar#service=calendar&event=1&first_event=Physio%20baseline%20assessment&line=1&excerpt=health-calendar",
        "title": "Physio schedule",
        "timestamp": "2025-12-02T08:00:00Z",
        "records": (
            {
                "id": "rq_x_health_event_assessment",
                "kind": "event",
                "layer": "episodic",
                "content": "On 2025-12-02, Tobias Lindqvist ran the baseline movement assessment and cleared strength work.",
                "summary": "Tobias ran the baseline movement assessment on 2025-12-02.",
                "importance": 3,
                "sector": "Personal Health",
                "topics": ["personal-health", "physio", "assessment"],
                "occurred_at": "2025-12-02",
                "entity_ids": ["x_person_tobias_lindqvist"],
            },
        ),
        "entities": (
            {"id": "x_person_tobias_lindqvist", "kind": "person", "name": "Tobias Lindqvist", "aliases": ["Tobias"], "context": "Physiotherapist."},
        ),
    },
    {
        "source": "github",
        "source_url": "cortex-source://github#service=github&repository=kestrel&file=docs/field-guide.md&line=5&row=2&excerpt=kestrel-field-guide",
        "title": "Kestrel field guide",
        "timestamp": "2026-02-27T13:00:00Z",
        "records": (
            {
                "id": "rq_x_kestrel_tiles",
                "kind": "claim",
                "layer": "semantic",
                "content": "Project Kestrel caches offline map tiles in an LRU disk cache capped at 512 MB per device.",
                "summary": "Kestrel caches offline map tiles in a 512 MB LRU cache.",
                "importance": 4,
                "sector": "Project Kestrel",
                "topics": ["project-kestrel", "offline", "cache"],
            },
            {
                "id": "rq_x_kestrel_neg_regression",
                "kind": "negative",
                "layer": "negative",
                "content": "Do not merge tile cache changes without running the offline regression suite on hardware.",
                "summary": "No tile cache merges without the offline regression suite.",
                "importance": 4,
                "sector": "Project Kestrel",
                "topics": ["project-kestrel", "negative", "regression"],
            },
            {
                "id": "rq_x_kestrel_style_changelog",
                "kind": "style",
                "layer": "style",
                "content": "Kestrel changelog style: one terse line per fix grouped by subsystem heading.",
                "summary": "Kestrel changelogs use one line per fix grouped by subsystem.",
                "importance": 3,
                "sector": "Project Kestrel",
                "topics": ["project-kestrel", "style", "changelog"],
            },
            {
                "id": "rq_x_kestrel_dec_log",
                "kind": "decision",
                "layer": "decision",
                "content": "Decision: Project Kestrel persists offline drafts in an append-only log file instead of a client database engine.",
                "summary": "Kestrel persists offline drafts in an append-only log.",
                "importance": 4,
                "sector": "Project Kestrel",
                "topics": ["project-kestrel", "decision", "storage"],
            },
            {
                "id": "rq_x_kestrel_dec_location",
                "kind": "decision",
                "layer": "decision",
                "content": "Decision: Project Kestrel caps background location sampling at one fix per minute to protect all-day battery.",
                "summary": "Kestrel caps location sampling at one fix per minute.",
                "importance": 4,
                "sector": "Project Kestrel",
                "topics": ["project-kestrel", "decision", "battery"],
            },
            {
                "id": "rq_x_kestrel_proc_release",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Procedure: cut a Kestrel release by freezing the branch, running the offline suite on hardware, and staging a canary to ten percent.",
                "summary": "Kestrel releases freeze the branch and stage a ten percent canary.",
                "importance": 4,
                "sector": "Project Kestrel",
                "topics": ["project-kestrel", "procedure", "release"],
            },
            {
                "id": "rq_x_kestrel_proc_fieldkit",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Procedure: pack the Kestrel field kit with two test devices, a battery brick, and the laminated offline checklist.",
                "summary": "Kestrel field kit packs two devices and a battery brick.",
                "importance": 3,
                "sector": "Project Kestrel",
                "topics": ["project-kestrel", "procedure", "field-kit"],
            },
            {
                "id": "rq_x_kestrel_dmitri",
                "kind": "event",
                "layer": "episodic",
                "content": "On 2026-02-27, Dmitri Volkov ran the Kestrel bug bash and filed nineteen offline edge cases.",
                "summary": "Dmitri filed nineteen offline edge cases in the Kestrel bug bash.",
                "importance": 3,
                "sector": "Project Kestrel",
                "topics": ["project-kestrel", "bug-bash", "qa"],
                "occurred_at": "2026-02-27",
                "entity_ids": ["x_person_dmitri_volkov"],
            },
            {
                "id": "rq_x_validity_kestrel_devices_current",
                "kind": "decision",
                "layer": "decision",
                "content": "Current Kestrel device floor: Android 13 and iOS 16 are the minimum field devices.",
                "summary": "Kestrel device floor is Android 13 and iOS 16.",
                "importance": 3,
                "sector": "Project Kestrel",
                "topics": ["project-kestrel", "devices", "floor"],
                "valid_from": EXPANDED_VALIDITY_ALWAYS_VALID_FROM,
            },
            {
                "id": "rq_x_validity_kestrel_devices_expired",
                "kind": "decision",
                "layer": "decision",
                "content": "Expired Kestrel device floor: Android 9 and iOS 12 were allowed during the prototype phase.",
                "summary": "Expired Kestrel device floor allowed Android 9 and iOS 12.",
                "importance": 5,
                "sector": "Project Kestrel",
                "topics": ["project-kestrel", "devices", "floor"],
                "valid_to": EXPANDED_VALIDITY_EXPIRED_AT,
            },
            {
                "id": "rq_x_validity_kestrel_devices_superseded",
                "kind": "decision",
                "layer": "decision",
                "content": "Superseded Kestrel device floor: Android 11 and iOS 14 with a best-effort caveat.",
                "summary": "Superseded Kestrel device floor was Android 11 and iOS 14.",
                "importance": 5,
                "sector": "Project Kestrel",
                "topics": ["project-kestrel", "devices", "floor"],
                "superseded_by": "rq_x_validity_kestrel_devices_current",
            },
            {
                "id": "rq_x_sector_kestrel_onboarding",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Onboarding steps: Project Kestrel newcomers flash a test device, join the field-log channel, and read the cache primer.",
                "summary": "Kestrel newcomers flash a device and read the cache primer.",
                "importance": 3,
                "sector": "Project Kestrel",
                "topics": ["project-kestrel", "onboarding", "steps"],
            },
            {
                "id": "rq_x_sector_foundry_onboarding",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Onboarding steps: Project Foundry newcomers shadow a recruiter screen, read the rubric handbook, and join the debrief rotation.",
                "summary": "Foundry newcomers shadow a screen and read the rubric handbook.",
                "importance": 3,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "onboarding", "steps"],
            },
            {
                "id": "rq_x_leak_larkspur_access",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Access check rule: Project Larkspur re-reviews Figma library permissions at the start of each quarter.",
                "summary": "Larkspur re-reviews Figma library permissions quarterly.",
                "importance": 3,
                "sector": "Project Larkspur",
                "topics": ["project-larkspur", "access", "permissions"],
            },
            {
                "id": "rq_x_leak_health_access",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Access check rule: Personal Health re-reviews wearable data sharing permissions at the start of each quarter.",
                "summary": "Personal Health re-reviews wearable sharing permissions quarterly.",
                "importance": 3,
                "sector": "Personal Health",
                "topics": ["personal-health", "access", "permissions"],
            },
        ),
        "entities": (
            {"id": "x_person_dmitri_volkov", "kind": "person", "name": "Dmitri Volkov", "aliases": ["Dmitri"], "context": "Kestrel QA engineer."},
        ),
    },
    {
        "source": "calendar",
        "source_url": "cortex-source://calendar#service=calendar&event=1&first_event=Kestrel%20field%20test&line=1&excerpt=kestrel-calendar",
        "title": "Kestrel field events",
        "timestamp": "2026-01-20T09:00:00Z",
        "records": (
            {
                "id": "rq_x_kestrel_event_fieldtest",
                "kind": "event",
                "layer": "episodic",
                "content": "On 2026-01-20, Yuki Tanaka led the Kestrel field test in the subway tunnel with airplane-mode devices.",
                "summary": "Yuki led the Kestrel subway field test on 2026-01-20.",
                "importance": 3,
                "sector": "Project Kestrel",
                "topics": ["project-kestrel", "field-test", "offline"],
                "occurred_at": "2026-01-20",
                "entity_ids": ["x_person_yuki_tanaka"],
            },
        ),
        "entities": (
            {"id": "x_person_yuki_tanaka", "kind": "person", "name": "Yuki Tanaka", "aliases": ["Yuki"], "context": "Kestrel mobile lead."},
        ),
    },
    {
        "source": "notion",
        "source_url": "cortex-source://notion#service=notion&page=Larkspur%20Design%20System&line=9&excerpt=larkspur-design-system",
        "title": "Larkspur design system page",
        "timestamp": "2026-04-03T12:00:00Z",
        "records": (
            {
                "id": "rq_x_larkspur_palette",
                "kind": "claim",
                "layer": "semantic",
                "content": "Project Larkspur design tokens define a four-step neutral palette and one accent hue per surface.",
                "summary": "Larkspur tokens define a four-step neutral palette and one accent hue.",
                "importance": 4,
                "sector": "Project Larkspur",
                "topics": ["project-larkspur", "tokens", "palette"],
            },
            {
                "id": "rq_x_larkspur_neg_hex",
                "kind": "negative",
                "layer": "negative",
                "content": "Do not hard-code hex colors in component styles; reference Larkspur tokens instead.",
                "summary": "No hard-coded hex colors; use Larkspur tokens.",
                "importance": 4,
                "sector": "Project Larkspur",
                "topics": ["project-larkspur", "negative", "colors"],
            },
            {
                "id": "rq_x_larkspur_style_spec",
                "kind": "style",
                "layer": "style",
                "content": "Larkspur spec style: annotated frames with redline measurements and rationale footnotes.",
                "summary": "Larkspur specs use annotated frames with redline measurements.",
                "importance": 3,
                "sector": "Project Larkspur",
                "topics": ["project-larkspur", "style", "specs"],
            },
            {
                "id": "rq_x_larkspur_dec_font",
                "kind": "decision",
                "layer": "decision",
                "content": "Decision: Project Larkspur licenses one variable font family and drops the three legacy typefaces.",
                "summary": "Larkspur licenses one variable font family.",
                "importance": 4,
                "sector": "Project Larkspur",
                "topics": ["project-larkspur", "decision", "typography"],
            },
            {
                "id": "rq_x_larkspur_dec_darkmode",
                "kind": "decision",
                "layer": "decision",
                "content": "Decision: Project Larkspur derives dark mode from tokens automatically rather than hand-tuning each screen.",
                "summary": "Larkspur derives dark mode from tokens automatically.",
                "importance": 4,
                "sector": "Project Larkspur",
                "topics": ["project-larkspur", "decision", "dark-mode"],
            },
            {
                "id": "rq_x_larkspur_proc_component",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Procedure: promote a Larkspur component by passing the contrast audit, adding usage docs, and tagging a minor version.",
                "summary": "Larkspur components need the contrast audit and usage docs.",
                "importance": 3,
                "sector": "Project Larkspur",
                "topics": ["project-larkspur", "procedure", "components"],
            },
            {
                "id": "rq_x_larkspur_hassan",
                "kind": "claim",
                "layer": "semantic",
                "content": "Hassan Odeh redrew the Larkspur icon set on a twenty-four pixel grid with two-pixel strokes.",
                "summary": "Hassan redrew the Larkspur icon set on a twenty-four pixel grid.",
                "importance": 3,
                "sector": "Project Larkspur",
                "topics": ["project-larkspur", "icons", "grid"],
                "entity_ids": ["x_person_hassan_odeh"],
            },
            {
                "id": "rq_x_validity_larkspur_browsers_current",
                "kind": "decision",
                "layer": "decision",
                "content": "Current Larkspur browser matrix: evergreen Chrome, Firefox, and Safari 17 with no legacy Edge shims.",
                "summary": "Larkspur ships to evergreen Chrome, Firefox, and Safari 17.",
                "importance": 3,
                "sector": "Project Larkspur",
                "topics": ["project-larkspur", "browsers", "matrix"],
                "valid_from": EXPANDED_VALIDITY_ALWAYS_VALID_FROM,
            },
            {
                "id": "rq_x_validity_larkspur_browsers_expired",
                "kind": "decision",
                "layer": "decision",
                "content": "Expired Larkspur browser matrix: Internet Explorer 11 with polyfill bundles.",
                "summary": "Expired Larkspur matrix included Internet Explorer 11.",
                "importance": 5,
                "sector": "Project Larkspur",
                "topics": ["project-larkspur", "browsers", "matrix"],
                "valid_to": EXPANDED_VALIDITY_EXPIRED_AT,
            },
            {
                "id": "rq_x_validity_larkspur_browsers_superseded",
                "kind": "decision",
                "layer": "decision",
                "content": "Superseded Larkspur browser matrix: Chrome and Firefox only while Safari testing was blocked.",
                "summary": "Superseded Larkspur matrix was Chrome and Firefox only.",
                "importance": 5,
                "sector": "Project Larkspur",
                "topics": ["project-larkspur", "browsers", "matrix"],
                "superseded_by": "rq_x_validity_larkspur_browsers_current",
            },
        ),
        "entities": (
            {"id": "x_person_hassan_odeh", "kind": "person", "name": "Hassan Odeh", "aliases": ["Hassan"], "context": "Larkspur designer."},
        ),
    },
    {
        "source": "calendar",
        "source_url": "cortex-source://calendar#service=calendar&event=1&first_event=Larkspur%20contrast%20audit&line=1&excerpt=larkspur-calendar",
        "title": "Larkspur audit events",
        "timestamp": "2026-04-03T15:00:00Z",
        "records": (
            {
                "id": "rq_x_larkspur_event_audit",
                "kind": "event",
                "layer": "episodic",
                "content": "On 2026-04-03, Wren Caldwell finished the Larkspur contrast audit across all forty components.",
                "summary": "Wren finished the Larkspur contrast audit on 2026-04-03.",
                "importance": 3,
                "sector": "Project Larkspur",
                "topics": ["project-larkspur", "audit", "accessibility"],
                "occurred_at": "2026-04-03",
                "entity_ids": ["x_person_wren_caldwell"],
            },
        ),
        "entities": (
            {"id": "x_person_wren_caldwell", "kind": "person", "name": "Wren Caldwell", "aliases": ["Wren"], "context": "Larkspur accessibility reviewer."},
        ),
    },
    {
        "source": "gmail",
        "source_url": "cortex-source://gmail#service=gmail&subject=Quarterly%20budget%20caps&line=4&excerpt=budget-caps",
        "title": "Quarterly budget caps",
        "timestamp": "2026-03-18T10:00:00Z",
        "records": (
            {
                "id": "rq_x_leak_foundry_budget",
                "kind": "decision",
                "layer": "decision",
                "content": "Budget cap note: Project Foundry caps agency spend at twenty percent of the hiring budget.",
                "summary": "Foundry caps agency spend at twenty percent.",
                "importance": 3,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "budget", "caps"],
            },
            {
                "id": "rq_x_leak_larkspur_budget",
                "kind": "decision",
                "layer": "decision",
                "content": "Budget cap note: Project Larkspur caps contractor spend at fifteen percent of the design budget.",
                "summary": "Larkspur caps contractor spend at fifteen percent.",
                "importance": 3,
                "sector": "Project Larkspur",
                "topics": ["project-larkspur", "budget", "caps"],
            },
            {
                "id": "rq_x_sector_health_goals",
                "kind": "claim",
                "layer": "semantic",
                "content": "Quarterly goals snapshot: Personal Health targets a faster 10k split and a steadier sleep midpoint.",
                "summary": "Personal Health targets a faster 10k split this quarter.",
                "importance": 3,
                "sector": "Personal Health",
                "topics": ["personal-health", "goals", "quarter"],
            },
            {
                "id": "rq_x_sector_granite_goals",
                "kind": "claim",
                "layer": "semantic",
                "content": "Quarterly goals snapshot: Project Granite targets zero replay drift and a shorter cutover window.",
                "summary": "Granite targets zero replay drift this quarter.",
                "importance": 3,
                "sector": "Project Granite",
                "topics": ["project-granite", "goals", "quarter"],
            },
        ),
        "entities": (),
    },
    {
        "source": "notion",
        "source_url": "cortex-source://notion#service=notion&page=Team%20Working%20Agreements&line=2&excerpt=retro-rules",
        "title": "Team working agreements",
        "timestamp": "2026-05-02T09:00:00Z",
        "records": (
            {
                "id": "rq_x_leak_kestrel_retro",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Retro notes rule: Project Kestrel files retro follow-ups in the mobile tracker within two days.",
                "summary": "Kestrel files retro follow-ups in the mobile tracker.",
                "importance": 3,
                "sector": "Project Kestrel",
                "topics": ["project-kestrel", "retro", "tracker"],
            },
            {
                "id": "rq_x_leak_foundry_retro",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Retro notes rule: Project Foundry files retro follow-ups in the hiring tracker within two days.",
                "summary": "Foundry files retro follow-ups in the hiring tracker.",
                "importance": 3,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "retro", "tracker"],
            },
        ),
        "entities": (),
    },
    {
        "source": "slack",
        "source_url": "cortex-source://slack#service=slack&channel=CGRANITE&message=1772800000000300&line=1&excerpt=granite-replay-thread",
        "title": "Granite replay thread",
        "timestamp": "2026-03-20T15:00:00Z",
        "records": (
            {
                "id": "rq_x_rel_granite_replay_decision",
                "kind": "decision",
                "layer": "decision",
                "content": "Related decision: Project Granite replays production traffic through the shadow pipeline before every cutover milestone.",
                "summary": "Granite replays production traffic through the shadow pipeline.",
                "importance": 5,
                "sector": "Project Granite",
                "topics": ["project-granite", "replay", "pipeline"],
                "entity_ids": ["x_granite_replay_pipeline"],
            },
            {
                "id": "rq_x_rel_granite_replay_procedure",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Companion procedure: warm the replica set, start the diff recorder, and archive mismatches for review.",
                "summary": "Replay companion procedure warms replicas and starts the diff recorder.",
                "importance": 3,
                "sector": "Project Granite",
                "topics": ["project-granite", "replay", "pipeline"],
                "entity_ids": ["x_granite_replay_pipeline"],
            },
        ),
        "entities": (
            {"id": "x_granite_replay_pipeline", "kind": "project", "name": "Granite replay pipeline", "aliases": ["replay pipeline"], "context": "Granite replay eval fixture."},
        ),
    },
    {
        "source": "gmail",
        "source_url": "cortex-source://gmail#service=gmail&subject=Granite%20rollback%20plan&line=5&excerpt=granite-rollback-plan",
        "title": "Granite rollback plan",
        "timestamp": "2026-04-08T10:00:00Z",
        "records": (
            {
                "id": "rq_x_rel_granite_rollback_decision",
                "kind": "decision",
                "layer": "decision",
                "content": "Related decision: Project Granite keeps a one-click rollback to the legacy warehouse until two clean rehearsals pass.",
                "summary": "Granite keeps a one-click rollback to the legacy warehouse.",
                "importance": 5,
                "sector": "Project Granite",
                "topics": ["project-granite", "rollback", "plan"],
                "entity_ids": ["x_granite_rollback_plan"],
            },
            {
                "id": "rq_x_rel_granite_rollback_procedure",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Companion procedure: snapshot the shard set, restore the standby copy, and replay the holdback journal.",
                "summary": "Rollback companion procedure restores the standby copy.",
                "importance": 3,
                "sector": "Project Granite",
                "topics": ["project-granite", "rollback", "plan"],
                "entity_ids": ["x_granite_rollback_plan"],
            },
        ),
        "entities": (
            {"id": "x_granite_rollback_plan", "kind": "project", "name": "Granite rollback plan", "aliases": ["rollback plan"], "context": "Granite rollback eval fixture."},
        ),
    },
    {
        "source": "notion",
        "source_url": "cortex-source://notion#service=notion&page=Foundry%20Interview%20Rubric&line=7&excerpt=foundry-rubric",
        "title": "Foundry interview rubric",
        "timestamp": "2026-01-28T11:00:00Z",
        "records": (
            {
                "id": "rq_x_rel_foundry_rubric_decision",
                "kind": "decision",
                "layer": "decision",
                "content": "Related decision: Project Foundry scores every panel with the shared rubric before any debrief opinions.",
                "summary": "Foundry scores panels with the shared rubric before debriefs.",
                "importance": 5,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "rubric", "panels"],
                "entity_ids": ["x_foundry_interview_rubric"],
            },
            {
                "id": "rq_x_rel_foundry_rubric_procedure",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Companion procedure: collect scorecards within a day, flag split votes, and hold the follow-up conversation.",
                "summary": "Rubric companion procedure collects scorecards and flags split votes.",
                "importance": 3,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "rubric", "panels"],
                "entity_ids": ["x_foundry_interview_rubric"],
            },
        ),
        "entities": (
            {"id": "x_foundry_interview_rubric", "kind": "project", "name": "Foundry interview rubric", "aliases": ["interview rubric"], "context": "Foundry rubric eval fixture."},
        ),
    },
    {
        "source": "gmail",
        "source_url": "cortex-source://gmail#service=gmail&subject=Foundry%20offer%20flow&line=6&excerpt=foundry-offer-flow",
        "title": "Foundry offer flow",
        "timestamp": "2026-02-18T12:00:00Z",
        "records": (
            {
                "id": "rq_x_rel_foundry_offer_decision",
                "kind": "decision",
                "layer": "decision",
                "content": "Related decision: Project Foundry sends offers only after references and the compensation band check clear.",
                "summary": "Foundry sends offers after references and the band check clear.",
                "importance": 5,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "offers", "flow"],
                "entity_ids": ["x_foundry_offer_flow"],
            },
            {
                "id": "rq_x_rel_foundry_offer_procedure",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Companion procedure: draft the letter from the template, route it for signature, and set the acceptance reminder.",
                "summary": "Offer companion procedure routes the letter for signature.",
                "importance": 3,
                "sector": "Project Foundry",
                "topics": ["project-foundry", "offers", "flow"],
                "entity_ids": ["x_foundry_offer_flow"],
            },
        ),
        "entities": (
            {"id": "x_foundry_offer_flow", "kind": "project", "name": "Foundry offer flow", "aliases": ["offer flow"], "context": "Foundry offer eval fixture."},
        ),
    },
    {
        "source": "github",
        "source_url": "cortex-source://github#service=github&repository=kestrel&file=docs/sync-design.md&line=11&row=3&excerpt=kestrel-sync-design",
        "title": "Kestrel sync design",
        "timestamp": "2026-03-30T14:00:00Z",
        "records": (
            {
                "id": "rq_x_rel_kestrel_sync_decision",
                "kind": "decision",
                "layer": "decision",
                "content": "Related decision: Project Kestrel syncs field edits with a last-writer-wins journal merged on reconnect.",
                "summary": "Kestrel syncs field edits with a last-writer-wins journal.",
                "importance": 5,
                "sector": "Project Kestrel",
                "topics": ["project-kestrel", "sync", "journal"],
                "entity_ids": ["x_kestrel_sync_protocol"],
            },
            {
                "id": "rq_x_rel_kestrel_sync_procedure",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Companion procedure: empty the outbox queue, check vector clocks, and prune tombstones after merge.",
                "summary": "Sync companion procedure empties the outbox and prunes tombstones.",
                "importance": 3,
                "sector": "Project Kestrel",
                "topics": ["project-kestrel", "sync", "journal"],
                "entity_ids": ["x_kestrel_sync_protocol"],
            },
        ),
        "entities": (
            {"id": "x_kestrel_sync_protocol", "kind": "project", "name": "Kestrel sync protocol", "aliases": ["sync protocol"], "context": "Kestrel sync eval fixture."},
        ),
    },
    {
        "source": "obsidian",
        "source_url": "cortex-source://obsidian#service=obsidian&note=Strength%20Program.md&line=3&excerpt=health-strength-program",
        "title": "Strength program note",
        "timestamp": "2026-01-06T07:00:00Z",
        "records": (
            {
                "id": "rq_x_rel_health_program_decision",
                "kind": "decision",
                "layer": "decision",
                "content": "Related decision: the personal strength program stays at three gym sessions weekly with a deload every fifth week.",
                "summary": "Strength program stays at three gym sessions weekly.",
                "importance": 5,
                "sector": "Personal Health",
                "topics": ["personal-health", "strength", "program"],
                "entity_ids": ["x_health_strength_program"],
            },
            {
                "id": "rq_x_rel_health_program_procedure",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Companion procedure: warm up ten minutes, log bar weights immediately, and stretch hips afterwards.",
                "summary": "Strength companion procedure logs bar weights immediately.",
                "importance": 3,
                "sector": "Personal Health",
                "topics": ["personal-health", "strength", "program"],
                "entity_ids": ["x_health_strength_program"],
            },
        ),
        "entities": (
            {"id": "x_health_strength_program", "kind": "project", "name": "Strength program", "aliases": ["gym program"], "context": "Personal health strength eval fixture."},
        ),
    },
    {
        "source": "notion",
        "source_url": "cortex-source://notion#service=notion&page=Larkspur%20Token%20Rollout&line=4&excerpt=larkspur-token-rollout",
        "title": "Larkspur token rollout",
        "timestamp": "2026-05-12T13:00:00Z",
        "records": (
            {
                "id": "rq_x_rel_larkspur_rollout_decision",
                "kind": "decision",
                "layer": "decision",
                "content": "Related decision: Project Larkspur ships token updates behind a theme flag with one pilot squad first.",
                "summary": "Larkspur ships token updates behind a theme flag.",
                "importance": 5,
                "sector": "Project Larkspur",
                "topics": ["project-larkspur", "rollout", "tokens"],
                "entity_ids": ["x_larkspur_token_rollout"],
            },
            {
                "id": "rq_x_rel_larkspur_rollout_procedure",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Companion procedure: bump the package version, regenerate the gallery, and screenshot-diff affected components.",
                "summary": "Rollout companion procedure regenerates the gallery and screenshot-diffs.",
                "importance": 3,
                "sector": "Project Larkspur",
                "topics": ["project-larkspur", "rollout", "tokens"],
                "entity_ids": ["x_larkspur_token_rollout"],
            },
        ),
        "entities": (
            {"id": "x_larkspur_token_rollout", "kind": "project", "name": "Larkspur token rollout", "aliases": ["token rollout"], "context": "Larkspur rollout eval fixture."},
        ),
    },
    {
        "source": "obsidian",
        "source_url": "cortex-source://obsidian#service=obsidian&note=Race%20Plan.md&line=2&excerpt=health-race-plan",
        "title": "Race plan note",
        "timestamp": "2026-06-01T06:30:00Z",
        "records": (
            {
                "id": "rq_x_rel_health_race_decision",
                "kind": "decision",
                "layer": "decision",
                "content": "Related decision: the autumn 10k race plan prioritizes negative splits over a new shoe experiment.",
                "summary": "The autumn 10k race plan prioritizes negative splits.",
                "importance": 5,
                "sector": "Personal Health",
                "topics": ["personal-health", "race", "plan"],
                "entity_ids": ["x_health_race_plan"],
            },
            {
                "id": "rq_x_rel_health_race_procedure",
                "kind": "procedure",
                "layer": "procedural",
                "content": "Companion procedure: taper the final ten days, rehearse fueling on long runs, and lay out the kit the night before.",
                "summary": "Race companion procedure tapers ten days and rehearses fueling.",
                "importance": 3,
                "sector": "Personal Health",
                "topics": ["personal-health", "race", "plan"],
                "entity_ids": ["x_health_race_plan"],
            },
        ),
        "entities": (
            {"id": "x_health_race_plan", "kind": "project", "name": "Autumn race plan", "aliases": ["race plan"], "context": "Personal health race eval fixture."},
        ),
    },
)

EXPANDED_SEED_MEMORY_COUNT = sum(len(capture["records"]) for capture in EXPANDED_SEED_CAPTURES)


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
    # ------------------------------------------------------------------
    # Expanded multi-source corpus cases (targets seeded from
    # EXPANDED_SEED_CAPTURES). Existing cases above are frozen; only add
    # below this line.
    # ------------------------------------------------------------------
    # focused ----------------------------------------------------------
    RetrievalCase(
        name="focused_granite_shard_layout",
        query="Granite legacy Postgres warehouse per-tenant shards",
        expected_id="rq_x_granite_shards",
        expected_layer="semantic",
        expected_phrase="per-tenant SQLite shards",
    ),
    RetrievalCase(
        name="focused_granite_vendor_contract",
        query="Granite vendor contract replication tooling",
        expected_id="rq_x_granite_vendor",
        expected_layer="semantic",
        expected_phrase="fiscal 2027",
    ),
    RetrievalCase(
        name="focused_foundry_pipeline_stages",
        query="Foundry candidate pipeline panel loop board",
        expected_id="rq_x_foundry_pipeline",
        expected_layer="semantic",
        expected_phrase="one pipeline board",
    ),
    RetrievalCase(
        name="focused_foundry_referral_rewards",
        query="Foundry referral rewards ninety days",
        expected_id="rq_x_foundry_referral",
        expected_layer="semantic",
        expected_phrase="ninety days",
    ),
    RetrievalCase(
        name="focused_foundry_afternoon_interviews",
        query="Foundry interviews afternoon block",
        expected_id="rq_x_foundry_pref_afternoon",
        expected_layer="preference",
        expected_phrase="afternoon block",
    ),
    RetrievalCase(
        name="focused_health_resting_heart_rate",
        query="resting heart rate baseline chest strap",
        expected_id="rq_x_health_resting_hr",
        expected_layer="semantic",
        expected_phrase="52 bpm",
    ),
    RetrievalCase(
        name="focused_health_sleep_target",
        query="nightly sleep target training blocks",
        expected_id="rq_x_health_sleep",
        expected_layer="semantic",
        expected_phrase="above seven hours",
    ),
    RetrievalCase(
        name="focused_health_metric_units",
        query="track workouts metric kilometers",
        expected_id="rq_x_health_pref_metric",
        expected_layer="preference",
        expected_phrase="never miles",
    ),
    RetrievalCase(
        name="focused_kestrel_tile_cache",
        query="Kestrel offline map tiles disk cache",
        expected_id="rq_x_kestrel_tiles",
        expected_layer="semantic",
        expected_phrase="capped at 512 MB",
    ),
    RetrievalCase(
        name="focused_larkspur_token_palette",
        query="Larkspur design tokens neutral palette accent hue",
        expected_id="rq_x_larkspur_palette",
        expected_layer="semantic",
        expected_phrase="four-step neutral palette",
    ),
    # paraphrase -------------------------------------------------------
    RetrievalCase(
        name="paraphrase_granite_checksum_manifest",
        query="Granite warehouse checksum manifest batch",
        expected_id="rq_x_granite_shards",
        expected_layer="semantic",
        expected_phrase="checksum manifest",
        category="paraphrase",
    ),
    RetrievalCase(
        name="paraphrase_foundry_offer_approval",
        query="Foundry recruiter screen offer approval",
        expected_id="rq_x_foundry_pipeline",
        expected_layer="semantic",
        expected_phrase="offer approval",
        category="paraphrase",
    ),
    RetrievalCase(
        name="paraphrase_health_morning_heart_rate",
        query="morning resting heart rate 52 bpm",
        expected_id="rq_x_health_resting_hr",
        expected_layer="semantic",
        expected_phrase="morning resting heart rate",
        category="paraphrase",
    ),
    RetrievalCase(
        name="paraphrase_kestrel_device_cache",
        query="Kestrel offline tiles cache device",
        expected_id="rq_x_kestrel_tiles",
        expected_layer="semantic",
        expected_phrase="LRU disk cache",
        category="paraphrase",
    ),
    RetrievalCase(
        name="paraphrase_larkspur_accent_surface",
        query="Larkspur accent hue surface palette",
        expected_id="rq_x_larkspur_palette",
        expected_layer="semantic",
        expected_phrase="accent hue per surface",
        category="paraphrase",
    ),
    RetrievalCase(
        name="paraphrase_granite_vendor_tooling",
        query="Saoirse Quinn replication tooling fiscal 2027",
        expected_id="rq_x_granite_vendor",
        expected_layer="semantic",
        expected_phrase="vendor contract",
        category="paraphrase",
    ),
    RetrievalCase(
        name="paraphrase_foundry_referral_payout",
        query="Foundry referral new hire starts",
        expected_id="rq_x_foundry_referral",
        expected_layer="semantic",
        expected_phrase="referral rewards",
        category="paraphrase",
    ),
    RetrievalCase(
        name="paraphrase_health_sleep_hours",
        query="average nightly sleep seven hours",
        expected_id="rq_x_health_sleep",
        expected_layer="semantic",
        expected_phrase="nightly sleep",
        category="paraphrase",
    ),
    # negative_recall --------------------------------------------------
    RetrievalCase(
        name="negative_granite_backfills",
        query="destructive backfills tenant shards snapshot",
        expected_id="rq_x_granite_neg_backfill",
        expected_layer="negative",
        expected_phrase="destructive backfills",
        category="negative_recall",
    ),
    RetrievalCase(
        name="negative_granite_dashboards",
        query="analytics dashboards primary shards cutover",
        expected_id="rq_x_granite_neg_dashboards",
        expected_layer="negative",
        expected_phrase="analytics dashboards",
        category="negative_recall",
    ),
    RetrievalCase(
        name="negative_foundry_compensation",
        query="compensation numbers recruiter screens",
        expected_id="rq_x_foundry_neg_comp",
        expected_layer="negative",
        expected_phrase="quote compensation numbers",
        category="negative_recall",
    ),
    RetrievalCase(
        name="negative_health_hiit_recovery",
        query="high-intensity interval sessions recovery days",
        expected_id="rq_x_health_neg_hiit",
        expected_layer="negative",
        expected_phrase="consecutive recovery days",
        category="negative_recall",
    ),
    RetrievalCase(
        name="negative_health_medication_log",
        query="medication doses structured tracker",
        expected_id="rq_x_health_neg_freeform",
        expected_layer="negative",
        expected_phrase="free-form text",
        category="negative_recall",
    ),
    RetrievalCase(
        name="negative_kestrel_regression",
        query="tile cache changes offline regression hardware",
        expected_id="rq_x_kestrel_neg_regression",
        expected_layer="negative",
        expected_phrase="offline regression suite",
        category="negative_recall",
    ),
    RetrievalCase(
        name="negative_larkspur_hex_colors",
        query="hard-code hex colors component tokens",
        expected_id="rq_x_larkspur_neg_hex",
        expected_layer="negative",
        expected_phrase="hard-code hex colors",
        category="negative_recall",
    ),
    # style_recall -----------------------------------------------------
    RetrievalCase(
        name="style_granite_runbook_voice",
        query="migration runbook numbered imperative voice command",
        expected_id="rq_x_granite_style_runbook",
        expected_layer="style",
        expected_phrase="imperative voice",
        category="style_recall",
    ),
    RetrievalCase(
        name="style_granite_status_notes",
        query="status note digest shard counts blocking",
        expected_id="rq_x_granite_style_status",
        expected_layer="style",
        expected_phrase="blocking-issues line",
        category="style_recall",
    ),
    RetrievalCase(
        name="style_foundry_outreach",
        query="hiring outreach plain sentences buzzwords",
        expected_id="rq_x_foundry_style_outreach",
        expected_layer="style",
        expected_phrase="zero buzzwords",
        category="style_recall",
    ),
    RetrievalCase(
        name="style_foundry_feedback_verdict",
        query="interview feedback verdict observed behaviors",
        expected_id="rq_x_foundry_style_feedback",
        expected_layer="style",
        expected_phrase="verdict first",
        category="style_recall",
    ),
    RetrievalCase(
        name="style_health_journal_entries",
        query="health journal dated numeric entries",
        expected_id="rq_x_health_style_journal",
        expected_layer="style",
        expected_phrase="no narrative filler",
        category="style_recall",
    ),
    RetrievalCase(
        name="style_health_coaching_notes",
        query="coaching note physio cue verbatim",
        expected_id="rq_x_health_style_coach",
        expected_layer="style",
        expected_phrase="physio cue verbatim",
        category="style_recall",
    ),
    RetrievalCase(
        name="style_kestrel_changelog",
        query="changelog terse line subsystem heading",
        expected_id="rq_x_kestrel_style_changelog",
        expected_layer="style",
        expected_phrase="grouped by subsystem",
        category="style_recall",
    ),
    RetrievalCase(
        name="style_larkspur_spec_frames",
        query="annotated frames redline measurements footnotes",
        expected_id="rq_x_larkspur_style_spec",
        expected_layer="style",
        expected_phrase="rationale footnotes",
        category="style_recall",
    ),
    # temporal_recall --------------------------------------------------
    RetrievalCase(
        name="temporal_granite_dryrun_day",
        query="what happened on 2026-03-12 Granite warehouse dry-run",
        expected_id="rq_x_granite_event_dryrun",
        expected_layer="episodic",
        expected_phrase="staging enclave",
        category="temporal_recall",
        expected_occurred_at="2026-03-12",
    ),
    RetrievalCase(
        name="temporal_granite_rehearsal_day",
        query="what happened on 2026-06-11 Granite rehearsal production traffic",
        expected_id="rq_x_granite_event_rehearsal",
        expected_layer="episodic",
        expected_phrase="without drift",
        category="temporal_recall",
        expected_occurred_at="2026-06-11",
    ),
    RetrievalCase(
        name="temporal_granite_freeze_month",
        query="Granite schema freeze May 2026",
        expected_id="rq_x_granite_event_freeze",
        expected_layer="decision",
        expected_phrase="schema freeze",
        category="temporal_recall",
    ),
    RetrievalCase(
        name="temporal_foundry_kickoff_month",
        query="Foundry sourcing sprint November 2025 platform role",
        expected_id="rq_x_foundry_event_kickoff",
        expected_layer="episodic",
        expected_phrase="sourcing sprint",
        category="temporal_recall",
    ),
    RetrievalCase(
        name="temporal_foundry_panel_day",
        query="what happened on 2026-02-09 Foundry panel candidates",
        expected_id="rq_x_foundry_event_panel",
        expected_layer="episodic",
        expected_phrase="back to back",
        category="temporal_recall",
        expected_occurred_at="2026-02-09",
    ),
    RetrievalCase(
        name="temporal_health_assessment_month",
        query="baseline movement assessment December 2025",
        expected_id="rq_x_health_event_assessment",
        expected_layer="episodic",
        expected_phrase="cleared strength work",
        category="temporal_recall",
    ),
    RetrievalCase(
        name="temporal_kestrel_fieldtest_day",
        query="what happened on 2026-01-20 Kestrel subway field test",
        expected_id="rq_x_kestrel_event_fieldtest",
        expected_layer="episodic",
        expected_phrase="airplane-mode devices",
        category="temporal_recall",
        expected_occurred_at="2026-01-20",
    ),
    RetrievalCase(
        name="temporal_larkspur_audit_month",
        query="Larkspur contrast audit April 2026 components",
        expected_id="rq_x_larkspur_event_audit",
        expected_layer="episodic",
        expected_phrase="forty components",
        category="temporal_recall",
    ),
    # sector_scoping ---------------------------------------------------
    RetrievalCase(
        name="sector_scope_granite_escalation",
        query="escalation runbook data on-call status thread",
        expected_id="rq_x_sector_granite_escalation",
        expected_layer="procedural",
        expected_phrase="freezes the replay queue",
        category="sector_scoping",
        sector="Project Granite",
        disallowed_ids=("rq_x_sector_kestrel_escalation",),
        source_url_contains=("cortex-source://slack#service=slack", "channel=", "excerpt="),
    ),
    RetrievalCase(
        name="sector_scope_kestrel_escalation",
        query="escalation runbook mobile on-call status thread",
        expected_id="rq_x_sector_kestrel_escalation",
        expected_layer="procedural",
        expected_phrase="disables background sync",
        category="sector_scoping",
        sector="Project Kestrel",
        disallowed_ids=("rq_x_sector_granite_escalation",),
        source_url_contains=("cortex-source://slack#service=slack", "channel=", "excerpt="),
    ),
    RetrievalCase(
        name="sector_scope_foundry_weekly",
        query="weekly review agenda stalled candidate panels",
        expected_id="rq_x_sector_foundry_weekly",
        expected_layer="procedural",
        expected_phrase="ages every stalled candidate",
        category="sector_scoping",
        sector="Project Foundry",
        disallowed_ids=("rq_x_sector_larkspur_weekly",),
        source_url_contains=("cortex-source://notion#service=notion", "page=", "excerpt="),
    ),
    RetrievalCase(
        name="sector_scope_larkspur_weekly",
        query="weekly review agenda token backlog critiques",
        expected_id="rq_x_sector_larkspur_weekly",
        expected_layer="procedural",
        expected_phrase="books design critiques",
        category="sector_scoping",
        sector="Project Larkspur",
        disallowed_ids=("rq_x_sector_foundry_weekly",),
        source_url_contains=("cortex-source://notion#service=notion", "page=", "excerpt="),
    ),
    RetrievalCase(
        name="sector_scope_health_goals",
        query="quarterly goals snapshot sleep midpoint",
        expected_id="rq_x_sector_health_goals",
        expected_layer="semantic",
        expected_phrase="steadier sleep midpoint",
        category="sector_scoping",
        sector="Personal Health",
        disallowed_ids=("rq_x_sector_granite_goals",),
        source_url_contains=("cortex-source://gmail#service=gmail", "subject=", "excerpt="),
    ),
    RetrievalCase(
        name="sector_scope_granite_goals",
        query="quarterly goals snapshot replay drift cutover",
        expected_id="rq_x_sector_granite_goals",
        expected_layer="semantic",
        expected_phrase="zero replay drift",
        category="sector_scoping",
        sector="Project Granite",
        disallowed_ids=("rq_x_sector_health_goals",),
        source_url_contains=("cortex-source://gmail#service=gmail", "subject=", "excerpt="),
    ),
    RetrievalCase(
        name="sector_scope_kestrel_onboarding",
        query="onboarding steps flash test device cache primer",
        expected_id="rq_x_sector_kestrel_onboarding",
        expected_layer="procedural",
        expected_phrase="read the cache primer",
        category="sector_scoping",
        sector="Project Kestrel",
        disallowed_ids=("rq_x_sector_foundry_onboarding",),
        source_url_contains=("cortex-source://github#service=github", "file=", "excerpt="),
    ),
    RetrievalCase(
        name="sector_scope_foundry_onboarding",
        query="onboarding steps rubric handbook debrief rotation",
        expected_id="rq_x_sector_foundry_onboarding",
        expected_layer="procedural",
        expected_phrase="rubric handbook",
        category="sector_scoping",
        sector="Project Foundry",
        disallowed_ids=("rq_x_sector_kestrel_onboarding",),
        source_url_contains=("cortex-source://github#service=github", "file=", "excerpt="),
    ),
    # related_memory ---------------------------------------------------
    RetrievalCase(
        name="related_granite_replay_pipeline",
        query="Granite replay production traffic shadow pipeline milestone",
        expected_id="rq_x_rel_granite_replay_decision",
        expected_layer="decision",
        expected_phrase="shadow pipeline",
        category="related_memory",
        sector="Project Granite",
        include_related=True,
        expected_related_id="rq_x_rel_granite_replay_procedure",
        expected_related_layer="procedural",
        expected_related_phrase="diff recorder",
        expected_relationship_kind="shared_entity",
        source_url_contains=("cortex-source://slack#service=slack", "excerpt=granite-replay-thread"),
    ),
    RetrievalCase(
        name="related_granite_rollback_plan",
        query="Granite one-click rollback legacy warehouse rehearsals",
        expected_id="rq_x_rel_granite_rollback_decision",
        expected_layer="decision",
        expected_phrase="one-click rollback",
        category="related_memory",
        sector="Project Granite",
        include_related=True,
        expected_related_id="rq_x_rel_granite_rollback_procedure",
        expected_related_layer="procedural",
        expected_related_phrase="standby copy",
        expected_relationship_kind="shared_entity",
        source_url_contains=("cortex-source://gmail#service=gmail", "excerpt=granite-rollback-plan"),
    ),
    RetrievalCase(
        name="related_foundry_rubric",
        query="Foundry panel shared rubric debrief opinions",
        expected_id="rq_x_rel_foundry_rubric_decision",
        expected_layer="decision",
        expected_phrase="shared rubric",
        category="related_memory",
        sector="Project Foundry",
        include_related=True,
        expected_related_id="rq_x_rel_foundry_rubric_procedure",
        expected_related_layer="procedural",
        expected_related_phrase="flag split votes",
        expected_relationship_kind="shared_entity",
        source_url_contains=("cortex-source://notion#service=notion", "excerpt=foundry-rubric"),
    ),
    RetrievalCase(
        name="related_foundry_offer_flow",
        query="Foundry offers references compensation band clear",
        expected_id="rq_x_rel_foundry_offer_decision",
        expected_layer="decision",
        expected_phrase="compensation band check",
        category="related_memory",
        sector="Project Foundry",
        include_related=True,
        expected_related_id="rq_x_rel_foundry_offer_procedure",
        expected_related_layer="procedural",
        expected_related_phrase="route it for signature",
        expected_relationship_kind="shared_entity",
        source_url_contains=("cortex-source://gmail#service=gmail", "excerpt=foundry-offer-flow"),
    ),
    RetrievalCase(
        name="related_kestrel_sync_protocol",
        query="Kestrel field edits last-writer-wins reconnect",
        expected_id="rq_x_rel_kestrel_sync_decision",
        expected_layer="decision",
        expected_phrase="last-writer-wins",
        category="related_memory",
        sector="Project Kestrel",
        include_related=True,
        expected_related_id="rq_x_rel_kestrel_sync_procedure",
        expected_related_layer="procedural",
        expected_related_phrase="prune tombstones",
        expected_relationship_kind="shared_entity",
        source_url_contains=("cortex-source://github#service=github", "excerpt=kestrel-sync-design"),
    ),
    RetrievalCase(
        name="related_health_strength_program",
        query="strength program three gym sessions deload",
        expected_id="rq_x_rel_health_program_decision",
        expected_layer="decision",
        expected_phrase="deload every fifth week",
        category="related_memory",
        sector="Personal Health",
        include_related=True,
        expected_related_id="rq_x_rel_health_program_procedure",
        expected_related_layer="procedural",
        expected_related_phrase="log bar weights",
        expected_relationship_kind="shared_entity",
        source_url_contains=("cortex-source://obsidian#service=obsidian", "excerpt=health-strength-program"),
    ),
    RetrievalCase(
        name="related_larkspur_token_rollout",
        query="Larkspur token updates theme flag pilot squad",
        expected_id="rq_x_rel_larkspur_rollout_decision",
        expected_layer="decision",
        expected_phrase="theme flag",
        category="related_memory",
        sector="Project Larkspur",
        include_related=True,
        expected_related_id="rq_x_rel_larkspur_rollout_procedure",
        expected_related_layer="procedural",
        expected_related_phrase="screenshot-diff",
        expected_relationship_kind="shared_entity",
        source_url_contains=("cortex-source://notion#service=notion", "excerpt=larkspur-token-rollout"),
    ),
    RetrievalCase(
        name="related_health_race_plan",
        query="autumn 10k race plan negative splits",
        expected_id="rq_x_rel_health_race_decision",
        expected_layer="decision",
        expected_phrase="negative splits",
        category="related_memory",
        sector="Personal Health",
        include_related=True,
        expected_related_id="rq_x_rel_health_race_procedure",
        expected_related_layer="procedural",
        expected_related_phrase="rehearse fueling",
        expected_relationship_kind="shared_entity",
        source_url_contains=("cortex-source://obsidian#service=obsidian", "excerpt=health-race-plan"),
    ),
    # temporal_validity ------------------------------------------------
    RetrievalCase(
        name="validity_granite_retention",
        query="Granite replay retention window",
        expected_id="rq_x_validity_granite_retention_current",
        expected_layer="decision",
        expected_phrase="fourteen days",
        category="temporal_validity",
        disallowed_ids=(
            "rq_x_validity_granite_retention_expired",
            "rq_x_validity_granite_retention_superseded",
        ),
    ),
    RetrievalCase(
        name="validity_granite_cutover_window",
        query="Granite cutover window two-hour drain",
        expected_id="rq_x_validity_granite_cutover_current",
        expected_layer="decision",
        expected_phrase="two-hour drain",
        category="temporal_validity",
        disallowed_ids=(
            "rq_x_validity_granite_cutover_expired",
            "rq_x_validity_granite_cutover_superseded",
        ),
    ),
    RetrievalCase(
        name="validity_granite_alert_threshold",
        query="Granite drift alert threshold on-call",
        expected_id="rq_x_validity_granite_alert_current",
        expected_layer="decision",
        expected_phrase="0.5 percent",
        category="temporal_validity",
        disallowed_ids=(
            "rq_x_validity_granite_alert_expired",
            "rq_x_validity_granite_alert_superseded",
        ),
    ),
    RetrievalCase(
        name="validity_foundry_compensation_bands",
        query="Foundry compensation bands location factors",
        expected_id="rq_x_validity_foundry_comp_current",
        expected_layer="decision",
        expected_phrase="level three to level five",
        category="temporal_validity",
        disallowed_ids=(
            "rq_x_validity_foundry_comp_expired",
            "rq_x_validity_foundry_comp_superseded",
        ),
    ),
    RetrievalCase(
        name="validity_foundry_headcount",
        query="Foundry headcount plan platform hires fiscal",
        expected_id="rq_x_validity_foundry_headcount_current",
        expected_layer="decision",
        expected_phrase="six platform hires",
        category="temporal_validity",
        disallowed_ids=(
            "rq_x_validity_foundry_headcount_expired",
            "rq_x_validity_foundry_headcount_superseded",
        ),
    ),
    RetrievalCase(
        name="validity_health_hydration",
        query="hydration target liters electrolytes",
        expected_id="rq_x_validity_health_hydration_current",
        expected_layer="decision",
        expected_phrase="three liters daily",
        category="temporal_validity",
        disallowed_ids=(
            "rq_x_validity_health_hydration_expired",
            "rq_x_validity_health_hydration_superseded",
        ),
    ),
    RetrievalCase(
        name="validity_health_physio_cadence",
        query="physio cadence strength block week",
        expected_id="rq_x_validity_health_physio_current",
        expected_layer="decision",
        expected_phrase="every other week",
        category="temporal_validity",
        disallowed_ids=(
            "rq_x_validity_health_physio_expired",
            "rq_x_validity_health_physio_superseded",
        ),
    ),
    RetrievalCase(
        name="validity_kestrel_device_floor",
        query="Kestrel minimum field devices Android iOS",
        expected_id="rq_x_validity_kestrel_devices_current",
        expected_layer="decision",
        expected_phrase="Android 13 and iOS 16",
        category="temporal_validity",
        disallowed_ids=(
            "rq_x_validity_kestrel_devices_expired",
            "rq_x_validity_kestrel_devices_superseded",
        ),
    ),
    RetrievalCase(
        name="validity_larkspur_browser_matrix",
        query="Larkspur browser matrix Chrome Safari",
        expected_id="rq_x_validity_larkspur_browsers_current",
        expected_layer="decision",
        expected_phrase="no legacy Edge shims",
        category="temporal_validity",
        disallowed_ids=(
            "rq_x_validity_larkspur_browsers_expired",
            "rq_x_validity_larkspur_browsers_superseded",
        ),
    ),
    # cross_project_no_leak --------------------------------------------
    RetrievalCase(
        name="no_leak_granite_deploy_freeze",
        query="Granite deploy freeze rule blocks week",
        expected_id="rq_x_leak_granite_freeze",
        expected_layer="decision",
        expected_phrase="replay verification week",
        category="cross_project_no_leak",
        sector="Project Granite",
        disallowed_ids=("rq_x_leak_kestrel_freeze",),
    ),
    RetrievalCase(
        name="no_leak_kestrel_deploy_freeze",
        query="Kestrel deploy freeze rule blocks week",
        expected_id="rq_x_leak_kestrel_freeze",
        expected_layer="decision",
        expected_phrase="field pilot week",
        category="cross_project_no_leak",
        sector="Project Kestrel",
        disallowed_ids=("rq_x_leak_granite_freeze",),
    ),
    RetrievalCase(
        name="no_leak_foundry_budget_cap",
        query="Foundry budget cap agency spend percent",
        expected_id="rq_x_leak_foundry_budget",
        expected_layer="decision",
        expected_phrase="agency spend",
        category="cross_project_no_leak",
        sector="Project Foundry",
        disallowed_ids=("rq_x_leak_larkspur_budget",),
    ),
    RetrievalCase(
        name="no_leak_larkspur_budget_cap",
        query="Larkspur budget cap contractor spend percent",
        expected_id="rq_x_leak_larkspur_budget",
        expected_layer="decision",
        expected_phrase="contractor spend",
        category="cross_project_no_leak",
        sector="Project Larkspur",
        disallowed_ids=("rq_x_leak_foundry_budget",),
    ),
    RetrievalCase(
        name="no_leak_health_morning_block",
        query="morning block rule reserves training meetings",
        expected_id="rq_x_leak_health_morning",
        expected_layer="semantic",
        expected_phrase="06:30 to 07:30",
        category="cross_project_no_leak",
        sector="Personal Health",
        disallowed_ids=("rq_x_leak_granite_morning",),
    ),
    RetrievalCase(
        name="no_leak_granite_morning_block",
        query="morning block rule reserves migration standup",
        expected_id="rq_x_leak_granite_morning",
        expected_layer="semantic",
        expected_phrase="09:00 to 09:15",
        category="cross_project_no_leak",
        sector="Project Granite",
        disallowed_ids=("rq_x_leak_health_morning",),
    ),
    RetrievalCase(
        name="no_leak_kestrel_retro_tracker",
        query="retro notes rule mobile tracker",
        expected_id="rq_x_leak_kestrel_retro",
        expected_layer="procedural",
        expected_phrase="mobile tracker",
        category="cross_project_no_leak",
        sector="Project Kestrel",
        disallowed_ids=("rq_x_leak_foundry_retro",),
    ),
    RetrievalCase(
        name="no_leak_foundry_retro_tracker",
        query="retro notes rule hiring tracker",
        expected_id="rq_x_leak_foundry_retro",
        expected_layer="procedural",
        expected_phrase="hiring tracker",
        category="cross_project_no_leak",
        sector="Project Foundry",
        disallowed_ids=("rq_x_leak_kestrel_retro",),
    ),
    RetrievalCase(
        name="no_leak_larkspur_access_review",
        query="access check rule Figma library permissions quarter",
        expected_id="rq_x_leak_larkspur_access",
        expected_layer="procedural",
        expected_phrase="Figma library permissions",
        category="cross_project_no_leak",
        sector="Project Larkspur",
        disallowed_ids=("rq_x_leak_health_access",),
    ),
    RetrievalCase(
        name="no_leak_health_access_review",
        query="access check rule wearable sharing permissions quarter",
        expected_id="rq_x_leak_health_access",
        expected_layer="procedural",
        expected_phrase="wearable data sharing",
        category="cross_project_no_leak",
        sector="Personal Health",
        disallowed_ids=("rq_x_leak_larkspur_access",),
    ),
    # entity_recall ----------------------------------------------------
    RetrievalCase(
        name="entity_marcus_bell_capacity",
        query="Marcus Bell Granite replica fleet twelve nodes",
        expected_id="rq_x_granite_marcus",
        expected_layer="semantic",
        expected_phrase="twelve nodes",
        category="entity_recall",
    ),
    RetrievalCase(
        name="entity_ingrid_halvorsen_freeze",
        query="Ingrid Halvorsen Granite schema freeze",
        expected_id="rq_x_granite_event_freeze",
        expected_layer="decision",
        expected_phrase="dry-run diff",
        category="entity_recall",
    ),
    RetrievalCase(
        name="entity_saoirse_quinn_vendor",
        query="Saoirse Quinn Granite vendor contract",
        expected_id="rq_x_granite_vendor",
        expected_layer="semantic",
        expected_phrase="Saoirse Quinn",
        category="entity_recall",
    ),
    RetrievalCase(
        name="entity_fatima_rahman_sourcing",
        query="Fatima Rahman Foundry sourcing platform",
        expected_id="rq_x_foundry_event_kickoff",
        expected_layer="episodic",
        expected_phrase="Fatima Rahman",
        category="entity_recall",
    ),
    RetrievalCase(
        name="entity_beatriz_sousa_scorecards",
        query="Beatriz Sousa Foundry scorecard template",
        expected_id="rq_x_foundry_beatriz",
        expected_layer="semantic",
        expected_phrase="scorecard template",
        category="entity_recall",
    ),
    RetrievalCase(
        name="entity_tobias_lindqvist_assessment",
        query="Tobias Lindqvist baseline movement assessment",
        expected_id="rq_x_health_event_assessment",
        expected_layer="episodic",
        expected_phrase="Tobias Lindqvist",
        category="entity_recall",
    ),
    RetrievalCase(
        name="entity_yuki_tanaka_fieldtest",
        query="Yuki Tanaka Kestrel field test subway tunnel",
        expected_id="rq_x_kestrel_event_fieldtest",
        expected_layer="episodic",
        expected_phrase="Yuki Tanaka",
        category="entity_recall",
    ),
    RetrievalCase(
        name="entity_dmitri_volkov_bugbash",
        query="Dmitri Volkov Kestrel bug bash offline",
        expected_id="rq_x_kestrel_dmitri",
        expected_layer="episodic",
        expected_phrase="nineteen offline edge cases",
        category="entity_recall",
    ),
    RetrievalCase(
        name="entity_hassan_odeh_icons",
        query="Hassan Odeh Larkspur icon set pixel grid",
        expected_id="rq_x_larkspur_hassan",
        expected_layer="semantic",
        expected_phrase="twenty-four pixel grid",
        category="entity_recall",
    ),
    RetrievalCase(
        name="entity_wren_caldwell_audit",
        query="Wren Caldwell Larkspur contrast audit",
        expected_id="rq_x_larkspur_event_audit",
        expected_layer="episodic",
        expected_phrase="Wren Caldwell",
        category="entity_recall",
    ),
    # decision_recall --------------------------------------------------
    RetrievalCase(
        name="decision_granite_dualwrite",
        query="Granite dual-writes legacy sharded stores shadow reads",
        expected_id="rq_x_granite_dec_dualwrite",
        expected_layer="decision",
        expected_phrase="dual-writes",
        category="decision_recall",
    ),
    RetrievalCase(
        name="decision_granite_replay_differ",
        query="Granite replay differ in-house vendor comparison",
        expected_id="rq_x_granite_dec_differ",
        expected_layer="decision",
        expected_phrase="replay differ",
        category="decision_recall",
    ),
    RetrievalCase(
        name="decision_foundry_takehome",
        query="Foundry take-home paired ninety-minute working session",
        expected_id="rq_x_foundry_dec_takehome",
        expected_layer="decision",
        expected_phrase="ninety-minute working session",
        category="decision_recall",
    ),
    RetrievalCase(
        name="decision_foundry_applicant_tracker",
        query="Foundry applicant tracker quarterly cycles",
        expected_id="rq_x_foundry_dec_ats",
        expected_layer="decision",
        expected_phrase="applicant tracker",
        category="decision_recall",
    ),
    RetrievalCase(
        name="decision_health_zone_two",
        query="base mileage zone two heart rate autumn",
        expected_id="rq_x_health_dec_zone2",
        expected_layer="decision",
        expected_phrase="zone two",
        category="decision_recall",
    ),
    RetrievalCase(
        name="decision_health_wearable",
        query="wearable winter accuracy battery gains",
        expected_id="rq_x_health_dec_wearable",
        expected_layer="decision",
        expected_phrase="battery gains",
        category="decision_recall",
    ),
    RetrievalCase(
        name="decision_kestrel_append_log",
        query="Kestrel append-only log client database engine",
        expected_id="rq_x_kestrel_dec_log",
        expected_layer="decision",
        expected_phrase="append-only log",
        category="decision_recall",
    ),
    RetrievalCase(
        name="decision_kestrel_location_sampling",
        query="Kestrel background location sampling battery",
        expected_id="rq_x_kestrel_dec_location",
        expected_layer="decision",
        expected_phrase="one fix per minute",
        category="decision_recall",
    ),
    RetrievalCase(
        name="decision_larkspur_font",
        query="Larkspur variable font family legacy typefaces",
        expected_id="rq_x_larkspur_dec_font",
        expected_layer="decision",
        expected_phrase="variable font family",
        category="decision_recall",
    ),
    RetrievalCase(
        name="decision_larkspur_darkmode",
        query="Larkspur dark mode tokens hand-tuning",
        expected_id="rq_x_larkspur_dec_darkmode",
        expected_layer="decision",
        expected_phrase="derives dark mode",
        category="decision_recall",
    ),
    # procedural_recall ------------------------------------------------
    RetrievalCase(
        name="procedural_granite_cutover",
        query="Granite cutover draining writers flipping reads tail journal",
        expected_id="rq_x_granite_proc_cutover",
        expected_layer="procedural",
        expected_phrase="tail journal",
        category="procedural_recall",
    ),
    RetrievalCase(
        name="procedural_granite_tenant_onboarding",
        query="onboard Granite tenant provisioning shard seeding fixtures",
        expected_id="rq_x_granite_proc_tenant",
        expected_layer="procedural",
        expected_phrase="seeding fixtures",
        category="procedural_recall",
    ),
    RetrievalCase(
        name="procedural_foundry_debrief",
        query="close Foundry panel scorecards debrief decision logged",
        expected_id="rq_x_foundry_proc_debrief",
        expected_layer="procedural",
        expected_phrase="decision logged",
        category="procedural_recall",
    ),
    RetrievalCase(
        name="procedural_foundry_references",
        query="Foundry reference calls former peers direct report",
        expected_id="rq_x_foundry_proc_reference",
        expected_layer="procedural",
        expected_phrase="one direct report",
        category="procedural_recall",
    ),
    RetrievalCase(
        name="procedural_health_long_run",
        query="weekend long run fueling charged watch recovery meal",
        expected_id="rq_x_health_proc_longrun",
        expected_layer="procedural",
        expected_phrase="every forty minutes",
        category="procedural_recall",
    ),
    RetrievalCase(
        name="procedural_health_knee_flare",
        query="knee flares drop mileage ice physio",
        expected_id="rq_x_health_proc_flare",
        expected_layer="procedural",
        expected_phrase="ice twice daily",
        category="procedural_recall",
    ),
    RetrievalCase(
        name="procedural_kestrel_release",
        query="cut Kestrel release freezing branch canary hardware",
        expected_id="rq_x_kestrel_proc_release",
        expected_layer="procedural",
        expected_phrase="canary to ten percent",
        category="procedural_recall",
    ),
    RetrievalCase(
        name="procedural_kestrel_field_kit",
        query="pack Kestrel field kit test devices battery brick",
        expected_id="rq_x_kestrel_proc_fieldkit",
        expected_layer="procedural",
        expected_phrase="laminated offline checklist",
        category="procedural_recall",
    ),
    RetrievalCase(
        name="procedural_larkspur_component",
        query="promote Larkspur component contrast usage docs minor version",
        expected_id="rq_x_larkspur_proc_component",
        expected_layer="procedural",
        expected_phrase="tagging a minor version",
        category="procedural_recall",
    ),
    # source_scoped ----------------------------------------------------
    RetrievalCase(
        name="source_scoped_gmail_granite_shards",
        query="Granite Postgres shards checksum batch",
        expected_id="rq_x_granite_shards",
        expected_layer="semantic",
        expected_phrase="legacy Postgres warehouse",
        category="source_scoped",
        source_url_contains=("cortex-source://gmail#service=gmail", "subject=", "line=", "excerpt="),
    ),
    RetrievalCase(
        name="source_scoped_gmail_granite_dualwrite",
        query="Granite dual-writes thirty consecutive days",
        expected_id="rq_x_granite_dec_dualwrite",
        expected_layer="decision",
        expected_phrase="thirty consecutive days",
        category="source_scoped",
        source_url_contains=("cortex-source://gmail#service=gmail", "subject=", "line=", "excerpt="),
    ),
    RetrievalCase(
        name="source_scoped_slack_granite_backfills",
        query="destructive backfills rehearsal snapshot",
        expected_id="rq_x_granite_neg_backfill",
        expected_layer="negative",
        expected_phrase="rehearsal snapshot",
        category="source_scoped",
        source_url_contains=("cortex-source://slack#service=slack", "channel=", "message=", "line=", "excerpt="),
    ),
    RetrievalCase(
        name="source_scoped_slack_foundry_feedback",
        query="interview feedback observed behaviors timestamps",
        expected_id="rq_x_foundry_style_feedback",
        expected_layer="style",
        expected_phrase="observed behaviors",
        category="source_scoped",
        source_url_contains=("cortex-source://slack#service=slack", "channel=", "message=", "line=", "excerpt="),
    ),
    RetrievalCase(
        name="source_scoped_github_kestrel_tiles",
        query="Kestrel LRU disk cache 512",
        expected_id="rq_x_kestrel_tiles",
        expected_layer="semantic",
        expected_phrase="offline map tiles",
        category="source_scoped",
        source_url_contains=("cortex-source://github#service=github", "file=", "row=", "line=", "excerpt="),
    ),
    RetrievalCase(
        name="source_scoped_github_kestrel_release",
        query="Kestrel release branch offline suite canary",
        expected_id="rq_x_kestrel_proc_release",
        expected_layer="procedural",
        expected_phrase="freezing the branch",
        category="source_scoped",
        source_url_contains=("cortex-source://github#service=github", "file=", "row=", "line=", "excerpt="),
    ),
    RetrievalCase(
        name="source_scoped_notion_foundry_pipeline",
        query="Foundry sourcing offer approval pipeline board",
        expected_id="rq_x_foundry_pipeline",
        expected_layer="semantic",
        expected_phrase="offer approval",
        category="source_scoped",
        source_url_contains=("cortex-source://notion#service=notion", "page=", "line=", "excerpt="),
    ),
    RetrievalCase(
        name="source_scoped_notion_larkspur_spec",
        query="Larkspur spec annotated redline rationale",
        expected_id="rq_x_larkspur_style_spec",
        expected_layer="style",
        expected_phrase="redline measurements",
        category="source_scoped",
        source_url_contains=("cortex-source://notion#service=notion", "page=", "line=", "excerpt="),
    ),
    RetrievalCase(
        name="source_scoped_calendar_granite_dryrun",
        query="Granite warehouse cutover staging enclave",
        expected_id="rq_x_granite_event_dryrun",
        expected_layer="episodic",
        expected_phrase="warehouse cutover",
        category="source_scoped",
        source_url_contains=("cortex-source://calendar#service=calendar", "event=", "line=", "excerpt="),
    ),
    RetrievalCase(
        name="source_scoped_obsidian_health_baseline",
        query="resting heart rate 52 bpm chest strap",
        expected_id="rq_x_health_resting_hr",
        expected_layer="semantic",
        expected_phrase="chest strap",
        category="source_scoped",
        source_url_contains=("cortex-source://obsidian#service=obsidian", "note=", "line=", "excerpt="),
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
    return [*result["memories"], *_seed_expanded_corpus_memories(store, user_id)]


def _seed_expanded_corpus_memories(store: CortexStore, user_id: str = USER_ID) -> list[dict[str, Any]]:
    """Seed the expanded multi-source, multi-project corpus captures."""
    memories: list[dict[str, Any]] = []
    for capture in EXPANDED_SEED_CAPTURES:
        records = [
            {
                "confidence": "confirmed",
                "entity_ids": [],
                **record,
            }
            for record in capture["records"]
        ]
        result = store.save_capture(
            user_id=user_id,
            content="\n".join(str(record["content"]) for record in records),
            source=str(capture["source"]),
            source_url=str(capture["source_url"]),
            title=str(capture["title"]),
            extracted={
                "_timestamp": str(capture["timestamp"]),
                "summary": str(capture["title"]),
                "records": records,
                "tasks": [],
                "entities": [dict(entity) for entity in capture.get("entities", ())],
            },
        )
        memories.extend(result["memories"])
    return memories


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
    # Scope the noisy-import assertions to import-produced memories: the
    # expanded seed corpus legitimately reuses slack/github/notion/calendar
    # sources but uses fixture record ids (rq_*), which imports never emit.
    memories = [
        memory
        for memory in store.recent(user_id, limit=400)
        if memory["source"] in NOISY_IMPORT_SOURCES and not str(memory["id"]).startswith("rq_")
    ]
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
    layers = sorted({str(item.get("expected_layer") or "") for item in checks if item.get("expected_layer")})
    return {
        "overall": summarize(checks),
        "by_category": {
            category: summarize([item for item in checks if item["category"] == category])
            for category in categories
        },
        "by_layer": {
            layer: summarize([item for item in checks if item.get("expected_layer") == layer])
            for layer in layers
        },
    }


def _default_relevance_basis() -> str:
    """Basis label for a relevance the search layer did not tag itself.

    Mirrors CortexStore._rerank_rows' gate: cosine only when reranking is forced on AND a real
    (non-hash) embedder is active; otherwise the fused reciprocal-rank ordering. Under the
    deterministic hash embedder (retrieval_eval's default) this is always "rrf".
    """
    mode = os.environ.get("CORTEX_RERANK", "off").strip().lower()
    if mode not in {"", "off", "0", "false", "none"} and embedding_status().get("provider") != "hash":
        return "cosine"
    return "rrf"


def _emitted_relevance(row: dict[str, Any], rank_index: int) -> tuple[float, str]:
    """Emitted relevance score + its basis for one returned row.

    Prefers a `relevance`/`relevance_basis` surfaced by the search layer (the additive reranker
    signal); when absent — the default hash path, which stays byte-identical — derives a strictly
    rank-monotonic relevance from the emitted order so the monotonicity gate reproduces the
    emitted ranking exactly and never fires spuriously.
    """
    raw = row.get("relevance")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        basis = str(row.get("relevance_basis") or "").strip() or _default_relevance_basis()
        return float(raw), basis
    # No emitted relevance: derive strictly from the emitted rank (basis is literally the rank).
    return 1.0 / (rank_index + 1), "rank"


def _relevance_grades(case: RetrievalCase) -> dict[str, int]:
    """Graded relevance for a case: the expected id is grade 2, its expected related companion
    (when the case exercises related-memory retrieval) grade 1, everything else grade 0."""
    grades = {case.expected_id: 2}
    if case.expected_related_id:
        grades.setdefault(case.expected_related_id, 1)
    return grades


def _relevance_rows_for_results(case: RetrievalCase, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tag each returned row with its graded relevance and emitted `relevance`. Captured at the
    exact limit/corpus state the top1/recall floors are measured on, so re-sorting these rows BY
    the emitted relevance is an apples-to-apples reproduction of those floors (a fresh re-query
    would drift as later fixtures are seeded into the shared corpus)."""
    grades = _relevance_grades(case)
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(results):
        value, basis = _emitted_relevance(item, index)
        rows.append(
            {
                "id": item["id"],
                "grade": grades.get(item["id"], 0),
                "relevance": round(value, 6),
                "basis": basis,
                "rank": index + 1,
            }
        )
    return rows


def _row_relevance_map(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{id -> {relevance, basis, rank}} for a result list, using the emitted relevance (or its
    rank-derived fallback). First occurrence wins so the strongest rank is kept for an id."""
    out: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(results):
        memory_id = item["id"]
        if memory_id in out:
            continue
        value, basis = _emitted_relevance(item, index)
        out[memory_id] = {"relevance": value, "basis": basis, "rank": index + 1}
    return out


# The pairwise probe re-queries at a wide limit so the fixtures' designed same-topic distractors
# (a case's disallowed_ids — the labeled non-relevant rows) actually surface to be compared
# against the expected id, which the precise limit=3 search usually returns alone.
RELEVANCE_PROBE_LIMIT = 25


def evaluate_relevance_monotonicity(
    store: CortexStore,
    user_id: str,
    cases: tuple[RetrievalCase, ...],
    checks: list[dict[str, Any]],
    probe_limit: int = RELEVANCE_PROBE_LIMIT,
) -> dict[str, Any]:
    """Relevance-monotonicity diagnostics over the graded fixtures.

    Two independent measurements on the emitted `relevance` (preferring a real search-layer
    signal, else the rank-derived fallback):

    * Pairwise concordance — for every case that labels non-relevant distractors (disallowed_ids),
      the graded-relevant expected id must outscore each labeled distractor. A distractor that does
      not surface even at the wide probe limit counts as concordant (excluded ⇒ ranked below the
      retrieved set). Reported as a rate so an isolated tie/inversion under a future real relevance
      signal does not flake, while a scrambled signal (~0.5) trips.
    * Reordered reproduction — re-sorting each case's floor-time rows (captured by _evaluate_case at
      the exact limit/corpus state the top1/recall floors use) BY the emitted relevance must
      reproduce those floors, i.e. relevance is a faithful ranking key that never regresses the
      ordering.
    """
    concordant_pairs = 0
    total_pairs = 0
    top1_hits = 0
    recall_at: dict[int, float] = {k: 0.0 for k in METRIC_K_VALUES}
    reordered_cases = 0
    bases: set[str] = set()

    for check in checks:
        rows = check.get("relevance_rows") if isinstance(check, dict) else None
        if not rows:
            continue
        for row in rows:
            bases.add(str(row.get("basis") or ""))
        ordered_ids = [row["id"] for row in sorted(rows, key=lambda row: (-row["relevance"], row["rank"]))]
        expected_id = check.get("expected_id")
        reordered_cases += 1
        if ordered_ids[:1] == [expected_id]:
            top1_hits += 1
        case_metrics = _metrics_for_results(expected_id, ordered_ids, METRIC_K_VALUES)
        for k in METRIC_K_VALUES:
            recall_at[k] += case_metrics[f"recall@{k}"]

    for case in cases:
        if not case.disallowed_ids:
            continue
        wide = store.search(
            user_id, case.query, limit=probe_limit, sector=case.sector, include_related=case.include_related
        )
        wide_map = _row_relevance_map(wide)
        for meta in wide_map.values():
            bases.add(str(meta["basis"] or ""))
        expected = wide_map.get(case.expected_id)
        if expected is None:
            # Expected id not retrieved at all — a correctness failure the top1/recall floors
            # already own; do not double-count it as a monotonicity miss here.
            continue
        for distractor_id in case.disallowed_ids:
            total_pairs += 1
            distractor = wide_map.get(distractor_id)
            if distractor is None or expected["relevance"] > distractor["relevance"]:
                concordant_pairs += 1

    summary: dict[str, Any] = {
        "case_count": reordered_cases,
        "pair_count": total_pairs,
        "pairwise_concordance": round(concordant_pairs / total_pairs, 4) if total_pairs else 1.0,
        "reordered_top1_accuracy": round(top1_hits / reordered_cases, 4) if reordered_cases else 0.0,
        "bases": sorted(basis for basis in bases if basis),
    }
    for k in METRIC_K_VALUES:
        summary[f"reordered_recall@{k}"] = round(recall_at[k] / reordered_cases, 4) if reordered_cases else 0.0
    return summary


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
        "relevance_rows": _relevance_rows_for_results(case, results),
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

    relevance_monotonicity = evaluate_relevance_monotonicity(
        store, user_id, (*RETRIEVAL_CASES, *noisy_cases, *direct_cases), checks
    )

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
        "relevance_monotonicity": relevance_monotonicity,
        "checks": checks,
    }


def run_retrieval_eval(db_path: Path, vault_path: Path | None = None, user_id: str = USER_ID) -> dict[str, Any]:
    init_db(db_path)
    store = CortexStore(db_path, vault_path)
    return evaluate_retrieval(store, user_id)


def check_relevance_monotonicity(result: dict[str, Any]) -> list[str]:
    """Return relevance-monotonicity gate failures (empty list == pass).

    Additive to check_retrieval_metric_thresholds: it never relaxes an existing floor. It asserts
    the emitted relevance ranks graded-relevant rows above non-relevant ones (pairwise floor),
    that ordering by relevance reproduces the top1/recall floors, and that the emitted basis stays
    rank/rrf under the deterministic hash path (cosine only surfaces in rerank_eval's real path).
    """
    failures: list[str] = []
    summary = result.get("relevance_monotonicity")
    if not isinstance(summary, dict):
        failures.append("relevance_monotonicity summary missing from eval result")
        return failures
    thresholds = RELEVANCE_MONOTONICITY_THRESHOLDS

    case_count = int(summary.get("case_count") or 0)
    if case_count < thresholds["min_case_count"]:
        failures.append(f"relevance monotonicity corpus shrank: case_count={case_count} < {thresholds['min_case_count']}")

    pair_count = int(summary.get("pair_count") or 0)
    if pair_count < thresholds["min_pair_count"]:
        failures.append(
            f"relevance monotonicity has too few graded pairs to be meaningful: pair_count={pair_count} < {thresholds['min_pair_count']}"
        )

    concordance = float(summary.get("pairwise_concordance") or 0.0)
    if concordance < thresholds["pairwise_concordance"]:
        failures.append(f"pairwise_concordance={concordance} < {thresholds['pairwise_concordance']}")

    for key in ("reordered_top1_accuracy", "reordered_recall@1", "reordered_recall@3"):
        value = float(summary.get(key) or 0.0)
        if value < thresholds[key]:
            failures.append(f"{key}={value} < {thresholds[key]}")

    allowed = set(thresholds["allowed_bases"])
    unexpected = sorted(basis for basis in (summary.get("bases") or []) if basis not in allowed)
    if unexpected:
        failures.append(f"unexpected relevance basis under hash path: {unexpected} (allowed {sorted(allowed)})")

    return failures


def check_retrieval_metric_thresholds(result: dict[str, Any]) -> list[str]:
    """Return a list of regression-gate failures (empty list == pass).

    Enforces the minimum corpus size, overall metric floors, presence of every
    required layer/behavior category, and per-category metric floors from
    RETRIEVAL_METRIC_THRESHOLDS.
    """
    failures: list[str] = []
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    overall = metrics.get("overall") if isinstance(metrics.get("overall"), dict) else {}
    by_category = metrics.get("by_category") if isinstance(metrics.get("by_category"), dict) else {}
    by_layer = metrics.get("by_layer") if isinstance(metrics.get("by_layer"), dict) else {}

    case_count = int(overall.get("case_count") or 0)
    if case_count < RETRIEVAL_METRIC_THRESHOLDS["min_case_count"]:
        failures.append(f"corpus shrank: case_count={case_count} < {RETRIEVAL_METRIC_THRESHOLDS['min_case_count']}")

    for key, floor in RETRIEVAL_METRIC_THRESHOLDS["overall"].items():
        value = float(overall.get(key) or 0.0)
        if value < floor:
            failures.append(f"overall {key}={value} < {floor}")

    for category in RETRIEVAL_METRIC_THRESHOLDS["required_categories"]:
        if category not in by_category:
            failures.append(f"missing required category '{category}'")

    for category, cat_metrics in sorted(by_category.items()):
        if not isinstance(cat_metrics, dict):
            continue
        for key, floor in RETRIEVAL_METRIC_THRESHOLDS["per_category"].items():
            value = float(cat_metrics.get(key) or 0.0)
            if value < floor:
                failures.append(f"category '{category}' {key}={value} < {floor}")

    # Per-layer rollup: only enforce required-layer presence when a by_layer
    # rollup is actually reported (synthetic gate fixtures omit it), then apply
    # the per-layer metric floors to whatever layers are present.
    if by_layer:
        for layer in RETRIEVAL_METRIC_THRESHOLDS["required_layers"]:
            if layer not in by_layer:
                failures.append(f"missing required layer '{layer}'")
    for layer, layer_metrics in sorted(by_layer.items()):
        if not isinstance(layer_metrics, dict):
            continue
        for key, floor in RETRIEVAL_METRIC_THRESHOLDS["per_layer"].items():
            value = float(layer_metrics.get(key) or 0.0)
            if value < floor:
                failures.append(f"layer '{layer}' {key}={value} < {floor}")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Cortex retrieval quality harness and regression gate.")
    parser.add_argument("--db-path", type=Path, help="Optional SQLite path. Defaults to a temporary database.")
    parser.add_argument("--vault-path", type=Path, help="Optional vault path. Defaults beside the SQLite database.")
    parser.add_argument("--user-id", default=USER_ID)
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Print metrics without failing on threshold regressions (local inspection).",
    )
    args = parser.parse_args()

    if args.db_path:
        result = run_retrieval_eval(args.db_path.expanduser(), args.vault_path.expanduser() if args.vault_path else None, args.user_id)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_retrieval_eval(root / "retrieval-eval.sqlite", root / "Cortex.vault", args.user_id)

    print(json.dumps(result, indent=2, sort_keys=True))

    failures = check_retrieval_metric_thresholds(result)
    failures.extend(check_relevance_monotonicity(result))
    if failures and not args.report_only:
        print("\nRETRIEVAL QUALITY GATE FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
