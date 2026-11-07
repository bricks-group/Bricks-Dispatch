"""Listing stills colour honesty: embedded wide-gamut ICC → real sRGB pixels.

`_to_srgb_rgb` / `_encode_jpeg` strip ICC so portals see a plain sRGB JPEG.
Stripping a Display P3 (iPhone HEIC) or Adobe RGB (camera) profile without
converting the pixels first re-interprets them as sRGB — reds/greens go
dull and skin/wood shifts on the MLS listing. `image_io.open_rgb` is the one
decode choke point (enhance, AE listing export, HEIC→JPEG, QC variants), so
it must colour-convert embedded non-sRGB profiles to sRGB.

Format/colour path only — no Frame ACCEPT / HDR quality claim.
"""
from __future__ import annotations

import io
import struct

import pytest
from PIL import Image, ImageCms


def _s15f16(v: float) -> bytes:
    return struct.pack(">i", int(round(v * 65536)))


def _xyz(x: float, y: float, z: float) -> bytes:
    return b"XYZ " + b"\0" * 4 + _s15f16(x) + _s15f16(y) + _s15f16(z)


def _curv_gamma(g: float) -> bytes:
    return b"curv" + b"\0" * 4 + struct.pack(">I", 1) + struct.pack(">H", int(round(g * 256))) + b"\0\0"


def _desc(text: str) -> bytes:
    a = text.encode("ascii") + b"\0"
    body = b"desc" + b"\0" * 4 + struct.pack(">I", len(a)) + a
    body += struct.pack(">II", 0, 0)  # unicode lang + count
    body += struct.pack(">HB", 0, 0) + b"\0" * 67  # scriptcode
    while len(body) % 4:
        body += b"\0"
    return body


def _text(text: str) -> bytes:
    body = b"text" + b"\0" * 4 + text.encode("ascii") + b"\0"
    while len(body) % 4:
        body += b"\0"
    return body


def _matrix_icc(name: str, r, g, b, gamma: float = 2.2) -> bytes:
    """Minimal ICC v2 RGB matrix/TRC display profile (D50 PCS)."""
    tags = [
        (b"desc", _desc(name)),
        (b"cprt", _text("test fixture")),
        (b"wtpt", _xyz(0.9642, 1.0, 0.8249)),
        (b"rXYZ", _xyz(*r)),
        (b"gXYZ", _xyz(*g)),
        (b"bXYZ", _xyz(*b)),
        (b"rTRC", _curv_gamma(gamma)),
        (b"gTRC", _curv_gamma(gamma)),
        (b"bTRC", _curv_gamma(gamma)),
    ]
    table_len = 4 + 12 * len(tags)
    offset = 128 + table_len
    table = struct.pack(">I", len(tags))
    data = b""
    for sig, blob in tags:
        table += sig + struct.pack(">II", offset + len(data), len(blob))
        data += blob
    size = 128 + table_len + len(data)
    header = struct.pack(">I", size) + b"lcms" + struct.pack(">I", 0x02100000)
    header += b"mntr" + b"RGB " + b"XYZ " + b"\0" * 12 + b"acsp" + b"APPL"
    header += b"\0" * 4 + b"\0" * 4 + b"\0" * 4 + b"\0" * 8 + struct.pack(">I", 0)
    header += _s15f16(0.9642) + _s15f16(1.0) + _s15f16(0.8249) + b"lcms"
    header += b"\0" * (128 - len(header))
    assert len(header) == 128
    return header + table + data


# Display P3 primaries, D50-adapted (Apple "Display P3" profile values).
P3_ICC = _matrix_icc(
    "Display P3 (test)",
    (0.5151, 0.2412, -0.0011),
    (0.2919, 0.6922, 0.0419),
    (0.1572, 0.0666, 0.7841),
)
# Adobe RGB (1998) primaries, D50-adapted.
ADOBE_ICC = _matrix_icc(
    "Adobe RGB (1998) (test)",
    (0.6097, 0.3111, 0.0195),
    (0.2053, 0.6257, 0.0609),
    (0.1492, 0.0632, 0.7446),
    gamma=2.2,
)

SATURATED = (215, 40, 35)  # brick-red facade


