from __future__ import annotations

import argparse
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
from backend.app.storage import CortexStore


USER_ID = "adaptation-quality"
SEED_TIMESTAMP = "2026-06-29T10:00:00+00:00"
NOISY_ADAPTATION_SOURCES = {"email", "slack"}
NOISY_ADAPTATION_USER_PHRASES = (
    "source-backed adaptation preferences from my email replies",
    "compact adaptation Slack summaries",
)
NOISY_ADAPTATION_EXTERNAL_SIGNAL_GUARDS: tuple[dict[str, str], ...] = (
    {"source": "email", "layer": "preference", "phrase": "external sender onboarding theater"},
    {"source": "email", "layer": "style", "phrase": "external sender sales prose"},
    {"source": "slack", "layer": "preference", "phrase": "external Slack consensus rituals"},
)
LOCAL_FILE_SOURCE_URL = "/Users/sarptandoven/Documents/Cortex Beta/Adaptation Local Notes.md#line=14&excerpt=adaptation-local-citation"
LOCAL_FILE_SAFE_SOURCE_URL_PREFIX = "local-file://Adaptation%20Local%20Notes.md#line=14&excerpt=adaptation-local-citation"
LOCAL_FILE_RAW_FRAGMENTS = ("/Users/sarptandoven", "Documents/Cortex Beta")
LOCAL_FILE_CITATION_CONTENT = (
    "Local adaptation citation fixture keeps local file source locators sanitized in profile focus and adaptation evidence."
)


@dataclass(frozen=True)
class AdaptationSeed:
    id: str
    kind: str
    layer: str
    content: str
    phrase: str
    source_url: str
    topics: tuple[str, ...]


ADAPTATION_SEEDS: tuple[AdaptationSeed, ...] = (
    AdaptationSeed(
        id="aq_preference_clear_path",
        kind="preference",
        layer="preference",
        content="Preference: when prioritizing product work, choose the clearest user path before adding more controls.",
        phrase="clearest user path",
        source_url="cortex-eval://adaptation/preference#clear-path",
        topics=("preference", "product", "prioritization"),
    ),
    AdaptationSeed(
        id="aq_negative_copy_brief",
        kind="negative",
        layer="negative",
        content="Do not use copy-only memory brief workflows as the primary Cortex product experience.",
        phrase="copy-only memory brief",
        source_url="cortex-eval://adaptation/negative#copy-brief",
        topics=("negative", "product", "workflow"),
    ),
    AdaptationSeed(
        id="aq_style_direct_tradeoffs",
        kind="style",
        layer="style",
        content="Writing style: use concise technical answers with direct tradeoffs and clear next steps.",
        phrase="direct tradeoffs",
        source_url="cortex-eval://adaptation/style#direct-tradeoffs",
        topics=("style", "writing", "answers"),
    ),
    AdaptationSeed(
        id="aq_decision_simple_surfaces",
        kind="decision",
        layer="decision",
        content="Decision: Cortex should use Home, Review, Ask, and a secondary Connections & Privacy sheet.",
        phrase="Home, Review, Ask",
        source_url="cortex-eval://adaptation/decision#simple-surfaces",
        topics=("decision", "ui", "product-loop"),
    ),
    AdaptationSeed(
        id="aq_episodic_onboarding",
        kind="event",
        layer="episodic",
        content="On 2026-06-29, the Taipei redesign removed first-run quick memory from onboarding.",
        phrase="removed first-run quick memory",
        source_url="cortex-eval://adaptation/episodic#taipei-onboarding",
        topics=("episodic", "onboarding", "taipei"),
    ),
    AdaptationSeed(
        id="aq_semantic_local_first",
        kind="claim",
        layer="semantic",
        content="Cortex is a local-first personal memory system for adapting agents with cited user data.",
        phrase="local-first personal memory system",
        source_url="cortex-eval://adaptation/semantic#local-first",
        topics=("semantic", "local-first", "memory"),
    ),
    AdaptationSeed(
        id="aq_procedural_release_check",
        kind="procedure",
        layer="procedural",
        content="Procedure: before shipping Cortex, run backend tests, build the macOS app, verify codesign, and check the local health endpoint.",
        phrase="run backend tests",
        source_url="cortex-eval://adaptation/procedural#release-check",
        topics=("procedural", "release", "verification"),
    ),
)

