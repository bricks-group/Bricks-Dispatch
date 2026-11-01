"""SMS notifications for Dispatch assignment.

Reads Twilio env at call time. Missing credentials → log and return False.
Never reports sent unless Twilio actually queued/sent/delivered the message.
"""
import logging
import os

logger = logging.getLogger(__name__)


def _twilio_config():
    sid = (os.environ.get("TWILIO_ACCOUNT_SID") or "").strip()
    token = (os.environ.get("TWILIO_AUTH_TOKEN") or "").strip()
    from_number = (
        (os.environ.get("TWILIO_PHONE_NUMBER") or "").strip()
        or (os.environ.get("TWILIO_FROM_NUMBER") or "").strip()
    )
    return sid, token, from_number


def send_dispatch_sms(to: str, body: str) -> bool:
    """Sends an SMS for dispatch. False if Twilio is missing or the send fails."""
    if not (to or "").strip():
        logger.info("Twilio SMS skipped — no destination phone")
        return False
    sid, token, from_number = _twilio_config()
    if not all([sid, token, from_number]):
        logger.info("Twilio credentials not fully configured. Skipping dispatch SMS.")
        return False
    try:
        from twilio.rest import Client as TwilioClient
    except ImportError:
        logger.info("Twilio package not installed. Skipping dispatch SMS.")
        return False
    try:
        message = TwilioClient(sid, token).messages.create(
            body=body,
            from_=from_number,
            to=to,
        )
        status = getattr(message, "status", None)
        logger.info("Dispatch SMS to %s status=%s", to, status)
        return status in ("queued", "sent", "delivered")
    except Exception as e:
        logger.error("Error sending dispatch SMS: %s", e)
        return False


def notify_job_assigned_to_vendor(vendor_phone: str, job_details: dict) -> bool:
    """SMS when a job is assigned. Returns False if Twilio/phone missing — never fakes sent."""
    details = job_details or {}
    address = details.get("address") or details.get("description") or "a property"
    scheduled = details.get("scheduled_date") or ""
    window = details.get("time_window_start") or ""
    body = f"Bricks: new job assigned — {address}"
    if scheduled:
        body += f" on {scheduled}"
    if window:
        body += f" at {window}"
    body += ". Open the Bricks app for details."
    return send_dispatch_sms(to=vendor_phone, body=body)


def notify_job_status_change(vendor_phone: str, job_details: dict, new_status: str) -> bool:
    """SMS when a job's status changes. Same missing-env contract as assignment."""
    details = job_details or {}
    job_id = details.get("job_id") or details.get("id") or "N/A"
    body = f"Bricks: job {job_id} is now {new_status}."
    return send_dispatch_sms(to=vendor_phone, body=body)


def public_notification(row: dict) -> dict:
    """Shape both Expo (message/type/read) and Swift (body/notification_type/is_read)."""
    body = row.get("body") or row.get("message") or ""
    ntype = row.get("type") or row.get("notification_type") or "info"
    read = bool(row.get("read") or row.get("is_read"))
    return {
        "id": row.get("id"),
        "title": row.get("title") or "",
        "body": body,
        "message": body,
        "type": ntype,
        "notification_type": ntype,
        "read": read,
        "is_read": read,
        "created_at": row.get("created_at") or "",
        "data": row.get("data") or {},
    }
