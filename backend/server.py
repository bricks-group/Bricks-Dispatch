import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, FastAPI, HTTPException
from starlette.middleware.cors import CORSMiddleware

_SEED_ENABLED = os.environ.get("SEED_ENABLED", "false").lower() == "true"

from app.routers.auth import router as auth_router
from app.routers.dispatch import router as dispatch_router
from app.routers.vendor_os import router as vendor_os_router
from app.routers.onboarding import router as onboarding_router
from app.routers.admin_ops import router as admin_ops_router
from app.shared.database import client, db
from app.shared.schemas import (
    AvailabilitySettings,
    ChecklistItem,
    Job,
    NotificationRecord,
    Profile,
    UploadRecord,
    VendorMember,
    VendorSettings,
)


app = FastAPI()
seed_router = APIRouter(prefix="/api", tags=["seed"])

@seed_router.post("/seed")
async def seed_data():
    if not _SEED_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")
    await db.jobs.delete_many({})
    await db.uploads.delete_many({})
    await db.profile.delete_many({})
    await db.notifications.delete_many({})
    await db.vendor_members.delete_many({})
    await db.vendor_availability.delete_many({})
    await db.vendor_settings.delete_many({})
    await db.vendor_sessions.delete_many({})

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    day_after = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")

    jobs = [
        Job(address="22 Rosehill Ave", city="Toronto", state="ON", zip_code="M4T 1G5", time_window_start="09:00", time_window_end="10:30", job_type="photo", status="assigned", priority="high", property_details="4BR/3BA detached home. 2,800 sqft. Renovated kitchen and master suite. Large backyard.", client_instructions="Focus on kitchen upgrades and open floor plan. Capture backyard entertaining area.", scheduled_date=today, deliverables_count=35, uploaded_count=0, checklist=[ChecklistItem(label="Exterior front elevation"), ChecklistItem(label="Exterior rear with yard"), ChecklistItem(label="Living room wide angle"), ChecklistItem(label="Kitchen overview"), ChecklistItem(label="Master bedroom"), ChecklistItem(label="Master bathroom"), ChecklistItem(label="Backyard"), ChecklistItem(label="Drone aerial shot")]),
        Job(address="88 Harbour Square", city="Toronto", state="ON", zip_code="M5J 2S2", time_window_start="11:00", time_window_end="12:30", job_type="video", status="assigned", priority="urgent", property_details="2BR/2BA lakefront condo. 1,400 sqft. Floor-to-ceiling windows. Lake Ontario views.", client_instructions="Cinematic walkthrough. Emphasise lake views and building amenities. Include drone flyover.", scheduled_date=today, deliverables_count=3, uploaded_count=0, checklist=[ChecklistItem(label="Walkthrough video (4K)"), ChecklistItem(label="Drone flyover footage"), ChecklistItem(label="Twilight exterior shots")]),
        Job(address="312 King St W, Unit 1802", city="Toronto", state="ON", zip_code="M5V 1J2", time_window_start="14:00", time_window_end="15:30", job_type="3d_tour", status="in_progress", priority="normal", property_details="1BR+den condo. 750 sqft. Entertainment district. Modern finishes.", client_instructions="Full Matterport scan including storage and den. Tag kitchen island and city view windows.", scheduled_date=today, deliverables_count=12, uploaded_count=4, checklist=[ChecklistItem(label="Living area scan", completed=True), ChecklistItem(label="Kitchen scan", completed=True), ChecklistItem(label="Bedroom scan", completed=True), ChecklistItem(label="Den scan", completed=True), ChecklistItem(label="Bathroom scan"), ChecklistItem(label="Balcony scan"), ChecklistItem(label="Storage scan"), ChecklistItem(label="Hallway scan")]),
        Job(address="45 Dunbar Rd", city="Hamilton", state="ON", zip_code="L8P 3B4", time_window_start="16:00", time_window_end="17:00", job_type="floor_plan", status="completed", priority="normal", property_details="3BR/2BA century home. 1,750 sqft. Detached garage. Corner lot.", client_instructions="Standard floor plan with room dimensions. Include garage and yard footprint.", scheduled_date=today, deliverables_count=2, uploaded_count=2, checklist=[ChecklistItem(label="Main floor measurement", completed=True), ChecklistItem(label="Floor plan rendering", completed=True)]),
        Job(address="9 Harbour St E", city="Mississauga", state="ON", zip_code="L5G 1A7", time_window_start="08:00", time_window_end="10:00", job_type="drone", status="assigned", priority="high", property_details="4BR/3BA executive home. 3,200 sqft. Pool. Backing onto conservation land.", client_instructions="Aerial photography and video. Capture conservation backdrop and pool area.", scheduled_date=tomorrow, deliverables_count=15, uploaded_count=0, checklist=[ChecklistItem(label="Aerial front approach"), ChecklistItem(label="Overhead property shot"), ChecklistItem(label="Conservation land backdrop"), ChecklistItem(label="Pool and backyard aerial"), ChecklistItem(label="Neighbourhood context shot")]),
        Job(address="150 King St W", city="Toronto", state="ON", zip_code="M5H 1J9", time_window_start="13:00", time_window_end="14:30", job_type="photo", status="assigned", priority="normal", property_details="Commercial office space. 3,100 sqft. Financial district. Corner unit with city views.", client_instructions="Standard commercial photography. Emphasise natural light and boardroom.", scheduled_date=tomorrow, deliverables_count=18, uploaded_count=0, checklist=[ChecklistItem(label="Exterior building shot"), ChecklistItem(label="Reception area"), ChecklistItem(label="Open office"), ChecklistItem(label="Boardroom")]),
        Job(address="1 Yonge St, Unit 4201", city="Toronto", state="ON", zip_code="M5E 1W7", time_window_start="10:00", time_window_end="12:00", job_type="video", status="assigned", priority="normal", property_details="2BR/2BA luxury condo. 1,100 sqft. 42nd floor. Panoramic lake and city views.", client_instructions="Lifestyle walkthrough video. Capture sunrise lake view. Music to be added in post.", scheduled_date=day_after, deliverables_count=4, uploaded_count=0, checklist=[ChecklistItem(label="Interior walkthrough"), ChecklistItem(label="Lake view footage"), ChecklistItem(label="Sunrise timelapse"), ChecklistItem(label="Amenities B-roll")]),
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
        NotificationRecord(title="New Job Assigned", body="22 Rosehill Ave — Photo shoot at 09:00", type="new_job", data={"job_id": jobs[0].id}),
        NotificationRecord(title="New Job Assigned", body="88 Harbour Square — URGENT video shoot at 11:00", type="new_job", data={"job_id": jobs[1].id}),
        NotificationRecord(title="Job Started", body="312 King St W — 3D tour in progress", type="status_change", data={"job_id": jobs[2].id}),
        NotificationRecord(title="Approaching Time Window", body="22 Rosehill Ave — starts in 30 min", type="reminder", data={"job_id": jobs[0].id}),
    ]
    for n in sample_notifs:
        await db.notifications.insert_one(n.dict())

    profile = Profile()
    await db.profile.insert_one(profile.dict())
    await db.vendor_members.insert_one(VendorMember(
        id="default_user", 
        name=profile.name, 
        email=profile.email, 
        phone=profile.phone, 
        role="Owner Operator",
        is_active=True,
        onboarding_status="approved"
    ).dict())
    await db.vendor_availability.insert_one(AvailabilitySettings(member_id="default_user").dict())
    await db.vendor_settings.insert_one(VendorSettings().dict())
    await db.audit_events.delete_many({})
    return {"seeded": True, "jobs_count": len(jobs), "uploads_count": len(sample_uploads)}

app.include_router(dispatch_router)
app.include_router(auth_router)
app.include_router(vendor_os_router)
app.include_router(onboarding_router)
app.include_router(admin_ops_router)
app.include_router(seed_router)
_ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_ALLOWED_ORIGINS, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@app.on_event("startup")
async def startup_event():
    logger.info("Dispatch service started")

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

@app.get("/health")
async def health():
    try:
        await db.command("ping")
        return {"status": "ok", "db": "connected"}
    except Exception:
        return {"status": "degraded", "db": "unreachable"}
