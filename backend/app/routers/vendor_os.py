import asyncio
import logging
from datetime import datetime, timedelta, timezone
import httpx
import os
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Body, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from ..services.capture_entitlements import stamp_capture
from ..services.vendor_selection import assign_vendor_for_job
from ..shared.audit import write_audit_event
from ..shared.geospatial import haversine_km as _haversine_km
from ..shared.auth import can_access_stripe_connect, ensure_assigned_to_actor, get_vendor_member, get_vendor_member_ids, is_vendor_admin, resolve_vendor_actor_id
from ..shared.vendor_identity import is_owner_member, photographer_payout_cents, public_profile
from ..shared.job_scope import (
    ORG_VENDOR_ID,
    job_visible_to,
    jobs_or_filter,
    parse_scheduled_date,
    photographer_assignee_ids,
    resolve_manual_assignment,
    toronto_today,
    unassigned_assignee_or_filter,
    vendor_scope_ids,
)
from ..routers.vendor_profile import earnings_impl
from ..shared.database import get_db
from ..services.webhooks import send_dispatch_status_update_webhook
from ..shared.job_timezone import tz_abbrev, zoneinfo_for_job
from ..shared import noshow_policy
from ..shared.errors import api_error
from ..shared.pagination import normalize_pagination, paginated_response
from ..shared.schemas import (
    AvailabilitySettings,
    AvailabilityUpdate,
    JobAssignmentUpdate, JobStatusUpdate,
    JobCreate,
    VendorMember,
    VendorMemberCreate,
    VendorMemberUpdate,
    VendorSettings,
    VendorSettingsUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vendor-os", tags=["vendor-os"])

# Module-level set keeps strong references to background tasks so the GC
# cannot collect them before they complete (asyncio only weakly refs tasks).
_background_tasks: set = set()




class AgentNoShowReport(BaseModel):
    notes: Optional[str] = None
    photographer_lat: Optional[float] = None
    photographer_lng: Optional[float] = None


class CheckInRequest(BaseModel):
    """Device GPS at check-in. Required when the job has a geocoded address."""
    lat: Optional[float] = None
    lng: Optional[float] = None
    accuracy_m: Optional[float] = None


class CallAttemptRequest(BaseModel):
    method: Optional[str] = "dialer"

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class JobListingUpdate(BaseModel):
    transaction_id: Optional[str] = None



class ConfirmArriveRequest(BaseModel):
    """Photographer confirms exact arrive clock within the paid /book window."""
    arrive_at: str  # HH:MM in the job's property timezone


def _is_uuid(value: Optional[str]) -> bool:
    return bool(value and _UUID_RE.match(str(value).strip()))


def _listing_public(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    deal = row.get("deal_type")
    return {
        "id": row.get("id"),
        "address": row.get("property_address") or "",
        "city": row.get("city") or "",
        "deal_type": str(deal).capitalize() if deal else None,
    }


def _apply_listing(job: dict, listings: dict) -> dict:
    tid = job.get("transaction_id") or None
    listing = listings.get(tid) if tid else None
    job["transaction_id"] = tid
    job["listing_address"] = (listing or {}).get("address") if listing else None
    job["listing"] = listing
    return stamp_capture(job)


async def _listings_by_ids(db, ids) -> dict:
    clean = [i for i in ids if i]
    if not clean:
        return {}
    res = await db.table("transactions").select(
        "id, property_address, city, deal_type"
    ).in_("id", clean).execute()
    out = {}
    for row in res.data or []:
        pub = _listing_public(row)
        if pub and pub.get("id"):
            out[pub["id"]] = pub
    return out



def _maybe_single_row(res) -> dict:
    """postgrest maybe_single().execute() is None when there is no row.

    Reading .data on that None is AttributeError. The global handler turns it
    into HTTP 500 with no CORS, so the browser shows net::ERR_FAILED instead
    of the route's 404 or Postgres fallback.
    """
    data = getattr(res, "data", None) if res is not None else None
    return data if isinstance(data, dict) else {}


async def _load_visible_job(db, job_id: str, actor_id: str, actor_member: dict, *, admin_all: bool = False) -> dict:
    try:
        res = await db.table("jobs").select("*").eq("id", job_id).maybe_single().execute()
        job = _maybe_single_row(res)
    except Exception as e:
        # Fair-Use: PostgREST job reads 402 — load via Postgres so confirm-arrive /
        # check-in / status can still authorize against the assigned row.
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_job_by_id
        if not _is_rest_quota_error(e):
            raise
        job = pg_select_job_by_id(job_id) or {}
    if not job.get("id"):
        # Empty REST body can also mean Fair-Use silent failure (no exception).
        from ..shared.pg_fallback import pg_select_job_by_id
        job = pg_select_job_by_id(job_id) or job
    allow_all = admin_all and is_vendor_admin(actor_member)
    if not job_visible_to(job, actor_id, actor_member, assigned_only=not allow_all, admin_all=allow_all):
        api_error(404, "Job not found", "job_not_found")
    return job


async def _update_job_fields(db, job_id: str, fields: dict) -> None:
    """jobs UPDATE with the Postgres fallback for Fair-Use 402s."""
    try:
        await db.table("jobs").update(fields).eq("id", job_id).execute()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_update_job
        if not _is_rest_quota_error(e):
            raise
        if not pg_update_job(job_id, fields):
            raise HTTPException(status_code=503, detail="Saving is unavailable right now; try again shortly")


def _valid_coord(lat, lng) -> bool:
    try:
        return lat is not None and lng is not None and -90 <= float(lat) <= 90 and -180 <= float(lng) <= 180
    except (TypeError, ValueError):
        return False


@router.post("/jobs/{job_id}/check-in")
async def check_in_job(
    job_id: str,
    payload: Optional[CheckInRequest] = Body(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Vendor checks in at the property. Records the time and device GPS.

    Location is required to check in when the job has a geocoded address, and the
    device must be within noshow_policy.CHECKIN_RADIUS_M of it. Jobs without
    coordinates record the GPS with no distance; a no-show on such a job is held
    for review. The first check-in time is kept; later calls do not move it.
    """
    payload = payload or CheckInRequest()
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    db = await get_db()
    job = await _load_visible_job(db, job_id, actor_id, actor_member, admin_all=False)
    ensure_assigned_to_actor(job, actor_id, actor_member)

    has_gps = _valid_coord(payload.lat, payload.lng)
    radius = noshow_policy.CHECKIN_RADIUS_M
    distance_m = noshow_policy.checkin_distance_m(job, payload.lat, payload.lng) if has_gps else None
    if noshow_policy.job_has_coords(job):
        if not has_gps:
            api_error(
                422,
                "Location is required to check in. Turn on location for Bricks and try again.",
                "checkin_location_required",
            )
        if distance_m is not None and distance_m > radius:
            api_error(
                422,
                f"You're about {int(round(distance_m))} m from the property. Check in when you're there (within {radius} m).",
                "checkin_too_far",
            )

    result: dict = {"status": "updated", "job_id": job_id, "new_status": job.get("status")}
    if job.get("status") != "in_progress":
        result = await update_vendor_job_status(
            job_id, JobStatusUpdate(status="in_progress"), authorization, x_user_id
        )

    now = datetime.now(timezone.utc)
    if not job.get("checked_in_at"):
        fields = {
            "checked_in_at": now.isoformat(),
            "check_in_lat": float(payload.lat) if has_gps else None,
            "check_in_lng": float(payload.lng) if has_gps else None,
            "check_in_accuracy_m": float(payload.accuracy_m) if payload.accuracy_m is not None else None,
            "check_in_distance_m": distance_m,
            "updated_at": now.isoformat(),
        }
        await _update_job_fields(db, job_id, fields)
        job.update(fields)
    job["status"] = "in_progress"
    out = dict(result or {})
    out["checked_in_at"] = job.get("checked_in_at")
    out["check_in_distance_m"] = job.get("check_in_distance_m")
    out["noshow"] = noshow_policy.readiness(job, now)
    return out


@router.get("/jobs/{job_id}/noshow-status")
async def get_noshow_status(
    job_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """The four no-show conditions for this job, plus the wait and radius rules."""
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    db = await get_db()
    job = await _load_visible_job(db, job_id, actor_id, actor_member, admin_all=False)
    out = noshow_policy.readiness(job)
    # The client's number is shared with the assigned vendor once they've checked in,
    # so the in-app Call client button can open the dialer.
    out["client_phone"] = None
    if job.get("checked_in_at"):
        try:
            ensure_assigned_to_actor(job, actor_id, actor_member)
            out["client_phone"] = await _client_phone(db, job)
        except HTTPException:
            pass
    return out


async def _client_phone(db, job: dict) -> Optional[str]:
    """Booking contact phone, then guest phone, then the booking user's phone."""
    booking_id = job.get("booking_id")
    user_id = job.get("agent_user_id")
    try:
        if booking_id:
            res = await db.table("bookings").select("contact_phone, guest_phone, user_id").eq(
                "id", booking_id
            ).maybe_single().execute()
            bk = _maybe_single_row(res)
            for key in ("contact_phone", "guest_phone"):
                if str(bk.get(key) or "").strip():
                    return str(bk[key]).strip()
            user_id = bk.get("user_id") or user_id
        if user_id:
            res_u = await db.table("users").select("phone").eq("id", user_id).maybe_single().execute()
            phone = str(_maybe_single_row(res_u).get("phone") or "").strip()
            return phone or None
    except Exception as e:
        logger.warning(f"Client phone lookup failed for job {job.get('id')}: {e}")
    return None


async def _record_call_attempt(db, job: dict, method: str) -> dict:
    now = datetime.now(timezone.utc)
    fields = {
        "noshow_call_attempted_at": now.isoformat(),
        "noshow_call_method": method,
        "updated_at": now.isoformat(),
    }
    await _update_job_fields(db, job["id"], fields)
    job.update(fields)
    return job


@router.post("/jobs/{job_id}/call-attempt")
async def log_call_attempt(
    job_id: str,
    payload: Optional[CallAttemptRequest] = Body(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Log that the vendor tried to call the client (the in-app Call client button).

    Works without Twilio: the app opens the phone dialer and logs the attempt here.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    db = await get_db()
    job = await _load_visible_job(db, job_id, actor_id, actor_member, admin_all=False)
    ensure_assigned_to_actor(job, actor_id, actor_member)
    if not job.get("checked_in_at"):
        api_error(409, "Check in at the property before calling the client.", "noshow_not_checked_in")
    method = ((payload.method if payload else None) or "dialer").strip().lower()
    if method not in noshow_policy.CALL_METHODS or method == "sms":
        method = "dialer"
    if method == "no_number" and await _client_phone(db, job):
        method = "dialer"
    await _record_call_attempt(db, job, method)
    return noshow_policy.readiness(job)


def _photo_ext(content_type: str) -> str:
    return {"image/png": "png", "image/webp": "webp", "image/heic": "heic", "image/heif": "heif"}.get(
        (content_type or "").lower(), "jpg"
    )


@router.post("/jobs/{job_id}/noshow-photo")
async def upload_noshow_photo(
    job_id: str,
    photo: UploadFile = File(...),
    taken_at: Optional[str] = Form(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """On-site photo for a no-show report. Stored in the private evidence bucket.

    The server stamps the upload time; `taken_at` (from the camera) is kept for the
    reviewer. The photo must be uploaded after check-in.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    db = await get_db()
    job = await _load_visible_job(db, job_id, actor_id, actor_member, admin_all=False)
    ensure_assigned_to_actor(job, actor_id, actor_member)
    if not job.get("checked_in_at"):
        api_error(409, "Check in at the property before taking the photo.", "noshow_not_checked_in")

    content_type = (photo.content_type or "image/jpeg").lower()
    if not content_type.startswith("image/"):
        api_error(400, "That file isn't a photo. Take a photo of the property.", "noshow_photo_not_image")
    data = await photo.read()
    if not data:
        api_error(400, "The photo was empty. Take it again.", "noshow_photo_empty")
    if len(data) > noshow_policy.PHOTO_MAX_BYTES:
        api_error(413, "That photo is too large. Take it again.", "noshow_photo_too_large")

    now = datetime.now(timezone.utc)
    path = f"{job_id}/{now.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}.{_photo_ext(content_type)}"
    from ..services.storage import upload_to_bucket
    if not await upload_to_bucket(noshow_policy.EVIDENCE_BUCKET, path, data, content_type):
        api_error(503, "Couldn't save the photo. Try again.", "noshow_photo_upload_failed")

    taken = noshow_policy.parse_ts(taken_at)
    fields = {
        "noshow_photo_path": path,
        "noshow_photo_taken_at": taken.isoformat() if taken else None,
        "noshow_photo_uploaded_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    await _update_job_fields(db, job_id, fields)
    job.update(fields)
    return noshow_policy.readiness(job, now)



# ---------------------------------------------------------------------------
# Trade job checklist: saved ticks and proof (Kam, 2026-10-31).
# Media jobs keep their read-only shoot checklist; these routes refuse them.
# ---------------------------------------------------------------------------


class ChecklistInventoryRow(BaseModel):
    item: Optional[str] = None
    room: Optional[str] = None
    note: Optional[str] = None


class ChecklistItemPatch(BaseModel):
    completed: Optional[bool] = None
    note: Optional[str] = None
    none_found: Optional[bool] = None
    inventory: Optional[list[ChecklistInventoryRow]] = None


async def _checklist_view(job: dict, items: list[dict]) -> dict:
    from ..services import job_checklist as jc
    from ..services.storage import signed_url_in_bucket

    signed: dict = {}
    for path in jc.storage_paths(items):
        signed[path] = await signed_url_in_bucket(jc.PROOF_BUCKET, path, 3600)
    return jc.view(job, items, signed)


async def _load_trade_checklist_job(job_id: str, authorization, x_user_id, *, edit: bool):
    from ..services import job_checklist as jc

    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    db = await get_db()
    job = await _load_visible_job(db, job_id, actor_id, actor_member, admin_all=not edit)
    if edit:
        ensure_assigned_to_actor(job, actor_id, actor_member)
    if not jc.is_trade_job(job):
        api_error(409, "This job uses the shoot checklist, which Bricks ticks for you.", "checklist_not_trade_job")
    if edit and str(job.get("status") or "") not in jc.EDITABLE_STATUSES:
        api_error(409, "This job is closed, so its checklist can't be changed.", "checklist_locked")
    return db, job, actor_id, jc.effective_checklist(job)


async def _save_checklist(db, job: dict, items: list[dict]) -> None:
    stored = [{k: v for k, v in item.items() if k not in ("proof", "missing")} for item in items]
    now = datetime.now(timezone.utc).isoformat()
    await _update_job_fields(db, job["id"], {"checklist": stored, "updated_at": now})
    job["checklist"] = stored
    job["updated_at"] = now


def _checklist_item_or_404(items: list[dict], item_id: str) -> dict:
    from ..services import job_checklist as jc

    item = jc.find_item(items, item_id)
    if not item:
        api_error(404, "That checklist item isn't on this job any more. Refresh the job.", "checklist_item_not_found")
    return item


async def _read_proof_file(upload: UploadFile, allowed: set, wrong_type: str) -> tuple[bytes, str]:
    from ..services import job_checklist as jc

    content_type = (upload.content_type or "").lower().split(";")[0].strip()
    name = (upload.filename or "").lower()
    if not content_type or content_type == "application/octet-stream":
        content_type = "application/pdf" if name.endswith(".pdf") else "image/jpeg"
    if content_type == "image/jpg":
        content_type = "image/jpeg"
    if content_type not in allowed:
        api_error(400, wrong_type, "checklist_file_type")
    data = await upload.read()
    if not data:
        api_error(400, "That file was empty. Try again.", "checklist_file_empty")
    if len(data) > jc.MAX_UPLOAD_BYTES:
        api_error(413, "That file is over 4 MB. Use a smaller photo or file.", "checklist_file_too_large")
    return data, content_type


@router.get("/jobs/{job_id}/checklist")
async def get_trade_checklist(
    job_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Trade job checklist with saved ticks, proof and what's still needed."""
    _db, job, _actor, items = await _load_trade_checklist_job(job_id, authorization, x_user_id, edit=False)
    return await _checklist_view(job, items)


@router.patch("/jobs/{job_id}/checklist/{item_id}")
async def patch_trade_checklist_item(
    job_id: str,
    item_id: str,
    payload: ChecklistItemPatch,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Tick or untick an item, or save its note, nothing-found answer or inventory.

    An item can only be ticked once its proof is attached.
    """
    from ..services import job_checklist as jc

    db, job, actor_id, items = await _load_trade_checklist_job(job_id, authorization, x_user_id, edit=True)
    item = _checklist_item_or_404(items, item_id)
    patch = payload.model_dump(exclude_unset=True) if hasattr(payload, "model_dump") else payload.dict(exclude_unset=True)
    err = jc.apply_patch(item, patch, actor_id)
    if err:
        api_error(400, err, "checklist_proof_missing")
    await _save_checklist(db, job, items)
    return await _checklist_view(job, items)


@router.post("/jobs/{job_id}/checklist/{item_id}/photos")
async def add_trade_checklist_photo(
    job_id: str,
    item_id: str,
    photo: UploadFile = File(...),
    taken_at: Optional[str] = Form(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Add one proof photo to a checklist item (private bucket, 4 MB max)."""
    from ..services import job_checklist as jc
    from ..services.storage import upload_to_bucket

    db, job, _actor, items = await _load_trade_checklist_job(job_id, authorization, x_user_id, edit=True)
    item = _checklist_item_or_404(items, item_id)
    if jc.proof_kind(item.get("label")) not in (jc.PROOF_PHOTO, jc.PROOF_FINDINGS):
        api_error(400, "This item doesn't take photos.", "checklist_no_photos")
    photos = list(item.get("photos") or [])
    if len(photos) >= jc.MAX_PHOTOS_PER_ITEM:
        api_error(400, f"This item already has {jc.MAX_PHOTOS_PER_ITEM} photos.", "checklist_photo_limit")
    allowed = {t for t in jc.REPORT_TYPES if t.startswith("image/")}
    data, content_type = await _read_proof_file(photo, allowed, "That file isn't a photo. Add a JPG, PNG or HEIC photo.")
    path = jc.proof_path(job_id, item_id, content_type)
    if not await upload_to_bucket(jc.PROOF_BUCKET, path, data, content_type):
        api_error(503, "Couldn't save the photo. Try again.", "checklist_upload_failed")
    taken = noshow_policy.parse_ts(taken_at)
    photos.append({
        "id": uuid.uuid4().hex,
        "path": path,
        "content_type": content_type,
        "uploaded_at": jc.now_iso(),
        "taken_at": taken.isoformat() if taken else None,
    })
    item["photos"] = photos
    await _save_checklist(db, job, items)
    return await _checklist_view(job, items)


@router.delete("/jobs/{job_id}/checklist/{item_id}/photos/{photo_id}")
async def delete_trade_checklist_photo(
    job_id: str,
    item_id: str,
    photo_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Remove a proof photo. The item unticks if that leaves it without proof."""
    from ..services import job_checklist as jc
    from ..services.storage import remove_from_bucket

    db, job, _actor, items = await _load_trade_checklist_job(job_id, authorization, x_user_id, edit=True)
    item = _checklist_item_or_404(items, item_id)
    photos = list(item.get("photos") or [])
    gone = [p for p in photos if isinstance(p, dict) and str(p.get("id")) == str(photo_id)]
    if not gone:
        api_error(404, "That photo is already gone. Refresh the job.", "checklist_photo_not_found")
    item["photos"] = [p for p in photos if p not in gone]
    jc.settle_tick(item)
    await _save_checklist(db, job, items)
    await remove_from_bucket(jc.PROOF_BUCKET, [p.get("path") for p in gone])
    return await _checklist_view(job, items)


@router.post("/jobs/{job_id}/checklist/{item_id}/report")
async def upload_trade_checklist_report(
    job_id: str,
    item_id: str,
    report: UploadFile = File(...),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Upload (or replace) the report for a report item. PDF or photo, 4 MB max."""
    from ..services import job_checklist as jc
    from ..services.storage import remove_from_bucket, upload_to_bucket

    db, job, _actor, items = await _load_trade_checklist_job(job_id, authorization, x_user_id, edit=True)
    item = _checklist_item_or_404(items, item_id)
    if jc.proof_kind(item.get("label")) != jc.PROOF_REPORT:
        api_error(400, "This item doesn't take a report.", "checklist_no_report")
    data, content_type = await _read_proof_file(report, jc.REPORT_TYPES, "Upload the report as a PDF or a photo.")
    path = jc.proof_path(job_id, item_id, content_type)
    if not await upload_to_bucket(jc.PROOF_BUCKET, path, data, content_type):
        api_error(503, "Couldn't save the report. Try again.", "checklist_upload_failed")
    old = item.get("report") if isinstance(item.get("report"), dict) else None
    item["report"] = {
        "path": path,
        "filename": (report.filename or "report")[:200],
        "content_type": content_type,
        "uploaded_at": jc.now_iso(),
    }
    await _save_checklist(db, job, items)
    if old and old.get("path"):
        await remove_from_bucket(jc.PROOF_BUCKET, [old["path"]])
    return await _checklist_view(job, items)


@router.post("/jobs/{job_id}/confirm-arrive")
async def confirm_job_arrive_time(
    job_id: str,
    payload: ConfirmArriveRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Assigned photographer sets exact arrive HH:MM; emails the booking agent."""
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    db = await get_db()
    job = await _load_visible_job(db, job_id, actor_id, actor_member, admin_all=False)
    ensure_assigned_to_actor(job, actor_id, actor_member)

    raw = (payload.arrive_at or "").strip()
    m = re.match(r"^(\d{1,2}):(\d{2})$", raw)
    if not m:
        raise HTTPException(status_code=400, detail="arrive_at must be HH:MM")
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        raise HTTPException(status_code=400, detail="arrive_at must be a real clock time")
    arrive = f"{hour:02d}:{minute:02d}"

    win_start = (job.get("time_window_start") or "").strip()[:5]
    win_end = (job.get("time_window_end") or "").strip()[:5]
    # Prefer canonical window from preferred_window when present
    preferred = (job.get("preferred_window") or "").strip().lower()
    canonical = {
        "morning": ("09:00", "12:00"),
        "afternoon": ("12:00", "16:00"),
        "evening": ("16:00", "19:00"),
    }
    if preferred in canonical:
        win_start, win_end = canonical[preferred]
    if win_start and win_end and not (win_start <= arrive <= win_end):
        raise HTTPException(
            status_code=400,
            detail=f"Arrive time must fall inside the shoot window ({win_start}–{win_end} {tz_abbrev(job)})",
        )

    now = datetime.now(timezone.utc).isoformat()
    update = {
        "confirmed_arrive_at": arrive,
        "updated_at": now,
    }
    # Keep window bounds; narrow displayed start to the confirmed arrive for countdowns.
    if win_end:
        update["time_window_start"] = arrive
        update["time_window_end"] = win_end
    if preferred:
        update["preferred_window"] = preferred

    try:
        await db.table("jobs").update(update).eq("id", job_id).execute()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_update_job
        if not _is_rest_quota_error(e):
            raise
        if not pg_update_job(job_id, update):
            raise HTTPException(
                status_code=503,
                detail="Job arrive confirm unavailable (Fair Use); try again shortly",
            )
    refreshed = {**job, **update}

    try:
        from ..services.notifications import notify_arrive_confirmed
        await notify_arrive_confirmed(refreshed, actor_member)
    except Exception:
        logger.exception("Arrive-confirm agent notify failed for job %s", job_id)

    return stamp_capture(refreshed)


@router.get("/overview")
async def get_vendor_os_overview(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id") or ORG_VENDOR_ID
    assignees = photographer_assignee_ids(actor_id, actor_member)
    vendors = vendor_scope_ids(actor_id, actor_member)

    fair_use = False
    member_ids: list = []
    try:
        member_ids = await get_vendor_member_ids(vendor_id)
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error
        if not _is_rest_quota_error(e):
            raise
        fair_use = True
        member_ids = [actor_id]

    jobs = []
    try:
        db = await get_db()
        # Assigned jobs for this photographer, including default_vendor rows even
        # when vendor_members.vendor_id is the member UUID (Kam Owner session).
        query = db.table("jobs").select("*")
        if assignees:
            query = query.in_("assignee_id", assignees)
        else:
            query = query.or_(jobs_or_filter(vendors, assignees))
        res_jobs = await query.limit(500).execute()
        jobs = [j for j in (res_jobs.data or []) if job_visible_to(j, actor_id, actor_member, assigned_only=True)]
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_jobs_for_assignees
        if not _is_rest_quota_error(e):
            raise
        fair_use = True
        want_ids = assignees or [actor_id]
        jobs, _total = pg_select_jobs_for_assignees(want_ids, status=None, limit=500, offset=0)
        jobs = [j for j in jobs if job_visible_to(j, actor_id, actor_member, assigned_only=True)]

    today = toronto_today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    assigned_today = assigned_week = completed_week = scheduled_this_week = 0
    upcoming = []
    today_jobs = []
    total_capacity = max(len(member_ids) * 4, 1)
    for job in jobs:
        scheduled = parse_scheduled_date(job.get("scheduled_date"))
        if scheduled is None:
            continue
        if scheduled == today:
            assigned_today += 1
            if job.get("status") not in ("completed", "delivered", "cancelled"):
                today_jobs.append(job)
        if week_start <= scheduled <= week_end:
            assigned_week += 1
            scheduled_this_week += 1
            if job.get("status") in ("completed", "delivered"):
                completed_week += 1
        if scheduled >= today and job.get("status") not in ("completed", "delivered"):
            upcoming.append(job)
    today_jobs = sorted(today_jobs, key=lambda j: (j.get("scheduled_date", ""), j.get("time_window_start", "")))
    payload = {
        "jobs_assigned_today": assigned_today,
        "jobs_assigned_week": assigned_week,
        "jobs_completed_week": completed_week,
        "capacity_utilization_pct": round(min((scheduled_this_week / (total_capacity * 7)) * 100, 100), 1),
        "today_jobs": today_jobs,
        "upcoming_jobs": sorted(upcoming, key=lambda j: (j.get("scheduled_date", ""), j.get("time_window_start", "")))[:20],
        "filter": {
            "table": "jobs",
            "assignee_ids": assignees,
            "vendor_ids": vendors,
            "actor_id": actor_id,
        },
    }
    if fair_use:
        payload["fair_use_pg_fallback"] = True
    return payload


@router.get("/team")
async def get_vendor_team(
    search: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id") or ORG_VENDOR_ID
    vendors = vendor_scope_ids(actor_id, actor_member)

    sb = await get_db(); query = sb.table("vendor_members").select("*", count="exact").in_("vendor_id", vendors)
    if status:
        query = query.eq("status", status)
    if search:
        query = query.or_(f"name.ilike.%{search}%,email.ilike.%{search}%,role.ilike.%{search}%")
    
    page, page_size, skip = normalize_pagination(page, page_size)
    res = await query.order("created_at", desc=False).range(skip, skip + page_size - 1).execute()
    members = res.data
    total = res.count or 0
    
    for member in members:
        res_count = await (await get_db()).table("jobs").select("*", count="exact").in_("vendor_id", vendors).eq("assignee_id", member["id"]).in_("status", ["pending_assignment", "assigned", "in_progress", "processing"]).execute()
        member["assigned_jobs"] = res_count.count or 0
        pub = public_profile(member)
        member["title"] = pub["title"]
        member["skills"] = pub["skills"]
        member["assignable"] = pub["assignable"]
        member["assignable_reason"] = pub["assignable_reason"]
        member["rpas_certificate_url"] = pub["rpas_certificate_url"]
        member["rpas_certificate_level"] = pub["rpas_certificate_level"]
        member["payout_model"] = pub["payout_model"]
        member["payout_label"] = pub["payout_label"]
        
    return paginated_response(members, total, page, page_size)


@router.post("/team")
async def create_vendor_member(
    payload: VendorMemberCreate,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
    from ..services.vendor_profile import (
        ic_title_for_vendor_type,
        normalize_vendor_type,
        role_for_vendor_type,
        storage_vendor_type,
    )
    vtype = normalize_vendor_type(getattr(payload, "vendor_type", None) or "photographer")
    role = (payload.role or "").strip() or role_for_vendor_type(vtype)
    member = VendorMember(
        name=payload.name,
        email=payload.email.strip().lower(),
        phone=payload.phone,
        role=role,
        title=ic_title_for_vendor_type(vtype),
        # inspector / measurer / handyman ride on role + title until the
        # vendor_type check is widened (migration awaiting approval).
        vendor_type=storage_vendor_type(vtype),
        vendor_id=actor_member.get("vendor_id", "default_vendor"),
    )
    member_dict = member.dict()
    await (await get_db()).table("vendor_members").insert(member_dict).execute()
    member_dict["assigned_jobs"] = 0
    await write_audit_event(actor_id, "vendor_member", member_dict["id"], "create", vendor_id=member_dict["vendor_id"], details={"email": member_dict["email"]})
    return member_dict


@router.put("/team/{member_id}")
async def update_vendor_member(
    member_id: str,
    payload: VendorMemberUpdate,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
    update_data = {k: v for k, v in payload.dict().items() if v is not None}
    if not update_data:
        api_error(400, "No update data", "no_update_data")
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    res = await (await get_db()).table("vendor_members").update(update_data).eq("id", member_id).eq("vendor_id", vendor_id).execute()
    if not res.data:
        api_error(404, "Member not found", "member_not_found")
    
    member = res.data[0]
    res_count = await (await get_db()).table("jobs").select("*", count="exact").eq("vendor_id", vendor_id).eq("assignee_id", member_id).neq("status", "completed").execute()
    member["assigned_jobs"] = res_count.count or 0
    await write_audit_event(actor_id, "vendor_member", member_id, "update", vendor_id=vendor_id, details=update_data)
    return member


@router.delete("/team/{member_id}")
async def delete_vendor_member(
    member_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
    if member_id == "default_user":
        api_error(400, "Cannot remove default member", "cannot_remove_default_member")
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    
    res = await (await get_db()).table("vendor_members").delete().eq("id", member_id).eq("vendor_id", vendor_id).execute()
    if not res.data:
        api_error(404, "Member not found", "member_not_found")
    
    await (await get_db()).table("jobs").update({"assignee_id": "default_user", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("assignee_id", member_id).eq("vendor_id", vendor_id).execute()
    await write_audit_event(actor_id, "vendor_member", member_id, "delete", vendor_id=vendor_id)
    return {"deleted": True}


@router.get("/jobs")
async def get_vendor_os_jobs(
    status: Optional[str] = None,
    search: Optional[str] = None,
    assignee_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id") or ORG_VENDOR_ID
    aliases = photographer_assignee_ids(actor_id, actor_member)
    vendors = vendor_scope_ids(actor_id, actor_member)

    page, page_size, skip = normalize_pagination(page, page_size)
    jobs = []
    total = 0
    fair_use_jobs = False
    try:
        sb = await get_db()
        query = sb.table("jobs").select("*", count="exact")
        if status:
            query = query.eq("status", status)
        # Default: assigned jobs for this photographer (aliases include Kam 0c3fa7c2).
        # Owner Jobs page passes assignee_id=all so unassigned default_vendor jobs list.
        if is_vendor_admin(actor_member) and assignee_id == "all":
            query = query.or_(jobs_or_filter(vendors, aliases))
        elif is_vendor_admin(actor_member) and assignee_id == "unassigned":
            query = query.or_(jobs_or_filter(vendors, [])).or_(unassigned_assignee_or_filter())
        elif assignee_id and assignee_id not in ("all", "unassigned"):
            query = query.eq("assignee_id", assignee_id)
        else:
            query = query.in_("assignee_id", aliases or [actor_id])
        if search:
            query = query.or_(f"address.ilike.%{search}%,city.ilike.%{search}%")

        res = await query.order("scheduled_date", desc=False).range(skip, skip + page_size - 1).execute()
        jobs = res.data or []
        total = res.count or 0
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_jobs_for_assignees
        if not _is_rest_quota_error(e):
            raise
        fair_use_jobs = True
        # Fair-Use: list assigned jobs via Postgres/OS. Admin "all"/"unassigned"
        # scopes still collapse to this photographer's aliases under 402.
        want_ids = aliases or [actor_id]
        if assignee_id and assignee_id not in ("all", "unassigned"):
            want_ids = [assignee_id]
        jobs, total = pg_select_jobs_for_assignees(
            want_ids, status=status, limit=page_size, offset=skip
        )
        if search:
            q = (search or "").strip().lower()
            jobs = [
                j for j in jobs
                if q in str(j.get("address") or "").lower()
                or q in str(j.get("city") or "").lower()
            ]
            total = len(jobs)

    member_map = {actor_member.get("id"): actor_member.get("name", "Unknown")}
    member_full = {actor_member.get("id"): actor_member}
    listings = {}
    if not fair_use_jobs:
        try:
            db = await get_db()
            res_members = await db.table("vendor_members").select("id, name").in_("vendor_id", vendors).limit(500).execute()
            member_map = {m["id"]: m.get("name", "Unknown") for m in (res_members.data or [])}
            listings = await _listings_by_ids(db, [j.get("transaction_id") for j in jobs])
            res_full = await db.table("vendor_members").select("*").in_("vendor_id", vendors).limit(500).execute()
            member_full = {m["id"]: m for m in (res_full.data or [])}
            for job in jobs:
                job["assignee_name"] = member_map.get(job.get("assignee_id"), "Unassigned")
                _apply_listing(job, listings)
                m = member_full.get(job.get("assignee_id")) or actor_member
                cents = await photographer_payout_cents(db, job, m)
                job["payout_display_cad"] = round(cents / 100, 2)
                job["payout_model"] = "owner_full" if is_owner_member(m) else "ic_rate_card"
                if job["payout_model"] == "owner_full":
                    job["deduction"] = 0
        except Exception as e:
            from ..shared.pg_fallback import _is_rest_quota_error
            if not _is_rest_quota_error(e):
                raise
            fair_use_jobs = True

    if fair_use_jobs:
        # Skip listing/payout PostgREST enrichers — stamp Owner payout honestly.
        for job in jobs:
            m = member_full.get(job.get("assignee_id")) or actor_member
            job["assignee_name"] = member_map.get(job.get("assignee_id")) or m.get("name") or "Unassigned"
            job["payout_model"] = "owner_full" if is_owner_member(m) else "ic_rate_card"
            if job["payout_model"] == "owner_full":
                job["deduction"] = 0
                # Owner keeps rack; prefer package_price/rack_price when present.
                dollars = job.get("package_price") or job.get("rack_price") or 0
                try:
                    job["payout_display_cad"] = round(float(dollars), 2)
                except Exception:
                    job["payout_display_cad"] = 0
            else:
                job.setdefault("payout_display_cad", 0)

    payload = paginated_response(jobs, total, page, page_size)
    payload["jobs"] = jobs  # Swift JobStore decodes { jobs: [...] }
    if fair_use_jobs:
        payload["fair_use_pg_fallback"] = True
    return payload



@router.get("/jobs/{job_id}")
async def get_vendor_os_job(
    job_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    try:
        db = await get_db()
        job = await _load_visible_job(db, job_id, actor_id, actor_member, admin_all=is_vendor_admin(actor_member))
        listings = await _listings_by_ids(db, [job.get("transaction_id")])
        res_member = await db.table("vendor_members").select("id, name").eq("id", job.get("assignee_id") or "").maybe_single().execute()
        member_row = _maybe_single_row(res_member)
        job["assignee_name"] = member_row.get("name") or "Unassigned"
        return _apply_listing(job, listings)
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_job_by_id
        from ..shared.errors import api_error
        from ..shared.job_scope import photographer_assignee_ids as _aliases
        # _load_visible_job may raise api_error (HTTP) — only catch PostgREST quota
        if not _is_rest_quota_error(e):
            raise
        job = pg_select_job_by_id(job_id)
        if not job:
            api_error(404, "Job not found", "job_not_found")
        allowed = set(_aliases(actor_id, actor_member) or [actor_id])
        if not is_vendor_admin(actor_member) and str(job.get("assignee_id") or "") not in allowed:
            api_error(403, "You are not assigned to this job", "job_not_assigned_to_actor")
        job["assignee_name"] = actor_member.get("name") or "Unassigned"
        return job


@router.get("/listings")
async def search_office_listings(
    q: Optional[str] = None,
    job_id: Optional[str] = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Search Office listings (transactions) so a photographer can attach a shoot.

    Empty query returns []. Honest empty — not a fake Connected state.
    Scoped to the job's office when job_id is provided and jobs.brokerage_id is set.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    query = (q or "").strip()
    if not query:
        return {"items": []}
    if len(query) > 80:
        query = query[:80]
    query = query.replace("%", "").replace("_", "")

    db = await get_db()
    office_id = None
    if job_id:
        job_row = await _load_visible_job(db, job_id, actor_id, actor_member, admin_all=is_vendor_admin(actor_member))
        office_id = job_row.get("brokerage_id")

    if _is_uuid(query):
        lookup = db.table("transactions").select("id, property_address, city, deal_type, brokerage_id").eq("id", query)
        if office_id:
            lookup = lookup.eq("brokerage_id", office_id)
        res = await lookup.maybe_single().execute()
        pub = _listing_public(res.data if res else None)
        return {"items": [pub] if pub else []}

    if len(query) < 2:
        return {"items": []}

    search = db.table("transactions").select("id, property_address, city, deal_type").ilike("property_address", f"%{query}%")
    if office_id:
        search = search.eq("brokerage_id", office_id)
    res = await search.order("created_at", desc=True).limit(20).execute()
    items = [_listing_public(row) for row in (res.data or [])]
    return {"items": [i for i in items if i]}


@router.patch("/jobs/{job_id}/listing")
async def attach_job_listing(
    job_id: str,
    payload: JobListingUpdate,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Attach or detach this shoot to an Office listing via jobs.transaction_id."""
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id") or ORG_VENDOR_ID
    db = await get_db()
    job = await _load_visible_job(db, job_id, actor_id, actor_member, admin_all=is_vendor_admin(actor_member))
    if not is_vendor_admin(actor_member):
        ensure_assigned_to_actor(job, actor_id, actor_member)

    raw = payload.transaction_id
    txn_id = None if raw is None or str(raw).strip() == "" else str(raw).strip()
    listing = None
    if txn_id:
        if not _is_uuid(txn_id):
            api_error(400, "Invalid listing id", "invalid_listing_id")
        tq = db.table("transactions").select("id, property_address, city, deal_type, brokerage_id").eq("id", txn_id)
        office_id = job.get("brokerage_id")
        if office_id:
            tq = tq.eq("brokerage_id", office_id)
        t_res = await tq.maybe_single().execute()
        # maybe_single() is None when the listing/transaction row is missing —
        # None.data was a CORS-less 500 instead of the 404 below.
        txn_row = _maybe_single_row(t_res)
        if not txn_row:
            api_error(404, "Listing not found", "listing_not_found")
        listing = _listing_public(txn_row)

    now = datetime.now(timezone.utc).isoformat()
    await db.table("jobs").update({
        "transaction_id": txn_id,
        "updated_at": now,
    }).eq("id", job_id).execute()
    job["transaction_id"] = txn_id
    job["updated_at"] = now
    listings = {listing["id"]: listing} if listing else {}
    await write_audit_event(
        actor_id, "job", job_id, "attach_listing",
        vendor_id=vendor_id,
        details={"transaction_id": txn_id},
    )
    return _apply_listing(job, listings)


@router.post("/jobs")
async def create_job(
    payload: JobCreate,
    x_internal_api_key: str | None = Header(default=None, alias="X-Internal-Api-Key"),
):
    """Internal-only: creates a job with caller-supplied pricing (rack_price/
    package_price) and vendor/assignee assignment. Previously accepted any
    non-empty X-User-Id as an alternative to a valid internal key -- that
    identity was computed but never actually used anywhere in job_row, so the
    real effect was letting anyone insert an arbitrary job (including its
    payout-driving price fields) with zero real verification. Found via the
    same 2026-08-15 auth audit that fixed resolve_vendor_actor_id; this
    endpoint needed its own fix since it never routed through that function.
    Booking (Companion) is the only legitimate caller -- same requirement
    dispatch.py's own job-intake endpoint already correctly enforces."""
    from ..shared.auth import verify_internal_api_key
    if not verify_internal_api_key(x_internal_api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing internal API key")

    from ..services.payout_service import is_retired_for_new_jobs
    if is_retired_for_new_jobs(payload.package_id):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Package '{payload.package_id}' is retired for new jobs. "
                "Use photos_only / essentials / story / showcase / full (or suite). "
                "Legacy package_ids remain readable for historical payouts only."
            ),
        )

    job_id = payload.id or str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    job_row = {
        "id": job_id,
        "booking_id": payload.booking_id,
        "address": payload.address,
        "city": payload.city,
        "state": payload.state,
        "zip_code": payload.zip_code,
        "time_window_start": payload.time_window_start,
        "time_window_end": payload.time_window_end,
        "job_type": payload.job_type,
        "status": payload.status,
        "priority": payload.priority,
        "property_details": payload.property_details,
        "client_instructions": payload.client_instructions,
        "scheduled_date": payload.scheduled_date,
        "deliverables_count": payload.deliverables_count,
        "uploaded_count": payload.uploaded_count,
        "assignee_id": payload.assignee_id,
        "vendor_id": payload.vendor_id,
        "package_id": payload.package_id,
        "package_name": payload.package_name,
        "package_price": payload.package_price,
        "rack_price": payload.rack_price if payload.rack_price is not None else payload.package_price,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    from ..services.visit_checklist import ensure_visit_checklist
    from ..services.shoot_duration import estimate_shoot_duration_minutes
    job_row["checklist"] = ensure_visit_checklist(job_row)
    job_row["estimated_duration_min"] = estimate_shoot_duration_minutes(job_row)

    try:
        from ..services.vendor_profile import _missing_column
        insert_row = dict(job_row)
        result = None
        last_err = None
        for _ in range(6):
            try:
                result = await (await get_db()).table("jobs").insert(insert_row).execute()
                break
            except Exception as e:
                last_err = e
                miss = _missing_column(str(e), list(insert_row.keys()))
                if not miss:
                    raise
                insert_row.pop(miss, None)
        if result is None:
            raise last_err or RuntimeError("insert failed")
        return {"id": job_id, **insert_row}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create job: {type(e).__name__}: {str(e)}")


@router.put("/jobs/{job_id}/assign")
async def assign_job_to_member(
    job_id: str,
    payload: JobAssignmentUpdate,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    from ..shared.pg_fallback import (
        _is_rest_quota_error,
        pg_select_job_by_id,
        pg_select_vendor_member,
        pg_update_job,
    )

    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    db = await get_db()
    try:
        res_job = await db.table("jobs").select("*").eq("id", job_id).maybe_single().execute()
        existing_job = _maybe_single_row(res_job)
    except Exception as e:
        # Fair-Use: PostgREST job reads 402 — load via Postgres so Owner assign still works.
        if not _is_rest_quota_error(e):
            raise
        existing_job = pg_select_job_by_id(job_id) or {}
    member_id = (payload.member_id or "").strip() if payload.member_id is not None else ""
    target = None
    if member_id and member_id.lower() not in ("auto", "unassign", "none", "null"):
        try:
            res_member = await db.table("vendor_members").select("*").eq("id", member_id).maybe_single().execute()
            target = _maybe_single_row(res_member) or None
        except Exception as e:
            if not _is_rest_quota_error(e):
                raise
            target = pg_select_vendor_member(member_id)
    from ..services.commercial_jobs import refresh_company_policy
    await refresh_company_policy(db)
    status_code, err, patch = resolve_manual_assignment(
        actor_id=actor_id,
        actor_member=actor_member,
        job=existing_job if existing_job.get("id") else None,
        member_id=member_id or None,
        target_member=target,
    )
    if status_code == 403:
        api_error(403, "Insufficient permissions", err)
    if status_code == 404:
        api_error(404, "Job not found" if err == "job_not_found" else "Member not found", err)
    if status_code == 409:
        api_error(
            409,
            "Commercial jobs need a Tier A vendor with verified, unexpired drone liability "
            "insurance at or above the job minimum and Advanced RPAS",
            err,
        )
    vendor_id = str(existing_job.get("vendor_id") or actor_member.get("vendor_id") or ORG_VENDOR_ID)

    if err == "auto":
        new_assignee_id = await assign_vendor_for_job(job_id)
    else:
        try:
            await db.table("jobs").update(patch).eq("id", job_id).execute()
        except Exception as e:
            if not _is_rest_quota_error(e):
                raise
            if not pg_update_job(job_id, patch):
                raise HTTPException(
                    status_code=502,
                    detail="Job assign unavailable (Fair Use); try again shortly",
                )
        new_assignee_id = patch.get("assignee_id")
        if new_assignee_id:
            # Await on Vercel serverless — create_task / BackgroundTasks freeze after the
            # HTTP response (same hollow-notify bug as paid /book assign + Mark complete).
            try:
                from ..services.notifications import notify_assignment
                await notify_assignment({**existing_job, **patch, "id": job_id}, new_assignee_id)
            except Exception as _ne:
                logger.error("Assignment notify failed for job %s: %s", job_id, _ne)

    await write_audit_event(actor_id, "job", job_id, "assign", vendor_id=vendor_id, details={"from": existing_job.get("assignee_id"), "to": new_assignee_id, "method": "auto" if member_id == "auto" else "manual"})
    try:
        res_final = await db.table("jobs").select("*").eq("id", job_id).maybe_single().execute()
        final = _maybe_single_row(res_final)
    except Exception as e:
        if not _is_rest_quota_error(e):
            raise
        final = pg_select_job_by_id(job_id) or {}

    # Send booking confirmed email for manual assignments only.
    # Auto-assign path calls notify_booking_confirmed inside assign_vendor_for_job already;
    # duplicating it here would fire a second email. Skip entirely if auto-assign returned None.
    if member_id != "auto" and new_assignee_id:
        try:
            from ..services.client_comms import notify_booking_confirmed
            await notify_booking_confirmed(job_id)
        except Exception as _e:
            logger.error(f"Booking confirmed notification failed for job {job_id}: {_e}")

    if new_assignee_id:
        try:
            named = await db.table("vendor_members").select("id, name").eq("id", new_assignee_id).maybe_single().execute()
            final["assignee_name"] = _maybe_single_row(named).get("name") or "Unassigned"
        except Exception as e:
            if _is_rest_quota_error(e):
                named_row = pg_select_vendor_member(new_assignee_id) or {}
                final["assignee_name"] = named_row.get("name") or "Unassigned"
            else:
                final["assignee_name"] = "Unassigned"
    else:
        final["assignee_name"] = "Unassigned"

    booking_callback_url = os.environ.get("BOOKING_CALLBACK_URL")
    if not booking_callback_url:
        logger.warning("BOOKING_CALLBACK_URL not set — skipping assignment callback")
        return final
    callback_payload = {
        "booking_id": final.get("booking_id"),
        "job_id": job_id,
        "status": final.get("status", "assigned"),
        "completed_at": final.get("completed_at"),
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{booking_callback_url}/api/callbacks/job-status",
                json=callback_payload,
                headers={"Content-Type": "application/json"},
            )
    except Exception:
        pass

    return final


@router.get("/availability")
async def get_vendor_availability(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    from ..shared.pg_fallback import (
        _is_rest_quota_error,
        pg_select_vendor_availability,
        pg_upsert_vendor_availability,
    )

    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    member = await get_vendor_member(actor_id)
    vendor_id = member.get("vendor_id", "default_vendor")
    try:
        res = await (
            await get_db()
        ).table("vendor_availability").select("*").eq(
            "vendor_id", vendor_id
        ).eq("member_id", actor_id).maybe_single().execute()
        # maybe_single() is None when the member has no availability row yet
        # (every new GO photographer). None.data was a CORS-less 500 before the
        # default-create branch below could run.
        record = _maybe_single_row(res)
    except Exception as e:
        # Fair-Use: PostgREST availability read 402 — load/create via Postgres.
        if not _is_rest_quota_error(e):
            raise
        record = pg_select_vendor_availability(actor_id, vendor_id)
        if not record:
            default = AvailabilitySettings(member_id=actor_id, vendor_id=vendor_id).dict()
            pg_upsert_vendor_availability(default)
            return default
        return record
    if not record:
        # Empty REST body can also be a Fair-Use silent miss: do not insert a
        # duplicate (vendor_id, member_id) over a row Postgres already has.
        existing = pg_select_vendor_availability(actor_id, vendor_id)
        if existing:
            return existing
        default = AvailabilitySettings(member_id=actor_id, vendor_id=vendor_id).dict()
        try:
            await (await get_db()).table("vendor_availability").insert(default).execute()
        except Exception as e:
            if not _is_rest_quota_error(e):
                raise
            pg_upsert_vendor_availability(default)
        return default
    return record


@router.put("/availability")
async def update_vendor_availability(
    payload: AvailabilityUpdate,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    from ..shared.pg_fallback import (
        _is_rest_quota_error,
        pg_select_vendor_availability,
        pg_upsert_vendor_availability,
    )

    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    member = await get_vendor_member(actor_id)
    vendor_id = member.get("vendor_id", "default_vendor")
    if payload.jobs_per_day < 0:
        api_error(400, "jobs_per_day must be non-negative", "invalid_capacity")
    update_data = payload.dict()
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    row = {
        "vendor_id": vendor_id,
        "member_id": actor_id,
        **update_data,
    }

    try:
        await (await get_db()).table("vendor_availability").upsert(row).execute()
    except Exception as e:
        if not _is_rest_quota_error(e):
            raise
        upserted = pg_upsert_vendor_availability(row)
        try:
            await write_audit_event(
                actor_id,
                "availability",
                actor_id,
                "update",
                vendor_id=vendor_id,
                details=update_data,
            )
        except Exception:
            pass
        return upserted or pg_select_vendor_availability(actor_id, vendor_id)

    try:
        await write_audit_event(
            actor_id,
            "availability",
            actor_id,
            "update",
            vendor_id=vendor_id,
            details=update_data,
        )
    except Exception:
        pass

    try:
        res = await (
            await get_db()
        ).table("vendor_availability").select("*").eq(
            "vendor_id", vendor_id
        ).eq("member_id", actor_id).maybe_single().execute()
        saved = _maybe_single_row(res)
        if saved:
            return saved
    except Exception as e:
        if not _is_rest_quota_error(e):
            raise
    return pg_select_vendor_availability(actor_id, vendor_id) or row




@router.get("/public/availability-days")
async def public_availability_days(
    start: str | None = None,
    days: int = 42,
    area: str = "",
    tz: str = "America/Toronto",
):
    """Per-day booking availability aggregated from photographers' linked calendars,
    work hours and booked jobs. Public (no PII): only dates + open counts."""
    from datetime import date as _date
    from ..services.vendor_busy_sync import availability_days
    try:
        d0 = _date.fromisoformat((start or "")[:10]) if start else datetime.now(timezone.utc).date()
    except ValueError:
        raise HTTPException(status_code=400, detail="start must be YYYY-MM-DD")
    try:
        return await availability_days(await get_db(), d0, int(days or 42), (area or "")[:120], tz or "America/Toronto")
    except Exception as e:
        logger.warning("availability-days soft-fail: %s", e)
        return {"ok": False, "source": "unavailable", "days": []}


def _cron_authorized(authorization: str | None, x_internal_key: str | None) -> bool:
    secret = (os.environ.get("CRON_SECRET") or "").strip()
    if secret and (authorization or "").strip() == f"Bearer {secret}":
        return True
    keys = [k.strip() for k in (os.environ.get("INTERNAL_API_KEYS") or "").split(",") if k.strip()]
    return bool(x_internal_key and x_internal_key.strip() in keys)


@router.get("/calendars/sync-cron")
async def calendars_sync_cron(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_internal_key: str | None = Header(default=None, alias="X-Internal-Key"),
):
    """Vercel cron: refresh synced free/busy for every linked photographer calendar."""
    if not _cron_authorized(authorization, x_internal_key):
        raise HTTPException(status_code=401, detail="unauthorized")
    from ..services.vendor_busy_sync import sync_all
    res = await sync_all(await get_db())
    return {"ok": res.get("ok"), "members": res.get("members")}


@router.post("/calendars/sync")
async def calendars_sync_self(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Sync the signed-in photographer's linked calendars now."""
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    from ..services.vendor_busy_sync import sync_member
    member = await get_vendor_member(actor_id)
    return await sync_member(await get_db(), actor_id, member)


@router.get("/calendars")
async def list_vendor_calendars(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """List calendar connection status for the actor — honest Connected only."""
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    from ..services.vendor_calendars import list_vendor_calendar_status, google_oauth_configured, microsoft_oauth_configured
    db = await get_db()
    statuses = await list_vendor_calendar_status(db, actor_id)
    from ..services.vendor_calendars import google_connect_via, google_direct_oauth
    return {
        "connections": statuses,
        "oauth": {
            "google_configured": google_oauth_configured(),
            "microsoft_configured": microsoft_oauth_configured(),
            "google_connect_via": google_connect_via(),
            "google_direct_oauth": google_direct_oauth(),
        },
        "copy": (
            "Connecting a calendar tells Bricks when you’re free so auto-assign "
            "only offers jobs that fit your real schedule. Busy on any connected "
            "calendar = you won’t get that slot. Manual hours still apply if no "
            "calendar is connected."
        ),
    }


@router.post("/calendars/{provider}/connect")
async def connect_vendor_calendar(
    provider: str,
    payload: dict | None = Body(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Start OAuth or save CalDAV/ICS credentials. Never fakes Connected."""
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    member = await get_vendor_member(actor_id)
    vendor_id = member.get("vendor_id", "default_vendor")
    provider = (provider or "").strip().lower()
    payload = payload or {}
    from ..services.vendor_calendars import (
        google_oauth_configured,
        connection_status,
        PROVIDERS,
    )
    if provider not in PROVIDERS:
        api_error(400, "Unknown calendar provider", "invalid_provider")

    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()

    if provider == "google":
        from ..services.vendor_calendars import (
            google_redirect_uri,
            build_google_authorize_url,
            build_google_supabase_connect_payload,
            google_connect_via,
            google_direct_oauth,
            google_calendar_ping,
            upsert_oauth_connection,
            GOOGLE_CAL_SCOPE,
        )
        if not google_oauth_configured():
            return {
                "connected": False,
                "status": "needs_env",
                "detail": "Google OAuth client id not configured (needs GOOGLE_CLIENT_ID plus Supabase or a client secret).",
                "oauth_url": None,
                "mode": None,
                "connect_via": None,
            }
        # Persist tokens only when supplied — Connected only after real tokens (and ping on complete-session).
        if payload.get("access_token"):
            from ..services.calendar_token_crypto import encrypt_connection_secrets
            from ..services.vendor_calendars import _expires_at_iso
            row = {
                "member_id": actor_id,
                "vendor_id": vendor_id,
                "provider": "google",
                "access_token": payload.get("access_token"),
                "refresh_token": payload.get("refresh_token"),
                "token_expires_at": _expires_at_iso(payload.get("expires_in") or 3600),
                "updated_at": now,
                "created_at": now,
            }
            stored = encrypt_connection_secrets(row)
            try:
                await db.table("vendor_calendar_connections").upsert(stored, on_conflict="member_id,provider").execute()
            except Exception as e:
                from ..shared.pg_fallback import (
                    _is_rest_quota_error,
                    pg_upsert_vendor_calendar_connection,
                )
                if _is_rest_quota_error(e):
                    if not pg_upsert_vendor_calendar_connection(stored):
                        return {
                            "connected": False,
                            "status": "error",
                            "detail": "Could not save calendar connection (Fair-Use).",
                        }
                else:
                    logger.warning("calendar upsert soft-fail: %s", e)
            return connection_status(stored)

        via = google_connect_via()
        # Default: Supabase-mediated Google OAuth (secret lives in Supabase).
        if via == "supabase":
            body = build_google_supabase_connect_payload()
            return {
                "connected": False,
                "status": "oauth_ready",
                "detail": "Continue with Google to grant calendar access (via Supabase).",
                "oauth_url": None,
                **body,
            }
        # Optional direct Google authorize URL when client secret is on Vercel.
        redirect = google_redirect_uri()
        if not redirect:
            return {
                "connected": False,
                "status": "needs_env",
                "detail": "GOOGLE_OAUTH_REDIRECT_URI is not set — cannot start direct Google OAuth.",
                "oauth_url": None,
                "mode": None,
                "connect_via": "direct" if google_direct_oauth() else None,
            }
        oauth_url = build_google_authorize_url(actor_id)
        return {
            "connected": False,
            "status": "oauth_ready",
            "detail": "Open Google OAuth to connect",
            "oauth_url": oauth_url,
            "mode": "direct",
            "connect_via": "direct",
            "scope": GOOGLE_CAL_SCOPE,
            "redirect_uri": redirect,
        }

    if provider == "microsoft":
        # Outlook Connect not offered to vendors (Azure/M365 free signup requires a card).
        # FreeBusy + disconnect paths remain for any legacy connection.
        return {
            "connected": False,
            "status": "unavailable",
            "detail": "Outlook Connect isn’t offered without an Azure directory (no card). Use Google, Apple, or an ICS feed.",
            "oauth_url": None,
        }

    if provider == "apple":
        caldav_url = (payload.get("caldav_url") or "").strip()
        password = (payload.get("app_password") or payload.get("caldav_password") or "").strip()
        username = (payload.get("username") or member.get("email") or "").strip()
        if not caldav_url or not password:
            return {
                "connected": False,
                "status": "needs_app_password",
                "detail": "Apple Calendar needs an iCloud CalDAV URL and an app-specific password. Connected is only shown after both are saved.",
            }
        from ..services.calendar_token_crypto import encrypt_connection_secrets
        row = {
            "member_id": actor_id,
            "vendor_id": vendor_id,
            "provider": "apple",
            "caldav_url": caldav_url,
            "caldav_password": password,
            "caldav_username": username,
            "updated_at": now,
            "created_at": now,
        }
        stored = encrypt_connection_secrets(row)
        try:
            await db.table("vendor_calendar_connections").upsert(stored, on_conflict="member_id,provider").execute()
        except Exception as e:
            from ..shared.pg_fallback import (
                _is_rest_quota_error,
                pg_upsert_vendor_calendar_connection,
            )
            if _is_rest_quota_error(e):
                if not pg_upsert_vendor_calendar_connection(stored):
                    return {
                        "connected": False,
                        "status": "error",
                        "detail": "Could not save calendar connection (Fair-Use).",
                    }
            else:
                logger.warning("calendar upsert soft-fail: %s", e)
                return {"connected": False, "status": "error", "detail": f"Could not save CalDAV connection: {e}"}
        return connection_status(stored)

    if provider == "ics":
        ics_url = (payload.get("ics_url") or "").strip()
        if not ics_url:
            return {"connected": False, "status": "not_connected", "detail": "Paste a public ICS calendar URL"}
        row = {
            "member_id": actor_id,
            "vendor_id": vendor_id,
            "provider": "ics",
            "ics_url": ics_url,
            "updated_at": now,
            "created_at": now,
        }
        try:
            await db.table("vendor_calendar_connections").upsert(row, on_conflict="member_id,provider").execute()
        except Exception as e:
            from ..shared.pg_fallback import (
                _is_rest_quota_error,
                pg_upsert_vendor_calendar_connection,
            )
            if _is_rest_quota_error(e):
                if not pg_upsert_vendor_calendar_connection(row):
                    return {
                        "connected": False,
                        "status": "error",
                        "detail": "Could not save calendar connection (Fair-Use).",
                    }
            else:
                logger.warning("calendar upsert soft-fail: %s", e)
                return {"connected": False, "status": "error", "detail": f"Could not save ICS URL: {e}"}
        return connection_status(row)

    api_error(400, "Unsupported provider", "invalid_provider")


class GoogleCalendarSessionComplete(BaseModel):
    provider_token: Optional[str] = None
    provider_refresh_token: Optional[str] = None
    scope: Optional[str] = None


@router.post("/calendars/google/complete-session")
async def google_calendar_complete_session(
    body: GoogleCalendarSessionComplete,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Persist Google tokens from a Supabase OAuth session.

    Never mark Connected without a refresh token + successful Calendar ping.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    member = await get_vendor_member(actor_id)
    vendor_id = member.get("vendor_id", "default_vendor")
    from ..services.vendor_calendars import (
        google_calendar_ping,
        upsert_oauth_connection,
        connection_status,
        GOOGLE_CAL_SCOPE,
    )

    access = (body.provider_token or "").strip()
    refresh = (body.provider_refresh_token or "").strip()
    scope = (body.scope or "").strip()
    if not refresh:
        return {
            "connected": False,
            "status": "noscope",
            "detail": "Google Calendar permission is required (missing refresh token). Try Connect again and approve calendar access.",
        }
    if not access:
        return {
            "connected": False,
            "status": "error",
            "detail": "Could not connect Google Calendar (missing access token).",
        }
    if scope and "calendar" not in scope:
        return {
            "connected": False,
            "status": "noscope",
            "detail": "Google Calendar permission is required. Approve calendar access and retry.",
        }
    ping = await google_calendar_ping(access)
    if not ping.get("ok"):
        logger.warning("Google complete-session ping failed (%s via %s)", ping.get("status"), ping.get("via"))
        if ping.get("status") in (401, 403):
            return {
                "connected": False,
                "status": "noscope",
                "detail": "Google Calendar permission is required. Approve calendar access and retry.",
            }
        return {
            "connected": False,
            "status": "error",
            "detail": "Could not verify Google Calendar access. Try again.",
        }

    db = await get_db()
    try:
        row = await upsert_oauth_connection(
            db,
            member_id=actor_id,
            vendor_id=vendor_id,
            provider="google",
            token_payload={
                "access_token": access,
                "refresh_token": refresh,
                "expires_in": 3600,
                "scope": scope or GOOGLE_CAL_SCOPE,
            },
        )
    except Exception as e:
        logger.warning("Google complete-session upsert failed: %s", e)
        return {"connected": False, "status": "error", "detail": f"Could not save calendar connection: {e}"}
    status = connection_status(row)
    status["ping"] = ping.get("via")
    status["connect_via"] = "supabase"
    return status


@router.get("/calendars/google/callback")
async def google_calendar_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """Google OAuth redirect — exchange code for tokens, then send browser to Availability."""
    from urllib.parse import urlencode
    from ..services.vendor_calendars import (
        calendar_success_redirect_base,
        exchange_google_code,
        google_direct_oauth,
        upsert_oauth_connection,
    )

    base = calendar_success_redirect_base()

    def _ok() -> RedirectResponse:
        return RedirectResponse(f"{base}?{urlencode({'calendar_connected': 'google'})}", status_code=302)

    def _err(msg: str) -> RedirectResponse:
        return RedirectResponse(f"{base}?{urlencode({'calendar_error': msg[:240]})}", status_code=302)

    if error:
        return _err(error_description or error)
    if not code or not state:
        return _err("Missing OAuth code or state (member id)")
    if not google_direct_oauth():
        return _err("Direct Google OAuth requires GOOGLE_CLIENT_SECRET on the API (use Supabase Connect instead)")

    try:
        tokens = await exchange_google_code(code)
    except Exception as e:
        logger.warning("Google calendar OAuth exchange failed: %s", e)
        return _err(str(e) or "Google token exchange failed")

    member_id = str(state).strip()
    vendor_id = "default_vendor"
    db = await get_db()
    try:
        row = _maybe_single_row(
            await db.table("vendor_members").select("id,vendor_id").eq("id", member_id).maybe_single().execute()
        )
        if not row:
            return _err("Unknown vendor member in OAuth state")
        vendor_id = row.get("vendor_id") or vendor_id
    except Exception as e:
        logger.warning("OAuth member lookup failed: %s", e)
        return _err("Could not resolve vendor member for OAuth state")
    from ..services.vendor_calendars import google_calendar_ping
    ping = await google_calendar_ping(tokens.get("access_token") or "")
    if not ping.get("ok"):
        return _err("Google Calendar permission could not be verified after OAuth")
    try:
        await upsert_oauth_connection(
            db,
            member_id=member_id,
            vendor_id=vendor_id,
            provider="google",
            token_payload=tokens,
        )
    except Exception as e:
        logger.warning("Google calendar upsert failed: %s", e)
        return _err(f"Connected tokens could not be saved: {e}")
    return _ok()


@router.get("/calendars/microsoft/callback")
async def microsoft_calendar_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """Microsoft OAuth redirect — exchange code for tokens, then send browser to Availability."""
    from urllib.parse import urlencode
    from ..services.vendor_calendars import (
        calendar_success_redirect_base,
        exchange_microsoft_code,
        microsoft_oauth_configured,
        upsert_oauth_connection,
    )

    base = calendar_success_redirect_base()

    def _ok() -> RedirectResponse:
        return RedirectResponse(f"{base}?{urlencode({'calendar_connected': 'microsoft'})}", status_code=302)

    def _err(msg: str) -> RedirectResponse:
        return RedirectResponse(f"{base}?{urlencode({'calendar_error': msg[:240]})}", status_code=302)

    if error:
        return _err(error_description or error)
    if not code or not state:
        return _err("Missing OAuth code or state (member id)")
    if not microsoft_oauth_configured():
        return _err("Microsoft OAuth client id/secret not configured on this environment")

    try:
        tokens = await exchange_microsoft_code(code)
    except Exception as e:
        logger.warning("Microsoft calendar OAuth exchange failed: %s", e)
        return _err(str(e) or "Microsoft token exchange failed")

    member_id = str(state).strip()
    vendor_id = "default_vendor"
    db = await get_db()
    try:
        row = _maybe_single_row(
            await db.table("vendor_members").select("id,vendor_id").eq("id", member_id).maybe_single().execute()
        )
        if not row:
            return _err("Unknown vendor member in OAuth state")
        vendor_id = row.get("vendor_id") or vendor_id
    except Exception as e:
        logger.warning("OAuth member lookup failed: %s", e)
        return _err("Could not resolve vendor member for OAuth state")
    try:
        await upsert_oauth_connection(
            db,
            member_id=member_id,
            vendor_id=vendor_id,
            provider="microsoft",
            token_payload=tokens,
        )
    except Exception as e:
        logger.warning("Microsoft calendar upsert failed: %s", e)
        return _err(f"Connected tokens could not be saved: {e}")
    return _ok()


@router.delete("/calendars/{provider}")
async def disconnect_vendor_calendar(
    provider: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    provider = (provider or "").strip().lower()
    db = await get_db()
    try:
        await db.table("vendor_calendar_connections").delete().eq("member_id", actor_id).eq("provider", provider).execute()
    except Exception as e:
        from ..shared.pg_fallback import (
            _is_rest_quota_error,
            pg_delete_vendor_calendar_connection,
        )
        if _is_rest_quota_error(e):
            if not pg_delete_vendor_calendar_connection(actor_id, provider):
                return {
                    "connected": True,
                    "provider": provider,
                    "status": "error",
                    "detail": "Could not disconnect calendar (Fair-Use).",
                }
        else:
            logger.warning("calendar disconnect soft-fail: %s", e)
    return {"connected": False, "provider": provider, "status": "not_connected", "detail": "Disconnected"}


@router.get("/performance")
async def get_vendor_performance(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    
    res_members = await (await get_db()).table("vendor_members").select("id").eq("vendor_id", vendor_id).eq("is_active", True).limit(500).execute()
    member_ids = [m["id"] for m in res_members.data] or ["default_user"]
    
    res_jobs = await (await get_db()).table("jobs").select("*").eq("vendor_id", vendor_id).in_("assignee_id", member_ids).limit(500).execute()
    jobs = res_jobs.data
    
    today = datetime.now(timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    week_jobs = []
    completed = []
    for job in jobs:
        try:
            scheduled = datetime.strptime(job.get("scheduled_date", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if week_start <= scheduled <= week_end:
            week_jobs.append(job)
            if job.get("status") in ("completed", "delivered"):
                completed.append(job)
    # Use the authoritative on_time_rate from vendor_members — updated by update_vendor_job_status
    # at the moment a job is marked `completed` (when updated_at is the actual completion time).
    # Recomputing from job.updated_at here is inaccurate for delivered jobs where updated_at
    # reflects delivery time (hours later), not completion time.
    res_member = await (await get_db()).table("vendor_members").select("on_time_rate").eq("id", actor_id).maybe_single().execute()
    on_time_rate_pct = round((_maybe_single_row(res_member).get("on_time_rate") or 0) * 100, 1)
    return {
        "completion_rate": round((len(completed) / len(week_jobs) * 100), 1) if week_jobs else 0.0,
        "on_time_rate": on_time_rate_pct,
        "job_volume_week": len(week_jobs),
    }


@router.get("/settings")
async def get_vendor_settings(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    res = await (await get_db()).table("vendor_settings").select("*").eq("vendor_id", vendor_id).maybe_single().execute()
    # New vendors have no settings row yet — None.data was a CORS-less 500
    # before the default-insert branch could run.
    settings = _maybe_single_row(res)
    if not settings:
        default = VendorSettings(vendor_id=vendor_id).dict()
        await (await get_db()).table("vendor_settings").insert(default).execute()
        return default
    return settings


@router.put("/settings")
async def update_vendor_settings(
    payload: VendorSettingsUpdate,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    update_data = {k: v for k, v in payload.dict().items() if v is not None}
    if not update_data:
        api_error(400, "No update data", "no_update_data")
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    await (await get_db()).table("vendor_settings").upsert({
        "vendor_id": vendor_id,
        **update_data
    }).execute()
    
    await write_audit_event(actor_id, "settings", vendor_id, "update", vendor_id=vendor_id, details=update_data)
    res = await (await get_db()).table("vendor_settings").select("*").eq("vendor_id", vendor_id).maybe_single().execute()
    # Upsert already wrote; a null re-read must not 500 — return merged defaults.
    row = _maybe_single_row(res)
    if row:
        return row
    return {**VendorSettings(vendor_id=vendor_id).dict(), **update_data}


# ---------------------------------------------------------------------------
# Stripe Connect payout onboarding
#
# ADDED 2026-08-16: create_connect_account_and_link existed in
# stripe_connect_service.py but was never reachable from any route or UI --
# no vendor beyond whoever was manually set up via the Supabase dashboard
# had any way to connect a Stripe payout account, meaning
# handle_payout_to_vendor would fail for every new vendor's first payout.
# ---------------------------------------------------------------------------

@router.get("/stripe-connect/status")
async def get_stripe_connect_status(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Honest Connected = Stripe-verified payout-ready (details_submitted + payouts_enabled).
    Shadowed at boot by stripe_connect_status router; keep body honest if ever remounted."""
    from ..routers.stripe_connect_status import get_stripe_connect_status as _honest
    return await _honest(authorization=authorization, x_user_id=x_user_id)


class StripeConnectOnboardRequest(BaseModel):
    return_url: str


@router.post("/stripe-connect/onboard")
async def start_stripe_connect_onboarding(
    payload: StripeConnectOnboardRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Generate a Stripe Connect Express onboarding link for this vendor org.
    Caller (the web dashboard) supplies return_url = its own current page,
    since it's the only side that reliably knows its own deployed origin."""
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    # Active Photographer (post-approval) + Manager/Owner/Admin may start Connect.
    # Each photographer gets their own Express account (contractors.member_id).
    if not can_access_stripe_connect(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
    vendor_id = actor_member.get("vendor_id", "default_vendor")

    if not os.environ.get("STRIPE_SECRET_KEY"):
        api_error(503, "Payouts are not configured yet", "stripe_not_configured")

    from ..services.stripe_connect_service import connect_member_key, create_connect_account_and_link
    try:
        url = await create_connect_account_and_link(
            vendor_id=vendor_id,
            refresh_url=payload.return_url,
            return_url=payload.return_url,
            full_name=actor_member.get("name", ""),
            email=actor_member.get("email", ""),
            member_id=connect_member_key(actor_member),
        )
    except ValueError as e:
        api_error(502, str(e), "stripe_connect_failed")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("stripe-connect/onboard failed for member %s: %s", actor_member.get("id"), e)
        api_error(502, "Could not open Stripe payout setup. Try again in a minute.", "stripe_connect_failed")

    await write_audit_event(actor_id, "stripe_connect", vendor_id, "onboard_started", vendor_id=vendor_id)
    return {"url": url}



@router.post("/push-token")
async def register_vendor_os_push_token(
    payload: dict,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Swift GO PushNotificationService POSTs device_token + platform=ios."""
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    token = str(payload.get("device_token") or payload.get("token") or "").strip()
    if not token:
        api_error(400, "device_token is required", "token_required")
    platform = str(payload.get("platform") or "ios").strip() or "ios"
    from ..services.notifications import store_push_token
    try:
        return await store_push_token(actor_id, token, platform)
    except Exception:
        api_error(503, "Could not store push token", "push_token_store_failed")


@router.get("/earnings")
async def get_vendor_os_earnings(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Owner sees full package and $0 deduction. ICs see the fixed rate card, never % of rack."""
    return await earnings_impl(authorization, x_user_id)


@router.get("/audit-events")
async def get_audit_events(
    entity_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    
    sb = await get_db(); query = sb.table("audit_events").select("*", count="exact").eq("vendor_id", vendor_id)
    if entity_type:
        query = query.eq("entity_type", entity_type)
        
    page, page_size, skip = normalize_pagination(page, page_size)
    res = await query.order("created_at", desc=True).range(skip, skip + page_size - 1).execute()
    items = res.data
    total = res.count or 0
    
    return paginated_response(items, total, page, page_size)


async def _refresh_decline_rate(db, assignee_id: str, now: str, job_id: str) -> None:
    """Recompute rolling 30-day decline_rate; REST→PG under Fair Use. Soft-fail."""
    try:
        from ..shared.pg_fallback import (
            _is_rest_quota_error,
            pg_select_jobs_for_assignees,
            pg_update_vendor_member_fields,
        )
        _thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        recent_jobs: list = []
        try:
            res_recent = await db.table("jobs").select("id, status, created_at").eq(
                "assignee_id", assignee_id
            ).gte("created_at", _thirty_days_ago).execute()
            recent_jobs = res_recent.data or []
        except Exception as e:
            if not _is_rest_quota_error(e):
                raise
            jobs, _total = pg_select_jobs_for_assignees(
                [assignee_id], status=None, limit=500, offset=0
            )
            recent_jobs = [
                j for j in (jobs or [])
                if str(j.get("created_at") or "") >= _thirty_days_ago
            ]
        recent_declined = sum(1 for j in recent_jobs if j.get("status") == "declined")
        total_recent = len(recent_jobs)
        new_rate = round(recent_declined / total_recent, 4) if total_recent > 0 else 0.0
        fields = {"decline_rate": new_rate, "updated_at": now}
        try:
            await db.table("vendor_members").update(fields).eq("id", assignee_id).execute()
        except Exception as e:
            if not _is_rest_quota_error(e):
                raise
            if not pg_update_vendor_member_fields(assignee_id, fields):
                logger.error("decline_rate PG update failed for job %s", job_id)
    except Exception as e:
        logger.error(f"decline_rate update failed for job {job_id}: {e}")


@router.api_route("/jobs/{job_id}/status", methods=["PUT", "PATCH"])
async def update_vendor_job_status(
    job_id: str,
    payload: JobStatusUpdate,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id", "default_vendor")

    db = await get_db()
    job = await _load_visible_job(db, job_id, actor_id, actor_member, admin_all=False)
    # FIXED 2026-08-16: this endpoint (the one the mobile app actually calls,
    # confirmed via app/job/[id].tsx) never checked the caller was the
    # assigned photographer -- only vendor_id scope. Any active member of the
    # same vendor org could mark ANY other member's job in_progress/
    # completed/declined, corrupting that member's decline_rate/on_time_rate
    # and prematurely triggering post-production/payout on a job they never
    # touched. The sibling watch-action endpoint below already enforces this
    # correctly; ensure_assigned_to_actor existed in shared/auth.py unused.
    ensure_assigned_to_actor(job, actor_id, actor_member)
    old_status = job.get("status")
    new_status = payload.status
    booking_id = job.get("booking_id")

    _IMMUTABLE_STATUSES = {"cancelled", "delivered", "agent_no_show", "dispatch_failed", "qc_failed"}
    if old_status in _IMMUTABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"Job is in terminal state '{old_status}' — status cannot be changed")

    # Only these statuses may be set by a vendor via this endpoint.
    # "delivered" is set exclusively by the post-production pipeline
    # (_finalize_job_if_complete) which also handles payout and delivery notification.
    _VENDOR_SETTABLE_STATUSES = {"in_progress", "completed", "declined"}
    if new_status not in _VENDOR_SETTABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Status '{new_status}' cannot be set via this endpoint — it is managed by the system",
        )


    # Trade jobs: every checklist item ticked with its proof (Kam, 2026-10-31).
    # Media jobs return None here and keep their completion rules.
    if new_status == "completed" and old_status != "completed":
        from ..services.job_checklist import completion_block_message
        blocked = completion_block_message(job)
        if blocked:
            api_error(400, blocked, "checklist_incomplete")

    # Hard gate: HDR stills need full −/0/+ brackets before Mark complete.
    # Mirrors web validateHdrUploadsForComplete; Visit/3D + drone/reel excluded.
    if new_status == "completed" and old_status != "completed":
        try:
            from ..services.bracket_require import validate_hdr_uploads_for_complete
            from ..shared.pg_fallback import _is_rest_quota_error, pg_select_uploads_by_job_id
            uploads_rows: list = []
            try:
                res_up = await db.table("uploads").select(
                    "id, filename, mime_type, provider, file_url"
                ).eq("job_id", job_id).limit(500).execute()
                uploads_rows = res_up.data or []
            except Exception as e:
                if not _is_rest_quota_error(e):
                    raise
                uploads_rows = pg_select_uploads_by_job_id(job_id) or []
            bracket_issue = validate_hdr_uploads_for_complete(uploads_rows)
            if bracket_issue:
                api_error(400, bracket_issue["message"], "incomplete_hdr_brackets")
        except HTTPException:
            raise
        except Exception as e:
            logger.error("HDR bracket gate failed for job %s: %s", job_id, e)
            # Fail closed — do not mark complete when we cannot verify brackets.
            api_error(
                503,
                "Could not verify HDR brackets; try again shortly",
                "bracket_gate_unavailable",
            )

    now = datetime.now(timezone.utc).isoformat()
    status_fields = {"status": new_status, "updated_at": now}
    try:
        await db.table("jobs").update(status_fields).eq("id", job_id).execute()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_update_job
        if not _is_rest_quota_error(e):
            raise
        if not pg_update_job(job_id, status_fields):
            raise HTTPException(
                status_code=503,
                detail="Job status update unavailable (Fair Use); try again shortly",
            )

    # When vendor marks completed, run post-production only when uploads are
    # either not expected (deliverables_count=0) or already fully received.
    # For jobs with a count, uploads.py fires post-production when the count is
    # reached — this path is a safe fallback due to the atomic gate in process_job_assets.
    # AWAIT on Vercel serverless (same class of bug as assigned notify): create_task /
    # BackgroundTasks are frozen when the HTTP response returns, which left Mark
    # complete advancing status while enhance→listing never ran (hollow media_delivery).
    if new_status == "completed" and old_status != "completed":
        _deliverables = job.get("deliverables_count") or 0
        _uploaded = job.get("uploaded_count") or 0
        if _deliverables == 0 or _uploaded >= _deliverables:
            try:
                from ..services.post_production import process_job_assets
                await process_job_assets(job_id)
            except Exception as e:
                logger.error(f"Post-production trigger failed for job {job_id}: {e}")

    if new_status == "declined" and old_status in ("pending_assignment", "assigned"):
        # Rolling 30-day decline rate (per IC agreement §40% threshold).
        # Denominator = ALL jobs offered to this vendor in the last 30 days.
        # REST→PG under Fair Use; soft-fail — never block the status response.
        assignee_id = job.get("assignee_id")
        if assignee_id and assignee_id != "default_user":
            await _refresh_decline_rate(db, assignee_id, now, job_id)

    if new_status in ("completed", "delivered") and old_status not in ("completed", "delivered"):
        try:
            from ..shared.pg_fallback import (
                _is_rest_quota_error,
                pg_select_vendor_member,
                pg_update_vendor_member_fields,
            )
            assignee_id = job.get("assignee_id")
            if assignee_id and assignee_id != "default_user":
                time_end = job.get("time_window_end", "")
                sched_date = job.get("scheduled_date", "")
                member_row = None
                try:
                    res_m = await db.table("vendor_members").select(
                        "total_assignments, on_time_rate"
                    ).eq("id", assignee_id).maybe_single().execute()
                    member_row = _maybe_single_row(res_m) or None
                except Exception as e:
                    if not _is_rest_quota_error(e):
                        raise
                    member_row = pg_select_vendor_member(assignee_id) or None
                if member_row:
                    prev_total = member_row.get("total_assignments") or 0
                    prev_rate = member_row.get("on_time_rate") or 1.0
                    new_total = prev_total + 1
                    update_fields: dict = {"total_assignments": new_total, "updated_at": now}
                    if sched_date and time_end:
                        # Only update on_time_rate when schedule data exists; updating with
                        # was_on_time=False when data is missing would unfairly penalise the vendor.
                        deadline = datetime.strptime(
                            f"{sched_date} {time_end}", "%Y-%m-%d %H:%M"
                        ).replace(tzinfo=zoneinfo_for_job(job))
                        # Use vendor-submitted completed_at if provided and not in the future.
                        # Vendors submit from their device when the shoot ends — using
                        # API call time unfairly penalises late status submissions.
                        _reported_at = None
                        if payload.completed_at:
                            try:
                                _reported_at = datetime.fromisoformat(
                                    payload.completed_at.replace("Z", "+00:00")
                                )
                            except ValueError:
                                pass
                        _completion_time = min(
                            _reported_at if _reported_at else datetime.now(timezone.utc),
                            datetime.now(timezone.utc),
                        )
                        was_on_time = _completion_time <= deadline
                        prev_on_time = round(prev_rate * prev_total)
                        new_on_time = prev_on_time + (1 if was_on_time else 0)
                        update_fields["on_time_rate"] = round(new_on_time / new_total, 4)
                    try:
                        await db.table("vendor_members").update(update_fields).eq("id", assignee_id).execute()
                    except Exception as e:
                        if not _is_rest_quota_error(e):
                            raise
                        if not pg_update_vendor_member_fields(assignee_id, update_fields):
                            logger.error("on_time_rate PG update failed for job %s", job_id)
        except Exception as e:
            logger.error(f"on_time_rate update failed for job {job_id}: {e}")

    if new_status == "in_progress" and old_status != "in_progress":
        try:
            from ..services.client_comms import notify_photographer_en_route
            await notify_photographer_en_route(job_id)
        except Exception as e:
            logger.error(f"En-route notification failed for job {job_id}: {e}")

    if booking_id:
        await send_dispatch_status_update_webhook(
            booking_id=booking_id,
            job_id=job_id,
            new_status=new_status,
            previous_status=old_status,
            actor_id=actor_id,
            actor_type="vendor_member"
        )

    return {"status": "updated", "job_id": job_id, "new_status": new_status}


# ---------------------------------------------------------------------------
# watchOS quick-action endpoint
# ---------------------------------------------------------------------------

class WatchActionRequest(BaseModel):
    action: str  # "accept" | "decline" | "on_way" | "arrived"


_WATCH_ACTION_STATUS_MAP = {
    "accept":  "assigned",
    "decline": "declined",
    "on_way":  "in_progress",
    "arrived": "in_progress",
}


@router.post("/jobs/{job_id}/watch-action")
async def watch_job_action(
    job_id: str,
    body: WatchActionRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Called by the watchOS / GO app to accept, decline, mark on-way, or mark arrived."""
    if body.action not in _WATCH_ACTION_STATUS_MAP:
        raise HTTPException(status_code=400, detail=f"Unknown action '{body.action}'")

    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)

    db = await get_db()
    # Fair-Use: load via _load_visible_job (REST→PG) instead of raw PostgREST.
    job = await _load_visible_job(db, job_id, actor_id, actor_member, admin_all=False)
    assignee_id = job.get("assignee_id")
    if assignee_id != actor_id:
        raise HTTPException(status_code=403, detail="You are not the assigned photographer for this job")

    old_status = job.get("status")
    _IMMUTABLE = {"cancelled", "delivered", "agent_no_show", "dispatch_failed", "qc_failed"}
    if old_status in _IMMUTABLE:
        raise HTTPException(status_code=409, detail=f"Job is in terminal state '{old_status}'")

    new_status = _WATCH_ACTION_STATUS_MAP[body.action]
    if new_status == old_status:
        return {"status": "no_change", "job_id": job_id, "current_status": old_status}

    now = datetime.now(timezone.utc).isoformat()
    status_fields = {"status": new_status, "updated_at": now}
    try:
        await db.table("jobs").update(status_fields).eq("id", job_id).execute()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_update_job
        if not _is_rest_quota_error(e):
            raise
        if not pg_update_job(job_id, status_fields):
            raise HTTPException(
                status_code=503,
                detail="Job status update unavailable (Fair Use); try again shortly",
            )

    if new_status == "in_progress" and old_status != "in_progress":
        try:
            from ..services.client_comms import notify_photographer_en_route
            await notify_photographer_en_route(job_id)
        except Exception as e:
            logger.error(f"En-route notification failed for job {job_id}: {e}")

    if new_status == "declined" and old_status in ("pending_assignment", "assigned"):
        await _refresh_decline_rate(db, actor_id, now, job_id)

    booking_id = job.get("booking_id")
    if booking_id:
        await send_dispatch_status_update_webhook(
            booking_id=booking_id,
            job_id=job_id,
            new_status=new_status,
            previous_status=old_status,
            actor_id=actor_id,
            actor_type="vendor_member",
        )

    return {"status": "updated", "job_id": job_id, "action": body.action, "new_status": new_status}


# ---------------------------------------------------------------------------
# No-show policy
# ---------------------------------------------------------------------------
# Client no-show (Kam, 2026-10-30): the vendor waits 20 minutes on site, checks in,
# takes a photo and tries to call the client, then files a report. Bricks reviews it;
# once an admin approves it the vendor is paid half the job's normal base payout,
# HST on top as usual (payout_service.trigger_payout_for_job(noshow_claim_id=...)).
# Claims live in agent_noshow_trip_fees (legacy table name). amount_cents starts at
# 0 (set explicitly: the column default is the old 6000) and is set to the half
# payout on approval.
AGENT_NOSHOW_CLAIM_AMOUNT_CENTS = 0
# Wait, window and check-in radius live in shared/noshow_policy.py (one place).
_NOSHOW_WINDOW_AFTER_MIN = noshow_policy.CLAIM_WINDOW_AFTER_MIN
_NOSHOW_RATE_LIMIT_DAYS = 30
_NOSHOW_RATE_LIMIT_THRESHOLD = 1
_NOSHOW_AGENT_RATE_LIMIT_THRESHOLD = 3
_GPS_MAX_DISTANCE_KM = 0.5            # 500m — flag if photographer is further away
_ESCALATION_MIN_WAIT_MIN = noshow_policy.WAIT_MIN


async def _validate_noshow_claim(
    db,
    job: dict,
    reporter_id: str,
    photographer_lat: Optional[float] = None,
    photographer_lng: Optional[float] = None,
) -> dict:
    """
    Run all anti-abuse checks. Returns:
      {"ok": True, "hold_for_review": False}                  → record claim
      {"ok": True, "hold_for_review": True, "reason": "..."}  → hold for admin review
      {"ok": False, "reason": "..."}                          → reject outright (HTTP 422)
    """
    assignee_id = job.get("assignee_id")
    job_id = job.get("id")
    booking_id = job.get("booking_id")

    # Gate 1: must be the assigned photographer
    if assignee_id != reporter_id:
        return {"ok": False, "reason": "Only the assigned photographer can report a no-show"}

    # Gate 2: job must be in an active state — completed/delivered/processing mean the
    # shoot happened or media is in-flight; no-show claims are invalid in those states.
    current_status = job.get("status", "")
    if current_status in ("completed", "delivered", "processing", "qc_failed", "agent_no_show", "cancelled"):
        return {"ok": False, "reason": f"Cannot report no-show on a job with status '{current_status}'"}
    # Gate 2.5: the vendor must have checked in through the app (job in_progress).
    if current_status != "in_progress":
        return {"ok": False, "reason": "Check in on the job in the app before reporting a no-show"}

    # Gate 3: idempotency
    existing = await db.table("agent_noshow_trip_fees").select("id, status").eq(
        "job_id", job_id
    ).maybe_single().execute()
    if _maybe_single_row(existing):
        return {"ok": False, "reason": "A no-show claim already exists for this job"}

    # Gate 4: the four no-show conditions (shared/noshow_policy.py). The vendor
    # checked in near the property, waited WAIT_MIN minutes from the later of
    # check-in and the booked start, took a photo after check-in and tried to call
    # the client. Fail closed on each one.
    ready = noshow_policy.readiness(job)
    if not ready["checked_in"]:
        return {"ok": False, "reason": "Check in at the property in the app before reporting a no-show"}
    if ready["expired"]:
        return {"ok": False, "reason": ready["missing"][0]}
    if not ready["waited"]:
        return {"ok": False, "reason": ready["missing"][0]}
    if not ready["has_photo"]:
        return {"ok": False, "reason": "Take a photo of the property before reporting a no-show"}
    if not ready["has_call"]:
        return {"ok": False, "reason": "Tap Call client to try reaching the client before reporting a no-show"}
    shoot_start = (
        noshow_policy.booked_start(job)
        or noshow_policy.parse_ts(ready["wait_started_at"])
        or datetime.now(timezone.utc)
    )

    # Gate 4.5: GPS proximity — soft hold if photographer is far from property.
    # Also hold when the job has known coordinates but the photographer omits their GPS:
    # omitting coordinates would otherwise bypass proximity enforcement via direct API calls.
    # Do NOT return early here — subsequent hard-reject gates (4.6, 5, 6, 7) must still run.
    gps_hold = False
    gps_hold_reason = ""
    job_lat = job.get("lat")
    job_lng = job.get("lng")
    if photographer_lat is not None and photographer_lng is not None:
        if job_lat is not None and job_lng is not None:
            try:
                dist_km = _haversine_km(
                    photographer_lat, photographer_lng,
                    float(job_lat), float(job_lng),
                )
                if dist_km > _GPS_MAX_DISTANCE_KM:
                    gps_hold = True
                    gps_hold_reason = (
                        f"Photographer GPS is {dist_km:.1f} km from property — "
                        f"held for location verification (threshold {_GPS_MAX_DISTANCE_KM * 1000:.0f} m)"
                    )
            except (TypeError, ValueError):
                pass
    elif job_lat is not None and job_lng is not None and job.get("check_in_distance_m") is None:
        # Job has a known location but there's no GPS on the report and no verified
        # check-in distance — hold for review so proximity can't be bypassed.
        # A check-in within the radius (Gate 4) already proves the vendor was there.
        gps_hold = True
        gps_hold_reason = "GPS coordinates required for property-located jobs — held for location verification"

    # Gate 4.6 (call attempt) is enforced in Gate 4. The Twilio SMS path
    # (escalate-absent-agent) logs a call attempt too, so either one counts.
    if job.get("check_in_distance_m") is None:
        gps_hold = True
        gps_hold_reason = "; ".join(
            r for r in [gps_hold_reason, "Check-in distance unknown (job has no map location) — held for location verification"] if r
        )

    # Gate 5: booking must not have been cancelled before shoot start
    if booking_id:
        try:
            res_b = await db.table("bookings").select("status, cancelled_at").eq(
                "id", booking_id
            ).maybe_single().execute()
            bk_row = _maybe_single_row(res_b)
            if bk_row:
                bk_status = bk_row.get("status", "")
                cancelled_at = bk_row.get("cancelled_at")
                if bk_status == "cancelled" and cancelled_at:
                    try:
                        cancel_dt = datetime.fromisoformat(cancelled_at.replace("Z", "+00:00"))
                        if cancel_dt < shoot_start:
                            return {
                                "ok": False,
                                "reason": "Booking was cancelled before the shoot — a no-show does not apply"
                            }
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass

    # Gate 6: photographer rate limit — accumulate flag, do NOT early-return so Gate 7 (collusion) always runs.
    rate_hold = False
    rate_hold_reason = ""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=_NOSHOW_RATE_LIMIT_DAYS)).isoformat()
        res_recent = await db.table("agent_noshow_trip_fees").select("id").eq(
            "member_id", assignee_id
        ).in_("status", ["pending", "paid_out", "pending_review"]).gte(
            "created_at", cutoff
        ).execute()
        recent_count = len(res_recent.data or [])
        if recent_count >= _NOSHOW_RATE_LIMIT_THRESHOLD:
            rate_hold = True
            rate_hold_reason = (
                f"Rate limit: {recent_count} claims in the last {_NOSHOW_RATE_LIMIT_DAYS} days "
                f"— held for admin review before payout"
            )
    except Exception:
        # Query failed — fail closed so the claim is held for review rather than auto-approved
        rate_hold = True
        rate_hold_reason = "Rate-limit check unavailable — held for admin review"

    # Gate 7: per-agent rate limit (collusion detection) — always runs regardless of Gate 6.
    collusion_hold = False
    collusion_hold_reason = ""
    if booking_id:
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=_NOSHOW_RATE_LIMIT_DAYS)).isoformat()
            # Find bookings by this agent that had no-show claims in the window
            res_this_bk = await db.table("bookings").select("user_id").eq(
                "id", booking_id
            ).maybe_single().execute()
            agent_user_id = _maybe_single_row(res_this_bk).get("user_id")
            if agent_user_id:
                res_agent_bks = await db.table("bookings").select("id").eq(
                    "user_id", agent_user_id
                ).execute()
                agent_booking_ids = [r["id"] for r in (res_agent_bks.data or []) if r.get("id")]
                if agent_booking_ids:
                    # Count DISTINCT photographers who filed claims against this agent —
                    # collusion requires multiple photographers, not just multiple claims from one.
                    res_against = await db.table("agent_noshow_trip_fees").select(
                        "member_id"
                    ).in_("booking_id", agent_booking_ids).gte("created_at", cutoff).in_(
                        "status", ["pending", "paid_out", "pending_review"]
                    ).execute()
                    distinct_photographers = len({
                        r["member_id"] for r in (res_against.data or [])
                        if r.get("member_id") and r["member_id"] != reporter_id
                    })
                    if distinct_photographers >= _NOSHOW_AGENT_RATE_LIMIT_THRESHOLD - 1:
                        collusion_hold = True
                        collusion_hold_reason = (
                            f"Agent-level flag: {distinct_photographers} distinct photographers filed "
                            f"no-show claims against this agent in {_NOSHOW_RATE_LIMIT_DAYS} days "
                            f"— held for admin review"
                        )
        except Exception as e:
            # Query failed — fail closed so the claim is held rather than auto-approved
            collusion_hold = True
            collusion_hold_reason = "Collusion check unavailable — held for admin review"
            logger.error(f"Agent rate limit check failed for job {job_id}: {e}")

    # Combine all soft-hold flags — include all reasons in the review note.
    hold_reasons = [r for r in [rate_hold_reason, collusion_hold_reason, gps_hold_reason] if r]
    wait_started_at = ready.get("wait_started_at")
    if rate_hold or collusion_hold or gps_hold:
        return {"ok": True, "hold_for_review": True, "reason": "; ".join(hold_reasons), "wait_started_at": wait_started_at}
    return {"ok": True, "hold_for_review": False, "wait_started_at": wait_started_at}


@router.post("/jobs/{job_id}/escalate-absent-agent")
async def escalate_absent_agent(
    job_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    Photographer reports that the agent is not yet present — sends SMS alert.

    Must be called before report_agent_no_show when Twilio is configured.
    Two SMS attempts allowed per job (attempt 1 at arrival, attempt 2 if still absent).
    After first SMS, photographer must wait 20 minutes before filing the no-show claim.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    db = await get_db()

    res_job = await db.table("jobs").select("*").eq("id", job_id).maybe_single().execute()
    job = _maybe_single_row(res_job)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.get("assignee_id") != actor_id:
        raise HTTPException(status_code=403, detail="Not assigned to this job")

    booking_id = job.get("booking_id")

    res_esc = await db.table("agent_noshow_escalations").select(
        "id, attempt_number, sent_at"
    ).eq("job_id", job_id).execute()
    existing = res_esc.data or []

    if len(existing) >= 2:
        first_sent = min(
            datetime.fromisoformat(e["sent_at"].replace("Z", "+00:00")) for e in existing
        )
        elapsed_min = (datetime.now(timezone.utc) - first_sent).total_seconds() / 60
        can_file_now = elapsed_min >= _ESCALATION_MIN_WAIT_MIN
        wait = max(int(_ESCALATION_MIN_WAIT_MIN - elapsed_min) + 1, 0)
        return {
            "message": "Maximum escalation attempts sent.",
            "can_file_now": can_file_now,
            "wait_minutes": 0 if can_file_now else wait,
            "attempts": len(existing),
        }

    attempt_number = len(existing) + 1

    # Fetch agent phone from booking → users table
    agent_phone = None
    if booking_id:
        try:
            res_bk = await db.table("bookings").select("user_id").eq(
                "id", booking_id
            ).maybe_single().execute()
            bk_row = _maybe_single_row(res_bk)
            if bk_row:
                user_id = bk_row.get("user_id")
                if user_id:
                    res_user = await db.table("users").select("phone").eq(
                        "id", user_id
                    ).maybe_single().execute()
                    # Missing user row: skip SMS notify, do not 500.
                    agent_phone = _maybe_single_row(res_user).get("phone")
        except Exception as e:
            logger.warning(f"Could not fetch agent phone for escalation (job {job_id}): {e}")

    sms_sid = None
    sms_sent = False
    if agent_phone:
        from ..services.sms_escalation import send_agent_noshow_sms
        sms_sid, sms_sent = await send_agent_noshow_sms(
            to_phone=agent_phone,
            job_id=job_id,
            address=job.get("address", "the property"),
            attempt_number=attempt_number,
        )

    now = datetime.now(timezone.utc).isoformat()
    await db.table("agent_noshow_escalations").insert({
        "job_id": job_id,
        "booking_id": booking_id,
        "agent_phone": agent_phone,
        "attempt_number": attempt_number,
        "sms_sid": sms_sid,
        "sent_at": now,
        "delivery_status": "sent" if sms_sent else ("no_phone" if not agent_phone else "failed"),
    }).execute()

    # A sent SMS counts as the call attempt (Twilio path). Dialer calls are logged
    # by POST /jobs/{id}/call-attempt instead.
    if sms_sent and job.get("checked_in_at"):
        try:
            await _record_call_attempt(db, job, "sms")
        except Exception as e:
            logger.warning(f"Could not log SMS call attempt for job {job_id}: {e}")
    ready = noshow_policy.readiness(job)
    rule = (
        f"You can report a no-show {noshow_policy.WAIT_MIN} minutes after you check in "
        f"or the booked start, whichever is later."
    )

    return {
        "attempt_number": attempt_number,
        "sms_sent": sms_sent,
        "agent_notified": bool(agent_phone),
        "can_file_now": bool(ready["can_report"]),
        "wait_minutes": noshow_policy.WAIT_MIN,
        "can_file_noshow_after": ready["ready_at"],
        "noshow": ready,
        "note": (f"Client notified by text. {rule}" if sms_sent else f"No text sent (no phone on file). {rule}"),
    }


@router.post("/jobs/{job_id}/agent-no-show")
async def report_agent_no_show(
    job_id: str,
    payload: AgentNoShowReport,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    Photographer reports that the agent/client was not home.

    Records the claim for ops review. Once an admin approves it, the vendor is paid
    half the job's base payout (HST on top as usual); see admin_ops.review_noshow_claim.
    The client pays no fee beyond the booking amount retained under the cancellation
    policy (no refund on a no-show).

    Validation gates:
    - Assigned photographer only
    - Active job state, and the vendor has checked in (job in_progress)
    - No duplicate claim
    - Checked in through the app, waited 20 min from the later of check-in and the
      booked start, photo uploaded after check-in, call attempt logged (dialer or SMS);
      filed within 90 min of the wait starting
    - GPS proximity check: if coordinates supplied and >500m, holds for review
    - Check-in distance unknown (job has no coordinates): holds for review
    - Booking not pre-cancelled
    - Rate limit (photographer): 2nd+ claim in 30 days → held for review
    - Rate limit (agent): 3+ claims against same agent in 30 days → held for review
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    db = await get_db()

    res_job = await db.table("jobs").select("*").eq("id", job_id).maybe_single().execute()
    job = _maybe_single_row(res_job)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    vendor_id = job.get("vendor_id", "default_vendor")
    assignee_id = job.get("assignee_id")
    booking_id = job.get("booking_id")
    now = datetime.now(timezone.utc).isoformat()

    validation = await _validate_noshow_claim(
        db, job, actor_id,
        photographer_lat=payload.photographer_lat,
        photographer_lng=payload.photographer_lng,
    )
    if not validation["ok"]:
        raise HTTPException(status_code=422, detail=validation["reason"])

    hold_for_review = validation.get("hold_for_review", False)
    review_reason = validation.get("reason", "")
    claim_status = "pending_review" if hold_for_review else "pending"

    # Insert the claim record BEFORE updating job status.
    # If the insert fails we return an error and the job is still in its prior state (retryable).
    # If status were updated first and the insert then failed, Gate 2 would block any retry.
    if assignee_id and assignee_id != "default_user":
        await db.table("agent_noshow_trip_fees").insert({
            "job_id": job_id,
            "member_id": assignee_id,
            "vendor_id": vendor_id,
            "amount_cents": AGENT_NOSHOW_CLAIM_AMOUNT_CENTS,
            "booking_id": booking_id,
            "notes": payload.notes or "",
            "status": claim_status,
            "review_reason": review_reason or None,
            "created_at": now,
            # Evidence the reviewer sees (copied from the job at report time).
            "checked_in_at": job.get("checked_in_at"),
            "check_in_lat": job.get("check_in_lat"),
            "check_in_lng": job.get("check_in_lng"),
            "check_in_accuracy_m": job.get("check_in_accuracy_m"),
            "check_in_distance_m": job.get("check_in_distance_m"),
            "wait_started_at": validation.get("wait_started_at"),
            "photo_path": job.get("noshow_photo_path"),
            "photo_taken_at": job.get("noshow_photo_taken_at"),
            "photo_uploaded_at": job.get("noshow_photo_uploaded_at"),
            "call_attempted_at": job.get("noshow_call_attempted_at"),
            "call_method": job.get("noshow_call_method"),
        }).execute()

    await db.table("jobs").update({"status": "agent_no_show", "updated_at": now}).eq("id", job_id).execute()

    # Only notify Booking when the claim is immediately approved — for pending_review holds,
    # defer until admin approves so the agent is not charged before admin review completes.
    if booking_id and not hold_for_review:
        try:
            await send_dispatch_status_update_webhook(
                booking_id=booking_id,
                job_id=job_id,
                new_status="agent_no_show",
                previous_status=job.get("status"),
                actor_id=actor_id,
                actor_type="vendor_member",
            )
        except Exception as exc:
            logger.warning(f"No-show Booking penalty callback failed for {booking_id}: {exc}")

    await write_audit_event(
        actor_id, "job", job_id, "agent_no_show", vendor_id=vendor_id,
        details={
            "assignee_id": assignee_id,
            "claim_status": claim_status,
            "hold_for_review": hold_for_review,
            "review_reason": review_reason,
            "photographer_lat": payload.photographer_lat,
            "photographer_lng": payload.photographer_lng,
        }
    )

    if hold_for_review:
        return {
            "job_id": job_id,
            "photographer_id": assignee_id,
            "claim_status": "pending_review",
            "hold_for_review": True,
            "note": review_reason,
        }

    return {
        "job_id": job_id,
        "photographer_id": assignee_id,
        "claim_status": "pending",
        "note": "No-show recorded. Client booking retained.",
    }


@router.post("/jobs/{job_id}/contractor-no-show")
async def report_contractor_no_show(
    job_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    Admin reports that the assigned photographer failed to show up.
    Effects: cancel_rate updated, job re-dispatched, agent notified with new assignment.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions — admin only", "insufficient_permissions")

    vendor_id = actor_member.get("vendor_id", "default_vendor")
    db = await get_db()

    res_job = await db.table("jobs").select("*").eq("id", job_id).eq("vendor_id", vendor_id).maybe_single().execute()
    job = _maybe_single_row(res_job)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    assignee_id = job.get("assignee_id")
    now = datetime.now(timezone.utc).isoformat()

    if assignee_id and assignee_id != "default_user":
        try:
            res_m = await db.table("vendor_members").select(
                "total_assignments, cancel_rate, on_time_rate"
            ).eq("id", assignee_id).maybe_single().execute()
            # Missing member row: skip cancel_rate update, do not 500.
            m_row = _maybe_single_row(res_m)
            if m_row:
                prev_total = m_row.get("total_assignments") or 0
                total = prev_total + 1
                prev_cancel = m_row.get("cancel_rate") or 0.0
                prev_cancels = round(prev_cancel * prev_total)
                new_cancel_rate = round((prev_cancels + 1) / total, 4)
                # on_time_rate is NOT updated here — contractor no-shows are cancellations,
                # not late completions; recalculating would unfairly dilute dispatch scores.
                await db.table("vendor_members").update({
                    "total_assignments": total,
                    "cancel_rate": new_cancel_rate,
                    "updated_at": now,
                }).eq("id", assignee_id).execute()
                logger.warning(f"Contractor {assignee_id} no-show: cancel_rate now {new_cancel_rate:.1%}")
        except Exception as e:
            logger.error(f"cancel_rate update failed for member {assignee_id}: {e}")

    await write_audit_event(
        actor_id, "job", job_id, "contractor_no_show", vendor_id=vendor_id,
        details={"assignee_id": assignee_id}
    )

    await db.table("jobs").update({
        "status": "pending_assignment",
        "assignee_id": "default_user",
        "updated_at": now,
    }).eq("id", job_id).execute()

    redispatch_member_id = None
    try:
        from ..services.vendor_selection import assign_vendor_for_job
        # Exclude the no-show vendor so they cannot be immediately re-selected
        redispatch_member_id = await assign_vendor_for_job(job_id, exclude_member_id=assignee_id)
    except Exception as e:
        logger.error(f"Re-dispatch after contractor no-show failed for job {job_id}: {e}")

    return {
        "job_id": job_id,
        "original_contractor": assignee_id,
        "redispatched_to": redispatch_member_id,
        "redispatch_status": "success" if redispatch_member_id else "failed_manual_required",
        "note": "Contractor cancel_rate updated (dispatch score impact); no cash penalty applied",
    }
