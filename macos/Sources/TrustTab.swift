import SwiftUI

struct TrustTab: View {
    @ObservedObject var state: AppState
    @State private var integrationsExpanded = false
    @State private var sourcesExpanded = false
    @State private var advancedExpanded = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let summary = state.trustSummary {
                    TrustScoreSection(summary: summary)
                    if let lifecycle = state.dataLifecycleReport {
                        TrustLifecycleSection(report: lifecycle)
                    }
                    TrustPolicySection(state: state)
                    SettingsPrivacySection(state: state)
                    SettingsDataRecoverySection(state: state)
                    TrustActionsSection(state: state)
                    DisclosureGroup("Connected AI tools", isExpanded: $integrationsExpanded) {
                        VStack(alignment: .leading, spacing: 14) {
                            IntegrationCenterView(state: state, compact: true)
                            IntegrationTokensSection(state: state)
                        }
                        .padding(.top, 8)
                    }
                    DisclosureGroup("Sources and audit trail", isExpanded: $sourcesExpanded) {
                        VStack(alignment: .leading, spacing: 14) {
                            TrustSourceSection(state: state, summary: summary)
                            TrustAuditSection(events: state.auditEvents, refresh: {
                                Task { await state.loadTrust() }
                            })
                        }
                        .padding(.top, 8)
                    }
                    DisclosureGroup("Advanced diagnostics", isExpanded: $advancedExpanded) {
                        VStack(alignment: .leading, spacing: 14) {
                            Group {
                                SettingsOnboardingSection(state: state)
                                Divider()
                                SettingsReliabilitySection(state: state)
                                Divider()
                            }
                            Group {
                                AdvancedGraphSection(state: state)
                                SettingsStatsSection(state: state)
                                Divider()
                                TrustSyncManifestSection(state: state)
                                Divider()
                                SettingsUpdatesSection(state: state)
                                Divider()
                            }
                            Group {
                                SettingsHealthSection(state: state)
                                SettingsBackendSection(state: state)
                            }
                        }
                        .padding(.top, 8)
                    }
                } else {
                    VStack(spacing: 12) {
                        ProgressView()
                        Text("Preparing trust controls")
                            .font(.headline)
                        Text("Cortex is reading local policy, source history, and recent audit events.")
                            .foregroundColor(.secondary)
                    }
                    .frame(maxWidth: .infinity, minHeight: 420)
                }
            }
            .padding(16)
        }
        .task {
            await state.loadTrust()
        }
        .onChange(of: advancedExpanded) { expanded in
            if expanded {
                Task { await loadAdvancedDiagnostics() }
            }
        }
    }

    private func loadAdvancedDiagnostics() async {
        await state.loadDiagnostics()
        await state.loadReliability()
        await state.loadStats()
        await state.loadGraph()
    }
}
