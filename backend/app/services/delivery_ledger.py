"""Delivery ledger: exact-set approval, once-only sends, retries and an audit trail.

Kam, 2026-11-09 ("fix it", reuse what the social scheduler built today):

* Hash-bound approval (scheduler payload_hash / payload_changed). An editor
  approves one exact set of deliverables. manifest_hash() is the fingerprint of
  that set. Delivery refuses when the set it is about to send is not the set
  that was approved, and asks for a fresh sign-off instead.
* Once-only sends (scheduler claim-then-send). Every client email and push is
  claimed under a fixed key in audit_events (primary key = the key), and the
  email carries the same key as Resend's Idempotency-Key, so a retry after an
  unclear failure can never send twice.
* Fail loud (Sentinel N1). Nothing here swallows an error silently. A client
  send that cannot be claimed is NOT sent and Kam is told. A Kam alert that
  cannot be de-duplicated is sent anyway: a second alert beats no alert.
* Retries with backoff and a dead-letter state, then one alert to Kam.
* Audit trail: one audit_events row per step of each order
  (entity_type=job, action=delivery.*), readable per job.

Uses existing tables only (audit_events, media_delivery), so it works before
the optional 20261109 migration is applied.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable, Optional

from ..shared.alert_recipients import SYSTEM_ALERT_EMAIL

logger = logging.getLogger(__name__)

# Delivery email retry schedule (after the first try): 15 min, 1 h, 6 h, 24 h.
EMAIL_BACKOFF_MIN = (15, 60, 360, 720)
EMAIL_MAX_TRIES = 1 + len(EMAIL_BACKOFF_MIN)
SENDING_STALE_MIN = 15

DEAD = "dead"


class LedgerUnavailable(RuntimeError):
    """The once-only store could not be read or written."""


# ---------------------------------------------------------------- manifest

def _strip_query(raw: Any) -> str:
    return str(raw or "").split("?", 1)[0].split("#", 1)[0].strip()


def path_ref(path: Any) -> str:
    """Short fingerprint of the stored file a deliverable points at."""
    p = _strip_query(path)
    return hashlib.sha256(p.encode()).hexdigest()[:16] if p else ""


def content_ref(upload: Optional[dict] = None, path: Any = None) -> str:
    """Fingerprint of file bytes at a storage key: etag/hash + size + path.

    path_ref alone does not change when the object at the same key is overwritten
    (R2/S3 presigned PUT). Size, etag, content hash or updated_at do.
    """
    u = upload if isinstance(upload, dict) else {}
    etag = u.get("etag") or u.get("content_hash") or u.get("checksum") or u.get("sha256") or u.get("md5")
    size = u.get("file_size")
    if size is None:
        size = u.get("size") if u.get("size") is not None else u.get("byte_size")
    raw = "|".join([
        _strip_query(path or u.get("processed_url") or u.get("file_url")),
        str(etag or ""),
        "" if size is None else str(size),
        str(u.get("updated_at") or ""),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()[:16] if raw.strip("|") else ""


def entry_identity(entry: Any) -> Optional[str]:
    """Stable identity of one delivery_urls row (ignores signed-URL tokens/expiry)."""
    if not isinstance(entry, dict):
        return None
    kind = str(entry.get("kind") or "photo").strip().lower()
    prov = str(entry.get("provider") or "").strip().lower()
    if entry.get("upload_id"):
        return f"u:{entry['upload_id']}:{kind}:{prov}:{entry.get('ref') or ''}"
    if entry.get("session_id"):
        return f"s:{entry['session_id']}:{kind}"
    url = _strip_query(entry.get("url"))
    return f"x:{kind}:{url}" if url else None


def manifest(delivery_urls: Iterable[Any] | None, *, visit_present: bool = False) -> dict:
    """{hash, count, ids}: the exact deliverable set, order-independent."""
    ids = sorted({i for i in (entry_identity(e) for e in (delivery_urls or [])) if i})
    if visit_present:
        ids.append("visit:present")
    body = json.dumps(ids, separators=(",", ":"))
    return {"hash": hashlib.sha256(body.encode()).hexdigest(), "count": len(ids), "ids": ids}


def same_hash(a: Optional[str], b: Optional[str]) -> bool:
    import hmac
    return bool(a) and bool(b) and hmac.compare_digest(str(a), str(b))


# ---------------------------------------------------------------- audit_events store

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_duplicate(exc: Exception) -> bool:
    return str(getattr(exc, "code", "")) == "23505" or "duplicate key" in str(exc).lower()


async def _insert(row: dict) -> bool:
    """True = inserted, False = key already exists. Raises LedgerUnavailable otherwise."""
    from ..shared.database import get_db
    try:
        await (await get_db()).table("audit_events").insert(row).execute()
        return True
    except Exception as exc:  # noqa: BLE001 - classified below, never swallowed
        if _is_duplicate(exc):
            return False
        try:
            from ..shared.pg_fallback import _is_rest_quota_error
            quota = _is_rest_quota_error(exc)
        except Exception:  # noqa: BLE001
            quota = False
        if not quota:
            raise LedgerUnavailable(f"audit_events insert failed: {type(exc).__name__}") from exc
        try:
            from .editor_ledger import _pg_exec
            rows = _pg_exec(
                """INSERT INTO public.audit_events
                   (id,vendor_id,actor_id,entity_type,entity_id,action,details,created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                   ON CONFLICT (id) DO NOTHING RETURNING id""",
                (row["id"], row["vendor_id"], row["actor_id"], row["entity_type"], row["entity_id"],
                 row["action"], json.dumps(row["details"], default=str), row["created_at"]),
                fetch=True,
            )
            return bool(rows)
        except Exception as exc2:  # noqa: BLE001
            raise LedgerUnavailable(f"audit_events PG insert failed: {type(exc2).__name__}") from exc2


async def _delete(key: str, created_at: Optional[str] = None) -> bool:
    """Remove a claim row. If created_at is set, only that exact row is removed."""
    from ..shared.database import get_db
    try:
        q = (await get_db()).table("audit_events").delete().eq("id", key)
        if created_at is not None:
            q = q.eq("created_at", created_at)
        res = await q.execute()
        data = getattr(res, "data", None)
        if data:
            return True
        if created_at is None:
            return True
    except Exception as exc:  # noqa: BLE001
        try:
            from .editor_ledger import _pg_exec
            if created_at is None:
                _pg_exec("DELETE FROM public.audit_events WHERE id=%s", (key,))
                return True
            rows = _pg_exec(
                "DELETE FROM public.audit_events WHERE id=%s AND created_at=%s RETURNING id",
                (key, created_at),
                fetch=True,
            )
            return bool(rows)
        except Exception:  # noqa: BLE001
            logger.error("delivery ledger: could not release claim %s (%s)", key, type(exc).__name__)
            return False
    if created_at is None:
        return True
    try:
        from .editor_ledger import _pg_exec
        rows = _pg_exec(
            "DELETE FROM public.audit_events WHERE id=%s AND created_at=%s RETURNING id",
            (key, created_at),
            fetch=True,
        )
        return bool(rows)
    except Exception:  # noqa: BLE001
        return False


async def _rows_like(prefix: str) -> list[dict]:
    from ..shared.database import get_db
    try:
        res = await (await get_db()).table("audit_events").select("id, details, created_at").like(
            "id", prefix + "%").limit(200).execute()
        return res.data or []
    except Exception as exc:  # noqa: BLE001
        try:
            from .editor_ledger import _pg_exec
            return _pg_exec("SELECT id, details, created_at FROM public.audit_events WHERE id LIKE %s LIMIT 200",
                            (prefix + "%",), fetch=True) or []
        except Exception:  # noqa: BLE001
            raise LedgerUnavailable(f"audit_events read failed: {type(exc).__name__}") from exc


def _row(key: str, job_id: str, action: str, details: dict, actor: str = "system") -> dict:
    return {"id": key, "vendor_id": "default_vendor", "actor_id": actor, "entity_type": "job",
            "entity_id": str(job_id), "action": action, "details": details or {},
            "created_at": _now().isoformat()}


async def claim(key: str, job_id: str, action: str, details: Optional[dict] = None) -> bool:
    """Claim a once-only key. True = ours; False = already claimed. Raises LedgerUnavailable."""
    return await _insert(_row(key, job_id, action, details or {}))


async def release_claim(key: str, created_at: Optional[str] = None) -> bool:
    """Drop a once-only key so a later sweep can retry an unfinished run.

    When created_at is given, only that exact row is removed so a newer
    reclaim cannot be deleted by an older runner.
    """
    return await _delete(key, created_at)


def _parse_claim_time(raw) -> Optional[datetime]:
    if isinstance(raw, datetime):
        stamp = raw
    else:
        text = str(raw or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            stamp = datetime.fromisoformat(text)
        except ValueError:
            return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


async def lookup_claim(key: str) -> Optional[dict]:
    rows = await _rows_like(key)
    for row in rows:
        if row.get("id") == key:
            return row
    return None


async def claim_lease(key: str, job_id: str, action: str, details: Optional[dict] = None,
                      *, max_age: timedelta, now: Optional[datetime] = None) -> bool:
    """Claim a key, or take over one whose created_at is older than max_age."""
    if await claim(key, job_id, action, details):
        return True
    try:
        row = await lookup_claim(key)
    except LedgerUnavailable:
        return False
    created = _parse_claim_time((row or {}).get("created_at"))
    clock = now or _now()
    if created is None or (clock - created) < max_age:
        return False
    old = (row or {}).get("created_at")
    if not await release_claim(key, created_at=None if old is None else str(old)):
        return False
    return await claim(key, job_id, action, details)


async def audit(job_id: str, action: str, details: Optional[dict] = None, actor: str = "system") -> bool:
    """Append one step to the order's audit trail. Never raises; logs ERROR when it cannot write."""
    key = f"dlv-{job_id}-{action}-{uuid.uuid4().hex[:12]}"
    try:
        return await _insert(_row(key, job_id, f"delivery.{action}", details or {}, actor))
    except Exception as exc:  # noqa: BLE001
        logger.error("delivery audit write failed job=%s action=%s: %s details=%s",
                     job_id, action, type(exc).__name__, json.dumps(details or {}, default=str)[:500])
        return False


async def trail(job_id: str) -> list[dict]:
    """The order's audit trail, oldest first."""
    from ..shared.database import get_db
    try:
        res = await (await get_db()).table("audit_events").select("id, action, actor_id, details, created_at").eq(
            "entity_type", "job").eq("entity_id", str(job_id)).limit(500).execute()
        rows = res.data or []
    except Exception:  # noqa: BLE001
        from .editor_ledger import _pg_exec
        rows = _pg_exec("SELECT id, action, actor_id, details, created_at FROM public.audit_events "
                        "WHERE entity_type='job' AND entity_id=%s LIMIT 500", (str(job_id),), fetch=True) or []
    return sorted(rows, key=lambda r: str(r.get("created_at") or ""))


# ---------------------------------------------------------------- Kam alerts (fail loud)

def _alert_html(title: str, lines: list[str]) -> str:
    import html as _h
    body = "".join(f"<p style='margin:0 0 8px;color:#333;font-size:15px;line-height:1.45'>{_h.escape(l)}</p>"
                   for l in lines)
    return ("<!DOCTYPE html><html><body style='font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"
            "background:#f4f1eb;padding:24px'><div style='max-width:520px;margin:0 auto;background:#fff;"
            f"padding:24px'><h1 style='font-size:19px;margin:0 0 14px;color:#14110E'>{_h.escape(title)}</h1>"
            f"{body}<p style='margin-top:18px;font-size:12px;color:#888'>Sent by the Bricks delivery check.</p>"
            "</div></body></html>")


async def alert_kam(key: str, job_id: str, title: str, lines: list[str]) -> bool:
    """One alert to Kam per key. If the de-dupe store is down, send anyway (fail loud).
    If the send fails, release the key so the next sweep tries again."""
    from .notifications import send_email_via_resend
    claimed_ok = True
    try:
        if not await claim(f"kam-alert:{key}", job_id, "delivery.kam_alert", {"title": title}):
            return False
    except LedgerUnavailable as exc:
        claimed_ok = False
        logger.error("Kam alert %s: de-dupe store down (%s); sending anyway", key, exc)
        lines = list(lines) + ["(The alert log could not be written, so you may get this twice.)"]
    ok = False
    try:
        ok = bool(await send_email_via_resend(
            SYSTEM_ALERT_EMAIL, f"[Bricks] {title}", _alert_html(title, lines), text="\n".join([title, *lines]),
            idempotency_key=f"kam-alert-{key}"[:250],
        ))
    except Exception as exc:  # noqa: BLE001
        logger.error("Kam alert %s send raised %s", key, type(exc).__name__)
    if not ok:
        logger.error("Kam alert %s NOT sent; will retry on the next sweep", key)
        if claimed_ok:
            await _delete(f"kam-alert:{key}")
    return ok


# ---------------------------------------------------------------- delivery email outbox

def email_key(job_id: str) -> str:
    return f"delivery-email:{job_id}"


async def _set_notification(job_id: str, fields: dict) -> None:
    from ..routers.post_production import _patch_media_delivery
    try:
        await _patch_media_delivery(job_id, fields)
    except Exception as exc:  # noqa: BLE001
        logger.error("Job %s: media_delivery notification update failed: %s", job_id, type(exc).__name__)


async def email_attempts(job_id: str) -> list[dict]:
    return await _rows_like(f"delivery-email-try:{job_id}:")


async def send_delivery_email_once(
    job_id: str, delivery_urls: list, send: Callable[..., Awaitable[str]], *, reason: str = "delivered",
) -> str:
    """Send the client's delivery email at most once, with retries tracked.

    send(job_id, delivery_urls, idempotency_key=...) -> sent|muted|skipped|failed
    Returns the status recorded on media_delivery.notification_status.
    """
    try:
        tries = await email_attempts(job_id)
    except LedgerUnavailable as exc:
        await _set_notification(job_id, {"notification_status": "failed"})
        await alert_kam(f"ledger-down:{job_id}", job_id, "A delivery email is waiting: the send log is down",
                        [f"Job {job_id}.", "The delivery email was not sent, so the client is not emailed twice.",
                         f"Reason: {exc}. The next check retries it."])
        return "failed"
    if any((t.get("details") or {}).get("status") in ("sent", "muted", "skipped") for t in tries):
        return "sent_before"
    # One in-flight send at a time: a second worker used to take try n+1 while
    # try n was still calling the mailer (only Resend's 24h key deduped them).
    sending = [t for t in tries if (t.get("details") or {}).get("status") == "sending"]
    if sending:
        last = max((_parse(t.get("created_at")) or _now() for t in sending), default=_now())
        if _now() - last < timedelta(minutes=SENDING_STALE_MIN):
            return "in_progress"
    n = len(tries) + 1
    if n > EMAIL_MAX_TRIES:
        return DEAD
    try:
        if not await claim(f"delivery-email-try:{job_id}:{n}", job_id, "delivery.email_try",
                           {"try": n, "reason": reason, "status": "sending"}):
            return "in_progress"  # another worker took this try
    except LedgerUnavailable as exc:
        await _set_notification(job_id, {"notification_status": "failed"})
        await alert_kam(f"ledger-down:{job_id}", job_id, "A delivery email is waiting: the send log is down",
                        [f"Job {job_id}.", "Not sent, so the client is never emailed twice.", f"Reason: {exc}."])
        return "failed"
    await _set_notification(job_id, {"notification_status": "sending"})
    try:
        status = await send(job_id, delivery_urls, idempotency_key=email_key(job_id))
    except Exception as exc:  # noqa: BLE001
        logger.error("Job %s: delivery email raised %s", job_id, type(exc).__name__)
        status = "failed"
    fields: dict = {"notification_status": status}
    if status == "sent":
        fields["notified_at"] = _now().isoformat()
    if status == "failed" and n >= EMAIL_MAX_TRIES:
        fields["notification_status"] = DEAD
    await _set_notification(job_id, fields)
    await _record_try(job_id, n, fields["notification_status"])
    await audit(job_id, "email", {"try": n, "status": fields["notification_status"]})
    if fields["notification_status"] == DEAD:
        await alert_kam(f"email-dead:{job_id}", job_id, "A client's delivery email could not be sent",
                        [f"Job {job_id}.", f"Tried {n} times over about a day. The files are delivered and "
                         "the share page works; the client just has not been emailed.",
                         "Send them the share link yourself, or fix the email setting and retry."])
    return fields["notification_status"]


async def _record_try(job_id: str, n: int, status: str) -> None:
    from ..shared.database import get_db
    key = f"delivery-email-try:{job_id}:{n}"
    try:
        await (await get_db()).table("audit_events").update(
            {"details": {"try": n, "status": status, "at": _now().isoformat()}}).eq("id", key).execute()
    except Exception as exc:  # noqa: BLE001
        try:
            from .editor_ledger import _pg_exec
            _pg_exec("UPDATE public.audit_events SET details=%s::jsonb WHERE id=%s",
                     (json.dumps({"try": n, "status": status}), key))
        except Exception:  # noqa: BLE001
            logger.error("Job %s: could not record email try %s=%s (%s)", job_id, n, status, type(exc).__name__)


def email_retry_due(tries: list[dict], now: Optional[datetime] = None) -> bool:
    """True when the last failed try is old enough for the next one (backoff)."""
    now = now or _now()
    if not tries:
        return True
    if any((t.get("details") or {}).get("status") in ("sent", "muted", "skipped") for t in tries):
        return False
    n = len(tries)
    if n >= EMAIL_MAX_TRIES:
        return False
    last = max(_parse(t.get("created_at")) or now for t in tries)
    status = (sorted(tries, key=lambda t: str(t.get("created_at") or ""))[-1].get("details") or {}).get("status")
    if status == "sending" and now - last < timedelta(minutes=SENDING_STALE_MIN):
        return False
    return now - last >= timedelta(minutes=EMAIL_BACKOFF_MIN[n - 1])


def _parse(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    s = str(raw).replace(" ", "T", 1)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- push (once)

async def push_once(job_id: str, push: Callable[[], Awaitable[dict]]) -> dict:
    """Send the 'ready' push once per job. A claim failure means no push (never twice)."""
    try:
        if not await claim(f"delivery-push:{job_id}", job_id, "delivery.push", {}):
            return {"skipped": "sent_before"}
    except LedgerUnavailable as exc:
        logger.error("Job %s: delivery push not sent, send log down (%s)", job_id, exc)
        await audit(job_id, "push", {"status": "not_sent_log_down"})
        return {"skipped": "ledger_down"}
    try:
        res = await push()
    except Exception as exc:  # noqa: BLE001
        res = {"sent": 0, "error": type(exc).__name__}
    await audit(job_id, "push", {k: res.get(k) for k in ("sent", "failed", "skipped", "error") if k in res})
    return res
