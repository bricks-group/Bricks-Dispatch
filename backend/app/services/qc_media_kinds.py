"""Automatic QC for client deliverables that are not listing photos.

Kam rule: every client deliverable goes through automatic QC that can hold a
file back. Listing stills (HDR, drone stills, staging, twilight, reel stills)
already go through the OpenCV/vision photo check in qc_agent. Three kinds did
not get an honest check:

- Floor plans (PNG/JPEG/PDF). The photo check cannot decode a PDF, so every
  PDF floor plan was held as "unreadable_image"; a clean PNG export was sent
  to Review for low file size / blown white background. Floor plans now get a
  plan check instead: the file must open and must not be blank.
- Video (drone / reel mp4/mov/webm/avi). Videos were passed without being
  looked at. A video is now held when it is empty or is not a video
  container at all (e.g. an error page or a still saved as .mp4). Only the
  first bytes are fetched; nothing is re-encoded or edited.
- Visit / 3D (.ply / .splat / .spz). Were auto-passed as "Non-image asset".
  A Visit file is now held when it is empty or is not a Visit container
  (error page / still / wrong bytes). No edit, no re-encode, no viewer —
  first bytes only. Polycam URL / link-only Visit rows stay skipped.

Floor plan / video / Visit that cannot be fetched used to fail open (pass
with a skip note), so an unreviewed file could reach the client — same honesty
hole listing stills closed with image_unavailable. Fetch miss / empty head now
holds with rejected + image_unavailable; late-fold prune and hold notices
already understand that verdict. Old skip-pass rows ("QC skipped (... unavailable)")
are re-checked on the next delivery/late-fold QC pass (same narrow pattern as
Non-image asset / unreadable_image) — no DB migration.
"""
from __future__ import annotations

import io
import logging
from typing import Optional, Tuple

import httpx

from .media_intake import (
    VIDEO_EXTS,
    infer_media_type,
    is_pdf_file,
    is_visit_file,
)

logger = logging.getLogger(__name__)

VIDEO_HEAD_BYTES = 64 * 1024

# Pixels that differ from the page background by more than this count as ink.
_FLOORPLAN_INK_DELTA = 40
# Below this ink share the plan is blank (a real plan's walls/labels are far above).
_FLOORPLAN_MIN_INK = 0.002

