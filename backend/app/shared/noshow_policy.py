"""Client no-show rules (Kam, 2026-10-30). One place for every number.

A client no-show counts only when the vendor:
  1. checked in through the app, near the property (CHECKIN_RADIUS_M),
  2. waited WAIT_MIN minutes, counted from the LATER of check-in and the booked start,
  3. took a photo on site (uploaded after check-in), and
  4. tried to call the client (in-app "Call client" dialer, or the Twilio SMS path).

Bricks reviews each report within 2 business days; the vendor can appeal to
help@brxs.ca within 7 days. An approved no-show pays half the normal pre-HST
payout (payout_service.noshow_payout_cents).

CHECKIN_RADIUS_M is the single check-in radius. Change it with the
NOSHOW_CHECKIN_RADIUS_M env var on the Dispatch API; the go and Expo apps read it
from GET /api/vendor-os/jobs/{id}/noshow-status and never hard-code it.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from .geospatial import haversine_km
from .job_timezone import zoneinfo_for_job


def _int_env(name: str, default: int) -> int:
    try:
        value = int(str(os.environ.get(name, "")).strip() or default)
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


CHECKIN_RADIUS_M: int = _int_env("NOSHOW_CHECKIN_RADIUS_M", 100)  # Kam 2026-11-02: 100 m geofence
WAIT_MIN: int = 20
# A report must be filed within this many minutes of the wait starting.
CLAIM_WINDOW_AFTER_MIN: int = 90
EVIDENCE_BUCKET: str = os.environ.get("NOSHOW_EVIDENCE_BUCKET", "noshow-evidence")
PHOTO_MAX_BYTES: int = 4 * 1024 * 1024  # Vercel request body cap is 4.5 MB
# dialer: in-app Call client opened the phone dialer. sms: Twilio text sent.
# no_number: Call client tapped but Bricks has no number for the client.
CALL_METHODS = ("dialer", "sms", "no_number")


def parse_ts(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def booked_start(job: dict) -> Optional[datetime]:
    """Booked start in UTC: scheduled_date + time_window_start in the property zone."""
    date_s = str(job.get("scheduled_date") or "").strip()[:10]
    time_s = str(job.get("time_window_start") or "").strip()[:5]
    if date_s and time_s:
        try:
            local = datetime.fromisoformat(f"{date_s}T{time_s}:00").replace(tzinfo=zoneinfo_for_job(job))
            return local.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None
    return parse_ts(job.get("scheduled_at"))


def checkin_distance_m(job: dict, lat: Optional[float], lng: Optional[float]) -> Optional[float]:
    """Metres from the job's geocoded address, or None when either point is unknown."""
    if lat is None or lng is None:
        return None
    try:
        jlat, jlng = job.get("lat"), job.get("lng")
        if jlat is None or jlng is None:
            return None
        return round(haversine_km(float(lat), float(lng), float(jlat), float(jlng)) * 1000.0, 1)
    except (TypeError, ValueError):
        return None


def job_has_coords(job: dict) -> bool:
    try:
        return job.get("lat") is not None and job.get("lng") is not None and (
            float(job["lat"]) != 0.0 or float(job["lng"]) != 0.0
        )
    except (TypeError, ValueError):
        return False


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def readiness(job: dict, now: Optional[datetime] = None) -> dict:
    """Where the vendor stands on the four no-show conditions.

    `missing` lists plain sentences for what still blocks a report, in order.
    """
    now = now or datetime.now(timezone.utc)
    checked_in_at = parse_ts(job.get("checked_in_at"))
    start = booked_start(job)
    wait_started_at = None
    ready_at = None
    expires_at = None
    if checked_in_at:
        wait_started_at = max(checked_in_at, start) if start else checked_in_at
        ready_at = wait_started_at + timedelta(minutes=WAIT_MIN)
        expires_at = wait_started_at + timedelta(minutes=CLAIM_WINDOW_AFTER_MIN)

    photo_uploaded_at = parse_ts(job.get("noshow_photo_uploaded_at"))
    has_photo = bool(job.get("noshow_photo_path")) and bool(
        checked_in_at and photo_uploaded_at and photo_uploaded_at >= checked_in_at
    )
    call_at = parse_ts(job.get("noshow_call_attempted_at"))
    has_call = bool(checked_in_at and call_at and call_at >= checked_in_at)
    waited = bool(ready_at and now >= ready_at)
    expired = bool(expires_at and now > expires_at)

    missing: list[str] = []
    if not checked_in_at:
        missing.append("Check in at the property first.")
    elif not waited:
        mins = max(1, int(((ready_at - now).total_seconds() + 59) // 60))
        missing.append(f"Wait {mins} more min. The {WAIT_MIN}-minute wait starts at check-in or the booked start, whichever is later.")
    if not has_photo:
        missing.append("Take a photo of the property.")
    if not has_call:
        missing.append("Tap Call client to try reaching them.")
    if expired:
        missing = [f"It's been more than {CLAIM_WINDOW_AFTER_MIN} min since your wait started, so this can't be reported in the app. Email help@brxs.ca."]

    return {
        "checked_in": bool(checked_in_at),
        "checked_in_at": _iso(checked_in_at),
        "booked_start_at": _iso(start),
        "wait_started_at": _iso(wait_started_at),
        "ready_at": _iso(ready_at),
        "expires_at": _iso(expires_at),
        "waited": waited,
        "has_photo": has_photo,
        "has_call": has_call,
        "expired": expired,
        "can_report": bool(checked_in_at and waited and has_photo and has_call and not expired),
        "missing": missing,
        "wait_minutes": WAIT_MIN,
        "checkin_radius_m": CHECKIN_RADIUS_M,
        "check_in_distance_m": job.get("check_in_distance_m"),
        "call_method": job.get("noshow_call_method"),
        "photo_uploaded_at": _iso(photo_uploaded_at),
        "now": _iso(now),
    }
