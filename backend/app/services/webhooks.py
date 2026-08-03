
from datetime import datetime, timezone
from uuid import uuid4
import httpx
import os
from pydantic import BaseModel, Field

# Internal service-to-service auth
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "")
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
