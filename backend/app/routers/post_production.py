"""
Post-production callback and reconciliation endpoints.

Providers call back here when processing is complete:
  POST /api/v1/post-production/callback/fotello
  POST /api/v1/post-production/callback/autoenhance
  POST /api/v1/post-production/callback/boxbrownie

BoxBrownie handles: virtual staging (the only capability actually wired up
here -- see CLAUDE.md's 2026-09-12 research note; "photo editing,
retouching, day-to-dusk, item removal" describes BoxBrownie's platform in
general, not a live call site in this repo). BoxBrownie uses polling (not
webhooks) — this endpoint handles any push notifications they may send for
completed orders.

Gaussian splat / 3D visits are handled entirely by Polycam (embed URL or a
manually uploaded .ply/.splat file) — no backend processing needed; see
_finalize_job_if_complete()'s splat_url resolution below.

Internal reconciliation (admin-only, for stuck uploads):
  POST /api/v1/post-production/reconcile

Internal, addressable object-removal capability (mask-guided inpainting,
NOT part of the automatic per-asset pipeline -- see the endpoint's own
docstring below and object_removal_service.py's module docstring):
  POST /api/v1/post-production/object-removal
"""
import base64
import hmac
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel

from ..services.object_removal_service import remove_object, upload_object_removal_result
from ..services.qc_agent import run_qc_for_job
from ..services.storage import get_signed_download_url, SIGNED_URL_EXPIRY
from ..shared.auth import get_own_internal_api_key, verify_internal_api_key
from ..shared.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/post-production", tags=["post-production"])


def _verify_callback_secret(secret: Optional[str]) -> None:
    """Verify webhook secret token. Read at request time so late-injected env vars work."""
    _secret = os.environ.get("POST_PRODUCTION_CALLBACK_SECRET", "")
    if not _secret:
        raise HTTPException(status_code=500, detail="Callback secret not configured")
    if not secret or not hmac.compare_digest(secret, _secret):
        raise HTTPException(status_code=401, detail="Invalid callback secret")


