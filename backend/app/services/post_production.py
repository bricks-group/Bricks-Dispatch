import asyncio
import os
import httpx
import logging
import time
from typing import List, Optional, Dict
from datetime import datetime, timezone
from ..shared.database import get_db
from ..services.storage import get_signed_download_url

# PIL Tier-3 enhancement (the old, still-unchanged last-resort reconcile path)
# is imported lazily inside the reconcile path to avoid hard-importing
# numpy/PIL on every request in case the packages are missing. The new
# primary in-house CV path below (bricks_inhouse_cv) is imported lazily for
# the same reason, from _run_inhouse_cv_enhancement.
_FOTELLO_API_KEY_ENV = "FOTELLO_API_KEY"
_AUTOENHANCE_API_KEY_ENV = "AUTOENHANCE_API_KEY"
_INHOUSE_CV_ENHANCEMENT_ENABLED_ENV = "INHOUSE_CV_ENHANCEMENT_ENABLED"

logger = logging.getLogger(__name__)

# --- Config ---
BOXBROWNIE_PUBLIC_KEY = os.environ.get("BOXBROWNIE_PUBLIC_KEY")
BOXBROWNIE_PRIVATE_KEY = os.environ.get("BOXBROWNIE_PRIVATE_KEY")
AUTOENHANCE_API_KEY = os.environ.get("AUTOENHANCE_API_KEY")
FOTELLO_API_KEY = os.environ.get("FOTELLO_API_KEY")


def _inhouse_cv_enabled() -> bool:
    """Read fresh at call time (not cached at import) so tests and late-injected
    env vars both work, matching this file's existing `_verify_callback_secret`
    convention in the router."""
    return os.environ.get(_INHOUSE_CV_ENHANCEMENT_ENABLED_ENV, "true").lower() == "true"

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

# --- Enhancement provider selection (Phase 0/1, 2026-09-11) ---
#
# Historically this was a static, config-time check inline in process_job_assets:
#   if FOTELLO_API_KEY: trigger fotello elif AUTOENHANCE_API_KEY: trigger autoenhance
# with no runtime fallback -- if Fotello's key was set but the actual API call
# failed, the asset was just dropped. select_enhancement_provider() below picks
# the config-time starting point; _select_and_run_enhancement() then does the
# real runtime failover (in-house raises, or a vendor call fails -> try the
# next provider in the chain within the same attempt) that was the actual gap.


def select_enhancement_provider(asset: Dict) -> Optional[str]:
    """
    Decide which enhancement provider a single non-staging photo asset should
    start with. Returns one of "bricks_inhouse_cv", "fotello", "autoenhance",
    or None (no provider configured/available).

    Staging assets (asset.get("requires_staging")) are explicitly OUT OF SCOPE
    for this function -- BoxBrownie's staging routing is untouched and handled
    entirely by the caller *before* this function is ever consulted. Returning
    None here for a staging asset is a defensive no-op, not its real routing
    decision (that stays trigger_boxbrownie_staging, unchanged).

    Default routing: bricks_inhouse_cv is primary whenever
    INHOUSE_CV_ENHANCEMENT_ENABLED is true (default) -- see CLAUDE.md's
    Post-Production Pipeline section for the routing history. When disabled,
    falls back to the pre-existing config-time Fotello -> Autoenhance chain.
    """
    if asset.get("requires_staging"):
        return None
    if _inhouse_cv_enabled():
        return "bricks_inhouse_cv"
    if FOTELLO_API_KEY:
        return "fotello"
    if AUTOENHANCE_API_KEY:
        return "autoenhance"
    return None


def _log_enhancement_attempt(
    provider: str, job_id: str, upload_id: str, outcome: str, start_time: float, error: Optional[Exception] = None
) -> None:
    """One structured log line per enhancement attempt -- provider/job_id/upload_id/
    outcome/duration_ms. Closes a real gap: there was previously zero volume/cost
    signal anywhere in the codebase for these vendor calls."""
    duration_ms = int((time.monotonic() - start_time) * 1000)
    fields = {
        "provider": provider,
        "job_id": job_id,
        "upload_id": upload_id,
        "outcome": outcome,
        "duration_ms": duration_ms,
    }
    suffix = f" error={error}" if error is not None else ""
    message = (
        f"post_production.enhancement provider={provider} job_id={job_id} "
        f"upload_id={upload_id} outcome={outcome} duration_ms={duration_ms}{suffix}"
    )
    log_fn = logger.info if outcome == "success" else logger.warning
    log_fn(message, extra=fields)


