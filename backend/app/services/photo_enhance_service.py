"""
In-house MLS listing stills post-production (Canadian desk standard).

Acceptance bar: Frame PropTx MLS stills brief
(`/workspace/mls-brief/BRICKS_MLS_STILLS_ACCEPTANCE_BRIEF.md`) — PropTx
Art. 5.01 accuracy / persons / advertising bans + Bricks craft (beat mid).

Self-hosted OpenCV + Pillow + numpy. No third-party edit API. BoxBrownie /
virtual staging / object removal / day-to-dusk are NOT in this pipeline
(package add-ons only; staging delivers originals honestly).

Default stack (order), with honest flags via capabilities():

  1. EXIF auto-orient
  2. HDR merge when bracket siblings are provided: prefer Enfuse CLI
     (ppsp/RE-interior: mid+overs body stack, under reserved for −EV glass) when
     installed; else OpenCV MergeMertens uint8 mid+overs
     (OpenCV float[0,1] path is broken on cv2 5.x — near-black). Never
     invent exposures; single-frame continues honestly without siblings
  3. Magenta/pink clipped-highlight neutralize (neutral grey/white — no invent
     sky/dusk); window pull from under/over where available (prefer readable
     −EV when conf good; leave-mid only for mottled low-conf); soft single-frame
     highlight recovery otherwise. Highlight-cap vs mid; body brightness from mid+overs Enfuse/Mertens (not lift-chase).
  4. Exterior sky replacement when sky is detected: SKY_SERVICE_URL HTTP
     first; in-process OpenCV heuristic fallback when URL unset/down
     (natural blue gradient — desk-standard, no separate host required)
  5. Straighten verticals / level horizon (mild Hough-based rotate; skips when
     correction would bow strong horizontals / ceiling lines; no mushy warp)
  6. WB / exposure polish (gentle CLAHE + grey-world — inviting interiors,
     not HDR-halo / oversat; CLAHE gated off bright ceiling / near-white
     lit panels to prevent sparkle/fuzz)
  7. MLS export profile: 4:3 landscape, long-edge clamp 2048–2400 (no
     upscale), sRGB JPEG ~LISTING_JPEG_QUALITY with soft ~1–3 MB band;
     corner overlay detect → qc_soft_fail by default (aggressive inpaint
     OFF unless WATERMARK_STRIP=1)
  8. People soft-fail metadata (PropTx 5.01d) — quarantine flag for GO/MLS;
     never hard-blocks enhance

enhance_image(bytes) — single-frame entry used by deliver_asset_without_vendor.
enhance_bracket(list[bytes]) — multi-frame entry for grouped +/-EV sets.
enhance_bracket(..., return_meta=True) — bytes + MLS/QC soft-fail metadata.
"""

from __future__ import annotations

import io
import logging
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from . import image_io  # registers HEIF opener for iPhone stills

logger = logging.getLogger(__name__)

STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "brxs-deliverables")
SKY_SERVICE_URL = (os.getenv("SKY_SERVICE_URL") or "").rstrip("/")

# Listing geometry — Frame PropTx MLS stills brief (market convention +
# Bricks product). Long edge 2048–2400: we cap at 2400 and never upscale.
# PropTx public rules are silent on px/MB; do not invent harder mandates.
LISTING_MAX_LONG_EDGE = 2400
LISTING_TARGET_MIN_LONG_EDGE = 2048  # documented band floor (no upscale)
LISTING_ASPECT: Tuple[int, int] = (4, 3)  # landscape width:height
LISTING_JPEG_QUALITY = 88  # mid of desk 85-90
LISTING_JPEG_QUALITY_MIN = 78  # soft floor when shrinking toward size band
LISTING_JPEG_QUALITY_MAX = 92
# Soft size band (~1–3 MB when feasible). Not a hard PropTx statute.
LISTING_JPEG_TARGET_MIN_BYTES = 500_000
LISTING_JPEG_TARGET_MAX_BYTES = 3_000_000
MLS_EXPORT_PROFILE = "frame_proptx_mls_stills_v1"
MLS_BRIEF_REF = "Frame PropTx MLS stills acceptance brief (Art. 5.01)"

# Aggressive corner inpaint is OFF by default (Frame v3 cove: smudged rocks).
# Detect → qc_soft_fail reason only. Set WATERMARK_STRIP=1 to enable strip.
WATERMARK_STRIP_ENABLED = (os.getenv("WATERMARK_STRIP") or "").strip().lower() in (
    "1", "true", "yes", "on",
)
REASON_CORNER_OVERLAY = "possible_corner_overlay"

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
        "hdr_merge": (("enfuse_re_interior_mid_overs" if __import__("shutil").which("enfuse") else "opencv_mertens_u8_mid_overs") if cv else "unavailable"),
        "window_pull": "glass_only_bracket" if cv else "single_frame_soft",
        "sky_replace": sky,
        "straighten": "hough_verticals_gated" if cv else "unavailable",
        "wb_exposure": "clahe_greyworld_ceiling_gated",
        "listing_crop": "4:3_landscape_content",
        "long_edge_px": LISTING_MAX_LONG_EDGE,
        "long_edge_band_px": [LISTING_TARGET_MIN_LONG_EDGE, LISTING_MAX_LONG_EDGE],
        "jpeg_quality": LISTING_JPEG_QUALITY,
        "jpeg_target_bytes": [LISTING_JPEG_TARGET_MIN_BYTES, LISTING_JPEG_TARGET_MAX_BYTES],
        "color_space": "srgb",
        "mls_export_profile": MLS_EXPORT_PROFILE,
        "mls_brief": MLS_BRIEF_REF,
        "people_soft_fail": "yunet_proptx_5_01d",
        "watermark_strip": ("corner_inpaint_opt_in" if WATERMARK_STRIP_ENABLED else "detect_soft_fail"),
        "virtual_staging": False,
        "object_removal": False,
        "day_to_dusk": False,
        "opencv": cv,
    }


# ---------------------------------------------------------------------------
# Bracket grouping (filename / EXIF exposure bias)
# ---------------------------------------------------------------------------

