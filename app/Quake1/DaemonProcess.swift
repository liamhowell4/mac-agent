import Foundation

/// Owns the Python daemon child process. Spawned lazily when the socket
/// connection fails; killed and respawned by "Restart Daemon".
@MainActor
final class DaemonProcess {
    static let defaultRepoPath = "/Users/liamhowell/coding_projects/personal/mac-agent"

    private var process: Process?

    var repoPath: String {
        UserDefaults.standard.string(forKey: "repoPath") ?? Self.defaultRepoPath
    }

    var isRunning: Bool {
        process?.isRunning ?? false
    }

    /// Spawn the daemon unless our child is already running. (If a daemon
    /// was started externally and owns the socket, the duplicate will fail
    /// to bind and exit — harmless.)
    func spawnIfNeeded() {
        guard !isRunning else { return }
        spawn()
    }

    func spawn() {
        // Ensure the socket directory exists before the daemon binds.
        let socketDir = FileManager.default.urls(
            for: .applicationSupportDirectory, in: .userDomainMask
        ).first!.appendingPathComponent("Quake1")
        try? FileManager.default.createDirectory(
            at: socketDir, withIntermediateDirectories: true
        )

        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/zsh")
        // -l so the login shell PATH (homebrew, uv) is available.
        p.arguments = ["-lc", "uv --directory \(shellQuoted(repoPath)) run quake-daemon"]
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        do {
            try p.run()
            process = p
        } catch {
            NSLog("Quake1: failed to spawn daemon: \(error)")
            process = nil
        }
    }

    func terminate() {
        guard let process, process.isRunning else { return }
        process.terminate()
        self.process = nil
    }

    func restartDaemon() {
        terminate()
        spawn()
    }

    private func shellQuoted(_ path: String) -> String {
        "'" + path.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }
}
