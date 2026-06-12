import SwiftUI

/// The Spotlight-style input capsule.
struct CapsuleView: View {
    @EnvironmentObject private var client: DaemonClient
    @State private var text = ""
    @FocusState private var focused: Bool

    var body: some View {
        VStack(spacing: 10) {
            HStack(spacing: 12) {
                Image(systemName: "bolt.fill")
                    .font(.system(size: 18))
                    .foregroundStyle(.secondary)

                TextField("Ask Quake…", text: $text)
                    .textFieldStyle(.plain)
                    .font(.system(size: 22, weight: .regular))
                    .focused($focused)
                    .onSubmit(submit)

                trailingIndicator
            }
            .padding(.horizontal, 20)
            .frame(height: 56)
            .glassCard(cornerRadius: 28)

            if client.state == .idle && client.transcript.isEmpty && text.isEmpty {
                Text("Press ⌘⌃Space to open Quake anywhere")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .onAppear { focused = true }
        // Esc: clear the field if it has text, otherwise let the panel's
        // cancelOperation close it.
        .onExitCommand {
            if !text.isEmpty {
                text = ""
            } else {
                NSApp.keyWindow?.orderOut(nil)
            }
        }
    }

    @ViewBuilder
    private var trailingIndicator: some View {
        switch client.state {
        case .warming:
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text("Warming up…")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        case .working:
            ProgressView().controlSize(.small)
        case .runningTool:
            ProgressView().controlSize(.small)
        default:
            if !client.isConnected {
                Image(systemName: "bolt.slash")
                    .font(.system(size: 14))
                    .foregroundStyle(.secondary)
                    .help("Daemon not connected")
            }
        }
    }

    private func submit() {
        let query = text
        text = ""
        client.send(query: query)
    }
}
