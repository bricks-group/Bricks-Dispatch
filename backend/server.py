from fastapi import FastAPI, APIRouter, HTTPException, Form, Header
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
from datetime import datetime, timezone, timedelta

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI()
api_router = APIRouter(prefix="/api")

# --- Notification Sending Utility ---
async def send_push_notification(title: str, body: str, data: dict = None):
    """Send push notification to all registered tokens"""
    tokens = await db.push_tokens.find({"active": True}, {"_id": 0}).to_list(100)
    if not tokens:
        return

    try:
        from exponent_server_sdk import PushClient, PushMessage, DeviceNotRegisteredError
        import requests

        session = requests.Session()
        session.headers.update({
            "accept": "application/json",
            "accept-encoding": "gzip, deflate",
            "content-type": "application/json",
        })
        push_client = PushClient(session=session)

        for token_doc in tokens:
            try:
                msg = PushMessage(
                    to=token_doc["token"],
                    title=title,
                    body=body,
                    data=data or {},
                    sound="default",
                    priority="high",
                )
                response = push_client.publish(msg)
                response.validate_response()
                logger.info(f"Push sent to {token_doc['token'][:30]}...")
            except DeviceNotRegisteredError:
                await db.push_tokens.update_one(
                    {"token": token_doc["token"]},
                    {"$set": {"active": False}}
                )
                logger.warning(f"Token deactivated: {token_doc['token'][:30]}...")
            except Exception as e:
                logger.error(f"Push error: {e}")
    except ImportError:
        logger.warning("exponent_server_sdk not available, skipping push")
    except Exception as e:
        logger.error(f"Push notification error: {e}")

# --- Models ---
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

# --- Routes ---

@api_router.get("/")
async def root():
    return {"message": "Bricks Dispatch API"}

