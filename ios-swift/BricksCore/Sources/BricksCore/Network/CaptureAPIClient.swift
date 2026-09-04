import Foundation

/// Phone capture kinds on dispatch-api. `splat` is the path-confined 3D tour.
/// Virtual visit is not a shutter mode.
public enum CaptureKind: String, Codable, Sendable, Hashable, Identifiable {
    case splat
    case floorplan

    public var id: String { rawValue }

    public var shortLabel: String {
        switch self {
        case .floorplan: return "Floor plan"
        case .splat: return "3D tour"
        }
    }

    /// Honest status line for job detail / post-submit — matches Expo `captureStatusCopy`.
    public static func statusCopy(_ status: String?, kind: CaptureKind = .splat) -> String {
        let label = kind.shortLabel
        switch (status ?? "").lowercased() {
        case "delivered":
            return "\(label) ready."
        case "failed":
            return "\(label) compile failed — open review to retry or recapture."
        case "submitted", "splatting", "tracing":
            return "\(label) compiling… usually a few minutes."
        case "in_progress":
            return "\(label) capture in progress."
        default:
            return ""
        }
    }

    public static func isTerminalPipelineStatus(_ status: String?) -> Bool {
        let st = (status ?? "").lowercased()
        return ["submitted", "splatting", "tracing", "delivered", "failed"].contains(st)
    }

    public static func isCompilingStatus(_ status: String?) -> Bool {
        let st = (status ?? "").lowercased()
        return ["submitted", "splatting", "tracing"].contains(st)
    }
}

public enum CaptureRole: String, Codable, Sendable {
    case pan
    case equirect
    case usdz
    case roomplan
}

public struct CaptureRoom: Sendable, Identifiable, Hashable {
    public var id: String
    public var name: String
    public var tone: String
    public var n: Int

    public init(id: String, name: String, tone: String, n: Int) {
        self.id = id; self.name = name; self.tone = tone; self.n = n
    }
}

public struct CapturePipelineStage: Sendable, Hashable {
    public var title: String
    public var detail: String
    public var status: String

    public init(title: String, detail: String, status: String) {
        self.title = title; self.detail = detail; self.status = status
    }
}

public struct CaptureStationInfo: Sendable, Hashable {
    public var stationIndex: Int
    public var room: String?
    public var panCount: Int
    public var hasEquirect: Bool
    public var heading: Double?
    public var contentType: String?

    public init(stationIndex: Int, room: String?, panCount: Int, hasEquirect: Bool, heading: Double?, contentType: String?) {
        self.stationIndex = stationIndex; self.room = room; self.panCount = panCount
        self.hasEquirect = hasEquirect; self.heading = heading; self.contentType = contentType
    }
}

public struct CaptureGuidance: Sendable, Hashable {
    public var room: String
    public var message: String
    public init(room: String, message: String) {
        self.room = room; self.message = message
    }
}

/// Live capture session from `/api/capture/*`. Named apart from AVCaptureSession.
public struct GOCaptureSession: Sendable, Hashable {
    public var sessionId: String?
    public var currentRoomId: String?
    public var address: String
    public var meta: String
    public var kind: CaptureKind
    public var status: String?
    public var rooms: [CaptureRoom]
    public var pipeline: [CapturePipelineStage]
    public var frameCount: Int
    public var guidance: CaptureGuidance?
    public var estimatedDeliveryHours: Double?
    public var outputStoragePath: String?
    public var stationCount: Int
    public var currentStationIndex: Int
    public var stations: [CaptureStationInfo]

    public static func empty(kind: CaptureKind = .splat, address: String = "Property") -> GOCaptureSession {
        GOCaptureSession(
            sessionId: nil, currentRoomId: nil, address: address, meta: "", kind: kind,
            status: nil, rooms: [], pipeline: [], frameCount: 0, guidance: nil,
            estimatedDeliveryHours: nil, outputStoragePath: nil, stationCount: 0,
            currentStationIndex: 0, stations: []
        )
    }

