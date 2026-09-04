import XCTest
@testable import BricksCore

final class BricksCoreTests: XCTestCase {
    func testModuleLoads() {
        XCTAssertTrue(true)
    }

    func testAPIURLUsesQueryItemsNotPathComponent() {
        let base = URL(string: "https://dispatch-api.brxs.ca")!
        let url = BricksAPIURL.make(
            base: base,
            path: "/api/vendor-os/listings",
            query: [
                URLQueryItem(name: "q", value: "king"),
                URLQueryItem(name: "job_id", value: "abc"),
            ]
        )
        XCTAssertEqual(url.scheme, "https")
        XCTAssertEqual(url.host, "dispatch-api.brxs.ca")
        XCTAssertEqual(url.path, "/api/vendor-os/listings")
        XCTAssertTrue(url.absoluteString.contains("q=king"))
        XCTAssertTrue(url.absoluteString.contains("job_id=abc"))
        XCTAssertFalse(url.path.contains("?"))
    }

    func testAPIURLDoesNotPercentEncodeQueryInPath() {
        let base = URL(string: "https://dispatch-api.brxs.ca")!
        let url = BricksAPIURL.make(base: base, path: "/api/vendor-os/team?page=1&page_size=200")
        XCTAssertEqual(url.path, "/api/vendor-os/team")
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        XCTAssertEqual(items.first { $0.name == "page" }?.value, "1")
        XCTAssertEqual(items.first { $0.name == "page_size" }?.value, "200")
    }

    func testJobDecodesMissingOptionalFields() throws {
        let json = """
        {"id":"job-1","address":"10 Main St","status":"pending_assignment","scheduled_date":"2026-08-29"}
        """.data(using: .utf8)!
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let job = try decoder.decode(DispatchJob.self, from: json)
        XCTAssertEqual(job.id, "job-1")
        XCTAssertEqual(job.address, "10 Main St")
        XCTAssertEqual(job.status, .pendingAssignment)
        XCTAssertNil(job.assigneeId)
        XCTAssertNil(job.assigneeName)
        XCTAssertNil(job.transactionId)
        XCTAssertNil(job.listingAddress)
        XCTAssertEqual(job.lat, 0)
        XCTAssertEqual(job.checklist, [])
    }

    func testJobDecodesAssigneeAndListing() throws {
        let json = """
        {"id":"job-2","assignee_id":"0c3fa7c2-aaaa","assignee_name":"Kam","transaction_id":"txn-1","listing_address":"12 King","status":"assigned"}
        """.data(using: .utf8)!
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let job = try decoder.decode(DispatchJob.self, from: json)
        XCTAssertEqual(job.assigneeId, "0c3fa7c2-aaaa")
        XCTAssertEqual(job.assigneeName, "Kam")
        XCTAssertEqual(job.transactionId, "txn-1")
        XCTAssertEqual(job.listingAddress, "12 King")
        XCTAssertEqual(job.status, .assigned)
    }

    func testJobsResponseKeyIsItems() throws {
        struct JobsResponse: Decodable { let items: [DispatchJob] }
        let json = """
        {"items":[{"id":"a","status":"assigned"}],"total":1}
        """.data(using: .utf8)!
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let res = try decoder.decode(JobsResponse.self, from: json)
        XCTAssertEqual(res.items.count, 1)
        XCTAssertEqual(res.items[0].id, "a")
    }

    func testVendorAdminGate() {
        XCTAssertTrue(VendorRole.isVendorAdmin(id: "x", role: "Owner", title: nil))
        XCTAssertTrue(VendorRole.isVendorAdmin(id: "x", role: "vendor", title: "Office Manager"))
        XCTAssertTrue(VendorRole.isVendorAdmin(id: "0c3fa7c2-ffff", role: "Photographer IC", title: nil))
        XCTAssertFalse(VendorRole.isVendorAdmin(id: "ic-1", role: "Photographer IC", title: "Photographer IC"))
    }

