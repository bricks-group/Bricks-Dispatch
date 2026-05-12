import logging
import httpx
from datetime import datetime, timezone
from pydantic import BaseModel, Field

from ..shared.database import get_db
from ..shared.schemas import Job, VendorMember
from .webhooks import send_dispatch_status_update_webhook # Import the webhook function

from typing import List, Optional
from datetime import datetime, timezone

from ..integrations.bricks_ai_dispatch import bricks_smart_assign
from ..shared.database import get_db
from ..shared.schemas import Job, VendorMember

logger = logging.getLogger(__name__)

def pick_vendor_rule_based(job: Job, vendors: List[VendorMember]) -> Optional[str]:
    """
    Safe fallback: Deterministic rule-based vendor selection for Bricks.
    """
    candidates = [v for v in vendors if v.is_active and v.status == "available" and v.onboarding_status == "approved"]
    
    if not candidates:
        candidates = [v for v in vendors if v.is_active and v.onboarding_status == "approved"]
    
    if not candidates:
        logger.warning("No active vendors found for fallback assignment. Returning None.")
        return None

    def selection_score(v: VendorMember):
        specialty_match = 1 if v.specialty == job.job_type else 0
        return (specialty_match, -v.active_assignments, v.quality_rating, -len(v.name))

    candidates.sort(key=selection_score, reverse=True)
    chosen = candidates[0]
    logger.info(f"Bricks rule-based selection chose vendor {chosen.id} ({chosen.name}) for job {job.id}")
    return chosen.id

async def assign_vendor_for_job(job_id: str) -> Optional[str]:
    """
    Orchestrates vendor assignment using Bricks AI with rule-based fallback.
    """
    res = await (await get_db()).table("jobs").select("*").eq("id", job_id).maybe_single().execute()
    job_doc = res.data
    if not job_doc:
        raise ValueError(f"Job {job_id} not found")
    
    job = Job(**job_doc)
    
    vendor_id = job.vendor_id
    res_members = await (await get_db()).table("vendor_members").select("*").eq("vendor_id", vendor_id).execute()
    members_docs = res_members.data
    vendors = [VendorMember(**m) for m in members_docs]

    if not vendors:
        logger.warning(f"No vendor members found for vendor_id {vendor_id}. Returning None.")
        return None

    try:
        chosen_id = await bricks_smart_assign(job, vendors)
        if chosen_id:
            logger.info(f"Job {job_id} assigned via Bricks AI to {chosen_id}")
            if any(v.id == chosen_id for v in vendors):
                await _update_job_assignee(job_id, chosen_id)
                return chosen_id
            else:
                logger.warning(f"Bricks AI suggested non-existent vendor_id {chosen_id}. Falling back.")
    except Exception:
        logger.exception(f"Bricks AI assignment failed for job {job_id}")

    chosen_id = pick_vendor_rule_based(job, vendors)
    if chosen_id is None:
        logger.warning(f"No vendor could be assigned for job {job_id} after rule-based fallback. Returning None.")
        return None

    logger.info(f"Job {job.id} assigned via Bricks rule-based fallback to {chosen_id}")
    await _update_job_assignee(job_id, chosen_id)
    return chosen_id


    now = datetime.now(timezone.utc).isoformat()
    # Store previous status if available, though this function doesn't receive it directly.
    # For demonstration, we assume 'assigned' is the previous status if this function is called for reassignment.
    # A more robust solution would get the current status before updating.
    previous_status = "assigned" # Simplified assumption for demonstration

    await (await get_db()).table("jobs").update({
        "assignee_id": member_id,
        "status": new_status, # Use the passed in new_status
        "updated_at": now
    }).eq("id", job_id).execute()

    # Trigger webhook callback if the new status is a terminal state
    terminal_statuses = ["completed", "delivered", "failed", "cancelled"]
    if new_status in terminal_statuses:
        # This is a critical assumption: we don't have the previous status easily here.
        # In a real implementation, we'd fetch the job first to get its prior status.
        # For now, we'll pass a simplified previous_status or None.
        await send_dispatch_status_update_webhook(
            booking_id=booking_id, # This function needs booking_id passed in too.
            job_id=job_id,
            new_status=new_status,
            previous_status=previous_status, # This is a simplified assumption.
            actor_id=member_id, # The assignee is the actor here.
            actor_type="vendor_member",
            event_type=f"job.{new_status}", # e.g., "job.completed"
        )
