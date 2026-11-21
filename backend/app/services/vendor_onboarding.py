"""Vendor onboarding checklist (Kam, 2026-10-31).

Approval puts a vendor into onboarding. They become auto-assign eligible only
when every step is done; then vendor_members.onboarding_completed_at is stamped
(vendor_profile.member_eligible_for_job requires it) and a short "You're live"
email goes out. Steps:

  signed_in     first time the vendor opens the Bricks app (GO /auth/me)
  payouts       their own Stripe Express account is payout-ready (contractors.member_id)
                - not required for in-house staff or the owner (no per-job payout)
  availability  vendor confirmed their availability in the app
  guide         vendor read the vendor guide and said so
  agreement     current IC agreement version accepted
  credentials   every required trade document is person-verified (set at approve/verify)

There is no test shoot (dropped 2026-10-31). Owners can still pause anyone
(is_active=false); that keeps the stamp but blocks auto-assign.

OS reads the same columns (backend/common/vendor_onboarding_view.py).
"""
from __future__ import annotations

import html as _html
import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

STEP_KEYS = ("signed_in", "payouts", "availability", "guide", "agreement", "credentials")

# Vendor-facing copy. Vendors only ever see "Bricks" (never GO / Bricks OS).
STEP_COPY = {
    "signed_in": ("Sign in to the Bricks app", "You're in. Nothing else to do here."),
    "payouts": ("Connect payouts", "Set up Stripe so we can pay you after each job."),
    "availability": ("Confirm your availability", "Check the days and hours you can shoot, then confirm."),
    "guide": ("Read the vendor guide", "How jobs, delivery and pay work at Bricks. Cancelling an accepted job "
              "within 48 hours of the start, or checking in late, closes your account."),
    "agreement": ("Accept the contractor agreement", "Read and accept the current version and amendment A1."),
    "credentials": ("Verified documents on file",
                    "A person at Bricks has checked your ID, insurance and proof for your trade. "
                    "If something expires, upload a renewal so you can keep taking jobs."),
}

# Vendor mail comes from help@ so replies reach a person.
VENDOR_MAIL_FROM = "Bricks <help@brxs.ca>"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(m: Any) -> dict:
    if m is None:
        return {}
    if isinstance(m, dict):
        return m
    if hasattr(m, "model_dump"):
        return m.model_dump()
    return dict(m)


def payouts_required(member: dict) -> bool:
    from ..shared.vendor_identity import is_owner_member
    from .commercial_jobs import is_employee
    return not (is_owner_member(member) or is_employee(member))


def current_agreement_version() -> str:
    from ..routers.onboarding import _IC_AGREEMENT_VERSION
    return _IC_AGREEMENT_VERSION


def current_amendment_version() -> str:
    from ..routers.onboarding import _IC_AMENDMENT_VERSION
    return _IC_AMENDMENT_VERSION


def checklist(member: Any, contractor: Optional[dict] = None) -> dict:
    """Pure: {complete, completed_at, state, steps:[{key,title,detail,done,required}], done, total}."""
    m = _as_dict(member)
    need_payouts = payouts_required(m)
    connect = ((contractor or {}).get("connect_status") or "").lower()
    from . import trade_specs as _ts
    creds_done = _ts.credentials_stamp(m) or bool(m.get("onboarding_completed_at"))
    done = {
        "signed_in": bool(m.get("first_signed_in_at")),
        "payouts": (connect == "connected") or not need_payouts,
        "availability": bool(m.get("availability_confirmed_at")),
        "guide": bool(m.get("guide_acknowledged_at")),
        "agreement": bool(m.get("terms_accepted_at"))
        and str(m.get("ic_agreement_version") or "") == current_agreement_version()
        # New vendors accept amendment A1 too; vendors already set up get it in the GO gate.
        and (bool(m.get("onboarding_completed_at")) or str(m.get("ic_amendment_version") or "") == current_amendment_version()),
        # Person-verified required docs (approval hard gate). Grandfather vendors
        # who already completed onboarding before this checklist item existed.
        "credentials": creds_done,
    }
    steps = []
    for key in STEP_KEYS:
        title, detail = STEP_COPY[key]
        if key == "payouts" and not need_payouts:
            detail = "Not needed for your account."
        elif key == "payouts" and connect == "pending":
            detail = "Stripe is still checking your details. Finish any steps Stripe asks for."
        steps.append({
            "key": key, "title": title, "detail": detail,
            "done": done[key], "required": key != "payouts" or need_payouts,
        })
    status = (m.get("onboarding_status") or "pending").lower()
    completed_at = m.get("onboarding_completed_at")
    if status in ("rejected", "terminated"):
        state = "rejected"
    elif status != "approved":
        state = "applicant"
    elif not m.get("is_active"):
        state = "paused"
    elif completed_at:
        state = "live"
    else:
        state = "onboarding"
    return {
        "state": state,
        "complete": bool(completed_at),
        "completed_at": completed_at,
        "all_steps_done": all(done.values()),
        "steps": steps,
        "done": sum(1 for v in done.values() if v),
        "total": len(STEP_KEYS),
    }


