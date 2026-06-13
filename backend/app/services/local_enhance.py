"""
Tier-3 local photo enhancement fallback.

Used only when both Fotello (Tier-1) and Autoenhance (Tier-2) are unavailable
or have failed. Applies lightweight PIL-based corrections that are sufficient
for delivery without any external API dependency.

Pipeline:
  1. Auto-levels per channel (2nd–98th percentile stretch)
  2. Unsharp mask (radius=1, percent=150, threshold=3)
  3. Grey-world white balance

Never invoke on the hot dispatch path — only from the reconcile stuck-job sweep.
"""

import io
import logging
import os
from typing import Optional

import numpy as np
from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "brxs-deliverables")


# ---------------------------------------------------------------------------
# Enhancement
# ---------------------------------------------------------------------------

def enhance_image_pil(image_bytes: bytes) -> bytes:
    """
    Tier-3 photo enhancement fallback when Fotello and Autoenhance both fail.
    Auto-levels + unsharp mask + grey-world white balance.
    Returns enhanced JPEG bytes.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # --- Step 1: Auto-levels per channel (2nd–98th percentile stretch) ---
    arr = np.array(img, dtype=np.float32)
    channels = []
    for c in range(3):  # R, G, B
        ch = arr[:, :, c]
        p2 = float(np.percentile(ch, 2))
        p98 = float(np.percentile(ch, 98))
        if p98 > p2:
            ch = (ch - p2) * 255.0 / (p98 - p2)
        else:
            # Flat channel — leave unchanged (avoid div-by-zero)
            ch = ch - p2
        ch = np.clip(ch, 0, 255)
        channels.append(ch)
    levelled = np.stack(channels, axis=2).astype(np.uint8)
    img = Image.fromarray(levelled, "RGB")

    # --- Step 2: Unsharp mask ---
    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=150, threshold=3))

    # --- Step 3: Grey-world white balance ---
    arr = np.array(img, dtype=np.float32)
    r_mean = float(arr[:, :, 0].mean())
    g_mean = float(arr[:, :, 1].mean())
    b_mean = float(arr[:, :, 2].mean())
    global_mean = (r_mean + g_mean + b_mean) / 3.0
    if r_mean > 0 and g_mean > 0 and b_mean > 0:
        arr[:, :, 0] = np.clip(arr[:, :, 0] * (global_mean / r_mean), 0, 255)
        arr[:, :, 1] = np.clip(arr[:, :, 1] * (global_mean / g_mean), 0, 255)
        arr[:, :, 2] = np.clip(arr[:, :, 2] * (global_mean / b_mean), 0, 255)
    img = Image.fromarray(arr.astype(np.uint8), "RGB")

    # Save to JPEG bytes
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Storage upload
# ---------------------------------------------------------------------------

async def upload_enhanced(storage_client, job_id: str, asset_id: str, enhanced_bytes: bytes) -> str:
    """
    Upload a PIL-enhanced image to Supabase Storage.
    Returns the storage path: f"enhanced/{job_id}/{asset_id}.jpg"

    Args:
        storage_client: The Supabase storage client (db.storage).
        job_id:         Bricks GO job ID.
        asset_id:       Upload row ID used as the filename stem.
        enhanced_bytes: JPEG bytes from enhance_image_pil().

    Returns:
        Storage path string (not a signed URL — caller must sign if needed).
    """
    storage_path = f"enhanced/{job_id}/{asset_id}.jpg"
    bucket = storage_client.from_(STORAGE_BUCKET)
    await bucket.upload(
        path=storage_path,
        file=enhanced_bytes,
        file_options={"content-type": "image/jpeg", "upsert": "true"},
    )
    logger.info(
        f"PIL Tier-3: enhanced image uploaded to {STORAGE_BUCKET}/{storage_path} "
        f"({len(enhanced_bytes):,} bytes)"
    )
    return storage_path
