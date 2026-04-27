from fastapi import APIRouter, Header, Request
from ..shared.audit import write_audit_event
from ..shared.auth import (
    get_vendor_member,
    is_vendor_admin,
    resolve_vendor_actor_id,
)
from ..shared.database import get_db
from ..shared.errors import api_error
from ..shared.schemas import VendorAuthLogin

router = APIRouter(prefix="/api", tags=["auth"])

@router.post("/vendor-os/auth/login")
async def vendor_os_login(payload: VendorAuthLogin):
    email = payload.email.strip().lower()
    if not email:
        api_error(400, "Email is required", "email_required")
    
    # In Supabase, we send OTP
    try:
        res = await (await get_db()).auth.sign_in_with_otp({"email": email})
        return {"message": "OTP sent to email", "email": email}
    except Exception as e:
        api_error(500, f"Error sending OTP: {str(e)}", "otp_error")

@router.post("/vendor-os/auth/verify")
async def vendor_os_verify(payload: dict):
    email = payload.get("email")
    token = payload.get("token") # The OTP code
    if not email or not token:
        api_error(400, "Email and token are required", "missing_fields")
    
    try:
        res = await (await get_db()).auth.verify_otp({"email": email, "token": token, "type": "magiclink"})
        if res.session:
            # We might want to ensure the member exists in our table too
            user = res.user
            member_res = await (await get_db()).table("vendor_members").select("*").eq("id", user.id).maybe_single().execute()
            if not member_res.data:
                await (await get_db()).table("vendor_members").insert({
                    "id": user.id,
                    "email": user.email,
                    "name": user.user_metadata.get("name", email.split("@")[0]),
                    "role": "Photographer",
                    "vendor_id": "default_vendor"
                }).execute()
            
            await write_audit_event(user.id, "session", user.id, "login", vendor_id="default_vendor")
            return {
                "access_token": res.session.access_token,
                "refresh_token": res.session.refresh_token,
                "user": res.user
            }
        else:
            api_error(401, "Invalid OTP", "invalid_otp")
    except Exception as e:
        api_error(401, f"Verification failed: {str(e)}", "verification_failed")

@router.get("/vendor-os/auth/me")
async def vendor_os_me(authorization: str | None = Header(default=None, alias="Authorization")):
    actor_id = await resolve_vendor_actor_id(authorization, None)
    member = await get_vendor_member(actor_id)
    return member

@router.post("/vendor-os/auth/logout")
async def vendor_os_logout(authorization: str | None = Header(default=None, alias="Authorization")):
    if not authorization:
        api_error(401, "Missing Authorization header", "missing_authorization")
    
    token = authorization.replace("Bearer ", "").strip()
    try:
        # Supabase logout
        # Note: SignOut usually clears the session on the server side for that token
        await (await get_db()).auth.sign_out()
        return {"logged_out": True}
    except Exception:
        # Even if it fails, we treat it as logged out locally
        return {"logged_out": True}

# Session management endpoints (cleanup, list, revoke) are now handled by Supabase Auth, 
# but if we want to keep them, we'd need to use Supabase Admin API.
# For now, I'll keep them as stubs or remove if they are redundant.
