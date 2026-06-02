import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

import httpx

from ..shared.database import get_db
from ..shared.geospatial import haversine_km as _haversine_km
from ..shared.schemas import Job, VendorMember
from .webhooks import send_dispatch_status_update_webhook
from ..integrations.bricks_ai_dispatch import bricks_smart_assign

logger = logging.getLogger(__name__)

OPS_EMAIL = "ops@brxs.ca"


async def _escalate_no_vendor(job: Job, attempt_count: int = 1, error_message: str = "") -> None:
    db = await get_db()
    try:
        await db.table("ops_escalations").insert({
            "booking_id": job.booking_id,
            "job_id": job.id,
            "escalation_type": "no_vendor_available",
            "attempt_count": attempt_count,
            "error_message": error_message or f"No eligible vendor found after {attempt_count} attempt(s)",
            "status": "open",
        }).execute()
    except Exception as e:
        logger.error(f"Failed to write ops_escalation for job {job.id}: {e}")

    resend_key = os.environ.get("RESEND_API_KEY")
    if not resend_key:
        return
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {resend_key}"},
                json={
                    "from": "Bricks Dispatch <noreply@brxs.ca>",
                    "to": [OPS_EMAIL],
                    "subject": f"[OPS] No vendor available — job {job.id}",
                    "html": (
                        f"<p><b>Job:</b> {job.id}<br/>"
                        f"<b>Booking:</b> {job.booking_id}<br/>"
                        f"<b>Address:</b> {job.address}<br/>"
                        f"<b>Package:</b> {job.package_name}<br/>"
                        f"<b>Min tier:</b> {job.min_tier_required}<br/>"
                        f"<b>Attempts:</b> {attempt_count}</p>"
                        f"<p>No eligible vendor could be assigned. Manual assignment required.</p>"
                    ),
                },
            )
    except Exception as e:
        logger.error(f"Resend ops escalation email failed for job {job.id}: {e}")



def pick_vendor_rule_based(job: Job, vendors: List[VendorMember]) -> Optional[str]:
    """
    Deterministic rule-based vendor selection.
    Hard gates: min_tier, is_active, approved, and drone_certified for drone jobs.
    Ranks by: (specialty_match, tier_match, on_time_rate, quality_rating, -active_assignments, proximity)
    """
    min_tier = job.min_tier_required or 1
    preferred_tier = job.preferred_tier or min_tier
    is_drone_job = job.job_type == "drone"

    eligible = [
        v for v in vendors
        if (
            v.is_active
            and v.onboarding_status == "approved"
            and v.tier >= min_tier
            and (not is_drone_job or v.drone_certified)
        )
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
        if v.home_lat is not None and v.home_lng is not None and job.lat is not None and job.lng is not None:
            dist_km = _haversine_km(v.home_lat, v.home_lng, job.lat, job.lng)
            # Normalize: 0 km → 1.0, 50 km → 0.5, 100+ km → ~0
            proximity_score = max(0.0, 1.0 - dist_km / 100.0)

        # Reliability: penalise cancel_rate plus decline_rate above the 40% neutral band.
        # Declines within the 40% threshold are treated as zero penalty (IC right to refuse).
        _decline_penalty = max(0.0, (v.decline_rate or 0.0) - 0.40)
        _reliability = max(0.0, 1.0 - (v.cancel_rate or 0.0) - _decline_penalty)

        return (
            specialty_match,
            tier_match,
            round(v.on_time_rate, 2),
            round(v.quality_rating, 2),
            round(_reliability, 2),
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


_MAX_ASSIGNMENT_ATTEMPTS = 3


async def assign_vendor_for_job(job_id: str) -> Optional[str]:
    """
    Orchestrates vendor assignment using Bricks AI with rule-based fallback.
    Retries up to _MAX_ASSIGNMENT_ATTEMPTS times; on final failure writes an
    ops_escalation record and fires an email to ops@brxs.ca.
    """
    db = await get_db()
    res = await db.table("jobs").select("*").eq("id", job_id).maybe_single().execute()
    job_doc = res.data
    if not job_doc:
        raise ValueError(f"Job {job_id} not found")

    job = Job(**job_doc)

    vendor_id = job_doc.get("vendor_id", "default_vendor")
    res_members = await db.table("vendor_members").select("*").eq("vendor_id", vendor_id).execute()
    vendors = [VendorMember(**m) for m in (res_members.data or [])]

    if not vendors:
        logger.warning(f"No vendor members found for vendor_id {vendor_id}.")
        await _escalate_no_vendor(job, 1, "No vendor members in this vendor account.")
        return None

    min_tier = job.min_tier_required or 1
    eligible_ids = {v.id for v in vendors if v.tier >= min_tier}

    actual_attempts = 0
    for attempt in range(1, _MAX_ASSIGNMENT_ATTEMPTS + 1):
        actual_attempts = attempt
        _had_exception = False
        try:
            chosen_id = await bricks_smart_assign(job, vendors)
            if chosen_id and chosen_id in eligible_ids:
                logger.info(f"Job {job_id} assigned via Bricks AI to {chosen_id} (attempt {attempt})")
                await _update_job_assignee(job_id, chosen_id)
                return chosen_id
            if chosen_id:
                logger.warning(
                    f"Bricks AI suggested vendor {chosen_id} which is ineligible "
                    f"(not found or below min_tier={min_tier}). Falling back (attempt {attempt})."
                )
        except Exception:
            _had_exception = True
            logger.exception(f"Bricks AI assignment failed for job {job_id} (attempt {attempt})")

        chosen_id = pick_vendor_rule_based(job, vendors)
        if chosen_id is not None:
            logger.info(f"Job {job.id} assigned via rule-based fallback to {chosen_id} (attempt {attempt})")
            await _update_job_assignee(job_id, chosen_id)
            return chosen_id

        logger.warning(f"No vendor assigned for job {job_id} (attempt {attempt}/{_MAX_ASSIGNMENT_ATTEMPTS}).")
        if not _had_exception:
            # Both scorers are deterministic — retrying identical inputs won't change the result.
            # Only loop again if a transient exception occurred in bricks_smart_assign.
            break

    await db.table("jobs").update({"status": "dispatch_failed"}).eq("id", job_id).execute()
    await _escalate_no_vendor(job, actual_attempts)
    return None
