import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from ..shared.schemas import Job, VendorMember

logger = logging.getLogger(__name__)

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent"
)

_OUTDOOR_JOB_TYPES = {"drone", "exterior", "video", "twilight"}


async def call_bricks_ai_api(prompt: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            _GEMINI_URL,
            params={"key": api_key},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


async def _get_weather_context(lat: float, lng: float, scheduled_date: str) -> Optional[dict]:
    """
    Fetch hourly forecast from Open-Meteo (free, no key required) for the job date.
    Returns a brief weather summary dict, or None on failure.
    WMO weather codes: 0=clear, 45-48=fog, 51-67=rain, 71-77=snow, 80-82=showers, 95-99=thunder.
    """
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lng,
                    "hourly": "weathercode,windspeed_10m,precipitation_probability",
                    "timezone": "America/Toronto",
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

        # Find hours on the scheduled date (9:00–17:00 window)
        relevant_codes, relevant_winds, relevant_precip = [], [], []
        for i, t in enumerate(times):
            if t.startswith(scheduled_date) and "T" in t:
                hour = int(t.split("T")[1].split(":")[0])
                if 9 <= hour <= 17:
                    if i < len(codes):
                        relevant_codes.append(codes[i])
                    if i < len(winds):
                        relevant_winds.append(winds[i])
                    if i < len(precip):
                        relevant_precip.append(precip[i])

        if not relevant_codes:
            return None

        max_code = max(relevant_codes)
        max_wind = max(relevant_winds) if relevant_winds else 0
        max_precip_pct = max(relevant_precip) if relevant_precip else 0

        is_precipitation = max_code >= 51
        is_high_wind = max_wind > 30  # km/h threshold for drone ops

        condition = "clear"
        if max_code >= 95:
            condition = "thunderstorm"
        elif max_code >= 80:
            condition = "heavy_rain"
        elif max_code >= 61:
            condition = "rain"
        elif max_code >= 51:
            condition = "drizzle"
        elif max_code >= 45:
            condition = "fog"

        return {
            "condition": condition,
            "max_wind_kmh": round(max_wind, 1),
            "max_precip_pct": max_precip_pct,
            "drone_unsafe": is_precipitation or is_high_wind,
            "outdoor_caution": is_precipitation,
        }
    except Exception as e:
        logger.debug(f"Weather fetch failed (non-fatal): {e}")
        return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


async def bricks_smart_assign(job: Job, vendors: List[VendorMember]) -> Optional[str]:
    use_ai = os.environ.get("USE_AI_DISPATCH", "true").lower() == "true"
    api_key = os.environ.get("GEMINI_API_KEY")

    if not use_ai or not api_key:
        logger.info("Bricks AI dispatch disabled or GEMINI_API_KEY missing. Skipping AI path.")
        return None

    try:
        min_tier = job.min_tier_required or 1

        # Fetch weather context for outdoor/drone jobs
        weather_context = None
        is_outdoor_job = job.job_type in _OUTDOOR_JOB_TYPES
        if is_outdoor_job and job.lat and job.lng and job.scheduled_date:
            weather_context = await _get_weather_context(job.lat, job.lng, job.scheduled_date)

        vendor_data = []
        for v in vendors:
            if not (v.is_active and v.onboarding_status == "approved" and v.tier >= min_tier):
                continue

            travel_km: Optional[float] = None
            if v.home_lat and v.home_lng and job.lat and job.lng:
                travel_km = round(_haversine_km(v.home_lat, v.home_lng, job.lat, job.lng), 1)

            vendor_data.append({
                "id": v.id,
                "name": v.name,
                "specialty": v.specialty,
                "tier": v.tier,
                "operation_polygon": [c.dict() for c in v.operation_polygon],
                "home_city": v.home_city,
                "max_travel_km": v.max_travel_km,
                "travel_km_to_job": travel_km,
                "service_areas": v.service_areas,
                "completion_rate": v.completion_rate,
                "on_time_rate": v.on_time_rate,
                "avg_turnaround_days": v.avg_turnaround_days,
                "quality_rating": v.quality_rating,
                "active_assignments": v.active_assignments,
            })

        if not vendor_data:
            logger.warning(f"No eligible vendors (min_tier={min_tier}) to pass to AI for job {job.id}")
            return None

        job_context = {
            "address": job.address,
            "city": job.city,
            "lat": job.lat,
            "lng": job.lng,
            "job_type": job.job_type,
            "priority": job.priority,
            "property_details": job.property_details,
            "client_instructions": job.client_instructions,
            "scheduled_date": job.scheduled_date,
            "min_tier_required": min_tier,
            "preferred_tier": job.preferred_tier or min_tier,
            "is_outdoor": is_outdoor_job,
            "weather": weather_context,
        }

        weather_guidance = ""
        if weather_context and weather_context.get("drone_unsafe") and job.job_type == "drone":
            weather_guidance = (
                "\nWEATHER ALERT: Drone conditions on the shoot date are unsafe "
                f"({weather_context['condition']}, wind {weather_context['max_wind_kmh']} km/h). "
                "Note this in your reasoning but still select the best available candidate — "
                "rescheduling is handled separately."
            )
        elif weather_context and weather_context.get("outdoor_caution"):
            weather_guidance = (
                f"\nWEATHER NOTE: Forecast shows {weather_context['condition']} on shoot date. "
                "Consider this when evaluating candidate reliability."
            )

        prompt = f"""
You are the Bricks Smart Dispatch Assistant for Ontario, Canada.
Assign the best photographer for the following real estate shoot:{weather_guidance}

Job: {json.dumps(job_context, indent=2)}

Candidates: {json.dumps(vendor_data, indent=2)}

Rules (apply in priority order):
1. All candidates already meet the minimum tier requirement — never select below min_tier_required.
2. Area of Operations: Prioritize candidates whose "operation_polygon" contains the job's lat/lng.
3. Geography: If no polygon match, prefer candidates whose "travel_km_to_job" is within "max_travel_km", or whose "service_areas" includes the job city.
4. Specialty match: Prefer "specialty" matching job_type (photo, video, drone, 3d_tour, floor_plan).
5. Tier preference: Prefer "tier" matching preferred_tier when other factors are equal.
6. Reliability: Prefer higher "on_time_rate" and "completion_rate". High on_time_rate is critical for time-sensitive luxury jobs.
7. Quality: Prefer higher "quality_rating".
8. Load balance: Prefer fewer "active_assignments".

Return ONLY a JSON object with the "vendor_id" of the best match.
Example: {{"vendor_id": "vendor_abc"}}
"""

        response_text = await call_bricks_ai_api(prompt)
        result = json.loads(response_text)

        vendor_id = result.get("vendor_id")
        if vendor_id:
            logger.info(f"Bricks AI suggested vendor_id: {vendor_id}")
            return vendor_id

    except Exception:
        logger.exception("Error in Bricks AI smart_assign")

    return None
