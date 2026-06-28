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

async def _approved_vendor_by_email(email: str) -> dict | None:
    """Look up an admin-approved vendor application by email.

    vendor_members rows are created at /api/onboarding/apply time with a
    fresh UUID (no Supabase auth account exists yet for a pending
    applicant), so this matches by email rather than by auth user id."""
    res = await (await get_db()).table("vendor_members").select("*").eq("email", email).maybe_single().execute()
    member = res.data
    if member and member.get("onboarding_status") == "approved" and member.get("is_active"):
        return member
    return None

@router.post("/vendor-os/auth/login")
async def vendor_os_login(payload: VendorAuthLogin):
    email = payload.email.strip().lower()
    if not email:
        api_error(400, "Email is required", "email_required")

    # Only send a login OTP to emails that are actually admin-approved
    # vendors — Bricks admin approves photographers into the network, not
    # this endpoint. Anyone else (e.g. a brokerage manager's email) must
    # not be able to get into the vendor app.
    if not await _approved_vendor_by_email(email):
        api_error(403, "No approved vendor account found for this email. Apply at /onboarding/apply.", "vendor_not_approved")

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
    email = email.strip().lower()

    member = await _approved_vendor_by_email(email)
    if not member:
        api_error(403, "No approved vendor account found for this email. Apply at /onboarding/apply.", "vendor_not_approved")

    try:
        res = await (await get_db()).auth.verify_otp({"email": email, "token": token, "type": "magiclink"})
        if res.session:
            user = res.user
            await write_audit_event(user.id, "session", user.id, "login", vendor_id="default_vendor")
            return {
                "access_token": res.session.access_token,
                "refresh_token": res.session.refresh_token,
                "user": res.user,
                "member_id": member["id"],
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
