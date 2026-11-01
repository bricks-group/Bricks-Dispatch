"""Editor cost ledger + Autoenhance plan watch (Kam, Oct 31 2026).

Kam: "The credits land as soon as the first customer pays ... take it from the
bricks cut." and "We will sign up for the 30 a month for 100 images".

What this does (no migration — rows live in audit_events with fixed ids):

* record_editor_cost(job_id): after delivery, counts stills Autoenhance edited
  for the job (uploads.provider='autoenhance'; one credit per downloaded image)
  and writes a job ledger row  id=ledger-editor-<job_id>, entity_type=job_ledger,
  action=editor_cost.  The cost comes out of Bricks' cut only:
      bricks_cut_after = client_price - vendor_payout - editor_cost
  Vendor pay and the client price are never changed. Stills that fell back to
  the in-house editor (bricks_inhouse) are counted and the job is marked
  reedit_candidate so it can be re-run through Autoenhance once credits exist.

* maybe_prompt_autoenhance_plan(job): on the first LIVE paid booking (the www
  webhook stamps "stripe cs_live_..." into property_details; cs_test_ never
  counts) — once ever — tell Kam in-app and by email to his own address with a
  one-tap link to the Autoenhance subscription page. Autoenhance has no API to
  buy a plan or read credits, so the purchase itself is Kam's tap with the card
  he keeps in Autoenhance. No card data ever touches Bricks.

* low-credit check: images ledgered this calendar month vs the plan size
  (AUTOENHANCE_MONTHLY_CREDITS, default 100). At or under the threshold
  (AUTOENHANCE_LOW_CREDIT_AT, default 20) Kam gets one alert per month.

Env (all optional):
  AUTOENHANCE_PLAN_ACTIVE=true          plan bought; stop the subscribe prompt
  AUTOENHANCE_UNIT_COST_CENTS=29        USD cents per image (Essential $29/100)
  AUTOENHANCE_MONTHLY_CREDITS=100
  AUTOENHANCE_LOW_CREDIT_AT=20
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

SUBSCRIBE_URL = "https://app.autoenhance.ai/settings?setting=subscription"
PRICING_URL = "https://www.autoenhance.ai/pricing"
KAM_EMAIL = "akashorter@gmail.com"
PROMPT_ID = "ae-plan-prompt"
_LIVE_SESSION = re.compile(r"\bstripe\s+cs_live_[A-Za-z0-9]+")


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "")).strip() or default)
    except ValueError:
        return default


def plan_active() -> bool:
    return str(os.environ.get("AUTOENHANCE_PLAN_ACTIVE", "")).strip().lower() in ("1", "true", "yes")


def unit_cost_cents() -> int:
    return _env_int("AUTOENHANCE_UNIT_COST_CENTS", 29)


def is_live_paid_booking(job: dict) -> bool:
    """True only for a www booking paid in Stripe live mode."""
    details = str((job or {}).get("property_details") or "")
    return bool(_LIVE_SESSION.search(details))


def _dollars_to_cents(v: Any) -> int:
    try:
        return int(round(float(v) * 100))
    except (TypeError, ValueError):
        return 0


def editor_cost_entry(job: dict, uploads: list[dict], vendor_payout_cents: Optional[int]) -> dict:
    """Pure: the ledger details for one job. Deducts only from Bricks' cut."""
    primaries = [u for u in uploads if (u.get("provider") or "") not in ("bracket_sibling", "")]
    ae = sum(1 for u in primaries if u.get("provider") == "autoenhance")
    inhouse = sum(1 for u in primaries if u.get("provider") == "bricks_inhouse")
    unit = unit_cost_cents()
    editor_cost = ae * unit
    price = job.get("package_price")
    if price is None:
        price = job.get("rack_price")
    client_cents = _dollars_to_cents(price)
    payout = int(vendor_payout_cents) if vendor_payout_cents is not None else None
    before = (client_cents - payout) if payout is not None else None
    return {
        "provider": "autoenhance",
        "autoenhance_images": ae,
        "inhouse_images": inhouse,
        "unit_cost_cents": unit,
        "unit_cost_currency": "USD",
        "editor_cost_cents": editor_cost,
        "deducted_from": "bricks_cut",
        "client_price_cents": client_cents,
        "vendor_payout_cents": payout,
        "bricks_cut_before_cents": before,
        "bricks_cut_after_cents": (before - editor_cost) if before is not None else None,
        "reedit_candidate": inhouse > 0,
        "test_job": not is_live_paid_booking(job),
    }


