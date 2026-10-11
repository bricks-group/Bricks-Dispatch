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


def _flatten_alpha_and_depth(img: Image.Image) -> Image.Image:
    """Composite transparency onto white and scale 16-bit greyscale to 8-bit.

    Plain convert("RGB") drops alpha — transparent pixels are usually stored
    as 0,0,0 so cutout / staging PNG margins became a black slab on the MLS
    still (and QC scored the black variant). It also clips 16-bit greyscale
    ("I;16" / "I") to pure white. Opaque / 8-bit images pass through as-is.
    """
    mode = img.mode
    if mode.startswith("I;16") or mode == "I":
        try:
            import numpy as np

            arr = np.asarray(img, dtype=np.float64)
            scale = 257.0 if (mode.startswith("I;16") or float(arr.max(initial=0)) > 255.0) else 1.0
            out = Image.fromarray(np.clip(np.round(arr / scale), 0, 255).astype("uint8"), "L")
            out.info = dict(img.info or {})
            return out
        except Exception as e:
            logger.warning("image_io: 16-bit scale failed (%s) — using raw convert", e)
            return img
    has_alpha = mode in ("RGBA", "LA", "PA", "RGBa", "La") or (
        mode == "P" and "transparency" in (img.info or {})
    ) or (mode in ("L", "RGB") and "transparency" in (img.info or {}))
    if not has_alpha:
        return img
    try:
        rgba = img.convert("RGBA")
        if rgba.getextrema()[3][0] == 255:
            flat = rgba.convert("RGB")
        else:
            page = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            page.alpha_composite(rgba)
            flat = page.convert("RGB")
        info = dict(img.info or {})
        info.pop("transparency", None)
        flat.info = info
        return flat
    except Exception as e:
        logger.warning("image_io: alpha flatten failed (%s) — using raw convert", e)
        return img


def to_srgb_rgb(img: Image.Image) -> Image.Image:
    """Convert to 8-bit RGB with pixels in sRGB.

    Listing export strips ICC (portals assume sRGB). Stripping a Display P3
    (iPhone HEIC) or Adobe RGB (camera) profile without converting first
    re-interprets wide-gamut values as sRGB — dull reds/greens, shifted skin
    and wood on the MLS listing. Convert embedded non-sRGB RGB/CMYK profiles
    through littlecms; any failure falls back to plain convert("RGB")
    (previous behaviour). Returned image carries no icc_profile.
    """
    img = _flatten_alpha_and_depth(img)
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


# 360° equirectangular panoramas (DJI sphere, Insta360, Ricoh Theta, phone
# photo-sphere) are 2:1 full-sphere projections. The listing export crops every
# still to a 4:3 landscape and in-house enhance may replace sky — on a sphere
# that produces a warped centre slice that is neither a listing still nor a
# usable 360. These belong in Visit / tour, never the listing gallery.
_GPANO_EQUIRECT_MARKERS = (
    b'GPano:ProjectionType="equirectangular"',
    b"GPano:ProjectionType='equirectangular'",
    b"<GPano:ProjectionType>equirectangular</GPano:ProjectionType>",
)
EQUIRECT_ASPECT = 2.0
EQUIRECT_ASPECT_TOL = 0.01  # ±0.5% of 2:1
EQUIRECT_MIN_WIDTH = 4000  # spherical cameras export ≥4K wide; avoids small 2:1 crops


def is_equirect_360(image_bytes: Optional[bytes]) -> bool:
    """True when upload bytes are a 360° equirectangular panorama.

    Primary signal: XMP GPano ProjectionType=equirectangular (written by DJI,
    Insta360, Theta, Google/Android photo sphere). Fallback: exact 2:1 frame at
    ≥4000 px wide (sphere cameras that strip XMP). Header-only read — no decode.
    Cylindrical / partial phone panoramas (GPano cylindrical, 3:1+) stay False.
    """
    if not image_bytes:
        return False
    head = bytes(image_bytes[:262144])
    if any(m in head for m in _GPANO_EQUIRECT_MARKERS):
        return True
    if b"GPano:ProjectionType" in head:
        # Explicit non-equirect projection (cylindrical / rectilinear) — trust it.
        return False
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            w, h = img.size
    except Exception:
        return False
    if h <= 0 or w < EQUIRECT_MIN_WIDTH:
        return False
    return abs((w / float(h)) - EQUIRECT_ASPECT) <= EQUIRECT_ASPECT_TOL


# Wide stitched panoramas (phone "Pano" mode, cylindrical stitches, drone
# horizontal panos) are 2.5:1 and wider. The listing export centre-crops every
# still to 4:3 landscape — on a 3:1 pano that discards over half the frame and
# on a 6:1 pano nearly four fifths, so the client gets a slice the photographer
# never framed. Hold uncropped for review instead of auto-delivering the slice.
WIDE_PANO_MIN_ASPECT = 2.5  # 4:3 crop keeps ≤53% of the width at this ratio
_EXIF_ORIENTATION_TAG = 0x0112
_EXIF_ROTATED_90 = (5, 6, 7, 8)


def is_wide_panorama(image_bytes: Optional[bytes]) -> bool:
    """True when upload bytes are a wide (≥2.5:1, as displayed) stitched panorama.

    Uses the displayed orientation (EXIF 5–8 swap width/height). 360°
    equirect spheres are classified by ``is_equirect_360`` first; 16:9, 2:1
    and 21:9 frames stay False (their 4:3 crop keeps most of the shot).
    Header-only read — no decode.
    """
    if not image_bytes:
        return False
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            w, h = img.size
            try:
                orientation = int(img.getexif().get(_EXIF_ORIENTATION_TAG) or 1)
            except Exception:
                orientation = 1
    except Exception:
        return False
    if orientation in _EXIF_ROTATED_90:
        w, h = h, w
    if w <= 0 or h <= 0:
        return False
    return (w / float(h)) >= WIDE_PANO_MIN_ASPECT


def is_tall_panorama(image_bytes: Optional[bytes]) -> bool:
    """True when upload bytes are a tall (≥1:2.5, as displayed) vertical panorama.

    Vertical phone pano / vertical drone pano (stairwell, tower, façade). The
    listing export forces landscape 4:3, so a 1:3 source would keep ~25% of its
    height. Ordinary portrait (3:4, 2:3, 9:16) stays False. Uses the displayed
    orientation (EXIF 5–8 swap). Header-only read — no decode.
    """
    if not image_bytes:
        return False
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            w, h = img.size
            try:
                orientation = int(img.getexif().get(_EXIF_ORIENTATION_TAG) or 1)
            except Exception:
                orientation = 1
    except Exception:
        return False
    if orientation in _EXIF_ROTATED_90:
        w, h = h, w
    if w <= 0 or h <= 0:
        return False
    return (h / float(w)) >= WIDE_PANO_MIN_ASPECT


# Below this long edge (px) an upload is a thumbnail / screenshot / messaging-app
# copy, not a listing-grade original. Internal conservative floor (not an MLS
# rule): every camera, drone and phone Bricks vendors shoot on exceeds it by far.
LOW_RES_MIN_LONG_EDGE = 1024


def is_low_resolution_still(image_bytes: Optional[bytes]) -> bool:
    """True when upload bytes decode to an image whose long edge < 1024 px.

    Header-only read — no decode. Unreadable bytes return False (other holds
    own those). Orientation does not matter for the long edge.
    """
    if not image_bytes:
        return False
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            w, h = img.size
    except Exception:
        return False
    if w <= 0 or h <= 0:
        return False
    return max(w, h) < LOW_RES_MIN_LONG_EDGE
