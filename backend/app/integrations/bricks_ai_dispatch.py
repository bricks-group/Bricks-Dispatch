import json
import logging
import os
from typing import List, Optional

import httpx
from ..shared.schemas import Job, VendorMember

logger = logging.getLogger(__name__)

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent"
)


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


async def bricks_smart_assign(job: Job, vendors: List[VendorMember]) -> Optional[str]:
    use_ai = os.environ.get("USE_AI_DISPATCH", "true").lower() == "true"
    api_key = os.environ.get("GEMINI_API_KEY")

    if not use_ai or not api_key:
        logger.info("Bricks AI dispatch disabled or GEMINI_API_KEY missing. Skipping AI path.")
        return None

    try:
        vendor_data = [
            {
                "id": v.id,
                "name": v.name,
                "specialty": v.specialty,
                "operation_polygon": [c.dict() for c in v.operation_polygon],
                "home_city": v.home_city,
                "max_travel_km": v.max_travel_km,
                "service_areas": v.service_areas,
                "completion_rate": v.completion_rate,
                "avg_turnaround_days": v.avg_turnaround_days,
                "quality_rating": v.quality_rating,
                "active_assignments": v.active_assignments,
            }
            for v in vendors
            if v.is_active and v.onboarding_status == "approved"
        ]

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
        }

        prompt = f"""
        You are the Bricks Smart Dispatch Assistant for Ontario.
        Assign the best photographer for the following job:
        Job: {json.dumps(job_context)}

        Candidates: {json.dumps(vendor_data)}

        Rules:
        1. Area of Operations (AO): Prioritize candidates whose "operation_polygon" contains the job's lat/lng.
        2. Geographical Match: If no polygon match, check if job's "city" is in their "service_areas" or within "max_travel_km" radius.
        3. Match specialty to job_type (photo, video, drone, 3d_tour, floor_plan).
        4. Prefer high quality_rating and completion_rate.
        5. Balance load: prefer fewer active_assignments.

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
