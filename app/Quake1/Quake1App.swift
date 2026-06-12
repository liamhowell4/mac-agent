import SwiftUI

@main
struct Quake1App: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        // LSUIElement menubar app: no main window. The floating panel is
        // owned by AppDelegate; Settings is the only SwiftUI scene.
        Settings {
            SettingsView()
        }
    }
}

private struct SettingsView: View {
    @AppStorage("repoPath") private var repoPath: String = DaemonProcess.defaultRepoPath

    var body: some View {
        Form {
            TextField("Repo path", text: $repoPath)
                .frame(minWidth: 380)
            Text("The daemon is launched as: uv --directory <repo> run quake-daemon")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(20)
        .frame(width: 480)
    }
}
