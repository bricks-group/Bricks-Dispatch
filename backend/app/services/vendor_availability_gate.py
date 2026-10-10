"""Manual Availability settings as hard gates for auto-assign.

UI copy: calendar FreeBusy when connected; manual hours when not.
Always respect available=false, blocked_windows, and jobs_per_day capacity.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from .shoot_duration import _as_dict, _parse_hhmm, _slot_bounds

logger = logging.getLogger(__name__)


def _parse_iso(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _hm_to_minutes(hm: tuple[int, int]) -> int:
    return hm[0] * 60 + hm[1]


def work_hours_cover_slot(
    work_start: Any,
    work_end: Any,
    job: Any,
    duration_min: Optional[int] = None,
) -> Optional[bool]:
    """True if the shoot slot sits inside work_start..work_end (property-local).

    None = cannot evaluate (missing clocks) → fail-soft continue.
    """
    start_hm = _parse_hhmm(work_start)
    end_hm = _parse_hhmm(work_end)
    if not start_hm or not end_hm:
        return None
    start_m = _hm_to_minutes(start_hm)
    end_m = _hm_to_minutes(end_hm)
    if end_m <= start_m:
        # Overnight window e.g. 22:00–06:00 — treat as always open for now.
        return True

    j = _as_dict(job)
    arrive = _parse_hhmm(j.get("confirmed_arrive_at") or j.get("time_window_start"))
    if not arrive:
        return None
    arrive_m = _hm_to_minutes(arrive)

    leave_hm = _parse_hhmm(j.get("time_window_end"))
    if leave_hm:
        leave_m = _hm_to_minutes(leave_hm)
    else:
        dur = int(duration_min or j.get("estimated_duration_min") or 60)
        leave_m = arrive_m + max(15, dur)

    if arrive_m < start_m or leave_m > end_m:
        return False
    return True


def blocked_window_overlaps(blocked_windows: Iterable[Any], job: Any, duration_min: Optional[int] = None) -> bool:
    bounds = _slot_bounds(job, duration_min)
    if not bounds:
        return False
    slot_start, slot_end = bounds
    for w in blocked_windows or []:
        wd = _as_dict(w)
        a = _parse_iso(wd.get("start"))
        b = _parse_iso(wd.get("end"))
        if not a or not b or b <= a:
            continue
        if a < slot_end and b > slot_start:
            return True
    return False


def capacity_exceeded(jobs_per_day: Any, occupied: Iterable[Any], job: Any) -> bool:
    try:
        cap = int(jobs_per_day)
    except (TypeError, ValueError):
        return False
    if cap < 0:
        return False
    if cap == 0:
        return True
    j = _as_dict(job)
    day = str(j.get("scheduled_date") or "")[:10]
    job_id = str(j.get("id") or "")
    count = 0
    for o in occupied or []:
        od = _as_dict(o)
        if job_id and str(od.get("id") or "") == job_id:
            continue
        status = str(od.get("status") or "").lower()
        if status in ("cancelled", "declined", "unassigned"):
            continue
        oday = str(od.get("scheduled_date") or "")[:10]
        if day and oday and oday != day:
            continue
        if day and not oday:
            continue
        count += 1
    return count >= cap


def manual_availability_allows(
    settings: Optional[dict],
    job: Any,
    *,
    occupied: Optional[Iterable[Any]] = None,
    duration_min: Optional[int] = None,
    has_calendar: bool = False,
) -> bool:
    """Hard-gate: False means exclude vendor from auto-assign.

    - available=false always excludes
    - blocked_windows + jobs_per_day always apply
    - work_start/work_end apply when no calendar is connected (FreeBusy covers busy otherwise)
    Missing settings → allow (fail-open so Kam still gets Mississauga books).
    """
    if not settings:
        return True
    s = _as_dict(settings)
    if s.get("available") is False:
        return False
    if blocked_window_overlaps(s.get("blocked_windows") or [], job, duration_min):
        return False
    if capacity_exceeded(s.get("jobs_per_day"), occupied or [], job):
        return False
    if not has_calendar:
        covers = work_hours_cover_slot(s.get("work_start"), s.get("work_end"), job, duration_min)
        if covers is False:
            return False
    return True


async def load_availability_by_member(db, member_ids: list[str]) -> dict[str, dict]:
    """Map member_id → vendor_availability row."""
    out: dict[str, dict] = {}
    ids = [i for i in member_ids if i]
    if not ids or db is None:
        return out
    try:
        res = await (
            db.table("vendor_availability")
            .select("*")
            .in_("member_id", ids[:80])
            .execute()
        )
        for row in res.data or []:
            mid = row.get("member_id")
            if mid:
                out[str(mid)] = row
    except Exception as e:
        from ..shared.pg_fallback import (
            _is_rest_quota_error,
            pg_select_vendor_availability_by_member_ids,
        )
        if _is_rest_quota_error(e):
            pg_map = pg_select_vendor_availability_by_member_ids(ids[:80])
            for mid, row in (pg_map or {}).items():
                if mid:
                    out[str(mid)] = row
            return out
        logger.warning("load_availability_by_member failed (soft): %s", e)
    return out


async def member_has_calendar(db, member_id: str) -> bool:
    """True if any calendar connection exists for FreeBusy (not just configured OAuth)."""
    if not member_id or db is None:
        return False
    try:
        from .vendor_calendars import list_vendor_calendar_status

        statuses = await list_vendor_calendar_status(db, member_id)
        return any(bool(s.get("connected")) for s in statuses or [])
    except Exception as e:
        logger.debug("member_has_calendar soft-fail: %s", e)
        return False
