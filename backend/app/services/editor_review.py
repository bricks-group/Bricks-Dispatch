"""Admin-only editor queue and at-most-once transactional hold notices.

Uses the same audit_events ledger + Resend delivery as Autoenhance holds, but
claims with INSERT/unique id rather than read-then-upsert (concurrent safe).
A claimed send is never retried automatically, even after an ambiguous failure.
"""
from __future__ import annotations

from ..shared.alert_recipients import SYSTEM_ALERT_EMAIL
import json
import logging
import os
from datetime import datetime, timezone
from html import escape
from urllib.parse import quote

from . import fulfillment_gate as fg
from .notifications import send_email_via_resend

logger = logging.getLogger(__name__)
DIGEST_KEY = "editor-review-initial-digest-2026-11-07"


def alert_email() -> str:
    return SYSTEM_ALERT_EMAIL


def queue_url(job_id: str = "") -> str:
    base = os.environ.get("EDITOR_REVIEW_URL", "https://go.brxs.ca/admin/review").rstrip("/")
    return base + ("#" + quote(str(job_id), safe="") if job_id else "")


def client_label(job: dict) -> str:
    return str(job.get("client_name") or job.get("agent_name") or job.get("client_email")
               or job.get("agent_email") or job.get("client_id") or "Client not recorded")


async def _claim_alert(key: str, job_id: str, details: dict) -> bool:
    from ..shared.database import get_db
    row = {"id": key, "vendor_id": "default_vendor", "actor_id": "system",
           "entity_type": "job", "entity_id": job_id, "action": "editor_review_alert",
           "details": details, "created_at": datetime.now(timezone.utc).isoformat()}
    try:
        await (await get_db()).table("audit_events").insert(row).execute()
        return True
    except Exception as exc:
        if str(getattr(exc, "code", "")) == "23505" or "duplicate key" in str(exc).lower():
            return False
        from ..shared.pg_fallback import _is_rest_quota_error
        if not _is_rest_quota_error(exc):
            raise
        from .editor_ledger import _pg_exec
        rows = _pg_exec("""INSERT INTO public.audit_events
            (id,vendor_id,actor_id,entity_type,entity_id,action,details,created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
            ON CONFLICT (id) DO NOTHING RETURNING id""",
            (key, row["vendor_id"], row["actor_id"], row["entity_type"], job_id,
             row["action"], json.dumps(details), row["created_at"]), fetch=True)
        return bool(rows)


def _hold_text(job: dict, verdict: dict) -> str:
    return "\n".join([
        f"Property: {job.get('address') or 'Address not recorded'}",
        f"Job: {job['id']}", f"Client: {client_label(job)}",
        "Required: " + ", ".join(verdict.get("required") or []),
        "Present: " + (", ".join(verdict.get("present") or []) or "none"),
        "Missing: " + (", ".join(verdict.get("missing") or []) or "none"),
        "Hold: " + "; ".join(verdict.get("reasons") or []),
        "Delivery and payout remain held. Watch the media and approve only when complete.",
        queue_url(job["id"]),
    ])


def _html(text: str, url: str) -> str:
    return ("<h2>Bricks · Editor review</h2><p>" + escape(text).replace("\n", "<br>")
            + '</p><p><a href="' + escape(url, quote=True) + '">Open review queue</a></p>')


async def alert_editor_hold(job: dict, verdict: dict) -> bool:
    if verdict.get("ready") or verdict.get("editor_approved"):
        return False
    try:
        if not await _claim_alert(f"editor-hold-{job['id']}", job["id"], {"verdict": verdict, "to": alert_email()}):
            return False
        text = _hold_text(job, verdict)
        ok = await send_email_via_resend(to=alert_email(),
            subject=f"Bricks: editor review needed — {job.get('address') or job['id']}",
            html=_html(text, queue_url(job["id"])), text=text,
            idempotency_key=f"editor-hold-{job['id']}")
        if not ok:
            # Fail loud (Sentinel N1 lesson): release the claim so the next
            # finalize or sweep alerts again. Resend's idempotency key stops a
            # duplicate if the first send actually went out.
            logger.error("Editor alert send failed; releasing claim so it retries: %s", job["id"])
            from .delivery_ledger import _delete
            await _delete(f"editor-hold-{job['id']}")
        return bool(ok)
    except Exception:
        logger.exception("Editor hold alert failed for %s", job.get("id"))
        return False


