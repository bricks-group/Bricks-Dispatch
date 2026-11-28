"""Photographer job visibility for GO Today / job detail.

Paid /book writes jobs.vendor_id = default_vendor and assignee_id = Kam
(0c3fa7c2-…). Older GO queries required jobs.vendor_id == vendor_members.vendor_id,
so Kam's Owner session (vendor_id historically his UUID) saw an empty Today.
Filter assigned jobs by photographer aliases, not a single vendor_id.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from .vendor_identity import is_assignable, is_owner_member

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
        if str(row.get("id") or "").lower() == KAM_MEMBER_ID:
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
    if is_owner_member(member) or email in KAM_EMAILS or str(actor_id or "").lower() == KAM_MEMBER_ID:
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

UNASSIGN_TOKENS = {"", "unassign", "none", "null"}
UNASSIGNED_JOB_STATUSES = {"pending_assignment", "unassigned", "dispatch_failed", ""}
# The shoot is done: an assignment change must not send the job back into dispatch.
FINISHED_JOB_STATUSES = frozenset({"completed", "processing", "delivered"})
EMPTY_ASSIGNEE_VALUES = {"", "default_user", "null", "none"}


def is_unassigned_assignee(assignee_id: Any) -> bool:
    return str(assignee_id or "").strip().lower() in EMPTY_ASSIGNEE_VALUES


def actor_can_assign(member: Optional[dict]) -> bool:
    """Owner / admin / manager may assign. Same gate as is_vendor_admin."""
    if is_owner_member(member):
        return True
    blob = f"{(member or {}).get('role') or ''} {(member or {}).get('title') or ''}".lower()
    return any(k in blob for k in ("owner", "admin", "manager"))


def is_manual_assignable(member: Optional[dict]) -> bool:
    """Active photographers only. Owner is always included; pending Aman is not."""
    if not member:
        return False
    if not member.get("is_active"):
        return False
    if is_owner_member(member):
        return True
    return is_assignable(member)


def member_in_vendor_scope(member: Optional[dict], actor_id: str, actor_member: Optional[dict]) -> bool:
    if not member:
        return False
    vendors = vendor_scope_ids(actor_id, actor_member)
    vid = str(member.get("vendor_id") or "").strip()
    if vid in vendors:
        return True
    mid = str(member.get("id") or "").strip()
    return mid == KAM_MEMBER_ID or mid in vendors


def unassigned_assignee_or_filter() -> str:
    """paid /book writes assignee_id='' (not always SQL NULL)."""
    return "assignee_id.is.null,assignee_id.eq.,assignee_id.eq.default_user"


def assignment_fields(job: dict, new_assignee_id: Optional[str], now: Optional[str] = None) -> dict:
    ts = now or datetime.now(timezone.utc).isoformat()
    if not new_assignee_id:
        return {"assignee_id": None, "status": "pending_assignment", "updated_at": ts}
    status = str(job.get("status") or "").strip()
    if status in UNASSIGNED_JOB_STATUSES or is_unassigned_assignee(job.get("assignee_id")):
        status = "assigned"
    return {"assignee_id": new_assignee_id, "status": status, "updated_at": ts}


def resolve_manual_assignment(
    *,
    actor_id: str,
    actor_member: Optional[dict],
    job: Optional[dict],
    member_id: Optional[str],
    target_member: Optional[dict],
) -> tuple[int, str, dict]:
    """Decide assign / reassign / unassign. Returns (http_status, code, patch)."""
    if not actor_can_assign(actor_member):
        return 403, "insufficient_permissions", {}
    if not job or not job.get("id"):
        return 404, "job_not_found", {}
    if not job_visible_to(job, actor_id, actor_member, assigned_only=False, admin_all=True):
        return 404, "job_not_found", {}
    finished = str(job.get("status") or "").strip() in FINISHED_JOB_STATUSES
    mid = str(member_id or "").strip()
    if mid.lower() == "auto":
        return (409, "job_finished", {}) if finished else (200, "auto", {})
    if mid.lower() in UNASSIGN_TOKENS:
        return (409, "job_finished", {}) if finished else (200, "ok", assignment_fields(job, None))
    if not target_member or str(target_member.get("id") or "") != mid:
        return 404, "member_not_found", {}
    if not is_manual_assignable(target_member):
        return 404, "member_not_found", {}
    if not member_in_vendor_scope(target_member, actor_id, actor_member):
        return 404, "member_not_found", {}
    # Commercial jobs: manual assign still enforces Tier A + insurance + RPAS +
    # skills (location/availability stay an admin override as before).
    from ..services.commercial_jobs import commercial_gate_failures
    from ..services.vendor_profile import has_advanced_rpas, vendor_skills
    if commercial_gate_failures(
        target_member, job,
        has_skills=vendor_skills(target_member),
        advanced_rpas=has_advanced_rpas(target_member),
    ):
        return 409, "commercial_requirements_unmet", {}
    patch = assignment_fields(job, mid)
    if finished and patch.get("status") != job.get("status"):
        return 409, "job_finished", {}
    return 200, "ok", patch