PENDING_PHRASE = "Pending-only adaptation text should not appear"


def _seed_record(seed: AdaptationSeed) -> dict[str, Any]:
    return {
        "id": seed.id,
        "kind": seed.kind,
        "layer": seed.layer,
        "content": seed.content,
        "summary": seed.content,
        "confidence": "confirmed",
        "importance": 5,
        "topics": list(seed.topics),
        "entity_ids": [],
    }


def seed_adaptation_memories(store: CortexStore, user_id: str = USER_ID) -> list[dict[str, Any]]:
    store.update_settings(
        user_id,
        {
            "review_new_captures": False,
            "allow_pending_in_context": True,
            "redact_sensitive_context": True,
        },
    )
    approved_memories: list[dict[str, Any]] = []
    for index, seed in enumerate(ADAPTATION_SEEDS):
        saved = store.save_capture(
            user_id=user_id,
            content=seed.content,
            source="adaptation-eval",
            source_url=seed.source_url,
            title=f"Adaptation quality {seed.layer} seed",
            extracted={
                "_timestamp": f"2026-06-29T10:00:{index:02d}+00:00",
                "summary": seed.content,
                "records": [_seed_record(seed)],
                "tasks": [],
                "entities": [],
            },
        )
        approved_memories.extend(saved["memories"])
    store.save_capture(
        user_id=user_id,
        content="Open question: which connector should be validated next for adaptation quality?",
        source="adaptation-eval",
        source_url="cortex-eval://adaptation/open-loop#connector",
        title="Adaptation quality open loop",
        extracted={
            "_timestamp": SEED_TIMESTAMP,
            "summary": "Adaptation quality open loop.",
            "records": [],
            "tasks": [
                {
                    "id": "aq_task_followup",
                    "kind": "question",
                    "content": "Open question: which connector should be validated next for adaptation quality?",
                    "status": "open",
                    "importance": 3,
                    "topics": ["connectors", "adaptation"],
                    "entity_ids": [],
                }
            ],
            "entities": [],
        },
    )

    store.update_settings(user_id, {"review_new_captures": True, "allow_pending_in_context": True})
    store.save_capture(
        user_id=user_id,
        content=PENDING_PHRASE,
        source="adaptation-eval-pending",
        source_url="cortex-eval://adaptation/pending-seed",
        title="Pending adaptation quality seed",
        extracted={
            "_timestamp": "2026-06-29T10:05:00+00:00",
            "summary": PENDING_PHRASE,
            "records": [
                {
                    "id": "aq_pending_should_not_leak",
                    "kind": "preference",
                    "layer": "preference",
                    "content": PENDING_PHRASE,
                    "summary": PENDING_PHRASE,
                    "confidence": "unverified",
                    "importance": 5,
                    "topics": ["pending", "adaptation"],
                    "entity_ids": [],
                }
            ],
            "tasks": [],
            "entities": [],
        },
    )
    return approved_memories


def seed_noisy_adaptation_imports(store: CortexStore, user_id: str = USER_ID) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="cortex-adaptation-import-") as tmp:
        root = Path(tmp)
        mail = root / "mail"
        slack = root / "slack"
        channel = slack / "general"
        mail.mkdir(parents=True)
        channel.mkdir(parents=True)

        (mail / "user_reply.eml").write_text(
            "Subject: Adaptation user reply\n"
            "From: Adapt User <adapt@example.com>\n"
            "To: Dana Partner <dana@example.com>\n"
            "Date: Mon, 29 Jun 2026 12:00:00 +0000\n"
            "\n"
            "I prefer source-backed adaptation preferences from my email replies.\n",
            encoding="utf-8",
        )
        (mail / "external_advice.eml").write_text(
            "Subject: External adaptation advice\n"
            "From: Dana Partner <dana@example.com>\n"
            "To: Adapt User <adapt@example.com>\n"
            "Date: Mon, 29 Jun 2026 12:05:00 +0000\n"
            "\n"
            "I prefer external sender onboarding theater for every adaptation summary.\n"
            "My writing style is external sender sales prose.\n",
            encoding="utf-8",
        )
        (slack / "users.json").write_text(
            json.dumps(
                [
                    {"id": "U1", "name": "adapt", "real_name": "Adapt User", "profile": {"email": "adapt@example.com"}},
                    {"id": "U2", "name": "dana", "real_name": "Dana Partner", "profile": {"email": "dana@example.com"}},
                ]
            ),
            encoding="utf-8",
        )
        (channel / "2026-06-29.json").write_text(
            json.dumps(
                [
                    {
                        "type": "message",
                        "user": "U1",
                        "text": "I prefer compact adaptation Slack summaries with source labels.",
                        "ts": "1782740000.0001",
                    },
                    {
                        "type": "message",
                        "user": "U2",
                        "text": "I prefer external Slack consensus rituals for every adaptation answer.",
                        "ts": "1782740001.0001",
                    },
                ]
            ),
            encoding="utf-8",
        )

        store.update_settings(
            user_id,
            {
                "review_new_captures": False,
                "allow_pending_in_context": True,
                "identity_aliases": ["adapt@example.com", "adapt", "Adapt User"],
            },
        )
        result = store.import_sources(
            user_id=user_id,
            paths=[str(mail), str(slack)],
            processing="sync",
            max_records=20,
        )
        if result["failed"]:
            raise AssertionError(f"Noisy adaptation import failed: {result['errors']}")
    return [memory for memory in store.recent(user_id, limit=120) if memory["source"] in NOISY_ADAPTATION_SOURCES]


