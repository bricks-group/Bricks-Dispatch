import SwiftUI
import MapKit
import CoreLocation
import BricksCore

// MARK: — Navigation controller

@MainActor
final class NavigationController: NSObject, ObservableObject {

    @Published var userLocation: CLLocationCoordinate2D?
    @Published var route: MKRoute?
    @Published var currentStepIndex = 0
    @Published var etaString = ""
    @Published var distanceToNextString = ""
    @Published var lookAroundScene: MKLookAroundScene?
    @Published var state: NavState = .locating

    enum NavState { case locating, routing, navigating, arrived, error(String) }

    private let locationManager = CLLocationManager()
    private let destination: CLLocationCoordinate2D

    init(destination: CLLocationCoordinate2D) {
        self.destination = destination
        super.init()
        locationManager.delegate = self
        locationManager.desiredAccuracy = kCLLocationAccuracyBestForNavigation
        locationManager.requestWhenInUseAuthorization()
        locationManager.startUpdatingLocation()
        locationManager.startUpdatingHeading()
    }

    func startRouting() async {
        state = .routing
        async let routeTask: () = fetchRoute()
        async let lookTask: () = fetchLookAround()
        await routeTask
        await lookTask
        if case .error = state { return }
        state = .navigating
    }

    func markArrived() {
        locationManager.stopUpdatingLocation()
        locationManager.stopUpdatingHeading()
        state = .arrived
    }

    private func fetchRoute() async {
        guard let origin = userLocation else {
            state = .error("Location not available")
            return
        }
        let req = MKDirections.Request()
        req.source = MKMapItem(placemark: MKPlacemark(coordinate: origin))
        req.destination = MKMapItem(placemark: MKPlacemark(coordinate: destination))
        req.transportType = .automobile
        req.requestsAlternateRoutes = false

        do {
            let resp = try await MKDirections(request: req).calculate()
            guard let best = resp.routes.first else {
                state = .error("No route found")
                return
            }
            route = best
            currentStepIndex = 0
            refreshMetrics()
        } catch {
            state = .error("Route unavailable")
        }
    }

    private func fetchLookAround() async {
        lookAroundScene = try? await MKLookAroundSceneRequest(coordinate: destination).scene
    }

    private func refreshMetrics() {
        guard let route else { return }
        let arrival = Date().addingTimeInterval(route.expectedTravelTime)
        let fmt = DateFormatter()
        fmt.dateFormat = "h:mm a"
        etaString = fmt.string(from: arrival)

        let steps = route.steps.filter { !$0.instructions.isEmpty }
        if currentStepIndex < steps.count {
            distanceToNextString = formatDistance(steps[currentStepIndex].distance)
        }
    }

    private func formatDistance(_ m: CLLocationDistance) -> String {
        m < 1_000 ? "\(Int(m)) m" : String(format: "%.1f km", m / 1_000)
    }

    private func advanceStepIfNeeded(from userCoord: CLLocationCoordinate2D) {
        guard let route else { return }
        let steps = route.steps.filter { !$0.instructions.isEmpty }
        guard currentStepIndex < steps.count - 1 else { return }
        let stepEnd = steps[currentStepIndex].polyline.coordinate
        let d = CLLocation(latitude: userCoord.latitude, longitude: userCoord.longitude)
            .distance(from: CLLocation(latitude: stepEnd.latitude, longitude: stepEnd.longitude))
        if d < 40 {
            currentStepIndex += 1
            refreshMetrics()
        }
    }
}

extension NavigationController: CLLocationManagerDelegate {
    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        Task { @MainActor [weak self] in
            guard let self else { return }
            self.userLocation = loc.coordinate
            if case .locating = self.state {
                Task { await self.startRouting() }
            }
            self.advanceStepIfNeeded(from: loc.coordinate)
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didChangeAuthorization status: CLAuthorizationStatus) {
        if status == .denied || status == .restricted {
            Task { @MainActor [weak self] in
                self?.state = .error("Location access required for navigation.")
            }
        }
    }
}

// MARK: — Main view

struct JobNavigationView: View {

    let job: DispatchJob
    @Environment(\.dismiss) private var dismiss
    @StateObject private var nav: NavigationController
    @ObservedObject private var store = JobStore.shared

    @State private var cameraPosition: MapCameraPosition = .userLocation(fallback: .automatic)
    @State private var showLookAround = false
    @State private var routeFitted = false

    private var destination: CLLocationCoordinate2D {
        CLLocationCoordinate2D(latitude: job.lat, longitude: job.lng)
    }

