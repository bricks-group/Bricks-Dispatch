"""
Client communications agent — triggered at key job lifecycle events.

www.brxs.ca /book stamps jobs.agent_email (and a media_booking_leads id as
booking_id). Companion Book uses a real bookings row id and may omit
agent_email. Prefer Resend to jobs.agent_email when present so paid www
books actually get confirm / en-route / deliver mail. Fall back to the
Booking service only when agent_email is missing and booking_id looks like
a Companion booking.

Events handled:
  booking_confirmed      — when job is assigned to a photographer
  photographer_en_route  — when job moves to in_progress
  job_delivered          — capture compile + photo post_production
"""
import html as _html
import logging
import os

import httpx

from ..shared.auth import get_own_internal_api_key

logger = logging.getLogger(__name__)

_EMAIL_FONT_STACK = (
    "'General Sans','Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',"
    "Roboto,Helvetica,Arial,sans-serif"
)
_EMAIL_LOGO_URL = "https://www.brxs.ca/bricks-mark.png"
_OS_DESK_URL = (os.environ.get("OS_WEB_URL") or "https://app.brxs.ca").rstrip("/")


def _listing_desk_url(job_id: str | None) -> str:
    """Deep link to the listing file (property_id == jobs.id). Never invent a path."""
    jid = (job_id or "").strip()
    if not jid:
        return f"{_OS_DESK_URL}/properties"
    return f"{_OS_DESK_URL}/properties/{jid}"




