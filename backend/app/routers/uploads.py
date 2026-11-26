"""Upload endpoints for the Dispatch service.

Handles:
  GET  /api/uploads           — list all uploads (optionally filtered by job_id)
  POST /api/uploads           — small-file create (base64; 4MB Vercel cap)
  POST /api/uploads/sign      — signed PUT for camera HDR / drone / reel
  POST /api/uploads/complete  — register the signed object on the job
  GET  /api/uploads/{id}      — get a single upload by id
  DELETE /api/uploads/{id}    — delete an upload and decrement job count
"""
import base64
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Form, Header, HTTPException

from ..services.media_intake import (
    assert_listing_still_decodable,
    assert_upload_size,
    is_video_upload,
    durable_media_filename,
    file_too_large_message,
    infer_media_type,
    is_camera_raw_file,
    stills_storage_path,
    upload_content_type,
    wants_staging,
)
from ..services.storage import create_signed_upload_url, upload_file
from ..services.post_production import process_job_assets
from fastapi import BackgroundTasks
from ..shared.auth import get_vendor_member, resolve_vendor_actor_id
from ..shared.database import get_db
from ..shared.form_limits import base64_file_form_post


def _maybe_single_row(res) -> dict:
    """postgrest maybe_single().execute() is None when there is no row."""
    data = getattr(res, "data", None) if res is not None else None
    return data if isinstance(data, dict) else {}

from ..shared.errors import api_error
from ..shared.schemas import UploadRecord


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

# Real public.uploads columns only — never send media_type / requires_staging to PG/REST core.
_UPLOADS_DB_COLS = (
    "id", "job_id", "filename", "file_size", "mime_type", "thumbnail",
    "file_url", "thumbnail_url", "created_at", "processing_status",
    "provider", "provider_job_id", "processed_url", "processed_at",
    "qc_status", "qc_score", "qc_notes", "qc_at", "qc_variant_url",
)


def _uploads_db_row(payload_row: dict) -> dict:
    """Whitelist real uploads columns for REST/PG inserts (no media_type/requires_staging)."""
    out = {}
    for k in _UPLOADS_DB_COLS:
        if k in payload_row and payload_row[k] is not None:
            out[k] = payload_row[k]
    return out


async def _actor_job(authorization, x_user_id, job_id: str):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    db = await get_db()
    job = {}
    try:
        res_job = await db.table("jobs").select(
            "id, uploaded_count, deliverables_count, status, vendor_id, assignee_id"
        ).eq("id", job_id).maybe_single().execute()
        job = _maybe_single_row(res_job)
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_job_by_id
        if not _is_rest_quota_error(e):
            raise
        job = pg_select_job_by_id(job_id) or {}
    if not job or (
        job.get("vendor_id") not in (vendor_id, actor_id)
        and job.get("assignee_id") != actor_id
    ):
        api_error(404, "Job not found", "job_not_found")
    return db, actor_id, vendor_id, job


async def _maybe_start_post(job: dict, new_uploaded: int, job_id: str) -> None:
    """Run post-prod when the declared stills count is met on a completed job.

    When deliverables_count is unset (0), vendor_os's completed transition
    starts process_job_assets — firing here on every still would process a
    partial set and the atomic gate would skip later completes.

    Await on Vercel serverless — BackgroundTasks often never run after the
    response (same hollow-delivery bug as Mark complete create_task).
    """
    deliverables = job.get("deliverables_count") or 0
    status = (job.get("status") or "")
    if status == "completed" and deliverables and new_uploaded >= deliverables:
        try:
            await process_job_assets(job_id)
        except Exception as e:
            logger.error("Post-production trigger failed for job %s: %s", job_id, e)


