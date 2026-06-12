import Foundation

// MARK: - AnyJSON

/// Minimal JSON value type for arbitrary tool arguments / schemas.
enum AnyJSON: Codable, Equatable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case null
    case array([AnyJSON])
    case object([String: AnyJSON])

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let b = try? container.decode(Bool.self) {
            self = .bool(b)
        } else if let n = try? container.decode(Double.self) {
            self = .number(n)
        } else if let s = try? container.decode(String.self) {
            self = .string(s)
        } else if let a = try? container.decode([AnyJSON].self) {
            self = .array(a)
        } else if let o = try? container.decode([String: AnyJSON].self) {
            self = .object(o)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unsupported JSON value"
            )
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let s): try container.encode(s)
        case .number(let n): try container.encode(n)
        case .bool(let b): try container.encode(b)
        case .null: try container.encodeNil()
        case .array(let a): try container.encode(a)
        case .object(let o): try container.encode(o)
        }
    }

    /// Human-friendly rendering for editable fields / raw display.
    var displayString: String {
        switch self {
        case .string(let s): return s
        case .number(let n):
            return n == n.rounded() && abs(n) < 1e15
                ? String(Int64(n))
                : String(n)
        case .bool(let b): return b ? "true" : "false"
        case .null: return ""
        case .array(let a): return a.map(\.displayString).joined(separator: ", ")
        case .object(let o):
            let inner = o.keys.sorted()
                .map { "\($0): \(o[$0]!.displayString)" }
                .joined(separator: ", ")
            return "{\(inner)}"
        }
    }

    var boolValue: Bool? {
        if case .bool(let b) = self { return b }
        return nil
    }

    var objectValue: [String: AnyJSON]? {
        if case .object(let o) = self { return o }
        return nil
    }
}

// MARK: - Shared payloads

struct ToolCallPayload: Codable, Equatable {
    var name: String
    var arguments: [String: AnyJSON]

    /// "calendar.create_event" -> "calendar"
    var domain: String {
        name.split(separator: ".").first.map(String.init) ?? name
    }

    /// "calendar.create_event" -> "create_event"
    var shortName: String {
        name.split(separator: ".").dropFirst().joined(separator: ".")
            .ifEmpty(name)
    }
}

// MARK: - Server -> client

enum ServerMessage: Decodable {
    case status(state: DaemonState)
    case toolStarted(id: String, call: ToolCallPayload)
    case toolFinished(id: String, call: ToolCallPayload, status: String, hint: String?)
    case confirm(ConfirmRequest)
    case ask(AskRequest)
    case done(id: String, text: String?)
    case error(id: String?, message: String)
    case text(id: String, text: String)

    enum DaemonState: String, Decodable {
        case warming
        case ready
    }

    private enum CodingKeys: String, CodingKey {
        case type, state, id, call, status, hint, danger, schema
        case question, options, text, message
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let type = try c.decode(String.self, forKey: .type)
        switch type {
        case "status":
            self = .status(state: try c.decode(DaemonState.self, forKey: .state))
        case "tool_started":
            self = .toolStarted(
                id: try c.decode(String.self, forKey: .id),
                call: try c.decode(ToolCallPayload.self, forKey: .call)
            )
        case "tool_finished":
            self = .toolFinished(
                id: try c.decode(String.self, forKey: .id),
                call: try c.decode(ToolCallPayload.self, forKey: .call),
                status: try c.decode(String.self, forKey: .status),
                hint: try c.decodeIfPresent(String.self, forKey: .hint)
            )
        case "confirm":
            self = .confirm(ConfirmRequest(
                id: try c.decode(String.self, forKey: .id),
                call: try c.decode(ToolCallPayload.self, forKey: .call),
                danger: try c.decodeIfPresent(Bool.self, forKey: .danger) ?? false,
                schema: try c.decodeIfPresent(AnyJSON.self, forKey: .schema)
            ))
        case "ask":
            self = .ask(AskRequest(
                id: try c.decode(String.self, forKey: .id),
                question: try c.decode(String.self, forKey: .question),
                options: try c.decodeIfPresent([String].self, forKey: .options) ?? []
            ))
        case "done":
            self = .done(
                id: try c.decode(String.self, forKey: .id),
                text: try c.decodeIfPresent(String.self, forKey: .text)
            )
        case "error":
            self = .error(
                id: try c.decodeIfPresent(String.self, forKey: .id),
                message: try c.decode(String.self, forKey: .message)
            )
        case "text":
            self = .text(
                id: try c.decode(String.self, forKey: .id),
                text: try c.decode(String.self, forKey: .text)
            )
        default:
            throw DecodingError.dataCorruptedError(
                forKey: .type, in: c,
                debugDescription: "Unknown message type: \(type)"
            )
        }
    }
}

struct ConfirmRequest: Equatable {
    var id: String
    var call: ToolCallPayload
    var danger: Bool
    /// JSON schema for the tool's parameters (object with "properties"), if provided.
    var schema: AnyJSON?
}

struct AskRequest: Equatable {
    var id: String
    var question: String
    var options: [String]
}

// MARK: - Client -> server

enum ClientMessage: Encodable {
    case query(id: String, text: String)
    case confirmReply(id: String, approved: Bool, arguments: [String: AnyJSON]?)
    case askReply(id: String, answer: String)
    case reset
    case ping
    case cancel(id: String)

    private enum CodingKeys: String, CodingKey {
        case type, id, text, approved, arguments, answer
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        switch self {
        case .query(let id, let text):
            try c.encode("query", forKey: .type)
            try c.encode(id, forKey: .id)
            try c.encode(text, forKey: .text)
        case .confirmReply(let id, let approved, let arguments):
            try c.encode("confirm_reply", forKey: .type)
            try c.encode(id, forKey: .id)
            try c.encode(approved, forKey: .approved)
            try c.encodeIfPresent(arguments, forKey: .arguments)
        case .askReply(let id, let answer):
            try c.encode("ask_reply", forKey: .type)
            try c.encode(id, forKey: .id)
            try c.encode(answer, forKey: .answer)
        case .reset:
            try c.encode("reset", forKey: .type)
        case .ping:
            try c.encode("ping", forKey: .type)
        case .cancel(let id):
            try c.encode("cancel", forKey: .type)
            try c.encode(id, forKey: .id)
        }
    }
}

// MARK: - Transcript

enum TranscriptItem: Identifiable, Equatable {
    case user(id: UUID, text: String)
    case tool(id: String, call: ToolCallPayload, state: ToolState, hint: String?)
    case assistant(id: UUID, text: String)

    enum ToolState: Equatable {
        case running
        case done
        case failed
    }

    var id: String {
        switch self {
        case .user(let id, _): return "user-\(id.uuidString)"
        case .tool(let id, _, _, _): return "tool-\(id)"
        case .assistant(let id, _): return "assistant-\(id.uuidString)"
        }
    }
}

// MARK: - Helpers

extension String {
    func ifEmpty(_ fallback: String) -> String {
        isEmpty ? fallback : self
    }
}
