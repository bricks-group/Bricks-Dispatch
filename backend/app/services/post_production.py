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
    gate = await db.table("jobs").update({
        "status": "processing",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", job_id).not_.in_("status", ["processing", "delivered", "cancelled", "agent_no_show", "dispatch_failed"]).execute()
    if not (gate.data):
        logger.info(f"Job {job_id}: skipping process_job_assets — already processing or terminal")
        return

    res_job = await db.table("jobs").select("*").eq("id", job_id).maybe_single().execute()
    if not res_job.data:
        return

    job = res_job.data
    res_assets = await db.table("uploads").select("*").eq("job_id", job_id).execute()
    assets = res_assets.data

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
        await db.table("uploads").update({
            "processing_status": "processing",
            "provider": res["provider"],
            "provider_job_id": res["provider_job_id"]
        }).eq("id", res["asset_id"]).execute()

    # Without vendor keys (or for drone/reel), leftover stills used to sit in
    # processing forever — finalize never ran. Deliver them now so the listing
    # file / client share is not a hollow path.
    for asset in leftover:
        await deliver_asset_without_vendor(db, job_id, asset)

    if not results:
        try:
            from ..routers.post_production import _finalize_job_if_complete
            await _finalize_job_if_complete(job_id)
        except Exception as e:
            logger.error(f"Job {job_id}: finalize after local deliver failed: {e}")


async def deliver_asset_without_vendor(db, job_id: str, asset: dict) -> None:
    """Mark an upload done so listing delivery can proceed without vendor APIs.

    Photos get in-house enhance when possible; drone/reel/video stay as-is.
    Staging always delivers the original (honest — not virtually staged; no third-party).
    """
    from .media_intake import delivery_route
    from .storage import download_bytes

    now = datetime.now(timezone.utc).isoformat()
    upload_id = asset.get("id")
    file_path = asset.get("file_url")
    route = delivery_route(asset)
    if not upload_id or not file_path:
        return
    # Visit (.splat/.ply) is not a listing still, but must leave "pending"
    # or finalize blocks forever on in_flight. Mark done with the raw path;
    # _finalize_job_if_complete still excludes these from photo delivery_urls
    # and registers property_visits from the splat row separately.
    if route == "visit":
        await db.table("uploads").update({
            "processing_status": "done",
            "processed_url": file_path,
            "provider": "visit",
            "processed_at": now,
        }).eq("id", upload_id).execute()
        return

    processed = file_path
    provider = "as_is"
    qc_path = asset.get("qc_variant_url")

    if route in ("vendor_photo", "staging"):
        try:
            from .photo_enhance_service import enhance_image, upload_enhanced, upload_qc_variant
            original = await download_bytes(file_path)
            if original:
                enhanced = enhance_image(original)
                processed = await upload_enhanced(db.storage, job_id, upload_id, enhanced)
                qc_path = await upload_qc_variant(db.storage, job_id, upload_id, enhanced)
                provider = "bricks_inhouse"
        except Exception as e:
            logger.error(f"In-house enhance failed for {upload_id}: {e} — delivering original")
            processed = file_path
            provider = "original"
        if route == "staging":
            logger.info(
                f"Job {job_id}: staging upload {upload_id} delivered as original "
                "(no third-party virtual staging — owned pipeline later)"
            )

    update = {
        "processing_status": "done",
        "processed_url": processed,
        "provider": provider,
        "processed_at": now,
    }
    if qc_path:
        update["qc_variant_url"] = qc_path
    await db.table("uploads").update(update).eq("id", upload_id).execute()
