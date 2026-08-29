"""Upload endpoints for the Dispatch service.

Handles:
  GET  /api/uploads           — list all uploads (optionally filtered by job_id)
  POST /api/uploads           — create a new upload record + store file to S3
  GET  /api/uploads/{id}      — get a single upload by id
  DELETE /api/uploads/{id}    — delete an upload and decrement job count
"""
import base64
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Form, Header, HTTPException

from ..services.storage import upload_file
from ..services.post_production import process_job_assets
from fastapi import BackgroundTasks
from ..shared.auth import get_vendor_member, resolve_vendor_actor_id
from ..shared.database import get_db
from ..shared.errors import api_error
from ..shared.schemas import UploadRecord


router = APIRouter(prefix="/api/uploads", tags=["uploads"])


@router.get("")
async def list_uploads(
    job_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """
    Return upload records scoped to the caller's own vendor.

    FIXED 2026-08-15: this used to resolve the actor but never actually use it to
    scope the query -- any authenticated vendor could list uploads for ANY job_id,
    or every upload on the platform with no filter at all (up to 500 rows).
    `uploads.thumbnail` stores real base64 image data directly on the row, so this
    wasn't just metadata exposure -- it leaked actual (thumbnail-resolution) photo
    content across vendors. `uploads` has no vendor_id column, only job_id, so
    scoping goes through jobs.vendor_id, same as create_upload/delete_upload below
    already correctly do for writes.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id", "default_vendor")

    sb = await get_db()

    if job_id:
        res_job = await sb.table("jobs").select("id, vendor_id, assignee_id").eq("id", job_id).maybe_single().execute()
        job = res_job.data or {}
        if not job or (
            job.get("vendor_id") not in (vendor_id, actor_id)
            and job.get("assignee_id") != actor_id
        ):
            api_error(404, "Job not found", "job_not_found")
        query = sb.table("uploads").select("*").eq("job_id", job_id)
    else:
        res_jobs = await sb.table("jobs").select("id").eq("vendor_id", vendor_id).execute()
        job_ids = [j["id"] for j in (res_jobs.data or [])]
        if not job_ids:
            return []
        query = sb.table("uploads").select("*").in_("job_id", job_ids)

    res = await query.order("created_at", desc=True).limit(500).execute()
    return res.data


@router.post("")
async def create_upload(
    background_tasks: BackgroundTasks,
    job_id: str = Form(...),
    filename: str = Form(...),
    file_size: int = Form(0),
    mime_type: str = Form(""),
    file_data: str = Form(""),        # base64-encoded file content
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """
    Create an upload record.

    Steps:
      1. Validate the job exists and belongs to the actor's vendor.
      2. Decode the base64 file payload.
      3. Upload the file (and thumbnail) to S3 via the storage service.
      4. Insert the UploadRecord into the DB.
      5. Increment job.uploaded_count.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id", "default_vendor")

    db = await get_db()

    # Verify job belongs to this vendor
    res_job = await db.table("jobs").select("id, uploaded_count, deliverables_count, status, vendor_id, assignee_id").eq("id", job_id).maybe_single().execute()
    job_row = res_job.data or {}
    if not job_row or (
        job_row.get("vendor_id") not in (vendor_id, actor_id)
        and job_row.get("assignee_id") != actor_id
    ):
        api_error(404, "Job not found", "job_not_found")
    res_job.data = job_row

    # Decode base64 payload
    try:
        file_bytes = base64.b64decode(file_data) if file_data else b""
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 file_data")

    upload_id = str(uuid.uuid4())

    # Upload to storage; fail fast if storage is unavailable rather than inserting a bad record.
    file_url, thumbnail_url, thumbnail_b64, qc_variant_url = await upload_file(
        file_bytes=file_bytes,
        job_id=job_id,
        upload_id=upload_id,
        filename=filename,
        mime_type=mime_type,
    )

    if file_url is None:
        raise HTTPException(status_code=500, detail="File upload to storage failed")

    # Persist record
    record = UploadRecord(
        id=upload_id,
        job_id=job_id,
        filename=filename,
        file_size=file_size,
        mime_type=mime_type,
        thumbnail=thumbnail_b64,
        file_url=file_url,
        thumbnail_url=thumbnail_url,
        qc_variant_url=qc_variant_url,
    )

    await db.table("uploads").insert(record.dict()).execute()

    # Increment job.uploaded_count
    job = res_job.data
    new_uploaded = (job.get("uploaded_count") or 0) + 1
    await db.table("jobs").update({
        "uploaded_count": new_uploaded,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", job_id).execute()

    # Trigger post-production when uploads reach the declared count, but only once
    # the job is in `completed` status (vendor has wrapped up on-site).
    # When deliverables_count=0 (not set by Booking), post-production fires from the
    # "completed" status transition in vendor_os.py instead — triggering here on every
    # upload would fire before all files arrive, processing only a partial set.
    deliverables = job.get("deliverables_count") or 0
    if deliverables and new_uploaded == deliverables:
        # Re-read status fresh — photographer may have marked the job `completed` while
        # this upload was in-flight, leaving the snapshot from the top of this handler stale.
        res_fresh = await db.table("jobs").select("status").eq("id", job_id).maybe_single().execute()
        job_status = (res_fresh.data or {}).get("status", "")
        if job_status == "completed":
            background_tasks.add_task(process_job_assets, job_id)

    return record.dict()


@router.get("/{upload_id}")
async def get_upload(
    upload_id: str,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """Fetch a single upload record by ID, scoped to the caller's own vendor.

    FIXED 2026-08-15: this used to verify the caller was *some* valid vendor
    but never that they owned this specific upload -- any authenticated vendor
    could fetch any other vendor's upload (including its embedded base64
    thumbnail) just by guessing/incrementing an id. Same fix pattern as
    delete_upload below, which already scopes correctly."""
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id", "default_vendor")

    db = await get_db()
    res = await db.table("uploads").select("*").eq("id", upload_id).maybe_single().execute()
    if not res.data:
        api_error(404, "Upload not found", "upload_not_found")

    res_job = await db.table("jobs").select("id").eq("id", res.data["job_id"]).eq("vendor_id", vendor_id).maybe_single().execute()
    if not res_job.data:
        api_error(404, "Upload not found", "upload_not_found")

    return res.data


@router.delete("/{upload_id}")
async def delete_upload(
    upload_id: str,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """
    Delete an upload record and decrement the associated job's uploaded_count.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id", "default_vendor")

    db = await get_db()

    # Fetch the upload to find its job_id
    res_upload = await db.table("uploads").select("id, job_id").eq("id", upload_id).maybe_single().execute()
    if not res_upload.data:
        api_error(404, "Upload not found", "upload_not_found")

    upload_job_id = res_upload.data["job_id"]

    # Verify the job belongs to this vendor
    res_job = await db.table("jobs").select("id, uploaded_count").eq("id", upload_job_id).eq("vendor_id", vendor_id).maybe_single().execute()
    if not res_job.data:
        api_error(404, "Job not found", "job_not_found")

    # Delete the upload record
    await db.table("uploads").delete().eq("id", upload_id).execute()

    # Decrement job.uploaded_count (floor at 0)
    current = res_job.data.get("uploaded_count") or 0
    new_count = max(current - 1, 0)
    await db.table("jobs").update({
        "uploaded_count": new_count,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", upload_job_id).execute()

    return {"deleted": True, "id": upload_id}
