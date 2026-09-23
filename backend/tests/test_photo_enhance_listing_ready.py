"""Unit tests for listing-ready stills geometry in photo_enhance_service.

No network — synthetic JPEGs via Pillow only.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image, ImageOps

from app.services.photo_enhance_service import (
    LISTING_ASPECT,
    LISTING_JPEG_QUALITY,
    LISTING_MAX_LONG_EDGE,
    _center_crop_aspect,
    _exif_auto_orient,
    _resize_long_edge,
    enhance_image,
    format_listing_ready,
)


def _jpeg_bytes(width: int, height: int, color=(40, 120, 200), orient: int | None = None) -> bytes:
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    exif = img.getexif()
    if orient is not None:
        # PIL Exif tag 274 = Orientation
        exif[274] = orient
        img.save(buf, format="JPEG", quality=85, exif=exif)
    else:
        img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def test_constants_documented():
    assert LISTING_MAX_LONG_EDGE == 2400
    assert LISTING_ASPECT == (4, 3)
    assert LISTING_JPEG_QUALITY == 90


def test_resize_long_edge_downscales_only():
    wide = Image.new("RGB", (4800, 2400), (1, 2, 3))
    out = _resize_long_edge(wide, 2400)
    assert max(out.size) == 2400
    assert out.size == (2400, 1200)

    small = Image.new("RGB", (800, 600), (1, 2, 3))
    same = _resize_long_edge(small, 2400)
    assert same.size == (800, 600)


def test_center_crop_4_3_wide_and_tall():
    # 16:9-ish → trim sides to 4:3
    wide = Image.new("RGB", (1600, 900), (10, 20, 30))
    cropped = _center_crop_aspect(wide, (4, 3))
    w, h = cropped.size
    assert abs((w / h) - (4 / 3)) < 0.02
    assert h == 900
    assert w == 1200

    # Portrait → trim top/bottom
    tall = Image.new("RGB", (900, 1600), (10, 20, 30))
    cropped2 = _center_crop_aspect(tall, (4, 3))
    w2, h2 = cropped2.size
    assert abs((w2 / h2) - (4 / 3)) < 0.02
    assert w2 == 900


def test_format_listing_ready_caps_and_aspect():
    img = Image.new("RGB", (4000, 3000), (50, 60, 70))
    out = format_listing_ready(img)
    assert max(out.size) <= LISTING_MAX_LONG_EDGE
    w, h = out.size
    assert abs((w / h) - (4 / 3)) < 0.02


def test_exif_auto_orient_rotates():
    # Orientation 6 = rotate 90 CW → 100x200 pixels become 200x100 after transpose
    raw = _jpeg_bytes(100, 200, orient=6)
    img = Image.open(io.BytesIO(raw))
    oriented = _exif_auto_orient(img)
    assert oriented.size == (200, 100)


def test_enhance_image_returns_listing_ready_jpeg():
    raw = _jpeg_bytes(3200, 1800)  # 16:9 over max long edge
    out = enhance_image(raw)
    assert out[:2] == b"\xff\xd8"  # JPEG SOI
    result = Image.open(io.BytesIO(out))
    assert result.format == "JPEG"
    w, h = result.size
    assert max(w, h) <= LISTING_MAX_LONG_EDGE
    assert abs((w / h) - (4 / 3)) < 0.02
    # Already-small 4:3 still gets enhanced + re-encoded
    small = enhance_image(_jpeg_bytes(800, 600))
    s = Image.open(io.BytesIO(small))
    assert abs((s.size[0] / s.size[1]) - (4 / 3)) < 0.02
