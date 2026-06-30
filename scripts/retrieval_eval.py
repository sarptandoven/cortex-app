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
from backend.app.storage import CortexStore, MEMORY_LAYERS


USER_ID = "retrieval-quality"
SEED_TIMESTAMP = "2026-01-01T00:00:00Z"
METRIC_K_VALUES = (1, 3)
NOISY_IMPORT_SOURCES = {"chatgpt", "claude", "slack", "email", "docs", "notion", "cloud-docs", "calendar", "github"}
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
LOCAL_FILE_SAFE_SOURCE_URL = "local-file://Local%20Citation%20Plan.md#line=9&excerpt=local-file-citation"
LOCAL_FILE_RAW_FRAGMENTS = ("/Users/sarptandoven", "Documents/Cortex Beta")
LOCAL_FILE_CITATION_CONTENT = (
    "Local file citation fixture prefers sanitized source locators in shared answer and context outputs."
)
EXTERNAL_SPEAKER_PERSONAL_SIGNAL_GUARDS: tuple[dict[str, str], ...] = (
    {"source": "email", "layer": "preference", "phrase": "long onboarding checklists"},
    {"source": "email", "layer": "style", "phrase": "verbose and salesy"},
    {"source": "slack", "layer": "preference", "phrase": "long-form consensus memos"},
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


def seed_local_file_citation_memory(store: CortexStore, user_id: str = USER_ID) -> dict[str, Any]:
    store.update_settings(user_id, {"review_new_captures": False, "allow_pending_in_context": True})
    saved = store.save_capture(
        user_id=user_id,
        content=LOCAL_FILE_CITATION_CONTENT,
        source="docs",
        source_url=LOCAL_FILE_SOURCE_URL,
        title="Local citation sanitization fixture",
        extracted={
            "_timestamp": "2026-06-29T10:12:00Z",
            "summary": LOCAL_FILE_CITATION_CONTENT,
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
    return saved["memories"][0]


def assert_shared_local_file_citations_sanitized(store: CortexStore, user_id: str = USER_ID) -> dict[str, Any]:
    memory = seed_local_file_citation_memory(store, user_id)
    query = "sanitized source locators shared answer context outputs"
    answer = store.answer_query(user_id, query, limit=3)
    context = store.context_pack(user_id, query=query, limit=3)
    serialized_answer = json.dumps(answer, sort_keys=True)
    combined = f"{serialized_answer}\n{context}"

    if LOCAL_FILE_SOURCE_URL in combined:
        raise AssertionError("Shared answer/context output leaked the raw local source_url")
    for fragment in LOCAL_FILE_RAW_FRAGMENTS:
        if fragment in combined:
            raise AssertionError(f"Shared answer/context output leaked local path fragment: {fragment}")
    if LOCAL_FILE_SAFE_SOURCE_URL not in serialized_answer:
        raise AssertionError(f"Shared answer output missed sanitized local citation {LOCAL_FILE_SAFE_SOURCE_URL!r}")
    if LOCAL_FILE_SAFE_SOURCE_URL not in context:
        raise AssertionError(f"Context pack missed sanitized local citation {LOCAL_FILE_SAFE_SOURCE_URL!r}")

    citation = next((item for item in answer.get("citations") or [] if item.get("id") == memory["id"]), None)
    if not citation:
        raise AssertionError("Shared answer citations missed the local-file citation fixture")
    if citation.get("source_url") != LOCAL_FILE_SAFE_SOURCE_URL:
        raise AssertionError(f"Shared answer citation was not sanitized: {citation}")

    return {
        "memory_id": memory["id"],
        "raw_source_url": LOCAL_FILE_SOURCE_URL,
        "safe_source_url": LOCAL_FILE_SAFE_SOURCE_URL,
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
    results = store.search(user_id, case.query, limit=limit)
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
    source_url = str(top.get("source_url") or "")
    for expected_fragment in case.source_url_contains:
        if expected_fragment not in source_url:
            raise AssertionError(f"{case.name}: expected source_url fragment {expected_fragment!r} in {source_url!r}")
    if case.expected_occurred_at and top.get("occurred_at") != case.expected_occurred_at:
        raise AssertionError(f"{case.name}: expected occurred_at {case.expected_occurred_at}, got {top.get('occurred_at')}")

    layer_results = store.search(user_id, case.query, limit=limit, layer=case.expected_layer)
    layer_ids = [item["id"] for item in layer_results]
    if case.expected_id not in layer_ids:
        raise AssertionError(f"{case.name}: layer-filtered search missed {case.expected_id}: {layer_ids}")

    result_ids = [item["id"] for item in results]
    expected_rank = result_ids.index(case.expected_id) + 1 if case.expected_id in result_ids else None
    return {
        "name": case.name,
        "category": case.category,
        "query": case.query,
        "expected_layer": case.expected_layer,
        "expected_id": case.expected_id,
        "expected_rank": expected_rank,
        "top_result": top["id"],
        "top_layer": top["layer"],
        "top_source_url": top.get("source_url"),
        "top_occurred_at": top.get("occurred_at"),
        "result_ids": result_ids,
        "layer_filtered_results": layer_ids,
        "metrics": _metrics_for_results(case.expected_id, result_ids, METRIC_K_VALUES),
    }


def evaluate_retrieval(store: CortexStore, user_id: str = USER_ID, limit: int = 3) -> dict[str, Any]:
    seeded = seed_representative_memories(store, user_id)
    distractors = seed_distractor_memories(store, user_id)
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

    local_file_citation = assert_shared_local_file_citations_sanitized(store, user_id)
    state_leakage = assert_state_leakage_excluded(store, user_id)

    return {
        "status": "ok",
        "seeded_memories": len(seeded),
        "distractor_memories": len(distractors),
        "noisy_import_memories": len(noisy_memories),
        "local_file_citation": local_file_citation,
        "state_leakage_seeded": state_leakage,
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
