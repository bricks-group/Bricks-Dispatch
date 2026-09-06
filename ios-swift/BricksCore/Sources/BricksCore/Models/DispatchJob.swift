import Foundation

public struct CaptureEntitlements: Codable, Sendable, Hashable {
    public let tour: Bool
    public let floorplan: Bool
    public let visit: Bool

    public init(tour: Bool, floorplan: Bool, visit: Bool) {
        self.tour = tour; self.floorplan = floorplan; self.visit = visit
    }

    public static let none = CaptureEntitlements(tour: false, floorplan: false, visit: false)

    public var hasPhoneMode: Bool { tour || floorplan }

    /// True when GO has any deliver/capture action for this job (shutter or Visit deliver).
    public var hasGOAction: Bool { tour || floorplan || visit }

    public var neitherCopy: String {
        if visit && !tour && !floorplan {
            return "This job has Virtual Visit only — open Deliver Visit to paste a Polycam URL or upload a .ply / .splat. HDR stills stay on the camera."
        }
        return "This job has no in-app 3D tour, floor plan, or Virtual Visit. HDR stills stay on the camera."
    }
}

public struct DispatchJob: Codable, Identifiable, Sendable, Hashable {
    public let id: String
    public let bookingId: String
    public let address: String
    public let city: String
    public let state: String
    public let zipCode: String
    public let lat: Double
    public let lng: Double
    // Stored as "HH:MM" — always combine with scheduledDate before Date math
    public let timeWindowStart: String
    public let timeWindowEnd: String
    /// Paid /book window: morning | afternoon | evening (optional).
    public let preferredWindow: String?
    /// Exact arrive HH:MM in the job timezone after photographer confirms on GO.
    public let confirmedArriveAt: String?
    /// IANA timezone from intake / Stripe metadata. Missing → province fallback.
    public let timezone: String?
    public let scheduledDate: String         // "YYYY-MM-DD"
    public let jobType: String               // "photo" | "drone" | "video"
    public var status: JobStatus
    public let packageName: String?
    public let packagePrice: Int?            // cents CAD
    public let vendorPayout: Double?         // CAD — photographer's cut
    public let agentName: String?
    public let agentPhone: String?
    public let agentEmail: String?
    public let clientInstructions: String?
    public var checklist: [ChecklistItem]
    public let complexityScore: Int?
    public let createdAt: String
    public let updatedAt: String
    public var assigneeId: String?
    public var assigneeName: String?
    public var transactionId: String?
    public var listingAddress: String?
    public var capture: CaptureEntitlements?

    public init(
        id: String, bookingId: String, address: String, city: String, state: String, zipCode: String,
        lat: Double, lng: Double, timeWindowStart: String, timeWindowEnd: String, scheduledDate: String,
        jobType: String, status: JobStatus, packageName: String?, packagePrice: Int?, vendorPayout: Double?,
        agentName: String?, agentPhone: String?, agentEmail: String?, clientInstructions: String?,
        checklist: [ChecklistItem], complexityScore: Int?, createdAt: String, updatedAt: String,
        assigneeId: String? = nil, assigneeName: String? = nil,
        transactionId: String? = nil, listingAddress: String? = nil,
        preferredWindow: String? = nil,
        confirmedArriveAt: String? = nil,
        timezone: String? = nil,
        capture: CaptureEntitlements? = nil
    ) {
        self.id = id; self.bookingId = bookingId; self.address = address; self.city = city
        self.state = state; self.zipCode = zipCode; self.lat = lat; self.lng = lng
        self.timeWindowStart = timeWindowStart; self.timeWindowEnd = timeWindowEnd
        self.preferredWindow = preferredWindow
        self.confirmedArriveAt = confirmedArriveAt
        self.timezone = timezone
        self.scheduledDate = scheduledDate; self.jobType = jobType; self.status = status
        self.packageName = packageName; self.packagePrice = packagePrice; self.vendorPayout = vendorPayout
        self.agentName = agentName; self.agentPhone = agentPhone; self.agentEmail = agentEmail
        self.clientInstructions = clientInstructions; self.checklist = checklist
        self.complexityScore = complexityScore; self.createdAt = createdAt; self.updatedAt = updatedAt
        self.assigneeId = assigneeId; self.assigneeName = assigneeName
        self.transactionId = transactionId; self.listingAddress = listingAddress
        self.capture = capture
    }

