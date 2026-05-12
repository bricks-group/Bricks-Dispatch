import logging
import os
from ..shared.database import get_db
from ..shared.schemas import NotificationRecord

logger = logging.getLogger(__name__)


def _build_html_email(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f4f1eb;padding:24px">
<div style="max-width:480px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)">
<div style="background:#d2542b;padding:16px 24px">
<h1 style="color:#fff;margin:0;font-size:20px;font-weight:600">{title}</h1>
</div>
<div style="padding:24px"><p style="color:#333;font-size:15px;line-height:1.5;margin:0">{body}</p></div>
<div style="padding:12px 24px;border-top:1px solid #eee;font-size:12px;color:#888">
<p style="margin:0">BRICKS — Real Estate Media Platform</p></div>
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
                    "from": "BRICKS <notifications@bricks.io>",
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


async def send_push_notification(title: str, body: str, data: dict | None = None):
    res = await (await get_db()).table("push_tokens").select("*").eq("active", True).limit(100).execute()
    tokens = res.data
    if not tokens:
        return

    try:
        from exponent_server_sdk import PushClient, PushMessage, DeviceNotRegisteredError
        import requests

        session = requests.Session()
        session.headers.update({
            "accept": "application/json",
            "accept-encoding": "gzip, deflate",
            "content-type": "application/json",
        })
        push_client = PushClient(session=session)

        for token_doc in tokens:
            try:
                msg = PushMessage(
                    to=token_doc["token"],
                    title=title,
                    body=body,
                    data=data or {},
                    sound="default",
                    priority="high",
                )
                response = push_client.publish(msg)
                response.validate_response()
            except DeviceNotRegisteredError:
                await (await get_db()).table("push_tokens").update({"active": False}).eq("token", token_doc["token"]).execute()
            except Exception as exc:
                logger.error("Push error: %s", exc)
    except ImportError:
        logger.warning("exponent_server_sdk not available, skipping push")
    except Exception as exc:
        logger.error("Push notification error: %s", exc)


async def create_notification(
    title: str,
    body: str,
    ntype: str = "info",
    data: dict | None = None,
    *,
    email_to: str | None = None,
    email_subject: str | None = None,
):
    notif = NotificationRecord(title=title, body=body, type=ntype, data=data or {})
    notif_dict = notif.dict()
    await (await get_db()).table("notifications").insert(notif_dict).execute()
    await send_push_notification(title, body, data)
    if email_to:
        await send_email_via_resend(
            to=email_to,
            subject=email_subject or title,
            html=_build_html_email(title, body),
        )
    return notif_dict
