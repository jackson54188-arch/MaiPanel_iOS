import SwiftUI
import UIKit

@MainActor
final class PanelModel: ObservableObject {
    @Published private(set) var zones: Set<String> = []
    @Published private(set) var fingers: [CGPoint] = []
    @Published private(set) var connected = false

    @Published var mirrored = false
    @Published var rotated = false
    @Published var showLabels = true
    @Published private(set) var scopeIndex = 1

    private static let scopes: [CGFloat] = [0.012, 0.02, 0.03]
    var scopeLabel: String { ["小", "中", "大"][scopeIndex] }

    func cycleScope() {
        scopeIndex = (scopeIndex + 1) % Self.scopes.count
        mask?.sampleScope = Self.scopes[scopeIndex]
    }

    let mask: ZoneMask?
    let maskImage: UIImage?
    let zoneCenters: [String: CGPoint]
    private let server = PanelServer()

    init() {
        let maskURL = Bundle.main.url(forResource: "panel_mask", withExtension: "png")
        mask = maskURL.flatMap { ZoneMask(url: $0) }
        maskImage = maskURL.flatMap { UIImage(contentsOfFile: $0.path) }
        zoneCenters = Self.loadCenters()

        server.onConnected = { [weak self] in
            Task { @MainActor in self?.connected = true }
        }
        server.onDisconnected = { [weak self] in
            Task { @MainActor in self?.connected = false }
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
    /// Panel diameter as a fraction of the shorter screen side. The rest of the
    /// screen is reserved for the cabinet frame / LED ring.
    private let panelScale: CGFloat = 0.78

    var body: some View {
        GeometryReader { geometry in
            let side = min(geometry.size.width, geometry.size.height) * panelScale
            ZStack {
                Color.black.ignoresSafeArea()

                panel(size: side)
                    .frame(width: side, height: side)
                    .position(x: geometry.size.width / 2, y: geometry.size.height / 2)
            }
            .overlay(alignment: .topLeading) {
                status.padding(14)
            }
            .overlay(alignment: .topTrailing) {
                toggles.padding(14)
            }
        }
        .statusBarHidden(true)
        .persistentSystemOverlays(.hidden)
        .onAppear { UIApplication.shared.isIdleTimerDisabled = true }
    }

    private func panel(size: CGFloat) -> some View {
        ZStack {
            if let image = model.maskImage {
                Image(uiImage: image)
                    .resizable()
                    .interpolation(.medium)
                    .opacity(0.55)
                    .allowsHitTesting(false)
            }

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

            MultiTouchOverlay { model.handleTouch($0) }
                .contentShape(Circle())
                .clipShape(Circle())
        }
    }

    private var status: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(model.connected ? "PC 已连接" : "PC 未连接")
                .foregroundStyle(model.connected ? Color.green : Color.orange)
            Text("端口 \(panelServicePort)")
            Text(model.zones.isEmpty ? "触摸: 无" : "触摸: " + model.zones.sorted().joined(separator: " "))
        }
        .font(.system(size: 12, weight: .medium, design: .monospaced))
        .foregroundStyle(Color.white.opacity(0.75))
        .allowsHitTesting(false)
    }

    private var toggles: some View {
        VStack(alignment: .trailing, spacing: 8) {
            chip("镜像", isOn: model.mirrored) { model.mirrored.toggle() }
            chip("旋转", isOn: model.rotated) { model.rotated.toggle() }
            chip("编号", isOn: model.showLabels) { model.showLabels.toggle() }
            chip("范围 \(model.scopeLabel)", isOn: false) { model.cycleScope() }
        }
    }

    private func chip(_ title: String, isOn: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 12, weight: .medium, design: .monospaced))
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
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
