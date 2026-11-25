"""Tell Bricks ops a photographer sent a certificate that needs checking.

www /photographers promises construction work only goes to pilots with
"verified drone liability insurance of at least $2M" on "a certificate Bricks
verifies". Uploads land as pending vendor_documents rows, but nobody was told
one had arrived, so a pilot could wait indefinitely for a review. This sends a
short internal email to raph@brxs.ca on each upload. It never raises and never
contacts the photographer.
"""
from __future__ import annotations

from ..shared.alert_recipients import SYSTEM_ALERT_EMAIL
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

CREDENTIAL_ADMIN_EMAIL = SYSTEM_ALERT_EMAIL
REVIEW_URL = "https://app.brxs.ca/hiring"
MIN_CONSTRUCTION_LIABILITY_CAD = 2_000_000
SEND_TIMEOUT_S = 8.0


def _money(v) -> str:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return "not given"
    if n >= 1_000_000 and n % 100_000 == 0:
        s = f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"${s}M"
    return f"${n:,}"


def _who(member: dict) -> str:
    name = str(member.get("name") or "").strip()
    email = str(member.get("email") or "").strip()
    if name and email:
        return f"{name} ({email})"
    return name or email or "A photographer"


def _short_name(member: dict) -> str:
    return str(member.get("name") or "").strip() or str(member.get("email") or "").strip() or "photographer"


def insurance_email(member: dict, declared: dict) -> tuple[str, str]:
    """Subject + plain body for a certificate-of-insurance upload."""
    limit = declared.get("liability_cad")
    aviation = bool(declared.get("aviation_covered"))
    lines = [
        f"{_who(member)} sent a certificate of insurance for review.",
        "",
        f"Liability limit: {_money(limit)} (as entered)",
        f"Expires: {declared.get('expires_on') or 'not given'}",
        f"Covers drone liability: {'yes' if aviation else 'no'} (as entered)",
    ]
    if declared.get("insurer"):
        lines.append(f"Insurer: {declared['insurer']}")
    if declared.get("policy_number"):
        lines.append(f"Policy: {declared['policy_number']}")
    notes = []
    try:
        if int(limit) < MIN_CONSTRUCTION_LIABILITY_CAD:
            notes.append("The limit entered is below the $2M needed for construction jobs.")
    except (TypeError, ValueError):
        pass
    if not aviation:
        notes.append("They didn't say it covers drone liability.")
    if notes:
        lines += [""] + notes
    lines += [
        "",
        "These values are what the photographer typed. Check them against the certificate before verifying.",
        f"Review it in Bricks: {REVIEW_URL}",
    ]
    return f"Insurance to review: {_short_name(member)}", "\n".join(lines)


def rpas_email(member: dict, level: str, certificate_number: str = "") -> tuple[str, str]:
    """Subject + plain body for a drone pilot certificate upload."""
    raw = (level or "").strip().lower()
    lvl = "Advanced" if raw == "advanced" else "Basic" if raw == "basic" else ""
    lines = [
        f"{_who(member)} sent a drone pilot certificate for review.",
        "",
        f"Level: {lvl} (as entered)" if lvl else "Level: not entered",
    ]
    if (certificate_number or "").strip():
        lines.append(f"Certificate number: {certificate_number.strip()[:60]}")
    if lvl == "Basic":
        lines += ["", "Basic certificates don't qualify for construction jobs."]
    lines += ["", f"Review it in Bricks: {REVIEW_URL}"]
    return f"Drone certificate to review: {_short_name(member)}", "\n".join(lines)


async def _send(subject: str, body: str) -> bool:
    from .notifications import _build_html_email, send_email_via_resend

    return bool(await asyncio.wait_for(
        send_email_via_resend(
            to=CREDENTIAL_ADMIN_EMAIL,
            subject=subject,
            html=_build_html_email(subject, body),
            text=body,
        ),
        timeout=SEND_TIMEOUT_S,
    ))


async def notify_insurance_uploaded(member: dict, declared: Optional[dict]) -> bool:
    try:
        subject, body = insurance_email(member or {}, declared or {})
        return await _send(subject, body)
    except Exception as e:  # never block the upload
        logger.error("Insurance review notice failed: %s", type(e).__name__)
        return False


async def notify_rpas_uploaded(member: dict, level: str, certificate_number: str = "") -> bool:
    try:
        subject, body = rpas_email(member or {}, level, certificate_number)
        return await _send(subject, body)
    except Exception as e:
        logger.error("RPAS review notice failed: %s", type(e).__name__)
        return False
