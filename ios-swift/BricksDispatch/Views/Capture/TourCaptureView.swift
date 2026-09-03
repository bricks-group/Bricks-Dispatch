import SwiftUI
import BricksCore

/// Native 3D-tour shutter: guided pan JPEG frames with yaw, role=pan, 24-tick dome.
/// Server stitches to equirect. Not a slideshow, not .splat, not Luma, not HDR stills.
struct TourCaptureView: View {
    let job: DispatchJob
    @Environment(\.dismiss) private var dismiss
    @StateObject private var flow: CaptureFlowModel

    init(job: DispatchJob) {
        self.job = job
        _flow = StateObject(wrappedValue: CaptureFlowModel(job: job, kind: .splat))
    }

    var body: some View {
        ZStack {
            CaptureChrome.night.ignoresSafeArea()
            CameraPreviewView(session: flow.hardware.captureSession)
                .ignoresSafeArea()
            CaptureChrome.night.opacity(0.35).ignoresSafeArea().allowsHitTesting(false)

            if flow.purchasedBlocked {
                CapturePermGate(
                    message: flow.error ?? "That capture mode is not on this job.",
                    onAllow: nil,
                    onClose: { dismiss() }
                )
            } else {
            switch flow.hardware.cameraStatus {
            case .denied:
                CapturePermGate(
                    message: "Bricks needs camera access to photograph listings.",
                    onAllow: { Task { await flow.hardware.requestCameraAccess() } },
                    onClose: { dismiss() }
                )
            case .unavailable(let msg):
                CapturePermGate(message: msg, onAllow: nil, onClose: { dismiss() })
            case .idle, .requesting:
                VStack {
                    topBar
                    Spacer()
                    ProgressView().tint(CaptureChrome.moon)
                    Spacer()
                }
            case .ready:
                captureChrome
            }
        }
        }
        .preferredColorScheme(.dark)
        .task {
            await flow.boot()
        }
        .onDisappear { flow.shutdown() }
        .fullScreenCover(isPresented: $flow.showReview) {
            CaptureReviewView(flow: flow) { dismiss() }
        }
    }

    private var captureChrome: some View {
        VStack(spacing: 0) {
            topBar
            Text("Station \(flow.stationIndex) · \(flow.ticks.count)/\(CaptureDome.total) · \(flow.frameCount) frames")
                .font(BricksFont.mono(10.5))
                .foregroundStyle(CaptureChrome.moon)
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(Color(hex: "#141110").opacity(0.6))
                .overlay(
                    RoundedRectangle(cornerRadius: 2)
                        .stroke(CaptureChrome.moon.opacity(0.10), lineWidth: 1)
                )
                .clipShape(RoundedRectangle(cornerRadius: 2))
                .padding(.top, 12)

            Spacer()

            ZStack {
                CoverageDomeView(captured: flow.ticks, headingTick: flow.aimTick)
                VStack(spacing: 2) {
                    Text("\(pct)%")
                        .font(BricksFont.mono(30))
                        .foregroundStyle(CaptureChrome.moon)
                    Text("Station \(flow.stationIndex) sweep")
                        .font(BricksFont.mono(9))
                        .tracking(1.5)
                        .textCase(.uppercase)
                        .foregroundStyle(CaptureChrome.moon.opacity(0.67))
                }
            }

            Spacer()

            if let note = flow.hardware.headingNote {
                Text(note)
                    .font(.system(size: 11))
                    .foregroundStyle(CaptureChrome.moon.opacity(0.7))
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 20)
                    .padding(.bottom, 8)
            }

            CaptureGuidanceBanner(title: guidanceTitle, bodyText: guidanceBody)

            if let err = flow.error {
                CaptureErrorBanner(text: err)
                    .padding(.top, 8)
            }

            HStack {
                CaptureGhostChip(title: "+St") {
                    Task { await flow.newStation() }
                }
                Spacer()
                CaptureShutterButton(pressed: flow.shutterPressed) {
                    Task { await flow.shoot() }
                }
                .disabled(flow.shutterBusy || flow.purchasedBlocked)
                Spacer()
                CaptureGhostChip(title: "✓") {
                    flow.showReview = true
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 18)
        }
    }

    private var topBar: some View {
        HStack {
            CaptureCloseChip { dismiss() }
            Spacer()
            VStack(spacing: 1) {
                Text(flow.session.address.isEmpty ? job.address : flow.session.address)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(CaptureChrome.moon)
                    .lineLimit(1)
                Text(roomLabel)
                    .font(BricksFont.mono(10))
                    .tracking(1.4)
                    .foregroundStyle(CaptureChrome.brick)
            }
            Spacer()
            Color.clear.frame(width: 34, height: 34)
        }
        .padding(.horizontal, 20)
        .padding(.top, 8)
    }

    private var pct: Int {
        Int(round(Double(min(flow.ticks.count, CaptureDome.total)) / Double(CaptureDome.total) * 100))
    }

    private var roomLabel: String {
        if let room = flow.session.guidance?.room, !room.isEmpty {
            return room.uppercased()
        }
        if let name = flow.currentRoom?.name, !name.isEmpty {
            return name.uppercased()
        }
        return "ROOM 1"
    }

    private var guidanceTitle: String {
        if let g = flow.session.guidance {
            return "360 station · \(g.room)"
        }
        return "Stand still · pan slowly through 360"
    }

    private var guidanceBody: String {
        flow.session.guidance?.message
            ?? "Fill the coverage dome for this station, then walk to the next. Server stitches the pan into a 360."
    }
}
