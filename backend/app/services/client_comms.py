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

async def register_property_visit(
    property_id: str,
    storage_path: str = "",
    title: str = "Virtual Visit",
    embed_url: str | None = None,
    visit_type: str | None = None,
) -> bool:
    """POST a finished capture to Bricks-OS property_visits via
    POST /api/internal/visit-ready.

    type=file + storage_path — Gaussian splat / .ply virtual visit (VisitViewer gsplat).
    type=url + embed_url — hosted 3D tour walkthrough (VisitViewer iframe). Do not
    send phone-tour HTML as type=file; that path is the splat viewer.

    Same INTERNAL_API_KEYS / X-Internal-Api-Key convention as _call_booking_comms.
    """
    api_key = get_own_internal_api_key()
    os_url = (os.environ.get("BRICKS_OS_API_URL") or "").strip().rstrip("/")
    if not os_url:
        os_url = "https://bricks-brokerage-api.vercel.app"
    if not api_key:
        logger.warning("INTERNAL_API_KEYS not set — skipping visit-ready call")
        return False
    body = {"property_id": property_id, "title": title}
    if storage_path:
        body["storage_path"] = storage_path
    if embed_url:
        body["embed_url"] = embed_url
        body["type"] = visit_type or "url"
    elif visit_type:
        body["type"] = visit_type
    if "type" not in body:
        body["type"] = "file"
    if body["type"] == "file" and not storage_path:
        logger.warning("visit-ready skipped — type=file needs storage_path")
        return False
    if body["type"] == "url" and not embed_url:
        logger.warning("visit-ready skipped — type=url needs embed_url")
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{os_url}/api/internal/visit-ready",
                headers={"X-Internal-Api-Key": api_key},
                json=body,
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


async def notify_capture_delivered(
    job_id: str,
    *,
    products: list[str],
    address: str = "",
    booking_id: str = "",
    delivery_urls: list | None = None,
) -> bool:
    """Notify the booking agent that a 3D tour and/or floor plan landed on the listing file.

    Reuses Companion POST /api/internal/comms/job-delivered with `products` so the
    email names what landed (not "photos"). Best-effort — same keys as photo delivery.
    """
    if not products:
        return False
    from ..shared.database import get_db
    db = await get_db()

    bid = (booking_id or "").strip()
    addr = (address or "").strip()
    if not bid or not addr:
        res_job = await db.table("jobs").select("booking_id, address").eq("id", job_id).maybe_single().execute()
        if not res_job.data:
            logger.warning(f"Job {job_id}: missing job — skipping capture delivery notification")
            return False
        bid = bid or (res_job.data.get("booking_id") or "")
        addr = addr or (res_job.data.get("address") or "")
    if not bid:
        logger.warning(f"Job {job_id}: no booking_id — skipping capture delivery notification")
        return False

    urls = delivery_urls or []
    ok = await _call_booking_comms("/api/internal/comms/job-delivered", {
        "booking_id": bid,
        "job_id": job_id,
        "address": addr,
        "delivery_count": max(len(urls), len(products), 1),
        "delivery_urls": urls[:5],
        "products": products,
        "splat_pending": False,
    })
    if ok:
        logger.info(f"Job {job_id}: capture delivery notification sent products={products!r}")
    return ok