    public var thinStations: [CaptureStationInfo] {
        stations.filter { !$0.hasEquirect && $0.panCount < 8 }
    }

    public var canSubmit: Bool {
        !pipeline.isEmpty && stationCount > 0 && thinStations.isEmpty
    }
}

public struct CaptureCallResult: Sendable {
    public var ok: Bool
    public var error: String?
    public var code: String?
    public var session: GOCaptureSession?
    public var frameCount: Int?

    public static func failure(_ error: String, code: String? = nil) -> CaptureCallResult {
        CaptureCallResult(ok: false, error: error, code: code, session: nil, frameCount: nil)
    }

    public static func success(_ session: GOCaptureSession, frameCount: Int? = nil) -> CaptureCallResult {
        CaptureCallResult(ok: true, error: nil, code: nil, session: session, frameCount: frameCount ?? session.frameCount)
    }
}

public struct CaptureSignPayload: Sendable {
    public var path: String
    public var token: String?
    public var signedURL: String?
    public var bucket: String
    public var sessionId: String
    public var roomId: String?
    public var stationIndex: Int?
    public var role: String
    public var contentType: String
}

/// 24-tick coverage dome math — matches Expo `capture.tsx`.
public enum CaptureDome {
    public static let total = 24
    public static let floorplanCorners = 4

    public static func tick(yaw: Double, origin: Double, total: Int = CaptureDome.total) -> Int {
        let rel = (yaw - origin).truncatingRemainder(dividingBy: 360)
        let positive = rel < 0 ? rel + 360 : rel
        let step = 360.0 / Double(total)
        let t = Int((positive / step).rounded()) % total
        return t < 0 ? t + total : t
    }
}

/// Capture API — same contract as `web/src/lib/capture-api.ts` / `frontend/lib/capture-api.ts`.
/// JPEG bytes PUT to `signed_url`; never POST /api/uploads.
public final class CaptureAPIClient: @unchecked Sendable {
    public static let shared = CaptureAPIClient()

    public static let notPurchasedCode = "capture_kind_not_purchased"

    private let urlSession: URLSession