async def send_initial_digest(items: list[dict]) -> bool:
    """Explicit one-time backfill only; never invoked by reading the queue."""
    items = [j for j in items if j.get("waiting_since")]
    if not items:
        return False
    if not await _claim_alert(DIGEST_KEY, "editor-review", {"job_ids": [j["id"] for j in items]}):
        return False
    selected = []
    for item in items:
        if await _claim_alert(f"editor-hold-{item['id']}", item["id"], {"digest": DIGEST_KEY, "to": alert_email()}):
            selected.append(item)
    if not selected:
        return False
    blocks = [_hold_text({**j, "client_name": j.get("client")}, j["verdict"]) for j in selected]
    text = f"{len(selected)} jobs are waiting for your editor review.\n\n" + "\n\n".join(blocks)
    return bool(await send_email_via_resend(to=alert_email(),
        subject=f"Bricks: {len(selected)} jobs waiting for editor review",
        html=_html(text, queue_url()), text=text))


def deliverable_entries(uploads: list[dict]) -> list[dict]:
    """Mirror finalize's QC/provider rules without running QC or mutating jobs."""
    from ..routers.post_production import _still_delivery_url_entry, is_listing_still_delivery_row
    from .raw_decode import is_camera_raw_name
    out = []
    for u in uploads:
        path = u.get("processed_url") or u.get("file_url")
        if (not path or u.get("qc_status") != "passed" or u.get("processing_status") != "done"
                or not is_listing_still_delivery_row(u) or is_camera_raw_name(path)
                or fg.is_source_clip(u)):
            continue
        out.append(_still_delivery_url_entry(u, path, ""))
    return out


async def _query(db, table: str, job_id: str | None = None) -> list[dict]:
    try:
        q = db.table(table).select("*")
        if job_id:
            q = q.eq("job_id", job_id)
        else:
            q = q.in_("status", ["pending", "confirmed", "scheduled", "assigned", "accepted",
                                "in_progress", "completed", "processing", "qc_failed"])
        return (await q.execute()).data or []
    except Exception as exc:
        from ..shared.pg_fallback import _is_rest_quota_error
        if not _is_rest_quota_error(exc):
            raise
        from .editor_ledger import _pg_exec
        # Fixed identifiers only, never request-controlled.
        if job_id and table in ("uploads", "capture_sessions"):
            return _pg_exec(f"SELECT * FROM public.{table} WHERE job_id=%s", (job_id,), fetch=True) or []
        if table == "jobs" and not job_id:
            return _pg_exec("SELECT * FROM public.jobs WHERE status = ANY(%s)",
                           (["pending", "confirmed", "scheduled", "assigned", "accepted", "in_progress",
                             "completed", "processing", "qc_failed"],), fetch=True) or []
        raise


def _saved(job: dict) -> dict:
    saved = job.get("fulfillment_gate") or {}
    if isinstance(saved, str):
        try:
            saved = json.loads(saved)
        except ValueError:
            saved = {}
    return saved if isinstance(saved, dict) else {}


async def _delivered_with_pending(db) -> list[dict]:
    """Delivered orders holding late files for a fresh sign-off."""
    try:
        rows = (await db.table("jobs").select("*").eq("status", "delivered").limit(300).execute()).data or []
    except Exception as exc:  # noqa: BLE001
        logger.error("editor queue: delivered-job read failed (%s)", type(exc).__name__)
        return []
    return [j for j in rows if _saved(j).get("pending_manifest_hash")]


def _needs_reapproval(job: dict) -> list[str]:
    """Gate reasons saying the approval no longer matches the files."""
    return [r for r in (_saved(job).get("reasons") or []) if "approv" in str(r) and "files" in str(r)]


