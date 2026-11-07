"""Ops autopilot endpoints (Kam, 2026-11-02).

GET|POST /api/ops/sweep   every 15 min from Supabase pg_cron (job brxs-ops-sweep)
GET|POST /api/ops/daily   once a day from the Vercel cron (health, backup, digest)
GET      /api/ops/act?t=  one-tap link from an email: shows a confirm page
POST     /api/ops/act     runs the signed action (single use)
GET|POST /api/ops/appeal  a terminated vendor's 7-day appeal form (signed link in the notice)

Cron auth: Vercel's CRON_SECRET bearer, an INTERNAL_API_KEYS key, or the
ops_cron_secret held in Supabase vault (pg_cron sends it; never leaves the DB).
"""
from __future__ import annotations

import hmac
import html as _html
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Form, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from ..services import ops_autopilot as ops
from ..shared.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ops")

_vault_cache: dict = {"v": None, "at": 0.0}


def _vault_secret() -> Optional[str]:
    if _vault_cache["v"] and time.time() - _vault_cache["at"] < 600:
        return _vault_cache["v"]
    try:
        v = ops.sweep_secret_from_vault()
    except Exception as e:
        logger.warning("vault secret read failed: %s", type(e).__name__)
        v = None
    _vault_cache.update(v=v, at=time.time())
    return v


def _authorized(authorization: Optional[str], x_internal_key: Optional[str]) -> bool:
    from .vendor_os import _cron_authorized
    if _cron_authorized(authorization, x_internal_key):
        return True
    got = (authorization or "").strip()
    if got.startswith("Bearer "):
        v = _vault_secret()
        return bool(v) and hmac.compare_digest(got[7:].strip(), v)
    return False


@router.api_route("/sweep", methods=["GET", "POST"])
async def ops_sweep(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_internal_key: Optional[str] = Header(default=None, alias="X-Internal-Key"),
):
    if not _authorized(authorization, x_internal_key):
        raise HTTPException(status_code=401, detail="unauthorized")
    db = await get_db()
    try:
        out = await ops.sweep(db)
        ok = not out.get("errors")
    except Exception as e:
        logger.exception("ops sweep crashed")
        out, ok = {"error": str(e)[:300]}, False
    await ops.record_run(db, "sweep", ok, out)
    return {"ok": ok, **{k: (len(v) if isinstance(v, list) else v) for k, v in out.items()}}


@router.api_route("/daily", methods=["GET", "POST"])
async def ops_daily(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_internal_key: Optional[str] = Header(default=None, alias="X-Internal-Key"),
    backup: bool = Query(default=True),
    notify: bool = Query(default=True),
):
    if not _authorized(authorization, x_internal_key):
        raise HTTPException(status_code=401, detail="unauthorized")
    db = await get_db()
    try:
        out = await ops.daily(db, do_backup=backup, notify=notify)
        ok = not out.get("problems")
    except Exception as e:
        logger.exception("ops daily crashed")
        out, ok = {"error": str(e)[:300]}, False
        try:
            await ops.send_kam(db, f"daily_crash:{datetime.now(timezone.utc).date()}", "Bricks daily check crashed",
                               [str(e)[:300]])
        except Exception:
            pass
    await ops.record_run(db, "daily", ok, out)
    return {"ok": ok, **out}


def _page(title: str, body_html: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        "<!DOCTYPE html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_html.escape(title)}</title></head><body style='font-family:-apple-system,Segoe UI,Helvetica,Arial,"
        "sans-serif;background:#f4f1eb;padding:24px'><div style='max-width:480px;margin:0 auto;background:#fff;"
        f"border-radius:12px;padding:24px'><h1 style='font-size:20px;margin:0 0 12px'>{_html.escape(title)}</h1>"
        f"{body_html}</div></body></html>", status_code=status)


def _describe(payload: dict) -> str:
    a, p = payload.get("a"), payload.get("p") or {}
    label = ops.ACTION_LABELS.get(a, a)
    ref = ", ".join(f"{k} {str(v)[:8]}" for k, v in p.items())
    return f"{label} ({ref})"