# ISO base media (mp4/m4v/mov) top-level box types that can open a file.
_ISO_BOX_TYPES = (b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide", b"pnot")


def qc_kind_for_upload(upload: Optional[dict]) -> str:
    """'visit' | 'video' | 'floorplan' | 'still' for one uploads row."""
    row = upload if isinstance(upload, dict) else {}
    filename = str(row.get("filename") or "")
    name = filename.lower()
    file_url = row.get("file_url") or row.get("processed_url")
    if is_visit_file(name) or str(row.get("provider") or "").strip().lower() == "visit":
        return "visit"
    if name.endswith(VIDEO_EXTS) or str(row.get("mime_type") or "").lower().startswith("video/"):
        return "video"
    if is_pdf_file(filename, row.get("mime_type")):
        return "floorplan"
    if infer_media_type(filename, row.get("mime_type"), None, file_url) == "floorplan":
        return "floorplan"
    return "still"


# public.uploads.qc_status column default — "not reviewed yet", not a verdict.
QC_UNREVIEWED = ("", "pending")


def has_qc_verdict(upload: Optional[dict]) -> bool:
    """True when QC already recorded a verdict (passed / flagged / rejected / legacy).

    public.uploads.qc_status defaults to 'pending' on insert (Bricks Canada).
    A truthy-string check read that default as an existing verdict, so
    run_qc_for_job and late-fold skipped every fresh upload and listing stills
    reached the client never reviewed. Null / '' / 'pending' = not reviewed;
    any other value (incl. legacy approved/accepted) is kept as before.
    """
    row = upload if isinstance(upload, dict) else {}
    return str(row.get("qc_status") or "").strip().lower() not in QC_UNREVIEWED


def is_legacy_unreadable_floorplan_hold(upload: Optional[dict]) -> bool:
    """True when a floor plan/PDF was held by the old listing-photo QC as unreadable_image.

    Those rows keep qc_status=rejected, so late-fold would otherwise never re-run QC.
    Only this exact prior reason is eligible — real photo holds and honest floor-plan
    holds (blank, not a PDF, etc.) stay untouched.
    """
    row = upload if isinstance(upload, dict) else {}
    if str(row.get("qc_status") or "").strip().lower() != "rejected":
        return False
    notes = str(row.get("qc_notes") or "").lower()
    if "unreadable_image" not in notes:
        return False
    return qc_kind_for_upload(row) == "floorplan"


def is_legacy_non_image_asset_pass(upload: Optional[dict]) -> bool:
    """True when Visit/video was auto-passed as "Non-image asset — QC skipped".

    Before Visit container QC (feae411) and video container QC (b154726), both
    kinds were stamped passed without a bytes check. Those rows keep
    qc_status=passed, so late-fold / run_qc_for_job would otherwise never hold a
    broken .ply/.splat/.spz or a still-saved-as-.mp4. Only this exact prior note
    is eligible — honest Visit/video container passes and photo verdicts stay.
    """
    row = upload if isinstance(upload, dict) else {}
    if str(row.get("qc_status") or "").strip().lower() != "passed":
        return False
    notes = str(row.get("qc_notes") or "").lower()
    if "non-image asset" not in notes:
        return False
    return qc_kind_for_upload(row) in ("visit", "video")



def is_legacy_unavailable_skip_pass(upload: Optional[dict]) -> bool:
    """True when a deliverable was auto-passed as QC skipped (... unavailable).

    Before image_unavailable holds (586427b stills; cb66e7e Visit/video/floorplan),
    fetch miss stamped passed with a skip note so an unreviewed file could reach
    the client. Those rows keep qc_status=passed, so late-fold / run_qc_for_job
    would otherwise never re-hold them. Only these exact prior notes are eligible
    — honest container/photo passes, provider/error skips, and real holds stay.
    """
    row = upload if isinstance(upload, dict) else {}
    if str(row.get("qc_status") or "").strip().lower() != "passed":
        return False
    notes = str(row.get("qc_notes") or "").lower()
    kind = qc_kind_for_upload(row)
    if "qc skipped (image unavailable)" in notes:
        return kind == "still"
    if "qc skipped (visit unavailable)" in notes:
        return kind == "visit"
    if "qc skipped (video unavailable)" in notes:
        return kind == "video"
    if "qc skipped (floor plan unavailable)" in notes:
        return kind == "floorplan"
    return False


# Exact note post_production writes when enhance/listing_export detects faces
# and pre-stamps qc_status=flagged before QC ever runs (bracket primary,
# twilight, vendor_photo single-frame). QC's own people note is appended to
# its verdict text and never carries "; qc_face_count=".
ENHANCE_PEOPLE_PRESTAMP_PREFIX = (
    "proptx 5.01(d) persons/faces — mls_quarantine soft-fail; qc_face_count="
)


def is_enhance_people_prestamp(upload: Optional[dict]) -> bool:
    """True when a listing still's only verdict is the enhance-time people flag.

    post_production stamps qc_status=flagged when faces are detected during
    auto-edit. run_qc_for_job / late-fold read that as an existing verdict
    (has_qc_verdict), so crooked / dark / blurry checks never ran on the
    delivered edit and "flagged" still reached the client. QC re-detects
    people itself (merge_people_into_qc / _qc_payload), so re-reviewing keeps
    the quarantine flag and adds the real quality verdict.
    """
    row = upload if isinstance(upload, dict) else {}
    if str(row.get("qc_status") or "").strip().lower() != "flagged":
        return False
    notes = str(row.get("qc_notes") or "").strip().lower()
    if not notes.startswith(ENHANCE_PEOPLE_PRESTAMP_PREFIX):
        return False
    return qc_kind_for_upload(row) == "still"


def _verdict(status: str, score: int, notes: str, source: str) -> dict:
    return {
        "qc_status": status,
        "qc_score": score,
        "qc_notes": notes[:500],
        "qc_source": source,
    }


def _media_unavailable_hold(detail: str) -> dict:
    """Hold when Visit / video / floor plan bytes cannot be fetched for QC.

    Same spirit as listing-still image_unavailable: never pass an unreviewed
    deliverable. Late-fold prune + hold notices already understand rejected +
    image_unavailable.
    """
    detail = (detail or "could not review media for QC").strip()
    return _verdict("rejected", 0, f"image_unavailable — {detail}", "unavailable")


def check_floorplan_bytes(data: Optional[bytes], filename: str = "", mime_type: str = "") -> dict:
    """Pass a floor plan that opens and has linework; hold one that is empty,
    unreadable, or blank. Photo checks (exposure, sharpness, tilt, size) do
    not apply to a plan drawing."""
    if not data:
        return _verdict("rejected", 0, "Floor plan file is empty.", "floorplan_check")

    # PDF header must appear within the first 1024 bytes (PDF spec).
    if b"%PDF-" in data[:1024]:
        # A complete PDF ends with the %%EOF marker (ISO 32000 7.5.5); readers
        # look for it in the last 1024 bytes. Missing = upload/write cut off
        # mid-file — the client would get a plan that will not open.
        if b"%%EOF" not in data[-1024:]:
            return _verdict(
                "rejected",
                0,
                "Floor plan PDF is truncated (no %%EOF end marker) — re-upload the full file.",
                "floorplan_check",
            )
        return _verdict("passed", 100, "Floor plan PDF opens.", "floorplan_check")
    named_pdf = is_pdf_file(filename, mime_type)

    try:
        import numpy as np
        from PIL import Image

        from .image_io import open_rgb

        img = Image.open(io.BytesIO(data))
        img.load()
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            # Transparent plan exports: flatten on white so the page is not read as black.
            rgba = img.convert("RGBA")
            page = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            page.alpha_composite(rgba)
            gray_img = page.convert("L")
        else:
            gray_img = open_rgb(data).convert("L")
        gray_img.thumbnail((4096, 4096))
        gray = np.asarray(gray_img, dtype=np.int16)
    except Exception as e:
        if named_pdf:
            why = "Floor plan is not a readable PDF (no PDF header)."
        else:
            why = f"Floor plan image could not be opened ({type(e).__name__})."
        return _verdict("rejected", 0, why, "floorplan_check")

    if gray.size == 0:
        return _verdict("rejected", 0, "Floor plan image is empty.", "floorplan_check")
    background = int(np.median(gray))
    ink = float(np.mean(np.abs(gray - background) > _FLOORPLAN_INK_DELTA))
    if ink < _FLOORPLAN_MIN_INK:
        return _verdict(
            "rejected",
            0,
            f"Floor plan looks blank (no linework; ink {ink:.4f}).",
            "floorplan_check",
        )
    return _verdict("passed", 100, f"Floor plan opens with linework (ink {ink:.3f}).", "floorplan_check")


def _iso_truncation_reason(head: bytes, total_size: Optional[int]) -> Optional[str]:
    """Why an MP4/MOV cannot play, judged from top-level box headers only.

    An MP4/MOV needs a ``moov`` box (the index) to open. A camera that lost
    power mid-record, or an upload/Blob write cut off, leaves ``ftyp`` +
    ``mdat`` with no ``moov`` — or an ``mdat`` whose declared size runs past
    the end of the file. Both passed the old magic-bytes check and reached
    the client as a reel/drone video that will not play.

    Walks the top-level box chain while each header is inside ``head``
    (ISO/IEC 14496-12 §4.2: 32-bit size, 1 = 64-bit largesize, 0 = to EOF).
    Only says broken when the chain provably reaches the end of the file
    without a ``moov`` or overruns it. A ``moov`` after ``mdat`` whose
    header lies beyond the fetched head is unknown → None (pass, as before).
    Unknown/non-printable box types stop the walk (None). Needs total_size
    for the end-of-file rules; without it only a size-0 box can decide.
    """
    off = 0
    seen_moov = False
    n = len(head)
    while off + 8 <= n:
        size = int.from_bytes(head[off:off + 4], "big")
        typ = head[off + 4:off + 8]
        if not all(0x20 <= b <= 0x7E for b in typ):
            return None
        hdr = 8
        if size == 1:
            if off + 16 > n:
                return None
            size = int.from_bytes(head[off + 8:off + 16], "big")
            hdr = 16
        elif size == 0:
            # Box extends to end of file: nothing can follow it.
            if typ == b"moov" or seen_moov:
                return None
            return "is incomplete (no moov index — recording or upload cut off; it will not play) — re-upload the full file."
        if size < hdr:
            return None
        if typ == b"moov":
            seen_moov = True
        end = off + size
        if total_size is not None:
            if end > total_size:
                return (
                    f"is truncated ({typ.decode('ascii', 'replace')} box runs past the end of the file "
                    "— upload cut off; it will not play) — re-upload the full file."
                )
            if end == total_size:
                if seen_moov:
                    return None
                return "is incomplete (no moov index — recording or upload cut off; it will not play) — re-upload the full file."
        off = end
    return None


def check_video_head(head: Optional[bytes], total_size: Optional[int] = None) -> dict:
    """Hold a video that is empty or is not a video container. Never judges
    content (no auto-edit claim); just that the delivered file is a video."""
    if total_size == 0 or not head:
        return _verdict("rejected", 0, "Video file is empty.", "video_check")
    if len(head) < 12:
        return _verdict("rejected", 0, "Video file is truncated (too small to be a video).", "video_check")
    if head[4:8] in _ISO_BOX_TYPES:
        broken = _iso_truncation_reason(head, total_size)
        if broken:
            return _verdict("rejected", 0, f"Video file {broken}", "video_check")
        return _verdict("passed", 100, "Video container OK (MP4/MOV).", "video_check")
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return _verdict("passed", 100, "Video container OK (WebM/MKV).", "video_check")
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return _verdict("passed", 100, "Video container OK (AVI).", "video_check")
    snippet = head[:64].lstrip().lower()
    if snippet.startswith((b"<!doctype", b"<html", b"<?xml", b"{")):
        why = "an error page/text, not a video"
    elif head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n":
        why = "a still image, not a video"
    else:
        why = "not a recognized video container"
    return _verdict("rejected", 0, f"Video file is {why}.", "video_check")


def check_visit_head(
    head: Optional[bytes],
    total_size: Optional[int] = None,
    filename: str = "",
) -> dict:
    """Hold a Visit/3D file that is empty or not a .ply/.splat/.spz container.

    Never judges scan quality (no editor / no viewer); just that the delivered
    file is a Visit asset, not an error page or a still saved with a Visit
    extension.
    """
    name = (filename or "").lower()
    if total_size == 0 or not head:
        return _verdict("rejected", 0, "Visit file is empty.", "visit_check")
    if len(head) < 4:
        return _verdict(
            "rejected",
            0,
            "Visit file is truncated (too small to be a Visit asset).",
            "visit_check",
        )

    snippet = head[:64].lstrip().lower()
    if snippet.startswith((b"<!doctype", b"<html", b"<?xml", b"{")):
        return _verdict(
            "rejected", 0, "Visit file is an error page/text, not a Visit asset.", "visit_check"
        )
    if head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n":
        return _verdict(
            "rejected", 0, "Visit file is a still image, not a Visit asset.", "visit_check"
        )
    if b"%PDF-" in head[:1024]:
        return _verdict(
            "rejected", 0, "Visit file is a PDF, not a Visit asset.", "visit_check"
        )

    # PLY: ASCII header starts with "ply" then whitespace/newline.
    if name.endswith(".ply") or head[:3].lower() == b"ply":
        if head[:3].lower() == b"ply" and (len(head) == 3 or head[3:4] in b" \t\r\n"):
            return _verdict("passed", 100, "Visit container OK (PLY).", "visit_check")
        return _verdict(
            "rejected", 0, "Visit file is not a readable PLY (missing ply header).", "visit_check"
        )

    # SPZ: Niantic v4 "NGSP" magic, or legacy gzip-wrapped v1–3.
    if name.endswith(".spz") or head[:4] == b"NGSP" or head[:2] == b"\x1f\x8b":
        if head[:4] == b"NGSP" or head[:2] == b"\x1f\x8b":
            return _verdict("passed", 100, "Visit container OK (SPZ).", "visit_check")
        return _verdict(
            "rejected", 0, "Visit file is not a recognized SPZ container.", "visit_check"
        )

    # .splat (antimatter15): headerless fixed 32-byte records. No magic —
    # hold when size is known and not a multiple of 32, or looks like junk.
    if name.endswith(".splat"):
        if total_size is not None:
            if total_size < 32:
                return _verdict(
                    "rejected",
                    0,
                    "Visit file is truncated (too small to be a splat).",
                    "visit_check",
                )
            if total_size % 32 != 0:
                return _verdict(
                    "rejected",
                    0,
                    "Visit file is not a recognized splat container (size not a multiple of 32).",
                    "visit_check",
                )
        # Enough head bytes to be at least one record, and not text-like.
        if len(head) >= 32:
            return _verdict("passed", 100, "Visit container OK (splat).", "visit_check")
        return _verdict(
            "rejected",
            0,
            "Visit file is truncated (too small to be a splat).",
            "visit_check",
        )

    return _verdict(
        "rejected", 0, "Visit file is not a recognized Visit container.", "visit_check"
    )


async def run_visit_qc(upload_id: str, url: str, filename: str = "") -> dict:
    head, total = await fetch_head(url)
    if head is None:
        logger.info(
            "QC upload %s: image_unavailable — holding (Visit bytes not fetched)",
            upload_id,
        )
        return _media_unavailable_hold(
            "could not fetch Visit for QC review"
        )
    verdict = check_visit_head(head, total, filename)
    logger.info("QC upload %s: Visit %s", upload_id, verdict["qc_status"])
    return verdict


def _total_from_headers(resp: httpx.Response) -> Optional[int]:
    cr = resp.headers.get("content-range") or ""
    if "/" in cr:
        tail = cr.rsplit("/", 1)[-1].strip()
        if tail.isdigit():
            return int(tail)
    if resp.status_code == 200:
        cl = resp.headers.get("content-length") or ""
        if cl.isdigit():
            return int(cl)
    return None


async def fetch_head(url: str, n: int = VIDEO_HEAD_BYTES) -> Tuple[Optional[bytes], Optional[int]]:
    """First n bytes + total size (when the server says). (None, None) on fetch failure."""
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            async with client.stream("GET", url, headers={"Range": f"bytes=0-{n - 1}"}) as resp:
                if resp.status_code >= 400:
                    if resp.status_code == 416:
                        return b"", 0  # range not satisfiable → zero-byte object
                    logger.error("QC: video head fetch HTTP %s", resp.status_code)
                    return None, None
                total = _total_from_headers(resp)
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) >= n:
                        break
                return bytes(buf[:n]), total
    except Exception as e:
        logger.error("QC: failed to fetch video head: %s", type(e).__name__)
        return None, None


async def run_video_qc(upload_id: str, url: str) -> dict:
    head, total = await fetch_head(url)
    if head is None:
        logger.info(
            "QC upload %s: image_unavailable — holding (video bytes not fetched)",
            upload_id,
        )
        return _media_unavailable_hold(
            "could not fetch video for QC review"
        )
    verdict = check_video_head(head, total)
    logger.info("QC upload %s: video %s", upload_id, verdict["qc_status"])
    return verdict


async def run_floorplan_qc(upload_id: str, url: str, filename: str = "", mime_type: str = "") -> dict:
    from .qc_agent import _fetch_image_bytes

    data = await _fetch_image_bytes(url)
    if data is None:
        logger.info(
            "QC upload %s: image_unavailable — holding (floor plan bytes not fetched)",
            upload_id,
        )
        return _media_unavailable_hold(
            "could not fetch floor plan for QC review"
        )
    verdict = check_floorplan_bytes(data, filename, mime_type)
    logger.info("QC upload %s: floor plan %s", upload_id, verdict["qc_status"])
    return verdict
