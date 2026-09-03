"""Supabase Storage-backed file storage for Dispatch photo uploads."""
import base64
import io
import os
from typing import Optional, Tuple
from urllib.parse import quote

from PIL import Image

from ..shared.database import get_db

STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "brxs-deliverables")
SIGNED_URL_EXPIRY = int(os.getenv("SIGNED_URL_EXPIRY_SECONDS", "3600"))

_KEY_PREFIX = "bricks-go"

# QC only needs enough resolution to judge sharpness/exposure/composition —
# not the full-res original. Fetching this instead of the original in
# qc_agent.py cuts Supabase Storage egress per review substantially.
QC_VARIANT_SIZE: Tuple[int, int] = (1600, 1600)


def _make_thumbnail_bytes(image_bytes: bytes, size: Tuple[int, int] = (400, 300)) -> bytes:
    """Resize image to thumbnail and return JPEG bytes."""
    img = Image.open(io.BytesIO(image_bytes))
    img.thumbnail(size, Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=75)
    return buf.getvalue()


def make_qc_variant_bytes(image_bytes: bytes, size: Tuple[int, int] = QC_VARIANT_SIZE) -> bytes:
    """Resize image to QC review size and return JPEG bytes."""
    img = Image.open(io.BytesIO(image_bytes))
    img.thumbnail(size, Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=82)
    return buf.getvalue()


async def upload_file(
    file_bytes: bytes,
    job_id: str,
    upload_id: str,
    filename: str,
    mime_type: str,
) -> Tuple[Optional[str], Optional[str], str, Optional[str]]:
    """Upload file, thumbnail, and QC variant to Supabase Storage.

    Returns (file_path, thumbnail_path, thumbnail_b64_200, qc_variant_path).
    file_path / thumbnail_path / qc_variant_path are storage object paths —
    sign on demand via get_signed_download_url.
    thumbnail_b64_200 is a 200-char base64 preview for in-line display.
    qc_variant_path is None for non-image uploads (e.g. onboarding PDFs).
    """
    # filename is client-supplied (multipart Form field) -- strip any path
    # components before it goes into a storage object key. Supabase Storage
    # keys aren't a real filesystem, but an unsanitized "../other_job/x.jpg"
    # or embedded "/" still produces a key outside this upload's own
    # job_id/upload_id prefix, letting one upload collide with or shadow
    # another job's files in the same bucket.
    safe_filename = os.path.basename(filename).lstrip(".") or "file"
    prefix = f"{_KEY_PREFIX}/jobs/{job_id}/{upload_id}"
    file_path = f"{prefix}/{safe_filename}"
    thumb_path = f"{prefix}/thumb_{safe_filename.rsplit('.', 1)[0]}.jpg"
    qc_path = f"{prefix}/qc_{safe_filename.rsplit('.', 1)[0]}.jpg"

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

        try:
            qc_bytes = make_qc_variant_bytes(file_bytes)
            await bucket.upload(
                path=qc_path,
                file=qc_bytes,
                file_options={"content-type": "image/jpeg"},
            )
        except Exception:
            qc_path = None

    except Exception:
        try:
            thumb_bytes = _make_thumbnail_bytes(file_bytes)
            thumb_b64 = base64.b64encode(thumb_bytes).decode()[:200]
        except Exception:
            thumb_b64 = base64.b64encode(file_bytes[:150]).decode()[:200]
        return None, None, thumb_b64, None

    return file_path, thumb_path, thumb_b64, qc_path


async def get_signed_download_url(storage_path: str, expiry: int = SIGNED_URL_EXPIRY) -> Optional[str]:
    """Return a Supabase signed URL valid for `expiry` seconds."""
    if not storage_path:
        return None
    try:
        db = await get_db()
        result = await db.storage.from_(STORAGE_BUCKET).create_signed_url(storage_path, expiry)
        return result.get("signedURL") or result.get("signedUrl") or result.get("signed_url")
    except Exception:
        return None


def _signed_upload_fields(result) -> Optional[dict]:
    """Normalize storage3 / REST signed-upload payloads to {path, token, signed_url}."""
    if result is None:
        return None
    data = result if isinstance(result, dict) else getattr(result, "__dict__", None)
    if not data:
        return None
    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    path = inner.get("path") or inner.get("Key") or inner.get("key")
    token = inner.get("token")
    signed = (
        inner.get("signedURL")
        or inner.get("signedUrl")
        or inner.get("signed_url")
        or inner.get("url")
    )
    if signed and not token and "token=" in str(signed):
        token = str(signed).split("token=", 1)[-1].split("&", 1)[0]
    if not path and not signed:
        return None
    return {"path": path, "token": token, "signed_url": signed}


async def create_signed_upload_url(storage_path: str) -> Optional[dict]:
    """Mint a short-lived signed upload URL for an object in brxs-deliverables.

    Used by phone capture so JPEG bytes go straight to Storage and never through
    POST /api/uploads (professional stills, 4MB Vercel cap).
    Returns {path, token, signed_url} or None.
    """
    if not storage_path:
        return None
    try:
        db = await get_db()
        bucket = db.storage.from_(STORAGE_BUCKET)
        result = await bucket.create_signed_upload_url(storage_path)
        parsed = _signed_upload_fields(result)
        if parsed:
            parsed["path"] = parsed.get("path") or storage_path
            return parsed
    except Exception:
        pass

    # REST fallback — same service key get_db() already uses.
    try:
        import httpx

        url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_KEY", ""))
        if not url or not key:
            return None
        encoded = "/".join(quote(part, safe="") for part in storage_path.split("/") if part)
        endpoint = f"{url}/storage/v1/object/upload/sign/{STORAGE_BUCKET}/{encoded}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                endpoint,
                headers={"Authorization": f"Bearer {key}", "apikey": key},
            )
        if resp.status_code >= 400:
            return None
        parsed = _signed_upload_fields(resp.json())
        if not parsed:
            return None
        parsed["path"] = parsed.get("path") or storage_path
        signed = parsed.get("signed_url") or ""
        if signed.startswith("/"):
            parsed["signed_url"] = f"{url}/storage/v1{signed}"
        return parsed
    except Exception:
        return None



def resize_image_bytes(image_bytes: bytes, max_side: int = 1280, quality: int = 75) -> bytes:
    """Downscale an image to JPEG for Gemini / tour scenes."""
    img = Image.open(io.BytesIO(image_bytes))
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


async def download_bytes(storage_path: str) -> Optional[bytes]:
    """Fetch object bytes from brxs-deliverables. Signed-URL fallback if download() fails."""
    if not storage_path:
        return None
    try:
        db = await get_db()
        data = await db.storage.from_(STORAGE_BUCKET).download(storage_path)
        if data:
            return bytes(data)
    except Exception:
        pass
    url = await get_signed_download_url(storage_path, expiry=120)
    if not url:
        return None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
    except Exception:
        return None


async def upload_bytes(
    storage_path: str,
    data: bytes,
    content_type: str = "application/octet-stream",
    upsert: bool = True,
) -> bool:
    """Upload raw bytes to brxs-deliverables. Returns True on success."""
    if not storage_path or not data:
        return False
    try:
        db = await get_db()
        bucket = db.storage.from_(STORAGE_BUCKET)
        options = {
            "content-type": content_type or "application/octet-stream",
            "upsert": "true" if upsert else "false",
        }
        await bucket.upload(path=storage_path, file=data, file_options=options)
        return True
    except Exception:
        return False
