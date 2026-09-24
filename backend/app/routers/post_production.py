"""
Post-production callback and reconciliation endpoints.

Providers call back here when processing is complete:
  POST /api/v1/post-production/callback/fotello
  POST /api/v1/post-production/callback/autoenhance
  POST /api/v1/post-production/callback/boxbrownie

BoxBrownie handles: photo editing, retouching, day-to-dusk, item removal, and virtual staging.
BoxBrownie uses polling (not webhooks) — this endpoint handles any push
notifications they may send for completed orders.

Gaussian splat / 3D visits are handled entirely by Polycam (embed URL or a
manually uploaded .ply/.splat file) — no backend processing needed; see
_finalize_job_if_complete()'s splat_url resolution below.

Internal reconciliation (admin-only, for stuck uploads):
  POST /api/v1/post-production/reconcile
"""
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Request

from ..services.media_intake import is_listing_still_delivery_row, pick_visit_upload
from ..services.qc_agent import run_qc_for_job
from ..services.storage import get_signed_download_url, SIGNED_URL_EXPIRY
from ..shared.auth import get_own_internal_api_key
from ..shared.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/post-production", tags=["post-production"])


def _verify_callback_secret(secret: Optional[str]) -> None:
    """Verify webhook secret token. Read at request time so late-injected env vars work."""
    _secret = os.environ.get("POST_PRODUCTION_CALLBACK_SECRET", "")
    if not _secret:
        raise HTTPException(status_code=500, detail="Callback secret not configured")
    if secret != _secret:
        raise HTTPException(status_code=401, detail="Invalid callback secret")


