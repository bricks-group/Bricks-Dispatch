"""
Post-production callback and reconciliation endpoints.

Providers call back here when processing is complete:
  POST /api/v1/post-production/callback/fotello
  POST /api/v1/post-production/callback/autoenhance
  POST /api/v1/post-production/callback/boxbrownie

Internal reconciliation (admin-only, for stuck uploads):
  POST /api/v1/post-production/reconcile
"""
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from ..services.qc_agent import run_qc_for_job
from ..services.post_production import poll_boxbrownie_job
from ..services.storage import get_signed_download_url, SIGNED_URL_EXPIRY
from ..shared.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/post-production", tags=["post-production"])

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
CALLBACK_SECRET = os.environ.get("POST_PRODUCTION_CALLBACK_SECRET", "")

INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "")


def _verify_callback_secret(secret: Optional[str]) -> None:
    """Verify webhook secret token. Fail closed if secret not configured."""
    if not CALLBACK_SECRET:
        raise HTTPException(status_code=500, detail="Callback secret not configured")
    if secret != CALLBACK_SECRET:
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

    # Only finalize when every non-skipped upload is done
    pending = [u for u in uploads if u.get("processing_status") not in ("done", "skipped", "failed")]
    if pending:
        logger.info(f"Job {job_id}: {len(pending)} upload(s) still processing — waiting")
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
        path = u.get("processed_url") or u.get("file_url")
        if not path:
            continue
        signed = await get_signed_download_url(path)
        if signed:
            delivery_urls.append({
                "upload_id": u["id"],
                "filename": u.get("filename", ""),
                "url": signed,
                "expires_at": expires_at,
                "qc_flagged": u.get("qc_status") == "flagged",
            })

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

    # Notify booking agent via Resend (best-effort)
    await _send_delivery_notification(job_id, delivery_urls)


async def _send_delivery_notification(job_id: str, delivery_urls: list) -> None:
    if not RESEND_API_KEY:
        logger.info(f"Job {job_id}: RESEND_API_KEY not set — skipping delivery email")
        return

    db = await get_db()
    res_job = await db.table("jobs").select("booking_id, address").eq("id", job_id).maybe_single().execute()
    if not res_job.data:
        return

    job = res_job.data
    booking_id = job.get("booking_id") or ""

    # Look up agent email from bookings → users join
    agent_email: Optional[str] = None
    if booking_id:
        res_booking = await db.table("bookings").select("user_id").eq("id", booking_id).maybe_single().execute()
        if res_booking.data:
            res_user = await db.table("users").select("email").eq("id", res_booking.data["user_id"]).maybe_single().execute()
            if res_user.data:
                agent_email = res_user.data.get("email")

    if not agent_email:
        logger.warning(f"Job {job_id}: no agent email found — skipping delivery notification")
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            payload = {
                "from": "Bricks <noreply@brxs.ca>",
                "to": [agent_email],
                "subject": f"Your photos are ready — {job.get('address', '')}",
                "html": (
                    f"<p>Your edited photos for <strong>{job.get('address', 'your property')}</strong> "
                    f"are ready. You can access {len(delivery_urls)} file(s) via the Bricks app or "
                    f"the link(s) below (valid for 24 hours).</p>"
                    + "".join(
                        f'<p><a href="{u["url"]}">{u["filename"]}</a></p>'
                        for u in delivery_urls[:5]
                    )
                    + ("<p>…and more in the app.</p>" if len(delivery_urls) > 5 else "")
                ),
            }
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            await db.table("media_delivery").update({
                "notified_at": datetime.now(timezone.utc).isoformat(),
                "notification_status": "sent",
            }).eq("job_id", job_id).execute()
            logger.info(f"Job {job_id}: delivery notification sent to {agent_email}")
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

    if status not in ("completed", "done", "success", ""):
        logger.info(f"Fotello callback: image {provider_job_id} status={status} — not yet complete")
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
# Payload: {"jobId": "...", "status": "complete", "files": [{"url": "..."}]}
# ---------------------------------------------------------------------------

@router.post("/callback/boxbrownie")
async def boxbrownie_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    secret: Optional[str] = None,
):
    _verify_callback_secret(secret)

    body: Dict[str, Any] = await request.json()
    provider_job_id = body.get("jobId") or body.get("job_id")
    status = body.get("status", "")
    files = body.get("files") or []
    processed_url = files[0].get("url") if files else None

    if not provider_job_id:
        raise HTTPException(status_code=422, detail="Missing jobId in BoxBrownie callback")

    if status not in ("complete", "completed", "done"):
        logger.info(f"BoxBrownie callback: job {provider_job_id} status={status} — not complete")
        return {"received": True}

    db = await get_db()
    res = await db.table("uploads").select("id, job_id").eq("provider_job_id", provider_job_id).eq("provider", "box_brownie").maybe_single().execute()
    if not res.data:
        logger.warning(f"BoxBrownie callback: no upload for provider_job_id={provider_job_id}")
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
    Poll BoxBrownie jobs for completion (no webhook — polling only).
    Mark non-BoxBrownie uploads stuck >2h as failed, then re-trigger finalization.
    """
    if not INTERNAL_API_KEY:
        raise HTTPException(status_code=500, detail="INTERNAL_API_KEY not configured")
    if x_internal_api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")

    db = await get_db()

    # --- BoxBrownie: poll ALL in-flight jobs regardless of age ---
    res_bb = await db.table("uploads").select("id, job_id, provider_job_id").eq(
        "processing_status", "processing"
    ).eq("provider", "box_brownie").limit(100).execute()

    bb_completed = 0
    bb_jobs_to_finalize: set[str] = set()
    for u in (res_bb.data or []):
        ref = u.get("provider_job_id")
        if not ref:
            continue
        file_url = await poll_boxbrownie_job(ref)
        if file_url:
            await _mark_upload_done(u["id"], file_url)
            bb_jobs_to_finalize.add(u["job_id"])
            bb_completed += 1

    for job_id in bb_jobs_to_finalize:
        background_tasks.add_task(_finalize_job_if_complete, job_id)

    # --- Fotello/Autoenhance: mark stuck (>2h) as failed, then finalize ---
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    res_stuck = await db.table("uploads").select("id, job_id, provider").eq(
        "processing_status", "processing"
    ).neq("provider", "box_brownie").lt("created_at", cutoff).limit(100).execute()

    stuck = res_stuck.data or []
    stuck_ids = [u["id"] for u in stuck]
    other_job_ids = list({u["job_id"] for u in stuck if u.get("job_id")})

    if stuck_ids:
        await db.table("uploads").update({
            "processing_status": "failed",
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }).in_("id", stuck_ids).execute()
        logger.warning(f"Reconcile: marked {len(stuck_ids)} non-BoxBrownie stuck uploads as failed")

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
        "boxbrownie_polled": len(res_bb.data or []),
        "boxbrownie_completed": bb_completed,
        "other_timed_out": len(stuck_ids),
        "jobs_queued_for_finalization": len(bb_jobs_to_finalize) + len(other_job_ids),
    }
