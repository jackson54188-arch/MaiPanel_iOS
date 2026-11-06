import SwiftUI
import UIKit

/// Everything the ring needs in one published value, so a 30 Hz LED stream
/// only triggers one SwiftUI update per frame.
struct LedRenderState {
    var valid = false
    var colors: [UIColor] = Array(repeating: .white, count: 8)
    var body: CGFloat = 0
    var circle: CGFloat = 0
    var side: CGFloat = 0
}

@MainActor
final class PanelModel: ObservableObject {
    @Published private(set) var zones: Set<String> = []
    @Published private(set) var fingers: [CGPoint] = []
    @Published private(set) var connected = false
    @Published private(set) var buttons: UInt8 = 0
    @Published private(set) var led = LedRenderState()

    @Published var mirrored = false
    @Published var rotated = false
    @Published var showLabels = true
    @Published var showLights = true
    @Published private(set) var scopeIndex = 1

    private static let scopes: [CGFloat] = [0.012, 0.02, 0.03]
    var scopeLabel: String { ["小", "中", "大"][scopeIndex] }
    var pressedButtons: [Int] {
        (0..<8).filter { (buttons & UInt8(1 << $0)) != 0 }.map { $0 + 1 }
    }

    func cycleScope() {
        scopeIndex = (scopeIndex + 1) % Self.scopes.count
        mask?.sampleScope = Self.scopes[scopeIndex]
    }

    let mask: ZoneMask?
    let zoneCenters: [String: CGPoint]
    private let server = PanelServer()
    private var lastLed: LedSnapshot?

    init() {
        let maskURL = Bundle.main.url(forResource: "panel_mask", withExtension: "png")
        mask = maskURL.flatMap { ZoneMask(url: $0) }
        zoneCenters = Self.loadCenters()

        server.onConnected = { [weak self] in
            Task { @MainActor in self?.connected = true }
        }
        server.onDisconnected = { [weak self] in
            Task { @MainActor in
                self?.connected = false
                self?.led.valid = false
            }
        }
        server.onLed = { [weak self] snapshot in
            Task { @MainActor in self?.applyLed(snapshot) }
        }
        server.start()
    }

    func handleTouch(_ points: [CGPoint]) {
        guard let mask else { return }
        var hits: Set<String> = []
        var mapped: [CGPoint] = []
        for point in points {
            var local = point
            if mirrored { local.x = 1 - local.x }
            if rotated {
                local.x = 1 - local.x
                local.y = 1 - local.y
            }
            mapped.append(local)
            hits.formUnion(mask.zones(at: CGPoint(x: local.x.clamped, y: local.y.clamped)))
        }
        fingers = mapped
        guard hits != zones else { return }
        zones = hits
        server.sendTouch(mask: zoneMask(of: hits))
    }

    func handleButtons(_ mask: UInt8) {
        guard mask != buttons else { return }
        buttons = mask
        server.sendButtons(mask: mask)
    }

    func tapSystem(_ key: String) {
        guard let key = SystemKey(rawValue: key) else { return }
        server.sendSystem(key)
    }

    private func applyLed(_ snapshot: LedSnapshot) {
        guard snapshot != lastLed else { return }
        lastLed = snapshot
        led = LedRenderState(
            valid: snapshot.valid,
            colors: stride(from: 0, to: snapshot.colors.count, by: 3).map { index in
                UIColor(
                    red: CGFloat(snapshot.colors[index]) / 255,
                    green: CGFloat(snapshot.colors[index + 1]) / 255,
                    blue: CGFloat(snapshot.colors[index + 2]) / 255,
                    alpha: 1
                )
            },
            body: CGFloat(snapshot.body) / 255,
            circle: CGFloat(snapshot.circle) / 255,
            side: CGFloat(snapshot.side) / 255
        )
    }

    private static func loadCenters() -> [String: CGPoint] {
        guard
            let url = Bundle.main.url(forResource: "zone_centers", withExtension: "json"),
            let data = try? Data(contentsOf: url),
            let raw = try? JSONSerialization.jsonObject(with: data) as? [String: [String: Double]]
        else { return [:] }
        var centers: [String: CGPoint] = [:]
        for (zone, point) in raw {
            if let x = point["x"], let y = point["y"] {
                centers[zone] = CGPoint(x: x, y: y)
            }
        }
        return centers
    }
}

private extension CGFloat {
    var clamped: CGFloat { Swift.min(Swift.max(self, 0), 1) }
}

struct ContentView: View {
    @StateObject private var model = PanelModel()
    /// Panel diameter as a fraction of the shorter side of the pad area (the
    /// screen minus the name plate and control bar strips).
    private let panelScale: CGFloat = 0.72
    /// Top strip kept clear for the cabinet's name plate, so the pad sits low
    /// like the real screen instead of being centred.
    private let namePlateFraction: CGFloat = 0.17
    /// Bottom strip reserved for the control bar, so the enlarged buttons never
    /// cover the outermost zones.
    private let controlBarHeight: CGFloat = 56

