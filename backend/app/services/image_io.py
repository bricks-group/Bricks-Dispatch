"""Shared image decode for Dispatch uploads (JPEG/PNG/WebP/HEIC).

Registers pillow-heif once at import so PIL.Image.open can decode iPhone
HEIC stills. JPEG/PNG paths keep working if pillow-heif is absent.
"""
from __future__ import annotations

import io
import logging

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

_HEIF_REGISTERED = False

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    _HEIF_REGISTERED = True
except ImportError:
    logger.warning(
        "image_io: pillow-heif not installed — HEIC uploads will fail to decode; "
        "JPEG/PNG still work"
    )


def heif_opener_registered() -> bool:
    return _HEIF_REGISTERED


def open_rgb(image_bytes: bytes) -> Image.Image:
    """Open upload bytes, EXIF-transpose, convert to RGB."""
    img = Image.open(io.BytesIO(image_bytes))
    try:
        oriented = ImageOps.exif_transpose(img)
        if oriented is not None:
            img = oriented
    except Exception:
        logger.warning("image_io: EXIF transpose failed — using raw pixels")
    return img.convert("RGB")
