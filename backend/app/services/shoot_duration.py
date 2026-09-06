"""Estimate on-site shoot duration and detect same-day slot/travel conflicts.

One booked clock, one photographer, full package in one visit — duration is
the continuous block from arrive to leave used by auto-assign.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional


def _as_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return getattr(obj, "__dict__", {}) or {}


def _addon_keys(add_ons: Iterable) -> set[str]:
    keys: set[str] = set()
    for item in add_ons or []:
        if isinstance(item, dict):
            raw = item.get("key") or item.get("id") or item.get("name") or ""
            if item.get("included") is False or item.get("enabled") is False:
                continue
        else:
            raw = item
        keys.add(str(raw).strip().lower())
    return keys


def _photo_base_minutes(sqft: Optional[int]) -> int:
    """HDR stills base by footprint (minutes on site)."""
    if sqft is None:
        return 60
    try:
        n = int(sqft)
    except (TypeError, ValueError):
        return 60
    if n <= 1500:
        return 45
    if n <= 2500:
        return 60
    if n <= 4000:
        return 75
    if n <= 6000:
        return 90
    return 120


def estimate_shoot_duration_minutes(job: Any) -> int:
    """Total on-site minutes for the full package in one visit."""
    j = _as_dict(job)
    keys = _addon_keys(j.get("add_ons") or [])
    blob = (
        f"{j.get('package_id') or ''} {j.get('package_name') or ''} "
        f"{j.get('job_type') or ''} {' '.join(keys)}"
    ).lower()
    jt = str(j.get("job_type") or "photo").lower()
    sqft = j.get("sqft")

    # Standalone non-photo shoots still get a sensible block.
    if jt == "drone" and "photo" not in blob and not any(k in keys for k in ("photo", "photos", "hdr")):
        minutes = 40
    elif jt in ("reel", "video") and "photo" not in blob:
        minutes = 50
    elif jt == "twilight" and "photo" not in blob:
        minutes = 45
    elif jt in ("tour", "virtual", "3d_tour"):
        minutes = 55
    else:
        minutes = _photo_base_minutes(sqft if isinstance(sqft, int) or (isinstance(sqft, str) and str(sqft).isdigit()) else None)

    if "drone" in keys or jt == "drone" or "drone" in blob:
        minutes += 25
    if "reel" in keys or "reel" in blob or jt in ("reel", "video"):
        minutes += 30
    if "tour" in keys or "virtual" in blob or "3d" in blob or jt in ("tour", "virtual"):
        minutes += 35
    if "twilight" in keys or "twilight" in blob or jt == "twilight":
        # Same visit — twilight is captured after interiors, not a return trip.
        minutes += 35
    if "visit" in keys or "gaussian" in blob:
        minutes += 20
    if "floorplan" in keys or "floor plan" in blob or "floor_plan" in blob:
        minutes += 15

    # Rush / complexity soft bump
    if j.get("is_rush"):
        minutes += 10
    band = str(j.get("complexity_band") or "").lower()
    if band in ("high", "complex"):
        minutes += 15

    explicit = j.get("estimated_duration_min")
    if explicit is not None:
        try:
            return max(30, int(explicit))
        except (TypeError, ValueError):
            pass
    return max(30, min(int(minutes), 300))


def _parse_hhmm(raw: Any) -> Optional[tuple[int, int]]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # Accept HH:MM or ISO with T…HH:MM
    if "T" in text:
        text = text.split("T", 1)[1][:5]
    text = text[:5]
    if len(text) < 4 or ":" not in text:
        return None
    try:
        h, m = text.split(":", 1)
        return int(h), int(m)
    except (TypeError, ValueError):
        return None


def _job_zoneinfo(job: dict):
    """Property-local ZoneInfo for booked clocks (Canada-wide)."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    from ..shared.job_timezone import resolve_job_iana

    iana = resolve_job_iana(job)
    try:
        return ZoneInfo(iana)
    except ZoneInfoNotFoundError:
        return ZoneInfo("America/Toronto")


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _slot_bounds(job: Any, duration_min: Optional[int] = None) -> Optional[tuple[datetime, datetime]]:
    """Return (start, end) as UTC-aware datetimes for the one-visit booked block.

    Bare HH:MM clocks are property-local (resolve_job_iana). Converting to UTC
    before FreeBusy / Graph / CalDAV keeps Canada-wide assign honest — treating
    Vancouver 2pm as 14:00Z was wrong by seven hours.
    """
    j = _as_dict(job)
    day = str(j.get("scheduled_date") or "")[:10]
    if len(day) < 10:
        return None

    start_iso = j.get("selected_slot_start")
    end_iso = j.get("selected_slot_end")
    if start_iso and end_iso:
        try:
            s = datetime.fromisoformat(str(start_iso).replace("Z", "+00:00"))
            e = datetime.fromisoformat(str(end_iso).replace("Z", "+00:00"))
            s, e = _as_utc(s), _as_utc(e)
            if e > s:
                return s, e
        except (TypeError, ValueError):
            pass

    start_hm = _parse_hhmm(j.get("confirmed_arrive_at") or j.get("time_window_start"))
    if not start_hm:
        return None
    try:
        y, mo, d = int(day[0:4]), int(day[5:7]), int(day[8:10])
    except ValueError:
        return None

    zi = _job_zoneinfo(j)
    start_local = datetime(y, mo, d, start_hm[0], start_hm[1], tzinfo=zi)

    end_hm = _parse_hhmm(j.get("time_window_end"))
    dur = duration_min if duration_min is not None else estimate_shoot_duration_minutes(j)
    if end_hm:
        end_local = datetime(y, mo, d, end_hm[0], end_hm[1], tzinfo=zi)
        # If window is shorter than estimated duration, extend to duration.
        if end_local <= start_local or (end_local - start_local).total_seconds() / 60 < dur:
            end_local = start_local + timedelta(minutes=dur)
    else:
        end_local = start_local + timedelta(minutes=dur)
    return _as_utc(start_local), _as_utc(end_local)