def _approval_target(job: dict) -> dict:
    """The exact set an approval binds to (what finalize last computed)."""
    saved = _saved(job)
    if job.get("status") == "delivered" and saved.get("pending_manifest_hash"):
        return {"hash": saved["pending_manifest_hash"], "count": saved.get("pending_manifest_count") or 0,
                "ids": saved.get("pending_manifest_ids") or [], "late": True}
    return {"hash": saved.get("manifest_hash") or "", "count": saved.get("manifest_count") or 0,
            "ids": saved.get("manifest_ids") or [], "late": False}


async def list_review_items(db, *, include_media: bool = True) -> list[dict]:
    from ..routers.post_production import _list_delivered_capture_sessions, _delivery_urls_from_capture_sessions
    from .storage import get_signed_download_url
    items = []
    jobs = await _query(db, "jobs")
    jobs += await _delivered_with_pending(db)
    for job in jobs:
        late = job.get("status") == "delivered" and bool(_saved(job).get("pending_manifest_hash"))
        if fg.is_trade_job(job) or job.get("status") in ("cancelled", "canceled"):
            continue
        if not late and (fg.editor_approved(job) or job.get("status") == "delivered"):
            # Approved jobs whose files changed since come back via the gate
            # (finalize clears editor_approved in the verdict); show those too.
            if not (fg.editor_approved(job) and _needs_reapproval(job)):
                continue
        uploads = await _query(db, "uploads", job["id"])
        entries = deliverable_entries(uploads)
        captures = await _list_delivered_capture_sessions(db, job["id"])
        entries += _delivery_urls_from_capture_sessions(job["id"], captures)
        from ..routers.post_production import pick_visit_upload
        visit_present = bool(pick_visit_upload(uploads))
        # A saved verdict includes Visit-bridge readiness not represented in uploads.
        saved = job.get("fulfillment_gate") or {}
        if isinstance(saved, str):
            try: saved = json.loads(saved)
            except ValueError: saved = {}
        visit_present = visit_present or "visit" in (saved.get("present") or [])
        verdict = fg.evaluate(job, entries, visit_present=visit_present)
        media = []
        if include_media:
            for u in uploads:
                if str(u.get("provider") or "").lower() == "bracket_sibling":
                    continue
                path = u.get("processed_url") or u.get("file_url")
                if not path:
                    continue
                url = path if str(path).startswith("https://") else await get_signed_download_url(path)
                if not url:
                    continue
                video = fg.is_source_clip(u) or str(u.get("mime_type") or "").startswith("video/") or str(u.get("filename") or "").lower().endswith(fg._VIDEO_EXTS)
                media.append({"id": u["id"], "filename": u.get("filename"), "url": url,
                              "kind": "video" if video else "file" if str(u.get("filename") or "").lower().endswith((".pdf", ".ply", ".splat", ".spz")) else "image",
                              "provider": u.get("provider"), "qc_status": u.get("qc_status"),
                              "processing_status": u.get("processing_status")})
        changed = _needs_reapproval(job)
        if changed:
            verdict["editor_approved"] = False
            verdict["reasons"] = list(verdict.get("reasons") or []) + changed
        target = _approval_target(job)
        in_set = {i.split(":")[1] for i in target["ids"] if i.startswith(("u:", "s:"))}
        for m_ in media:
            m_["in_delivery"] = m_["id"] in in_set
        items.append({"id": job["id"], "address": job.get("address"), "client": client_label(job),
                      "manifest_hash": target["hash"], "manifest_count": target["count"],
                      "late_files": target["late"],
                      "scheduled_date": job.get("scheduled_date"), "created_at": job.get("created_at"),
                      "waiting_since": saved.get("held_at"),
                      "status": job.get("status"), "verdict": verdict, "media": media,
                      "capture_media": [e for e in entries if not e.get("upload_id")]})
    return sorted(items, key=lambda j: str(j.get("waiting_since") or ""))
