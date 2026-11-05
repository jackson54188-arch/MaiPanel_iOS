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
