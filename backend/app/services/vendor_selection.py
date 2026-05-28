import logging
import os
import httpx
from datetime import datetime, timezone
from pydantic import BaseModel, Field

from ..shared.database import get_db
from ..shared.schemas import Job, VendorMember
from .webhooks import send_dispatch_status_update_webhook

from typing import List, Optional

from ..integrations.bricks_ai_dispatch import bricks_smart_assign

# Set REQUIRE_VENDOR_COMPLIANCE=true in Vercel env when insurance records are populated
REQUIRE_VENDOR_COMPLIANCE = os.environ.get("REQUIRE_VENDOR_COMPLIANCE", "false").lower() == "true"

logger = logging.getLogger(__name__)

def pick_vendor_rule_based(job: Job, vendors: List[VendorMember]) -> Optional[str]:
    """
    Safe fallback: Deterministic rule-based vendor selection for Bricks.
    Enforces min_tier_required as a hard gate before any ranking.
    """
    min_tier = job.min_tier_required or 1
    preferred_tier = job.preferred_tier or min_tier

    eligible = [
        v for v in vendors
        if v.is_active and v.onboarding_status == "approved" and v.tier >= min_tier
    ]

    # Prefer available vendors; fall back to any eligible if none are available
    candidates = [v for v in eligible if v.status == "available"] or eligible

    if not candidates:
        logger.warning(
            f"No vendor meets min_tier={min_tier} for job {job.id} "
            f"(job_type={job.job_type}). Returning None."
        )
        return None

    def selection_score(v: VendorMember):
        specialty_match = 1 if v.specialty == job.job_type else 0
        tier_match = 1 if v.tier == preferred_tier else 0
        return (specialty_match, tier_match, -v.active_assignments, v.quality_rating)

    candidates.sort(key=selection_score, reverse=True)
    chosen = candidates[0]
    logger.info(
        f"Bricks rule-based selection chose vendor {chosen.id} ({chosen.name}) "
        f"tier={chosen.tier} for job {job.id} (min_tier={min_tier}, preferred={preferred_tier})"
    )
    return chosen.id

async def _update_job_assignee(job_id: str, member_id: str):
    now = datetime.now(timezone.utc).isoformat()
    await (await get_db()).table("jobs").update({
        "assignee_id": member_id,
        "status": "assigned",
        "updated_at": now
    }).eq("id", job_id).execute()

    # Notify agent that shoot is confirmed
    try:
        from .client_comms import notify_booking_confirmed
        await notify_booking_confirmed(job_id)
    except Exception as e:
        logger.error(f"Booking confirmed notification failed for job {job_id}: {e}")

async def assign_vendor_for_job(job_id: str) -> Optional[str]:
    """
    Orchestrates vendor assignment using Bricks AI with rule-based fallback.
    """
    res = await (await get_db()).table("jobs").select("*").eq("id", job_id).maybe_single().execute()
    job_doc = res.data
    if not job_doc:
        raise ValueError(f"Job {job_id} not found")
    
    job = Job(**job_doc)

    vendor_id = job_doc.get("vendor_id", "default_vendor")
    res_members = await (await get_db()).table("vendor_members").select("*").eq("vendor_id", vendor_id).execute()
    members_docs = res_members.data
    vendors = [VendorMember(**m) for m in members_docs]

    if not vendors:
        logger.warning(f"No vendor members found for vendor_id {vendor_id}. Returning None.")
        return None

    min_tier = job.min_tier_required or 1
    eligible_ids = {v.id for v in vendors if v.tier >= min_tier}

    # Compliance gate: check insurance/WSIB at the vendor (company) level once.
    # Enabled by REQUIRE_VENDOR_COMPLIANCE=true (flip when insurance records are populated).
    if REQUIRE_VENDOR_COMPLIANCE:
        db = await get_db()
        try:
            res_ok = await db.rpc("vendor_compliance_ok", {"p_vendor_id": vendor_id}).execute()
            is_compliant = bool(res_ok.data)
        except Exception as e:
            logger.warning(f"Compliance check RPC failed for vendor {vendor_id}: {e} — failing closed")
            is_compliant = False

        if not is_compliant:
            logger.warning(f"Vendor {vendor_id} failed compliance gate (insurance/WSIB) — job {job_id} cannot be assigned")
            return None
    else:
        logger.debug(f"Compliance gate disabled (REQUIRE_VENDOR_COMPLIANCE not set) for vendor {vendor_id}")

    try:
        chosen_id = await bricks_smart_assign(job, vendors)
        if chosen_id:
            logger.info(f"Job {job_id} assigned via Bricks AI to {chosen_id}")
            if chosen_id in eligible_ids:
                await _update_job_assignee(job_id, chosen_id)
                return chosen_id
            else:
                logger.warning(
                    f"Bricks AI suggested vendor {chosen_id} which is ineligible "
                    f"(not found or below min_tier={min_tier}). Falling back."
                )
    except Exception:
        logger.exception(f"Bricks AI assignment failed for job {job_id}")

    chosen_id = pick_vendor_rule_based(job, vendors)
    if chosen_id is None:
        logger.warning(f"No vendor could be assigned for job {job_id} after rule-based fallback. Returning None.")
        return None

    logger.info(f"Job {job.id} assigned via Bricks rule-based fallback to {chosen_id}")
    await _update_job_assignee(job_id, chosen_id)
    return chosen_id
