import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

import httpx

from ..shared.database import get_db
from ..shared.geospatial import haversine_km as _haversine_km
from ..shared.schemas import Job, VendorMember
from .webhooks import send_dispatch_status_update_webhook
from ..integrations.bricks_ai_dispatch import bricks_smart_assign, _get_weather_context
from .vendor_profile import (
    has_advanced_rpas,
    is_drone_job as _is_drone_job,
    is_platform_owner,
    member_eligible_for_job,
    min_tier_for_job,
    ORG_VENDOR_ID,
    preferred_tier_for_job,
    travel_within_cap,
    vendor_skills,
    _missing_column,
)
from .shoot_duration import (
    estimate_shoot_duration_minutes,
    previous_job_coords,
    vendor_slot_conflicts,
    _slot_bounds,
)

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
                    "from": "Bricks GO <noreply@brxs.ca>",
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




async def load_occupied_jobs_by_vendor(
    db,
    vendor_member_ids: List[str],
    scheduled_date: str | None = None,
) -> dict[str, list[dict]]:
    """Jobs already on each vendor's book for conflict / travel chaining."""
    occupied: dict[str, list[dict]] = {vid: [] for vid in vendor_member_ids}
    ids = [i for i in vendor_member_ids if i]
    if not ids:
        return occupied
    try:
        q = (
            db.table("jobs")
            .select(
                "id, assignee_id, scheduled_date, time_window_start, time_window_end, "
                "confirmed_arrive_at, selected_slot_start, selected_slot_end, "
                "estimated_duration_min, lat, lng, status, address, city"
            )
            .in_("assignee_id", ids[:80])
            .in_("status", ["assigned", "in_progress", "pending_assignment", "completed"])
        )
        if scheduled_date:
            q = q.eq("scheduled_date", str(scheduled_date)[:10])
        res = await q.limit(400).execute()
        for row in res.data or []:
            aid = row.get("assignee_id")
            if aid in occupied:
                occupied[aid].append(row)
            elif aid:
                occupied.setdefault(aid, []).append(row)
    except Exception as e:
        # Soft-drop missing columns (selected_slot_*, estimated_duration_min)
        msg = str(e)
        miss = _missing_column(msg, [
            "selected_slot_start", "selected_slot_end", "estimated_duration_min", "confirmed_arrive_at"
        ])
        if miss:
            try:
                q = (
                    db.table("jobs")
                    .select(
                        "id, assignee_id, scheduled_date, time_window_start, time_window_end, "
                        "lat, lng, status, address, city"
                    )
                    .in_("assignee_id", ids[:80])
                    .in_("status", ["assigned", "in_progress", "pending_assignment", "completed"])
                )
                if scheduled_date:
                    q = q.eq("scheduled_date", str(scheduled_date)[:10])
                res = await q.limit(400).execute()
                for row in res.data or []:
                    aid = row.get("assignee_id")
                    if aid:
                        occupied.setdefault(aid, []).append(row)
            except Exception as e2:
                logger.warning("load_occupied_jobs_by_vendor fallback failed: %s", e2)
        else:
            logger.warning("load_occupied_jobs_by_vendor failed: %s", e)
    return occupied


def filter_assignable_vendors(
    job: Job,
    vendors: List[VendorMember],
    occupied_by_vendor: Optional[dict] = None,
) -> List[VendorMember]:
    """Apply eligibility + same-day slot conflict + travel-from-previous hard gates."""
    occupied_by_vendor = occupied_by_vendor or {}
    out: List[VendorMember] = []
    for v in vendors:
        if not member_eligible_for_job(v, job):
            continue
        occupied = occupied_by_vendor.get(v.id) or []
        if vendor_slot_conflicts(job, occupied):
            logger.info("Vendor %s excluded: slot conflict for job %s", v.id, job.id)
            continue
        origin = previous_job_coords(job, occupied)
        if origin is not None:
            cap = travel_within_cap(v, job, origin=origin)
            if cap is False:
                logger.info("Vendor %s excluded: over travel cap from previous job for %s", v.id, job.id)
                continue
        out.append(v)
    return out


