import html as _html
import logging
import os
import re
from datetime import date as _date
from zoneinfo import ZoneInfo
from ..shared.database import get_db
from ..shared.job_timezone import resolve_job_iana, tz_abbrev
from ..shared.schemas import NotificationRecord

logger = logging.getLogger(__name__)


# Brand values pulled directly from BricksWordmark.tsx (frontend/components/
# BricksWordmark.tsx) -- lowercase "bricks" in General Sans 500, terracotta
# accent #D2542B, ink #14110E. This template already had the right accent
# color and a real card layout (better than the other two repos' plain
# <p>-only emails), but was missing the font reference and the actual
# wordmark -- added both without touching the working layout.
_EMAIL_FONT_STACK = "'General Sans','Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
# Real hosted image, not a CSS square -- Outlook has no inline-SVG support and a
# flat colored block can't approximate BrickMark's actual 3-rect "b" shape.
# www.brxs.ca is served by bricks-site's Vercel project (renamed from bricks-prelaunch).
_EMAIL_LOGO_URL = "https://www.brxs.ca/bricks-mark.png"


_GO_WEB_URL = os.environ.get("GO_WEB_URL", "https://bricks-dispatch-web.vercel.app").rstrip("/")

# Legacy morning/afternoon/evening fills only when no booked clock is stored.
# Bookers pick a concrete arrive/start time; do not present these ranges as the booking.
_CANONICAL_WINDOWS = {
    "morning": ("09:00", "12:00"),
    "afternoon": ("12:00", "16:00"),
    "evening": ("16:00", "19:00"),
}

_DOW_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MON_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_SHOOT_TIME_UNSET = "Shoot time not set"


def _clock_hhmm(raw, job: dict | None = None) -> str:
    """Normalize HH:MM in the job's property timezone.

    Bare HH:MM / HH:MM:SS stay as local wall clocks. ISO datetimes (T or space,
    with Z/offset) convert to the job IANA — never slice the UTC hour as if it
    were Eastern (13:00Z is 9:00 AM ET in September, not 1:00 PM).
    """
    s = (raw or "").strip()
    if not s:
        return ""
    # Bare clock first — do not run through fromisoformat (naive shift risk).
    if len(s) >= 5 and s[2] == ":" and "T" not in s and s.count("-") == 0:
        return s[:5]
    if "T" in s or (s[0].isdigit() and s.count("-") >= 2):
        try:
            from datetime import datetime
            iana = resolve_job_iana(job)
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=ZoneInfo(iana))
            return d.astimezone(ZoneInfo(iana)).strftime("%H:%M")
        except Exception:
            return ""
    return ""


def _format_clock_12h(hhmm: str) -> str:
    """'14:00' → '2:00 PM'. Empty if not a real clock."""
    if not hhmm or len(hhmm) < 5 or hhmm[2] != ":":
        return ""
    try:
        hour = int(hhmm[:2])
        minute = int(hhmm[3:5])
    except ValueError:
        return ""
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return ""
    suffix = "AM" if hour < 12 else "PM"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute:02d} {suffix}"


def _booked_clock_hhmm(job: dict | None) -> str:
    """Booked arrive/start: confirmed_arrive_at, else time_window_start, else preferred_window start."""
    job = job or {}
    confirmed = _clock_hhmm(job.get("confirmed_arrive_at"), job)
    if confirmed:
        return confirmed
    start = _clock_hhmm(job.get("time_window_start"), job)
    if start:
        return start
    preferred = (job.get("preferred_window") or "").strip().lower()
    if preferred in _CANONICAL_WINDOWS:
        return _CANONICAL_WINDOWS[preferred][0]
    return ""


def format_shoot_date(scheduled_date: str | None) -> str:
    """'2026-09-25' → 'Fri, Sep 25'. Locale-stable English; no year."""
    raw = (scheduled_date or "").strip()
    if not raw:
        return ""
    ymd = raw[:10]
    if len(ymd) >= 10 and ymd[4] == "-":
        try:
            d = _date.fromisoformat(ymd)
        except ValueError:
            return ymd
        return f"{_DOW_ABBR[d.weekday()]}, {_MON_ABBR[d.month - 1]} {d.day}"
    return ymd


