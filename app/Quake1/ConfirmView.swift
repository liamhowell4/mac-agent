import SwiftUI

/// Schema-driven editable confirmation card (Macatron-style): the daemon
/// proposes a tool call, the user can tweak arguments inline before approving.
struct ConfirmView: View {
    @EnvironmentObject private var client: DaemonClient
    let request: ConfirmRequest

    @State private var fields: [EditableField] = []

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
                .padding(16)

            Divider()
                .overlay(Color.white.opacity(0.08))

            VStack(alignment: .leading, spacing: 10) {
                ForEach($fields) { $field in
                    fieldRow($field)
                }
                if request.danger {
                    rawArgumentsBlock
                }
            }
            .padding(16)

            Divider()
                .overlay(Color.white.opacity(0.08))

            footer
                .padding(16)
        }
        .glassCard(cornerRadius: 16, tintedBorder: request.danger ? .red : nil)
        .onAppear { fields = EditableField.build(from: request) }
        .id(request.id) // rebuild state if a new confirm arrives
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: ToolIcon.symbol(forDomain: request.call.domain))
                .font(.system(size: 16, weight: .medium))
                .foregroundStyle(request.danger ? Color.red : Color.accentColor)
                .frame(width: 32, height: 32)
                .background(
                    Circle().fill(
                        (request.danger ? Color.red : Color.accentColor).opacity(0.15)
                    )
                )

            VStack(alignment: .leading, spacing: 1) {
                Text(Humanize.title(forTool: request.call.name))
                    .font(.system(size: 15, weight: .semibold))
                Text(request.call.name)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            if request.danger {
                Label("Destructive", systemImage: "exclamationmark.triangle.fill")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.red)
            }
        }
    }

    // MARK: - Fields

    @ViewBuilder
    private func fieldRow(_ field: Binding<EditableField>) -> some View {
        let label = Humanize.label(field.wrappedValue.key)
        switch field.wrappedValue.kind {
        case .boolean:
            Toggle(isOn: field.boolValue) {
                Text(label)
                    .font(.system(size: 13))
            }
            .toggleStyle(.switch)
            .controlSize(.small)

        case .integer, .number:
            LabeledContent {
                TextField("0", text: field.textValue)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 13, design: .monospaced))
                    .multilineTextAlignment(.trailing)
                    .frame(maxWidth: 160)
            } label: {
                Text(label).font(.system(size: 13))
            }

        case .array:
            LabeledContent {
                TextField("comma, separated", text: field.textValue)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 13))
            } label: {
                Text(label).font(.system(size: 13))
            }

        case .string, .other:
            LabeledContent {
                TextField("", text: field.textValue)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 13))
            } label: {
                Text(label).font(.system(size: 13))
            }
        }
    }

    private var rawArgumentsBlock: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Raw arguments")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            Text(rawArgumentsString)
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(
                    RoundedRectangle(cornerRadius: 6)
                        .fill(Color.black.opacity(0.2))
                )
        }
        .padding(.top, 4)
    }

    private var rawArgumentsString: String {
        let pairs = request.call.arguments.keys.sorted().map { key in
            "\(key): \(request.call.arguments[key]!.displayString)"
        }
        return pairs.isEmpty ? "(no arguments)" : pairs.joined(separator: "\n")
    }

    // MARK: - Footer

    private var footer: some View {
        HStack {
            Button("Cancel") {
                client.reply(approved: false)
            }
            .keyboardShortcut(.cancelAction)

            Spacer()

            if let appName = ToolIcon.appName(forDomain: request.call.domain) {
                Button("Open \(appName) ↗") {
                    openApp(named: appName)
                }
            }

            if request.danger {
                // Destructive: red, and deliberately NOT the default button —
                // Return must not approve a dangerous call.
                Button(role: .destructive) {
                    approve()
                } label: {
                    Text(Humanize.verb(forTool: request.call.name))
                }
                .buttonStyle(.bordered)
                .tint(.red)
            } else {
                Button(Humanize.verb(forTool: request.call.name)) {
                    approve()
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
            }
        }
    }

    private func approve() {
        var edited = request.call.arguments
        for field in fields {
            edited[field.key] = field.jsonValue
        }
        client.reply(approved: true, editedArguments: edited)
    }

    private func openApp(named appName: String) {
        let bundleNames: [String: String] = [
            "Calendar": "/System/Applications/Calendar.app",
            "Mail": "/System/Applications/Mail.app",
            "Messages": "/System/Applications/Messages.app",
            "Finder": "/System/Library/CoreServices/Finder.app",
            "Music": "/System/Applications/Music.app",
            "Reminders": "/System/Applications/Reminders.app",
            "Contacts": "/System/Applications/Contacts.app",
            "Notes": "/System/Applications/Notes.app",
        ]
        if let path = bundleNames[appName] {
            NSWorkspace.shared.openApplication(
                at: URL(fileURLWithPath: path),
                configuration: NSWorkspace.OpenConfiguration()
            )
        }
    }
}