def _branded_card(title: str, body_inner: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:{_EMAIL_FONT_STACK};background:#f4f1eb;padding:24px">
<div style="max-width:480px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;padding:16px 24px 0 24px">
<tr>
<td width="16" height="16" style="width:16px;height:16px"><img src="{_EMAIL_LOGO_URL}" width="16" height="16" alt="" style="display:block;width:16px;height:16px"></td>
<td width="7" style="width:7px;font-size:0;line-height:0">&nbsp;</td>
<td style="font-family:{_EMAIL_FONT_STACK};font-size:14px;font-weight:500;letter-spacing:-0.5px;color:#14110E">bricks</td>
</tr>
</table>
<div style="background:#d2542b;padding:16px 24px;margin-top:12px">
<h1 style="color:#fff;margin:0;font-size:20px;font-weight:600;font-family:{_EMAIL_FONT_STACK}">{_html.escape(title)}</h1>
</div>
<div style="padding:24px">
{body_inner}
<p style="margin-top:24px;font-size:13px;color:#999;line-height:1.5">Bricks · brxs.ca</p>
</div>
</div></body></html>"""


async def _call_booking_comms(path: str, payload: dict) -> bool:
    """POST an internal comms event to the Booking service."""
    api_key = get_own_internal_api_key()
    booking_url = os.environ.get("BOOKING_API_URL") or os.environ.get("BOOKING_CALLBACK_URL")
    if not api_key:
        logger.warning(f"INTERNAL_API_KEYS not set — skipping comms call to {path}")
        return False
    if not booking_url:
        logger.warning(f"BOOKING_API_URL not set — skipping comms call to {path}")
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{booking_url.rstrip('/')}{path}",
                headers={"X-Internal-Api-Key": api_key},
                json=payload,
            )
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"Booking comms call to {path} failed: {e}")
        return False


async def register_property_visit(
    property_id: str,
    storage_path: str = "",
    title: str = "Virtual Visit",
    embed_url: str | None = None,
    visit_type: str | None = None,
    rooms: list | None = None,
    manifest: dict | None = None,
    qc: dict | None = None,
) -> bool:
    """POST a finished capture to Bricks-OS property_visits via
    POST /api/internal/visit-ready.

    type=file + storage_path — Gaussian splat / .ply virtual visit (VisitViewer gsplat).
    type=url + embed_url — hosted 3D tour walkthrough (VisitViewer iframe). Do not
    send phone-tour HTML as type=file; that path is the splat viewer.

    rooms/manifest/qc (owned Visit pipeline): multi-room streamed Visit —
    OS stores rooms in property_visit_rooms (room-by-room streaming).

    Same INTERNAL_API_KEYS / X-Internal-Api-Key convention as _call_booking_comms.
    """
    api_key = get_own_internal_api_key()
    os_url = (os.environ.get("BRICKS_OS_API_URL") or "").strip().rstrip("/")
    if not os_url:
        os_url = "https://api.brxs.ca"
    if not api_key:
        logger.warning("INTERNAL_API_KEYS not set — skipping visit-ready call")
        return False
    body = {"property_id": property_id, "title": title}
    if storage_path:
        body["storage_path"] = storage_path
    if embed_url:
        body["embed_url"] = embed_url
        body["type"] = visit_type or "url"
    elif visit_type:
        body["type"] = visit_type
    if "type" not in body:
        body["type"] = "file"
    if rooms:
        body["rooms"] = rooms
    if manifest:
        body["manifest"] = manifest
    if qc:
        body["qc"] = qc
    if body["type"] == "file" and not storage_path:
        logger.warning("visit-ready skipped — type=file needs storage_path")
        return False
    if body["type"] == "url" and not embed_url:
        logger.warning("visit-ready skipped — type=url needs embed_url")
        return False
    # One retry on transient network / 5xx — avoid hollow listing when OS blips.
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{os_url}/api/internal/visit-ready",
                    headers={"X-Internal-Api-Key": api_key},
                    json=body,
                )
                resp.raise_for_status()
                return True
        except Exception as e:
            last_err = e
            if attempt == 0:
                logger.warning(
                    "visit-ready retry for property %s after: %s", property_id, e
                )
                continue
    logger.error(f"visit-ready call for property {property_id} failed: {last_err}")
    return False


async def _load_job(job_id: str, columns: str) -> dict:
    """Load a job row for agent emails. REST first; Postgres on Fair-Use 402."""
    from ..shared.database import get_db
    from ..shared.pg_fallback import _is_rest_quota_error, pg_select_job_by_id

    try:
        db = await get_db()
        from ..shared.database import maybe_single_row
        res = await db.table("jobs").select(columns).eq("id", job_id).maybe_single().execute()
        data = maybe_single_row(res)
        if data:
            return data
        # Empty body can be a silent Fair-Use failure — try Postgres once.
        row = pg_select_job_by_id(job_id)
        return row or {}
    except Exception as e:
        if _is_rest_quota_error(e):
            row = pg_select_job_by_id(job_id)
            if row:
                logger.warning("client_comms _load_job PG fallback ok job=%s", job_id)
            return row or {}
        logger.warning("_load_job failed for %s: %s", job_id, e)
        return {}


def _photographer_name_from(member_row: dict | None, fallback: str = "your Bricks photographer") -> str:
    name = ((member_row or {}).get("name") or "").strip()
    return name or fallback


async def _resolve_photographer_name(assignee_id: str | None, fallback: str) -> str:
    if not assignee_id:
        return fallback
    from ..shared.pg_fallback import _is_rest_quota_error, pg_select_vendor_member
    try:
        from ..shared.database import get_db
        db = await get_db()
        from ..shared.database import maybe_single_row
        res = await db.table("vendor_members").select("name").eq("id", assignee_id).maybe_single().execute()
        row = maybe_single_row(res)
        if row:
            return _photographer_name_from(row, fallback)
        member = pg_select_vendor_member(actor_id=assignee_id)
        return _photographer_name_from(member, fallback)
    except Exception as exc:
        if _is_rest_quota_error(exc):
            member = pg_select_vendor_member(actor_id=assignee_id)
            return _photographer_name_from(member, fallback)
        logger.warning("Photographer name lookup failed for %s: %s", assignee_id, exc)
        return fallback


def _agent_allows(email: str, field: str) -> bool:
    """Bricks OS Settings → Notifications (fails open)."""
    from ..shared.notif_prefs_gate import agent_allows
    return agent_allows(email, field)


async def _email_agent_direct(to: str, subject: str, html: str) -> bool:
    from .notifications import send_email_via_resend
    return bool(await send_email_via_resend(to, subject, html))


def _when_line(job: dict) -> str:
    from .notifications import format_when_line
    return format_when_line(job)


async def _push_agent_new_booking(agent_email: str, address: str, when: str, photographer: str, job_id: str) -> dict:
    """"New booking: push" from Bricks OS Settings → Notifications. Web Push to every
    device the agent turned on; gated by notification_prefs.new_booking_push (fails
    open like the email). Never raises: a push problem must not block the booking."""
    try:
        if not (agent_email or "").strip() or not _agent_allows(agent_email, "new_booking_push"):
            return {"sent": 0, "skipped": "muted_or_no_email"}
        from ..shared import web_push
        from ..shared.pg_fallback import _pg_connect
        parts = [p for p in (when, photographer) if p]
        msg = web_push.notification(
            f"Shoot confirmed · {address}",
            " · ".join(parts) or "Your Bricks shoot is confirmed.",
            _listing_desk_url(job_id),
            f"booking-{job_id}",
        )
        res = await web_push.push_to_email(_pg_connect, agent_email, msg)
        logger.info("New booking push for job %s: %s", job_id, res)
        return res
    except Exception as exc:  # noqa: BLE001
        logger.warning("New booking push failed for job %s: %s", job_id, type(exc).__name__)
        return {"sent": 0, "error": type(exc).__name__}


async def notify_booking_confirmed(job_id: str) -> None:
    """
    Notifies the agent that their shoot is confirmed and a photographer has been assigned.
    www /book → jobs.agent_email (Resend). Companion Book → Booking service when no email on job.
    """
    job = await _load_job(
        job_id,
        "id, booking_id, address, scheduled_date, time_window_start, time_window_end, "
        "preferred_window, package_name, assignee_id, agent_email, agent_name",
    )
    if not job:
        return

    photographer_name = await _resolve_photographer_name(
        job.get("assignee_id"), "your Bricks photographer"
    )
    address = (job.get("address") or "").strip() or "your property"
    package_name = (job.get("package_name") or "").strip() or "Listing media"
    when = _when_line(job)
    agent_email = (job.get("agent_email") or "").strip()
    agent_name = (job.get("agent_name") or "").strip() or "there"

    if agent_email:
        who = _html.escape(agent_name)
        rows = (
            f'<tr><td style="padding:6px 0;color:#888;width:110px">Property</td>'
            f'<td style="padding:6px 0"><strong>{_html.escape(address)}</strong></td></tr>'
            f'<tr><td style="padding:6px 0;color:#888">When</td>'
            f'<td style="padding:6px 0">{_html.escape(when or "TBD")}</td></tr>'
            f'<tr><td style="padding:6px 0;color:#888">Package</td>'
            f'<td style="padding:6px 0">{_html.escape(package_name)}</td></tr>'
            f'<tr><td style="padding:6px 0;color:#888">Photographer</td>'
            f'<td style="padding:6px 0">{_html.escape(photographer_name)}</td></tr>'
        )
        body = f"""
<p style="color:#555;line-height:1.6;margin:0 0 12px 0">Hi {who},</p>
<p style="color:#555;line-height:1.6;margin:0 0 12px 0">Your shoot is confirmed. A photographer is on it in Bricks GO.</p>
<table style="border-collapse:collapse;font-size:14px;width:100%">{rows}</table>
<p style="margin-top:16px;font-size:13px;color:#888;line-height:1.5">
  Open this listing on your desk: <a href="{_listing_desk_url(job_id)}" style="color:#d2542b">{_listing_desk_url(job_id)}</a>
</p>
<p style="font-size:13px;color:#888;line-height:1.5">
  Need to reschedule? Reply to this email or write help@brxs.ca.
</p>
"""
        await _push_agent_new_booking(agent_email, address, when, photographer_name, job_id)
        if not _agent_allows(agent_email, "new_booking_email"):
            logger.info("Booking confirmed email muted by agent's notification settings for job %s", job_id)
            return
        sent = await _email_agent_direct(
            agent_email,
            f"Shoot confirmed — {address}",
            _branded_card("Shoot confirmed", body),
        )
        logger.info(
            "Booking confirmed email %s for job %s (agent_email)",
            "sent" if sent else "failed",
            job_id,
        )
        return

    booking_id = (job.get("booking_id") or "").strip()
    if not booking_id:
        logger.warning(f"Job {job_id}: no agent_email or booking_id — skipping booking confirmed")
        return

    await _call_booking_comms("/api/internal/comms/booking-confirmed", {
        "booking_id": booking_id,
        "job_id": job_id,
        "photographer_name": photographer_name,
        "address": address,
        "scheduled_date": job.get("scheduled_date") or "",
        "time_window_start": job.get("time_window_start") or "",
        "package_name": package_name,
    })
    logger.info(f"Booking confirmed comms dispatched for job {job_id} (booking {booking_id})")


async def notify_photographer_en_route(job_id: str) -> None:
    """Notifies the agent that the photographer is on their way."""
    job = await _load_job(
        job_id,
        "id, booking_id, address, scheduled_date, time_window_start, time_window_end, "
        "preferred_window, assignee_id, agent_email, agent_name",
    )
    if not job:
        return

    photographer_name = await _resolve_photographer_name(
        job.get("assignee_id"), "Your Bricks photographer"
    )
    address = (job.get("address") or "").strip() or "your property"
    when = _when_line(job)
    agent_email = (job.get("agent_email") or "").strip()
    agent_name = (job.get("agent_name") or "").strip() or "there"

    if agent_email:
        body = f"""
<p style="color:#555;line-height:1.6;margin:0 0 12px 0">Hi {_html.escape(agent_name)},</p>
<p style="color:#555;line-height:1.6;margin:0 0 12px 0">
  <strong>{_html.escape(photographer_name)}</strong> is on the way to
  <strong>{_html.escape(address)}</strong>.
</p>
<p style="color:#555;line-height:1.6;margin:0 0 12px 0">Scheduled: {_html.escape(when or "see GO")}</p>
<p style="font-size:13px;color:#888;line-height:1.5">
  Please keep the property accessible. Questions → help@brxs.ca.
</p>
"""
        sent = await _email_agent_direct(
            agent_email,
            f"Photographer en route — {address}",
            _branded_card("Photographer en route", body),
        )
        logger.info(
            "En-route email %s for job %s (agent_email)",
            "sent" if sent else "failed",
            job_id,
        )
        return

    booking_id = (job.get("booking_id") or "").strip()
    if not booking_id:
        logger.warning(f"Job {job_id}: no agent_email or booking_id — skipping en-route")
        return

    await _call_booking_comms("/api/internal/comms/photographer-en-route", {
        "booking_id": booking_id,
        "job_id": job_id,
        "photographer_name": photographer_name,
        "address": address,
        "scheduled_date": job.get("scheduled_date") or "",
        "time_window_start": job.get("time_window_start") or "",
    })
    logger.info(f"En-route comms dispatched for job {job_id} (booking {booking_id})")


async def notify_capture_delivered(
    job_id: str,
    *,
    products: list[str],
    address: str = "",
    booking_id: str = "",
    delivery_urls: list | None = None,
) -> bool:
    """Notify the booking agent that a 3D tour and/or floor plan landed on the listing file.

    Prefer jobs.agent_email (www /book). Fall back to Companion job-delivered when
    agent_email is missing and a Companion booking_id exists.
    """
    if not products:
        return False

    job = await _load_job(
        job_id,
        "booking_id, address, agent_email, agent_name, agent_user_id",
    )
    if not job and not (booking_id or "").strip():
        logger.warning(f"Job {job_id}: missing job — skipping capture delivery notification")
        return False

    bid = (booking_id or "").strip() or ((job or {}).get("booking_id") or "")
    addr = (address or "").strip() or ((job or {}).get("address") or "") or "your property"
    agent_email = ((job or {}).get("agent_email") or "").strip()
    agent_name = ((job or {}).get("agent_name") or "").strip() or "there"
    urls = delivery_urls or []

    clean_products = [str(p).strip() for p in products if str(p).strip()]
    if not clean_products:
        return False

    if len(clean_products) == 1:
        what = clean_products[0]
        subject = f"Your {what} is ready — {addr}"
        ready_phrase = f"Your {_html.escape(what)}"
        verb = "is"
        open_it = "Open it"
    else:
        what = (
            " and ".join(clean_products)
            if len(clean_products) == 2
            else ", ".join(clean_products[:-1]) + f", and {clean_products[-1]}"
        )
        subject = f"Your media is ready — {addr}"
        ready_phrase = f"Your {_html.escape(what)}"
        verb = "are"
        open_it = "Open them"

    links = "".join(
        f'<p style="margin:8px 0"><a href="{_html.escape(u.get("url", "#"))}" '
        f'style="color:#d2542b">{_html.escape(u.get("filename") or "Open")}</a></p>'
        for u in urls[:5]
        if isinstance(u, dict) and u.get("url")
    )

    if agent_email and not _agent_allows(agent_email, "delivery_ready_email"):
        logger.info(f"Job {job_id}: delivery email muted by agent's notification settings")
        return False

    if agent_email:
        desk = _listing_desk_url(job_id)
        body = f"""
<p style="color:#555;line-height:1.6;margin:0 0 12px 0">Hi {_html.escape(agent_name)},</p>
<p style="color:#555;line-height:1.6;margin:0 0 12px 0">
  {ready_phrase} for <strong>{_html.escape(addr)}</strong> {verb} on the listing file.
  {open_it} on your desk
  {'or via the link(s) below' if links else ''}.
  From the listing, use <strong>Copy client link</strong> to share with your seller.
</p>
{links}
<p style="margin-top:16px;font-size:13px;color:#888;line-height:1.5">
  <a href="{desk}" style="color:#d2542b">{desk}</a>
</p>
"""
        ok = await _email_agent_direct(
            agent_email,
            subject,
            _branded_card("Media ready", body),
        )
        if ok:
            logger.info(f"Job {job_id}: capture delivery email sent products={clean_products!r}")
        return ok

    if not bid:
        logger.warning(f"Job {job_id}: no agent_email or booking_id — skipping capture delivery")
        return False

    ok = await _call_booking_comms("/api/internal/comms/job-delivered", {
        "booking_id": bid,
        "job_id": job_id,
        "address": addr,
        "delivery_count": max(len(urls), len(clean_products), 1),
        "delivery_urls": urls[:5],
        "products": clean_products,
        "splat_pending": False,
    })
    if ok:
        logger.info(f"Job {job_id}: capture delivery notification sent products={clean_products!r}")
    return ok


# ---------------------------------------------------------------------------
# Photo / video delivery email (post_production)
# ---------------------------------------------------------------------------

_VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm")


async def _client_share_link(job_id: str) -> str:
    """Never-expiring client share page for the listing (OS mints the token).

    The share page re-signs storage links on every open, so the email never
    carries a signed URL that dies. Returns "" when OS can't be reached.
    """
    api_key = get_own_internal_api_key()
    os_url = (os.environ.get("BRICKS_OS_API_URL") or "https://api.brxs.ca").strip().rstrip("/")
    if not api_key or not job_id:
        return ""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{os_url}/api/internal/share-link",
                headers={"X-Internal-Api-Key": api_key},
                json={"property_id": job_id},
            )
            resp.raise_for_status()
            url = str((resp.json() or {}).get("share_url") or "").strip()
            return url if url.startswith("https://") else ""
    except Exception as e:  # noqa: BLE001 - email still goes out with the desk link
        logger.warning("share-link mint failed for job %s: %s", job_id, type(e).__name__)
        return ""


def _delivery_has_video(delivery_urls: list | None) -> bool:
    for u in delivery_urls or []:
        if not isinstance(u, dict):
            continue
        name = str(u.get("filename") or u.get("url") or "").lower().split("?", 1)[0]
        mime = str(u.get("mime_type") or "").lower()
        if mime.startswith("video/") or name.endswith(_VIDEO_EXTS):
            return True
    return False


def photos_delivered_email(
    *, agent_name: str, address: str, share_url: str, desk_url: str, has_video: bool
) -> tuple[str, str]:
    """(subject, html) for the delivery email. Links never expire."""
    what = "media" if has_video else "photos"
    addr = (address or "").strip() or "your listing"
    subject = f"Your photos are ready · {addr}" if what == "photos" else f"Your media is ready · {addr}"
    verb = "are" if what == "photos" else "is"
    primary = share_url or desk_url
    note = (
        "This link doesn't expire. You can share it with your seller as is."
        if share_url
        else "Sign in to view and download everything."
    )
    body = f"""
<p style="color:#555;line-height:1.6;margin:0 0 12px 0">Hi {_html.escape(agent_name or 'there')},</p>
<p style="color:#555;line-height:1.6;margin:0 0 20px 0">
  Your {what} for <strong>{_html.escape(addr)}</strong> {verb} ready.
</p>
<p style="margin:0 0 20px 0">
  <a href="{_html.escape(primary)}" style="display:inline-block;background:#14110E;color:#F4F1EB;text-decoration:none;font-weight:500;font-size:14px;padding:12px 20px;border-radius:2px">View and download</a>
</p>
<p style="color:#888;font-size:13px;line-height:1.5;margin:0 0 8px 0">{note}</p>
"""
    if share_url and desk_url:
        body += (
            f'<p style="color:#888;font-size:13px;line-height:1.5;margin:0">'
            f'Or open the listing file: <a href="{_html.escape(desk_url)}" style="color:#d2542b">'
            f"{_html.escape(desk_url)}</a></p>"
        )
    title = "Media ready" if has_video else "Photos ready"
    return subject, _branded_card(title, body)


async def _call_booking_comms_json(path: str, payload: dict) -> dict | None:
    """Like _call_booking_comms but returns the JSON body (None on failure)."""
    api_key = get_own_internal_api_key()
    booking_url = (os.environ.get("BOOKING_API_URL") or os.environ.get("BOOKING_CALLBACK_URL") or "").strip()
    if not api_key or not booking_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{booking_url.rstrip('/')}{path}",
                headers={"X-Internal-Api-Key": api_key},
                json=payload,
            )
            resp.raise_for_status()
            try:
                return resp.json() or {}
            except Exception:  # noqa: BLE001
                return {}
    except Exception as e:  # noqa: BLE001
        logger.error(f"Booking comms call to {path} failed: {e}")
        return None


def _looks_like_uuid(v: str) -> bool:
    import uuid as _uuid
    try:
        _uuid.UUID(str(v))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


async def notify_photos_delivered(
    job_id: str, delivery_urls: list | None = None, *, to_override: str | None = None
) -> str:
    """Email the agent that the listing media is delivered.

    Returns the media_delivery.notification_status to record:
    sent | muted | skipped (no recipient) | failed.

    Why: this used to only delegate to Companion by booking_id, but www /book
    bookings live on jobs.agent_email (Companion's bookings table has no row
    for them, and e2e ids aren't UUIDs), so every prod delivery email failed.
    Now: jobs.agent_email first (same as en-route / capture delivery), with a
    link to the never-expiring share page instead of 1h signed URLs.
    to_override: ops test sends only (the agent is never contacted).
    """
    job = await _load_job(job_id, "id, booking_id, address, agent_email, agent_name")
    if not job:
        logger.warning(f"Job {job_id}: missing job — skipping delivery email")
        return "skipped"
    address = (job.get("address") or "").strip()
    agent_email = (to_override or job.get("agent_email") or "").strip()
    agent_name = (job.get("agent_name") or "").strip() if not to_override else "there"
    share_url = await _client_share_link(job_id)
    desk_url = _listing_desk_url(job_id)
    has_video = _delivery_has_video(delivery_urls)

    if agent_email:
        if not to_override and not _agent_allows(agent_email, "delivery_ready_email"):
            logger.info(f"Job {job_id}: delivery email muted by agent's notification settings")
            return "muted"
        subject, html = photos_delivered_email(
            agent_name=agent_name, address=address, share_url=share_url,
            desk_url=desk_url, has_video=has_video,
        )
        ok = await _email_agent_direct(agent_email, subject, html)
        logger.info(
            "Job %s: delivery email %s (share_link=%s)", job_id, "sent" if ok else "failed", bool(share_url)
        )
        return "sent" if ok else "failed"

    booking_id = (job.get("booking_id") or "").strip()
    if not booking_id or not _looks_like_uuid(booking_id):
        logger.warning(f"Job {job_id}: no agent_email and no Companion booking — delivery email skipped")
        return "skipped"
    body = await _call_booking_comms_json("/api/internal/comms/job-delivered", {
        "booking_id": booking_id,
        "job_id": job_id,
        "address": address,
        "delivery_count": len(delivery_urls or []),
        # Never raw signed URLs (they die in 1h); the share page re-signs.
        "delivery_urls": [],
        "share_url": share_url,
        "desk_url": desk_url,
        "has_video": has_video,
        "splat_pending": False,
    })
    if body is None:
        return "failed"
    if body.get("sent"):
        return "sent"
    if body.get("muted"):
        return "muted"
    return "skipped"
