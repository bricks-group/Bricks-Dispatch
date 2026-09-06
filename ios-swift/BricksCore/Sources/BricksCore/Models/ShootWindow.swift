import Foundation

/// Booked clock times (America/Toronto). Mirrors web `shoot-window.ts`.
public enum ShootWindow {
    private static let canonical: [(key: String, start: String)] = [
        ("morning", "09:00"),
        ("afternoon", "12:00"),
        ("evening", "16:00"),
    ]

    private static let unset = "Shoot time not set"

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
        return ""
    }

    private static func clock12(_ hhmm: String) -> String {
        guard hhmm.count >= 5, hhmm[hhmm.index(hhmm.startIndex, offsetBy: 2)] == ":" else { return "" }
        let hour = Int(hhmm.prefix(2)) ?? -1
        let minute = Int(hhmm.dropFirst(3).prefix(2)) ?? -1
        guard (0...23).contains(hour), (0...59).contains(minute) else { return "" }
        let suffix = hour < 12 ? "AM" : "PM"
        let hour12 = hour % 12 == 0 ? 12 : hour % 12
        return "\(hour12):\(String(format: "%02d", minute)) \(suffix)"
    }

    private static func bookedClock(
        preferredWindow: String?,
        timeWindowStart: String?,
        confirmedArriveAt: String?
    ) -> String {
        let confirmed = clock(confirmedArriveAt)
        if !confirmed.isEmpty { return confirmed }
        let start = clock(timeWindowStart)
        if !start.isEmpty { return start }
        let key = (preferredWindow ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if let hit = canonical.first(where: { $0.key == key }) { return hit.start }
        return ""
    }

    /// Booked arrive/start: "2:00 PM ET". Not a window label or range.
    public static func format(
        preferredWindow: String?,
        timeWindowStart: String?,
        timeWindowEnd _: String?,
        confirmedArriveAt: String? = nil
    ) -> String {
        let hhmm = bookedClock(
            preferredWindow: preferredWindow,
            timeWindowStart: timeWindowStart,
            confirmedArriveAt: confirmedArriveAt
        )
        if hhmm.isEmpty { return unset }
        let pretty = clock12(hhmm)
        return pretty.isEmpty ? "\(hhmm) ET" : "\(pretty) ET"
    }

    public static func formatDate(_ scheduledDate: String?) -> String {
        let raw = String((scheduledDate ?? "").prefix(10))
        guard raw.count >= 10, raw[raw.index(raw.startIndex, offsetBy: 4)] == "-" else { return raw }
        let inFmt = DateFormatter()
        inFmt.locale = Locale(identifier: "en_US_POSIX")
        inFmt.timeZone = TimeZone(identifier: "UTC")
        inFmt.dateFormat = "yyyy-MM-dd"
        guard let d = inFmt.date(from: raw) else { return raw }
        let out = DateFormatter()
        out.locale = Locale(identifier: "en_US_POSIX")
        out.timeZone = TimeZone(identifier: "UTC")
        out.dateFormat = "EEE, MMM d"
        return out.string(from: d)
    }

    /// Date + booked clock: "Fri, Sep 25 · 2:00 PM ET".
    public static func formatWhen(
        scheduledDate: String?,
        preferredWindow: String?,
        timeWindowStart: String?,
        timeWindowEnd: String?,
        confirmedArriveAt: String? = nil
    ) -> String {
        let date = formatDate(scheduledDate)
        let clock = format(
            preferredWindow: preferredWindow,
            timeWindowStart: timeWindowStart,
            timeWindowEnd: timeWindowEnd,
            confirmedArriveAt: confirmedArriveAt
        )
        if clock == unset { return date.isEmpty ? unset : date }
        if !date.isEmpty { return "\(date) · \(clock)" }
        return clock
    }
}

private extension String {
    func padLeft(toLength: Int, withPad: String) -> String {
        let pad = max(0, toLength - count)
        return String(repeating: withPad, count: pad) + self
    }
}
