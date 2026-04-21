import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional

from .database import db
from .errors import api_error
from .schemas import VendorMember, VendorSession


def resolve_actor_id(x_user_id: Optional[str]) -> str:
    actor_id = (x_user_id or "").strip()
    if not actor_id:
        api_error(401, "Missing X-User-Id header", "missing_actor_header")
    return actor_id


def ensure_assigned_to_actor(job: dict, actor_id: str):
    assignee_id = job.get("assignee_id", "default_user")
    if assignee_id != actor_id:
        api_error(403, "You are not assigned to this job", "job_not_assigned_to_actor")


async def ensure_default_vendor_member():
    existing = await db.vendor_members.find_one({"id": "default_user"}, {"_id": 0})
    if existing:
        return
    profile = await db.profile.find_one({"id": "default_user"}, {"_id": 0})
    name = profile["name"] if profile else "Default Vendor"
    email = profile["email"] if profile else "vendor@bricksdispatch.com"
    member = VendorMember(id="default_user", name=name, email=email, role="Owner Operator")
    await db.vendor_members.insert_one(member.dict())


async def get_vendor_member_ids(vendor_id: str = "default_vendor") -> list[str]:
    await ensure_default_vendor_member()
    members = await db.vendor_members.find({"vendor_id": vendor_id, "is_active": True}, {"_id": 0, "id": 1}).to_list(200)
    ids = [m["id"] for m in members]
    return ids if ids else ["default_user"]


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def get_vendor_member(actor_id: str) -> dict:
    await ensure_default_vendor_member()
    member = await db.vendor_members.find_one({"id": actor_id}, {"_id": 0})
    if not member:
        api_error(403, "Vendor member not found", "vendor_member_not_found")
    return member


def is_vendor_admin(member: dict) -> bool:
    role = (member.get("role") or "").lower()
    return any(k in role for k in ["owner", "admin", "manager"])


async def resolve_vendor_actor_id(authorization: Optional[str], x_user_id: Optional[str] = None) -> str:
    if authorization:
        token = authorization.replace("Bearer ", "").strip()
        if token:
            session = await db.vendor_sessions.find_one({"token_hash": hash_session_token(token), "active": True}, {"_id": 0})
            if not session:
                api_error(401, "Invalid session token", "invalid_session_token")
            try:
                expires_at = datetime.fromisoformat(session["expires_at"])
            except Exception:
                api_error(401, "Invalid session expiration", "invalid_session_expiration")
            if expires_at < datetime.now(timezone.utc):
                await db.vendor_sessions.update_one({"token_hash": hash_session_token(token)}, {"$set": {"active": False}})
                api_error(401, "Session expired", "session_expired")
            return session["member_id"]
    return resolve_actor_id(x_user_id)


async def create_vendor_session(member: dict) -> dict:
    raw_token = secrets.token_urlsafe(48)
    session = VendorSession(
        member_id=member["id"],
        vendor_id=member.get("vendor_id", "default_vendor"),
        token_hash=hash_session_token(raw_token),
    ).dict()
    await db.vendor_sessions.insert_one(session)
    return {
        "token": raw_token,
        "member": member,
        "expires_at": session["expires_at"],
    }


async def deactivate_expired_sessions():
    now = datetime.now(timezone.utc).isoformat()
    await db.vendor_sessions.update_many(
        {"active": True, "expires_at": {"$lt": now}},
        {"$set": {"active": False}},
    )

