import SwiftUI

/// Displays a static thumbnail of a Gaussian splat scene.
///
/// On first call for a given `jobId`, downloads the `.splat` file from `splatURL`,
/// renders a thumbnail frame offscreen via `SplatThumbnailRenderer`, and caches to disk.
/// Subsequent loads read the JPEG from disk instantly.
///
/// Falls back to `fallbackImageURL` (a standard photo) if the splat URL is nil,
/// the render fails, or the QC confidence is below threshold.
///
/// Usage in a grid:
/// ```swift
/// SplatThumbnailView(
///     jobId:       job.id,
///     splatURL:    job.splatFileURL,
///     fallbackURL: job.heroPhotoURL
/// )
/// .aspectRatio(4/3, contentMode: .fit)
/// .clipShape(RoundedRectangle(cornerRadius: 12))
/// ```
public struct SplatThumbnailView: View {

    public let jobId: String
    public let splatURL: URL?
    public let fallbackURL: URL?

    @State private var thumbnail: UIImage?
    @State private var loadState: LoadState = .idle

    private enum LoadState { case idle, loading, loaded, failed }

    public init(jobId: String, splatURL: URL?, fallbackURL: URL? = nil) {
        self.jobId       = jobId
        self.splatURL    = splatURL
        self.fallbackURL = fallbackURL
    }

    public var body: some View {
        ZStack {
            Color.black

            switch loadState {
            case .idle, .loading:
                shimmer

            case .loaded:
                if let img = thumbnail {
                    Image(uiImage: img)
                        .resizable()
                        .scaledToFill()
                        .transition(.opacity.animation(.easeIn(duration: 0.3)))
                }

            case .failed:
                if let url = fallbackURL {
                    AsyncImage(url: url) { phase in
                        switch phase {
                        case .success(let img): img.resizable().scaledToFill()
                        default: Color.white.opacity(0.06)
                        }
                    }
                } else {
                    Image(systemName: "cube.transparent")
                        .font(.title2)
                        .foregroundStyle(.white.opacity(0.25))
                }
            }

            // 3D badge — visible when splat loaded successfully
            if loadState == .loaded {
                VStack {
                    HStack {
                        Spacer()
                        Text("3D")
                            .font(.system(size: 9, weight: .black, design: .rounded))
                            .foregroundStyle(.black)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 2)
                            .background(Color("BricksGold"), in: Capsule())
                            .padding(6)
                    }
                    Spacer()
                }
            }
        }
        .task(id: jobId) { await loadThumbnail() }
        .clipped()
    }

    // MARK: — Shimmer placeholder

    private var shimmer: some View {
        GeometryReader { geo in
            ShimmerRect(size: geo.size)
        }
    }

    // MARK: — Load logic

    private func loadThumbnail() async {
        // 1. Disk cache hit — instant
        if let cached = SplatThumbnailCache.shared.read(jobId: jobId) {
            thumbnail  = cached
            loadState  = .loaded
            return
        }

        // 2. No splat URL — fall through to fallback photo
        guard let url = splatURL else {
            loadState = .failed
            return
        }

        loadState = .loading

        do {
            // 3. Download .splat file
            let (data, _) = try await URLSession.shared.data(from: url)
            let file      = try SplatFile(data: data)

            // 4. Render offscreen thumbnail
            let img = try await SplatThumbnailRenderer.render(file)

            // 5. Cache to disk and display
            SplatThumbnailCache.shared.write(img, jobId: jobId)
            thumbnail = img
            loadState = .loaded

        } catch {
            loadState = .failed
        }
    }
}

// MARK: — Shimmer animation

private struct ShimmerRect: View {
    let size: CGSize
    @State private var phase: CGFloat = 0

    var body: some View {
        Rectangle()
            .fill(
                LinearGradient(
                    stops: [
                        .init(color: Color.white.opacity(0.04), location: 0),
                        .init(color: Color.white.opacity(0.04), location: phase - 0.2),
                        .init(color: Color.white.opacity(0.10), location: phase),
                        .init(color: Color.white.opacity(0.04), location: phase + 0.2),
                        .init(color: Color.white.opacity(0.04), location: 1),
                    ],
                    startPoint: .leading,
                    endPoint: .trailing
                )
            )
            .onAppear {
                withAnimation(.linear(duration: 1.4).repeatForever(autoreverses: false)) {
                    phase = 1.4
                }
            }
    }
}
