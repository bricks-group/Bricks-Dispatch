"""
Bricks Smart Dispatch — deterministic weighted scorer with optional Gemini explainability.

PRIMARY path: scoring function (fast, auditable, no LLM latency on the hot path).
SECONDARY path: Gemini 1.5 Flash generates a "why this photographer" explanation
  string for the admin dashboard — called async after routing is done, never blocks.

Scoring formula (max 100 points):
  specialty_match (30) + tier_match (10) + proximity (20) + on_time (15) +
  quality (10) + reliability (10) + load_balance (5)

Repeat-agent bonus: +5 points if vendor has previously completed a job for this agent.
Drone gate: vendor must have drone_certified=True for drone job_type.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from ..shared.schemas import Job, VendorMember
from ..shared.geospatial import haversine_km as _haversine_km
from ..services.vendor_profile import member_eligible_for_job, travel_within_cap
from ..services.shoot_duration import previous_job_coords, vendor_slot_conflicts

logger = logging.getLogger(__name__)

# Module-level set keeps a strong reference to fire-and-forget tasks so the garbage
# collector cannot cancel them before they complete (per Python asyncio docs).
_background_tasks: set = set()

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent"
)

_OUTDOOR_JOB_TYPES = {"drone", "exterior", "twilight"}

# Scoring weights — must sum to 100
_W = {
    "specialty": 30,
    "tier":      10,
    "proximity": 20,
    "on_time":   15,
    "quality":   10,
    "reliability": 10,
    "load":      5,
}
_REPEAT_AGENT_BONUS       = 5   # added on top; doesn't affect weight sum
_BROKERAGE_FAVORITE_BONUS = 15  # brokerage has marked this photographer as a favourite
_MAX_DAILY_CAP            = 4   # default if availability not queried
_DRONE_MAX_WIND_KMH = 25  # above this → drone_unsafe flag in job context

# IC agreement Clause 2: decline rate below this threshold has zero score effect.
# Only the excess above 40% over a rolling 30-day period reduces reliability.
_DECLINE_NEUTRAL_THRESHOLD = 0.40


# ---------------------------------------------------------------------------
# Weather helper (Open-Meteo — free, no key required)
# ---------------------------------------------------------------------------

async def _get_weather_context(
    lat: float, lng: float, scheduled_date: str, timezone_name: str | None = None
) -> Optional[dict]:
    from ..shared.job_timezone import valid_iana, DEFAULT_IANA
    tz = valid_iana(timezone_name) or DEFAULT_IANA
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lng,
                    "hourly": "weathercode,windspeed_10m,precipitation_probability",
                    "timezone": tz,
                    "forecast_days": 7,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        codes = hourly.get("weathercode", [])
        winds = hourly.get("windspeed_10m", [])
        precip = hourly.get("precipitation_probability", [])

        r_codes, r_winds, r_precip = [], [], []
        for i, t in enumerate(times):
            if t.startswith(scheduled_date) and "T" in t:
                hour = int(t.split("T")[1].split(":")[0])
                if 9 <= hour <= 17:
                    if i < len(codes):   r_codes.append(codes[i])
                    if i < len(winds):   r_winds.append(winds[i])
                    if i < len(precip):  r_precip.append(precip[i])

        if not r_codes:
            return None

        max_code = max(r_codes)
        max_wind = max(r_winds) if r_winds else 0

        condition = "clear"
        if max_code >= 95:   condition = "thunderstorm"
        elif max_code >= 80: condition = "heavy_rain"
        elif max_code >= 61: condition = "rain"
        elif max_code >= 51: condition = "drizzle"
        elif max_code >= 45: condition = "fog"

        return {
            "condition": condition,
            "max_wind_kmh": round(max_wind, 1),
            "max_precip_pct": max(r_precip) if r_precip else 0,
            "drone_unsafe": max_code >= 51 or max_wind > _DRONE_MAX_WIND_KMH,
            "outdoor_caution": max_code >= 51,
        }
    except Exception as e:
        logger.debug(f"Weather fetch failed (non-fatal): {e}")
        return None


# ---------------------------------------------------------------------------
# Core deterministic scoring
# ---------------------------------------------------------------------------

def _score_vendor(
    v: VendorMember,
    job: Job,
    preferred_tier: int,
    has_repeat_history: bool = False,
    is_brokerage_favorite: bool = False,
    return_breakdown: bool = False,
):
    """
    Compute a 0-100 score for a vendor candidate.
    Higher is better. Tie-break: more active_assignments = worse.
    When return_breakdown=True, returns (score, breakdown_dict) instead of score.
    """
    # Specialty match: binary
    specialty = 1.0 if v.specialty == job.job_type else 0.0

    # Tier preference match: binary (already gated on min_tier upstream)
    tier = 1.0 if v.tier == preferred_tier else 0.0

    # Proximity: 1.0 at 0 km, 0.0 at 100+ km
    proximity = 0.5  # neutral default when coordinates unknown
    dist_km: Optional[float] = None
    if v.home_lat is not None and v.home_lng is not None and job.lat is not None and job.lng is not None:
        dist_km = _haversine_km(v.home_lat, v.home_lng, job.lat, job.lng)
        proximity = max(0.0, 1.0 - dist_km / 100.0)

    # On-time rate: already 0-1
    on_time = max(0.0, min(1.0, v.on_time_rate))

    # Quality: normalize 1-5 scale to 0-1
    quality = max(0.0, min(1.0, (v.quality_rating - 1.0) / 4.0))

    # Reliability: cancel_rate penalizes accepted-then-abandoned jobs.
    # Decline rate (refusing an offer before acceptance) is IC Clause 2 neutral
    # below 40%/30d — only the excess above the threshold reduces reliability.
    decline_penalty = max(0.0, (v.decline_rate or 0.0) - _DECLINE_NEUTRAL_THRESHOLD)
    reliability = max(0.0, min(1.0, 1.0 - v.cancel_rate - decline_penalty))

    # Load balance: inverse of fractional load (default cap = 4 jobs/day)
    load = max(0.0, 1.0 - v.active_assignments / _MAX_DAILY_CAP)

    specialty_pts    = round(_W["specialty"]   * specialty,   3)
    tier_pts         = round(_W["tier"]        * tier,        3)
    proximity_pts    = round(_W["proximity"]   * proximity,   3)
    on_time_pts      = round(_W["on_time"]     * on_time,     3)
    quality_pts      = round(_W["quality"]     * quality,     3)
    reliability_pts  = round(_W["reliability"] * reliability, 3)
    load_pts         = round(_W["load"]        * load,        3)

    bonus_total = 0.0
    if has_repeat_history:
        bonus_total += _REPEAT_AGENT_BONUS
    if is_brokerage_favorite:
        bonus_total += _BROKERAGE_FAVORITE_BONUS

    score = round(
        specialty_pts + tier_pts + proximity_pts + on_time_pts +
        quality_pts + reliability_pts + load_pts + bonus_total,
        3,
    )

    if not return_breakdown:
        return score

    breakdown = {
        "specialty":   specialty_pts,
        "tier":        tier_pts,
        "proximity":   proximity_pts,
        "on_time":     on_time_pts,
        "quality":     quality_pts,
        "reliability": reliability_pts,
        "load":        load_pts,
        "bonuses":     round(bonus_total, 3),
        "_dist_km":    round(dist_km, 1) if dist_km is not None else None,
        "_on_time_pct": round(v.on_time_rate * 100),
    }
    return score, breakdown


# ---------------------------------------------------------------------------
# Coded explanation fallback (deterministic — no LLM required)
# ---------------------------------------------------------------------------

_COMPONENT_LABELS = {
    "specialty":   "specialty match",
    "tier":        "tier match",
    "proximity":   "proximity",
    "on_time":     "on-time rate",
    "quality":     "quality rating",
    "reliability": "reliability",
    "load":        "availability",
    "bonuses":     "bonus",
}


def _coded_explanation(job: Job, vendor: VendorMember, score: float, breakdown: dict) -> str:
    scored_components = [
        (k, v) for k, v in breakdown.items()
        if not k.startswith("_") and v > 0
    ]
    scored_components.sort(key=lambda x: x[1], reverse=True)
    top3 = scored_components[:3]

    parts = []
    for key, pts in top3:
        label = _COMPONENT_LABELS.get(key, key)
        if key == "on_time" and breakdown.get("_on_time_pct") is not None:
            parts.append(f"{breakdown['_on_time_pct']}% {label} ({round(pts)}pts)")
        elif key == "proximity" and breakdown.get("_dist_km") is not None:
            parts.append(f"{breakdown['_dist_km']}km from property ({round(pts)}pts)")
        else:
            parts.append(f"{label} ({round(pts)}pts)")

    summary = ", ".join(parts)
    return f"Matched for {summary}. Total score: {round(score)}/100."


# ---------------------------------------------------------------------------
# Gemini explainability (non-blocking — called after routing is decided)
# ---------------------------------------------------------------------------

async def generate_dispatch_explanation(job: Job, vendor: VendorMember, score: float) -> Optional[str]:
    """
    Generate a one-sentence "why this photographer" string for the admin dashboard.
    Returns None if Gemini is unavailable — never blocks dispatch.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        prompt = f"""
In one sentence (max 25 words), explain why this photographer was chosen for this job.
Job: {job.job_type} at {job.city}, tier {job.min_tier_required}+ required, scheduled {job.scheduled_date}.
Photographer: {vendor.name}, tier {vendor.tier}, specialty {vendor.specialty},
  on_time_rate {vendor.on_time_rate:.0%}, quality {vendor.quality_rating}/5,
  active_assignments {vendor.active_assignments}, score {score:.1f}/100.
Return ONLY the explanation sentence as a plain JSON string.
"""
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                _GEMINI_URL,
                params={"key": api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json"},
                },
            )
            resp.raise_for_status()
            data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text) if text else None
    except Exception:
        return None


