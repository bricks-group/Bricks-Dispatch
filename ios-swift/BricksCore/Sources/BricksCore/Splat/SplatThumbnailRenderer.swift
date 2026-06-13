import Metal
import MetalKit
import UIKit
import simd

/// Renders a single frame of a Gaussian splat scene to a UIImage.
/// Used to generate property thumbnails at delivery time — stored to disk and
/// used in list/grid views without spinning up a live MTKView.
public final class SplatThumbnailRenderer {

    public enum RenderError: Error {
        case noDevice
        case noPipeline
        case noTexture
        case noCommandBuffer
        case readbackFailed
    }

    // Singleton device — shared across thumbnail renders to avoid repeated
    // MTLCreateSystemDefaultDevice() calls (expensive on first call).
    private static let sharedDevice: MTLDevice? = MTLCreateSystemDefaultDevice()

    // MARK: — Public API

    /// Renders one frame of `file` from the given camera and returns a UIImage.
    /// Always called off the main thread; throws on Metal failure.
    public static func render(
        _ file: SplatFile,
        camera: OrbitCamera = defaultCamera(),
        size: CGSize = CGSize(width: 400, height: 300)
    ) async throws -> UIImage {
        guard let device = sharedDevice else { throw RenderError.noDevice }
        // Run on a background priority task — no UI interaction needed here
        return try await Task.detached(priority: .userInitiated) {
            try renderSync(file: file, camera: camera, size: size, device: device)
        }.value
    }

    // MARK: — Default camera

    /// A slightly elevated, diagonal orbit angle that works well for most real estate splats.
    public static func defaultCamera() -> OrbitCamera {
        var cam = OrbitCamera()
        cam.theta  = Float.pi / 5       // 36° horizontal
        cam.phi    = Float.pi / 3       // 60° from top
        cam.radius = 5
        return cam
    }

    // MARK: — Synchronous render (called from detached task)