def seed_local_file_citation_memory(store: CortexStore, user_id: str = USER_ID) -> dict[str, Any]:
    store.update_settings(user_id, {"review_new_captures": False, "allow_pending_in_context": True})
    saved = store.save_capture(
        user_id=user_id,
        content=LOCAL_FILE_CITATION_CONTENT,
        source="docs",
        source_url=LOCAL_FILE_SOURCE_URL,
        title="Adaptation local citation sanitization fixture",
        extracted={
            "_timestamp": "2026-06-29T10:07:00+00:00",
            "summary": LOCAL_FILE_CITATION_CONTENT,
            "records": [
                {
                    "id": "aq_local_file_citation_sanitized",
                    "kind": "claim",
                    "layer": "semantic",
                    "content": LOCAL_FILE_CITATION_CONTENT,
                    "summary": LOCAL_FILE_CITATION_CONTENT,
                    "confidence": "confirmed",
                    "importance": 1,
                    "topics": ["citations", "local-files", "adaptation"],
                    "entity_ids": [],
                }
            ],
            "tasks": [],
            "entities": [],
        },
    )
    return saved["memories"][0]


def assert_adaptation_local_file_citations_sanitized(store: CortexStore, user_id: str = USER_ID) -> dict[str, Any]:
    memory = seed_local_file_citation_memory(store, user_id)
    query = "local adaptation citation fixture sanitized profile focus adaptation evidence"
    profile = store.personal_profile(user_id, query=query, limit=8, include_pending=False)
    artifact = store.agent_adaptation(user_id, query=query, target="Claude", limit=8, include_pending=False)
    serialized = json.dumps({"profile": profile, "artifact": artifact}, sort_keys=True)

    if LOCAL_FILE_SOURCE_URL in serialized:
        raise AssertionError("Adaptation profile/evidence leaked the raw local source_url")
    for fragment in LOCAL_FILE_RAW_FRAGMENTS:
        if fragment in serialized:
            raise AssertionError(f"Adaptation profile/evidence leaked local path fragment: {fragment}")
    if LOCAL_FILE_SAFE_SOURCE_URL_PREFIX not in serialized:
        raise AssertionError(f"Adaptation profile/evidence missed sanitized local citation {LOCAL_FILE_SAFE_SOURCE_URL_PREFIX!r}")

    focus = next((item for item in profile.get("focus") or [] if item.get("id") == memory["id"]), None)
    if not focus:
        raise AssertionError("Personal profile focus missed the local-file citation fixture")
    focus_source_url = str(focus.get("source_url") or "")
    if not focus_source_url.startswith(LOCAL_FILE_SAFE_SOURCE_URL_PREFIX) or "path_hash=" not in focus_source_url:
        raise AssertionError(f"Personal profile focus citation was not sanitized: {focus}")

    evidence = next((item for item in artifact.get("evidence") or [] if item.get("id") == memory["id"]), None)
    if not evidence:
        raise AssertionError("Adaptation evidence missed the local-file citation fixture")
    evidence_source_url = str(evidence.get("source_url") or "")
    if not evidence_source_url.startswith(LOCAL_FILE_SAFE_SOURCE_URL_PREFIX) or "path_hash=" not in evidence_source_url:
        raise AssertionError(f"Adaptation evidence citation was not sanitized: {evidence}")

    return {
        "memory_id": memory["id"],
        "raw_source_url": LOCAL_FILE_SOURCE_URL,
        "safe_source_url": focus_source_url,
    }


