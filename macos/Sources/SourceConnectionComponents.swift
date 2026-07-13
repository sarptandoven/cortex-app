import SwiftUI

/// The primary source ledger card — the notes folder's home in Connections & Privacy. It reads as a
/// catalog card, not a System-Settings row: the state is a mono "stamp" in the accession vocabulary
/// (moss = kept/synced, gold = needs attention, wax = the one call to action), a wax-red margin
/// spine marks a connected source, and the one main action is the single wax `CortexButton.primary`.
///
/// Only ever instantiated with the obsidian (local notes) connector, so the card speaks the notes
/// language directly — the old generic "Direct source" else-branch (which merely re-opened this same
/// sheet) has been removed along with its status branches.
struct SourceConnectorStatusCard: View {
    @ObservedObject var state: AppState
    let connector: SourceConnectorCatalogItem
    let connected: Bool
    var needsContent: Bool = false
    var needsAttention: Bool = false
    var attentionDetail: String? = nil
    @State private var confirmDisconnect = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 12) {
                // The catalog glyph, pressed into a soft wax-tinted well rather than a raw system icon.
                ZStack {
                    RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                        .fill(stampColor.opacity(0.12))
                    Image(systemName: statusIcon)
                        .font(.system(size: 24, weight: .semibold))
                        .foregroundColor(stampColor)
                }
                .frame(width: 46, height: 46)

                VStack(alignment: .leading, spacing: 4) {
                    Text("Notes folder")
                        .font(CortexDesign.Typography.title)
                        .foregroundColor(CortexDesign.ink)
                    // State as words and ink — a catalog accession stamp, never a colored capsule.
                    AccessionStamp(
                        segments: ["NOTES", statusStamp],
                        emphasisIndex: stampEmphasized ? 1 : nil
                    )
                }
                Spacer(minLength: 0)
            }

            Text(statusDetail)
                .font(CortexDesign.Typography.body)
                .foregroundColor(CortexDesign.inkSecondary)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 8) {
                // The one wax-red primary on this surface: connect / sync / reconnect / fix notes.
                CortexButton(
                    title: primaryButtonTitle,
                    systemImage: primaryButtonIcon,
                    role: .primary,
                    size: .large
                ) {
                    state.connectLocalNotesFolder(connector, chooseNew: needsContent)
                }
                .disabled(state.isBusy)

                if state.hasConnectedObsidianVault {
                    CortexButton(title: "Change folder", systemImage: "folder", role: .secondary, size: .large) {
                        state.connectLocalNotesFolder(connector, chooseNew: true)
                    }
                    .disabled(state.isBusy)

                    CortexButton(title: "Disconnect", systemImage: "xmark.circle", role: .destructive, size: .large) {
                        confirmDisconnect = true
                    }
                    .disabled(state.isBusy)
                    .help("Stop syncing this notes folder. Already synced memory is kept.")
                    .confirmationDialog(
                        "Disconnect notes folder?",
                        isPresented: $confirmDisconnect,
                        titleVisibility: .visible
                    ) {
                        Button("Disconnect notes", role: .destructive) {
                            state.disconnectObsidianNotes()
                        }
                        Button("Cancel", role: .cancel) {}
                    } message: {
                        Text("\(DistributionMode.appDisplayName) stops syncing this folder. Memory already synced and reviewed is kept and stays available to Ask. You can reconnect a folder later.")
                    }
                }

                Spacer(minLength: 0)
            }
        }
        .cortexCard()
        // Kept and recorded: a connected notes source carries the wax-red margin spine; a source that
        // needs attention shifts the spine to gold; an unconnected one leaves the margin bare.
        .archiveSpine(spineColor)
    }

    // MARK: - Stamp vocabulary (moss / gold / wax — words and ink, no capsules)

    /// True when the source reads as connected in the ledger but has no local notes-folder bookmark on
    /// this Mac (state.hasConnectedObsidianVault == false). The detail text and primary button already
    /// tell the user to "Reconnect notes" in this case, so the stamp/spine must agree instead of
    /// claiming "Synced" against a missing local vault.
    private var needsLocalReconnect: Bool {
        connected && !state.hasConnectedObsidianVault
    }

    /// The mono accession word for the current state.
    private var statusStamp: String {
        if needsAttention { return "Needs attention" }
        if needsContent { return "No notes found" }
        if needsLocalReconnect { return "Reconnect" }
        return connected ? "Synced" : "Not connected"
    }

    /// Whether the state stamp is emphasized (tinted wax) — reserved for the states that want a look.
    private var stampEmphasized: Bool {
        needsAttention || needsContent || needsLocalReconnect || !connected
    }

    /// The semantic color for the glyph well and the state — moss when synced (LOCAL, healthy),
    /// gold when it needs attention, wax for the call-to-connect.
    private var stampColor: Color {
        if needsAttention || needsContent || needsLocalReconnect { return CortexDesign.gold }
        return connected ? CortexDesign.sealMoss : CortexDesign.accent
    }

    /// The margin spine: wax when connected/kept, gold when attention is owed, bare otherwise.
    private var spineColor: Color {
        if needsAttention || needsContent || needsLocalReconnect { return CortexDesign.gold }
        if connected { return CortexDesign.accent }
        return Color.clear
    }

    private var statusIcon: String {
        if needsAttention { return "exclamationmark.triangle.fill" }
        if needsContent { return "folder.badge.questionmark" }
        return connected ? "checkmark.seal.fill" : "link.badge.plus"
    }

    private var statusDetail: String {
        if needsAttention {
            return attentionDetail ?? "\(DistributionMode.appDisplayName) lost permission to read this folder, usually after it moved or macOS revoked access. Choose the folder again to resume syncing."
        }
        if needsContent {
            return "\(DistributionMode.appDisplayName) could not find usable notes there. Choose a notes library with real content."
        }
        if connected {
            if !state.hasConnectedObsidianVault {
                return "Reconnect the notes folder on this Mac so \(DistributionMode.appDisplayName) can keep syncing."
            }
            return "\(DistributionMode.appDisplayName) keeps these notes synced. Review approves memory before Ask uses it."
        }
        return "Choose a notes folder once. \(DistributionMode.appDisplayName) syncs locally and keeps citations attached."
    }

    private var primaryButtonTitle: String {
        if needsAttention { return "Fix notes" }
        if needsContent { return "Choose notes" }
        if connected {
            return state.hasConnectedObsidianVault ? "Sync notes" : "Reconnect notes"
        }
        return "Connect notes"
    }

    private var primaryButtonIcon: String {
        if needsAttention { return "exclamationmark.triangle.fill" }
        if needsContent { return "folder.badge.questionmark" }
        if connected {
            return state.hasConnectedObsidianVault ? "arrow.triangle.2.circlepath" : "folder.badge.plus"
        }
        return "folder.badge.plus"
    }
}