_PACKAGE_LINES: dict[str, tuple[str, str]] = {
    "photos_only": ("Photos only", "HDR photo"),
    "photosonly": ("Photos only", "HDR photo"),
    "essentials": ("Essentials", "HDR photo + Drone + Floor plan"),
    "story": ("Story", "HDR photo + Drone + Cinematic reel + Floor plan"),
    "showcase": ("Showcase", "HDR photo + Drone + Cinematic reel + Virtual tour + Floor plan"),
    "suite": ("Full suite", "HDR photo + Drone + Cinematic reel + Virtual tour + Twilight + Floor plan"),
    "full": ("Full suite", "HDR photo + Drone + Cinematic reel + Virtual tour + Twilight + Floor plan"),
    # Companion ladder (booking-api): Visit is never packaged.
    "standard": ("Standard", "HDR photo + Floor plan"),
    "premium": ("Premium", "HDR photo + Floor plan + Virtual tour"),
    "luxury": ("Luxury", "HDR photo + Floor plan + Virtual tour"),
    "estate": ("Estate", "HDR photo + Floor plan + Virtual tour"),
}


def format_package_line(package_name: str | None, package_id: str | None = None) -> str:
    """Expand bare Showcase → Showcase — inclusions (matches /book servicesSummary)."""
    raw = (package_name or "").strip()
    if " — " in raw or " – " in raw:
        return raw
    key = (package_id or "").strip().lower().replace("-", "_")
    if not key and raw:
        key = raw.lower().replace(" ", "_").replace("-", "_")
    if key not in _PACKAGE_LINES:
        low = raw.lower()
        for token in ("photos_only", "photos only", "essentials", "story", "showcase", "suite", "full", "standard", "premium", "luxury", "estate"):
            if token == low or token.replace("_", " ") == low or token in low:
                key = token.replace(" ", "_")
                break
    if key in _PACKAGE_LINES:
        label, bits = _PACKAGE_LINES[key]
        return f"{label} — {bits}"
    return raw or "Listing media"


def format_shoot_window(job: dict | None) -> str:
    """Booked arrive/start clock: '2:00 PM PT'. Not a window label or range."""
    job = job or {}
    hhmm = _booked_clock_hhmm(job)
    if not hhmm:
        return _SHOOT_TIME_UNSET
    pretty = _format_clock_12h(hhmm)
    abbrev = tz_abbrev(job)
    return f"{pretty} {abbrev}" if pretty else f"{hhmm} {abbrev}"


def format_when_line(job: dict | None) -> str:
    """Vendor/admin when-line: 'Fri, Sep 25 · 2:00 PM PT'."""
    job = job or {}
    day = format_shoot_date(job.get("scheduled_date"))
    clock = format_shoot_window(job)
    if clock == _SHOOT_TIME_UNSET:
        return day or _SHOOT_TIME_UNSET
    if day:
        return f"{day} · {clock}"
    return clock




def _arrive_confirm_email_html(agent_name: str, photographer: str, address: str, when_line: str, arrive: str, abbrev: str = "") -> str:
    who = (agent_name or "").strip() or "there"
    photo = (photographer or "").strip() or "Your Bricks photographer"
    addr = (address or "").strip() or "your property"
    when = (when_line or "").strip()
    arrive_s = (arrive or "").strip()
    zone = (abbrev or "").strip()
    arrive_label = f"{arrive_s} {zone}".strip()
    when_p = f'<p style="color:#555;line-height:1.6;margin:0 0 12px 0">{when}</p>' if when else ""
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
<h1 style="color:#fff;margin:0;font-size:20px;font-weight:600;font-family:{_EMAIL_FONT_STACK}">Arrive time confirmed</h1>
</div>
<div style="padding:24px">
<p style="color:#555;line-height:1.6;margin:0 0 12px 0">Hi {who},</p>
<p style="color:#555;line-height:1.6;margin:0 0 12px 0"><strong>{photo}</strong> will arrive around <strong>{arrive_label}</strong> at <strong>{addr}</strong>.</p>
{when_p}
<p style="margin-top:24px;font-size:13px;color:#999;line-height:1.5">Bricks · brxs.ca</p>
</div>
</div></body></html>"""


async def notify_arrive_confirmed(job: dict, photographer: dict | None = None) -> dict:
    """Email the booking agent the photographer's exact arrive time. Never raises."""
    result = {"email": False}
    job = job or {}
    to_email = (job.get("agent_email") or "").strip()
    if not to_email:
        logger.info("Arrive confirm: no agent_email on job %s — skip email", job.get("id"))
        return result
    arrive = _clock_hhmm(job.get("confirmed_arrive_at"), job) or _clock_hhmm(job.get("time_window_start"), job)
    if not arrive:
        return result
    arrive_display = _format_clock_12h(arrive) or arrive
    abbrev = tz_abbrev(job)
    when_line = format_when_line(job)
    if when_line == _SHOOT_TIME_UNSET:
        when_line = ""
    photo_name = ((photographer or {}).get("name") or "").strip() or "Your Bricks photographer"
    try:
        html = _arrive_confirm_email_html(
            job.get("agent_name") or "",
            photo_name,
            job.get("address") or "",
            when_line,
            arrive_display,
            abbrev,
        )
        result["email"] = bool(
            await send_email_via_resend(
                to_email,
                f"Photographer arriving ~{arrive_display} {abbrev} — {job.get('address') or 'your shoot'}",
                html,
            )
        )
    except Exception as exc:
        logger.error("Arrive confirm email failed for job %s: %s", job.get("id"), exc)
    return result