async def _mark_upload_done(upload_id: str, processed_url: Optional[str]) -> None:
    db = await get_db()
    update = {
        "processing_status": "done",
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
    if processed_url:
        update["processed_url"] = processed_url
    await db.table("uploads").update(update).eq("id", upload_id).execute()


async def _finalize_job_if_complete(job_id: str) -> None:
    """
    Called after each upload is marked done.
    If all uploads for the job are in state 'done', generate delivery
    record, sign URLs, update job status, and send notification.
    """
    db = await get_db()

    res_uploads = await db.table("uploads").select(
        "id, filename, file_url, processed_url, processing_status"
    ).eq("job_id", job_id).execute()
    uploads = res_uploads.data or []

    if not uploads:
        return

    # Idempotency guard: if media_delivery already exists, finalization already ran.
    # Prevents duplicate QC runs, duplicate delivery emails, and double payout triggers
    # when concurrent provider callbacks arrive close together.
    existing_delivery = await db.table("media_delivery").select("id").eq("job_id", job_id).maybe_single().execute()
    if existing_delivery.data:
        logger.info(f"Job {job_id}: media_delivery already exists — skipping duplicate finalization")
        return

    # Block finalization while any uploads are still in-flight or awaiting submission.
    # "pending" = provider submission failed/queued and never progressed; these are timed
    # out to "failed" by the reconcile endpoint after 2 h, at which point finalization proceeds.
    in_flight = [u for u in uploads if u.get("processing_status") in ("processing", "pending")]
    if in_flight:
        logger.info(f"Job {job_id}: {len(in_flight)} upload(s) still in-flight (processing/pending) — waiting")
        return

    # Run QC vision agent on all processed uploads before delivery
    try:
        qc_counts = await run_qc_for_job(job_id)
        logger.info(f"Job {job_id}: QC complete — {qc_counts}")
    except Exception as e:
        logger.error(f"Job {job_id}: QC agent failed (continuing to delivery): {e}")

    # Re-fetch uploads after QC (qc_status now populated)
    res_uploads = await db.table("uploads").select(
        "id, filename, file_url, processed_url, processing_status, qc_status"
    ).eq("job_id", job_id).execute()
    uploads = res_uploads.data or []

    # Build delivery URL list — prefer processed_url, fallback to signed original
    # Rejected uploads are excluded; flagged uploads are included with a warning label
    delivery_urls = []
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=SIGNED_URL_EXPIRY)).isoformat()

    for u in uploads:
        # Skip rejected uploads — not delivered
        if u.get("qc_status") == "rejected":
            logger.info(f"Job {job_id}: upload {u['id']} ({u.get('filename')}) excluded — QC rejected")
            continue
        # Skip failed uploads — processing failed, raw file_url not deliverable
        if u.get("processing_status") == "failed":
            logger.info(f"Job {job_id}: upload {u['id']} ({u.get('filename')}) excluded — processing failed")
            continue
        # Skip 3D assets — .splat/.ply files are stored separately as splat_url, not photo files
        _fname = (u.get("filename") or "").lower()
        if _fname.endswith(".splat") or _fname.endswith(".ply"):
            logger.info(f"Job {job_id}: upload {u['id']} ({u.get('filename')}) excluded from photo list — 3D asset")
            continue
        path = u.get("processed_url") or u.get("file_url")
        if not path:
            continue
        # Provider-returned HTTPS URLs are already public signed links — no need to re-sign.
        # Calling get_signed_download_url on an HTTPS URL would pass it as a Supabase storage
        # path, causing a 404 error.
        if path.startswith("https://"):
            url = path
        else:
            url = await get_signed_download_url(path)
        if url:
            delivery_urls.append({
                "upload_id": u["id"],
                "filename": u.get("filename", ""),
                "url": url,
                "expires_at": expires_at,
                "qc_flagged": u.get("qc_status") == "flagged",
            })

    if not delivery_urls:
        # All uploads were QC-rejected or had no path — job cannot be delivered.
        # Advance to qc_failed so it doesn't stay stuck in processing indefinitely.
        logger.error(f"Job {job_id}: no deliverable URLs after QC — marking qc_failed")
        await db.table("jobs").update({
            "status": "qc_failed",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", job_id).execute()
        return

    # Find the first .splat upload for this job (if any) to register as a property
    # visit after delivery -- see the post-upsert block below. NOTE: this used to sign
    # a splat_url and write it into media_delivery below; media_delivery has no
    # splat_url column on the live DB (confirmed via BRICKS_STATUS.md, 2026-08-02)
    # and that upsert would raise on every job with a splat upload, aborting the rest
    # of this function (job never reaches "delivered", payout never triggers, agent
    # never notified) -- a real, currently-live bug for any such job, now fixed by
    # removing the dead field rather than by working around it.
    splat_upload = next(
        (u for u in uploads if (u.get("filename") or "").lower().endswith(".splat")),
        None,
    )

    # Upsert media_delivery row (idempotent)
    delivery_row = {
        "job_id": job_id,
        "delivery_urls": delivery_urls,
        "delivered_at": datetime.now(timezone.utc).isoformat(),
        "notification_status": "pending",
    }
    await db.table("media_delivery").upsert(
        delivery_row, on_conflict="job_id"
    ).execute()

    # Advance job to delivered
    await db.table("jobs").update({
        "status": "delivered",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", job_id).execute()

    logger.info(f"Job {job_id}: all {len(uploads)} uploads processed → status=delivered, {len(delivery_urls)} URLs generated")

    # Register the property visit on Bricks-OS now that the job is delivered (best-effort —
    # property_visits rows aren't load-bearing for this job's own status/payout/notification).
    if splat_upload:
        splat_path = splat_upload.get("processed_url") or splat_upload.get("file_url")
        # register_property_visit expects an internal storage path (Bricks-OS re-signs it
        # at read time) -- an already-external https:// URL isn't one; same discriminator
        # used above for delivery_urls. Not handling the external-URL case here (no
        # evidence a raw .splat/.ply upload ever produces one, unlike processed photos).
        if splat_path and not splat_path.startswith("https://"):
            try:
                from ..services.client_comms import register_property_visit
                await register_property_visit(job_id, splat_path)
            except Exception as e:
                logger.error(f"Job {job_id}: register_property_visit failed: {e}")

    # Trigger vendor payout now that job is delivered (best-effort — payout_service is idempotent)
    try:
        from ..services.payout_service import trigger_payout_for_job
        await trigger_payout_for_job(db, job_id)
    except Exception as e:
        logger.error(f"Job {job_id}: payout trigger failed after delivery: {e}")

    # Notify booking agent via Resend (best-effort)
    await _send_delivery_notification(job_id, delivery_urls)


async def _send_delivery_notification(job_id: str, delivery_urls: list) -> None:
    """Delegates delivery notification to Booking service (which owns the agent relationship)."""
    db = await get_db()
    res_job = await db.table("jobs").select("booking_id, address").eq("id", job_id).maybe_single().execute()
    if not res_job.data:
        return

    booking_id = res_job.data.get("booking_id") or ""
    if not booking_id:
        logger.warning(f"Job {job_id}: no booking_id — skipping delivery notification")
        return

    _internal_api_key = get_own_internal_api_key()
    _booking_api_url = os.environ.get("BOOKING_API_URL", "https://bricks-companion-api.vercel.app")

    if not _internal_api_key:
        logger.info(f"Job {job_id}: INTERNAL_API_KEYS not set — skipping delivery notification")
        return

    # Determine splat_pending: True when splat is still processing or has failed,
    # so Booking can append "Your Virtual Visit is processing — it will arrive separately
    # within 24h" to the agent delivery email.
    splat_pending = False
    try:
        res_md = await db.table("media_delivery").select("splat_status").eq("job_id", job_id).maybe_single().execute()
        if res_md.data:
            _splat_status = res_md.data.get("splat_status", "none")
            splat_pending = _splat_status == "processing_failed"
    except Exception as sp_err:
        logger.warning(f"Job {job_id}: could not read splat_status for notification: {sp_err}")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_booking_api_url}/api/internal/comms/job-delivered",
                headers={"X-Internal-Api-Key": _internal_api_key},
                json={
                    "booking_id": booking_id,
                    "job_id": job_id,
                    "address": res_job.data.get("address", ""),
                    "delivery_count": len(delivery_urls),
                    "delivery_urls": delivery_urls[:5],
                    "splat_pending": splat_pending,
                },
            )
            resp.raise_for_status()
            await db.table("media_delivery").update({
                "notified_at": datetime.now(timezone.utc).isoformat(),
                "notification_status": "sent",
            }).eq("job_id", job_id).execute()
            logger.info(f"Job {job_id}: delivery notification delegated to Booking service (splat_pending={splat_pending})")
    except Exception as e:
        logger.error(f"Job {job_id}: delivery notification failed: {e}")
        await db.table("media_delivery").update({
            "notification_status": "failed",
        }).eq("job_id", job_id).execute()


# ---------------------------------------------------------------------------
# Fotello callback
# POST /api/v1/post-production/callback/fotello?secret=<CALLBACK_SECRET>
# Payload: {"image_id": "...", "status": "completed", "result_url": "..."}
# ---------------------------------------------------------------------------

@router.post("/callback/fotello")
async def fotello_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    secret: Optional[str] = None,
):
    _verify_callback_secret(secret)

    body: Dict[str, Any] = await request.json()
    provider_job_id = body.get("image_id") or body.get("job_id")
    status = body.get("status", "")
    processed_url = body.get("result_url") or body.get("output_url") or body.get("url")

    if not provider_job_id:
        raise HTTPException(status_code=422, detail="Missing image_id in Fotello callback")

    if not status or status not in ("completed", "done", "success"):
        logger.info(f"Fotello callback: image {provider_job_id} status={status!r} — not yet complete")
        return {"received": True}

    db = await get_db()
    res = await db.table("uploads").select("id, job_id").eq("provider_job_id", provider_job_id).eq("provider", "fotello").maybe_single().execute()
    if not res.data:
        logger.warning(f"Fotello callback: no upload found for provider_job_id={provider_job_id}")
        return {"received": True}

    upload_id = res.data["id"]
    job_id = res.data["job_id"]

    await _mark_upload_done(upload_id, processed_url)
    background_tasks.add_task(_finalize_job_if_complete, job_id)

    return {"received": True, "upload_id": upload_id}


# ---------------------------------------------------------------------------
# Autoenhance callback
# POST /api/v1/post-production/callback/autoenhance?secret=<CALLBACK_SECRET>
# Payload: {"image_id": "...", "enhanced_url": "https://..."}
# ---------------------------------------------------------------------------

@router.post("/callback/autoenhance")
async def autoenhance_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    secret: Optional[str] = None,
):
    _verify_callback_secret(secret)

    body: Dict[str, Any] = await request.json()
    provider_job_id = body.get("image_id") or body.get("id")
    processed_url = body.get("enhanced_url") or body.get("result_url") or body.get("output_url")
    status = body.get("status", "complete")

    if not provider_job_id:
        raise HTTPException(status_code=422, detail="Missing image_id in Autoenhance callback")

    if status not in ("complete", "completed", "done", "success"):
        logger.info(f"Autoenhance callback: image {provider_job_id} status={status} — not complete")
        return {"received": True}

    db = await get_db()
    res = await db.table("uploads").select("id, job_id").eq("provider_job_id", provider_job_id).eq("provider", "autoenhance").maybe_single().execute()
    if not res.data:
        logger.warning(f"Autoenhance callback: no upload for provider_job_id={provider_job_id}")
        return {"received": True}

    upload_id = res.data["id"]
    job_id = res.data["job_id"]

    await _mark_upload_done(upload_id, processed_url)
    background_tasks.add_task(_finalize_job_if_complete, job_id)

    return {"received": True, "upload_id": upload_id}


