"""
In-house MLS listing stills post-production (Canadian desk standard).

Self-hosted OpenCV + Pillow + numpy. No third-party edit API. BoxBrownie /
virtual staging / object removal / day-to-dusk are NOT in this pipeline
(package add-ons only; staging delivers originals honestly).

Default stack (order), with honest flags via capabilities():

  1. EXIF auto-orient
  2. HDR merge when bracket siblings are provided (OpenCV MergeMertens);
     otherwise single-frame continue (honest — no invented exposures)
  3. Window pull from under/over bracket frames where available; soft
     single-frame highlight recovery otherwise (does not invent glass detail)
  4. Exterior sky replacement via SKY_SERVICE_URL (sky-service HTTP) when
     sky is detected; skipped cleanly when the service is unset/down
  5. Straighten verticals / level horizon (mild Hough-based rotate; no mushy
     full-room warp)
  6. WB / exposure polish (gentle CLAHE + grey-world — inviting interiors,
     not HDR-halo / oversat)
  7. 4:3 landscape crop (content-aware when source is portrait), long-edge
     cap LISTING_MAX_LONG_EDGE (2400), sRGB JPEG ~LISTING_JPEG_QUALITY (88)

enhance_image(bytes) — single-frame entry used by deliver_asset_without_vendor.
enhance_bracket(list[bytes]) — multi-frame entry for grouped +/-EV sets.
"""

from __future__ import annotations

import io
import logging
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageFilter, ImageOps

logger = logging.getLogger(__name__)

STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "brxs-deliverables")
SKY_SERVICE_URL = (os.getenv("SKY_SERVICE_URL") or "").rstrip("/")

# Listing geometry — MLS / Canadian brochure still convention.
# Long edge targets the 2048-2400 band: we cap at 2400 and never upscale.
LISTING_MAX_LONG_EDGE = 2400
LISTING_TARGET_MIN_LONG_EDGE = 2048  # documented band floor (no upscale)
LISTING_ASPECT: Tuple[int, int] = (4, 3)  # landscape width:height
LISTING_JPEG_QUALITY = 88  # mid of desk 85-90

# Back-compat for earlier tests / callers
LISTING_JPEG_Q = LISTING_JPEG_QUALITY


def capabilities() -> Dict[str, Any]:
    """Honest flags for what this build actually does."""
    sky = "http_sky_service" if SKY_SERVICE_URL else "unavailable"
    try:
        import cv2  # noqa: F401
        cv = True
    except ImportError:
        cv = False
    return {
        "exif_orient": True,
        "hdr_merge": "opencv_mertens" if cv else "unavailable",
        "window_pull": "bracket_blend" if cv else "single_frame_soft",
        "sky_replace": sky,
        "straighten": "hough_verticals" if cv else "unavailable",
        "wb_exposure": "clahe_greyworld",
        "listing_crop": "4:3_landscape_content",
        "long_edge_px": LISTING_MAX_LONG_EDGE,
        "jpeg_quality": LISTING_JPEG_QUALITY,
        "virtual_staging": False,
        "object_removal": False,
        "day_to_dusk": False,
        "opencv": cv,
    }


# ---------------------------------------------------------------------------
# Bracket grouping (filename / EXIF exposure bias)
# ---------------------------------------------------------------------------

_EV_SUFFIX = re.compile(
    r"(?P<stem>.+?)(?:[_-](?:ev)?(?P<ev>[+-]?\d+(?:\.\d+)?)|_m(?P<m>\d+)|_p(?P<p>\d+)|_und(?:er)?|_over|_ov)$",
    re.IGNORECASE,
)


def bracket_stem(filename: str) -> str:
    """Collapse +/-EV / under / over suffixes so siblings share one stem."""
    base = (filename or "").rsplit("/", 1)[-1]
    name = base.rsplit(".", 1)[0] if "." in base else base
    m = _EV_SUFFIX.match(name)
    if m:
        return m.group("stem").rstrip("_-")
    return re.sub(r"[_-]0$", "", name)