def _assignment_email_html(name: str, address: str, when_line: str, go_url: str) -> str:
    who = (name or "").strip() or "there"
    addr = (address or "").strip() or "a property"
    when = (when_line or "").strip()
    when_p = f'<p style="color:#555;line-height:1.6;margin:0 0 12px 0">{when}</p>' if when else ""
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
<h1 style="color:#fff;margin:0;font-size:20px;font-weight:600;font-family:{_EMAIL_FONT_STACK}">New job on Bricks GO</h1>
</div>
<div style="padding:24px">
<p style="color:#555;line-height:1.6;margin:0 0 12px 0">Hi {who},</p>
<p style="color:#555;line-height:1.6;margin:0 0 12px 0">A shoot just landed on your Today list: <strong>{addr}</strong>.</p>
{when_p}
<a href="{go_url}" style="display:inline-block;margin-top:8px;padding:12px 24px;background:#d2542b;color:#fff;text-decoration:none;border-radius:2px;font-weight:500">Open Bricks GO</a>
<p style="margin-top:24px;font-size:13px;color:#999;line-height:1.5">Or go to <a href="{go_url}" style="color:#d2542b">{go_url}</a></p>
</div>
<div style="padding:12px 24px;border-top:1px solid #eee;font-size:12px;color:#888;font-family:{_EMAIL_FONT_STACK}">
<p style="margin:0">Bricks · brxs.ca</p></div>
</div></body></html>"""



def _build_html_email(title: str, body: str) -> str:
    safe_title = _html.escape(title or "")
    safe_body = _html.escape(body or "").replace("\n", "<br>")
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
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
<h1 style="color:#fff;margin:0;font-size:20px;font-weight:600;font-family:{_EMAIL_FONT_STACK}">{safe_title}</h1>
</div>
<div style="padding:24px"><p style="color:#333;font-size:15px;line-height:1.5;margin:0;font-family:{_EMAIL_FONT_STACK}">{safe_body}</p></div>
<div style="padding:12px 24px;border-top:1px solid #eee;font-size:12px;color:#888;font-family:{_EMAIL_FONT_STACK}">
<p style="margin:0">Bricks &middot; brxs.ca</p></div>
</div></body></html>"""


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _looks_like_uuid(value: str | None) -> bool:
    return bool(_UUID_RE.match((value or "").strip()))


def short_booking_ref(booking_id: str | None) -> str:
    """First 8 characters of a booking id. Never return a full UUID."""
    raw = (booking_id or "").strip()
    if not raw:
        return "n/a"
    return raw[:8]