# Push Token Registration
@api_router.post("/push-tokens")
async def register_push_token(reg: PushTokenRegister):
    existing = await db.push_tokens.find_one({"token": reg.token})
    if existing:
        await db.push_tokens.update_one(
            {"token": reg.token},
            {"$set": {"active": True, "platform": reg.platform, "updated_at": datetime.now(timezone.utc).isoformat()}}
        )
    else:
        await db.push_tokens.insert_one({
            "token": reg.token,
            "platform": reg.platform,
            "active": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    return {"registered": True}

# Notifications
@api_router.get("/notifications")
async def get_notifications():
    notifs = await db.notifications.find({}, {"_id": 0}).sort("created_at", -1).to_list(50)
    return notifs

@api_router.put("/notifications/{notif_id}/read")
async def mark_notification_read(notif_id: str):
    await db.notifications.update_one({"id": notif_id}, {"$set": {"read": True}})
    return {"read": True}

@api_router.get("/notifications/unread-count")
async def unread_count():
    count = await db.notifications.count_documents({"read": False})
    return {"count": count}

async def create_notification(title: str, body: str, ntype: str = "info", data: dict = None):
    """Create a notification record and send push"""
    notif = NotificationRecord(title=title, body=body, type=ntype, data=data or {})
    notif_dict = notif.dict()
    await db.notifications.insert_one(notif_dict)
    notif_dict.pop("_id", None)
    # Fire push notification
    await send_push_notification(title, body, data)
    return notif_dict

# Jobs
@api_router.get("/jobs")
async def get_jobs(status: Optional[str] = None):
    query = {}
    if status:
        query["status"] = status
    jobs = await db.jobs.find(query, {"_id": 0}).sort("time_window_start", 1).to_list(100)
    return jobs

@api_router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

def resolve_actor_id(x_user_id: Optional[str]) -> str:
    actor_id = (x_user_id or "").strip()
    if not actor_id:
        raise HTTPException(status_code=401, detail="Missing X-User-Id header")
    return actor_id

def ensure_assigned_to_actor(job: dict, actor_id: str):
    assignee_id = job.get("assignee_id", "default_user")
    if assignee_id != actor_id:
        raise HTTPException(status_code=403, detail="You are not assigned to this job")

async def ensure_default_vendor_member():
    existing = await db.vendor_members.find_one({"id": "default_user"}, {"_id": 0})
    if existing:
        return
    profile = await db.profile.find_one({"id": "default_user"}, {"_id": 0})
    name = profile["name"] if profile else "Default Vendor"
    email = profile["email"] if profile else "vendor@bricksdispatch.com"
    member = VendorMember(id="default_user", name=name, email=email, role="Owner Operator")
    await db.vendor_members.insert_one(member.dict())

async def get_vendor_member_ids() -> List[str]:
    await ensure_default_vendor_member()
    members = await db.vendor_members.find({"vendor_id": "default_vendor", "is_active": True}, {"_id": 0, "id": 1}).to_list(200)
    ids = [m["id"] for m in members]
    return ids if ids else ["default_user"]

@api_router.put("/jobs/{job_id}/status")
async def update_job_status(job_id: str, update: JobStatusUpdate, x_user_id: Optional[str] = Header(default=None, alias="X-User-Id")):
    valid_transitions = {"assigned": ["in_progress"], "in_progress": ["completed"], "completed": []}
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    actor_id = resolve_actor_id(x_user_id)
    ensure_assigned_to_actor(job, actor_id)
    current_status = job["status"]
    if update.status not in valid_transitions.get(current_status, []):
        raise HTTPException(status_code=400, detail=f"Cannot transition from {current_status} to {update.status}")
    if current_status == "in_progress" and update.status == "completed":
        checklist = job.get("checklist", [])
        incomplete_count = len([item for item in checklist if not item.get("completed", False)])
        required_uploads = max(job.get("deliverables_count", 0), 0)
        uploaded_count = max(job.get("uploaded_count", 0), 0)
        missing_uploads = max(required_uploads - uploaded_count, 0)
        if incomplete_count > 0 or missing_uploads > 0:
            blockers = []
            if incomplete_count > 0:
                blockers.append(f"{incomplete_count} checklist item(s) incomplete")
            if missing_uploads > 0:
                blockers.append(f"{missing_uploads} deliverable upload(s) remaining")
            raise HTTPException(status_code=400, detail=f"Cannot complete job: {', '.join(blockers)}")
    now = datetime.now(timezone.utc).isoformat()
    await db.jobs.update_one({"id": job_id}, {"$set": {"status": update.status, "updated_at": now}})
    updated = await db.jobs.find_one({"id": job_id}, {"_id": 0})

    # Notification trigger: status change
    status_labels = {"in_progress": "Started", "completed": "Completed"}
    await create_notification(
        title=f"Job {status_labels.get(update.status, update.status)}",
        body=f"{job['address']} — status changed to {update.status.replace('_', ' ')}",
        ntype="status_change",
        data={"job_id": job_id, "status": update.status}
    )
    return updated

@api_router.put("/jobs/{job_id}/checklist/{item_id}")
async def toggle_checklist_item(job_id: str, item_id: str, toggle: ChecklistToggle, x_user_id: Optional[str] = Header(default=None, alias="X-User-Id")):
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    actor_id = resolve_actor_id(x_user_id)
    ensure_assigned_to_actor(job, actor_id)
    if job.get("status") != "in_progress":
        raise HTTPException(status_code=400, detail="Checklist can only be updated while job is in progress")
    result = await db.jobs.update_one(
        {"id": job_id, "checklist.id": item_id},
        {"$set": {"checklist.$.completed": toggle.completed, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    updated = await db.jobs.find_one({"id": job_id}, {"_id": 0})
    return updated

# Schedule
@api_router.get("/schedule")
async def get_schedule(date: Optional[str] = None):
    query = {}
    if date:
        query["scheduled_date"] = date
    return await db.jobs.find(query, {"_id": 0}).sort("time_window_start", 1).to_list(100)

# Uploads
@api_router.get("/uploads")
async def get_uploads(job_id: Optional[str] = None):
    query = {}
    if job_id:
        query["job_id"] = job_id
    return await db.uploads.find(query, {"_id": 0}).sort("created_at", -1).to_list(200)

@api_router.post("/uploads")
async def create_upload(job_id: str = Form(...), filename: str = Form(...), file_size: int = Form(0), mime_type: str = Form(""), file_data: str = Form(""), x_user_id: Optional[str] = Header(default=None, alias="X-User-Id")):
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    actor_id = resolve_actor_id(x_user_id)
    ensure_assigned_to_actor(job, actor_id)
    if job.get("status") != "in_progress":
        raise HTTPException(status_code=400, detail="Uploads are only allowed while job is in progress")
    upload = UploadRecord(job_id=job_id, filename=filename, file_size=file_size, mime_type=mime_type, thumbnail=file_data[:200] if file_data else "")
    upload_dict = upload.dict()
    await db.uploads.insert_one(upload_dict)
    await db.jobs.update_one({"id": job_id}, {"$inc": {"uploaded_count": 1}})
    upload_dict.pop("_id", None)

    # Notification trigger: upload completed
    addr = job["address"] if job else "Unknown"
    await create_notification(
        title="Upload Complete",
        body=f"{filename} uploaded to {addr}",
        ntype="upload",
        data={"job_id": job_id, "filename": filename}
    )
    return upload_dict

@api_router.delete("/uploads/{upload_id}")
async def delete_upload(upload_id: str, x_user_id: Optional[str] = Header(default=None, alias="X-User-Id")):
    upload = await db.uploads.find_one({"id": upload_id}, {"_id": 0})
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    job = await db.jobs.find_one({"id": upload["job_id"]}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    actor_id = resolve_actor_id(x_user_id)
    ensure_assigned_to_actor(job, actor_id)
    if job.get("status") == "completed":
        raise HTTPException(status_code=400, detail="Cannot delete uploads from completed jobs")
    if job.get("status") != "in_progress":
        raise HTTPException(status_code=400, detail="Uploads can only be deleted while job is in progress")
    await db.uploads.delete_one({"id": upload_id})
    next_count = max(job.get("uploaded_count", 0) - 1, 0)
    await db.jobs.update_one({"id": upload["job_id"]}, {"$set": {"uploaded_count": next_count, "updated_at": datetime.now(timezone.utc).isoformat()}})
    return {"deleted": True}

# Profile
@api_router.get("/profile")
async def get_profile():
    profile = await db.profile.find_one({"id": "default_user"}, {"_id": 0})
    if not profile:
        default = Profile()
        d = default.dict()
        await db.profile.insert_one(d)
        d.pop("_id", None)
        return d
    return profile

@api_router.put("/profile")
async def update_profile(update: ProfileUpdate):
    update_data = {k: v for k, v in update.dict().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No update data")
    await db.profile.update_one({"id": "default_user"}, {"$set": update_data}, upsert=True)
    return await db.profile.find_one({"id": "default_user"}, {"_id": 0})

# Vendor OS - Overview
@api_router.get("/vendor-os/overview")
async def get_vendor_os_overview(x_user_id: Optional[str] = Header(default=None, alias="X-User-Id")):
    resolve_actor_id(x_user_id)
    member_ids = await get_vendor_member_ids()
    jobs = await db.jobs.find({"assignee_id": {"$in": member_ids}}, {"_id": 0}).to_list(500)
    today = datetime.now(timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    assigned_today = 0
    assigned_week = 0
    completed_week = 0
    upcoming = []
    total_capacity = max(len(member_ids) * 4, 1)
    scheduled_this_week = 0

    for job in jobs:
        try:
            scheduled = datetime.strptime(job.get("scheduled_date", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if scheduled == today:
            assigned_today += 1
        if week_start <= scheduled <= week_end:
            assigned_week += 1
            scheduled_this_week += 1
            if job.get("status") == "completed":
                completed_week += 1
        if scheduled >= today and job.get("status") != "completed":
            upcoming.append(job)

    capacity_utilization = round(min((scheduled_this_week / (total_capacity * 7)) * 100, 100), 1)
    upcoming_sorted = sorted(upcoming, key=lambda j: (j.get("scheduled_date", ""), j.get("time_window_start", "")))[:5]
    return {
        "jobs_assigned_today": assigned_today,
        "jobs_assigned_week": assigned_week,
        "jobs_completed_week": completed_week,
        "capacity_utilization_pct": capacity_utilization,
        "upcoming_jobs": upcoming_sorted,
    }

# Vendor OS - Team
@api_router.get("/vendor-os/team")
async def get_vendor_team(x_user_id: Optional[str] = Header(default=None, alias="X-User-Id")):
    resolve_actor_id(x_user_id)
    await ensure_default_vendor_member()
    members = await db.vendor_members.find({"vendor_id": "default_vendor"}, {"_id": 0}).sort("created_at", 1).to_list(200)
    for member in members:
        member["assigned_jobs"] = await db.jobs.count_documents({"assignee_id": member["id"], "status": {"$ne": "completed"}})
    return members

@api_router.post("/vendor-os/team")
async def create_vendor_member(payload: VendorMemberCreate, x_user_id: Optional[str] = Header(default=None, alias="X-User-Id")):
    resolve_actor_id(x_user_id)
    member = VendorMember(
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        role=payload.role,
    )
    member_dict = member.dict()
    await db.vendor_members.insert_one(member_dict)
    member_dict["assigned_jobs"] = 0
    return member_dict

@api_router.put("/vendor-os/team/{member_id}")
async def update_vendor_member(member_id: str, payload: VendorMemberUpdate, x_user_id: Optional[str] = Header(default=None, alias="X-User-Id")):
    resolve_actor_id(x_user_id)
    update_data = {k: v for k, v in payload.dict().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No update data")
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.vendor_members.update_one({"id": member_id, "vendor_id": "default_vendor"}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Member not found")
    member = await db.vendor_members.find_one({"id": member_id}, {"_id": 0})
    member["assigned_jobs"] = await db.jobs.count_documents({"assignee_id": member_id, "status": {"$ne": "completed"}})
    return member

@api_router.delete("/vendor-os/team/{member_id}")
async def delete_vendor_member(member_id: str, x_user_id: Optional[str] = Header(default=None, alias="X-User-Id")):
    resolve_actor_id(x_user_id)
    if member_id == "default_user":
        raise HTTPException(status_code=400, detail="Cannot remove default member")
    result = await db.vendor_members.delete_one({"id": member_id, "vendor_id": "default_vendor"})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Member not found")
    await db.jobs.update_many({"assignee_id": member_id}, {"$set": {"assignee_id": "default_user", "updated_at": datetime.now(timezone.utc).isoformat()}})
    return {"deleted": True}

# Vendor OS - Jobs assignment
@api_router.put("/vendor-os/jobs/{job_id}/assign")
async def assign_job_to_member(job_id: str, payload: JobAssignmentUpdate, x_user_id: Optional[str] = Header(default=None, alias="X-User-Id")):
    resolve_actor_id(x_user_id)
    member = await db.vendor_members.find_one({"id": payload.member_id, "vendor_id": "default_vendor", "is_active": True}, {"_id": 0})
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    result = await db.jobs.update_one({"id": job_id}, {"$set": {"assignee_id": payload.member_id, "updated_at": datetime.now(timezone.utc).isoformat()}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Job not found")
    return await db.jobs.find_one({"id": job_id}, {"_id": 0})

# Vendor OS - Availability
@api_router.get("/vendor-os/availability")
async def get_vendor_availability(x_user_id: Optional[str] = Header(default=None, alias="X-User-Id")):
    actor_id = resolve_actor_id(x_user_id)
    record = await db.vendor_availability.find_one({"vendor_id": "default_vendor", "member_id": actor_id}, {"_id": 0})
    if not record:
        default = AvailabilitySettings(member_id=actor_id).dict()
        await db.vendor_availability.insert_one(default)
        return default
    return record

@api_router.put("/vendor-os/availability")
async def update_vendor_availability(payload: AvailabilityUpdate, x_user_id: Optional[str] = Header(default=None, alias="X-User-Id")):
    actor_id = resolve_actor_id(x_user_id)
    update_data = payload.dict()
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.vendor_availability.update_one(
        {"vendor_id": "default_vendor", "member_id": actor_id},
        {"$set": update_data, "$setOnInsert": {"id": str(uuid.uuid4()), "vendor_id": "default_vendor", "member_id": actor_id}},
        upsert=True,
    )
    return await db.vendor_availability.find_one({"vendor_id": "default_vendor", "member_id": actor_id}, {"_id": 0})

# Vendor OS - Performance
@api_router.get("/vendor-os/performance")
async def get_vendor_performance(x_user_id: Optional[str] = Header(default=None, alias="X-User-Id")):
    resolve_actor_id(x_user_id)
    member_ids = await get_vendor_member_ids()
    jobs = await db.jobs.find({"assignee_id": {"$in": member_ids}}, {"_id": 0}).to_list(500)
    today = datetime.now(timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    week_jobs = []
    completed = []
    for job in jobs:
        try:
            scheduled = datetime.strptime(job.get("scheduled_date", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if week_start <= scheduled <= week_end:
            week_jobs.append(job)
            if job.get("status") == "completed":
                completed.append(job)

    completion_rate = round((len(completed) / len(week_jobs) * 100), 1) if week_jobs else 0.0
    on_time = 0
    for job in completed:
        try:
            deadline = datetime.strptime(f"{job.get('scheduled_date')} {job.get('time_window_end', '23:59')}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            updated_at = datetime.fromisoformat(job.get("updated_at"))
            if updated_at <= deadline:
                on_time += 1
        except Exception:
            continue
    on_time_rate = round((on_time / len(completed) * 100), 1) if completed else 0.0
    return {
        "completion_rate": completion_rate,
        "on_time_rate": on_time_rate,
        "job_volume_week": len(week_jobs),
    }

# Vendor OS - Settings
@api_router.get("/vendor-os/settings")
async def get_vendor_settings(x_user_id: Optional[str] = Header(default=None, alias="X-User-Id")):
    resolve_actor_id(x_user_id)
    settings = await db.vendor_settings.find_one({"id": "default_vendor_settings"}, {"_id": 0})
    if not settings:
        default = VendorSettings().dict()
        await db.vendor_settings.insert_one(default)
        return default
    return settings

@api_router.put("/vendor-os/settings")
async def update_vendor_settings(payload: VendorSettingsUpdate, x_user_id: Optional[str] = Header(default=None, alias="X-User-Id")):
    resolve_actor_id(x_user_id)
    update_data = {k: v for k, v in payload.dict().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No update data")
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.vendor_settings.update_one({"id": "default_vendor_settings"}, {"$set": update_data}, upsert=True)
    return await db.vendor_settings.find_one({"id": "default_vendor_settings"}, {"_id": 0})

# Seed
@api_router.post("/seed")
async def seed_data():
    await db.jobs.delete_many({})
    await db.uploads.delete_many({})
    await db.profile.delete_many({})
    await db.notifications.delete_many({})
    await db.vendor_members.delete_many({})
    await db.vendor_availability.delete_many({})
    await db.vendor_settings.delete_many({})

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    day_after = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")

    jobs = [
        Job(address="1245 Ocean Blvd", city="Santa Monica", state="CA", zip_code="90401", time_window_start="09:00", time_window_end="10:30", job_type="photo", status="assigned", priority="high", property_details="4BR/3BA luxury beachfront property. 3,200 sqft. Recently renovated kitchen and master suite. Ocean-facing balcony.", client_instructions="Capture sunset views from balcony. Focus on kitchen upgrades and open floor plan. Include aerial drone shots of beachfront.", scheduled_date=today, deliverables_count=25, uploaded_count=0, checklist=[ChecklistItem(label="Exterior front elevation"), ChecklistItem(label="Exterior rear with pool"), ChecklistItem(label="Living room wide angle"), ChecklistItem(label="Kitchen overview"), ChecklistItem(label="Master bedroom"), ChecklistItem(label="Master bathroom"), ChecklistItem(label="Balcony ocean view"), ChecklistItem(label="Drone aerial shot")]),
        Job(address="892 Maple Drive", city="Beverly Hills", state="CA", zip_code="90210", time_window_start="11:00", time_window_end="12:30", job_type="video", status="assigned", priority="urgent", property_details="6BR/5BA estate. 8,500 sqft. Pool, tennis court, 3-car garage. Mediterranean style.", client_instructions="Full walkthrough video. Cinematic style. Include drone flyover. Client wants twilight shots if possible.", scheduled_date=today, deliverables_count=3, uploaded_count=0, checklist=[ChecklistItem(label="Walkthrough video (4K)"), ChecklistItem(label="Drone flyover footage"), ChecklistItem(label="Twilight exterior shots")]),
        Job(address="3401 Wilshire Blvd #PH", city="Los Angeles", state="CA", zip_code="90010", time_window_start="14:00", time_window_end="15:30", job_type="3d_tour", status="in_progress", priority="normal", property_details="2BR/2BA penthouse condo. 1,800 sqft. Downtown skyline views. Modern minimalist design.", client_instructions="Matterport 3D scan. Cover all rooms including closets and storage. Tag key features.", scheduled_date=today, deliverables_count=12, uploaded_count=4, checklist=[ChecklistItem(label="Living area scan", completed=True), ChecklistItem(label="Kitchen scan", completed=True), ChecklistItem(label="Bedroom 1 scan", completed=True), ChecklistItem(label="Bedroom 2 scan", completed=True), ChecklistItem(label="Bathroom 1 scan"), ChecklistItem(label="Bathroom 2 scan"), ChecklistItem(label="Balcony scan"), ChecklistItem(label="Storage/utility scan")]),
        Job(address="567 Elm Street", city="Pasadena", state="CA", zip_code="91101", time_window_start="16:00", time_window_end="17:00", job_type="floor_plan", status="completed", priority="normal", property_details="3BR/2BA craftsman bungalow. 1,600 sqft. Detached garage. Large backyard.", client_instructions="Standard floor plan with room dimensions. Include garage and outdoor spaces.", scheduled_date=today, deliverables_count=2, uploaded_count=2, checklist=[ChecklistItem(label="Main floor measurement", completed=True), ChecklistItem(label="Floor plan rendering", completed=True)]),
        Job(address="2100 Pacific Coast Hwy", city="Malibu", state="CA", zip_code="90265", time_window_start="08:00", time_window_end="10:00", job_type="drone", status="assigned", priority="high", property_details="5BR/4BA oceanfront estate. 4,800 sqft. Infinity pool. Private beach access.", client_instructions="Aerial photography and video. Include coastline shots. FAA clearance obtained.", scheduled_date=tomorrow, deliverables_count=15, uploaded_count=0, checklist=[ChecklistItem(label="Aerial front approach"), ChecklistItem(label="Overhead property shot"), ChecklistItem(label="Coastline panorama"), ChecklistItem(label="Pool and backyard aerial"), ChecklistItem(label="Neighborhood context shot")]),
        Job(address="445 N Rodeo Drive", city="Beverly Hills", state="CA", zip_code="90210", time_window_start="13:00", time_window_end="14:30", job_type="photo", status="assigned", priority="normal", property_details="Commercial retail space. 2,400 sqft. Corner lot with high visibility.", client_instructions="Standard commercial photography package. Emphasis on storefront and foot traffic visibility.", scheduled_date=tomorrow, deliverables_count=18, uploaded_count=0, checklist=[ChecklistItem(label="Storefront exterior"), ChecklistItem(label="Interior wide shots"), ChecklistItem(label="Detail shots"), ChecklistItem(label="Loading/parking area")]),
        Job(address="7788 Sunset Strip", city="West Hollywood", state="CA", zip_code="90046", time_window_start="10:00", time_window_end="12:00", job_type="video", status="assigned", priority="normal", property_details="3BR/2BA modern condo. 2,100 sqft. Rooftop access. City views.", client_instructions="Lifestyle video with rooftop sunset shots. Background music will be added in post.", scheduled_date=day_after, deliverables_count=4, uploaded_count=0, checklist=[ChecklistItem(label="Interior walkthrough"), ChecklistItem(label="Rooftop footage"), ChecklistItem(label="Sunset timelapse"), ChecklistItem(label="Neighborhood B-roll")]),
    ]
    for job in jobs:
        await db.jobs.insert_one(job.dict())

    sample_uploads = [
        UploadRecord(job_id=jobs[2].id, filename="living_room_scan_001.e57", file_size=45000000, mime_type="application/octet-stream"),
        UploadRecord(job_id=jobs[2].id, filename="kitchen_scan_002.e57", file_size=38000000, mime_type="application/octet-stream"),
        UploadRecord(job_id=jobs[2].id, filename="bedroom1_scan_003.e57", file_size=42000000, mime_type="application/octet-stream"),
        UploadRecord(job_id=jobs[2].id, filename="bedroom2_scan_004.e57", file_size=39000000, mime_type="application/octet-stream"),
        UploadRecord(job_id=jobs[3].id, filename="floor_plan_main.pdf", file_size=2500000, mime_type="application/pdf"),
        UploadRecord(job_id=jobs[3].id, filename="floor_plan_rendered.png", file_size=8500000, mime_type="image/png"),
    ]
    for upload in sample_uploads:
        await db.uploads.insert_one(upload.dict())

    # Seed sample notifications
    sample_notifs = [
        NotificationRecord(title="New Job Assigned", body="1245 Ocean Blvd — Photo shoot at 09:00", type="new_job", data={"job_id": jobs[0].id}),
        NotificationRecord(title="New Job Assigned", body="892 Maple Drive — URGENT video shoot at 11:00", type="new_job", data={"job_id": jobs[1].id}),
        NotificationRecord(title="Job Started", body="3401 Wilshire Blvd #PH — 3D tour in progress", type="status_change", data={"job_id": jobs[2].id}),
        NotificationRecord(title="Approaching Time Window", body="1245 Ocean Blvd — starts in 30 min", type="reminder", data={"job_id": jobs[0].id}),
    ]
    for n in sample_notifs:
        await db.notifications.insert_one(n.dict())

    profile = Profile()
    await db.profile.insert_one(profile.dict())
    await db.vendor_members.insert_one(VendorMember(id="default_user", name=profile.name, email=profile.email, phone=profile.phone, role="Owner Operator").dict())
    await db.vendor_availability.insert_one(AvailabilitySettings(member_id="default_user").dict())
    await db.vendor_settings.insert_one(VendorSettings().dict())
    return {"seeded": True, "jobs_count": len(jobs), "uploads_count": len(sample_uploads)}

app.include_router(api_router)
app.add_middleware(CORSMiddleware, allow_credentials=True, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@app.on_event("startup")
async def startup_event():
    count = await db.jobs.count_documents({})
    if count == 0:
        logger.info("Seeding demo data...")
        await seed_data()
        logger.info("Demo data seeded")

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