    enum CodingKeys: String, CodingKey {
        case id, bookingId, address, city, state, zipCode, lat, lng
        case timeWindowStart, timeWindowEnd, preferredWindow, confirmedArriveAt, timezone, scheduledDate, jobType, status
        case packageName, packagePrice, vendorPayout
        case agentName, agentPhone, agentEmail, clientInstructions
        case checklist, complexityScore, createdAt, updatedAt
        case assigneeId, assigneeName, transactionId, listingAddress, capture
    }
    enum ExtraKeys: String, CodingKey {
        case payoutDisplayCad
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(String.self, forKey: .id) ?? ""
        bookingId = try c.decodeIfPresent(String.self, forKey: .bookingId) ?? ""
        address = try c.decodeIfPresent(String.self, forKey: .address) ?? ""
        city = try c.decodeIfPresent(String.self, forKey: .city) ?? ""
        state = try c.decodeIfPresent(String.self, forKey: .state) ?? ""
        zipCode = try c.decodeIfPresent(String.self, forKey: .zipCode) ?? ""
        lat = Self.decodeDouble(c, .lat)
        lng = Self.decodeDouble(c, .lng)
        timeWindowStart = try c.decodeIfPresent(String.self, forKey: .timeWindowStart) ?? ""
        timeWindowEnd = try c.decodeIfPresent(String.self, forKey: .timeWindowEnd) ?? ""
        preferredWindow = try c.decodeIfPresent(String.self, forKey: .preferredWindow)
        let rawArrive = try c.decodeIfPresent(String.self, forKey: .confirmedArriveAt)
        timezone = try c.decodeIfPresent(String.self, forKey: .timezone)
        confirmedArriveAt = {
            let s = (rawArrive ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            return s.isEmpty ? nil : ShootWindow.clock(s, timezone: timezone, state: state, address: address)
        }()
        let rawDate = try c.decodeIfPresent(String.self, forKey: .scheduledDate) ?? ""
        scheduledDate = String(rawDate.prefix(10))
        jobType = try c.decodeIfPresent(String.self, forKey: .jobType) ?? "photo"
        status = try c.decodeIfPresent(JobStatus.self, forKey: .status) ?? .pending
        packageName = try c.decodeIfPresent(String.self, forKey: .packageName)
        packagePrice = try c.decodeIfPresent(Int.self, forKey: .packagePrice)
        let extra = try decoder.container(keyedBy: ExtraKeys.self)
        vendorPayout = try c.decodeIfPresent(Double.self, forKey: .vendorPayout)
            ?? extra.decodeIfPresent(Double.self, forKey: .payoutDisplayCad)
        agentName = try c.decodeIfPresent(String.self, forKey: .agentName)
        agentPhone = try c.decodeIfPresent(String.self, forKey: .agentPhone)
        agentEmail = try c.decodeIfPresent(String.self, forKey: .agentEmail)
        clientInstructions = try c.decodeIfPresent(String.self, forKey: .clientInstructions)
        checklist = (try? c.decodeIfPresent([ChecklistItem].self, forKey: .checklist)) ?? []
        complexityScore = try c.decodeIfPresent(Int.self, forKey: .complexityScore)
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
        updatedAt = try c.decodeIfPresent(String.self, forKey: .updatedAt) ?? ""
        let rawAssignee = try c.decodeIfPresent(String.self, forKey: .assigneeId)
        assigneeId = (rawAssignee?.isEmpty == true) ? nil : rawAssignee
        assigneeName = try c.decodeIfPresent(String.self, forKey: .assigneeName)
        let rawTxn = try c.decodeIfPresent(String.self, forKey: .transactionId)
        transactionId = (rawTxn?.isEmpty == true) ? nil : rawTxn
        listingAddress = try c.decodeIfPresent(String.self, forKey: .listingAddress)
        capture = try c.decodeIfPresent(CaptureEntitlements.self, forKey: .capture)
    }

    private static func decodeDouble(_ c: KeyedDecodingContainer<CodingKeys>, _ key: CodingKeys) -> Double {
        if let v = try? c.decodeIfPresent(Double.self, forKey: key) { return v ?? 0 }
        if let v = try? c.decodeIfPresent(Int.self, forKey: key) { return Double(v ?? 0) }
        if let s = try? c.decodeIfPresent(String.self, forKey: key), let v = Double(s ?? "") { return v }
        return 0
    }

