import SwiftUI
import AVFoundation
import BricksCore

// MARK: — SplatCaptureView

struct SplatCaptureView: View {
    let jobId: String
    var onDismiss: (() -> Void)?

    @StateObject private var recorder = SplatRecorder()
    @State private var showUploadSheet = false
    @State private var uploadState: UploadState = .idle

    enum UploadState {
        case idle, uploading, success, failure(String)
    }

    var body: some View {
        ZStack {
            // Full-screen camera preview
            Color(hex: "#141210").ignoresSafeArea()
            CameraPreviewView(session: recorder.session)
                .ignoresSafeArea()

            // Dark vignette so overlays stay readable
            LinearGradient(
                colors: [
                    Color.black.opacity(0.55),
                    Color.clear,
                    Color.clear,
                    Color.black.opacity(0.72),
                ],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea()

            VStack(spacing: 0) {
                // ── Top bar ──────────────────────────────────────────
                topBar

                Spacer()

                // ── Center ring + timer ──────────────────────────────
                ringTimer

                Spacer()

                // ── Guidance text ────────────────────────────────────
                guidanceLabel

                // ── Record + cancel ──────────────────────────────────
                bottomControls
            }
            .padding(.top, 60)
            .padding(.bottom, 44)
        }
        .onAppear { recorder.start() }
        .onDisappear { recorder.stop() }
        .onChange(of: recorder.recordingFinished) { finished in
            if finished { showUploadSheet = true }
        }
        .sheet(isPresented: $showUploadSheet) {
            uploadSheet
        }
    }

    // MARK: — Top bar

    private var topBar: some View {
        HStack {
            Button(action: { onDismiss?() }) {
                Text("CANCEL")
                    .font(.system(size: 11, weight: .semibold, design: .monospaced))
                    .tracking(1.4)
                    .foregroundStyle(Color(hex: "#F4F1EB").opacity(0.7))
            }
            .padding(.leading, 24)

            Spacer()

            Text("3D CAPTURE")
                .font(.system(size: 11, weight: .semibold, design: .monospaced))
                .tracking(2)
                .foregroundStyle(Color(hex: "#D2542B"))
                .padding(.horizontal, 14)
                .padding(.vertical, 6)
                .background(Color(hex: "#D2542B").opacity(0.14))
                .clipShape(RoundedRectangle(cornerRadius: 2))

            Spacer()

            // Balance the cancel button
            Text("CANCEL")
                .font(.system(size: 11, weight: .semibold, design: .monospaced))
                .tracking(1.4)
                .foregroundStyle(.clear)
                .padding(.trailing, 24)
        }
    }

    // MARK: — Ring + timer

    private var ringTimer: some View {
        ZStack {
            // Track circle
            Circle()
                .stroke(Color(hex: "#F4F1EB").opacity(0.10), lineWidth: 5)
                .frame(width: 180, height: 180)

            // Progress arc (brick color)
            Circle()
                .trim(from: 0, to: recorder.progress)
                .stroke(
                    Color(hex: "#D2542B"),
                    style: StrokeStyle(lineWidth: 5, lineCap: .round)
                )
                .frame(width: 180, height: 180)
                .rotationEffect(.degrees(-90))
                .animation(.linear(duration: 1), value: recorder.progress)

            // Timer label
            VStack(spacing: 4) {
                Text(recorder.timerLabel)
                    .font(.system(size: 38, weight: .thin, design: .monospaced))
                    .foregroundStyle(Color(hex: "#F4F1EB"))
                    .monospacedDigit()

                if recorder.isRecording {
                    HStack(spacing: 6) {
                        Circle()
                            .fill(Color(hex: "#D2542B"))
                            .frame(width: 6, height: 6)
                            .opacity(recorder.blinkOn ? 1 : 0)
                            .animation(.easeInOut(duration: 0.5).repeatForever(), value: recorder.blinkOn)
                        Text("REC")
                            .font(.system(size: 9, weight: .semibold, design: .monospaced))
                            .tracking(1.8)
                            .foregroundStyle(Color(hex: "#D2542B"))
                    }
                } else {
                    Text("90 SEC MAX")
                        .font(.system(size: 9, weight: .regular, design: .monospaced))
                        .tracking(1.4)
                        .foregroundStyle(Color(hex: "#F4F1EB").opacity(0.4))
                }
            }
        }
    }

    // MARK: — Guidance text

    private var guidanceLabel: some View {
        Text(recorder.currentGuidance)
            .font(.system(size: 14, weight: .regular, design: .monospaced))
            .foregroundStyle(Color(hex: "#F4F1EB").opacity(0.85))
            .multilineTextAlignment(.center)
            .padding(.horizontal, 32)
            .padding(.bottom, 32)
            .animation(.easeInOut(duration: 0.5), value: recorder.currentGuidance)
            .id(recorder.currentGuidance) // force re-render with transition
            .transition(.opacity)
    }

    // MARK: — Bottom controls

    private var bottomControls: some View {
        VStack(spacing: 24) {
            // Record button
            Button(action: { recorder.toggleRecording() }) {
                ZStack {
                    Circle()
                        .stroke(Color(hex: "#F4F1EB"), lineWidth: 3)
                        .frame(width: 80, height: 80)

                    if recorder.isRecording {
                        // Stop square
                        RoundedRectangle(cornerRadius: 4)
                            .fill(Color(hex: "#D2542B"))
                            .frame(width: 28, height: 28)
                    } else {
                        // Record circle
                        Circle()
                            .fill(Color(hex: "#D2542B"))
                            .frame(width: 64, height: 64)
                    }
                }
            }
            .accessibilityLabel(recorder.isRecording ? "Stop recording" : "Start recording")
            .scaleEffect(recorder.isRecording ? 0.95 : 1.0)
            .animation(.easeInOut(duration: 0.15), value: recorder.isRecording)

            // Cancel text
            Button(action: { onDismiss?() }) {
                Text("CANCEL")
                    .font(.system(size: 11, weight: .semibold, design: .monospaced))
                    .tracking(1.8)
                    .foregroundStyle(Color(hex: "#F4F1EB").opacity(0.55))
            }
            .accessibilityLabel("Cancel 3D capture")
        }
    }

    // MARK: — Upload confirmation sheet

    @ViewBuilder
    private var uploadSheet: some View {
        VStack(spacing: 0) {
            // Handle
            Capsule()
                .fill(Color(hex: "#F4F1EB").opacity(0.2))
                .frame(width: 40, height: 4)
                .padding(.top, 14)
                .padding(.bottom, 28)

            switch uploadState {
            case .idle:
                idleUploadContent
            case .uploading:
                uploadingContent
            case .success:
                successContent
            case .failure(let msg):
                failureContent(msg)
            }

            Spacer()
        }
        .presentationDetents([.fraction(0.45)])
        .presentationDragIndicator(.hidden)
        .background(Color(hex: "#1C1A17").ignoresSafeArea())
        .interactiveDismissDisabled(uploadState == .uploading)
    }

    private var idleUploadContent: some View {
        VStack(spacing: 20) {
            Text("3D CAPTURE COMPLETE")
                .font(.system(size: 11, weight: .semibold, design: .monospaced))
                .tracking(1.8)
                .foregroundStyle(Color(hex: "#D2542B"))

            Text("Upload this \(recorder.recordingDurationLabel) capture\nfor Gaussian splat processing.")
                .font(.system(size: 15, weight: .regular))
                .foregroundStyle(Color(hex: "#F4F1EB"))
                .multilineTextAlignment(.center)
                .lineSpacing(4)

            Text("Processing takes approx. 20 minutes.\nYou'll be notified when the 3D tour is ready.")
                .font(.system(size: 12))
                .foregroundStyle(Color(hex: "#F4F1EB").opacity(0.45))
                .multilineTextAlignment(.center)
                .lineSpacing(3)

            Button(action: { Task { await uploadVideo() } }) {
                Text("UPLOAD FOR PROCESSING")
                    .font(.system(size: 13, weight: .semibold, design: .monospaced))
                    .tracking(1.2)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 16)
                    .background(Color(hex: "#D2542B"))
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 2))
            }
            .padding(.horizontal, 28)
            .padding(.top, 8)

