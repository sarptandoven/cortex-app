import SwiftUI

struct TrustTab: View {
    @ObservedObject var state: AppState
    @State private var integrationsExpanded = false
    @State private var sourcesExpanded = false
    @State private var advancedExpanded = false
    @State private var developerDetailsExpanded = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let summary = state.trustSummary {
                    TrustScoreSection(summary: summary)
                    TrustPolicySection(state: state)
                    SettingsPrivacySection(state: state)
                    TrustBackupSummarySection(state: state)
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
                    DisclosureGroup("Advanced settings and diagnostics", isExpanded: $advancedExpanded) {
                        VStack(alignment: .leading, spacing: 14) {
                            SettingsDataRecoverySection(state: state)
                            Divider()
                            SettingsReliabilitySection(state: state)
                            Divider()
                            SettingsHealthSection(state: state)
                            DisclosureGroup("Developer details", isExpanded: $developerDetailsExpanded) {
                                VStack(alignment: .leading, spacing: 14) {
                                    if let lifecycle = state.dataLifecycleReport {
                                        TrustLifecycleSection(report: lifecycle)
                                        Divider()
                                    }
                                    Group {
                                        SettingsOnboardingSection(state: state)
                                        Divider()
                                        AdvancedGraphSection(state: state)
                                        SettingsStatsSection(state: state)
                                        Divider()
                                    }
                                    Group {
                                        TrustSyncManifestSection(state: state)
                                        Divider()
                                        SettingsUpdatesSection(state: state)
                                        Divider()
                                        SettingsBackendSection(state: state)
                                    }
                                }
                                .padding(.top, 8)
                            }
                            .onChange(of: developerDetailsExpanded) { expanded in
                                if expanded {
                                    Task { await loadDeveloperDiagnostics() }
                                }
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
    }

    private func loadDeveloperDiagnostics() async {
        await state.loadStats()
        await state.loadGraph()
    }
}

struct TrustBackupSummarySection: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Vault backup")
                        .font(.headline)
                    Text("Create a local backup before connecting more tools or importing large source exports.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                Button {
                    state.createBackup()
                } label: {
                    Label("Back Up Now", systemImage: "archivebox")
                }
                .buttonStyle(.borderedProminent)
            }

            if let backup = state.lastBackupPath {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "checkmark.seal.fill")
                        .foregroundColor(.green)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Latest backup ready")
                            .font(.caption)
                            .fontWeight(.semibold)
                        Text(backup)
                            .font(.caption2)
                            .foregroundColor(.secondary)
                            .lineLimit(2)
                            .truncationMode(.middle)
                            .textSelection(.enabled)
                    }
                    Spacer(minLength: 0)
                }
            } else {
                TrustNotice(systemImage: "externaldrive", title: "No backup recorded", detail: "Backups stay local and can be managed from Advanced when needed.", color: .orange)
            }
        }
        .padding(12)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}