    init(job: DispatchJob) {
        self.job = job
        _nav = StateObject(wrappedValue: NavigationController(
            destination: CLLocationCoordinate2D(latitude: job.lat, longitude: job.lng)
        ))
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            mapLayer
            overlays
        }
        .ignoresSafeArea()
        .statusBarHidden(false)
        .onChange(of: nav.route) { _, route in
            guard let route, !routeFitted else { return }
            routeFitted = true
            fitCamera(to: route)
        }
        .onChange(of: nav.state) { _, state in
            if case .arrived = state { handleArrived() }
        }
    }

    // MARK: — Map

    private var mapLayer: some View {
        Map(position: $cameraPosition) {
            // Live user location
            UserAnnotation()

            // Route polyline
            if let route = nav.route {
                MapPolyline(route.polyline)
                    .stroke(BricksColors.brick, style: StrokeStyle(lineWidth: 6, lineCap: .round, lineJoin: .round))
            }

            // Property pin
            Annotation(job.address, coordinate: destination) {
                PropertyPin()
            }
        }
        .mapStyle(.standard(elevation: .realistic, pointsOfInterest: .including([.parking])))
        .mapControls {
            MapCompass()
            MapUserLocationButton()
        }
    }

    // MARK: — Overlays

    private var overlays: some View {
        VStack(spacing: 0) {
            // Top bar
            HStack(alignment: .top) {
                // Dismiss
                Button {
                    nav.locationManager.stopUpdatingLocation()
                    dismiss()
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.title2)
                        .symbolRenderingMode(.hierarchical)
                        .foregroundStyle(.secondary)
                        .padding(16)
                }
                .background(.regularMaterial, in: Circle())

                Spacer()

                // Look Around thumbnail
                if nav.lookAroundScene != nil {
                    lookAroundThumbnail
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 56)

            Spacer()

            // Bottom instruction strip
            instructionStrip
        }
    }

    // MARK: — Look Around thumbnail

    private var lookAroundThumbnail: some View {
        Button { showLookAround = true } label: {
            Group {
                if let scene = nav.lookAroundScene {
                    LookAroundPreview(initialScene: scene)
                        .frame(width: 120, height: 80)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                        .overlay(
                            RoundedRectangle(cornerRadius: 12)
                                .stroke(.white.opacity(0.3), lineWidth: 1)
                        )
                }
            }
        }
        .padding(.top, 16)
        .padding(.trailing, 16)
        .shadow(radius: 8, y: 2)
        .fullScreenCover(isPresented: $showLookAround) {
            if let scene = nav.lookAroundScene {
                ZStack(alignment: .topTrailing) {
                    LookAroundPreview(initialScene: scene, allowsNavigation: true)
                        .ignoresSafeArea()
                    Button { showLookAround = false } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(.title2)
                            .symbolRenderingMode(.hierarchical)
                            .foregroundStyle(.white.opacity(0.8))
                            .padding(20)
                    }
                }
            }
        }
    }

    // MARK: — Instruction strip

    @ViewBuilder
    private var instructionStrip: some View {
        switch nav.state {
        case .locating:
            stripCard {
                HStack(spacing: 12) {
                    ProgressView().tint(BricksColors.brick)
                    Text("Getting your location…")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            }

        case .routing:
            stripCard {
                HStack(spacing: 12) {
                    ProgressView().tint(BricksColors.brick)
                    Text("Calculating route…")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            }

        case .navigating:
            if let route = nav.route {
                let steps = route.steps.filter { !$0.instructions.isEmpty }
                let step = steps.indices.contains(nav.currentStepIndex) ? steps[nav.currentStepIndex] : nil
                navigationStrip(step: step)
            }

        case .arrived:
            stripCard {
                VStack(spacing: 8) {
                    HStack(spacing: 8) {
                        Image(systemName: "mappin.circle.fill")
                            .foregroundStyle(.green)
                        Text("You've arrived")
                            .font(.headline)
                    }
                    Text(job.address)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

        case .error(let msg):
            stripCard {
                HStack(spacing: 10) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                    Text(msg)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private func navigationStrip(step: MKRoute.Step?) -> some View {
        VStack(spacing: 0) {
            // Current instruction
            HStack(spacing: 14) {
                // Turn icon
                Image(systemName: turnIcon(for: step))
                    .font(.title2.weight(.semibold))
                    .foregroundStyle(BricksColors.brick)
                    .frame(width: 36)

                VStack(alignment: .leading, spacing: 3) {
                    Text(step?.instructions ?? "Head to destination")
                        .font(.headline)
                        .lineLimit(2)
                    if !nav.distanceToNextString.isEmpty {
                        Text("In \(nav.distanceToNextString)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer()

                // ETA badge
                if !nav.etaString.isEmpty {
                    VStack(spacing: 1) {
                        Text(nav.etaString)
                            .font(.subheadline.monospacedDigit().bold())
                        Text("ETA")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 16)

            Divider()

            // Action buttons
            HStack(spacing: 12) {
                // Navigate in Apple Maps fallback
                Button {
                    let item = MKMapItem(placemark: MKPlacemark(coordinate: destination))
                    item.name = job.address
                    item.openInMaps(launchOptions: [MKLaunchOptionsDirectionsModeKey: MKLaunchOptionsDirectionsModeDriving])
                } label: {
                    Label("Apple Maps", systemImage: "arrow.triangle.turn.up.right.circle")
                        .font(.caption.weight(.medium))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 11)
                        .background(Color(.secondarySystemFill), in: RoundedRectangle(cornerRadius: 10))
                }
                .foregroundStyle(.primary)

                // Arrived
                Button {
                    Task {
                        nav.markArrived()
                        await store.markInProgress(job)
                        BricksHaptics.shared.bookingConfirmed()
                    }
                } label: {
                    Label("I'm Here", systemImage: "mappin.circle.fill")
                        .font(.subheadline.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 11)
                        .background(BricksColors.brick, in: RoundedRectangle(cornerRadius: 10))
                }
                .foregroundStyle(.white)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 14)
            .padding(.bottom, 4)
        }
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
        .padding(.horizontal, 12)
        .padding(.bottom, 40)
        .shadow(color: .black.opacity(0.12), radius: 16, y: 4)
    }

    private func stripCard<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        content()
            .padding(.horizontal, 20)
            .padding(.vertical, 18)
            .frame(maxWidth: .infinity)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
            .padding(.horizontal, 12)
            .padding(.bottom, 40)
            .shadow(color: .black.opacity(0.1), radius: 12, y: 4)
    }

    // MARK: — Camera

    private func fitCamera(to route: MKRoute) {
        let rect = route.polyline.boundingMapRect
        let inset = -rect.size.width * 0.18
        let padded = rect.insetBy(dx: inset, dy: inset)

        withAnimation(.easeInOut(duration: 1.1)) {
            cameraPosition = .rect(padded)
        }

        // After 1.8s, zoom into user position for navigation mode
        Task {
            try? await Task.sleep(for: .seconds(1.8))
            withAnimation(.easeInOut(duration: 1.2)) {
                cameraPosition = .userLocation(
                    heading: .constant(0),
                    fallback: .automatic
                )
            }
        }
    }

    // MARK: — Arrived

    private func handleArrived() {
        // Send watch arrived action
        WatchConnectivityManager.shared.sendMessage([
            "action": WatchAction.arrived,
            "jobId": job.id,
        ])
        // Short delay then dismiss back to detail
        Task {
            try? await Task.sleep(for: .seconds(1.5))
            dismiss()
        }
    }

    // MARK: — Turn icon helper

    private func turnIcon(for step: MKRoute.Step?) -> String {
        guard let step else { return "arrow.up.circle.fill" }
        let instr = step.instructions.lowercased()
        if instr.contains("left")           { return "arrow.turn.up.left" }
        if instr.contains("right")          { return "arrow.turn.up.right" }
        if instr.contains("u-turn")         { return "arrow.uturn.left" }
        if instr.contains("roundabout")     { return "arrow.clockwise.circle" }
        if instr.contains("merge")          { return "arrow.merge" }
        if instr.contains("highway") || instr.contains("ramp") { return "road.lanes" }
        if instr.contains("arrive")         { return "mappin.circle.fill" }
        return "arrow.up.circle.fill"
    }
}

// MARK: — Property pin

private struct PropertyPin: View {
    @State private var scale: CGFloat = 1
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            if !reduceMotion {
                Circle()
                    .fill(BricksColors.brick.opacity(0.2))
                    .frame(width: 44, height: 44)
                    .scaleEffect(scale)
                    .animation(.easeInOut(duration: 1.4).repeatForever(autoreverses: true), value: scale)
                    .onAppear { scale = 1.7 }
            }
            Circle()
                .fill(BricksColors.brick)
                .frame(width: 18, height: 18)
                .shadow(color: BricksColors.brick.opacity(0.5), radius: 6, y: 2)
            Image(systemName: "camera.fill")
                .font(.system(size: 8, weight: .bold))
                .foregroundStyle(.white)
        }
    }
}
