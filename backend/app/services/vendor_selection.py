import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

import httpx

from ..shared.database import get_db, maybe_single_row
from ..shared.geospatial import haversine_km as _haversine_km
from ..shared.schemas import Job, VendorMember
from .webhooks import send_dispatch_status_update_webhook
from ..integrations.bricks_ai_dispatch import bricks_smart_assign, _get_weather_context
from .vendor_profile import (
    has_advanced_rpas,
    is_drone_job as _is_drone_job,
    is_platform_owner,
    member_eligible_for_job,
    min_tier_for_job,
    ORG_VENDOR_ID,
    preferred_tier_for_job,
    specialty_matches_job,
    travel_within_cap,
    _missing_column,
)
from .shoot_duration import (
    estimate_shoot_duration_minutes,
    previous_job_coords,
    vendor_slot_conflicts,
    _slot_bounds,
)

logger = logging.getLogger(__name__)

OPS_EMAIL = "ops@brxs.ca"


async def _mark_dispatch_failed(job_id: str) -> None:
    """Stamp jobs.status=dispatch_failed so admin /dispatch-failures can see + retry it.

    Historical bug: failure wrote status=unassigned while the ops queue only
    listed dispatch_failed — paid first bookings with no eligible vendor were
    invisible to the recovery path.

    Fair-Use: when PostgREST returns 402, write via Postgres (pg_update_job).
    Never raises.
    """
    from ..shared.pg_fallback import _is_rest_quota_error, pg_update_job

    payload = {
        "status": "dispatch_failed",
        "assignee_id": "",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await (await get_db()).table("jobs").update(payload).eq("id", job_id).execute()
    except Exception as e:
        if _is_rest_quota_error(e):
            try:
                if pg_update_job(job_id, payload):
                    return
            except Exception as pg_e:
                logger.error(f"pg_update_job dispatch_failed failed for {job_id}: {pg_e}")
        logger.error(f"Failed to mark job {job_id} dispatch_failed: {e}")


async def _notify_owner_no_vendor(job: Job, error_message: str) -> None:
    """SMS + in-app to Kam when auto-assign cannot place a paid job. Never raises."""
    from ..shared.job_scope import KAM_MEMBER_ID

    address = (getattr(job, "address", None) or "").strip() or "a property"
    when = (getattr(job, "scheduled_date", None) or "").strip()
    reason = (error_message or "no eligible vendor").strip()
    body_sms = f"Bricks: no photographer for {address}"
    if when:
        body_sms += f" on {when}"
    body_sms += ". Open GO → Dispatch failures to assign."

    phone = ""
    try:
        res = await (await get_db()).table("vendor_members").select("phone,email").eq(
            "id", KAM_MEMBER_ID
        ).maybe_single().execute()
        phone = (maybe_single_row(res).get("phone") or "").strip()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_vendor_member

        if _is_rest_quota_error(e):
            try:
                member = pg_select_vendor_member(KAM_MEMBER_ID) or {}
                phone = (member.get("phone") or "").strip()
            except Exception as pg_e:
                logger.error(f"Owner phone pg fallback failed for job {job.id}: {pg_e}")
        if not phone:
            logger.error(f"Owner phone lookup failed for job {job.id}: {e}")

    if phone:
        try:
            from ..notifications import send_dispatch_sms
            send_dispatch_sms(phone, body_sms)
        except Exception as e:
            logger.error(f"Owner no-vendor SMS failed for job {job.id}: {e}")

    try:
        from .notifications import create_notification
        await create_notification(
            title="Dispatch failed — needs assignment",
            body=f"{address}" + (f" · {when}" if when else "") + f"\n{reason}",
            ntype="dispatch_failed",
            data={"job_id": job.id, "booking_id": getattr(job, "booking_id", None) or ""},
            member_id=KAM_MEMBER_ID,
            send_push=True,
        )
    except Exception as e:
        logger.error(f"Owner no-vendor in-app notify failed for job {job.id}: {e}")


HOLD_ADMIN_EMAIL = "admin@brxs.ca"


def jev_hold_sms_body(address: str, scheduled_date: str, yes_probability) -> str:
    """Short owner text. No email address and no new phone number."""
    place = (address or "").strip() or "a property"
    when = (scheduled_date or "").strip()
    if isinstance(yes_probability, float):
        probability = f"{yes_probability:.2f}"
    else:
        probability = "n/a"
    when_bit = f" on {when}" if when else ""
    return (
        f"Bricks held a booking at {place}{when_bit}. "
        f"Jev yes-probability {probability}. It is unassigned."
    )


def jev_hold_email_subject() -> str:
    return "Bricks held a booking"


def jev_hold_email_body(job: dict, yes_probability) -> str:
    from .notifications import short_booking_ref

    job = job or {}
    if isinstance(yes_probability, float):
        probability = f"{yes_probability:.2f}"
    else:
        probability = "n/a"
    address = (job.get("address") or "").strip() or "n/a"
    when = (job.get("scheduled_date") or "").strip() or "n/a"
    booking_ref = short_booking_ref(job.get("booking_id"))
    return (
        "Bricks held a booking for a person. It is unassigned.\n"
        f"Address: {address}\n"
        f"Date: {when}\n"
        f"Booking ref: {booking_ref}\n"
        f"Jev yes-probability: {probability}"
    )


async def notify_owner_jev_hold(db, job: dict, yes_probability) -> None:
    """SMS the owner profile phone and email admin@brxs.ca. Never raises.

    A failed lookup, SMS, or email must not assign the held job.
    """
    from ..shared.job_scope import KAM_MEMBER_ID

    job = job or {}
    job_id = job.get("id")
    sms_body = jev_hold_sms_body(
        job.get("address") or "",
        job.get("scheduled_date") or "",
        yes_probability,
    )
    phone = ""
    try:
        res = await db.table("vendor_members").select("phone").eq(
            "id", KAM_MEMBER_ID
        ).maybe_single().execute()
        phone = (maybe_single_row(res).get("phone") or "").strip()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_vendor_member

        if _is_rest_quota_error(e):
            try:
                member = pg_select_vendor_member(KAM_MEMBER_ID) or {}
                phone = (member.get("phone") or "").strip()
            except Exception as pg_e:
                logger.error("Owner phone pg fallback failed for held job %s: %s", job_id, pg_e)
        if not phone:
            logger.error("Owner phone lookup failed for held job %s: %s", job_id, type(e).__name__)

    if phone:
        try:
            from ..notifications import send_dispatch_sms
            send_dispatch_sms(phone, sms_body)
        except Exception as e:
            logger.error("Jev hold SMS failed for job %s: %s", job_id, type(e).__name__)

    try:
        from .notifications import _build_html_email, send_email_via_resend

        subject = jev_hold_email_subject()
        body = jev_hold_email_body(job, yes_probability)
        await send_email_via_resend(
            to=HOLD_ADMIN_EMAIL,
            subject=subject,
            html=_build_html_email(subject, body),
        )
    except Exception as e:
        logger.error("Jev hold email failed for job %s: %s", job_id, type(e).__name__)


def vendor_application_fit_line(yes_probability, needs_review: bool = False) -> str:
    """Plain sentence for a person. Empty when there is no score and nothing to flag.

    The number is how sure the check is that the applicant is a poor fit.
    Above 0.7 is a weak fit. A portfolio nobody could open, or a location that
    may not be in the GTA, is "needs review", never a fit. No name, no decimal.
    """
    if isinstance(yes_probability, float) and yes_probability > 0.7:
        return "This looks like a weak fit."
    if needs_review:
        return "This needs review."
    if isinstance(yes_probability, float):
        return "This does not look like a weak fit."
    return ""


def _human_reason_lines(yes_probability, reasons) -> list:
    """Bullets only when there is a score. Drop jargon, names, and decimals."""
    if not isinstance(yes_probability, float):
        return []
    import re
    decimal = re.compile(r"\d+\.\d+")
    banned = ("jev", "poor-fit", "yes-probability")
    lines = []
    for reason in reasons or ():
        line = " ".join(str(reason or "").split())
        if line.startswith("- "):
            line = line[2:].strip()
        if not line:
            continue
        low = line.lower()
        if any(word in low for word in banned) or decimal.search(line):
            continue
        if not line.endswith("."):
            line += "."
        lines.append(line)
    return lines


# Strict GTA: Toronto and the four GTA regions. Wider Ontario is "may not be in the GTA".
_GTA_PLACES = {
    "gta", "greater toronto", "greater toronto area", "toronto", "etobicoke",
    "scarborough", "north york", "east york", "york", "downtown toronto",
    "mississauga", "brampton", "caledon", "peel",
    "oakville", "burlington", "milton", "halton hills", "georgetown", "halton",
    "vaughan", "markham", "richmond hill", "aurora", "newmarket",
    "king city", "stouffville", "whitchurch", "east gwillimbury",
    "georgina", "woodbridge", "concord", "thornhill", "maple", "unionville",
    "pickering", "ajax", "whitby", "oshawa", "clarington", "bowmanville",
    "uxbridge", "durham",
}
# Words that add nothing about where in Ontario someone is.
_LOCATION_FILLER = {
    "on", "ont", "ontario", "canada", "ca", "area", "the", "downtown",
    "based", "in", "near", "around", "city", "of", "region", "greater", "gta",
}


def _location_key(text: str) -> str:
    import unicodedata
    folded = unicodedata.normalize("NFKD", text or "")
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join(folded.lower().replace(".", " ").split())


def location_check(city: str) -> tuple:
    """(bullet, unclear). Self-reported text only; nothing is geocoded."""
    import re
    raw = " ".join(str(city or "").split())
    if not raw:
        return ("No city given.", True)
    key = _location_key(raw)
    parts = [p.strip() for p in re.split(r"[/,;|&+()\\-]| and | or ", key) if p.strip()]
    gta = 0
    other = 0
    for part in parts:
        if part in _GTA_PLACES:
            gta += 1
            continue
        words = [w for w in part.split() if w not in _LOCATION_FILLER]
        phrase = " ".join(words)
        if not phrase:
            if "gta" in part.split() or "greater" in part.split():
                gta += 1
            continue
        if phrase in _GTA_PLACES:
            gta += 1
        else:
            other += 1
    if gta and not other:
        return (f"Self-reported city: {raw} (GTA).", False)
    return (f"Location unclear / may not be in the GTA (says: {raw}).", True)


def _short(text: str, limit: int = 100) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def equipment_line(equipment) -> str:
    items = [" ".join(str(i or "").split()) for i in (equipment or ())]
    items = [i for i in items if i]
    if not items:
        return "No equipment listed."
    return f"Says they have: {_short(', '.join(items))}."


def drone_line(drone, level=None) -> str:
    if not drone:
        return "No drone certificate claimed."
    lvl = str(level or "").strip().lower()
    if lvl in ("basic", "advanced"):
        return f"Says they hold a {lvl} drone (RPAS) certificate, not verified."
    return "Says they are drone certified, not verified."


def portfolio_check_lines(check, urls) -> tuple:
    """(bullets, needs_review). Only what was actually seen.

    There is no image or vision model on this path, so a page that opens is
    "opened, work not reviewed". Nothing here says the work is a fit.
    """
    urls = list(urls or ())
    if not urls:
        return (["No portfolio link to check."], True)
    if check is None:
        return ([f"Portfolio not checked (link: {', '.join(urls)})."], True)
    lines = []
    opened = check.opened
    failed = check.failed
    if opened:
        lines.append(
            f"Portfolio opened, work not reviewed (link: {', '.join(c.url for c in opened)})."
        )
    if failed:
        lines.append(f"Portfolio couldn't be opened (link: {', '.join(c.url for c in failed)}).")
    if check.instagram:
        lines.append("Instagram link provided, not reviewed.")
    unchecked = [u for u in urls if u not in {c.url for c in check.links}]
    if unchecked:
        lines.append(f"Not checked (link: {', '.join(unchecked)}).")
    return (lines, check.needs_review)


def vendor_application_facts(
    city: str,
    portfolio_links=(),
    *,
    portfolio_check=None,
    equipment=None,
    drone=None,
    drone_level=None,
) -> tuple:
    """(bullets, needs_review) from what the applicant typed plus the link check.

    Equipment, city, and drone are self-reported and worded that way.
    None for equipment/drone means the caller did not pass them; skip the bullet.
    """
    urls = _public_portfolio_urls(portfolio_links)
    lines, review = portfolio_check_lines(portfolio_check, urls)
    loc, unclear = location_check(city)
    lines.append(loc)
    if equipment is not None:
        lines.append(equipment_line(equipment))
    if drone is not None:
        lines.append(drone_line(drone, drone_level))
    return (lines, review or unclear)


def vendor_application_sms_body(
    name: str,
    city: str,
    vendor_type: str,
    yes_probability,
    reasons=(),
    portfolio_links=(),
    facts=(),
    needs_review: bool = False,
) -> str:
    """Owner text. Fit sentence and bullets. Public portfolio URL on its own line."""
    who = (name or "").strip() or "Someone"
    where = (city or "").strip() or "an unknown city"
    kind = (vendor_type or "").strip() or "vendor"
    fit = vendor_application_fit_line(yes_probability, needs_review)
    head = f"New Bricks {kind} application: {who} in {where}."
    bullets = list(facts or ()) + _human_reason_lines(yes_probability, reasons)
    urls = _public_portfolio_urls(portfolio_links)
    if not fit and not urls and not bullets:
        return f"{head} Pending your approval."
    parts = [f"{head} {fit}"] if fit else [head]
    parts.extend(f"- {line}" for line in bullets)
    parts.extend(urls)
    parts.append("Pending your approval.")
    return "\n".join(parts)


def vendor_application_email_subject(name: str) -> str:
    who = (name or "").strip() or "Applicant"
    return f"New vendor application pending your approval: {who}"


def _public_portfolio_urls(portfolio_links) -> list:
    """Public http(s) portfolio URLs only. Not an email address and not a phone."""
    urls = []
    seen = set()
    for raw in portfolio_links or ():
        url = " ".join(str(raw or "").split())
        if not url.lower().startswith(("http://", "https://")):
            continue
        if "@" in url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _portfolio_lines(portfolio_links) -> list:
    urls = _public_portfolio_urls(portfolio_links)
    if not urls:
        return []
    return ["Portfolio:", *urls]


def vendor_application_email_body(
    name: str,
    city: str,
    vendor_type: str,
    yes_probability,
    reasons=(),
    portfolio_links=(),
    facts=(),
    needs_review: bool = False,
    source=None,
    source_detail=None,
) -> str:
    who = (name or "").strip() or "Someone"
    where = (city or "").strip() or "an unknown city"
    kind = (vendor_type or "").strip() or "vendor"
    lines = [f"{who} in {where} applied as a {kind}."]
    from ..shared.applicant_source import found_us_line

    found = found_us_line(source, source_detail)
    if found:
        lines.append(found)
    fit = vendor_application_fit_line(yes_probability, needs_review)
    if fit:
        lines.append(fit)
    lines.extend(f"- {line}" for line in facts or ())
    lines.extend(f"- {line}" for line in _human_reason_lines(yes_probability, reasons))
    lines.extend(_portfolio_lines(portfolio_links))
    lines.append("Pending your approval.")
    return "\n".join(lines)


async def notify_owner_vendor_application(
    db,
    *,
    name: str,
    city: str,
    vendor_type: str,
    yes_probability,
    reasons=(),
    portfolio_links=(),
    portfolio_check=None,
    equipment=None,
    drone=None,
    drone_level=None,
    source=None,
    source_detail=None,
) -> None:
    """SMS the owner profile phone and email admin@brxs.ca. Never raises.

    A failed lookup, SMS, or email leaves the application pending. This does
    not approve or reject.
    """
    from ..shared.job_scope import KAM_MEMBER_ID

    try:
        facts, needs_review = vendor_application_facts(
            city,
            portfolio_links,
            portfolio_check=portfolio_check,
            equipment=equipment,
            drone=drone,
            drone_level=drone_level,
        )
    except Exception as e:
        logger.error("Vendor application facts failed: %s", type(e).__name__)
        facts, needs_review = [], True
    sms_body = vendor_application_sms_body(
        name, city, vendor_type, yes_probability, reasons, portfolio_links, facts, needs_review
    )
    phone = ""
    try:
        res = await db.table("vendor_members").select("phone").eq(
            "id", KAM_MEMBER_ID
        ).maybe_single().execute()
        phone = (maybe_single_row(res).get("phone") or "").strip()
    except Exception as e:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_select_vendor_member

        if _is_rest_quota_error(e):
            try:
                member = pg_select_vendor_member(KAM_MEMBER_ID) or {}
                phone = (member.get("phone") or "").strip()
            except Exception as pg_e:
                logger.error("Owner phone pg fallback failed for vendor application: %s", type(pg_e).__name__)
        if not phone:
            logger.error("Owner phone lookup failed for vendor application: %s", type(e).__name__)

    if phone:
        try:
            from ..notifications import send_dispatch_sms
            send_dispatch_sms(phone, sms_body)
        except Exception as e:
            logger.error("Vendor application SMS failed: %s", type(e).__name__)

    try:
        from .notifications import _build_html_email, send_email_via_resend

        subject = vendor_application_email_subject(name)
        body = vendor_application_email_body(
            name, city, vendor_type, yes_probability, reasons, portfolio_links, facts, needs_review,
            source=source, source_detail=source_detail,
        )
        # Kam alone. No cc, no bcc, no applicant, no hiring@, no personal Gmail.
        if HOLD_ADMIN_EMAIL != "admin@brxs.ca":
            raise RuntimeError("vendor application email recipient drifted")
        await send_email_via_resend(
            to=HOLD_ADMIN_EMAIL,
            subject=subject,
            html=_build_html_email(subject, body),
        )
    except Exception as e:
        logger.error("Vendor application email failed: %s", type(e).__name__)


async def _escalate_no_vendor(job: Job, attempt_count: int = 1, error_message: str = "") -> None:
    """Record escalation, mark dispatch_failed, email ops, SMS/notify owner."""
    err = error_message or f"No eligible vendor found after {attempt_count} attempt(s)"
    await _mark_dispatch_failed(job.id)

    from ..shared.pg_fallback import _is_rest_quota_error, pg_insert_ops_escalation

    esc_row = {
        "booking_id": job.booking_id,
        "job_id": job.id,
        "escalation_type": "no_vendor_available",
        "attempt_count": attempt_count,
        "error_message": err,
        "status": "open",
    }
    db = await get_db()
    try:
        await db.table("ops_escalations").insert(esc_row).execute()
    except Exception as e:
        if _is_rest_quota_error(e):
            try:
                if pg_insert_ops_escalation(esc_row):
                    pass
                else:
                    logger.error(f"pg_insert_ops_escalation returned False for job {job.id}")
            except Exception as pg_e:
                logger.error(f"pg_insert_ops_escalation failed for job {job.id}: {pg_e}")
        else:
            logger.error(f"Failed to write ops_escalation for job {job.id}: {e}")

    await _notify_owner_no_vendor(job, err)

    resend_key = os.environ.get("RESEND_API_KEY")
    if not resend_key:
        return
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {resend_key}"},
                json={
                    "from": "Bricks GO <noreply@brxs.ca>",
                    "to": [OPS_EMAIL],
                    "subject": f"[OPS] No vendor available — job {job.id}",
                    "html": (
                        f"<p><b>Job:</b> {job.id}<br/>"
                        f"<b>Booking:</b> {job.booking_id}<br/>"
                        f"<b>Address:</b> {job.address}<br/>"
                        f"<b>Package:</b> {job.package_name}<br/>"
                        f"<b>Min tier:</b> {job.min_tier_required}<br/>"
                        f"<b>Attempts:</b> {attempt_count}</p>"
                        f"<p>No eligible vendor could be assigned. Manual assignment required.</p>"
                    ),
                },
            )
    except Exception as e:
        logger.error(f"Resend ops escalation email failed for job {job.id}: {e}")




