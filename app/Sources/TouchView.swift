import SwiftUI
import UIKit

/// Multi-touch capture. SwiftUI gestures only track one finger, so the panel is
/// driven by a plain UIView with every touch reported as a normalised point.
struct MultiTouchOverlay: UIViewRepresentable {
    let onChange: ([CGPoint]) -> Void

    func makeUIView(context: Context) -> TouchCaptureView {
        let view = TouchCaptureView()
        view.isMultipleTouchEnabled = true
        view.backgroundColor = .clear
        view.onChange = onChange
        return view
    }

    func updateUIView(_ view: TouchCaptureView, context: Context) {
        view.onChange = onChange
    }
}

final class TouchCaptureView: UIView {
    var onChange: (([CGPoint]) -> Void)?
    private var points: [ObjectIdentifier: CGPoint] = [:]

    /// The disc is drawn (and clipped) as a circle, so the square corners must
    /// not swallow touches meant for the outer buttons.
    override func point(inside point: CGPoint, with event: UIEvent?) -> Bool {
        let radius = min(bounds.width, bounds.height) / 2
        return hypot(point.x - bounds.midX, point.y - bounds.midY) <= radius
    }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        update(touches, event)
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        update(touches, event)
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) {
        update(touches, event)
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) {
        update(touches, event)
    }

    private func update(_ touches: Set<UITouch>, _ event: UIEvent?) {
        guard bounds.width > 0, bounds.height > 0 else { return }
        var live: [ObjectIdentifier: CGPoint] = [:]
        for touch in event?.allTouches ?? touches {
            guard touch.phase != .ended, touch.phase != .cancelled else { continue }
            let location = touch.location(in: self)
            live[ObjectIdentifier(touch)] = CGPoint(
                x: location.x / bounds.width,
                y: location.y / bounds.height
            )
        }
        points = live
        onChange?(Array(live.values))
    }
}

/// The eight cabinet ring buttons. Each one is an arc segment hugging the disc
/// (a trapezoid whose two bases are the inner and outer arcs), so the ring
/// reads as a continuation of the A zones. Touches are only claimed inside a
/// button, so the disc underneath keeps getting its own fingers.
struct OuterButtonPad: UIViewRepresentable {
    let center: CGPoint
    let panelRadius: CGFloat
    let pressed: UInt8
    let lights: Bool
    let onChange: (UInt8) -> Void

    func makeUIView(context: Context) -> OuterButtonPadView {
        let view = OuterButtonPadView()
        view.isMultipleTouchEnabled = true
        view.backgroundColor = .clear
        view.isOpaque = false
        view.onChange = onChange
        view.update(center: center, panelRadius: panelRadius)
        view.pressed = pressed
        view.lights = lights
        return view
    }

    func updateUIView(_ view: OuterButtonPadView, context: Context) {
        view.onChange = onChange
        view.update(center: center, panelRadius: panelRadius)
        view.pressed = pressed
        view.lights = lights
    }
}

final class OuterButtonPadView: UIView {
    var onChange: ((UInt8) -> Void)?
    var pressed: UInt8 = 0 {
        didSet {
            if pressed != oldValue { setNeedsDisplay() }
        }
    }
    var lights = true {
        didSet {
            if lights != oldValue { setNeedsDisplay() }
        }
    }

    /// The buttons sit just outside the disc and are as wide, in angle, as the
    /// A zones they extend (measured 28 degrees on the mask). The band is a bit
    /// thinner than the A ring because the screen has no more room.
    private static let innerScale: CGFloat = 1.03
    private static let outerScale: CGFloat = 1.35
    private static let halfSpan: CGFloat = 14.0 * CGFloat.pi / 180

    private var padCenter: CGPoint = .zero
    private var panelRadius: CGFloat = 0
    private var lastSent: UInt8 = 0

    private var innerRadius: CGFloat { panelRadius * Self.innerScale }
    private var outerRadius: CGFloat { panelRadius * Self.outerScale }
    private var thickness: CGFloat { outerRadius - innerRadius }

    /// Segment centres follow the A1...A8 zone angles so each button lines up
    /// with its zone: A1 top-right, going clockwise.
    private let angles: [CGFloat] = [-67.1, -22.1, 22.2, 67.1, 112.9, 157.8, -157.8, -112.9]
        .map { $0 * .pi / 180 }

    func update(center: CGPoint, panelRadius: CGFloat) {
        self.padCenter = center
        self.panelRadius = panelRadius
        setNeedsDisplay()
    }

    private func point(radius: CGFloat, angle: CGFloat) -> CGPoint {
        CGPoint(x: padCenter.x + cos(angle) * radius, y: padCenter.y + sin(angle) * radius)
    }

