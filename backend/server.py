import logging
import os
import time
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
from uuid import uuid4

# Load .env before any module-level code reads env vars
load_dotenv(Path(__file__).parent / ".env")

import sentry_sdk
from fastapi import APIRouter, FastAPI, HTTPException, Request
from sentry_sdk.integrations.fastapi import FastApiIntegration
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response
from app.shared.rate_limit import RateLimitMiddleware
from pythonjsonlogger import jsonlogger

_SEED_ENABLED = os.environ.get("SEED_ENABLED", "false").lower() == "true"

# Internal service-to-service auth (X-Internal-Api-Key header)
_INTERNAL_API_KEYS = set(
    k for k in os.environ.get("INTERNAL_API_KEYS", "").split(",") if k
)
_BROKERAGE_API_URL = os.environ.get("BROKERAGE_API_URL", "http://localhost:8003")
_BOOKING_API_URL = os.environ.get("BOOKING_API_URL", "http://localhost:8001")
_DISPATCH_API_URL = os.environ.get("DISPATCH_API_URL", "http://localhost:8002")

# ── Sentry ───────────────────────────────────────────────────────────────────────
SENTRY_DSN = os.environ.get("SENTRY_DSN")
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=0.2,
        profiles_sample_rate=0.1,
        integrations=[FastApiIntegration()],
    )

class InternalAuthMiddleware(BaseHTTPMiddleware):
    """Validate X-Internal-Api-Key on service-to-service requests."""
    async def dispatch(self, request: Request, call_next):
        # Skip internal auth for health, seed, docs, root, and job intake endpoints
        exempt_paths = ("/health", "/docs", "/openapi.json", "/redoc")
        exempt_prefixes = ("/api/seed", "/api/vendor-os", "/api/onboarding", "/api/admin", "/api/v1/payouts")
        if request.url.path in exempt_paths or any(request.url.path.startswith(p) for p in exempt_prefixes):
            return await call_next(request)

        internal_key = request.headers.get("X-Internal-Api-Key")
        if not internal_key:
            return Response(
                status_code=401,
                content='{"error":"missing_internal_auth","detail":"X-Internal-Api-Key header required"}',
                media_type="application/json",
            )
        if internal_key not in _INTERNAL_API_KEYS:
            return Response(
                status_code=403,
                content='{"error":"invalid_internal_auth","detail":"Invalid internal API key"}',
                media_type="application/json",
            )
        return await call_next(request)

