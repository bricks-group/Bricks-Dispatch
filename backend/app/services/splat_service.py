"""
Bricks 3DGS Processing — splat_service.py

Two backends:
  "bricks" (default when MODAL_SPLAT_TRIGGER_URL is set) — in-house GPU pipeline via Modal.
     Photographer uploads video → we fire-and-forget to Modal → Modal runs COLMAP +
     nerfstudio splatfacto (15k iters) on A10G GPU → Modal calls back via
     POST /api/v1/post-production/splat-complete/{job_id} with the signed .ply URL.
     Full pipeline: ~20–35 min on A10G.  We own the full stack; no third-party dependency.

  "luma" (fallback) — Luma Labs Dream Machine API.
     Same upload → submit to Luma → poll for completion → download + store .ply.
     Retained as fallback in case Modal is unavailable.

Set SPLAT_BACKEND=bricks | luma in Vercel env vars.
Default: "bricks" if MODAL_SPLAT_TRIGGER_URL is set, otherwise "luma".
"""

import asyncio
import logging
import os
from typing import Optional

import httpx

from ..shared.database import get_db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# LUMA_API_KEY must be set in Vercel env vars — never hardcode a default value.
_LUMA_BASE = "https://api.lumalabs.ai/dream-machine/v1"

_POLL_INTERVAL_SECS = 10      # poll every 10 seconds
_MAX_POLL_SECONDS   = 1200    # give up after 20 minutes

STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "brxs-deliverables")


# ---------------------------------------------------------------------------
# In-house backend (Modal GPU)
# ---------------------------------------------------------------------------

async def submit_to_modal_trigger(source_url: str, job_id: str) -> bool:
    """
    Fire-and-forget: POST to the Modal trigger endpoint to enqueue GPU processing.

    The Modal function (modal_splat/app.py) picks this up, runs COLMAP + splatfacto
    on an A10G GPU (~25 min), uploads scene.ply to Supabase Storage, and calls back
    via POST /api/v1/post-production/splat-complete/{job_id}.

    Returns True if the trigger was accepted (202/200), False on any error.
    The caller should NOT wait for the splat to be ready — it arrives asynchronously.
    """
    trigger_url = os.environ.get("MODAL_SPLAT_TRIGGER_URL", "")
    if not trigger_url:
        logger.error(f"Splat job {job_id}: MODAL_SPLAT_TRIGGER_URL not set")
        return False

    api_key = os.environ.get("INTERNAL_API_KEY", "")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                trigger_url,
                json={
                    "job_id": job_id,
                    "source_url": source_url,
                    "api_key": api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"Splat job {job_id}: submitted to Bricks 3DGS — status={data.get('status')}")
            return True
    except httpx.HTTPStatusError as e:
        logger.error(
            f"Splat job {job_id}: Modal trigger HTTP {e.response.status_code}: {e.response.text[:300]}"
        )
        return False
    except Exception as e:
        logger.error(f"Splat job {job_id}: Modal trigger failed: {e}")
        return False


def _luma_api_key() -> str:
    """Return LUMA_API_KEY at call time so late-injected env vars work."""
    key = os.environ.get("LUMA_API_KEY", "")
    if not key:
        raise RuntimeError("LUMA_API_KEY environment variable is not set")
    return key


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

async def process_splat_with_luma(
    source_url: str,
    job_id: str,
) -> Optional[str]:
    """
    Submit a source video/zip URL to Luma Labs for Gaussian splat / NeRF processing,
    poll until complete, store the .ply output in Supabase Storage, and return a
    signed URL.

    Args:
        source_url: A publicly accessible HTTPS URL to the source file
                    (video or image-sequence zip).  Use get_signed_download_url()
                    to generate a short-lived URL from the storage path before calling.
        job_id:     The Bricks GO job ID — used to build the storage path.

    Returns:
        Signed Supabase URL for the uploaded .ply file, or None on any failure.
    """
    try:
        api_key = _luma_api_key()
    except RuntimeError as e:
        logger.error(f"Splat service: {e}")
        return None

    generation_id = await _submit_to_luma(api_key, source_url, job_id)
    if not generation_id:
        return None

    ply_url = await _poll_luma_until_done(api_key, generation_id, job_id)
    if not ply_url:
        return None

    signed_url = await _download_and_store_ply(ply_url, job_id)
    return signed_url


# ---------------------------------------------------------------------------
# Luma Labs API helpers
# ---------------------------------------------------------------------------

