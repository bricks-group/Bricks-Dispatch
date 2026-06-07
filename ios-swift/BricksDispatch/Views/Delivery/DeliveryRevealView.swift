import SwiftUI
import BricksCore

/// The emotional core of the Dispatch app — the moment the photographer's work comes back
/// QC-passed, edited, and delivered. Photos reveal one at a time from black with a specular
/// sweep; earnings resolve last with a count-up and haptic pattern.
///
/// This is the ADA demo video shot. Slow, deliberate, cinematic.
struct DeliveryRevealView: View {

    let job: DispatchJob
    let photos: [DeliveredPhoto]

    @Environment(\.dismiss) private var dismiss
    @State private var visibleCount  = 0
    @State private var sweepTriggers = [Bool]()      // one per photo
    @State private var earningsVisible  = false
    @State private var earningsResolved = false

    private let stagger: Double = 0.38   // seconds between each photo reveal

    init(job: DispatchJob, photos: [DeliveredPhoto]) {
        self.job    = job
        self.photos = photos
        _sweepTriggers = State(initialValue: Array(repeating: false, count: photos.count))
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            VStack(spacing: 0) {
                photoGrid
                earningsFooter
            }

            closeButton
        }
        .statusBarHidden(true)
        .onAppear { startReveal() }
    }

    // MARK: — Photo grid

    private var photoGrid: some View {
        ScrollView(.vertical, showsIndicators: false) {
            LazyVGrid(columns: columns, spacing: 2) {
                ForEach(Array(photos.enumerated()), id: \.element.id) { idx, photo in
                    PhotoCell(
                        photo: photo,
                        visible: idx < visibleCount,
                        sweepTrigger: sweepTriggers[safe: idx] ?? false
                    )
                }
            }
        }
        .padding(.top, 60)          // clear Dynamic Island
    }

    private var columns: [GridItem] {
        Array(repeating: GridItem(.flexible(), spacing: 2), count: photos.count == 1 ? 1 : 2)
    }

    // MARK: — Earnings footer

    private var earningsFooter: some View {
        VStack(spacing: 6) {
            if earningsVisible {
                Text("Delivered · \(photos.count) photos")
                    .font(.caption.weight(.medium))
                    .foregroundStyle(.white.opacity(0.45))
                    .transition(.opacity.combined(with: .move(edge: .bottom)))

                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    if let payout = job.vendorPayout {
                        AnimatedDollar(
                            target: payout,
                            delay: 0.15
                        )
                        .font(.system(size: 48, weight: .bold, design: .rounded))
                        .foregroundStyle(earningsResolved ? Color("BricksGold") : .white)
                        .animation(.easeInOut(duration: 0.55), value: earningsResolved)
                        .onChange(of: earningsResolved) { _, resolved in
                            if resolved { BricksHaptics.shared.earningsResolved() }
                        }
                    }
                }
                .transition(.opacity.combined(with: .scale(scale: 0.88)))

                Text("Payout on next cycle")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.3))
                    .transition(.opacity)
            }
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 28)
        .frame(maxWidth: .infinity)
        .background(
            LinearGradient(
                colors: [.black.opacity(0), .black.opacity(0.92)],
                startPoint: .top,
                endPoint: .bottom
            )
        )
    }

    // MARK: — Close

    private var closeButton: some View {
        VStack {
            HStack {
                Spacer()
                Button {
                    dismiss()
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.title2)
                        .symbolRenderingMode(.hierarchical)
                        .foregroundStyle(.white.opacity(0.6))
                        .padding(20)
                }
            }
            Spacer()
        }
    }

    // MARK: — Reveal sequencing

    private func startReveal() {
        guard !photos.isEmpty else { return }

        for i in 0..<photos.count {
            let delay = Double(i) * stagger

            // Photo appears
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
                withAnimation(.spring(response: 0.48, dampingFraction: 0.74)) {
                    visibleCount = i + 1
                }
                BricksHaptics.shared.photoSettle()
            }

            // Specular sweep fires ~80ms after the photo settles
            DispatchQueue.main.asyncAfter(deadline: .now() + delay + 0.08) {
                sweepTriggers[i].toggle()
            }
        }

        // Earnings footer after all photos
        let allDone = Double(photos.count) * stagger + 0.3
        DispatchQueue.main.asyncAfter(deadline: .now() + allDone) {
            withAnimation(.spring(response: 0.55, dampingFraction: 0.8)) {
                earningsVisible = true
            }
        }

        // Gold color resolves after the counter finishes counting (~1.6s spring)
        let goldDelay = allDone + 1.8
        DispatchQueue.main.asyncAfter(deadline: .now() + goldDelay) {
            earningsResolved = true
        }
    }
}

// MARK: — Photo cell

private struct PhotoCell: View {
    let photo: DeliveredPhoto
    let visible: Bool
    let sweepTrigger: Bool

    var body: some View {
        GeometryReader { geo in
            Group {
                if let url = photo.photoURL {
                    AsyncImage(url: url) { phase in
                        switch phase {
                        case .success(let image):
                            image
                                .resizable()
                                .scaledToFill()
                        case .failure:
                            Rectangle().fill(Color.white.opacity(0.06))
                        default:
                            Rectangle().fill(Color.white.opacity(0.03))
                        }
                    }
                } else {
                    Rectangle().fill(Color.white.opacity(0.06))
                }
            }
            .frame(width: geo.size.width, height: geo.size.height)
            .clipped()
            .specularSweep(trigger: sweepTrigger)
            .overlay {
                if photo.qcFlagged {
                    VStack {
                        HStack {
                            Spacer()
                            Image(systemName: "exclamationmark.triangle.fill")
                                .font(.caption2)
                                .foregroundStyle(.yellow)
                                .padding(6)
                        }
                        Spacer()
                    }
                }
            }
        }
        .aspectRatio(4/3, contentMode: .fit)
        .scaleEffect(visible ? 1 : 0.94)
        .opacity(visible ? 1 : 0)
        .animation(.spring(response: 0.48, dampingFraction: 0.74), value: visible)
    }
}

// MARK: — Safe subscript

private extension Array {
    subscript(safe index: Int) -> Element? {
        indices.contains(index) ? self[index] : nil
    }
}

#Preview {
    DeliveryRevealView(
        job: .preview,
        photos: (0..<6).map { i in
            DeliveredPhoto(
                id: "photo-\(i)",
                filename: "IMG_\(1000 + i).jpg",
                url: "https://picsum.photos/seed/\(i)/800/600",
                expiresAt: "",
                qcFlagged: i == 2
            )
        }
    )
}

private extension DispatchJob {
    static var preview: DispatchJob {
        DispatchJob(
            id: "preview-job",
            bookingId: "booking-1",
            address: "123 King St W, Toronto",
            city: "Toronto",
            state: "ON",
            zipCode: "M5H 1J9",
            lat: 43.6489,
            lng: -79.3844,
            timeWindowStart: "10:00",
            timeWindowEnd: "12:00",
            scheduledDate: "2026-06-15",
            jobType: "photo",
            status: .delivered,
            packageName: "Premium",
            packagePrice: 59900,
            vendorPayout: 269.55,
            agentName: "Sarah Chen",
            agentPhone: nil,
            agentEmail: nil,
            clientInstructions: nil,
            checklist: [],
            complexityScore: 3,
            createdAt: "",
            updatedAt: ""
        )
    }
}
