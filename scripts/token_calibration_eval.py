from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.storage import resolve_profile


# Token-calibration gate (build-plan #7/#9). The context packer budgets a pack in TOKENS but only
# ever sees CHARACTERS, so it converts with a per-model `chars_per_token` divisor carried on each
# ModelProfile. This gate proves that profile-calibrated conversion is a genuinely better token
# estimate than the flat `chars/4` heuristic the legacy packer used — per model, on a fixture of
# representative text.
#
# Metric: MAPE (mean absolute percentage error) of an estimator's token count against the
# fixture's per-model "true" token count. Two estimators compete:
#   * profile: resolve_profile(model).estimate_tokens(text) == ceil(len(text) / chars_per_token)
#   * flat:    ceil(len(text) / 4.0)   (the legacy chars/4 baseline)
#
# Gates:
#   * For every model whose calibrated divisor differs from 4.0 (claude, cursor), the profile
#     estimator MUST strictly beat flat: profile_mape < flat_mape.
#   * For every model (including generic/gpt whose divisor == 4.0, where the two estimators are
#     identical), the profile estimator must never be WORSE than flat: profile_mape <= flat_mape.
#   * Aggregate: the mean profile MAPE across the calibrated models is at least IMPROVEMENT_MARGIN
#     (relative) below the mean flat MAPE — so the win is material, not incidental rounding.
#
# NOTE: the fixture's per-model token counts are SIMULATED placeholders (see fixture `note`).
# They are chosen near each profile's divisor but offset from a pure constant, so the profile
# estimator is good-but-not-perfect. Swap in true vendor-tokenizer counts when founder-provided;
# the gate logic is unchanged.
FIXTURE_PATH = ROOT / "scripts" / "token_calibration_fixture.json"

# Models whose calibrated divisor differs from the flat 4.0 baseline, so calibration must WIN.
STRICT_IMPROVEMENT_MODELS: tuple[str, ...] = ("claude", "cursor")
# Models whose calibrated divisor equals 4.0, so calibration ties the baseline (must not regress).
TIE_MODELS: tuple[str, ...] = ("generic", "gpt")
ALL_MODELS: tuple[str, ...] = STRICT_IMPROVEMENT_MODELS + TIE_MODELS

FLAT_CHARS_PER_TOKEN = 4.0
# Relative margin by which mean calibrated MAPE must undercut mean flat MAPE (0.20 == 20% better).
IMPROVEMENT_MARGIN = 0.20
# Numerical slack for the non-regression (tie) assertion.
TIE_EPSILON = 1e-9


def _flat_estimate(text: str) -> int:
    return max(1, math.ceil(len(str(text)) / FLAT_CHARS_PER_TOKEN))


def _profile_estimate(model: str, text: str) -> int:
    # None resolves to the generic profile; a real model name resolves to its calibrated profile.
    resolved = resolve_profile(None if model == "generic" else model, "agent")
    return resolved.estimate_tokens(text)


def _mape(estimates: list[int], truths: list[int]) -> float:
    errors = [abs(est - true) / true for est, true in zip(estimates, truths) if true > 0]
    return round(sum(errors) / len(errors), 6) if errors else 0.0


def _load_fixture(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not data.get("samples"):
        raise ValueError(f"fixture {path} has no samples")
    return data


def run_token_calibration_eval(fixture_path: Path = FIXTURE_PATH) -> dict[str, Any]:
    fixture = _load_fixture(fixture_path)
    samples = fixture["samples"]

    per_model: dict[str, dict[str, Any]] = {}
    for model in ALL_MODELS:
        texts = [str(s.get("text") or "") for s in samples]
        truths = [int((s.get("tokens") or {}).get(model, 0)) for s in samples]
        # A model missing from the fixture would silently pass; require full coverage.
        missing = [s.get("id") for s in samples if model not in (s.get("tokens") or {})]
        profile_est = [_profile_estimate(model, t) for t in texts]
        flat_est = [_flat_estimate(t) for t in texts]
        profile_mape = _mape(profile_est, truths)
        flat_mape = _mape(flat_est, truths)
        resolved = resolve_profile(None if model == "generic" else model, "agent")
        strict = model in STRICT_IMPROVEMENT_MODELS
        if missing:
            beats = False
            reason = f"fixture missing tokens for samples {missing}"
        elif strict:
            beats = profile_mape < flat_mape
            reason = "profile_mape < flat_mape required (calibrated divisor != 4.0)"
        else:
            beats = profile_mape <= flat_mape + TIE_EPSILON
            reason = "profile_mape <= flat_mape required (divisor == 4.0, tie ok)"
        per_model[model] = {
            "model": model,
            "resolved_profile": resolved.name,
            "chars_per_token": resolved.chars_per_token,
            "sample_count": len(samples),
            "profile_mape": profile_mape,
            "flat_mape": flat_mape,
            "improvement_abs": round(flat_mape - profile_mape, 6),
            "improvement_rel": round((flat_mape - profile_mape) / flat_mape, 6) if flat_mape > 0 else 0.0,
            "strict_improvement_required": strict,
            "criterion": reason,
            "ok": beats,
        }

    calibrated = [per_model[m] for m in STRICT_IMPROVEMENT_MODELS]
    mean_profile = round(sum(m["profile_mape"] for m in calibrated) / len(calibrated), 6) if calibrated else 0.0
    mean_flat = round(sum(m["flat_mape"] for m in calibrated) / len(calibrated), 6) if calibrated else 0.0
    aggregate_rel = round((mean_flat - mean_profile) / mean_flat, 6) if mean_flat > 0 else 0.0

    return {
        "harness": "token_calibration_eval",
        "fixture": str(fixture_path),
        "fixture_note": fixture.get("note"),
        "flat_baseline_chars_per_token": FLAT_CHARS_PER_TOKEN,
        "improvement_margin": IMPROVEMENT_MARGIN,
        "aggregate": {
            "calibrated_models": list(STRICT_IMPROVEMENT_MODELS),
            "mean_profile_mape": mean_profile,
            "mean_flat_mape": mean_flat,
            "improvement_rel": aggregate_rel,
        },
        "per_model": per_model,
    }


def check_token_calibration(result: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    per_model = result.get("per_model") or {}
    for model, entry in per_model.items():
        if not entry.get("ok"):
            failures.append(
                f"[{model}] calibration did not beat flat: "
                f"profile_mape={entry.get('profile_mape')} flat_mape={entry.get('flat_mape')} "
                f"({entry.get('criterion')})"
            )
    aggregate = result.get("aggregate") or {}
    rel = float(aggregate.get("improvement_rel") or 0.0)
    if rel < IMPROVEMENT_MARGIN:
        failures.append(
            f"aggregate calibrated improvement_rel={rel} < required margin {IMPROVEMENT_MARGIN} "
            f"(mean_profile_mape={aggregate.get('mean_profile_mape')} vs mean_flat_mape={aggregate.get('mean_flat_mape')})"
        )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Cortex per-model token-calibration gate.")
    parser.add_argument("--fixture", type=Path, default=FIXTURE_PATH, help="Path to the token-calibration fixture JSON.")
    parser.add_argument("--report-only", action="store_true", help="Print metrics without failing on regressions.")
    args = parser.parse_args()

    result = run_token_calibration_eval(args.fixture.expanduser())
    print(json.dumps(result, indent=2, sort_keys=True))

    failures = check_token_calibration(result)
    if failures and not args.report_only:
        print("\nTOKEN CALIBRATION GATE FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
