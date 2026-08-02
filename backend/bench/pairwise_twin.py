from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from backend.app.twin_eval import (
    BradleyTerryRanker,
    CitedProfileItem,
    ComparisonOutcome,
    DeterministicGenerator,
    EvaluationPrompt,
    EvaluationReport,
    HeldOutProfile,
    ObservableFeature,
    ObservableFeatureJudge,
    ObservableRubric,
    ObservableRule,
    PairwiseEvaluationRunner,
    RepeatedSwappedStrategy,
    canonical_hash,
    paired_clustered_bootstrap_delta,
    reliability_metrics,
    observable_utility,
)


SYSTEMS = ("strong", "partial", "mismatch")


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    stratum: str
    task: str
    candidates: Mapping[str, str]
    rubric: ObservableRubric
    expected_scores: Mapping[str, float] | None


def _rule(
    feature: ObservableFeature,
    value: float | str | tuple[str, ...],
    *,
    weight: float = 1.0,
    hard: bool = False,
) -> ObservableRule:
    return ObservableRule(feature, value, weight=weight, hard_constraint=hard)


def benchmark_profile() -> HeldOutProfile:
    return HeldOutProfile(
        "pairwise-subjective-profile-v1",
        (
            CitedProfileItem("style-concise", "My writing style is concise with short declarative sentences.", layer="style"),
            CitedProfileItem("style-direct", "I write without corporate filler phrases.", layer="style"),
            CitedProfileItem("style-lowercase", "I use lowercase in casual chat messages.", layer="style"),
            CitedProfileItem("style-example", "My posts open with a concrete example, never a definition.", layer="style"),
            CitedProfileItem("negative-exclaim", "I dislike exclamation points in professional messages.", layer="negative"),
            CitedProfileItem("negative-agenda", "I refuse meetings without an agenda.", layer="negative"),
            CitedProfileItem("negative-filler", "Never use corporate filler in my email.", layer="negative"),
            CitedProfileItem("negative-friday", "Never deploy on Friday.", layer="negative"),
            CitedProfileItem("preference-detail", "Use necessary detail for high-risk decisions.", layer="preference"),
            CitedProfileItem("decision-monday", "Decided production deploys happen Monday.", layer="decision"),
            CitedProfileItem(
                "agent-noise",
                "The user probably likes enthusiastic prose.",
                layer="style",
                author_class="agent",
            ),
            CitedProfileItem(
                "inactive-style",
                "Write every update in formal title case.",
                layer="style",
                status="archived",
            ),
        ),
    )


