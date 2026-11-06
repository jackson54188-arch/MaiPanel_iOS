import SwiftUI
import UIKit

/// Geometry shared by the ring drawing and the ring hit-test, so what is drawn
/// is exactly what responds.
enum OuterRing {
    /// Squarish cabinet blocks, larger than a D zone and pushed out until the
    /// ring almost touches the screen edge on the left and right.
    static let innerScale: CGFloat = 1.02
    static let outerScale: CGFloat = 1.35
    static let halfSpan: CGFloat = 7.5 * CGFloat.pi / 180
    /// Angular clearance between a button and the decorative filler beside it.
    static let gap: CGFloat = 2.0 * CGFloat.pi / 180
    /// Segment centres follow the A1...A8 zone angles: A1 top-right, clockwise.
    static let angles: [CGFloat] = [-67.1, -22.1, 22.2, 67.1, 112.9, 157.8, -157.8, -112.9]
        .map { $0 * CGFloat.pi / 180 }

    static func innerRadius(_ panelRadius: CGFloat) -> CGFloat { panelRadius * innerScale }
    static func outerRadius(_ panelRadius: CGFloat) -> CGFloat { panelRadius * outerScale }

    static func angleDelta(_ a: CGFloat, _ b: CGFloat) -> CGFloat {
        var delta = a - b
        while delta > .pi { delta -= 2 * .pi }
        while delta < -.pi { delta += 2 * .pi }
        return delta
    }

    static func button(at point: CGPoint, center: CGPoint, panelRadius: CGFloat) -> Int? {
        guard panelRadius > 0 else { return nil }
        let dx = point.x - center.x
        let dy = point.y - center.y
        let radius = hypot(dx, dy)
        let inner = innerRadius(panelRadius)
        let outer = outerRadius(panelRadius)
        // The disc takes precedence in PanelTouchView, so a little inward
        // slack here only helps fingers in the seam.
        let slack = (outer - inner) * 0.4
        guard radius >= inner - slack, radius <= outer + slack else { return nil }
        let angle = atan2(dy, dx)
        for index in angles.indices
        where abs(angleDelta(angle, angles[index])) <= halfSpan + 2 * CGFloat.pi / 180 {
            return index
        }
        return nil
    }

    /// Slot between button `index` and the next one, shrunk by `gap` on both
    /// sides so the filler piece does not touch either button.
    static func fillerSpan(_ index: Int) -> (center: CGFloat, half: CGFloat) {
        let next = (index + 1) % angles.count
        var delta = angles[next] - angles[index]
        while delta <= 0 { delta += 2 * .pi }
        return (angles[index] + delta / 2, delta / 2 - halfSpan - gap)
    }
}

/// One capture view for the whole pad: the disc zones and the ring buttons are
/// resolved from the same touches, so a finger can slide from a ring button
/// into the disc (or back) without being stuck to a single UIView.
struct PanelTouchOverlay: UIViewRepresentable {
    let center: CGPoint
    let panelRadius: CGFloat
    let onChange: ([CGPoint], UInt8) -> Void

    func makeUIView(context: Context) -> PanelTouchView {
        let view = PanelTouchView()
        view.isMultipleTouchEnabled = true
        view.backgroundColor = .clear
        view.onChange = onChange
        view.update(center: center, panelRadius: panelRadius)
        return view
    }

    func updateUIView(_ view: PanelTouchView, context: Context) {
        view.onChange = onChange
        view.update(center: center, panelRadius: panelRadius)
    }
}

final class PanelTouchView: UIView {
    var onChange: (([CGPoint], UInt8) -> Void)?
    private var padCenter: CGPoint = .zero
    private var panelRadius: CGFloat = 0

    func update(center: CGPoint, panelRadius: CGFloat) {
        padCenter = center
        self.panelRadius = panelRadius
    }

    /// The whole screen belongs to this view. SwiftUI chips live in overlays
    /// above it, so they still receive their own taps.
    override func point(inside point: CGPoint, with event: UIEvent?) -> Bool {
        true
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
        guard panelRadius > 0 else { return }
        var points: [CGPoint] = []
        var mask: UInt8 = 0
        for touch in event?.allTouches ?? touches {
            guard touch.phase != .ended, touch.phase != .cancelled else { continue }
            let location = touch.location(in: self)
            let dx = location.x - padCenter.x
            let dy = location.y - padCenter.y
            if hypot(dx, dy) <= panelRadius {
                points.append(CGPoint(
                    x: (dx / panelRadius + 1) / 2,
                    y: (dy / panelRadius + 1) / 2
                ))
            } else if let index = OuterRing.button(
                at: location, center: padCenter, panelRadius: panelRadius
            ) {
                mask |= UInt8(1 << index)
            }
        }
        onChange?(points, mask)
    }
}

