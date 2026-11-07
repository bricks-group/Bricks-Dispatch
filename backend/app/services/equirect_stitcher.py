"""Stitch a guided 360 pan burst into an equirectangular 2:1 JPEG.

Matterport phone pattern: overlapping perspective frames at known yaws,
compiled on our server. Not a gallery of stills. Fail honestly if coverage
is too thin or OpenCV/yaw-blend cannot produce a 2:1 equirect.

Yaw-blend path (not OpenCV Stitcher): exposure-normalize pans, then
2D-feathered weighted average onto the equirect canvas — stays inside
Vercel maxDuration budgets where full OpenCV stitch times out.
"""
from __future__ import annotations

import io
import logging
from typing import Optional

# Native array processing is loaded only when stitching is requested.
from PIL import Image

logger = logging.getLogger(__name__)

EQUIRECT_W = 2048
EQUIRECT_H = 1024
MIN_PAN_FRAMES = 8
MIN_YAW_SPAN = 270.0
PHONE_HFOV = 70.0
# Soft floor so a single strip still paints when alone; seams still taper hard.
FEATHER_FLOOR = 0.02
EXPOSURE_GAIN_MIN = 0.55
EXPOSURE_GAIN_MAX = 1.85
TARGET_LUMA = 110.0


class StitchError(RuntimeError):
    pass


def is_equirect_image(data: bytes) -> bool:
    try:
        img = Image.open(io.BytesIO(data))
        w, h = img.size
    except Exception:
        return False
    if h <= 0 or w < 512:
        return False
    ratio = w / float(h)
    return 1.8 <= ratio <= 2.25


def yaw_span_deg(yaws: list[float]) -> float:
    if not yaws:
        return 0.0
    xs = sorted((float(y) % 360.0) for y in yaws)
    if len(xs) == 1:
        return 0.0
    gaps = [(xs[i] - xs[i - 1]) % 360.0 for i in range(1, len(xs))]
    gaps.append((xs[0] + 360.0) - xs[-1])
    return 360.0 - max(gaps) if gaps else 0.0


def station_is_stitchable(pans: list[dict]) -> bool:
    """pans: [{bytes, yaw}]. Need overlapping coverage around the station.

    When enough frames carry yaw metadata, require MIN_YAW_SPAN — do not let
    a dense narrow burst pass via the no-yaw frame-count fallback.
    """
    usable = [p for p in pans if p.get("bytes")]
    if len(usable) < MIN_PAN_FRAMES:
        return False
    yaws = [p.get("yaw") for p in usable if p.get("yaw") is not None]
    if len(yaws) >= MIN_PAN_FRAMES:
        return yaw_span_deg(yaws) >= MIN_YAW_SPAN
    # No (enough) yaws: still stitchable if the photographer filled the dome.
    return len(usable) >= 12


def _decode_bgr(data: bytes) -> Optional[np.ndarray]:
    import numpy as np

    try:
        import cv2
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return img
    except Exception:
        try:
            im = Image.open(io.BytesIO(data)).convert("RGB")
            return np.array(im)[:, :, ::-1].copy()
        except Exception:
            return None


