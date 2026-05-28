import logging
import math
from datetime import datetime, timezone
from typing import List, Optional

from ..shared.database import get_db
from ..shared.schemas import Job, VendorMember
from .webhooks import send_dispatch_status_update_webhook
from ..integrations.bricks_ai_dispatch import bricks_smart_assign

logger = logging.getLogger(__name__)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def pick_vendor_rule_based(job: Job, vendors: List[VendorMember]) -> Optional[str]:
    """
    Deterministic rule-based vendor selection.
    Enforces min_tier_required as a hard gate, then ranks by:
    (specialty_match, tier_match, on_time_rate, quality_rating, -active_assignments, -travel_km)
    """
    min_tier = job.min_tier_required or 1
    preferred_tier = job.preferred_tier or min_tier

    eligible = [
        v for v in vendors
        if v.is_active and v.onboarding_status == "approved" and v.tier >= min_tier
    ]

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

        # Travel proximity bonus — prefer vendors closer to the job
        proximity_score = 0.0
        if v.home_lat and v.home_lng and job.lat and job.lng:
            dist_km = _haversine_km(v.home_lat, v.home_lng, job.lat, job.lng)
            # Normalize: 0 km → 1.0, 50 km → 0.5, 100+ km → ~0
            proximity_score = max(0.0, 1.0 - dist_km / 100.0)

        return (
            specialty_match,
            tier_match,
            round(v.on_time_rate, 2),
            round(v.quality_rating, 2),
            -v.active_assignments,
            round(proximity_score, 2),
        )

    candidates.sort(key=selection_score, reverse=True)
    chosen = candidates[0]
    logger.info(
        f"Rule-based selection chose vendor {chosen.id} ({chosen.name}) "
        f"tier={chosen.tier} for job {job.id} (min_tier={min_tier}, preferred={preferred_tier})"
    )
    return chosen.id


async def _update_job_assignee(job_id: str, member_id: str):
    now = datetime.now(timezone.utc).isoformat()
    await (await get_db()).table("jobs").update({
        "assignee_id": member_id,
        "status": "assigned",
        "updated_at": now,
    }).eq("id", job_id).execute()

    try:
        from .client_comms import notify_booking_confirmed
        await notify_booking_confirmed(job_id)
    except Exception as e:
        logger.error(f"Booking confirmed notification failed for job {job_id}: {e}")


async def assign_vendor_for_job(job_id: str) -> Optional[str]:
    """
    Orchestrates vendor assignment using Bricks AI with rule-based fallback.
    Bricks uses the Snappr/Homejab IC model — no compliance gate; contractors
    self-certify via the IC agreement at onboarding.
    """
    res = await (await get_db()).table("jobs").select("*").eq("id", job_id).maybe_single().execute()
    job_doc = res.data
    if not job_doc:
        raise ValueError(f"Job {job_id} not found")

    job = Job(**job_doc)

    vendor_id = job_doc.get("vendor_id", "default_vendor")
    res_members = await (await get_db()).table("vendor_members").select("*").eq("vendor_id", vendor_id).execute()
    vendors = [VendorMember(**m) for m in (res_members.data or [])]

    if not vendors:
        logger.warning(f"No vendor members found for vendor_id {vendor_id}.")
        return None

    min_tier = job.min_tier_required or 1
    eligible_ids = {v.id for v in vendors if v.tier >= min_tier}

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
        logger.warning(f"No vendor could be assigned for job {job_id}. Returning None.")
        return None

    logger.info(f"Job {job.id} assigned via rule-based fallback to {chosen_id}")
    await _update_job_assignee(job_id, chosen_id)
    return chosen_id