async def _insert_upload_row(db, payload_row: dict) -> None:
    """Insert uploads row via REST; on Fair-Use 402 use Postgres. Raises on hard failure."""
    from ..shared.pg_fallback import _is_rest_quota_error, pg_insert_upload

    db_row = _uploads_db_row(payload_row)
    # Prefer a slim REST payload first (real columns only) so unknown schema fields
    # never poison the insert; keep a second attempt with processing_status if needed.
    try:
        await db.table("uploads").insert(db_row).execute()
        return
    except Exception as e:
        if _is_rest_quota_error(e):
            if not pg_insert_upload(db_row):
                raise HTTPException(
                    status_code=503,
                    detail="Upload registration unavailable (Fair Use); try again shortly",
                )
            return
        # Non-quota: try even slimmer core set (legacy schemas).
        slim = {k: db_row[k] for k in (
            "id", "job_id", "filename", "file_size", "mime_type",
            "thumbnail", "file_url", "thumbnail_url", "qc_variant_url", "created_at",
        ) if k in db_row}
        try:
            await db.table("uploads").insert(slim).execute()
            return
        except Exception as e2:
            if _is_rest_quota_error(e2):
                if not pg_insert_upload(db_row if db_row else slim):
                    raise HTTPException(
                        status_code=503,
                        detail="Upload registration unavailable (Fair Use); try again shortly",
                    )
                return
            raise


async def _bump_uploaded_count(db, job_id: str, job: dict) -> int:
    """Increment jobs.uploaded_count via REST; on Fair-Use 402 use Postgres."""
    from ..shared.pg_fallback import _is_rest_quota_error, pg_update_job

    new_uploaded = (job.get("uploaded_count") or 0) + 1
    now = datetime.now(timezone.utc).isoformat()
    fields = {"uploaded_count": new_uploaded, "updated_at": now}
    try:
        await db.table("jobs").update(fields).eq("id", job_id).execute()
    except Exception as e:
        if not _is_rest_quota_error(e):
            raise
        if not pg_update_job(job_id, fields):
            raise HTTPException(
                status_code=503,
                detail="Job upload count unavailable (Fair Use); try again shortly",
            )
    return new_uploaded


# OS / browser duplicate-copy suffixes on the stem: "IMG_4031 (1)", "IMG_4031(2)",
# "IMG_4031 copy", "IMG_4031 copy 2", "IMG_4031 - Copy", "IMG_4031-copy", "IMG_4031_copy".
_COPY_SUFFIX_RE = re.compile(
    r"(?:\s*\(\d{1,3}\)|(?:\s+|\s*-\s*|_)copy(?:\s*\(?\d{1,3}\)?)?)$",
    re.IGNORECASE,
)


def _copy_stripped_name(name: str) -> str:
    """Lower-case filename with one trailing OS/browser copy suffix removed from the stem."""
    n = str(name or "").strip().lower()
    if "." in n:
        stem, ext = n.rsplit(".", 1)
        ext = "." + ext
    else:
        stem, ext = n, ""
    stripped = _COPY_SUFFIX_RE.sub("", stem).rstrip()
    return (stripped or stem) + ext


