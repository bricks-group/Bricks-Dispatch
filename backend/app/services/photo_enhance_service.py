"""
In-house real estate photo enhancement.

Fully self-hosted, free/open-source (OpenCV + Pillow + numpy) -- no
third-party API, no per-image cost, no vendor account. Supersedes
local_enhance.py's Tier-3-only framing: this is meant to become a real
processing tier, not just a last-resort fallback (see post_production.py
routing, and project_bricks_inhouse_post_production memory for the
decision context).

Pipeline:
  1. CLAHE on the L channel (LAB color space) -- local contrast/shadow-
     highlight recovery, the "HDR look" without needing bracketed
     exposures (Bricks captures single-frame-per-shot; there is no
     bracket-fusion input available).
  2. Edge-aware detail enhancement (OpenCV's built-in detailEnhance,
     non-photorealistic-rendering module -- no contrib package needed)
     for local micro-contrast, in the spirit of local Laplacian filtering.
  3. Grey-world white balance (carried over from local_enhance.py --
     CLAHE only touches luminance, so this still matters for color cast).
  4. Light unsharp mask for final crispness.

Falls back to the plain PIL/numpy pipeline (auto-levels + unsharp +
grey-world, no OpenCV) if cv2 is unavailable for any reason, so a
packaging/environment issue degrades quality rather than breaking the
pipeline.
"""

import io
import logging
import os
from typing import Optional

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

logger = logging.getLogger(__name__)

STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "brxs-deliverables")

_WINDOW_PULL_ENABLED_ENV = "INHOUSE_WINDOW_PULL_ENABLED"
_SKY_ENHANCE_ENABLED_ENV = "INHOUSE_SKY_ENHANCE_ENABLED"


def window_pull_enabled() -> bool:
    return os.environ.get(_WINDOW_PULL_ENABLED_ENV, "true").lower() == "true"


def sky_enhance_enabled() -> bool:
    return os.environ.get(_SKY_ENHANCE_ENABLED_ENV, "true").lower() == "true"


# ---------------------------------------------------------------------------
# Enhancement
# ---------------------------------------------------------------------------

def _enhance_with_opencv(arr_bgr) -> "np.ndarray":
    import cv2

    lab = cv2.cvtColor(arr_bgr, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_channel = clahe.apply(l_channel)
    lab = cv2.merge((l_channel, a_channel, b_channel))
    bgr = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    # Edge-aware local-contrast boost (mirrors local Laplacian filtering's
    # goal -- recover local detail -- without needing opencv-contrib's
    # ximgproc module).
    bgr = cv2.detailEnhance(bgr, sigma_s=10, sigma_r=0.15)

    return bgr


def _grey_world_white_balance(arr: "np.ndarray") -> "np.ndarray":
    r_mean = float(arr[:, :, 0].mean())
    g_mean = float(arr[:, :, 1].mean())
    b_mean = float(arr[:, :, 2].mean())
    global_mean = (r_mean + g_mean + b_mean) / 3.0
    if r_mean > 0 and g_mean > 0 and b_mean > 0:
        arr[:, :, 0] = np.clip(arr[:, :, 0] * (global_mean / r_mean), 0, 255)
        arr[:, :, 1] = np.clip(arr[:, :, 1] * (global_mean / g_mean), 0, 255)
        arr[:, :, 2] = np.clip(arr[:, :, 2] * (global_mean / b_mean), 0, 255)
    return arr


def _enhance_with_pil_only(img: Image.Image) -> Image.Image:
    """Fallback path if cv2 is unavailable -- the original local_enhance.py
    algorithm (auto-levels + unsharp + grey-world), unchanged."""
    arr = np.array(img, dtype=np.float32)
    channels = []
    for c in range(3):
        ch = arr[:, :, c]
        p2 = float(np.percentile(ch, 2))
        p98 = float(np.percentile(ch, 98))
        ch = (ch - p2) * 255.0 / (p98 - p2) if p98 > p2 else ch - p2
        channels.append(np.clip(ch, 0, 255))
    img = Image.fromarray(np.stack(channels, axis=2).astype(np.uint8), "RGB")
    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=150, threshold=3))
    arr = _grey_world_white_balance(np.array(img, dtype=np.float32))
    return Image.fromarray(arr.astype(np.uint8), "RGB")


# ---------------------------------------------------------------------------
# Window-pull (Phase 1, 2026-09-11)
# ---------------------------------------------------------------------------