def _tagged_jpeg(color, icc: bytes | None, size=(640, 480)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    kw = {"quality": 95, "subsampling": 0}
    if icc:
        kw["icc_profile"] = icc
    img.save(buf, format="JPEG", **kw)
    return buf.getvalue()


def _reference_srgb(color, icc: bytes):
    src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
    dst = ImageCms.createProfile("sRGB")
    out = ImageCms.profileToProfile(
        Image.new("RGB", (4, 4), color), src, dst, outputMode="RGB"
    )
    return out.getpixel((1, 1))


def _center(data_or_img):
    img = data_or_img
    if isinstance(data_or_img, (bytes, bytearray)):
        img = Image.open(io.BytesIO(data_or_img)).convert("RGB")
    w, h = img.size
    return img.getpixel((w // 2, h // 2))


def _close(a, b, tol=4):
    return all(abs(int(x) - int(y)) <= tol for x, y in zip(a, b))


@pytest.mark.parametrize("icc", [P3_ICC, ADOBE_ICC], ids=["display_p3", "adobe_rgb"])
def test_open_rgb_converts_wide_gamut_icc_to_srgb_pixels(icc):
    from app.services.image_io import open_rgb

    ref = _reference_srgb(SATURATED, icc)
    # Sanity: the fixture really is wide-gamut (naive strip would be visibly off).
    assert not _close(ref, SATURATED, tol=8)

    img = open_rgb(_tagged_jpeg(SATURATED, icc))
    assert img.mode == "RGB"
    assert not img.info.get("icc_profile")
    assert _close(_center(img), ref), (_center(img), ref)


@pytest.mark.parametrize("icc", [P3_ICC, ADOBE_ICC], ids=["display_p3", "adobe_rgb"])
def test_apply_listing_export_ships_srgb_pixels_not_stripped_profile(icc):
    from app.services.photo_enhance_service import apply_listing_export

    ref = _reference_srgb(SATURATED, icc)
    out, meta = apply_listing_export(_tagged_jpeg(SATURATED, icc, size=(900, 600)))
    assert meta["export"]["color_space"] == "srgb"
    res = Image.open(io.BytesIO(out))
    assert not res.info.get("icc_profile")
    assert _close(_center(out), ref), (_center(out), ref)


def test_qc_variant_and_thumbnail_also_see_srgb():
    from app.services.storage import _make_thumbnail_bytes, make_qc_variant_bytes

    ref = _reference_srgb(SATURATED, P3_ICC)
    src = _tagged_jpeg(SATURATED, P3_ICC)
    assert _close(_center(make_qc_variant_bytes(src)), ref)
    assert _close(_center(_make_thumbnail_bytes(src)), ref, tol=6)


def test_srgb_tagged_and_untagged_pixels_unchanged():
    from app.services.image_io import open_rgb

    srgb_icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    for icc in (None, srgb_icc):
        img = open_rgb(_tagged_jpeg(SATURATED, icc))
        assert _close(_center(img), SATURATED, tol=2), (icc is None, _center(img))


def test_corrupt_icc_falls_back_without_raising():
    from app.services.image_io import open_rgb

    img = Image.new("RGB", (64, 48), SATURATED)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95, subsampling=0, icc_profile=b"not-a-real-profile" * 4)
    out = open_rgb(buf.getvalue())
    assert out.mode == "RGB"
    assert out.size == (64, 48)
    assert _close(_center(out), SATURATED, tol=3)


def test_rgba_png_with_p3_icc_converts():
    from app.services.image_io import open_rgb

    img = Image.new("RGBA", (64, 48), SATURATED + (255,))
    buf = io.BytesIO()
    img.save(buf, format="PNG", icc_profile=P3_ICC)
    out = open_rgb(buf.getvalue())
    assert out.mode == "RGB"
    assert _close(_center(out), _reference_srgb(SATURATED, P3_ICC), tol=3)


def test_heic_p3_converts_to_srgb():
    try:
        import pillow_heif  # noqa: F401
    except ImportError:
        pytest.skip("pillow-heif not installed")
    from app.services.image_io import open_rgb

    img = Image.new("RGB", (64, 48), SATURATED)
    buf = io.BytesIO()
    try:
        img.save(buf, format="HEIF", quality=-1, icc_profile=P3_ICC)
    except Exception as e:  # encoder missing on this box
        pytest.skip(f"HEIF encode unavailable: {e}")
    decoded = Image.open(io.BytesIO(buf.getvalue()))
    if not decoded.info.get("icc_profile"):
        pytest.skip("HEIF encoder dropped ICC on this box")
    out = open_rgb(buf.getvalue())
    assert _close(_center(out), _reference_srgb(SATURATED, P3_ICC), tol=6)


def test_exif_rotated_p3_still_converts_and_rotates():
    from app.services.image_io import open_rgb

    img = Image.new("RGB", (80, 40), SATURATED)
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 CW on display
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95, subsampling=0, icc_profile=P3_ICC, exif=exif.tobytes())
    out = open_rgb(buf.getvalue())
    assert out.size == (40, 80)
    assert _close(_center(out), _reference_srgb(SATURATED, P3_ICC))