async def _load_member(member_id: str) -> dict:
    from ..shared.database import get_db, maybe_single_row
    res = await (await get_db()).table("vendor_members").select("*").eq("id", member_id).maybe_single().execute()
    return maybe_single_row(res) or {}


def _load_contractor(member: dict) -> Optional[dict]:
    if not payouts_required(member):
        return None
    try:
        from .stripe_connect_service import load_contractor_row
        return load_contractor_row(member.get("vendor_id") or "default_vendor", str(member.get("id") or ""))
    except Exception as e:
        logger.warning("onboarding: contractor lookup failed for %s: %s", member.get("id"), type(e).__name__)
        return None


async def _update_member(member_id: str, fields: dict, *, only_if_null: Optional[str] = None) -> list:
    from ..shared.database import get_db
    q = (await get_db()).table("vendor_members").update(fields).eq("id", member_id)
    if only_if_null:
        q = q.is_(only_if_null, "null")
    res = await q.execute()
    return list(getattr(res, "data", None) or [])


async def stamp_step(member_id: str, column: str) -> None:
    """Set a checklist timestamp once (first time wins)."""
    assert column in ("first_signed_in_at", "guide_acknowledged_at", "availability_confirmed_at")
    await _update_member(member_id, {column: _now()}, only_if_null=column)


async def refresh_onboarding(member_id: str, *, send_email: bool = True, member: Optional[dict] = None) -> dict:
    """Recompute the checklist; when everything is done for an approved, active
    vendor, stamp onboarding_completed_at (once) and send "You're live"."""
    m = member or await _load_member(member_id)
    if not m:
        return checklist({})
    contractor = _load_contractor(m)
    state = checklist(m, contractor)
    if (
        state["all_steps_done"]
        and not m.get("onboarding_completed_at")
        and (m.get("onboarding_status") or "") == "approved"
        and m.get("is_active")
    ):
        now = _now()
        rows = await _update_member(member_id, {"onboarding_completed_at": now}, only_if_null="onboarding_completed_at")
        if rows:
            m = {**m, "onboarding_completed_at": now}
            state = checklist(m, contractor)
            logger.info("onboarding complete: %s is now auto-assign eligible", member_id)
            try:
                from ..shared.audit import write_audit_event
                await write_audit_event(
                    member_id, "vendor_member", member_id, "onboarding_completed",
                    vendor_id=m.get("vendor_id") or "default_vendor", details={"steps": list(STEP_KEYS)},
                )
            except Exception:
                pass
            if send_email:
                await send_live_email(m)
    return state


async def ensure_signed_in(member: dict) -> Optional[dict]:
    """Called from GO /auth/me. Stamps the sign-in step and refreshes the
    checklist for vendors still onboarding. Never raises."""
    try:
        mid = str(member.get("id") or "")
        if not mid or member.get("onboarding_completed_at"):
            return None
        if (member.get("onboarding_status") or "") != "approved" or not member.get("is_active"):
            return None
        if not member.get("first_signed_in_at"):
            await stamp_step(mid, "first_signed_in_at")
            member = {**member, "first_signed_in_at": _now()}
        return await refresh_onboarding(mid, member=member)
    except Exception as e:
        logger.warning("onboarding sign-in stamp failed: %s", type(e).__name__)
        return None


# ── Email ───────────────────────────────────────────────────────────────────

def _go_url() -> str:
    from .notifications import _GO_WEB_URL
    return _GO_WEB_URL


