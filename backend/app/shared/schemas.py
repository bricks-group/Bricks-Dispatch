from datetime import datetime, timezone, timedelta
from typing import List, Optional
import uuid

from pydantic import BaseModel, Field


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
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class JobIntakeRequest(BaseModel):
    """Payload accepted by POST /api/v1/dispatch/jobs from Booking."""
    booking_id: str
    address: str
    city: str = ""
    state: str = ""
    zip_code: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    time_window_start: str
    time_window_end: str = ""
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


class JobStatusUpdate(BaseModel):
    status: str


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
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Profile(BaseModel):
    id: str = "default_user"
    name: str = "Alex Rivera"
    email: str = "alex.rivera@bricksdispatch.com"
    role: str = "Lead Photographer"
    phone: str = "+1 (555) 867-5309"
    availability: bool = True
    avatar_url: str = ""
    total_jobs_completed: int = 147
    rating: float = 4.9
    member_since: str = "2024-03-15"


class ProfileUpdate(BaseModel):
    availability: Optional[bool] = None
    name: Optional[str] = None
    phone: Optional[str] = None


class PushTokenRegister(BaseModel):
    token: str
    platform: str = "expo"


class NotificationRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    body: str
    type: str = "info"
    data: dict = {}
    read: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Coordinate(BaseModel):
    lat: float
    lng: float

class VendorMember(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    vendor_id: str = "default_vendor"
    name: str
    email: str
    phone: str = ""
    role: str = "Photographer"
    specialty: str = "photo"
    status: str = "available"
    is_active: bool = False
    onboarding_status: str = "pending"
    portfolio_links: List[str] = []
    equipment_list: List[str] = []
    service_areas: List[str] = [] # Legacy city-based
    operation_polygon: List[Coordinate] = [] # The "Connected Dots"
    home_city: str = ""
    max_travel_km: int = 50
    tier: int = 1
    hst_number: Optional[str] = None
    wsib_number: Optional[str] = None
    insurance_policy_number: Optional[str] = None
    internal_notes: str = ""
    completion_rate: float = 1.0
    avg_turnaround_days: float = 0.0
    quality_rating: float = 4.0
    on_time_rate: float = 1.0
    cancel_rate: float = 0.0
    acceptance_latency_avg_sec: Optional[int] = None
    total_assignments: int = 0
    active_assignments: int = 0
    home_lat: Optional[float] = None
    home_lng: Optional[float] = None
    ic_agreement_version: Optional[str] = None
    terms_accepted_at: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class VendorApplicationCreate(BaseModel):
    name: str
    email: str
    phone: str
    specialties: List[str]
    portfolio_links: List[str] = []
    equipment_list: List[str] = []
    city: str
    max_travel_km: int = 50
    operation_polygon: List[Coordinate] = []
    home_lat: Optional[float] = None
    home_lng: Optional[float] = None
    # Ontario Regions: GTA, York, Peel, Halton, Durham, Hamilton, Waterloo, Ottawa, etc.
    preferred_regions: List[str] = []
    accepted_terms: bool
    ic_agreement_version: str = "1.0"
    hst_number: Optional[str] = None


class VendorDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    vendor_id: str
    member_id: str
    doc_type: str  # coi, wsib, hst_registry, id_verification
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


class VendorMemberUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None
    is_active: Optional[bool] = None
    onboarding_status: Optional[str] = None
    hst_number: Optional[str] = None
    wsib_number: Optional[str] = None
    internal_notes: Optional[str] = None
    quality_rating: Optional[float] = None
    on_time_rate: Optional[float] = None
    cancel_rate: Optional[float] = None


class JobAssignmentUpdate(BaseModel):
    member_id: str


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
