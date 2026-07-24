"""Pydantic models for cross-service dispatch job contracts.

These models define the contract between Bricks-Companion and Bricks GO
from the Dispatch side. They mirror the Booking-side dispatch_models.py.

Reference schemas at /Volumes/BRICKS/schemas/v1/:
    - booking-to-dispatch.schema.json (POST /api/v1/dispatch/jobs)
    - dispatch-booking-status.schema.json (PUT /api/v1/dispatch-jobs/{booking_id}/status)
    - dispatch-upload.schema.json (POST /api/v1/dispatch-jobs/{booking_id}/uploads)
    - dispatch-webhook-event.schema.json (callback notification)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Reusable sub-models
# ---------------------------------------------------------------------------

class AgentInfo(BaseModel):
    """Agent who created the booking / drives the listing."""
    user_id: str
    name: str
    email: str
    brokerage: str


class PackageInfo(BaseModel):
    """Pricing package tied to the booking."""
    id: str
    name: str
    price: int  # cents
    currency: str = "CAD"


class PriorityFactors(BaseModel):
    """Factors the dispatch system uses to prioritise job assignment."""
    is_rush: bool = False
    complexity_score: Optional[int] = None
    complexity_band: Optional[str] = None
    min_tier_required: Optional[int] = None
    preferred_tier: Optional[int] = None
    ai_flags: list[str] = []


class VendorAssignment(BaseModel):
    """Pre-assigned vendor for the job."""
    vendor_id: str
    name: str = ""
    tier: int = 1
    payout: int = 0  # cents


class PricingInfo(BaseModel):
    """Split between vendor payout and platform margin."""
    vendor_payout: int = 0
    platform_margin: int = 0


class ChecklistItem(BaseModel):
    """A single checklist item on the dispatch job."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    label: str
    completed: bool = False


class ChecklistProgress(BaseModel):
    """Progress snapshot sent in status updates."""
    total: int
    completed: int


class EnrichedBooking(BaseModel):
    """Booking record enriched with dispatch status."""
    booking_id: str
    dispatch_status: str
    uploads_count: int = 0
    dispatch_job_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Booking -> Dispatch: Job Intake (POST /api/v1/dispatch/jobs)
# ---------------------------------------------------------------------------

class DispatchJobCreate(BaseModel):
    """Payload received from Booking to create a new dispatch job.

    Sent after payment confirmation for a booking. This is the inbound
    contract model — validates the payload Booking sends.

    See: booking-to-dispatch.schema.json
    """
    api_version: str = "v1"
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    source: str = "booking"

    booking_id: str
    address: str
    city: str = ""
    state: str = ""
    zip_code: str = ""
    time_window_start: str = "09:00"
    time_window_end: str = ""
    job_type: str = "photo"
    status: str = "pending_assignment"
    priority: str = "normal"
    property_details: str = ""
    client_instructions: str = ""
    scheduled_date: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_rush: bool = False
    sqft: Optional[int] = None
    add_ons: list[str] = []
    agent: AgentInfo
    package: PackageInfo
    billing_type: str
    add_on_total: int = 0
    overage_amount: Optional[int] = None
    checklist_template_id: str = "standard"

    priority_factors: PriorityFactors = Field(default_factory=PriorityFactors)
    vendor: Optional[VendorAssignment] = None
    pricing: PricingInfo = Field(default_factory=PricingInfo)


class DispatchJobCreateResponse(BaseModel):
    """Response to Booking after successful job intake."""
    job_id: str
    status: str
    created_at: str
    checklist: list[ChecklistItem] = []


# ---------------------------------------------------------------------------
# Dispatch -> Booking: Status Update (PUT /api/v1/dispatch-jobs/{booking_id}/status)
# ---------------------------------------------------------------------------

class DispatchStatusUpdate(BaseModel):
    """Payload sent from Dispatch to Booking when a job status changes.

    See: dispatch-booking-status.schema.json

    Status lifecycle:
        pending_assignment -> assigned -> in_progress -> completed -> delivered
        Any state -> cancelled
        Any state -> failed
    """
    api_version: str = "v1"
    job_id: str
    new_status: str
    previous_status: Optional[str] = None
    changed_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    actor_id: Optional[str] = None
    actor_type: Optional[str] = None
    notes: str = ""
    checklist_progress: Optional[ChecklistProgress] = None


class DispatchStatusUpdateResponse(BaseModel):
    """Booking's acknowledgment of a status update."""
    accepted: bool
    booking_id: str
    updated_dispatch_status: str
    enriched_booking: Optional[EnrichedBooking] = None


# ---------------------------------------------------------------------------
# Dispatch -> Booking: Upload Notification (POST /api/v1/dispatch-jobs/{booking_id}/uploads)
# ---------------------------------------------------------------------------

class DispatchUploadNotification(BaseModel):
    """Payload sent from Dispatch when a vendor completes an upload.

    See: dispatch-upload.schema.json
    """
    api_version: str = "v1"
    upload_id: str
    job_id: str
    filename: str
    file_size: int
    mime_type: str
    uploaded_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class DispatchUploadNotificationResponse(BaseModel):
    """Booking's acknowledgment of an upload notification."""
    accepted: bool
    booking_id: str
    new_uploads_count: int
    total_uploads: int


# ---------------------------------------------------------------------------
# Optional: Dispatch Webhook Event (callback-based delivery)
# ---------------------------------------------------------------------------

class DispatchWebhookEventData(BaseModel):
    """The inner payload of a webhook event."""
    booking_id: str
    job_id: str
    previous_status: Optional[str] = None
    new_status: str
    actor_id: Optional[str] = None
    actor_type: Optional[str] = None


class DispatchWebhookEvent(BaseModel):
    """Webhook event sent by Dispatch to Booking's callback URL.

    See: dispatch-webhook-event.schema.json

    Event types:
        job.created, job.assigned, job.in_progress, job.checklist_updated,
        job.upload_added, job.completed, job.delivered
    """
    event_type: str
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    source: str = "dispatch"
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    data: DispatchWebhookEventData
