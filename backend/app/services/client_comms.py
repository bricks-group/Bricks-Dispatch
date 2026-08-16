"""
Client communications agent — triggered at key job lifecycle events.

Agent email delivery is delegated to the Booking service, which owns the
agent relationship (bookings → users). Dispatch reads only its own tables
(jobs, vendor_members) and forwards the relevant data to Booking via
internal HTTP call.

Events handled:
  booking_confirmed      — when job is assigned to a photographer
  photographer_en_route  — when job moves to in_progress
  job_delivered          — handled via post_production.py
"""
import logging
import os

import httpx

from ..shared.auth import get_own_internal_api_key

logger = logging.getLogger(__name__)

async def _call_booking_comms(path: str, payload: dict) -> bool:
    """POST an internal comms event to the Booking service."""
    api_key = get_own_internal_api_key()
    booking_url = os.environ.get("BOOKING_API_URL") or os.environ.get("BOOKING_CALLBACK_URL")
    if not api_key:
        logger.warning(f"INTERNAL_API_KEYS not set — skipping comms call to {path}")
        return False
    if not booking_url:
        logger.warning(f"BOOKING_API_URL not set — skipping comms call to {path}")
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{booking_url}{path}",
                headers={"X-Internal-Api-Key": api_key},
                json=payload,
            )
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"Booking comms call to {path} failed: {e}")
        return False

async def register_property_visit(property_id: str, storage_path: str, title: str = "Virtual Visit") -> bool:
    """POST a finished splat to Bricks-OS's property_visits table via
    POST /api/internal/visit-ready. Replaces the dead media_delivery.splat_url write
    (that column was never actually added to the live DB -- see BRICKS_STATUS.md).
    "Visit" not "tour" (renamed 2026-08-03, Kam): a tour implies a fixed, guided
    path; a visit is self-directed exploration, which is what the viewer actually
    lets someone do.
    Same INTERNAL_API_KEYS / X-Internal-Api-Key convention as _call_booking_comms
    above, just a different target service -- this repo's own INTERNAL_API_KEYS
    value, which must be one Bricks-OS also accepts (that's a deploy/ops step,
    not verifiable from this repo). Previously read a separate INTERNAL_API_KEY
    (singular) var that was never configured, so this call always no-op'd --
    fixed 2026-08-15, see get_own_internal_api_key()'s docstring."""
    api_key = get_own_internal_api_key()
    os_url = os.environ.get("BRICKS_OS_API_URL")
    if not api_key:
        logger.warning("INTERNAL_API_KEYS not set — skipping visit-ready call")
        return False
    if not os_url:
        logger.warning("BRICKS_OS_API_URL not set — skipping visit-ready call")
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{os_url}/api/internal/visit-ready",
                headers={"X-Internal-Api-Key": api_key},
                json={"property_id": property_id, "storage_path": storage_path, "title": title},
            )
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"visit-ready call for property {property_id} failed: {e}")
        return False


async def notify_booking_confirmed(job_id: str) -> None:
    """
    Notifies the agent that their shoot is confirmed and a photographer has been assigned.
    Reads photographer name from Dispatch's vendor_members; delegates email to Booking.
    """
    from ..shared.database import get_db
    db = await get_db()

    res_job = await db.table("jobs").select(
        "id, booking_id, address, scheduled_date, time_window_start, package_name, assignee_id"
    ).eq("id", job_id).maybe_single().execute()
    if not res_job.data:
        return

    job = res_job.data
    booking_id = job.get("booking_id") or ""
    if not booking_id:
        logger.warning(f"Job {job_id}: no booking_id — skipping booking confirmed notification")
        return

    photographer_name = "your Bricks photographer"
    assignee_id = job.get("assignee_id")
    if assignee_id:
        res_member = await db.table("vendor_members").select("name").eq("id", assignee_id).maybe_single().execute()
        if res_member.data:
            photographer_name = res_member.data.get("name", photographer_name)

    await _call_booking_comms("/api/internal/comms/booking-confirmed", {
        "booking_id": booking_id,
        "job_id": job_id,
        "photographer_name": photographer_name,
        "address": job.get("address", ""),
        "scheduled_date": job.get("scheduled_date", ""),
        "time_window_start": job.get("time_window_start", ""),
        "package_name": job.get("package_name", ""),
    })
    logger.info(f"Booking confirmed comms dispatched for job {job_id} (booking {booking_id})")


async def notify_photographer_en_route(job_id: str) -> None:
    """
    Notifies the agent that the photographer is on their way.
    Reads photographer name from Dispatch's vendor_members; delegates email to Booking.
    """
    from ..shared.database import get_db
    db = await get_db()

    res_job = await db.table("jobs").select(
        "id, booking_id, address, scheduled_date, time_window_start, assignee_id"
    ).eq("id", job_id).maybe_single().execute()
    if not res_job.data:
        return

    job = res_job.data
    booking_id = job.get("booking_id") or ""
    if not booking_id:
        logger.warning(f"Job {job_id}: no booking_id — skipping en-route notification")
        return

    photographer_name = "Your Bricks photographer"
    assignee_id = job.get("assignee_id")
    if assignee_id:
        res_member = await db.table("vendor_members").select("name").eq("id", assignee_id).maybe_single().execute()
        if res_member.data:
            photographer_name = res_member.data.get("name", photographer_name)

    await _call_booking_comms("/api/internal/comms/photographer-en-route", {
        "booking_id": booking_id,
        "job_id": job_id,
        "photographer_name": photographer_name,
        "address": job.get("address", ""),
        "scheduled_date": job.get("scheduled_date", ""),
        "time_window_start": job.get("time_window_start", ""),
    })
    logger.info(f"En-route comms dispatched for job {job_id} (booking {booking_id})")
