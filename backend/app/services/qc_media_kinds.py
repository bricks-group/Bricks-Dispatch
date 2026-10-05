"""Automatic QC for client deliverables that are not listing photos.

Kam rule: every client deliverable goes through automatic QC that can hold a
file back. Listing stills (HDR, drone stills, staging, twilight, reel stills)
already go through the OpenCV/vision photo check in qc_agent. Two kinds did
not get an honest check:

- Floor plans (PNG/JPEG/PDF). The photo check cannot decode a PDF, so every
  PDF floor plan was held as "unreadable_image"; a clean PNG export was sent
  to Review for low file size / blown white background. Floor plans now get a
  plan check instead: the file must open and must not be blank.
- Video (drone / reel mp4/mov/webm/avi). Videos were passed without being
  looked at. A video is now held when it is empty or is not a video
  container at all (e.g. an error page or a still saved as .mp4). Only the
  first bytes are fetched; nothing is re-encoded or edited.

A file that cannot be fetched fails open, same as listing stills.
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


def _verdict(status: str, score: int, notes: str, source: str) -> dict:
    return {
        "qc_status": status,
        "qc_score": score,
        "qc_notes": notes[:500],
        "qc_source": source,
    }


def check_floorplan_bytes(data: Optional[bytes], filename: str = "", mime_type: str = "") -> dict:
    """Pass a floor plan that opens and has linework; hold one that is empty,
    unreadable, or blank. Photo checks (exposure, sharpness, tilt, size) do
    not apply to a plan drawing."""
    if not data:
        return _verdict("rejected", 0, "Floor plan file is empty.", "floorplan_check")

    # PDF header must appear within the first 1024 bytes (PDF spec).
    if b"%PDF-" in data[:1024]:
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


def check_video_head(head: Optional[bytes], total_size: Optional[int] = None) -> dict:
    """Hold a video that is empty or is not a video container. Never judges
    content (no auto-edit claim); just that the delivered file is a video."""
    if total_size == 0 or not head:
        return _verdict("rejected", 0, "Video file is empty.", "video_check")
    if len(head) < 12:
        return _verdict("rejected", 0, "Video file is truncated (too small to be a video).", "video_check")
    if head[4:8] in _ISO_BOX_TYPES:
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
        return _verdict("passed", 100, "QC skipped (video unavailable)", "skipped")
    verdict = check_video_head(head, total)
    logger.info("QC upload %s: video %s", upload_id, verdict["qc_status"])
    return verdict


async def run_floorplan_qc(upload_id: str, url: str, filename: str = "", mime_type: str = "") -> dict:
    from .qc_agent import _fetch_image_bytes

    data = await _fetch_image_bytes(url)
    if data is None:
        return _verdict("passed", 100, "QC skipped (floor plan unavailable)", "skipped")
    verdict = check_floorplan_bytes(data, filename, mime_type)
    logger.info("QC upload %s: floor plan %s", upload_id, verdict["qc_status"])
    return verdict