# Mirrors qc_opencv.py's own blown-highlight detection style: a near-white
# grayscale threshold plus a minimum-area-percentage gate, rather than a new
# heuristic invented from scratch. qc_opencv.py flags "blown_highlights" at
# >5% of pixels reading pure 255 in the grayscale histogram; here the goal is
# to *fix* moderate blown windows before they ever reach that flagged state,
# so the area floor is intentionally lower (1%) and the brightness threshold
# a bit more forgiving (>=245, not only the single 255 bin) to actually catch
# typical blown-window regions in real listing photos.
_WINDOW_BRIGHT_THRESHOLD = 245
_WINDOW_MIN_AREA_PCT = 0.01
_WINDOW_MAX_AREA_PCT = 0.60  # near-entirely-blown frame -> a local pull can't help, skip

# A pixel that's already fully clipped to 255 has no tonal information left for
# a gamma curve to recover (1.0 ** anything == 1.0) -- a straight multiplicative
# exposure-compensation scale is what actually pulls a blown window down, same
# as reducing exposure by roughly two-thirds of a stop. Conservative on purpose:
# this is meant to recover a hint of window detail, not turn day into dusk.
_WINDOW_PULL_EXPOSURE_SCALE = 0.55
_WINDOW_PULL_STRENGTH = 0.65  # how much of the darkened copy to blend into the masked region
_WINDOW_MASK_FEATHER_RADIUS = 8


def window_pull(image: Image.Image) -> Image.Image:
    """
    Detect likely blown-highlight window regions (near-white, contiguous-ish,
    concentrated area) and locally reduce exposure / recover detail in just
    that masked region -- blending a gamma/exposure-compensated darker copy
    of the same region back in, not a global exposure change.

    No-ops (returns the image unchanged) if no significant blown-highlight
    region is detected, per spec -- this must never mangle a normal photo
    that just happens to contain a few bright pixels.
    """
    rgb = image.convert("RGB")
    arr = np.array(rgb, dtype=np.float32)
    gray = arr.mean(axis=2)

    mask = gray >= _WINDOW_BRIGHT_THRESHOLD
    mask_pct = float(mask.mean())
    if mask_pct < _WINDOW_MIN_AREA_PCT or mask_pct > _WINDOW_MAX_AREA_PCT:
        return rgb

    # Soften the raw pixel mask so the correction blends rather than leaving a
    # hard-edged patch -- a simple Gaussian feather via PIL, no cv2 dependency.
    mask_img = Image.fromarray((mask.astype(np.uint8) * 255), mode="L").filter(
        ImageFilter.GaussianBlur(radius=_WINDOW_MASK_FEATHER_RADIUS)
    )
    mask_soft = np.array(mask_img, dtype=np.float32)[:, :, None] / 255.0

    darker = np.clip(arr * _WINDOW_PULL_EXPOSURE_SCALE, 0, 255)
    blended = arr * (1 - mask_soft * _WINDOW_PULL_STRENGTH) + darker * (mask_soft * _WINDOW_PULL_STRENGTH)
    blended = np.clip(blended, 0, 255).astype(np.uint8)
    return Image.fromarray(blended, "RGB")


# ---------------------------------------------------------------------------
# Sky tone enhancement (Phase 1, 2026-09-11)
# ---------------------------------------------------------------------------

# NOT full sky replacement -- no compositing against a different sky image,
# that's an explicitly later phase. This only nudges saturation/contrast of
# the sky that's already in the shot, matching the tone-mapping-class portion
# of what Fotello's sky work does.
_SKY_TOP_FRACTION = 0.55  # sky lives in the upper portion of an exterior frame
_SKY_HUE_MIN_DEG = 180.0
_SKY_HUE_MAX_DEG = 250.0
_SKY_MIN_SATURATION = 25  # PIL HSV 'S' channel is 0-255
_SKY_WHITE_MIN_VALUE = 200  # overcast/haze sky: high value, low saturation
_SKY_WHITE_MAX_SATURATION = 60
_SKY_MIN_AREA_PCT = 0.04
_SKY_SATURATION_BOOST = 1.25
_SKY_CONTRAST_BOOST = 1.08
_SKY_MASK_FEATHER_RADIUS = 10