            Button(action: {
                showUploadSheet = false
                recorder.discardRecording()
                onDismiss?()
            }) {
                Text("Discard")
                    .font(.system(size: 13))
                    .foregroundStyle(Color(hex: "#F4F1EB").opacity(0.4))
            }
        }
        .padding(.horizontal, 28)
    }

    private var uploadingContent: some View {
        VStack(spacing: 20) {
            ProgressView()
                .progressViewStyle(.circular)
                .tint(Color(hex: "#D2542B"))
                .scaleEffect(1.4)
                .padding(.top, 12)

            Text("Uploading capture…")
                .font(.system(size: 15))
                .foregroundStyle(Color(hex: "#F4F1EB"))

            Text("Keep the app open.")
                .font(.system(size: 12))
                .foregroundStyle(Color(hex: "#F4F1EB").opacity(0.4))
        }
    }

    private var successContent: some View {
        VStack(spacing: 16) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 48))
                .foregroundStyle(Color(hex: "#D2542B"))

            Text("Upload complete")
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(Color(hex: "#F4F1EB"))

            Text("Gaussian splat processing has started.\nYour 3D tour will be ready in ~20 min.")
                .font(.system(size: 13))
                .foregroundStyle(Color(hex: "#F4F1EB").opacity(0.5))
                .multilineTextAlignment(.center)
                .lineSpacing(3)

            Button(action: {
                showUploadSheet = false
                onDismiss?()
            }) {
                Text("DONE")
                    .font(.system(size: 13, weight: .semibold, design: .monospaced))
                    .tracking(1.4)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 16)
                    .background(Color(hex: "#D2542B"))
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 2))
            }
            .padding(.horizontal, 28)
            .padding(.top, 4)
        }
    }

    private func failureContent(_ message: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 40))
                .foregroundStyle(Color(hex: "#D2542B").opacity(0.8))

            Text("Upload failed")
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(Color(hex: "#F4F1EB"))

            Text(message)
                .font(.system(size: 12))
                .foregroundStyle(Color(hex: "#F4F1EB").opacity(0.45))
                .multilineTextAlignment(.center)

            HStack(spacing: 12) {
                Button(action: {
                    showUploadSheet = false
                    recorder.discardRecording()
                    onDismiss?()
                }) {
                    Text("Discard")
                        .font(.system(size: 13))
                        .foregroundStyle(Color(hex: "#F4F1EB").opacity(0.4))
                }

                Button(action: { Task { await uploadVideo() } }) {
                    Text("RETRY")
                        .font(.system(size: 13, weight: .semibold, design: .monospaced))
                        .tracking(1.2)
                        .padding(.horizontal, 28)
                        .padding(.vertical, 14)
                        .background(Color(hex: "#D2542B"))
                        .foregroundStyle(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 2))
                }
            }
            .padding(.top, 4)
        }
        .padding(.horizontal, 28)
    }

    // MARK: — Upload

    private func uploadVideo() async {
        guard let url = recorder.outputURL else { return }
        uploadState = .uploading

        do {
            let data = try Data(contentsOf: url)
            let apiBase = URL(string: BricksConfig.apiBaseURL)!
            let endpoint = apiBase.appendingPathComponent(
                "api/v1/post-production/splat-upload/\(jobId)"
            )

            var request = URLRequest(url: endpoint)
            request.httpMethod = "POST"
            if let token = await BricksAuth.shared.accessToken {
                request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            }

            let boundary = "BricksGO-\(UUID().uuidString)"
            request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

            var body = Data()
            let filename = "splat_\(jobId).mov"
            body.append("--\(boundary)\r\n".data(using: .utf8)!)
            body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n".data(using: .utf8)!)
            body.append("Content-Type: video/quicktime\r\n\r\n".data(using: .utf8)!)
            body.append(data)
            body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
            request.httpBody = body

            let config = URLSessionConfiguration.default
            config.timeoutIntervalForRequest = 300 // 5-min timeout for large video
            let session = URLSession(configuration: config)
            let (_, response) = try await session.data(for: request)
            let http = response as! HTTPURLResponse

            if (200...299).contains(http.statusCode) {
                uploadState = .success
            } else {
                uploadState = .failure("Server returned \(http.statusCode). Check your connection and try again.")
            }
        } catch {
            uploadState = .failure(error.localizedDescription)
        }
    }
}

