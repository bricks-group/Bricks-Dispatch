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
    Return all upload records. Optionally filter by job_id.
    The actor is resolved so we can scope queries to the caller's vendor if needed.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)

    sb = await get_db()
    query = sb.table("uploads").select("*")

    if job_id:
        query = query.eq("job_id", job_id)

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
    res_job = await db.table("jobs").select("id, uploaded_count, deliverables_count").eq("id", job_id).eq("vendor_id", vendor_id).maybe_single().execute()
    if not res_job.data:
        api_error(404, "Job not found", "job_not_found")

    # Decode base64 payload
    try:
        file_bytes = base64.b64decode(file_data) if file_data else b""
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 file_data")

    upload_id = str(uuid.uuid4())

    # Upload to storage; fail fast if storage is unavailable rather than inserting a bad record.
    file_url, thumbnail_url, thumbnail_b64 = await upload_file(
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
    )

    await db.table("uploads").insert(record.dict()).execute()

    # Increment job.uploaded_count
    job = res_job.data
    new_uploaded = (job.get("uploaded_count") or 0) + 1
    await db.table("jobs").update({
        "uploaded_count": new_uploaded,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", job_id).execute()

    # Trigger post-production exactly once — when the count first reaches the target.
    # Using == (not >=) prevents re-triggering on extra uploads beyond the target count.
    deliverables = job.get("deliverables_count") or 0
    if deliverables and new_uploaded == deliverables:
        background_tasks.add_task(process_job_assets, job_id)

    return record.dict()


@router.get("/{upload_id}")
async def get_upload(
    upload_id: str,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """Fetch a single upload record by ID."""
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    await get_vendor_member(actor_id)   # verify actor is valid

    res = await (await get_db()).table("uploads").select("*").eq("id", upload_id).maybe_single().execute()
    if not res.data:
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