    /// Sampled instead of `addArc` so the sweep direction never depends on the
    /// flipped UIKit coordinate system; 28 degrees is smooth either way.
    private func addArc(to path: UIBezierPath, radius: CGFloat, from start: CGFloat, to end: CGFloat) {
        let steps = 24
        for step in 0...steps {
            let angle = start + (end - start) * CGFloat(step) / CGFloat(steps)
            let next = point(radius: radius, angle: angle)
            if path.isEmpty {
                path.move(to: next)
            } else {
                path.addLine(to: next)
            }
        }
    }

    private func buttonPath(_ index: Int, grow: CGFloat = 0, anglePad: CGFloat = 0) -> UIBezierPath {
        let start = angles[index] - Self.halfSpan - anglePad
        let end = angles[index] + Self.halfSpan + anglePad
        let path = UIBezierPath()
        addArc(to: path, radius: innerRadius, from: start, to: end)
        addArc(to: path, radius: outerRadius + grow, from: end, to: start)
        path.close()
        return path
    }

    override func point(inside point: CGPoint, with event: UIEvent?) -> Bool {
        guard panelRadius > 0 else { return false }
        return button(at: point) != nil
    }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        update(touches, event)
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        update(touches, event)
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) {
        update(touches, event)
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) {
        update(touches, event)
    }

    private func update(_ touches: Set<UITouch>, _ event: UIEvent?) {
        var live: [ObjectIdentifier: Int] = [:]
        for touch in event?.allTouches ?? touches {
            guard touch.phase != .ended, touch.phase != .cancelled else { continue }
            if let index = button(at: touch.location(in: self)) {
                live[ObjectIdentifier(touch)] = index
            }
        }
        var mask: UInt8 = 0
        for index in live.values {
            mask |= UInt8(1 << index)
        }
        pressed = mask
        guard mask != lastSent else { return }
        lastSent = mask
        onChange?(mask)
    }

    private func button(at point: CGPoint) -> Int? {
        let dx = point.x - padCenter.x
        let dy = point.y - padCenter.y
        let radius = hypot(dx, dy)
        // Extra reach only outward: growing inward would steal the outermost
        // pixels of the A zones, which live just inside the disc edge.
        let slack = thickness * 0.3
        guard radius >= innerRadius, radius <= outerRadius + slack else { return nil }
        let angle = atan2(dy, dx)
        for index in angles.indices
        where abs(angleDelta(angle, angles[index])) <= Self.halfSpan + 2 * CGFloat.pi / 180 {
            return index
        }
        return nil
    }

    private func angleDelta(_ a: CGFloat, _ b: CGFloat) -> CGFloat {
        var delta = a - b
        while delta > .pi { delta -= 2 * .pi }
        while delta < -.pi { delta += 2 * .pi }
        return delta
    }

    override func draw(_ rect: CGRect) {
        guard let context = UIGraphicsGetCurrentContext(), panelRadius > 0 else { return }
        let font = UIFont.monospacedDigitSystemFont(ofSize: max(12, thickness * 0.55), weight: .semibold)
        let midRadius = (innerRadius + outerRadius) / 2

        for index in 0..<8 {
            let on = (pressed & UInt8(1 << index)) != 0
            let path = buttonPath(index)

            if lights {
                // Backlight halo: four arc segments of falling alpha growing
                // outward and sideways, so the glow follows the segment shape.
                let base = on ? 0.10 : 0.04
                for step in stride(from: 4, through: 1, by: -1) {
                    let grow = thickness * 0.35 * CGFloat(step) / 4
                    let pad = Self.halfSpan * 0.12 * CGFloat(step) / 4
                    let alpha = base * CGFloat(5 - step) / 4
                    context.setFillColor(UIColor.white.withAlphaComponent(alpha).cgColor)
                    buttonPath(index, grow: grow, anglePad: pad).fill()
                }
                // Frosted acrylic body: white, semi-transparent when idle.
                context.setFillColor(
                    (on ? UIColor.white : UIColor.white.withAlphaComponent(0.30)).cgColor
                )
                path.fill()
                context.setStrokeColor(UIColor.white.withAlphaComponent(on ? 1.0 : 0.60).cgColor)
            } else {
                context.setFillColor(
                    (on ? UIColor.white.withAlphaComponent(0.85) : UIColor.clear).cgColor
                )
                path.fill()
                context.setStrokeColor(UIColor.white.withAlphaComponent(on ? 0.9 : 0.30).cgColor)
            }
            context.setLineWidth(max(1, thickness * 0.09))
            path.stroke()

            let text = "\(index + 1)" as NSString
            let attributes: [NSAttributedString.Key: Any] = [
                .font: font,
                .foregroundColor: on ? UIColor.black : UIColor.white.withAlphaComponent(0.85),
            ]
            let size = text.size(withAttributes: attributes)
            let anchor = point(radius: midRadius, angle: angles[index])
            text.draw(
                at: CGPoint(x: anchor.x - size.width / 2, y: anchor.y - size.height / 2),
                withAttributes: attributes
            )
        }
    }
}
