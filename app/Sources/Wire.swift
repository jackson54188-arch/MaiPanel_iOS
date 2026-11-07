import Foundation

/// Zone bit order must match the game's touch panel protocol:
/// A1..A8, B1..B8, C1, C2, D1..D8, E1..E8 (34 zones).
let zoneNames: [String] = {
    var names: [String] = []
    names += (1...8).map { "A\($0)" }
    names += (1...8).map { "B\($0)" }
    names += ["C1", "C2"]
    names += (1...8).map { "D\($0)" }
    names += (1...8).map { "E\($0)" }
    return names
}()

enum FrameType: UInt8 {
    case touch = 0x01
    case ping = 0x02
    case pong = 0x03
    case buttons = 0x04
    case system = 0x05
    case led = 0x06
    case control = 0x07
    case video = 0x10
}

/// Control payload is [command, value]; 1/1 asks the PC to mirror the game
/// window, 1/0 stops it.
func encodeVideoControl(_ on: Bool) -> Data {
    encodeFrame(.control, Data([1, on ? 1 : 0]))
}

/// Command 2 reports which slot the app is set to (1 or 2), so the bridge can
/// warn when an iPad is on the wrong port.
func encodeSlotControl(_ slot: Int) -> Data {
    encodeFrame(.control, Data([2, UInt8(max(1, min(2, slot)))]))
}

/// Game LED state relayed from the PC mod: 8 button colours plus the cabinet
/// body/circle/side brightness bytes.
struct LedSnapshot: Equatable {
    let valid: Bool
    let colors: [UInt8]  // 24 bytes, RGB per button 1...8
    let body: UInt8
    let circle: UInt8
    let side: UInt8

    init?(payload: [UInt8]) {
        guard payload.count >= 28 else { return nil }
        valid = payload[0] & 1 != 0
        colors = Array(payload[1..<25])
        body = payload[25]
        circle = payload[26]
        side = payload[27]
    }
}

/// Momentary cabinet keys; the ids are mirrored by pc/bridge.py.
enum SystemKey: String {
    case test
    case service
    case coin
    case select
    case card
    case close

    var id: UInt8 {
        switch self {
        case .test: return 1
        case .service: return 2
        case .coin: return 3
        case .select: return 4
        case .card: return 5
        case .close: return 6
        }
    }
}

let protocolVersion: UInt8 = 1

/// One PC with two iPads: 1P listens on 24870, 2P on 24880. The bridge runs
/// one usbmux forward per port, so each iPad pairs itself by slot.
let panelServicePort1P: UInt16 = 24870
let panelServicePort2P: UInt16 = 24880
let panelServicePort: UInt16 = panelServicePort1P

func panelPort(for slot: Int) -> UInt16 {
    slot == 2 ? panelServicePort2P : panelServicePort1P
}

/// Wire format, little endian:
///   "MPNL" | version:u8 | type:u8 | length:u16 | payload
func encodeFrame(_ type: FrameType, _ payload: Data) -> Data {
    var frame = Data("MPNL".utf8)
    frame.append(protocolVersion)
    frame.append(type.rawValue)
    var length = UInt16(payload.count).littleEndian
    withUnsafeBytes(of: &length) { frame.append(contentsOf: $0) }
    frame.append(payload)
    return frame
}

/// Touch payload is the 34 bit zone mask packed into 5 bytes, little endian.
func encodeTouch(_ mask: UInt64) -> Data {
    var value = mask.littleEndian
    return withUnsafeBytes(of: &value) { Data($0.prefix(5)) }
}

/// Outer buttons 1...8, bit 0 = button 1. One byte is enough.
func encodeButtons(_ mask: UInt8) -> Data {
    Data([mask])
}

func zoneMask(of zones: Set<String>) -> UInt64 {
    var mask: UInt64 = 0
    for zone in zones {
        if let index = zoneNames.firstIndex(of: zone) {
            mask |= 1 << UInt64(index)
        }
    }
    return mask
}