from app.routers.auth import router as auth_router
from app.routers.dispatch import router as dispatch_router
from app.routers.vendor_os import router as vendor_os_router
from app.routers.onboarding import router as onboarding_router
from app.routers.admin_ops import router as admin_ops_router
from app.routers.uploads import router as uploads_router
from app.routers.payouts import router as payouts_router
from app.shared.database import get_db
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
    db = await get_db()
    if not _SEED_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")
    
    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    day_after = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")

    # Build raw job dicts to avoid schema mismatch with Supabase
    j1_id, j2_id, j3_id, j4_id, j5_id, j6_id, j7_id = [str(uuid4()) for _ in range(7)]
    now_iso = datetime.now(timezone.utc).isoformat()

    raw_jobs = [
        dict(id=j1_id, address="22 Rosehill Ave", city="Toronto", state="ON", zip_code="M4T 1G5", time_window_start="09:00", time_window_end="10:30", job_type="photo", status="assigned", priority="high", property_details="4BR/3BA detached home. 2,800 sqft. Renovated kitchen and master suite. Large backyard.", client_instructions="Focus on kitchen upgrades and open floor plan. Capture backyard entertaining area.", scheduled_date=today, deliverables_count=35, uploaded_count=0, assignee_id="default_user", vendor_id="default_vendor", checklist=[{"label": "Exterior front elevation", "completed": False}, {"label": "Exterior rear with yard", "completed": False}, {"label": "Living room wide angle", "completed": False}, {"label": "Kitchen overview", "completed": False}, {"label": "Master bedroom", "completed": False}, {"label": "Master bathroom", "completed": False}, {"label": "Backyard", "completed": False}, {"label": "Drone aerial shot", "completed": False}], created_at=now_iso, updated_at=now_iso),
        dict(id=j2_id, address="88 Harbour Square", city="Toronto", state="ON", zip_code="M5J 2S2", time_window_start="11:00", time_window_end="12:30", job_type="video", status="assigned", priority="urgent", property_details="2BR/2BA lakefront condo. 1,400 sqft. Floor-to-ceiling windows. Lake Ontario views.", client_instructions="Cinematic walkthrough. Emphasise lake views and building amenities. Include drone flyover.", scheduled_date=today, deliverables_count=3, uploaded_count=0, assignee_id="default_user", vendor_id="default_vendor", checklist=[{"label": "Walkthrough video (4K)", "completed": False}, {"label": "Drone flyover footage", "completed": False}, {"label": "Twilight exterior shots", "completed": False}], created_at=now_iso, updated_at=now_iso),
        dict(id=j3_id, address="312 King St W, Unit 1802", city="Toronto", state="ON", zip_code="M5V 1J2", time_window_start="14:00", time_window_end="15:30", job_type="3d_tour", status="in_progress", priority="normal", property_details="1BR+den condo. 750 sqft. Entertainment district. Modern finishes.", client_instructions="Full Gaussian splat capture including storage and den. Tag kitchen island and city view windows.", scheduled_date=today, deliverables_count=12, uploaded_count=4, assignee_id="default_user", vendor_id="default_vendor", checklist=[{"label": "Living area splat capture", "completed": True}, {"label": "Kitchen splat capture", "completed": True}, {"label": "Bedroom splat capture", "completed": True}, {"label": "Den splat capture", "completed": True}, {"label": "Bathroom splat capture", "completed": False}, {"label": "Balcony splat capture", "completed": False}, {"label": "Storage splat capture", "completed": False}, {"label": "Hallway splat capture", "completed": False}], created_at=now_iso, updated_at=now_iso),
        dict(id=j4_id, address="45 Dunbar Rd", city="Hamilton", state="ON", zip_code="L8P 3B4", time_window_start="16:00", time_window_end="17:00", job_type="floor_plan", status="completed", priority="normal", property_details="3BR/2BA century home. 1,750 sqft. Detached garage. Corner lot.", client_instructions="Standard floor plan with room dimensions. Include garage and yard footprint.", scheduled_date=today, deliverables_count=2, uploaded_count=2, assignee_id="default_user", vendor_id="default_vendor", checklist=[{"label": "Main floor measurement", "completed": True}, {"label": "Floor plan rendering", "completed": True}], created_at=now_iso, updated_at=now_iso),
        dict(id=j5_id, address="9 Harbour St E", city="Mississauga", state="ON", zip_code="L5G 1A7", time_window_start="08:00", time_window_end="10:00", job_type="drone", status="assigned", priority="high", property_details="4BR/3BA executive home. 3,200 sqft. Pool. Backing onto conservation land.", client_instructions="Aerial photography and video. Capture conservation backdrop and pool area.", scheduled_date=tomorrow, deliverables_count=15, uploaded_count=0, assignee_id="default_user", vendor_id="default_vendor", checklist=[{"label": "Aerial front approach", "completed": False}, {"label": "Overhead property shot", "completed": False}, {"label": "Conservation land backdrop", "completed": False}, {"label": "Pool and backyard aerial", "completed": False}, {"label": "Neighbourhood context shot", "completed": False}], created_at=now_iso, updated_at=now_iso),
        dict(id=j6_id, address="150 King St W", city="Toronto", state="ON", zip_code="M5H 1J9", time_window_start="13:00", time_window_end="14:30", job_type="photo", status="assigned", priority="normal", property_details="Commercial office space. 3,100 sqft. Financial district. Corner unit with city views.", client_instructions="Standard commercial photography. Emphasise natural light and boardroom.", scheduled_date=tomorrow, deliverables_count=18, uploaded_count=0, assignee_id="default_user", vendor_id="default_vendor", checklist=[{"label": "Exterior building shot", "completed": False}, {"label": "Reception area", "completed": False}, {"label": "Open office", "completed": False}, {"label": "Boardroom", "completed": False}], created_at=now_iso, updated_at=now_iso),
        dict(id=j7_id, address="1 Yonge St, Unit 4201", city="Toronto", state="ON", zip_code="M5E 1W7", time_window_start="10:00", time_window_end="12:00", job_type="video", status="assigned", priority="normal", property_details="2BR/2BA luxury condo. 1,100 sqft. 42nd floor. Panoramic lake and city views.", client_instructions="Lifestyle walkthrough video. Capture sunrise lake view. Music to be added in post.", scheduled_date=day_after, deliverables_count=4, uploaded_count=0, assignee_id="default_user", vendor_id="default_vendor", checklist=[{"label": "Interior walkthrough", "completed": False}, {"label": "Lake view footage", "completed": False}, {"label": "Sunrise timelapse", "completed": False}, {"label": "Amenities B-roll", "completed": False}], created_at=now_iso, updated_at=now_iso),
    ]
    for job in raw_jobs:
        await (await get_db()).table("jobs").insert(job).execute()

    sample_uploads = [
        UploadRecord(id=str(uuid4()), job_id=j3_id, filename="living_room_scan_001.e57", file_size=45000000, mime_type="application/octet-stream"),
        UploadRecord(id=str(uuid4()), job_id=j3_id, filename="kitchen_scan_002.e57", file_size=38000000, mime_type="application/octet-stream"),
        UploadRecord(id=str(uuid4()), job_id=j3_id, filename="bedroom1_scan_003.e57", file_size=42000000, mime_type="application/octet-stream"),
        UploadRecord(id=str(uuid4()), job_id=j3_id, filename="bedroom2_scan_004.e57", file_size=39000000, mime_type="application/octet-stream"),
        UploadRecord(id=str(uuid4()), job_id=j4_id, filename="floor_plan_main.pdf", file_size=2500000, mime_type="application/pdf"),
        UploadRecord(id=str(uuid4()), job_id=j4_id, filename="floor_plan_rendered.png", file_size=8500000, mime_type="image/png"),
    ]
    for upload in sample_uploads:
        await (await get_db()).table("uploads").insert(upload.dict()).execute()

    sample_notifs = [
        NotificationRecord(id=str(uuid4()), title="New Job Assigned", body="22 Rosehill Ave — Photo shoot at 09:00", type="new_job", data={"job_id": j1_id}),
        NotificationRecord(id=str(uuid4()), title="New Job Assigned", body="88 Harbour Square — URGENT video shoot at 11:00", type="new_job", data={"job_id": j2_id}),
        NotificationRecord(id=str(uuid4()), title="Job Started", body="312 King St W — 3D tour in progress", type="status_change", data={"job_id": j3_id}),
        NotificationRecord(id=str(uuid4()), title="Approaching Time Window", body="22 Rosehill Ave — starts in 30 min", type="reminder", data={"job_id": j1_id}),
    ]
    for n in sample_notifs:
        await (await get_db()).table("notifications").insert(n.dict()).execute()

    profile = Profile(id="default_user")
    try:
        await (await get_db()).table("profiles").insert(profile.dict()).execute()
    except Exception:
        pass  # table may not exist
    try:
        await (await get_db()).table("vendor_members").insert(VendorMember(
            id="default_user",
            name=profile.name,
            email=profile.email,
            phone=profile.phone,
            role="Owner Operator",
            is_active=True,
            onboarding_status="approved"
        ).dict()).execute()
    except Exception:
        pass
    try:
        await (await get_db()).table("vendor_availability").insert(AvailabilitySettings(member_id="default_user").dict()).execute()
    except Exception:
        pass
    try:
        await (await get_db()).table("vendor_settings").insert(VendorSettings(vendor_id="default_vendor").dict()).execute()
    except Exception:
        pass
    return {"seeded": True, "jobs_count": len(raw_jobs), "uploads_count": len(sample_uploads)}