def _truncate_clean(text: str, max_len: int = 60) -> str:
    """Truncate on a word/punctuation boundary. Never mid-word."""
    text = " ".join((text or "").split())
    if len(text) <= max_len:
        return text
    slice_ = text[:max_len]
    cut = -1
    for sep in (" ", ",", "·"):
        idx = slice_.rfind(sep)
        if idx > cut:
            cut = idx
    if cut >= max(12, max_len // 3):
        slice_ = slice_[:cut]
    return slice_.rstrip(" ,;·-") + "…"


def paid_booking_admin_subject(address: str | None) -> str:
    """Inbox subject: no emoji, no raw UUID, address truncated ~60 chars."""
    addr = (address or "").strip()
    if not addr or _looks_like_uuid(addr):
        return "New booking paid"
    return f"New booking paid · {_truncate_clean(addr, 60)}"


def _email_kv_row(label: str, value: str) -> str:
    return (
        f'<tr>'
        f'<td style="padding:6px 0;color:#888;width:110px;vertical-align:top;'
        f'font-family:{_EMAIL_FONT_STACK}">{_html.escape(label)}</td>'
        f'<td style="padding:6px 0;color:#333;font-family:{_EMAIL_FONT_STACK}">'
        f'{_html.escape(value)}</td>'
        f'</tr>'
    )


def _paid_booking_admin_email_html(
    *,
    address: str,
    package: str,
    when: str,
    assignee: str,
    booking_ref: str,
) -> str:
    """Branded admin card for a newly paid Booking intake job."""
    rows = "".join([
        _email_kv_row("Address", address or "n/a"),
        _email_kv_row("Package", package or "n/a"),
        _email_kv_row("When", when or "n/a"),
        _email_kv_row("Assignee", assignee or "unassigned"),
        _email_kv_row("Booking ref", booking_ref or "n/a"),
    ])
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
<div style="background:#D2542B;padding:16px 24px;margin-top:12px">
<h1 style="color:#fff;margin:0;font-size:20px;font-weight:600;font-family:{_EMAIL_FONT_STACK}">New booking paid</h1>
</div>
<div style="padding:24px">
<p style="color:#555;line-height:1.6;margin:0 0 16px 0;font-family:{_EMAIL_FONT_STACK}">A paid shoot just landed from Booking.</p>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;font-size:14px;width:100%;font-family:{_EMAIL_FONT_STACK}">{rows}</table>
</div>
<div style="padding:12px 24px;border-top:1px solid #eee;font-size:12px;color:#888;font-family:{_EMAIL_FONT_STACK}">
<p style="margin:0">Bricks · brxs.ca</p></div>
</div></body></html>"""


async def send_email_via_resend(to: str, subject: str, html: str) -> bool:
    """Send transactional email via Resend. Returns True on success."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.debug("RESEND_API_KEY not set — skipping email")
        return False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": "BRICKS <notifications@brxs.ca>",
                    "to": [to],
                    "subject": subject,
                    "html": html,
                },
            )
            r.raise_for_status()
            return True
    except Exception as exc:
        logger.error("Resend email error (to=%s, subj=%s): %s", to, subject, exc)
        return False


def _apns_configured() -> bool:
    key_id = (os.environ.get("APNS_KEY_ID") or "").strip()
    team_id = (os.environ.get("APNS_TEAM_ID") or "").strip()
    p8 = (
        os.environ.get("APNS_P8")
        or os.environ.get("APNS_AUTH_KEY")
        or os.environ.get("APNS_KEY")
        or ""
    ).strip()
    return bool(key_id and team_id and p8)


def _looks_like_expo_token(token: str) -> bool:
    t = (token or "").strip()
    return t.startswith("ExponentPushToken") or t.startswith("ExpoPushToken")


def _looks_like_apns_token(token: str) -> bool:
    t = (token or "").strip()
    return len(t) in (64, 32) and all(c in "0123456789abcdefABCDEF" for c in t)


async def _send_expo_push(token: str, title: str, body: str, data: dict | None) -> bool:
    """POST to Expo's push API. No secret required for ExponentPushToken. False on failure."""
    if not token:
        return False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.post(
                "https://exp.host/--/api/v2/push/send",
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json={
                    "to": token,
                    "title": title,
                    "body": body,
                    "data": data or {},
                    "sound": "default",
                    "priority": "high",
                },
            )
        payload = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        status = ((payload.get("data") or {}) if isinstance(payload.get("data"), dict) else {})
        if isinstance(payload.get("data"), list) and payload["data"]:
            status = payload["data"][0] or {}
        if r.status_code >= 400:
            logger.info("Expo push not sent (HTTP %s)", r.status_code)
            return False
        if (status.get("status") or "").lower() == "error":
            logger.info("Expo push not sent: %s", status.get("message") or status)
            if status.get("details", {}).get("error") == "DeviceNotRegistered":
                try:
                    await (await get_db()).table("push_tokens").update({"active": False}).eq("token", token).execute()
                except Exception:
                    pass
            return False
        return (status.get("status") or "ok").lower() in ("ok", "success") or r.status_code == 200
    except Exception as exc:
        logger.error("Expo push error: %s", exc)
        return False