async def _mark_upload_done(upload_id: str, processed_url: Optional[str]) -> None:
    db = await get_db()
    update = {
        "processing_status": "done",
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
    if processed_url:
        update["processed_url"] = processed_url
    try:
        await db.table("uploads").update(update).eq("id", upload_id).execute()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_update_upload
        if not _is_rest_quota_error(e):
            raise
        if not pg_update_upload(upload_id, update):
            raise


async def _finalize_job_if_complete(job_id: str) -> None:
    """
    Called after each upload is marked done.
    If all uploads for the job are in state 'done', generate delivery
    record, sign URLs, update job status, and send notification.
    """
    db = await get_db()

    try:
        res_uploads = await db.table("uploads").select(
            "id, filename, file_url, processed_url, processing_status, qc_status, provider"
        ).eq("job_id", job_id).execute()
        uploads = res_uploads.data or []
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_uploads_by_job_id
        if not _is_rest_quota_error(e):
            raise
        uploads = pg_select_uploads_by_job_id(job_id)

    if not uploads:
        return

    # Idempotency guard: if media_delivery already exists, finalization already ran.
    # Prevents duplicate QC runs, duplicate delivery emails, and double payout triggers
    # when concurrent provider callbacks arrive close together.
    existing = None
    try:
        existing_delivery = await db.table("media_delivery").select("id").eq("job_id", job_id).maybe_single().execute()
        existing = existing_delivery.data
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_media_delivery_by_job_id
        if not _is_rest_quota_error(e):
            raise
        existing = pg_select_media_delivery_by_job_id(job_id)
    if existing:
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
    try:
        res_uploads = await db.table("uploads").select(
            "id, filename, file_url, processed_url, processing_status, qc_status, provider"
        ).eq("job_id", job_id).execute()
        uploads = res_uploads.data or []
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_uploads_by_job_id
        if not _is_rest_quota_error(e):
            raise
        uploads = pg_select_uploads_by_job_id(job_id)

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
        # Skip Visit/3D + HDR bracket siblings (non-primary share primary processed_url)
        if not is_listing_still_delivery_row(u):
            reason = "bracket sibling" if str(u.get("provider") or "").lower() == "bracket_sibling" else "Visit/3D asset"
            logger.info(f"Job {job_id}: upload {u['id']} ({u.get('filename')}) excluded from photo list — {reason}")
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

    # Find Visit (.splat > .ply > .spz) BEFORE the empty-stills gate so Visit-only
    # (or all-stills-rejected) jobs still register property_visits + deliver.
    # Previously qc_failed returned here and Visit never landed on the listing.
    splat_upload = pick_visit_upload(uploads)

    if not delivery_urls and not splat_upload:
        # Truly nothing to deliver (no listing stills, no Visit) — don't stay stuck.
        logger.error(f"Job {job_id}: no deliverable URLs after QC — marking qc_failed")
        fields = {
            "status": "qc_failed",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await db.table("jobs").update(fields).eq("id", job_id).execute()
        except Exception as e:
            from ..shared.pg_fallback import _is_rest_quota_error, pg_update_job
            if not _is_rest_quota_error(e):
                raise
            if not pg_update_job(job_id, fields):
                raise
        return

    if not delivery_urls and splat_upload:
        logger.info(
            f"Job {job_id}: no listing stills after QC — delivering Visit-only "
            f"({splat_upload.get('filename') or splat_upload.get('id')})"
        )

    # Upsert media_delivery row (idempotent). Fair-Use: PostgREST 402 → Postgres
    # so enhance→listing does not hollow after Blob upload succeeds.
    delivery_row = {
        "job_id": job_id,
        "delivery_urls": delivery_urls,
        "delivered_at": datetime.now(timezone.utc).isoformat(),
        "notification_status": "pending",
    }
    try:
        await db.table("media_delivery").upsert(
            delivery_row, on_conflict="job_id"
        ).execute()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_upsert_media_delivery
        if not _is_rest_quota_error(e):
            raise
        if not pg_upsert_media_delivery(delivery_row):
            raise

    # Advance job to delivered
    delivered_fields = {
        "status": "delivered",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await db.table("jobs").update(delivered_fields).eq("id", job_id).execute()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_update_job
        if not _is_rest_quota_error(e):
            raise
        if not pg_update_job(job_id, delivered_fields):
            raise

    logger.info(f"Job {job_id}: all {len(uploads)} uploads processed → status=delivered, {len(delivery_urls)} URLs generated")

    # Register the property visit on Bricks-OS now that the job is delivered.
    # Failure is logged loudly — Visit-only deliveries must not silently hollow.
    if splat_upload:
        # Prefer internal storage path (file_url / non-https processed_url). OS re-signs.
        candidates = [
            splat_upload.get("file_url"),
            splat_upload.get("processed_url"),
        ]
        splat_path = next(
            (p for p in candidates if p and not str(p).startswith("https://")),
            None,
        )
        if splat_path:
            try:
                from ..services.client_comms import register_property_visit
                ok = await register_property_visit(job_id, splat_path)
                if not ok:
                    logger.error(
                        f"Job {job_id}: register_property_visit returned False "
                        f"for {splat_path} — Visit missing on listing"
                    )
            except Exception as e:
                logger.error(f"Job {job_id}: register_property_visit failed: {e}")
        else:
            logger.error(
                f"Job {job_id}: Visit upload {splat_upload.get('id')} has no "
                f"internal storage path — skipping visit-ready"
            )

    # Trigger vendor payout now that job is delivered (best-effort — payout_service is idempotent)
    try:
        from ..services.payout_service import trigger_payout_for_job
        await trigger_payout_for_job(db, job_id)
    except Exception as e:
        logger.error(f"Job {job_id}: payout trigger failed after delivery: {e}")

    # Notify booking agent via Resend (best-effort)
    await _send_delivery_notification(job_id, delivery_urls)


async def _patch_media_delivery(job_id: str, fields: dict) -> None:
    """Update media_delivery via REST; Postgres on Fair-Use 402."""
    db = await get_db()
    try:
        await db.table("media_delivery").update(fields).eq("job_id", job_id).execute()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_update_media_delivery
        if not _is_rest_quota_error(e):
            raise
        if not pg_update_media_delivery(job_id, fields):
            raise


async def _send_delivery_notification(job_id: str, delivery_urls: list) -> None:
    """Delegates delivery notification to Booking service (which owns the agent relationship)."""
    db = await get_db()
    job = {}
    try:
        res_job = await db.table("jobs").select("booking_id, address").eq("id", job_id).maybe_single().execute()
        job = res_job.data or {}
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_job_by_id
        if not _is_rest_quota_error(e):
            raise
        job = pg_select_job_by_id(job_id) or {}
    if not job:
        return

    booking_id = job.get("booking_id") or ""
    if not booking_id:
        logger.warning(f"Job {job_id}: no booking_id — skipping delivery notification")
        return

    _internal_api_key = get_own_internal_api_key()
    _booking_api_url = os.environ.get("BOOKING_API_URL", "https://bricks-companion-api.vercel.app")

    if not _internal_api_key:
        logger.info(f"Job {job_id}: INTERNAL_API_KEYS not set — skipping delivery notification")
        return

    # splat_status is not on the live media_delivery schema (Visit registers via
    # property_visits). Keep splat_pending=False — Visit arrives separately.
    splat_pending = False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_booking_api_url}/api/internal/comms/job-delivered",
                headers={"X-Internal-Api-Key": _internal_api_key},
                json={
                    "booking_id": booking_id,
                    "job_id": job_id,
                    "address": job.get("address", ""),
                    "delivery_count": len(delivery_urls),
                    "delivery_urls": delivery_urls[:5],
                    "splat_pending": splat_pending,
                },
            )
            resp.raise_for_status()
            await _patch_media_delivery(job_id, {
                "notified_at": datetime.now(timezone.utc).isoformat(),
                "notification_status": "sent",
            })
            logger.info(f"Job {job_id}: delivery notification delegated to Booking service (splat_pending={splat_pending})")
    except Exception as e:
        logger.error(f"Job {job_id}: delivery notification failed: {e}")
        try:
            await _patch_media_delivery(job_id, {"notification_status": "failed"})
        except Exception as e2:
            logger.error(f"Job {job_id}: media_delivery notify-failed patch failed: {e2}")


# ---------------------------------------------------------------------------
# Fotello callback
# POST /api/v1/post-production/callback/fotello?secret=<CALLBACK_SECRET>
# Payload: {"image_id": "...", "status": "completed", "result_url": "..."}
# ---------------------------------------------------------------------------

@router.post("/callback/fotello")
async def fotello_callback(
    request: Request,
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
    # Await in-request: Vercel serverless drops FastAPI BackgroundTasks after the HTTP
    # response (same hollow media_delivery bug as Mark complete create_task / upload-complete).
    await _finalize_job_if_complete(job_id)

    return {"received": True, "upload_id": upload_id}


# ---------------------------------------------------------------------------
# Autoenhance callback
# POST /api/v1/post-production/callback/autoenhance?secret=<CALLBACK_SECRET>
# Payload: {"image_id": "...", "enhanced_url": "https://..."}
# ---------------------------------------------------------------------------

@router.post("/callback/autoenhance")
async def autoenhance_callback(
    request: Request,
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
    # Await in-request: Vercel serverless drops FastAPI BackgroundTasks after the HTTP
    # response (same hollow media_delivery bug as Mark complete create_task / upload-complete).
    await _finalize_job_if_complete(job_id)

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
    # Await in-request: Vercel serverless drops FastAPI BackgroundTasks after the HTTP
    # response (same hollow media_delivery bug as Mark complete create_task / upload-complete).
    await _finalize_job_if_complete(job_id)

    return {"received": True, "upload_id": upload_id}


# ---------------------------------------------------------------------------
# Reconciliation: re-check status of uploads stuck in 'processing' > 2h
# POST /api/v1/post-production/reconcile  (internal, requires X-Internal-Api-Key)
# ---------------------------------------------------------------------------

@router.post("/reconcile")
async def reconcile_stuck_uploads(
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

    # Track every job that may now be fully done so we await finalize once at the end.
    # Includes BoxBrownie poll successes, PIL/in-house rescues, and timed-out failures.
    # (If PIL finishes every upload for a job, that job never re-enters the stuck list —
    # without this set it would never finalize → hollow listing delivery.)
    job_ids_to_finalize: set = set()

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
                    if upload.get("job_id"):
                        job_ids_to_finalize.add(upload["job_id"])
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

            # Non-photo assets (Visit + video/archives) — keep .spz with .splat/.ply
            _SPLAT_EXTS = (".mp4", ".mov", ".avi", ".zip", ".tar", ".ply", ".splat", ".spz")
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

                    pil_fields = {
                        "processing_status": "done",
                        "processed_url": storage_path,
                        "qc_variant_url": qc_variant_path,
                        "provider": "bricks_inhouse",
                        "processed_at": datetime.now(timezone.utc).isoformat(),
                    }
                    try:
                        await db.table("uploads").update(pil_fields).eq("id", upload["id"]).execute()
                    except Exception as up_err:
                        from ..shared.pg_fallback import _is_rest_quota_error, pg_update_upload
                        if not _is_rest_quota_error(up_err):
                            raise
                        if not pg_update_upload(upload["id"], pil_fields):
                            raise

                    if upload.get("job_id"):
                        job_ids_to_finalize.add(upload["job_id"])
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
    for u in stuck:
        if u.get("job_id"):
            job_ids_to_finalize.add(u["job_id"])

    if stuck_ids:
        fail_fields = {
            "processing_status": "failed",
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await db.table("uploads").update(fail_fields).in_("id", stuck_ids).execute()
        except Exception as e:
            from ..shared.pg_fallback import _is_rest_quota_error, pg_update_upload
            if not _is_rest_quota_error(e):
                raise
            for sid in stuck_ids:
                pg_update_upload(sid, fail_fields)
        logger.warning(f"Reconcile: marked {len(stuck_ids)} stuck uploads as failed")

    # Await finalize in-request. Vercel serverless drops FastAPI BackgroundTasks after the
    # HTTP response (same hollow media_delivery bug as Mark complete create_task / upload-complete).
    # Finalize is idempotent via the media_delivery guard, so order across job ids does not matter.
    for jid in job_ids_to_finalize:
        await _finalize_job_if_complete(jid)

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
        "jobs_queued_for_finalization": len(job_ids_to_finalize),
    }
