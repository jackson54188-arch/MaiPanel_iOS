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

    /// The game is a 9:16 portrait picture: a menu rectangle on top and the
    /// play disc pinned to the bottom edge. On a 2:3 iPad that frame is fitted
    /// by height, so its width sets how large the disc may get.
    private let gameAspect: CGFloat = 9.0 / 16.0
    /// Clearance between the outer ring and the screen edge.
    private let screenMargin: CGFloat = 6
    private let controlRowHeight: CGFloat = 50
    /// Distance from the top of the outer ring to the control row.
    private let controlRowGap: CGFloat = 14
    private let menuPanelHeight: CGFloat = 116

    @State private var menuOpen = false

    var body: some View {
        GeometryReader { geometry in
            let size = geometry.size
            let gameWidth = min(size.width, size.height * gameAspect)
            // The ring is the widest element, so it decides the disc radius;
            // the ring's bottom edge is what sits screenMargin above the
            // bottom, which leaves the disc itself a small gap above it.
            let radius = min(
                gameWidth * 0.485,
                (size.width / 2 - screenMargin) / OuterRing.outerScale
            )
            let ringOuter = radius * OuterRing.outerScale
            let center = CGPoint(x: size.width / 2, y: size.height - screenMargin - ringOuter)
            let rowBottom = center.y - ringOuter - controlRowGap
            let rowWidth = min(size.width - 24, 460)

            ZStack {
                Color.black.ignoresSafeArea()

                panel(size: radius * 2)
                    .frame(width: radius * 2, height: radius * 2)
                    .position(center)

                OuterButtonRing(
                    center: center,
                    panelRadius: radius,
                    pressed: model.buttons,
                    lights: model.showLights,
                    ledValid: model.led.valid,
                    ledColors: model.led.colors,
                    circleLevel: model.led.circle,
                    bodyLevel: model.led.body,
                    sideLevel: model.led.side
                )
                .frame(width: size.width, height: size.height)

                PanelTouchOverlay(center: center, panelRadius: radius) { points, buttons in
                    model.handleTouch(points)
                    model.handleButtons(buttons)
                }
                .frame(width: size.width, height: size.height)

                if menuOpen {
                    menuPanel
                        .frame(width: rowWidth, height: menuPanelHeight)
                        .position(
                            x: size.width / 2,
                            y: rowBottom - controlRowHeight - 8 - menuPanelHeight / 2
                        )
                }

                controlRow(width: rowWidth)
                    .frame(width: rowWidth, height: controlRowHeight)
                    .position(x: size.width / 2, y: rowBottom - controlRowHeight / 2)
            }
            .overlay(alignment: .topLeading) {
                status.padding(14)
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

    /// A bright body LED floods the area above the ring with white, so the
    /// controls sitting there have to swap to dark text.
    private var shellLit: Bool {
        model.showLights && model.led.valid && model.led.body > 0.35
    }

    private var controlTint: Color {
        shellLit ? Color.black.opacity(0.85) : Color.white.opacity(0.9)
    }

    private var controlFill: Color {
        shellLit ? Color.black.opacity(0.12) : Color.white.opacity(0.12)
    }

    /// Only the three keys used mid-credit keep a permanent button; everything
    /// else, including the setup toggles, lives one tap deeper in 菜单.
    private func controlRow(width: CGFloat) -> some View {
        HStack(spacing: 10) {
            systemKey("三角", key: "select")
            systemKey("刷卡", key: "card")
            Button {
                menuOpen.toggle()
            } label: {
                Text("菜单")
                    .font(.system(size: 18, weight: .semibold, design: .monospaced))
                    .frame(maxWidth: .infinity, minHeight: 50)
                    .background(
                        (menuOpen ? Color.white.opacity(0.85) : (shellLit ? Color.black.opacity(0.20) : Color.white.opacity(0.12))),
                        in: Capsule()
                    )
                    .foregroundStyle(menuOpen ? Color.black : controlTint)
            }
            .buttonStyle(.plain)
        }
        .frame(width: width)
    }

    private var menuPanel: some View {
        VStack(spacing: 8) {
            HStack(spacing: 8) {
                panelKey("TEST", key: "test")
                panelKey("SERVICE", key: "service")
                panelKey("投币", key: "coin")
            }
            HStack(spacing: 8) {
                panelChip("镜像", isOn: model.mirrored) { model.mirrored.toggle() }
                panelChip("旋转", isOn: model.rotated) { model.rotated.toggle() }
                panelChip("编号", isOn: model.showLabels) { model.showLabels.toggle() }
                panelChip("灯光", isOn: model.showLights) { model.showLights.toggle() }
                panelChip("范围 \(model.scopeLabel)", isOn: false) { model.cycleScope() }
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(
            shellLit ? Color.black.opacity(0.10) : Color.white.opacity(0.06),
            in: RoundedRectangle(cornerRadius: 18)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 18)
                .stroke(shellLit ? Color.black.opacity(0.15) : Color.white.opacity(0.12), lineWidth: 1)
        )
    }

    private func systemKey(_ title: String, key: String) -> some View {
        Button {
            model.tapSystem(key)
        } label: {
            Text(title)
                .font(.system(size: 18, weight: .semibold, design: .monospaced))
                .frame(maxWidth: .infinity, minHeight: 50)
                .background(controlFill, in: Capsule())
                .foregroundStyle(controlTint)
        }
        .buttonStyle(.plain)
    }

    private func panelKey(_ title: String, key: String) -> some View {
        Button {
            model.tapSystem(key)
        } label: {
            Text(title)
                .font(.system(size: 14, weight: .semibold, design: .monospaced))
                .frame(maxWidth: .infinity, minHeight: 44)
                .background(controlFill, in: Capsule())
                .foregroundStyle(controlTint)
        }
        .buttonStyle(.plain)
    }

    private func panelChip(_ title: String, isOn: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 13, weight: .medium, design: .monospaced))
                .frame(maxWidth: .infinity, minHeight: 44)
                .background(
                    (isOn ? Color.white.opacity(0.85) : controlFill),
                    in: Capsule()
                )
                .foregroundStyle(isOn ? Color.black : controlTint)
        }
        .buttonStyle(.plain)
    }

    private var status: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(model.connected ? "PC 已连接" : "PC 未连接")
                .foregroundStyle(shellLit ? Color.black.opacity(0.8)
                                          : (model.connected ? Color.green : Color.orange))
            Text("端口 \(panelServicePort)")
            Text(model.led.valid ? "灯光: 游戏" : "灯光: 无")
            Text(model.zones.isEmpty ? "触摸: 无" : "触摸: " + model.zones.sorted().joined(separator: " "))
            Text(model.pressedButtons.isEmpty
                 ? "外键: 无"
                 : "外键: " + model.pressedButtons.map(String.init).joined(separator: " "))
        }
        .font(.system(size: 12, weight: .medium, design: .monospaced))
        .foregroundStyle(controlTint.opacity(0.85))
        .allowsHitTesting(false)
    }
}

struct ContentView_Previews: PreviewProvider {
    static var previews: some View {
        ContentView()
    }
}
