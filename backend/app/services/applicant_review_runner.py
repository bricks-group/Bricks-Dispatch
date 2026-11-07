"""Load an applicant, run the automatic review, store it, notify Kam.

Kam alone gets the notification (raphael@brxs.ca + his profile phone). The
applicant is never emailed from here. Approve / reject stays manual in OS.
"""

from __future__ import annotations

import logging
import posixpath
from typing import Optional

from ..shared.database import get_db, maybe_single_row
from . import applicant_email as mail
from .applicant_review import review_input_from_member, run_review

logger = logging.getLogger(__name__)

OWNER_EMAIL = "raphael@brxs.ca"
THUMB_LINK_TTL = 7 * 24 * 3600


async def _load_member(member_id: str) -> Optional[dict]:
    try:
        res = await (await get_db()).table("vendor_members").select("*").eq("id", member_id).maybe_single().execute()
        return maybe_single_row(res) or None
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_vendor_member

        if _is_rest_quota_error(e):
            return pg_select_vendor_member(member_id)
        logger.warning("applicant review: member load failed (%s)", type(e).__name__)
        return None


async def _load_availability(member_id: str) -> Optional[dict]:
    try:
        res = await (await get_db()).table("vendor_availability").select("*").eq("member_id", member_id).limit(1).execute()
        rows = res.data or []
        return rows[0] if rows else None
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_vendor_availability

        if _is_rest_quota_error(e):
            try:
                return pg_select_vendor_availability(member_id)
            except Exception:
                return None
        return None


async def _load_hashes() -> list:
    try:
        res = await (await get_db()).table("vendor_applicant_image_hashes").select(
            "member_id,sha256,dhash"
        ).order("created_at", desc=True).limit(5000).execute()
        return res.data or []
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_applicant_hashes

        if _is_rest_quota_error(e):
            return pg_select_applicant_hashes()
        return []


async def _insert(table: str, rows: list) -> None:
    if not rows:
        return
    try:
        await (await get_db()).table(table).insert(rows).execute()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_insert_applicant_rows

        if _is_rest_quota_error(e):
            pg_insert_applicant_rows(table, rows)
        else:
            logger.error("applicant review: insert %s failed (%s)", table, type(e).__name__)


async def _store_thumbs(result: dict) -> None:
    """Upload 640px thumbnails for the picked uploads; thumb_ref -> storage path."""
    from .storage import upload_bytes

    blobs = result.pop("_thumb_bytes", {}) or {}
    for t in result.get("thumbs") or []:
        if t.get("origin") != "upload":
            continue
        orig = t["ref"]
        data = blobs.get(orig)
        if not data:
            continue
        d, base = posixpath.split(orig)
        thumb_path = f"{d}/thumb-{base.rsplit('.', 1)[0]}.jpg"
        if not await upload_bytes(thumb_path, data, content_type="image/jpeg"):
            continue
        t["ref"] = thumb_path
        t["source_ref"] = orig
        for img in result.get("images") or []:
            if img.get("ref") == orig:
                img["thumb_ref"] = thumb_path


async def signed_thumb_urls(result: dict, ttl: int = THUMB_LINK_TTL) -> list:
    from .storage import get_signed_download_url

    out = []
    for t in result.get("thumbs") or []:
        if t.get("origin") == "upload":
            url = await get_signed_download_url(t["ref"], ttl)
        else:
            url = t.get("ref")
        if url:
            out.append({"label": t.get("label"), "url": url})
    return out


async def _owner_phone() -> str:
    from ..shared.job_scope import KAM_MEMBER_ID

    try:
        res = await (await get_db()).table("vendor_members").select("phone").eq("id", KAM_MEMBER_ID).maybe_single().execute()
        return (maybe_single_row(res).get("phone") or "").strip()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_vendor_member

        if _is_rest_quota_error(e):
            try:
                return ((pg_select_vendor_member(KAM_MEMBER_ID) or {}).get("phone") or "").strip()
            except Exception:
                return ""
        return ""


