import Foundation
import Combine
import SwiftUI
import BricksCore

@MainActor
final class CaptureFlowModel: ObservableObject {
    let job: DispatchJob
    let kind: CaptureKind

    @Published var session: GOCaptureSession
    @Published var error: String?
    @Published var errorCode: String?
    @Published var purchasedBlocked = false
    @Published var shutterPressed = false
    @Published var shutterBusy = false
    @Published var ticks: Set<Int> = []
    @Published var heading0: Double?
    @Published var selectedRoomId: String?
    @Published var newRoomName = ""
    @Published var addingRoom = false
    @Published var showReview = false
    @Published var submitting = false
    @Published var submitted = false

    let hardware = CaptureHardware()
    private var hardwareBag = Set<AnyCancellable>()

    init(job: DispatchJob, kind: CaptureKind) {
        self.job = job
        self.kind = kind
        self.session = .empty(kind: kind, address: job.address)
        hardware.objectWillChange
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in
                self?.objectWillChange.send()
            }
            .store(in: &hardwareBag)
    }

    var stationIndex: Int { session.currentStationIndex }
    var frameCount: Int { session.frameCount }
    var yaw: Double { hardware.headingAvailable ? hardware.headingDegrees : 0 }
    var aimTick: Int {
        CaptureDome.tick(yaw: yaw, origin: heading0 ?? yaw, total: CaptureDome.total)
    }

    var currentRoom: CaptureRoom? {
        if let id = selectedRoomId {
            return session.rooms.first { $0.id == id } ?? session.rooms.first
        }
        if let name = session.guidance?.room {
            return session.rooms.first { $0.name == name } ?? session.rooms.first
        }
        return session.rooms.first
    }

    func boot() async {
        let result = await CaptureAPIClient.shared.ensureSession(jobId: job.id, kind: kind)
        apply(result, resetTicks: false)
        if result.code == CaptureAPIClient.notPurchasedCode {
            purchasedBlocked = true
            return
        }
        await hardware.start()
    }

    func shutdown() {
        hardware.stop()
    }

    func shoot() async {
        guard !shutterBusy, !purchasedBlocked else { return }
        shutterBusy = true
        shutterPressed = true
        error = nil
        errorCode = nil
        defer {
            shutterBusy = false
            shutterPressed = false
        }
        let yawNow = yaw
        if heading0 == nil { heading0 = yawNow }
        let origin = heading0 ?? yawNow
        let tick = CaptureDome.tick(yaw: yawNow, origin: origin)
        do {
            let jpeg = try await hardware.takeJPEG(quality: 0.8)
            let result = await CaptureAPIClient.shared.uploadJPEG(
                jobId: job.id,
                jpeg: jpeg,
                roomId: selectedRoomId ?? session.currentRoomId ?? currentRoom?.id,
                kind: kind,
                stationIndex: stationIndex,
                yaw: yawNow,
                heading: yawNow,
                role: .pan
            )
            apply(result, resetTicks: false)
            if result.ok {
                if kind == .floorplan {
                    ticks.insert(ticks.count % CaptureDome.floorplanCorners)
                } else {
                    ticks.insert(tick)
                }
            }
        } catch {
            self.error = error.localizedDescription
        }
    }

    func newStation() async {
        guard !purchasedBlocked else { return }
        let result = await CaptureAPIClient.shared.openStation(
            jobId: job.id,
            kind: kind,
            roomId: selectedRoomId ?? session.currentRoomId,
            heading: yaw,
            increment: true
        )
        apply(result, resetTicks: result.ok)
    }

    func addRoom() async {
        let name = newRoomName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, !addingRoom else { return }
        addingRoom = true
        error = nil
        defer { addingRoom = false }
        let result = await CaptureAPIClient.shared.addRoom(jobId: job.id, name: name, kind: kind)
        apply(result, resetTicks: result.ok)
        if result.ok {
            newRoomName = ""
            if let last = result.session?.rooms.last {
                selectedRoomId = last.id
            }
        }
    }

    func refreshSession() async {
        let s = await CaptureAPIClient.shared.getSession(jobId: job.id, kind: kind)
        session = s
        if selectedRoomId == nil {
            selectedRoomId = s.currentRoomId ?? s.rooms.first?.id
        }
        if CaptureKind.isTerminalPipelineStatus(s.status) {
            submitted = true
        }
    }

    func submit() async -> Bool {
        submitting = true
        error = nil
        defer { submitting = false }
        let result = await CaptureAPIClient.shared.submit(jobId: job.id, kind: kind)
        apply(result, resetTicks: false)
        // Match Expo/web: show compiling / failed / ready even when ok:false.
        submitted = true
        return result.ok
    }

    func clearSubmittedForRecapture() {
        submitted = false
        error = nil
        errorCode = nil
    }

    private func apply(_ result: CaptureCallResult, resetTicks: Bool) {
        if let session = result.session {
            self.session = session
            if selectedRoomId == nil {
                selectedRoomId = session.currentRoomId ?? session.rooms.first?.id
            } else if let id = session.currentRoomId, kind == .floorplan, result.ok, resetTicks {
                selectedRoomId = id
            }
        }
        if result.ok {
            error = nil
            errorCode = nil
            if resetTicks {
                ticks = []
                heading0 = nil
            }
        } else {
            error = result.error
            errorCode = result.code
            if result.code == CaptureAPIClient.notPurchasedCode {
                purchasedBlocked = true
            }
        }
    }
}