@router.get("/act", response_class=HTMLResponse)
async def ops_act_confirm(t: str = Query(...)):
    payload = ops.verify_action(t)
    if not payload:
        return _page("Link expired or invalid", "<p>Open the latest email from Bricks, or do this in the OS admin.</p>", 400)
    db = await get_db()
    if await ops.seen(db, f"act:{t[-24:]}"):
        return _page("Already done", "<p>This button was already used.</p>")
    return _page("Confirm", (
        f"<p style='font-size:15px'>{_html.escape(_describe(payload))}</p>"
        "<form method='post' action='/api/ops/act'>"
        f"<input type='hidden' name='t' value='{_html.escape(t)}'>"
        "<button style='padding:12px 18px;background:#d2542b;color:#fff;border:0;border-radius:8px;font-size:15px;"
        "font-weight:600'>Confirm</button></form>"))


@router.post("/act", response_class=HTMLResponse)
async def ops_act_run(t: str = Form(...)):
    payload = ops.verify_action(t)
    if not payload:
        return _page("Link expired or invalid", "<p>Nothing was changed.</p>", 400)
    db = await get_db()
    if not await ops.once(db, f"act:{t[-24:]}", {"a": payload.get("a"), "p": payload.get("p")}):
        return _page("Already done", "<p>This button was already used. Nothing was changed.</p>")
    try:
        res = await ops.run_action(db, payload)
    except Exception as e:
        logger.exception("ops action failed")
        return _page("That did not work", f"<p>{_html.escape(str(e)[:300])}</p>", 500)
    ok = bool(res.get("ok"))
    detail = ""
    if payload.get("a") == "reassign":
        detail = "Assigned to a new vendor." if res.get("to") else "No other vendor was free. The job is marked dispatch failed and you got the no-vendor email."
    return _page("Done" if ok else "Not done", f"<p>{_html.escape(_describe(payload))}</p><p>{_html.escape(detail or str(res.get('error') or ''))}</p>")


def _appeal_member(t: str) -> Optional[str]:
    payload = ops.verify_action(t)
    if not payload or payload.get("a") != "vendor_appeal":
        return None
    return (payload.get("p") or {}).get("member_id")


_CLOSED = "<p>The appeal window has closed. Questions: help@brxs.ca.</p>"


@router.get("/appeal", response_class=HTMLResponse)
async def vendor_appeal_form(t: str = Query(...)):
    from ..services.vendor_termination import APPEAL_DAYS, appeal_open
    mid = _appeal_member(t)
    db = await get_db()
    if not mid or not await appeal_open(db, mid):
        return _page("Appeal closed", _CLOSED, 400)
    return _page("Appeal your account closure", (
        f"<p style='font-size:15px'>Tell Bricks what happened. You can appeal once, within {APPEAL_DAYS} days "
        "of the closure. Bricks will reply by email.</p>"
        "<form method='post' action='/api/ops/appeal'>"
        f"<input type='hidden' name='t' value='{_html.escape(t)}'>"
        "<textarea name='message' required minlength='5' maxlength='2000' rows='6' style='width:100%;box-sizing:border-box;"
        "padding:10px;border:1px solid #ccc;border-radius:8px;font:inherit'></textarea>"
        "<button style='margin-top:12px;padding:12px 18px;background:#d2542b;color:#fff;border:0;border-radius:8px;"
        "font-size:15px;font-weight:600'>Send appeal</button></form>"))


@router.post("/appeal", response_class=HTMLResponse)
async def vendor_appeal_submit(t: str = Form(...), message: str = Form(...)):
    from ..services.vendor_termination import submit_appeal
    mid = _appeal_member(t)
    if not mid:
        return _page("Appeal closed", _CLOSED, 400)
    db = await get_db()
    res = await submit_appeal(db, mid, message)
    if res.get("ok"):
        return _page("Appeal sent", "<p>Thanks. Bricks will review it and email you.</p>")
    err = res.get("error")
    if err == "already_sent":
        return _page("Appeal already sent", "<p>Bricks has your appeal and will email you.</p>")
    if err == "empty":
        return _page("Add a few words", "<p>Go back and tell us what happened.</p>", 400)
    return _page("Appeal closed", _CLOSED, 400)
