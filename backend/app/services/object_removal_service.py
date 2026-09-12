"""
In-house object removal -- mask-guided inpainting (Phase 2 of the in-house
post-production effort; see photo_enhance_service.py's docstring for
Phase 0/1's classical-CV tone-mapping work, a separate, earlier capability).

Business direction: replace reliance on third-party real-estate SaaS
vendors (Fotello, BoxBrownie) for object removal with two self-controlled
engines, with symmetric failover between them:

  1. bricks_mac_inference -- a small self-hosted inpainting server the user
     runs on their own Apple Silicon Mac (see mac-inference-server/ at the
     repo root, a separate deployable artifact -- not part of this Vercel
     app). Reached over a Cloudflare Tunnel URL (MAC_INFERENCE_URL) with a
     shared secret (MAC_INFERENCE_KEY). Runs real mask-guided LaMa
     inpainting: image + a mask marking what to erase in, a filled-in photo
     out. This is the PRIMARY engine (self-hosted, no per-call vendor
     cost) whenever it's configured and reachable.

  2. gemini -- Gemini's current image model (see shared/gemini_config.py's
     get_gemini_image_model(), env-overridable -- this repo learned the hard
     way, the same day this file was written, that hardcoding a Gemini
     model string anywhere is how you end up silently 404ing in production
     once Google retires it; see gemini_config.py's own incident writeup).
     Same generateContent endpoint family already used for QC in
     qc_vision_provider.py, different (image-capable) model. Researched
     2026-09-12: Gemini's image-editing API is mask-FREE /
     natural-language-instruction-driven -- there is no bitmask request
     parameter (this is a real, current API constraint, not an oversight in
     the code below). To still honor the mask we're given,
     the mask is burned into a second copy of the photo as a solid
     highlight overlay, and the model is given both the clean original and
     the highlighted copy with an instruction to remove/reconstruct
     whatever is under the highlight using the original as context. This is
     a best-effort technique, not pixel-exact the way the LaMa/Mac path is.

FAILOVER: Mac is tried first whenever MAC_INFERENCE_URL/MAC_INFERENCE_KEY
are configured (a quick /health check first, so a sleeping/offline Mac
fails fast instead of hanging on a full photo upload). On any Mac failure
(unreachable, health check fails, the /inpaint call itself errors or times
out), Gemini is tried next if configured. If Mac isn't configured at all,
Gemini is tried directly. If Gemini is the only thing configured and it
fails, there's nothing left to fall back to. Neither available/working ->
the asset fails cleanly: this returns None and logs why, the same
worst-case shape as post_production.py's _select_and_run_enhancement
("asset dropped") -- callers must not assume a result.

NOT wired into the automatic per-asset pipeline (process_job_assets).
Object removal needs a mask, and nothing in the current automatic pipeline
produces one -- see routers/post_production.py's object-removal endpoint
docstring, and CLAUDE.md's Object Removal section, for what mask SOURCING
(who or what decides what to remove) would take, and why it's a
deliberately separate, not-yet-built piece.
"""
import base64
import io
import logging
import os
import time
from typing import Dict, Optional

import httpx
from PIL import Image

from ..shared.gemini_config import gemini_generate_content_url, get_gemini_image_model

logger = logging.getLogger(__name__)

_MAC_INFERENCE_URL_ENV = "MAC_INFERENCE_URL"
_MAC_INFERENCE_KEY_ENV = "MAC_INFERENCE_KEY"
_GEMINI_API_KEY_ENV = "GEMINI_API_KEY"

# The text/vision-scoring model this repo centralizes in gemini_config.py
# (get_gemini_model(), used by qc_vision_provider.py etc.) cannot return an
# edited image -- image generation/editing is a distinct Gemini model
# family. get_gemini_image_model() is that family's own env-overridable
# default (see gemini_config.py's own comment for the research/uncertainty
# behind the current choice, and why it's a *separate* default from the
# text model's). The URL itself is still built by the one shared
# gemini_generate_content_url() helper, just with this model passed in.

# Home internet, not a datacenter -- generous but bounded. A health check is
# cheap and should fail fast; the real /inpaint call includes a photo
# upload + LaMa inference + the result coming back down, so it gets a much
# longer budget.
_MAC_HEALTH_TIMEOUT_SECONDS = 6.0
_MAC_INPAINT_TIMEOUT_SECONDS = 90.0
_GEMINI_TIMEOUT_SECONDS = 60.0

