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

import numpy as np
from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "brxs-deliverables")


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


def enhance_image(image_bytes: bytes) -> bytes:
    """
    In-house photo enhancement: CLAHE + edge-aware detail enhancement +
    grey-world white balance + unsharp mask. Returns enhanced JPEG bytes.
    Self-hosted, free/open-source only -- no external API call.
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
