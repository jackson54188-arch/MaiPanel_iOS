import ImageIO
import SwiftUI
import UIKit

/// Decodes mirrored JPEG frames off the main thread and hands the pixels to a
/// layer. Routing them through @Published made every frame rebuild the whole
/// SwiftUI tree, which is where most of the lag came from; here the newest
/// frame wins and a slow decode simply drops the frames it missed.
final class VideoSink {
    static let shared = VideoSink()

    private let lock = NSLock()
    private let decodeQueue = DispatchQueue(label: "maipanel.video.decode", qos: .userInteractive)
    private var latest: Data?
    private var draining = false
    private weak var target: MirrorImageView?

    func attach(_ view: MirrorImageView) {
        lock.lock()
        target = view
        lock.unlock()
    }

    func push(_ data: Data) {
        lock.lock()
        latest = data
        let start = !draining
        draining = true
        lock.unlock()
        guard start else { return }
        decodeQueue.async { [self] in drain() }
    }

    func clear() {
        lock.lock()
        latest = nil
        lock.unlock()
        DispatchQueue.main.async { [weak self] in
            self?.target?.clear()
        }
    }

    private func drain() {
        while true {
            lock.lock()
            let data = latest
            latest = nil
            if data == nil { draining = false }
            let view = target
            lock.unlock()
            guard let data else { return }
            // Nothing to show it on (mirror off, view not built yet).
            guard view != nil else { continue }
            guard let image = Self.decode(data) else { continue }
            DispatchQueue.main.async {
                view?.show(image)
            }
        }
    }

    private static func decode(_ data: Data) -> CGImage? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
        return CGImageSourceCreateImageAtIndex(
            source, 0, [kCGImageSourceShouldCacheImmediately: true] as CFDictionary
        )
    }
}

/// The mirrored game picture, cut down to the two parts that are really on the
/// screen: the strip along the top and the round play area. The game paints
/// solid black filler in between, which used to cover the cabinet shell light.
final class MirrorImageView: UIView {
    /// Measured on the real screen, as fractions of the 9:16 game frame: the
    /// top strip ends at 21.1% and the round screen starts at 43.7%.
    static let stripFraction: CGFloat = 0.211
    /// Height over width of that strip crop, used to size the pink frame.
    static let stripAspect: CGFloat = stripFraction * 16 / 9
    private static let discCenterX: CGFloat = 0.4984
    private static let discCenterY: CGFloat = 0.719
    private static let discRadius: CGFloat = 0.2815

    private let stripLayer = CALayer()
    private let discLayer = CALayer()
    private var image: CGImage?
    /// Where the strip and the 9:16 frame go, in this view's coordinates. The
    /// strip is displayed wider than the 9:16 frame, so it cannot inherit the
    /// frame's scale; each gets its own crop-to-target mapping.
    private var stripTarget: CGRect = .zero
    private var videoTarget: CGRect = .zero

    override init(frame: CGRect) {
        super.init(frame: frame)
        backgroundColor = .clear
        isUserInteractionEnabled = false
        for layer in [stripLayer, discLayer] {
            layer.contentsGravity = .resize
            layer.magnificationFilter = .linear
            layer.minificationFilter = .linear
            self.layer.addSublayer(layer)
        }
        discLayer.masksToBounds = true
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    func setTargets(strip: CGRect, video: CGRect, corner: CGFloat) {
        stripTarget = strip
        videoTarget = video
        stripLayer.cornerRadius = corner
        stripLayer.masksToBounds = corner > 0
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        placeLayers()
        CATransaction.commit()
    }

    func show(_ image: CGImage) {
        self.image = image
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        stripLayer.contents = image.cropping(to: Self.stripRect(in: image))
        discLayer.contents = image.cropping(to: Self.discRect(in: image))
        placeLayers()
        CATransaction.commit()
    }

    func clear() {
        image = nil
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        stripLayer.contents = nil
        discLayer.contents = nil
        CATransaction.commit()
    }

    /// Integer pixel rects, so a crop never lands on a half pixel (which would
    /// soften the edge) and the point-size placement below matches exactly.
    private static func stripRect(in image: CGImage) -> CGRect {
        CGRect(x: 0, y: 0, width: CGFloat(image.width),
               height: (stripFraction * CGFloat(image.height)).rounded())
    }

    /// The round screen square, clamped so it never runs past the bottom edge.
    private static func discRect(in image: CGImage) -> CGRect {
        let height = CGFloat(image.height)
        let side = (2 * discRadius * height).rounded()
        let top = (discCenterY * height - side / 2).rounded()
            .clamped(low: 0, high: height - side)
        let left = (discCenterX * CGFloat(image.width) - side / 2).rounded()
            .clamped(low: 0, high: CGFloat(image.width) - side)
        return CGRect(x: left, y: top, width: side, height: side)
    }

    private func placeLayers() {
        stripLayer.frame = stripTarget
        guard let image, videoTarget.width > 0 else { return }
        let scale = videoTarget.width / CGFloat(image.width)
        let disc = Self.discRect(in: image)
        let frame = CGRect(
            x: videoTarget.minX + disc.minX * scale,
            y: videoTarget.minY + disc.minY * scale,
            width: disc.width * scale,
            height: disc.height * scale
        )
        discLayer.frame = frame
        discLayer.cornerRadius = frame.width / 2
    }
}

private extension CGFloat {
    func clamped(low: CGFloat, high: CGFloat) -> CGFloat {
        Swift.min(Swift.max(self, low), Swift.max(low, high))
    }
}

struct MirrorView: UIViewRepresentable {
    /// Strip and 9:16 frame rectangles, relative to the view's own bounds.
    let strip: CGRect
    let video: CGRect
    /// Strip corner radius, matched to the inside of the pink frame.
    let corner: CGFloat

    func makeUIView(context: Context) -> MirrorImageView {
        let view = MirrorImageView(frame: .zero)
        VideoSink.shared.attach(view)
        view.setTargets(strip: strip, video: video, corner: corner)
        return view
    }

    func updateUIView(_ view: MirrorImageView, context: Context) {
        view.setTargets(strip: strip, video: video, corner: corner)
    }
}
