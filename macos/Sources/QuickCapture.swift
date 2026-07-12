import AppKit
import ApplicationServices
import Carbon
import CoreGraphics
import SwiftUI
import Vision

// MARK: - KeyCombo

/// A user-recordable keyboard shortcut. `keyCode` is a virtual key code (Carbon `kVK_*` / the same
/// space `NSEvent.keyCode` reports); `modifiers` are stored as the raw value of an
/// `NSEvent.ModifierFlags` device-independent mask so the combo round-trips cleanly through
/// `Codable`/`UserDefaults`. Helpers translate to the Carbon modifier mask that
/// `RegisterEventHotKey` expects and to a human "⌥⌘C" display string.
struct KeyCombo: Codable, Equatable {
    var keyCode: UInt32
    /// Raw value of `NSEvent.ModifierFlags` (device-independent flags only).
    var modifiers: UInt

    init(keyCode: UInt32, modifiers: UInt) {
        self.keyCode = keyCode
        self.modifiers = modifiers
    }

    /// Convenience for building a combo directly from an `NSEvent`.
    init(event: NSEvent) {
        self.keyCode = UInt32(event.keyCode)
        self.modifiers = event.modifierFlags.intersection(.deviceIndependentFlagsMask).rawValue
    }

    private var flags: NSEvent.ModifierFlags {
        NSEvent.ModifierFlags(rawValue: modifiers)
    }

    /// The default Cortex quick-capture shortcut: ⌥⌘C.
    static let defaultCapture = KeyCombo(
        keyCode: UInt32(kVK_ANSI_C),
        modifiers: NSEvent.ModifierFlags([.command, .option]).rawValue
    )

    /// The Carbon modifier mask (`cmdKey | optionKey | …`) for `RegisterEventHotKey`.
    var carbonModifiers: UInt32 {
        var mask: UInt32 = 0
        if flags.contains(.command) { mask |= UInt32(cmdKey) }
        if flags.contains(.option) { mask |= UInt32(optionKey) }
        if flags.contains(.control) { mask |= UInt32(controlKey) }
        if flags.contains(.shift) { mask |= UInt32(shiftKey) }
        return mask
    }

    /// A shortcut is only registrable if it carries at least one command/control/option modifier —
    /// registering a bare key (or shift-only) would steal every keystroke system-wide.
    var isRegistrable: Bool {
        flags.contains(.command) || flags.contains(.control) || flags.contains(.option)
    }

    /// Human display string, e.g. "⌥⌘C". Modifier glyph order matches macOS menu convention
    /// (⌃⌥⇧⌘) and the key symbol is derived from the virtual key code.
    var displayString: String {
        var out = ""
        if flags.contains(.control) { out += "⌃" }
        if flags.contains(.option) { out += "⌥" }
        if flags.contains(.shift) { out += "⇧" }
        if flags.contains(.command) { out += "⌘" }
        out += KeyCombo.keySymbol(for: keyCode)
        return out
    }

    /// Maps a virtual key code to a printable glyph for the display string. Falls back to a keypad
    /// notation for anything not in the common table.
    static func keySymbol(for keyCode: UInt32) -> String {
        switch Int(keyCode) {
        case kVK_Space: return "Space"
        case kVK_Return: return "↩"
        case kVK_Tab: return "⇥"
        case kVK_Delete: return "⌫"
        case kVK_ForwardDelete: return "⌦"
        case kVK_Escape: return "⎋"
        case kVK_LeftArrow: return "←"
        case kVK_RightArrow: return "→"
        case kVK_UpArrow: return "↑"
        case kVK_DownArrow: return "↓"
        case kVK_Home: return "↖"
        case kVK_End: return "↘"
        case kVK_PageUp: return "⇞"
        case kVK_PageDown: return "⇟"
        case kVK_F1: return "F1"
        case kVK_F2: return "F2"
        case kVK_F3: return "F3"
        case kVK_F4: return "F4"
        case kVK_F5: return "F5"
        case kVK_F6: return "F6"
        case kVK_F7: return "F7"
        case kVK_F8: return "F8"
        case kVK_F9: return "F9"
        case kVK_F10: return "F10"
        case kVK_F11: return "F11"
        case kVK_F12: return "F12"
        default:
            break
        }
        if let char = KeyCombo.characterFromKeyCode(keyCode) {
            return char.uppercased()
        }
        return "Key \(keyCode)"
    }

