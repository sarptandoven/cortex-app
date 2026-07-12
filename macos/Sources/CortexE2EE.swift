import CryptoKit
import Foundation

// MARK: - Cortex zero-access end-to-end encryption (client-side)
//
// SECURITY-CRITICAL. This file is the ENTIRE trusted computing base for Cortex's zero-access mode:
// the 256-bit sync key is generated on-device, stored ONLY in this Mac's Keychain (via the existing
// CortexCredentialStore, same data-protection keychain the account tokens use), and NEVER leaves the
// device on any wire. When zero-access is ON, CortexPushSync encrypts each capture with this key
// before it touches the network, and CortexPullSync decrypts pulled ciphertext locally; the hosted
// account is a blind relay that only ever sees opaque CXEC1 blobs.
//
// Threat model / invariants (an adversarial crypto review will check these):
//   * The key material is created with the system CSPRNG (SymmetricKey(size:) / AES.GCM.Nonce()),
//     is 256 bits, and is only ever persisted as base64 in the Keychain. No endpoint, log, or
//     UserDefaults value ever receives it.
//   * Every blob is bound to its capture id via AES-GCM's AAD (= UTF8(client_capture_id)), so a
//     server that shuffles/relabels ciphertext across captures cannot make one decrypt under
//     another's id — the tag check fails.
//   * A decrypt failure (bad tag, wrong key, truncated/oversized/malformed blob, id mismatch) is a
//     hard error. It NEVER yields plaintext and NEVER falls back to treating ciphertext as text.
//   * The recovery code is the ONLY off-device representation of the key and exists solely so the
//     user can restore on a second device; the round-trip (key -> code -> key) is exact.

enum CortexE2EEError: LocalizedError, Equatable {
    case noKey
    case malformedBlob(String)
    case decryptFailed
    case captureIDMismatch
    case invalidRecoveryCode(String)
    case encodingFailed

    var errorDescription: String? {
        switch self {
        case .noKey:
            return "No zero-access encryption key exists on this Mac. Enable zero-access to create one, or restore it from your recovery code."
        case .malformedBlob(let why):
            return "An encrypted memory item was malformed and could not be read (\(why)). It was skipped."
        case .decryptFailed:
            return "An encrypted memory item could not be decrypted with this Mac's key. Restore the correct recovery code, then it will sync in."
        case .captureIDMismatch:
            return "An encrypted memory item failed its integrity check (capture-id binding). It was skipped as untrusted."
        case .invalidRecoveryCode(let why):
            return "That recovery code is not valid (\(why))."
        case .encodingFailed:
            return "Could not encode the memory item for encryption."
        }
    }
}

enum CortexE2EE {

    // MARK: Constants

    /// CXEC1 = "Cortex client-encrypted v1". Five ASCII bytes prepended to every blob so the
    /// decryptor (and the offline tool) can positively identify + version the envelope.
    static let magic: [UInt8] = Array("CXEC1".utf8)   // [0x43,0x58,0x45,0x43,0x31]
    static let magicCount = 5
    static let nonceCount = 12                          // AES-GCM standard 96-bit nonce
    static let tagCount = 16                            // AES-GCM 128-bit auth tag
    static let keyByteCount = 32                        // 256-bit key

    /// enc_meta hint values (non-secret; never any key material).
    static let algName = "AES-256-GCM"
    static let keyID = "e2ee:v1"

    /// Keychain account for the raw key (base64 of 32 bytes). Distinct from the account tokens so a
    /// sign-out that clears the refresh token never touches the encryption key (losing it = data loss).
    static let keychainKeyID = "cortexE2EESyncKey.v1"
    /// UserDefaults flag for the opt-in. Default false — plaintext sync is unchanged until the user
    /// deliberately enables zero-access AND a key exists.
    static let enabledDefaultsKey = "cortexZeroAccess.v1"

    // MARK: Key management (Keychain-backed, on-device only)

    /// True iff a valid 32-byte key is present in the Keychain.
    static var hasKey: Bool { currentKey() != nil }

