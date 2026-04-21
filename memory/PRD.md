# PRD: Vendor Onboarding & Vetting System

## 1. Goal
To build a scalable, trust-based pipeline for identifying, vetting, and onboarding high-quality photographers and field technicians (Vendors) into the BRICKS ecosystem.

## 2. Target Users
- **Applicants**: External photographers wanting to join the platform.
- **Admin (Bricks Operations)**: Internal users who review applications, verify insurance, and approve vendors.
- **Dispatch AI**: The system that consumes approved vendor profiles for job assignment.

## 3. The Vendor Lifecycle (States)
1. **Applied**: Initial form submitted. Profile created in `vendor_members` with `is_active: false` and `status: "pending_review"`.
2. **Vetting**: Admin is reviewing portfolio, equipment list, and conducting interviews.
3. **Compliance**: Vendor has passed initial vetting and is now uploading COI (Certificate of Insurance) and W-9.
4. **Approved**: Verification complete. `is_active` set to `true`. Vendor can now receive AI-dispatched jobs.
5. **Rejected/Disabled**: Not a fit or removed from the platform.

## 4. Key Requirements

### A. Public Application Flow
- **Endpoint**: `POST /api/onboarding/apply`
- **Fields**: 
    - Personal: Name, Email, Phone, Primary City.
    - Professional: Specialties (Photo, Video, Drone, etc.), Years of Experience.
    - Portfolio: Links to previous work (Instagram, Website, Drive).
    - Equipment: List of gear (e.g., "Sony A7IV, DJI Mavic 3, Matterport Pro3").

### B. Admin Vetting Dashboard (Brokerage Side)
- List view of all applicants filtered by state.
- Detail view to see portfolio links and equipment.
- Action buttons: "Move to Compliance", "Reject", "Approve".

### C. Compliance Management
- **File Uploads**: Integration with S3/Storage service to hold sensitive documents.
- **Expiry Tracking**: Automated system to flag expired Insurance (COI) in the future.

### D. Integration with Dispatch
- Once **Approved**:
    - Assign an initial `quality_rating` (default: 4.0 or based on admin assessment).
    - Set `completion_rate` to 100% (baseline).
    - Initialize `AvailabilitySettings` (default: 8am-5pm).

## 5. Schema Updates (Proposed)

### VendorMember (Additions)
- `onboarding_status`: `pending`, `vetting`, `compliance`, `approved`, `rejected`
- `portfolio_links`: List of strings.
- `equipment_list`: List of strings.
- `service_areas`: List of Zip codes or City names.
- `internal_notes`: For admin use during vetting.

### Documents (New Collection)
- `id`, `vendor_id`, `doc_type` (COI, W9), `file_url`, `expiry_date`, `status` (verified/pending).

## 6. User Stories
- **As an applicant**, I want a simple form to submit my work so I can start getting jobs from Bricks.
- **As an Admin**, I want to see a list of new applicants in my city so I can grow the local crew.
- **As an Admin**, I want to verify that a photographer has a drone license before I approve them for drone jobs.
- **As the Dispatch AI**, I should only see "Approved" vendors with valid compliance docs.

## 7. Success Metrics
- **Time to Onboard**: Average time from "Applied" to "Approved" < 5 business days.
- **Vetting Accuracy**: < 5% of newly onboarded vendors receive a rating below 3 stars on their first 5 jobs.
