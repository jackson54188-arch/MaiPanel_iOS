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

/// The eight cabinet ring buttons. Touches are only claimed inside a button
/// circle, so the disc underneath keeps getting its own fingers.
struct OuterButtonPad: UIViewRepresentable {
    let center: CGPoint
    let ringRadius: CGFloat
    let buttonRadius: CGFloat
    let pressed: UInt8
    let onChange: (UInt8) -> Void

    func makeUIView(context: Context) -> OuterButtonPadView {
        let view = OuterButtonPadView()
        view.isMultipleTouchEnabled = true
        view.backgroundColor = .clear
        view.isOpaque = false
        view.onChange = onChange
        view.update(center: center, ringRadius: ringRadius, buttonRadius: buttonRadius)
        view.pressed = pressed
        return view
    }

    func updateUIView(_ view: OuterButtonPadView, context: Context) {
        view.onChange = onChange
        view.update(center: center, ringRadius: ringRadius, buttonRadius: buttonRadius)
        view.pressed = pressed
    }
}

final class OuterButtonPadView: UIView {
    var onChange: ((UInt8) -> Void)?
    var pressed: UInt8 = 0 {
        didSet {
            if pressed != oldValue { setNeedsDisplay() }
        }
    }

    private var center: CGPoint = .zero
    private var ringRadius: CGFloat = 0
    private var buttonRadius: CGFloat = 0
    private var lastSent: UInt8 = 0

    /// Button centres follow the A1...A8 zone angles so the ring lines up with
    /// the panel artwork: A1 top-right, going clockwise.
    private let angles: [CGFloat] = [-67.1, -22.1, 22.2, 67.1, 112.9, 157.8, -157.8, -112.9]
        .map { $0 * .pi / 180 }

    func update(center: CGPoint, ringRadius: CGFloat, buttonRadius: CGFloat) {
        self.center = center
        self.ringRadius = ringRadius
        self.buttonRadius = buttonRadius
        setNeedsDisplay()
    }

    func buttonCenter(_ index: Int) -> CGPoint {
        let angle = angles[index]
        return CGPoint(
            x: center.x + cos(angle) * ringRadius,
            y: center.y + sin(angle) * ringRadius
        )
    }

    override func point(inside point: CGPoint, with event: UIEvent?) -> Bool {
        guard buttonRadius > 0 else { return false }
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
        let reach = buttonRadius * 1.3
        return (0..<8)
            .filter { distance(point, buttonCenter($0)) <= reach }
            .min(by: { distance(point, buttonCenter($0)) < distance(point, buttonCenter($1)) })
    }

    private func distance(_ a: CGPoint, _ b: CGPoint) -> CGFloat {
        hypot(a.x - b.x, a.y - b.y)
    }

    override func draw(_ rect: CGRect) {
        guard let context = UIGraphicsGetCurrentContext(), buttonRadius > 0 else { return }
        let font = UIFont.monospacedDigitSystemFont(ofSize: max(12, buttonRadius * 0.85), weight: .semibold)
        for index in 0..<8 {
            let center = buttonCenter(index)
            let box = CGRect(
                x: center.x - buttonRadius,
                y: center.y - buttonRadius,
                width: buttonRadius * 2,
                height: buttonRadius * 2
            )
            let on = (pressed & UInt8(1 << index)) != 0
            context.setFillColor(on ? UIColor.white.cgColor : UIColor.white.withAlphaComponent(0.10).cgColor)
            context.fillEllipse(in: box)
            context.setStrokeColor(UIColor.white.withAlphaComponent(on ? 0.9 : 0.45).cgColor)
            context.setLineWidth(max(1, buttonRadius * 0.06))
            context.strokeEllipse(in: box)

            let text = "\(index + 1)" as NSString
            let attributes: [NSAttributedString.Key: Any] = [
                .font: font,
                .foregroundColor: on ? UIColor.black : UIColor.white.withAlphaComponent(0.85),
            ]
            let size = text.size(withAttributes: attributes)
            text.draw(
                at: CGPoint(x: center.x - size.width / 2, y: center.y - size.height / 2),
                withAttributes: attributes
            )
        }
    }
}