    private static func renderSync(
        file: SplatFile,
        camera: OrbitCamera,
        size: CGSize,
        device: MTLDevice
    ) throws -> UIImage {
        let w = Int(size.width)
        let h = Int(size.height)

        // -----------------------------------------------------------------------
        // Pipeline
        // -----------------------------------------------------------------------
        guard
            let lib    = try? device.makeDefaultLibrary(bundle: .module),
            let vertFn = lib.makeFunction(name: "splat_vertex"),
            let fragFn = lib.makeFunction(name: "splat_fragment"),
            let queue  = device.makeCommandQueue()
        else { throw RenderError.noPipeline }

        let pipeDesc                        = MTLRenderPipelineDescriptor()
        pipeDesc.vertexFunction             = vertFn
        pipeDesc.fragmentFunction           = fragFn
        pipeDesc.colorAttachments[0].pixelFormat = .bgra8Unorm

        let att = pipeDesc.colorAttachments[0]!
        att.isBlendingEnabled           = true
        att.rgbBlendOperation           = .add
        att.alphaBlendOperation         = .add
        att.sourceRGBBlendFactor        = .one
        att.sourceAlphaBlendFactor      = .one
        att.destinationRGBBlendFactor   = .oneMinusSourceAlpha
        att.destinationAlphaBlendFactor = .oneMinusSourceAlpha

        guard let pipeline = try? device.makeRenderPipelineState(descriptor: pipeDesc)
        else { throw RenderError.noPipeline }

        // -----------------------------------------------------------------------
        // Offscreen texture
        // -----------------------------------------------------------------------
        let texDesc           = MTLTextureDescriptor.texture2DDescriptor(
            pixelFormat: .bgra8Unorm, width: w, height: h, mipmapped: false)
        texDesc.usage         = [.renderTarget, .shaderRead]
        texDesc.storageMode   = .shared  // CPU-readable on Apple Silicon & A-series
        guard let texture = device.makeTexture(descriptor: texDesc)
        else { throw RenderError.noTexture }

        // -----------------------------------------------------------------------
        // Splat buffer — CPU depth sort (back-to-front for painter's algorithm)
        // -----------------------------------------------------------------------
        let viewMat = camera.viewMatrix
        let sorted  = file.points.sorted {
            viewSpaceZ($0.position, view: viewMat) < viewSpaceZ($1.position, view: viewMat)
        }
        let byteCount = sorted.count * MemoryLayout<SplatPoint>.stride
        guard let splatBuf = device.makeBuffer(length: max(byteCount, 64), options: .storageModeShared)
        else { throw RenderError.noPipeline }
        sorted.withUnsafeBytes { splatBuf.contents().copyMemory(from: $0.baseAddress!, byteCount: byteCount) }

        // -----------------------------------------------------------------------
        // Uniforms
        // -----------------------------------------------------------------------
        let aspect = Float(w) / Float(h)
        var uni = SplatUniforms(
            viewProj: camera.projectionMatrix(aspect: aspect) * viewMat,
            view:     viewMat,
            focal:    camera.focal(viewportSize: size),
            viewport: SIMD2(Float(w), Float(h))
        )

        // -----------------------------------------------------------------------
        // Render pass
        // -----------------------------------------------------------------------
        let passDesc = MTLRenderPassDescriptor()
        passDesc.colorAttachments[0].texture    = texture
        passDesc.colorAttachments[0].loadAction  = .clear
        passDesc.colorAttachments[0].clearColor  = MTLClearColor(red: 0.04, green: 0.04, blue: 0.06, alpha: 1)
        passDesc.colorAttachments[0].storeAction = .store

        guard
            let cmdBuf  = queue.makeCommandBuffer(),
            let encoder = cmdBuf.makeRenderCommandEncoder(descriptor: passDesc)
        else { throw RenderError.noCommandBuffer }

        encoder.setRenderPipelineState(pipeline)
        encoder.setVertexBuffer(splatBuf, offset: 0, index: 0)
        encoder.setVertexBytes(&uni, length: MemoryLayout<SplatUniforms>.size, index: 1)
        encoder.drawPrimitives(type: .triangleStrip, vertexStart: 0, vertexCount: 4,
                               instanceCount: sorted.count)
        encoder.endEncoding()
        cmdBuf.commit()
        cmdBuf.waitUntilCompleted()

        // -----------------------------------------------------------------------
        // Readback
        // -----------------------------------------------------------------------
        // Align bytesPerRow to 256 — required on some devices
        let alignment   = 256
        let rawBPR      = w * 4
        let bytesPerRow = (rawBPR + alignment - 1) & ~(alignment - 1)
        var pixels      = [UInt8](repeating: 0, count: bytesPerRow * h)
        texture.getBytes(
            &pixels,
            bytesPerRow: bytesPerRow,
            from: MTLRegion(origin: MTLOrigin(x: 0, y: 0, z: 0),
                            size:   MTLSize(width: w, height: h, depth: 1)),
            mipmapLevel: 0
        )

        // BGRA8 with .byteOrder32Little + .premultipliedFirst
        let bitmapInfo = CGBitmapInfo(rawValue:
            CGBitmapInfo.byteOrder32Little.rawValue |
            CGImageAlphaInfo.premultipliedFirst.rawValue)
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        guard
            let provider = CGDataProvider(data: Data(pixels) as CFData),
            let cgImg    = CGImage(
                width:             w,
                height:            h,
                bitsPerComponent:  8,
                bitsPerPixel:      32,
                bytesPerRow:       bytesPerRow,
                space:             colorSpace,
                bitmapInfo:        bitmapInfo,
                provider:          provider,
                decode:            nil,
                shouldInterpolate: true,
                intent:            .defaultIntent)
        else { throw RenderError.readbackFailed }

        return UIImage(cgImage: cgImg)
    }

    private static func viewSpaceZ(_ pos: SIMD3<Float>, view: float4x4) -> Float {
        view.columns.2.x * pos.x + view.columns.2.y * pos.y
            + view.columns.2.z * pos.z + view.columns.2.w
    }
}

// MARK: — Disk cache

/// Manages JPEG thumbnails keyed by jobId.
/// Write from background; read from any thread.
public final class SplatThumbnailCache {

    public static let shared = SplatThumbnailCache()

    private let dir: URL = {
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        let dir = caches.appendingPathComponent("com.bricks.splat-thumbs", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }()

    private init() {}

    public func read(jobId: String) -> UIImage? {
        let url = path(for: jobId)
        guard let data = try? Data(contentsOf: url) else { return nil }
        return UIImage(data: data)
    }

    public func write(_ image: UIImage, jobId: String) {
        guard let data = image.jpegData(compressionQuality: 0.82) else { return }
        try? data.write(to: path(for: jobId))
    }

    public func exists(jobId: String) -> Bool {
        FileManager.default.fileExists(atPath: path(for: jobId).path)
    }

    private func path(for jobId: String) -> URL {
        dir.appendingPathComponent("\(jobId).jpg")
    }
}
