"""Supabase Storage-backed file storage for Dispatch photo uploads.

Fair-Use fallback: when Canada Supabase Storage/Auth is 402 (or any failure),
and BLOB_READ_WRITE_TOKEN is set, write/read via private Vercel Blob and mint
HMAC-signed proxy URLs under the Dispatch API so GO clients never need the RW token.
"""
import base64
import hashlib
import hmac
import io
import logging
import os
import time
from typing import Optional, Tuple, Union
from urllib.parse import quote

from PIL import Image

from ..shared.database import get_db

STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "brxs-deliverables")
SIGNED_URL_EXPIRY = int(os.getenv("SIGNED_URL_EXPIRY_SECONDS", "3600"))

_KEY_PREFIX = "bricks-go"
_logger = logging.getLogger("storage")

# QC only needs enough resolution to judge sharpness/exposure/composition —
# not the full-res original. Fetching this instead of the original in
# qc_agent.py cuts Supabase Storage egress per review substantially.
QC_VARIANT_SIZE: Tuple[int, int] = (1600, 1600)

BLOB_API_URL = os.getenv("VERCEL_BLOB_API_URL", "https://vercel.com/api/blob")
DEFAULT_PUT_EXPIRY = 3600
DEFAULT_GET_EXPIRY = 3600
MAX_GET_EXPIRY = 7 * 24 * 3600


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


# ---------------------------------------------------------------------------
# Fair-Use Vercel Blob fallback (private store + HMAC Dispatch proxy URLs)
# ---------------------------------------------------------------------------

def _blob_token() -> str:
    return (os.environ.get("BLOB_READ_WRITE_TOKEN") or "").strip()


def blob_fallback_enabled() -> bool:
    return bool(_blob_token())


def _hmac_secret() -> bytes:
    raw = (
        os.environ.get("CAPTURE_PUT_SECRET")
        or os.environ.get("SUPABASE_JWT_SECRET")
        or (_blob_token()[:32] if _blob_token() else "")
    )
    return (raw or "bricks-capture-dev-secret").encode("utf-8")


def _normalize_storage_path(storage_path: str) -> str:
    path = (storage_path or "").strip().lstrip("/")
    if not path or ".." in path.split("/"):
        raise ValueError("invalid storage path")
    return path


def _dispatch_public_base() -> str:
    return (
        os.environ.get("DISPATCH_PUBLIC_URL")
        or os.environ.get("EXPO_PUBLIC_BACKEND_URL")
        or "https://dispatch-api.brxs.ca"
    ).rstrip("/")


def mint_capture_hmac(method: str, path: str, exp: int) -> str:
    """HMAC-SHA256 hex of ``METHOD:path:exp`` (path without leading slash)."""
    method_u = (method or "").upper()
    path_n = _normalize_storage_path(path)
    msg = f"{method_u}:{path_n}:{int(exp)}".encode("utf-8")
    return hmac.new(_hmac_secret(), msg, hashlib.sha256).hexdigest()


def verify_capture_hmac(method: str, path: str, exp: Union[int, str], sig: str) -> bool:
    """Return True when sig matches and exp is still in the future."""
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_i < int(time.time()):
        return False
    if not sig or not isinstance(sig, str):
        return False
    expected = mint_capture_hmac(method, path, exp_i)
    return hmac.compare_digest(expected, sig.strip().lower())


def build_hmac_proxy_url(method: str, storage_path: str, expiry_seconds: int) -> str:
    """Public Dispatch API URL the GO client can PUT/GET without a Blob token."""
    path_n = _normalize_storage_path(storage_path)
    exp = int(time.time()) + max(60, int(expiry_seconds))
    sig = mint_capture_hmac(method, path_n, exp)
    encoded = quote(path_n, safe="/")
    kind = "blob-put" if method.upper() == "PUT" else "blob-get"
    return f"{_dispatch_public_base()}/api/capture/{kind}/{encoded}?exp={exp}&sig={sig}"


def _normalize_store_id(store_id: str) -> str:
    """Match @vercel/blob normalizeStoreId — hostname uses id without store_ prefix."""
    sid = (store_id or "").strip()
    if sid.startswith("store_"):
        return sid[len("store_"):]
    return sid


def _store_id_from_token(token: str) -> Optional[str]:
    """Parse store id from vercel_blob_rw_<storeId>_<secret> (SDK: 4th _-segment)."""
    explicit = (os.environ.get("BLOB_STORE_ID") or "").strip()
    if explicit:
        return _normalize_store_id(explicit)
    parts = token.split("_")
    # vercel_blob_rw_<storeId>_<secret...>
    if len(parts) >= 5 and parts[0] == "vercel" and parts[1] == "blob" and parts[2] == "rw":
        return _normalize_store_id(parts[3])
    return None


