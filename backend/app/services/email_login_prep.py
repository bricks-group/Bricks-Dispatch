"""First-time vendor email login: make Supabase send a login code, not 'Confirm your email'.

signInWithOtp({email}) on an email with no confirmed Auth user sends the
'Confirm signup' template (a link, no usable code). For an email that belongs
to a vendor_members row we make sure a confirmed, password-less Auth user
exists first (service role), so the same call sends the 6-digit login code.
Ownership of the account is then proven only by that emailed code.

SECURITY (2026-11-02): this used to UPDATE auth.users SET email_confirmed_at
on an EXISTING unconfirmed user. Public signup is open with confirmation on,
so anyone could sign up with a vendor's email and their own password, call
this endpoint to get it confirmed, then log in with that password and be
matched to the vendor by email (account takeover). We now never confirm an
existing user:
  - no Auth user           -> create one, confirmed, no password
  - confirmed Auth user    -> leave it alone
  - unconfirmed Auth user  -> if it is a plain email signup that never signed
                              in and is not linked to a member, delete it and
                              recreate it confirmed with no password (drops any
                              password a stranger set); otherwise do nothing.
Unknown emails are left alone.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s%*,()]+@[^@\s%*,()_]+\.[^@\s%*,()_]+$")


def _is_already_registered(err: Exception) -> bool:
    msg = str(err).lower()
    return "already" in msg and ("registered" in msg or "exists" in msg)


def _exact(rows, email: str) -> list:
    """ilike treats % and _ (and PostgREST *) as wildcards; keep exact matches only."""
    return [r for r in (rows or []) if isinstance(r, dict) and str(r.get("email") or "").strip().lower() == email]


async def _vendor_rows(db, email: str) -> list:
    try:
        res = await db.table("vendor_members").select("id,email,auth_user_id").ilike("email", email).limit(10).execute()
        return _exact(getattr(res, "data", None), email)
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_vendor_member
        if _is_rest_quota_error(e):
            try:
                row = pg_select_vendor_member(email=email)
            except TypeError:
                return []
            return _exact([row] if row else [], email)
        raise


def _pg_auth_user(email: str) -> Optional[dict]:
    """Read the Auth user for an email (read-only). None when there is none.
    Raises RuntimeError when Postgres is unavailable so callers fail closed."""
    from ..shared.pg_fallback import _pg_connect
    conn = _pg_connect()
    if conn is None:
        raise RuntimeError("no postgres")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT u.id::text, u.email_confirmed_at, u.last_sign_in_at, u.phone, u.phone_confirmed_at, "
                "COALESCE((SELECT array_agg(DISTINCT i.provider) FROM auth.identities i WHERE i.user_id = u.id), '{}') "
                "FROM auth.users u WHERE lower(u.email) = %s ORDER BY u.created_at LIMIT 1",
                (email,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "email_confirmed_at": row[1],
            "last_sign_in_at": row[2],
            "phone": row[3],
            "phone_confirmed_at": row[4],
            "providers": [p for p in (row[5] or []) if p],
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def can_replace_unconfirmed(user: dict, member_rows: list) -> bool:
    """Only a bare, never-used email signup may be dropped and recreated."""
    if user.get("email_confirmed_at"):
        return False
    if user.get("last_sign_in_at"):
        return False
    if user.get("phone") or user.get("phone_confirmed_at"):
        return False
    if any(p != "email" for p in user.get("providers") or []):
        return False
    uid = str(user.get("id") or "")
    for m in member_rows or []:
        if uid and (str(m.get("id") or "") == uid or str(m.get("auth_user_id") or "") == uid):
            return False
    return True


async def _create_confirmed(db, email: str) -> None:
    # No password: the account can only be entered with an emailed code/link,
    # Google (same verified email) or a phone the vendor links later.
    await db.auth.admin.create_user({"email": email, "email_confirm": True})


async def ensure_vendor_email_login_ready(db, email: str) -> str:
    """Returns 'invalid', 'not_vendor', 'created', 'recreated', 'ok' (already
    confirmed), 'kept_unconfirmed' (unconfirmed but not safe to replace) or
    'unknown' (could not inspect; nothing changed). Never confirms an existing user."""
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        return "invalid"
    members = await _vendor_rows(db, email)
    if not members:
        return "not_vendor"
    try:
        user = _pg_auth_user(email)
    except Exception as e:
        logger.warning("email login prep: auth lookup unavailable: %s", type(e).__name__)
        # Can't inspect: a create is still safe (it fails if the email exists).
        try:
            await _create_confirmed(db, email)
            return "created"
        except Exception as ce:
            if not _is_already_registered(ce):
                logger.warning("email login prep: create_user failed: %s", str(ce)[:200])
            return "unknown"
    if user is None:
        try:
            await _create_confirmed(db, email)
            logger.info("email login prep: created confirmed auth user for vendor email")
            return "created"
        except Exception as e:
            if _is_already_registered(e):
                return "ok"
            logger.warning("email login prep: create_user failed: %s", str(e)[:200])
            return "unknown"
    if user.get("email_confirmed_at"):
        return "ok"
    if not can_replace_unconfirmed(user, members):
        logger.warning("email login prep: unconfirmed auth user %s kept (not a bare email signup)", user.get("id"))
        return "kept_unconfirmed"
    try:
        await db.auth.admin.delete_user(user["id"])
    except Exception as e:
        logger.warning("email login prep: delete unconfirmed user failed: %s", str(e)[:200])
        return "kept_unconfirmed"
    logger.warning("email login prep: replaced unconfirmed signup %s on a vendor email", user.get("id"))
    try:
        await _create_confirmed(db, email)
    except Exception as e:
        logger.warning("email login prep: recreate failed: %s", str(e)[:200])
        return "unknown"
    return "recreated"


# Back-compat name used by routers.
ensure_confirmed_vendor_auth_user = ensure_vendor_email_login_ready