    var body: some View {
        GeometryReader { geometry in
            let width = geometry.size.width
            let height = geometry.size.height
            let top = max(84, height * namePlateFraction)
            let padArea = max(0, height - top - controlBarHeight)
            let side = min(width, padArea) * panelScale
            let center = CGPoint(x: width / 2, y: top + padArea / 2)
            ZStack {
                Color.black.ignoresSafeArea()

                panel(size: side)
                    .frame(width: side, height: side)
                    .position(center)

                OuterButtonRing(
                    center: center,
                    panelRadius: side / 2,
                    pressed: model.buttons,
                    lights: model.showLights,
                    ledValid: model.led.valid,
                    ledColors: model.led.colors,
                    circleLevel: model.led.circle,
                    bodyLevel: model.led.body,
                    sideLevel: model.led.side
                )
                .frame(width: width, height: height)

                PanelTouchOverlay(center: center, panelRadius: side / 2) { points, buttons in
                    model.handleTouch(points)
                    model.handleButtons(buttons)
                }
                .frame(width: width, height: height)
            }
            .overlay(alignment: .topLeading) {
                status.padding(14)
            }
            .overlay(alignment: .bottomLeading) {
                controlBar
                    .padding(.horizontal, 14)
                    .padding(.bottom, 10)
            }
        }
        .statusBarHidden(true)
        .persistentSystemOverlays(.hidden)
        .onAppear { UIApplication.shared.isIdleTimerDisabled = true }
    }

    private func panel(size: CGFloat) -> some View {
        ZStack {
            if model.showLabels {
                ForEach(zoneNames, id: \.self) { zone in
                    if let center = model.zoneCenters[zone] {
                        Text(zone)
                            .font(.system(size: max(11, size * 0.026), weight: .semibold, design: .monospaced))
                            .foregroundStyle(model.zones.contains(zone) ? Color.black : Color.white.opacity(0.8))
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(
                                model.zones.contains(zone) ? Color.white.opacity(0.9) : Color.clear,
                                in: Capsule()
                            )
                            .position(x: center.x * size, y: center.y * size)
                            .allowsHitTesting(false)
                    }
                }
            }

            ForEach(Array(model.fingers.enumerated()), id: \.offset) { item in
                Circle()
                    .fill(Color.white.opacity(0.35))
                    .frame(width: size * 0.055, height: size * 0.055)
                    .position(x: item.element.x * size, y: item.element.y * size)
                    .allowsHitTesting(false)
            }
        }
    }

    /// Cabinet keys and pad options share one bottom bar so every control is
    /// the same size and sits where the thumbs already are.
    private var controlBar: some View {
        HStack(spacing: 8) {
            systemKey("TEST", key: "test")
            systemKey("SERVICE", key: "service")
            systemKey("投币", key: "coin")
            systemKey("△ 三角", key: "select")
            Rectangle()
                .fill(Color.white.opacity(0.18))
                .frame(width: 1, height: 22)
                .padding(.horizontal, 4)
            chip("镜像", isOn: model.mirrored) { model.mirrored.toggle() }
            chip("旋转", isOn: model.rotated) { model.rotated.toggle() }
            chip("编号", isOn: model.showLabels) { model.showLabels.toggle() }
            chip("灯光", isOn: model.showLights) { model.showLights.toggle() }
            chip("范围 \(model.scopeLabel)", isOn: false) { model.cycleScope() }
        }
    }

    private func systemKey(_ title: String, key: String) -> some View {
        Button {
            model.tapSystem(key)
        } label: {
            Text(title)
                .font(.system(size: 14, weight: .medium, design: .monospaced))
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(Color.white.opacity(0.12), in: Capsule())
                .foregroundStyle(Color.white.opacity(0.8))
        }
        .buttonStyle(.plain)
    }

    private var status: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(model.connected ? "PC 已连接" : "PC 未连接")
                .foregroundStyle(model.connected ? Color.green : Color.orange)
            Text("端口 \(panelServicePort)")
            Text(model.led.valid ? "灯光: 游戏" : "灯光: 无")
            Text(model.zones.isEmpty ? "触摸: 无" : "触摸: " + model.zones.sorted().joined(separator: " "))
            Text(model.pressedButtons.isEmpty
                 ? "外键: 无"
                 : "外键: " + model.pressedButtons.map(String.init).joined(separator: " "))
        }
        .font(.system(size: 12, weight: .medium, design: .monospaced))
        .foregroundStyle(Color.white.opacity(0.75))
        .allowsHitTesting(false)
    }

    private func chip(_ title: String, isOn: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 14, weight: .medium, design: .monospaced))
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(isOn ? Color.white.opacity(0.85) : Color.white.opacity(0.12), in: Capsule())
                .foregroundStyle(isOn ? Color.black : Color.white.opacity(0.8))
        }
        .buttonStyle(.plain)
    }
}

struct ContentView_Previews: PreviewProvider {
    static var previews: some View {
        ContentView()
    }
}
