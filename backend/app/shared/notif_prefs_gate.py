"""Honour the agent's Bricks OS Settings → Notifications toggles.

Bricks OS stores one row per user in public.notification_prefs (same Supabase
database), keyed by user id with the lower-cased account email. Dispatch only
knows jobs.agent_email, so we look the agent up by email.

Fails OPEN: no row, no table, or no database means "send", because dropping a
booking or delivery receipt silently is worse than sending one that was muted.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

FIELDS = frozenset({"new_booking_email", "new_booking_push", "delivery_ready_email"})


def agent_allows(email: str, field: str, *, connect=None) -> bool:
    if field not in FIELDS:
        raise ValueError(f"unknown notification field {field}")
    addr = (email or "").strip()
    if not addr:
        return True
    conn = None
    try:
        if connect is None:
            from .pg_fallback import _pg_connect as connect  # noqa: N813
        conn = connect()
        if conn is None:
            return True
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {field} FROM public.notification_prefs "
                "WHERE lower(email) = lower(%s) ORDER BY updated_at DESC LIMIT 1",
                (addr,),
            )
            row = cur.fetchone()
        if not row or row[0] is None:
            return True
        return bool(row[0])
    except Exception as e:  # noqa: BLE001 - missing table / DB blip => send
        logger.info("notification prefs lookup skipped: %s", type(e).__name__)
        return True
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
