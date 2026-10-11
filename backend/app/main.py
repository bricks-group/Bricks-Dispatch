from fastapi import FastAPI, Depends, HTTPException
from supabase import create_client, Client
import os
import sentry_sdk
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware

# Import routers
from app.routers import dispatch, notifications, payouts, post_production, vendor_os, onboarding, admin_ops, uploads, auth, jobs, capture
from app.routers.stripe_connect_status import router as stripe_connect_status_router
from app.routers.stripe_connect_webhook import router as stripe_connect_webhook_router

limiter = Limiter(key_func=get_remote_address, default_limits=['100/minute'])

app = FastAPI()
app.state.limiter = limiter

try:
    _sentry_dsn = os.environ.get("SENTRY_DSN")
    if _sentry_dsn:
        sentry_sdk.init(
            dsn=_sentry_dsn,
            traces_sample_rate=1.0,
            profiles_sample_rate=1.0,
        )
    else:
        print("Sentry init skipped (no SENTRY_DSN set)")
except Exception as e:
    print(f"Sentry init failed: {e}")

app.add_middleware(SlowAPIMiddleware)

async def get_db() -> Client:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        raise HTTPException(status_code=500, detail="Supabase credentials not found")
    return create_client(supabase_url, supabase_key)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "bricks-dispatch"}

@app.get("/health/ready")
async def ready(db: Client = Depends(get_db)):
    # Read-only on purpose: this is a liveness/readiness probe, polled
    # frequently by uptime monitors and the platform's own health checks.
    # It must never perform writes. Owner-profile seeding used to run here
    # as a workaround and got demoted to an explicit admin-triggered
    # endpoint instead -- see POST /api/admin/seed-owner-profile.
    try:
        db.table("jobs").select("id").limit(1).execute() # Example check
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB unreachable: {e}")
    return {"status": "ready", "db": "ok"}

# --- Register Routers ---
# dispatch.router and payouts.router carry their own /api/v1/* prefix internally.
app.include_router(dispatch.router, tags=["dispatch"])
app.include_router(payouts.router, tags=["payouts"])
app.include_router(notifications.router, tags=["notifications"])
app.include_router(jobs.router, tags=["jobs"])
app.include_router(post_production.router, tags=["post-production"])
app.include_router(stripe_connect_status_router, tags=["vendor-os"])  # honest Connected (sole status route)
app.include_router(vendor_os.router, tags=["vendor-os"])
# Drop hollow Connected duplicate if still defined in vendor_os (honest router is sole status).
from fastapi.routing import APIRoute
app.router.routes = [
    r for r in app.router.routes
    if not (
        isinstance(r, APIRoute)
        and r.path == "/api/vendor-os/stripe-connect/status"
        and getattr(r.endpoint, "__module__", "").endswith("routers.vendor_os")
    )
]
app.include_router(stripe_connect_webhook_router, tags=["stripe-connect"])
app.include_router(onboarding.router, tags=["onboarding"])
app.include_router(admin_ops.router, tags=["admin"])
# Mounted 2026-07-27: both carry their own /api/* prefix internally, real
# working code that was built but never wired in. uploads.py is a dependency
# of vendor_os.py's post-production trigger comment; auth.py is the only path
# for the documented IC-agreement re-prompt-on-login behavior.
app.include_router(uploads.router, tags=["uploads"])
app.include_router(auth.router, tags=["auth"])
app.include_router(capture.router, tags=["capture"])
from .routers import capture_visit_video
app.include_router(capture_visit_video.router, tags=["capture"])
from .routers.vendor_profile import router as vendor_profile_router, os_router as vendor_profile_os_router
app.include_router(vendor_profile_router)
app.include_router(vendor_profile_os_router)
