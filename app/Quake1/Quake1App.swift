import SwiftUI

@main
struct Quake1App: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        // LSUIElement menubar app: no main window. The floating panel is
        // owned by AppDelegate; Settings is the only SwiftUI scene.
        Settings {
            SettingsView(
                onApply: { appDelegate.applySettingsAndRestartDaemon() }
            )
        }
    }
}

struct SettingsView: View {
    @AppStorage("modelTag") private var modelTag: String = ""
    @AppStorage("thinkMode") private var thinkMode: Bool = false
    @AppStorage("repoPath") private var repoPath: String = DaemonProcess.defaultRepoPath

    @State private var availableModels: [String] = []
    @State private var loadError: String?
    @State private var applied = false

    @State private var granting = false
    @State private var grantStatus: String?

    var onApply: () -> Void = {}

    var body: some View {
        Form {
            Section("Model") {
                if availableModels.isEmpty {
                    HStack {
                        TextField("Ollama model tag", text: $modelTag,
                                  prompt: Text("default (eval-validated qwen 4B)"))
                        Button("Load installed models") { Task { await loadModels() } }
                    }
                } else {
                    Picker("Model", selection: $modelTag) {
                        Text("Default (eval-validated qwen 4B)").tag("")
                        ForEach(availableModels, id: \.self) { tag in
                            Text(tag).tag(tag)
                        }
                    }
                    .pickerStyle(.menu)
                }
                if let loadError {
                    Text(loadError).font(.caption).foregroundStyle(.red)
                }
                Toggle("Reasoning mode (slower, same accuracy on the eval)",
                       isOn: $thinkMode)
            }

            Section("Permissions") {
                Text("Grant every macOS permission once, attributed to Quake. " +
                     "Click through each dialog that appears; the grants then " +
                     "cover the assistant's real actions and persist across updates.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                HStack {
                    Button(granting ? "Granting…" : "Grant all permissions") {
                        grantAll()
                    }
                    .disabled(granting)
                    Button("Open Automation Settings") {
                        PermissionSetup.openAutomationSettings()
                    }
                    if granting { ProgressView().controlSize(.small) }
                }
                if let grantStatus {
                    Text(grantStatus)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Section("Full Disk Access (read Messages history)") {
                Text("Reading iMessage/SMS history isn't scriptable — the daemon reads " +
                     "chat.db directly, which needs Full Disk Access. macOS keys FDA to the " +
                     "daemon's resolved binary, NOT to Quake.app, so grant it there:")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(PermissionSetup.daemonBinaryPath(repoPath: repoPath))
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                HStack {
                    Button("Open Full Disk Access Settings") {
                        PermissionSetup.openFullDiskAccessSettings()
                    }
                    Button("Reveal daemon binary in Finder") {
                        PermissionSetup.revealDaemonBinary(repoPath: repoPath)
                    }
                }
                Text("Drag the revealed binary into the list (or use +), enable it, then " +
                     "Apply & Restart Daemon below.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Advanced") {
                TextField("Repo path", text: $repoPath)
                Text("The daemon runs from <repo>/.venv/bin/quake-daemon")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            HStack {
                Spacer()
                if applied {
                    Text("Restarting daemon…")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Button("Apply & Restart Daemon") {
                    onApply()
                    applied = true
                    DispatchQueue.main.asyncAfter(deadline: .now() + 3) { applied = false }
                }
                .keyboardShortcut(.defaultAction)
            }
        }
        .formStyle(.grouped)
        .padding(12)
        .frame(width: 520)
        .task { await loadModels() }
    }

    /// Walk every permission prompt from Quake itself (see PermissionSetup).
    private func grantAll() {
        granting = true
        grantStatus = nil
        PermissionSetup.grantAll(
            progress: { index, total, app in
                grantStatus = "Requesting \(app)… (\(index + 1)/\(total))"
            },
            completion: { results in
                granting = false
                let denied = results.filter { !$0.granted }.map(\.app)
                grantStatus = denied.isEmpty
                    ? "All permissions granted ✓"
                    : "Granted, except: \(denied.joined(separator: ", ")). " +
                      "Re-run, or enable them in System Settings."
            }
        )
    }

    /// List installed Ollama models so switching is a picker, not a typing exercise.
    private func loadModels() async {
        guard let url = URL(string: "http://localhost:11434/api/tags") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            struct Tags: Decodable {
                struct M: Decodable { let name: String }
                let models: [M]
            }
            let tags = try JSONDecoder().decode(Tags.self, from: data)
            availableModels = tags.models.map(\.name).sorted()
            loadError = nil
        } catch {
            loadError = "Couldn't list models — is Ollama running?"
        }
    }
}