def find_duplicate_upload(rows: list | None, *, upload_id: str, filename: str, file_size: int) -> dict:
    """Return the existing uploads row this /complete call duplicates, else {}.

    Two cases (GO web retry / re-selecting the same files):
      1. Same upload_id already registered (client retried /complete after a
         network blip) — the PK insert would 500 although the file is on the job.
      2. Same durable filename AND same non-zero byte size already on the job and
         not rejected/failed — the vendor re-picked a file they had already
         uploaded. Registering it again delivered the same still twice on the
         listing and doubled a bracket frame (same-EV pair → "same-side frames"
         on Mark complete / skewed HDR merge).
    A browser/OS duplicate-copy name ("IMG_4031 (1).jpg", "IMG_4031 copy.jpg",
    "IMG_4031 - Copy.jpg") of the same byte size is the same file re-picked from
    Downloads/Finder and dedupes too.
    Exact byte size + name is the match — different camera files sharing a name
    almost never share a byte count; rejected/failed rows can be re-uploaded.
    """
    uid = str(upload_id or "").strip()
    name = str(filename or "").strip().lower()
    size = int(file_size or 0)
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if uid and str(row.get("id") or "") == uid:
            return row
    if not name or size <= 0:
        return {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        row_name = str(row.get("filename") or "").strip().lower()
        if row_name != name and _copy_stripped_name(row_name) != _copy_stripped_name(name):
            continue
        try:
            if int(row.get("file_size") or 0) != size:
                continue
        except (TypeError, ValueError):
            continue
        if row.get("qc_status") == "rejected" or row.get("processing_status") == "failed":
            continue
        return row
    return {}


async def _job_upload_rows(db, job_id: str) -> list:
    """Uploads on a job for duplicate checks (Fair-Use PG fallback; best-effort)."""
    try:
        res = await db.table("uploads").select(
            "id, job_id, filename, file_size, mime_type, file_url, processing_status, qc_status"
        ).eq("job_id", job_id).limit(500).execute()
        return res.data or []
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_uploads_by_job_id
        if _is_rest_quota_error(e):
            return pg_select_uploads_by_job_id(job_id) or []
        logger.warning("duplicate-check uploads select failed job=%s: %s", job_id, str(e)[:200])
        return []


@router.post("/sign")
async def sign_stills_upload(
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """Mint a direct browser → storage upload (bypasses the 4.5MB Vercel POST).

    Body: job_id, filename, mime_type?, media_type?, file_size?
    Upload bytes to `tus` (resumable, big files) or PUT to signed_url, then
    POST /api/uploads/complete. Camera RAW is accepted (the master); the
    pipeline edits from a working JPEG made by POST /api/uploads/{id}/preview.
    """
    from ..services.upload_backend import BLOB_PROXY_MAX_BYTES, create_direct_upload

    job_id = str((payload or {}).get("job_id") or "").strip()
    filename = str((payload or {}).get("filename") or "still.jpg")
    mime_type = str((payload or {}).get("mime_type") or "")
    try:
        file_size = int((payload or {}).get("file_size") or 0)
    except (TypeError, ValueError):
        file_size = 0
    media_type = infer_media_type(filename, mime_type, (payload or {}).get("media_type"))
    durable_name = durable_media_filename(filename, media_type)
    assert_listing_still_decodable(filename, mime_type, media_type)
    if not job_id:
        api_error(400, "job_id is required", "missing_job_id")
    assert_upload_size(file_size, filename, mime_type, media_type)
    await _actor_job(authorization, x_user_id, job_id)
    upload_id = str(uuid.uuid4())
    content_type = upload_content_type(filename, mime_type)
    # Keep signed storage_path on the client-provided basename so PUT contract stays stable.
    key = stills_storage_path(job_id, upload_id, filename)
    target = await create_direct_upload(
        key, content_type, file_size,
        video=is_video_upload(filename, mime_type, media_type),
    )
    if not target or not target.get("signed_url"):
        api_error(502, "Could not mint stills upload URL", "stills_sign_failed")
    if target.get("via_proxy") and file_size > BLOB_PROXY_MAX_BYTES:
        # Fair-Use fallback proxies bytes through a Vercel function (4.5 MB cap):
        # the PUT would fail at the end. Say so now instead.
        msg = (
            "Big uploads are paused right now. Upload the JPEG for now and keep the RAW."
            if is_camera_raw_file(filename, mime_type)
            else "Big uploads are paused right now. Try again in a few minutes."
        )
        api_error(503, msg, "storage_unavailable_large")
    path = target.get("storage_path") or key
    return {
        "ok": True,
        "upload_id": upload_id,
        "job_id": job_id,
        "storage_path": path,
        "path": target.get("path") or path,
        "signed_url": target.get("signed_url"),
        "token": target.get("token"),
        "method": target.get("method") or "PUT",
        "provider": target.get("provider"),
        "tus": target.get("tus"),
        "headers": target.get("headers") or {"Content-Type": content_type},
        "max_file_bytes": target.get("max_file_bytes"),
        "content_type": content_type,
        "filename": durable_name,
        "media_type": media_type,
        "camera_raw": is_camera_raw_file(filename, mime_type),
        "requires_staging": media_type == "staging",
    }


@router.post("/complete")
async def complete_stills_upload(
    background_tasks: BackgroundTasks,
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """Register a stills file after the client PUT to the signed URL."""
    job_id = str((payload or {}).get("job_id") or "").strip()
    upload_id = str((payload or {}).get("upload_id") or "").strip()
    storage_path = str((payload or {}).get("storage_path") or (payload or {}).get("path") or "").strip()
    filename = str((payload or {}).get("filename") or "still.jpg")
    mime_type = str((payload or {}).get("mime_type") or "")
    file_size = int((payload or {}).get("file_size") or 0)
    media_type = infer_media_type(filename, mime_type, (payload or {}).get("media_type"))
    assert_listing_still_decodable(filename, mime_type, media_type)
    mime_type = upload_content_type(filename, mime_type)
    # Persist kind in filename column (uploads has no media_type col) for leftover routing.
    filename = durable_media_filename(filename, media_type)
    if not job_id or not upload_id or not storage_path:
        api_error(400, "job_id, upload_id, and storage_path are required", "missing_complete_fields")
    prefix = f"bricks-go/jobs/{job_id}/{upload_id}/"
    if not (storage_path.startswith(prefix) or storage_path.startswith(f"r2:{prefix}")):
        api_error(400, "storage_path must be the signed stills path for this job", "bad_stills_path")
    db, _, _, job = await _actor_job(authorization, x_user_id, job_id)

    dup = find_duplicate_upload(
        await _job_upload_rows(db, job_id),
        upload_id=upload_id,
        filename=filename,
        file_size=file_size,
    )
    if dup:
        logger.info(
            "complete stills duplicate job=%s upload=%s -> existing=%s (%s)",
            job_id, upload_id, dup.get("id"), filename,
        )
        out = dict(dup)
        out.setdefault("media_type", media_type)
        out["duplicate"] = True
        return out

    record = UploadRecord(
        id=upload_id,
        job_id=job_id,
        filename=filename,
        file_size=file_size,
        mime_type=mime_type,
        file_url=storage_path,
        media_type=media_type,
        processing_status="pending",
        requires_staging=media_type == "staging",
    )
    await _insert_upload_row(db, record.dict())

    new_uploaded = await _bump_uploaded_count(db, job_id, job)
    fresh = dict(job)
    # Best-effort refresh; Fair-Use may block the re-select.
    try:
        res_fresh = await db.table("jobs").select(
            "status, deliverables_count, uploaded_count"
        ).eq("id", job_id).maybe_single().execute()
        fresh = _maybe_single_row(res_fresh) or job
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_job_by_id
        if _is_rest_quota_error(e):
            fresh = pg_select_job_by_id(job_id) or job
        else:
            logger.warning("complete stills fresh job select failed: %s", str(e)[:200])
    fresh["uploaded_count"] = new_uploaded
    await _maybe_start_post(fresh, new_uploaded, job_id)
    return record.dict()


RAW_PREVIEW_REVIEW_MESSAGE = (
    "We couldn't read this RAW. Bricks will check it. Keep the file until the job is approved."
)


@router.post("/{upload_id}/preview")
async def make_upload_preview(
    upload_id: str,
    payload: dict = Body(default={}),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """Make the working JPEG + preview for a camera RAW right after upload.

    Background step the web app fires after /complete so post-production
    reads a small JPEG instead of decoding every RAW at delivery time. The
    RAW stays the master. Non-RAW uploads return skipped. A RAW that cannot
    be decoded is reported (needs_review) — post-production holds it.
    """
    from ..services.raw_working import RawDecodeError, ensure_working_jpeg, is_raw_asset
    from ..services.storage import get_signed_download_url

    job_id = str((payload or {}).get("job_id") or "").strip()
    if not job_id:
        api_error(400, "job_id is required", "missing_job_id")
    db, _, _, _job = await _actor_job(authorization, x_user_id, job_id)
    row = {}
    for r in await _job_upload_rows(db, job_id):
        if str((r or {}).get("id") or "") == upload_id:
            row = r
            break
    if not row:
        api_error(404, "Upload not found", "upload_not_found")
    path = row.get("file_url") or ""
    if not is_raw_asset(row, path):
        return {"ok": True, "upload_id": upload_id, "camera_raw": False, "skipped": True}
    try:
        jpeg, meta = await ensure_working_jpeg(path)
    except RawDecodeError as e:
        logger.warning("RAW preview failed upload=%s: %s", upload_id, str(e)[:200])
        return {
            "ok": False,
            "upload_id": upload_id,
            "camera_raw": True,
            "status": "needs_review",
            "message": RAW_PREVIEW_REVIEW_MESSAGE,
        }
    if not jpeg:
        api_error(404, "Uploaded file not found in storage", "upload_object_missing")
    preview_path = meta.get("preview_path")
    if preview_path and not row.get("thumbnail_url"):
        try:
            await db.table("uploads").update({"thumbnail_url": preview_path}).eq("id", upload_id).execute()
        except Exception as e:
            logger.info("RAW preview thumbnail_url not saved upload=%s: %s", upload_id, str(e)[:120])
    preview_url = await get_signed_download_url(preview_path, expiry=3600) if preview_path else None
    return {
        "ok": True,
        "upload_id": upload_id,
        "camera_raw": True,
        "status": "ready",
        "preview_url": preview_url,
        "working": {
            "width": meta.get("width"),
            "height": meta.get("height"),
            "method": meta.get("method"),
            "exposure_bias": meta.get("exposure_bias"),
            "cached": bool(meta.get("cached")),
        },
    }


def _annotate_inferred_media_type(rows: list | None) -> list:
    """Attach inferred media_type for GO Mark complete (not a DB column).

    GO validateHdrUploadsForComplete used to default missing media_type → hdr,
    so DJI/reel frames false-blocked complete beside a full −/0/+ stills set.
    Annotating here keeps older clients honest until the TS infer ship lands.
    """
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        annotated = dict(row)
        if not str(annotated.get("media_type") or "").strip():
            annotated["media_type"] = infer_media_type(
                annotated.get("filename"), annotated.get("mime_type"), None
            )
        out.append(annotated)
    return out


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
        job = {}
        try:
            res_job = await sb.table("jobs").select("id, vendor_id, assignee_id").eq("id", job_id).maybe_single().execute()
            job = _maybe_single_row(res_job)
        except Exception as e:
            from ..shared.pg_fallback import _is_rest_quota_error, pg_select_job_by_id
            if not _is_rest_quota_error(e):
                raise
            job = pg_select_job_by_id(job_id) or {}
        if not job or (
            job.get("vendor_id") not in (vendor_id, actor_id)
            and job.get("assignee_id") != actor_id
        ):
            api_error(404, "Job not found", "job_not_found")
        try:
            query = sb.table("uploads").select("*").eq("job_id", job_id)
            res = await query.order("created_at", desc=True).limit(500).execute()
            rows = res.data or []
        except Exception as e:
            from ..shared.pg_fallback import _is_rest_quota_error, pg_select_uploads_by_job_id
            if not _is_rest_quota_error(e):
                raise
            rows = pg_select_uploads_by_job_id(job_id) or []
        return _annotate_inferred_media_type(rows)
    else:
        try:
            res_jobs = await sb.table("jobs").select("id").eq("vendor_id", vendor_id).execute()
            job_ids = [j["id"] for j in (res_jobs.data or [])]
        except Exception as e:
            from ..shared.pg_fallback import _is_rest_quota_error
            if not _is_rest_quota_error(e):
                raise
            # Without a job_id filter, listing every vendor job via PG is heavier;
            # return empty rather than fail the whole surface under Fair Use.
            logger.warning("list_uploads vendor jobs REST 402 — returning []")
            return []
        if not job_ids:
            return []
        query = sb.table("uploads").select("*").in_("job_id", job_ids)
        res = await query.order("created_at", desc=True).limit(500).execute()
        return _annotate_inferred_media_type(res.data or [])


@base64_file_form_post(router, "")
async def create_upload(
    background_tasks: BackgroundTasks,
    job_id: str = Form(...),
    filename: str = Form(...),
    file_size: int = Form(0),
    mime_type: str = Form(""),
    file_data: str = Form(""),        # base64-encoded file content (small files only)
    media_type: str = Form(""),
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

    # Verify job belongs to this vendor (Fair-Use: PG select on 402)
    job_row = {}
    try:
        res_job = await db.table("jobs").select(
            "id, uploaded_count, deliverables_count, status, vendor_id, assignee_id"
        ).eq("id", job_id).maybe_single().execute()
        job_row = _maybe_single_row(res_job)
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_job_by_id
        if not _is_rest_quota_error(e):
            raise
        job_row = pg_select_job_by_id(job_id) or {}
    if not job_row or (
        job_row.get("vendor_id") not in (vendor_id, actor_id)
        and job_row.get("assignee_id") != actor_id
    ):
        api_error(404, "Job not found", "job_not_found")

    kind_preview = infer_media_type(filename, mime_type, media_type)
    assert_listing_still_decodable(filename, mime_type, kind_preview)

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

    # Persist record — durable filename carries media kind (no media_type DB column).
    kind = infer_media_type(filename, mime_type, media_type)
    durable_name = durable_media_filename(filename, kind)
    record = UploadRecord(
        id=upload_id,
        job_id=job_id,
        filename=durable_name,
        file_size=file_size,
        mime_type=mime_type,
        thumbnail=thumbnail_b64,
        file_url=file_url,
        thumbnail_url=thumbnail_url,
        qc_variant_url=qc_variant_url,
        media_type=kind,
        processing_status="pending",
        requires_staging=kind == "staging" or wants_staging({"filename": durable_name, "media_type": kind}),
    )

    await _insert_upload_row(db, record.dict())

    # Increment job.uploaded_count (Fair-Use PG fallback)
    new_uploaded = await _bump_uploaded_count(db, job_id, job_row)

    # Trigger post-production when uploads reach the declared count, but only once
    # the job is in `completed` status (vendor has wrapped up on-site).
    # When deliverables_count=0 (not set by Booking), post-production fires from the
    # "completed" status transition in vendor_os.py instead — triggering here on every
    # upload would fire before all files arrive, processing only a partial set.
    deliverables = job_row.get("deliverables_count") or 0
    if deliverables and new_uploaded >= deliverables:
        job_status = job_row.get("status", "")
        try:
            res_fresh = await db.table("jobs").select("status").eq("id", job_id).maybe_single().execute()
            job_status = _maybe_single_row(res_fresh).get("status", "") or job_status
        except Exception as e:
            from ..shared.pg_fallback import _is_rest_quota_error, pg_select_job_by_id
            if _is_rest_quota_error(e):
                fresh = pg_select_job_by_id(job_id) or {}
                job_status = fresh.get("status", "") or job_status
        if job_status == "completed":
            try:
                await process_job_assets(job_id)
            except Exception as e:
                logger.error("Post-production trigger failed for job %s: %s", job_id, e)

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
    upload_row = _maybe_single_row(res)
    if not upload_row:
        api_error(404, "Upload not found", "upload_not_found")

    res_job = await db.table("jobs").select("id").eq("id", upload_row["job_id"]).eq("vendor_id", vendor_id).maybe_single().execute()
    if not _maybe_single_row(res_job):
        api_error(404, "Upload not found", "upload_not_found")

    return upload_row


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
    upload_row = _maybe_single_row(res_upload)
    if not upload_row:
        api_error(404, "Upload not found", "upload_not_found")

    upload_job_id = upload_row["job_id"]

    # Verify the job belongs to this vendor
    res_job = await db.table("jobs").select("id, uploaded_count").eq("id", upload_job_id).eq("vendor_id", vendor_id).maybe_single().execute()
    job_row = _maybe_single_row(res_job)
    if not job_row:
        api_error(404, "Job not found", "job_not_found")

    # Delete the upload record
    await db.table("uploads").delete().eq("id", upload_id).execute()

    # Decrement job.uploaded_count (floor at 0)
    current = job_row.get("uploaded_count") or 0
    new_count = max(current - 1, 0)
    await db.table("jobs").update({
        "uploaded_count": new_count,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", upload_job_id).execute()

    return {"deleted": True, "id": upload_id}
