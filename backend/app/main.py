from fastapi import FastAPI, Depends, HTTPException, Request
from supabase import create_client, Client
import os
import sentry_sdk
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware

# Import routers
from app.routers import auth, dispatch, jobs, uploads, vendors, notifications, payouts, post_production, vendor_os, onboarding, admin_ops

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
    try:
        db.table("jobs").select("id").limit(1).execute() # Example check
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB unreachable: {e}")
    return {"status": "ready", "db": "ok"}

# --- Register Routers ---
# dispatch.router and payouts.router carry their own /api/v1/* prefix internally.
app.include_router(auth.router, tags=["auth"])
app.include_router(dispatch.router, tags=["dispatch"])
app.include_router(payouts.router, tags=["payouts"])
app.include_router(jobs.router, prefix="/dispatch/jobs", tags=["jobs"])
app.include_router(uploads.router, tags=["uploads"])
app.include_router(vendors.router, prefix="/dispatch/vendors", tags=["vendors"])
app.include_router(notifications.router, prefix="/dispatch/notifications", tags=["notifications"])
app.include_router(post_production.router, tags=["post-production"])
app.include_router(vendor_os.router, tags=["vendor-os"])
app.include_router(onboarding.router, tags=["onboarding"])
app.include_router(admin_ops.router, tags=["admin"])