// MARK: - Editable field model

struct EditableField: Identifiable {
    enum Kind {
        case string
        case integer
        case number
        case boolean
        case array
        case other
    }

    let key: String
    let kind: Kind
    var text: String
    var boolean: Bool

    var id: String { key }

    /// Build the field list by merging call arguments with schema properties.
    /// Schema (when present) determines type; arguments provide values.
    /// Schema-only keys appear empty so the user can fill them in.
    static func build(from request: ConfirmRequest) -> [EditableField] {
        let args = request.call.arguments
        let properties = request.schema?
            .objectValue?["properties"]?
            .objectValue ?? [:]

        // Preserve a stable order: argument keys (sorted) first, then
        // schema-only keys (sorted).
        var keys = args.keys.sorted()
        for key in properties.keys.sorted() where !keys.contains(key) {
            keys.append(key)
        }

        return keys.map { key in
            let value = args[key]
            let kind = fieldKind(schemaProperty: properties[key], value: value)
            switch kind {
            case .boolean:
                return EditableField(
                    key: key, kind: kind,
                    text: "",
                    boolean: value?.boolValue ?? false
                )
            default:
                return EditableField(
                    key: key, kind: kind,
                    text: value?.displayString ?? "",
                    boolean: false
                )
            }
        }
    }

    private static func fieldKind(schemaProperty: AnyJSON?, value: AnyJSON?) -> Kind {
        if let typeValue = schemaProperty?.objectValue?["type"],
           case .string(let type) = typeValue {
            switch type {
            case "string": return .string
            case "integer": return .integer
            case "number": return .number
            case "boolean": return .boolean
            case "array": return .array
            default: return .other
            }
        }
        switch value {
        case .string: return .string
        case .number: return .number
        case .bool: return .boolean
        case .array: return .array
        case .object, .null, .none: return .other
        }
    }

    /// Convert the edited UI value back into JSON for confirm_reply.
    var jsonValue: AnyJSON {
        switch kind {
        case .boolean:
            return .bool(boolean)
        case .integer, .number:
            let trimmed = text.trimmingCharacters(in: .whitespaces)
            return Double(trimmed).map(AnyJSON.number) ?? .string(trimmed)
        case .array:
            let parts = text
                .split(separator: ",")
                .map { $0.trimmingCharacters(in: .whitespaces) }
                .filter { !$0.isEmpty }
            return .array(parts.map { .string($0) })
        case .string, .other:
            return .string(text)
        }
    }
}

extension Binding where Value == EditableField {
    var textValue: Binding<String> {
        Binding<String>(
            get: { wrappedValue.text },
            set: { wrappedValue.text = $0 }
        )
    }

    var boolValue: Binding<Bool> {
        Binding<Bool>(
            get: { wrappedValue.boolean },
            set: { wrappedValue.boolean = $0 }
        )
    }
}

// MARK: - Humanizing helpers

enum Humanize {
    /// "calendar.create_event" -> "New Event"
    static func title(forTool name: String) -> String {
        let short = name.split(separator: ".").last.map(String.init) ?? name
        let words = short.split(separator: "_").map(String.init)
        guard let first = words.first else { return short }

        let rest = words.dropFirst().map(\.capitalized).joined(separator: " ")
        switch first {
        case "create", "new", "add":
            return rest.isEmpty ? "Create" : "New \(rest)"
        case "send":
            return rest.isEmpty ? "Send" : "Send \(rest)"
        case "delete", "remove":
            return rest.isEmpty ? "Delete" : "Delete \(rest)"
        case "move":
            return rest.isEmpty ? "Move" : "Move \(rest)"
        default:
            return words.map(\.capitalized).joined(separator: " ")
        }
    }

    /// "calendar.create_event" -> "Create"; default "Run".
    static func verb(forTool name: String) -> String {
        let short = name.split(separator: ".").last.map(String.init) ?? name
        guard let first = short.split(separator: "_").first else { return "Run" }
        switch first {
        case "create", "add", "new": return "Create"
        case "send": return "Send"
        case "move": return "Move"
        case "delete", "remove": return "Delete"
        case "update", "edit", "set": return "Update"
        case "open": return "Open"
        case "play": return "Play"
        case "compress": return "Compress"
        case "run": return "Run"
        default: return "Run"
        }
    }

    /// "start_time" -> "Start Time"
    static func label(_ key: String) -> String {
        key.split(separator: "_").map(\.capitalized).joined(separator: " ")
    }
}