# ---------------------------------------------------------------------------
# BoxBrownie callback
# POST /api/v1/post-production/callback/boxbrownie?secret=<CALLBACK_SECRET>
# BoxBrownie handles: photo editing, retouching, day-to-dusk, item removal, virtual staging.
# BoxBrownie uses polling (not webhooks) — this endpoint handles any push
# notifications they may send for completed orders.
# Payload: {"order_id": "...", "status": "completed", "result_url": "...",
#           "service_type": "day_to_dusk" | "item_removal" | "photo_editing" | "virtual_staging" | ...}
# ---------------------------------------------------------------------------

@router.post("/callback/boxbrownie")
async def boxbrownie_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    secret: Optional[str] = None,
):
    _verify_callback_secret(secret)

    body: Dict[str, Any] = await request.json()

    provider_job_id = body.get("order_id") or body.get("image_id") or body.get("job_id")
    status = body.get("status", "")
    processed_url = body.get("result_url") or body.get("output_url") or body.get("url")

    if not provider_job_id:
        raise HTTPException(status_code=422, detail="Missing order_id in BoxBrownie callback")

    if not status or status not in ("completed", "done", "success"):
        logger.info(f"BoxBrownie callback: order {provider_job_id} status={status!r} — not yet complete")
        return {"received": True}

    db = await get_db()
    res = await db.table("uploads").select("id, job_id").eq("provider_job_id", provider_job_id).eq("provider", "boxbrownie").maybe_single().execute()
    if not res.data:
        logger.warning(f"BoxBrownie callback: no upload found for provider_job_id={provider_job_id}")
        return {"received": True}

    upload_id = res.data["id"]
    job_id = res.data["job_id"]

    await _mark_upload_done(upload_id, processed_url)
    background_tasks.add_task(_finalize_job_if_complete, job_id)

    return {"received": True, "upload_id": upload_id}


