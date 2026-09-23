"""
In-house real estate photo enhancement.

Fully self-hosted, free/open-source (OpenCV + Pillow + numpy) -- no
third-party API, no per-image cost, no vendor account. Supersedes
local_enhance.py's Tier-3-only framing: this is meant to become a real
processing tier, not just a last-resort fallback (see post_production.py
routing, and project_bricks_inhouse_post_production memory for the
decision context).

Pipeline (listing-ready stills):
  0. EXIF auto-orient (ImageOps.exif_transpose) so phone/camera rotations
     land upright before any math.
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
  5. Long-edge resize to LISTING_MAX_LONG_EDGE (2400px) — MLS / share
     friendly without shipping multi-megabyte camera originals.
  6. Center-crop to LISTING_ASPECT (4:3) — standard listing still frame.
  7. Encode JPEG at LISTING_JPEG_QUALITY (~90).

Falls back to the plain PIL/numpy pipeline (auto-levels + unsharp +
grey-world, no OpenCV) if cv2 is unavailable for any reason, so a
packaging/environment issue degrades quality rather than breaking the
pipeline.
"""

import io
import logging
import os
from typing import Optional, Tuple

import numpy as np
from PIL import Image, ImageFilter, ImageOps

logger = logging.getLogger(__name__)

STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "brxs-deliverables")

# Listing-delivery geometry (documented constants — change deliberately).
# Long edge capped so OS/Companion share downloads stay lean; 4:3 matches
# the MLS / brochure still convention Bricks ships on the listing file.
LISTING_MAX_LONG_EDGE = 2400
LISTING_ASPECT: Tuple[int, int] = (4, 3)  # width:height
LISTING_JPEG_QUALITY = 90


# ---------------------------------------------------------------------------
# Geometry helpers (listing-ready format / crop)
# ---------------------------------------------------------------------------

def _exif_auto_orient(img: Image.Image) -> Image.Image:
    """Apply EXIF Orientation so the pixel grid matches what the shooter saw."""
    try:
        oriented = ImageOps.exif_transpose(img)
        return oriented if oriented is not None else img
    except Exception:
        logger.warning("photo_enhance_service: EXIF transpose failed — using raw pixels")
        return img


def _resize_long_edge(img: Image.Image, max_long_edge: int = LISTING_MAX_LONG_EDGE) -> Image.Image:
    """Downscale so the longer side is at most max_long_edge. Never upscales."""
    w, h = img.size
    long_edge = max(w, h)
    if long_edge <= max_long_edge or long_edge == 0:
        return img
    scale = max_long_edge / float(long_edge)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    return img.resize((new_w, new_h), Image.Resampling.LANCZOS)


def _center_crop_aspect(
    img: Image.Image,
    aspect: Tuple[int, int] = LISTING_ASPECT,
) -> Image.Image:
    """Center-crop to the target aspect ratio (prefer center over letterbox)."""
    aw, ah = aspect
    if aw <= 0 or ah <= 0:
        return img
    target = aw / float(ah)
    w, h = img.size
    if w <= 0 or h <= 0:
        return img
    current = w / float(h)
    if abs(current - target) < 1e-6:
        return img
    if current > target:
        # Too wide — trim left/right
        new_w = max(1, int(round(h * target)))
        left = max(0, (w - new_w) // 2)
        return img.crop((left, 0, left + new_w, h))
    # Too tall — trim top/bottom
    new_h = max(1, int(round(w / target)))
    top = max(0, (h - new_h) // 2)
    return img.crop((0, top, w, top + new_h))


def format_listing_ready(
    img: Image.Image,
    *,
    max_long_edge: int = LISTING_MAX_LONG_EDGE,
    aspect: Tuple[int, int] = LISTING_ASPECT,
) -> Image.Image:
    """Resize long-edge then center-crop to listing aspect. Pure geometry."""
    return _center_crop_aspect(_resize_long_edge(img, max_long_edge), aspect)


def _encode_jpeg(img: Image.Image, quality: int = LISTING_JPEG_QUALITY) -> bytes:
    buf = io.BytesIO()
    rgb = img.convert("RGB")
    rgb.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


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


def _apply_tone_enhance(img: Image.Image) -> Image.Image:
    """CLAHE / detail / WB / unsharp — no geometry. img must already be RGB."""
    try:
        import cv2

        arr_rgb = np.array(img)
        arr_bgr = cv2.cvtColor(arr_rgb, cv2.COLOR_RGB2BGR)
        arr_bgr = _enhance_with_opencv(arr_bgr)
        arr_rgb = cv2.cvtColor(arr_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
        arr_rgb = _grey_world_white_balance(arr_rgb)
        out = Image.fromarray(arr_rgb.astype(np.uint8), "RGB")
        return out.filter(ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))
    except ImportError:
        logger.warning("photo_enhance_service: cv2 unavailable, falling back to PIL-only pipeline")
        return _enhance_with_pil_only(img)


def enhance_image(image_bytes: bytes) -> bytes:
    """
    Listing-ready in-house enhance: EXIF orient → tone enhance → long-edge
    resize (max 2400) → center-crop 4:3 → JPEG ~90. Self-hosted only.
    """
    img = Image.open(io.BytesIO(image_bytes))
    img = _exif_auto_orient(img).convert("RGB")
    img = _apply_tone_enhance(img)
    img = format_listing_ready(img)
    return _encode_jpeg(img)


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