    /// Best-effort translation of a virtual key code to its base character using the current
    /// keyboard layout (so a French/German layout shows the right letter). Modifier-free.
    private static func characterFromKeyCode(_ keyCode: UInt32) -> String? {
        guard let layoutData = TISGetInputSourceProperty(
            TISCopyCurrentKeyboardLayoutInputSource().takeRetainedValue(),
            kTISPropertyUnicodeKeyLayoutData
        ) else { return nil }
        let data = Unmanaged<CFData>.fromOpaque(layoutData).takeUnretainedValue() as Data
        var result: String?
        data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            guard let base = raw.baseAddress else { return }
            let keyboardLayout = base.assumingMemoryBound(to: UCKeyboardLayout.self)
            var deadKeyState: UInt32 = 0
            var chars = [UniChar](repeating: 0, count: 4)
            var length = 0
            let status = UCKeyTranslate(
                keyboardLayout,
                UInt16(keyCode),
                UInt16(kUCKeyActionDisplay),
                0,
                UInt32(LMGetKbdType()),
                OptionBits(kUCKeyTranslateNoDeadKeysBit),
                &deadKeyState,
                chars.count,
                &length,
                &chars
            )
            if status == noErr, length > 0 {
                result = String(utf16CodeUnits: chars, count: length)
            }
        }
        return result
    }
}

// MARK: - QuickCapture

/// Global quick-capture: a user-chosen hotkey grabs the current text selection (or an OCR'd screen
/// region) from any app and hands it to Cortex.
///
/// Distribution gating: EVERY capture action is a no-op under `DistributionMode.isAppStore`. The
/// three capabilities this uses — a global Carbon hotkey, synthesizing ⌘C / reading the focused
/// element via Accessibility, and Screen Recording for OCR — are all incompatible with the App
/// Store sandbox and would be rejected by App Review, so quick capture exists only in the notarized
/// direct/DMG build.
///
/// OS permissions by path:
///   - Global hotkey (`setEnabled`): none (Carbon `RegisterEventHotKey` needs no TCC grant).
///   - `triggerHighlightCapture`: primary path (synthesized ⌘C via `CGEvent`) needs **Accessibility**
///     (CGEvent posting is gated by TCC). The AX fallback (`kAXSelectedTextAttribute`) also needs
///     **Accessibility**. We prompt via `AXIsProcessTrustedWithOptions` and degrade gracefully.
///   - `triggerScreenshotCapture`: needs **Screen Recording** (`CGWindowListCreateImage` returns a
///     blank/desktop-only image without it). We detect the denial and guide the user to System
///     Settings; Vision OCR itself needs no permission.
@MainActor
final class QuickCapture {
    static let shared = QuickCapture()

    /// Called on the main actor with captured text and its source tag ("quick-capture") whenever any
    /// capture path succeeds. Wire this in AppState.
    var onCapturedText: ((_ text: String, _ source: String) -> Void)?

    private var hotKeyRef: EventHotKeyRef?
    private var eventHandlerRef: EventHandlerRef?
    private var isEnabled = false
    private var currentKeybind: KeyCombo?

    private init() {}

    // MARK: Enable / hotkey registration

    /// Enables or disables the global capture hotkey. A no-op (with a logged reason) under the App
    /// Store build. Re-registers cleanly when the keybind changes.
    func setEnabled(_ enabled: Bool, keybind: KeyCombo?) {
        guard !DistributionMode.isAppStore else {
            log("setEnabled ignored — quick capture is unavailable in the App Store build (global hotkey, Accessibility, and Screen Recording are sandbox-incompatible).")
            return
        }
        // Always tear down first so a keybind change or a disable leaves no stale registration.
        unregisterHotKey()
        isEnabled = enabled
        currentKeybind = keybind

        guard enabled else { return }
        guard let keybind else {
            log("setEnabled(true) with no keybind — nothing registered.")
            return
        }
        guard keybind.isRegistrable else {
            log("keybind \(keybind.displayString) has no command/control/option modifier — refusing to register a system-wide bare key.")
            return
        }
        registerHotKey(keybind)
    }

    private func registerHotKey(_ combo: KeyCombo) {
        var eventSpec = EventTypeSpec(
            eventClass: OSType(kEventClassKeyboard),
            eventKind: OSType(kEventHotKeyPressed)
        )
        // Install a dedicated handler; recover `self` from userData in the bare C callback.
        let installStatus = InstallEventHandler(
            GetApplicationEventTarget(),
            quickCaptureHotKeyEventHandler,
            1,
            &eventSpec,
            Unmanaged.passUnretained(self).toOpaque(),
            &eventHandlerRef
        )
        if installStatus != noErr {
            log("InstallEventHandler failed (status \(installStatus)).")
        }
        let hotKeyID = EventHotKeyID(signature: quickCaptureHotKeySignature, id: 1)
        let status = RegisterEventHotKey(
            combo.keyCode,
            combo.carbonModifiers,
            hotKeyID,
            GetApplicationEventTarget(),
            0,
            &hotKeyRef
        )
        if status != noErr {
            log("RegisterEventHotKey for \(combo.displayString) failed (status \(status)) — likely already claimed by another app.")
        } else {
            log("registered quick-capture hotkey \(combo.displayString).")
        }
    }