def pick_vendor_rule_based(job: Job, vendors: List[VendorMember], occupied_by_vendor: Optional[dict] = None) -> Optional[str]:
    """
    Deterministic vendor selection for paid /book and Dispatch.
    Hard gates: approved+active (Aman/pending never), location, package min-tier,
    skills, Advanced RPAS for drone. No match → None (job stays unassigned).
    Owner (Kam) ranks first among eligible so he wins matching GTA jobs.
    """
    min_tier = min_tier_for_job(job)
    preferred_tier = preferred_tier_for_job(job)
    drone_job = _is_drone_job(job)

    eligible = filter_assignable_vendors(job, vendors, occupied_by_vendor=occupied_by_vendor)

    candidates = [v for v in eligible if v.status == "available"] or eligible

    if not candidates:
        logger.warning(
            f"No vendor meets location/package/tier/skills/RPAS min_tier={min_tier} for job {job.id} "
            f"(job_type={job.job_type} drone={drone_job}). Returning None."
        )
        return None

    def selection_score(v: VendorMember):
        owner_bonus = 1 if is_platform_owner(v) else 0
        have = vendor_skills(v)
        specialty_match = 1 if v.specialty == job.job_type or job.job_type in have else 0
        tier_match = 1 if v.tier == preferred_tier else 0
        # Higher tier wins premium/luxury packages when preferred_tier is top.
        tier_rank = v.tier or 1

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
            owner_bonus,
            specialty_match,
            tier_rank,
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


async def _patch_job_assign(job_id: str, patch: dict) -> None:
    """Update job assign fields; soft-drop columns PostgREST says are missing."""
    data = dict(patch)
    last = None
    db = await get_db()
    for _ in range(8):
        try:
            await db.table("jobs").update(data).eq("id", job_id).execute()
            return
        except Exception as e:
            last = e
            miss = _missing_column(str(e), list(data.keys()))
            if not miss:
                raise
            data.pop(miss, None)
    if last:
        raise last


async def _update_job_assignee(job_id: str, member_id: str, estimated_duration_min: Optional[int] = None):
    now = datetime.now(timezone.utc).isoformat()
    patch = {
        "assignee_id": member_id,
        "status": "assigned",
        "updated_at": now,
    }
    if estimated_duration_min is not None:
        patch["estimated_duration_min"] = int(estimated_duration_min)
    await _patch_job_assign(job_id, patch)

    try:
        from .client_comms import notify_booking_confirmed
        await notify_booking_confirmed(job_id)
    except Exception as e:
        logger.error(f"Booking confirmed notification failed for job {job_id}: {e}")

    try:
        from .notifications import notify_assignment
        res = await (await get_db()).table("jobs").select("*").eq("id", job_id).maybe_single().execute()
        await notify_assignment(res.data or {"id": job_id}, member_id)
    except Exception as e:
        logger.error(f"Vendor assignment notify failed for job {job_id}: {e}")


_MAX_ASSIGNMENT_ATTEMPTS = 3


