import os

from fastapi import APIRouter, Body, Header
from ..shared.audit import write_audit_event
from ..shared.auth import (
    get_vendor_member,
    is_vendor_admin,
    mint_supabase_access_token,
    resolve_vendor_actor_id,
)
from ..shared.database import get_db
from ..shared.errors import api_error
from ..shared.pg_fallback import _pg_connect, pg_select_vendor_member, pg_verify_auth_password, pg_create_auth_user, pg_upsert_vendor_member
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
            from ..shared.job_scope import KAM_EMAILS, KAM_MEMBER_ID, pick_canonical_member
            db = await get_db()
            member_res = await db.table("vendor_members").select("*").eq("id", user.id).maybe_single().execute()
            # maybe_single() is None when no member row — None.data was AttributeError.
            _mdata = getattr(member_res, "data", None) if member_res is not None else None
            rows = [_mdata] if isinstance(_mdata, dict) else []
            if user.email:
                by_email = await db.table("vendor_members").select("*").ilike("email", user.email).limit(10).execute()
                for row in by_email.data or []:
                    if row and row.get("id") not in {r.get("id") for r in rows}:
                        rows.append(row)
            if (user.email or "").lower() in KAM_EMAILS or str(user.id).lower().startswith("0c3fa7c2"):
                kam = await db.table("vendor_members").select("*").eq("id", KAM_MEMBER_ID).maybe_single().execute()
                _kdata = getattr(kam, "data", None) if kam is not None else None
                if isinstance(_kdata, dict) and _kdata.get("id") not in {r.get("id") for r in rows}:
                    rows.append(_kdata)
            picked = pick_canonical_member(rows, user.id, user.email or "")
            if not picked:
                await db.table("vendor_members").insert({
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
        from ..shared.vendor_identity import needs_terms
        from ..services.vendor_onboarding import ensure_signed_in
        onboarding = await ensure_signed_in(member)
        if onboarding and onboarding.get("complete"):
            member = {**member, "onboarding_completed_at": onboarding.get("completed_at")}
        out = {**member, "needs_terms": needs_terms(member)}
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


def fair_use_auth_enabled() -> bool:
    """Fair-Use PG auth is an emergency path; OFF unless FAIR_USE_AUTH_ENABLED is literally "true"."""
    return os.environ.get("FAIR_USE_AUTH_ENABLED") == "true"


def _require_fair_use_auth_enabled() -> None:
    if not fair_use_auth_enabled():
        api_error(404, "Not found", "not_found")


@router.post("/vendor-os/auth/fair-use-login")
async def vendor_os_fair_use_login(payload: dict = Body(default={})):
    """Email+password login via direct Postgres when Supabase Auth is Fair-Use 402.

    Verifies crypt() against auth.users, resolves vendor_members, mints a
    Supabase-compatible HS256 JWT with SUPABASE_JWT_SECRET.
    Disabled (404) unless FAIR_USE_AUTH_ENABLED == "true".
    """
    _require_fair_use_auth_enabled()
    raw = payload if isinstance(payload, dict) else {}
    email = (raw.get("email") or "").strip().lower()
    password = raw.get("password")
    if password is None:
        password = ""
    password = str(password)
    if not email or not password:
        api_error(400, "Email and password are required", "missing_fields")

    # Probe PG first so "wrong password" (None from verify) is not confused with 503.
    probe = _pg_connect()
    if probe is None:
        api_error(
            503,
            "Fair-Use login unavailable: Postgres not configured",
            "fair_use_pg_unavailable",
        )
    try:
        probe.close()
    except Exception:
        pass

    user = pg_verify_auth_password(email, password)
    if not user:
        api_error(401, "Invalid email or password", "invalid_credentials")

    # Resolve by auth user id / member id only — never adopt a row by email.
    member = pg_select_vendor_member(user["id"], "")
    if not member:
        api_error(403, "Vendor member not found", "vendor_member_not_found")

    expires_in = 7 * 24 * 3600
    try:
        access_token = mint_supabase_access_token(
            user["id"],
            user.get("email") or email,
            expires_in=expires_in,
        )
    except Exception as e:
        api_error(503, f"Could not mint session: {e}", "fair_use_jwt_unavailable")

    # Twin refresh token (same JWT) — client setSession needs both fields.
    refresh_token = access_token

    try:
        await write_audit_event(
            member["id"],
            "session",
            member["id"],
            "fair_use_login",
            vendor_id=member.get("vendor_id") or "default_vendor",
        )
    except Exception:
        pass

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
        "user": {"id": user["id"], "email": user.get("email") or email},
        "fair_use": True,
        "member_id": member["id"],
    }


@router.post("/vendor-os/auth/fair-use-signup")
async def vendor_os_fair_use_signup(payload: dict = Body(default={})):
    """Create photographer account via Postgres when Supabase Auth is Fair-Use 402.

    Disabled (404) unless FAIR_USE_AUTH_ENABLED == "true".
    """
    _require_fair_use_auth_enabled()
    raw = payload if isinstance(payload, dict) else {}
    email = (raw.get("email") or "").strip().lower()
    password = "" if raw.get("password") is None else str(raw.get("password"))
    name = (raw.get("name") or "").strip()
    if not email or not password:
        api_error(400, "Email and password are required", "missing_fields")
    if len(password) < 8:
        api_error(400, "Password must be at least 8 characters", "password_too_short")
    probe = _pg_connect()
    if probe is None:
        api_error(503, "Fair-Use signup unavailable: Postgres not configured", "fair_use_pg_unavailable")
    try:
        probe.close()
    except Exception:
        pass
    try:
        user = pg_create_auth_user(email, password, full_name=name)
    except ValueError as e:
        code = str(e)
        if code == "email_taken":
            api_error(409, "An account with this email already exists", "email_taken")
        if code == "password_too_short":
            api_error(400, "Password must be at least 8 characters", "password_too_short")
        api_error(400, str(e), "signup_failed")
    if not user:
        api_error(500, "Could not create account", "fair_use_signup_failed")
    member = pg_upsert_vendor_member(user["id"], user.get("email") or email, name=name or email.split("@")[0])
    if not member:
        api_error(500, "Account created but vendor profile failed", "vendor_member_create_failed")
    expires_in = 7 * 24 * 3600
    try:
        access_token = mint_supabase_access_token(user["id"], user.get("email") or email, expires_in=expires_in)
    except Exception as e:
        api_error(503, f"Could not mint session: {e}", "fair_use_jwt_unavailable")
    refresh_token = access_token
    try:
        await write_audit_event(
            member.get("id") or user["id"],
            "session",
            member.get("id") or user["id"],
            "fair_use_signup",
            vendor_id=member.get("vendor_id") or "default_vendor",
        )
    except Exception:
        pass
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
        "user": {"id": user["id"], "email": user.get("email") or email},
        "fair_use": True,
        "member_id": member.get("id"),
        "needs_onboarding": True,
    }