def benchmark_cases() -> tuple[BenchmarkCase, ...]:
    cases: list[BenchmarkCase] = []

    def add(
        case_id: str,
        stratum: str,
        task: str,
        candidates: tuple[str, str, str],
        citations: tuple[str, ...],
        rules: tuple[ObservableRule, ...],
        expected: tuple[float, float, float] | None = (3.0, 2.0, 1.0),
        tie_epsilon: float = 0.0,
    ) -> None:
        cases.append(
            BenchmarkCase(
                case_id,
                stratum,
                task,
                dict(zip(SYSTEMS, candidates)),
                ObservableRubric(case_id, citations, rules, tie_epsilon),
                dict(zip(SYSTEMS, expected)) if expected is not None else None,
            )
        )

    # Clear style signals.
    add(
        "style-concise",
        "clear_style",
        "Post a project update.",
        (
            "api tests pass. launch stays on monday.",
            "The API tests pass, and the planned launch remains on Monday.",
            "I wanted to provide a comprehensive update regarding our ongoing launch initiative and the many associated workstreams currently in progress.",
        ),
        ("style-concise",),
        (_rule(ObservableFeature.MAX_WORDS, 12), _rule(ObservableFeature.MAX_SENTENCE_WORDS, 8)),
    )
    add(
        "style-direct",
        "clear_style",
        "Decline a vendor call.",
        (
            "No, this is not a fit.",
            "Thanks, but we will pass.",
            "I hope this message finds you well. At this point in time, we regretfully must decline.",
        ),
        ("style-direct",),
        (
            _rule(ObservableFeature.MAX_WORDS, 10),
            _rule(ObservableFeature.PROHIBITED_PHRASE, "i hope this message finds you"),
        ),
        expected=(2.0, 2.0, 1.0),
    )
    add(
        "style-lowercase",
        "clear_style",
        "Send a casual chat reply.",
        ("yep, shipped it this morning", "quick update: shipped this morning", "URGENT UPDATE: SHIPPED THIS MORNING"),
        ("style-lowercase",),
        (
            _rule(ObservableFeature.LOWERCASE_RATIO_MIN, 0.95),
            _rule(ObservableFeature.MAX_WORDS, 7),
        ),
        expected=(2.0, 2.0, 1.0),
    )
    add(
        "style-example",
        "clear_style",
        "Open a short post about retrieval quality.",
        (
            "For example, one noisy note can bury the right answer.",
            "One noisy note can bury the right answer.",
            "Retrieval quality is defined as the measurement of relevant information retrieval.",
        ),
        ("style-example",),
        (
            _rule(ObservableFeature.REQUIRED_PHRASE, "for example"),
            _rule(ObservableFeature.PROHIBITED_PHRASE, "is defined as"),
        ),
    )

    # Explicit negative constraints.
    add(
        "negative-exclaim",
        "negative_constraint",
        "Write a professional status note.",
        ("The build is ready.", "The build is ready for review.", "The build is ready!!!"),
        ("negative-exclaim",),
        (
            _rule(ObservableFeature.MAX_EXCLAMATIONS, 0, hard=True),
            _rule(ObservableFeature.MAX_WORDS, 8),
        ),
        expected=(2.0, 2.0, 1.0),
    )
    add(
        "negative-agenda",
        "negative_constraint",
        "Accept a recurring meeting.",
        (
            "Yes. Send the agenda first.",
            "I can join if there is an agenda.",
            "Yes, add it to my calendar.",
        ),
        ("negative-agenda",),
        (
            _rule(ObservableFeature.REQUIRED_PHRASE, "agenda", hard=True),
            _rule(ObservableFeature.MAX_WORDS, 8),
        ),
        expected=(2.0, 2.0, 1.0),
    )
    add(
        "negative-filler",
        "negative_constraint",
        "Send a work email.",
        (
            "The review is complete. Two issues remain.",
            "Thanks. The review is complete.",
            "I hope this message finds you well. The review is complete.",
        ),
        ("negative-filler",),
        (
            _rule(ObservableFeature.PROHIBITED_PHRASE, "i hope this message finds you", hard=True),
            _rule(ObservableFeature.MAX_WORDS, 8),
        ),
        expected=(2.0, 2.0, 1.0),
    )
    add(
        "negative-friday",
        "negative_constraint",
        "Choose a production deploy day.",
        (
            "Deploy Monday after the smoke test.",
            "Deploy after the smoke test.",
            "Deploy Friday after the smoke test.",
        ),
        ("negative-friday", "decision-monday"),
        (
            _rule(ObservableFeature.PROHIBITED_PHRASE, "friday", hard=True),
            _rule(ObservableFeature.REQUIRED_PHRASE, "monday"),
        ),
    )

    # Near ties: strong and partial are deliberately equal under observable rules.
    for index, pair in enumerate(
        (
            ("looks good. ship it.", "ship it. looks good."),
            ("review complete. no blockers.", "no blockers. review complete."),
            ("monday works for me.", "monday is fine."),
            ("thanks, i will review.", "i will review, thanks."),
        ),
        start=1,
    ):
        add(
            f"near-tie-{index}",
            "near_tie",
            "Send a concise chat response.",
            (pair[0], pair[1], "I hope this message finds you well! This is a very detailed response."),
            ("style-concise", "negative-exclaim"),
            (
                _rule(ObservableFeature.MAX_WORDS, 6),
                _rule(ObservableFeature.MAX_EXCLAMATIONS, 0),
            ),
            expected=(2.0, 2.0, 1.0),
        )

    # Conflicting signals with explicit precedence.
    conflict_specs = (
        (
            "conflict-detail",
            "Explain a risky migration.",
            "Risk: data loss. Back up first. Then migrate in two verified steps.",
            "Back up first, then migrate.",
            "Migrate now.",
            (_rule(ObservableFeature.TASK_TERMS, ("risk", "back up", "migrate")), _rule(ObservableFeature.MAX_WORDS, 16)),
        ),
        (
            "conflict-professional",
            "Give blunt professional feedback.",
            "The proposal is unclear. Rewrite the rollout section.",
            "Please rewrite the rollout section.",
            "Amazing proposal!!! Maybe consider a tiny update.",
            (_rule(ObservableFeature.MAX_EXCLAMATIONS, 0, hard=True), _rule(ObservableFeature.TASK_TERMS, ("proposal", "rewrite"))),
        ),
        (
            "conflict-decision",
            "Pick a deploy day despite generic flexibility.",
            "Use Monday. That is the recorded decision.",
            "Use a weekday after testing.",
            "Friday is easiest.",
            (_rule(ObservableFeature.PROHIBITED_PHRASE, "friday", hard=True), _rule(ObservableFeature.REQUIRED_PHRASE, "monday")),
        ),
        (
            "conflict-length",
            "Document a security exception.",
            "Risk: token exposure. Scope: staging only. Expire it Monday. Owner: security.",
            "Approve the staging exception until Monday.",
            "Approved.",
            (_rule(ObservableFeature.TASK_TERMS, ("risk", "staging", "monday", "security")), _rule(ObservableFeature.MAX_WORDS, 16)),
        ),
    )
    for case_id, task, strong, partial, mismatch, rules in conflict_specs:
        add(
            case_id,
            "conflicting",
            task,
            (strong, partial, mismatch),
            ("style-concise", "preference-detail", "negative-exclaim", "decision-monday"),
            rules,
        )

    # Sparse/noisy evidence must abstain rather than guess generic quality.
    sparse_specs = (
        ("sparse-missing", ("missing-memory",), ()),
        ("sparse-agent-only", ("agent-noise",), (_rule(ObservableFeature.MAX_WORDS, 8),)),
        ("sparse-inactive", ("inactive-style",), (_rule(ObservableFeature.MAX_WORDS, 8),)),
        ("sparse-no-rubric", ("style-concise",), ()),
    )
    for case_id, citations, rules in sparse_specs:
        add(
            case_id,
            "sparse_noisy",
            "Answer an unrelated personal preference question.",
            ("Option one.", "Option two.", "Option three."),
            citations,
            rules,
            expected=None,
        )

    # Deceptive outputs: claims of fidelity cannot override observable violations.
    deceptive_specs = (
        (
            "deceptive-claim",
            "Post a concise update.",
            "tests pass. ship monday.",
            "This matches your concise style: tests pass.",
            "This matches your style perfectly!!! I hope this message finds you well.",
            (_rule(ObservableFeature.MAX_WORDS, 8), _rule(ObservableFeature.MAX_EXCLAMATIONS, 0, hard=True)),
        ),
        (
            "deceptive-wrong-task",
            "Report test and launch status.",
            "Tests pass. Launch Monday.",
            "Tests pass.",
            "Your writing style is concise and direct.",
            (_rule(ObservableFeature.TASK_TERMS, ("tests", "launch", "monday")),),
        ),
        (
            "deceptive-filler",
            "Decline a meeting.",
            "No. There is no agenda.",
            "Declining because there is no agenda.",
            "I hope this message finds you well. I enthusiastically accept!",
            (
                _rule(ObservableFeature.REQUIRED_PHRASE, "agenda", hard=True),
                _rule(ObservableFeature.PROHIBITED_PHRASE, "i hope this message finds you", hard=True),
            ),
        ),
        (
            "deceptive-surface",
            "Choose a deploy day.",
            "monday after tests.",
            "after tests.",
            "friday after tests.",
            (_rule(ObservableFeature.PROHIBITED_PHRASE, "friday", hard=True), _rule(ObservableFeature.REQUIRED_PHRASE, "monday")),
        ),
    )
    for case_id, task, strong, partial, mismatch, rules in deceptive_specs:
        add(
            case_id,
            "deceptive",
            task,
            (strong, partial, mismatch),
            ("style-concise", "negative-exclaim", "negative-agenda", "negative-filler", "negative-friday", "decision-monday"),
            rules,
            expected=(2.0, 2.0, 1.0) if case_id in {"deceptive-claim", "deceptive-filler"} else (3.0, 2.0, 1.0),
        )

    if len(cases) != 24:
        raise AssertionError(f"benchmark must contain 24 cases, got {len(cases)}")
    return tuple(cases)


