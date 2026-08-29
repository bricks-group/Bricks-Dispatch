"""Photographer job visibility for GO Today / job detail.

Paid /book writes jobs.vendor_id = default_vendor and assignee_id = Kam
(0c3fa7c2-…). Older GO queries required jobs.vendor_id == vendor_members.vendor_id,
so Kam's Owner session (vendor_id historically his UUID) saw an empty Today.
Filter assigned jobs by photographer aliases, not a single vendor_id.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from .vendor_identity import is_owner_member

KAM_MEMBER_ID = "0c3fa7c2-d23b-4e44-90ad-ab2d19af2580"
KAM_EMAILS = {"akashorter@icloud.com", "akashorter@gmail.com"}
ORG_VENDOR_ID = "default_vendor"
_TORONTO = ZoneInfo("America/Toronto")


def _add(ids: list[str], value: Any) -> None:
    s = str(value or "").strip()
    if s and s not in ids:
        ids.append(s)


def pick_canonical_member(rows: list[dict], actor_id: str = "", email: str = "") -> Optional[dict]:
    """Prefer Kam's Owner row (0c3fa7c2) over a duplicate login-created IC row."""
    if not rows:
        return None
    email = (email or "").strip().lower()
    for row in rows:
        if str(row.get("id") or "").lower().startswith("0c3fa7c2"):
            return row
    kam_email = email in KAM_EMAILS or any(
        str(r.get("email") or "").strip().lower() in KAM_EMAILS for r in rows
    )
    if kam_email:
        for row in rows:
            if is_owner_member(row):
                return row
    for row in rows:
        if str(row.get("id") or "") == str(actor_id or ""):
            return row
    for row in rows:
        if is_owner_member(row):
            return row
    return rows[0]


def photographer_assignee_ids(actor_id: str, member: Optional[dict] = None) -> list[str]:
    ids: list[str] = []
    _add(ids, actor_id)
    _add(ids, (member or {}).get("id"))
    email = str((member or {}).get("email") or "").strip().lower()
    if is_owner_member(member) or email in KAM_EMAILS or str(actor_id or "").lower().startswith("0c3fa7c2"):
        _add(ids, KAM_MEMBER_ID)
    return ids


def vendor_scope_ids(actor_id: str, member: Optional[dict] = None) -> list[str]:
    ids: list[str] = []
    _add(ids, (member or {}).get("vendor_id") or ORG_VENDOR_ID)
    _add(ids, ORG_VENDOR_ID)
    _add(ids, actor_id)
    _add(ids, (member or {}).get("id"))
    return ids


def jobs_or_filter(vendor_ids: list[str], assignee_ids: list[str]) -> str:
    parts = [f"vendor_id.eq.{v}" for v in vendor_ids] + [f"assignee_id.eq.{a}" for a in assignee_ids]
    return ",".join(parts) if parts else f"vendor_id.eq.{ORG_VENDOR_ID}"


def job_visible_to(
    job: Optional[dict],
    actor_id: str,
    member: Optional[dict],
    *,
    assigned_only: bool = True,
    admin_all: bool = False,
) -> bool:
    if not job or not job.get("id"):
        return False
    assignees = photographer_assignee_ids(actor_id, member)
    vendors = vendor_scope_ids(actor_id, member)
    aid = str(job.get("assignee_id") or "").strip()
    vid = str(job.get("vendor_id") or "").strip()
    if aid in assignees:
        return True
    if assigned_only and not admin_all:
        return False
    return vid in vendors


def parse_scheduled_date(raw: Any) -> Optional[date]:
    if not raw:
        return None
    s = str(raw).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(_TORONTO).date()
    except Exception:
        return None


def toronto_today() -> date:
    return datetime.now(_TORONTO).date()
