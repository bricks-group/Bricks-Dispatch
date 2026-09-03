import AVFoundation
import CoreLocation
import SwiftUI
import UIKit

/// Back camera + compass heading for GO capture.
/// Heading is optional: if CoreLocation heading is missing, yaw is 0 (honest).
final class CaptureHardware: NSObject, ObservableObject {
    @Published var cameraStatus: CameraStatus = .idle
    @Published var headingDegrees: Double = 0
    @Published var headingAvailable = false
    @Published var headingNote: String?

    enum CameraStatus: Equatable {
        case idle
        case requesting
        case ready
        case denied
        case unavailable(String)
    }

    let captureSession = AVCaptureSession()
    private let sessionQueue = DispatchQueue(label: "ca.brxs.go.capture")
    private let photoOutput = AVCapturePhotoOutput()
    private var photoDelegate: PhotoSink?
    private let locationManager = CLLocationManager()
    private var configured = false

    override init() {
        super.init()
        locationManager.delegate = self
        locationManager.headingFilter = 1
        locationManager.desiredAccuracy = kCLLocationAccuracyBest
    }

    func start() async {
        await startCamera()
        startHeading()
    }

    func stop() {
        sessionQueue.async { [weak self] in
            self?.captureSession.stopRunning()
        }
        locationManager.stopUpdatingHeading()
        locationManager.stopUpdatingLocation()
    }

    func requestCameraAccess() async {
        await startCamera()
    }

    func takeJPEG(quality: CGFloat = 0.8) async throws -> Data {
        let status = await MainActor.run { cameraStatus }
        guard status == .ready else {
            throw CaptureHardwareError.cameraNotReady
        }
        let data: Data = try await withCheckedThrowingContinuation { cont in
            sessionQueue.async { [weak self] in
                guard let self else {
                    cont.resume(throwing: CaptureHardwareError.cameraNotReady)
                    return
                }
                let settings = AVCapturePhotoSettings()
                let sink = PhotoSink { result in
                    cont.resume(with: result)
                }
                self.photoDelegate = sink
                self.photoOutput.capturePhoto(with: settings, delegate: sink)
            }
        }
        if let img = UIImage(data: data), let jpeg = img.jpegData(compressionQuality: quality) {
            return jpeg
        }
        return data
    }

    // MARK: — Camera

    private func startCamera() async {
        await MainActor.run { cameraStatus = .requesting }
        let granted: Bool
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            granted = true
        case .notDetermined:
            granted = await AVCaptureDevice.requestAccess(for: .video)
        default:
            granted = false
        }
        guard granted else {
            await MainActor.run { cameraStatus = .denied }
            return
        }
        let error: String? = await withCheckedContinuation { cont in
            sessionQueue.async { [weak self] in
                guard let self else {
                    cont.resume(returning: "Camera stopped")
                    return
                }
                do {
                    try self.configureLocked()
                    if !self.captureSession.isRunning {
                        self.captureSession.startRunning()
                    }
                    cont.resume(returning: nil)
                } catch {
                    cont.resume(returning: error.localizedDescription)
                }
            }
        }
        await MainActor.run {
            if let error {
                cameraStatus = .unavailable(error)
            } else {
                cameraStatus = .ready
            }
        }
    }

    private func configureLocked() throws {
        if configured { return }
        captureSession.beginConfiguration()
        captureSession.sessionPreset = .photo

        guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back)
                ?? AVCaptureDevice.default(for: .video) else {
            captureSession.commitConfiguration()
            throw CaptureHardwareError.noCamera
        }
        let input = try AVCaptureDeviceInput(device: device)
        guard captureSession.canAddInput(input) else {
            captureSession.commitConfiguration()
            throw CaptureHardwareError.noCamera
        }
        captureSession.addInput(input)

        guard captureSession.canAddOutput(photoOutput) else {
            captureSession.commitConfiguration()
            throw CaptureHardwareError.noCamera
        }
        captureSession.addOutput(photoOutput)
        photoOutput.maxPhotoQualityPrioritization = .quality
        configured = true
        captureSession.commitConfiguration()
    }

    // MARK: — Heading

    private func startHeading() {
        if !CLLocationManager.headingAvailable() {
            headingAvailable = false
            headingDegrees = 0
            headingNote = "Compass heading unavailable — yaw sent as 0."
            return
        }
        let status = locationManager.authorizationStatus
        if status == .notDetermined {
            locationManager.requestWhenInUseAuthorization()
        }
        locationManager.startUpdatingHeading()
        locationManager.startUpdatingLocation()
    }

    private func publishHeading(available: Bool, degrees: Double, note: String?) {
        if Thread.isMainThread {
            headingAvailable = available
            headingDegrees = degrees
            headingNote = note
        } else {
            DispatchQueue.main.async { [weak self] in
                self?.headingAvailable = available
                self?.headingDegrees = degrees
                self?.headingNote = note
            }
        }
    }
}

extension CaptureHardware: CLLocationManagerDelegate {
    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        switch manager.authorizationStatus {
        case .authorizedWhenInUse, .authorizedAlways:
            if CLLocationManager.headingAvailable() {
                manager.startUpdatingHeading()
                manager.startUpdatingLocation()
            }
        case .denied, .restricted:
            publishHeading(available: false, degrees: 0, note: "Location permission denied — compass heading unavailable, yaw sent as 0.")
        default:
            break
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateHeading newHeading: CLHeading) {
        if newHeading.headingAccuracy < 0 {
            publishHeading(available: false, degrees: 0, note: "Compass heading unavailable — yaw sent as 0.")
            return
        }
        let yaw = newHeading.trueHeading >= 0 ? newHeading.trueHeading : newHeading.magneticHeading
        publishHeading(available: true, degrees: yaw, note: nil)
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        if !headingAvailable {
            publishHeading(available: false, degrees: 0, note: "Compass heading unavailable — yaw sent as 0.")
        }
    }
}

enum CaptureHardwareError: LocalizedError {
    case noCamera
    case cameraNotReady

    var errorDescription: String? {
        switch self {
        case .noCamera: return "No back camera on this device. Capture needs a physical iPhone."
        case .cameraNotReady: return "Camera is not ready"
        }
    }
}

private final class PhotoSink: NSObject, AVCapturePhotoCaptureDelegate {
    let finish: (Result<Data, Error>) -> Void
    private var sent = false

    init(finish: @escaping (Result<Data, Error>) -> Void) {
        self.finish = finish
    }

    func photoOutput(_ output: AVCapturePhotoOutput, didFinishProcessingPhoto photo: AVCapturePhoto, error: Error?) {
        guard !sent else { return }
        sent = true
        if let error {
            finish(.failure(error))
            return
        }
        guard let data = photo.fileDataRepresentation() else {
            finish(.failure(CaptureHardwareError.cameraNotReady))
            return
        }
        finish(.success(data))
    }
}

struct CameraPreviewView: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context: Context) -> PreviewUIView {
        let view = PreviewUIView()
        view.previewLayer.session = session
        view.previewLayer.videoGravity = .resizeAspectFill
        return view
    }

    func updateUIView(_ uiView: PreviewUIView, context: Context) {
        if uiView.previewLayer.session !== session {
            uiView.previewLayer.session = session
        }
    }

    final class PreviewUIView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var previewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
    }
}