async def assign_vendor_for_job(job_id: str, exclude_member_id: Optional[str] = None) -> Optional[str]:
    """
    Orchestrates vendor assignment using Bricks AI with rule-based fallback.
    Retries up to _MAX_ASSIGNMENT_ATTEMPTS times; on final failure writes an
    ops_escalation record and fires an email to ops@brxs.ca.

    exclude_member_id: skip this vendor member on all attempts (e.g. contractor who no-showed).
    """
    db = await get_db()
    res = await db.table("jobs").select("*").eq("id", job_id).maybe_single().execute()
    job_doc = res.data
    if not job_doc:
        raise ValueError(f"Job {job_id} not found")

    job = Job(**job_doc)

    vendor_id = job_doc.get("vendor_id", ORG_VENDOR_ID)
    # Pool is the photography org (default_vendor) plus any members tagged with
    # this job's vendor_id. Paid /book previously stored Kam's member UUID as
    # jobs.vendor_id; those rows must still see every approved photographer.
    org_ids = list({ORG_VENDOR_ID, vendor_id}) if vendor_id else [ORG_VENDOR_ID]
    res_members = await db.table("vendor_members").select("*").in_("vendor_id", org_ids).execute()
    rows = res_members.data or []
    vendors = []
    for m in rows:
        try:
            vendors.append(VendorMember(**m))
        except Exception as e:
            logger.warning(f"Skipping vendor_members row {m.get('id')}: {e}")

    # Exclude the specified member (e.g. contractor who no-showed — must not be re-selected)
    if exclude_member_id:
        vendors = [v for v in vendors if v.id != exclude_member_id]

    if not vendors:
        logger.warning(f"No vendor members found for vendor_id {vendor_id}.")
        await _escalate_no_vendor(job, 1, "No vendor members in this vendor account.")
        return None

    # Drone weather gate: if unsafe, block dispatch entirely — the rule-based fallback
    # has no weather awareness, so we must short-circuit before the retry loop.
    if _is_drone_job(job) and job.lat and job.lng and job.scheduled_date:
        try:
            from ..shared.job_timezone import resolve_job_iana
            _weather = await _get_weather_context(
                job.lat, job.lng, job.scheduled_date, resolve_job_iana(job_doc)
            )
            if _weather and _weather.get("drone_unsafe"):
                logger.warning(
                    f"Job {job_id}: drone_unsafe weather ({_weather.get('condition')}, "
                    f"wind={_weather.get('max_wind_kmh')}km/h) — blocking all assignment attempts"
                )
                await _escalate_no_vendor(job, 0, "drone weather unsafe — dispatch blocked")
                return None
        except Exception as _we:
            logger.error(f"Job {job_id}: weather check failed for drone job ({_we}) — blocking dispatch (fail-closed)")
            await _escalate_no_vendor(job, 0, f"drone weather check failed — dispatch blocked: {_we}")
            return None

    duration_min = estimate_shoot_duration_minutes(job)
    occupied_by_vendor = await load_occupied_jobs_by_vendor(
        db, [v.id for v in vendors], scheduled_date=job.scheduled_date or None
    )

    # Calendar FreeBusy hard gate (busy=True excludes; None/False continue)
    try:
        from .vendor_calendars import vendor_is_busy
        bounds = _slot_bounds(job, duration_min)
        if bounds:
            filtered = []
            for v in vendors:
                busy = await vendor_is_busy(v, bounds[0], bounds[1], db=db)
                if busy is True:
                    logger.info("Vendor %s excluded: calendar busy for job %s", v.id, job_id)
                    continue
                filtered.append(v)
            vendors = filtered
    except Exception as e:
        logger.debug("Calendar busy gate skipped: %s", e)

    min_tier = min_tier_for_job(job)
    eligible_ids = {v.id for v in filter_assignable_vendors(job, vendors, occupied_by_vendor)}

    actual_attempts = 0
    for attempt in range(1, _MAX_ASSIGNMENT_ATTEMPTS + 1):
        actual_attempts = attempt
        _had_exception = False
        try:
            chosen_id = await bricks_smart_assign(job, vendors, occupied_by_vendor=occupied_by_vendor)
            if chosen_id and chosen_id in eligible_ids:
                logger.info(f"Job {job_id} assigned via Bricks AI to {chosen_id} (attempt {attempt})")
                await _update_job_assignee(job_id, chosen_id, estimated_duration_min=duration_min)
                return chosen_id
            if chosen_id:
                logger.warning(
                    f"Bricks AI suggested vendor {chosen_id} which is ineligible "
                    f"(not found or below min_tier={min_tier}). Falling back (attempt {attempt})."
                )
        except Exception:
            _had_exception = True
            logger.exception(f"Bricks AI assignment failed for job {job_id} (attempt {attempt})")

        chosen_id = pick_vendor_rule_based(job, vendors, occupied_by_vendor=occupied_by_vendor)
        if chosen_id is not None:
            logger.info(f"Job {job.id} assigned via rule-based fallback to {chosen_id} (attempt {attempt})")
            await _update_job_assignee(job_id, chosen_id, estimated_duration_min=duration_min)
            return chosen_id

        logger.warning(f"No vendor assigned for job {job_id} (attempt {attempt}/{_MAX_ASSIGNMENT_ATTEMPTS}).")
        if not _had_exception:
            # Both scorers are deterministic — retrying identical inputs won't change the result.
            # Only loop again if a transient exception occurred in bricks_smart_assign.
            break

    await db.table("jobs").update({"status": "unassigned", "assignee_id": "", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", job_id).execute()
    await _escalate_no_vendor(job, actual_attempts)
    return None