// MARK: — Camera Preview (UIViewRepresentable)

struct CameraPreviewView: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context: Context) -> PreviewUIView {
        let view = PreviewUIView()
        view.previewLayer.session = session
        view.previewLayer.videoGravity = .resizeAspectFill
        return view
    }

    func updateUIView(_ uiView: PreviewUIView, context: Context) {}

    final class PreviewUIView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var previewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
    }
}

// MARK: — SplatRecorder (ObservableObject)

@MainActor
final class SplatRecorder: NSObject, ObservableObject {
    // Camera session
    let session = AVCaptureSession()
    private let movieOutput = AVCaptureMovieFileOutput()
    private var sessionQueue = DispatchQueue(label: "splat.camera.session", qos: .userInitiated)

    // Recording state
    @Published var isRecording = false
    @Published var recordingFinished = false
    @Published var elapsedSeconds: Int = 0
    @Published var blinkOn = false
    @Published var currentGuidance: String = guidanceSteps[0]

    // Output
    private(set) var outputURL: URL?
    private var recordingTimer: Timer?
    private var guidanceTimer: Timer?
    private var guidanceIndex = 0
    private var totalElapsed: Int = 0

    private static let maxDuration = 90

    static let guidanceSteps = [
        "Walk around the exterior slowly",
        "Move indoors — orbit each main room",
        "Keep camera level and steady",
        "Capture corners and ceilings",
    ]

