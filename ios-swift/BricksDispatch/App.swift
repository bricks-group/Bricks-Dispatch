import SwiftUI
import UIKit
import BricksCore

// MARK: — UIApplicationDelegate bridge (APNs token + silent push)

final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        PushNotificationService.shared.registerDeviceToken(deviceToken)
    }

    func application(
        _ application: UIApplication,
        didReceiveRemoteNotification userInfo: [AnyHashable: Any],
        fetchCompletionHandler completionHandler: @escaping (UIBackgroundFetchResult) -> Void
    ) {
        Task { @MainActor in
            PushNotificationService.shared.handleRemoteNotification(userInfo)
        }
        completionHandler(.newData)
    }
}

@main
struct BricksDispatchApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    @StateObject private var auth = BricksAuth.shared
    @StateObject private var jobStore = JobStore.shared
    @StateObject private var watchBridge = WatchConnectivityManager.shared

    init() {
        BricksConfig.apiBaseURL = "https://dispatch-api.brxs.ca"
        BricksAuthConfig.supabaseAnonKey = Bundle.main.infoPlistValue(for: "SUPABASE_ANON_KEY")
        PushNotificationService.shared.configure()
    }

    var body: some Scene {
        WindowGroup {
            Group {
                if auth.isLoading {
                    SplashView()
                } else if auth.isAuthenticated {
                    MainTabView()
                        .task { await jobStore.loadTodayJobs() }
                        .onChange(of: auth.session) { _ in
                            if auth.isAuthenticated { Task { await jobStore.loadTodayJobs() } }
                        }
                } else {
                    LoginView()
                }
            }
            .task { await auth.restoreSession() }
        }
    }
}

struct SplashView: View {
    var body: some View {
        ZStack {
            Color(.systemBackground).ignoresSafeArea()
            BricksLogoMark(size: 3)
                .symbolEffect(.pulse)
        }
    }
}

struct MainTabView: View {
    var body: some View {
        TabView {
            TodayJobsView()
                .tabItem { Label("Today", systemImage: "calendar") }

            EarningsView()
                .tabItem { Label("Earnings", systemImage: "banknote") }

            ProfileView()
                .tabItem { Label("Profile", systemImage: "person.circle") }
        }
        .tint(BricksColors.brick)
    }
}

// MARK: — Brand mark

struct BricksLogoMark: View {
    let size: CGFloat
    var color: Color = BricksColors.brick

    var body: some View {
        let h     = 7 * size
        let wTop  = 11 * size
        let wBotL = 6  * size
        let wBotR = 10 * size
        let rGap  = 2  * size
        let cGap  = max(1, size)
        let r     = 1.2 * size

        return ZStack(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: r).fill(color)
                .frame(width: wTop, height: h)
                .offset(x: 0, y: 0)
            RoundedRectangle(cornerRadius: r).fill(color)
                .frame(width: wBotL, height: h)
                .offset(x: 0, y: h + rGap)
            RoundedRectangle(cornerRadius: r).fill(color)
                .frame(width: wBotR, height: h)
                .offset(x: wBotL + cGap, y: h + rGap)
        }
        .frame(width: wBotL + cGap + wBotR, height: h + rGap + h)
    }
}

extension Bundle {
    func infoPlistValue(for key: String) -> String {
        (infoDictionary?[key] as? String) ?? ""
    }
}