_EV_SUFFIX = re.compile(
    r"(?P<stem>.+?)(?:[_-](?:ev)?(?P<ev>[+-]?\d+(?:\.\d+)?)|"
    r"[_-](?P<sign>minus|plus)(?P<mp>\d+(?:\.\d+)?)(?:ev)?|"
    r"_m(?P<m>\d+)|_p(?P<p>\d+)|_und(?:er)?|_over|_ov|_normal)$",
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
    if m.group("mp") is not None:
        try:
            mag = float(m.group("mp"))
        except ValueError:
            return None
        sign = (m.group("sign") or "").lower()
        return -mag if sign == "minus" else mag
    if m.group("m") is not None:
        return -float(m.group("m"))
    if m.group("p") is not None:
        return float(m.group("p"))
    low = name.lower()
    if low.endswith("_normal") or low.endswith("-normal"):
        return 0.0
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


def apply_listing_export(image_bytes: bytes) -> Tuple[bytes, Dict[str, Any]]:
    """PropTx MLS geometry/JPEG on already-enhanced bytes (Autoenhance path).

    Autoenhance full-res is often 3:2 / long-edge >2400. In-house enhance_bracket
    already ends with format_listing_ready; AE leftovers must get the same export
    before upload so listing/share/MLS zip stay board-safe.
    Does not re-run HDR/sky/tone — crop 4:3, long-edge cap, sRGB JPEG only.
    """
    img = _load_rgb(image_bytes)
    img = format_listing_ready(img)
    out = _encode_jpeg(img)
    inv = listing_export_invariants(out)
    meta: Dict[str, Any] = {
        "mls_export_profile": MLS_EXPORT_PROFILE,
        "mls_brief": MLS_BRIEF_REF,
        "listing_export_applied": True,
        "export": inv,
        "accept_claim": False,
    }
    try:
        from .mls_people_detect import people_soft_fail_metadata

        meta.update(people_soft_fail_metadata(out))
    except Exception as e:
        logger.warning("photo_enhance: people soft-fail meta skipped on listing export (%s)", e)
        meta.update(
            {
                "qc_people_detected": False,
                "qc_face_count": 0,
                "mls_quarantine": False,
                "qc_soft_fail": False,
                "qc_soft_fail_reasons": [],
            }
        )
    return out, meta


def _to_srgb_rgb(img: Image.Image) -> Image.Image:
    """Flatten to 8-bit sRGB RGB — drop exotic ICC / CMYK casts for MLS JPEG."""
    if img.mode == "CMYK":
        img = img.convert("RGB")
    # Pillow ImageCms is optional; convert("RGB") + strip info is enough for desk.
    rgb = img.convert("RGB")
    # Drop embedded ICC / EXIF so portals see plain sRGB JPEG without wild casts.
    rgb.info = {}
    return rgb


def _detect_corner_overlays(img: Image.Image) -> List[Dict[str, Any]]:
    """Detect possible burned corner logos/CTAs without mutating pixels.

    High-confidence only. Texture-rich corners (rocks, foliage, furniture)
    are rejected so we never soft-fail real scene content as overlay.
    """
    try:
        import cv2
    except ImportError:
        return []

    arr = np.asarray(_to_srgb_rgb(img), dtype=np.uint8)
    h, w = arr.shape[:2]
    if h < 200 or w < 200:
        return []

    # Smaller than v3 (~8%) — logos sit tight in corners; rocks fill large boxes
    side = int(max(40, min(h, w) * 0.08))
    corners = [
        ("tl", 0, 0, side, side),
        ("tr", w - side, 0, w, side),
        ("bl", 0, h - side, side, h),
        ("br", w - side, h - side, w, h),
    ]
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    found: List[Dict[str, Any]] = []
    for name, x0, y0, x1, y1 in corners:
        roi = gray[y0:y1, x0:x1]
        if roi.size == 0:
            continue
        edges = cv2.Canny(roi, 90, 180)
        edge_pct = float(edges.mean()) / 255.0
        # Logo plate: moderate edge density on otherwise flatter margin
        if edge_pct < 0.055 or edge_pct > 0.22:
            continue
        mean = float(roi.mean())
        std = float(roi.std())
        # Reject textured natural content (rocks/foliage → high local std)
        if std > 42.0:
            continue
        # Prefer bright/dark plate overlays, not mid-tone rock faces
        if 55 < mean < 200 and edge_pct < 0.09:
            continue
        # Flat-ish surround with localized high-contrast glyph
        lap = cv2.Laplacian(roi, cv2.CV_64F)
        lap_std = float(lap.std())
        if lap_std < 12.0 or lap_std > 85.0:
            continue
        found.append({
            "corner": name,
            "edge_pct": round(edge_pct, 4),
            "mean": round(mean, 1),
            "std": round(std, 1),
        })
    return found


def _strip_corner_watermarks(
    img: Image.Image,
    *,
    apply: Optional[bool] = None,
) -> Tuple[Image.Image, bool, List[str]]:
    """Corner overlay handling (PropTx 5.01c).

    Default (Frame v3 P0): detect only → soft-fail reasons; do NOT inpaint.
    Opt-in strip via apply=True or WATERMARK_STRIP=1. Never invent content
    outside high-conf corner plates.
    """
    do_apply = WATERMARK_STRIP_ENABLED if apply is None else bool(apply)
    hits = _detect_corner_overlays(img)
    reasons: List[str] = [REASON_CORNER_OVERLAY] if hits else []
    if not hits or not do_apply:
        return img, False, reasons

    try:
        import cv2
    except ImportError:
        return img, False, reasons

    arr = np.asarray(_to_srgb_rgb(img), dtype=np.uint8)
    h, w = arr.shape[:2]
    side = int(max(40, min(h, w) * 0.08))
    corner_boxes = {
        "tl": (0, 0, side, side),
        "tr": (w - side, 0, w, side),
        "bl": (0, h - side, side, h),
        "br": (w - side, h - side, w, h),
    }
    out = arr.copy()
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    stripped = False
    for hit in hits:
        x0, y0, x1, y1 = corner_boxes[hit["corner"]]
        roi = gray[y0:y1, x0:x1]
        edges = cv2.Canny(roi, 90, 180)
        mask = np.zeros((h, w), dtype=np.uint8)
        mask[y0:y1, x0:x1] = np.where(edges > 0, 255, 0).astype(np.uint8)
        mask[y0:y1, x0:x1] = cv2.dilate(
            mask[y0:y1, x0:x1], np.ones((3, 3), np.uint8), iterations=1
        )
        if int(mask.sum()) == 0:
            continue
        bgr = cv2.cvtColor(out, cv2.COLOR_RGB2BGR)
        repaired = cv2.inpaint(bgr, mask, 3, cv2.INPAINT_TELEA)
        out = cv2.cvtColor(repaired, cv2.COLOR_BGR2RGB)
        stripped = True
    if not stripped:
        return img, False, reasons
    return Image.fromarray(out, "RGB"), True, reasons


def _encode_jpeg(img: Image.Image, quality: int = LISTING_JPEG_QUALITY) -> bytes:
    """sRGB JPEG encode with soft ~1–3 MB size band when feasible."""
    rgb = _to_srgb_rgb(img)
    q = int(max(LISTING_JPEG_QUALITY_MIN, min(LISTING_JPEG_QUALITY_MAX, quality)))

    def _save(qq: int) -> bytes:
        buf = io.BytesIO()
        rgb.save(buf, format="JPEG", quality=qq, optimize=True, subsampling=1)
        return buf.getvalue()

    data = _save(q)
    # Shrink toward band if oversized (soft — stop at quality floor)
    while len(data) > LISTING_JPEG_TARGET_MAX_BYTES and q > LISTING_JPEG_QUALITY_MIN:
        q = max(LISTING_JPEG_QUALITY_MIN, q - 4)
        data = _save(q)
    # Nudge up only when tiny AND we have headroom (never force bloat)
    if len(data) < LISTING_JPEG_TARGET_MIN_BYTES and q < LISTING_JPEG_QUALITY:
        q = min(LISTING_JPEG_QUALITY, q + 4)
        data = _save(q)
    return data


def listing_export_invariants(image_bytes: bytes) -> Dict[str, Any]:
    """Check MLS export geometry/format invariants (convention + Bricks profile)."""
    img = Image.open(io.BytesIO(image_bytes))
    w, h = img.size
    long_edge = max(w, h)
    aspect = (w / float(h)) if h else 0.0
    target = LISTING_ASPECT[0] / float(LISTING_ASPECT[1])
    return {
        "format": (img.format or "").upper() or "JPEG",
        "mode": img.mode,
        "width": w,
        "height": h,
        "long_edge": long_edge,
        "landscape": w >= h,
        "aspect_ok": abs(aspect - target) < 0.02,
        "long_edge_ok": long_edge <= LISTING_MAX_LONG_EDGE,
        "long_edge_band_soft": LISTING_TARGET_MIN_LONG_EDGE <= long_edge <= LISTING_MAX_LONG_EDGE
        or long_edge < LISTING_TARGET_MIN_LONG_EDGE,  # no upscale — small sources allowed
        "bytes": len(image_bytes),
        "color_space": "srgb",
        "mls_export_profile": MLS_EXPORT_PROFILE,
    }


# ---------------------------------------------------------------------------
# HDR merge + window pull
# ---------------------------------------------------------------------------

def _to_bgr_float(img: Image.Image) -> np.ndarray:
    import cv2

    rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR).astype(np.float32) / 255.0


def _to_bgr_u8(img: Image.Image) -> np.ndarray:
    import cv2

    rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _body_luma_mean(img: Image.Image) -> float:
    """Listing body crop luma (sofa/rug family) — matches desk demo metrics."""
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    h, w = arr.shape[:2]
    y0, y1 = int(h * 0.38), int(h * 0.82)
    x0, x1 = int(w * 0.12), int(w * 0.72)
    crop = arr[y0:y1, x0:x1]
    return float(
        0.2126 * crop[:, :, 0].mean()
        + 0.7152 * crop[:, :, 1].mean()
        + 0.0722 * crop[:, :, 2].mean()
    )


def _enfuse_bin() -> Optional[str]:
    """Return path to enfuse CLI when present (optional — Vercel has no binary)."""
    import shutil

    return shutil.which("enfuse")


def _body_plates_for_fuse(images: Sequence[Image.Image]) -> List[Image.Image]:
    """Photomatix-Interior / ppsp practice: fuse mid+overs only.

    Darkest under plate(s) crush interior walls when included in the body
    stack. Keep them out of fusion; window_pull uses real −EV after merge.
    With 2 frames keep both. With ≥3 drop the darkest by mean luma.
    """
    if len(images) < 3:
        return list(images)
    scored = sorted(
        images,
        key=lambda im: float(np.asarray(im.convert("L"), dtype=np.float32).mean()),
    )
    # Drop darkest; keep mid→bright (and any extras brighter than mid)
    return list(scored[1:])


