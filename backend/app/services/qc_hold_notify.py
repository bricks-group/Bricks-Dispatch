"""Tell the owner when automatic QC holds listing files back.

Kam rule: QC can hold a file back from client delivery. A hold nobody hears
about is a silent short delivery, so text the owner profile phone and email
admin@brxs.ca with the held filenames and reasons. Never raises.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

QC_HOLD_ADMIN_EMAIL = "admin@brxs.ca"
_MAX_LISTED = 12
_MAX_REASON = 140


def _clean_reason(note) -> str:
    text = " ".join(str(note or "").split())
    if not text:
        return "Did not pass the automatic quality check."
    if len(text) > _MAX_REASON:
        cut = text[:_MAX_REASON].rsplit(" ", 1)[0]
        text = (cut or text[:_MAX_REASON]).rstrip(" ,;:") + "…"
    return text


def held_rows(uploads, only_ids: Optional[Iterable[str]] = None) -> list[dict]:
    """Rejected image uploads, optionally limited to ids that just ran QC."""
    limit = {str(i) for i in only_ids} if only_ids is not None else None
    out = []
    for u in uploads or []:
        if not isinstance(u, dict) or u.get("qc_status") != "rejected":
            continue
        # One held still is one photo: bracket siblings are not listing stills,
        # so a stale sibling verdict never inflates the notice. Visit/3D files
        # that fail container QC are real holds and stay in the notice (as files).
        if str(u.get("provider") or "").strip().lower() == "bracket_sibling":
            continue
        uid = str(u.get("id") or "")
        if limit is not None and uid not in limit:
            continue
        out.append(u)
    return out


def photos_only(rows) -> bool:
    """True when every held row is a listing photo (not a video / floor plan)."""
    from .media_intake import is_photo_file

    return all(
        is_photo_file(u.get("filename"), u.get("mime_type"), u.get("media_type"))
        for u in (rows or [])
    )


def _noun(held_count: int, photos: bool) -> str:
    base = "photo" if photos else "file"
    return base if held_count == 1 else f"{base}s"


def qc_hold_sms_body(address: str, held_count: int, photos: bool = True) -> str:
    place = (address or "").strip() or "a property"
    noun = _noun(held_count, photos)
    return (
        f"Bricks QC held back {held_count} listing {noun} at {place}. "
        "They were not sent to the client. Details went to the admin inbox."
    )


def qc_hold_email_subject(held_count: int, photos: bool = True) -> str:
    return f"Bricks QC held back {held_count} listing {_noun(held_count, photos)}"


def qc_hold_email_body(job: dict, rows: list[dict]) -> str:
    from .notifications import short_booking_ref

    job = job or {}
    address = (job.get("address") or "").strip() or "n/a"
    when = (job.get("scheduled_date") or "").strip() or "n/a"
    booking_ref = short_booking_ref(job.get("booking_id"))
    noun = "photos" if photos_only(rows) else "files"
    lines = [
        f"Automatic QC held these listing {noun} back. They were not sent to the client.",
        f"Address: {address}",
        f"Date: {when}",
        f"Booking ref: {booking_ref}",
        "",
    ]
    for u in rows[:_MAX_LISTED]:
        name = (u.get("filename") or "").strip() or "unnamed file"
        lines.append(f"• {name}: {_clean_reason(u.get('qc_notes'))}")
    extra = len(rows) - _MAX_LISTED
    if extra > 0:
        lines.append(f"• and {extra} more")
    lines.append("")
    lines.append("Re-edit or re-shoot them, then release from the desk.")
    return "\n".join(lines)


async def _owner_phone(db) -> str:
    from ..shared.job_scope import KAM_MEMBER_ID

    try:
        res = await db.table("vendor_members").select("phone").eq(
            "id", KAM_MEMBER_ID
        ).maybe_single().execute()
        return ((getattr(res, "data", None) or {}).get("phone") or "").strip()
    except Exception as e:
        logger.error("QC hold owner phone lookup failed: %s", type(e).__name__)
        return ""


async def _job_row(db, job_id: str) -> dict:
    try:
        res = await db.table("jobs").select(
            "id, address, scheduled_date, booking_id"
        ).eq("id", job_id).maybe_single().execute()
        return getattr(res, "data", None) or {}
    except Exception as e:
        logger.error("QC hold job lookup failed for %s: %s", job_id, type(e).__name__)
        return {}


async def _uploads(db, job_id: str) -> list[dict]:
    try:
        res = await db.table("uploads").select(
            "id, filename, qc_status, qc_notes, provider"
        ).eq("job_id", job_id).execute()
        return getattr(res, "data", None) or []
    except Exception as e:
        logger.error("QC hold uploads lookup failed for %s: %s", job_id, type(e).__name__)
        return []


async def notify_owner_qc_hold(
    db,
    job_id: str,
    qc_counts: Optional[dict],
    only_ids: Optional[Iterable[str]] = None,
) -> bool:
    """SMS owner + email admin@brxs.ca when QC rejected listing stills.

    only_ids limits the notice to uploads that just ran QC (late-fold), so a
    re-run does not repeat an earlier hold. Returns True when a notice went
    out on at least one channel. Never raises.
    """
    try:
        if not isinstance(qc_counts, dict) or int(qc_counts.get("rejected") or 0) <= 0:
            return False
        rows = held_rows(await _uploads(db, job_id), only_ids=only_ids)
        if not rows:
            return False
        job = await _job_row(db, job_id)
        photos = photos_only(rows)
        sent = False

        phone = await _owner_phone(db)
        if phone:
            try:
                from ..notifications import send_dispatch_sms

                if send_dispatch_sms(phone, qc_hold_sms_body(job.get("address") or "", len(rows), photos)):
                    sent = True
            except Exception as e:
                logger.error("QC hold SMS failed for job %s: %s", job_id, type(e).__name__)

        try:
            from .notifications import _build_html_email, send_email_via_resend

            subject = qc_hold_email_subject(len(rows), photos)
            ok = await send_email_via_resend(
                to=QC_HOLD_ADMIN_EMAIL,
                subject=subject,
                html=_build_html_email(subject, qc_hold_email_body(job, rows)),
            )
            sent = sent or bool(ok)
        except Exception as e:
            logger.error("QC hold email failed for job %s: %s", job_id, type(e).__name__)
        return sent
    except Exception as e:
        logger.error("QC hold notify failed for job %s: %s", job_id, type(e).__name__)
        return False
