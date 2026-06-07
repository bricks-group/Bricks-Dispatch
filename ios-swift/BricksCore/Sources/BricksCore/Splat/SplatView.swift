import SwiftUI
import MetalKit
import CoreMotion

/// SwiftUI view that renders a Gaussian Splat scene.
///
/// Usage:
/// ```swift
/// SplatView(file: splatFile)
///     .frame(maxWidth: .infinity, maxHeight: .infinity)
///     .ignoresSafeArea()
/// ```
public struct SplatView: UIViewRepresentable {
    public let file: SplatFile
    /// How strongly gyroscope tilt offsets the orbit camera (radians/g).
    public var gyroSensitivity: Float = 0.06

    public init(file: SplatFile, gyroSensitivity: Float = 0.06) {
        self.file = file
        self.gyroSensitivity = gyroSensitivity
    }

    public func makeCoordinator() -> Coordinator {
        Coordinator(file: file, gyroSensitivity: gyroSensitivity)
    }

    public func makeUIView(context: Context) -> MTKView {
        let view = MTKView(frame: .zero, device: context.coordinator.device)
        view.delegate            = context.coordinator.renderer
        view.clearColor          = MTLClearColor(red: 0, green: 0, blue: 0, alpha: 1)
        view.colorPixelFormat    = .bgra8Unorm
        view.framebufferOnly     = true
        view.isPaused            = false
        view.enableSetNeedsDisplay = false
        view.preferredFramesPerSecond = 60

        context.coordinator.attachGestures(to: view)
        return view
    }

    public func updateUIView(_ uiView: MTKView, context: Context) {}
}

// MARK: — Coordinator

public final class Coordinator: NSObject {

    let device: MTLDevice
    let renderer: SplatRenderer
    private let gyroSensitivity: Float
    private let motionManager = CMMotionManager()

    // Orbit state — touches drive theta/phi, pinch drives radius
    private var lastPanTranslation: CGPoint = .zero
    private var baseRadius: Float = 0

    init(file: SplatFile, gyroSensitivity: Float) {
        guard
            let dev = MTLCreateSystemDefaultDevice(),
            let rend = SplatRenderer(device: dev)
        else {
            fatalError("SplatView: Metal not available on this device")
        }
        self.device           = dev
        self.renderer         = rend
        self.gyroSensitivity  = gyroSensitivity
        super.init()
        rend.load(file)
        startGyro()
    }

    func attachGestures(to view: UIView) {
        let pan   = UIPanGestureRecognizer(target: self, action: #selector(handlePan(_:)))
        let pinch = UIPinchGestureRecognizer(target: self, action: #selector(handlePinch(_:)))
        pan.maximumNumberOfTouches = 1
        view.addGestureRecognizer(pan)
        view.addGestureRecognizer(pinch)
        view.isUserInteractionEnabled = true
    }

    // MARK: — Gestures

    @objc private func handlePan(_ g: UIPanGestureRecognizer) {
        let t = g.translation(in: g.view)
        if g.state == .began {
            lastPanTranslation = t
        }
        let dx = Float(t.x - lastPanTranslation.x)
        let dy = Float(t.y - lastPanTranslation.y)
        lastPanTranslation = t

        let sensitivity: Float = 0.005
        renderer.camera.theta -= dx * sensitivity
        renderer.camera.phi    = max(0.087, min(Float.pi - 0.087,
                                   renderer.camera.phi - dy * sensitivity))
    }

    @objc private func handlePinch(_ g: UIPinchGestureRecognizer) {
        if g.state == .began {
            baseRadius = renderer.camera.radius
        }
        renderer.camera.radius = max(0.5, min(50, baseRadius / Float(g.scale)))
    }

    // MARK: — Gyroscope parallax

    private func startGyro() {
        guard motionManager.isDeviceMotionAvailable else { return }
        motionManager.deviceMotionUpdateInterval = 1.0 / 60.0
        motionManager.startDeviceMotionUpdates(to: .main) { [weak self] motion, _ in
            guard let self, let m = motion else { return }
            // Small offset layered on top of orbit — not replacing it
            let dTheta = Float(m.gravity.x) * self.gyroSensitivity
            let dPhi   = Float(m.gravity.y) * self.gyroSensitivity * -1
            self.renderer.camera.theta += dTheta * 0.015
            self.renderer.camera.phi    = max(0.087, min(Float.pi - 0.087,
                                           self.renderer.camera.phi + dPhi * 0.015))
        }
    }

    deinit {
        motionManager.stopDeviceMotionUpdates()
    }
}

// MARK: — Preview wrapper

#if DEBUG
struct SplatViewPreview: View {
    @State private var file: SplatFile? = nil
    @State private var error: String?

    var body: some View {
        Group {
            if let f = file {
                SplatView(file: f)
                    .ignoresSafeArea()
            } else if let e = error {
                Text(e).foregroundStyle(.red).padding()
            } else {
                ProgressView("Loading splat…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Color.black)
            }
        }
        .onAppear { loadSample() }
    }

    private func loadSample() {
        guard let url = Bundle.module.url(forResource: "sample", withExtension: "splat") else {
            error = "No sample.splat in bundle — drop one in BricksCore/Resources to preview"
            return
        }
        do {
            file = try SplatFile(url: url)
        } catch {
            self.error = error.localizedDescription
        }
    }
}

#Preview {
    SplatViewPreview()
}
#endif
