import Metal
import MetalKit
import simd

/// Uniforms struct mirroring the Metal shader layout (must be identical).
struct SplatUniforms {
    var viewProj: float4x4
    var view:     float4x4
    var focal:    SIMD2<Float>
    var viewport: SIMD2<Float>
}

/// Orbit camera driven by gesture recognizers in SplatView.
public struct OrbitCamera {
    /// Horizontal rotation angle in radians.
    public var theta: Float = 0
    /// Vertical elevation angle in radians (0 = top, π = bottom). Clamped 5°–175°.
    public var phi: Float = Float.pi / 3
    /// Distance from target.
    public var radius: Float = 5
    /// World-space target (look-at point).
    public var target: SIMD3<Float> = .zero
    /// Field of view in radians.
    public var fovY: Float = Float.pi / 4

    public init() {}

    /// View matrix (world → camera).
    public var viewMatrix: float4x4 {
        let eye = eyePosition
        return lookAt(eye: eye, center: target, up: SIMD3(0, 1, 0))
    }

    public var eyePosition: SIMD3<Float> {
        let sinPhi = sin(phi)
        let cosPhi = cos(phi)
        let sinTht = sin(theta)
        let cosTht = cos(theta)
        return target + radius * SIMD3(sinPhi * sinTht, cosPhi, sinPhi * cosTht)
    }

    public func projectionMatrix(aspect: Float) -> float4x4 {
        perspectiveFov(fovY: fovY, aspect: aspect, near: 0.05, far: 200)
    }

    /// Focal lengths in pixels for a given viewport size.
    public func focal(viewportSize: CGSize) -> SIMD2<Float> {
        let h = Float(viewportSize.height)
        let fy = h / (2.0 * tan(fovY * 0.5))
        let fx = fy * Float(viewportSize.width / viewportSize.height)
        return SIMD2(fx, fy)
    }
}

// MARK: — Metal renderer

public final class SplatRenderer: NSObject, MTKViewDelegate {

    // MARK: State

    private let device: MTLDevice
    private let commandQueue: MTLCommandQueue
    private var pipelineState: MTLRenderPipelineState?
    private var splatBuffer: MTLBuffer?
    private var splatCount = 0

    private var sortedSplats: [SplatPoint] = []
    private var sortWorkItem: DispatchWorkItem?
    private let sortQueue = DispatchQueue(label: "com.bricks.splat.sort", qos: .userInteractive)

    public var camera = OrbitCamera()

    // MARK: Init

    public init?(device: MTLDevice) {
        self.device = device
        guard let q = device.makeCommandQueue() else { return nil }
        self.commandQueue = q
        super.init()
    }

    // MARK: Load splats

    public func load(_ file: SplatFile) {
        sortedSplats = file.points
        splatCount = file.points.count
        uploadSortedBuffer()
    }

    // MARK: MTKViewDelegate

    public func mtkView(_ view: MTKView, drawableSizeWillChange size: CGSize) {
        buildPipeline(view: view)
    }

    public func draw(in view: MTKView) {
        guard splatCount > 0 else { return }
        sortAndDraw(view: view)
    }

    // MARK: Pipeline

    private func buildPipeline(view: MTKView) {
        guard pipelineState == nil else { return }

        guard
            let lib     = try? device.makeDefaultLibrary(bundle: .module),
            let vertFn  = lib.makeFunction(name: "splat_vertex"),
            let fragFn  = lib.makeFunction(name: "splat_fragment")
        else {
            assertionFailure("SplatRenderer: failed to load Metal shaders from BricksCore bundle")
            return
        }

        let desc                        = MTLRenderPipelineDescriptor()
        desc.vertexFunction             = vertFn
        desc.fragmentFunction           = fragFn
        desc.colorAttachments[0].pixelFormat = view.colorPixelFormat

        // Pre-multiplied alpha blending
        let att = desc.colorAttachments[0]!
        att.isBlendingEnabled             = true
        att.rgbBlendOperation             = .add
        att.alphaBlendOperation           = .add
        att.sourceRGBBlendFactor          = .one
        att.sourceAlphaBlendFactor        = .one
        att.destinationRGBBlendFactor     = .oneMinusSourceAlpha
        att.destinationAlphaBlendFactor   = .oneMinusSourceAlpha

        pipelineState = try? device.makeRenderPipelineState(descriptor: desc)
    }

