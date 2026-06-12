import SwiftUI

/// Transcript card rendered under the capsule: prior query, tool rows,
/// assistant text.
struct ResponseView: View {
    @EnvironmentObject private var client: DaemonClient

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(client.transcript) { item in
                        row(for: item)
                            .id(item.id)
                    }
                    if case .error(let message) = client.state {
                        errorRow(message)
                    }
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 360)
            .fixedSize(horizontal: false, vertical: true)
            .onChange(of: client.transcript.count) {
                if let last = client.transcript.last {
                    proxy.scrollTo(last.id, anchor: .bottom)
                }
            }
        }
        .background(
            .ultraThinMaterial,
            in: RoundedRectangle(cornerRadius: 16, style: .continuous)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .strokeBorder(Color.white.opacity(0.1), lineWidth: 1)
        )
        .shadow(color: .black.opacity(0.3), radius: 20, y: 6)
    }

    @ViewBuilder
    private func row(for item: TranscriptItem) -> some View {
        switch item {
        case .user(_, let text):
            Text(text)
                .font(.caption)
                .foregroundStyle(.secondary)

        case .tool(_, let call, let state, let hint):
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    Image(systemName: ToolIcon.symbol(forDomain: call.domain))
                        .font(.system(size: 13))
                        .foregroundStyle(.secondary)
                        .frame(width: 22, height: 22)
                        .background(Circle().fill(Color.white.opacity(0.08)))

                    Text(call.shortName.replacingOccurrences(of: "_", with: " "))
                        .font(.system(size: 13, weight: .medium))

                    Spacer()

                    switch state {
                    case .running:
                        ProgressView().controlSize(.small)
                    case .done:
                        Image(systemName: "checkmark.circle")
                            .foregroundStyle(.green)
                    case .failed:
                        Image(systemName: "xmark")
                            .foregroundStyle(.red)
                    }
                }
                if let hint, !hint.isEmpty {
                    HStack(spacing: 8) {
                        Text(hint)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Button("Open Settings") {
                            openSystemSettings()
                        }
                        .controlSize(.small)
                    }
                    .padding(.leading, 30)
                }
            }

        case .assistant(_, let text):
            Text(text)
                .font(.body)
                .textSelection(.enabled)
        }
    }

    private func errorRow(_ message: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.red)
            Text(message)
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
            Button("Dismiss") { client.dismissError() }
                .controlSize(.small)
        }
    }

    private func openSystemSettings() {
        // Hints typically point at permission grants (Automation, Full Disk
        // Access, etc.) — land the user in Privacy & Security.
        let url = URL(string: "x-apple.systempreferences:com.apple.preference.security")!
        NSWorkspace.shared.open(url)
    }
}

/// Domain -> SF Symbol / app name maps shared by ResponseView and ConfirmView.
enum ToolIcon {
    static func symbol(forDomain domain: String) -> String {
        switch domain {
        case "calendar": return "calendar"
        case "mail": return "envelope"
        case "messages": return "message"
        case "files": return "folder"
        case "system": return "gearshape"
        case "music": return "music.note"
        default: return "bolt"
        }
    }

    static func appName(forDomain domain: String) -> String? {
        switch domain {
        case "calendar": return "Calendar"
        case "mail": return "Mail"
        case "messages": return "Messages"
        case "files": return "Finder"
        case "music": return "Music"
        case "reminders": return "Reminders"
        case "contacts": return "Contacts"
        case "notes": return "Notes"
        default: return nil
        }
    }
}