def benchmark_prompts() -> tuple[EvaluationPrompt, ...]:
    return tuple(
        EvaluationPrompt(
            case.case_id,
            case.task,
            {"stratum": case.stratum},
        )
        for case in benchmark_cases()
    )


def _expected_outcome(case: BenchmarkCase, system_a: str, system_b: str) -> ComparisonOutcome:
    if case.expected_scores is None:
        return ComparisonOutcome.ABSTAIN
    delta = case.expected_scores[system_a] - case.expected_scores[system_b]
    if delta > 0:
        return ComparisonOutcome.LEFT
    if delta < 0:
        return ComparisonOutcome.RIGHT
    return ComparisonOutcome.TIE


def _absolute_bin(utility: float) -> int:
    """Preregistered five-bin absolute-rating analogue for the same rubric."""
    if utility <= -5.0:
        return 1
    if utility < 0.0:
        return 2
    if utility < 1.0:
        return 3
    if utility < 2.0:
        return 4
    return 5


def build_offline_benchmark_report(
    *,
    seed: int = 20260724,
    repetitions: int = 3,
) -> EvaluationReport:
    cases = benchmark_cases()
    case_by_id = {case.case_id: case for case in cases}
    prompts = benchmark_prompts()
    rubrics = {case.case_id: case.rubric for case in cases}

    generators = tuple(
        DeterministicGenerator(
            system_id,
            lambda prompt, profile, child_seed, system_id=system_id: case_by_id[prompt.prompt_id].candidates[system_id],
        )
        for system_id in SYSTEMS
    )
    return PairwiseEvaluationRunner(
        generators,
        ObservableFeatureJudge(rubrics),
        RepeatedSwappedStrategy(repetitions=repetitions),
        BradleyTerryRanker(),
        metadata={
            "dataset_id": "subjective-mechanical-v1",
            "claim_boundary": "synthetic observable-feature preference recovery only",
        },
    ).run(benchmark_profile(), prompts, seed=seed)