/// The eight cabinet ring buttons plus the filler pieces between them. Drawing
/// only: touches are handled by `PanelTouchOverlay`.
struct OuterButtonRing: UIViewRepresentable {
    let center: CGPoint
    let panelRadius: CGFloat
    let pressed: UInt8
    let lights: Bool

    func makeUIView(context: Context) -> OuterButtonRingView {
        let view = OuterButtonRingView()
        view.backgroundColor = .clear
        view.isOpaque = false
        view.isUserInteractionEnabled = false
        view.update(center: center, panelRadius: panelRadius)
        view.pressed = pressed
        view.lights = lights
        return view
    }

    func updateUIView(_ view: OuterButtonRingView, context: Context) {
        view.update(center: center, panelRadius: panelRadius)
        view.pressed = pressed
        view.lights = lights
    }
}

final class OuterButtonRingView: UIView {
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

    private var padCenter: CGPoint = .zero
    private var panelRadius: CGFloat = 0

    private var innerRadius: CGFloat { OuterRing.innerRadius(panelRadius) }
    private var outerRadius: CGFloat { OuterRing.outerRadius(panelRadius) }
    private var thickness: CGFloat { outerRadius - innerRadius }

    func update(center: CGPoint, panelRadius: CGFloat) {
        padCenter = center
        self.panelRadius = panelRadius
        setNeedsDisplay()
    }

    private func point(radius: CGFloat, angle: CGFloat) -> CGPoint {
        CGPoint(x: padCenter.x + cos(angle) * radius, y: padCenter.y + sin(angle) * radius)
    }

    /// Sampled instead of `addArc` so the sweep direction never depends on the
    /// flipped UIKit coordinate system; the spans here are small either way.
    private func addArc(to path: UIBezierPath, radius: CGFloat, from start: CGFloat, to end: CGFloat) {
        let steps = 16
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

    private func segmentPath(center: CGFloat, half: CGFloat, grow: CGFloat = 0) -> UIBezierPath {
        let path = UIBezierPath()
        addArc(to: path, radius: innerRadius, from: center - half, to: center + half)
        addArc(to: path, radius: outerRadius + grow, from: center + half, to: center - half)
        path.close()
        return path
    }

    private func buttonPath(_ index: Int, grow: CGFloat = 0, anglePad: CGFloat = 0) -> UIBezierPath {
        segmentPath(center: OuterRing.angles[index], half: OuterRing.halfSpan + anglePad, grow: grow)
    }

    override func draw(_ rect: CGRect) {
        guard let context = UIGraphicsGetCurrentContext(), panelRadius > 0 else { return }
        let font = UIFont.monospacedDigitSystemFont(ofSize: max(12, thickness * 0.30), weight: .semibold)
        let midRadius = (innerRadius + outerRadius) / 2

        // The gaps between buttons are cabinet ring, not buttons: same arc
        // trapezoids but flat and dimmer, and they ignore touches.
        for index in OuterRing.angles.indices {
            let slot = OuterRing.fillerSpan(index)
            guard slot.half > 0 else { continue }
            let path = segmentPath(center: slot.center, half: slot.half)
            context.setFillColor(UIColor.white.withAlphaComponent(lights ? 0.18 : 0.05).cgColor)
            path.fill()
            context.setStrokeColor(UIColor.white.withAlphaComponent(lights ? 0.42 : 0.18).cgColor)
            context.setLineWidth(max(1, thickness * 0.05))
            path.stroke()
        }

        for index in 0..<8 {
            let on = (pressed & UInt8(1 << index)) != 0
            let path = buttonPath(index)

            if lights {
                // Backlight halo: four arc segments of falling alpha growing
                // outward and sideways, so the glow follows the segment shape.
                let base = on ? 0.10 : 0.04
                for step in stride(from: 4, through: 1, by: -1) {
                    let grow = thickness * 0.35 * CGFloat(step) / 4
                    let pad = OuterRing.halfSpan * 0.12 * CGFloat(step) / 4
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
            context.setLineWidth(max(1, thickness * 0.07))
            path.stroke()

            let text = "\(index + 1)" as NSString
            let attributes: [NSAttributedString.Key: Any] = [
                .font: font,
                .foregroundColor: on ? UIColor.black : UIColor.white.withAlphaComponent(0.85),
            ]
            let size = text.size(withAttributes: attributes)
            let anchor = point(radius: midRadius, angle: OuterRing.angles[index])
            text.draw(
                at: CGPoint(x: anchor.x - size.width / 2, y: anchor.y - size.height / 2),
                withAttributes: attributes
            )
        }
    }
}
