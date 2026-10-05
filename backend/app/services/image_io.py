"""Shared image decode for Dispatch uploads (JPEG/PNG/WebP/HEIC).

Registers pillow-heif once at import so PIL.Image.open can decode iPhone
HEIC stills. JPEG/PNG paths keep working if pillow-heif is absent.
"""
from __future__ import annotations

import io
import logging
from typing import Optional

from PIL import Image, ImageOps

try:
    from PIL import ImageCms
except ImportError:  # Pillow built without littlecms — strip-only fallback
    ImageCms = None  # type: ignore[assignment]

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


_SRGB_PROFILE = None
_RELATIVE_COLORIMETRIC = (
    getattr(getattr(ImageCms, "Intent", None), "RELATIVE_COLORIMETRIC", 1)
    if ImageCms is not None
    else 1
)


def _srgb_profile():
    global _SRGB_PROFILE
    if _SRGB_PROFILE is None and ImageCms is not None:
        _SRGB_PROFILE = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    return _SRGB_PROFILE


def _profile_is_srgb(profile) -> bool:
    try:
        desc = (ImageCms.getProfileDescription(profile) or "").lower()
    except Exception:
        return False
    return "srgb" in desc.replace(" ", "")


def to_srgb_rgb(img: Image.Image) -> Image.Image:
    """Convert to 8-bit RGB with pixels in sRGB.

    Listing export strips ICC (portals assume sRGB). Stripping a Display P3
    (iPhone HEIC) or Adobe RGB (camera) profile without converting first
    re-interprets wide-gamut values as sRGB — dull reds/greens, shifted skin
    and wood on the MLS listing. Convert embedded non-sRGB RGB/CMYK profiles
    through littlecms; any failure falls back to plain convert("RGB")
    (previous behaviour). Returned image carries no icc_profile.
    """
    icc: Optional[bytes] = img.info.get("icc_profile") if img.info else None
    out: Optional[Image.Image] = None
    if icc and ImageCms is not None:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            if not _profile_is_srgb(src):
                work = img
                if work.mode in ("RGBA", "LA", "P", "PA", "RGBX", "RGBa"):
                    work = work.convert("RGB")
                if work.mode in ("RGB", "CMYK"):
                    out = ImageCms.profileToProfile(
                        work,
                        src,
                        _srgb_profile(),
                        renderingIntent=_RELATIVE_COLORIMETRIC,
                        outputMode="RGB",
                    )
        except Exception as e:
            logger.warning(
                "image_io: ICC→sRGB conversion failed (%s) — using raw pixels", e
            )
            out = None
    if out is None:
        out = img.convert("RGB")
    info = dict(img.info or {})
    info.pop("icc_profile", None)
    out.info = info
    return out


def open_rgb(image_bytes: bytes) -> Image.Image:
    """Open upload bytes, EXIF-transpose, convert to sRGB RGB."""
    img = Image.open(io.BytesIO(image_bytes))
    try:
        oriented = ImageOps.exif_transpose(img)
        if oriented is not None:
            img = oriented
    except Exception:
        logger.warning("image_io: EXIF transpose failed — using raw pixels")
    return to_srgb_rgb(img)