    func testPickerMemberIncludesOwnerExcludesAman() {
        let kam = TeamMember(id: "0c3fa7c2-aaaa", name: "Kam", role: "Owner", title: "Owner", isActive: true)
        let ic = TeamMember(id: "ic-1", name: "Pat", role: "Photographer IC", isActive: true, assignable: true)
        let aman = TeamMember(id: "aman-1", name: "Aman", role: "Photographer IC", isActive: true, assignable: false)
        let inactive = TeamMember(id: "old", name: "Old", isActive: false)
        XCTAssertTrue(VendorRole.isPickerMember(kam))
        XCTAssertTrue(VendorRole.isPickerMember(ic))
        XCTAssertFalse(VendorRole.isPickerMember(aman))
        XCTAssertFalse(VendorRole.isPickerMember(inactive))
    }

    func testTorontoTodayFormat() {
        let s = DispatchJob.torontoToday()
        XCTAssertEqual(s.count, 10)
        XCTAssertTrue(s.contains("-"))
    }

    func testCaptureSessionParsesCamelCase() throws {
        let json = """
        {"sessionId":"s1","currentRoomId":"r1","address":"10 Main","meta":"PHOTO","kind":"splat","frameCount":12,"stationCount":1,"currentStationIndex":0,"rooms":[{"id":"r1","name":"Kitchen","tone":"ok","n":12}],"pipeline":[{"title":"Captured","detail":"12 frames","status":"active"}],"stations":[{"station_index":0,"room":"Kitchen","panCount":12,"hasEquirect":false}]}
        """.data(using: .utf8)!
        let s = CaptureAPIClient.parseSession(json, kind: .splat)
        XCTAssertEqual(s.sessionId, "s1")
        XCTAssertEqual(s.currentRoomId, "r1")
        XCTAssertEqual(s.address, "10 Main")
        XCTAssertEqual(s.frameCount, 12)
        XCTAssertEqual(s.stationCount, 1)
        XCTAssertEqual(s.rooms.count, 1)
        XCTAssertEqual(s.rooms[0].name, "Kitchen")
        XCTAssertEqual(s.stations.first?.panCount, 12)
        XCTAssertTrue(s.canSubmit)
    }

    func testCaptureSessionParsesSnakeCase() throws {
        let json = """
        {"session_id":"s2","current_room_id":"r2","address":"12 King","kind":"floorplan","frame_count":4,"station_count":1,"current_station_index":1,"rooms":[{"id":"r2","name":"Bath","tone":"warn","frames":4}]}
        """.data(using: .utf8)!
        let s = CaptureAPIClient.parseSession(json, kind: .floorplan)
        XCTAssertEqual(s.sessionId, "s2")
        XCTAssertEqual(s.kind, .floorplan)
        XCTAssertEqual(s.frameCount, 4)
        XCTAssertEqual(s.currentStationIndex, 1)
        XCTAssertEqual(s.rooms.first?.n, 4)
    }

    func testCaptureForbiddenErrorCopy() {
        let data = """
        {"detail":{"code":"capture_kind_not_purchased","message":"This job has no 3D tour. Start Capture does not shoot a tour that was not purchased."}}
        """.data(using: .utf8)!
        let parsed = BricksAPIError.parseBody(data)
        XCTAssertEqual(parsed.code, CaptureAPIClient.notPurchasedCode)
        XCTAssertTrue(parsed.message.contains("3D tour"))
        let fail = CaptureAPIClient.fail(data, fallback: "HTTP 403")
        XCTAssertFalse(fail.ok)
        XCTAssertEqual(fail.code, "capture_kind_not_purchased")
        XCTAssertEqual(fail.error, parsed.message)
    }

