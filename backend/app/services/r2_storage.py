"""Cloudflare R2 (S3 API) presigned URLs — no SDK, SigV4 query signing.

Activated by config (preview first):
  STILLS_UPLOAD_BACKEND=r2 (new uploads) and STORAGE_READ_BACKEND=r2 (all paths)
  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
  (optional R2_ENDPOINT, UPLOAD_MAX_FILE_MB)
plus a CORS rule on the bucket allowing PUT/GET from https://go.brxs.ca.
Objects on R2 are stored in uploads.file_url as "r2:<key>" so every reader
(storage.download_bytes / get_signed_download_url / upload_bytes) routes by
prefix and existing Supabase paths keep working unchanged.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import os
from typing import Optional
from urllib.parse import quote

R2_PREFIX = "r2:"


def is_r2_path(path: Optional[str]) -> bool:
    return str(path or "").startswith(R2_PREFIX)


def r2_key(path: str) -> str:
    p = str(path or "")
    return p[len(R2_PREFIX):] if p.startswith(R2_PREFIX) else p


def r2_config() -> Optional[dict]:
    acct = (os.getenv("R2_ACCOUNT_ID") or "").strip()
    key = (os.getenv("R2_ACCESS_KEY_ID") or "").strip()
    secret = (os.getenv("R2_SECRET_ACCESS_KEY") or "").strip()
    bucket = (os.getenv("R2_BUCKET") or "").strip()
    endpoint = (os.getenv("R2_ENDPOINT") or "").strip().rstrip("/")
    if not endpoint and acct:
        endpoint = f"https://{acct}.r2.cloudflarestorage.com"
    if not (endpoint and key and secret and bucket):
        return None
    return {"endpoint": endpoint, "key": key, "secret": secret, "bucket": bucket, "region": os.getenv("R2_REGION", "auto")}


def r2_configured() -> bool:
    return r2_config() is not None


# Supabase bucket -> (R2 bucket env, default R2 bucket, key prefix). Keys are
# copied unchanged, so the same path works on both sides during the move.
_BUCKET_MAP = {
    "brxs-deliverables": ("R2_BUCKET_DELIVERABLES", "bricks-deliverables", ""),
    "noshow-evidence": ("R2_BUCKET_EVIDENCE", "bricks-evidence", ""),
    "job-checklist-proof": ("R2_BUCKET_EVIDENCE", "bricks-evidence", "job-checklist-proof/"),
    "db-backups": ("R2_BUCKET_BACKUPS", "bricks-backups", ""),
    "Bricks Images": ("R2_BUCKET_IMAGES", "bricks-images", ""),
}


def read_backend_is_r2() -> bool:
    """STORAGE_READ_BACKEND=r2: every storage path (prefixed or not) lives on R2."""
    raw = (os.getenv("STORAGE_READ_BACKEND") or "supabase").strip().lower()
    return raw == "r2" and r2_configured()


def r2_target(supabase_bucket: Optional[str], path: str) -> tuple[str, str]:
    """(R2 bucket, object key) for a Supabase bucket id + storage path."""
    cfg = r2_config() or {}
    key = r2_key(path)
    entry = _BUCKET_MAP.get(supabase_bucket or "brxs-deliverables")
    if not entry:
        return cfg.get("bucket", ""), key
    env_name, default, prefix = entry
    bucket = (os.getenv(env_name) or "").strip()
    if not bucket and env_name == "R2_BUCKET_DELIVERABLES":
        bucket = cfg.get("bucket", "")
    return bucket or default, f"{prefix}{key}"


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def presign_url(
    method: str,
    key: str,
    *,
    expires: int = 3600,
    endpoint: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    region: str = "auto",
    now: Optional[_dt.datetime] = None,
    virtual_host: bool = False,
) -> str:
    """AWS SigV4 presigned URL (UNSIGNED-PAYLOAD), path-style by default."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    scheme, host = endpoint.split("://", 1)
    host = host.rstrip("/")
    encoded_key = "/".join(quote(part, safe="-_.~") for part in key.split("/"))
    if virtual_host:
        host = f"{bucket}.{host}"
        canonical_uri = f"/{encoded_key}"
    else:
        canonical_uri = f"/{quote(bucket, safe='-_.~')}/{encoded_key}"
    scope = f"{datestamp}/{region}/s3/aws4_request"
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(int(expires)),
        "X-Amz-SignedHeaders": "host",
    }
    canonical_qs = "&".join(
        f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(params.items())
    )
    canonical_request = "\n".join([
        method.upper(), canonical_uri, canonical_qs, f"host:{host}\n", "host", "UNSIGNED-PAYLOAD",
    ])
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    k = _sign(("AWS4" + secret_key).encode("utf-8"), datestamp)
    k = _sign(k, region)
    k = _sign(k, "s3")
    k = _sign(k, "aws4_request")
    signature = hmac.new(k, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{scheme}://{host}{canonical_uri}?{canonical_qs}&X-Amz-Signature={signature}"


def r2_presign(
    method: str, path: str, expires: int = 3600, supabase_bucket: Optional[str] = None
) -> Optional[str]:
    cfg = r2_config()
    if not cfg:
        return None
    if supabase_bucket is None:
        bucket, key = cfg["bucket"], r2_key(path)
    else:
        bucket, key = r2_target(supabase_bucket, path)
    return presign_url(
        method, key, expires=expires, endpoint=cfg["endpoint"], bucket=bucket,
        access_key=cfg["key"], secret_key=cfg["secret"], region=cfg["region"],
    )


async def r2_get(path: str, supabase_bucket: Optional[str] = None) -> Optional[bytes]:
    url = r2_presign("GET", path, 300, supabase_bucket)
    if not url:
        return None
    import httpx

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.get(url)
    return resp.content if resp.status_code < 400 else None


async def r2_put(
    path: str,
    data: bytes,
    content_type: str = "application/octet-stream",
    supabase_bucket: Optional[str] = None,
) -> bool:
    url = r2_presign("PUT", path, 600, supabase_bucket)
    if not url:
        return False
    import httpx

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.put(url, content=data, headers={"Content-Type": content_type})
    return resp.status_code < 400


async def r2_delete(paths: list[str], supabase_bucket: Optional[str] = None) -> bool:
    """Delete objects one presigned DELETE at a time. Missing keys count as deleted."""
    import httpx

    ok = True
    async with httpx.AsyncClient(timeout=30.0) as client:
        for path in paths or []:
            url = r2_presign("DELETE", path, 300, supabase_bucket)
            if not url:
                return False
            resp = await client.delete(url)
            if resp.status_code >= 400 and resp.status_code != 404:
                ok = False
    return ok