    // Identity is the job id so navigation destinations stay stable after assign/attach.
    public static func == (lhs: DispatchJob, rhs: DispatchJob) -> Bool { lhs.id == rhs.id }
    public func hash(into hasher: inout Hasher) { hasher.combine(id) }

    // Derived helpers

    public var scheduledDateTime: Date? {
        let combined = "\(scheduledDate)T\(timeWindowStart):00"
        return ISO8601DateFormatter().date(from: combined)
    }

    public var minutesUntilStart: Int? {
        guard let dt = scheduledDateTime else { return nil }
        return Int(dt.timeIntervalSinceNow / 60)
    }

    public var formattedPayout: String {
        guard let p = vendorPayout else { return "—" }
        return String(format: "$%.2f", p)
    }

    public var isActive: Bool { status == .inProgress }
    public var isUpcoming: Bool { status == .assigned }
    public var isUnassigned: Bool { status == .pendingAssignment || status == .pending }


    public var captureModes: CaptureEntitlements {
        if let capture { return capture }
        return Self.inferredCapture(packageName: packageName, jobType: jobType, checklist: checklist)
    }

    public static func inferredCapture(packageName: String?, jobType: String, checklist: [ChecklistItem]) -> CaptureEntitlements {
        let name = (packageName ?? "").lowercased()
        let type = jobType.lowercased().replacingOccurrences(of: "-", with: "_")
        let blob = ([name, type] + checklist.map { $0.label.lowercased() }).joined(separator: " ")
        let photosOnly = blob.contains("photos only") || blob.contains("photo only") || blob.contains("no floor plan")
        let visit = blob.contains("virtual visit") || blob.contains("gaussian splat") || blob.contains("supersplat")
        let cleaned = blob.replacingOccurrences(of: "virtual visit", with: " ")
            .replacingOccurrences(of: "gaussian splat 3d virtual tour", with: " ")
            .replacingOccurrences(of: "gaussian splat", with: " ")
            .replacingOccurrences(of: "supersplat", with: " ")
        let tourType = ["3d_tour", "3dtour", "virtual_tour", "virtualtour"].contains(type)
        let tourPkg = name.contains("showcase") || name.contains("full suite") || name.hasPrefix("suite")
        let tour = tourType || tourPkg || cleaned.contains("virtual tour") || cleaned.contains("3d tour") || cleaned.contains("path-confined") || cleaned.contains("path confined")
        let explicitFloor = blob.contains("floor plan") && !blob.contains("floor plan capture submitted")
        let photoPkg = ["essentials", "story", "showcase", "suite", "standard", "premium", "luxury", "estate"].contains(where: { name.contains($0) })
            || type == "photo"
            || name.contains("hdr photo")
        let floorplan: Bool
        if photosOnly && !explicitFloor { floorplan = false }
        else if explicitFloor || type.contains("floorplan") || type.contains("floor_plan") { floorplan = true }
        else { floorplan = photoPkg }
        return CaptureEntitlements(tour: tour, floorplan: floorplan, visit: visit)
    }

    public var displayAssignee: String {
        if let name = assigneeName, !name.isEmpty, name.lowercased() != "unassigned" {
            return name
        }
        if let id = assigneeId, !id.isEmpty { return id }
        return "Unassigned"
    }

    /// Calendar date in the device timezone, matching Expo `en-CA` Today filter.
    public static func torontoToday(now: Date = Date()) -> String {
        calendarToday(now: now)
    }

    public static func calendarToday(now: Date = Date()) -> String {
        let fmt = DateFormatter()
        fmt.calendar = Calendar(identifier: .gregorian)
        fmt.locale = Locale(identifier: "en_CA")
        fmt.timeZone = TimeZone.current
        fmt.dateFormat = "yyyy-MM-dd"
        return fmt.string(from: now)
    }