async def load_occupied_jobs_by_vendor(
    db,
    vendor_member_ids: List[str],
    scheduled_date: str | None = None,
) -> dict[str, list[dict]]:
    """Jobs already on each vendor's book for conflict / travel chaining."""
    occupied: dict[str, list[dict]] = {vid: [] for vid in vendor_member_ids}
    ids = [i for i in vendor_member_ids if i]
    if not ids:
        return occupied
    try:
        q = (
            db.table("jobs")
            .select(
                "id, assignee_id, scheduled_date, time_window_start, time_window_end, "
                "confirmed_arrive_at, selected_slot_start, selected_slot_end, "
                "estimated_duration_min, lat, lng, status, address, city"
            )
            .in_("assignee_id", ids[:80])
            .in_("status", ["assigned", "in_progress", "pending_assignment", "completed"])
        )
        if scheduled_date:
            q = q.eq("scheduled_date", str(scheduled_date)[:10])
        res = await q.limit(400).execute()
        for row in res.data or []:
            aid = row.get("assignee_id")
            if aid in occupied:
                occupied[aid].append(row)
            elif aid:
                occupied.setdefault(aid, []).append(row)
    except Exception as e:
        # Fair-Use 402: PostgREST blocked — read conflicts via Postgres.
        from ..shared.pg_fallback import (
            _is_rest_quota_error,
            pg_select_occupied_jobs_by_assignees,
        )
        if _is_rest_quota_error(e):
            pg_map = pg_select_occupied_jobs_by_assignees(
                ids[:80], scheduled_date=scheduled_date
            )
            for aid, jobs in (pg_map or {}).items():
                if aid in occupied:
                    occupied[aid].extend(jobs)
                elif aid:
                    occupied.setdefault(aid, []).extend(jobs)
            return occupied
        # Soft-drop missing columns (selected_slot_*, estimated_duration_min)
        msg = str(e)
        miss = _missing_column(msg, [
            "selected_slot_start", "selected_slot_end", "estimated_duration_min", "confirmed_arrive_at"
        ])
        if miss:
            try:
                q = (
                    db.table("jobs")
                    .select(
                        "id, assignee_id, scheduled_date, time_window_start, time_window_end, "
                        "lat, lng, status, address, city"
                    )
                    .in_("assignee_id", ids[:80])
                    .in_("status", ["assigned", "in_progress", "pending_assignment", "completed"])
                )
                if scheduled_date:
                    q = q.eq("scheduled_date", str(scheduled_date)[:10])
                res = await q.limit(400).execute()
                for row in res.data or []:
                    aid = row.get("assignee_id")
                    if aid:
                        occupied.setdefault(aid, []).append(row)
            except Exception as e2:
                logger.warning("load_occupied_jobs_by_vendor fallback failed: %s", e2)
        else:
            logger.warning("load_occupied_jobs_by_vendor failed: %s", e)
    return occupied


