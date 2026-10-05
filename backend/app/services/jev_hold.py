"""Jev hold gate for booking auto-assign.

One noul question on OpenRouter's Decisions API, asked before the existing
auto-assign runs. A yes-probability above 0.7 holds the booking for a person.
A missing key, timeout, or any other failure returns hold=False so today's
auto-assign still runs. This gate must not be able to take a booking down.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from .vendor_profile import _LOCATION_PLACEHOLDERS

logger = logging.getLogger(__name__)

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL = "typesafe/jev-1.13"
HOLD_QUESTION_KEY = "hold_for_human"
HOLD_INSTRUCTIONS = "Should this booking be held for a human instead of auto-assigned?"
HOLD_YES_THRESHOLD = 0.7
# Short on purpose. A hung Decisions call falls open into auto-assign.
JEV_TIMEOUT_SECONDS = 4.0

# Fields the intake already has. Blank ones are reported to Jev; they are not
# a local hold rule. lat/lng are often absent on Ontario bookings and the
# assigner already fail-opens on that — Jev is told the same in the criteria.
_MISSING_FIELD_KEYS = (
    "job_type",
    "address",
    "city",
    "scheduled_date",
    "time_window_start",
    "lat",
    "lng",
    "package_name",
)


@dataclass(frozen=True)
class JevHoldDecision:
    hold: bool
    yes_probability: Optional[float] = None


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _missing_location_text(value: Any) -> bool:
    """Same placeholders auto-assign already treats as 'not a location'."""
    if _blank(value):
        return True
    return isinstance(value, str) and value.strip().lower() in _LOCATION_PLACEHOLDERS


def vendor_match_quality(job: dict) -> dict:
    """Match signal already on the booking. Not a new scoring rule.

    Dispatch scores photographers inside auto-assign, which has not run yet.
    ``unscored`` means no vendor tier was sent with the booking, not that the
    match is poor.
    """
    vendor_tier = job.get("vendor_tier")
    min_tier = job.get("min_tier_required")
    preferred = job.get("preferred_tier")
    label = "unscored"
    try:
        if vendor_tier is not None and min_tier is not None and int(vendor_tier) < int(min_tier):
            label = "below_min_tier"
        elif vendor_tier is not None and preferred is not None and int(vendor_tier) >= int(preferred):
            label = "meets_preferred_tier"
        elif vendor_tier is not None and min_tier is not None and int(vendor_tier) >= int(min_tier):
            label = "meets_min_tier"
        elif vendor_tier is not None:
            label = "tier_present"
    except (TypeError, ValueError):
        label = "unscored"
    return {
        "vendor_tier": vendor_tier,
        "min_tier_required": min_tier,
        "preferred_tier": preferred,
        "label": label,
    }


def booking_decision_state(job: dict) -> dict:
    """Service, location, missing fields, and vendor match quality already on the job."""
    job = job or {}
    missing = []
    for key in _MISSING_FIELD_KEYS:
        value = job.get(key)
        blank = _missing_location_text(value) if key in ("address", "city") else _blank(value)
        if blank:
            missing.append(key)
    return {
        "service": {
            "job_type": job.get("job_type") or "",
            "package_name": job.get("package_name") or "",
            "package_id": job.get("package_id") or "",
        },
        "location": {
            "address": job.get("address") or "",
            "city": job.get("city") or "",
            "state": job.get("state") or "",
            "zip_code": job.get("zip_code") or "",
            "lat": job.get("lat"),
            "lng": job.get("lng"),
        },
        "missing_fields": missing,
        "vendor_match_quality": vendor_match_quality(job),
    }


def yes_probability(payload: Any) -> Optional[float]:
    """Read answers.<question>.noul. None when the body is not a usable noul."""
    if not isinstance(payload, dict):
        return None
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        return None
    answer = answers.get(HOLD_QUESTION_KEY)
    if not isinstance(answer, dict) or "noul" not in answer:
        return None
    try:
        return float(answer["noul"])
    except (TypeError, ValueError):
        return None


def _decision_body(job: dict) -> dict:
    return {
        "model": JEV_MODEL,
        "state": booking_decision_state(job),
        "questions": {
            HOLD_QUESTION_KEY: {
                "type": "noul",
                "instructions": HOLD_INSTRUCTIONS,
                "criteria": {
                    "true": (
                        "A person should review this booking before anyone is assigned. "
                        "The service, location, missing fields, or vendor match quality "
                        "is incomplete, contradictory, or a poor fit for automatic assignment."
                    ),
                    "false": (
                        "Auto-assign with the existing rules. A city or street address is "
                        "enough location even when lat/lng are missing. Vendor match quality "
                        "labeled unscored means no photographer has been scored yet, and that "
                        "alone is not a reason to hold."
                    ),
                },
            }
        },
    }


async def jev_hold_decision(job: dict) -> JevHoldDecision:
    """Ask Jev whether to hold. Fail-open: only a yes-probability above 0.7 holds."""
    api_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not api_key:
        logger.info("OPENROUTER_API_KEY unset — Jev hold gate skipped, auto-assign continues")
        return JevHoldDecision(hold=False)

    booking_id = (job or {}).get("booking_id")
    try:
        async with httpx.AsyncClient(timeout=JEV_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                DECISIONS_URL,
                json=_decision_body(job),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code != 200:
            logger.warning(
                "Jev hold gate HTTP %s for booking %s — auto-assign continues",
                resp.status_code,
                booking_id,
            )
            return JevHoldDecision(hold=False)
        probability = yes_probability(resp.json())
    except Exception as exc:
        logger.warning(
            "Jev hold gate failed open for booking %s (%s) — auto-assign continues",
            booking_id,
            type(exc).__name__,
        )
        return JevHoldDecision(hold=False)

    if probability is None:
        logger.warning(
            "Jev hold gate returned no noul for booking %s — auto-assign continues",
            booking_id,
        )
        return JevHoldDecision(hold=False)

    hold = probability > HOLD_YES_THRESHOLD
    logger.info(
        "Jev hold gate booking %s yes_probability=%.3f hold=%s",
        booking_id,
        probability,
        hold,
    )
    return JevHoldDecision(hold=hold, yes_probability=probability)
