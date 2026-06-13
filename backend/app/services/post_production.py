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
BOXBROWNIE_PUBLIC_KEY = os.environ.get("BOXBROWNIE_PUBLIC_KEY")
BOXBROWNIE_PRIVATE_KEY = os.environ.get("BOXBROWNIE_PRIVATE_KEY")
AUTOENHANCE_API_KEY = os.environ.get("AUTOENHANCE_API_KEY")
FOTELLO_API_KEY = os.environ.get("FOTELLO_API_KEY")

# --- BoxBrownie Helpers ---

def _build_bb_virtual_staging_xml(source_url: str, room_type: str, style_preset: str) -> str:
    room_map = {'Living Room': 'Living Room', 'Bedroom': 'Bedroom 1', 'Kitchen': 'Kitchen'}
    style_map = {'modern': 'Modern', 'coastal': 'Contemporary', 'scandi': 'Minimalist'}
    room_name = room_map.get(room_type, 'Living Room')
    furniture_style = style_map.get(style_preset, 'Modern')
    
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<job>
  <jobTitle>Bricks Virtual Staging - {room_name} - {furniture_style}</jobTitle>
  <virtualStagingType>VIRTUAL_STAGING</virtualStagingType>
  <fileGroup>
    <files><fileURL>{source_url}</fileURL></files>
    <options>
      <option><optionName>ROOM_NAME</optionName><optionValue>{room_name}</optionValue></option>
      <option><optionName>FURNITURE_STYLE</optionName><optionValue>{furniture_style}</optionValue></option>
    </options>
  </fileGroup>
