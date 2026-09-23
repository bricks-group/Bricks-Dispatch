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
  4. Exterior sky replacement when sky is detected: SKY_SERVICE_URL HTTP
     first; in-process OpenCV heuristic fallback when URL unset/down
     (natural blue gradient — desk-standard, no separate host required)
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
    try:
        import cv2  # noqa: F401
        cv = True
    except ImportError:
        cv = False
    if SKY_SERVICE_URL:
        sky = "http_sky_service"
    elif cv:
        sky = "opencv_heuristic"
    else:
        sky = "unavailable"
    return {
        "exif_orient": True,
        "hdr_merge": "opencv_mertens" if cv else "unavailable",
        "window_pull": "glass_only_bracket" if cv else "single_frame_soft",
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
    """Soft exposure fusion. Biases mid contrast; do not rely on this alone for MLS."""
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
    # Lower contrast weight → less "HDR crunch"; keep exposure/sat moderate.
    merge = cv2.createMergeMertens(contrast_weight=0.35, saturation_weight=0.2, exposure_weight=1.0)
    fused = merge.process(aligned)
    fused_u8 = np.clip(fused * 255.0, 0, 255).astype(np.uint8)
    rgb = cv2.cvtColor(fused_u8, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb, "RGB")


def _mid_biased_body(
    mid: Image.Image,
    fused: Optional[Image.Image],
    *,
    fused_mix: float = 0.35,
) -> Image.Image:
    """Room/exterior body stays mostly mid; fusion only where mid is weak.

    Frame v2 P0 — bright sky / sun-adjacent: bias harder to mid (never average
    highlight stress into muddy yellow-grey).
    """
    if fused is None:
        return mid
    a = np.asarray(mid.convert("RGB"), dtype=np.float32)
    b = np.asarray(
        fused.convert("RGB").resize(mid.size, Image.Resampling.LANCZOS),
        dtype=np.float32,
    )
    luma = a.mean(axis=2)
    h = luma.shape[0]
    yy = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]

    # Shadows: allow a little fused lift
    w = np.clip((50.0 - luma) / 50.0, 0.0, 1.0) * 0.45

    # Highlight stress: prefer mid plate, not fused mud (Frame cove P0)
    highlight = np.clip((luma - 200.0) / 40.0, 0.0, 1.0)
    # Upper-frame bright = likely sky — zero out fusion there
    skyish = highlight * np.clip(1.0 - yy * 1.15, 0.0, 1.0)
    w = w * (1.0 - skyish)
    # Also kill fusion anywhere mid is already bright + low-chroma (sky/window blow)
    chroma = a.max(axis=2) - a.min(axis=2)
    bright_flat = highlight * np.clip((28.0 - chroma) / 28.0, 0.0, 1.0)
    w = w * (1.0 - bright_flat)

    w = (w * float(np.clip(fused_mix / 0.35, 0.4, 1.0)))[..., None]
    out = a * (1.0 - w) + b * w
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def _glass_only_matte(
    mid_rgb: np.ndarray,
    under_rgb: np.ndarray,
) -> np.ndarray:
    """Glass-only soft matte. Wall/trim/plant leaks hard-rejected (Frame v2 P0).

    Seeds on blown low-chroma mid + usable −EV exterior, then morphologically
    opens away thin bleeds, erodes so feather stays inside glass, drops weak
    components. Low confidence → 0 (keep mid pane — never grey fill).
    """
    import cv2

    mid = mid_rgb.astype(np.float32)
    under = under_rgb.astype(np.float32)
    luma_m = mid.mean(axis=2)
    luma_u = under.mean(axis=2)
    chroma_m = mid.max(axis=2) - mid.min(axis=2)
    chroma_u = under.max(axis=2) - under.min(axis=2)

    # Mid: blown / near-white and relatively flat (glass glare, not painted wall)
    hi = np.clip((luma_m - 210.0) / 35.0, 0.0, 1.0)
    flat = np.clip((32.0 - chroma_m) / 32.0, 0.0, 1.0)

    # Under: has exterior content (foliage chroma or mid-tone structure)
    under_ok = np.clip((luma_u - 20.0) / 35.0, 0.0, 1.0) * np.clip((215.0 - luma_u) / 50.0, 0.0, 1.0)
    under_detail = np.clip(chroma_u / 40.0, 0.0, 1.0)
    # Real DR: under darker than blown mid
    delta = np.clip((luma_m - luma_u) / 50.0, 0.0, 1.0)

    seed = hi * flat * under_ok * np.clip(0.25 + delta + 0.35 * under_detail, 0.0, 1.0)
    seed = cv2.GaussianBlur(seed.astype(np.float32), (0, 0), sigmaX=1.2)

    # Binary for component cleanup
    conf_thresh = 0.42
    binary = (seed >= conf_thresh).astype(np.uint8) * 255
    # Open: kill thin wall/pillar smudges and plant edge leaks
    k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, k_open, iterations=1)
    # Close small muntin gaps inside a pane (keep as one pane)
    k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, k_close, iterations=1)
    # Erode so feather stays inside glass (not onto trim/curtains)
    k_erode = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    binary = cv2.erode(binary, k_erode, iterations=1)

    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    h, w = binary.shape
    min_area = max(80, int(0.0008 * h * w))
    keep = np.zeros_like(seed, dtype=np.float32)
    for i in range(1, n_labels):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        comp = labels == i
        mean_conf = float(seed[comp].mean()) if comp.any() else 0.0
        # Weak / wall-bleed components → discard (fall back to mid)
        if mean_conf < conf_thresh:
            continue
        # Aspect sanity: reject huge full-wall blobs (matte painted the room)
        bw = int(stats[i, cv2.CC_STAT_WIDTH])
        bh = int(stats[i, cv2.CC_STAT_HEIGHT])
        if bw > 0.85 * w and bh > 0.55 * h:
            continue
        keep[comp] = seed[comp]

    # Soft feather inside glass only
    matte = cv2.GaussianBlur(keep, (0, 0), sigmaX=2.0)
    # Hard floor: below confidence → exact mid (no grey slab)
    matte = np.where(matte >= 0.28, matte, 0.0).astype(np.float32)
    return np.clip(matte, 0.0, 1.0)


