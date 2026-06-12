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

    /// Take ownership of the daemon: if some *other* process is serving the
    /// socket (e.g. one a terminal started — its osascript prompts get
    /// attributed to that terminal, not Quake), kill it and spawn our own so
    /// macOS treats Quake.app as the responsible process for every tool call.
    func takeOwnership() {
        guard !isRunning else { return }
        if let pid = foreignDaemonPID() {
            kill(pid, SIGTERM)
            // Give it a moment to release the socket, then clear any stale files
            // so our fresh daemon binds cleanly instead of seeing the dying one.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in
                self?.clearStaleSocket()
                self?.spawn()
            }
        } else {
            clearStaleSocket()
            spawn()
        }
    }

    /// PID of a daemon we did NOT spawn, if one is currently serving the socket.
    private func foreignDaemonPID() -> pid_t? {
        let infoURL = appSupportDir.appendingPathComponent("daemon.json")
        guard let data = try? Data(contentsOf: infoURL),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let pid = obj["pid"] as? Int else { return nil }
        // Alive? kill(pid, 0) succeeds (or EPERM) for a live process; ESRCH if gone.
        guard kill(pid_t(pid), 0) == 0 || errno == EPERM else { return nil }
        // Not our own child.
        if let ours = process?.processIdentifier, Int(ours) == pid { return nil }
        return pid_t(pid)
    }

    private func clearStaleSocket() {
        let dir = appSupportDir
        try? FileManager.default.removeItem(at: dir.appendingPathComponent("quake1.sock"))
        try? FileManager.default.removeItem(at: dir.appendingPathComponent("daemon.json"))
    }

    private var appSupportDir: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)
            .first!.appendingPathComponent("Quake1")
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
        // Prefer the repo venv's entry point directly — zero PATH dependence.
        // (zsh -lc does NOT source .zshrc, so PATH additions from conda/brew in
        // .zshrc are invisible here; `uv` lookup failed silently that way.)
        let venvDaemon = "\(repoPath)/.venv/bin/quake-daemon"
        if FileManager.default.isExecutableFile(atPath: venvDaemon) {
            p.executableURL = URL(fileURLWithPath: venvDaemon)
        } else {
            // fallback: probe common uv install locations, then bare `uv`
            let home = NSHomeDirectory()
            let candidates = [
                "\(home)/.local/bin/uv", "/opt/homebrew/bin/uv",
                "/usr/local/bin/uv", "\(home)/miniconda/bin/uv",
            ]
            let uv = candidates.first {
                FileManager.default.isExecutableFile(atPath: $0)
            } ?? "uv"
            p.executableURL = URL(fileURLWithPath: "/bin/zsh")
            p.arguments = ["-lc",
                "\(uv) --directory \(shellQuoted(repoPath)) run quake-daemon"]
        }
        // log somewhere inspectable instead of the void
        let logURL = FileManager.default.urls(
            for: .applicationSupportDirectory, in: .userDomainMask
        ).first!.appendingPathComponent("Quake1/daemon.log")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        let log = try? FileHandle(forWritingTo: logURL)
        p.standardOutput = log ?? FileHandle.nullDevice
        p.standardError = log ?? FileHandle.nullDevice
        p.currentDirectoryURL = URL(fileURLWithPath: repoPath)
        var env = ProcessInfo.processInfo.environment
        let model = UserDefaults.standard.string(forKey: "modelTag") ?? ""
        if !model.isEmpty { env["QUAKE_MODEL"] = model }
        env["QUAKE_THINK"] = UserDefaults.standard.bool(forKey: "thinkMode") ? "on" : "off"
        p.environment = env
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
