import logging
from typing import List, Optional

from ..integrations.bricks_ai_dispatch import bricks_smart_assign
from ..shared.database import db
from ..shared.schemas import Job, VendorMember

logger = logging.getLogger(__name__)

def pick_vendor_rule_based(job: Job, vendors: List[VendorMember]) -> str:
    """
    Safe fallback: Deterministic rule-based vendor selection for Bricks.
    """
    # 1. Filter by status == "active" and "approved"
    candidates = [v for v in vendors if v.is_active and v.status == "available" and v.onboarding_status == "approved"]
    
    if not candidates:
        # Fallback to any active approved vendor if none are "available"
        candidates = [v for v in vendors if v.is_active and v.onboarding_status == "approved"]
    
    if not candidates:
        logger.warning("No active vendors found for fallback assignment. Using 'default_user'.")
        return "default_user"

    # 2. Prefer specialty match
    # 3. Sort by active_assignments (asc), then quality_rating (desc), then name
    
    def selection_score(v: VendorMember):
        # Specialty match is highest priority
        specialty_match = 1 if v.specialty == job.job_type else 0
        # We want to maximize specialty_match, minimize active_assignments, maximize quality_rating
        return (specialty_match, -v.active_assignments, v.quality_rating, -len(v.name))

    candidates.sort(key=selection_score, reverse=True)
    
    chosen = candidates[0]
    logger.info(f"Bricks rule-based selection chose vendor {chosen.id} ({chosen.name}) for job {job.id}")
    return chosen.id

async def assign_vendor_for_job(job_id: str) -> str:
    """
    Orchestrates vendor assignment using Bricks AI with rule-based fallback.
    """
    job_doc = await db.jobs.find_one({"id": job_id})
    if not job_doc:
        raise ValueError(f"Job {job_id} not found")
    
    job = Job(**job_doc)
    
    # Load all vendor members for this job's vendor_id (company)
    vendor_id = job.vendor_id or "default_vendor"
    members_cursor = db.vendor_members.find({"vendor_id": vendor_id})
    members_docs = await members_cursor.to_list(100)
    vendors = [VendorMember(**m) for m in members_docs]

    if not vendors:
        logger.warning(f"No vendor members found for vendor_id {vendor_id}. Falling back to default.")
        return "default_user"

    # 1. Bricks AI Path
    try:
        chosen_id = await bricks_smart_assign(job, vendors)
        if chosen_id:
            logger.info(f"Job {job_id} assigned via Bricks AI to {chosen_id}")
            # Verify the suggested vendor exists in our list
            if any(v.id == chosen_id for v in vendors):
                await _update_job_assignee(job_id, chosen_id)
                return chosen_id
            else:
                logger.warning(f"Bricks AI suggested non-existent vendor_id {chosen_id}. Falling back.")
    except Exception:
        logger.exception(f"Bricks AI assignment failed for job {job_id}")

    # 2. Fallback Path
    chosen_id = pick_vendor_rule_based(job, vendors)
    logger.info(f"Job {job_id} assigned via Bricks rule-based fallback to {chosen_id}")
    await _update_job_assignee(job_id, chosen_id)
    return chosen_id

async def _update_job_assignee(job_id: str, member_id: str):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.jobs.update_one(
        {"id": job_id},
        {"$set": {
            "assignee_id": member_id, 
            "status": "assigned", 
            "updated_at": now
        }}
    )
