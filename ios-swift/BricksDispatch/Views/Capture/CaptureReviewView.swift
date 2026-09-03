import SwiftUI
import BricksCore

/// Submit gate after a 360 / floor-plan shoot. Matches Expo `review.tsx`.
struct CaptureReviewView: View {
    @ObservedObject var flow: CaptureFlowModel
    var onDone: () -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack(alignment: .bottom) {
            Color(hex: "#F4F1EB").ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    ZStack(alignment: .bottomLeading) {
                        CaptureChrome.night.frame(height: 200)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(flow.session.address)
                                .font(.system(size: 19, weight: .semibold))
                                .foregroundStyle(CaptureChrome.moon)
                            Text(heroMeta)
                                .font(BricksFont.mono(11))
                                .foregroundStyle(CaptureChrome.moon.opacity(0.73))
                        }
                        .padding(18)

                        Button { dismiss() } label: {
                            Text("‹")
                                .font(.system(size: 18))
                                .foregroundStyle(CaptureChrome.moon)
                                .frame(width: 32, height: 32)
                                .overlay(
                                    RoundedRectangle(cornerRadius: 2)
                                        .stroke(CaptureChrome.moon.opacity(0.19), lineWidth: 1)
                                )
                        }
                        .buttonStyle(.plain)
                        .padding(.leading, 18)
                        .padding(.top, 14)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    }

                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text("Coverage by room")
                                .font(.system(size: 13, weight: .semibold))
                            Spacer()
                            Text("\(coveredCount) / \(flow.session.rooms.count) captured")
                                .font(BricksFont.mono(11))
                                .foregroundStyle(Color(hex: "#6B6054"))
                        }

                        if flow.session.rooms.isEmpty {
                            Text("No rooms recorded yet.")
                                .font(.system(size: 13))
                                .foregroundStyle(.secondary)
                        } else {
                            ForEach(flow.session.rooms) { room in
                                HStack(spacing: 10) {
                                    Circle()
                                        .fill(toneColor(room.tone))
                                        .frame(width: 8, height: 8)
                                    Text(room.name)
                                        .font(.system(size: 14, weight: .medium))
                                    Spacer()
                                    Text("\(room.n)")
                                        .font(BricksFont.mono(13))
                                        .foregroundStyle(.secondary)
                                }
                                .padding(.vertical, 6)
                            }
                        }

                        if flow.session.stationCount == 0 || !flow.session.thinStations.isEmpty {
                            Text(thinCopy)
                                .font(.system(size: 12))
                                .foregroundStyle(Color(hex: "#3B332A"))
                                .padding(10)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(Color(hex: "#F6E8D1"))
                                .clipShape(RoundedRectangle(cornerRadius: 2))
                        }

                        Text("Pipeline")
                            .font(.system(size: 13, weight: .semibold))
                            .padding(.top, 12)

                        if flow.session.pipeline.isEmpty {
                            Text("No pipeline yet — submit is unavailable until a session exists.")
                                .font(.system(size: 13))
                                .foregroundStyle(.secondary)
                        } else {
                            ForEach(Array(flow.session.pipeline.enumerated()), id: \.element.title) { i, stage in
                                HStack(alignment: .top, spacing: 12) {
                                    VStack(spacing: 0) {
                                        Circle()
                                            .fill(pipeColor(stage.status))
                                            .frame(width: 10, height: 10)
                                        if i != flow.session.pipeline.count - 1 {
                                            Rectangle()
                                                .fill(stage.status == "done" ? BricksColors.ok : Color(hex: "#E4DED2"))
                                                .frame(width: 1, height: 28)
                                        }
                                    }
                                    .frame(width: 22)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(stage.title)
                                            .font(.system(size: 14, weight: .medium))
                                            .foregroundStyle(stage.status == "todo" ? Color(hex: "#6B6054") : Color(hex: "#14110E"))
                                        Text(stage.detail)
                                            .font(.system(size: 12))
                                            .foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    if stage.status == "active" {
                                        Text("In progress")
                                            .font(BricksFont.mono(10))
                                            .foregroundStyle(CaptureChrome.brick)
                                    }
                                }
                            }
                        }
                    }
                    .padding(22)
                    .padding(.bottom, 120)
                }
            }

            VStack(spacing: 0) {
                if let err = flow.error {
                    Text(err)
                        .font(.system(size: 11))
                        .foregroundStyle(CaptureChrome.moon)
                        .frame(maxWidth: .infinity)
                        .padding(8)
                        .background(BricksColors.bad)
                }
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Est. delivery")
                            .font(BricksFont.mono(10))
                            .foregroundStyle(.secondary)
                        Text(eta)
                            .font(.system(size: 16, weight: .semibold))
                    }
                    Spacer()
                    Button {
                        Task {
                            let ok = await flow.submit()
                            if ok {
                                dismiss()
                                onDone()
                            }
                        }
                    } label: {
                        Text(flow.submitting ? "Submitting…" : "Submit to pipeline →")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.white)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 12)
                            .background(CaptureChrome.brick.opacity(canSubmit ? 1 : 0.5))
                            .clipShape(RoundedRectangle(cornerRadius: 2))
                    }
                    .buttonStyle(.plain)
                    .disabled(!canSubmit || flow.submitting)
                }
                .padding(16)
                .background(Color.white)
            }
        }
        .task { await flow.refreshSession() }
    }

    private var heroMeta: String {
        let kindLabel = flow.kind == .floorplan ? "FLOOR PLAN" : "3D TOUR"
        let n = flow.session.stationCount
        let frames = flow.session.frameCount
        return [flow.session.meta, kindLabel, "\(n) STATION\(n == 1 ? "" : "S")", "\(frames) PANS"]
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
    }

    private var coveredCount: Int { flow.session.rooms.filter { $0.n > 0 }.count }

    private var canSubmit: Bool { flow.session.canSubmit && !flow.purchasedBlocked }

    private var thinCopy: String {
        if flow.session.stationCount == 0 {
            return "Submit needs at least one 360 station (coverage dome), not snapshot stills."
        }
        if let first = flow.session.thinStations.first {
            return "Station \(first.stationIndex) sweep is thin — fill the dome (8+ overlapping pans) or capture an equirect."
        }
        return "Coverage is thin."
    }

    private var eta: String {
        if let h = flow.session.estimatedDeliveryHours {
            return "~\(Int(h)) hrs"
        }
        return "—"
    }

    private func toneColor(_ tone: String) -> Color {
        switch tone {
        case "ok": return BricksColors.ok
        case "warn": return BricksColors.warn
        default: return BricksColors.bad
        }
    }

    private func pipeColor(_ status: String) -> Color {
        switch status {
        case "done": return BricksColors.ok
        case "active": return CaptureChrome.brick
        default: return Color(hex: "#E4DED2")
        }
    }
}
