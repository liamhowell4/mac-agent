import SwiftUI

/// One exchange = the user's query plus everything that answered it.
private struct Exchange: Identifiable {
    let id: String
    let items: [TranscriptItem]
}

/// Transcript rendered as reverse-chronological cards: the newest exchange sits
/// directly under the capsule; older ones stack below it.
struct ResponseView: View {
    @EnvironmentObject private var client: DaemonClient

    /// Group the flat transcript into exchanges (a `.user` item starts a new one),
    /// newest first.
    private var exchanges: [Exchange] {
        var groups: [[TranscriptItem]] = []
        for item in client.transcript {
            if case .user = item {
                groups.append([item])
            } else if groups.isEmpty {
                groups.append([item])
            } else {
                groups[groups.count - 1].append(item)
            }
        }
        return groups.reversed().map { Exchange(id: $0.first?.id ?? UUID().uuidString,
                                                items: $0) }
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    if case .error(let message) = client.state {
                        errorRow(message)
                            .padding(12)
                            .background(cardShape)
                    }
                    ForEach(exchanges) { exchange in
                        card(for: exchange)
                            .id(exchange.id)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 360)
            .fixedSize(horizontal: false, vertical: true)
            .onChange(of: client.transcript.count) {
                if let newest = exchanges.first {
                    proxy.scrollTo(newest.id, anchor: .top)
                }
            }
        }
    }

    private var cardShape: some View {
        RoundedRectangle(cornerRadius: 16, style: .continuous)
            .fill(.ultraThinMaterial)
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .strokeBorder(Color.white.opacity(0.1), lineWidth: 1)
            )
            .shadow(color: .black.opacity(0.25), radius: 14, y: 4)
    }

    private var isWorking: Bool {
        switch client.state {
        case .working, .runningTool: return true
        default: return false
        }
    }

    private func card(for exchange: Exchange) -> some View {
        let isNewest = exchange.id == exchanges.first?.id
        return VStack(alignment: .leading, spacing: 10) {
            ForEach(exchange.items) { item in
                row(for: item)
            }
            if isNewest && isWorking {
                WorkingRow(label: workingLabel, isToolRunning: isToolRunning)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(cardShape)
    }

    private var workingLabel: String {
        if case .runningTool(let name) = client.state {
            return name.replacingOccurrences(of: ".", with: " › ")
                       .replacingOccurrences(of: "_", with: " ")
        }
        return "working…"
    }

    private var isToolRunning: Bool {
        if case .runningTool = client.state { return true }
        return false
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

/// Status line under the newest card. If a tool runs suspiciously long, surface
/// that macOS may be sitting on a permission dialog (the #1 silent blocker).
private struct WorkingRow: View {
    let label: String
    let isToolRunning: Bool
    @State private var slow = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text(label)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if slow && isToolRunning {
                Text("Still waiting — macOS may be showing a permission dialog. " +
                     "Check for a prompt (it can appear behind other windows).")
                    .font(.caption2)
                    .foregroundStyle(.orange)
            }
        }
        .task(id: label) {
            slow = false
            try? await Task.sleep(nanoseconds: 8_000_000_000)
            slow = true
        }
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