def _pg_exec(sql: str, params: tuple, fetch: bool = False):
    from ..shared.pg_fallback import _pg_connect, _rows_from_cursor
    conn = _pg_connect()
    if not conn:
        raise RuntimeError("no Postgres connection")
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = _rows_from_cursor(cur) if fetch else None
        conn.commit()
        return rows
    finally:
        try:
            conn.close()
        except Exception:
            pass


async def _get_event(event_id: str) -> Optional[dict]:
    try:
        from ..shared.database import get_db
        res = await (await get_db()).table("audit_events").select("*").eq("id", event_id).limit(1).execute()
        return (res.data or [None])[0]
    except Exception:
        rows = _pg_exec("SELECT * FROM public.audit_events WHERE id=%s LIMIT 1", (event_id,), fetch=True)
        return (rows or [None])[0]


async def _upsert_event(event_id: str, entity_type: str, entity_id: str, action: str, details: dict) -> None:
    row = {
        "id": event_id,
        "vendor_id": "default_vendor",
        "actor_id": "system",
        "entity_type": entity_type,
        "entity_id": entity_id,
        "action": action,
        "details": details,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        from ..shared.database import get_db
        await (await get_db()).table("audit_events").upsert(row).execute()
    except Exception:
        _pg_exec(
            """INSERT INTO public.audit_events (id, vendor_id, actor_id, entity_type, entity_id, action, details, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
               ON CONFLICT (id) DO UPDATE SET details=EXCLUDED.details, action=EXCLUDED.action""",
            (row["id"], row["vendor_id"], row["actor_id"], entity_type, entity_id, action,
             json.dumps(details), row["created_at"]),
        )


async def _job_and_uploads(job_id: str) -> tuple[dict, list[dict], Optional[int]]:
    from ..shared.database import get_db
    db = await get_db()
    try:
        job = ((await db.table("jobs").select("*").eq("id", job_id).limit(1).execute()).data or [{}])[0]
        uploads = (await db.table("uploads").select("id,provider").eq("job_id", job_id).execute()).data or []
        pays = (await db.table("payouts").select("amount,net_amount,status").eq("job_id", job_id).execute()).data or []
    except Exception:
        from ..shared.pg_fallback import pg_select_job_by_id, pg_select_uploads_by_job_id, pg_select_payout_by_job_id
        job = pg_select_job_by_id(job_id) or {}
        uploads = pg_select_uploads_by_job_id(job_id)
        p = pg_select_payout_by_job_id(job_id)
        pays = [p] if p else []
    payout = None
    live = [p for p in pays if (p or {}).get("status") != "failed"]
    if live:
        payout = sum(int(p.get("net_amount") or p.get("amount") or 0) for p in live)
    elif job.get("payout_amount") is not None:
        payout = int(job["payout_amount"])
    return job, uploads, payout


async def record_editor_cost(job_id: str) -> Optional[dict]:
    """Write/refresh ledger-editor-<job_id>. Best-effort; never raises."""
    try:
        job, uploads, payout = await _job_and_uploads(job_id)
        if not job:
            return None
        entry = editor_cost_entry(job, uploads, payout)
        await _upsert_event(f"ledger-editor-{job_id}", "job_ledger", job_id, "editor_cost", entry)
        logger.info("Job %s ledger: editor_cost=%s cents (%s AE images) from Bricks cut",
                    job_id, entry["editor_cost_cents"], entry["autoenhance_images"])
        if entry["autoenhance_images"] and not entry["test_job"]:
            await check_low_credit()
        return entry
    except Exception as e:
        logger.error("Job %s: editor cost ledger failed: %s", job_id, e)
        return None


async def _notify_kam(title: str, body: str, subject: str, html: str, data: dict) -> None:
    from .notifications import create_notification
    await create_notification(
        title=title, body=body, ntype="ops_alert", data=data,
        email_to=KAM_EMAIL, email_subject=subject, email_html=html,
    )


async def maybe_prompt_autoenhance_plan(job: dict) -> bool:
    """First live paid job → one-time 'start the Autoenhance plan' prompt to Kam."""
    try:
        if plan_active() or not is_live_paid_booking(job):
            return False
        if await _get_event(PROMPT_ID):
            return False
        details = {
            "job_id": job.get("id"),
            "recommended_plan": "Essential — $29 USD/month, 100 images, $0.29/extra image",
            "pay_per_image": "$0.75 USD/image, invoiced monthly",
            "subscribe_url": SUBSCRIBE_URL,
            "status": "prompted",
        }
        # Claim first (fixed id) so a retry or a second booking can't double-send.
        await _upsert_event(PROMPT_ID, "autoenhance_plan", "autoenhance", "prompted", details)
        body = (
            "Your first paying client just booked. Start the Autoenhance Essential plan "
            "($29 USD/month for 100 images) so stills get Autoenhance edits instead of the "
            f"in-house fallback: {SUBSCRIBE_URL}\n"
            "Each image's cost comes out of Bricks' cut on the job ledger. When the plan is on, "
            "set AUTOENHANCE_PLAN_ACTIVE=true on dispatch-api."
        )
        html = (
            "<p>Your first paying client just booked.</p>"
            "<p>Start the Autoenhance <b>Essential</b> plan ($29 USD/month for 100 images, "
            "$0.29 per extra image) so stills get Autoenhance edits instead of the in-house fallback.</p>"
            f'<p><a href="{SUBSCRIBE_URL}" style="background:#C2410C;color:#fff;padding:10px 16px;'
            'border-radius:6px;text-decoration:none">Start the plan</a></p>'
            f'<p>Other sizes: <a href="{PRICING_URL}">pricing</a>. Pay per image is $0.75 USD.</p>'
            "<p>Each image's cost comes out of Bricks' cut on the job ledger, never vendor pay.</p>"
        )
        await _notify_kam("Start Autoenhance plan", body, "First client paid — start the Autoenhance plan", html,
                          {"job_id": job.get("id"), "url": SUBSCRIBE_URL})
        return True
    except Exception as e:
        logger.error("Autoenhance plan prompt failed: %s", e)
        return False


async def check_low_credit() -> Optional[int]:
    """Credits left this month (estimate from the ledger); alert Kam once/month when low."""
    try:
        credits = _env_int("AUTOENHANCE_MONTHLY_CREDITS", 100)
        low_at = _env_int("AUTOENHANCE_LOW_CREDIT_AT", 20)
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        rows = _pg_exec(
            """SELECT COALESCE(SUM((details->>'autoenhance_images')::int),0) AS used
               FROM public.audit_events
               WHERE entity_type='job_ledger' AND action='editor_cost'
                 AND COALESCE((details->>'test_job')::boolean,false)=false
                 AND created_at >= %s""",
            (f"{month}-01",), fetch=True,
        )
        used = int((rows or [{}])[0].get("used") or 0)
        left = credits - used
        if left <= low_at and not await _get_event(f"ae-low-credit-{month}"):
            await _upsert_event(f"ae-low-credit-{month}", "autoenhance_plan", "autoenhance", "low_credit",
                                {"used": used, "left": left, "credits": credits})
            await _notify_kam(
                "Autoenhance credits low",
                f"About {max(left, 0)} of {credits} Autoenhance images left this month. "
                f"Extra images bill as overage; top up or move up a plan: {SUBSCRIBE_URL}",
                "Autoenhance credits low",
                f"<p>About <b>{max(left, 0)}</b> of {credits} Autoenhance images left this month.</p>"
                f'<p><a href="{SUBSCRIBE_URL}">Open Autoenhance billing</a></p>',
                {"used": used, "left": left},
            )
        return left
    except Exception as e:
        logger.error("Autoenhance low-credit check failed: %s", e)
        return None