def filter_assignable_vendors(
    job: Job,
    vendors: List[VendorMember],
    occupied_by_vendor: Optional[dict] = None,
    availability_by_member: Optional[dict] = None,
    calendar_connected: Optional[dict] = None,
    duration_min: Optional[int] = None,
) -> List[VendorMember]:
    """Apply eligibility + slot/travel + manual Availability hard gates."""
    from .vendor_availability_gate import manual_availability_allows

    occupied_by_vendor = occupied_by_vendor or {}
    availability_by_member = availability_by_member or {}
    calendar_connected = calendar_connected or {}
    out: List[VendorMember] = []
    for v in vendors:
        if not member_eligible_for_job(v, job):
            continue
        occupied = occupied_by_vendor.get(v.id) or []
        if vendor_slot_conflicts(job, occupied):
            logger.info("Vendor %s excluded: slot conflict for job %s", v.id, job.id)
            continue
        origin = previous_job_coords(job, occupied)
        if origin is not None:
            cap = travel_within_cap(v, job, origin=origin)
            if cap is False:
                logger.info("Vendor %s excluded: over travel cap from previous job for %s", v.id, job.id)
                continue
        settings = availability_by_member.get(v.id)
        if not manual_availability_allows(
            settings,
            job,
            occupied=occupied,
            duration_min=duration_min,
            has_calendar=bool(calendar_connected.get(v.id)),
        ):
            logger.info("Vendor %s excluded: manual availability for job %s", v.id, job.id)
            continue
        out.append(v)
    return out