    private func unregisterHotKey() {
        if let hotKeyRef {
            UnregisterEventHotKey(hotKeyRef)
            self.hotKeyRef = nil
        }
        if let eventHandlerRef {
            RemoveEventHandler(eventHandlerRef)
            self.eventHandlerRef = nil
        }
    }

    /// Called from the Carbon C handler when the registered hotkey fires.
    fileprivate func handleHotKey() {
        triggerHighlightCapture()
    }

    // MARK: Highlight capture (selection)

    /// Reads the current text selection from the frontmost app. Primary path synthesizes ⌘C and
    /// reads the pasteboard (restoring it afterward); falls back to the Accessibility
    /// `kAXSelectedTextAttribute` of the focused element. Requires Accessibility permission.
    func triggerHighlightCapture() {
        guard !DistributionMode.isAppStore else {
            log("triggerHighlightCapture ignored — selection capture uses Accessibility/CGEvent, which are unavailable in the App Store build.")
            return
        }
        guard ensureAccessibilityPermission() else {
            NotchNotifier.shared.show(
                title: "Cortex needs Accessibility",
                subtitle: "Opening System Settings › Privacy › Accessibility…",
                style: .info
            )
            // U-LIVE5: the notch is non-interactive, so the toast alone leaves the user hunting through
            // System Settings. Deep-link straight to the exact pane so enabling Cortex is one step.
            openPrivacyPane("Privacy_Accessibility")
            return
        }

        if let text = copySelectionViaPasteboard(), !text.isEmpty {
            deliver(text)
            return
        }
        if let text = selectionViaAccessibility(), !text.isEmpty {
            deliver(text)
            return
        }
        NotchNotifier.shared.show(
            title: "Nothing selected",
            subtitle: "Highlight some text, then press your capture shortcut.",
            style: .info
        )
    }

