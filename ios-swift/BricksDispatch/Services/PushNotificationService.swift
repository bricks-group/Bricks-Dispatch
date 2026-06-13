import UIKit
import UserNotifications
import BricksCore

final class PushNotificationService: NSObject {
    static let shared = PushNotificationService()

    func configure() {
        UNUserNotificationCenter.current().delegate = self
        Task {
            try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])
            await MainActor.run {
                UIApplication.shared.registerForRemoteNotifications()
            }
        }
    }

    func registerDeviceToken(_ tokenData: Data) {
        let token = tokenData.map { String(format: "%02x", $0) }.joined()
        Task {
            try? await BricksAPIClient.shared.postVoid(
                "/api/vendor-os/push-token",
                body: ["device_token": token, "platform": "ios"]
            )
        }
    }

    /// Route an incoming APNs payload to the appropriate handler.
    /// Called by the UIApplicationDelegate bridge in App.swift.
    @MainActor
    func handleRemoteNotification(_ userInfo: [AnyHashable: Any]) {
        guard let type = userInfo["type"] as? String else { return }
        switch type {
        case "job_assigned":
            if let jobId = userInfo["job_id"] as? String {
                Task { await JobStore.shared.receivePushOffer(jobId: jobId) }
            }
        case "job_status_update":
            Task { await JobStore.shared.loadTodayJobs() }
        default:
            break
        }
    }
}

extension PushNotificationService: UNUserNotificationCenterDelegate {
    // Show banner even when app is foregrounded
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound, .badge]
    }

    // Tap on a notification while app is in background/killed
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        await handleRemoteNotification(response.notification.request.content.userInfo)
    }
}

