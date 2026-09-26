import os
import httpx
import logging
from typing import List, Optional, Dict
from datetime import datetime, timezone
from ..shared.database import get_db
from ..services.storage import get_signed_download_url

# PIL Tier-3 enhancement is imported lazily inside the reconcile path to avoid
# hard-importing numpy/PIL on every request in case the packages are missing.
_FOTELLO_API_KEY_ENV = "FOTELLO_API_KEY"
_AUTOENHANCE_API_KEY_ENV = "AUTOENHANCE_API_KEY"

logger = logging.getLogger(__name__)


async def _safe_update_upload(db, upload_id: str, update: dict) -> None:
    """Update uploads row via REST; Postgres on Fair-Use 402 (enhance→listing)."""
    try:
        await db.table("uploads").update(update).eq("id", upload_id).execute()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_update_upload
        if not _is_rest_quota_error(e):
            raise
        if not pg_update_upload(upload_id, update):
            raise



# --- Config ---
# BoxBrownie / third-party virtual staging is intentionally disabled (Kam 2026-09-12).
# Staging uploads deliver originals to the listing file — never call vendors or claim furniture was staged.
# Owned Bricks staging ships later. Fotello/Autoenhance remain optional HDR enhance only.
AUTOENHANCE_API_KEY = os.environ.get("AUTOENHANCE_API_KEY")
FOTELLO_API_KEY = os.environ.get("FOTELLO_API_KEY")


async def trigger_boxbrownie_staging(source_url: str, room_type: str = "Living Room", style_preset: str = "modern") -> Optional[str]:
    """Disabled — no third-party virtual staging on the launch path."""
    logger.info("BoxBrownie staging skipped (disabled): owned pipeline only")
    return None


async def poll_boxbrownie_job(job_reference: str) -> Optional[str]:
    """Disabled — never poll BoxBrownie for staging results."""
    return None

# --- Autoenhance Helpers ---

