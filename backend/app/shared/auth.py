import os
from typing import Optional
from .database import get_db
from .errors import api_error
from .schemas import VendorMember

def resolve_actor_id(x_user_id: Optional[str]) -> str:
    actor_id = (x_user_id or "").strip()
    if not actor_id:
        api_error(401, "Missing X-User-Id header", "missing_actor_header")
    return actor_id

def ensure_assigned_to_actor(job: dict, actor_id: str):
    assignee_id = job.get("assignee_id", "default_user")
    if assignee_id != actor_id:
        api_error(403, "You are not assigned to this job", "job_not_assigned_to_actor")

async def get_vendor_member(actor_id: str) -> dict:
    # Query supabase for vendor member
    response = await (await get_db()).table("vendor_members").select("*").eq("id", actor_id).maybe_single().execute()
    if not response.data:
        # Fallback for migration/default
        if actor_id == "default_user":
             return {"id": "default_user", "name": "Default Vendor", "role": "Owner Operator"}
        api_error(403, "Vendor member not found", "vendor_member_not_found")
    return response.data

def is_vendor_admin(member: dict) -> bool:
    role = (member.get("role") or "").lower()
    return any(k in role for k in ["owner", "admin", "manager"])

async def resolve_vendor_actor_id(authorization: Optional[str], x_user_id: Optional[str] = None) -> str:
    if authorization:
        token = authorization.replace("Bearer ", "").strip()
        if token:
            try:
                # Verify with Supabase
                user_resp = await (await get_db()).auth.get_user(token)
                if user_resp and user_resp.user:
                    return user_resp.user.id
            except Exception as e:
                api_error(401, f"Invalid session: {str(e)}", "invalid_session_token")
    
    if x_user_id:
        return resolve_actor_id(x_user_id)
    
    api_error(401, "Authentication required", "auth_required")

async def get_vendor_member_ids(vendor_id: str = "default_vendor") -> list[str]:
    response = await (await get_db()).table("vendor_members").select("id").eq("vendor_id", vendor_id).eq("is_active", True).execute()
    ids = [m["id"] for m in response.data]
    return ids if ids else ["default_user"]
