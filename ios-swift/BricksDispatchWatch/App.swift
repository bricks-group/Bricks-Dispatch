import SwiftUI
import BricksCore
import WatchKit

@main
struct BricksDispatchWatchApp: App {
    @StateObject private var connectivity = WatchConnectivityManager.shared
    @StateObject private var watchStore = WatchJobStore()

    var body: some Scene {
        WindowGroup {
            WatchRootView()
                .environmentObject(watchStore)
                .environmentObject(connectivity)
        }
    }
}

// WatchRootView — shows active job, upcoming job, or idle state
struct WatchRootView: View {
    @EnvironmentObject var store: WatchJobStore
    @EnvironmentObject var connectivity: WatchConnectivityManager

    var body: some View {
        if let active = store.activeJob {
            ActiveJobView(job: active)
        } else if let next = store.nextJob {
            UpcomingJobView(job: next)
        } else {
            IdleView()
        }
    }
}

struct IdleView: View {
    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: "checkmark.circle.fill")
                .font(.largeTitle)
                .foregroundStyle(.green)
            Text("All clear")
                .font(.headline)
            Text("No upcoming jobs")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }
}
