import SwiftUI
import BricksCore

struct NotificationsView: View {
    @StateObject private var store = NotificationsStore()

    var body: some View {
        Group {
            if store.isLoading && store.notifications.isEmpty {
                ProgressView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if store.notifications.isEmpty {
                emptyState
            } else {
                List {
                    ForEach(store.notifications) { n in
                        NotificationRow(notification: n)
                            .listRowInsets(EdgeInsets(top: 0, leading: 16, bottom: 0, trailing: 16))
                    }
                }
                .listStyle(.plain)
            }
        }
        .navigationTitle("Notifications")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if !store.notifications.isEmpty {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Mark all read") {
                        Task { await store.markAllRead() }
                    }
                    .font(BricksFont.mono(13))
                    .tint(BricksColors.brick)
                }
            }
        }
        .task { await store.load() }
        .refreshable { await store.load() }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "bell.slash")
                .font(.system(size: 40))
                .foregroundStyle(Color(.tertiaryLabel))
            Text("No notifications")
                .font(.headline)
                .foregroundStyle(Color(.secondaryLabel))
            Text("Job alerts and updates appear here")
                .font(.subheadline)
                .foregroundStyle(Color(.tertiaryLabel))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

// MARK: — Row

private struct NotificationRow: View {
    let notification: BricksNotification

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            ZStack {
                Circle()
                    .fill(iconBg)
                    .frame(width: 36, height: 36)
                Image(systemName: iconName)
                    .font(.system(size: 15))
                    .foregroundStyle(iconFg)
            }
            .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 4) {
                Text(notification.title)
                    .font(.system(size: 14, weight: notification.isRead ? .regular : .semibold))
                if let body = notification.body, !body.isEmpty {
                    Text(body)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
                Text(relativeTime)
                    .font(BricksFont.mono(10))
                    .foregroundStyle(Color(.tertiaryLabel))
                    .padding(.top, 2)
            }

            if !notification.isRead {
                Spacer()
                Circle()
                    .fill(BricksColors.brick)
                    .frame(width: 7, height: 7)
                    .padding(.top, 6)
                    .accessibilityHidden(true)
            }
        }
        .padding(.vertical, 12)
    }

    private var iconName: String {
        switch notification.notificationType {
        case "job_assigned":    return "briefcase"
        case "job_completed":   return "checkmark.circle"
        case "payout":          return "banknote"
        case "reminder":        return "clock"
        default:                return "bell"
        }
    }

    private var iconBg: Color {
        switch notification.notificationType {
        case "payout":          return .green.opacity(0.15)
        case "job_assigned":    return BricksColors.brick.opacity(0.12)
        default:                return Color(.secondarySystemBackground)
        }
    }

    private var iconFg: Color {
        switch notification.notificationType {
        case "payout":          return .green
        case "job_assigned":    return BricksColors.brick
        default:                return Color(.secondaryLabel)
        }
    }

    private var relativeTime: String {
        let f = ISO8601DateFormatter()
        guard let date = f.date(from: notification.createdAt) else { return notification.createdAt }
        let diff = Date().timeIntervalSince(date)
        if diff < 60 { return "Just now" }
        if diff < 3600 { return "\(Int(diff / 60))m ago" }
        if diff < 86400 { return "\(Int(diff / 3600))h ago" }
        return "\(Int(diff / 86400))d ago"
    }
}

// MARK: — Model

struct BricksNotification: Identifiable, Decodable {
    let id: String
    let title: String
    let body: String?
    let notificationType: String
    let createdAt: String
    var isRead: Bool
}

// MARK: — Store

@MainActor
final class NotificationsStore: ObservableObject {
    @Published var notifications: [BricksNotification] = []
    @Published var isLoading = false

    private let api = BricksAPIClient.shared

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            notifications = try await api.get("/api/notifications")
        } catch {}
    }

    func markAllRead() async {
        do {
            struct Empty: Decodable {}
            let _: Empty = try await api.post("/api/notifications/mark-all-read", body: EmptyBody())
            notifications = notifications.map {
                var n = $0; n.isRead = true; return n
            }
        } catch {}
    }
}

private struct EmptyBody: Encodable {}