</job>"""

async def trigger_boxbrownie_staging(source_url: str, room_type: str = "Living Room", style_preset: str = "modern") -> Optional[str]:
    if not BOXBROWNIE_PUBLIC_KEY or not BOXBROWNIE_PRIVATE_KEY:
        logger.error("BoxBrownie keys not configured")
        return None
    xml = _build_bb_virtual_staging_xml(source_url, room_type, style_preset)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://www.boxbrownie.com/api/v2/job/createVirtualStaging",
                headers={"Api-Public-Key": BOXBROWNIE_PUBLIC_KEY, "Api-Private-Key": BOXBROWNIE_PRIVATE_KEY},
                data={"xml": xml}
            )
            resp.raise_for_status()
            data = resp.json()
            job = data.get("job", {})
            # Must store jobReference (e.g. "VS373735194179") — the GET endpoint uses this,
            # not jobId (hex hash). BoxBrownie has no webhooks; we poll via jobReference.
            return job.get("jobReference") or data.get("jobReference")
    except Exception as e:
        logger.exception(f"BoxBrownie staging request failed: {e}")
        return None


async def poll_boxbrownie_job(job_reference: str) -> Optional[str]:
    """
    Poll BoxBrownie for a single job's status.
    Returns the first completed fileURL, or None if not done yet or on error.
    BoxBrownie has no webhooks — this is the only way to know when a job is done.
    """
    if not BOXBROWNIE_PUBLIC_KEY or not BOXBROWNIE_PRIVATE_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://www.boxbrownie.com/api/v2/job/{job_reference}",
                headers={"Api-Public-Key": BOXBROWNIE_PUBLIC_KEY, "Api-Private-Key": BOXBROWNIE_PRIVATE_KEY},
            )
            resp.raise_for_status()
            data = resp.json()
            job = data.get("job", {})
            status = job.get("jobStatus", "")
            if status == "Completed":
                files = job.get("output", {}).get("file", [])
                if isinstance(files, dict):
                    files = [files]
                if files:
                    return files[0].get("fileURL")
            return None
    except Exception as e:
        logger.error(f"BoxBrownie poll failed for {job_reference}: {e}")
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

    # Detect splat source files — video or zip uploads flagged for 3D processing.
    # These are NOT photo assets; they are source captures uploaded by the photographer
    # for Gaussian splat / NeRF generation via Luma Labs.  They are routed separately.
    _SPLAT_SOURCE_EXTENSIONS = (".mp4", ".mov", ".avi", ".zip", ".tar")
    splat_source_assets = [
        a for a in assets
        if (a.get("filename") or "").lower().endswith(_SPLAT_SOURCE_EXTENSIONS)
        and a.get("provider") in (None, "", "luma")
        and a.get("processing_status") not in ("processing", "done", "failed")
    ]

    results = []
    for asset in assets:
        storage_path = asset.get("file_url")
        filename = (asset.get("filename") or "").lower()
        if not storage_path: continue

        # SKIP pre-processed .splat / .ply 3D assets (legacy direct-upload path)
        if filename.endswith(".splat") or filename.endswith(".ply"):
            logger.info(f"Skipping processing for pre-built 3D asset: {filename}")
            continue

        # SKIP splat source files — they are handled by the Luma pipeline below
        if filename.endswith(_SPLAT_SOURCE_EXTENSIONS):
            continue

        # Providers need an HTTP URL — generate a short-lived signed URL from the storage path
        provider_url = await get_signed_download_url(storage_path)
        if not provider_url:
            logger.warning(f"Could not generate signed URL for asset {asset['id']} ({storage_path}) — skipping")
            continue

        # ROUTE based on metadata
        if asset.get("requires_staging"):
            bb_id = await trigger_boxbrownie_staging(provider_url)
            if bb_id:
                results.append({"asset_id": asset["id"], "provider": "boxbrownie", "provider_job_id": bb_id})
        else:
            if FOTELLO_API_KEY:
                ft_id = await trigger_fotello_edit(provider_url)
                if ft_id:
                    results.append({"asset_id": asset["id"], "provider": "fotello", "provider_job_id": ft_id})
            elif AUTOENHANCE_API_KEY:
                ae_id = await trigger_autoenhance_edit(provider_url)
                if ae_id:
                    results.append({"asset_id": asset["id"], "provider": "autoenhance", "provider_job_id": ae_id})

    for res in results:
        await db.table("uploads").update({
            "processing_status": "processing",
            "provider": res["provider"],
            "provider_job_id": res["provider_job_id"]
        }).eq("id", res["asset_id"]).execute()

    # --- Luma Labs splat processing ---
    # Trigger asynchronously for each splat source file found in this job's uploads.
    # This is a fire-and-forget background path — photo finalization is independent.
    if splat_source_assets:
        import asyncio
        from .splat_service import process_splat_with_luma

        async def _run_luma_for_asset(asset: dict) -> None:
            storage_path = asset.get("file_url", "")
            asset_id = asset["id"]
            provider_url = await get_signed_download_url(storage_path, expiry=7200)
            if not provider_url:
                logger.warning(f"Job {job_id}: could not sign splat source URL for asset {asset_id}")
                # Mark upload failed so reconcile doesn't block finalization, then
                # ensure photo delivery still proceeds independently.
                await db.table("uploads").update({
                    "processing_status": "failed",
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", asset_id).execute()
                await db.table("media_delivery").upsert({
                    "job_id": job_id,
                    "splat_status": "processing_failed",
                    "delivery_urls": [],
                    "notification_status": "pending",
                }, on_conflict="job_id").execute()
                return
            await db.table("uploads").update({
                "processing_status": "processing",
                "provider": "luma",
            }).eq("id", asset_id).execute()
            # Mark splat as processing in media_delivery so the agent can be notified
            # that a 3D tour is coming separately, even if photos arrive first.
            await db.table("media_delivery").upsert({
                "job_id": job_id,
                "splat_status": "processing",
                "delivery_urls": [],
                "notification_status": "pending",
            }, on_conflict="job_id").execute()

            signed_ply_url = await process_splat_with_luma(source_url=provider_url, job_id=job_id)
            if signed_ply_url:
                await db.table("uploads").update({
                    "processing_status": "done",
                    "processed_url": f"splats/{job_id}/scene.ply",
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", asset_id).execute()
                # Write / merge splat_url + splat_status=completed into media_delivery.
                # Photo delivery (_finalize_job_if_complete) runs independently and may
                # have already written the delivery_urls — use update, not insert.
                existing = await db.table("media_delivery").select("id").eq("job_id", job_id).maybe_single().execute()
                if existing.data:
                    await db.table("media_delivery").update({
                        "splat_url": signed_ply_url,
                        "splat_status": "completed",
                    }).eq("job_id", job_id).execute()
                else:
                    await db.table("media_delivery").upsert({
                        "job_id": job_id,
                        "splat_url": signed_ply_url,
                        "splat_status": "completed",
                        "delivery_urls": [],
                        "delivered_at": datetime.now(timezone.utc).isoformat(),
                        "notification_status": "pending",
                    }, on_conflict="job_id").execute()
                logger.info(f"Job {job_id}: Luma splat complete — splat_url written to media_delivery")
            else:
                # Luma failed — mark upload and media_delivery accordingly.
                # IMPORTANT: do NOT block photo delivery.  The photo finalization path
                # (_finalize_job_if_complete in routers/post_production.py) checks only
                # photo upload rows; the splat upload's "failed" status will unblock it.
                await db.table("uploads").update({
                    "processing_status": "failed",
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", asset_id).execute()
                await db.table("media_delivery").upsert({
                    "job_id": job_id,
                    "splat_status": "processing_failed",
                    "delivery_urls": [],
                    "notification_status": "pending",
                }, on_conflict="job_id").execute()
                logger.error(
                    f"Job {job_id}: Luma processing failed for asset {asset_id} — "
                    f"splat_status=processing_failed; photo delivery unaffected"
                )

        # Run Luma jobs concurrently (one splat source per job is typical, but handle multiples)
        await asyncio.gather(*[_run_luma_for_asset(a) for a in splat_source_assets], return_exceptions=True)
