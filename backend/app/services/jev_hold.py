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


def yes_probability(payload: Any, question_key: str = HOLD_QUESTION_KEY) -> Optional[float]:
    """Read answers.<question>.noul. None when the body is not a usable noul."""
    if not isinstance(payload, dict):
        return None
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        return None
    answer = answers.get(question_key)
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
                        "A person should review this booking before anyone is assigned "
                        "because the request is contradictory or otherwise a poor fit for "
                        "automatic assignment. Do not answer true only because service, "
                        "date, or address is blank, because lat/lng are missing, or because "
                        "no photographer has been scored yet."
                    ),
                    "false": (
                        "Auto-assign with the existing rules. Paid bookings already have "
                        "service, date, and a street address; blanks in those fields are "
                        "not a hold by themselves. Missing lat/lng is not a hold. Vendor "
                        "match quality labeled unscored means no photographer has been "
                        "scored yet, and that alone is not a reason to hold."
                    ),
                },
            }
        },
    }


SCORE_INSTRUCTIONS = "Is this photographer a poor fit for automatic assignment to this booking?"


def apply_jev_fit(base_score: float, poor_fit: Optional[float]) -> float:
    """Scale today's score by Jev fit. None leaves the score unchanged.

    poor_fit is a yes-probability that this photographer is a poor fit.
    Fit is 1 - that probability, so 0 poor-fit does not change the score.
    """
    if poor_fit is None:
        return base_score
    try:
        probability = float(poor_fit)
    except (TypeError, ValueError):
        return base_score
    probability = max(0.0, min(1.0, probability))
    return round(float(base_score) * (1.0 - probability), 3)


def _question_key_for_vendor(vendor_id: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in str(vendor_id))
    return f"poor_fit_{safe}"


def _vendor_dict(vendor: Any) -> dict:
    if isinstance(vendor, dict):
        return vendor
    if hasattr(vendor, "model_dump"):
        return vendor.model_dump()
    if hasattr(vendor, "dict"):
        return vendor.dict()
    return {}


def candidate_match_state(job: dict, vendors: list) -> dict:
    """Booking fields already on the job, plus each candidate's existing profile."""
    people = []
    for vendor in vendors:
        row = _vendor_dict(vendor)
        people.append({
            "id": row.get("id"),
            "tier": row.get("tier"),
            "specialty": row.get("specialty") or "",
            "skills": row.get("skills") or [],
            "home_city": row.get("home_city") or "",
            "service_areas": row.get("service_areas") or [],
            "on_time_rate": row.get("on_time_rate"),
            "quality_rating": row.get("quality_rating"),
            "active_assignments": row.get("active_assignments"),
        })
    return {
        "booking": booking_decision_state(job),
        "candidates": people,
    }


def _score_body(job: dict, vendors: list) -> tuple[dict, dict]:
    keys = {}
    questions = {}
    for vendor in vendors:
        row = _vendor_dict(vendor)
        vendor_id = row.get("id")
        if not vendor_id:
            continue
        key = _question_key_for_vendor(str(vendor_id))
        keys[key] = str(vendor_id)
        questions[key] = {
            "type": "noul",
            "instructions": SCORE_INSTRUCTIONS,
            "criteria": {
                "true": (
                    "This photographer is a poor fit for automatic assignment to this "
                    "booking given their tier, skills, and location versus the booking's "
                    "service and address."
                ),
                "false": (
                    "Acceptable to auto-assign under the existing rules. Missing lat/lng "
                    "is not a poor fit. A photographer who has not been scored yet is not "
                    "a poor fit by itself. Blank service, date, or address is not a poor "
                    "fit by itself."
                ),
            },
        }
    body = {
        "model": JEV_MODEL,
        "state": candidate_match_state(job, vendors),
        "questions": questions,
    }
    return body, keys


def _openrouter_key() -> str:
    return (os.environ.get("OPENROUTER_API_KEY") or "").strip()


