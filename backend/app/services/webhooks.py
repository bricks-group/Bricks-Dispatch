
from datetime import datetime, timezone
from uuid import uuid4
import httpx
import os
from pydantic import BaseModel, Field

# Internal service-to-service auth
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "bk-5274a1f72baf20a0699c405")
BOOKING_API_URL = os.environ.get("BOOKING_API_URL", "https://bricks-booking-api.vercel.app")

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
    Endpoint: PUT /api/v1/dispatch-jobs/{booking_id}/status
    """
    webhook_url = f"{BOOKING_API_URL}/api/v1/dispatch-jobs/{booking_id}/status"

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
