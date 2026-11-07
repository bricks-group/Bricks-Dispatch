"""HEIC decode for GO iPhone stills → enhance → MLS JPEG listing.

Format path only — does not claim Frame ACCEPT / desk HDR quality.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "heic-smoke.heic"


def _heic_bytes() -> bytes:
    if not FIXTURE.is_file():
        pytest.skip(f"missing HEIC fixture: {FIXTURE}")
    return FIXTURE.read_bytes()


def test_heif_opener_registers_when_pillow_heif_present():
    from app.services import image_io

    try:
        import pillow_heif  # noqa: F401
    except ImportError:
        pytest.skip("pillow-heif not installed")
    assert image_io.heif_opener_registered() is True


def test_open_rgb_heic_returns_rgb_expected_size():
    from app.services.image_io import open_rgb

    try:
        import pillow_heif  # noqa: F401
    except ImportError:
        pytest.skip("pillow-heif not installed")

    img = open_rgb(_heic_bytes())
    assert img.mode == "RGB"
    assert img.size == (64, 48)


def test_enhance_bracket_heic_returns_jpeg_with_mls_meta():
    from app.services.photo_enhance_service import enhance_bracket

    try:
        import pillow_heif  # noqa: F401
    except ImportError:
        pytest.skip("pillow-heif not installed")

    out, meta = enhance_bracket([_heic_bytes()], return_meta=True)
    assert isinstance(out, (bytes, bytearray))
    assert out[:2] == b"\xff\xd8"  # JPEG SOI

    result = Image.open(io.BytesIO(out))
    assert (result.format or "").upper() == "JPEG"
    assert result.mode in ("RGB", "L")
    assert result.size[0] >= result.size[1]  # landscape MLS export

    assert meta.get("mls_export_profile")
    export = meta.get("export") or {}
    assert export.get("aspect_ok") is True
    assert (export.get("format") or "").upper() == "JPEG"
    # Soft geometry only — no Frame ACCEPT claim on tiny synthetic HEIC.