    /// Save the pasteboard, synthesize ⌘C, read the copied string, then restore the pasteboard.
    /// Best-effort: the small delay lets the target app service the copy before we read.
    private func copySelectionViaPasteboard() -> String? {
        let pasteboard = NSPasteboard.general
        // Snapshot the existing pasteboard so we can restore whatever the user had.
        let saved = snapshotPasteboard(pasteboard)
        let priorChangeCount = pasteboard.changeCount

        pasteboard.clearContents()
        synthesizeCopy()

        // Poll briefly for the target app to write its selection to the pasteboard.
        var copied: String?
        let deadline = Date().addingTimeInterval(0.5)
        while Date() < deadline {
            if pasteboard.changeCount != priorChangeCount {
                copied = pasteboard.string(forType: .string)
                break
            }
            RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.02))
        }
        if copied == nil {
            copied = pasteboard.string(forType: .string)
        }

        restorePasteboard(pasteboard, from: saved)
        return copied?.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Post a synthetic ⌘C key-down/up to the frontmost app via CGEvent (needs Accessibility).
    private func synthesizeCopy() {
        let source = CGEventSource(stateID: .combinedSessionState)
        let cKey = CGKeyCode(kVK_ANSI_C)
        guard let keyDown = CGEvent(keyboardEventSource: source, virtualKey: cKey, keyDown: true),
              let keyUp = CGEvent(keyboardEventSource: source, virtualKey: cKey, keyDown: false)
        else { return }
        keyDown.flags = .maskCommand
        keyUp.flags = .maskCommand
        keyDown.post(tap: .cghidEventTap)
        keyUp.post(tap: .cghidEventTap)
    }

    /// Fallback: read `kAXSelectedTextAttribute` from the focused UI element of the frontmost app.
    private func selectionViaAccessibility() -> String? {
        let systemWide = AXUIElementCreateSystemWide()
        var focused: CFTypeRef?
        let focusErr = AXUIElementCopyAttributeValue(
            systemWide, kAXFocusedUIElementAttribute as CFString, &focused
        )
        guard focusErr == .success, let element = focused else { return nil }
        // `focused` is an AXUIElement; ask it for its selected text.
        let axElement = element as! AXUIElement
        var selected: CFTypeRef?
        let selErr = AXUIElementCopyAttributeValue(
            axElement, kAXSelectedTextAttribute as CFString, &selected
        )
        guard selErr == .success, let value = selected as? String else { return nil }
        return value.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    // MARK: Screenshot capture (OCR)

    /// Captures the main display, OCRs it with Vision, and delivers the recognized text. Requires
    /// Screen Recording permission; guides the user if it is missing. Vision OCR needs no permission.
    func triggerScreenshotCapture() {
        guard !DistributionMode.isAppStore else {
            log("triggerScreenshotCapture ignored — screen capture is unavailable in the App Store build (Screen Recording is sandbox-incompatible).")
            return
        }
        guard ensureScreenRecordingPermission() else {
            NotchNotifier.shared.show(
                title: "Cortex needs Screen Recording",
                subtitle: "Opening System Settings › Privacy › Screen Recording…",
                style: .info
            )
            // U-LIVE5: deep-link straight to the Screen Recording pane so the non-interactive notch
            // toast becomes actionable — the user just flips the toggle and re-triggers capture.
            openPrivacyPane("Privacy_ScreenCapture")
            return
        }
        guard let image = captureMainDisplay() else {
            NotchNotifier.shared.show(
                title: "Couldn't capture the screen",
                subtitle: nil,
                style: .info
            )
            return
        }
        recognizeText(in: image) { [weak self] text in
            guard let self else { return }
            let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.isEmpty {
                NotchNotifier.shared.show(
                    title: "No text found",
                    subtitle: "Cortex couldn't read any text in that capture.",
                    style: .info
                )
            } else {
                self.deliver(trimmed)
            }
        }
    }

    /// Snapshot the main display as a CGImage. `CGWindowListCreateImage` is 13-compatible (we do NOT
    /// use ScreenCaptureKit-14 APIs). Without Screen Recording permission this yields desktop-only
    /// pixels, which we pre-empt with the permission check above.
    private func captureMainDisplay() -> CGImage? {
        let displayID = CGMainDisplayID()
        let bounds = CGDisplayBounds(displayID)
        return CGWindowListCreateImage(
            bounds,
            .optionOnScreenOnly,
            kCGNullWindowID,
            [.bestResolution, .boundsIgnoreFraming]
        )
    }

    /// Run Vision text recognition (accurate, language-corrected) and join the results by line.
    /// Vision work runs off the main actor; the completion is hopped back to the main actor.
    private func recognizeText(in image: CGImage, completion: @escaping (String) -> Void) {
        let request = VNRecognizeTextRequest { request, _ in
            let observations = (request.results as? [VNRecognizedTextObservation]) ?? []
            let lines = observations.compactMap { $0.topCandidates(1).first?.string }
            let joined = lines.joined(separator: "\n")
            DispatchQueue.main.async { completion(joined) }
        }
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                try handler.perform([request])
            } catch {
                DispatchQueue.main.async { completion("") }
            }
        }
    }

    // MARK: Delivery

    private func deliver(_ text: String) {
        onCapturedText?(text, "quick-capture")
        NotchNotifier.shared.show(
            title: "Saved to Cortex",
            subtitle: QuickCapture.preview(of: text),
            style: .captured
        )
    }

    /// A short single-line preview of captured text for the notch toast.
    private static func preview(of text: String, limit: Int = 60) -> String {
        let collapsed = text
            .replacingOccurrences(of: "\n", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if collapsed.count <= limit { return collapsed }
        let end = collapsed.index(collapsed.startIndex, offsetBy: limit)
        return String(collapsed[..<end]) + "…"
    }

    // MARK: Permissions

    /// Returns true if Accessibility is trusted; otherwise prompts (system dialog) and returns false.
    private func ensureAccessibilityPermission() -> Bool {
        let options: [String: Any] = [
            kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true
        ]
        return AXIsProcessTrustedWithOptions(options as CFDictionary)
    }

    /// Returns true if Screen Recording is granted. Uses `CGPreflightScreenCaptureAccess` (10.15+)
    /// and requests access if not yet granted, guarding so a denial never crashes.
    private func ensureScreenRecordingPermission() -> Bool {
        if CGPreflightScreenCaptureAccess() { return true }
        // Triggers the one-time system prompt; returns immediately (grant takes effect on relaunch),
        // so we still return false this pass and guide the user.
        _ = CGRequestScreenCaptureAccess()
        return false
    }

    /// U-LIVE5: open a specific System Settings › Privacy & Security pane by its anchor (e.g.
    /// "Privacy_Accessibility", "Privacy_ScreenCapture"). Pairs with the permission toasts so a denial
    /// deep-links the user straight to the toggle instead of leaving them to navigate there by hand.
    private func openPrivacyPane(_ anchor: String) {
        guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(anchor)") else { return }
        NSWorkspace.shared.open(url)
    }

    // MARK: Logging

    private func log(_ message: String) {
        NSLog("[QuickCapture] %@", message)
    }
}

