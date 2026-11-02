"""14-day notice email for IC amendment A1 ("IC-A1 v1.0") to current vendors.

Kam approved one send on 2026-11-02 (10:26 ET). It runs server-side inside the
/api/ops sweep, and only when a request marker exists in ops_autopilot_events
(that marker is the flag):
  ic_a1_notice:request_test  -> one copy to TEST_TO, once
  ic_a1_notice:request_send  -> every current vendor, once each
Each vendor send claims ic_a1_notice:<member_id> and ic_a1_notice_email:<email>
first, so a re-run (or a second sweep) can never send twice. A failed send
releases its claims so the next sweep retries it. The Resend id is kept on the
member's marker.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

FROM_HELP = "Bricks <help@brxs.ca>"
TEST_TO = "akashorter@gmail.com"
SUBJECT = "An update to your Bricks contractor agreement"
GUIDE_LINE = ("You can hand back a job up to 48 hours before. After that, or if you don't check in within "
              "30 minutes of the window start, your Bricks account ends. Emergencies are excepted, and "
              "completed work is always paid.")
K_TEST_REQ, K_TEST_DONE = "ic_a1_notice:request_test", "ic_a1_notice:test_sent"
K_SEND_REQ = "ic_a1_notice:request_send"

_TEST_DOMAINS = ("brxs.ca", "brxs.test", "example.com", "example.org", "test.com", "mailinator.com")
_TEST_LOCALS = re.compile(r"^(akashorter|raph\.mercs)|test|smoke|e2e|seed|demo|fake", re.I)
_TEST_NAMES = re.compile(r"^\s*test\b|smoke|\be2e\b|\bseed\b|\bdemo\b", re.I)


def skip_reason(m: dict) -> Optional[str]:
    """None when this member is a real current vendor who gets the notice."""
    from ..shared.vendor_identity import is_owner_member
    from .commercial_jobs import is_employee
    email = (m.get("email") or "").strip().lower()
    local, _, domain = email.partition("@")
    if is_owner_member(m):
        return "owner"
    if str(m.get("id") or "").startswith("test-") or _TEST_NAMES.search(m.get("name") or "") \
            or domain in _TEST_DOMAINS or domain.endswith(".test") or _TEST_LOCALS.search(local):
        return "test or seed account"
    if not m.get("is_active"):
        return "not an active vendor (applicant or inactive)"
    if (m.get("role") or "").strip().lower() == "applicant":
        return "applicant, not yet a vendor"
    if m.get("terminated_at"):
        return "terminated"
    try:
        if is_employee(m):
            return "employee, not a contractor"
    except Exception:
        pass
    if not email or "@" not in email:
        return "no email"
    return None


def notice_body(m: dict) -> tuple[str, str]:
    from .ops_autopilot import api_base, fmt_et, _parse_ts
    from .vendor_onboarding import _go_url
    first = ((m.get("name") or "").strip().split(" ") or ["there"])[0] or "there"
    acc = _parse_ts(m.get("ic_amendment_accepted_at")) if m.get("ic_amendment_version") == "IC-A1 v1.0" else None
    when = (f"You accepted it in the Bricks app on {fmt_et(acc)}, so it already applies to jobs you accept from then on."
            if acc else
            "It binds you from 16 November 2026, or earlier if you accept it in the Bricks app. You'll see it the "
            "next time you open the app. It applies to jobs you accept on or after that date.")
    title = "An update to your contractor agreement"
    body = (
        f"Hi {first},\n\n"
        "We've added amendment A1 (IC-A1 v1.0) to your Bricks independent contractor agreement. It covers what "
        "happens when you accept a job and then can't do it.\n\n"
        "What changed\n"
        f"{GUIDE_LINE}\n\n"
        "• Check in with the Bricks app within 100 m of the property.\n"
        "• You can't send someone else in your place.\n"
        "• The exceptions are a medical emergency or bereavement, an accident or breakdown on the way, severe "
        "weather or unsafe drone conditions, no access or an unsafe site at the property, and a Bricks app or "
        "GPS outage. Choose the reason in the app when you hand a job back, or tell us as soon as you safely can.\n"
        "• If your account ends and you think it shouldn't have, you can appeal within 7 days.\n\n"
        "When it applies\n"
        f"{when}\n\n"
        f"Read amendment A1: {api_base()}/api/onboarding/ic-amendment.txt\n"
        f"Open the Bricks app: {_go_url()}\n\n"
        "Questions? Email help@brxs.ca.\n\n"
        "Bricks"
    )
    return title, body


async def _send(to: str, title: str, body: str) -> Optional[str]:
    """Send through Resend; returns the Resend email id, or None."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key or not to:
        return None
    try:
        import httpx
        from .notifications import _build_html_email
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post("https://api.resend.com/emails",
                                  headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                                  json={"from": FROM_HELP, "reply_to": "help@brxs.ca", "to": [to],
                                        "subject": SUBJECT, "html": _build_html_email(title, body), "text": body})
            r.raise_for_status()
            return (r.json() or {}).get("id") or "sent"
    except Exception as e:
        logger.error("ic_a1 notice send failed (%s): %s", to.partition("@")[2], type(e).__name__)
        return None