# How the mask is burned into the "highlighted" copy sent to Gemini (see
# module docstring -- Gemini has no real mask parameter to hand this to).
_GEMINI_HIGHLIGHT_COLOR = (255, 0, 220)  # a color unlikely to occur naturally in a real estate photo
_GEMINI_HIGHLIGHT_OPACITY = 0.75


def _mac_configured() -> bool:
    return bool(os.environ.get(_MAC_INFERENCE_URL_ENV, "").strip() and os.environ.get(_MAC_INFERENCE_KEY_ENV, "").strip())


def _gemini_configured() -> bool:
    return bool(os.environ.get(_GEMINI_API_KEY_ENV, "").strip())


def _detect_mime(image_bytes: bytes) -> str:
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "image/jpeg"


def _log_object_removal_attempt(
    provider: str, job_id: str, upload_id: str, outcome: str, start_time: float, error: Optional[Exception] = None
) -> None:
    """Same structured shape as post_production.py's _log_enhancement_attempt
    -- provider/job_id/upload_id/outcome/duration_ms -- so the two
    capabilities show up consistently in logs/monitoring."""
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
        f"post_production.object_removal provider={provider} job_id={job_id} "
        f"upload_id={upload_id} outcome={outcome} duration_ms={duration_ms}{suffix}"
    )
    log_fn = logger.info if outcome == "success" else logger.warning
    log_fn(message, extra=fields)


# ---------------------------------------------------------------------------
# Mac (LaMa) engine
# ---------------------------------------------------------------------------