async def _save_dispatch_explanation(job: Job, vendor: VendorMember, score: float, breakdown: dict) -> None:
    """Fire-and-forget: generate Gemini explanation and persist to jobs table.
    Falls back to a deterministic coded explanation when Gemini is unavailable."""
    try:
        explanation = await generate_dispatch_explanation(job, vendor, score)
        if not explanation:
            logger.info(f"Using coded dispatch explanation (Gemini unavailable) for job {job.id}")
            explanation = _coded_explanation(job, vendor, score, breakdown)
        from ..shared.database import get_db
        db = await get_db()
        await db.table("jobs").update({"dispatch_explanation": explanation}).eq("id", job.id).execute()
        logger.info(f"Dispatch explanation saved for job {job.id}")
    except Exception as e:
        logger.debug(f"Dispatch explanation skipped for job {job.id}: {e}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _filter_with_occupied(
    job: Job,
    vendors: List[VendorMember],
    occupied_by_vendor: Optional[dict] = None,
) -> List[VendorMember]:
    """Same slot/travel filters as rule-based path (avoids circular import)."""
    occupied_by_vendor = occupied_by_vendor or {}
    out: List[VendorMember] = []
    for v in vendors:
        if not member_eligible_for_job(v, job):
            continue
        occupied = occupied_by_vendor.get(v.id) or []
        if vendor_slot_conflicts(job, occupied):
            continue
        origin = previous_job_coords(job, occupied)
        if origin is not None and travel_within_cap(v, job, origin=origin) is False:
            continue
        out.append(v)
    return out