async def _send_apns(device_token: str, title: str, body: str, data: dict | None) -> bool:
    """APNs HTTP/2. Logs and returns False when env or HTTP/2 is missing. Never fakes sent."""
    key_id = (os.environ.get("APNS_KEY_ID") or "").strip()
    team_id = (os.environ.get("APNS_TEAM_ID") or "").strip()
    bundle = (os.environ.get("APNS_BUNDLE_ID") or "com.bricks.go").strip()
    p8 = (
        os.environ.get("APNS_P8")
        or os.environ.get("APNS_AUTH_KEY")
        or os.environ.get("APNS_KEY")
        or ""
    ).strip()
    if not all([key_id, team_id, p8, device_token]):
        logger.info("APNs env missing — skipping iOS push")
        return False
    try:
        import jwt
        import httpx
        import time
        token = jwt.encode(
            {"iss": team_id, "iat": int(time.time())},
            p8,
            algorithm="ES256",
            headers={"kid": key_id, "alg": "ES256"},
        )
    except Exception as exc:
        logger.warning("APNs JWT could not be built (%s) — skipping iOS push", type(exc).__name__)
        return False
    host = "api.push.apple.com" if os.environ.get("APNS_PRODUCTION", "").lower() in ("1", "true", "yes") else "api.sandbox.push.apple.com"
    payload = {
        "aps": {"alert": {"title": title, "body": body}, "sound": "default"},
        **(data or {}),
    }
    try:
        async with httpx.AsyncClient(http2=True, timeout=12.0) as client:
            r = await client.post(
                f"https://{host}/3/device/{device_token}",
                headers={
                    "authorization": f"bearer {token}",
                    "apns-topic": bundle,
                    "apns-push-type": "alert",
                    "apns-priority": "10",
                },
                json=payload,
            )
        if r.status_code == 200:
            return True
        logger.warning("APNs not sent (HTTP %s)", r.status_code)
        if r.status_code in (410, 400):
            try:
                await (await get_db()).table("push_tokens").update({"active": False}).eq("token", device_token).execute()
            except Exception:
                pass
        return False
    except Exception as exc:
        logger.warning("APNs send failed: %s", exc)
        return False


async def send_push_to_member(member_id: str, title: str, body: str, data: dict | None = None) -> bool:
    """Push to this photographer's stored tokens. False if none sent. Never fakes sent."""
    if not member_id:
        return False
    try:
        res = await (await get_db()).table("push_tokens").select("*").eq("member_id", member_id).eq("active", True).limit(50).execute()
        tokens = res.data or []
    except Exception as exc:
        logger.error("push_tokens lookup failed: %s", exc)
        return False
    if not tokens:
        logger.info("No push tokens for member %s — skipping push", member_id)
        return False
    any_sent = False
    for row in tokens:
        token = (row.get("token") or row.get("device_token") or "").strip()
        platform = (row.get("platform") or "").strip().lower()
        if not token:
            continue
        sent = False
        if _looks_like_expo_token(token) or platform in ("expo", "android"):
            sent = await _send_expo_push(token, title, body, data)
        elif platform == "ios" or _looks_like_apns_token(token):
            if not _apns_configured():
                logger.info("APNs env missing — skipping iOS push")
            else:
                sent = await _send_apns(token, title, body, data)
        else:
            sent = await _send_expo_push(token, title, body, data)
            if not sent and _looks_like_apns_token(token):
                sent = await _send_apns(token, title, body, data)
        any_sent = any_sent or sent
    return any_sent


async def send_push_notification(title: str, body: str, data: dict | None = None, member_id: str | None = None):
    """Member-scoped push. Broadcast-to-all-tokens is intentionally gone."""
    if member_id:
        return await send_push_to_member(member_id, title, body, data)
    logger.info("Push skipped — no member_id (refusing to broadcast to every token)")
    return False


async def store_push_token(member_id: str, token: str, platform: str = "expo") -> dict:
    """Upsert a device/Expo token for this photographer."""
    import uuid
    from datetime import datetime, timezone
    token = (token or "").strip()
    platform = (platform or "expo").strip().lower() or "expo"
    if not token:
        raise ValueError("token is required")
    now = datetime.now(timezone.utc).isoformat()
    db = await get_db()
    try:
        existing = await db.table("push_tokens").select("id").eq("token", token).maybe_single().execute()
        if existing.data:
            await db.table("push_tokens").update({
                "member_id": member_id,
                "platform": platform,
                "active": True,
                "updated_at": now,
            }).eq("id", existing.data["id"]).execute()
            return {"registered": True, "id": existing.data["id"]}
    except Exception as exc:
        logger.error("push_tokens lookup failed: %s", exc)
    row = {
        "id": str(uuid.uuid4()),
        "member_id": member_id,
        "token": token,
        "platform": platform,
        "active": True,
        "created_at": now,
        "updated_at": now,
    }
    try:
        await db.table("push_tokens").insert(row).execute()
        return {"registered": True, "id": row["id"]}
    except Exception as exc:
        logger.error("push_tokens insert failed: %s", exc)
        slim = {k: row[k] for k in ("id", "token", "active") if k in row}
        slim["member_id"] = member_id
        try:
            await db.table("push_tokens").insert(slim).execute()
            return {"registered": True, "id": row["id"]}
        except Exception as exc2:
            logger.error("push_tokens insert retry failed: %s", exc2)
            raise


