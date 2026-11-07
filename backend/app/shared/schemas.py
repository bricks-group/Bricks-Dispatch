from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field, model_validator


SESSION_TTL_DAYS = 7


class ChecklistItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    label: str
    completed: bool = False


class Job(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # Cross-service reference — set when job originates from a Booking booking
    booking_id: Optional[str] = None
    address: str
    city: str = ""
    state: str = ""
    zip_code: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    time_window_start: str
    time_window_end: str = ""
    preferred_window: Optional[str] = None  # morning|afternoon|evening from /book
    confirmed_arrive_at: Optional[str] = None  # photographer HH:MM in job timezone
    selected_slot_start: Optional[str] = None  # ISO start of booked clock (one visit)
    selected_slot_end: Optional[str] = None  # ISO end of booked clock
    estimated_duration_min: Optional[int] = None  # on-site minutes for full package
    timezone: Optional[str] = None  # IANA, e.g. America/Vancouver. Missing → resolve from province.
    job_type: str
    status: str = "assigned"
    priority: str = "normal"
    property_details: str = ""
    client_instructions: str = ""
    checklist: List[ChecklistItem] = []
    deliverables_count: int = 0
    uploaded_count: int = 0
    assignee_id: str = "default_user"
    vendor_id: str = "default_vendor"
    scheduled_date: str = ""
    # Agent context — populated when job originates from Booking
    agent_user_id: Optional[str] = None
    agent_name: Optional[str] = None
    agent_email: Optional[str] = None
    agent_brokerage: Optional[str] = None
    # Package context
    package_id: Optional[str] = None
    package_name: Optional[str] = None
    package_price: Optional[int] = None
    rack_price: Optional[int] = None  # undiscounted rate; vendor payout must always be computed off this, never package_price once discounted billing ships
    add_ons: list = []
    add_on_total: Optional[int] = None
    billing_type: Optional[str] = None
    overage_amount: Optional[int] = None
    # Enriched Booking fields — populated when job originates via Booking HTTP intake
    vendor_name: Optional[str] = None
    vendor_tier: Optional[int] = None
    vendor_payout: Optional[float] = None
    platform_margin: Optional[float] = None
    sqft: Optional[int] = None
    is_rush: bool = False
    complexity_score: Optional[int] = None
    complexity_band: Optional[str] = None
    min_tier_required: Optional[int] = None
    preferred_tier: Optional[int] = None
    ai_flags: Optional[list] = None
    # brokerage_id (jobs.brokerage_id, uuid): read by bricks_ai_dispatch.py's scorer for the
    # +15pt brokerage-favourite bonus. Was missing here until 2026-07-27 -- pydantic v2's
    # default extra='ignore' silently dropped it on every Job(**job_doc) reconstruction,
    # so getattr(job, "brokerage_id", None) always returned None and the bonus never fired,
    # unconditionally, regardless of what Booking/Companion sent. See BRICKS_STATUS.md.
    brokerage_id: Optional[str] = None
    # Office listing this shoot belongs to (jobs.transaction_id -> transactions.id).
    transaction_id: Optional[str] = None
    # payout_amount / dispatch_explanation: written directly via jobs.update() elsewhere
    # (payout_service.py, bricks_ai_dispatch.py) -- also missing here until 2026-07-27, same
    # silent-drop pattern, just not yet known to be read back anywhere.
    payout_amount: Optional[int] = None
    dispatch_explanation: Optional[str] = None
    # Cross-service reference — set when job originates from a Bricks-OS
    # developer/REIT progress-capture visit instead of a Booking booking.
    # Exactly one of booking_id / dev_capture_visit_id should be set.
    dev_project_id: Optional[str] = None
    dev_phase_id: Optional[str] = None
    dev_capture_visit_id: Optional[str] = None
    # Commercial jobs: minimum verified drone/aviation liability (CAD) the
    # assignee must hold; None = $2M default (5_000_000 for CCDC 41 contracts).
    min_insurance_cad: Optional[int] = None
    # Commercial jobs default to Advanced RPAS; False only when the site is
    # confirmed outside controlled airspace / away from people.
    requires_rpas_advanced: Optional[bool] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class JobIntakeRequest(BaseModel):
    """Payload accepted by POST /api/v1/dispatch/jobs from Booking, or from
    Bricks-OS for a developer/REIT progress-capture visit. booking_id is
    optional so the latter can omit it — see dev_capture_visit_id below;
    create_dispatch_job() requires exactly one of the two to be set."""
    booking_id: Optional[str] = None
    address: str
    city: str = ""
    state: str = ""
    zip_code: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    time_window_start: str
    time_window_end: str = ""
    preferred_window: Optional[str] = None
    confirmed_arrive_at: Optional[str] = None
    selected_slot_start: Optional[str] = None
    selected_slot_end: Optional[str] = None
    estimated_duration_min: Optional[int] = None
    timezone: Optional[str] = None  # IANA from OS / Stripe metadata; province fallback
    job_type: str
    priority: str = "normal"
    property_details: str = ""
    client_instructions: str = ""
    checklist: List[dict] = []
    deliverables_count: int = 0
    assignee_id: str = "default_user"
    vendor_id: str = "default_vendor"
    scheduled_date: str = ""
    agent_user_id: Optional[str] = None
    agent_name: Optional[str] = None
    agent_email: Optional[str] = None
    agent_brokerage: Optional[str] = None
    package_id: Optional[str] = None
    package_name: Optional[str] = None
    package_price: Optional[int] = None
    rack_price: Optional[int] = None  # undiscounted rate; vendor payout must always be computed off this, never package_price once discounted billing ships
    add_ons: list = []
    add_on_total: Optional[int] = None
    billing_type: Optional[str] = None
    overage_amount: Optional[int] = None
    vendor_name: Optional[str] = None
    vendor_tier: Optional[int] = None
    vendor_payout: Optional[float] = None
    platform_margin: Optional[float] = None
    sqft: Optional[int] = None
    is_rush: bool = False
    complexity_score: Optional[int] = None
    complexity_band: Optional[str] = None
    min_tier_required: Optional[int] = None
    preferred_tier: Optional[int] = None
    ai_flags: Optional[list] = None
    # jobs.brokerage_id (uuid) already exists in the live DB; this field was missing from
    # intake until 2026-07-27, so Companion's brokerage_id was silently dropped before it
    # ever reached the insert. See Job's comment above for the downstream consequence.
    brokerage_id: Optional[str] = None
    # See Job's comment above — set instead of booking_id for a developer/REIT
    # progress-capture visit; create_dispatch_job() requires exactly one.
    dev_project_id: Optional[str] = None
    dev_phase_id: Optional[str] = None
    dev_capture_visit_id: Optional[str] = None
    # Commercial jobs: minimum verified drone/aviation liability (CAD) the
    # assignee must hold; None = $2M default (5_000_000 for CCDC 41 contracts).
    min_insurance_cad: Optional[int] = None
    # Commercial jobs default to Advanced RPAS; False only when the site is
    # confirmed outside controlled airspace / away from people.
    requires_rpas_advanced: Optional[bool] = None


class JobStatusUpdate(BaseModel):
    status: str
    completed_at: Optional[str] = None  # vendor device timestamp for on-time accuracy
    cancel_reason: Optional[str] = None  # vendor hand-back reason (vendor_termination.CANCEL_REASONS)
    cancel_note: Optional[str] = None


class ChecklistProgress(BaseModel):
    """Progress snapshot for status update payloads."""
    total: int
    completed: int


class JobStatusUpdateFull(BaseModel):
    """Full status update payload from Dispatch to Booking.

    Sent via PUT /api/v1/dispatch-jobs/{booking_id}/status
    See: dispatch-booking-status.schema.json
    """
    api_version: str = "v1"
    job_id: str
    new_status: str
    previous_status: Optional[str] = None
    changed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    actor_id: Optional[str] = None
    actor_type: Optional[str] = None
    notes: str = ""
    checklist_progress: Optional[ChecklistProgress] = None


class StatusUpdateResponse(BaseModel):
    """Response from Booking acknowledging a status update."""
    accepted: bool
    booking_id: str
    updated_dispatch_status: str
    dispatch_job_id: Optional[str] = None


class UploadNotification(BaseModel):
    """Upload notification payload from Dispatch to Booking.

    Sent via POST /api/v1/dispatch-jobs/{booking_id}/uploads
    See: dispatch-upload.schema.json
    """
    api_version: str = "v1"
    upload_id: str
    job_id: str
    filename: str
    file_size: int
    mime_type: str
    uploaded_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class UploadNotificationResponse(BaseModel):
    """Response from Booking acknowledging an upload notification."""
    accepted: bool
    booking_id: str
    new_uploads_count: int
    total_uploads: int


class WebhookEventData(BaseModel):
    """Inner payload of a dispatch webhook event."""
    booking_id: str
    job_id: str
    previous_status: Optional[str] = None
    new_status: str
    actor_id: Optional[str] = None
    actor_type: Optional[str] = None


class DispatchWebhookEvent(BaseModel):
    """Webhook event sent by Dispatch to Booking's callback URL.

    See: dispatch-webhook-event.schema.json
    """
    event_type: str
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str = "dispatch"
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    data: WebhookEventData


class ChecklistToggle(BaseModel):
    completed: bool


class UploadRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str
    filename: str
    file_size: int = 0
    mime_type: str = ""
    thumbnail: str = ""          # 200-char base64 preview (backward compat)
    file_url: Optional[str] = None     # S3 object key for original file
    thumbnail_url: Optional[str] = None  # S3 object key for thumbnail
    qc_variant_url: Optional[str] = None  # S3 object key for QC-review-sized JPEG
    media_type: str = "hdr"  # hdr | drone | reel | twilight | staging
    processing_status: Optional[str] = None  # pending | processing | done | failed
    requires_staging: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Profile(BaseModel):
    id: str = "default_user"
    name: str = "Alex Rivera"
    email: str = "alex.rivera@bricksdispatch.com"
    role: str = "Photographer IC"
    title: str = "Photographer IC"
    phone: str = "+1 (555) 867-5309"
    availability: bool = True
    avatar_url: str = ""
    total_jobs_completed: int = 147
    rating: float = 4.9
    member_since: str = "2024-03-15"
    skills: List[str] = []
    service_areas: List[str] = []
    rpas_certificate_url: Optional[str] = None
    rpas_certificate_level: Optional[str] = None
    payout_model: str = "ic_rate_card"
    assignable: bool = False


class ProfileUpdate(BaseModel):
    availability: Optional[bool] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    skills: Optional[List[str]] = None
    service_areas: Optional[List[str]] = None
    home_city: Optional[str] = None
    bio: Optional[str] = None


class PushTokenRegister(BaseModel):
    token: Optional[str] = None
    device_token: Optional[str] = None  # Swift GO posts this
    platform: str = "expo"


class NotificationRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    body: str
    type: str = "info"
    data: dict = {}
    read: bool = False
    member_id: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Coordinate(BaseModel):
    lat: float
    lng: float


class PortfolioSample(BaseModel):
    """One portfolio example keyed to a Bricks catalog media type."""
    media_type: str  # hdr | drone | floor_plan | tour | twilight | reel
    url: Optional[str] = None  # storage key or signed path for uploaded image
    link: Optional[str] = None  # supplemental external URL
    thumb_url: Optional[str] = None
    filename: Optional[str] = None


class VendorMember(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _null_columns_use_defaults(cls, data: Any) -> Any:
        # DB rows carry NULL for columns a signup path never wrote (title, phone,
        # home_city, internal_notes...). A NULL in a non-Optional field used to
        # fail validation, and auto-assign silently skipped that vendor
        # ("Skipping vendor_members row"). Drop NULLs for fields that have a
        # default so the default applies; Optional fields keep None.
        if not isinstance(data, dict):
            return data
        out = dict(data)
        for key, value in data.items():
            if value is not None:
                continue
            field = cls.model_fields.get(key)
            if field is None or field.is_required():
                continue
            ann = field.annotation
            if ann is not None and type(None) in getattr(ann, "__args__", ()):
                continue
            out.pop(key, None)
        return out

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    vendor_id: str = "default_vendor"
    name: str
    email: str
    phone: str = ""
    role: str = "Photographer IC"
    title: str = "Photographer IC"
    # photographer | stager | cleaner (DB check). inspector | measurer | handyman
    # are stored in application_profile.trade until the widening migration.
    # Drone/RPAS is a photographer capability.
    vendor_type: str = "photographer"
    specialty: str = "photo"
    skills: List[str] = []
    payout_mode: Optional[str] = None  # owner_full | ic_rate_card
    rpas_certificate_url: Optional[str] = None
    status: str = "available"
    is_active: bool = False
    onboarding_status: str = "pending"
    portfolio_links: List[str] = []
    portfolio_samples: List[PortfolioSample] = []
    equipment_list: List[str] = []
    service_areas: List[str] = [] # Legacy city-based
    operation_polygon: List[Coordinate] = [] # The "Connected Dots"
    home_city: str = ""
    max_travel_km: int = 50
    tier: int = 1
    drone_certified: bool = False
    rpas_advanced: bool = False
    rpas_certificate_number: Optional[str] = None
    rpas_certificate_level: Optional[str] = None  # "basic" | "advanced"
    # Polycam / splat gear flag for Virtual Visit deliverables. Virtual Visit is
    # always a paid www add-on (never packaged into Photos only–Full suite).
    # Unlike drone_certified there is no Transport Canada registry check —
    # admin-set via VendorMemberUpdate. Defaults False for new applicants.
    polycam_equipped: bool = False
    hst_number: Optional[str] = None
    wsib_number: Optional[str] = None
    insurance_policy_number: Optional[str] = None
    # Optional copies of a verified COI/WSIB expiry (admin_ops.verify_document).
    # Historical storage only. Apply, approval, assignment, and dispatch do
    # not read these fields and do not require a COI or WSIB record.
    coi_expires_on: Optional[str] = None
    wsib_expires_on: Optional[str] = None
    # Commercial gate (Kam, 2026-10-17): listing jobs still ignore insurance.
    # Commercial jobs and Tier A require coi_verified + coi_aviation_covered +
    # unexpired coi_expires_on + coi_liability_cad >= job minimum (default $2M).
    # Set only by an admin verify (OS /admin/vendors/{id}/insurance or
    # Dispatch admin_ops.verify_document), never by the vendor.
    coi_verified: Optional[bool] = False
    coi_liability_cad: Optional[int] = None
    coi_aviation_covered: Optional[bool] = False
    coi_verified_at: Optional[str] = None
    # contractor (default) | employee (in-house staff: company policy covers
    # commercial insurance; no per-job payout; salary handled outside Dispatch)
    worker_type: Optional[str] = "contractor"
    internal_notes: str = ""
    completion_rate: float = 1.0
    avg_turnaround_days: float = 0.0
    quality_rating: float = 4.0
    on_time_rate: float = 1.0
    cancel_rate: float = 0.0
    decline_rate: float = 0.0  # job-offer refusals / offers received (rolling 30d)
    acceptance_latency_avg_sec: Optional[int] = None
    total_assignments: int = 0
    active_assignments: int = 0
    home_lat: Optional[float] = None
    home_lng: Optional[float] = None
    ic_agreement_version: Optional[str] = None
    terms_accepted_at: Optional[str] = None
    # Separate bearer credential for the pre-approval onboarding endpoints
    # (GET /status/{member_id}, POST /documents), which have no real login
    # session to check against yet. Only set for applicants created via
    # /apply -- None for members created directly by an admin (create_vendor_member).
    application_token: Optional[str] = None
    # How the applicant heard about Bricks. Null for older applicants.
    source: Optional[str] = None
    source_detail: Optional[str] = None
    # Structured application (migration 0017). Null for older applicants.
    application_profile: Optional[Dict[str, Any]] = None
    # Onboarding gate (migration 20261031_vendor_onboarding_gate). Approval puts a
    # vendor into onboarding; auto-assign needs onboarding_completed_at.
    onboarding_completed_at: Optional[str] = None
    first_signed_in_at: Optional[str] = None
    guide_acknowledged_at: Optional[str] = None
    availability_confirmed_at: Optional[str] = None
    rejected_at: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class VendorApplicationCreate(BaseModel):
    name: str
    email: str
    phone: str
    # Wave A vendor class; photographers.html posts photographer (default).
    vendor_type: str = "photographer"
    specialties: List[str]
    portfolio_links: List[str] = []
    portfolio_samples: List[PortfolioSample] = []
    equipment_list: List[str] = []
    city: str
    max_travel_km: int = 50
    operation_polygon: List[Coordinate] = []
    home_lat: Optional[float] = None
    home_lng: Optional[float] = None
    # Ontario Regions: GTA, York, Peel, Halton, Durham, Hamilton, Waterloo, Ottawa, etc.
    preferred_regions: List[str] = []
    accepted_terms: bool
    ic_agreement_version: str = "1.9"
    hst_number: Optional[str] = None
    # CRA small-supplier exemption: under $30,000 CAD/year across all clients,
    # registration for GST/HST isn't legally required. Lets an applicant pass
    # the gate below without a number by attesting to the exemption instead.
    hst_exempt: bool = False
    drone_certified: bool = False
    rpas_certificate_number: Optional[str] = None
    rpas_certificate_level: Optional[str] = None  # "basic" | "advanced" (Transport Canada)
    # Honeypot: real users never see/fill this (off-screen). Bots that autofill
    # every field will, and we reject silently-ish on the server.
    company_website: Optional[str] = None
    # "How did you hear about Bricks?" (www form). Optional here so older
    # clients still apply; see app/shared/applicant_source.py.
    source: Optional[str] = None
    source_detail: Optional[str] = None
    # Evidence (2026-10-12). Photographers upload 8-15 sample photos straight
    # to storage via POST /api/onboarding/sample-uploads, then send the paths
    # here with the signed session id. See app/services/applicant_evidence.py.
    sample_upload_session: Optional[str] = None
    sample_paths: List[str] = []
    rpas_cert_path: Optional[str] = None
    # Photographers (2026-10-31): samples must be real estate (interiors and
    # exteriors of homes) and a resume is required. The resume goes up through
    # /sample-uploads (kind=resume) in the same session.
    samples_are_real_estate: bool = False
    resume_path: Optional[str] = None
    resume_name: Optional[str] = None
    # {camera_body, lenses, drone_model, lighting, tripod}
    equipment: Optional[Dict[str, Any]] = None
    # {days_of_week, time_windows, weekends, earliest_start_date,
    #  shoots_per_week, max_shoots_per_day, notice_hours, has_car, has_licence}
    availability: Optional[Dict[str, Any]] = None
    # Non-media trades (2026-10-26). {cgl_confirmed, cgl_limit_cents,
    # insurer, wsib_number, years_experience, licences, team_size, service_notes}.
    # Repairs and inspections must confirm $2M CGL; the COI file goes to
    # POST /api/onboarding/documents (doc_type=coi) after /apply.
    trade_credentials: Optional[Dict[str, Any]] = None
    # Trade vetting (2026-11-01): reviews link or references, commercial drone flag.
    vetting: Optional[Dict[str, Any]] = None
    # www trade form uploads documents after /apply, then calls /apply/{id}/submitted,
    # which runs the review and notifies Kam. False keeps the old notify-on-apply.
    docs_pending: bool = False


class SampleUploadFile(BaseModel):
    name: str = ""
    type: str
    size: int
    kind: str = "sample"  # sample | rpas_cert | resume


class SampleUploadRequest(BaseModel):
    files: List[SampleUploadFile]
    session: Optional[str] = None


class VendorDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    vendor_id: str
    member_id: str
    doc_type: str  # coi, wsib, hst_registry, id_verification, rpas_certificate, resume
    file_url: str
    status: str = "pending"  # pending, verified, rejected
    expiry_date: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class VendorLead(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    portfolio_url: Optional[str] = None
    city: str
    source: str  # instagram, google, linkedin, referral
    status: str = "identified"  # identified, contacted, interested, converted, not_interested
    notes: str = ""
    last_contacted_at: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class VendorMemberCreate(BaseModel):
    name: str
    email: str
    phone: str = ""
    role: str = "Photographer"
    vendor_type: str = "photographer"


class VendorMemberUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    title: Optional[str] = None
    vendor_type: Optional[str] = None
    skills: Optional[List[str]] = None
    service_areas: Optional[List[str]] = None
    payout_mode: Optional[str] = None
    rpas_certificate_url: Optional[str] = None
    rpas_certificate_level: Optional[str] = None
    status: Optional[str] = None
    is_active: Optional[bool] = None
    onboarding_status: Optional[str] = None
    polycam_equipped: Optional[bool] = None
    hst_number: Optional[str] = None
    wsib_number: Optional[str] = None
    internal_notes: Optional[str] = None
    quality_rating: Optional[float] = None
    on_time_rate: Optional[float] = None
    cancel_rate: Optional[float] = None


class JobAssignmentUpdate(BaseModel):
    member_id: Optional[str] = None


class JobCreate(BaseModel):
    id: Optional[str] = None
    booking_id: Optional[str] = None
    address: str
    city: str = ""
    state: str = "ON"
    zip_code: str = ""
    time_window_start: str = "10:00"
    time_window_end: str = "11:00"
    job_type: str
    status: str = "assigned"
    priority: str = "normal"
    property_details: str = ""
    client_instructions: str = ""
    scheduled_date: str
    deliverables_count: int = 0
    uploaded_count: int = 0
    assignee_id: str = "default_user"
    vendor_id: str = "default_vendor"
    package_id: Optional[str] = None
    package_name: Optional[str] = None
    package_price: Optional[int] = None
    rack_price: Optional[int] = None  # undiscounted rate; vendor payout must always be computed off this, never package_price once discounted billing ships


class BlockedWindow(BaseModel):
    start: str
    end: str


class AvailabilitySettings(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    vendor_id: str = "default_vendor"
    member_id: str = "default_user"
    available: bool = True
    work_start: str = "08:00"
    work_end: str = "17:00"
    jobs_per_day: int = 4
    blocked_windows: List[BlockedWindow] = []
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AvailabilityUpdate(BaseModel):
    available: bool
    work_start: str
    work_end: str
    jobs_per_day: int
    blocked_windows: List[BlockedWindow] = []


class VendorSettings(BaseModel):
    id: str = "default_vendor_settings"
    vendor_id: str = "default_vendor"
    company_name: str = "Bricks Vendor"
    service_areas: List[str] = ["Los Angeles"]
    support_email: str = "support@bricksdispatch.com"
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class VendorSettingsUpdate(BaseModel):
    company_name: Optional[str] = None
    service_areas: Optional[List[str]] = None
    support_email: Optional[str] = None


class VendorAuthLogin(BaseModel):
    email: str
    name: Optional[str] = None


class VendorSession(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    token_hash: str
    vendor_id: str = "default_vendor"
    member_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: str = Field(default_factory=lambda: (datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)).isoformat())
    active: bool = True


class AuditEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    vendor_id: str = "default_vendor"
    actor_id: str
    entity_type: str
    entity_id: str
    action: str
    details: dict = {}
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
