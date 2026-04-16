import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, FastAPI
from starlette.middleware.cors import CORSMiddleware

from app.routers.auth import router as auth_router
from app.routers.dispatch import router as dispatch_router
from app.routers.vendor_os import router as vendor_os_router
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
    await db.audit_events.delete_many({})
    return {"seeded": True, "jobs_count": len(jobs), "uploads_count": len(sample_uploads)}

app.include_router(dispatch_router)
app.include_router(auth_router)
app.include_router(vendor_os_router)
app.include_router(seed_router)
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
