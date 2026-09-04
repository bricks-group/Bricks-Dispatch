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

            if flow.submitted {
                resultScroll
            } else {
                reviewScroll
                submitBar
            }
        }
        .task { await flow.refreshSession() }
    }

    // MARK: — Post-submit: compiling / failed / ready

    private var resultScroll: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                hero(metaExtra: false)

                VStack(alignment: .leading, spacing: 16) {
                    resultCard
                    if flow.kind == .splat {
                        TourTagEditor(jobId: flow.job.id, session: flow.session) { s in
                            flow.replaceSession(s)
                        }
                    }
                }
                .padding(22)
                .padding(.bottom, 40)
            }
        }
    }

    private var resultCard: some View {
        let status = (flow.session.status ?? "").lowercased()
        // Match Expo: failed when status is failed OR submit returned ok:false (error set).
        let failed = status == "failed" || flow.error != nil
        let ready = !failed && status == "delivered"
        let title: String = failed ? "Compile failed" : ready ? "Submitted · ready" : "Submitted · compiling"
        let statusLine = CaptureKind.statusCopy(flow.session.status, kind: flow.kind)
        let body: String = {
            if failed {
                if let err = flow.error, !err.isEmpty { return err }
                if !statusLine.isEmpty { return statusLine }
                return "Compile failed — try again from review or recapture stations."
            }
            if !statusLine.isEmpty { return statusLine }
            if ready {
                return "\(flow.kind.shortLabel) is ready on the job."
            }
            return "\(flow.kind.shortLabel) is compiling. This usually takes a few minutes."
        }()

        return VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(Color(hex: "#14110E"))
            Text(body)
                .font(.system(size: 13))
                .foregroundStyle(Color(hex: "#3B332A"))
                .fixedSize(horizontal: false, vertical: true)

            if let rebuildMsg = flow.rebuildMsg, !rebuildMsg.isEmpty {
                Text(rebuildMsg)
                    .font(.system(size: 12))
                    .foregroundStyle(rebuildMsg.hasPrefix("Tour player rebuilt") ? BricksColors.ok : BricksColors.bad)
                    .fixedSize(horizontal: false, vertical: true)
            }

            HStack(spacing: 8) {
                Button {
                    dismiss()
                    onDone()
                } label: {
                    Text("Back to job")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Color(hex: "#14110E"))
                        .padding(.horizontal, 14)
                        .padding(.vertical, 10)
                        .overlay(
                            RoundedRectangle(cornerRadius: 2)
                                .stroke(Color(hex: "#14111020"), lineWidth: 1)
                        )
                }
                .buttonStyle(.plain)

                if failed {
                    Button {
                        Task { _ = await flow.submit() }
                    } label: {
                        Text(flow.submitting ? "Retrying…" : "Retry compile")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.white)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 10)
                            .background(CaptureChrome.brick.opacity(canSubmit && !flow.submitting ? 1 : 0.5))
                            .clipShape(RoundedRectangle(cornerRadius: 2))
                    }
                    .buttonStyle(.plain)
                    .disabled(!canSubmit || flow.submitting)
                }

                if ready && (flow.kind == .splat || flow.kind == .floorplan) {
                    Button {
                        Task { await flow.rebuildTourPlayer() }
                    } label: {
                        Text(flow.rebuilding ? "Rebuilding…" : (flow.kind == .floorplan ? "Rebuild floor plan" : "Rebuild tour player"))
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(Color(hex: "#14110E"))
                            .padding(.horizontal, 14)
                            .padding(.vertical, 10)
                            .overlay(
                                RoundedRectangle(cornerRadius: 2)
                                    .stroke(Color(hex: "#14111020"), lineWidth: 1)
                            )
                    }
                    .buttonStyle(.plain)
                    .disabled(flow.rebuilding)
                }

                Button {
                    flow.clearSubmittedForRecapture()
                    dismiss()
                } label: {
                    Text(failed ? "Recapture" : "Continue capture")
                        .font(.system(size: 13))
                        .foregroundStyle(CaptureChrome.brick)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 10)
                }
                .buttonStyle(.plain)
            }
            .padding(.top, 4)
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(resultTone(failed: failed, ready: ready).opacity(0.08))
        .overlay(
            RoundedRectangle(cornerRadius: 2)
                .stroke(resultTone(failed: failed, ready: ready).opacity(0.55), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 2))
    }

    private func resultTone(failed: Bool, ready: Bool) -> Color {
        if failed { return Color(hex: "#8B2E1F") }
        if ready { return Color(hex: "#2F6B4F") }
        return CaptureChrome.brick
    }

    // MARK: — Pre-submit review

    private var reviewScroll: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                hero(metaExtra: true)

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

                    if flow.kind == .splat {
                        TourTagEditor(jobId: flow.job.id, session: flow.session) { s in
                            flow.replaceSession(s)
                        }
                        .padding(.top, 8)
                    }
                }
                .padding(22)
                .padding(.bottom, 120)
            }
        }
    }

    private var submitBar: some View {
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
                    Task { _ = await flow.submit() }
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

    @ViewBuilder
    private func hero(metaExtra: Bool) -> some View {
        ZStack(alignment: .bottomLeading) {
            CaptureChrome.night.frame(height: 200)
            VStack(alignment: .leading, spacing: 4) {
                Text(flow.session.address)
                    .font(.system(size: 19, weight: .semibold))
                    .foregroundStyle(CaptureChrome.moon)
                Text(metaExtra ? heroMeta : heroMetaShort)
                    .font(BricksFont.mono(11))
                    .foregroundStyle(CaptureChrome.moon.opacity(0.73))
            }
            .padding(18)

            Button {
                if flow.submitted {
                    dismiss()
                    onDone()
                } else {
                    dismiss()
                }
            } label: {
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
    }

    private var heroMeta: String {
        let kindLabel = flow.kind == .floorplan ? "FLOOR PLAN" : "3D TOUR"
        let n = flow.session.stationCount
        let frames = flow.session.frameCount
        return [flow.session.meta, kindLabel, "\(n) STATION\(n == 1 ? "" : "S")", "\(frames) PANS"]
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
    }

    private var heroMetaShort: String {
        let kindLabel = flow.kind == .floorplan ? "FLOOR PLAN" : "3D TOUR"
        return [flow.session.meta, kindLabel]
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
    }

    private var coveredCount: Int { flow.session.rooms.filter { $0.n > 0 }.count }

    private var canSubmit: Bool { flow.session.canSubmit && !flow.purchasedBlocked }

    private var thinCopy: String {
        if flow.session.stationCount == 0 {
            return flow.kind == .floorplan
                ? "Submit needs at least one station with room corners, not snapshot stills."
                : "Submit needs at least one 360 station (coverage dome), not snapshot stills."
        }
        if let first = flow.session.thinStations.first {
            return flow.kind == .floorplan
                ? "Station \(first.stationIndex) needs more corners — capture 4 corners of the room."
                : "Station \(first.stationIndex) sweep is thin — fill the dome (8+ overlapping pans) or capture an equirect."
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

/// Standalone review host — open from job detail CAPTURE STATUS (retry / open review).
struct CaptureReviewHost: View {
    let job: DispatchJob
    let kind: CaptureKind
    @StateObject private var flow: CaptureFlowModel
    @Environment(\.dismiss) private var dismiss

    init(job: DispatchJob, kind: CaptureKind) {
        self.job = job
        self.kind = kind
        _flow = StateObject(wrappedValue: CaptureFlowModel(job: job, kind: kind))
    }

    var body: some View {
        CaptureReviewView(flow: flow, onDone: { dismiss() })
    }
}

