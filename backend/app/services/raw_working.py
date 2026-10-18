"""Working JPEG derivatives for camera RAW uploads (storage-aware).

The RAW is the master and is never delivered. Next to it we keep:
  work_<stem>.jpg   full-quality working copy (edit + bracket merge input)
  thumb_<stem>.jpg  small preview for the vendor app / review
Both are made once (POST /api/uploads/{id}/preview right after upload, or
lazily by post-production) and reused.
"""
from __future__ import annotations

import io
import logging
from typing import Any, Dict, Optional, Tuple

from PIL import Image

from .raw_decode import (
    RawDecodeError,
    is_camera_raw_name,
    preview_jpeg_path,
    raw_to_working_jpeg,
    sniff_camera_raw,
    working_jpeg_path,
)

logger = logging.getLogger(__name__)

PREVIEW_SIZE = (800, 800)


def is_raw_asset(asset: Optional[dict] = None, path: Any = None) -> bool:
    row = asset or {}
    for v in (row.get("filename"), path, row.get("file_url"), row.get("storage_path")):
        if v and is_camera_raw_name(v):
            return True
    return False


def _is_jpeg(b: Optional[bytes]) -> bool:
    return bool(b) and b[:3] == b"\xff\xd8\xff"


def _preview_bytes(jpeg: bytes) -> bytes:
    img = Image.open(io.BytesIO(jpeg))
    img = img.convert("RGB")
    img.thumbnail(PREVIEW_SIZE, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=78, optimize=True)
    return buf.getvalue()


async def ensure_working_jpeg(
    raw_path: str,
    *,
    raw_bytes: Optional[bytes] = None,
    force: bool = False,
) -> Tuple[Optional[bytes], Dict[str, Any]]:
    """Return (working JPEG bytes, meta). (None, {...}) when the RAW is missing.

    Raises RawDecodeError when the RAW downloads but cannot be decoded.
    """
    from .storage import download_bytes, upload_bytes

    work_path = working_jpeg_path(raw_path)
    thumb_path = preview_jpeg_path(raw_path)
    if not force and raw_bytes is None:
        cached = await download_bytes(work_path)
        if _is_jpeg(cached):
            return cached, {"work_path": work_path, "preview_path": thumb_path, "cached": True}
    data = raw_bytes if raw_bytes is not None else await download_bytes(raw_path)
    if not data:
        return None, {"work_path": work_path, "missing": True}
    jpeg, meta = raw_to_working_jpeg(data)
    meta = {**meta, "work_path": work_path, "preview_path": thumb_path, "cached": False}
    if not await upload_bytes(work_path, jpeg, content_type="image/jpeg", upsert=True):
        logger.warning("raw_working: could not store working copy %s (continuing in memory)", work_path)
        meta["work_path"] = None
    try:
        if not await upload_bytes(thumb_path, _preview_bytes(jpeg), content_type="image/jpeg", upsert=True):
            meta["preview_path"] = None
    except Exception as e:
        logger.warning("raw_working: preview failed %s: %s", thumb_path, e)
        meta["preview_path"] = None
    logger.info(
        "raw_working: %s -> %s %sx%s via %s",
        raw_path, meta.get("work_path"), meta.get("width"), meta.get("height"), meta.get("method"),
    )
    return jpeg, meta


async def load_still_source(asset: Optional[dict], path: Optional[str] = None) -> Optional[bytes]:
    """Bytes the editor should read for an upload: the working JPEG for RAW.

    None = source missing (caller's existing download-miss handling applies).
    Raises RawDecodeError for an undecodable RAW (caller holds it for review).
    """
    from .storage import download_bytes

    row = asset or {}
    p = path or row.get("file_url") or row.get("storage_path")
    if not p:
        return None
    if is_raw_asset(row, p):
        jpeg, _ = await ensure_working_jpeg(p)
        return jpeg
    data = await download_bytes(p)
    if data and sniff_camera_raw(data):
        # RAW under a non-RAW name (renamed .jpg, missing extension).
        jpeg, _ = await ensure_working_jpeg(p, raw_bytes=data)
        return jpeg
    return data


__all__ = [
    "RawDecodeError",
    "ensure_working_jpeg",
    "is_raw_asset",
    "load_still_source",
]