async def trigger_autoenhance_edit(image_url: str) -> Optional[str]:
    if not AUTOENHANCE_API_KEY:
        logger.error("AUTOENHANCE_API_KEY not configured")
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            img_res = await client.get(image_url)
            img_res.raise_for_status()
            resp = await client.post(
                "https://api.autoenhance.ai/v2/image",
                headers={"x-api-key": AUTOENHANCE_API_KEY},
                files={"image": ("image.jpg", img_res.content, "image/jpeg")}
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("image_id")
    except Exception as e:
        logger.exception(f"Autoenhance edit failed: {e}")
        return None

# --- Fotello Helpers ---

async def trigger_fotello_edit(image_url: str) -> Optional[str]:
    """
    Triggers AI-powered real estate photo enhancement via Fotello's API.
    Specifically leverages their premium window pulls, sky replacements,
    and automatic object removals.
    """
    if not FOTELLO_API_KEY:
        logger.error("FOTELLO_API_KEY not configured")
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Download the source image
            img_res = await client.get(image_url)
            img_res.raise_for_status()
            
            # Post to Fotello's endpoint using standard bearer token auth
            resp = await client.post(
                "https://api.fotello.co/v1/images",
                headers={
                    "Authorization": f"Bearer {FOTELLO_API_KEY}",
                    "Accept": "application/json"
                },
                files={"image": ("image.jpg", img_res.content, "image/jpeg")}
            )
            resp.raise_for_status()
            data = resp.json()
            # Fotello returns { "image_id": "img_..." } or { "job_id": "job_..." }
            return data.get("image_id") or data.get("job_id")
    except Exception as e:
        logger.exception(f"Fotello AI edit failed: {e}")
        return None

# --- Pipeline Orchestrator ---

async def process_job_assets(job_id: str):
    """
    Main entry point for post-production processing.
    Triggered when all assets are uploaded.
    """
    db = await get_db()

    # Atomic gate: only advance to processing if the job is not already processing or terminal.
    # Two concurrent upload completions can both call this function — the second one will find
    # 0 rows updated here and bail out early, preventing double-submission to providers.
    # NOTE: "completed" is the expected input state (shoot done, post-production starting),
    # so it must NOT be in the exclusion list.
    claimed = False
    try:
        gate = await db.table("jobs").update({
            "status": "processing",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", job_id).not_.in_("status", ["processing", "delivered", "cancelled", "agent_no_show", "dispatch_failed"]).execute()
        claimed = bool(gate.data)
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_claim_job_for_processing
        if not _is_rest_quota_error(e):
            raise
        claimed = pg_claim_job_for_processing(job_id)
    if not claimed:
        logger.info(f"Job {job_id}: skipping process_job_assets — already processing or terminal")
        return

    try:
        res_job = await db.table("jobs").select("*").eq("id", job_id).maybe_single().execute()
        job = res_job.data
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_job_by_id
        if not _is_rest_quota_error(e):
            raise
        job = pg_select_job_by_id(job_id)
    if not job:
        return

    try:
        res_assets = await db.table("uploads").select("*").eq("job_id", job_id).execute()
        assets = res_assets.data
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_uploads_by_job_id
        if not _is_rest_quota_error(e):
            raise
        assets = pg_select_uploads_by_job_id(job_id)

    logger.info(f"Starting post-production for job {job_id}")

    from .media_intake import delivery_route

    results = []
    leftover = []
    for asset in assets or []:
        route = delivery_route(asset)
        if route == "skip":
            continue
        if route == "visit":
            logger.info(f"Skipping processing for pre-built 3D asset: {asset.get('filename')}")
            leftover.append(asset)
            continue
        if route == "as_is":
            leftover.append(asset)
            continue

        storage_path = asset.get("file_url")
        provider_url = await get_signed_download_url(storage_path)
        if not provider_url:
            logger.warning(f"Could not generate signed URL for asset {asset['id']} ({storage_path}) — local deliver")
            leftover.append(asset)
            continue

        if route == "staging":
            # Never call BoxBrownie / third-party staging. Deliver original (honest).
            leftover.append(asset)
            continue

        if route == "twilight":
            # Real dusk shots — never Fotello/Autoenhance/BoxBrownie (those invent day-to-dusk / blue sky).
            leftover.append(asset)
            continue

        if FOTELLO_API_KEY:
            ft_id = await trigger_fotello_edit(provider_url)
            if ft_id:
                results.append({"asset_id": asset["id"], "provider": "fotello", "provider_job_id": ft_id})
            else:
                leftover.append(asset)
        elif AUTOENHANCE_API_KEY:
            ae_id = await trigger_autoenhance_edit(provider_url)
            if ae_id:
                results.append({"asset_id": asset["id"], "provider": "autoenhance", "provider_job_id": ae_id})
            else:
                leftover.append(asset)
        else:
            leftover.append(asset)

    for res in results:
        await _safe_update_upload(db, res["asset_id"], {
            "processing_status": "processing",
            "provider": res["provider"],
            "provider_job_id": res["provider_job_id"],
        })

    # Without vendor keys (or for drone/reel), leftover stills used to sit in
    # processing forever — finalize never ran. Deliver them now so the listing
    # file / client share is not a hollow path.
    # Group photo leftovers by bracket stem so +/-EV bursts HDR-merge once.
    await _deliver_leftovers_with_brackets(db, job_id, leftover)

    if not results:
        try:
            from ..routers.post_production import _finalize_job_if_complete
            await _finalize_job_if_complete(job_id)
        except Exception as e:
            logger.error(f"Job {job_id}: finalize after local deliver failed: {e}")



async def _deliver_leftovers_with_brackets(db, job_id: str, leftover: list) -> None:
    """Deliver leftovers; fuse photo brackets into one listing still per stem."""
    from .media_intake import delivery_route
    from .photo_enhance_service import group_assets_by_bracket

    photos = []
    others = []
    for asset in leftover:
        route = delivery_route(asset)
        # Staging / twilight / drone-aerial / reel must never enter HDR photos group
        # (no blue-sky invent on dusk or aerial JPEGs). Only vendor_photo leftovers bracket-merge.
        if route == "vendor_photo":
            photos.append(asset)
        else:
            others.append(asset)

    for asset in others:
        await deliver_asset_without_vendor(db, job_id, asset)

    for group in group_assets_by_bracket(photos):
        if len(group) <= 1:
            await deliver_asset_without_vendor(db, job_id, group[0])
            continue
        # Primary = closest to 0 EV (or middle); siblings share processed_url.
        await deliver_bracket_group_without_vendor(db, job_id, group)


async def deliver_bracket_group_without_vendor(db, job_id: str, group: list) -> None:
    """HDR-merge + window-pull a +/-EV set; mark all siblings done."""
    from .photo_enhance_service import (
        enhance_bracket,
        exposure_bias_from_name,
        upload_enhanced,
        upload_qc_variant,
    )
    from .storage import download_bytes

    now = datetime.now(timezone.utc).isoformat()
    frames = []
    filenames = []
    loaded = []
    for asset in group:
        path = asset.get("file_url")
        if not path:
            continue
        raw = await download_bytes(path)
        if not raw:
            continue
        frames.append(raw)
        filenames.append(str(asset.get("filename") or ""))
        loaded.append(asset)

    if not loaded:
        return
    if len(frames) == 1:
        await deliver_asset_without_vendor(db, job_id, loaded[0])
        return

    # Pick primary: bias closest to 0, else middle of sorted group.
    def _bias_key(item):
        b = exposure_bias_from_name(str(item.get("filename") or ""))
        return (999.0 if b is None else abs(b), str(item.get("filename") or ""))

    primary = sorted(loaded, key=_bias_key)[0]
    primary_id = primary.get("id")

    try:
        enhanced, enhance_meta = enhance_bracket(
            frames, filenames, apply_sky=True, return_meta=True
        )
        processed = await upload_enhanced(db.storage, job_id, primary_id, enhanced)
        qc_path = await upload_qc_variant(db.storage, job_id, primary_id, enhanced)
        provider = "bricks_inhouse"
    except Exception as e:
        logger.error(f"Bracket enhance failed for job {job_id}: {e} — delivering originals")
        for asset in loaded:
            await deliver_asset_without_vendor(db, job_id, asset)
        return

    for asset in loaded:
        uid = asset.get("id")
        is_primary = uid == primary_id
        update = {
            "processing_status": "done",
            "processed_url": processed,
            "provider": provider if is_primary else "bracket_sibling",
            "processed_at": now,
        }
        if is_primary and qc_path:
            update["qc_variant_url"] = qc_path
        # PropTx people soft-fail: flag primary for GO/MLS quarantine (not hard-fail)
        if is_primary and enhance_meta.get("mls_quarantine"):
            update["qc_status"] = "flagged"
            update["qc_notes"] = (
                "PropTx 5.01(d) persons/faces — mls_quarantine soft-fail; "
                f"qc_face_count={enhance_meta.get('qc_face_count', 0)}"
            )[:500]
        await _safe_update_upload(db, uid, update)


async def deliver_asset_without_vendor(db, job_id: str, asset: dict) -> None:
    """Mark an upload done so listing delivery can proceed without vendor APIs.

    Photos get in-house enhance when possible; drone/reel/video stay as-is.
    Staging always delivers the original (honest — not virtually staged; no third-party).
    Twilight gets in-house enhance with apply_sky=False (preserve real dusk; no blue-sky invention).
    """
    from .media_intake import delivery_route
    from .storage import download_bytes

    now = datetime.now(timezone.utc).isoformat()
    upload_id = asset.get("id")
    file_path = asset.get("file_url")
    route = delivery_route(asset)
    if not upload_id or not file_path:
        return
    # Visit (.splat/.ply/.spz) is not a listing still, but must leave "pending"
    # or finalize blocks forever on in_flight. Mark done with the raw path;
    # _finalize_job_if_complete still excludes these from photo delivery_urls
    # and registers property_visits from the Visit row separately.
    if route == "visit":
        await _safe_update_upload(db, upload_id, {
            "processing_status": "done",
            "processed_url": file_path,
            "provider": "visit",
            "processed_at": now,
        })
        return

    # Staging: honest original only — no in-house enhance, no third-party virtual staging.
    if route == "staging":
        await _safe_update_upload(db, upload_id, {
            "processing_status": "done",
            "processed_url": file_path,
            "provider": "as_is",
            "processed_at": now,
        })
        logger.info(
            f"Job {job_id}: staging upload {upload_id} delivered as_is "
            "(original file — no enhance, no virtual staging; owned pipeline later)"
        )
        return

    processed = file_path
    provider = "as_is"
    qc_path = asset.get("qc_variant_url")
    enhance_meta = {}

    # Twilight: polish only — never blue-sky replacement or day-to-dusk invention.
    if route == "twilight":
        try:
            from .photo_enhance_service import enhance_bracket, upload_enhanced, upload_qc_variant
            original = await download_bytes(file_path)
            if original:
                enhanced, enhance_meta = enhance_bracket(
                    [original], apply_sky=False, return_meta=True
                )
                processed = await upload_enhanced(db.storage, job_id, upload_id, enhanced)
                qc_path = await upload_qc_variant(db.storage, job_id, upload_id, enhanced)
                provider = "bricks_inhouse"
        except Exception as e:
            logger.error(
                f"Twilight enhance failed for {upload_id}: {e} — delivering original"
            )
            processed = file_path
            provider = "original"
            enhance_meta = {}

        update = {
            "processing_status": "done",
            "processed_url": processed,
            "provider": provider,
            "processed_at": now,
        }
        if qc_path:
            update["qc_variant_url"] = qc_path
        if enhance_meta.get("mls_quarantine"):
            update["qc_status"] = "flagged"
            update["qc_notes"] = (
                "PropTx 5.01(d) persons/faces — mls_quarantine soft-fail; "
                f"qc_face_count={enhance_meta.get('qc_face_count', 0)}"
            )[:500]
        await _safe_update_upload(db, upload_id, update)
        logger.info(
            f"Job {job_id}: twilight upload {upload_id} delivered "
            f"provider={provider} (apply_sky=False)"
        )
        return

    if route == "vendor_photo":
        try:
            from .photo_enhance_service import enhance_image, upload_enhanced, upload_qc_variant
            original = await download_bytes(file_path)
            enhance_meta = {}
            if original:
                enhanced, enhance_meta = enhance_image(original, return_meta=True)
                processed = await upload_enhanced(db.storage, job_id, upload_id, enhanced)
                qc_path = await upload_qc_variant(db.storage, job_id, upload_id, enhanced)
                provider = "bricks_inhouse"
        except Exception as e:
            logger.error(f"In-house enhance failed for {upload_id}: {e} — delivering original")
            processed = file_path
            provider = "original"

    update = {
        "processing_status": "done",
        "processed_url": processed,
        "provider": provider,
        "processed_at": now,
    }
    if qc_path:
        update["qc_variant_url"] = qc_path
    if enhance_meta.get("mls_quarantine"):
        update["qc_status"] = "flagged"
        update["qc_notes"] = (
            "PropTx 5.01(d) persons/faces — mls_quarantine soft-fail; "
            f"qc_face_count={enhance_meta.get('qc_face_count', 0)}"
        )[:500]
    await _safe_update_upload(db, upload_id, update)
