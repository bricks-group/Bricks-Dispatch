"""Classify sold listing media so intake + post-process are not hollow.

HDR stills, twilight, drone, reel, staging, and floorplan all land on the listing
file through /api/uploads (signed PUT for camera JPEGs / video / PDF — not the
4MB Vercel POST body). Phone capture is only tour / floor plan / Visit bridge.
Floorplan PNG/PDF/CubiCasa exports deliver as_is (honest — no HDR enhance).
"""
from __future__ import annotations

import re
from typing import Any, Optional

VALID_MEDIA_TYPES = ("hdr", "photo", "drone", "reel", "twilight", "staging", "floorplan")
PHOTO_TYPES = frozenset({"hdr", "photo", "twilight", "staging", "still", "image", ""})
AS_IS_TYPES = frozenset({"drone", "reel", "video", "floorplan"})
STAGING_TYPES = frozenset({"staging", "virtual_staging"})
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".avi")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".heic", ".heif")
PDF_EXTS = (".pdf",)
# Camera RAW is the primary stills upload (Bricks Standard v1.1: vendors
# capture, Bricks edits). The RAW stays the master; raw_decode makes the
# working JPEG that edit / QC / previews / delivery read. Never delivered raw.
from .raw_decode import CAMERA_RAW_EXTS, raw_mime_for  # noqa: E402

_CAMERA_RAW_MIME_MARKERS = (
    "image/x-adobe-dng",
    "image/dng",
    "image/x-canon-cr2",
    "image/x-canon-cr3",
    "image/x-nikon-nef",
    "image/x-sony-arw",
    "image/x-fuji-raf",
    "image/x-olympus-orf",
    "image/x-panasonic-rw2",
    "image/x-panasonic-raw",
    "image/x-pentax-pef",
    "image/x-samsung-srw",
    "image/x-sigma-x3f",
    "image/x-raw",
    "image/x-dcraw",
)

# Storage per-file cap. Supabase Free = 50 MB for every object (cannot be
# raised on Free); R2 defaults to 5 GB. UPLOAD_MAX_FILE_MB overrides.
def is_video_upload(filename=None, mime_type=None, media_type=None) -> bool:
    """Reel / video master (gets the larger video cap)."""
    if _lower(media_type) in ("reel", "video", "cinematic"):
        return True
    mime = _lower(mime_type)
    name = _lower(filename)
    return mime.startswith("video/") or name.endswith(VIDEO_EXTS)


def upload_max_file_bytes(video: bool = False) -> int:
    from .upload_backend import max_file_bytes

    return max_file_bytes(video)


FILE_TOO_LARGE_RAW_MESSAGE = (
    "This file is over 50 MB. Upload the JPEG for now and keep the RAW."
)
FILE_TOO_LARGE_MESSAGE = "This file is over 50 MB. Export a smaller file and try again."


FILE_TOO_LARGE_VIDEO_MESSAGE = "This video is over 50 MB. Export a smaller file and try again."


def _limit_label(limit: int) -> str:
    mb = limit / (1024 * 1024)
    if mb >= 1024 and abs(mb / 1024 - round(mb / 1024)) < 0.01:
        return f"{int(round(mb / 1024))} GB"
    return f"{int(round(mb))} MB"


def file_too_large_message(filename=None, mime_type=None, limit_bytes=None, media_type=None) -> str:
    video = is_video_upload(filename, mime_type, media_type)
    limit = int(limit_bytes or upload_max_file_bytes(video))
    if video:
        return FILE_TOO_LARGE_VIDEO_MESSAGE.replace("50 MB", _limit_label(limit))
    mb = int(round(limit / (1024 * 1024)))
    if is_camera_raw_file(filename, mime_type):
        return FILE_TOO_LARGE_RAW_MESSAGE.replace("50 MB", f"{mb} MB")
    return FILE_TOO_LARGE_MESSAGE.replace("50 MB", f"{mb} MB")


def assert_upload_size(file_size=None, filename=None, mime_type=None, media_type=None) -> None:
    """413 before minting a URL the storage would reject at the end of the PUT."""
    try:
        size = int(file_size or 0)
    except (TypeError, ValueError):
        size = 0
    limit = upload_max_file_bytes(is_video_upload(filename, mime_type, media_type))
    if size > limit:
        from ..shared.errors import api_error

        api_error(
            413,
            file_too_large_message(filename, mime_type, limit, media_type),
            "file_too_large",
        )