app.include_router(dispatch_router)
app.include_router(auth_router)
app.include_router(vendor_os_router)
app.include_router(onboarding_router)
app.include_router(admin_ops_router)
app.include_router(uploads_router)
app.include_router(payouts_router)
app.include_router(seed_router)
app.add_middleware(InternalAuthMiddleware)
_ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_ALLOWED_ORIGINS, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(RateLimitMiddleware)

# ── Structured JSON Logging ────────────────────────────────────────────────────────
log_handler = logging.StreamHandler()
log_handler.setFormatter(jsonlogger.JsonFormatter(
    fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
))
logging.basicConfig(level=logging.INFO, handlers=[log_handler])
logger = logging.getLogger(__name__)

# ── Uptime tracking ────────────────────────────────────────────────────────────────
START_TIME = time.time()


def _uptime_seconds() -> int:
    return int(time.time() - _START_TIME)


def _sentry_status() -> str:
    return "initialized" if SENTRY_DSN else "not_configured"


@app.on_event("startup")
async def startup_event():
    logger.info("Dispatch service started", extra={"version": "1.0.0"})


# ── Request logging middleware ─────────────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration_ms = int((time.time() - start) * 1000)
    logger.info(
        "request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    return response


# ── Global exception handler ─────────────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    sentry_sdk.capture_exception(exc)
    logger.error(
        "unhandled_exception",
        extra={
            "method": request.method,
            "path": request.url.path,
            "error": str(exc),
            "error_type": type(exc).__name__,
        },
    )
    return Response(
        status_code=500,
        content='{"error":"internal_server_error","detail":"An unexpected error occurred"}',
        media_type="application/json",
    )


# ── Health endpoint ───────────────────────────────────────────────────────────────────



@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/health/ready")
async def ready():
    db_status = "connected"
    try:
        await (await get_db()).table('jobs').select('id').limit(1).execute()
    except Exception as e:
        db_status = "unreachable"
        raise HTTPException(status_code=503, detail=f'DB unreachable: {e}')
    return {"status": "ready", "db": db_status}
