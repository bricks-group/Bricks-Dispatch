import Foundation
import simd

/// One 3D Gaussian splat as decoded from the .splat binary format.
/// Layout matches the 32-byte per-point encoding used by the 3DGS reference implementation.
public struct SplatPoint: Sendable {
    /// World-space center (x, y, z)
    public var position: SIMD3<Float>
    /// Log-scale — actual axis radii are exp(logScale)
    public var logScale: SIMD3<Float>
    /// RGBA color, each component 0–255
    public var colorRGBA: SIMD4<UInt8>
    /// Rotation quaternion components mapped 0–255 → [−1, 1]; order: w, x, y, z
    public var rotationWXYZ: SIMD4<UInt8>
}

/// Parses a `.splat` binary file into an array of `SplatPoint`.
/// The format is a flat array of 32-byte records (no header).
public struct SplatFile {
    public let points: [SplatPoint]

    public enum ParseError: Error {
        case badSize(Int)
    }

    public init(data: Data) throws {
        let stride = 32
        guard data.count % stride == 0 else {
            throw ParseError.badSize(data.count)
        }
        let count = data.count / stride
        var pts = [SplatPoint]()
        pts.reserveCapacity(count)

        data.withUnsafeBytes { raw in
            for i in 0..<count {
                let off = i * stride
                // Floats live at [0..11] and [12..23]
                let px = raw.load(fromByteOffset: off + 0,  as: Float.self)
                let py = raw.load(fromByteOffset: off + 4,  as: Float.self)
                let pz = raw.load(fromByteOffset: off + 8,  as: Float.self)
                let sx = raw.load(fromByteOffset: off + 12, as: Float.self)
                let sy = raw.load(fromByteOffset: off + 16, as: Float.self)
                let sz = raw.load(fromByteOffset: off + 20, as: Float.self)
                // Bytes live at [24..27] color and [28..31] rotation
                let r  = raw.load(fromByteOffset: off + 24, as: UInt8.self)
                let g  = raw.load(fromByteOffset: off + 25, as: UInt8.self)
                let b  = raw.load(fromByteOffset: off + 26, as: UInt8.self)
                let a  = raw.load(fromByteOffset: off + 27, as: UInt8.self)
                let qw = raw.load(fromByteOffset: off + 28, as: UInt8.self)
                let qx = raw.load(fromByteOffset: off + 29, as: UInt8.self)
                let qy = raw.load(fromByteOffset: off + 30, as: UInt8.self)
                let qz = raw.load(fromByteOffset: off + 31, as: UInt8.self)

                pts.append(SplatPoint(
                    position:     SIMD3(px, py, pz),
                    logScale:     SIMD3(sx, sy, sz),
                    colorRGBA:    SIMD4(r, g, b, a),
                    rotationWXYZ: SIMD4(qw, qx, qy, qz)
                ))
            }
        }
        self.points = pts
    }

    /// Convenience: load from a local file URL.
    public init(url: URL) throws {
        try self.init(data: Data(contentsOf: url))
    }
}