def _resize_bgr(img: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    import numpy as np

    w, h = size
    try:
        import cv2
        return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
    except Exception:
        rgb = Image.fromarray(img[:, :, ::-1].astype(np.uint8))
        rgb = rgb.resize((w, h), Image.BILINEAR)
        return np.array(rgb)[:, :, ::-1].copy()


def _encode_jpeg(bgr: np.ndarray, quality: int = 85) -> bytes:
    import numpy as np

    try:
        import cv2
        ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if ok:
            return buf.tobytes()
    except Exception:
        pass
    rgb = np.clip(bgr[:, :, ::-1], 0, 255).astype(np.uint8)
    im = Image.fromarray(rgb)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _letterbox_equirect(pano: np.ndarray, out_w: int = EQUIRECT_W, out_h: int = EQUIRECT_H) -> np.ndarray:
    import numpy as np

    h, w = pano.shape[:2]
    if w <= 0 or h <= 0:
        raise StitchError("Empty panorama")
    scale = out_w / float(w)
    nh = max(1, int(round(h * scale)))
    resized = _resize_bgr(pano, (out_w, nh))
    canvas = np.zeros((out_h, out_w, 3), dtype=np.uint8)
    if nh >= out_h:
        y0 = (nh - out_h) // 2
        canvas[:] = resized[y0:y0 + out_h]
    else:
        y0 = (out_h - nh) // 2
        canvas[y0:y0 + nh] = resized
    return canvas


def _opencv_stitch(images: list[np.ndarray]) -> Optional[np.ndarray]:
    try:
        import cv2
        stitcher = cv2.Stitcher_create(cv2.Stitcher_PANORAMA)
        status, pano = stitcher.stitch(images)
        if status != cv2.Stitcher_OK or pano is None:
            logger.info("OpenCV Stitcher status=%s", status)
            return None
        return pano
    except Exception as e:
        logger.info("OpenCV Stitcher unavailable: %s", e)
        return None


def _luma_mean_bgr(img: np.ndarray) -> float:
    """Rec.601 luma from BGR float/uint8 array."""
    import numpy as np

    x = img.astype(np.float64)
    return float(np.mean(0.114 * x[:, :, 0] + 0.587 * x[:, :, 1] + 0.299 * x[:, :, 2]))


def _exposure_gains(lumas: list[float]) -> list[float]:
    """Per-frame gains toward the median pan luma (clamped)."""
    import numpy as np

    valid = [L for L in lumas if L > 1e-3]
    if not valid:
        return [1.0] * len(lumas)
    target = float(np.median(np.asarray(valid, dtype=np.float64)))
    # Nudge very dark/bright sets toward a readable mid-gray without blowing highlights.
    target = 0.7 * target + 0.3 * TARGET_LUMA
    target = max(40.0, min(180.0, target))
    gains: list[float] = []
    for L in lumas:
        if L <= 1e-3:
            gains.append(1.0)
            continue
        g = target / L
        gains.append(float(np.clip(g, EXPOSURE_GAIN_MIN, EXPOSURE_GAIN_MAX)))
    return gains


def _feather_2d(strip_w: int, strip_h: int) -> np.ndarray:
    """Raised-cosine weight field: hard taper at edges (seams), soft floor in core."""
    import numpy as np

    xs = np.linspace(-1.0, 1.0, strip_w, dtype=np.float64)
    ys = np.linspace(-1.0, 1.0, strip_h, dtype=np.float64)
    # Cosine → 0 at ±1, 1 at center. Vertical taper is gentler (0.85 mix) so
    # horizon band stays stronger than zenith/nadir letterbox edges.
    hann_x = 0.5 * (1.0 + np.cos(np.pi * xs))
    hann_y = 0.5 * (1.0 + np.cos(np.pi * ys))
    hann_y = 0.85 * hann_y + 0.15
    w2d = hann_y[:, None] * hann_x[None, :]
    return np.clip(w2d, FEATHER_FLOOR, 1.0)


def _place_weighted(
    canvas: np.ndarray,
    weight: np.ndarray,
    contrib: np.ndarray,
    wstrip: np.ndarray,
    y0: int,
    strip_h: int,
    start: int,
    out_w: int,
) -> None:
    strip_w = contrib.shape[1]
    end = start + strip_w
    if end <= out_w:
        canvas[y0:y0 + strip_h, start:end] += contrib
        weight[y0:y0 + strip_h, start:end] += wstrip
        return
    first = out_w - start
    canvas[y0:y0 + strip_h, start:out_w] += contrib[:, :first]
    weight[y0:y0 + strip_h, start:out_w] += wstrip[:, :first]
    rest = strip_w - first
    canvas[y0:y0 + strip_h, 0:rest] += contrib[:, first:]
    weight[y0:y0 + strip_h, 0:rest] += wstrip[:, first:]


def _yaw_blend(frames: list[dict], out_w: int = EQUIRECT_W, out_h: int = EQUIRECT_H) -> np.ndarray:
    """Project each pan frame onto a 2:1 canvas at its yaw. Requires yaws.

    Exposure-normalizes strips toward median luma, then blends with a 2D
    raised-cosine feather so overlapping seams prefer frame centers.
    """
    import numpy as np

    canvas = np.zeros((out_h, out_w, 3), dtype=np.float64)
    weight = np.zeros((out_h, out_w), dtype=np.float64)
    strip_w = max(32, int(out_w * PHONE_HFOV / 360.0))
    strip_h = max(32, int(out_h * 0.52))
    w2d = _feather_2d(strip_w, strip_h)
    y0 = (out_h - strip_h) // 2

    decoded: list[tuple[np.ndarray, float]] = []
    for i, fr in enumerate(frames):
        img = _decode_bgr(fr["bytes"])
        if img is None:
            continue
        yaw = fr.get("yaw")
        if yaw is None:
            yaw = (i / max(len(frames), 1)) * 360.0
        im = _resize_bgr(img, (strip_w, strip_h)).astype(np.float64)
        decoded.append((im, float(yaw)))

    if len(decoded) < MIN_PAN_FRAMES:
        raise StitchError(
            f"Only {len(decoded)} pan frames decoded — need {MIN_PAN_FRAMES} for a 360 station"
        )

    lumas = [_luma_mean_bgr(im) for im, _ in decoded]
    gains = _exposure_gains(lumas)

    for (im, yaw), gain in zip(decoded, gains):
        im_n = np.clip(im * gain, 0.0, 255.0)
        contrib = im_n * w2d[:, :, None]
        x_center = int((float(yaw) % 360.0) / 360.0 * out_w)
        start = (x_center - strip_w // 2) % out_w
        _place_weighted(canvas, weight, contrib, w2d, y0, strip_h, start, out_w)

    filled = weight > 1e-6
    if filled.mean() < 0.35:
        raise StitchError("Pan coverage is too thin to stitch a 360 equirect")
    out = np.zeros((out_h, out_w, 3), dtype=np.uint8)
    np.divide(canvas, weight[:, :, None], out=canvas, where=weight[:, :, None] > 1e-6)
    out[filled] = np.clip(canvas[filled], 0, 255).astype(np.uint8)
    return out


def stitch_pan_to_equirect(pans: list[dict]) -> bytes:
    """Return 2:1 JPEG bytes. pans: [{bytes, yaw}]. Raises StitchError."""
    usable = [p for p in pans if p.get("bytes")]
    if not station_is_stitchable(usable):
        yaws = [p.get("yaw") for p in usable if p.get("yaw") is not None]
        raise StitchError(
            f"Station pan is not a 360 sweep ({len(usable)} frames, "
            f"yaw span {yaw_span_deg(yaws):.0f}°). Fill the coverage dome."
        )
    # Yaw-blend first: OpenCV stitch of 24 HD frames times out on Vercel.
    ordered = sorted(usable, key=lambda p: (p.get("yaw") is None, float(p.get("yaw") or 0)))
    pano = _yaw_blend(ordered)
    h, w = pano.shape[:2]
    if abs(w / float(h) - 2.0) > 0.05:
        pano = _letterbox_equirect(pano)
    h, w = pano.shape[:2]
    if w != EQUIRECT_W or h != EQUIRECT_H:
        pano = _letterbox_equirect(pano)
    return _encode_jpeg(pano)
