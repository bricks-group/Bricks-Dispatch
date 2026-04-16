from fastapi import APIRouter, Header

from ..shared.audit import write_audit_event
from ..shared.auth import (
    create_vendor_session,
    deactivate_expired_sessions,
    get_vendor_member,
    hash_session_token,
    is_vendor_admin,
    resolve_vendor_actor_id,
)
from ..shared.database import db
from ..shared.errors import api_error
from ..shared.schemas import VendorAuthLogin


router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/vendor-os/auth/login")
async def vendor_os_login(payload: VendorAuthLogin):
    email = payload.email.strip().lower()
    if not email:
        api_error(400, "Email is required", "email_required")
    member = await db.vendor_members.find_one({"vendor_id": "default_vendor", "email": email}, {"_id": 0})
    if not member:
        from ..shared.schemas import VendorMember

        member = VendorMember(
            name=(payload.name or email.split("@")[0]).strip() or "Vendor User",
            email=email,
            role="Photographer",
        ).dict()
        await db.vendor_members.insert_one(member)
    session = await create_vendor_session(member)
    await write_audit_event(member["id"], "session", member["id"], "login", vendor_id=member.get("vendor_id", "default_vendor"))
    return session


@router.get("/vendor-os/auth/me")
async def vendor_os_me(authorization: str | None = Header(default=None, alias="Authorization")):
    actor_id = await resolve_vendor_actor_id(authorization, None)
    member = await db.vendor_members.find_one({"id": actor_id}, {"_id": 0})
    if not member:
        api_error(404, "Member not found", "member_not_found")
    return member


@router.post("/vendor-os/auth/refresh")
async def vendor_os_refresh(authorization: str | None = Header(default=None, alias="Authorization")):
    if not authorization:
        api_error(401, "Missing Authorization header", "missing_authorization")
    token = authorization.replace("Bearer ", "").strip()
    token_hash = hash_session_token(token)
    session = await db.vendor_sessions.find_one({"token_hash": token_hash, "active": True}, {"_id": 0})
    if not session:
        api_error(401, "Invalid session token", "invalid_session_token")

    member = await db.vendor_members.find_one({"id": session["member_id"], "vendor_id": session["vendor_id"]}, {"_id": 0})
    if not member:
        api_error(404, "Member not found", "member_not_found")

    await db.vendor_sessions.update_one({"token_hash": token_hash}, {"$set": {"active": False}})
    refreshed = await create_vendor_session(member)
    await write_audit_event(member["id"], "session", member["id"], "refresh", vendor_id=member.get("vendor_id", "default_vendor"))
    return refreshed


@router.post("/vendor-os/auth/logout")
async def vendor_os_logout(authorization: str | None = Header(default=None, alias="Authorization")):
    if not authorization:
        api_error(401, "Missing Authorization header", "missing_authorization")
    token = authorization.replace("Bearer ", "").strip()
    token_hash = hash_session_token(token)
    session = await db.vendor_sessions.find_one({"token_hash": token_hash, "active": True}, {"_id": 0})
    result = await db.vendor_sessions.update_one({"token_hash": token_hash, "active": True}, {"$set": {"active": False}})
    if result.matched_count == 0:
        api_error(401, "Invalid session token", "invalid_session_token")
    if session:
        await write_audit_event(session["member_id"], "session", session["member_id"], "logout", vendor_id=session.get("vendor_id", "default_vendor"))
    return {"logged_out": True}


@router.post("/vendor-os/auth/cleanup")
async def vendor_os_cleanup_sessions(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
    await deactivate_expired_sessions()
    count = await db.vendor_sessions.count_documents({"active": False})
    await write_audit_event(actor_id, "session", actor_id, "cleanup", vendor_id=actor_member.get("vendor_id", "default_vendor"), details={"inactive_sessions": count})
    return {"inactive_sessions": count}


@router.get("/vendor-os/auth/sessions")
async def vendor_os_list_sessions(authorization: str | None = Header(default=None, alias="Authorization")):
    actor_id = await resolve_vendor_actor_id(authorization, None)
    actor_member = await get_vendor_member(actor_id)
    sessions = await db.vendor_sessions.find(
        {"member_id": actor_id},
        {"_id": 0, "id": 1, "created_at": 1, "expires_at": 1, "active": 1},
    ).sort("created_at", -1).to_list(100)
    return {"items": sessions, "total": len(sessions), "page": 1, "page_size": len(sessions), "total_pages": 1, "vendor_id": actor_member.get("vendor_id", "default_vendor")}


@router.post("/vendor-os/auth/revoke/{session_id}")
async def vendor_os_revoke_session(session_id: str, authorization: str | None = Header(default=None, alias="Authorization")):
    actor_id = await resolve_vendor_actor_id(authorization, None)
    actor_member = await get_vendor_member(actor_id)
    result = await db.vendor_sessions.update_one({"id": session_id, "member_id": actor_id}, {"$set": {"active": False}})
    if result.matched_count == 0:
        api_error(404, "Session not found", "session_not_found")
    await write_audit_event(actor_id, "session", session_id, "revoke", vendor_id=actor_member.get("vendor_id", "default_vendor"))
    return {"revoked": True}


@router.post("/vendor-os/auth/revoke-others")
async def vendor_os_revoke_other_sessions(authorization: str | None = Header(default=None, alias="Authorization")):
    if not authorization:
        api_error(401, "Missing Authorization header", "missing_authorization")
    actor_id = await resolve_vendor_actor_id(authorization, None)
    actor_member = await get_vendor_member(actor_id)
    token = authorization.replace("Bearer ", "").strip()
    token_hash = hash_session_token(token)
    result = await db.vendor_sessions.update_many(
        {"member_id": actor_id, "token_hash": {"$ne": token_hash}, "active": True},
        {"$set": {"active": False}},
    )
    await write_audit_event(actor_id, "session", actor_id, "revoke_others", vendor_id=actor_member.get("vendor_id", "default_vendor"), details={"revoked_count": result.modified_count})
    return {"revoked_count": result.modified_count}