def render_all(member: dict, result: dict, thumb_urls: list) -> dict:
    from ..shared.applicant_source import found_us_line

    prof = member.get("application_profile") or {}
    city = ((prof.get("location") or {}).get("city") if isinstance(prof, dict) else None) or member.get("home_city") or ""
    found = found_us_line(member.get("source"), member.get("source_detail")) or ""
    found = found.replace("Found us via: ", "").strip()
    specs = (prof.get("specialties") if isinstance(prof, dict) else None) or member.get("skills") or ([member.get("specialty")] if member.get("specialty") else [])
    kw = dict(member_id=str(member.get("id")), name=member.get("name") or "", city=city,
              specialties=specs, found_via=found, review=result, thumb_urls=thumb_urls)
    return {
        "subject": mail.subject(member.get("name") or "", result),
        "html": mail.render_html(**kw),
        "text": mail.render_text(**kw),
        "sms": mail.render_sms(member_id=str(member.get("id")), name=member.get("name") or "", city=city, review=result),
    }


async def notify_owner(member: dict, result: dict) -> None:
    """Text Kam's profile phone and email raphael@brxs.ca. Never raises."""
    try:
        rendered = render_all(member, result, await signed_thumb_urls(result))
    except Exception as e:
        logger.error("applicant notify render failed (%s)", type(e).__name__)
        return
    phone = await _owner_phone()
    if phone:
        try:
            from ..notifications import send_dispatch_sms

            send_dispatch_sms(phone, rendered["sms"])
        except Exception as e:
            logger.error("applicant notify SMS failed (%s)", type(e).__name__)
    try:
        from .notifications import send_email_via_resend

        if OWNER_EMAIL != "raphael@brxs.ca":
            raise RuntimeError("applicant email recipient drifted")
        await send_email_via_resend(to=OWNER_EMAIL, subject=rendered["subject"], html=rendered["html"], text=rendered["text"])
    except Exception as e:
        logger.error("applicant notify email failed (%s)", type(e).__name__)


async def review_application(
    member_id: str,
    *,
    notify: bool = True,
    trigger: str = "apply",
    store: bool = True,
    member: Optional[dict] = None,
) -> Optional[dict]:
    """Full run. Returns the stored result (without thumbnail bytes). Never raises."""
    try:
        member = member or await _load_member(member_id)
        if not member:
            return None
        availability = await _load_availability(member_id)
        inp = review_input_from_member(member, availability)
        result = await run_review(inp, other_hashes=await _load_hashes())
        if store:
            await _store_thumbs(result)
        else:
            result.pop("_thumb_bytes", None)
        if store:
            await _insert("vendor_applicant_reviews", [{
                "member_id": member_id,
                "verdict": result["verdict"],
                "needs_samples": bool(result.get("needs_samples")),
                "result": result,
                "model": result.get("model"),
                "cost_usd": result.get("cost_usd"),
                "trigger": trigger,
            }])
            seen = set()
            hash_rows = []
            for img in result.get("images") or []:
                if img.get("sha256") and img["sha256"] not in seen:
                    seen.add(img["sha256"])
                    hash_rows.append({"member_id": member_id, "source": img.get("origin") or "upload",
                                      "ref": (img.get("ref") or "")[:500], "sha256": img["sha256"], "dhash": img.get("dhash") or ""})
            await _insert("vendor_applicant_image_hashes", hash_rows)
        if notify:
            await notify_owner(member, result)
        logger.info("applicant review member=%s verdict=%s score=%s cost=%s", member_id[:8], result["verdict"], result.get("score"), result.get("cost_usd"))
        return result
    except Exception:
        logger.exception("applicant review failed member=%s", (member_id or "")[:8])
        return None


async def pending_without_review(limit: int = 5) -> list:
    """Pending photographer ids with no row in vendor_applicant_reviews (oldest first)."""
    try:
        db = await get_db()
        pend = await db.table("vendor_members").select("id,vendor_type,role,title,application_profile,created_at").eq(
            "onboarding_status", "pending"
        ).order("created_at").limit(200).execute()
        from .vendor_profile import member_vendor_type
        ids = [r["id"] for r in (pend.data or []) if member_vendor_type(r) == "photographer"]
        if not ids:
            return []
        done = await db.table("vendor_applicant_reviews").select("member_id").in_("member_id", ids).execute()
        have = {r["member_id"] for r in (done.data or [])}
        return [i for i in ids if i not in have][:limit]
    except Exception as e:
        logger.warning("applicant review backlog lookup failed (%s)", type(e).__name__)
        return []