    /// Load the on-device sync key, or nil if none exists / it is corrupt.
    static func currentKey() -> SymmetricKey? {
        guard let stored = CortexCredentialStore.loadSecret(forKey: keychainKeyID),
              let data = Data(base64Encoded: stored),
              data.count == keyByteCount else {
            return nil
        }
        return SymmetricKey(data: data)
    }

    /// Create a fresh 256-bit key from the system CSPRNG and persist it, unless one already exists.
    /// Idempotent: never overwrites an existing key (that would strand every capture already sealed
    /// under the old one). Returns the effective key.
    @discardableResult
    static func generateKeyIfNeeded() -> SymmetricKey {
        if let existing = currentKey() { return existing }
        let key = SymmetricKey(size: .bits256)
        persist(key)
        return key
    }

    /// Persist a key as base64(rawBytes) in the Keychain. Private: callers use generateKeyIfNeeded()
    /// or importRecoveryCode(_:) so a key is never written from an untrusted/short buffer.
    private static func persist(_ key: SymmetricKey) {
        let raw = key.withUnsafeBytes { Data($0) }
        CortexCredentialStore.saveSecret(raw.base64EncodedString(), forKey: keychainKeyID)
    }

    /// Remove the key from the Keychain. Only used by an explicit "forget key on this device" path;
    /// not wired to sign-out (sign-out must NOT destroy the key — see resetToLocalDefaults).
    static func removeKey() {
        CortexCredentialStore.removeSecret(forKey: keychainKeyID)
    }

    // MARK: Zero-access opt-in state

    /// The user-facing toggle. Enabling REQUIRES a key to already exist (the UI generates one first),
    /// so a "true" here always implies encrypt-on-push is actually possible. Reading is cheap +
    /// side-effect free; the guard in setEnabled(_:) is where the invariant is enforced.
    static var isEnabled: Bool {
        get { UserDefaults.standard.bool(forKey: enabledDefaultsKey) }
        set { UserDefaults.standard.set(newValue, forKey: enabledDefaultsKey) }
    }

    /// Turn zero-access on/off with the enforced invariant that ON requires a present key. Returns
    /// whether the resulting state is `on` (so a caller that asked to enable with no key sees false
    /// and can surface an error instead of a silent no-op that would send plaintext).
    @discardableResult
    static func setEnabled(_ on: Bool) -> Bool {
        if on {
            guard hasKey else { isEnabled = false; return false }
            isEnabled = true
            return true
        }
        isEnabled = false
        return false
    }

    // MARK: CXEC1 encrypt

    /// Encrypt the capture fields into a CXEC1 blob bound to `captureID`.
    ///
    /// plaintext  = UTF8(JSON of {content,title,source_url,source,captured_at,review_status})
    /// nonce      = 12 random bytes (AES.GCM.Nonce())
    /// sealed     = AES.GCM.seal(plaintext, key, nonce, authenticating: UTF8(captureID))
    /// blob       = magic(5) || nonce(12) || ciphertext || tag(16)
    /// returns    (base64(blob), enc_meta)
    static func encryptPayload(_ fields: [String: Any], captureID: String) throws -> (payloadB64: String, encMeta: [String: Any]) {
        guard let key = currentKey() else { throw CortexE2EEError.noKey }

        // Canonical plaintext JSON. sortedKeys makes the bytes deterministic given the same input
        // (helpful for the offline tool + tests); GCM does not require it, but it costs nothing.
        let plainFields = canonicalPlaintextFields(fields)
        guard JSONSerialization.isValidJSONObject(plainFields),
              let plaintext = try? JSONSerialization.data(withJSONObject: plainFields, options: [.sortedKeys]) else {
            throw CortexE2EEError.encodingFailed
        }
        guard let aad = captureID.data(using: .utf8) else { throw CortexE2EEError.encodingFailed }

        let nonce = AES.GCM.Nonce()   // 12 bytes from the system CSPRNG
        let sealed: AES.GCM.SealedBox
        do {
            sealed = try AES.GCM.seal(plaintext, using: key, nonce: nonce, authenticating: aad)
        } catch {
            throw CortexE2EEError.encodingFailed
        }

        let nonceBytes = Data(nonce)                  // exactly 12
        let ciphertext = sealed.ciphertext            // == plaintext length
        let tag = sealed.tag                          // exactly 16
        // Defensive: CryptoKit guarantees these sizes, but a mismatch would corrupt the wire format.
        guard nonceBytes.count == nonceCount, tag.count == tagCount else {
            throw CortexE2EEError.encodingFailed
        }

        var blob = Data()
        blob.reserveCapacity(magicCount + nonceCount + ciphertext.count + tagCount)
        blob.append(contentsOf: magic)
        blob.append(nonceBytes)
        blob.append(ciphertext)
        blob.append(tag)

        let encMeta: [String: Any] = [
            "alg": algName,
            "nonce_b64": nonceBytes.base64EncodedString(),   // redundant-but-explicit; equals the in-blob nonce
            "key_id": keyID,
            "aad_context": captureID,
        ]
        return (blob.base64EncodedString(), encMeta)
    }

