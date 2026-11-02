"""Link a vendor_members row after an emailed-code (OTP) sign-in.

Runs on the shared service-role client, only after Supabase verified the code,
so RLS never hides an unlinked row (auth_user_id NULL) from the lookup or blocks
the link. Never runs as the user's session.

Rules (2026-11-02):
  - candidates: id == user, auth_user_id == user, or exact verified email
  - an email row linked to a DIFFERENT auth user is never used or relinked
  - the picked row is linked only while auth_user_id IS NULL (conditional update)
  - no row at all -> create the default Photographer row (old behaviour), unless
    the email belongs to someone else's linked row (no duplicate, nothing claimed)
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _norm(v) -> str:
    return str(v or "").strip().lower()


async def link_member_after_otp(db, user_id: str, email: str, name: str = "") -> dict:
    """Returns {'member': row|None, 'action': 'existing'|'linked'|'created'|'conflict'}."""
    from ..shared.job_scope import KAM_EMAILS, KAM_MEMBER_ID, pick_canonical_member

    uid = str(user_id or "")
    email_l = _norm(email)
    rows: list[dict] = []
    seen: set[str] = set()
    conflict = False

    def add(row):
        if isinstance(row, dict) and row.get("id") and str(row["id"]) not in seen:
            seen.add(str(row["id"]))
            rows.append(row)

    res = await db.table("vendor_members").select("*").eq("id", uid).maybe_single().execute()
    add(getattr(res, "data", None) if res is not None else None)
    res = await db.table("vendor_members").select("*").eq("auth_user_id", uid).limit(5).execute()
    for row in getattr(res, "data", None) or []:
        if isinstance(row, dict) and str(row.get("auth_user_id") or "") == uid:
            add(row)
    if email_l:
        res = await db.table("vendor_members").select("*").ilike("email", email_l).limit(10).execute()
        for row in getattr(res, "data", None) or []:
            if not isinstance(row, dict) or _norm(row.get("email")) != email_l:
                continue  # ilike wildcards: exact address only
            linked = str(row.get("auth_user_id") or "")
            if linked and linked != uid and str(row.get("id") or "") != uid:
                conflict = True  # someone else's row: never claim it
                continue
            add(row)
    if email_l in KAM_EMAILS or uid.lower().startswith("0c3fa7c2"):
        res = await db.table("vendor_members").select("*").eq("id", KAM_MEMBER_ID).maybe_single().execute()
        add(getattr(res, "data", None) if res is not None else None)

    picked: Optional[dict] = pick_canonical_member(rows, uid, email_l)
    if picked:
        if (not picked.get("auth_user_id") and str(picked.get("id") or "") != str(KAM_MEMBER_ID)
                and _norm(picked.get("email")) == email_l):
            await db.table("vendor_members").update({"auth_user_id": uid}).eq(
                "id", picked["id"]).is_("auth_user_id", "null").execute()
            return {"member": {**picked, "auth_user_id": uid}, "action": "linked"}
        return {"member": picked, "action": "existing"}
    if conflict:
        logger.warning("otp verify: %s matches a member linked to another auth user; not linked", uid)
        return {"member": None, "action": "conflict"}
    row = {"id": uid, "email": email, "name": name or (email_l.split("@")[0] if email_l else ""),
           "role": "Photographer", "vendor_id": "default_vendor"}
    await db.table("vendor_members").insert(row).execute()
    return {"member": row, "action": "created"}
