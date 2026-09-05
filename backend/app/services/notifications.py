import logging
import os
from ..shared.database import get_db
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
# www.brxs.ca is served by bricks-prelaunch's Vercel project (confirmed live).
_EMAIL_LOGO_URL = "https://www.brxs.ca/bricks-mark.png"


_GO_WEB_URL = os.environ.get("GO_WEB_URL", "https://bricks-dispatch-web.vercel.app").rstrip("/")

_CANONICAL_WINDOWS = {
    "morning": ("09:00", "12:00", "Morning"),
    "afternoon": ("12:00", "16:00", "Afternoon"),
    "evening": ("16:00", "19:00", "Evening"),
}


def _clock_hhmm(raw) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if len(s) >= 5 and s[2] == ":":
        return s[:5]
    return s[:5] if s else ""


def format_shoot_window(job: dict | None) -> str:
    """Match GO web shoot-window.ts. Never invent times."""
    job = job or {}
    start = _clock_hhmm(job.get("time_window_start"))
    end = _clock_hhmm(job.get("time_window_end"))
    preferred = (job.get("preferred_window") or "").strip().lower()
    label = ""
    if preferred in _CANONICAL_WINDOWS:
        label = _CANONICAL_WINDOWS[preferred][2]
    elif start and end:
        for _k, (s, e, lab) in _CANONICAL_WINDOWS.items():
            if s == start and e == end:
                label = lab
                break
    elif preferred:
        label = preferred[:1].upper() + preferred[1:]

    confirmed = _clock_hhmm(job.get("confirmed_arrive_at"))
    if confirmed:
        if label and end:
            return f"{label} · arrives ~{confirmed} ET (until {end})"
        if label:
            return f"{label} · arrives ~{confirmed} ET"
        return f"Arrives ~{confirmed} ET"

    if start and end:
        rng = f"{start}–{end} ET"
        return f"{label} · {rng}" if label else rng
    if start and not end:
        rng = f"{start} ET"
        return f"{label} · from {rng}" if label else f"From {rng}"
    if not start and end:
        rng = f"{end} ET"
        return f"{label} · until {rng}" if label else f"Until {rng}"
    if label:
        return f"{label} · times not set"
    return "Shoot window not set"




def _arrive_confirm_email_html(agent_name: str, photographer: str, address: str, when_line: str, arrive: str) -> str:
    who = (agent_name or "").strip() or "there"
    photo = (photographer or "").strip() or "Your Bricks photographer"
    addr = (address or "").strip() or "your property"
    when = (when_line or "").strip()
    arrive_s = (arrive or "").strip()
    when_p = f'<p style="color:#555;line-height:1.6;margin:0 0 12px 0">Window: {when}</p>' if when else ""
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
<p style="color:#555;line-height:1.6;margin:0 0 12px 0"><strong>{photo}</strong> will arrive around <strong>{arrive_s} ET</strong> at <strong>{addr}</strong>.</p>
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
    arrive = _clock_hhmm(job.get("confirmed_arrive_at")) or _clock_hhmm(job.get("time_window_start"))
    if not arrive:
        return result
    window = format_shoot_window(job)
    date = (job.get("scheduled_date") or "")[:10]
    when_bits = [b for b in (date, window if window != "Shoot window not set" else "") if b]
    when_line = " · ".join(when_bits)
    photo_name = ((photographer or {}).get("name") or "").strip() or "Your Bricks photographer"
    try:
        html = _arrive_confirm_email_html(
            job.get("agent_name") or "",
            photo_name,
            job.get("address") or "",
            when_line,
            arrive,
        )
        result["email"] = bool(
            await send_email_via_resend(
                to_email,
                f"Photographer arriving ~{arrive} ET — {job.get('address') or 'your shoot'}",
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
<h1 style="color:#fff;margin:0;font-size:20px;font-weight:600;font-family:{_EMAIL_FONT_STACK}">{title}</h1>
</div>
<div style="padding:24px"><p style="color:#333;font-size:15px;line-height:1.5;margin:0;font-family:{_EMAIL_FONT_STACK}">{body}</p></div>
<div style="padding:12px 24px;border-top:1px solid #eee;font-size:12px;color:#888;font-family:{_EMAIL_FONT_STACK}">
<p style="margin:0">Bricks &middot; brxs.ca</p></div>
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
            html=_build_html_email(title, body),
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
    }
    try:
        from ..notifications import notify_job_assigned_to_vendor
        result["sms"] = bool(notify_job_assigned_to_vendor(phone, details))
    except Exception as exc:
        logger.error("Assignment SMS failed for job %s: %s", job.get("id"), exc)

    window = format_shoot_window(details)
    date = (details["scheduled_date"] or "")[:10]
    when_bits = [b for b in (date, window if window != "Shoot window not set" else "") if b]
    when_line = " · ".join(when_bits)

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
