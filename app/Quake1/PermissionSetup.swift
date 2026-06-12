import AppKit
import CoreGraphics
import CoreServices

/// Drives every macOS permission prompt once, from Quake itself.
///
/// Why in-app rather than via the daemon: macOS attributes an Automation grant
/// to the *responsible process*. When Quake.app requests it, that's Quake.app;
/// when the app-spawned daemon sends an Apple Event at runtime, the responsible
/// process is still Quake.app (it launched the daemon). Same TCC client either
/// way — so a grant triggered here applies to real tool calls too.
///
/// We request via `AEDeterminePermissionToAutomateTarget`, Apple's purpose-built
/// API, rather than firing a dummy AppleScript event. Sending an event is an
/// unreliable request mechanism — for several apps (Calendar, Reminders,
/// Contacts, System Events) the launch/consent race returns errAEEventNotPermitted
/// (-1743) even when the user would allow it, so the dummy-event path reported
/// false denials. This API prompts cleanly and returns the true status.
enum PermissionSetup {
    /// (display name, bundle id) for each app the assistant automates.
    static let targets: [(name: String, bundleID: String)] = [
        ("Calendar", "com.apple.iCal"),
        ("Reminders", "com.apple.reminders"),
        ("Contacts", "com.apple.AddressBook"),
        ("Messages", "com.apple.MobileSMS"),
        ("Mail", "com.apple.mail"),
        ("Music", "com.apple.Music"),
        ("Notes", "com.apple.Notes"),
        ("System Events", "com.apple.systemevents"),
        ("Finder", "com.apple.finder"),
    ]

    struct Result {
        var app: String
        var granted: Bool
        var detail: String?
    }

    /// Request automation permission for one target app, prompting if needed.
    /// Returns the real TCC status (blocks on the dialog when it appears).
    private static func requestAutomation(bundleID: String) -> OSStatus {
        var target = AEDesc(descriptorType: typeNull, dataHandle: nil)
        let bytes = Array(bundleID.utf8)
        let createErr = bytes.withUnsafeBytes { raw in
            AECreateDesc(typeApplicationBundleID, raw.baseAddress, raw.count, &target)
        }
        guard createErr == noErr else { return OSStatus(createErr) }
        defer { AEDisposeDesc(&target) }
        // askUserIfNeeded: true -> shows the consent dialog and blocks for the
        // answer. typeWildCard/typeWildCard = "any event" (we just want the grant).
        return AEDeterminePermissionToAutomateTarget(
            &target, typeWildCard, typeWildCard, true)
    }

    /// Walk every target sequentially off the main thread (each blocks on its
    /// dialog), reporting progress on the main queue.
    static func grantAll(
        progress: @escaping (Int, Int, String) -> Void,
        completion: @escaping ([Result]) -> Void
    ) {
        // Bring Quake forward so the system dialogs are anchored to the front app.
        NSApp.activate(ignoringOtherApps: true)
        DispatchQueue.global(qos: .userInitiated).async {
            var results: [Result] = []
            for (index, target) in targets.enumerated() {
                DispatchQueue.main.async {
                    progress(index, targets.count, target.name)
                }
                let status = requestAutomation(bundleID: target.bundleID)
                // noErr (0) = allowed. -1743 = user denied. Anything else is an
                // unexpected error we surface verbatim.
                let granted = status == noErr
                let detail: String? = {
                    if granted { return nil }
                    if status == -1743 { return "Denied" }
                    return "Error \(status)"
                }()
                results.append(Result(app: target.name, granted: granted, detail: detail))
            }
            // Screen Recording is its own (non-Automation) prompt.
            let screenOK = CGRequestScreenCaptureAccess()
            results.append(Result(app: "Screen Recording", granted: screenOK,
                                  detail: screenOK ? nil : "Enable in Settings"))
            DispatchQueue.main.async { completion(results) }
        }
    }

    /// Open System Settings at the Automation pane so the user can review/toggle
    /// the per-app grants Quake holds.
    static func openAutomationSettings() {
        let url = URL(
            string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"
        )!
        NSWorkspace.shared.open(url)
    }
}
