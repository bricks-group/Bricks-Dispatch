import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_TWILIO_CLIENT = None

_SMS_TEMPLATES = {
    1: (
        "Bricks: Your photographer is at {address} for your scheduled shoot. "
        "Please arrive within 15 minutes or the appointment may be marked as a no-show. "
        "Questions? Reply to your booking confirmation email."
    ),
    2: (
        "Bricks FINAL NOTICE: Your photographer has been waiting at {address}. "
        "If you do not arrive within 10 minutes your booking will be marked no-show "
        "and the full cancellation policy will apply."
    ),
}


def _get_twilio_client():
    global _TWILIO_CLIENT
    if _TWILIO_CLIENT is None:
        sid = os.environ.get("TWILIO_ACCOUNT_SID")
        token = os.environ.get("TWILIO_AUTH_TOKEN")
        if not sid or not token:
            return None
        try:
            from twilio.rest import Client
            _TWILIO_CLIENT = Client(sid, token)
        except ImportError:
            logger.error("Twilio package not installed")
            return None
    return _TWILIO_CLIENT


async def send_agent_noshow_sms(
    to_phone: str,
    job_id: str,
    address: str,
    attempt_number: int,
) -> Tuple[Optional[str], bool]:
    """
    Send an escalation SMS to the agent. Returns (sms_sid, sent_ok).
    Gracefully no-ops if Twilio is not configured.
    """
    client = _get_twilio_client()
    if not client:
        logger.warning(f"Twilio not configured — skipping escalation SMS for job {job_id}")
        return None, False

    from_number = os.environ.get("TWILIO_FROM_NUMBER")
    if not from_number:
        logger.warning(f"TWILIO_FROM_NUMBER not set — skipping escalation SMS for job {job_id}")
        return None, False

    template = _SMS_TEMPLATES.get(attempt_number, _SMS_TEMPLATES[2])
    body = template.format(address=address or "the property")

    try:
        message = client.messages.create(body=body, from_=from_number, to=to_phone)
        logger.info(f"Escalation SMS {attempt_number} sent to {to_phone} for job {job_id}: {message.sid}")
        return message.sid, True
    except Exception as e:
        logger.error(f"Twilio SMS failed for job {job_id}: {e}")
        return None, False