async def _post_decision(body: dict) -> Optional[dict]:
    """POST one Decisions call. None on missing key, timeout, HTTP error, or bad JSON."""
    api_key = _openrouter_key()
    if not api_key:
        return None
    async with httpx.AsyncClient(timeout=JEV_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            DECISIONS_URL,
            json=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
    if resp.status_code != 200:
        return None
    payload = resp.json()
    if not isinstance(payload, dict):
        return None
    return payload


async def jev_candidate_poor_fits(job: dict, vendors: list) -> dict:
    """Vendor id -> poor-fit yes-probability.

    Empty dict on missing key, timeout, HTTP error, or a body with no usable
    noul, so today's scores stay unchanged. A candidate with no noul is omitted.
    """
    vendors = list(vendors or [])
    if not vendors:
        return {}
    if not _openrouter_key():
        logger.info("OPENROUTER_API_KEY unset — Jev score skipped, ranking unchanged")
        return {}
    booking_id = (job or {}).get("booking_id") or (job or {}).get("id")
    try:
        body, keys = _score_body(job, vendors)
        if not keys:
            return {}
        payload = await _post_decision(body)
    except Exception as exc:
        logger.warning(
            "Jev score failed open for booking %s (%s) — ranking unchanged",
            booking_id,
            type(exc).__name__,
        )
        return {}
    if payload is None:
        logger.warning(
            "Jev score returned no decision for booking %s — ranking unchanged",
            booking_id,
        )
        return {}
    found = {}
    for key, vendor_id in keys.items():
        probability = yes_probability(payload, key)
        if probability is None:
            continue
        found[vendor_id] = probability
    if not found:
        logger.warning(
            "Jev score returned no noul for booking %s — ranking unchanged",
            booking_id,
        )
        return {}
    return found


async def jev_hold_decision(job: dict) -> JevHoldDecision:
    """Ask Jev whether to hold. Fail-open: only a yes-probability above 0.7 holds."""
    api_key = _openrouter_key()
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


APPLICANT_QUESTION_KEY = "poor_fit"
APPLICANT_INSTRUCTIONS = "Does this applicant look like a poor fit for Bricks photographer work?"
# A factor at or below this is not a reason the applicant looks weak.
_REASON_POOR_FIT = 0.5

# Same decision call. Plain sentences only. No name and no number in the text.
# Equipment only. This model sees the applicant's typed fields, never the
# portfolio itself, so it must not produce a portfolio, city, or drone "fit"
# line. Those bullets come from vendor_selection.vendor_application_facts
# (a real link fetch plus self-reported wording).
_APPLICANT_REASONS = (
    (
        "equipment",
        "Does the listed equipment look like a poor fit for Bricks photographer work?",
        "Listed equipment may not suit this work.",
    ),
)


@dataclass(frozen=True)
class ApplicantFitDecision:
    yes_probability: Optional[float] = None
    reasons: tuple = ()


def applicant_decision_state(applicant: dict) -> dict:
    """Name, city, vendor type, equipment, portfolio, and drone already stored."""
    applicant = applicant or {}
    portfolio = applicant.get("portfolio") or {}
    if not isinstance(portfolio, dict):
        portfolio = {}
    return {
        "name": applicant.get("name") or "",
        "city": applicant.get("city") or "",
        "vendor_type": applicant.get("vendor_type") or "",
        "equipment": applicant.get("equipment") or [],
        "portfolio": {
            "links": portfolio.get("links") or [],
            "samples": portfolio.get("samples") or [],
        },
        "drone": bool(applicant.get("drone")),
    }


def _applicant_questions() -> dict:
    questions = {
        APPLICANT_QUESTION_KEY: {
            "type": "noul",
            "instructions": APPLICANT_INSTRUCTIONS,
            "criteria": {
                "true": (
                    "This applicant looks like a poor fit for Bricks photographer "
                    "work given their name, city, vendor type, equipment, portfolio, "
                    "and drone certification."
                ),
                "false": (
                    "This applicant does not look like a poor fit for Bricks "
                    "photographer work. A short field, a missing portfolio link, or "
                    "drone certification being false is not a poor fit by itself."
                ),
            },
        }
    }
    for key, instructions, _weak in _APPLICANT_REASONS:
        questions[key] = {
            "type": "noul",
            "instructions": instructions,
            "criteria": {
                "true": "This part of the application is a poor fit for Bricks photographer work.",
                "false": (
                    "This part is acceptable, or there is not enough there to call it a poor fit. "
                    "A blank field or drone certification being false is not a poor fit by itself."
                ),
            },
        }
    return questions


def _applicant_body(applicant: dict) -> dict:
    return {
        "model": JEV_MODEL,
        "state": applicant_decision_state(applicant),
        "questions": _applicant_questions(),
    }


def applicant_reason_lines(payload: Any, overall: float) -> tuple:
    """Weak-signal bullets only, and only when the overall call is weak.

    No "looks suitable" line: a text model reading a typed list has not
    verified anything.
    """
    if overall <= 0.7:
        return ()
    lines = []
    for key, _instructions, weak_line in _APPLICANT_REASONS:
        probability = yes_probability(payload, key)
        if probability is not None and probability > _REASON_POOR_FIT:
            lines.append(weak_line)
    return tuple(lines)


async def jev_applicant_poor_fit(applicant: dict) -> ApplicantFitDecision:
    """One decision: poor-fit score plus plain reason lines.

    Missing key, timeout, HTTP error, or no overall score returns no score and
    no reasons. Never approves or rejects. The caller still notifies.
    """
    if not _openrouter_key():
        logger.info("OPENROUTER_API_KEY unset — Jev applicant score skipped")
        return ApplicantFitDecision()
    try:
        payload = await _post_decision(_applicant_body(applicant))
    except Exception as exc:
        logger.warning(
            "Jev applicant score failed open (%s) — application still notifies",
            type(exc).__name__,
        )
        return ApplicantFitDecision()
    if payload is None:
        logger.warning("Jev applicant score returned no decision — application still notifies")
        return ApplicantFitDecision()
    probability = yes_probability(payload, APPLICANT_QUESTION_KEY)
    if probability is None:
        logger.warning("Jev applicant score returned no noul — application still notifies")
        return ApplicantFitDecision()
    reasons = applicant_reason_lines(payload, probability)
    logger.info("Jev applicant score yes_probability=%.3f reasons=%s", probability, len(reasons))
    return ApplicantFitDecision(yes_probability=probability, reasons=reasons)