def vendor_email_html(heading: str, paragraphs: list[str], button_label: str, button_url: str,
                      steps: Optional[list[str]] = None) -> str:
    """Branded vendor email: sentence case, one orange (#D2542B) button."""
    from .notifications import _EMAIL_FONT_STACK, _EMAIL_LOGO_URL
    p = f'style="color:#555;line-height:1.6;margin:0 0 14px 0;font-family:{_EMAIL_FONT_STACK}"'
    body = "".join(f"<p {p}>{t}</p>" for t in paragraphs[:-1]) if steps else "".join(f"<p {p}>{t}</p>" for t in paragraphs)
    if steps:
        items = "".join(f'<li style="margin:4px 0">{_html.escape(s)}</li>' for s in steps)
        body += f'<ol style="color:#555;line-height:1.6;padding-left:20px;margin:0 0 14px 0;font-family:{_EMAIL_FONT_STACK}">{items}</ol>'
        body += f"<p {p}>{paragraphs[-1]}</p>"
    url = _html.escape(button_url, quote=True)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="color-scheme" content="light dark"></head>
<body style="font-family:{_EMAIL_FONT_STACK};background:#f4f1eb;padding:24px;margin:0">
<div style="max-width:480px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;padding:16px 24px 0 24px">
<tr>
<td width="16" style="width:16px"><img src="{_EMAIL_LOGO_URL}" width="16" height="16" alt="" style="display:block;width:16px;height:16px"></td>
<td width="7" style="width:7px;font-size:0;line-height:0">&nbsp;</td>
<td style="font-family:{_EMAIL_FONT_STACK};font-size:14px;font-weight:500;letter-spacing:-0.5px;color:#14110E">bricks</td>
</tr></table>
<div style="padding:20px 24px 24px 24px">
<h1 style="color:#14110E;margin:0 0 14px 0;font-size:20px;font-weight:600;font-family:{_EMAIL_FONT_STACK}">{_html.escape(heading)}</h1>
{body}
<a href="{url}" style="display:inline-block;margin-top:6px;padding:12px 24px;background:#D2542B;color:#ffffff;text-decoration:none;border-radius:6px;font-weight:500;font-family:{_EMAIL_FONT_STACK}">{_html.escape(button_label)}</a>
<p style="margin:20px 0 0 0;font-size:13px;color:#999;line-height:1.5;font-family:{_EMAIL_FONT_STACK}">Or go to <a href="{url}" style="color:#D2542B">{url}</a></p>
<p style="margin:20px 0 0 0;font-size:12px;color:#999;font-family:{_EMAIL_FONT_STACK}">Questions? Reply to this email or write help@brxs.ca.</p>
</div>
<div style="padding:12px 24px;border-top:1px solid #eee;font-size:12px;color:#888;font-family:{_EMAIL_FONT_STACK}">Bricks · brxs.ca</div>
</div></body></html>"""


def _first_name(member: dict) -> str:
    return _html.escape(((member.get("name") or "").strip().split(" ") or [""])[0] or "there")


ONBOARDING_EMAIL_STEPS = [
    "Sign in to the Bricks app",
    "Connect payouts so we can pay you",
    "Confirm your availability",
    "Read the vendor guide",
    "Accept the current contractor agreement",
]


def onboarding_welcome_email(member: dict) -> tuple[str, str, str]:
    """(subject, html, plain body) for approval: welcome + the checklist."""
    link = f"{_go_url()}/onboarding"
    subject = "You're approved — finish setting up in the Bricks app"
    html = vendor_email_html(
        "You're approved",
        [
            f"Hi {_first_name(member)},",
            "Welcome to Bricks. Before we send you paid jobs, finish a few quick steps in the Bricks app:",
            "When they're done you're live, and jobs that fit your schedule and area start coming to you automatically.",
            "Open Availability to connect your calendar. We’ll avoid times you’ve already booked.",
        ],
        "Finish setup",
        link,
        steps=ONBOARDING_EMAIL_STEPS,
    )
    body = "You're approved. Finish a few quick steps in the Bricks app to start getting jobs."
    return subject, html, body


def live_email(member: dict) -> tuple[str, str, str]:
    subject = "You're live on Bricks"
    html = vendor_email_html(
        "You're live",
        [
            f"Hi {_first_name(member)},",
            "Your setup is done. Jobs that fit your schedule and area will now come to you automatically, "
            "and we'll let you know each time one is assigned.",
            "Keep your availability up to date so you only get jobs you can take.",
            "Once a job is yours, show up for it yourself. You can hand a job back free until 48 hours "
            "before the start. Cancelling inside 48 hours, or not checking in within 30 minutes of the start, "
            "closes your Bricks account. You can't send someone else in your place.",
        ],
        "Open the Bricks app",
        _go_url(),
    )
    body = "Your setup is done. Jobs that fit your schedule and area will now come to you automatically."
    return subject, html, body


async def send_onboarding_welcome(member: dict) -> bool:
    email = (member.get("email") or "").strip()
    if not email or member.get("onboarding_completed_at"):
        return False
    subject, html, body = onboarding_welcome_email(member)
    from .notifications import create_notification
    try:
        await create_notification(
            title="Welcome to Bricks",
            body=body,
            ntype="onboarding_approved",
            data={"member_id": member.get("id")},
            member_id=member.get("id"),
            email_to=email,
            email_subject=subject,
            email_html=html,
            email_from=VENDOR_MAIL_FROM,
        )
        return True
    except Exception as e:
        logger.warning("onboarding welcome email failed for %s: %s", member.get("id"), type(e).__name__)
        return False


async def send_live_email(member: dict) -> bool:
    email = (member.get("email") or "").strip()
    if not email:
        return False
    subject, html, body = live_email(member)
    from .notifications import create_notification
    try:
        await create_notification(
            title="You're live on Bricks",
            body=body,
            ntype="onboarding_complete",
            data={"member_id": member.get("id")},
            member_id=member.get("id"),
            email_to=email,
            email_subject=subject,
            email_html=html,
            email_from=VENDOR_MAIL_FROM,
        )
        return True
    except Exception as e:
        logger.warning("you're live email failed for %s: %s", member.get("id"), type(e).__name__)
        return False


REAPPLY_COOLDOWN_DAYS = 90


def reapply_date(rejected_at: Optional[datetime] = None) -> datetime:
    from datetime import timedelta
    return (rejected_at or datetime.now(timezone.utc)) + timedelta(days=REAPPLY_COOLDOWN_DAYS)


def date_label(d: datetime) -> str:
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def reject_email(member: dict, rejected_at: Optional[datetime] = None) -> tuple[str, str]:
    """Same wording as OS server._vendor_status_email (reject)."""
    from .notifications import _EMAIL_FONT_STACK, _EMAIL_LOGO_URL
    when = date_label(reapply_date(rejected_at))
    p = f'style="color:#555;line-height:1.6;margin:0 0 14px 0;font-family:{_EMAIL_FONT_STACK}"'
    subject = "Update on your Bricks application"
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="color-scheme" content="light dark"></head>
<body style="font-family:{_EMAIL_FONT_STACK};background:#f4f1eb;padding:24px;margin:0">
<div style="max-width:480px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;padding:16px 24px 0 24px"><tr>
<td width="16" style="width:16px"><img src="{_EMAIL_LOGO_URL}" width="16" height="16" alt="" style="display:block;width:16px;height:16px"></td>
<td width="7" style="width:7px;font-size:0;line-height:0">&nbsp;</td>
<td style="font-family:{_EMAIL_FONT_STACK};font-size:14px;font-weight:500;letter-spacing:-0.5px;color:#14110E">bricks</td>
</tr></table>
<div style="padding:20px 24px 24px 24px">
<h1 style="color:#14110E;margin:0 0 14px 0;font-size:20px;font-weight:600;font-family:{_EMAIL_FONT_STACK}">Application update</h1>
<p {p}>Hi {_first_name(member)},</p>
<p {p}>Thank you for applying to Bricks. We're not able to approve your application right now.</p>
<p {p}>You're welcome to apply again from {when} at <a href="https://brxs.ca/photographers" style="color:#D2542B">brxs.ca/photographers</a>. Real estate samples (interiors and exteriors of homes) and a resume that shows your property work help most.</p>
<p {p}>If you have questions, reply to this email or write help@brxs.ca.</p>
<p style="margin:20px 0 0 0;font-size:12px;color:#999;font-family:{_EMAIL_FONT_STACK}">— The Bricks team</p>
</div></div></body></html>"""
    return subject, html


async def send_reject_email(member: dict, rejected_at: Optional[datetime] = None) -> bool:
    email = (member.get("email") or "").strip()
    if not email:
        return False
    subject, html = reject_email(member, rejected_at)
    from .notifications import send_email_via_resend
    try:
        return bool(await send_email_via_resend(email, subject, html, from_addr=VENDOR_MAIL_FROM))
    except Exception as e:
        logger.warning("reject email failed for %s: %s", member.get("id"), type(e).__name__)
        return False
