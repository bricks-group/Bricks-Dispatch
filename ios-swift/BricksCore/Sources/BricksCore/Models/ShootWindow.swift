import Foundation

/// Booked clock times in the property timezone. Mirrors web `shoot-window.ts`.
public enum ShootWindow {
    public static let defaultIANA = "America/Toronto"

    private static let canonical: [(key: String, start: String)] = [
        ("morning", "09:00"),
        ("afternoon", "12:00"),
        ("evening", "16:00"),
    ]

    private static let unset = "Shoot time not set"

    private static let provinceToIANA: [String: String] = [
        "bc": "America/Vancouver", "british columbia": "America/Vancouver",
        "ab": "America/Edmonton", "alberta": "America/Edmonton",
        "sk": "America/Regina", "saskatchewan": "America/Regina",
        "mb": "America/Winnipeg", "manitoba": "America/Winnipeg",
        "on": "America/Toronto", "ont": "America/Toronto", "ontario": "America/Toronto",
        "qc": "America/Toronto", "que": "America/Toronto", "quebec": "America/Toronto",
        "nb": "America/Moncton", "new brunswick": "America/Moncton",
        "ns": "America/Halifax", "nova scotia": "America/Halifax",
        "pe": "America/Halifax", "pei": "America/Halifax", "prince edward island": "America/Halifax",
        "nl": "America/St_Johns", "nf": "America/St_Johns",
        "newfoundland": "America/St_Johns", "newfoundland and labrador": "America/St_Johns",
        "yt": "America/Whitehorse", "yukon": "America/Whitehorse",
        "nt": "America/Yellowknife", "northwest territories": "America/Yellowknife",
        "nu": "America/Iqaluit", "nunavut": "America/Iqaluit",
    ]

    private static let postalToIANA: [Character: String] = [
        "A": "America/St_Johns", "B": "America/Halifax", "C": "America/Halifax",
        "E": "America/Moncton",
        "G": "America/Toronto", "H": "America/Toronto", "J": "America/Toronto",
        "K": "America/Toronto", "L": "America/Toronto", "M": "America/Toronto",
        "N": "America/Toronto", "P": "America/Toronto",
        "R": "America/Winnipeg", "S": "America/Regina", "T": "America/Edmonton",
        "V": "America/Vancouver", "X": "America/Yellowknife", "Y": "America/Whitehorse",
    ]

    private static let aliasToIANA: [String: String] = [
        "et": "America/Toronto", "est": "America/Toronto", "eastern": "America/Toronto",
        "pt": "America/Vancouver", "pst": "America/Vancouver", "pacific": "America/Vancouver",
        "mt": "America/Edmonton", "ct": "America/Winnipeg",
        "at": "America/Halifax", "nt": "America/St_Johns",
    ]

    private static let stdToLetter: [String: String] = [
        "EST": "ET", "EDT": "ET", "CST": "CT", "CDT": "CT",
        "MST": "MT", "MDT": "MT", "PST": "PT", "PDT": "PT",
        "AST": "AT", "ADT": "AT", "NST": "NT", "NDT": "NT",
    ]

