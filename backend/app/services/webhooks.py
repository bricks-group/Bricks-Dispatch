
from datetime import datetime, timezone
from uuid import uuid4
import httpx
import os
from pydantic import BaseModel, Field
from ..shared.auth import get_own_internal_api_key
from ..shared.database import get_db

# Internal service-to-service auth -- reads INTERNAL_API_KEYS (see get_own_internal_api_key),
# not a separate INTERNAL_API_KEY var that was never actually configured.
INTERNAL_API_KEY = get_own_internal_api_key()
BOOKING_API_URL = os.environ.get("BOOKING_API_URL", "https://bricks-companion-api.vercel.app")

class DispatchWebhookEventData(BaseModel):
    booking_id: str
    job_id: str
    previous_status: str | None = None
    new_status: str
    actor_id: str | None = None
    actor_type: str | None = None

async def send_dispatch_status_update_webhook(
    booking_id: str,
    job_id: str,
    new_status: str,
    previous_status: str | None = None,
    actor_id: str | None = None,
    actor_type: str | None = None,
):
    """
    Sends a status update callback to the Booking service.
    Endpoint: PUT /api/dispatch-jobs/{booking_id}/status -- NOT /api/v1/. Companion's
    api_router has prefix="/api" with no version segment (Bricks-Companion/server.py:529,
    route at line 2435); this call was still posting to a /v1/ path that Companion never
    served, 404ing on every real call. Companion's own CLAUDE.md already noted the route
    had no /v1/ (corrected 2026-07-27) but this caller was never updated to match --
    found and fixed 2026-08-03.
    """
    # Builder/developer bookings from www live in Bricks OS
    # (public.commercial_bookings, booking_id "cb_<id>"), not in Companion's
    # bookings table, so there is no Companion booking to update.
    if str(booking_id or "").startswith("cb_"):
        return False
    webhook_url = f"{BOOKING_API_URL}/api/dispatch-jobs/{booking_id}/status"

    payload = {
        "job_id": job_id,
        "new_status": new_status,
        "previous_status": previous_status,
        "actor_id": actor_id,
        "actor_type": actor_type,
        "changed_at": datetime.now(timezone.utc).isoformat()
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.put(
                webhook_url,
                json=payload,
                headers={"X-Internal-Api-Key": INTERNAL_API_KEY}
            )
            response.raise_for_status()
            return True
    except Exception as e:
        # In a real system, we might queue this for retry
        print(f"Failed to send status update webhook for booking {booking_id}: {e}")
        return False


OS_API_URL = os.environ.get("OS_API_URL", "https://bricks-brokerage-api.vercel.app")

# Dispatch's own job status values that mean "this capture visit is done, one
# way or another" -- dev_capture_visits has no in-progress state of its own
# (see 20260913000000_developer_projects.sql), only scheduled/completed/failed.
_CAPTURE_VISIT_TERMINAL_STATUS = {
    "completed": "completed",
    "delivered": "completed",
    "cancelled": "failed",
    "dispatch_failed": "failed",
}


async def send_dev_capture_status_update_webhook(dev_capture_visit_id: str, job_id: str, new_status: str) -> bool:
    """Reports a progress-capture job's status back to Bricks-OS's
    dev_capture_visits row -- the developer/REIT vertical's equivalent of
    send_dispatch_status_update_webhook above, for jobs that originated from
    a developer project instead of a Booking booking (see dev_capture_visit_id
    on Job/JobIntakeRequest).

    Only terminal statuses are worth a round trip -- dev_capture_visits has no
    column to reflect assigned/in_progress, so mid-flight updates are a no-op.
    """
    mapped_status = _CAPTURE_VISIT_TERMINAL_STATUS.get(new_status)
    if not mapped_status:
        return True

    payload: dict = {"status": mapped_status}

    if mapped_status == "completed":
        # Best-effort only: surfacing a delivered photo/video URl on the
        # developer's capture-visit record is a nice-to-have, not something
        # that should block reporting the status itself.
        try:
            db = await get_db()
            res = await db.table("media_delivery").select("delivery_urls").eq("job_id", job_id).maybe_single().execute()
            urls = (res.data or {}).get("delivery_urls") or []
            if urls:
                payload["media_delivery_url"] = urls[0]
        except Exception as e:
            print(f"send_dev_capture_status_update_webhook: media_delivery lookup failed for job {job_id}: {e}")

    webhook_url = f"{OS_API_URL}/api/dev/capture-visits/{dev_capture_visit_id}/status"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.patch(
                webhook_url,
                json=payload,
                headers={"X-Internal-Api-Key": INTERNAL_API_KEY},
            )
            response.raise_for_status()
            return True
    except Exception as e:
        print(f"Failed to send capture-visit status update for {dev_capture_visit_id}: {e}")
        return False
