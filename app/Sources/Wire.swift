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
    case video = 0x10
}

let protocolVersion: UInt8 = 1
let panelServicePort: UInt16 = 24870

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