def merge_brackets_enfuse(images: Sequence[Image.Image]) -> Image.Image:
    """Enfuse CLI — proven RE-interior path (ppsp / Photomatix-Interior class).

    Body stack should be mid+overs only (callers exclude under). Soft-mask
    exposure fusion with bright optimum; windows get real −EV via
    window_pull_from_brackets after this merge. No generative sky invent.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    if len(images) < 2:
        return images[0]
    bin_path = _enfuse_bin()
    if not bin_path:
        raise RuntimeError("enfuse binary not found")

    h0, w0 = images[0].size
    with tempfile.TemporaryDirectory(prefix="enfuse_") as td:
        td_path = Path(td)
        inputs = []
        for i, im in enumerate(images):
            rgb = im.convert("RGB")
            if rgb.size != (w0, h0):
                rgb = rgb.resize((w0, h0), Image.Resampling.LANCZOS)
            p = td_path / f"b{i:02d}.tif"
            rgb.save(p, format="TIFF")
            inputs.append(p)
        out = td_path / "fused.jpg"
        # Photomatix Interior–class: exposure-driven, soft mask, bright optimum
        cmd = [
            bin_path,
            "--exposure-weight=1.0",
            "--saturation-weight=0.2",
            "--contrast-weight=0.0",
            # RE interior bright body (ppsp / tipsforRE Enfuse practice)
            "--exposure-optimum=0.80",
            "--exposure-width=0.25",
            "--soft-mask",
            "-o",
            str(out),
            *[str(p) for p in inputs],
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(
                f"enfuse failed rc={proc.returncode}: {(proc.stderr or proc.stdout)[:400]}"
            )
        return Image.open(out).convert("RGB")


def merge_brackets_mertens(images: Sequence[Image.Image]) -> Image.Image:
    """OpenCV MergeMertens (Mertens/Kautz/Van Reeth exposure fusion).

    CRITICAL: feed uint8 BGR. OpenCV 5.x MergeMertens on float[0,1] returns
    near-black (mean ~0.002) — that was crushing desk MLS darker than mid.

    Photomatix Interior–class bias: duplicate mid + over so well-exposed
    interior walls dominate; darkest plate stays available for window_pull.
    """
    import cv2

    if len(images) < 2:
        return images[0]

    mats = [_to_bgr_u8(im) for im in images]
    h0, w0 = mats[0].shape[:2]
    aligned = []
    for m in mats:
        if m.shape[0] != h0 or m.shape[1] != w0:
            m = cv2.resize(m, (w0, h0), interpolation=cv2.INTER_AREA)
        aligned.append(m)

    # Rank by mean luma (dark → bright). Body fuse = mid+overs only
    # (Photomatix Interior / ppsp). Under stays out — window_pull uses it.
    scored = sorted(range(len(aligned)), key=lambda i: float(aligned[i].mean()))
    ordered = [aligned[i] for i in scored]
    # Caller already passed mid+overs; still drop darkest if ≥3 slipped in.
    body = ordered[1:] if len(ordered) >= 3 else ordered
    mid_i = len(body) // 2
    merge_list: List[np.ndarray] = []
    for i, m in enumerate(body):
        merge_list.append(m)
        if i >= mid_i:
            merge_list.append(m)

    merge = cv2.createMergeMertens(
        contrast_weight=1.0, saturation_weight=0.75, exposure_weight=1.0
    )
    fused = merge.process(merge_list)
    # Output is float ~[0,1] (may slightly undershoot/overshoot)
    if float(np.nanmax(fused)) <= 1.5:
        fused_u8 = np.clip(fused * 255.0, 0, 255).astype(np.uint8)
    else:
        fused_u8 = np.clip(fused, 0, 255).astype(np.uint8)
    rgb = cv2.cvtColor(fused_u8, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb, "RGB")


def merge_exposure_fusion(
    images: Sequence[Image.Image],
    *,
    mid: Optional[Image.Image] = None,
) -> Tuple[Image.Image, str]:
    """Prefer Enfuse; fall back to proven uint8 Mertens. Reject near-black fuse.

    Returns (fused_image, backend_tag). backend_tag in {enfuse, mertens_u8, mid}.
    """
    if len(images) < 2:
        return images[0], "mid"

    mid_ref = mid if mid is not None else images[len(images) // 2]
    mid_body = _body_luma_mean(mid_ref)
    candidates: List[Tuple[str, Image.Image]] = []

    body_plates = _body_plates_for_fuse(images)
    logger.info(
        "photo_enhance: body fuse plates=%d/%d (mid+overs; under reserved for −EV glass)",
        len(body_plates), len(images),
    )

    if _enfuse_bin():
        try:
            candidates.append(("enfuse", merge_brackets_enfuse(body_plates)))
        except Exception as e:
            logger.warning("photo_enhance: Enfuse merge failed (%s) — Mertens fallback", e)

    try:
        candidates.append(("mertens_u8", merge_brackets_mertens(body_plates)))
    except Exception as e:
        logger.warning("photo_enhance: Mertens merge failed (%s)", e)

    best_tag, best_img, best_body = "mid", mid_ref, mid_body
    for tag, img in candidates:
        # Resize to mid for fair body compare
        if img.size != mid_ref.size:
            img = img.resize(mid_ref.size, Image.Resampling.LANCZOS)
        b = _body_luma_mean(img)
        # Reject near-black / crush (the cv2 float bug landed ~0.1)
        if b < max(8.0, mid_body * 0.55):
            logger.warning(
                "photo_enhance: reject %s fuse body=%.1f (mid=%.1f) — near-black/crush",
                tag, b, mid_body,
            )
            continue
        if b > best_body:
            best_tag, best_img, best_body = tag, img, b

    if best_tag == "mid":
        logger.warning("photo_enhance: no usable fuse — mid plate only")
    else:
        logger.info(
            "photo_enhance: fuse backend=%s body=%.1f mid=%.1f",
            best_tag, best_body, mid_body,
        )
    return best_img, best_tag


def _interior_body_lift(
    working: Image.Image,
    mid: Image.Image,
    *,
    target_body: float = 155.0,
    gain_cap: float = 1.55,
    shoulder_t: float = 0.945,
) -> Image.Image:
    """Frame-style body lift toward Heroes/AutoHDR-class readable interiors.

    Gain sized on full body_mean (sofa crop incl. bay glass region) toward
    ~155 Heroes bar. Applied only off true glass (mid-plate blown flat /
    near-clip) so −EV window pull stays. Luma soft-shoulder avoids milk.
    Does not invent sky. Cove mid-bias stays locked upstream.
    """
    try:
        import cv2
    except ImportError:
        return working

    a = np.asarray(working.convert("RGB"), dtype=np.float32) / 255.0
    cur = _body_luma_mean(working)
    if cur < 1.0:
        return working
    mid_body = _body_luma_mean(mid)
    # Heroes-class (~152–155) but never below a clear beat of mid
    target = float(max(target_body, mid_body * 1.20))
    gain = float(np.clip(target / cur, 0.98, float(gain_cap)))
    if gain <= 1.01:
        return working

    m = np.asarray(
        mid.convert("RGB").resize(working.size, Image.Resampling.LANCZOS),
        dtype=np.float32,
    )
    luma_m = m.mean(axis=2)
    chroma_m = m.max(axis=2) - m.min(axis=2)
    # True glass only — bright walls/furniture stay liftable (v3 mask was too wide)
    glass = ((luma_m >= 200.0) & (chroma_m < 38.0)) | (luma_m >= 235.0)
    glass_w = cv2.GaussianBlur(glass.astype(np.float32), (0, 0), sigmaX=1.6)

    lifted = a * gain
    # Luma-based soft shoulder (preserve chromaticity; less midtone crush)
    luma = (
        0.2126 * lifted[:, :, 0]
        + 0.7152 * lifted[:, :, 1]
        + 0.0722 * lifted[:, :, 2]
    )
    over = luma > float(shoulder_t)
    if over.any():
        t = float(shoulder_t)
        compressed_L = t + (1.0 - t) * (
            1.0 - np.exp(-(luma - t) / ((1.0 - t) * 1.4 + 1e-6))
        )
        scale = np.ones_like(luma)
        scale[over] = compressed_L[over] / (luma[over] + 1e-6)
        lifted = lifted * scale[:, :, None]

    gw = np.clip(glass_w, 0.0, 1.0)[:, :, None]
    out = lifted * (1.0 - gw) + a * gw
    logger.info(
        "photo_enhance: interior body lift cur=%.1f target=%.1f gain=%.3f glass=%.3f",
        cur, target, gain, float(glass_w.mean()),
    )
    return Image.fromarray(np.clip(out * 255.0, 0, 255).astype(np.uint8), "RGB")


def _sky_region_weight(rgb: np.ndarray) -> np.ndarray:
    """Soft weight for bright / highlight sky (incl. sunset family).

    Prefer mid plate here — never green-yellow reconstruct from fusion/−EV.
    """
    arr = rgb.astype(np.float32)
    luma = arr.mean(axis=2)
    chroma = arr.max(axis=2) - arr.min(axis=2)
    h = luma.shape[0]
    yy = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
    upper = np.clip(1.0 - yy * 1.05, 0.0, 1.0)

    highlight = np.clip((luma - 185.0) / 55.0, 0.0, 1.0)
    flat = np.clip((40.0 - chroma) / 40.0, 0.0, 1.0)
    # Sunset / dawn family: warm mid-chroma still sky (pink/peach/gold)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    warm = np.clip((r - b) / 40.0, 0.0, 1.0) * np.clip((r + g) / 2.0 / 180.0, 0.0, 1.0)
    sunsetish = np.clip((luma - 140.0) / 70.0, 0.0, 1.0) * warm * np.clip(
        (70.0 - np.abs(r - g)) / 70.0, 0.0, 1.0
    )
    cool_bright = highlight * flat
    w = np.maximum(cool_bright, sunsetish * 0.85) * np.clip(0.35 + 0.65 * upper, 0.0, 1.0)
    # Also catch mid-frame bright blowouts that are still open sky (cove sun)
    w = np.maximum(w, highlight * flat * 0.55)
    return np.clip(w, 0.0, 1.0).astype(np.float32)


def _mid_biased_body(
    mid: Image.Image,
    fused: Optional[Image.Image],
    *,
    fused_mix: float = 0.35,
) -> Image.Image:
    """Compose listing body from bright fuse; protect sky/highlights to mid.

    When fused body clearly beats mid (Enfuse / fixed Mertens), use fused as
    the room base — do NOT crush back toward mid. Sky / blown highlights stay
    mid-biased (Cove ACCEPT; never green-yellow mud).
    """
    if fused is None:
        return mid
    a = np.asarray(mid.convert("RGB"), dtype=np.float32)
    b = np.asarray(
        fused.convert("RGB").resize(mid.size, Image.Resampling.LANCZOS),
        dtype=np.float32,
    )
    mid_body = _body_luma_mean(mid)
    fused_body = _body_luma_mean(
        Image.fromarray(np.clip(b, 0, 255).astype(np.uint8), "RGB")
    )
    # Bright-even path: fused is the body (Photomatix Interior / AutoHDR bar)
    if fused_body >= mid_body * 0.98 and fused_body > 8.0:
        sky_w = _sky_region_weight(a)
        chroma = a.max(axis=2) - a.min(axis=2)
        luma = a.mean(axis=2)
        # Hard lock: bright low-chroma mid sky/cove stays mid (Cove ACCEPT)
        hard_sky = ((luma >= 200.0) & (chroma < 35.0)).astype(np.float32)
        highlight = np.clip((luma - 200.0) / 40.0, 0.0, 1.0)
        bright_flat = highlight * np.clip((28.0 - chroma) / 28.0, 0.0, 1.0)
        h = luma.shape[0]
        yy = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
        upper = np.clip(1.0 - yy * 1.35, 0.0, 1.0)
        upper_bright = upper * np.clip((luma - 160.0) / 60.0, 0.0, 1.0)
        protect = np.clip(
            np.maximum(
                np.maximum(np.maximum(sky_w, bright_flat), upper_bright),
                hard_sky,
            ),
            0.0,
            1.0,
        )[..., None]
        out = b * (1.0 - protect) + a * protect
        return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")

    # Legacy soft mix when fuse is weak/dark — shadows only
    luma = a.mean(axis=2)
    w = np.clip((50.0 - luma) / 50.0, 0.0, 1.0) * 0.45
    sky_w = _sky_region_weight(a)
    w = w * (1.0 - sky_w)
    chroma = a.max(axis=2) - a.min(axis=2)
    highlight = np.clip((luma - 200.0) / 40.0, 0.0, 1.0)
    bright_flat = highlight * np.clip((28.0 - chroma) / 28.0, 0.0, 1.0)
    w = w * (1.0 - bright_flat)
    w = (w * float(np.clip(fused_mix / 0.35, 0.4, 1.0)))[..., None]
    out = a * (1.0 - w) + b * w
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def _green_yellow_mud(rgb: np.ndarray) -> np.ndarray:
    """Dirtiness score: green-yellow reconstruct vs natural cool/sunset sky."""
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    # Yellow-green: G high vs B, and G competitive with R
    return (
        np.clip((g - b) / 40.0, 0.0, 1.0)
        * np.clip((g + 10.0 - r) / 35.0, 0.0, 1.0)
    ).astype(np.float32)


def _sky_mid_bias_restore(
    working: Image.Image,
    mid: Image.Image,
    under: Optional[Image.Image] = None,
) -> Image.Image:
    """Force bright/highlight sky back toward mid (or under if mid blown).

    Fail criterion (Frame): desk sky must not be dirtier/weirder than mid.
    Never green-yellow reconstruct. Sunset family preserved from mid plate.
    Does not undo intentional −EV glass foliage (interior bays).
    """
    a = np.asarray(working.convert("RGB"), dtype=np.float32)
    m = np.asarray(
        mid.convert("RGB").resize(working.size, Image.Resampling.LANCZOS),
        dtype=np.float32,
    )
    sky_w = _sky_region_weight(m)
    h = sky_w.shape[0]
    yy = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
    upper = np.clip(1.0 - yy * 1.25, 0.0, 1.0)
    # Bias restore to upper sky; keep lower/mid-frame glass pulls intact
    sky_w = sky_w * np.clip(0.25 + 0.75 * upper, 0.0, 1.0)
    if float(sky_w.max()) < 0.12:
        return working

    # Only restore where working is dirtier/weirder OR strongly diverged from mid
    mud_a = _green_yellow_mud(a)
    mud_m = _green_yellow_mud(m)
    dirtier = np.clip((mud_a - mud_m) + 0.05, 0.0, 1.0)
    diverged = np.clip(np.abs(a - m).mean(axis=2) / 45.0, 0.0, 1.0)
    need = np.clip(np.maximum(dirtier * 1.5, diverged * upper), 0.0, 1.0)

    # Interior glass guard: under foliage chroma under blown mid → leave pull
    if under is not None:
        u = np.asarray(
            under.convert("RGB").resize(working.size, Image.Resampling.LANCZOS),
            dtype=np.float32,
        )
        chroma_u = u.max(axis=2) - u.min(axis=2)
        foliage = np.clip((chroma_u - 25.0) / 40.0, 0.0, 1.0) * np.clip(
            (u[:, :, 1] - u[:, :, 2]) / 30.0, 0.0, 1.0
        )
        mid_blown = np.clip((m.mean(axis=2) - 230.0) / 20.0, 0.0, 1.0)
        glass_pull = foliage * mid_blown * (1.0 - upper * 0.7)
        need = need * (1.0 - glass_pull)
    else:
        u = None

    # When working is clearly muddier in a sky-ish region, near-full mid copy
    # (sky_w alone caps ~0.6 — must not leave green-yellow linger)
    muddy_sky = (sky_w > 0.12) & (dirtier > 0.15)
    hard = np.where(
        muddy_sky,
        np.clip(0.92 + 0.08 * dirtier, 0.0, 1.0),
        sky_w * np.clip(dirtier * 2.2, 0.0, 1.0),
    ).astype(np.float32)
    w = np.maximum(sky_w * need, hard).astype(np.float32)
    w = np.clip(w, 0.0, 1.0)
    if float(w.max()) < 0.08:
        return working

    src = m.copy()
    # Where mid is fully blown, soft-fall to under ONLY if under is not muddier
    if u is not None:
        mid_luma = m.mean(axis=2)
        blown = np.clip((mid_luma - 245.0) / 10.0, 0.0, 1.0)
        mid_chroma = m.max(axis=2) - m.min(axis=2)
        under_g_bias = np.clip((u[:, :, 1] - u[:, :, 2]) / 35.0, 0.0, 1.0)
        mid_g_bias = np.clip((m[:, :, 1] - m[:, :, 2]) / 35.0, 0.0, 1.0)
        mud = np.clip(under_g_bias - mid_g_bias, 0.0, 1.0)
        use_under = blown * (1.0 - mud) * np.clip((25.0 - mid_chroma) / 25.0, 0.0, 1.0)
        src = m * (1.0 - use_under[..., None]) + u * use_under[..., None]

    ww = w[..., None]
    out = a * (1.0 - ww) + src * ww
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def _frame_edge_lock_mask(mid_rgb: np.ndarray) -> np.ndarray:
    """1 where mullion / frame / pillar edges must stay mid (exclude −EV).

    Strong near-vertical and near-horizontal structure on the mid plate —
    white bay frames, curtain folds at edges, wall pillars. Slightly stronger
    dilate than v5 so soft matte feather cannot dirty mid-white frames.
    """
    import cv2

    gray = cv2.cvtColor(
        np.clip(mid_rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY
    )
    # Soft denoise so glass glare noise does not look like frames
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(gray, 55, 130)
    h, w = edges.shape
    kx = max(11, (w // 70) | 1)
    ky = max(11, (h // 70) | 1)
    horiz = cv2.morphologyEx(
        edges, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (kx, 1))
    )
    vert = cv2.morphologyEx(
        edges, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, ky))
    )
    struct = cv2.bitwise_or(horiz, vert)
    # Dilate so feather never paints onto the frame itself (v6: +1px vs v5)
    rad = max(3, min(h, w) // 300)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (rad * 2 + 1, rad * 2 + 1))
    struct = cv2.dilate(struct, k, iterations=2)
    return struct.astype(np.float32) / 255.0


def _interior_same_chromaticity(
    mid_rgb: np.ndarray, under_rgb: np.ndarray
) -> np.ndarray:
    """1 where under is just a darker mid (wall/curtain/furniture), not exterior.

    Blown glass mid is near-neutral; −EV foliage shifts chromaticity. Painted
    walls keep the same beige ratios when darkened.
    """
    mid = mid_rgb.astype(np.float32)
    under = under_rgb.astype(np.float32)
    lm = mid.mean(axis=2, keepdims=True) + 1e-3
    lu = under.mean(axis=2, keepdims=True) + 1e-3
    mid_n = mid / lm
    und_n = under / lu
    shift = np.abs(mid_n - und_n).mean(axis=2)
    # Low shift → same paint; scale so ~0.04–0.10 is the wall band
    return np.clip((0.09 - shift) / 0.09, 0.0, 1.0)


def _glass_only_matte(
    mid_rgb: np.ndarray,
    under_rgb: np.ndarray,
) -> np.ndarray:
    """Strict per-pane glass confidence matte (Frame v8 P0 −EV preference).

    −EV only inside blown glass panes. Morphologically protect mullions /
    transoms first (frames locked white before curtain/plant protect).
    Prefer readable −EV exterior when confidence is good. Leave-mid /
    blown only for true low-conf mottled risk — never default for whole
    pane banks. Hard-fail: any interior wall getting −EV paint (wall ghost).
    """
    import cv2

    mid = mid_rgb.astype(np.float32)
    under = under_rgb.astype(np.float32)
    luma_m = mid.mean(axis=2)
    luma_u = under.mean(axis=2)
    chroma_m = mid.max(axis=2) - mid.min(axis=2)
    chroma_u = under.max(axis=2) - under.min(axis=2)
    h, w = luma_m.shape

    # --- Hard glass brightness: walls/furniture sit well below this ---------
    # Absolute floor 200 kills wall ghost; seed ~205 catches recoverable panes.
    GLASS_FLOOR = 200.0
    GLASS_SEED = 200.0
    hi = np.clip((luma_m - 198.0) / 36.0, 0.0, 1.0)
    flat = np.clip((42.0 - chroma_m) / 42.0, 0.0, 1.0)  # blown glass is flat

    # Wall / curtain / furniture reject on mid plate
    wallish = np.clip((chroma_m - 14.0) / 30.0, 0.0, 1.0) * np.clip(
        (GLASS_FLOOR - luma_m) / 40.0 + 0.35, 0.0, 1.0
    )
    # Anything below glass floor is never a candidate (hard)
    below_floor = (luma_m < GLASS_FLOOR).astype(np.float32)

    # Under must be useful −EV (not still blown white, not empty black)
    # Allow brighter −EV when foliage chroma/structure is present (left bank).
    under_ok = np.clip((luma_u - 6.0) / 24.0, 0.0, 1.0) * np.clip(
        (230.0 - luma_u) / 55.0, 0.0, 1.0
    )
    # Still-blown −EV with no chroma → leave mid pane (no invent)
    still_blown = np.clip((luma_u - 225.0) / 18.0, 0.0, 1.0) * np.clip(
        (14.0 - chroma_u) / 14.0, 0.0, 1.0
    )
    delta = np.clip((luma_m - luma_u) / 40.0, 0.0, 1.0)

    under_u8 = np.clip(under, 0, 255).astype(np.uint8)
    under_gray = cv2.cvtColor(under_u8, cv2.COLOR_RGB2GRAY)
    mid_gray = cv2.cvtColor(np.clip(mid, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    under_edges = cv2.Canny(under_gray, 28, 85).astype(np.float32) / 255.0
    mid_edges = cv2.Canny(mid_gray, 40, 120).astype(np.float32) / 255.0
    # Novel under structure: branch/foliage edges absent on blown mid.
    # Bright walls share paneling edges mid↔under → novel ~0 (kills wall ghost).
    mid_edge_d = cv2.dilate(mid_edges, np.ones((5, 5), np.uint8))
    novel_under = under_edges * (1.0 - mid_edge_d)
    novel_under = cv2.GaussianBlur(novel_under, (0, 0), sigmaX=1.2)
    blur = cv2.GaussianBlur(under_gray.astype(np.float32), (0, 0), sigmaX=2.0)
    local_var = (
        cv2.GaussianBlur(under_gray.astype(np.float32) ** 2, (0, 0), sigmaX=2.0)
        - blur ** 2
    )
    var_n = np.clip(local_var / 160.0, 0.0, 1.0)
    under_detail = np.clip(chroma_u / 28.0, 0.0, 1.0)
    under_struct = np.clip(
        0.45 * under_detail
        + 0.25 * under_edges
        + 0.35 * var_n
        + 0.70 * novel_under,
        0.0,
        1.0,
    )
    under_struct = cv2.GaussianBlur(under_struct, (0, 0), sigmaX=1.0)
    under_struct = np.where(under_struct >= 0.10, under_struct, 0.0)

    # Interior chromaticity: wall/curtain under is darker paint, not foliage
    interior = _interior_same_chromaticity(mid, under)
    # Warm beige/grey wall under (R>B, R>=G) — classic wall-ghost cue vs exterior
    warm_under = np.clip((under[:, :, 0] - under[:, :, 2]) / 25.0, 0.0, 1.0) * np.clip(
        (under[:, :, 0] - under[:, :, 1]) / 20.0 + 0.35, 0.0, 1.0
    )

    seed = hi * flat * under_ok * np.clip(0.15 + 0.90 * under_struct, 0.0, 1.0)
    seed = seed * np.clip(0.15 + delta, 0.0, 1.0)
    seed = seed * (1.0 - wallish) * (1.0 - below_floor) * (1.0 - still_blown)
    # Interior reject only on paint-like mid (chroma). Blown flat glass mid
    # shares no paint ratios with foliage — do not kill those panes.
    paint_mid = np.clip((chroma_m - 10.0) / 25.0, 0.0, 1.0) * np.clip(
        (235.0 - luma_m) / 35.0, 0.0, 1.0
    )
    seed = seed * (1.0 - np.clip(interior * paint_mid, 0.0, 1.0))
    # Kill bright white wall panels (pass glass floor but warm under)
    seed = seed * (1.0 - np.clip(warm_under * 1.20, 0.0, 1.0))

    # Exterior open-sky suppress ONLY (upper + under looks like sky).
    sky_w = _sky_region_weight(mid)
    yy = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
    upper = np.clip(1.0 - yy * 1.2, 0.0, 1.0)
    under_skyish = (
        np.clip((luma_u - 50.0) / 90.0, 0.0, 1.0)
        * np.clip((30.0 - chroma_u) / 30.0, 0.0, 1.0)
    )
    seed = seed * (
        1.0 - sky_w * upper * np.clip(0.35 + 0.65 * under_skyish, 0.0, 1.0)
    )

    # Frame / mullion / pillar edge lock (keep mid-white; don't wipe panes)
    frame = _frame_edge_lock_mask(mid)
    seed = seed * (1.0 - np.clip(frame * 1.15, 0.0, 1.0))
    seed = cv2.GaussianBlur(seed.astype(np.float32), (0, 0), sigmaX=0.7)

    # v8: prefer −EV when conf good; leave-mid only for true mottled low-conf
    conf_thresh = 0.18
    binary = (seed >= conf_thresh).astype(np.uint8) * 255
    # Light open: kill single-pixel wall speckles without erasing pane seeds
    k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, k_open, iterations=1)
    # Light close to rejoin pane speckles — keep small so mullions stay split
    k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, k_close, iterations=1)
    # Split at frames after morph (v7: tighter split so transoms stay mid)
    binary = np.where(frame > 0.25, 0, binary).astype(np.uint8)
    # Absolute glass floor
    binary = np.where(luma_m < GLASS_FLOOR, 0, binary).astype(np.uint8)

    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    min_area = max(55, int(0.00018 * h * w))
    keep = np.zeros_like(seed, dtype=np.float32)
    accepted = []  # (comp_mask, mean_conf, mean_lu, mean_lm, cx, cy)
    for i in range(1, n_labels):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        comp = labels == i
        mean_conf = float(seed[comp].mean()) if comp.any() else 0.0
        # Per-pane consistency: weak / patchy panes leave mid entirely
        if mean_conf < conf_thresh * 1.00:
            continue
        bw = int(stats[i, cv2.CC_STAT_WIDTH])
        bh = int(stats[i, cv2.CC_STAT_HEIGHT])
        # Reject huge full-wall / curtain slabs
        if bw > 0.65 * w and bh > 0.40 * h:
            continue
        # Reject tall thin pillars / curtain drops / mullion strips
        if bh > 0.45 * h and bw < 0.10 * w:
            continue
        if bw <= max(6, int(0.012 * w)) and bh > 2.5 * max(bw, 1):
            continue
        # Reject wide thin ledge bleeds
        if bw > 0.50 * w and bh < 0.08 * h:
            continue
        # Component must be bright glass on mid (hard wall ghost kill)
        mean_lm = float(luma_m[comp].mean())
        if mean_lm < GLASS_FLOOR + 2.0:
            continue
        # Under must actually help (skip still-blown / useless −EV)
        mean_lu = float(luma_u[comp].mean())
        mean_cu = float(chroma_u[comp].mean())
        mean_cm = float(chroma_m[comp].mean())
        if mean_lu > 230.0 and mean_cu < 8.0:
            continue  # low-conf leave mid
        if (mean_lm - mean_lu) < 15.0 and mean_cu < 10.0:
            continue
        # Textured bright wall / art (not flat blown glass)
        if mean_cm > 14.0:
            continue
        # Warm under majority → wall/curtain ghost
        if float(warm_under[comp].mean()) > 0.28:
            continue
        # Interior paint majority on non-flat mid → wall ghost
        if mean_cm > 12.0 and float(interior[comp].mean()) > 0.60:
            continue
        # Require novel under edges OR strong exterior chroma on flat glass
        mean_novel = float(novel_under[comp].mean())
        exterior_chroma = mean_cu > 18.0 and mean_cm < 12.0 and float(warm_under[comp].mean()) < 0.25
        if mean_novel < 0.010 and not exterior_chroma:
            continue
        # v8: leave-mid only for true mottled / sparse seed — never default
        # whole banks. Solid high-conf dark foliage still pulls.
        delta_lu = mean_lm - mean_lu
        cov_seed = float((seed[comp] >= conf_thresh).mean())
        if cov_seed < 0.38 and mean_conf < 0.26:
            continue  # truly sparse mottled → leave mid
        if delta_lu > 130.0 and cov_seed < 0.50 and mean_conf < 0.26:
            continue
        if mean_lu < 28.0 and cov_seed < 0.48 and mean_conf < 0.26:
            continue
        cx = int(stats[i, cv2.CC_STAT_LEFT] + bw / 2)
        cy = int(stats[i, cv2.CC_STAT_TOP] + bh / 2)
        accepted.append((comp, mean_conf, mean_lu, mean_lm, cx, cy, cov_seed))

    # Uneven pane family (v8): drop only clearly mottled weak siblings —
    # never force solid high-conf panes to leave-mid for "consistency".
    if len(accepted) >= 2:
        confs = np.array([a[1] for a in accepted], dtype=np.float32)
        med = float(np.median(confs))
        kept_acc = []
        for a in accepted:
            comp, mean_conf, mean_lu, mean_lm, cx, cy, cov_seed = a
            # Only kill obviously mottled weak vs strong family median
            if mean_conf < med * 0.45 and mean_conf < 0.22 and cov_seed < 0.42:
                continue
            kept_acc.append(a)
        accepted = kept_acc

    for a in accepted:
        comp = a[0]
        keep[comp] = seed[comp]

    # --- Frames locked white FIRST (v7 order) --------------------------------
    # Stronger erode of matte off mullions/transoms before any plant/curtain
    # protect, so white frames never inherit soft −EV fringe.
    if float(keep.max()) > 0:
        pane_bin = (keep > 0.16).astype(np.uint8)
        k_er = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        eroded = cv2.erode(pane_bin, k_er, iterations=2)
        # Fallback cascade if panes would vanish
        if int(eroded.sum()) < max(20, int(0.18 * int(pane_bin.sum()))):
            k_er = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            eroded = cv2.erode(pane_bin, k_er, iterations=2)
        if int(eroded.sum()) < max(16, int(0.15 * int(pane_bin.sum()))):
            k_er = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            eroded = cv2.erode(pane_bin, k_er, iterations=1)
        if int(eroded.sum()) >= max(12, int(0.14 * int(pane_bin.sum()))):
            keep = np.where(eroded > 0, keep, 0.0).astype(np.float32)
        # Hard-zero residual on frame lock after erode (stronger than v6)
        keep = keep * (1.0 - np.clip(frame * 1.35, 0.0, 1.0))
        # Mid-white mullion / transom body → never −EV
        white_bar = (luma_m >= 218.0) & (chroma_m < 20.0) & (frame > 0.18)
        keep = np.where(white_bar, 0.0, keep).astype(np.float32)

    # Protect curtains / plants ONLY AFTER frames are locked white
    if float(keep.max()) > 0:
        # Curtain: warm mid chroma, not blown flat glass
        curtain_mid = (
            (chroma_m > 22.0)
            & (luma_m < 210.0)
            & (mid[:, :, 0] > mid[:, :, 2] + 8.0)
        )
        # Plant: green-dominant mid (interior foliage in front of glass)
        plant_mid = (
            (mid[:, :, 1] > mid[:, :, 0] + 6.0)
            & (mid[:, :, 1] > mid[:, :, 2] + 4.0)
            & (chroma_m > 18.0)
            & (luma_m < 200.0)
        )
        keep = np.where(curtain_mid | plant_mid, 0.0, keep).astype(np.float32)

    # NO soft dilate expand into walls (v4 wall-ghost path). Optional 1px
    # refill only into already-bright glass neighbors — never onto frames.
    if float(keep.max()) > 0:
        expand = cv2.dilate(
            (keep > 0.20).astype(np.uint8),
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
            iterations=1,
        )
        gate = (
            (luma_m >= GLASS_SEED)
            & (luma_u < 200.0)
            & ((luma_m - luma_u) > 30.0)
            & (frame < 0.12)
            & (wallish < 0.25)
            & (interior < 0.40)
            & (below_floor < 0.5)
            & (chroma_m < 16.0)
        )
        keep = np.where(
            (expand > 0) & gate & (keep < 0.20),
            np.maximum(keep, seed * 0.80),
            keep,
        ).astype(np.float32)

    # Soft bay blend — tighter sigma so feather stays off mullions
    matte = cv2.GaussianBlur(keep, (0, 0), sigmaX=0.70)
    matte = matte * (1.0 - np.clip(frame * 1.30, 0.0, 1.0))
    matte = matte * (1.0 - below_floor)
    # Absolute hard fails: wall ghost / furniture / dim interior
    matte = np.where(luma_m < GLASS_FLOOR, 0.0, matte)
    matte = np.where(warm_under > 0.38, 0.0, matte)
    matte = np.where((chroma_m > 20.0) & (luma_m < 235.0), 0.0, matte)
    # Final wall-ghost kill: no novel exterior structure AND no foliage chroma
    no_exterior = (novel_under < 0.008) & (chroma_u < 16.0)
    matte = np.where(no_exterior, 0.0, matte)
    # Only kill high-interior when mid looks painted (not blown glass)
    matte = np.where((interior > 0.75) & (chroma_m > 18.0) & (luma_m < 230.0), 0.0, matte)
    # Mid-white frame body with weak exterior cues → never dirty (edge-lock)
    white_frame_body = (
        (luma_m >= 218.0)
        & (chroma_m < 20.0)
        & (novel_under < 0.014)
        & (chroma_u < 14.0)
        & ((interior > 0.35) | (frame > 0.15))
    )
    matte = np.where(white_frame_body, 0.0, matte)
    # Low-confidence → leave mid pane (never grey fill / half-dirty)
    matte = np.where(matte >= 0.20, matte, 0.0).astype(np.float32)

    # v8: leave-mid only for true mottled residue — erode shrinks good panes
    # ~10–30%; do NOT wipe recoverable −EV banks that still have solid fill.
    if float(matte.max()) > 0:
        blown = (
            (luma_m >= 228.0)
            & (chroma_m < 22.0)
            & (frame < 0.40)
        ).astype(np.uint8) * 255
        n_b, lab_b, st_b, _ = cv2.connectedComponentsWithStats(blown, connectivity=8)
        pane_min = max(40, int(0.00012 * h * w))
        for i in range(1, n_b):
            area = int(st_b[i, cv2.CC_STAT_AREA])
            if area < pane_min:
                continue
            comp = lab_b == i
            cov = float((matte[comp] >= 0.20).mean())
            mean_m = float(matte[comp].mean())
            mean_lu = float(luma_u[comp].mean())
            mean_lm = float(luma_m[comp].mean())
            # Truly sparse / half-dirty speckles only
            if 0.0 < cov < 0.18:
                matte[comp] = 0.0
                continue
            if (mean_lm - mean_lu) > 145.0 and cov < 0.28 and mean_m < 0.16:
                matte[comp] = 0.0
                continue
            if 0.0 < mean_m < 0.06:
                matte[comp] = 0.0

    return np.clip(matte, 0.0, 1.0)


def _highlight_cap_vs_mid(
    working: Image.Image,
    mid: Image.Image,
    under: Optional[Image.Image] = None,
) -> Image.Image:
    """Cap desk glass/sky highlights vs mid (Frame v8 P0).

    Desk must not clip harder than mid when mid still has exterior detail
    (lawn/trees). Preserve mid exterior detail family. When mid is already
    blown, equal clip is OK. Does not invent sky/dusk. Body beat-mid outside
    glass/sky regions is preserved.
    """
    import cv2

    a = np.asarray(working.convert("RGB"), dtype=np.float32)
    m = np.asarray(
        mid.convert("RGB").resize(working.size, Image.Resampling.LANCZOS),
        dtype=np.float32,
    )
    luma_a = a.mean(axis=2)
    luma_m = m.mean(axis=2)
    chroma_m = m.max(axis=2) - m.min(axis=2)
    chroma_a = a.max(axis=2) - a.min(axis=2)

    glassish = ((luma_m >= 175.0) & (chroma_m < 40.0)) | (
        (luma_a >= 210.0) & (chroma_a < 30.0) & (luma_m >= 155.0)
    )
    sky_w = _sky_region_weight(m)
    region = np.clip(
        np.maximum(glassish.astype(np.float32), sky_w * 0.90), 0.0, 1.0
    )
    region = cv2.GaussianBlur(region, (0, 0), sigmaX=1.2)
    region = np.clip(region, 0.0, 1.0)

    # Mid still has exterior detail family (not pure blown)
    mid_detail = np.clip((252.0 - luma_m) / 30.0, 0.0, 1.0)
    mid_detail = mid_detail * np.clip(chroma_m / 18.0 + 0.25, 0.0, 1.0)
    u = None
    if under is not None:
        u = np.asarray(
            under.convert("RGB").resize(working.size, Image.Resampling.LANCZOS),
            dtype=np.float32,
        )
        chroma_u = u.max(axis=2) - u.min(axis=2)
        mid_detail = np.maximum(
            mid_detail,
            np.clip((chroma_u - 6.0) / 26.0, 0.0, 1.0)
            * np.clip((luma_m - 160.0) / 55.0, 0.0, 1.0),
        )

    # Prefer mid (or useful −EV) wherever desk is brighter in glass/sky
    over = np.clip((luma_a - luma_m) / 10.0, 0.0, 1.0)
    need = region * over * np.clip(0.40 + 0.60 * mid_detail, 0.0, 1.0)
    near = cv2.dilate(
        (region > 0.20).astype(np.uint8),
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)),
        iterations=1,
    ).astype(np.float32)
    bloom = near * np.clip((luma_a - luma_m - 2.0) / 16.0, 0.0, 1.0) * 0.75
    need = np.maximum(need, bloom * np.clip(region + 0.30, 0.0, 1.0))
    need = cv2.GaussianBlur(need.astype(np.float32), (0, 0), sigmaX=0.8)
    need = np.clip(need, 0.0, 1.0)
    if float(need.max()) < 0.05:
        return working

    src = m.copy()
    if u is not None:
        mid_blown = np.clip((luma_m - 228.0) / 20.0, 0.0, 1.0)
        u_luma = u.mean(axis=2)
        u_chroma = u.max(axis=2) - u.min(axis=2)
        useful_u = (
            np.clip((230.0 - u_luma) / 100.0, 0.0, 1.0)
            * np.clip((u_chroma - 5.0) / 20.0, 0.0, 1.0)
        )
        use_u = mid_blown * useful_u * region
        src = m * (1.0 - use_u[..., None]) + u * use_u[..., None]

    ww = np.clip(need * 1.25, 0.0, 1.0)[..., None]
    out = a * (1.0 - ww) + src * ww

    # Absolute per-pixel ceiling in glass/sky: desk ≤ mid (+tiny slack)
    # when mid still has exterior detail — never clip harder than mid.
    out_l = out.mean(axis=2)
    slack = 3.0 + 10.0 * (1.0 - np.clip(mid_detail, 0.0, 1.0))
    cap = luma_m + slack
    # Where mid is already blown (no detail), allow desk == mid (no extra blow)
    blown_mid = (luma_m >= 248.0) & (chroma_m < 12.0)
    cap = np.where(blown_mid, np.maximum(cap, luma_m), cap)
    enforce = np.clip(region * np.maximum(mid_detail, 0.35), 0.0, 1.0)
    # When desk over mid in region, hard-min toward mid plate
    ratio = np.where(out_l > cap, cap / np.maximum(out_l, 1e-3), 1.0)
    ratio = np.clip(ratio, 0.70, 1.0)
    # Also lerp toward mid RGB where enforced (preserves exterior chroma)
    mix = enforce * (1.0 - ratio) * 0.85 + enforce * over * 0.55
    mix = np.clip(mix, 0.0, 1.0)[..., None]
    out = out * (1.0 - mix) + m * mix
    # Final luma clamp soft-scale inside region
    out_l2 = out.mean(axis=2)
    ratio2 = np.where(out_l2 > cap, cap / np.maximum(out_l2, 1e-3), 1.0)
    ratio2 = np.clip(ratio2, 0.72, 1.0)
    ratio2 = 1.0 - enforce * (1.0 - ratio2)
    out = out * ratio2[..., None]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")




def _neutralize_magenta_highlights(img: Image.Image) -> Image.Image:
    """Kill pink/magenta clipped-channel sky before window pull (desk P0).

    Upstream CR3→JPEG (libraw blend, camera JPEG, vendor export) often leaves
    magenta in blown sky/glass. Force those pixels to neutral grey/white —
    never invent blue sky or dusk. No-ops when OpenCV missing.
    """
    try:
        import cv2
    except ImportError:
        return img

    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    luma = a.mean(axis=2)
    mag = np.clip((r + b) / 2.0 - g, 0.0, None)
    clipish = (mx >= 228.0) & (mag >= 4.0)
    bright_pink = (luma >= 180.0) & (mag >= 6.0) & ((mx - mn) < 55.0)
    chan_pink = (
        (r >= 218.0) & (b >= 198.0) & (g < r - 5.0) & (g < b - 3.0) & (luma >= 155.0)
    )
    mid_pink = (luma >= 135.0) & (mag >= 14.0) & (r > g + 8.0) & (b > g + 5.0)
    mask = (clipish | bright_pink | chan_pink | mid_pink).astype(np.float32)
    if float(mask.max()) < 0.05:
        return img
    mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=1.3)
    mask = np.clip(mask, 0.0, 1.0)[..., None]
    # Neutral grey at scene luma; hard-clip → white (readable −EV sky family)
    target_y = np.where(mx >= 248.0, 255.0, np.clip(luma + mag * 0.12, 0.0, 255.0))
    neut = np.stack([target_y, target_y, target_y], axis=-1)
    out = a * (1.0 - mask) + neut * mask
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def _body_beat_mid(
    working: Image.Image,
    mid: Image.Image,
    *,
    amount: float = 0.28,
) -> Image.Image:
    """Lift crushed interior body toward/above mid (Heroes-class readable).

    Glass/sky excluded. No milk/bloom on highlights. Does not invent sky.
    Cove/sky mid-bias is applied separately and stays locked.
    """
    try:
        import cv2
    except ImportError:
        return working

    a = np.asarray(working.convert("RGB"), dtype=np.float32)
    m = np.asarray(
        mid.convert("RGB").resize(working.size, Image.Resampling.LANCZOS),
        dtype=np.float32,
    )
    luma_a = a.mean(axis=2)
    luma_m = m.mean(axis=2)
    chroma_m = m.max(axis=2) - m.min(axis=2)
    # Glass/sky: leave for window pull / highlight cap / cove restore
    glass = (
        ((luma_m >= 185.0) & (chroma_m < 45.0))
        | (luma_a >= 220.0)
        | ((luma_m >= 210.0) & (chroma_m < 55.0))
    )
    body = (~glass) & (luma_a < 205.0)
    # Where desk lags mid (crushed sofa), lift toward mid * 1.04 (slight beat)
    lag = np.clip((luma_m + 10.0 - luma_a) / 45.0, 0.0, 1.0) * body.astype(np.float32)
    if float(lag.max()) < 0.04:
        return working
    lag = cv2.GaussianBlur(lag, (0, 0), sigmaX=2.0)
    # amount≈0.28 → up to ~0.85 mix on fully-lagging body (listing-readable, not milk)
    strength = float(np.clip(0.55 + 1.1 * float(amount), 0.55, 0.90))
    mix = np.clip(lag * strength, 0.0, strength)[..., None]
    target = np.clip(m * 1.04, 0.0, 248.0)
    out = a * (1.0 - mix) + target * mix
    # Tiny shared shadow toe so pure-black furniture isn't silhouette-only
    out_l = out.mean(axis=2)
    shadow = np.clip((42.0 - out_l) / 42.0, 0.0, 1.0) * body.astype(np.float32)
    shadow = cv2.GaussianBlur(shadow, (0, 0), sigmaX=1.5)
    out = np.clip(out + shadow[..., None] * 8.0, 0.0, 255.0)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def window_pull_from_brackets(
    mid: Image.Image,
    under: Optional[Image.Image],
    over: Optional[Image.Image],
    *,
    reference_mid: Optional[Image.Image] = None,
) -> Image.Image:
    """−EV on glass only; muntins/curtains/walls stay mid. Low matte → mid pane.

    Matte is always computed on the true mid plate (reference_mid) so fusion
    body glow cannot expand −EV onto walls. Pixels written onto `mid` canvas.
    """
    if under is None and over is None:
        return mid
    canvas = np.asarray(mid.convert("RGB"), dtype=np.float32)
    ref_img = reference_mid if reference_mid is not None else mid
    ref = np.asarray(
        ref_img.convert("RGB").resize(mid.size, Image.Resampling.LANCZOS),
        dtype=np.float32,
    )
    out = canvas.copy()
    luma = ref.mean(axis=2)

    if under is not None:
        u_img = under.convert("RGB").resize(mid.size, Image.Resampling.LANCZOS)
        u = np.asarray(u_img, dtype=np.float32)
        matte = _glass_only_matte(ref, u)
        # Absolute wall-ghost kill: never −EV-paint non-blown interior
        ref_luma = ref.mean(axis=2)
        ref_chroma = ref.max(axis=2) - ref.min(axis=2)
        matte = np.where(ref_luma < 200.0, 0.0, matte).astype(np.float32)
        # Edge-lock restore: mid-white mullions/pillars stay mid (not −EV dirty).
        # Only kill when frame-locked OR under looks like same interior paint
        # (not low-chroma exterior foliage/branches).
        frame_lock = _frame_edge_lock_mask(ref)
        interior = _interior_same_chromaticity(ref, u)
        u_chroma = u.max(axis=2) - u.min(axis=2)
        u_luma = u.mean(axis=2)
        paint_under = (
            (u_chroma < 16.0)
            & (u_luma > 40.0)
            & (u_luma < 160.0)
            & (interior > 0.55)
        )
        # v7: stronger mid-white mullion/transom restore after matte
        white_mullion = (
            (ref_luma >= 215.0)
            & (ref_chroma < 22.0)
            & ((frame_lock > 0.18) | paint_under)
        )
        matte = np.where(white_mullion, 0.0, matte).astype(np.float32)
        # Curtains/plants after frames locked: never −EV onto green/warm mid bodies
        plant_or_curtain = (
            (
                (ref[:, :, 1] > ref[:, :, 0] + 6.0)
                & (ref[:, :, 1] > ref[:, :, 2] + 4.0)
                & (ref_chroma > 18.0)
                & (ref_luma < 200.0)
            )
            | (
                (ref_chroma > 22.0)
                & (ref_luma < 210.0)
                & (ref[:, :, 0] > ref[:, :, 2] + 8.0)
            )
        )
        matte = np.where(plant_or_curtain, 0.0, matte).astype(np.float32)
        matte = matte[..., None]
        # Where matte is 0, canvas unchanged — never invent grey fill / wall paint
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
    # When any frame carries an EV name/exif bias, treat missing bias as 0.0
    # (base plate). Never mix EV stops with raw luminance — that mis-ranks
    # e.g. Lighthouse.jpg (None→~144) above Lighthouse_over (+2) so over
    # becomes "mid" and cove sky mid-bias regresses.
    known_ev = any(b is not None for b in biases)
    scored = []
    for im, bias in zip(images, biases):
        if bias is not None:
            scored.append((float(bias), im))
        elif known_ev:
            scored.append((0.0, im))
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


def straighten_verticals(img: Image.Image, max_degrees: float = 1.5) -> Image.Image:
    """Mild rotate to level dominant near-verticals.

    Vertical-only mild correction. Skips when signal is weak OR when the
    proposed rotate would bend strong horizontals (ceiling / cove lines /
    mullion tops) — that bow is worse than a slight lean.
    """
    try:
        import cv2
    except ImportError:
        return img

    arr = np.asarray(img.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 60, 160, apertureSize=3)
    min_len = max(24, min(arr.shape[:2]) // 5)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=90,
        minLineLength=min_len, maxLineGap=16,
    )
    if lines is None:
        return img

    vert_devs = []
    horiz_devs = []
    for line in lines:
        # OpenCV may return shape (N,1,4) or (N,4) depending on version.
        pts = line[0] if getattr(line, 'shape', ()) == (1, 4) or (hasattr(line, '__len__') and len(line) == 1) else line
        x1, y1, x2, y2 = [int(v) for v in pts]
        dx, dy = (x2 - x1), (y2 - y1)
        if dx == 0 and dy == 0:
            continue
        length = float(np.hypot(dx, dy))
        ang = np.degrees(np.arctan2(dy, dx))  # [-180, 180], 0 = horizontal
        # Near-vertical: angle near ±90
        v_dev = ang - 90.0 if ang > 0 else ang + 90.0
        if abs(v_dev) <= max_degrees * 1.5:
            vert_devs.append((v_dev, length))
        # Near-horizontal: |ang| small or near ±180
        h_ang = ang
        if h_ang > 90:
            h_ang -= 180
        elif h_ang < -90:
            h_ang += 180
        if abs(h_ang) <= max_degrees * 1.5:
            horiz_devs.append((h_ang, length))

    if len(vert_devs) < 6:
        return img
    # Length-weighted median of vertical deviations
    vert_devs.sort(key=lambda t: t[0])
    total_w = sum(w for _, w in vert_devs)
    acc = 0.0
    median = vert_devs[len(vert_devs) // 2][0]
    for ang, w in vert_devs:
        acc += w
        if acc >= total_w / 2.0:
            median = float(ang)
            break
    if abs(median) < 0.35 or abs(median) > max_degrees:
        return img

    # Risk gate: strong horizontals already near-level — rotating would bow them
    if horiz_devs:
        h_w = sum(w for _, w in horiz_devs)
        v_w = sum(w for _, w in vert_devs)
        h_med = float(np.median([a for a, _ in horiz_devs]))
        # If horizontals are flatter than the proposed vertical fix, skip —
        # prefer leaving mild mullion lean over bowed ceiling/cove.
        if h_w >= 0.6 * v_w and abs(h_med) < abs(median) * 0.75:
            logger.info(
                "photo_enhance: straighten skipped (horiz risk) vert=%.2f horiz=%.2f hw/vw=%.2f",
                median, h_med, h_w / max(v_w, 1e-6),
            )
            return img
        # Also skip if horizontals themselves are already well-leveled but
        # rotating by |median| would push them past ~0.6°
        if abs(h_med - median) > 0.6 and abs(h_med) < 0.35:
            logger.info(
                "photo_enhance: straighten skipped (would bow horizontals) vert=%.2f horiz=%.2f",
                median, h_med,
            )
            return img

    rotated = img.rotate(-median, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(0, 0, 0))
    return _crop_black_wedges(rotated)


def _bright_ceiling_panel_mask(l_ch: np.ndarray) -> np.ndarray:
    """Mask bright / near-white lit ceiling + cove / LED panels.

    CLAHE on these regions fizzes sparkle/fuzz (Kam Fuze reject). Prefer
    leaving them alone over local-contrast crunch.
    """
    h, w = l_ch.shape[:2]
    # Upper 45% favors ceiling / cove; allow full-frame for blown LED panels
    upper = np.zeros((h, w), dtype=bool)
    upper[: max(1, int(h * 0.45)), :] = True
    bright = l_ch.astype(np.float32)
    # Near-white lit panels (LAB L channel, 0–255) — gate early; CLAHE
    # sparkle starts well below clip white on warm cove LEDs.
    near_white = bright >= 188
    very_bright = bright >= 172
    # Soft upper-band gate: very bright in upper region, or near-white anywhere
    mask = (very_bright & upper) | near_white
    # Dilate slightly so CLAHE tile seams don't ring the panel edge
    try:
        import cv2
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        mask_u8 = (mask.astype(np.uint8) * 255)
        mask_u8 = cv2.dilate(mask_u8, kernel, iterations=1)
        return mask_u8 > 0
    except Exception:
        return mask


def _enhance_with_opencv(arr_bgr) -> np.ndarray:
    import cv2

    lab = cv2.cvtColor(arr_bgr, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    l_orig = l_ch.copy()
    # Frame P0: cap local contrast — old 1.6 + detailEnhance → muddy HDR crunch
    # Kam Fuze reject: even mild CLAHE sparkles lit cove/LED ceiling — gate it.
    bright_mask = _bright_ceiling_panel_mask(l_ch)
    bright_frac = float(bright_mask.mean()) if bright_mask.size else 0.0
    if bright_frac >= 0.28:
        # Substantial lit ceiling/panel coverage — skip CLAHE entirely
        logger.info("photo_enhance: CLAHE skipped (bright_frac=%.2f)", bright_frac)
    else:
        clip = 1.0 if bright_frac >= 0.08 else 1.10
        clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8))
        l_ch = clahe.apply(l_ch)
        if bright_mask.any():
            # Restore original L on bright ceiling / near-white panels
            l_ch = np.where(bright_mask, l_orig, l_ch)
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
        arr_pre = arr_rgb.copy()
        arr_bgr = cv2.cvtColor(arr_rgb, cv2.COLOR_RGB2BGR)
        arr_bgr = _enhance_with_opencv(arr_bgr)
        arr_rgb = cv2.cvtColor(arr_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
        arr_rgb = _wall_anchored_white_balance(arr_rgb)
        # Restore pre-tone RGB on bright ceiling / lit panels (kills CLAHE +
        # WB fizz; mid/HDR body already set those highlights).
        lab_pre = cv2.cvtColor(arr_pre, cv2.COLOR_RGB2LAB)
        bright_mask = _bright_ceiling_panel_mask(lab_pre[:, :, 0])
        if bright_mask.any():
            pre_f = arr_pre.astype(np.float32)
            m = bright_mask.astype(np.float32)[..., None]
            # Soft edge so restore doesn't seam
            m = cv2.GaussianBlur(m, (0, 0), sigmaX=2.0)
            if m.ndim == 2:
                m = m[..., None]
            arr_rgb = arr_rgb * (1.0 - m) + pre_f * m
        out = Image.fromarray(np.clip(arr_rgb, 0, 255).astype(np.uint8), "RGB")
        # Sharpen lightly, then put bright panels back (unsharp fizzes cove too)
        sharp = out.filter(ImageFilter.UnsharpMask(radius=0.7, percent=40, threshold=5))
        if bright_mask.any():
            sharp_a = np.array(sharp, dtype=np.float32)
            pre_f = arr_pre.astype(np.float32)
            m = bright_mask.astype(np.float32)[..., None]
            m = cv2.GaussianBlur(m, (0, 0), sigmaX=2.0)
            if m.ndim == 2:
                m = m[..., None]
            sharp_a = sharp_a * (1.0 - m) + pre_f * m
            return Image.fromarray(np.clip(sharp_a, 0, 255).astype(np.uint8), "RGB")
        return sharp
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
    return image_io.open_rgb(image_bytes)


def enhance_bracket(
    frames: Sequence[bytes],
    filenames: Optional[Sequence[str]] = None,
    *,
    apply_sky: bool = True,
    return_meta: bool = False,
):
    """Full MLS stack for a bracket set (or a single frame).

    When return_meta=True, returns (jpeg_bytes, meta_dict) including PropTx
    people soft-fail / MLS quarantine flags. Soft-fail never raises / blocks.
    """
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
    used_watermark_strip = False

    # Magenta/pink clipped highlights → neutral before merge/pull (CR3 JPEG P0)
    images = [_neutralize_magenta_highlights(im) for im in images]

    mid, under, over = _pick_under_mid_over(images, biases)
    body = mid

    if len(images) >= 2:
        fused = None
        fuse_backend = "none"
        try:
            fused, fuse_backend = merge_exposure_fusion(images, mid=mid)
            used_hdr = fuse_backend in ("enfuse", "mertens_u8")
        except Exception as e:
            logger.warning("photo_enhance: exposure fusion failed (%s) — mid body only", e)

        # Bright fuse as body when it beats mid; sky/highlight protect to mid
        body = _mid_biased_body(mid, fused, fused_mix=0.85)

        pulled = window_pull_from_brackets(body, under, over, reference_mid=mid)
        if pulled is not body:
            body = pulled
            used_window = under is not None or over is not None

        # Cove/exterior: bright sky must stay mid-biased (never dirtier than mid)
        # Cove ACCEPT regression bar stays locked — do not weaken this restore.
        body = _sky_mid_bias_restore(body, mid, under)
        # v8: glass/sky highlight cap vs mid (preserve exterior detail family)
        body = _highlight_cap_vs_mid(body, mid, under)
        # Kill residual pink in glass after pull/cap (no invent sky)
        body = _neutralize_magenta_highlights(body)
        # Mid+overs fuse is the RE body base; always finish toward Heroes-class
        # readable interiors (~152–155). Glass stays −EV (lift skips true glass).
        # Do not invent sky. Cove mid-bias restore above stays locked.
        mid_body = _body_luma_mean(mid)
        body = _body_beat_mid(body, mid, amount=0.35)
        body = _interior_body_lift(body, mid, target_body=155.0)

        # Soft black seat only — avoid crushing bright Enfuse interiors
        if _body_luma_mean(body) < mid_body * 1.05:
            body = restore_black_point(body)
        # Slight midtone punch for exteriors (Frame v2 P1); interiors stay gentler
        curve_amt = 0.14 if (under is not None and sky_region_likely(mid)) else 0.08
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
    # Tone/CLAHE can nudge body down — second Heroes lift if still short.
    # Glass protect + gain_cap keep milk/ceiling sparkle in check.
    if len(images) >= 2 and _body_luma_mean(working) < 148.0:
        working = _interior_body_lift(
            working, mid, target_body=155.0, gain_cap=1.35, shoulder_t=0.95
        )
    working = format_listing_ready(working)
    # Corner overlay: default detect → soft-fail; inpaint only if WATERMARK_STRIP=1
    working, used_watermark_strip, wm_reasons = _strip_corner_watermarks(working)
    out = _encode_jpeg(working)

    meta: Dict[str, Any] = {
        "mls_export_profile": MLS_EXPORT_PROFILE,
        "mls_brief": MLS_BRIEF_REF,
        "used_hdr": used_hdr,
        "used_window_pull": used_window,
        "used_sky": used_sky,
        "used_watermark_strip": used_watermark_strip,
        "export": listing_export_invariants(out),
    }
    # PropTx 5.01(d) soft-fail — never hard-fail / strand the enhance job
    try:
        from .mls_people_detect import people_soft_fail_metadata
        people_meta = people_soft_fail_metadata(out)
        meta.update(people_meta)
    except Exception as e:
        logger.warning("photo_enhance: people soft-fail meta skipped (%s)", e)
        meta.update({
            "qc_people_detected": False,
            "qc_face_count": 0,
            "mls_quarantine": False,
            "qc_soft_fail": False,
            "qc_soft_fail_reasons": [],
        })

    # Merge watermark detect soft-fail (does not quarantine MLS by itself)
    if wm_reasons:
        reasons = list(meta.get("qc_soft_fail_reasons") or [])
        for r in wm_reasons:
            if r not in reasons:
                reasons.append(r)
        meta["qc_soft_fail_reasons"] = reasons
        meta["qc_soft_fail"] = True

    logger.info(
        "photo_enhance: done hdr=%s window_pull=%s sky=%s wm=%s people=%s size=%dx%d",
        used_hdr, used_window, used_sky, used_watermark_strip,
        meta.get("qc_people_detected"), working.size[0], working.size[1],
    )
    if return_meta:
        return out, meta
    return out


def enhance_image(image_bytes: bytes, *, return_meta: bool = False):
    """Single-frame listing-ready enhance (no bracket siblings)."""
    return enhance_bracket([image_bytes], apply_sky=True, return_meta=return_meta)


# ---------------------------------------------------------------------------
# Storage upload
# ---------------------------------------------------------------------------

async def upload_enhanced(storage_client, job_id: str, asset_id: str, enhanced_bytes: bytes) -> str:
    """Write MLS-ready JPEG via storage.upload_bytes (Supabase → Vercel Blob on Fair Use).

    storage_client is unused (kept for call-site compatibility). Under Canada Storage
    402 the old bucket.upload path hollowed the desk — download_bytes already had
    Blob fallback but enhanced/{job}/{id}.jpg never landed on the listing.
    """
    from .storage import upload_bytes

    _ = storage_client  # call sites still pass db.storage
    storage_path = f"enhanced/{job_id}/{asset_id}.jpg"
    ok = await upload_bytes(
        storage_path,
        enhanced_bytes,
        content_type="image/jpeg",
        upsert=True,
    )
    if not ok:
        raise RuntimeError(
            f"In-house enhance upload failed for {storage_path} "
            "(Supabase Storage and Blob fallback both unavailable)"
        )
    logger.info(
        "In-house enhance: uploaded to %s/%s (%s bytes)",
        STORAGE_BUCKET, storage_path, f"{len(enhanced_bytes):,}",
    )
    return storage_path


async def upload_qc_variant(storage_client, job_id: str, asset_id: str, enhanced_bytes: bytes) -> Optional[str]:
    """QC JPEG beside the enhanced still; Blob fallback via upload_bytes (same as upload_enhanced)."""
    from .storage import make_qc_variant_bytes, upload_bytes

    _ = storage_client
    try:
        qc_bytes = make_qc_variant_bytes(enhanced_bytes)
    except Exception:
        logger.warning("In-house enhance: QC variant downscale failed for upload %s", asset_id)
        return None

    storage_path = f"enhanced/{job_id}/qc_{asset_id}.jpg"
    ok = await upload_bytes(
        storage_path,
        qc_bytes,
        content_type="image/jpeg",
        upsert=True,
    )
    if not ok:
        logger.warning(
            "In-house enhance: QC variant upload failed for %s (continuing without qc_variant_url)",
            storage_path,
        )
        return None
    return storage_path
