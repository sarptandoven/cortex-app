import Foundation

// Codable models for the phases 5-6 surfaces: proactive alerts (contradiction interrupts),
// the twin (would-I predictions + scorecard), and the phase 4/6 usage metrics (tool scorecard,
// alert precision, prefetch hit rate). Field names mirror the backend JSON exactly;
// backend/tests/test_macos_phase_ui_contract.py enforces the parity.

// MARK: - Proactive alerts (Phase 6.1/6.3)

struct ProactiveAlertsResponse: Codable {
    let alerts: [ProactiveAlertItem]
}

struct ProactiveAlertItem: Codable, Identifiable, Equatable, Hashable {
    let id: String
    let kind: String
    let status: String
    let title: String
    let detail: ProactiveAlertDetail?
    let created_at: String
    let delivered_at: String?
    let resolved_at: String?
}

/// Kind-specific payload. For `contradiction` alerts the backend fills all three fields;
/// other kinds may carry none of them, so everything stays optional.
struct ProactiveAlertDetail: Codable, Equatable, Hashable {
    let field: String?
    let new: ProactiveAlertClaim?
    let existing: ProactiveAlertClaim?
}

struct ProactiveAlertClaim: Codable, Equatable, Hashable {
    let memory_id: String?
    let claim: String?
    let trust_score: Double?
    let author_class: String?
}

// MARK: - Twin predictions (Phase 5)

struct TwinPredictionResponse: Codable, Equatable {
    let prediction_id: String
    let question: String
    let verdict: String
    let rationale: String
    let supporting: [TwinEvidenceItem]
    let opposing: [TwinEvidenceItem]
    let hard_constraints: [TwinEvidenceItem]
    let evidence_count: Int
    let generated_at: String

    var verdictLabel: String {
        switch verdict {
        case "likely_yes": return "Likely yes"
        case "likely_no": return "Likely no"
        case "mixed": return "Mixed evidence"
        default: return "Not enough evidence"
        }
    }

    var hasEvidence: Bool {
        !supporting.isEmpty || !opposing.isEmpty
    }
}

struct TwinEvidenceItem: Codable, Identifiable, Equatable, Hashable {
    let memory_id: String
    let layer: String
    let content: String
    let author_class: String
    let trust_score: Double
    let occurred_at: String?
    let source: String?

    var id: String { memory_id }
}

struct TwinScorecardResponse: Codable, Equatable {
    let generated_at: String
    let window_days: Int
    let predictions: Int
    let verdict_mix: [String: Int]
    let graded: Int
    let accuracy: Double?
    let ungraded: [TwinUngradedPrediction]
    let caveats: [String]
}

struct TwinUngradedPrediction: Codable, Identifiable, Equatable, Hashable {
    let prediction_id: String
    let question: String?
    let verdict: String?
    let predicted_at: String?
    let outcome: String?

    var id: String { prediction_id }

    var verdictLabel: String {
        switch verdict {
        case "likely_yes": return "Likely yes"
        case "likely_no": return "Likely no"
        case "mixed": return "Mixed evidence"
        default: return "Predicted"
        }
    }
}

// MARK: - Metrics read-models (Phases 4 and 6)

struct AlertPrecisionResponse: Codable, Equatable {
    let generated_at: String
    let window_days: Int
    let raised: Int
    let pending: Int
    let delivered: Int
    let accepted: Int
    let dismissed: Int
    let suppressed_by_budget: Int
    let precision: Double?

    var resolved: Int { accepted + dismissed }
}

struct PrefetchHitRateResponse: Codable, Equatable {
    let generated_at: String
    let window_days: Int
    let trials: Int
    let hits: Int
    let hit_rate: Double?
}

struct ToolScorecardResponse: Codable, Equatable {
    let generated_at: String
    let window_days: Int
    let hosts: [ToolScorecardHost]
    let caveats: [String]
}

struct ToolScorecardHost: Codable, Identifiable, Equatable, Hashable {
    let token_id: String?
    let token_label: String?
    let calls: Int
    let failed_calls: Int
    let read_calls: Int
    let write_calls: Int
    let coverage_mix: [String: Int]
    let conflict_packs_served: Int
    let grading: ToolScorecardGrading
    let active_days: Int
    let memory_usage_rate: Double

    var id: String { token_id ?? "untokened" }

    var displayName: String {
        let label = token_label?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !label.isEmpty { return label }
        return token_id == nil ? "Local tools" : "Connected tool"
    }
}

struct ToolScorecardGrading: Codable, Equatable, Hashable {
    let submissions: Int
    let consistent: Int
    let contradicted: Int
    let unsupported: Int
    let faithfulness_rate: Double?
}

// MARK: - Twin question detection

/// Ask stays a single box: when the question is about what the user themself would do,
/// the twin's cited prediction appears alongside the normal answer. Prefix-matched so the
/// twin never fires on ordinary lookups.
enum TwinQuestionDetector {
    static let prefixes = ["would i", "should i", "do i", "am i", "will i", "how would i"]

    static func isTwinQuestion(_ query: String) -> Bool {
        let lowered = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return prefixes.contains { lowered == $0 || lowered.hasPrefix($0 + " ") }
    }
}