def pick_vendor_rule_based(
    job: Job,
    vendors: List[VendorMember],
    occupied_by_vendor: Optional[dict] = None,
    availability_by_member: Optional[dict] = None,
    calendar_connected: Optional[dict] = None,
    duration_min: Optional[int] = None,
) -> Optional[str]:
    """
    Deterministic vendor selection for paid /book and Dispatch.
    Hard gates: approved+active (Aman/pending never), location, package min-tier,
    skills, Advanced RPAS for drone, manual Availability, slot/travel.
    No match → None (job stays unassigned).
    Owner (Kam) ranks first among eligible so he wins matching GTA jobs.
    """
    min_tier = min_tier_for_job(job)
    preferred_tier = preferred_tier_for_job(job)
    drone_job = _is_drone_job(job)

    eligible = filter_assignable_vendors(
        job,
        vendors,
        occupied_by_vendor=occupied_by_vendor,
        availability_by_member=availability_by_member,
        calendar_connected=calendar_connected,
        duration_min=duration_min,
    )

    candidates = [v for v in eligible if v.status == "available"] or eligible

    if not candidates:
        logger.warning(
            f"No vendor meets location/package/tier/skills/RPAS min_tier={min_tier} for job {job.id} "
            f"(job_type={job.job_type} drone={drone_job}). Returning None."
        )
        return None

    def selection_score(v: VendorMember):
        owner_bonus = 1 if is_platform_owner(v) else 0
        specialty_match = 1 if specialty_matches_job(v, job) else 0
        tier_match = 1 if v.tier == preferred_tier else 0
        # Higher tier wins premium/luxury packages when preferred_tier is top.
        tier_rank = v.tier or 1

        # Travel proximity bonus — prefer vendors closer to the job
        proximity_score = 0.0
        if v.home_lat is not None and v.home_lng is not None and job.lat is not None and job.lng is not None:
            dist_km = _haversine_km(v.home_lat, v.home_lng, job.lat, job.lng)
            # Normalize: 0 km → 1.0, 50 km → 0.5, 100+ km → ~0
            proximity_score = max(0.0, 1.0 - dist_km / 100.0)

        # Reliability: penalise cancel_rate plus decline_rate above the 40% neutral band.
        # Declines within the 40% threshold are treated as zero penalty (IC right to refuse).
        _decline_penalty = max(0.0, (v.decline_rate or 0.0) - 0.40)
        _reliability = max(0.0, 1.0 - (v.cancel_rate or 0.0) - _decline_penalty)

        return (
            owner_bonus,
            specialty_match,
            tier_rank,
            tier_match,
            round(v.on_time_rate, 2),
            round(v.quality_rating, 2),
            round(_reliability, 2),
            -v.active_assignments,
            round(proximity_score, 2),
        )

    candidates.sort(key=selection_score, reverse=True)
    chosen = candidates[0]
    logger.info(
        f"Rule-based selection chose vendor {chosen.id} ({chosen.name}) "
        f"tier={chosen.tier} for job {job.id} (min_tier={min_tier}, preferred={preferred_tier})"
    )
    return chosen.id


