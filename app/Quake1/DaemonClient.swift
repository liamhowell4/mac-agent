import Foundation
import Network

/// NDJSON client over a Unix domain socket to the Python daemon.
@MainActor
final class DaemonClient: ObservableObject {

    enum State: Equatable {
        case idle
        case warming
        case thinking
        case runningTool(String)
        case confirming(ConfirmRequest)
        case asking(AskRequest)
        case error(String)
    }

    @Published private(set) var state: State = .idle
    @Published private(set) var transcript: [TranscriptItem] = []
    @Published private(set) var isConnected = false

    /// Called when a connection attempt fails — AppDelegate uses it to
    /// spawn the daemon process.
    var onConnectFailure: (@MainActor () -> Void)?

    private var connection: NWConnection?
    private var receiveBuffer = Data()
    private var reconnectAttempt = 0
    private var reconnectTask: Task<Void, Never>?
    private var intentionallyDisconnected = false

    /// The id of the in-flight query, used for cancel().
    private var currentQueryID: String?

    static var socketPath: String {
        let appSupport = FileManager.default.urls(
            for: .applicationSupportDirectory, in: .userDomainMask
        ).first!
        return appSupport
            .appendingPathComponent("Quake1/quake1.sock")
            .path
    }

    // MARK: - Connection lifecycle

    func connect() {
        intentionallyDisconnected = false
        reconnectTask?.cancel()
        reconnectTask = nil
        openConnection()
    }

    func disconnect() {
        intentionallyDisconnected = true
        reconnectTask?.cancel()
        reconnectTask = nil
        connection?.cancel()
        connection = nil
        isConnected = false
    }

    private func openConnection() {
        connection?.cancel()
        receiveBuffer.removeAll()

        let endpoint = NWEndpoint.unix(path: Self.socketPath)
        let conn = NWConnection(to: endpoint, using: .tcp)
        connection = conn

        conn.stateUpdateHandler = { [weak self] newState in
            Task { @MainActor [weak self] in
                self?.handleConnectionState(newState)
            }
        }
        conn.start(queue: .main)
        receiveLoop(on: conn)
    }

    private func handleConnectionState(_ newState: NWConnection.State) {
        switch newState {
        case .ready:
            isConnected = true
            reconnectAttempt = 0
            send(.ping)
        case .failed, .cancelled:
            isConnected = false
            scheduleReconnect()
        case .waiting:
            isConnected = false
        default:
            break
        }
    }

    private func scheduleReconnect() {
        guard !intentionallyDisconnected, reconnectTask == nil else { return }
        if reconnectAttempt == 0 {
            onConnectFailure?()
        }
        reconnectAttempt += 1
        let delay = min(pow(2.0, Double(reconnectAttempt - 1)) * 0.5, 8.0)
        reconnectTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard let self, !Task.isCancelled else { return }
            self.reconnectTask = nil
            self.openConnection()
        }
    }

    // MARK: - Receive / NDJSON framing

    private func receiveLoop(on conn: NWConnection) {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 65536) {
            [weak self] data, _, isComplete, error in
            Task { @MainActor [weak self] in
                guard let self else { return }
                if let data, !data.isEmpty {
                    self.receiveBuffer.append(data)
                    self.drainBuffer()
                }
                if error != nil || isComplete {
                    self.isConnected = false
                    self.scheduleReconnect()
                    return
                }
                self.receiveLoop(on: conn)
            }
        }
    }

    private func drainBuffer() {
        while let newlineIndex = receiveBuffer.firstIndex(of: UInt8(ascii: "\n")) {
            let lineData = receiveBuffer[receiveBuffer.startIndex..<newlineIndex]
            receiveBuffer.removeSubrange(receiveBuffer.startIndex...newlineIndex)
            guard !lineData.isEmpty else { continue }
            do {
                let message = try JSONDecoder().decode(ServerMessage.self, from: Data(lineData))
                handle(message)
            } catch {
                NSLog("Quake1: failed to decode daemon line: \(error)")
            }
        }
    }

    // MARK: - Message handling

    private func handle(_ message: ServerMessage) {
        switch message {
        case .status(let daemonState):
            switch daemonState {
            case .warming: state = .warming
            case .ready: if state == .warming { state = .idle }
            }

        case .toolStarted(let id, let call):
            upsertTool(id: id, call: call, state: .running, hint: nil)
            state = .runningTool(call.name)

        case .toolFinished(let id, let call, let status, let hint):
            let toolState: TranscriptItem.ToolState =
                (status == "ok" || status == "success" || status == "done") ? .done : .failed
            upsertTool(id: id, call: call, state: toolState, hint: hint)
            state = .thinking

        case .confirm(let request):
            state = .confirming(request)

        case .ask(let request):
            state = .asking(request)

        case .text(_, let text):
            transcript.append(.assistant(id: UUID(), text: text))

        case .done(_, let text):
            if let text, !text.isEmpty {
                transcript.append(.assistant(id: UUID(), text: text))
            }
            currentQueryID = nil
            state = .idle

        case .error(_, let message):
            currentQueryID = nil
            state = .error(message)
        }
    }

    private func upsertTool(
        id: String,
        call: ToolCallPayload,
        state toolState: TranscriptItem.ToolState,
        hint: String?
    ) {
        let item = TranscriptItem.tool(id: id, call: call, state: toolState, hint: hint)
        if let index = transcript.firstIndex(where: { existing in
            if case .tool(let existingID, _, _, _) = existing { return existingID == id }
            return false
        }) {
            transcript[index] = item
        } else {
            transcript.append(item)
        }
    }

    // MARK: - Outbound API

    func send(query: String) {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        let id = UUID().uuidString
        currentQueryID = id
        transcript.append(.user(id: UUID(), text: trimmed))
        state = .thinking
        send(.query(id: id, text: trimmed))
    }

    func reply(approved: Bool, editedArguments: [String: AnyJSON]? = nil) {
        guard case .confirming(let request) = state else { return }
        send(.confirmReply(
            id: request.id,
            approved: approved,
            arguments: approved ? editedArguments : nil
        ))
        state = approved ? .thinking : .idle
    }

    func reply(answer: String) {
        guard case .asking(let request) = state else { return }
        send(.askReply(id: request.id, answer: answer))
        state = .thinking
    }

    func reset() {
        send(.reset)
        transcript.removeAll()
        currentQueryID = nil
        state = .idle
    }

    func cancel() {
        if let id = currentQueryID {
            send(.cancel(id: id))
        }
        currentQueryID = nil
        state = .idle
    }

    /// Clear an error banner without resetting the conversation.
    func dismissError() {
        if case .error = state { state = .idle }
    }

    private func send(_ message: ClientMessage) {
        guard let connection else { return }
        do {
            var data = try JSONEncoder().encode(message)
            data.append(UInt8(ascii: "\n"))
            connection.send(content: data, completion: .contentProcessed { error in
                if let error {
                    NSLog("Quake1: send failed: \(error)")
                }
            })
        } catch {
            NSLog("Quake1: encode failed: \(error)")
        }
    }
}