async def _run_inhouse_cv_enhancement(db, job_id: str, asset_id: str, provider_url: str) -> None:
    """
    Fetch the asset and run in-house CV enhancement SYNCHRONOUSLY inline --
    it's local CPU work with no network round-trip and no webhook to wait on,
    unlike Fotello/Autoenhance. Persists processed_url/qc_variant_url and marks
    the upload "done" immediately on success. Raises on any failure so the
    caller (_select_and_run_enhancement) can fall through to the vendor chain
    within the same attempt -- this asset is NOT marked done/failed here on
    exception, by design, so the retry can still succeed.

    The actual pixel-crunching (CLAHE / detailEnhance / unsharp / window-pull /
    sky-enhance) is dispatched via asyncio.to_thread so it never blocks the
    shared asyncio event loop -- see the Phase 0 "is synchronous-inline safe"
    writeup in the PR/report for why that matters even though this already
    runs off the HTTP request/response path (a background task).
    """
    from .photo_enhance_service import enhance_image, upload_enhanced, upload_qc_variant

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(provider_url)
        resp.raise_for_status()
        original_bytes = resp.content

    enhanced_bytes = await asyncio.to_thread(enhance_image, original_bytes)

    storage_path = await upload_enhanced(db.storage, job_id, asset_id, enhanced_bytes)
    qc_variant_path = await upload_qc_variant(db.storage, job_id, asset_id, enhanced_bytes)

    await db.table("uploads").update({
        "processing_status": "done",
        "processed_url": storage_path,
        "qc_variant_url": qc_variant_path,
        "provider": "bricks_inhouse_cv",
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", asset_id).execute()


async def _select_and_run_enhancement(db, job_id: str, asset: Dict, provider_url: str) -> Optional[Dict]:
    """
    Routes one non-staging asset through select_enhancement_provider()'s
    starting choice, with real runtime failover: if a step in the chain
    raises/fails, the next configured provider in the chain is tried within
    this same attempt (the concrete bug this refactor fixes -- previously a
    configured-but-failing Fotello call just dropped the asset, no Autoenhance
    fallback).

    Returns {"asset_id", "provider", "provider_job_id"} for a vendor call that
    is now awaiting its webhook callback, or None once the asset has already
    reached a terminal outcome within this call -- either an in-house success
    (uploads row already marked "done" by _run_inhouse_cv_enhancement) or every
    available provider having failed/being unconfigured (asset dropped, same
    end state as the old code when no vendor key was configured at all).
    """
    asset_id = asset["id"]
    provider = select_enhancement_provider(asset)
    if provider is None:
        return None

    # Build the full fallback chain from the starting provider. bricks_inhouse_cv
    # falls through to whichever vendor(s) are actually configured; fotello falls
    # through to autoenhance if configured (the originally-reported gap).
    chain = [provider]
    if provider == "bricks_inhouse_cv":
        if FOTELLO_API_KEY:
            chain.append("fotello")
        if AUTOENHANCE_API_KEY:
            chain.append("autoenhance")
    elif provider == "fotello" and AUTOENHANCE_API_KEY:
        chain.append("autoenhance")

    for candidate in chain:
        start = time.monotonic()
        try:
            if candidate == "bricks_inhouse_cv":
                await _run_inhouse_cv_enhancement(db, job_id, asset_id, provider_url)
                _log_enhancement_attempt(candidate, job_id, asset_id, "success", start)
                return None
            elif candidate == "fotello":
                ft_id = await trigger_fotello_edit(provider_url)
                if not ft_id:
                    raise RuntimeError("fotello returned no image/job id")
                _log_enhancement_attempt(candidate, job_id, asset_id, "success", start)
                return {"asset_id": asset_id, "provider": "fotello", "provider_job_id": ft_id}
            elif candidate == "autoenhance":
                ae_id = await trigger_autoenhance_edit(provider_url)
                if not ae_id:
                    raise RuntimeError("autoenhance returned no image id")
                _log_enhancement_attempt(candidate, job_id, asset_id, "success", start)
                return {"asset_id": asset_id, "provider": "autoenhance", "provider_job_id": ae_id}
        except Exception as e:
            outcome = "fallback_to_vendor" if candidate == "bricks_inhouse_cv" else "failure"
            _log_enhancement_attempt(candidate, job_id, asset_id, outcome, start, error=e)
            continue  # try the next provider in the chain, if any

    logger.warning(
        f"post_production.enhancement: no provider succeeded for upload {asset_id} (job {job_id}) — asset dropped"
    )
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

    # Video/archive uploads aren't photo assets — Fotello/Autoenhance/BoxBrownie only
    # edit images, so these are skipped from provider routing below and delivered as-is.
    # 3D visits are Polycam-only (embed URL or a manually uploaded .ply/.splat file,
    # handled generically by _finalize_job_if_complete's splat_url resolution) — no
    # automated processing pipeline exists for raw video/zip captures.
    _SPLAT_SOURCE_EXTENSIONS = (".mp4", ".mov", ".avi", ".zip", ".tar")

    results = []
    for asset in assets:
        storage_path = asset.get("file_url")
        filename = (asset.get("filename") or "").lower()
        if not storage_path: continue

        # SKIP pre-processed .splat / .ply 3D assets (legacy direct-upload path)
        if filename.endswith(".splat") or filename.endswith(".ply"):
            logger.info(f"Skipping processing for pre-built 3D asset: {filename}")
            continue

        # SKIP video/archive assets — not photo-editable, no automated 3D processing exists
        if filename.endswith(_SPLAT_SOURCE_EXTENSIONS):
            continue

        # Providers need an HTTP URL — generate a short-lived signed URL from the storage path
        provider_url = await get_signed_download_url(storage_path)
        if not provider_url:
            logger.warning(f"Could not generate signed URL for asset {asset['id']} ({storage_path}) — skipping")
            continue

        # ROUTE based on metadata. Staging is completely untouched (BoxBrownie only,
        # per select_enhancement_provider()'s own docstring). Everything else goes
        # through the enhancement provider chain (bricks_inhouse_cv primary by
        # default, with real runtime failover to Fotello -> Autoenhance).
        if asset.get("requires_staging"):
            bb_id = await trigger_boxbrownie_staging(provider_url)
            if bb_id:
                results.append({"asset_id": asset["id"], "provider": "boxbrownie", "provider_job_id": bb_id})
        else:
            result = await _select_and_run_enhancement(db, job_id, asset, provider_url)
            if result:
                results.append(result)

    # Only vendor calls (boxbrownie/fotello/autoenhance) land here awaiting a webhook —
    # an in-house success already wrote processing_status="done" itself inside
    # _run_inhouse_cv_enhancement, so it correctly never appears in `results`.
    for res in results:
        await db.table("uploads").update({
            "processing_status": "processing",
            "provider": res["provider"],
            "provider_job_id": res["provider_job_id"]
        }).eq("id", res["asset_id"]).execute()

    # In-house enhancement completes synchronously with no webhook to trigger
    # finalization later, unlike Fotello/Autoenhance/BoxBrownie. Now that
    # bricks_inhouse_cv is the default primary path, a job whose assets are ALL
    # in-house would otherwise never reach _finalize_job_if_complete at all.
    # _finalize_job_if_complete is idempotent and no-ops early on any upload
    # still "processing"/"pending" a vendor webhook, so it is always safe to
    # attempt here regardless of the mix of providers used above.
    try:
        from ..routers.post_production import _finalize_job_if_complete
        await _finalize_job_if_complete(job_id)
    except Exception as e:
        logger.error(f"Job {job_id}: finalization check after process_job_assets failed: {e}")
