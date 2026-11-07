"""Public, non-expiring gallery images embedded in delivery emails.

GET /api/public/delivery-email/{job_id}/{asset}?s=<hmac>[&ids=a,b,c]

The HMAC proves Dispatch minted the URL; every request also re-checks that the
job was delivered and each still is still QC-passed, so a QC hold or rejection
after delivery pulls the image from emails already sent (404).
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import Response

from ..services import delivery_email_media as dem

router = APIRouter(prefix="/api/public/delivery-email", tags=["delivery-email"])

_NOT_FOUND = Response(status_code=404, headers={"Cache-Control": "no-store"})


@router.get("/{job_id}/{asset}")
async def delivery_email_asset(job_id: str, asset: str, s: str = Query(""), ids: str = Query("")):
    parsed = dem.parse_asset(asset)
    if not parsed or not dem._ID_RE.match(job_id or ""):
        return _NOT_FOUND
    kind, uid = parsed
    if kind == "gif":
        id_list = dem.parse_ids(ids)
        if len(id_list) < 2:
            return _NOT_FOUND
    else:
        ids = ""
        id_list = [uid]
    if not dem.verify_asset(job_id, asset, ids, s):
        return _NOT_FOUND
    if not await dem.job_is_delivered(job_id):
        return _NOT_FOUND
    rows = await dem.load_eligible_uploads(job_id, id_list)
    if len(rows) != len(id_list):
        return _NOT_FOUND
    data = await dem.build_asset(job_id, asset, ids, {str(r["id"]): r for r in rows})
    if not data:
        return _NOT_FOUND
    return Response(
        content=data,
        media_type="image/gif" if kind == "gif" else "image/jpeg",
        # Short cache so a later QC hold takes effect within the hour.
        headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )
