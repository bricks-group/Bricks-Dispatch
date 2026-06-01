"""Supabase Storage-backed file storage for Dispatch photo uploads."""
import base64
import io
import os
from typing import Optional, Tuple

from PIL import Image

from ..shared.database import get_db

STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "brxs-deliverables")
SIGNED_URL_EXPIRY = int(os.getenv("SIGNED_URL_EXPIRY_SECONDS", "86400"))

_KEY_PREFIX = "bricks-dispatch"


def _make_thumbnail_bytes(image_bytes: bytes, size: Tuple[int, int] = (400, 300)) -> bytes:
    """Resize image to thumbnail and return JPEG bytes."""
    img = Image.open(io.BytesIO(image_bytes))
    img.thumbnail(size, Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=75)
    return buf.getvalue()


async def upload_file(
    file_bytes: bytes,
    job_id: str,
    upload_id: str,
    filename: str,
    mime_type: str,
) -> Tuple[Optional[str], Optional[str], str]:
    """Upload file and thumbnail to Supabase Storage.

    Returns (file_path, thumbnail_path, thumbnail_b64_200).
    file_path / thumbnail_path are storage object paths — sign on demand via get_signed_download_url.
    thumbnail_b64_200 is a 200-char base64 preview for in-line display.
    """
    prefix = f"{_KEY_PREFIX}/jobs/{job_id}/{upload_id}"
    file_path = f"{prefix}/{filename}"
    thumb_path = f"{prefix}/thumb_{filename.rsplit('.', 1)[0]}.jpg"

    try:
        db = await get_db()
        bucket = db.storage.from_(STORAGE_BUCKET)

        await bucket.upload(
            path=file_path,
            file=file_bytes,
            file_options={"content-type": mime_type or "application/octet-stream"},
        )

        try:
            thumb_bytes = _make_thumbnail_bytes(file_bytes)
            await bucket.upload(
                path=thumb_path,
                file=thumb_bytes,
                file_options={"content-type": "image/jpeg"},
            )
            thumb_b64 = base64.b64encode(thumb_bytes).decode()[:200]
        except Exception:
            thumb_path = None
            thumb_b64 = base64.b64encode(file_bytes[:150]).decode()[:200]

    except Exception:
        try:
            thumb_bytes = _make_thumbnail_bytes(file_bytes)
            thumb_b64 = base64.b64encode(thumb_bytes).decode()[:200]
        except Exception:
            thumb_b64 = base64.b64encode(file_bytes[:150]).decode()[:200]
        return None, None, thumb_b64

    return file_path, thumb_path, thumb_b64


async def get_signed_download_url(storage_path: str, expiry: int = SIGNED_URL_EXPIRY) -> Optional[str]:
    """Return a Supabase signed URL valid for `expiry` seconds."""
    if not storage_path:
        return None
    try:
        db = await get_db()
        result = await db.storage.from_(STORAGE_BUCKET).create_signed_url(storage_path, expiry)
        return result.get("signedURL") or result.get("signed_url")
    except Exception:
        return None