def window_pull_from_brackets(
    mid: Image.Image,
    under: Optional[Image.Image],
    over: Optional[Image.Image],
) -> Image.Image:
    """−EV on glass only; muntins/curtains/walls stay mid. Low matte → mid pane."""
    if under is None and over is None:
        return mid
    arr = np.asarray(mid.convert("RGB"), dtype=np.float32)
    out = arr.copy()
    luma = arr.mean(axis=2)

    if under is not None:
        u_img = under.convert("RGB").resize(mid.size, Image.Resampling.LANCZOS)
        u = np.asarray(u_img, dtype=np.float32)
        matte = _glass_only_matte(arr, u)[..., None]
        # Where matte is 0, mid is unchanged — never invent grey fill
        out = out * (1.0 - matte) + u * matte

    if over is not None:
        o = np.asarray(
            over.convert("RGB").resize(mid.size, Image.Resampling.LANCZOS),
            dtype=np.float32,
        )
        # Gentle shadow open only — do not milk blacks (Frame P0)
        w_lo = np.clip((28.0 - luma) / 28.0, 0.0, 1.0)[..., None]
        out = out * (1.0 - w_lo * 0.35) + o * (w_lo * 0.35)

    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def restore_black_point(img: Image.Image, *, percentile: float = 1.2) -> Image.Image:
    """Re-seat blacks after fusion so furniture keeps depth."""
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    # Per-channel soft black: don't crush to pure 0 globally
    for c in range(3):
        lo = float(np.percentile(arr[:, :, c], percentile))
        arr[:, :, c] = np.clip((arr[:, :, c] - lo) * (255.0 / max(255.0 - lo, 1.0)), 0, 255)
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def gentle_s_curve(img: Image.Image, amount: float = 0.12) -> Image.Image:
    """Mild contrast curve without milking mids."""
    arr = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
    # smoothstep-ish S around mid
    x = arr
    curved = x + amount * (x - 0.5) * (1.0 - (2.0 * (x - 0.5)) ** 2)
    return Image.fromarray(np.clip(curved * 255.0, 0, 255).astype(np.uint8), "RGB")


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

def _crop_black_wedges(img: Image.Image, *, thresh: int = 12) -> Image.Image:
    """Drop near-black borders left by rotate(expand=True)."""
    arr = np.asarray(img.convert("RGB"))
    if arr.size == 0:
        return img
    mask = arr.max(axis=2) > thresh
    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    if len(rows) < 8 or len(cols) < 8:
        return img
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    left, right = int(cols[0]), int(cols[-1]) + 1
    # Keep a 1px pad if content fills almost all
    return img.crop((left, top, right, bottom))


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
    rotated = img.rotate(-median, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(0, 0, 0))
    return _crop_black_wedges(rotated)


