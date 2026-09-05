import Foundation

/// Paid /book windows (America/Toronto). Mirrors web `shoot-window.ts`.
public enum ShootWindow {
    private static let canonical: [(key: String, start: String, end: String, label: String)] = [
        ("morning", "09:00", "12:00", "Morning"),
        ("afternoon", "12:00", "16:00", "Afternoon"),
        ("evening", "16:00", "19:00", "Evening"),
    ]

    public static func clock(_ raw: String?) -> String {
        let s = (raw ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { return "" }
        if let m = s.range(of: #"^(\d{1,2}):(\d{2})"#, options: .regularExpression) {
            let parts = s[m].split(separator: ":")
            let h = String(parts[0]).padLeft(toLength: 2, withPad: "0")
            let min = String(parts[1].prefix(2))
            return "\(h):\(min)"
        }
        // ISO datetime → clock in Toronto
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var d = iso.date(from: s)
        if d == nil {
            iso.formatOptions = [.withInternetDateTime]
            d = iso.date(from: s)
        }
        if let d {
            let fmt = DateFormatter()
            fmt.locale = Locale(identifier: "en_GB")
            fmt.timeZone = TimeZone(identifier: "America/Toronto")
            fmt.dateFormat = "HH:mm"
            return fmt.string(from: d)
        }
        return String(s.prefix(5))
    }

    private static func windowLabel(preferred: String?, start: String, end: String) -> String {
        let key = (preferred ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if let hit = canonical.first(where: { $0.key == key }) { return hit.label }
        if !start.isEmpty && !end.isEmpty {
            if let hit = canonical.first(where: { $0.start == start && $0.end == end }) { return hit.label }
        }
        if !key.isEmpty {
            return key.prefix(1).uppercased() + key.dropFirst()
        }
        return ""
    }

    public static func format(
        preferredWindow: String?,
        timeWindowStart: String?,
        timeWindowEnd: String?,
        confirmedArriveAt: String? = nil
    ) -> String {
        let start = clock(timeWindowStart)
        let end = clock(timeWindowEnd)
        let confirmed = clock(confirmedArriveAt)
        let label = windowLabel(preferred: preferredWindow, start: start, end: end)

        if !confirmed.isEmpty {
            if !label.isEmpty && !end.isEmpty {
                return "\(label) · arrives ~\(confirmed) ET (until \(end))"
            }
            if !label.isEmpty {
                return "\(label) · arrives ~\(confirmed) ET"
            }
            return "Arrives ~\(confirmed) ET"
        }

        if !start.isEmpty && !end.isEmpty {
            let range = "\(start)–\(end) ET"
            return label.isEmpty ? range : "\(label) · \(range)"
        }
        if !start.isEmpty && end.isEmpty {
            let range = "\(start) ET"
            return label.isEmpty ? "From \(range)" : "\(label) · from \(range)"
        }
        if start.isEmpty && !end.isEmpty {
            let range = "\(end) ET"
            return label.isEmpty ? "Until \(range)" : "\(label) · until \(range)"
        }
        if !label.isEmpty { return "\(label) · times not set" }
        return "Shoot window not set"
    }

    public static func formatWhen(
        scheduledDate: String?,
        preferredWindow: String?,
        timeWindowStart: String?,
        timeWindowEnd: String?,
        confirmedArriveAt: String? = nil
    ) -> String {
        let date = String((scheduledDate ?? "").prefix(10))
        let window = format(
            preferredWindow: preferredWindow,
            timeWindowStart: timeWindowStart,
            timeWindowEnd: timeWindowEnd,
            confirmedArriveAt: confirmedArriveAt
        )
        if !date.isEmpty && window != "Shoot window not set" { return "\(date) · \(window)" }
        if !date.isEmpty { return "\(date) · Shoot window not set" }
        return window
    }
}

private extension String {
    func padLeft(toLength: Int, withPad: String) -> String {
        let pad = max(0, toLength - count)
        return String(repeating: withPad, count: pad) + self
    }
}
