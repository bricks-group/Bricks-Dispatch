"""Transparent / 16-bit sources must not reach the listing as black / white.

image_io.to_srgb_rgb is the choke point for listing export (as_is staging /
drone PNG → apply_listing_export), in-house enhance, HEIC→JPEG delivery and
QC variants/thumbnails. Plain convert("RGB") drops alpha (transparent pixels
are usually stored as 0,0,0 → black slab on the MLS still, and QC scores the
black variant) and clips 16-bit greyscale to pure white.
"""
import io

import numpy as np
from PIL import Image

from app.services import image_io
from app.services.photo_enhance_service import apply_listing_export
from app.services.storage import make_qc_variant_bytes


def _png(img: Image.Image, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", **kw)
    return buf.getvalue()


def _staging_rgba() -> bytes:
    # Room still with a transparent margin (virtual-staging / cutout exports).
    img = Image.new("RGBA", (800, 600), (0, 0, 0, 0))
    img.paste((180, 140, 100, 255), (100, 75, 700, 525))
    return _png(img)


def test_open_rgb_flattens_transparent_on_white_not_black():
    out = image_io.open_rgb(_staging_rgba())
    assert out.mode == "RGB"
    assert out.getpixel((10, 10)) == (255, 255, 255)
    assert out.getpixel((400, 300)) == (180, 140, 100)


def test_open_rgb_half_alpha_blends_toward_white():
    img = Image.new("RGBA", (20, 20), (0, 0, 0, 128))
    px = image_io.open_rgb(_png(img)).getpixel((5, 5))
    assert all(120 <= c <= 135 for c in px)


def test_open_rgb_la_and_palette_transparency_flatten_white():
    la = Image.new("LA", (20, 20), (0, 0))
    assert image_io.open_rgb(_png(la)).getpixel((1, 1)) == (255, 255, 255)
    p = Image.new("P", (20, 20), 0)
    p.putpalette([0, 0, 0] + [255, 0, 0] * 255)
    assert image_io.open_rgb(_png(p, transparency=0)).getpixel((1, 1)) == (255, 255, 255)


def test_opaque_rgba_unchanged():
    img = Image.new("RGBA", (20, 20), (12, 34, 56, 255))
    assert image_io.open_rgb(_png(img)).getpixel((3, 3)) == (12, 34, 56)


def test_open_rgb_16bit_grey_scaled_not_clipped_white():
    arr = np.full((20, 20), 32896, dtype=np.uint16)  # ~mid grey (128 * 257)
    src = Image.fromarray(arr)
    assert src.mode == "I;16"
    out = image_io.open_rgb(_png(src))
    assert out.mode == "RGB"
    r, g, b = out.getpixel((2, 2))
    assert r == g == b and 126 <= r <= 130


def test_listing_export_transparent_png_has_white_margin():
    jpeg, meta = apply_listing_export(_staging_rgba())
    img = Image.open(io.BytesIO(jpeg))
    assert img.format == "JPEG" and meta["export"]["aspect_ok"]
    corner = np.asarray(img.convert("RGB"))[:10, :10].mean()
    assert corner > 240, corner


def test_qc_variant_transparent_png_not_black():
    var = Image.open(io.BytesIO(make_qc_variant_bytes(_staging_rgba())))
    assert np.asarray(var.convert("RGB"))[:10, :10].mean() > 240