    private init() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 60
        self.urlSession = URLSession(configuration: config)
    }

    private var baseURL: URL { URL(string: BricksConfig.apiBaseURL)! }

    public func getSession(jobId: String, kind: CaptureKind) async -> GOCaptureSession {
        guard !jobId.isEmpty else { return .empty(kind: kind) }
        do {
            let url = BricksAPIURL.make(base: baseURL, path: "/api/capture/sessions", query: [
                URLQueryItem(name: "job_id", value: jobId),
                URLQueryItem(name: "kind", value: kind.rawValue),
            ])
            let (data, http) = try await jsonRequest(url: url, method: "GET", body: nil)
            if http.statusCode < 200 || http.statusCode >= 300 {
                return .empty(kind: kind)
            }
            return Self.parseSession(data, kind: kind)
        } catch {
            return .empty(kind: kind)
        }
    }

    public func ensureSession(jobId: String, kind: CaptureKind) async -> CaptureCallResult {
        guard !jobId.isEmpty else { return .failure("Missing job") }
        do {
            let url = BricksAPIURL.make(base: baseURL, path: "/api/capture/sessions")
            let (data, http) = try await jsonRequest(url: url, method: "POST", body: [
                "job_id": jobId,
                "kind": kind.rawValue,
            ])
            if http.statusCode < 200 || http.statusCode >= 300 {
                return Self.fail(data, fallback: "HTTP \(http.statusCode)")
            }
            return .success(Self.parseSession(data, kind: kind))
        } catch {
            return .failure(error.localizedDescription)
        }
    }

    public func addRoom(jobId: String, name: String, kind: CaptureKind) async -> CaptureCallResult {
        guard !jobId.isEmpty else { return .failure("Missing job") }
        do {
            let url = BricksAPIURL.make(base: baseURL, path: "/api/capture/rooms")
            let (data, http) = try await jsonRequest(url: url, method: "POST", body: [
                "job_id": jobId,
                "kind": kind.rawValue,
                "name": name,
            ])
            if http.statusCode < 200 || http.statusCode >= 300 {
                return Self.fail(data, fallback: "HTTP \(http.statusCode)")
            }
            return .success(Self.parseSession(data, kind: kind))
        } catch {
            return .failure(error.localizedDescription)
        }
    }

    public func openStation(
        jobId: String,
        kind: CaptureKind,
        roomId: String?,
        heading: Double?,
        increment: Bool
    ) async -> CaptureCallResult {
        guard !jobId.isEmpty else { return .failure("Missing job") }
        do {
            var body: [String: Any] = [
                "job_id": jobId,
                "kind": kind.rawValue,
                "new": increment,
            ]
            if let roomId, !roomId.isEmpty { body["room_id"] = roomId }
            if let heading { body["heading"] = heading }
            let url = BricksAPIURL.make(base: baseURL, path: "/api/capture/stations")
            let (data, http) = try await jsonRequest(url: url, method: "POST", body: body)
            if http.statusCode < 200 || http.statusCode >= 300 {
                return Self.fail(data, fallback: "HTTP \(http.statusCode)")
            }
            return .success(Self.parseSession(data, kind: kind))
        } catch {
            return .failure(error.localizedDescription)
        }
    }

    public func submit(jobId: String, kind: CaptureKind) async -> CaptureCallResult {
        guard !jobId.isEmpty else { return .failure("Missing job") }
        do {
            let url = BricksAPIURL.make(base: baseURL, path: "/api/capture/submit")
            let (data, http) = try await jsonRequest(url: url, method: "POST", body: [
                "job_id": jobId,
                "kind": kind.rawValue,
            ])
            if http.statusCode < 200 || http.statusCode >= 300 {
                return Self.fail(data, fallback: "HTTP \(http.statusCode)")
            }
            let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            let sessionBlob: Any = obj?["session"] ?? (obj as Any)
            let session = Self.parseSessionObject(sessionBlob, kind: kind)
            // Backend may return HTTP 200 with { ok: false } when compile fails.
            if let obj, let okFlag = obj["ok"] as? Bool, okFlag == false {
                let message = string(obj["error"] ?? obj["detail"]) ?? "Compile failed"
                return CaptureCallResult(
                    ok: false,
                    error: message,
                    code: string(obj["code"]),
                    session: session,
                    frameCount: session.frameCount
                )
            }
            return .success(session)
        } catch {
            return .failure(error.localizedDescription)
        }
    }

    public func uploadJPEG(
        jobId: String,
        jpeg: Data,
        roomId: String?,
        kind: CaptureKind,
        stationIndex: Int?,
        yaw: Double?,
        heading: Double?,
        role: CaptureRole = .pan
    ) async -> CaptureCallResult {
        guard !jobId.isEmpty else { return .failure("Missing job") }
        guard !jpeg.isEmpty else { return .failure("Missing photo") }
        let contentType = "image/jpeg"
        do {
            var signBody: [String: Any] = [
                "job_id": jobId,
                "kind": kind.rawValue,
                "role": role.rawValue,
                "content_type": contentType,
            ]
            if let roomId, !roomId.isEmpty { signBody["room_id"] = roomId }
            if let stationIndex { signBody["station_index"] = stationIndex }
            if let yaw { signBody["yaw"] = yaw }
            if let heading { signBody["heading"] = heading }

            let signURL = BricksAPIURL.make(base: baseURL, path: "/api/capture/frames/sign")
            let (signData, signHTTP) = try await jsonRequest(url: signURL, method: "POST", body: signBody)
            if signHTTP.statusCode < 200 || signHTTP.statusCode >= 300 {
                return Self.fail(signData, fallback: "HTTP \(signHTTP.statusCode)")
            }
            guard let signed = Self.parseSign(signData) else {
                return .failure("No signed upload URL")
            }
            guard let putURL = signed.signedURL.flatMap(URL.init(string:)) else {
                return .failure("No signed upload URL")
            }

            var put = URLRequest(url: putURL)
            put.httpMethod = "PUT"
            put.setValue(contentType, forHTTPHeaderField: "Content-Type")
            put.httpBody = jpeg
            let (_, putResp) = try await urlSession.data(for: put)
            let putHTTP = putResp as! HTTPURLResponse
            if putHTTP.statusCode < 200 || putHTTP.statusCode >= 300 {
                return .failure("Storage PUT failed (\(putHTTP.statusCode))")
            }

            var commit: [String: Any] = [
                "job_id": jobId,
                "session_id": signed.sessionId,
                "storage_path": signed.path,
                "kind": kind.rawValue,
                "role": signed.role,
                "content_type": signed.contentType,
            ]
            if let rid = signed.roomId ?? roomId { commit["room_id"] = rid }
            if let idx = signed.stationIndex ?? stationIndex { commit["station_index"] = idx }
            if let yaw { commit["yaw"] = yaw }
            if let heading { commit["heading"] = heading }

            let commitURL = BricksAPIURL.make(base: baseURL, path: "/api/capture/frames/commit")
            let (commitData, commitHTTP) = try await jsonRequest(url: commitURL, method: "POST", body: commit)
            if commitHTTP.statusCode < 200 || commitHTTP.statusCode >= 300 {
                return Self.fail(commitData, fallback: "HTTP \(commitHTTP.statusCode)")
            }
            let session = Self.parseSession(commitData, kind: kind)
            return .success(session, frameCount: session.frameCount)
        } catch {
            return .failure(error.localizedDescription)
        }
    }

    // MARK: — HTTP

    private func jsonRequest(url: URL, method: String, body: [String: Any]?) async throws -> (Data, HTTPURLResponse) {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token = await BricksAuth.shared.accessToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let body {
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await urlSession.data(for: request)
        return (data, response as! HTTPURLResponse)
    }

    // MARK: — Parse

    public static func parseSession(_ data: Data, kind: CaptureKind) -> GOCaptureSession {
        let obj = (try? JSONSerialization.jsonObject(with: data)) ?? [:]
        return parseSessionObject(obj, kind: kind)
    }

    public static func parseSessionObject(_ raw: Any, kind fallback: CaptureKind) -> GOCaptureSession {
        guard let dict = raw as? [String: Any] else { return .empty(kind: fallback) }
        let kind = asKind(dict["kind"] ?? fallback.rawValue, fallback: fallback)
        let roomsAny = dict["rooms"] as? [Any] ?? []
        let rooms: [CaptureRoom] = roomsAny.compactMap { item in
            guard let r = item as? [String: Any] else { return nil }
            let name = string(r["name"]) ?? "Room"
            let id = string(r["id"]) ?? name
            return CaptureRoom(
                id: id,
                name: name,
                tone: string(r["tone"]) ?? "bad",
                n: int(r["n"] ?? r["frames"]) ?? 0
            )
        }
        let pipelineAny = dict["pipeline"] as? [Any] ?? []
        let pipeline: [CapturePipelineStage] = pipelineAny.compactMap { item in
            guard let p = item as? [String: Any] else { return nil }
            return CapturePipelineStage(
                title: string(p["title"]) ?? "",
                detail: string(p["detail"]) ?? "",
                status: string(p["status"]) ?? "todo"
            )
        }
        let stationsAny = dict["stations"] as? [Any] ?? []
        let stations: [CaptureStationInfo] = stationsAny.compactMap { item in
            guard let s = item as? [String: Any] else { return nil }
            return CaptureStationInfo(
                stationIndex: int(s["station_index"] ?? s["stationIndex"]) ?? 0,
                room: string(s["room"]),
                panCount: int(s["panCount"] ?? s["pan_count"]) ?? 0,
                hasEquirect: bool(s["hasEquirect"] ?? s["has_equirect"]) ?? false,
                heading: double(s["heading"]),
                contentType: string(s["content_type"] ?? s["contentType"])
            )
        }
        var guidance: CaptureGuidance?
        if let g = dict["guidance"] as? [String: Any], let room = string(g["room"]), let message = string(g["message"]) {
            guidance = CaptureGuidance(room: room, message: message)
        }
        return GOCaptureSession(
            sessionId: string(dict["sessionId"] ?? dict["session_id"]),
            currentRoomId: string(dict["currentRoomId"] ?? dict["current_room_id"]),
            address: string(dict["address"]).flatMap { $0.isEmpty ? nil : $0 } ?? "Property",
            meta: string(dict["meta"]) ?? "",
            kind: kind,
            status: string(dict["status"]),
            rooms: rooms,
            pipeline: pipeline,
            frameCount: int(dict["frameCount"] ?? dict["frame_count"]) ?? 0,
            guidance: guidance,
            estimatedDeliveryHours: double(dict["estimatedDeliveryHours"] ?? dict["estimated_delivery_hours"]),
            outputStoragePath: string(dict["outputStoragePath"] ?? dict["output_storage_path"]),
            stationCount: int(dict["stationCount"] ?? dict["station_count"]) ?? stations.count,
            currentStationIndex: int(dict["currentStationIndex"] ?? dict["current_station_index"]) ?? 0,
            stations: stations
        )
    }

    public static func parseSign(_ data: Data) -> CaptureSignPayload? {
        guard let dict = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return nil }
        guard let path = string(dict["path"]), !path.isEmpty else { return nil }
        let sid = string(dict["session_id"] ?? dict["sessionId"]) ?? ""
        return CaptureSignPayload(
            path: path,
            token: string(dict["token"]),
            signedURL: string(dict["signed_url"] ?? dict["signedUrl"]),
            bucket: string(dict["bucket"]) ?? "brxs-deliverables",
            sessionId: sid,
            roomId: string(dict["room_id"] ?? dict["roomId"]),
            stationIndex: int(dict["station_index"] ?? dict["stationIndex"]),
            role: string(dict["role"]) ?? "pan",
            contentType: string(dict["content_type"] ?? dict["contentType"]) ?? "image/jpeg"
        )
    }

    public static func fail(_ data: Data, fallback: String) -> CaptureCallResult {
        let parsed = BricksAPIError.parseBody(data)
        return .failure(parsed.message.isEmpty ? fallback : parsed.message, code: parsed.code)
    }

    private static func asKind(_ raw: Any, fallback: CaptureKind) -> CaptureKind {
        let k = String(describing: raw).lowercased().replacingOccurrences(of: "-", with: "").replacingOccurrences(of: "_", with: "")
        if k == "floorplan" || k == "floorplans" { return .floorplan }
        if k == "splat" || k == "tour" || k == "3dtour" { return .splat }
        return fallback
    }

    private static func string(_ v: Any?) -> String? {
        guard let v else { return nil }
        if v is NSNull { return nil }
        if let s = v as? String { return s.isEmpty ? nil : s }
        return String(describing: v)
    }

    private static func int(_ v: Any?) -> Int? {
        guard let v, !(v is NSNull) else { return nil }
        if let i = v as? Int { return i }
        if let n = v as? NSNumber { return n.intValue }
        if let d = v as? Double { return Int(d) }
        if let s = v as? String { return Int(s) }
        return nil
    }

    private static func double(_ v: Any?) -> Double? {
        guard let v, !(v is NSNull) else { return nil }
        if let d = v as? Double { return d }
        if let i = v as? Int { return Double(i) }
        if let n = v as? NSNumber { return n.doubleValue }
        if let s = v as? String { return Double(s) }
        return nil
    }

    private static func bool(_ v: Any?) -> Bool? {
        guard let v, !(v is NSNull) else { return nil }
        if let b = v as? Bool { return b }
        if let n = v as? NSNumber { return n.boolValue }
        return nil
    }
}