def upload_content_type(filename=None, mime_type=None) -> str:
    """Stable Content-Type for storage. Browsers send '' / octet-stream for RAW."""
    mime = _lower(mime_type)
    if is_camera_raw_file(filename, None) and (not mime or mime == "application/octet-stream" or not mime.startswith("image/")):
        return raw_mime_for(filename)
    return str(mime_type or "") or "application/octet-stream"


VISIT_EXTS = (".splat", ".ply", ".spz", ".sog")


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


# Delimited "plan" token — OS media_kind / frontend mediaKind.js parity.
# "Floor Plan.png" / "level-1-plan.jpg" must be as_is floorplan (not HDR enhance).
# Must NOT match airplane/plant/replant or ground_floor_* listing stills.
_DELIMITED_PLAN_RE = re.compile(r"(?:^|[_\s-])plan(?:[_\s.-]|$)", re.IGNORECASE)


def has_delimited_plan_token(filename: Any = None) -> bool:
    """True when basename has a delimited plan token (not substring airplane/plant)."""
    name = _lower(filename).replace("\\", "/").split("/")[-1]
    if not name:
        return False
    return bool(_DELIMITED_PLAN_RE.search(name))

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
        "floor_plan": "floorplan",
        "roomplan": "floorplan",
        "room_plan": "floorplan",
        "cubicasa": "floorplan",
        # OS media_kind parity — GO/API may send media_type=plan
        "plan": "floorplan",
        "plans": "floorplan",
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
    # Exact basename plan/plans (CubiCasa / broker exports) — not substring
    # ("airplane", "explainer") so ordinary stills stay vendor_photo.
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if stem in ("plan", "plans"):
        return "floorplan"
    # Delimited plan token — "Floor Plan.png" / "level-1-plan.jpg" (OS/frontend parity).
    if has_delimited_plan_token(name):
        return "floorplan"
    if (
        "floorplan" in name
        or "floor_plan" in name
        or "floor-plan" in name
        or "cubicasa" in name
        or "roomplan" in name
        or "room_plan" in name
        or "room-plan" in name
    ):
        return "floorplan"
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
    if mt in ("drone", "reel", "video"):
        return True
    mime = _lower(mime_type)
    if mime.startswith("video/"):
        return True
    name = _lower(filename)
    return name.endswith(VIDEO_EXTS)


def is_pdf_file(filename: Any = None, mime_type: Any = None) -> bool:
    """True for PDF floor-plan / document uploads — never HDR enhance."""
    name = _lower(filename)
    if name.endswith(PDF_EXTS):
        return True
    mime = _lower(mime_type)
    return mime == "application/pdf" or mime.endswith("/pdf")


def is_camera_raw_file(filename: Any = None, mime_type: Any = None) -> bool:
    """True when filename ext or mime marks a camera RAW (decoded via raw_decode)."""
    name = _lower(filename)
    if name.endswith(CAMERA_RAW_EXTS):
        return True
    mime = _lower(mime_type)
    if not mime:
        return False
    if any(marker in mime for marker in _CAMERA_RAW_MIME_MARKERS):
        return True
    # Broad fallback: image/x-*raw* / vendor raw tokens without matching IMAGE_EXTS
    if "raw" in mime and mime.startswith("image/") and not name.endswith(IMAGE_EXTS):
        # Avoid false-positive on generic image/* ; require x- vendor or /raw
        if "/x-" in mime or mime.endswith("/raw") or "raw-" in mime:
            return True
    return False


def assert_listing_still_decodable(
    filename: Any = None,
    mime_type: Any = None,
    media_type: Any = None,
) -> None:
    """Kept for callers: camera RAW is accepted now (decoded to a working JPEG).

    Nothing is rejected here any more; size is checked by assert_upload_size.
    """
    return None


