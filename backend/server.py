from fastapi import FastAPI, APIRouter, HTTPException, Form
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

@api_router.put("/jobs/{job_id}/status")
async def update_job_status(job_id: str, update: JobStatusUpdate):
    valid_transitions = {"assigned": ["in_progress"], "in_progress": ["completed"], "completed": []}
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    current_status = job["status"]
    if update.status not in valid_transitions.get(current_status, []):
        raise HTTPException(status_code=400, detail=f"Cannot transition from {current_status} to {update.status}")
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
async def toggle_checklist_item(job_id: str, item_id: str, toggle: ChecklistToggle):
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
async def create_upload(job_id: str = Form(...), filename: str = Form(...), file_size: int = Form(0), mime_type: str = Form(""), file_data: str = Form("")):
    upload = UploadRecord(job_id=job_id, filename=filename, file_size=file_size, mime_type=mime_type, thumbnail=file_data[:200] if file_data else "")
    upload_dict = upload.dict()
    await db.uploads.insert_one(upload_dict)
    await db.jobs.update_one({"id": job_id}, {"$inc": {"uploaded_count": 1}})
    upload_dict.pop("_id", None)

    # Notification trigger: upload completed
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0})
    addr = job["address"] if job else "Unknown"
    await create_notification(
        title="Upload Complete",
        body=f"{filename} uploaded to {addr}",
        ntype="upload",
        data={"job_id": job_id, "filename": filename}
    )
    return upload_dict

@api_router.delete("/uploads/{upload_id}")
async def delete_upload(upload_id: str):
    upload = await db.uploads.find_one({"id": upload_id}, {"_id": 0})
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    await db.uploads.delete_one({"id": upload_id})
    await db.jobs.update_one({"id": upload["job_id"]}, {"$inc": {"uploaded_count": -1}})
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

# Seed
@api_router.post("/seed")
async def seed_data():
    await db.jobs.delete_many({})
    await db.uploads.delete_many({})
    await db.profile.delete_many({})
    await db.notifications.delete_many({})

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
