import SwiftUI

extension Notification.Name {
    static let quakeOpenSettings = Notification.Name("quake.openSettings")
}

/// Invisible bridge: AppKit (menubar item) can't open the SwiftUI Settings
/// scene directly on modern macOS — only the openSettings environment action
/// can. AppDelegate hosts one of these in a permanently-present, off-screen,
/// zero-alpha window so the subscription stays alive without ever showing the
/// floating panel.
struct SettingsOpener: View {
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        Color.clear
            .frame(width: 0, height: 0)
            .onReceive(NotificationCenter.default.publisher(for: .quakeOpenSettings)) { _ in
                openSettings()
                NSApp.activate(ignoringOtherApps: true)
            }
    }
}

/// Root of the floating panel: capsule input on top, then whichever
/// interaction surface the daemon state calls for.
struct RootView: View {
    @EnvironmentObject private var client: DaemonClient

    var body: some View {
        VStack(spacing: 12) {
            CapsuleView()

            switch client.state {
            case .confirming(let request):
                ConfirmView(request: request)

            case .asking(let request):
                AskView(request: request)

            default:
                if !client.transcript.isEmpty || isErrorState {
                    ResponseView()
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .frame(width: FloatingPanel.panelWidth)
    }

    private var isErrorState: Bool {
        if case .error = client.state { return true }
        return false
    }
}

/// Clarifying-question card: question text, one button per option, plus a
/// free-text answer field.
struct AskView: View {
    @EnvironmentObject private var client: DaemonClient
    let request: AskRequest

    @State private var freeText = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: "questionmark.circle")
                    .foregroundStyle(.secondary)
                Text(request.question)
                    .font(.system(size: 14, weight: .medium))
            }

            if !request.options.isEmpty {
                FlowingButtons(options: request.options) { option in
                    client.reply(answer: option)
                }
            }

            HStack(spacing: 8) {
                TextField("Or type an answer…", text: $freeText)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 13))
                    .onSubmit(submitFreeText)
                Button("Answer", action: submitFreeText)
                    .disabled(freeText.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(16)
        .glassCard(cornerRadius: 16)
        .id(request.id)
    }

    private func submitFreeText() {
        let answer = freeText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !answer.isEmpty else { return }
        client.reply(answer: answer)
        freeText = ""
    }
}

/// Simple wrapping row of option buttons.
private struct FlowingButtons: View {
    let options: [String]
    let action: (String) -> Void

    var body: some View {
        // Options are typically short and few; a wrapping LazyVGrid keeps
        // this dependency-free without a custom flow layout.
        LazyVGrid(
            columns: [GridItem(.adaptive(minimum: 120), spacing: 8)],
            alignment: .leading,
            spacing: 8
        ) {
            ForEach(options, id: \.self) { option in
                Button(option) { action(option) }
                    .buttonStyle(.bordered)
            }
        }
    }
}
