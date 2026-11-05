import CoreGraphics
import Foundation
import ImageIO

/// Hit-tests touch points against the 34 zone colour map.
///
/// The map is a flat PNG where every zone is filled with one unique RGB colour.
/// Sampling the colour under the finger (plus a ring of points around it) is how
/// the established PC implementations resolve zones, and it handles the irregular
/// wedge / square shapes without hand written geometry.
final class ZoneMask {
    struct Bitmap {
        let width: Int
        let height: Int
        let pixels: [UInt8]  // RGBA8, row major
    }

    let bitmap: Bitmap
    private let zoneOfColour: [UInt32: String]
    /// Sample ring radius as a fraction of the map's shorter side. iPad touch
    /// coordinates are exact, so this only needs to approximate finger width.
    var sampleScope: CGFloat = 0.02

    private static let colourTable: [String: String] = [
        "41-65-93": "A1", "87-152-13": "A2", "213-109-81": "A3", "23-222-55": "A4",
        "69-203-71": "A5", "147-253-55": "A6", "77-19-35": "A7", "159-109-79": "A8",
        "87-217-111": "B1", "149-95-154": "B2", "97-233-9": "B3", "159-27-222": "B4",
        "152-173-186": "B5", "192-185-149": "B6", "158-45-23": "B7", "197-158-219": "B8",
        "127-144-79": "C1", "242-41-155": "C2",
        "69-67-213": "D1", "105-25-130": "D2", "17-39-170": "D3", "97-103-203": "D4",
        "113-25-77": "D5", "21-21-140": "D6", "155-179-166": "D7", "55-181-134": "D8",
        "61-33-27": "E1", "51-91-95": "E2", "143-227-63": "E3", "216-67-226": "E4",
        "202-181-245": "E5", "99-11-183": "E6", "75-119-224": "E7", "182-19-85": "E8",
    ]

    init?(bitmap: Bitmap) {
        self.bitmap = bitmap
        var table: [UInt32: String] = [:]
        for (key, zone) in Self.colourTable {
            let parts = key.split(separator: "-").compactMap { UInt32($0) }
            guard parts.count == 3 else { continue }
            table[(parts[0] << 16) | (parts[1] << 8) | parts[2]] = zone
        }
        zoneOfColour = table
    }

    convenience init?(url: URL) {
        guard
            let source = CGImageSourceCreateWithURL(url as CFURL, nil),
            let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
        else { return nil }

        let width = image.width
        let height = image.height
        var pixels = [UInt8](repeating: 0, count: width * height * 4)
        let colourSpace = CGColorSpaceCreateDeviceRGB()
        let drawn: Bool = pixels.withUnsafeMutableBytes { buffer in
            guard
                let context = CGContext(
                    data: buffer.baseAddress,
                    width: width,
                    height: height,
                    bitsPerComponent: 8,
                    bytesPerRow: width * 4,
                    space: colourSpace,
                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
                )
            else { return false }
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard drawn else { return nil }
        self.init(bitmap: Bitmap(width: width, height: height, pixels: pixels))
    }

    /// `point` is normalised (0...1) inside the map rectangle.
    func zones(at point: CGPoint) -> Set<String> {
        var found: Set<String> = []
        let radius = sampleScope * CGFloat(min(bitmap.width, bitmap.height))
        // centre + 8 points on a circle, matching the reference implementation
        for index in 0..<9 {
            let angle = CGFloat(index) * (.pi / 4)
            let offset = index == 0 ? CGPoint.zero : CGPoint(x: cos(angle) * radius, y: sin(angle) * radius)
            let px = Int((point.x * CGFloat(bitmap.width)) + offset.x)
            let py = Int((point.y * CGFloat(bitmap.height)) + offset.y)
            if let zone = zone(atPixelX: px, y: py) {
                found.insert(zone)
            }
        }
        return found
    }

    private func zone(atPixelX x: Int, y: Int) -> String? {
        guard x >= 0, y >= 0, x < bitmap.width, y < bitmap.height else { return nil }
        let offset = (y * bitmap.width + x) * 4
        let colour = (UInt32(bitmap.pixels[offset]) << 16)
            | (UInt32(bitmap.pixels[offset + 1]) << 8)
            | UInt32(bitmap.pixels[offset + 2])
        return zoneOfColour[colour]
    }
}
