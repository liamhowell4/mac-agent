import AppKit
import CoreGraphics

/// Drives every macOS permission prompt once, from Quake itself.
///
/// Why in-app rather than via the daemon: macOS attributes an Automation grant
/// to the *responsible process*. When Quake.app sends the Apple Event, that's
/// Quake.app; when the app-spawned daemon sends one at runtime, the responsible
/// process is still Quake.app (it launched the daemon). Same TCC client either
/// way — so a grant triggered here applies to real tool calls too.
///
/// Each app is probed *twice*. The first event surfaces the consent dialog; for
/// some apps (Calendar, Reminders, Contacts, System Events) the launch/consent
/// race makes that first call return errAEEventNotPermitted (-1743) even when the
/// user allows it. The second call runs after the grant has settled, so its
/// result is the true verdict.
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

    /// Run one probe script, returning its AppleScript error number (0 = no error).
    private static func runProbe(_ source: String) -> Int {
        var errorInfo: NSDictionary?
        NSAppleScript(source: source)?.executeAndReturnError(&errorInfo)
        return (errorInfo?["NSAppleScriptErrorNumber"] as? Int) ?? 0
    }

    /// Walk every probe sequentially off the main thread (each blocks on its
    /// dialog), reporting progress on the main queue.
    static func grantAll(
        progress: @escaping (Int, Int, String) -> Void,
        completion: @escaping ([Result]) -> Void
    ) {
        NSApp.activate(ignoringOtherApps: true)
        DispatchQueue.global(qos: .userInitiated).async {
            var results: [Result] = []
            for (index, probe) in probes.enumerated() {
                DispatchQueue.main.async {
                    progress(index, probes.count, probe.app)
                }
                // First call surfaces the dialog; second call reads the settled grant.
                _ = runProbe(probe.script)
                let code = runProbe(probe.script)
                // -1743 = errAEEventNotPermitted (automation denied). Any other code
                // (0, or an app-specific scripting error) means automation IS allowed.
                let denied = code == -1743
                results.append(Result(app: probe.app, granted: !denied,
                                      detail: denied ? "Denied" : nil))
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
