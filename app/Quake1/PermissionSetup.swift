import AppKit
import CoreGraphics

/// Drives every macOS permission prompt once, from Quake itself.
///
/// Why in-app rather than via the daemon: macOS attributes an Automation grant
/// to the *responsible process*. When Quake.app sends the Apple Event directly,
/// that's Quake.app; when the app-spawned daemon sends one at runtime, the
/// responsible process is still Quake.app (it launched the daemon). Same TCC
/// client either way — so a grant triggered here applies to real tool calls too.
enum PermissionSetup {
    /// (target app, probe AppleScript). Each first run triggers the Automation
    /// dialog for that app. Mirrors `SETUP_PROBES` in src/quake1/cli.py.
    static let probes: [(app: String, script: String)] = [
        ("Calendar", "tell application \"Calendar\" to get name of first calendar"),
        ("Reminders", "tell application \"Reminders\" to get name of first list"),
        ("Contacts", "tell application \"Contacts\" to get count of people"),
        ("Messages", "tell application \"Messages\" to get name"),
        ("Mail", "tell application \"Mail\" to get name"),
        ("Music", "tell application \"Music\" to get name"),
        ("Notes", "tell application \"Notes\" to get name"),
        ("System Events", "tell application \"System Events\" to get name of first process"),
        ("Finder", "tell application \"Finder\" to get name"),
    ]

    struct Result {
        var app: String
        var granted: Bool
        var detail: String?
    }

    /// Run all probes sequentially off the main thread (each blocks on its
    /// dialog), reporting progress back on the main queue.
    static func grantAll(
        progress: @escaping (Int, Int, String) -> Void,
        completion: @escaping ([Result]) -> Void
    ) {
        DispatchQueue.global(qos: .userInitiated).async {
            var results: [Result] = []
            for (index, probe) in probes.enumerated() {
                DispatchQueue.main.async {
                    progress(index, probes.count, probe.app)
                }
                var errorInfo: NSDictionary?
                let script = NSAppleScript(source: probe.script)
                _ = script?.executeAndReturnError(&errorInfo)
                // errAEEventNotPermitted (-1743) = user clicked Don't Allow (or a
                // prior denial). Anything else (e.g. app-not-running) still means
                // the Automation grant itself went through.
                let code = (errorInfo?["NSAppleScriptErrorNumber"] as? Int) ?? 0
                let denied = code == -1743
                results.append(Result(
                    app: probe.app,
                    granted: !denied,
                    detail: denied ? "Permission denied" : nil
                ))
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
