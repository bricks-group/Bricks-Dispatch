import UIKit
import UserNotifications

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
}

extension PushNotificationService: UNUserNotificationCenterDelegate {
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound, .badge]
    }
}