    var progress: CGFloat {
        CGFloat(elapsedSeconds) / CGFloat(Self.maxDuration)
    }

    var timerLabel: String {
        let m = elapsedSeconds / 60
        let s = elapsedSeconds % 60
        return String(format: "%02d:%02d", m, s)
    }

    var recordingDurationLabel: String {
        let s = elapsedSeconds
        if s < 60 { return "\(s)s" }
        return "\(s / 60)m \(s % 60)s"
    }

    // MARK: — Session setup

    func start() {
        sessionQueue.async { [weak self] in
            self?.configureSession()
        }
    }

    func stop() {
        stopTimers()
        sessionQueue.async { [weak self] in
            self?.session.stopRunning()
        }
    }

    private func configureSession() {
        session.beginConfiguration()
        session.sessionPreset = .high

        // Video input
        guard
            let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back),
            let videoInput = try? AVCaptureDeviceInput(device: device),
            session.canAddInput(videoInput)
        else {
            session.commitConfiguration()
            return
        }
        session.addInput(videoInput)

        // Audio input
        if let audioDevice = AVCaptureDevice.default(for: .audio),
           let audioInput = try? AVCaptureDeviceInput(device: audioDevice),
           session.canAddInput(audioInput) {
            session.addInput(audioInput)
        }

        // Movie output
        if session.canAddOutput(movieOutput) {
            session.addOutput(movieOutput)
            // Set max recording duration
            movieOutput.maxRecordedDuration = CMTime(
                seconds: Double(Self.maxDuration),
                preferredTimescale: 600
            )
        }

        session.commitConfiguration()
        session.startRunning()
    }

    // MARK: — Recording control

    func toggleRecording() {
        if isRecording {
            stopRecording()
        } else {
            startRecording()
        }
    }

    private func startRecording() {
        guard session.isRunning, !movieOutput.isRecording else { return }
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("splat_\(UUID().uuidString).mov")
        outputURL = url
        movieOutput.startRecording(to: url, recordingDelegate: self)
        isRecording = true
        elapsedSeconds = 0
        startTimers()
    }

    private func stopRecording() {
        guard movieOutput.isRecording else { return }
        movieOutput.stopRecording()
        // isRecording → false set in delegate callback
    }

    func discardRecording() {
        if let url = outputURL {
            try? FileManager.default.removeItem(at: url)
            outputURL = nil
        }
        recordingFinished = false
        elapsedSeconds = 0
    }

    // MARK: — Timers

    private func startTimers() {
        recordingTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.elapsedSeconds += 1
                self.blinkOn.toggle()
                // Auto-stop at 90s
                if self.elapsedSeconds >= Self.maxDuration {
                    self.stopRecording()
                }
            }
        }

        guidanceTimer = Timer.scheduledTimer(withTimeInterval: 8, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.guidanceIndex = (self.guidanceIndex + 1) % Self.guidanceSteps.count
                self.currentGuidance = Self.guidanceSteps[self.guidanceIndex]
            }
        }
    }

    private func stopTimers() {
        recordingTimer?.invalidate()
        recordingTimer = nil
        guidanceTimer?.invalidate()
        guidanceTimer = nil
    }
}

// MARK: — AVCaptureFileOutputRecordingDelegate

extension SplatRecorder: AVCaptureFileOutputRecordingDelegate {
    nonisolated func fileOutput(
        _ output: AVCaptureFileOutput,
        didFinishRecordingTo outputFileURL: URL,
        from connections: [AVCaptureConnection],
        error: Error?
    ) {
        Task { @MainActor [weak self] in
            guard let self else { return }
            self.isRecording = false
            self.stopTimers()
            if error == nil {
                self.recordingFinished = true
            }
        }
    }
}

// MARK: — Color hex helper (local)

private extension Color {
    init(hex: String) {
        let hex = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var int: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&int)
        let r = Double((int >> 16) & 0xFF) / 255
        let g = Double((int >> 8)  & 0xFF) / 255
        let b = Double(int         & 0xFF) / 255
        self.init(red: r, green: g, blue: b)
    }
}