async def create_notification(
    title: str,
    body: str,
    ntype: str = "info",
    data: dict | None = None,
    *,
    email_to: str | None = None,
    email_subject: str | None = None,
    email_html: str | None = None,
    member_id: str | None = None,
    send_push: bool = True,
):
    notif = NotificationRecord(title=title, body=body, type=ntype, data=data or {})
    try:
        notif_dict = notif.dict(exclude_none=True)
    except TypeError:
        notif_dict = {k: v for k, v in notif.dict().items() if v is not None}
    if member_id:
        notif_dict["member_id"] = member_id
    try:
        await (await get_db()).table("notifications").insert(notif_dict).execute()
    except Exception as exc:
        if member_id:
            logger.info("notifications insert with member_id failed (%s) — retrying without it", type(exc).__name__)
            notif_dict.pop("member_id", None)
            await (await get_db()).table("notifications").insert(notif_dict).execute()
        else:
            raise
    if member_id and send_push:
        await send_push_to_member(member_id, title, body, data)
    if email_to:
        await send_email_via_resend(
            to=email_to,
            subject=email_subject or title,
            html=email_html or _build_html_email(title, body),
        )
    return notif_dict


async def notify_assignment(job: dict, member_id: str) -> dict:
    """SMS + push + in-app + email after assign. Never raises. Never claims sent if env missing."""
    result = {"sms": False, "push": False, "in_app": False, "email": False}
    if not member_id:
        return result
    job = job or {}
    member: dict = {}
    try:
        res = await (await get_db()).table("vendor_members").select("*").eq("id", member_id).maybe_single().execute()
        member = res.data or {}
    except Exception as exc:
        logger.error("Assignment notify: failed to load member %s: %s", member_id, exc)

    phone = (member.get("phone") or "").strip()
    details = {
        "job_id": job.get("id"),
        "id": job.get("id"),
        "description": job.get("address") or job.get("package_name") or "",
        "address": job.get("address") or "",
        "scheduled_date": job.get("scheduled_date") or "",
        "time_window_start": job.get("time_window_start") or "",
        "time_window_end": job.get("time_window_end") or "",
        "preferred_window": job.get("preferred_window") or "",
        "confirmed_arrive_at": job.get("confirmed_arrive_at") or "",
        "timezone": job.get("timezone") or job.get("time_zone") or "",
        "state": job.get("state") or job.get("province") or "",
        "zip_code": job.get("zip_code") or job.get("postal_code") or "",
    }
    try:
        from ..notifications import notify_job_assigned_to_vendor
        result["sms"] = bool(notify_job_assigned_to_vendor(phone, details))
    except Exception as exc:
        logger.error("Assignment SMS failed for job %s: %s", job.get("id"), exc)

    when_line = format_when_line(details)
    if when_line == _SHOOT_TIME_UNSET:
        when_line = ""

    title = "New job assigned"
    address = details["address"] or "a property"
    body = address
    if when_line:
        body += f" · {when_line}"
    data = {"type": "job_assigned", "job_id": job.get("id")}

    try:
        result["push"] = bool(await send_push_to_member(member_id, title, body, data))
    except Exception as exc:
        logger.error("Assignment push failed for job %s: %s", job.get("id"), exc)

    try:
        await create_notification(title, body, ntype="job_assigned", data=data, member_id=member_id, send_push=False)
        result["in_app"] = True
    except Exception as exc:
        logger.error("Assignment in-app notification failed for job %s: %s", job.get("id"), exc)

    # Email the photographer — push tokens are often missing on first booking.
    to_email = (member.get("email") or "").strip()
    if to_email:
        try:
            html = _assignment_email_html(
                member.get("name") or "",
                address,
                when_line,
                _GO_WEB_URL,
            )
            result["email"] = bool(
                await send_email_via_resend(
                    to_email,
                    f"New job on Bricks GO — {address}",
                    html,
                )
            )
        except Exception as exc:
            logger.error("Assignment email failed for job %s: %s", job.get("id"), exc)

    return result