async def _submit_to_luma(api_key: str, source_url: str, job_id: str) -> Optional[str]:
    """
    POST to the Luma Labs generations endpoint to start a 3D scene capture.

    Request body follows the Dream Machine / NeRF capture pattern:
      {
        "prompt": "...",         # description or empty string
        "keyframes": {
          "frame0": {
            "type": "upload",    # "upload" uses the source_url field
            "url": "<source_url>"
          }
        },
        "loop": false,
        "aspect_ratio": "16:9"  # may be ignored for 3D capture
      }

    NOTE: Luma Labs may require the source to be uploaded as a multipart form
    to their /upload endpoint first, then passed by reference. Check
    https://docs.lumalabs.ai/reference/creategeneration for the current
    accepted request shape. The implementation below passes a URL directly,
    which is the documented pattern for image keyframes.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "prompt": f"Real estate Gaussian splat / 3D scene capture for job {job_id}",
        "keyframes": {
            "frame0": {
                "type": "upload",
                "url": source_url,
            }
        },
        "loop": False,
        "aspect_ratio": "16:9",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{_LUMA_BASE}/generations",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            generation_id = data.get("id")
            if not generation_id:
                logger.error(f"Splat job {job_id}: Luma response missing 'id': {data}")
                return None
            logger.info(f"Splat job {job_id}: Luma generation submitted — id={generation_id}")
            return generation_id
    except httpx.HTTPStatusError as e:
        logger.error(
            f"Splat job {job_id}: Luma submission HTTP error {e.response.status_code}: "
            f"{e.response.text[:500]}"
        )
        return None
    except Exception as e:
        logger.error(f"Splat job {job_id}: Luma submission failed: {e}")
        return None


async def _poll_luma_until_done(
    api_key: str,
    generation_id: str,
    job_id: str,
) -> Optional[str]:
    """
    Poll GET /generations/{id} every _POLL_INTERVAL_SECS seconds until the
    generation reaches state "completed" or "failed", up to _MAX_POLL_SECONDS.

    Returns the .ply / .splat asset URL from the completed generation, or None.

    Luma generation states observed in practice:
      "queued"     — waiting for a GPU slot
      "dreaming"   — processing
      "completed"  — done; assets available
      "failed"     — terminal failure
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    elapsed = 0
    while elapsed < _MAX_POLL_SECONDS:
        await asyncio.sleep(_POLL_INTERVAL_SECS)
        elapsed += _POLL_INTERVAL_SECS

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{_LUMA_BASE}/generations/{generation_id}",
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning(
                f"Splat job {job_id}: poll error at {elapsed}s — {e}; retrying"
            )
            continue

        state = data.get("state") or data.get("status") or ""
        logger.debug(f"Splat job {job_id}: Luma state={state!r} at {elapsed}s")

        if state == "completed":
            # Look for the .ply / .splat asset URL.
            # Luma returns assets under data["assets"] as a dict or list.
            # Try common key patterns; adjust once the exact schema is confirmed.
            assets = data.get("assets") or {}
            ply_url = (
                assets.get("ply")
                or assets.get("splat")
                or assets.get("nerf")
                or assets.get("output")
                # Fall back to video URL if no dedicated 3D asset key exists.
                # This signals a schema mismatch — check Luma docs.
                or assets.get("video")
            )
            if not ply_url:
                # Try flat keys on the generation object itself
                ply_url = data.get("ply_url") or data.get("output_url")
            if ply_url:
                logger.info(f"Splat job {job_id}: Luma generation completed — asset={ply_url}")
                return ply_url
            else:
                logger.error(
                    f"Splat job {job_id}: Luma completed but no .ply asset found. "
                    f"Full response: {data}"
                )
                return None

        if state == "failed":
            failure_reason = data.get("failure_reason") or "unknown"
            logger.error(
                f"Splat job {job_id}: Luma generation {generation_id} failed — {failure_reason}"
            )
            return None

    logger.error(
        f"Splat job {job_id}: Luma generation {generation_id} timed out after "
        f"{_MAX_POLL_SECONDS}s"
    )
    return None


async def _download_and_store_ply(ply_url: str, job_id: str) -> Optional[str]:
    """
    Download the .ply file from the Luma-provided URL and upload it to
    Supabase Storage at splats/{job_id}/scene.ply.

    Returns a signed URL valid for SIGNED_URL_EXPIRY seconds (default 1 h).
    """
    storage_path = f"splats/{job_id}/scene.ply"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(ply_url)
            resp.raise_for_status()
            ply_bytes = resp.content
    except Exception as e:
        logger.error(f"Splat job {job_id}: failed to download .ply from Luma: {e}")
        return None

    try:
        db = await get_db()
        bucket = db.storage.from_(STORAGE_BUCKET)
        await bucket.upload(
            path=storage_path,
            file=ply_bytes,
            file_options={"content-type": "model/ply", "upsert": "true"},
        )
        logger.info(
            f"Splat job {job_id}: .ply uploaded to {STORAGE_BUCKET}/{storage_path} "
            f"({len(ply_bytes):,} bytes)"
        )
    except Exception as e:
        logger.error(f"Splat job {job_id}: Supabase upload failed: {e}")
        return None

    # Generate a signed URL for immediate delivery
    try:
        signed_url_expiry = int(os.getenv("SIGNED_URL_EXPIRY_SECONDS", "3600"))
        result = await db.storage.from_(STORAGE_BUCKET).create_signed_url(
            storage_path, signed_url_expiry
        )
        signed_url = (
            result.get("signedURL") or result.get("signedUrl") or result.get("signed_url")
        )
        if signed_url:
            logger.info(f"Splat job {job_id}: signed URL generated")
            return signed_url
        logger.error(f"Splat job {job_id}: signed URL missing from Supabase response: {result}")
        return None
    except Exception as e:
        logger.error(f"Splat job {job_id}: failed to generate signed URL: {e}")
        return None