async def _mac_reachable(base_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=_MAC_HEALTH_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/health")
            resp.raise_for_status()
            data = resp.json()
            return bool(data.get("model_loaded"))
    except Exception as e:
        logger.warning(f"object_removal: Mac health check failed: {type(e).__name__}: {e}")
        return False


async def _run_mac_inpaint(image_bytes: bytes, mask_bytes: bytes, mime_type: str) -> bytes:
    """Calls the user's self-hosted Mac inference server. Raises on any
    failure (unreachable, non-2xx, timeout) -- callers handle the fallback."""
    base_url = os.environ.get(_MAC_INFERENCE_URL_ENV, "").strip()
    key = os.environ.get(_MAC_INFERENCE_KEY_ENV, "").strip()
    if not base_url or not key:
        raise RuntimeError("Mac inference not configured (MAC_INFERENCE_URL/MAC_INFERENCE_KEY)")

    if not await _mac_reachable(base_url):
        raise RuntimeError("Mac inference server did not pass its health check")

    async with httpx.AsyncClient(timeout=_MAC_INPAINT_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            f"{base_url.rstrip('/')}/inpaint",
            headers={"X-Mac-Inference-Key": key},
            files={
                "image": ("image", image_bytes, mime_type),
                "mask": ("mask", mask_bytes, "image/png"),
            },
        )
        resp.raise_for_status()
        return resp.content


# ---------------------------------------------------------------------------
# Gemini engine
# ---------------------------------------------------------------------------

def _burn_mask_overlay(image_bytes: bytes, mask_bytes: bytes) -> bytes:
    """
    Produces a JPEG copy of `image_bytes` with the masked region (white in
    the mask) painted a solid, saturated highlight color -- how the mask is
    communicated to Gemini in the absence of a real mask API parameter (see
    module docstring). Not sent anywhere else; only used for the Gemini
    fallback path.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    mask = Image.open(io.BytesIO(mask_bytes)).convert("L")
    if mask.size != img.size:
        mask = mask.resize(img.size, Image.NEAREST)

    overlay = Image.new("RGB", img.size, _GEMINI_HIGHLIGHT_COLOR)
    highlighted = Image.composite(overlay, img, mask.point(lambda p: int(p * _GEMINI_HIGHLIGHT_OPACITY)))

    buf = io.BytesIO()
    highlighted.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


_GEMINI_OBJECT_REMOVAL_PROMPT = (
    "You will be given two versions of the same real estate listing photo. "
    "The first image is the original, unedited photo. The second image is "
    "the same photo with an unwanted object or area highlighted in solid "
    "bright magenta. Remove whatever is underneath the magenta highlight in "
    "the second image, and fill that region in so it naturally matches the "
    "surrounding scene from the original photo (correct lighting, "
    "perspective, texture, and color -- no visible seam or leftover "
    "magenta). Do not change anything outside the highlighted region. "
    "Return only the final corrected photo as an image, with no text, no "
    "highlight, and no magenta remaining."
)


def _extract_inline_image(data: dict) -> Optional[bytes]:
    candidates = data.get("candidates") or []
    if not candidates:
        return None
    parts = (candidates[0].get("content") or {}).get("parts") or []
    for part in parts:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            return base64.b64decode(inline["data"])
    return None


async def _run_gemini_inpaint(image_bytes: bytes, mask_bytes: bytes, mime_type: str) -> bytes:
    """Calls Gemini's image model with the original + a highlight-overlay
    copy of the photo (see _burn_mask_overlay). Raises on any failure
    (missing key, network, non-2xx, no image in the response)."""
    api_key = os.environ.get(_GEMINI_API_KEY_ENV, "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")

    highlighted_bytes = _burn_mask_overlay(image_bytes, mask_bytes)

    payload = {
        "contents": [{
            "parts": [
                {"text": _GEMINI_OBJECT_REMOVAL_PROMPT},
                {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode()}},
                {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(highlighted_bytes).decode()}},
            ]
        }],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
        },
    }

    url = gemini_generate_content_url(get_gemini_image_model())
    async with httpx.AsyncClient(timeout=_GEMINI_TIMEOUT_SECONDS) as client:
        resp = await client.post(url, params={"key": api_key}, json=payload)
        resp.raise_for_status()
        data = resp.json()

    result = _extract_inline_image(data)
    if not result:
        raise RuntimeError("Gemini response contained no image data")
    return result


# ---------------------------------------------------------------------------
# Public entry point: failover chain
# ---------------------------------------------------------------------------

async def remove_object(
    image_bytes: bytes,
    mask_bytes: bytes,
    *,
    job_id: str,
    upload_id: str,
    mime_type: Optional[str] = None,
) -> Optional[Dict]:
    """
    Runs mask-guided object removal through the failover chain described in
    this module's docstring. Returns {"provider": ..., "image_bytes": ...}
    on success, or None once every configured engine has failed/is
    unconfigured -- callers must treat None as "could not be processed",
    not silently substitute the original image.
    """
    mime_type = mime_type or _detect_mime(image_bytes)

    chain = []
    if _mac_configured():
        chain.append("bricks_mac_inference")
    if _gemini_configured():
        chain.append("gemini")

    if not chain:
        logger.warning(
            f"object_removal: no provider configured (job {job_id}, upload {upload_id}) — asset dropped"
        )
        return None

    for candidate in chain:
        start = time.monotonic()
        try:
            if candidate == "bricks_mac_inference":
                result_bytes = await _run_mac_inpaint(image_bytes, mask_bytes, mime_type)
            elif candidate == "gemini":
                result_bytes = await _run_gemini_inpaint(image_bytes, mask_bytes, mime_type)
            else:  # pragma: no cover -- defensive, chain only ever contains the two above
                continue
            _log_object_removal_attempt(candidate, job_id, upload_id, "success", start)
            return {"provider": candidate, "image_bytes": result_bytes}
        except Exception as e:
            _log_object_removal_attempt(candidate, job_id, upload_id, "failure", start, error=e)
            continue

    logger.warning(
        f"object_removal: every configured provider failed for upload {upload_id} (job {job_id}) — asset dropped"
    )
    return None


async def upload_object_removal_result(storage_client, job_id: str, upload_id: str, image_bytes: bytes) -> str:
    """
    Uploads a completed object-removal result to Supabase Storage.
    Deliberately does NOT touch the `uploads` row -- this capability isn't
    wired into the automatic pipeline (see module docstring), and this repo
    doesn't own the `uploads` schema's columns for a "reviewed/accepted"
    state. The caller (routers/post_production.py) hands the resulting
    signed URL back to whatever invoked this so a human/future step can
    decide what to do with it.
    Returns the storage path: f"object-removed/{job_id}/{upload_id}_{timestamp}.jpg"
    """
    storage_bucket = os.getenv("SUPABASE_STORAGE_BUCKET", "brxs-deliverables")
    storage_path = f"object-removed/{job_id}/{upload_id}_{int(time.time())}.jpg"
    bucket = storage_client.from_(storage_bucket)
    await bucket.upload(
        path=storage_path,
        file=image_bytes,
        file_options={"content-type": "image/jpeg", "upsert": "true"},
    )
    logger.info(f"object_removal: uploaded result to {storage_bucket}/{storage_path} ({len(image_bytes):,} bytes)")
    return storage_path