    public static func resolveIANA(
        timezone: String? = nil,
        state: String? = nil,
        address: String? = nil,
        zipCode: String? = nil
    ) -> String {
        if let tz = validIANA(timezone) { return tz }
        let provKey = (state ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased().replacingOccurrences(of: ".", with: "")
        if let hit = provinceToIANA[provKey] { return hit }
        let postal = (zipCode ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            .uppercased().replacingOccurrences(of: " ", with: "")
        if let first = postal.first, let hit = postalToIANA[first] { return hit }
        if let fromAddr = ianaFromAddress(address) { return fromAddr }
        return defaultIANA
    }

    private static func validIANA(_ name: String?) -> String? {
        let s = (name ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { return nil }
        let key = s.lowercased().replacingOccurrences(of: "_", with: " ")
        if let alias = aliasToIANA[key] { return alias }
        if TimeZone(identifier: s) != nil { return s }
        return nil
    }

    private static func ianaFromAddress(_ address: String?) -> String? {
        let compact = " \((address ?? "").replacingOccurrences(of: ",", with: " ")) "
        let low = compact.lowercased()
        let keys = provinceToIANA.keys.sorted { $0.count > $1.count }
        for key in keys {
            if low.contains(" \(key) ") || low.trimmingCharacters(in: .whitespaces).hasSuffix(" \(key)") {
                return provinceToIANA[key]
            }
        }
        return nil
    }

    public static func abbrev(
        timezone: String? = nil,
        state: String? = nil,
        address: String? = nil,
        zipCode: String? = nil,
        scheduledDate: String? = nil
    ) -> String {
        let iana = resolveIANA(timezone: timezone, state: state, address: address, zipCode: zipCode)
        let tz = TimeZone(identifier: iana) ?? TimeZone(identifier: defaultIANA) ?? .current
        let fmt = DateFormatter()
        fmt.locale = Locale(identifier: "en_US_POSIX")
        fmt.timeZone = tz
        fmt.dateFormat = "z"
        let ymd = String((scheduledDate ?? "").prefix(10))
        let date: Date
        if ymd.count == 10 {
            let inFmt = DateFormatter()
            inFmt.locale = Locale(identifier: "en_US_POSIX")
            inFmt.timeZone = tz
            inFmt.dateFormat = "yyyy-MM-dd"
            date = inFmt.date(from: ymd) ?? Date()
        } else {
            date = Date()
        }
        let std = fmt.string(from: date).uppercased().replacingOccurrences(of: " ", with: "")
        if let letter = stdToLetter[std] { return letter }
        switch iana {
        case "America/Vancouver": return "PT"
        case "America/Edmonton", "America/Whitehorse", "America/Yellowknife": return "MT"
        case "America/Winnipeg", "America/Regina": return "CT"
        case "America/Halifax", "America/Moncton": return "AT"
        case "America/St_Johns": return "NT"
        default: return "ET"
        }
    }

    public static func clock(_ raw: String?, timezone: String? = nil, state: String? = nil, address: String? = nil) -> String {
        let s = (raw ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { return "" }
        if s.contains("T") == false, s.contains("-") == false,
           let m = s.range(of: #"^(\d{1,2}):(\d{2})"#, options: .regularExpression) {
            let parts = s[m].split(separator: ":")
            let h = String(parts[0]).padLeft(toLength: 2, withPad: "0")
            let min = String(parts[1].prefix(2))
            return "\(h):\(min)"
        }
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
            fmt.timeZone = TimeZone(identifier: resolveIANA(timezone: timezone, state: state, address: address))
                ?? TimeZone(identifier: defaultIANA)
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
        confirmedArriveAt: String?,
        timezone: String?,
        state: String?,
        address: String?
    ) -> String {
        let confirmed = clock(confirmedArriveAt, timezone: timezone, state: state, address: address)
        if !confirmed.isEmpty { return confirmed }
        let start = clock(timeWindowStart, timezone: timezone, state: state, address: address)
        if !start.isEmpty { return start }
        let key = (preferredWindow ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if let hit = canonical.first(where: { $0.key == key }) { return hit.start }
        return ""
    }

    /// Booked arrive/start: "2:00 PM PT". Not a window label or range.
    public static func format(
        preferredWindow: String?,
        timeWindowStart: String?,
        timeWindowEnd _: String?,
        confirmedArriveAt: String? = nil,
        timezone: String? = nil,
        state: String? = nil,
        address: String? = nil,
        zipCode: String? = nil,
        scheduledDate: String? = nil
    ) -> String {
        let hhmm = bookedClock(
            preferredWindow: preferredWindow,
            timeWindowStart: timeWindowStart,
            confirmedArriveAt: confirmedArriveAt,
            timezone: timezone,
            state: state,
            address: address
        )
        if hhmm.isEmpty { return unset }
        let pretty = clock12(hhmm)
        let zone = abbrev(
            timezone: timezone, state: state, address: address,
            zipCode: zipCode, scheduledDate: scheduledDate
        )
        return pretty.isEmpty ? "\(hhmm) \(zone)" : "\(pretty) \(zone)"
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

    /// Date + booked clock: "Fri, Sep 25 · 2:00 PM PT".
    public static func formatWhen(
        scheduledDate: String?,
        preferredWindow: String?,
        timeWindowStart: String?,
        timeWindowEnd: String?,
        confirmedArriveAt: String? = nil,
        timezone: String? = nil,
        state: String? = nil,
        address: String? = nil,
        zipCode: String? = nil
    ) -> String {
        let date = formatDate(scheduledDate)
        let clock = format(
            preferredWindow: preferredWindow,
            timeWindowStart: timeWindowStart,
            timeWindowEnd: timeWindowEnd,
            confirmedArriveAt: confirmedArriveAt,
            timezone: timezone,
            state: state,
            address: address,
            zipCode: zipCode,
            scheduledDate: scheduledDate
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
