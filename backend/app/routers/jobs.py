"""Photographer job list at GET /api/jobs.

Expo schedule + uploads, Swift Schedule + UploadsView expect a JSON array.
Optional ?status= filter matches historical tests.
Scoped to the caller's assigned jobs (Owner aliases include 0c3fa7c2).

Fair-Use: when Canada PostgREST returns 402, list via Postgres (or OS proxy)
so GO can still see assigned jobs while Free Storage quota is locked.
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
    try:
        query = db.table("jobs").select("*").in_("assignee_id", aliases)
        if status:
            query = query.eq("status", status)
        res = await query.order("scheduled_date", desc=False).limit(500).execute()
        return res.data or []
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_jobs_for_assignees

        if not _is_rest_quota_error(e):
            raise
        jobs, _total = pg_select_jobs_for_assignees(
            aliases,
            status=status,
            limit=500,
            offset=0,
        )
        return jobs or []
