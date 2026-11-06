import Foundation
import Network

/// TCP server the PC bridge dials through its usbmux forwarder (pc/forward.py).
final class PanelServer {
    private let queue = DispatchQueue(label: "maipanel.server")
    private var listener: NWListener?
    private var connection: NWConnection?
    private var lastMask: UInt64 = 0
    private var lastButtons: UInt8 = 0
    private var keepAlive: DispatchSourceTimer?
    private var inbox: [UInt8] = []

    var onConnected: (() -> Void)?
    var onDisconnected: (() -> Void)?

    func start(port: UInt16 = panelServicePort) {
        queue.async { [weak self] in
            guard let self else { return }
            do {
                let parameters = NWParameters.tcp
                parameters.allowLocalEndpointReuse = true
                let listener = try NWListener(using: parameters, on: NWEndpoint.Port(rawValue: port)!)
                listener.newConnectionHandler = { [weak self] connection in
                    self?.accept(connection)
                }
                listener.stateUpdateHandler = { state in
                    if case let .failed(error) = state {
                        NSLog("listener failed: \(error)")
                    }
                }
                listener.start(queue: self.queue)
                self.listener = listener
            } catch {
                NSLog("listener error: \(error)")
            }

            let timer = DispatchSource.makeTimerSource(queue: self.queue)
            timer.schedule(deadline: .now() + 1, repeating: 1)
            timer.setEventHandler { [weak self] in
                guard let self, let connection = self.connection else { return }
                self.send(encodeFrame(.touch, encodeTouch(self.lastMask)), on: connection)
                self.send(encodeFrame(.buttons, encodeButtons(self.lastButtons)), on: connection)
            }
            timer.resume()
            self.keepAlive = timer
        }
    }

    func sendTouch(mask: UInt64) {
        queue.async { [weak self] in
            guard let self else { return }
            self.lastMask = mask
            guard let connection = self.connection else { return }
            self.send(encodeFrame(.touch, encodeTouch(mask)), on: connection)
        }
    }

    func sendButtons(mask: UInt8) {
        queue.async { [weak self] in
            guard let self else { return }
            self.lastButtons = mask
            guard let connection = self.connection else { return }
            self.send(encodeFrame(.buttons, encodeButtons(mask)), on: connection)
        }
    }

    func sendSystem(_ key: SystemKey) {
        queue.async { [weak self] in
            guard let self, let connection = self.connection else { return }
            self.send(encodeFrame(.system, Data([key.id])), on: connection)
        }
    }

    private func accept(_ connection: NWConnection) {
        self.connection?.cancel()
        self.connection = connection
        inbox.removeAll()
        connection.stateUpdateHandler = { [weak self] state in
            guard let self else { return }
            switch state {
            case .ready:
                DispatchQueue.main.async { self.onConnected?() }
                self.send(encodeFrame(.touch, encodeTouch(self.lastMask)), on: connection)
                self.send(encodeFrame(.buttons, encodeButtons(self.lastButtons)), on: connection)
                self.receive(on: connection)
            case .failed, .cancelled:
                if self.connection === connection {
                    self.connection = nil
                    DispatchQueue.main.async { self.onDisconnected?() }
                }
            default:
                break
            }
        }
        connection.start(queue: queue)
    }

    private func receive(on connection: NWConnection) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 64 * 1024) { [weak self] data, _, isComplete, error in
            guard let self else { return }
            if let data, !data.isEmpty {
                self.handleIncoming(data)
            }
            if isComplete || error != nil {
                connection.cancel()
                return
            }
            self.receive(on: connection)
        }
    }

    /// TCP may split or coalesce frames, so keep a byte buffer and cut frames
    /// by their length field instead of assuming one callback equals one frame.
    /// Video frames land here once streaming exists; for now only pings are expected.
    private func handleIncoming(_ data: Data) {
        inbox.append(contentsOf: data)
        while inbox.count >= 8 {
            guard inbox[0] == 0x4D, inbox[1] == 0x50, inbox[2] == 0x4E, inbox[3] == 0x4C else {
                inbox.removeFirst()
                continue
            }
            let length = Int(inbox[6]) | (Int(inbox[7]) << 8)
            guard length <= 8192 else {
                inbox.removeAll()
                return
            }
            guard inbox.count >= 8 + length else { return }
            let type = inbox[5]
            inbox.removeFirst(8 + length)
            if type == FrameType.ping.rawValue, let connection = self.connection {
                send(encodeFrame(.pong, Data()), on: connection)
            }
        }
    }

    private func send(_ frame: Data, on connection: NWConnection) {
        connection.send(content: frame, completion: .contentProcessed { error in
            if let error {
                NSLog("send failed: \(error)")
            }
        })
    }
}