def exposure_bias_from_name(filename: str) -> Optional[float]:
    base = (filename or "").rsplit("/", 1)[-1]
    name = base.rsplit(".", 1)[0] if "." in base else base
    m = _EV_SUFFIX.match(name)
    if not m:
        return None
    if m.group("ev") is not None:
        try:
            return float(m.group("ev"))
        except ValueError:
            return None
    if m.group("m") is not None:
        return -float(m.group("m"))
    if m.group("p") is not None:
        return float(m.group("p"))
    low = name.lower()
    if "und" in low:
        return -2.0
    if "over" in low or low.endswith("_ov"):
        return 2.0
    return None


def exposure_bias_from_exif(image_bytes: bytes) -> Optional[float]:
    try:
        img = Image.open(io.BytesIO(image_bytes))
        exif = img.getexif()
        try:
            ifd = exif.get_ifd(0x8769) or {}
        except Exception:
            ifd = {}
        bias = ifd.get(0x9204)  # ExposureBiasValue
        if bias is None:
            return None
        if isinstance(bias, tuple) and len(bias) == 2 and bias[1]:
            return float(bias[0]) / float(bias[1])
        return float(bias)
    except Exception:
        return None


def group_assets_by_bracket(assets: Sequence[dict]) -> List[List[dict]]:
    """Group upload rows that look like the same room/angle +/-EV burst."""
    buckets: Dict[str, List[dict]] = {}
    order: List[str] = []
    for asset in assets:
        key = bracket_stem(str(asset.get("filename") or asset.get("id") or ""))
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(asset)
    return [buckets[k] for k in order]


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def _exif_auto_orient(img: Image.Image) -> Image.Image:
    try:
        oriented = ImageOps.exif_transpose(img)
        return oriented if oriented is not None else img
    except Exception:
        logger.warning("photo_enhance: EXIF transpose failed — using raw pixels")
        return img