    /// The exact key set + ordering the CXEC1 plaintext carries. null-able title/source_url map to
    /// NSNull so the JSON keeps the field (a pulling device can tell "absent" from "was null").
    private static func canonicalPlaintextFields(_ fields: [String: Any]) -> [String: Any] {
        func str(_ key: String) -> String { (fields[key] as? String) ?? "" }
        func optStr(_ key: String) -> Any {
            if let v = fields[key] as? String { return v }
            return NSNull()
        }
        return [
            "content": str("content"),
            "title": optStr("title"),
            "source_url": optStr("source_url"),
            "source": str("source"),
            "captured_at": str("captured_at"),
            "review_status": optStr("review_status"),
        ]
    }

    // MARK: CXEC1 decrypt

    /// Decrypt a CXEC1 blob back to its capture fields, verifying the capture-id binding.
    ///
    /// On ANY failure this throws — it NEVER returns partial/garbage fields and NEVER treats the
    /// ciphertext as plaintext. The caller (pull) must skip/surface the item on a throw, not apply it.
    static func decryptToFields(payloadB64: String, captureID: String) throws -> [String: Any] {
        guard let key = currentKey() else { throw CortexE2EEError.noKey }
        guard let blob = Data(base64Encoded: payloadB64) else {
            throw CortexE2EEError.malformedBlob("not valid base64")
        }
        // magic(5) + nonce(12) + tag(16) is the floor; ciphertext may be empty (0-length plaintext).
        let minLen = magicCount + nonceCount + tagCount
        guard blob.count >= minLen else {
            throw CortexE2EEError.malformedBlob("shorter than the CXEC1 header")
        }
        // Index into the Data by explicit offsets (Data slices keep their parent's indices, so
        // rebase every slice to 0 before use to avoid off-by-startIndex bugs).
        let bytes = [UInt8](blob)
        guard Array(bytes[0..<magicCount]) == magic else {
            throw CortexE2EEError.malformedBlob("bad CXEC1 magic")
        }
        let nonceStart = magicCount
        let nonceEnd = nonceStart + nonceCount
        let tagStart = bytes.count - tagCount
        // ciphertext occupies [nonceEnd, tagStart); guard the ordering so a hand-crafted short blob
        // can't produce a negative-length or overlapping slice.
        guard tagStart >= nonceEnd else {
            throw CortexE2EEError.malformedBlob("no room for nonce + tag")
        }
        let nonceBytes = Array(bytes[nonceStart..<nonceEnd])
        let ciphertext = Array(bytes[nonceEnd..<tagStart])
        let tag = Array(bytes[tagStart..<bytes.count])

        guard let aad = captureID.data(using: .utf8) else {
            throw CortexE2EEError.malformedBlob("capture id not UTF-8")
        }
        let nonce: AES.GCM.Nonce
        do {
            nonce = try AES.GCM.Nonce(data: Data(nonceBytes))
        } catch {
            throw CortexE2EEError.malformedBlob("bad nonce length")
        }
        let box: AES.GCM.SealedBox
        do {
            box = try AES.GCM.SealedBox(nonce: nonce, ciphertext: Data(ciphertext), tag: Data(tag))
        } catch {
            throw CortexE2EEError.malformedBlob("could not assemble sealed box")
        }
        let plaintext: Data
        do {
            // AAD = captureID: if the server relabeled this blob under a different id, the tag fails.
            plaintext = try AES.GCM.open(box, using: key, authenticating: aad)
        } catch {
            // Wrong key OR tampered ciphertext OR mismatched AAD — all indistinguishable + all hard.
            throw CortexE2EEError.decryptFailed
        }
        guard let obj = try? JSONSerialization.jsonObject(with: plaintext),
              let dict = obj as? [String: Any] else {
            throw CortexE2EEError.malformedBlob("decrypted payload is not a JSON object")
        }
        // Normalize NSNull back to Swift-absent so callers see String? cleanly.
        return normalizedFields(dict)
    }

