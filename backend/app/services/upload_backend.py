"""Direct browser → storage uploads behind one switch.

The vendor's file never passes through a Vercel function (4.5 MB body cap).
/api/uploads/sign asks this module for a direct upload target; the web app
uploads to it (TUS resumable to Supabase for big files, else one signed PUT).

STILLS_UPLOAD_BACKEND=supabase (default) | r2
Switching to R2 is config only (see r2_storage.py for the env vars).
"""
from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urlparse

from . import r2_storage

SUPABASE_FREE_MAX_MB = 50
R2_SINGLE_PUT_MAX_MB = 5000
# Supabase TUS requires exactly 6 MB chunks.
TUS_CHUNK_BYTES = 6 * 1024 * 1024
# Under this size a single signed PUT is fine; above it the client uses TUS.
TUS_MIN_BYTES = int(os.getenv("UPLOAD_TUS_MIN_BYTES", str(TUS_CHUNK_BYTES)))
# Fair-Use Blob fallback proxies bytes through dispatch-api (Vercel 4.5 MB cap).
BLOB_PROXY_MAX_BYTES = int(4.4 * 1024 * 1024)


def backend_name() -> str:
    raw = (os.getenv("STILLS_UPLOAD_BACKEND") or "supabase").strip().lower()
    if raw == "r2" and r2_storage.r2_configured():
        return "r2"
    return "supabase"


def max_file_bytes() -> int:
    env = os.getenv("UPLOAD_MAX_FILE_MB")
    default = R2_SINGLE_PUT_MAX_MB if backend_name() == "r2" else SUPABASE_FREE_MAX_MB
    try:
        mb = float(env) if env else float(default)
    except ValueError:
        mb = float(default)
    return int(mb * 1024 * 1024)


def storage_path_for(key: str) -> str:
    """Path recorded on uploads.file_url for a new object on the active backend."""
    return f"{r2_storage.R2_PREFIX}{key}" if backend_name() == "r2" else key


def _supabase_tus_endpoint() -> Optional[str]:
    base = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    if not base:
        return None
    host = urlparse(base).hostname or ""
    # Direct storage hostname is the documented fast path for resumable uploads.
    if host.endswith(".supabase.co") and ".storage." not in host:
        ref = host.split(".", 1)[0]
        return f"https://{ref}.storage.supabase.co/storage/v1/upload/resumable/sign"
    return f"{base}/storage/v1/upload/resumable/sign"


def _is_blob_proxy(url: str) -> bool:
    return "/api/capture/blob-put" in (url or "")


async def create_direct_upload(key: str, content_type: str, file_size: int = 0) -> Optional[dict]:
    """Return the direct upload target for `key`, or None when storage is unavailable.

    Shape: {provider, method, signed_url, token?, tus?, storage_path, headers,
            max_file_bytes, via_proxy}
    """
    if backend_name() == "r2":
        path = storage_path_for(key)
        url = r2_storage.r2_presign("PUT", path, 3600)
        if not url:
            return None
        return {
            "provider": "r2",
            "method": "PUT",
            "signed_url": url,
            "token": None,
            "tus": None,
            "storage_path": path,
            "headers": {"Content-Type": content_type},
            "max_file_bytes": max_file_bytes(),
            "via_proxy": False,
        }

    from .storage import STORAGE_BUCKET, create_signed_upload_url

    signed = await create_signed_upload_url(key)
    if not signed or not signed.get("signed_url"):
        return None
    url = str(signed.get("signed_url"))
    via_proxy = _is_blob_proxy(url)
    token = signed.get("token")
    tus = None
    if token and not via_proxy:
        endpoint = _supabase_tus_endpoint()
        if endpoint:
            tus = {
                "endpoint": endpoint,
                "headers": {"x-signature": token},
                "metadata": {
                    "bucketName": STORAGE_BUCKET,
                    "objectName": signed.get("path") or key,
                    "contentType": content_type,
                    "cacheControl": "3600",
                },
                "chunk_size": TUS_CHUNK_BYTES,
                "min_bytes": TUS_MIN_BYTES,
            }
    return {
        "provider": "blob_proxy" if via_proxy else "supabase",
        "method": "PUT",
        "signed_url": url,
        "token": token,
        "tus": tus,
        "storage_path": key,
        "path": signed.get("path") or key,
        "headers": {"Content-Type": content_type},
        "max_file_bytes": BLOB_PROXY_MAX_BYTES if via_proxy else max_file_bytes(),
        "via_proxy": via_proxy,
    }
