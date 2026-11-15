"""Cloudflare R2 (S3 API) presigned URLs — no SDK, SigV4 query signing.

Not active until Kam creates the R2 account/bucket/keys. Switching is config:
  STILLS_UPLOAD_BACKEND=r2
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
    extra_params: Optional[dict] = None,
) -> str:
    """AWS SigV4 presigned URL (UNSIGNED-PAYLOAD), path-style by default."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    scheme, host = endpoint.split("://", 1)
    host = host.rstrip("/")
    encoded_key = "/".join(quote(part, safe="-_.~") for part in key.split("/")) if key else ""
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
        **{str(k): str(v) for k, v in (extra_params or {}).items()},
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


def r2_presign(method: str, path: str, expires: int = 3600) -> Optional[str]:
    cfg = r2_config()
    if not cfg:
        return None
    return presign_url(
        method, r2_key(path), expires=expires, endpoint=cfg["endpoint"], bucket=cfg["bucket"],
        access_key=cfg["key"], secret_key=cfg["secret"], region=cfg["region"],
    )


async def r2_get(path: str) -> Optional[bytes]:
    url = r2_presign("GET", path, 300)
    if not url:
        return None
    import httpx

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.get(url)
    return resp.content if resp.status_code < 400 else None


async def r2_put(path: str, data: bytes, content_type: str = "application/octet-stream") -> bool:
    url = r2_presign("PUT", path, 600)
    if not url:
        return False
    import httpx

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.put(url, content=data, headers={"Content-Type": content_type})
    return resp.status_code < 400