    /// Turn the decrypted JSON dict into the field shape the local ingest expects: NSNull -> dropped,
    /// so `dict["title"] as? String` is nil for a null title (not an NSNull that would stringify).
    private static func normalizedFields(_ dict: [String: Any]) -> [String: Any] {
        var out: [String: Any] = [:]
        for (k, v) in dict {
            if v is NSNull { continue }
            out[k] = v
        }
        return out
    }

    // MARK: Recovery code (the ONLY off-device representation of the key)
    //
    // Scheme: Crockford Base32 (RFC-4648 alphabet minus I,L,O,U to avoid transcription slips) over
    // the 33 bytes = 32 key bytes || 1 checksum byte (CRC-8/ATM over the 32 key bytes). 33 bytes ->
    // ceil(33*8/5) = 53 Base32 symbols, grouped 4-4-... with hyphens for legibility:
    //   e.g. "K7QF-9M2X-...-NP3R" (14 groups; the last group is shorter). Case-insensitive on import;
    //   hyphens/whitespace are ignored. The checksum catches most single-character transcription
    //   errors before the (indistinguishable) AES-GCM decrypt failure would.
    //
    // Round-trip: recoveryCode() -> importRecoveryCode(_:) restores the exact 32 key bytes.

    private static let base32Alphabet = Array("0123456789ABCDEFGHJKMNPQRSTVWXYZ")   // Crockford, 32 symbols
    /// Reverse map built once; includes Crockford's transcription aliases (I/L->1, O->0).
    private static let base32Reverse: [Character: UInt8] = {
        var map: [Character: UInt8] = [:]
        for (i, c) in base32Alphabet.enumerated() { map[c] = UInt8(i) }
        map["I"] = 1; map["L"] = 1; map["O"] = 0
        return map
    }()

    /// The human-transcribable recovery code for the CURRENT key. Throws if no key exists.
    static func recoveryCode() throws -> String {
        guard let key = currentKey() else { throw CortexE2EEError.noKey }
        let raw = key.withUnsafeBytes { [UInt8]($0) }
        return encodeRecovery(raw)
    }

    /// Encode 32 raw key bytes into the grouped Base32-with-checksum code. Exposed at file scope for
    /// tests / the offline-tool spec; callers use recoveryCode().
    static func encodeRecovery(_ raw: [UInt8]) -> String {
        precondition(raw.count == keyByteCount, "recovery code encodes exactly \(keyByteCount) key bytes")
        var payload = raw
        payload.append(crc8(raw))                       // 33 bytes total
        let symbols = base32Encode(payload)
        // Group into 4s with hyphens.
        var grouped: [String] = []
        var idx = symbols.startIndex
        while idx < symbols.endIndex {
            let end = symbols.index(idx, offsetBy: 4, limitedBy: symbols.endIndex) ?? symbols.endIndex
            grouped.append(String(symbols[idx..<end]))
            idx = end
        }
        return grouped.joined(separator: "-")
    }