def _resize_long_edge(img: Image.Image, max_long_edge: int = LISTING_MAX_LONG_EDGE) -> Image.Image:
    w, h = img.size
    long_edge = max(w, h)
    if long_edge <= max_long_edge or long_edge == 0:
        return img
    scale = max_long_edge / float(long_edge)
    return img.resize(
        (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
        Image.Resampling.LANCZOS,
    )


def _center_crop_aspect(img: Image.Image, aspect: Tuple[int, int] = LISTING_ASPECT) -> Image.Image:
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
        new_w = max(1, int(round(h * target)))
        left = max(0, (w - new_w) // 2)
        return img.crop((left, 0, left + new_w, h))
    new_h = max(1, int(round(w / target)))
    top = max(0, (h - new_h) // 2)
    return img.crop((0, top, w, top + new_h))


def _content_scores_vertical(img: Image.Image) -> np.ndarray:
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    gy = np.abs(np.diff(gray, axis=0, prepend=gray[:1]))
    gx = np.abs(np.diff(gray, axis=1, prepend=gray[:, :1]))
    energy = gx + gy
    mid = 1.0 - np.abs(gray - 128.0) / 128.0
    return (energy * (0.5 + 0.5 * mid)).mean(axis=1)


def _landscape_content_crop(img: Image.Image, aspect: Tuple[int, int] = LISTING_ASPECT) -> Image.Image:
    """Force landscape 4:3. Portrait sources pick the densest vertical band."""
    aw, ah = aspect
    target = aw / float(ah)
    w, h = img.size
    if w <= 0 or h <= 0:
        return img
    if w >= h:
        return _center_crop_aspect(img, aspect)

    crop_h = max(1, int(round(w / target)))
    if crop_h >= h:
        return _center_crop_aspect(img, aspect)

    scores = _content_scores_vertical(img)
    csum = np.cumsum(scores)
    best_top = 0
    best_score = -1.0
    for top in range(0, h - crop_h + 1):
        bottom = top + crop_h
        total = float(csum[bottom - 1] - (csum[top - 1] if top > 0 else 0.0))
        bias = 1.0 - 0.15 * abs((top + crop_h / 2) / h - 0.42)
        total *= bias
        if total > best_score:
            best_score = total
            best_top = top
    return img.crop((0, best_top, w, best_top + crop_h))


def format_listing_ready(
    img: Image.Image,
    *,
    max_long_edge: int = LISTING_MAX_LONG_EDGE,
    aspect: Tuple[int, int] = LISTING_ASPECT,
) -> Image.Image:
    """Content-aware 4:3 landscape crop, then long-edge cap."""
    cropped = _landscape_content_crop(img, aspect)
    return _resize_long_edge(cropped, max_long_edge)


def _encode_jpeg(img: Image.Image, quality: int = LISTING_JPEG_QUALITY) -> bytes:
    buf = io.BytesIO()
    rgb = img.convert("RGB")
    rgb.save(buf, format="JPEG", quality=quality, optimize=True, subsampling=1)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# HDR merge + window pull
# ---------------------------------------------------------------------------

def _to_bgr_float(img: Image.Image) -> np.ndarray:
    import cv2

    rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR).astype(np.float32) / 255.0


def merge_brackets_mertens(images: Sequence[Image.Image]) -> Image.Image:
    """Exposure-fuse bracket set with OpenCV MergeMertens. Needs >=2 frames."""
    import cv2

    if len(images) < 2:
        return images[0]
    mats = [_to_bgr_float(im) for im in images]
    h0, w0 = mats[0].shape[:2]
    aligned = []
    for m in mats:
        if m.shape[0] != h0 or m.shape[1] != w0:
            m = cv2.resize(m, (w0, h0), interpolation=cv2.INTER_AREA)
        aligned.append(m)
    merge = cv2.createMergeMertens()
    fused = merge.process(aligned)
    fused_u8 = np.clip(fused * 255.0, 0, 255).astype(np.uint8)
    rgb = cv2.cvtColor(fused_u8, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb, "RGB")


def window_pull_from_brackets(
    mid: Image.Image,
    under: Optional[Image.Image],
    over: Optional[Image.Image],
) -> Image.Image:
    """Recover window detail from under where mid is clipped; lift shadows from over."""
    if under is None and over is None:
        return mid
    arr = np.asarray(mid.convert("RGB"), dtype=np.float32)
    out = arr.copy()
    luma = arr.mean(axis=2)

    if under is not None:
        u = np.asarray(
            under.convert("RGB").resize(mid.size, Image.Resampling.LANCZOS),
            dtype=np.float32,
        )
        w_hi = np.clip((luma - 220.0) / 35.0, 0.0, 1.0)[..., None]
        out = out * (1.0 - w_hi) + u * w_hi

    if over is not None:
        o = np.asarray(
            over.convert("RGB").resize(mid.size, Image.Resampling.LANCZOS),
            dtype=np.float32,
        )
        w_lo = np.clip((40.0 - luma) / 40.0, 0.0, 1.0)[..., None]
        out = out * (1.0 - w_lo * 0.65) + o * (w_lo * 0.65)

    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def _pick_under_mid_over(
    images: Sequence[Image.Image],
    biases: Sequence[Optional[float]],
) -> Tuple[Image.Image, Optional[Image.Image], Optional[Image.Image]]:
    if len(images) == 1:
        return images[0], None, None
    scored = []
    for im, bias in zip(images, biases):
        if bias is not None:
            scored.append((float(bias), im))
        else:
            mean = float(np.asarray(im.convert("L"), dtype=np.float32).mean())
            scored.append((mean, im))
    scored.sort(key=lambda t: t[0])
    under = scored[0][1]
    over = scored[-1][1]
    mid = scored[len(scored) // 2][1]
    return mid, under, over


# ---------------------------------------------------------------------------
# Straighten + tone
# ---------------------------------------------------------------------------

def straighten_verticals(img: Image.Image, max_degrees: float = 4.0) -> Image.Image:
    """Mild rotate to level dominant near-verticals. Skips if signal is weak."""
    try:
        import cv2
    except ImportError:
        return img

    arr = np.asarray(img.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 60, 160, apertureSize=3)
    min_len = max(20, min(arr.shape[:2]) // 6)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=80,
        minLineLength=min_len, maxLineGap=20,
    )
    if lines is None:
        return img

    angles = []
    for line in lines:
        # OpenCV may return shape (N,1,4) or (N,4) depending on version.
        pts = line[0] if getattr(line, 'shape', ()) == (1, 4) or (hasattr(line, '__len__') and len(line) == 1) else line
        x1, y1, x2, y2 = [int(v) for v in pts]
        dx, dy = (x2 - x1), (y2 - y1)
        if dx == 0 and dy == 0:
            continue
        ang = np.degrees(np.arctan2(dy, dx))
        dev = ang - 90.0 if ang > 0 else ang + 90.0
        if abs(dev) <= max_degrees * 1.5:
            angles.append(dev)
    if len(angles) < 4:
        return img
    median = float(np.median(angles))
    if abs(median) < 0.15 or abs(median) > max_degrees:
        return img
    return img.rotate(-median, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=(0, 0, 0))


def _enhance_with_opencv(arr_bgr) -> np.ndarray:
    import cv2

    lab = cv2.cvtColor(arr_bgr, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=1.6, tileGridSize=(8, 8))
    l_ch = clahe.apply(l_ch)
    lab = cv2.merge((l_ch, a_ch, b_ch))
    bgr = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    bgr = cv2.detailEnhance(bgr, sigma_s=8, sigma_r=0.12)
    return bgr


def _grey_world_white_balance(arr: np.ndarray) -> np.ndarray:
    r_mean = float(arr[:, :, 0].mean())
    g_mean = float(arr[:, :, 1].mean())
    b_mean = float(arr[:, :, 2].mean())
    global_mean = (r_mean + g_mean + b_mean) / 3.0
    if r_mean > 0 and g_mean > 0 and b_mean > 0:
        def _gain(ch_mean: float) -> float:
            return float(np.clip(global_mean / ch_mean, 0.85, 1.15))

        arr[:, :, 0] = np.clip(arr[:, :, 0] * _gain(r_mean), 0, 255)
        arr[:, :, 1] = np.clip(arr[:, :, 1] * _gain(g_mean), 0, 255)
        arr[:, :, 2] = np.clip(arr[:, :, 2] * _gain(b_mean), 0, 255)
    return arr


def _enhance_with_pil_only(img: Image.Image) -> Image.Image:
    arr = np.array(img, dtype=np.float32)
    channels = []
    for c in range(3):
        ch = arr[:, :, c]
        p2 = float(np.percentile(ch, 2))
        p98 = float(np.percentile(ch, 98))
        ch = (ch - p2) * 255.0 / (p98 - p2) if p98 > p2 else ch - p2
        channels.append(np.clip(ch, 0, 255))
    img = Image.fromarray(np.stack(channels, axis=2).astype(np.uint8), "RGB")
    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))
    arr = _grey_world_white_balance(np.array(img, dtype=np.float32))
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def _apply_tone_enhance(img: Image.Image) -> Image.Image:
    try:
        import cv2

        arr_rgb = np.array(img)
        arr_bgr = cv2.cvtColor(arr_rgb, cv2.COLOR_RGB2BGR)
        arr_bgr = _enhance_with_opencv(arr_bgr)
        arr_rgb = cv2.cvtColor(arr_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
        arr_rgb = _grey_world_white_balance(arr_rgb)
        out = Image.fromarray(arr_rgb.astype(np.uint8), "RGB")
        return out.filter(ImageFilter.UnsharpMask(radius=1, percent=100, threshold=3))
    except ImportError:
        logger.warning("photo_enhance: cv2 unavailable — PIL-only tone path")
        return _enhance_with_pil_only(img)


# ---------------------------------------------------------------------------
# Sky replace (HTTP -> sky-service; never invent staging)
# ---------------------------------------------------------------------------

def sky_region_likely(img: Image.Image) -> bool:
    """Cheap heuristic: top band is bright + bluish."""
    w, h = img.size
    if h < 32 or w < 32:
        return False
    top = img.crop((0, 0, w, max(1, h // 5))).convert("RGB")
    arr = np.asarray(top, dtype=np.float32)
    r, g, b = arr[:, :, 0].mean(), arr[:, :, 1].mean(), arr[:, :, 2].mean()
    bright = (r + g + b) / 3.0
    blue_dominant = b > r + 8 and b > g + 4
    return bright > 140 and (blue_dominant or bright > 200)


def replace_sky_via_service(image_bytes: bytes, timeout_s: float = 45.0) -> Optional[bytes]:
    """POST to sky-service /replace-sky. Returns JPEG bytes or None."""
    if not SKY_SERVICE_URL:
        return None
    try:
        import httpx
    except ImportError:
        logger.warning("photo_enhance: httpx missing — cannot call sky-service")
        return None
    url = f"{SKY_SERVICE_URL}/replace-sky"
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(url, files={"file": ("still.jpg", image_bytes, "image/jpeg")})
            if resp.status_code != 200 or not resp.content:
                logger.warning("photo_enhance: sky-service HTTP %s", resp.status_code)
                return None
            return resp.content
    except Exception as e:
        logger.warning("photo_enhance: sky-service call failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Public enhance entrypoints
# ---------------------------------------------------------------------------

def _load_rgb(image_bytes: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(image_bytes))
    return _exif_auto_orient(img).convert("RGB")


def enhance_bracket(
    frames: Sequence[bytes],
    filenames: Optional[Sequence[str]] = None,
    *,
    apply_sky: bool = True,
) -> bytes:
    """Full MLS stack for a bracket set (or a single frame)."""
    if not frames:
        raise ValueError("enhance_bracket requires at least one frame")

    images = [_load_rgb(b) for b in frames]
    biases: List[Optional[float]] = []
    for i, b in enumerate(frames):
        bias = exposure_bias_from_exif(b)
        if bias is None and filenames and i < len(filenames):
            bias = exposure_bias_from_name(filenames[i])
        biases.append(bias)

    used_hdr = False
    used_window = False
    used_sky = False

    mid, under, over = _pick_under_mid_over(images, biases)

    if len(images) >= 2:
        try:
            mid = merge_brackets_mertens(images)
            used_hdr = True
        except Exception as e:
            logger.warning("photo_enhance: Mertens merge failed (%s) — using mid frame", e)

        pulled = window_pull_from_brackets(mid, under, over)
        if pulled is not mid:
            mid = pulled
            used_window = under is not None or over is not None

    working = mid
    if apply_sky and sky_region_likely(working):
        probe = _encode_jpeg(working, quality=90)
        replaced = replace_sky_via_service(probe)
        if replaced:
            working = _load_rgb(replaced)
            used_sky = True

    working = straighten_verticals(working)
    working = _apply_tone_enhance(working)
    working = format_listing_ready(working)
    out = _encode_jpeg(working)
    logger.info(
        "photo_enhance: done hdr=%s window_pull=%s sky=%s size=%dx%d",
        used_hdr, used_window, used_sky, working.size[0], working.size[1],
    )
    return out


def enhance_image(image_bytes: bytes) -> bytes:
    """Single-frame listing-ready enhance (no bracket siblings)."""
    return enhance_bracket([image_bytes], apply_sky=True)


# ---------------------------------------------------------------------------
# Storage upload
# ---------------------------------------------------------------------------

async def upload_enhanced(storage_client, job_id: str, asset_id: str, enhanced_bytes: bytes) -> str:
    storage_path = f"enhanced/{job_id}/{asset_id}.jpg"
    bucket = storage_client.from_(STORAGE_BUCKET)
    await bucket.upload(
        path=storage_path,
        file=enhanced_bytes,
        file_options={"content-type": "image/jpeg", "upsert": "true"},
    )
    logger.info(
        "In-house enhance: uploaded to %s/%s (%s bytes)",
        STORAGE_BUCKET, storage_path, f"{len(enhanced_bytes):,}",
    )
    return storage_path


async def upload_qc_variant(storage_client, job_id: str, asset_id: str, enhanced_bytes: bytes) -> Optional[str]:
    from .storage import make_qc_variant_bytes

    try:
        qc_bytes = make_qc_variant_bytes(enhanced_bytes)
    except Exception:
        logger.warning("In-house enhance: QC variant downscale failed for upload %s", asset_id)
        return None

    storage_path = f"enhanced/{job_id}/qc_{asset_id}.jpg"
    bucket = storage_client.from_(STORAGE_BUCKET)
    await bucket.upload(
        path=storage_path,
        file=qc_bytes,
        file_options={"content-type": "image/jpeg", "upsert": "true"},
    )
    return storage_path