async def _patch_job_assign(job_id: str, patch: dict) -> None:
    """Update job assign fields; soft-drop columns PostgREST says are missing.

    Fair-Use: when PostgREST returns 402, write via Postgres (pg_update_job).
    """
    from ..shared.pg_fallback import _is_rest_quota_error, pg_update_job

    data = dict(patch)
    last = None
    db = await get_db()
    for _ in range(8):
        try:
            await db.table("jobs").update(data).eq("id", job_id).execute()
            return
        except Exception as e:
            last = e
            if _is_rest_quota_error(e):
                if pg_update_job(job_id, data):
                    return
                raise
            miss = _missing_column(str(e), list(data.keys()))
            if not miss:
                raise
            data.pop(miss, None)
    if last:
        raise last


async def _update_job_assignee(
    job_id: str,
    member_id: str,
    estimated_duration_min: Optional[int] = None,
    job_doc: Optional[dict] = None,
):
    now = datetime.now(timezone.utc).isoformat()
    patch = {
        "assignee_id": member_id,
        "status": "assigned",
        "updated_at": now,
    }
    if estimated_duration_min is not None:
        patch["estimated_duration_min"] = int(estimated_duration_min)
    # Backfill migration-0008 slot clocks when intake soft-dropped them or Booking omitted them.
    doc = dict(job_doc or {})
    if estimated_duration_min is not None and doc.get("estimated_duration_min") is None:
        doc["estimated_duration_min"] = int(estimated_duration_min)
    if not doc.get("selected_slot_start") or not doc.get("selected_slot_end"):
        try:
            from .shoot_duration import selected_slot_iso_pair
            pair = selected_slot_iso_pair(doc, estimated_duration_min)
            if pair:
                patch["selected_slot_start"], patch["selected_slot_end"] = pair
        except Exception as e:
            logger.debug("selected_slot backfill skipped for %s: %s", job_id, e)
    await _patch_job_assign(job_id, patch)

    try:
        from .client_comms import notify_booking_confirmed
        await notify_booking_confirmed(job_id)
    except Exception as e:
        logger.error(f"Booking confirmed notification failed for job {job_id}: {e}")

    try:
        from .notifications import notify_assignment
        res = await (await get_db()).table("jobs").select("*").eq("id", job_id).maybe_single().execute()
        await notify_assignment(maybe_single_row(res) or {"id": job_id}, member_id)
    except Exception as e:
        logger.error(f"Vendor assignment notify failed for job {job_id}: {e}")


