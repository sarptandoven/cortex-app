import Foundation

struct SyncDeviceListResponse: Codable {
    let results: [SyncDeviceItem]
}

struct SyncDeviceItem: Codable, Identifiable, Hashable {
    let id: String
    let user_id: String
    let device_name: String
    let platform: String
    let fingerprint: String
    let capabilities: [String]
    let first_cursor: String?
    let last_cursor: String?
    let last_seen_at: String?
    let created_at: String
    let updated_at: String
    let revoked_at: String?

    var isRevoked: Bool { revoked_at != nil }
}

struct SyncReceiptListResponse: Codable {
    let results: [SyncReceiptItem]
}

struct SyncReceiptItem: Codable, Identifiable, Hashable {
    let id: String
    let user_id: String
    let device_id: String
    let cursor: String
    let status: String
    let manifest_hash: String?
    let remote_ref: String?
    let error: String?
    let created_at: String
    let updated_at: String
}