def enhance_sky(image: Image.Image) -> Image.Image:
    """
    Detect a likely sky region (blue/white high-luminance mask concentrated
    in the upper portion of the frame -- HSV-range + position heuristic) and
    apply a mild saturation/contrast boost within just that mask.

    No-ops (returns the image unchanged) if no sky-like region is detected --
    e.g. an interior shot, or any frame without a plausible sky band.
    """
    rgb = image.convert("RGB")
    width, height = rgb.size
    if height == 0 or width == 0:
        return rgb

    hsv = np.array(rgb.convert("HSV"), dtype=np.float32)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    hue_deg = h / 255.0 * 360.0

    blue_sky = (hue_deg >= _SKY_HUE_MIN_DEG) & (hue_deg <= _SKY_HUE_MAX_DEG) & (s > _SKY_MIN_SATURATION)
    white_sky = (v > _SKY_WHITE_MIN_VALUE) & (s < _SKY_WHITE_MAX_SATURATION)
    color_mask = blue_sky | white_sky

    top_cutoff = int(height * _SKY_TOP_FRACTION)
    position_mask = np.zeros((height, width), dtype=bool)
    position_mask[:top_cutoff, :] = True

    sky_mask = color_mask & position_mask
    sky_pct = float(sky_mask.mean())
    if sky_pct < _SKY_MIN_AREA_PCT:
        return rgb

    boosted = ImageEnhance.Color(rgb).enhance(_SKY_SATURATION_BOOST)
    boosted = ImageEnhance.Contrast(boosted).enhance(_SKY_CONTRAST_BOOST)

    mask_img = Image.fromarray((sky_mask.astype(np.uint8) * 255), mode="L").filter(
        ImageFilter.GaussianBlur(radius=_SKY_MASK_FEATHER_RADIUS)
    )
    mask_soft = np.array(mask_img, dtype=np.float32)[:, :, None] / 255.0

    arr_orig = np.array(rgb, dtype=np.float32)
    arr_boosted = np.array(boosted, dtype=np.float32)
    blended = arr_orig * (1 - mask_soft) + arr_boosted * mask_soft
    blended = np.clip(blended, 0, 255).astype(np.uint8)
    return Image.fromarray(blended, "RGB")


def enhance_image(image_bytes: bytes) -> bytes:
    """
    In-house photo enhancement: CLAHE + edge-aware detail enhancement +
    grey-world white balance + unsharp mask, plus two independently
    feature-flagged conservative refinements (window-pull, sky tone
    enhancement). Returns enhanced JPEG bytes. Self-hosted, free/open-source
    only -- no external API call.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    try:
        import cv2

        arr_rgb = np.array(img)
        arr_bgr = cv2.cvtColor(arr_rgb, cv2.COLOR_RGB2BGR)
        arr_bgr = _enhance_with_opencv(arr_bgr)
        arr_rgb = cv2.cvtColor(arr_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
        arr_rgb = _grey_world_white_balance(arr_rgb)
        img = Image.fromarray(arr_rgb.astype(np.uint8), "RGB")
        img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))
    except ImportError:
        logger.warning("photo_enhance_service: cv2 unavailable, falling back to PIL-only pipeline")
        img = _enhance_with_pil_only(img)

    # Additive refinements -- each independently flagged and wrapped so a
    # failure in either never breaks the overall pipeline for this image;
    # fall back to the image without that specific enhancement and keep going.
    if window_pull_enabled():
        try:
            img = window_pull(img)
        except Exception:
            logger.warning("photo_enhance_service: window_pull failed, continuing without it", exc_info=True)

    if sky_enhance_enabled():
        try:
            img = enhance_sky(img)
        except Exception:
            logger.warning("photo_enhance_service: enhance_sky failed, continuing without it", exc_info=True)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Storage upload
# ---------------------------------------------------------------------------

async def upload_enhanced(storage_client, job_id: str, asset_id: str, enhanced_bytes: bytes) -> str:
    """
    Upload an in-house-enhanced image to Supabase Storage.
    Returns the storage path: f"enhanced/{job_id}/{asset_id}.jpg"
    """
    storage_path = f"enhanced/{job_id}/{asset_id}.jpg"
    bucket = storage_client.from_(STORAGE_BUCKET)
    await bucket.upload(
        path=storage_path,
        file=enhanced_bytes,
        file_options={"content-type": "image/jpeg", "upsert": "true"},
    )
    logger.info(
        f"In-house enhance: uploaded to {STORAGE_BUCKET}/{storage_path} "
        f"({len(enhanced_bytes):,} bytes)"
    )
    return storage_path


async def upload_qc_variant(storage_client, job_id: str, asset_id: str, enhanced_bytes: bytes) -> Optional[str]:
    """
    Downscale the already-enhanced image to QC review size and upload it.
    Reuses make_qc_variant_bytes from storage.py so QC never has to fetch the
    full-res enhanced original for a review pass.
    Returns the storage path, or None if the downscale fails.
    """
    from .storage import make_qc_variant_bytes

    try:
        qc_bytes = make_qc_variant_bytes(enhanced_bytes)
    except Exception:
        logger.warning(f"In-house enhance: QC variant downscale failed for upload {asset_id}")
        return None

    storage_path = f"enhanced/{job_id}/qc_{asset_id}.jpg"
    bucket = storage_client.from_(STORAGE_BUCKET)
    await bucket.upload(
        path=storage_path,
        file=qc_bytes,
        file_options={"content-type": "image/jpeg", "upsert": "true"},
    )
    return storage_path