_MAX_ASSIGNMENT_ATTEMPTS = 3


async def assign_vendor_for_job(job_id: str, exclude_member_id: Optional[str] = None) -> Optional[str]:
    """
    Orchestrates vendor assignment using Bricks AI with rule-based fallback.
    Retries up to _MAX_ASSIGNMENT_ATTEMPTS times; on final failure writes an
    ops_escalation, marks status=dispatch_failed, emails ops@brxs.ca, and SMS/notifies owner.

    exclude_member_id: skip this vendor member on all attempts (e.g. contractor who no-showed).
    """
    from ..shared.pg_fallback import (
        _is_rest_quota_error,
        pg_select_job_by_id,
        pg_select_vendor_members_by_vendor_ids,
    )

    db = await get_db()
    try:
        res = await db.table("jobs").select("*").eq("id", job_id).maybe_single().execute()
        job_doc = maybe_single_row(res) or None
    except Exception as e:
        if not _is_rest_quota_error(e):
            raise
        job_doc = pg_select_job_by_id(job_id)
    if not job_doc:
        raise ValueError(f"Job {job_id} not found")

    job = Job(**job_doc)

    vendor_id = job_doc.get("vendor_id", ORG_VENDOR_ID)
    # Pool is the photography org (default_vendor) plus any members tagged with
    # this job's vendor_id. Paid /book previously stored Kam's member UUID as
    # jobs.vendor_id; those rows must still see every approved photographer.
    org_ids = list({ORG_VENDOR_ID, vendor_id}) if vendor_id else [ORG_VENDOR_ID]
    try:
        res_members = await db.table("vendor_members").select("*").in_("vendor_id", org_ids).execute()
        rows = res_members.data or []
    except Exception as e:
        if not _is_rest_quota_error(e):
            raise
        rows = pg_select_vendor_members_by_vendor_ids(org_ids)
    vendors = []
    for m in rows:
        try:
            vendors.append(VendorMember(**m))
        except Exception as e:
            logger.warning(f"Skipping vendor_members row {m.get('id')}: {e}")

    # Exclude the specified member (e.g. contractor who no-showed — must not be re-selected)
    if exclude_member_id:
        vendors = [v for v in vendors if v.id != exclude_member_id]

    if not vendors:
        logger.warning(f"No vendor members found for vendor_id {vendor_id}.")
        await _escalate_no_vendor(job, 1, "No vendor members in this vendor account.")
        return None

    # Drone weather gate: if unsafe, block dispatch entirely — the rule-based fallback
    # has no weather awareness, so we must short-circuit before the retry loop.
    if _is_drone_job(job) and job.lat and job.lng and job.scheduled_date:
        try:
            from ..shared.job_timezone import resolve_job_iana
            _weather = await _get_weather_context(
                job.lat, job.lng, job.scheduled_date, resolve_job_iana(job_doc)
            )
            if _weather and _weather.get("drone_unsafe"):
                logger.warning(
                    f"Job {job_id}: drone_unsafe weather ({_weather.get('condition')}, "
                    f"wind={_weather.get('max_wind_kmh')}km/h) — blocking all assignment attempts"
                )
                await _escalate_no_vendor(job, 0, "drone weather unsafe — dispatch blocked")
                return None
        except Exception as _we:
            logger.error(f"Job {job_id}: weather check failed for drone job ({_we}) — blocking dispatch (fail-closed)")
            await _escalate_no_vendor(job, 0, f"drone weather check failed — dispatch blocked: {_we}")
            return None

    duration_min = estimate_shoot_duration_minutes(job)
    occupied_by_vendor = await load_occupied_jobs_by_vendor(
        db, [v.id for v in vendors], scheduled_date=job.scheduled_date or None
    )

    from .vendor_availability_gate import load_availability_by_member, member_has_calendar

    availability_by_member = await load_availability_by_member(db, [v.id for v in vendors])
    calendar_connected: dict = {}
    for v in vendors:
        try:
            calendar_connected[v.id] = await member_has_calendar(db, v.id)
        except Exception:
            calendar_connected[v.id] = False

    # Calendar FreeBusy hard gate (busy=True excludes; None/False continue)
    try:
        from .vendor_calendars import vendor_is_busy
        bounds = _slot_bounds(job, duration_min)
        if bounds:
            filtered = []
            for v in vendors:
                busy = await vendor_is_busy(v, bounds[0], bounds[1], db=db)
                if busy is True:
                    logger.info("Vendor %s excluded: calendar busy for job %s", v.id, job_id)
                    continue
                filtered.append(v)
            vendors = filtered
    except Exception as e:
        logger.warning("Calendar busy gate skipped (continuing with other gates): %s", e)

    min_tier = min_tier_for_job(job)
    eligible_ids = {
        v.id
        for v in filter_assignable_vendors(
            job,
            vendors,
            occupied_by_vendor=occupied_by_vendor,
            availability_by_member=availability_by_member,
            calendar_connected=calendar_connected,
            duration_min=duration_min,
        )
    }

    actual_attempts = 0
    for attempt in range(1, _MAX_ASSIGNMENT_ATTEMPTS + 1):
        actual_attempts = attempt
        _had_exception = False
        try:
            chosen_id = await bricks_smart_assign(job, vendors, occupied_by_vendor=occupied_by_vendor)
            if chosen_id and chosen_id in eligible_ids:
                logger.info(f"Job {job_id} assigned via Bricks AI to {chosen_id} (attempt {attempt})")
                await _update_job_assignee(job_id, chosen_id, estimated_duration_min=duration_min, job_doc=job_doc)
                return chosen_id
            if chosen_id:
                logger.warning(
                    f"Bricks AI suggested vendor {chosen_id} which is ineligible "
                    f"(not found or below min_tier={min_tier}). Falling back (attempt {attempt})."
                )
        except Exception:
            _had_exception = True
            logger.exception(f"Bricks AI assignment failed for job {job_id} (attempt {attempt})")

        chosen_id = pick_vendor_rule_based(
            job,
            vendors,
            occupied_by_vendor=occupied_by_vendor,
            availability_by_member=availability_by_member,
            calendar_connected=calendar_connected,
            duration_min=duration_min,
        )
        if chosen_id is not None:
            logger.info(f"Job {job.id} assigned via rule-based fallback to {chosen_id} (attempt {attempt})")
            await _update_job_assignee(job_id, chosen_id, estimated_duration_min=duration_min, job_doc=job_doc)
            return chosen_id

        logger.warning(f"No vendor assigned for job {job_id} (attempt {attempt}/{_MAX_ASSIGNMENT_ATTEMPTS}).")
        if not _had_exception:
            # Both scorers are deterministic — retrying identical inputs won't change the result.
            # Only loop again if a transient exception occurred in bricks_smart_assign.
            break

    await _escalate_no_vendor(job, actual_attempts)
    return None
