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
}
