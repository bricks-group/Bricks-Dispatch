"""Stitch a guided 360 pan burst into an equirectangular 2:1 JPEG.

Matterport phone pattern: overlapping perspective frames at known yaws,
compiled on our server. Not a gallery of stills. Fail honestly if coverage
is too thin or OpenCV/yaw-blend cannot produce a 2:1 equirect.
"""
from __future__ import annotations

import io
import logging
from typing import Optional

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

EQUIRECT_W = 2048
EQUIRECT_H = 1024
MIN_PAN_FRAMES = 8
MIN_YAW_SPAN = 270.0
PHONE_HFOV = 70.0


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
    """pans: [{bytes, yaw}]. Need overlapping coverage around the station."""
    usable = [p for p in pans if p.get("bytes")]
    if len(usable) < MIN_PAN_FRAMES:
        return False
    yaws = [p.get("yaw") for p in usable if p.get("yaw") is not None]
    if len(yaws) >= MIN_PAN_FRAMES and yaw_span_deg(yaws) >= MIN_YAW_SPAN:
        return True
    # No yaws: still stitchable if the photographer filled the dome (many frames).
    return len(usable) >= 12


def _decode_bgr(data: bytes) -> Optional[np.ndarray]:
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
    w, h = size
    try:
        import cv2
        return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
    except Exception:
        rgb = Image.fromarray(img[:, :, ::-1].astype(np.uint8))
        rgb = rgb.resize((w, h), Image.BILINEAR)
        return np.array(rgb)[:, :, ::-1].copy()


def _encode_jpeg(bgr: np.ndarray, quality: int = 85) -> bytes:
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


def _yaw_blend(frames: list[dict], out_w: int = EQUIRECT_W, out_h: int = EQUIRECT_H) -> np.ndarray:
    """Project each pan frame onto a 2:1 canvas at its yaw. Requires yaws."""
    canvas = np.zeros((out_h, out_w, 3), dtype=np.float64)
    weight = np.zeros((out_h, out_w), dtype=np.float64)
    strip_w = max(32, int(out_w * PHONE_HFOV / 360.0))
    strip_h = max(32, int(out_h * 0.52))
    xs = np.linspace(-1.0, 1.0, strip_w, dtype=np.float64)
    hann = np.clip(0.5 * (1.0 + np.cos(np.pi * xs)), 0.05, 1.0)
    y0 = (out_h - strip_h) // 2

    placed = 0
    for i, fr in enumerate(frames):
        img = _decode_bgr(fr["bytes"])
        if img is None:
            continue
        yaw = fr.get("yaw")
        if yaw is None:
            yaw = (i / max(len(frames), 1)) * 360.0
        im = _resize_bgr(img, (strip_w, strip_h)).astype(np.float64)
        contrib = im * hann[None, :, None]
        x_center = int((float(yaw) % 360.0) / 360.0 * out_w)
        start = (x_center - strip_w // 2) % out_w
        end = start + strip_w
        if end <= out_w:
            canvas[y0:y0 + strip_h, start:end] += contrib
            weight[y0:y0 + strip_h, start:end] += hann[None, :]
        else:
            first = out_w - start
            canvas[y0:y0 + strip_h, start:out_w] += contrib[:, :first]
            weight[y0:y0 + strip_h, start:out_w] += hann[None, :first]
            rest = strip_w - first
            canvas[y0:y0 + strip_h, 0:rest] += contrib[:, first:]
            weight[y0:y0 + strip_h, 0:rest] += hann[None, first:]
        placed += 1
    if placed < MIN_PAN_FRAMES:
        raise StitchError(f"Only {placed} pan frames decoded — need {MIN_PAN_FRAMES} for a 360 station")
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
    if w / float(h) < 1.6:
        pano = _letterbox_equirect(pano)
    return _encode_jpeg(pano)
