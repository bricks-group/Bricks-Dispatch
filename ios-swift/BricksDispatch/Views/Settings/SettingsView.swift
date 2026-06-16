import SwiftUI
import BricksCore

struct SettingsView: View {
    @AppStorage("push_notifications_enabled") private var pushEnabled = true
    @AppStorage("job_offer_alerts") private var offerAlerts = true
    @AppStorage("payout_alerts") private var payoutAlerts = true

    var body: some View {
        Form {
            Section("NOTIFICATIONS") {
                Toggle("Push Notifications", isOn: $pushEnabled)
                    .tint(BricksColors.brick)
                Toggle("New Job Offers", isOn: $offerAlerts)
                    .tint(BricksColors.brick)
                    .disabled(!pushEnabled)
                Toggle("Payout Confirmations", isOn: $payoutAlerts)
                    .tint(BricksColors.brick)
                    .disabled(!pushEnabled)
            }

            Section("NAVIGATION") {
                LabeledContent("Default Map App") {
                    Text("Apple Maps")
                        .foregroundStyle(.secondary)
                }
            }

            Section("ABOUT") {
                LabeledContent("App Version") {
                    Text(appVersion)
                        .foregroundStyle(.secondary)
                }
                LabeledContent("Build") {
                    Text(buildNumber)
                        .foregroundStyle(.secondary)
                }
                Link(destination: URL(string: "https://brxs.ca/privacy")!) {
                    Label("Privacy Policy", systemImage: "lock.shield")
                }
                .foregroundStyle(.primary)
                Link(destination: URL(string: "https://brxs.ca/terms")!) {
                    Label("Terms of Service", systemImage: "doc.text")
                }
                .foregroundStyle(.primary)
            }

            Section("SUPPORT") {
                Button {
                    if let url = URL(string: "mailto:support@brxs.ca") {
                        UIApplication.shared.open(url)
                    }
                } label: {
                    Label("Contact Support", systemImage: "envelope")
                }
                .foregroundStyle(.primary)
            }
        }
        .navigationTitle("Settings")
        .navigationBarTitleDisplayMode(.inline)
    }

    private var appVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "—"
    }

    private var buildNumber: String {
        Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "—"
    }
}
