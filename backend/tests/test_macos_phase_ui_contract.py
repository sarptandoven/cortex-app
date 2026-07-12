"""Contract tests for the phases 4-6 macOS UI (twin, proactive alerts, usage metrics).

Swift is not compiled in CI here, so these tests pin the wiring the same way
test_macos_connector_ui_contract.py does: by asserting the Swift sources reference
the exact REST paths, JSON field names, and view relationships the backend serves.
A rename on either side breaks this file before it breaks the app.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CORTEX_APP = ROOT / "macos" / "Sources" / "CortexApp.swift"
MODELS = ROOT / "macos" / "Sources" / "TwinAlertsModels.swift"
VIEWS = ROOT / "macos" / "Sources" / "TwinAlertsViews.swift"
REVIEW_TAB = ROOT / "macos" / "Sources" / "ReviewTab.swift"
ASK_TAB = ROOT / "macos" / "Sources" / "AskTab.swift"
MODEL_TAB = ROOT / "macos" / "Sources" / "ModelTab.swift"
CONNECTIONS_SHEET = ROOT / "macos" / "Sources" / "ConnectionsPrivacySheet.swift"


class PhaseUIEndpointContractTests(unittest.TestCase):
    """The app must call the endpoints the servers actually serve."""

    def setUp(self) -> None:
        self.app_source = CORTEX_APP.read_text(encoding="utf-8")

    def test_appstate_calls_the_phase_endpoints(self) -> None:
        for path in (
            "/v1/alerts?status=pending",
            "/resolve?resolution=",
            "/v1/twin/scorecard?days=90",
            "/v1/twin/would-i",
            "/v1/twin/grade",
            "/v1/alerts/precision?days=30",
            "/v1/prefetch/hit-rate?days=30",
            "/v1/eval/scorecard?days=7",
        ):
            with self.subTest(path=path):
                self.assertIn(path, self.app_source)

    def test_alert_resolution_labels_round_trip(self) -> None:
        # resolve_proactive_alert accepts exactly these two verdicts; the UI must send
        # them verbatim (dismissal-as-label is the annoyance budget's training signal).
        views = read(VIEWS)
        self.assertIn('resolve("dismissed")', views)
        self.assertIn('resolve("accepted")', views)
        self.assertIn("state.resolveProactiveAlert(alert, resolution: resolution)", views)

    def test_twin_grade_outcomes_round_trip(self) -> None:
        # grade_twin_prediction accepts correct/incorrect/unclear.
        views = read(VIEWS)
        self.assertIn('grade("correct")', views)
        self.assertIn('grade("incorrect")', views)
        self.assertIn('grade("unclear")', views)

    def test_settings_carry_the_alert_budget(self) -> None:
        self.assertIn("proactive_alerts_daily_budget", self.app_source)
        # The persisted settings body must include the budget or every unrelated
        # settings save would silently reset it server-side... it wouldn't (the backend
        # merges), but the UI stepper would still show a stale value after reload.
        self.assertIn('"proactive_alerts_daily_budget": appSettings.proactive_alerts_daily_budget', self.app_source)


class PhaseUIModelParityTests(unittest.TestCase):
    """Swift Codable field names must match the backend JSON exactly."""

    def setUp(self) -> None:
        self.models = read(MODELS)

    def test_alert_item_fields_match_alert_from_row(self) -> None:
        for field in ("id", "kind", "status", "title", "detail", "created_at", "delivered_at", "resolved_at"):
            with self.subTest(field=field):
                self.assertRegex(self.models, rf"let {field}\b")

    def test_twin_prediction_fields_match_would_i(self) -> None:
        for field in (
            "prediction_id",
            "question",
            "verdict",
            "rationale",
            "supporting",
            "opposing",
            "hard_constraints",
            "evidence_count",
            "generated_at",
        ):
            with self.subTest(field=field):
                self.assertRegex(self.models, rf"let {field}\b")

    def test_twin_evidence_fields_match_twin_evidence_item(self) -> None:
        for field in ("memory_id", "layer", "content", "author_class", "trust_score", "occurred_at", "source"):
            with self.subTest(field=field):
                self.assertRegex(self.models, rf"let {field}\b")

    def test_scorecard_and_metrics_fields_match_read_models(self) -> None:
        for field in (
            "window_days",
            "predictions",
            "verdict_mix",
            "graded",
            "accuracy",
            "ungraded",
            "caveats",
            "raised",
            "suppressed_by_budget",
            "precision",
            "trials",
            "hits",
            "hit_rate",
            "memory_usage_rate",
            "coverage_mix",
            "conflict_packs_served",
            "faithfulness_rate",
            "active_days",
        ):
            with self.subTest(field=field):
                self.assertRegex(self.models, rf"let {field}\b")

    def test_twin_question_detection_is_prefix_gated(self) -> None:
        # The twin consult must not fire on ordinary lookups; only explicit
        # self-referential questions engage it.
        self.assertIn('"would i"', self.models)
        self.assertIn('"should i"', self.models)
        self.assertIn("hasPrefix", self.models)


class PhaseUISurfaceWiringTests(unittest.TestCase):
    """Each tab renders its phase section, honestly gated."""

    def test_review_surfaces_alerts_and_grading(self) -> None:
        review = read(REVIEW_TAB)
        self.assertIn("ReviewProactiveAlertsSection(state: state)", review)
        self.assertIn("ReviewTwinGradingSection(state: state)", review)
        self.assertIn("loadProactiveAlerts()", review)
        self.assertIn("loadTwinScorecard()", review)

    def test_ask_surfaces_the_twin_card_only_for_twin_questions(self) -> None:
        ask = read(ASK_TAB)
        self.assertIn("AskTwinPredictionCard(prediction: twinPrediction)", ask)
        app_source = read(CORTEX_APP)
        self.assertIn("TwinQuestionDetector.isTwinQuestion(q)", app_source)

    def test_model_tab_shows_scorecard_only_after_predictions_exist(self) -> None:
        model = read(MODEL_TAB)
        self.assertIn("scorecard.predictions > 0", model)
        # U-TWIN1: the scorecard card now takes `state` so it can host the "Ask would I…" and
        # "Grade N predictions" actions; the predictions>0 honesty gate above is unchanged.
        self.assertIn("TwinScorecardCard(scorecard: scorecard, state: state)", model)

    def test_connections_sheet_has_the_activity_panel_and_budget(self) -> None:
        sheet = read(CONNECTIONS_SHEET)
        self.assertIn("ConnectionsToolUsageSection(state: state)", sheet)
        views = read(VIEWS)
        self.assertIn("Stepper", views)
        self.assertIn("proactive_alerts_daily_budget", views)
        # Budget is clamped in the UI to the backend's 0-20 range.
        self.assertIn("0...20", views)

    def test_alert_card_never_hides_the_dismiss_action(self) -> None:
        # "Always dismissible" is a product-survival constraint (roadmap 6.3).
        views = read(VIEWS)
        card = views[views.index("struct ReviewProactiveAlertCard") :]
        card = card[: card.index("struct ReviewTwinGradingSection")]
        self.assertIn('Label("Dismiss"', card)
        self.assertIn('Label("Noted"', card)

    def test_metrics_render_honest_placeholders_not_fabricated_zeros(self) -> None:
        # Nil rates render as a dash placeholder, never as a made-up "0%". (An en-dash "no data"
        # glyph, since prose em dashes were removed from the UI copy.)
        views = read(VIEWS)
        self.assertIn('return "\u2013"', views)
        self.assertIn("No alerts resolved yet", views)
        self.assertIn("No prefetch trials yet", views)

    def test_insufficient_evidence_language_stays_honest(self) -> None:
        # The twin's contract: say so instead of inventing.
        views = read(VIEWS)
        self.assertIn("won't invent a preference", views)
        self.assertIn('"insufficient_evidence"', views)


class PhaseUIThemeGuardrailTests(unittest.TestCase):
    """CortexDesign contract: three voices, gold never text, stamps uppercase+kerned."""

    def setUp(self) -> None:
        self.views = read(VIEWS)

    def test_gold_is_used_as_spine_fill_never_text(self) -> None:
        self.assertIn("archiveSpine(CortexDesign.gold)", self.views)
        self.assertNotIn("foregroundColor(CortexDesign.gold)", self.views)

    def test_stamps_use_the_mono_voice_with_kerning(self) -> None:
        stamp_uses = self.views.count("Typography.stamp")
        self.assertGreaterEqual(stamp_uses, 4)
        self.assertGreaterEqual(self.views.count("kerning(0.8)"), 4)

    def test_no_heavy_font_weights(self) -> None:
        # Max weight app-wide is .semibold.
        self.assertNotIn(".bold)", self.views)
        self.assertNotIn(".heavy", self.views)
        self.assertNotIn(".black", self.views)

    def test_prose_and_stats_use_the_serif_voice(self) -> None:
        self.assertIn("Typography.prose", self.views)
        self.assertIn("Typography.stat", self.views)
        self.assertIn("monospacedDigit()", self.views)

    def test_cards_use_the_index_card_recipe(self) -> None:
        self.assertIn("cortexCard(", self.views)
        self.assertIn("AccessionStamp(segments:", self.views)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