def selected_slot_iso_pair(job: Any, duration_min: Optional[int] = None) -> Optional[tuple[str, str]]:
    """UTC ISO pair for jobs.selected_slot_start / selected_slot_end."""
    bounds = _slot_bounds(job, duration_min)
    if not bounds:
        return None
    s, e = bounds
    return (
        s.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        e.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def vendor_slot_conflicts(job: Any, occupied_jobs: Iterable[Any]) -> bool:
    """True when job's booked block overlaps any occupied job for the same vendor."""
    target = _slot_bounds(job)
    if not target:
        return False
    t0, t1 = target
    for other in occupied_jobs or []:
        o = _as_dict(other)
        if o.get("id") and _as_dict(job).get("id") and o.get("id") == _as_dict(job).get("id"):
            continue
        status = str(o.get("status") or "").lower()
        if status in ("cancelled", "canceled", "declined", "unassigned"):
            continue
        other_bounds = _slot_bounds(o)
        if not other_bounds:
            continue
        o0, o1 = other_bounds
        # Overlap if intervals intersect
        if t0 < o1 and o0 < t1:
            return True
    return False


def previous_job_coords(job: Any, occupied: Iterable[Any]) -> Optional[tuple[float, float]]:
    """Lat/lng of the most recent same-day job ending before this job's start (travel origin)."""
    target = _slot_bounds(job)
    if not target:
        return None
    t0, _ = target
    day = str(_as_dict(job).get("scheduled_date") or "")[:10]
    best_end: Optional[datetime] = None
    best_coords: Optional[tuple[float, float]] = None
    for other in occupied or []:
        o = _as_dict(other)
        if str(o.get("scheduled_date") or "")[:10] != day:
            continue
        status = str(o.get("status") or "").lower()
        if status in ("cancelled", "canceled", "declined", "unassigned"):
            continue
        lat, lng = o.get("lat"), o.get("lng")
        if lat is None or lng is None:
            continue
        bounds = _slot_bounds(o)
        if not bounds:
            continue
        _, o1 = bounds
        if o1 <= t0 and (best_end is None or o1 > best_end):
            best_end = o1
            try:
                best_coords = (float(lat), float(lng))
            except (TypeError, ValueError):
                continue
    return best_coords
