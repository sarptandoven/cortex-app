import SwiftUI

struct TrustSyncManifestSection: View {
    @ObservedObject var state: AppState

    private var activeDevices: [SyncDeviceItem] {
        state.syncDevices.filter { !$0.isRevoked }
    }

    private var receiptCount: Int {
        state.syncReceiptsByDevice.values.reduce(0) { $0 + $1.count }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Sync manifests")
                        .font(.headline)
                    Text("Local device manifests and sync receipts for future hosted sync.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                Button {
                    Task { await state.loadSourceConnectivity() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 10)], spacing: 10) {
                LifecycleFact(label: "Active Devices", value: "\(activeDevices.count)", systemImage: "macbook.and.iphone")
                LifecycleFact(label: "Receipts", value: "\(receiptCount)", systemImage: "checkmark.seal")
                LifecycleFact(label: "Registry", value: state.syncDevices.isEmpty ? "Empty" : "Ready", systemImage: "list.bullet.rectangle")
                LifecycleFact(label: "Scope", value: "Local", systemImage: "lock")
            }

            if state.syncDevices.isEmpty {
                TrustNotice(
                    systemImage: "icloud.slash",
                    title: "No sync devices registered",
                    detail: "Cortex is still local-first. The manifest registry is ready for future multi-device materialization, but no remote sync device is active.",
                    color: .secondary
                )
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(state.syncDevices.prefix(5)) { device in
                        SyncManifestDeviceRow(device: device, receipts: state.syncReceiptsByDevice[device.id] ?? [])
                    }
                }
            }
        }
    }
}

struct SyncManifestDeviceRow: View {
    let device: SyncDeviceItem
    let receipts: [SyncReceiptItem]

    private var latestReceipt: SyncReceiptItem? {
        receipts.first
    }

    private var statusColor: Color {
        if device.isRevoked { return .secondary }
        if latestReceipt?.status == "failed" { return .orange }
        return .green
    }

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            Image(systemName: device.isRevoked ? "xmark.seal" : "checkmark.seal.fill")
                .foregroundColor(statusColor)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    Text(device.device_name)
                        .font(.callout)
                        .fontWeight(.semibold)
                    Text(device.platform)
                        .font(.caption)
                        .foregroundColor(.secondary)
                    if device.isRevoked {
                        Text("Revoked")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }
                }
                Text(detail)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 4)
    }

    private var detail: String {
        var pieces: [String] = ["fingerprint \(device.fingerprint)"]
        if let cursor = device.last_cursor, !cursor.isEmpty {
            pieces.append("cursor \(cursor)")
        }
        if let receipt = latestReceipt {
            pieces.append("last receipt \(receipt.status)")
            if let error = receipt.error, !error.isEmpty {
                pieces.append(CortexRecoveryText.inlineError(error, fallback: "Refresh sync status, then try again."))
            }
        } else {
            pieces.append("no receipts")
        }
        return pieces.joined(separator: " · ")
    }
}
