from fastapi import APIRouter, Header
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
    
    # In Supabase, we send OTP. Confirm a known vendor's Auth user first so a
    # first-time vendor gets the login-code email, not 'Confirm your email'.
    try:
        from ..services.email_login_prep import ensure_confirmed_vendor_auth_user
        await ensure_confirmed_vendor_auth_user(await get_db(), email)
    except Exception:
        pass
    try:
        res = await (await get_db()).auth.sign_in_with_otp({"email": email})
        return {"message": "OTP sent to email", "email": email}
    except Exception as e:
        api_error(500, f"Error sending OTP: {str(e)}", "otp_error")

@router.post("/vendor-os/auth/prepare-email-login")
async def vendor_os_prepare_email_login(payload: VendorAuthLogin):
    """Called by GO before signInWithOtp({email}). Always 200 with the same body
    so it cannot be used to probe which emails are vendors."""
    try:
        from ..services.email_login_prep import ensure_confirmed_vendor_auth_user
        await ensure_confirmed_vendor_auth_user(await get_db(), payload.email)
    except Exception:
        pass
    return {"ok": True}

@router.post("/vendor-os/auth/verify")
async def vendor_os_verify(payload: dict):
    email = payload.get("email")
    token = payload.get("token") # The OTP code
    if not email or not token:
        api_error(400, "Email and token are required", "missing_fields")
    
    try:
        # Verify on a throwaway auth client. verify_otp on the shared client
        # switched its Authorization header to the vendor's JWT, so the member
        # lookup/link below ran under RLS and failed for unlinked rows.
        from ..shared.database import new_auth_client
        res = await (await new_auth_client()).auth.verify_otp({"email": email, "token": token, "type": "email"})
        if res.session:
            user = res.user
            from ..services.vendor_otp_link import link_member_after_otp
            db = await get_db()  # service role
            meta = getattr(user, "user_metadata", None) or {}
            try:
                await link_member_after_otp(db, str(user.id), user.email or "", meta.get("name") or "")
            except Exception as le:
                # Code is valid; /auth/me resolves the member again. Don't fail the login.
                import logging
                logging.getLogger(__name__).warning("otp verify: member link failed: %s", str(le)[:200])

            await write_audit_event(user.id, "session", user.id, "login", vendor_id="default_vendor")
            return {
                "access_token": res.session.access_token,
                "refresh_token": res.session.refresh_token,
                "expires_in": res.session.expires_in,
                "user": {"id": user.id, "email": user.email},
            }
        else:
            api_error(401, "Invalid OTP", "invalid_otp")
    except Exception as e:
        api_error(401, f"Verification failed: {str(e)}", "verification_failed")

@router.get("/vendor-os/auth/me")
async def vendor_os_me(authorization: str | None = Header(default=None, alias="Authorization")):
    actor_id = await resolve_vendor_actor_id(authorization, None)
    member = await get_vendor_member(actor_id)
    if isinstance(member, dict):
        from ..shared.vendor_identity import needs_amendment, needs_ic_terms, needs_terms
        from ..services.vendor_onboarding import ensure_signed_in
        onboarding = await ensure_signed_in(member)
        if onboarding and onboarding.get("complete"):
            member = {**member, "onboarding_completed_at": onboarding.get("completed_at")}
        out = {**member, "needs_terms": needs_terms(member), "needs_ic_terms": needs_ic_terms(member),
               "needs_amendment": needs_amendment(member)}
        if onboarding is not None:
            out["onboarding"] = onboarding
        out["onboarding_complete"] = bool(member.get("onboarding_completed_at"))
        return out
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


# The Fair-Use HS256 minter and its /vendor-os/auth/fair-use-login and
# /vendor-os/auth/fair-use-signup routes were deleted 2026-11-14 (Sentinel):
# they signed 7-day, non-revocable tokens (no session_id) with the Supabase
# secret. Live api-2 never enabled them (FAIR_USE_AUTH_ENABLED unset -> 404).
