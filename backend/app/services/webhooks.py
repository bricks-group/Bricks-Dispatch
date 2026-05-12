
from datetime import datetime
from uuid import uuid4
import httpx
from pydantic import BaseModel, Field

# Assume BOOKING_API_URL is available from environment variables or config
# In server.py, _BOOKING_API_URL is defined. We'll assume it's accessible here.
# For now, let's hardcode it or assume it's passed in.
# In a real scenario, this would likely be injected or loaded from config.
BOOKING_API_URL = "http://localhost:8001" # Placeholder, should be configured

class DispatchWebhookEventData(BaseModel):
    booking_id: str
    job_id: str
    previous_status: str | None = None
    new_status: str
    actor_id: str | None = None
    actor_type: str | None = None

class DispatchWebhookEvent(BaseModel):
    event_type: str # e.g., "job.completed", "job.failed"
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    source: str = "dispatch"
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    data: DispatchWebhookEventData

async def send_dispatch_status_update_webhook(
    booking_id: str,
    job_id: str,
    new_status: str,
    previous_status: str | None = None,
    actor_id: str | None = None,
    actor_type: str | None = None,
    event_type: str = "job.status_updated", # Default event type
):
    """
    Sends a webhook event to the Booking service's callback URL.
    """
    webhook_url = f"{BOOKING_API_URL}/api/v1/bookings/webhook" # Assuming this is the callback endpoint

    event_data = DispatchWebhookEventData(
        booking_id=booking_id,
        job_id=job_id,
        new_status=new_status,
        previous_status=previous_status,
        actor_id=actor_id,
        actor_type=actor_type,
    )

    webhook_event = DispatchWebhookEvent(
        event_type=event_type,
        data=event_data,
    )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                webhook_url,
                json=webhook_event.dict(),
                headers={"X-Internal-Api-Key": "your-internal-api-key"} # Placeholder, needs actual key
            )
            response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
            print(f"Successfully sent webhook for job {job_id} to {webhook_url}")
            return True
    except httpx.HTTPStatusError as e:
        print(f"HTTP error sending webhook for job {job_id}: {e.response.status_code} - {e.response.text}")
        return False
    except httpx.RequestError as e:
        print(f"Request error sending webhook for job {job_id}: {e}")
        return False
    except Exception as e:
        print(f"An unexpected error occurred sending webhook for job {job_id}: {e}")
        return False

# Example usage (for testing purposes, would be called from job status update logic)
# async def trigger_completion_callback(job_id: str, booking_id: str):
#     await send_dispatch_status_update_webhook(
#         booking_id=booking_id,
#         job_id=job_id,
#         new_status="completed",
#         previous_status="in_progress",
#         event_type="job.completed",
#     )
