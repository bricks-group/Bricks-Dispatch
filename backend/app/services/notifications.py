import logging
from ..shared.database import get_db
from ..shared.schemas import NotificationRecord

logger = logging.getLogger(__name__)

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

async def create_notification(title: str, body: str, ntype: str = "info", data: dict | None = None):
    notif = NotificationRecord(title=title, body=body, type=ntype, data=data or {})
    notif_dict = notif.dict()
    await (await get_db()).table("notifications").insert(notif_dict).execute()
    await send_push_notification(title, body, data)
    return notif_dict