    /// Booked arrive/start for Today / job detail — e.g. "2:00 PM PT".
    public var formattedShootWindow: String {
        ShootWindow.format(
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
    }

    /// Date + booked clock when both useful — e.g. "Fri, Sep 25 · 2:00 PM PT".
    public var formattedShootWhen: String {
        ShootWindow.formatWhen(
            scheduledDate: scheduledDate,
            preferredWindow: preferredWindow,
            timeWindowStart: timeWindowStart,
            timeWindowEnd: timeWindowEnd,
            confirmedArriveAt: confirmedArriveAt,
            timezone: timezone,
            state: state,
            address: address,
            zipCode: zipCode
        )
    }

    public func applyingArrive(from other: DispatchJob) -> DispatchJob {
        return DispatchJob(
            id: id,
            bookingId: bookingId,
            address: address,
            city: city,
            state: state,
            zipCode: zipCode,
            lat: lat,
            lng: lng,
            timeWindowStart: other.timeWindowStart.isEmpty ? timeWindowStart : other.timeWindowStart,
            timeWindowEnd: other.timeWindowEnd.isEmpty ? timeWindowEnd : other.timeWindowEnd,
            scheduledDate: scheduledDate,
            jobType: jobType,
            status: status,
            packageName: packageName,
            packagePrice: packagePrice,
            vendorPayout: vendorPayout,
            agentName: agentName,
            agentPhone: agentPhone,
            agentEmail: agentEmail,
            clientInstructions: clientInstructions,
            checklist: checklist,
            complexityScore: complexityScore,
            createdAt: createdAt,
            updatedAt: other.updatedAt.isEmpty ? updatedAt : other.updatedAt,
            assigneeId: assigneeId,
            assigneeName: assigneeName,
            transactionId: transactionId,
            listingAddress: listingAddress,
            preferredWindow: {
                let p = (other.preferredWindow ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                return p.isEmpty ? preferredWindow : other.preferredWindow
            }(),
            confirmedArriveAt: other.confirmedArriveAt ?? confirmedArriveAt,
            timezone: other.timezone ?? timezone,
            capture: capture
        )
    }

    public func applyingAssignment(from other: DispatchJob) -> DispatchJob {
        var copy = self
        copy.assigneeId = other.assigneeId
        copy.assigneeName = other.assigneeName
        copy.status = other.status
        copy.transactionId = other.transactionId ?? copy.transactionId
        copy.listingAddress = other.listingAddress ?? copy.listingAddress
        return copy
    }

    public func applyingListing(transactionId: String?, listingAddress: String?) -> DispatchJob {
        var copy = self
        copy.transactionId = transactionId
        copy.listingAddress = listingAddress
        return copy
    }
}

public enum JobStatus: String, Codable, Sendable, Hashable {
    case pending
    case pendingAssignment = "pending_assignment"
    case assigned
    case inProgress = "in_progress"
    case completed, delivered
    case declined, cancelled
    case dispatchFailed = "dispatch_failed"
    case agentNoShow = "agent_no_show"

    public init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = JobStatus(rawValue: raw) ?? .pending
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(rawValue)
    }
}

public struct ChecklistItem: Codable, Identifiable, Sendable, Hashable {
    public let id: String
    public let label: String
    public var completed: Bool

    public init(id: String, label: String, completed: Bool) {
        self.id = id
        self.label = label
        self.completed = completed
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(String.self, forKey: .id) ?? UUID().uuidString
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? ""
        completed = try c.decodeIfPresent(Bool.self, forKey: .completed) ?? false
    }

    enum CodingKeys: String, CodingKey { case id, label, completed }
}

public struct JobStatusUpdate: Encodable {
    public let status: String
    public let notes: String?
    public let completedAt: String?
    public init(status: JobStatus, notes: String? = nil) {
        self.status = status.rawValue
        self.notes = notes
        self.completedAt = status == .completed ? ISO8601DateFormatter().string(from: Date()) : nil
    }
}

public struct WatchActionRequest: Encodable {
    public let action: String
    public init(action: WatchAction) { self.action = action.rawValue }
}

public enum WatchAction: String {
    case accept, decline, onWay = "on_way", arrived
}

public struct OfficeListing: Codable, Identifiable, Hashable, Sendable {
    public let id: String
    public let address: String
    public let city: String?
    public let dealType: String?

    public init(id: String, address: String, city: String? = nil, dealType: String? = nil) {
        self.id = id; self.address = address; self.city = city; self.dealType = dealType
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(String.self, forKey: .id) ?? ""
        address = try c.decodeIfPresent(String.self, forKey: .address) ?? "Listing"
        city = try c.decodeIfPresent(String.self, forKey: .city)
        dealType = try c.decodeIfPresent(String.self, forKey: .dealType)
    }

        enum CodingKeys: String, CodingKey { case id, address, city, dealType }

    public var subtitle: String {
        let bits = [city, dealType].compactMap { $0 }.filter { !$0.isEmpty }
        return bits.isEmpty ? "Office listing" : bits.joined(separator: " · ")
    }
}
