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
    address: str
    city: str
    state: str
    zip_code: str
    time_window_start: str
    time_window_end: str
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
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class JobStatusUpdate(BaseModel):
    status: str


class ChecklistToggle(BaseModel):
    completed: bool


class UploadRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str
    filename: str
    file_size: int = 0
    mime_type: str = ""
    thumbnail: str = ""
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


class VendorMember(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    vendor_id: str = "default_vendor"
    name: str
    email: str
    phone: str = ""
    role: str = "Photographer"
    status: str = "available"
    is_active: bool = True
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


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


class JobAssignmentUpdate(BaseModel):
    member_id: str


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