# ---------------------------------------------------------------------------
# Reconciliation: re-check status of uploads stuck in 'processing' > 2h
# POST /api/v1/post-production/reconcile  (internal, requires X-Internal-Api-Key)
# ---------------------------------------------------------------------------

@router.post("/reconcile")
async def reconcile_stuck_uploads(
    background_tasks: BackgroundTasks,
    x_internal_api_key: Optional[str] = Header(default=None, alias="X-Internal-Api-Key"),
):
    """
    Mark Fotello/Autoenhance/BoxBrownie uploads stuck >2h as failed, then re-trigger finalization.
    """
    _internal_api_keys_str = os.environ.get("INTERNAL_API_KEYS", "")
    if not _internal_api_keys_str:
        raise HTTPException(status_code=401, detail="INTERNAL_API_KEYS not configured")
    _keys = [k.strip() for k in _internal_api_keys_str.split(",") if k.strip()]
    if not x_internal_api_key or x_internal_api_key not in _keys:
        raise HTTPException(status_code=403, detail="Forbidden")

    db = await get_db()

    # --- BoxBrownie: poll all in-flight uploads (no webhooks — polling only) ---
    # Run before the timeout check so completed jobs aren't falsely marked failed.
    from ..services.post_production import poll_boxbrownie_job
    bb_completed = 0
    try:
        res_bb = await db.table("uploads").select("id, job_id, provider_job_id").eq(
            "processing_status", "processing"
        ).eq("provider", "boxbrownie").limit(100).execute()
        for upload in (res_bb.data or []):
            if upload.get("provider_job_id"):
                result_url = await poll_boxbrownie_job(upload["provider_job_id"])
                if result_url:
                    await _mark_upload_done(upload["id"], result_url)
                    background_tasks.add_task(_finalize_job_if_complete, upload["job_id"])
                    bb_completed += 1
    except Exception as e:
        logger.error(f"BoxBrownie poll sweep failed: {e}")

    # --- Fotello/Autoenhance/BoxBrownie: mark stuck (>2h) as failed, then finalize ---
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    # Also time out "pending" uploads — these were never successfully submitted to a provider
    # and will never receive a callback. Without this they block _finalize_job_if_complete forever.
    res_stuck = await db.table("uploads").select("id, job_id, provider, file_url, filename").in_(
        "processing_status", ["processing", "pending"]
    ).lt("created_at", cutoff).limit(100).execute()

    stuck = res_stuck.data or []

    # --- In-house enhancement fallback (photo_enhance_service): apply local
    # enhancement to photo assets that are stuck and whose provider keys are
    # unavailable. Still gated the same way as before (last resort, only when
    # neither vendor is configured) -- promoting this to a real Tier-2 ahead
    # of Autoenhance is a separate routing decision (see
    # project_bricks_inhouse_post_production memory / Phase 3), not made here.
    fotello_available = bool(os.environ.get("FOTELLO_API_KEY", ""))
    autoenhance_available = bool(os.environ.get("AUTOENHANCE_API_KEY", ""))
    pil_tier3_count = 0

    if not fotello_available and not autoenhance_available:
        try:
            from ..services.photo_enhance_service import enhance_image, upload_enhanced, upload_qc_variant
            from ..services.storage import get_signed_download_url as _sign_url

            _SPLAT_EXTS = (".mp4", ".mov", ".avi", ".zip", ".tar", ".ply", ".splat")
            _IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp")

            for upload in stuck:
                fname = (upload.get("filename") or "").lower()
                # Only process photo-type assets — skip splat source files and 3D assets
                if not any(fname.endswith(ext) for ext in _IMAGE_EXTS):
                    continue
                # Skip boxbrownie — it has its own polling path above
                if upload.get("provider") == "boxbrownie":
                    continue

                file_path = upload.get("file_url", "")
                if not file_path:
                    continue

                try:
                    signed_url = await _sign_url(file_path)
                    if not signed_url:
                        logger.warning(f"PIL Tier-3: could not sign URL for upload {upload['id']} — skipping")
                        continue

                    import httpx as _httpx
                    async with _httpx.AsyncClient(timeout=30.0) as client:
                        img_resp = await client.get(signed_url)
                        img_resp.raise_for_status()
                        original_bytes = img_resp.content

                    enhanced_bytes = enhance_image(original_bytes)
                    storage_path = await upload_enhanced(db.storage, upload["job_id"], upload["id"], enhanced_bytes)
                    qc_variant_path = await upload_qc_variant(db.storage, upload["job_id"], upload["id"], enhanced_bytes)

                    await db.table("uploads").update({
                        "processing_status": "done",
                        "processed_url": storage_path,
                        "qc_variant_url": qc_variant_path,
                        "provider": "bricks_inhouse",
                        "processed_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("id", upload["id"]).execute()

                    pil_tier3_count += 1
                    logger.info(
                        f"In-house enhance: processed upload {upload['id']} for job {upload['job_id']} "
                        f"→ {storage_path}"
                    )
                except Exception as pil_err:
                    logger.error(f"In-house enhance: failed for upload {upload['id']}: {pil_err}")
                    # Fall through — will be marked failed below with the rest

        except ImportError as ie:
            logger.warning(f"In-house enhance fallback unavailable (Pillow/numpy/cv2 not installed): {ie}")

    # Re-fetch stuck list after PIL pass — any uploads now "done" should be excluded
    # from the bulk failure sweep so they can proceed to finalization.
    if pil_tier3_count:
        res_stuck = await db.table("uploads").select("id, job_id, provider, file_url, filename").in_(
            "processing_status", ["processing", "pending"]
        ).lt("created_at", cutoff).limit(100).execute()
        stuck = res_stuck.data or []

    stuck_ids = [u["id"] for u in stuck]
    other_job_ids = list({u["job_id"] for u in stuck if u.get("job_id")})

    if stuck_ids:
        await db.table("uploads").update({
            "processing_status": "failed",
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }).in_("id", stuck_ids).execute()
        logger.warning(f"Reconcile: marked {len(stuck_ids)} stuck uploads as failed")

    for job_id in other_job_ids:
        background_tasks.add_task(_finalize_job_if_complete, job_id)

    # Alert on jobs stuck in post-production > 4 hours (Sentry / external monitoring)
    try:
        res_stuck_jobs = await db.table("jobs").select("id, booking_id, updated_at").in_(
            "status", ["processing", "completed"]
        ).lt("updated_at", (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()).execute()
        stuck_jobs = res_stuck_jobs.data or []
        if stuck_jobs:
            logger.error(
                f"POST-PRODUCTION ALERT: {len(stuck_jobs)} job(s) stuck in processing/completed >4h: "
                f"{[j['id'] for j in stuck_jobs]}"
            )
    except Exception:
        pass

    return {
        "boxbrownie_completed": bb_completed,
        "pil_tier3_applied": pil_tier3_count,
        "timed_out": len(stuck_ids),
        "jobs_queued_for_finalization": len(other_job_ids),
    }


# ---------------------------------------------------------------------------
# Object removal (mask-guided inpainting) -- Phase 2, 2026-09-12
# POST /api/v1/post-production/object-removal (internal, requires X-Internal-Api-Key)
# ---------------------------------------------------------------------------

class ObjectRemovalRequest(BaseModel):
    upload_id: str
    mask_base64: str  # single-channel PNG/JPEG, base64-encoded; white(255)=remove
    mime_type: Optional[str] = None  # of the ORIGINAL photo; defaults to sniffing it


@router.post("/object-removal")
async def object_removal(
    body: ObjectRemovalRequest,
    x_internal_api_key: Optional[str] = Header(default=None, alias="X-Internal-Api-Key"),
):
    """
    Runs mask-guided object removal on one existing upload and returns a
    signed URL to the result. Internal-only (same X-Internal-Api-Key gate
    as /reconcile) -- this is an addressable capability for a future caller
    (an editor review UI, or a later auto-mask-detection step) to invoke
    per-photo, NOT part of the automatic process_job_assets pipeline.

    Deliberately does not touch the `uploads` row or its processed_url --
    object removal here is a preview/candidate result, not an automatic
    replacement of the delivered photo. Promoting a result to be the actual
    delivered asset is a decision for whatever calls this, not made here.

    Mask SOURCING (who or what decides what to remove, and draws the mask)
    is a separate, not-yet-built piece -- see object_removal_service.py's
    module docstring and CLAUDE.md's Object Removal section. This endpoint
    only runs a mask it's handed.
    """
    if not verify_internal_api_key(x_internal_api_key):
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        mask_bytes = base64.b64decode(body.mask_base64, validate=True)
    except Exception:
        raise HTTPException(status_code=422, detail="mask_base64 is not valid base64")
    if not mask_bytes:
        raise HTTPException(status_code=422, detail="mask_base64 decoded to empty content")

    db = await get_db()
    res_upload = await db.table("uploads").select("id, job_id, file_url, filename").eq("id", body.upload_id).maybe_single().execute()
    if not res_upload.data:
        raise HTTPException(status_code=404, detail=f"Upload {body.upload_id} not found")

    upload = res_upload.data
    job_id = upload["job_id"]

    signed_url = await get_signed_download_url(upload["file_url"])
    if not signed_url:
        raise HTTPException(status_code=500, detail="Could not generate a signed URL for the source photo")

    async with httpx.AsyncClient(timeout=30.0) as client:
        img_resp = await client.get(signed_url)
        img_resp.raise_for_status()
        image_bytes = img_resp.content

    result = await remove_object(
        image_bytes,
        mask_bytes,
        job_id=job_id,
        upload_id=body.upload_id,
        mime_type=body.mime_type,
    )
    if not result:
        raise HTTPException(
            status_code=502,
            detail="Object removal failed: no configured provider succeeded (see server logs for per-attempt detail)",
        )

    storage_path = await upload_object_removal_result(db.storage, job_id, body.upload_id, result["image_bytes"])
    result_url = await get_signed_download_url(storage_path)

    return {
        "upload_id": body.upload_id,
        "job_id": job_id,
        "provider": result["provider"],
        "result_url": result_url,
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=SIGNED_URL_EXPIRY)).isoformat(),
    }