async def bricks_smart_assign(
    job: Job,
    vendors: List[VendorMember],
    occupied_by_vendor: Optional[dict] = None,
) -> Optional[str]:
    """
    Select the best vendor for a job using deterministic weighted scoring.
    Drone jobs enforce drone_certified=True as a hard gate.
    Accepts occupied_by_vendor for same-day slot + travel chaining filters.
    Returns the chosen vendor member id, or None if no eligible candidates.
    """
    min_tier = job.min_tier_required or 1
    preferred_tier = job.preferred_tier or min_tier
    is_drone_job = job.job_type == "drone"

    # Fetch weather context for outdoor/drone jobs (non-blocking)
    weather_context = None
    is_outdoor = job.job_type in _OUTDOOR_JOB_TYPES
    if is_outdoor and job.lat and job.lng and job.scheduled_date:
        from ..shared.job_timezone import resolve_job_iana
        weather_context = await _get_weather_context(
            job.lat,
            job.lng,
            job.scheduled_date,
            resolve_job_iana({
                "timezone": getattr(job, "timezone", None),
                "state": getattr(job, "state", None),
                "address": getattr(job, "address", None),
                "zip_code": getattr(job, "zip_code", None),
            }),
        )
        if weather_context:
            job_ctx = f"weather={weather_context['condition']}, wind={weather_context['max_wind_kmh']}km/h"
            if weather_context.get("drone_unsafe") and is_drone_job:
                logger.warning(f"Job {job.id}: drone conditions unsafe on {job.scheduled_date} — {job_ctx} — blocking dispatch")
                return None
            elif weather_context.get("outdoor_caution"):
                logger.info(f"Job {job.id}: outdoor caution on {job.scheduled_date} — {job_ctx}")

    # Filter eligible candidates — prefer available but fall back to all active/approved
    # so the scorer behaves consistently with pick_vendor_rule_based rather than hard-blocking
    # on status and forcing every unavailable-pool job through the rule-based path.
    # Same slot/travel filters as rule-based when occupied_by_vendor is provided.
    eligible = _filter_with_occupied(job, vendors, occupied_by_vendor=occupied_by_vendor)
    candidates = [v for v in eligible if v.status == "available"] or eligible

    if not eligible:
        reason = "location/package/tier/skills/RPAS"
        logger.warning(f"Job {job.id}: no eligible vendors after {reason}")
        return None

    # Enrich with repeat-agent history (single DB call for all candidates)
    all_member_ids = [v.id for v in candidates]
    agent_user_id = getattr(job, "agent_user_id", None)
    brokerage_id  = getattr(job, "brokerage_id", None)
    has_history_by_vendor:   dict[str, bool] = {}
    is_brokerage_fav_vendor: dict[str, bool] = {}
    if agent_user_id or brokerage_id:
        try:
            from ..shared.database import get_db
            db = await get_db()
            if agent_user_id:
                res = await db.table("jobs").select("assignee_id").eq(
                    "agent_user_id", agent_user_id
                ).in_("assignee_id", all_member_ids[:50]).in_("status", ["completed", "delivered"]).execute()
                for row in (res.data or []):
                    has_history_by_vendor[row["assignee_id"]] = True

            if brokerage_id:
                fav_res = await db.table("brokerage_vendor_favorites").select("vendor_id").eq(
                    "brokerage_id", brokerage_id
                ).in_("vendor_id", all_member_ids[:50]).execute()
                for row in (fav_res.data or []):
                    is_brokerage_fav_vendor[row["vendor_id"]] = True
        except Exception:
            pass

    # Score all candidates
    scored = []
    for v in candidates:
        score, breakdown = _score_vendor(
            v, job, preferred_tier,
            has_repeat_history=has_history_by_vendor.get(v.id, False),
            is_brokerage_favorite=is_brokerage_fav_vendor.get(v.id, False),
            return_breakdown=True,
        )
        scored.append((score, v.id, v, breakdown))

    # Jev fit scales the existing score. A missing key, timeout, HTTP error,
    # or body with no noul returns nothing and leaves these scores unchanged.
    try:
        from ..services.jev_hold import apply_jev_fit, jev_candidate_poor_fits
        job_state = job.model_dump() if hasattr(job, "model_dump") else job.dict()
        poor_fits = await jev_candidate_poor_fits(job_state, candidates)
    except Exception:
        logger.warning("Jev score skipped for job %s — ranking unchanged", job.id)
        poor_fits = {}
    if poor_fits:
        adjusted = []
        for score, vendor_id, vendor, breakdown in scored:
            poor_fit = poor_fits.get(vendor_id)
            new_score = apply_jev_fit(score, poor_fit)
            if poor_fit is not None:
                breakdown = dict(breakdown)
                breakdown["_jev_poor_fit"] = round(float(poor_fit), 3)
            adjusted.append((new_score, vendor_id, vendor, breakdown))
        scored = adjusted

    if not scored:
        return None

    # Sort: highest score first; stable UUID tiebreak avoids double-counting active_assignments
    # (already factored into the load_balance score component — no need to repeat it here)
    scored.sort(key=lambda x: (-x[0], x[1]))
    best_score, best_id, best_vendor, best_breakdown = scored[0]

    logger.info(
        f"Dispatch scored {len(scored)} candidates for job {job.id}: "
        f"chose {best_vendor.name} (id={best_id}, score={best_score:.1f}, "
        f"tier={best_vendor.tier}, on_time={best_vendor.on_time_rate:.0%})"
    )

    # Gemini explainability — fire-and-forget; never blocks dispatch.
    # Store task reference to prevent garbage collection mid-execution (Python asyncio docs).
    _task = asyncio.create_task(_save_dispatch_explanation(job, best_vendor, best_score, best_breakdown))
    _background_tasks.add(_task)
    _task.add_done_callback(_background_tasks.discard)

    return best_id