    func testDomeTickWraps() {
        XCTAssertEqual(CaptureDome.tick(yaw: 0, origin: 0), 0)
        XCTAssertEqual(CaptureDome.tick(yaw: 15, origin: 0), 1)
        XCTAssertEqual(CaptureDome.tick(yaw: 350, origin: 0), 23)
        XCTAssertEqual(CaptureDome.tick(yaw: 10, origin: 10), 0)
        XCTAssertEqual(CaptureDome.total, 24)
    }

    func testShootWindowAfternoon() {
        let s = ShootWindow.format(
            preferredWindow: "afternoon",
            timeWindowStart: "12:00",
            timeWindowEnd: "16:00"
        )
        XCTAssertEqual(s, "Afternoon · 12:00–16:00 ET")
    }

    func testShootWindowMissingHonest() {
        let s = ShootWindow.format(
            preferredWindow: nil,
            timeWindowStart: "",
            timeWindowEnd: ""
        )
        XCTAssertEqual(s, "Shoot window not set")
    }

    func testJobDecodesPreferredWindow() throws {
        let json = """
        {"id":"job-w","preferred_window":"evening","time_window_start":"16:00","time_window_end":"19:00","status":"assigned"}
        """.data(using: .utf8)!
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let job = try decoder.decode(DispatchJob.self, from: json)
        XCTAssertEqual(job.preferredWindow, "evening")
        XCTAssertEqual(job.formattedShootWindow, "Evening · 16:00–19:00 ET")
    }

    func testCaptureTagsParseAndFlatten() throws {
        let json = """
        {"sessionId":"s-tag","kind":"splat","address":"10 Main","frameCount":8,"stationCount":1,"rooms":[],"pipeline":[{"title":"Captured","detail":"ok","status":"done"}],"stations":[{"station_index":0,"room":"Kitchen","panCount":8,"hasEquirect":true}],"tags":[{"id":"t1","label":"Walk-in closet","note":"Cedar","yaw":45.2,"pitch":-8,"station_index":0}]}
        """.data(using: .utf8)!
        let s = CaptureAPIClient.parseSession(json, kind: .splat)
        XCTAssertEqual(s.tags.count, 1)
        XCTAssertEqual(s.allTags.first?.label, "Walk-in closet")
        XCTAssertEqual(s.allTags.first?.note, "Cedar")
        XCTAssertEqual(s.allTags.first?.yaw ?? 0, 45.2, accuracy: 0.01)
        XCTAssertEqual(s.allTags.first?.pitch ?? 0, -8, accuracy: 0.01)
        XCTAssertEqual(s.allTags.first?.stationIndex, 0)
    }

    func testCaptureTagsNestedOnStation() throws {
        let json = """
        {"session_id":"s-nested","kind":"splat","stations":[{"station_index":2,"room":"Bath","tags":[{"id":"n1","label":"Soaker tub","yaw":10,"pitch":0}]}]}
        """.data(using: .utf8)!
        let s = CaptureAPIClient.parseSession(json, kind: .splat)
        XCTAssertTrue(s.tags.isEmpty)
        XCTAssertEqual(s.allTags.count, 1)
        XCTAssertEqual(s.allTags[0].label, "Soaker tub")
        XCTAssertEqual(s.allTags[0].stationIndex, 2)
        XCTAssertEqual(s.stations.first?.tags.count, 1)
    }

    func testTourTagLookWrap() {
        XCTAssertEqual(TourTag.wrapYaw(190), -170, accuracy: 0.01)
        XCTAssertEqual(TourTag.wrapYaw(-190), 170, accuracy: 0.01)
        XCTAssertEqual(TourTag.wrapYaw(0), 0, accuracy: 0.01)
        XCTAssertEqual(TourTag.clampPitch(90), 80, accuracy: 0.01)
        XCTAssertEqual(TourTag.clampPitch(-90), -80, accuracy: 0.01)
        XCTAssertEqual(TourTag.clampPitch(12.34), 12.3, accuracy: 0.01)
    }
}
