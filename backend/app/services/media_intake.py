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
    file_url: Any = None,
) -> str:
    """Return a catalog media_type. Unknown → hdr (still photo).

    Optional file_url: when filename is empty/generic, glance at the storage
    path basename (durable prefixes land on the filename column; path may differ).
    """
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
    if not name or name in ("still.jpg", "still.jpeg", "photo.jpg"):
        url_base = _lower(str(file_url or "").replace("\\", "/").split("/")[-1])
        if url_base:
            name = url_base
    mime = _lower(mime_type)
    if "stag" in name:
        return "staging"
    if "twilight" in name or "dusk" in name or "golden" in name:
        return "twilight"
    if "drone" in name or "aerial" in name or name.startswith("dji"):
        return "drone"
    if "reel" in name or "cinematic" in name:
        return "reel"
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

    Order: skip → visit → staging → twilight → as_is (drone/reel/video) → vendor_photo → as_is.

    skip          — no file
    visit         — .ply/.splat/.spz, not a listing still
    staging       — honest original (no virtual staging / no enhance)
    twilight      — real dusk shot; in-house enhance with apply_sky=False (no blue-sky / day-to-dusk)
    as_is         — drone / reel / video, no photo edit (incl. aerial JPEGs)
    vendor_photo  — Fotello / Autoenhance / in-house enhance (may apply sky)

    media_type is not a DB column — always use infer_media_type (filename/mime/explicit)
    so DJI_/aerial_/*.mp4 stills never fall through to vendor_photo + sky replace.
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
    # Use inferred mt (not raw row media_type) — uploads table has no media_type col.
    if mt in AS_IS_TYPES:
        return "as_is"
    if is_video_file(row.get("filename"), row.get("mime_type"), row.get("media_type")):
        return "as_is"
    if is_photo_file(row.get("filename"), row.get("mime_type"), row.get("media_type")):
        return "vendor_photo"
    return "as_is"


_DURABLE_PREFIXES = {
    "drone": "aerial_",
    "reel": "reel_",
    "twilight": "twilight_",
    "staging": "staging_",
}


def durable_media_filename(filename: Any = None, media_type: Any = None) -> str:
    """Path-safe basename that survives DB insert without a media_type column.

    public.uploads strips media_type / requires_staging on insert. process_job_assets
    reloads rows and delivery_route only sees filename/mime — so a GO Twilight pick
    with IMG_1234.JPG would fall through to vendor_photo + sky invent. Prefix
    client-chosen kinds with cues infer_media_type already understands.
    Listing stills (hdr/photo) and filenames that already cue the same kind stay
    unchanged. Does not double-prefix.
    """
    base = (filename or "still.jpg").replace("\\", "/").split("/")[-1].lstrip(".") or "still.jpg"
    intended = infer_media_type(base, None, media_type)
    if intended == "hdr":
        return base
    from_name = infer_media_type(base, None, None)
    if from_name == intended:
        return base
    prefix = _DURABLE_PREFIXES.get(intended)
    if not prefix:
        return base
    if base.lower().startswith(prefix.lower()):
        return base
    return f"{prefix}{base}"


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
