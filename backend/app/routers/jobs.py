"""Photographer job list at GET /api/jobs.

Expo schedule + uploads and Swift UploadsView expect a JSON array.
Optional ?status= filter matches historical tests.
Scoped to the caller's assigned jobs (Owner aliases include 0c3fa7c2).
"""
from typing import Optional

from fastapi import APIRouter, Header

from ..shared.auth import get_vendor_member, resolve_vendor_actor_id
from ..shared.database import get_db
from ..shared.job_scope import photographer_assignee_ids

router = APIRouter(prefix="/api", tags=["jobs"])


@router.get("/jobs")
async def list_jobs(
    status: Optional[str] = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    aliases = photographer_assignee_ids(actor_id, actor_member) or [actor_id]
    db = await get_db()
    query = db.table("jobs").select("*").in_("assignee_id", aliases)
    if status:
        query = query.eq("status", status)
    res = await query.order("scheduled_date", desc=False).limit(500).execute()
    return res.data or []