    // MARK: Sort + draw

    private func sortAndDraw(view: MTKView) {
        let viewMatrix = camera.viewMatrix
        // Kick off background sort (double-buffered — draw uses previous frame's result)
        sortWorkItem?.cancel()
        let currentSplats = sortedSplats
        let workItem = DispatchWorkItem { [weak self] in
            guard let self else { return }
            // View-space z of each splat (negative = in front of camera)
            let sorted = currentSplats.sorted {
                let az = self.viewZ($0.position, view: viewMatrix)
                let bz = self.viewZ($1.position, view: viewMatrix)
                return az < bz   // back-to-front: most negative z last
            }
            DispatchQueue.main.async {
                self.sortedSplats = sorted
                self.uploadSortedBuffer()
                view.setNeedsDisplay()
            }
        }
        sortWorkItem = workItem
        sortQueue.async(execute: workItem)

        // Draw whatever is currently in the buffer
        render(view: view)
    }

    private func viewZ(_ pos: SIMD3<Float>, view: float4x4) -> Float {
        // z component of view * (pos, 1)
        return view.columns.2.x * pos.x
             + view.columns.2.y * pos.y
             + view.columns.2.z * pos.z
             + view.columns.2.w
    }

    private func uploadSortedBuffer() {
        guard !sortedSplats.isEmpty else { return }
        let byteCount = sortedSplats.count * MemoryLayout<SplatPoint>.stride
        if splatBuffer == nil || splatBuffer!.length < byteCount {
            splatBuffer = device.makeBuffer(length: byteCount, options: .storageModeShared)
        }
        guard let buf = splatBuffer else { return }
        sortedSplats.withUnsafeBytes {
            buf.contents().copyMemory(from: $0.baseAddress!, byteCount: byteCount)
        }
        splatCount = sortedSplats.count
    }

    private func render(view: MTKView) {
        guard
            let ps      = pipelineState,
            let buf     = splatBuffer,
            let cmdBuf  = commandQueue.makeCommandBuffer(),
            let desc    = view.currentRenderPassDescriptor,
            let encoder = cmdBuf.makeRenderCommandEncoder(descriptor: desc)
        else { return }

        let size   = view.drawableSize
        let aspect = Float(size.width / size.height)
        var uni    = SplatUniforms(
            viewProj: camera.projectionMatrix(aspect: aspect) * camera.viewMatrix,
            view:     camera.viewMatrix,
            focal:    camera.focal(viewportSize: size),
            viewport: SIMD2(Float(size.width), Float(size.height))
        )

        encoder.setRenderPipelineState(ps)
        encoder.setVertexBuffer(buf, offset: 0, index: 0)
        encoder.setVertexBytes(&uni, length: MemoryLayout<SplatUniforms>.size, index: 1)
        encoder.drawPrimitives(
            type:          .triangleStrip,
            vertexStart:   0,
            vertexCount:   4,
            instanceCount: splatCount
        )
        encoder.endEncoding()

        if let drawable = view.currentDrawable {
            cmdBuf.present(drawable)
        }
        cmdBuf.commit()
    }
}

// MARK: — Math helpers

private func lookAt(eye: SIMD3<Float>, center: SIMD3<Float>, up: SIMD3<Float>) -> float4x4 {
    let f = normalize(center - eye)
    let r = normalize(cross(f, up))
    let u = cross(r, f)
    return float4x4(columns: (
        SIMD4(r.x, u.x, -f.x, 0),
        SIMD4(r.y, u.y, -f.y, 0),
        SIMD4(r.z, u.z, -f.z, 0),
        SIMD4(-dot(r, eye), -dot(u, eye), dot(f, eye), 1)
    ))
}

private func perspectiveFov(fovY: Float, aspect: Float, near: Float, far: Float) -> float4x4 {
    let ys = 1 / tan(fovY * 0.5)
    let xs = ys / aspect
    let zs = far / (near - far)
    return float4x4(columns: (
        SIMD4(xs,  0,       0,  0),
        SIMD4( 0, ys,       0,  0),
        SIMD4( 0,  0,      zs, -1),
        SIMD4( 0,  0, zs*near,  0)
    ))
}
