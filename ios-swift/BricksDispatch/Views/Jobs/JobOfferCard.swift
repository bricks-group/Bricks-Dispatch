import SwiftUI
import BricksCore

/// Full-screen swipe card presented when a new job offer arrives.
/// Right swipe = accept, left swipe = decline. Velocity-tracked threshold at ±100 pt.
/// Spring physics snap back on incomplete drag; flies off-screen on decision.
struct JobOfferCard: View {

    let job: DispatchJob
    var onAccept: () -> Void = {}
    var onDecline: () -> Void = {}

    @State private var offset: CGFloat = 0
    @State private var isDragging = false
    @State private var decision: Decision? = nil

    private enum Decision { case accept, decline }

    private let threshold: CGFloat = 100

    var body: some View {
        ZStack {
            backgroundGlow

            VStack(spacing: 0) {
                Spacer()
                cardContent
                Spacer()
                swipeHint
                    .padding(.bottom, 44)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.black.ignoresSafeArea())
        .offset(x: offset)
        .rotationEffect(.degrees(Double(offset) / 28))
        .gesture(dragGesture)
        .allowsHitTesting(decision == nil)
    }

    // MARK: — Background glow that shifts green/red on drag

    private var backgroundGlow: some View {
        ZStack {
            if offset > 20 {
                Color.green
                    .opacity(Double(min(offset, threshold) / threshold) * 0.15)
                    .ignoresSafeArea()
                    .animation(.easeOut(duration: 0.1), value: offset)
            }
            if offset < -20 {
                Color.red
                    .opacity(Double(min(-offset, threshold) / threshold) * 0.15)
                    .ignoresSafeArea()
                    .animation(.easeOut(duration: 0.1), value: offset)
            }
        }
    }

    // MARK: — Card body

    private var cardContent: some View {
        VStack(spacing: 28) {
            decisionLabel

            // Address
            VStack(spacing: 6) {
                Text(job.address)
                    .font(.title2.weight(.bold))
                    .multilineTextAlignment(.center)
                    .foregroundStyle(.white)
                Text("\(job.city), \(job.state)")
                    .font(.subheadline)
                    .foregroundStyle(.white.opacity(0.55))
            }

            // Package + type pills
            HStack(spacing: 10) {
                if let pkg = job.packageName {
                    Pill(pkg.capitalized, color: Color("BricksGold"))
                }
                Pill(job.jobType.capitalized, color: .white.opacity(0.2))
            }

            Divider().background(.white.opacity(0.12))

            // Payout + time grid
            HStack(spacing: 0) {
                InfoCell(
                    icon: "dollarsign.circle.fill",
                    label: "Payout",
                    value: job.formattedPayout
                )
                Divider().frame(width: 1, height: 48).background(.white.opacity(0.12))
                InfoCell(
                    icon: "clock.fill",
                    label: "Time",
                    value: job.formattedShootWindow
                )
                Divider().frame(width: 1, height: 48).background(.white.opacity(0.12))
                InfoCell(
                    icon: "calendar",
                    label: "Date",
                    value: shortDate(job.scheduledDate)
                )
            }

            // Agent name if present
            if let agent = job.agentName {
                HStack(spacing: 6) {
                    Image(systemName: "person.fill")
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.4))
                    Text(agent)
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.55))
                }
            }
        }
        .padding(.horizontal, 28)
        .padding(.vertical, 36)
        .background {
            RoundedRectangle(cornerRadius: 28, style: .continuous)
                .fill(Color.white.opacity(0.06))
                .overlay {
                    RoundedRectangle(cornerRadius: 28, style: .continuous)
                        .strokeBorder(.white.opacity(0.1), lineWidth: 1)
                }
        }
        .padding(.horizontal, 20)
    }

    // MARK: — Accept / Decline label that fades in as drag increases

    @ViewBuilder
    private var decisionLabel: some View {
        ZStack {
            Label("ACCEPT", systemImage: "checkmark")
                .font(.headline.weight(.black))
                .foregroundStyle(.green)
                .opacity(Double(max(0, offset - 30) / threshold))
                .frame(maxWidth: .infinity, alignment: .leading)

            Label("DECLINE", systemImage: "xmark")
                .font(.headline.weight(.black))
                .foregroundStyle(.red)
                .opacity(Double(max(0, -offset - 30) / threshold))
                .frame(maxWidth: .infinity, alignment: .trailing)
        }
        .padding(.horizontal, 12)
    }

    // MARK: — Swipe hint

    private var swipeHint: some View {
        HStack(spacing: 8) {
            Image(systemName: "arrow.left")
                .foregroundStyle(.red.opacity(0.6))
            Text("Decline")
                .foregroundStyle(.white.opacity(0.3))
            Spacer()
            Text("Accept")
                .foregroundStyle(.white.opacity(0.3))
            Image(systemName: "arrow.right")
                .foregroundStyle(.green.opacity(0.6))
        }
        .font(.caption.weight(.medium))
        .padding(.horizontal, 36)
        .opacity(isDragging ? 0 : 1)
        .animation(.easeOut(duration: 0.2), value: isDragging)
    }

    // MARK: — Drag gesture

    private var dragGesture: some Gesture {
        DragGesture(minimumDistance: 8)
            .onChanged { value in
                isDragging = true
                withAnimation(.interactiveSpring(response: 0.25, dampingFraction: 0.86)) {
                    offset = value.translation.width
                }
            }
            .onEnded { value in
                isDragging = false
                let velocity = value.predictedEndTranslation.width
                let combinedX = value.translation.width + velocity * 0.3

                if combinedX > threshold {
                    commit(.accept)
                } else if combinedX < -threshold {
                    commit(.decline)
                } else {
                    // Snap back
                    withAnimation(.spring(response: 0.42, dampingFraction: 0.72)) {
                        offset = 0
                    }
                }
            }
    }

    private func commit(_ d: Decision) {
        decision = d
        let flyX: CGFloat = d == .accept ? 500 : -500
        BricksHaptics.shared.jobAccepted()
        if d == .decline { BricksHaptics.shared.jobDeclined() }

        withAnimation(.spring(response: 0.38, dampingFraction: 0.76)) {
            offset = flyX
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.38) {
            if d == .accept { onAccept() } else { onDecline() }
        }
    }

    // MARK: — Helpers

    private func shortDate(_ iso: String) -> String {
        let df = DateFormatter()
        df.dateFormat = "yyyy-MM-dd"
        guard let d = df.date(from: iso) else { return iso }
        let out = DateFormatter()
        out.dateFormat = "MMM d"
        return out.string(from: d)
    }
}

