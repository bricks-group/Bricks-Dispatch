import SwiftUI
import BricksCore

/// Floor-plan shutter: corners/walls from phone JPEG frames (Expo floorplan.tsx).
/// Not RoomPlan. Not LiDAR. Heading missing → yaw 0, honest copy.
struct FloorPlanCaptureView: View {
    let job: DispatchJob
    @Environment(\.dismiss) private var dismiss
    @StateObject private var flow: CaptureFlowModel

    private let cues: [(title: String, body: String)] = [
        ("Station 360", "Stand in the middle of this room. Pan slowly and fire at each tick of the ring. The server derives the plan from aligned stations — not from snapshot guesses."),
        ("Keep panning", "Overlap each frame. Fill the dome, then add the next room / next station."),
        ("Openings", "Include doors and openings in the sweep so rooms connect on the compiled plan."),
        ("Next room", "When this station is full, add a room and walk there. One station per room is the Matterport pattern."),
    ]

    init(job: DispatchJob) {
        self.job = job
        _flow = StateObject(wrappedValue: CaptureFlowModel(job: job, kind: .floorplan))
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
                    message: "Bricks needs camera access to capture 360 stations for the floor plan.",
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
        .task { await flow.boot() }
        .onDisappear { flow.shutdown() }
        .fullScreenCover(isPresented: $flow.showReview) {
            CaptureReviewView(flow: flow) { dismiss() }
        }
    }

    private var captureChrome: some View {
        VStack(spacing: 0) {
            topBar

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(flow.session.rooms) { room in
                        let on = room.id == flow.selectedRoomId
                        Button {
                            flow.selectedRoomId = room.id
                        } label: {
                            Text("\(room.name) · \(room.n)")
                                .font(BricksFont.mono(10))
                                .foregroundStyle(on ? CaptureChrome.moon : CaptureChrome.moon.opacity(0.67))
                                .padding(.horizontal, 10)
                                .padding(.vertical, 6)
                                .overlay(
                                    RoundedRectangle(cornerRadius: 2)
                                        .stroke(on ? CaptureChrome.brick : CaptureChrome.moon.opacity(0.19), lineWidth: 1)
                                )
                                .background(on ? CaptureChrome.brick.opacity(0.20) : Color.clear)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.top, 10)
            }

            HStack(spacing: 8) {
                TextField("Kitchen, Bedroom…", text: $flow.newRoomName)
                    .textInputAutocapitalization(.words)
                    .foregroundStyle(CaptureChrome.moon)
                    .padding(.horizontal, 10)
                    .frame(height: 36)
                    .background(Color(hex: "#141110").opacity(0.6))
                    .overlay(
                        RoundedRectangle(cornerRadius: 2)
                            .stroke(CaptureChrome.moon.opacity(0.13), lineWidth: 1)
                    )
                    .clipShape(RoundedRectangle(cornerRadius: 2))
                    .onSubmit { Task { await flow.addRoom() } }

                Button {
                    Task { await flow.addRoom() }
                } label: {
                    Text(flow.addingRoom ? "…" : "+ Room")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 12)
                        .frame(height: 36)
                        .background(CaptureChrome.brick)
                        .clipShape(RoundedRectangle(cornerRadius: 2))
                }
                .buttonStyle(.plain)
                .disabled(flow.addingRoom || flow.purchasedBlocked)
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)

            Spacer()

            FloorplanCornerBox(captured: roomFrames)
            Text("\(roomFrames) / \(CaptureDome.floorplanCorners)")
                .font(BricksFont.mono(22))
                .foregroundStyle(CaptureChrome.moon)
                .padding(.top, 4)
            Text("Station frames this room")
                .font(BricksFont.mono(9))
                .tracking(1.5)
                .textCase(.uppercase)
                .foregroundStyle(CaptureChrome.moon.opacity(0.67))

            Spacer()

            if let note = flow.hardware.headingNote {
                Text(note)
                    .font(.system(size: 11))
                    .foregroundStyle(CaptureChrome.moon.opacity(0.7))
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 20)
                    .padding(.bottom, 8)
            }

            CaptureGuidanceBanner(title: cue.title, bodyText: cue.body)

            Text("\(flow.frameCount) station frames · compiled on server")
                .font(BricksFont.mono(10.5))
                .foregroundStyle(CaptureChrome.moon)
                .padding(.top, 8)

            if let err = flow.error {
                CaptureErrorBanner(text: err)
                    .padding(.top, 8)
            }

            HStack {
                Color.clear.frame(width: 56, height: 44)
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
                Text("FLOOR PLAN · \(roomLabel)")
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

    private var roomLabel: String {
        (flow.currentRoom?.name ?? "Room 1").uppercased()
    }

    private var roomFrames: Int {
        flow.currentRoom?.n ?? 0
    }

    private var cue: (title: String, body: String) {
        cues[min(roomFrames, cues.count - 1)]
    }
}
