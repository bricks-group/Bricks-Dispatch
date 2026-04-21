"""S3-backed file storage for Dispatch photo uploads."""
import base64
import io
import os
from typing import Optional, Tuple

import boto3
from botocore.exceptions import ClientError
from PIL import Image

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET", "brxs-deliverables")
AWS_S3_REGION = os.getenv("AWS_S3_REGION", "auto")
AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL", "")
SIGNED_URL_EXPIRY = int(os.getenv("SIGNED_URL_EXPIRY_SECONDS", "3600"))

STORAGE_ENABLED = bool(AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY)

_KEY_PREFIX = "bricks-dispatch"

# Module-level singleton — boto3 sessions and connections are thread-safe and
# should be reused rather than recreated on every request.
def _make_s3_client():
    kwargs = dict(
        region_name=AWS_S3_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )
    if AWS_S3_ENDPOINT_URL:
        kwargs["endpoint_url"] = AWS_S3_ENDPOINT_URL
    return boto3.client("s3", **kwargs)

_s3 = _make_s3_client() if STORAGE_ENABLED else None


def _s3_client():
    return _s3


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
    """Upload file and thumbnail to S3.

    Returns (file_url, thumbnail_url, thumbnail_b64_200)
    file_url / thumbnail_url are S3 object keys (not signed — sign on demand).
    thumbnail_b64_200 is a 200-char base64 preview for backward compat.
    """
    thumb_b64 = ""
    file_key: Optional[str] = None
    thumb_key: Optional[str] = None

    if not STORAGE_ENABLED:
        # No credentials yet — store only in-memory thumbnail
        try:
            thumb_bytes = _make_thumbnail_bytes(file_bytes)
            thumb_b64 = base64.b64encode(thumb_bytes).decode()[:200]
        except Exception:
            thumb_b64 = base64.b64encode(file_bytes[:150]).decode()[:200]
        return None, None, thumb_b64

    s3 = _s3_client()
    prefix = f"{_KEY_PREFIX}/jobs/{job_id}/{upload_id}"
    file_key = f"{prefix}/{filename}"
    thumb_key = f"{prefix}/thumb_{filename.rsplit('.', 1)[0]}.jpg"

    # Upload original
    s3.put_object(
        Bucket=AWS_S3_BUCKET,
        Key=file_key,
        Body=file_bytes,
        ContentType=mime_type or "application/octet-stream",
    )

    # Generate and upload thumbnail
    try:
        thumb_bytes = _make_thumbnail_bytes(file_bytes)
        s3.put_object(
            Bucket=AWS_S3_BUCKET,
            Key=thumb_key,
            Body=thumb_bytes,
            ContentType="image/jpeg",
        )
        thumb_b64 = base64.b64encode(thumb_bytes).decode()[:200]
    except Exception:
        thumb_key = None
        thumb_b64 = base64.b64encode(file_bytes[:150]).decode()[:200]

    return file_key, thumb_key, thumb_b64


def get_signed_download_url(s3_key: str, expiry: int = SIGNED_URL_EXPIRY) -> Optional[str]:
    """Return a pre-signed S3 URL valid for `expiry` seconds."""
    if not STORAGE_ENABLED or not s3_key:
        return None
    try:
        s3 = _s3_client()
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": AWS_S3_BUCKET, "Key": s3_key},
            ExpiresIn=expiry,
        )
    except ClientError:
        return None