def run_offline_benchmark_with_report(
    *,
    seed: int = 20260724,
    repetitions: int = 3,
) -> tuple[dict, EvaluationReport]:
    cases = benchmark_cases()
    case_by_id = {case.case_id: case for case in cases}
    report = build_offline_benchmark_report(seed=seed, repetitions=repetitions)

    correct = 0
    pairwise_cluster_values: dict[str, list[float]] = {case.case_id: [] for case in cases}
    for resolved in report.resolved_comparisons:
        case = case_by_id[resolved.prompt_id]
        expected = _expected_outcome(case, resolved.system_a_id, resolved.system_b_id)
        observation = float(resolved.outcome is expected)
        correct += int(observation)
        pairwise_cluster_values[case.case_id].append(observation)

    absolute_cluster_values: dict[str, list[float]] = {}
    for case in cases:
        observations: list[float] = []
        utilities = {
            system: observable_utility(case.candidates[system], case.rubric)[0]
            for system in SYSTEMS
        }
        bins = {system: _absolute_bin(utilities[system]) for system in SYSTEMS}
        for system_a, system_b in (
            ("mismatch", "partial"),
            ("mismatch", "strong"),
            ("partial", "strong"),
        ):
            expected = _expected_outcome(case, system_a, system_b)
            if case.expected_scores is None:
                predicted = ComparisonOutcome.ABSTAIN
            elif bins[system_a] > bins[system_b]:
                predicted = ComparisonOutcome.LEFT
            elif bins[system_a] < bins[system_b]:
                predicted = ComparisonOutcome.RIGHT
            else:
                predicted = ComparisonOutcome.TIE
            observations.append(float(predicted is expected))
        absolute_cluster_values[case.case_id] = observations

    paired_delta = paired_clustered_bootstrap_delta(
        absolute_cluster_values,
        pairwise_cluster_values,
        seed=seed,
        resamples=2_000,
    )

    reliability = reliability_metrics(report)
    reported_confidences = [
        float(record.decision.confidence)
        for record in report.comparisons
        if record.decision.confidence is not None
    ]
    strict_expected = [case for case in cases if case.expected_scores is not None]
    expected_aggregate = {
        system: sum(case.expected_scores[system] for case in strict_expected)
        for system in SYSTEMS
    }
    expected_top = max(expected_aggregate, key=expected_aggregate.get)
    actual_top = report.ranking.ratings[0].system_id

    hard_violation_wins = 0
    hard_violation_decisions = 0
    for record in report.comparisons:
        left_bad = bool(record.decision.metadata.get("left_hard_violations"))
        right_bad = bool(record.decision.metadata.get("right_hard_violations"))
        if left_bad == right_bad:
            continue
        hard_violation_decisions += 1
        violating_won = (
            left_bad and record.decision.outcome is ComparisonOutcome.LEFT
        ) or (
            right_bad and record.decision.outcome is ComparisonOutcome.RIGHT
        )
        hard_violation_wins += int(violating_won)

    dataset_manifest = {
        "schema_version": "subjective-mechanical-dataset/v1",
        "profile": benchmark_profile(),
        "cases": cases,
        "systems": SYSTEMS,
        "oracle_version": "observable_feature_oracle_v1",
        "absolute_baseline_version": "absolute_rubric_five_bin_v1",
    }
    absolute_accuracy = (
        sum(sum(values) for values in absolute_cluster_values.values())
        / sum(len(values) for values in absolute_cluster_values.values())
    )
    payload = {
        "schema_version": "pairwise-twin-benchmark/v1",
        "dataset_id": "subjective-mechanical-v1",
        "dataset_digest": canonical_hash(dataset_manifest),
        "run_id": report.run_id,
        "artifact_digest": report.artifact_digest,
        "seed": seed,
        "cases": len(cases),
        "raw_judgments": reliability.raw_judgments,
        "logical_comparisons": reliability.logical_comparisons,
        "synthetic_pair_accuracy": correct / len(report.resolved_comparisons),
        "synthetic_five_bin_oracle_baseline_accuracy": absolute_accuracy,
        "paired_recovery_delta": paired_delta.estimate,
        "paired_recovery_delta_ci95": {
            "low": paired_delta.low,
            "high": paired_delta.high,
            "clusters": paired_delta.clusters,
            "resamples": paired_delta.resamples,
        },
        "swap_agreement": reliability.swap_agreement,
        "repeat_agreement": reliability.repeat_agreement,
        "position_bias": reliability.position_bias,
        "invalid_rate": reliability.invalid / reliability.logical_comparisons,
        "abstentions": reliability.abstentions,
        "reported_confidence_coverage": (
            len(reported_confidences) / reliability.raw_judgments
        ),
        "mean_reported_confidence": (
            sum(reported_confidences) / len(reported_confidences)
            if reported_confidences
            else None
        ),
        "hard_constraint_violation_win_rate": (
            hard_violation_wins / hard_violation_decisions
            if hard_violation_decisions
            else 0.0
        ),
        "ranking_connected": report.ranking.diagnostics.connected,
        "ranking_converged": report.ranking.diagnostics.converged,
        "ranking": [
            {
                "system_id": rating.system_id,
                "score": rating.score,
                "rank": rating.rank,
                "comparisons": rating.comparisons,
                "wins": rating.wins,
            }
            for rating in report.ranking.ratings
        ],
        "expected_top": expected_top,
        "actual_top": actual_top,
        "top_recovered": actual_top == expected_top,
        "claim_boundary": (
            "Mechanical validity only. Owner taste improvement requires blinded, "
            "held-out owner labels."
        ),
    }
    payload["mechanical_passed"] = all(
        (
            payload["synthetic_pair_accuracy"] == 1.0,
            payload["paired_recovery_delta"] >= 0.0,
            payload["swap_agreement"] == 1.0,
            payload["repeat_agreement"] == 1.0,
            payload["position_bias"] == 0.0,
            payload["invalid_rate"] == 0.0,
            payload["hard_constraint_violation_win_rate"] == 0.0,
            payload["ranking_connected"],
            payload["ranking_converged"],
            payload["top_recovered"],
        )
    )
    # Backward-compatible CLI exit contract. The explicit name above prevents
    # this synthetic plumbing gate from being mistaken for product approval.
    payload["passed"] = payload["mechanical_passed"]
    return payload, report


def run_offline_benchmark(*, seed: int = 20260724, repetitions: int = 3) -> dict:
    payload, _ = run_offline_benchmark_with_report(seed=seed, repetitions=repetitions)
    return payload