// MARK: — Supporting views

private struct Pill: View {
    let text: String
    let color: Color

    init(_ text: String, color: Color) {
        self.text = text
        self.color = color
    }

    var body: some View {
        Text(text)
            .font(.caption.weight(.semibold))
            .foregroundStyle(color)
            .padding(.horizontal, 12)
            .padding(.vertical, 5)
            .background(color.opacity(0.15), in: Capsule())
    }
}

private struct InfoCell: View {
    let icon: String
    let label: String
    let value: String

    var body: some View {
        VStack(spacing: 4) {
            Image(systemName: icon)
                .font(.caption)
                .foregroundStyle(.white.opacity(0.4))
            Text(value)
                .font(.subheadline.weight(.semibold).monospacedDigit())
                .foregroundStyle(.white)
            Text(label)
                .font(.caption2)
                .foregroundStyle(.white.opacity(0.4))
        }
        .frame(maxWidth: .infinity)
    }
}

// MARK: — TodayJobsView integration helper

/// Presented full-screen when store.pendingOffer is non-nil.
/// Dismiss after accept/decline so TodayJobsView can reload.
struct JobOfferHost: View {
    @ObservedObject var store: JobStore
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        if let job = store.pendingOffer {
            JobOfferCard(job: job) {
                Task {
                    await store.acceptOffer(job)
                    dismiss()
                }
            } onDecline: {
                Task {
                    await store.declineOffer(job)
                    dismiss()
                }
            }
        }
    }
}

// MARK: — Preview

#Preview {
    JobOfferCard(
        job: DispatchJob(
            id: "preview-1",
            bookingId: "b-1",
            address: "221 Bay St",
            city: "Toronto",
            state: "ON",
            zipCode: "M5J 2T3",
            lat: 43.648,
            lng: -79.383,
            timeWindowStart: "10:00",
            timeWindowEnd: "12:00",
            scheduledDate: "2026-06-15",
            jobType: "photo",
            status: .pending,
            packageName: "Premium",
            packagePrice: 59900,
            vendorPayout: 269.55,
            agentName: "Marcus Webb",
            agentPhone: nil,
            agentEmail: nil,
            clientInstructions: nil,
            checklist: [],
            complexityScore: 3,
            createdAt: "",
            updatedAt: ""
        )
    )
}
