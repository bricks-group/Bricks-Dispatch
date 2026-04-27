import logging
from typing import List, Optional
from datetime import datetime, timezone

from ..integrations.bricks_ai_dispatch import bricks_smart_assign
from ..shared.database import get_db
from ..shared.schemas import Job, VendorMember

logger = logging.getLogger(__name__)

def pick_vendor_rule_based(job: Job, vendors: List[VendorMember]) -> str:
    """
    Safe fallback: Deterministic rule-based vendor selection for Bricks.
    """
    candidates = [v for v in vendors if v.is_active and v.status == "available" and v.onboarding_status == "approved"]
    
    if not candidates:
        candidates = [v for v in vendors if v.is_active and v.onboarding_status == "approved"]
    
    if not candidates:
        logger.warning("No active vendors found for fallback assignment. Using 'default_user'.")
        return "default_user"

    def selection_score(v: VendorMember):
        specialty_match = 1 if v.specialty == job.job_type else 0
        return (specialty_match, -v.active_assignments, v.quality_rating, -len(v.name))

    candidates.sort(key=selection_score, reverse=True)
    chosen = candidates[0]
    logger.info(f"Bricks rule-based selection chose vendor {chosen.id} ({chosen.name}) for job {job.id}")
    return chosen.id

async def assign_vendor_for_job(job_id: str) -> str:
    """
    Orchestrates vendor assignment using Bricks AI with rule-based fallback.
    """
    res = await (await get_db()).table("jobs").select("*").eq("id", job_id).maybe_single().execute()
    job_doc = res.data
    if not job_doc:
        raise ValueError(f"Job {job_id} not found")
    
    job = Job(**job_doc)
    
    vendor_id = job.vendor_id or "default_vendor"
    res_members = await (await get_db()).table("vendor_members").select("*").eq("vendor_id", vendor_id).execute()
    members_docs = res_members.data
    vendors = [VendorMember(**m) for m in members_docs]

    if not vendors:
        logger.warning(f"No vendor members found for vendor_id {vendor_id}. Falling back to default.")
        return "default_user"

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
    logger.info(f"Job {job_id} assigned via Bricks rule-based fallback to {chosen_id}")
    await _update_job_assignee(job_id, chosen_id)
    return chosen_id

async def _update_job_assignee(job_id: str, member_id: str):
    now = datetime.now(timezone.utc).isoformat()
    await (await get_db()).table("jobs").update({
        "assignee_id": member_id, 
        "status": "assigned", 
        "updated_at": now
    }).eq("id", job_id).execute()
