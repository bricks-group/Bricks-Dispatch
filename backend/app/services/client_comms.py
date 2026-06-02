"""
Client communications agent — triggered at key job lifecycle events.

Events handled:
  booking_confirmed   — when job is created / assigned
  photographer_en_route — when job moves to in_progress
  photos_ready        — handled separately in post_production.py

Delivery: Resend email (primary) + future push notification hook.
"""
import html
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = "Bricks <noreply@brxs.ca>"


async def _send_email(to: str, subject: str, html: str) -> bool:
    if not RESEND_API_KEY:
        logger.info(f"RESEND_API_KEY not set — skipping email to {to}: {subject}")
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                json={"from": FROM_EMAIL, "to": [to], "subject": subject, "html": html},
            )
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"Email send failed to {to}: {e}")
        return False


async def _get_agent_email_for_job(job_id: str) -> Optional[tuple]:
    from ..shared.database import get_db
    db = await get_db()
    res_job = await db.table("jobs").select("booking_id").eq("id", job_id).maybe_single().execute()
    if not res_job.data:
        return None
    booking_id = res_job.data.get("booking_id")
    if not booking_id:
        return None
    res_booking = await db.table("bookings").select("user_id").eq("id", booking_id).maybe_single().execute()
    if not res_booking.data:
        return None
    user_id = res_booking.data.get("user_id")
    if not user_id:
        return None
    res_user = await db.table("users").select("email, name").eq("id", user_id).maybe_single().execute()
    if not res_user.data:
        return None
    return res_user.data.get("email"), res_user.data.get("name", "")


async def notify_booking_confirmed(job_id: str) -> None:
    """
    Sends 'Your shoot is confirmed' email to agent when a job is assigned.
    """
    from ..shared.database import get_db
    db = await get_db()

    res_job = await db.table("jobs").select(
        "id, address, scheduled_date, time_window_start, package_name, assignee_id"
    ).eq("id", job_id).maybe_single().execute()
    if not res_job.data:
        return

    job = res_job.data
    result = await _get_agent_email_for_job(job_id)
    if not result:
        return
    agent_email, agent_name = result if isinstance(result, tuple) else (result, "")

    # Look up assigned photographer name
    photographer_name = "your Bricks photographer"
    assignee_id = job.get("assignee_id")
    if assignee_id:
        res_member = await db.table("vendor_members").select("name").eq("id", assignee_id).maybe_single().execute()
        if res_member.data:
            photographer_name = res_member.data.get("name", photographer_name)

    subject = f"Shoot confirmed — {job.get('address', 'your property')}"
    _safe_name = html.escape(agent_name or 'there')
    _safe_addr = html.escape(job.get('address', ''))
    _safe_photo = html.escape(photographer_name)
    _safe_date = html.escape(job.get('scheduled_date', ''))
    _safe_time = html.escape(job.get('time_window_start', ''))
    _safe_pkg = html.escape(job.get('package_name', ''))
    body = f"""
<p>Hi {_safe_name},</p>
<p>Your shoot is confirmed. Here are the details:</p>
<table style="border-collapse:collapse;font-size:14px;">
  <tr><td style="padding:6px 12px;color:#888;">Property</td><td style="padding:6px 12px;"><strong>{_safe_addr}</strong></td></tr>
  <tr><td style="padding:6px 12px;color:#888;">Date</td><td style="padding:6px 12px;">{_safe_date}</td></tr>
  <tr><td style="padding:6px 12px;color:#888;">Time</td><td style="padding:6px 12px;">{_safe_time}</td></tr>
  <tr><td style="padding:6px 12px;color:#888;">Package</td><td style="padding:6px 12px;">{_safe_pkg}</td></tr>
  <tr><td style="padding:6px 12px;color:#888;">Photographer</td><td style="padding:6px 12px;">{_safe_photo}</td></tr>
</table>
<p style="margin-top:16px;font-size:13px;color:#888;">
  Need to reschedule or cancel? Reply to this email or contact support@brxs.ca.
  Cancellations within 24h may be subject to a fee.
</p>
<p style="font-size:13px;color:#bbb;">— The Bricks team · brxs.ca</p>
"""
    await _send_email(agent_email, subject, body)
    logger.info(f"Booking confirmed email sent for job {job_id} to {agent_email}")


async def notify_photographer_en_route(job_id: str) -> None:
    """
    Sends 'Photographer is on the way' email to agent when job moves to in_progress.
    """
    from ..shared.database import get_db
    db = await get_db()

    res_job = await db.table("jobs").select(
        "id, address, scheduled_date, time_window_start, assignee_id"
    ).eq("id", job_id).maybe_single().execute()
    if not res_job.data:
        return

    job = res_job.data
    result = await _get_agent_email_for_job(job_id)
    if not result:
        return
    agent_email, agent_name = result if isinstance(result, tuple) else (result, "")

    photographer_name = "Your Bricks photographer"
    assignee_id = job.get("assignee_id")
    if assignee_id:
        res_member = await db.table("vendor_members").select("name, phone").eq("id", assignee_id).maybe_single().execute()
        if res_member.data:
            photographer_name = res_member.data.get("name", photographer_name)

    subject = f"Photographer en route — {job.get('address', 'your property')}"
    _safe_name = html.escape(agent_name or 'there')
    _safe_addr = html.escape(job.get('address', ''))
    _safe_photo = html.escape(photographer_name)
    _safe_date = html.escape(job.get('scheduled_date', ''))
    _safe_time = html.escape(job.get('time_window_start', ''))
    body = f"""
<p>Hi {_safe_name},</p>
<p><strong>{_safe_photo}</strong> is on their way to your property now.</p>
<table style="border-collapse:collapse;font-size:14px;">
  <tr><td style="padding:6px 12px;color:#888;">Property</td><td style="padding:6px 12px;">{_safe_addr}</td></tr>
  <tr><td style="padding:6px 12px;color:#888;">Scheduled</td><td style="padding:6px 12px;">{_safe_date} at {_safe_time}</td></tr>
</table>
<p style="margin-top:16px;font-size:13px;color:#888;">
  Please ensure the property is accessible. If you need to reach the photographer,
  contact support@brxs.ca and we'll connect you immediately.
</p>
<p style="font-size:13px;color:#bbb;">— The Bricks team · brxs.ca</p>
"""
    await _send_email(agent_email, subject, body)
    logger.info(f"En-route email sent for job {job_id} to {agent_email}")
