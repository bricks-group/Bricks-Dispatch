"""Classify sold listing media so intake + post-process are not hollow.

HDR stills, twilight, drone, reel, and staging all land on the listing file
through /api/uploads (signed PUT for camera JPEGs / video — not the 4MB
Vercel POST body). Phone capture is only tour / floor plan / Visit bridge.
"""
from __future__ import annotations

from typing import Any, Optional

VALID_MEDIA_TYPES = ("hdr", "photo", "drone", "reel", "twilight", "staging")
PHOTO_TYPES = frozenset({"hdr", "photo", "twilight", "staging", "still", "image", ""})
AS_IS_TYPES = frozenset({"drone", "reel", "video"})
STAGING_TYPES = frozenset({"staging", "virtual_staging"})
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".avi")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".heic")
VISIT_EXTS = (".splat", ".ply", ".spz")


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def infer_media_type(
    filename: Any = None,
    mime_type: Any = None,
    explicit: Any = None,
) -> str:
    """Return a catalog media_type. Unknown → hdr (still photo)."""
    raw = _lower(explicit).replace(" ", "_").replace("-", "_")
    aliases = {
        "photos": "hdr",
        "still": "hdr",
        "stills": "hdr",
        "photography": "hdr",
        "aerial": "drone",
        "dji": "drone",
        "video": "reel",
        "cinematic": "reel",
        "cinematic_reel": "reel",
        "dusk": "twilight",
        "day_to_dusk": "twilight",
        "virtual_staging": "staging",
        "stage": "staging",
    }
    if raw in VALID_MEDIA_TYPES:
        return "hdr" if raw == "photo" else raw
    if raw in aliases:
        return aliases[raw]

    name = _lower(filename)
    mime = _lower(mime_type)
    if "stag" in name:
        return "staging"
    if "twilight" in name or "dusk" in name or "golden" in name:
        return "twilight"
    if "drone" in name or "aerial" in name or name.startswith("dji"):
        return "drone"
    if mime.startswith("video/") or name.endswith(VIDEO_EXTS):
        return "reel"
    return "hdr"


def is_visit_file(filename: Any = None) -> bool:
    name = _lower(filename)
    return name.endswith(VISIT_EXTS)


def is_video_file(filename: Any = None, mime_type: Any = None, media_type: Any = None) -> bool:
    mt = _lower(media_type)
    if mt in AS_IS_TYPES:
        return True
    mime = _lower(mime_type)
    if mime.startswith("video/"):
        return True
    name = _lower(filename)
    return name.endswith(VIDEO_EXTS)


def is_photo_file(filename: Any = None, mime_type: Any = None, media_type: Any = None) -> bool:
    if is_visit_file(filename):
        return False
    if is_video_file(filename, mime_type, media_type):
        return False
    mt = _lower(media_type)
    if mt in STAGING_TYPES or mt in PHOTO_TYPES:
        return True
    mime = _lower(mime_type)
    if mime.startswith("image/"):
        return True
    name = _lower(filename)
    return name.endswith(IMAGE_EXTS) or not name


def wants_staging(asset: Optional[dict] = None) -> bool:
    row = asset or {}
    if row.get("requires_staging") is True:
        return True
    return infer_media_type(row.get("filename"), row.get("mime_type"), row.get("media_type")) == "staging"


def delivery_route(asset: Optional[dict] = None) -> str:
    """How this upload should leave post-production.

    Order: skip → visit → staging → twilight → as_is (video) → vendor_photo → as_is.

    skip          — no file
    visit         — .ply/.splat/.spz, not a listing still
    staging       — honest original (no virtual staging / no enhance)
    twilight      — real dusk shot; in-house enhance with apply_sky=False (no blue-sky / day-to-dusk)
    as_is         — drone / reel / video, no photo edit
    vendor_photo  — Fotello / Autoenhance / in-house enhance (may apply sky)
    """
    row = asset or {}
    if not (row.get("file_url") or row.get("storage_path")):
        return "skip"
    if is_visit_file(row.get("filename")):
        return "visit"
    if wants_staging(row):
        return "staging"
    mt = infer_media_type(row.get("filename"), row.get("mime_type"), row.get("media_type"))
    if mt == "twilight":
        return "twilight"
    if is_video_file(row.get("filename"), row.get("mime_type"), row.get("media_type")):
        return "as_is"
    if is_photo_file(row.get("filename"), row.get("mime_type"), row.get("media_type")):
        return "vendor_photo"
    return "as_is"


def stills_storage_path(job_id: str, upload_id: str, filename: str) -> str:
    safe = (filename or "still.jpg").replace("\\", "/").split("/")[-1].lstrip(".") or "still.jpg"
    return f"bricks-go/jobs/{job_id}/{upload_id}/{safe}"


# Prefer .splat (native gsplat), then .ply, then .spz when multiple visit assets exist.
_VISIT_PICK_ORDER = (".splat", ".ply", ".spz")


def _visit_pick_rank(filename: Any = None) -> int:
    name = _lower(filename)
    for i, ext in enumerate(_VISIT_PICK_ORDER):
        if name.endswith(ext):
            return i
    return len(_VISIT_PICK_ORDER)


def is_photo_delivery_candidate(filename: Any = None) -> bool:
    """False for Visit/3D assets that must not land in listing still delivery_urls.

    Filename-only helper kept for backward compatibility. Prefer
    ``is_listing_still_delivery_row`` when the upload row is available so
    HDR bracket siblings are also excluded.
    """
    return not is_visit_file(filename)


def is_listing_still_delivery_row(upload: Any = None) -> bool:
    """True when this upload should appear once in listing still delivery.

    Excludes Visit/3D (filename or provider=visit) and HDR bracket siblings
    (non-primary frames that share the primary's processed_url after merge).
    Parity with OS is_listing_still_upload.
    """
    row = upload if isinstance(upload, dict) else {}
    if is_visit_file(row.get("filename")):
        return False
    provider = _lower(row.get("provider"))
    if provider in ("bracket_sibling", "visit"):
        return False
    return True


def _is_visit_upload_row(upload: Any = None) -> bool:
    row = upload if isinstance(upload, dict) else {}
    if is_visit_file(row.get("filename")):
        return True
    return _lower(row.get("provider")) == "visit"


def pick_visit_upload(uploads: list | None) -> Optional[dict]:
    """First Visit upload for property_visits registration (.splat > .ply > .spz)."""
    visits = [u for u in (uploads or []) if _is_visit_upload_row(u)]
    if not visits:
        return None
    visits.sort(key=lambda u: _visit_pick_rank((u or {}).get("filename")))
    return visits[0]