    /// Validate + import a recovery code, storing the recovered key. Throws on any structural or
    /// checksum failure (before any key is written). On success the key is persisted and usable.
    static func importRecoveryCode(_ code: String) throws {
        let key = try keyFromRecoveryCode(code)
        persist(key)
    }

    /// Decode a recovery code to a SymmetricKey WITHOUT persisting (used by import + tests).
    static func keyFromRecoveryCode(_ code: String) throws -> SymmetricKey {
        // Strip hyphens + whitespace, uppercase for the case-insensitive alphabet.
        let cleaned = code.uppercased().unicodeScalars
            .filter { !CharacterSet.whitespacesAndNewlines.contains($0) && $0 != "-" }
            .map { Character($0) }
        guard !cleaned.isEmpty else { throw CortexE2EEError.invalidRecoveryCode("empty") }

        var symbolValues: [UInt8] = []
        symbolValues.reserveCapacity(cleaned.count)
        for c in cleaned {
            guard let v = base32Reverse[c] else {
                throw CortexE2EEError.invalidRecoveryCode("contains an unrecognized character '\(c)'")
            }
            symbolValues.append(v)
        }
        let decoded = base32Decode(symbolValues)
        guard decoded.count == keyByteCount + 1 else {
            throw CortexE2EEError.invalidRecoveryCode("wrong length — expected a full \(keyByteCount)-byte key code")
        }
        let raw = Array(decoded[0..<keyByteCount])
        let checksum = decoded[keyByteCount]
        guard crc8(raw) == checksum else {
            throw CortexE2EEError.invalidRecoveryCode("checksum mismatch — re-check the characters")
        }
        return SymmetricKey(data: Data(raw))
    }

    // MARK: Base32 (big-endian bit packing) + CRC-8

    /// Pack bytes MSB-first into 5-bit symbols. Trailing bits (partial final symbol) are left-aligned
    /// and zero-padded, matching the decoder below so the round-trip is exact.
    private static func base32Encode(_ bytes: [UInt8]) -> String {
        var out = String()
        var buffer: UInt32 = 0
        var bits = 0
        for byte in bytes {
            buffer = (buffer << 8) | UInt32(byte)
            bits += 8
            while bits >= 5 {
                bits -= 5
                let index = Int((buffer >> UInt32(bits)) & 0x1F)
                out.append(base32Alphabet[index])
            }
        }
        if bits > 0 {
            let index = Int((buffer << UInt32(5 - bits)) & 0x1F)
            out.append(base32Alphabet[index])
        }
        return out
    }

    /// Inverse of base32Encode. Consumes 5-bit symbol values MSB-first; any residual padding bits
    /// (< 8) at the end are discarded, so an exact-length input recovers exactly the source bytes.
    private static func base32Decode(_ symbols: [UInt8]) -> [UInt8] {
        var out: [UInt8] = []
        var buffer: UInt32 = 0
        var bits = 0
        for sym in symbols {
            buffer = (buffer << 5) | UInt32(sym & 0x1F)
            bits += 5
            if bits >= 8 {
                bits -= 8
                out.append(UInt8((buffer >> UInt32(bits)) & 0xFF))
            }
        }
        return out
    }

    /// CRC-8/ATM (poly 0x07, init 0x00, no reflection, no final xor). A compact, well-defined 1-byte
    /// checksum — enough to catch typical single-symbol transcription slips before the AES-GCM decrypt
    /// (which would otherwise be the only, indistinguishable, failure signal).
    static func crc8(_ bytes: [UInt8]) -> UInt8 {
        var crc: UInt8 = 0x00
        for byte in bytes {
            crc ^= byte
            for _ in 0..<8 {
                if crc & 0x80 != 0 {
                    crc = (crc << 1) ^ 0x07
                } else {
                    crc <<= 1
                }
            }
        }
        return crc
    }
}
