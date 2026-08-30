"""In-app notifications + Expo/APNs token registration.

Expo:  GET /api/notifications
       PUT /api/notifications/{id}/read
       GET /api/notifications/unread-count
       POST /api/push-tokens
Swift: GET /api/notifications
       POST /api/notifications/mark-all-read
       POST /api/vendor-os/push-token  (mounted on vendor_os; same store)
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header
from ..notifications import public_notification
from ..services.notifications import store_push_token
from ..shared.auth import resolve_vendor_actor_id
from ..shared.database import get_db
from ..shared.errors import api_error
from ..shared.schemas import PushTokenRegister

router = APIRouter(prefix="/api", tags=["notifications"])


async def _actor(authorization: Optional[str], x_user_id: Optional[str]) -> str:
    return await resolve_vendor_actor_id(authorization, x_user_id)


async def _list_for_member(member_id: str) -> list[dict]:
    db = await get_db()
    try:
        res = await db.table("notifications").select("*").eq("member_id", member_id).order("created_at", desc=True).limit(100).execute()
        return [public_notification(r) for r in (res.data or [])]
    except Exception:
        # member_id column may not exist yet — honest empty, never leak every row
        return []


@router.get("/notifications")
async def list_notifications(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await _actor(authorization, x_user_id)
    return await _list_for_member(actor_id)


@router.get("/notifications/unread-count")
async def unread_count(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await _actor(authorization, x_user_id)
    items = await _list_for_member(actor_id)
    return {"count": sum(1 for n in items if not n["read"])}


@router.post("/notifications/mark-all-read")
async def mark_all_read(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await _actor(authorization, x_user_id)
    now = datetime.now(timezone.utc).isoformat()
    db = await get_db()
    try:
        await db.table("notifications").update({"read": True, "updated_at": now}).eq("member_id", actor_id).eq("read", False).execute()
    except Exception:
        pass
    return {"ok": True}


@router.put("/notifications/{notification_id}/read")
@router.post("/notifications/{notification_id}/read")
async def mark_one_read(
    notification_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await _actor(authorization, x_user_id)
    db = await get_db()
    res = await db.table("notifications").select("*").eq("id", notification_id).maybe_single().execute()
    row = res.data or {}
    if not row:
        api_error(404, "Notification not found", "notification_not_found")
    owner = row.get("member_id")
    if owner and owner != actor_id:
        api_error(404, "Notification not found", "notification_not_found")
    try:
        await db.table("notifications").update({"read": True}).eq("id", notification_id).execute()
    except Exception:
        pass
    row["read"] = True
    return public_notification(row)


@router.post("/push-tokens")
async def register_push_token(
    payload: PushTokenRegister,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await _actor(authorization, x_user_id)
    token = (payload.token or payload.device_token or "").strip()
    if not token:
        api_error(400, "token is required", "token_required")
    try:
        return await store_push_token(actor_id, token, payload.platform or "expo")
    except Exception:
        api_error(503, "Could not store push token", "push_token_store_failed")
