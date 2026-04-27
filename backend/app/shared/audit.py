from .database import get_db
from .schemas import AuditEvent

async def write_audit_event(
    actor_id: str,
    entity_type: str,
    entity_id: str,
    action: str,
    vendor_id: str = "default_vendor",
    details: dict | None = None,
):
    event = AuditEvent(
        vendor_id=vendor_id,
        actor_id=actor_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        details=details or {},
    )
    await (await get_db()).table("audit_events").insert(event.dict()).execute()
    return event.dict()