def is_photo_file(filename: Any = None, mime_type: Any = None, media_type: Any = None) -> bool:
    if is_visit_file(filename):
        return False
    if is_video_file(filename, mime_type, media_type):
        return False
    # PDF honesty: never treat as listing photo (PHOTO_TYPES includes "" so
    # missing media_type used to make plan.pdf look like a photo).
    if is_pdf_file(filename, mime_type):
        return False
    # Infer from filename when media_type absent — floorplan_/cubicasa_/aerial_
    # must not count as enhance-eligible photos.
    inferred = infer_media_type(filename, mime_type, media_type)
    if inferred in AS_IS_TYPES:
        return False
    mt = _lower(media_type)
    if mt in STAGING_TYPES or mt in PHOTO_TYPES:
        return True
    mime = _lower(mime_type)
    if mime.startswith("image/"):
        return True
    name = _lower(filename)
    if name.endswith(CAMERA_RAW_EXTS):
        return True
    return name.endswith(IMAGE_EXTS) or not name


def wants_staging(asset: Optional[dict] = None) -> bool:
    row = asset or {}
    if row.get("requires_staging") is True:
        return True
    return infer_media_type(row.get("filename"), row.get("mime_type"), row.get("media_type")) == "staging"


def delivery_route(asset: Optional[dict] = None) -> str:
    """How this upload should leave post-production.

    Order: skip → visit → staging → twilight → as_is (drone/reel/video/floorplan/PDF)
    → vendor_photo → as_is.

    skip          — no file
    visit         — .ply/.splat/.spz, not a listing still
    staging       — honest original (no virtual staging / no enhance)
    twilight      — real dusk shot; in-house enhance with apply_sky=False (no blue-sky / day-to-dusk)
    as_is         — drone / reel / video / floorplan / PDF, no photo edit (incl. aerial JPEGs)
    vendor_photo  — Fotello / Autoenhance / in-house enhance (may apply sky)

    media_type is not a DB column — always use infer_media_type (filename/mime/explicit)
    so DJI_/aerial_/*.mp4 / floorplan_* / CubiCasa / PDF never fall through to
    vendor_photo + sky replace.
    """
    row = asset or {}
    if not (row.get("file_url") or row.get("storage_path")):
        return "skip"
    if is_visit_file(row.get("filename")):
        return "visit"
    if wants_staging(row):
        return "staging"
    mt = infer_media_type(
        row.get("filename"),
        row.get("mime_type"),
        row.get("media_type"),
        file_url=row.get("file_url") or row.get("storage_path"),
    )
    if mt == "twilight":
        return "twilight"
    # Use inferred mt (not raw row media_type) — uploads table has no media_type col.
    if mt in AS_IS_TYPES:
        return "as_is"
    if is_video_file(row.get("filename"), row.get("mime_type"), row.get("media_type")):
        return "as_is"
    # PDF honesty — CubiCasa / broker PDF exports never enter HDR enhance.
    if is_pdf_file(row.get("filename"), row.get("mime_type")):
        return "as_is"
    if is_photo_file(row.get("filename"), row.get("mime_type"), row.get("media_type")):
        return "vendor_photo"
    return "as_is"


_DURABLE_PREFIXES = {
    "drone": "aerial_",
    "reel": "reel_",
    "twilight": "twilight_",
    "staging": "staging_",
    "floorplan": "floorplan_",
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


# Prefer .sog (compressed, streams per room), then .splat, .ply, .spz when multiple visit assets exist.
_VISIT_PICK_ORDER = (".sog", ".splat", ".ply", ".spz")


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
    """First Visit upload for property_visits registration (.splat > .ply > .spz).

    Skips QC-rejected and unreviewed (null/''/pending) Visit rows so a broken
    or never-reviewed .ply/.splat/.spz is never registered on the listing.
    Same honesty as listing-still delivery (has_qc_verdict) — inlined here to
    avoid a media_intake ↔ qc_media_kinds import cycle.
    """
    def _visit_qc_ready(u: Any) -> bool:
        status = str((u or {}).get("qc_status") or "").strip().lower()
        # pending/empty = DB default, not a verdict; rejected = held.
        return status not in ("", "pending", "rejected")

    visits = [
        u for u in (uploads or [])
        if _is_visit_upload_row(u) and _visit_qc_ready(u)
    ]
    if not visits:
        return None
    visits.sort(key=lambda u: _visit_pick_rank((u or {}).get("filename")))
    return visits[0]