def _private_blob_object_url(pathname: str) -> Optional[str]:
    token = _blob_token()
    if not token:
        return None
    store_id = _store_id_from_token(token)
    if not store_id:
        return None
    return f"https://{store_id}.private.blob.vercel-storage.com/{quote(pathname, safe='/')}"


async def _blob_put(
    storage_path: str,
    data: bytes,
    content_type: str = "application/octet-stream",
    upsert: bool = True,
) -> bool:
    """Upload bytes to private Vercel Blob via REST. Pathname stays stable."""
    token = _blob_token()
    if not token or not storage_path or not data:
        return False
    try:
        import httpx

        path_n = _normalize_storage_path(storage_path)
        params = quote(path_n, safe="")
        url = f"{BLOB_API_URL.rstrip('/')}/?pathname={params}"
        headers = {
            "Authorization": f"Bearer {token}",
            "x-vercel-blob-access": "private",
            "x-add-random-suffix": "0",
            "x-content-type": content_type or "application/octet-stream",
            "Content-Type": content_type or "application/octet-stream",
        }
        if upsert:
            headers["x-allow-overwrite"] = "1"
        else:
            headers["x-allow-overwrite"] = "0"
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.put(url, content=data, headers=headers)
        if resp.status_code >= 400:
            _logger.warning("blob put failed %s: %s %s", path_n, resp.status_code, resp.text[:300])
            return False
        return True
    except Exception as e:
        _logger.warning("blob put error %s: %s", storage_path, e)
        return False


async def _blob_get(storage_path: str) -> Optional[bytes]:
    """Download bytes from private Vercel Blob (Authorization required)."""
    token = _blob_token()
    if not token or not storage_path:
        return None
    try:
        import httpx

        path_n = _normalize_storage_path(storage_path)
        obj_url = _private_blob_object_url(path_n)
        if not obj_url:
            return None
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(obj_url, headers=headers)
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            _logger.warning("blob get failed %s: %s", path_n, resp.status_code)
            return None
        return resp.content
    except Exception as e:
        _logger.warning("blob get error %s: %s", storage_path, e)
        return None


async def get_signed_download_url(storage_path: str, expiry: int = SIGNED_URL_EXPIRY) -> Optional[str]:
    """Return a Supabase signed URL, or HMAC Blob proxy URL on Fair-Use fallback."""
    if not storage_path:
        return None
    try:
        db = await get_db()
        result = await db.storage.from_(STORAGE_BUCKET).create_signed_url(storage_path, expiry)
        url = result.get("signedURL") or result.get("signedUrl") or result.get("signed_url")
        if url:
            return url
    except Exception:
        pass

    if blob_fallback_enabled():
        try:
            capped = min(max(60, int(expiry or DEFAULT_GET_EXPIRY)), MAX_GET_EXPIRY)
            return build_hmac_proxy_url("GET", storage_path, capped)
        except Exception as e:
            _logger.warning("blob signed get url failed: %s", e)
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

    Fair-Use: on Supabase failure, return HMAC PUT proxy URL with token=None so
    GO's capture-api.ts uses the plain PUT branch.
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
        if url and key:
            encoded = "/".join(quote(part, safe="") for part in storage_path.split("/") if part)
            endpoint = f"{url}/storage/v1/object/upload/sign/{STORAGE_BUCKET}/{encoded}"
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {key}", "apikey": key},
                )
            if resp.status_code < 400:
                parsed = _signed_upload_fields(resp.json())
                if parsed:
                    parsed["path"] = parsed.get("path") or storage_path
                    signed = parsed.get("signed_url") or ""
                    if signed.startswith("/"):
                        parsed["signed_url"] = f"{url}/storage/v1{signed}"
                    return parsed
    except Exception:
        pass

    if blob_fallback_enabled():
        try:
            return {
                "path": storage_path,
                "token": None,
                "signed_url": build_hmac_proxy_url("PUT", storage_path, DEFAULT_PUT_EXPIRY),
            }
        except Exception as e:
            _logger.warning("blob signed put url failed: %s", e)
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
    """Fetch object bytes from brxs-deliverables. Blob fallback when Supabase fails."""
    if not storage_path:
        return None
    try:
        db = await get_db()
        data = await db.storage.from_(STORAGE_BUCKET).download(storage_path)
        if data:
            return bytes(data)
    except Exception:
        pass

    if blob_fallback_enabled():
        blob_data = await _blob_get(storage_path)
        if blob_data is not None:
            return blob_data

    # Supabase signed-URL fallback (may also be HMAC proxy if Blob is active).
    url = None
    try:
        db = await get_db()
        result = await db.storage.from_(STORAGE_BUCKET).create_signed_url(storage_path, 120)
        url = result.get("signedURL") or result.get("signedUrl") or result.get("signed_url")
    except Exception:
        url = None
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
    """Upload raw bytes to brxs-deliverables (Blob fallback on failure). Returns True on success."""
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
        pass

    if blob_fallback_enabled():
        return await _blob_put(storage_path, data, content_type=content_type, upsert=upsert)
    return False