def _check(checks: list[dict[str, Any]], name: str, ok: bool, detail: str, payload: dict[str, Any] | None = None) -> None:
    checks.append({"name": name, "status": "ok" if ok else "failed", "detail": detail, "payload": payload or {}})


def evaluate_adaptation(store: CortexStore, user_id: str = USER_ID) -> dict[str, Any]:
    seeded = seed_adaptation_memories(store, user_id)
    noisy_import_memories = seed_noisy_adaptation_imports(store, user_id)
    local_file_citation = assert_adaptation_local_file_citations_sanitized(store, user_id)
    artifact = store.agent_adaptation(
        user_id,
        query="Home Review Ask product work tradeoffs local-first adaptation",
        target="Claude",
        limit=8,
        include_pending=False,
    )
    rules = artifact.get("rules") or []
    markdown = artifact.get("markdown") or ""
    serialized = json.dumps(artifact, sort_keys=True)
    rules_by_layer: dict[str, list[dict[str, Any]]] = {}
    for rule in rules:
        rules_by_layer.setdefault(str(rule.get("layer") or ""), []).append(rule)
    evidence_ids = {item.get("id") for item in artifact.get("evidence") or []}
    checks: list[dict[str, Any]] = []

    noisy_joined = "\n".join(str(memory.get("content") or "") for memory in noisy_import_memories)
    _check(
        checks,
        "noisy_user_speaker_preferences_imported",
        all(phrase in noisy_joined for phrase in NOISY_ADAPTATION_USER_PHRASES),
        "Noisy email and Slack imports include user-authored personal preference signals.",
        {"phrases": NOISY_ADAPTATION_USER_PHRASES, "imported_memories": noisy_import_memories},
    )
    personal_signal_payload = json.dumps(
        {
            "imported_memories": noisy_import_memories,
            "rules": artifact.get("rules") or [],
            "evidence": artifact.get("evidence") or [],
            "style_guide": artifact.get("style_guide") or [],
            "preference_policy": artifact.get("preference_policy") or [],
            "negative_constraints": artifact.get("negative_constraints") or [],
        },
        sort_keys=True,
    )
    leaked_external_signals = [
        guard
        for guard in NOISY_ADAPTATION_EXTERNAL_SIGNAL_GUARDS
        if guard["phrase"] in personal_signal_payload
    ]
    _check(
        checks,
        "noisy_external_speaker_preferences_excluded",
        not leaked_external_signals,
        "External email and Slack speakers do not become user preference/style adaptation signals.",
        {"leaked": leaked_external_signals},
    )

    expected_layers = {seed.layer for seed in ADAPTATION_SEEDS}
    actual_layers = {str(rule.get("layer")) for rule in rules}
    _check(
        checks,
        "rules_cover_all_memory_layers",
        expected_layers.issubset(actual_layers),
        "Adaptation rules include semantic, episodic, style, decision, preference, negative, and procedural layers.",
        {"expected": sorted(expected_layers), "actual": sorted(actual_layers)},
    )

    for seed in ADAPTATION_SEEDS:
        layer_rules = rules_by_layer.get(seed.layer) or []
        rule = next((item for item in layer_rules if item.get("memory_id") == seed.id), {})
        instruction = str(rule.get("instruction") or "")
        _check(
            checks,
            f"{seed.layer}_rule_uses_expected_memory",
            seed.phrase in instruction and rule.get("memory_id") == seed.id,
            f"{seed.layer} rule is grounded in the expected approved memory.",
            {"memory_id": rule.get("memory_id"), "instruction": instruction},
        )
        _check(
            checks,
            f"{seed.layer}_rule_has_citation",
            bool(rule.get("source_url")),
            f"{seed.layer} rule carries a source_url citation.",
            {"source_url": rule.get("source_url")},
        )
        _check(
            checks,
            f"{seed.layer}_evidence_included",
            seed.id in evidence_ids,
            f"{seed.layer} source memory appears in adaptation evidence.",
        )

    _check(
        checks,
        "pending_memory_excluded_by_default",
        PENDING_PHRASE not in serialized,
        "Pending memory is excluded when include_pending is false, even if user settings allow pending context.",
    )
    policy_expectations = {
        "style_guide": ("style", "direct tradeoffs"),
        "preference_policy": ("preference", "clearest user path"),
        "decision_policy": ("decision", "Home, Review, Ask"),
        "negative_constraints": ("negative", "copy-only memory brief"),
    }
    for field, (layer, phrase) in policy_expectations.items():
        values = artifact.get(field) or []
        _check(
            checks,
            f"{field}_is_structured",
            bool(values) and all(item.get("memory_id") and item.get("source_url") for item in values),
            f"{field} exposes structured {layer} guidance with memory IDs and citations.",
            {"values": values},
        )
        _check(
            checks,
            f"{field}_contains_expected_guidance",
            any(phrase in str(item.get("instruction") or "") for item in values),
            f"{field} includes expected {layer} guidance.",
        )
    _check(
        checks,
        "citation_requirements_are_explicit",
        all(
            phrase in " ".join(artifact.get("citation_requirements") or [])
            for phrase in ("memory_id", "source_url", "pending memories")
        ),
        "Adaptation artifact states memory-id, source-url, and pending-memory citation requirements.",
        {"citation_requirements": artifact.get("citation_requirements")},
    )
    _check(
        checks,
        "complete_coverage_has_no_warnings",
        artifact.get("coverage_warnings") == [],
        "Complete representative layer coverage produces no coverage warnings.",
        {"coverage_warnings": artifact.get("coverage_warnings")},
    )
    _check(
        checks,
        "operating_principles_include_identity_safety",
        any("Do not claim to be the user" in item for item in artifact.get("operating_principles") or []),
        "Adaptation explicitly prevents identity overclaiming.",
    )
    _check(
        checks,
        "operating_principles_prioritize_newest_user_message",
        any("newest message" in item for item in artifact.get("operating_principles") or []),
        "Adaptation yields to the user's newest message over older memory.",
    )
    _check(
        checks,
        "limitations_disclose_not_finetuned",
        any("not a fine-tuned model" in item for item in artifact.get("limitations") or []),
        "Adaptation limitations disclose that this is not a fine-tuned model or complete copy of the user.",
    )
    _check(
        checks,
        "readiness_is_production_candidate",
        int(artifact.get("readiness") or 0) >= 80,
        "Representative complete memory coverage produces a production-candidate readiness score.",
        {"readiness": artifact.get("readiness")},
    )
    _check(
        checks,
        "markdown_contains_cited_rules",
        all(seed.source_url in markdown and seed.id in markdown for seed in ADAPTATION_SEEDS),
        "Markdown export includes every seeded memory id and source citation.",
    )

    failures = [check for check in checks if check["status"] != "ok"]
    return {
        "status": "failed" if failures else "ok",
        "seeded_memories": len(seeded),
        "noisy_import_memories": len(noisy_import_memories),
        "local_file_citation": local_file_citation,
        "rules": len(rules),
        "readiness": artifact.get("readiness"),
        "target": artifact.get("target"),
        "checks": checks,
        "failures": failures,
    }


def run_adaptation_eval(db_path: Path, vault_path: Path | None = None, user_id: str = USER_ID) -> dict[str, Any]:
    init_db(db_path)
    store = CortexStore(db_path, vault_path or db_path.with_suffix(".vault"))
    return evaluate_adaptation(store, user_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Cortex agent adaptation quality evaluation.")
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--vault-path", type=Path, default=None)
    parser.add_argument("--user-id", default=USER_ID)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.db_path:
        result = run_adaptation_eval(
            args.db_path.expanduser(),
            args.vault_path.expanduser() if args.vault_path else None,
            args.user_id,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="cortex-adaptation-eval-") as tmp:
            root = Path(tmp)
            result = run_adaptation_eval(root / "adaptation-eval.sqlite", root / "Cortex.vault", args.user_id)

    if args.json or result["status"] != "ok":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps({key: result[key] for key in ("status", "seeded_memories", "rules", "readiness", "target")}, indent=2))
    if result["status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