def _enhance_with_opencv(arr_bgr) -> np.ndarray:
    import cv2

    lab = cv2.cvtColor(arr_bgr, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    # Frame P0: cap local contrast — old 1.6 + detailEnhance → muddy HDR crunch
    clahe = cv2.createCLAHE(clipLimit=1.15, tileGridSize=(8, 8))
    l_ch = clahe.apply(l_ch)
    lab = cv2.merge((l_ch, a_ch, b_ch))
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def _wall_anchored_white_balance(arr: np.ndarray) -> np.ndarray:
    """WB from bright-neutral wall/ceiling samples; keep practical lamp warmth."""
    rgb = arr.astype(np.float32)
    luma = rgb.mean(axis=2)
    # Bright, low-chroma candidates (walls / ceiling)
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    chroma = mx - mn
    mask = (luma > 140) & (luma < 245) & (chroma < 28)
    if mask.sum() < 200:
        # Fallback: upper third mid-bright band
        h = rgb.shape[0]
        band = np.zeros_like(mask)
        band[: max(1, h // 3), :] = True
        mask = band & (luma > 120) & (chroma < 40)
    if mask.sum() < 50:
        return arr  # leave alone rather than grey-world the whole room
    samples = rgb[mask]
    means = samples.mean(axis=0)
    target = float(means.mean())
    gains = np.clip(target / np.maximum(means, 1.0), 0.92, 1.08)
    out = rgb.copy()
    out[:, :, 0] = np.clip(out[:, :, 0] * gains[0], 0, 255)
    out[:, :, 1] = np.clip(out[:, :, 1] * gains[1], 0, 255)
    out[:, :, 2] = np.clip(out[:, :, 2] * gains[2], 0, 255)
    return out


def _grey_world_white_balance(arr: np.ndarray) -> np.ndarray:
    # Kept for PIL fallback path; prefer wall-anchored in main tone pass.
    return _wall_anchored_white_balance(arr)


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
        arr_rgb = _wall_anchored_white_balance(arr_rgb)
        out = Image.fromarray(arr_rgb.astype(np.uint8), "RGB")
        # Sharpen last and lightly (Frame P0)
        return out.filter(ImageFilter.UnsharpMask(radius=0.8, percent=55, threshold=4))
    except ImportError:
        logger.warning("photo_enhance: cv2 unavailable — PIL-only tone path")
        return _enhance_with_pil_only(img)


# ---------------------------------------------------------------------------
# Sky replace (HTTP sky-service, then in-process OpenCV heuristic)
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


def _generate_procedural_sky(width: int, height: int, seed: int = 42) -> Image.Image:
    """Subtle real-estate blue gradient (desk-standard — not neon)."""
    rng = np.random.default_rng(seed)
    # Clear blue at zenith → soft near-horizon (matches sky-service phase2 tones)
    top_color = np.array([95, 155, 220], dtype=np.float32)
    horizon_color = np.array([195, 215, 235], dtype=np.float32)
    t = np.linspace(0, 1, height, dtype=np.float32)[:, None, None]
    gradient = top_color[None, None, :] * (1.0 - t) + horizon_color[None, None, :] * t
    gradient = np.broadcast_to(gradient, (height, width, 3)).copy()
    # Very subtle cloud noise — brightening/darkening only
    nh, nw = max(1, height // 8), max(1, width // 8)
    noise = rng.normal(0, 1, size=(nh, nw)).astype(np.float32)
    nmin, nmax = float(noise.min()), float(noise.max())
    if nmax > nmin:
        noise_u8 = ((noise - nmin) / (nmax - nmin) * 255.0).astype(np.uint8)
    else:
        noise_u8 = np.full((nh, nw), 128, dtype=np.uint8)
    noise_img = Image.fromarray(noise_u8, "L").resize((width, height), Image.Resampling.BICUBIC)
    blur_r = max(2.0, max(width, height) / 60.0)
    noise_img = noise_img.filter(ImageFilter.GaussianBlur(radius=blur_r))
    clouds = np.asarray(noise_img, dtype=np.float32) / 255.0
    cloud_strength = (clouds[..., None] - 0.5) * 28.0
    sky = np.clip(gradient + cloud_strength, 0, 255).astype(np.uint8)
    return Image.fromarray(sky, "RGB")


def _sky_mask_opencv(img: Image.Image) -> Optional[np.ndarray]:
    """Soft float mask [0,1] for blue/bright upper sky. None if too little sky."""
    try:
        import cv2
    except ImportError:
        return None

    arr = np.asarray(img.convert("RGB"), dtype=np.uint8)
    h, w = arr.shape[:2]
    if h < 32 or w < 32:
        return None

    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    # Blue / cyan sky hues (OpenCV H is 0-179)
    blue = cv2.inRange(hsv, (90, 25, 120), (135, 255, 255))
    # Pale / overcast bright sky (low sat, high value) — still sky-like
    pale = cv2.inRange(hsv, (0, 0, 200), (179, 55, 255))
    # RGB blue-dominant + bright (catches some JPEG quirk hues)
    rf, gf, bf = arr[:, :, 0].astype(np.float32), arr[:, :, 1].astype(np.float32), arr[:, :, 2].astype(np.float32)
    bright = (rf + gf + bf) / 3.0
    rgb_blue = ((bf > rf + 6) & (bf > gf + 3) & (bright > 130)).astype(np.uint8) * 255

    raw = cv2.bitwise_or(blue, pale)
    raw = cv2.bitwise_or(raw, rgb_blue)

    # Prefer upper region: zero out bottom ~40%, ramp mid
    yy = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    height_gate = np.clip(1.0 - (yy - 0.35) / 0.35, 0.0, 1.0)  # 1 above ~35%, 0 below ~70%
    gated = (raw.astype(np.float32) / 255.0) * height_gate
    gated_u8 = np.clip(gated * 255.0, 0, 255).astype(np.uint8)

    # Clean speckles; keep largest upper blob
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    cleaned = cv2.morphologyEx(gated_u8, cv2.MORPH_OPEN, kernel)
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, kernel)

    # Soft feather
    feather = max(3, min(w, h) // 80)
    soft = cv2.GaussianBlur(cleaned, (0, 0), sigmaX=float(feather), sigmaY=float(feather))
    mask = soft.astype(np.float32) / 255.0

    sky_frac = float(mask.mean())
    top_frac = float(mask[: max(1, h // 5), :].mean())
    # Need meaningful sky coverage in the upper band
    if top_frac < 0.12 or sky_frac < 0.04:
        return None
    return mask


def _match_sky_ambient(sky: Image.Image, scene: Image.Image, mask: np.ndarray) -> Image.Image:
    """Scale sky brightness toward non-sky scene (sky-service phase2 idea)."""
    scene_arr = np.asarray(scene.convert("RGB"), dtype=np.float32)
    weights = (1.0 - mask)[..., None]
    wsum = float(weights.sum())
    if wsum < 1e-3:
        return sky
    scene_brightness = float((scene_arr * weights).sum() / (wsum * 3.0))
    sky_arr = np.asarray(sky, dtype=np.float32)
    sky_brightness = float(sky_arr.mean())
    if sky_brightness < 1e-3:
        return sky
    factor = float(np.clip(scene_brightness / sky_brightness, 0.75, 1.25))
    matched = np.clip(sky_arr * factor, 0, 255).astype(np.uint8)
    return Image.fromarray(matched, "RGB")


def replace_sky_opencv(img: Image.Image) -> Optional[Image.Image]:
    """In-process heuristic sky replace. Returns RGB Image or None if skipped."""
    try:
        import cv2  # noqa: F401
    except ImportError:
        return None
    if not sky_region_likely(img):
        return None
    mask = _sky_mask_opencv(img)
    if mask is None:
        return None
    w, h = img.size
    sky = _generate_procedural_sky(w, h)
    sky = _match_sky_ambient(sky, img, mask)
    scene = np.asarray(img.convert("RGB"), dtype=np.float32)
    sky_arr = np.asarray(sky, dtype=np.float32)
    alpha = mask[..., None]
    out = scene * (1.0 - alpha) + sky_arr * alpha
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def replace_sky(image_bytes: bytes, img: Optional[Image.Image] = None) -> Optional[Image.Image]:
    """HTTP sky-service when configured; OpenCV heuristic fallback otherwise."""
    if SKY_SERVICE_URL:
        replaced = replace_sky_via_service(image_bytes)
        if replaced:
            return _load_rgb(replaced)
        logger.info("photo_enhance: sky-service unavailable — trying OpenCV fallback")
    working = img if img is not None else _load_rgb(image_bytes)
    return replace_sky_opencv(working)


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
    body = mid

    if len(images) >= 2:
        fused = None
        try:
            fused = merge_brackets_mertens(images)
            used_hdr = True
        except Exception as e:
            logger.warning("photo_enhance: Mertens merge failed (%s) — mid body only", e)

        # Mid-biased body (Frame P0) — do not replace room with equal HDR
        body = _mid_biased_body(mid, fused, fused_mix=0.35)

        pulled = window_pull_from_brackets(body, under, over)
        if pulled is not body:
            body = pulled
            used_window = under is not None or over is not None

        body = restore_black_point(body)
        # Slight midtone punch for exteriors (Frame v2 P1); interiors stay gentler
        curve_amt = 0.14 if (under is not None and sky_region_likely(mid)) else 0.10
        body = gentle_s_curve(body, amount=curve_amt)

    working = body
    # Interiors with windows: −EV glass already carries sky — skip inventing sky.
    # Only attempt sky replace when no under plate was used and region looks like open sky.
    if apply_sky and under is None and sky_region_likely(working):
        probe = _encode_jpeg(working, quality=90)
        replaced_img = replace_sky(probe, working)
        if replaced_img is not None:
            working = replaced_img
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
