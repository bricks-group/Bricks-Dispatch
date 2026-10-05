import os
from typing import Optional
from .database import get_db
from .errors import api_error
from .schemas import VendorMember
from .vendor_identity import is_owner_member


def _is_rest_quota_error(err: object) -> bool:
    e = str(err or "").lower()
    return any(
        tok in e
        for tok in (
            "402",
            "exceed_storage",
            "storage_size_quota",
            "fair-use",
            "fair use",
            "fair_use",
            "restricted",
            "quota exceeded",
            "over_quota",
        )
    )


def decode_supabase_user_from_jwt(token: str) -> Optional[tuple]:
    """Local HS256 verify when Auth API is Fair-Use blocked. Returns (user_id, email)."""
    secret = (os.environ.get("SUPABASE_JWT_SECRET") or "").strip()
    if not secret or not token:
        return None
    try:
        import jwt
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="authenticated",
            options={"require": ["sub", "exp"]},
        )
        uid = str(payload.get("sub") or "").strip()
        if not uid:
            return None
        email = payload.get("email")
        return uid, (str(email).strip() if email else None)
    except Exception:
        return None



def mint_supabase_access_token(
    user_id: str,
    email: str,
    *,
    expires_in: int = 7 * 24 * 3600,
) -> str:
    """Mint an HS256 JWT matching decode_supabase_user_from_jwt expectations."""
    import time
    import jwt

    secret = (os.environ.get("SUPABASE_JWT_SECRET") or "").strip()
    if not secret:
        raise RuntimeError("SUPABASE_JWT_SECRET missing")
    uid = str(user_id or "").strip()
    if not uid:
        raise RuntimeError("user_id required")
    now = int(time.time())
    payload = {
        "aud": "authenticated",
        "role": "authenticated",
        "sub": uid,
        "email": (email or "").strip().lower() or None,
        "iat": now,
        "exp": now + int(expires_in),
    }
    # Drop null email to keep payload clean
    if not payload["email"]:
        payload.pop("email", None)
    supabase_url = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    if supabase_url:
        payload["iss"] = f"{supabase_url}/auth/v1"
    token = jwt.encode(payload, secret, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token

def resolve_actor_id(x_user_id: Optional[str]) -> str:
    actor_id = (x_user_id or "").strip()
    if not actor_id:
        api_error(401, "Missing X-User-Id header", "missing_actor_header")
    return actor_id

def ensure_assigned_to_actor(job: dict, actor_id: str, member: Optional[dict] = None):
    from .job_scope import photographer_assignee_ids
    assignee_id = str(job.get("assignee_id") or "")
    if assignee_id not in photographer_assignee_ids(actor_id, member):
        api_error(403, "You are not assigned to this job", "job_not_assigned_to_actor")

async def lookup_vendor_member(actor_id: str, email: Optional[str] = None) -> Optional[dict]:
    """Map auth user → vendor_members row. Prefer Kam Owner 0c3fa7c2 over a duplicate IC."""
    from .job_scope import KAM_EMAILS, KAM_MEMBER_ID, pick_canonical_member
    from .pg_fallback import _is_rest_quota_error as _pg_quota, pg_select_vendor_member

    email_l = (email or "").strip().lower()
    try:
        db = await get_db()
        rows: list[dict] = []
        seen: set[str] = set()

        def add(row):
            if not row:
                return
            rid = str(row.get("id") or "")
            if rid and rid not in seen:
                seen.add(rid)
                rows.append(row)

        # postgrest maybe_single().execute() is None when there is no row. Applicants
        # and owner-added members have a random member id != auth uid, so the by-id
        # read is often empty; reading .data on None used to 401/500 GO before the
        # email match below could find them.
        by_id = await db.table("vendor_members").select("*").eq("id", actor_id).maybe_single().execute()
        add(getattr(by_id, "data", None))
        if str(actor_id or "").lower().startswith("0c3fa7c2") or email_l in KAM_EMAILS:
            kam = await db.table("vendor_members").select("*").eq("id", KAM_MEMBER_ID).maybe_single().execute()
            add(getattr(kam, "data", None))
        if email_l:
            by_email = await db.table("vendor_members").select("*").ilike("email", email_l).limit(10).execute()
            for row in by_email.data or []:
                add(row)
        return pick_canonical_member(rows, actor_id, email_l)
    except Exception as e:
        if _pg_quota(e) or _is_rest_quota_error(e):
            return pg_select_vendor_member(actor_id, email_l)
        raise


async def get_vendor_member(actor_id: str, email: Optional[str] = None) -> dict:
    # Query supabase for vendor member. No hardcoded fallback — the member
    # must actually exist in the DB (fail-closed). Lookup by id OR email so
    # Kam's Owner session maps to 0c3fa7c2 even if OTP created a second row.
    member = await lookup_vendor_member(actor_id, email)
    if not member:
        api_error(403, "Vendor member not found", "vendor_member_not_found")
    return member

def is_vendor_admin(member: dict) -> bool:
    if is_owner_member(member):
        return True
    role = (member.get("role") or "").lower()
    title = (member.get("title") or "").lower()
    return any(k in role or k in title for k in ["owner", "admin", "manager"])

def can_access_stripe_connect(member: dict) -> bool:
    """Who may start/view Stripe Connect onboard+status for their vendor org.

    Owner/Admin/Manager always (admin override). Any *active* vendor_member
    (including Photographer after approval) may open Connect for the org's
    contractor row. Applicants (is_active=False) stay gated out.

    TODO: multi-member Connect — today contractors are keyed by vendor_id
    (one Stripe Express account per org). Solo IC applicants are the Monday path;
    do not assume per-member stripe_account_id until redesigned.
    """
    if not member:
        return False
    if is_vendor_admin(member):
        return True
    return bool(member.get("is_active")) and bool(member.get("vendor_id"))

def verify_internal_api_key(key: Optional[str]) -> bool:
    """Validate a service-to-service X-Internal-Api-Key value.

    Fail-closed: if INTERNAL_API_KEYS is not configured, or the key is
    missing/unknown, this returns False.
    """
    allowed = {k for k in os.environ.get("INTERNAL_API_KEYS", "").split(",") if k}
    if not allowed or not key:
        return False
    return key in allowed

def get_own_internal_api_key() -> str:
    """The key this service presents when calling ANOTHER service's internal
    endpoints -- the first entry of our own INTERNAL_API_KEYS. Three call
    sites (webhooks.py, client_comms.py, post_production.py's delivery
    notification) used to read a separate INTERNAL_API_KEY (singular) env
    var that was never actually configured, so those outbound calls always
    sent an empty header and silently no-op'd (found via a systematic auth
    audit, 2026-08-15). Standardized on the one var every receiving
    endpoint in this repo already validates against."""
    return (os.environ.get("INTERNAL_API_KEYS", "").split(",")[0] or "").strip()

async def require_vendor_admin(
    authorization: Optional[str],
    x_user_id: Optional[str],
    x_internal_api_key: Optional[str] = None,
) -> tuple[str, Optional[dict]]:
    """Admin gate for /api/admin routes.

    Accepts EITHER a valid X-Internal-Api-Key (service-to-service) OR a
    properly authenticated vendor member that exists in the DB with an
    admin-level role. Returns (actor_id, actor_member). actor_member is
    None for internal service callers.
    """
    if x_internal_api_key is not None:
        # Key was supplied — it must be valid; never fall through on a bad key.
        if not verify_internal_api_key(x_internal_api_key):
            api_error(403, "Invalid internal API key", "invalid_internal_auth")
        return "internal_service", None

    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
    return actor_id, actor_member

async def resolve_vendor_actor_id(
    authorization: Optional[str],
    x_user_id: Optional[str] = None,
    x_internal_api_key: Optional[str] = None,
) -> str:
    """Resolve the caller's vendor identity.

    SECURITY (fixed 2026-08-15): the X-User-Id fallback below used to be
    trusted with ZERO verification whenever no Authorization header was
    sent -- any caller could impersonate any vendor by simply omitting
    Authorization and setting X-User-Id to a guessed/known vendor_members
    id. Confirmed via a real client-code audit that no production caller,
    client or server, has ever actually relied on X-User-Id-only auth
    (only test files did) -- so this is now gated on a valid
    X-Internal-Api-Key, same convention as require_vendor_admin/
    verify_internal_api_key. A real end-user always has a genuine Supabase
    session and goes through the Authorization branch below, completely
    unaffected by this change.
    """
    if authorization:
        token = authorization.replace("Bearer ", "").strip()
        if token:
            try:
                # Verify with Supabase Auth API
                user_resp = await (await get_db()).auth.get_user(token)
                if user_resp and user_resp.user:
                    user = user_resp.user
                    member = await lookup_vendor_member(user.id, getattr(user, "email", None))
                    if not member:
                        api_error(403, "Vendor member not found", "vendor_member_not_found")
                    return member["id"]
            except Exception as e:
                # Fair-Use locks Auth API — verify JWT locally with SUPABASE_JWT_SECRET
                if _is_rest_quota_error(e):
                    decoded = decode_supabase_user_from_jwt(token)
                    if decoded:
                        uid, email = decoded
                        member = await lookup_vendor_member(uid, email)
                        if not member:
                            api_error(403, "Vendor member not found", "vendor_member_not_found")
                        return member["id"]
                api_error(401, f"Invalid session: {str(e)}", "invalid_session_token")

    if x_user_id and verify_internal_api_key(x_internal_api_key):
        raw = resolve_actor_id(x_user_id)
        member = await lookup_vendor_member(raw)
        return (member or {}).get("id") or raw

    api_error(401, "Authentication required", "auth_required")

async def get_vendor_member_ids(vendor_id: str = "default_vendor") -> list[str]:
    response = await (await get_db()).table("vendor_members").select("id").eq("vendor_id", vendor_id).eq("is_active", True).execute()
    ids = [m["id"] for m in response.data]
    return ids if ids else ["default_user"]