async def _has(db, key: str) -> bool:
    res = await db.table("ops_autopilot_events").select("key").eq("key", key).limit(1).execute()
    return bool(res.data)


async def _release(db, *keys: str) -> None:
    for k in keys:
        try:
            await db.table("ops_autopilot_events").delete().eq("key", k).execute()
        except Exception:
            pass


async def plan(db) -> dict:
    res = await db.table("vendor_members").select("*").execute()
    send, skip = [], []
    for m in res.data or []:
        why = skip_reason(m)
        dom = (m.get("email") or "").partition("@")[2] or "none"
        (skip if why else send).append({"member_id": m.get("id"), "domain": dom, **({"why": why} if why else {})})
    return {"send": send, "skip": skip}


async def run(db) -> dict:
    """Called by the ops sweep. Does nothing unless a request marker exists."""
    from .ops_autopilot import once
    out: dict[str, Any] = {}
    try:
        if await _has(db, K_TEST_REQ) and await once(db, K_TEST_DONE, {"to": TEST_TO}):
            title, body = notice_body({"name": "there"})
            rid = await _send(TEST_TO, title, body)
            if rid:
                await db.table("ops_autopilot_events").update({"data": {"to": TEST_TO, "resend_id": rid}}) \
                    .eq("key", K_TEST_DONE).execute()
            else:
                await _release(db, K_TEST_DONE)
            out["test"] = rid or "failed"
        if not await _has(db, K_SEND_REQ):
            return out
        res = await db.table("vendor_members").select("*").execute()
        sent, failed = [], 0
        for m in res.data or []:
            if skip_reason(m):
                continue
            email = m["email"].strip().lower()
            k_m, k_e = f"ic_a1_notice:{m['id']}", f"ic_a1_notice_email:{email}"
            if not await once(db, k_m, {"domain": email.partition('@')[2]}):
                continue
            if not await once(db, k_e, {"member_id": m["id"]}):
                continue  # another member row with the same email already got it
            title, body = notice_body(m)
            rid = await _send(email, title, body)
            if rid:
                await db.table("ops_autopilot_events").update(
                    {"data": {"domain": email.partition("@")[2], "resend_id": rid}}).eq("key", k_m).execute()
                sent.append({"member_id": m["id"], "resend_id": rid})
            else:
                failed += 1
                await _release(db, k_m, k_e)
            await asyncio.sleep(0.6)  # Resend rate limit
        out.update(sent=sent, failed=failed)
    except Exception as e:
        logger.error("ic_a1 notice run failed: %s", e)
        out["error"] = type(e).__name__
    return out
