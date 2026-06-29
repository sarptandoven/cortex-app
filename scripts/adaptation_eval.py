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
        id="aq_decision_five_tabs",
        kind="decision",
        layer="decision",
        content="Decision: Cortex should use the five-tab Model, Sources, Review, Ask, Trust flow.",
        phrase="five-tab Model, Sources, Review, Ask, Trust flow",
        source_url="cortex-eval://adaptation/decision#five-tabs",
        topics=("decision", "ui", "tabs"),
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


def _check(checks: list[dict[str, Any]], name: str, ok: bool, detail: str, payload: dict[str, Any] | None = None) -> None:
    checks.append({"name": name, "status": "ok" if ok else "failed", "detail": detail, "payload": payload or {}})


def evaluate_adaptation(store: CortexStore, user_id: str = USER_ID) -> dict[str, Any]:
    seeded = seed_adaptation_memories(store, user_id)
    artifact = store.agent_adaptation(
        user_id,
        query="five tab product work tradeoffs local-first adaptation",
        target="Claude",
        limit=8,
        include_pending=False,
    )
    rules = artifact.get("rules") or []
    markdown = artifact.get("markdown") or ""
    serialized = json.dumps(artifact, sort_keys=True)
    rules_by_layer = {rule.get("layer"): rule for rule in rules}
    evidence_ids = {item.get("id") for item in artifact.get("evidence") or []}
    checks: list[dict[str, Any]] = []

    expected_layers = {seed.layer for seed in ADAPTATION_SEEDS}
    actual_layers = {str(rule.get("layer")) for rule in rules}
    _check(
        checks,
        "rules_cover_all_memory_layers",
        expected_layers.issubset(actual_layers),
        "Adaptation rules include semantic, episodic, style, decision, preference, and negative layers.",
        {"expected": sorted(expected_layers), "actual": sorted(actual_layers)},
    )

    for seed in ADAPTATION_SEEDS:
        rule = rules_by_layer.get(seed.layer) or {}
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
        "decision_policy": ("decision", "five-tab Model"),
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