// MARK: - Pasteboard snapshot/restore

private struct PasteboardSnapshot {
    let items: [[NSPasteboard.PasteboardType: Data]]
}

private extension QuickCapture {
    /// Capture every item/type currently on the pasteboard as raw data so we can restore it after
    /// our synthetic copy. Best-effort — unreadable types are skipped.
    func snapshotPasteboard(_ pasteboard: NSPasteboard) -> PasteboardSnapshot {
        var items: [[NSPasteboard.PasteboardType: Data]] = []
        for item in pasteboard.pasteboardItems ?? [] {
            var typeMap: [NSPasteboard.PasteboardType: Data] = [:]
            for type in item.types {
                if let data = item.data(forType: type) {
                    typeMap[type] = data
                }
            }
            if !typeMap.isEmpty { items.append(typeMap) }
        }
        return PasteboardSnapshot(items: items)
    }

    func restorePasteboard(_ pasteboard: NSPasteboard, from snapshot: PasteboardSnapshot) {
        pasteboard.clearContents()
        guard !snapshot.items.isEmpty else { return }
        var newItems: [NSPasteboardItem] = []
        for typeMap in snapshot.items {
            let item = NSPasteboardItem()
            for (type, data) in typeMap {
                item.setData(data, forType: type)
            }
            newItems.append(item)
        }
        pasteboard.writeObjects(newItems)
    }
}

// MARK: - Carbon C hotkey handler

// FourCharCode 'CXQC' identifying the Cortex quick-capture hotkey (distinct from the Spotlight
// 'CXTX' signature so the two registrations never collide).
private let quickCaptureHotKeySignature = OSType(0x43585143)

/// Bare C event handler for the quick-capture hotkey. No captured context — recovers the
/// QuickCapture instance from userData and hops to the main actor to run the capture.
private func quickCaptureHotKeyEventHandler(
    _ nextHandler: EventHandlerCallRef?,
    _ event: EventRef?,
    _ userData: UnsafeMutableRawPointer?
) -> OSStatus {
    guard let userData else { return noErr }
    let capture = Unmanaged<QuickCapture>.fromOpaque(userData).takeUnretainedValue()
    Task { @MainActor in
        capture.handleHotKey()
    }
    return noErr
}

// MARK: - KeybindRecorderView

/// A small SwiftUI control that shows the current shortcut and records a new one. While recording, a
/// local `NSEvent` monitor captures the next key-down (with its modifiers) and returns a `KeyCombo`
/// via the binding. Escape cancels recording without changing the value.
struct KeybindRecorderView: View {
    @Binding var combo: KeyCombo?
    /// Optional callback fired with the freshly recorded combo (in addition to updating the binding).
    var onRecorded: ((KeyCombo) -> Void)?

    @State private var isRecording = false
    @State private var monitor: Any?

    var body: some View {
        HStack(spacing: CortexDesign.Space.sm) {
            Text(displayText)
                .font(CortexDesign.Typography.hint)
                .foregroundColor(isRecording ? CortexDesign.accent : CortexDesign.ink)
                .frame(minWidth: 74, alignment: .center)
                .padding(.vertical, 6)
                .padding(.horizontal, 10)
                .background(
                    RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous)
                        .fill(CortexDesign.cardBackground)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous)
                        .stroke(isRecording ? CortexDesign.accent : CortexDesign.hairline, lineWidth: 1)
                )

            Button(isRecording ? "Press keys…  (⎋ cancels)" : "Record shortcut") {
                if isRecording { stopRecording() } else { startRecording() }
            }
            .font(CortexDesign.Typography.caption)
            .buttonStyle(.plain)
            .foregroundColor(CortexDesign.accent)
        }
        .onDisappear { stopRecording() }
    }

    private var displayText: String {
        if isRecording { return "…" }
        return combo?.displayString ?? "None"
    }

    private func startRecording() {
        isRecording = true
        monitor = NSEvent.addLocalMonitorForEvents(matching: [.keyDown]) { event in
            // Escape cancels without recording.
            if event.keyCode == UInt16(kVK_Escape) {
                stopRecording()
                return nil
            }
            let recorded = KeyCombo(event: event)
            combo = recorded
            onRecorded?(recorded)
            stopRecording()
            return nil // swallow the event so it doesn't leak into the UI
        }
    }

    private func stopRecording() {
        isRecording = false
        if let monitor {
            NSEvent.removeMonitor(monitor)
            self.monitor = nil
        }
    }
}
