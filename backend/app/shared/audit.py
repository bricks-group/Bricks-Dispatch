from .database import db
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
    await db.audit_events.insert_one(event.dict())
    return event.dict()

