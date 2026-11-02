"""First-time vendor email login: make Supabase send a login code, not 'Confirm your email'.

signInWithOtp({email}) on an email with no confirmed Auth user sends the
'Confirm signup' template (a link, no usable code). For an email that already
belongs to a vendor_members row, we create or confirm the Auth user first with
the service role, so the same call sends the magic-link template with the
6-digit code the GO login screen asks for. Unknown emails are left alone.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _is_already_registered(err: Exception) -> bool:
    msg = str(err).lower()
    return "already" in msg and ("registered" in msg or "exists" in msg)


async def _is_vendor_email(db, email: str) -> bool:
    try:
        res = await db.table("vendor_members").select("id").ilike("email", email).limit(1).execute()
        return bool(getattr(res, "data", None))
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_vendor_member
        if _is_rest_quota_error(e):
            try:
                return bool(pg_select_vendor_member(email=email))
            except TypeError:
                return False
        raise


def _pg_confirm_email(email: str) -> bool:
    from ..shared.pg_fallback import _pg_connect
    conn = _pg_connect()
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE auth.users SET email_confirmed_at = now() "
                "WHERE lower(email) = %s AND email_confirmed_at IS NULL",
                (email,),
            )
            n = cur.rowcount
        conn.commit()
        return n > 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


async def ensure_confirmed_vendor_auth_user(db, email: str) -> str:
    """Returns 'created', 'confirmed', 'ok' (already fine), 'not_vendor' or 'invalid'."""
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        return "invalid"
    if not await _is_vendor_email(db, email):
        return "not_vendor"
    try:
        await db.auth.admin.create_user({"email": email, "email_confirm": True})
        logger.info("email login prep: created confirmed auth user for vendor email")
        return "created"
    except Exception as e:
        if not _is_already_registered(e):
            logger.warning("email login prep: create_user failed: %s", str(e)[:200])
    try:
        return "confirmed" if _pg_confirm_email(email) else "ok"
    except Exception as e:
        logger.warning("email login prep: confirm failed: %s", str(e)[:200])
        return "ok"
