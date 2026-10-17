"""Commercial (builder / developer / GC / investor) job rules.

Kam, 2026-10-17: construction, mapping and progress jobs only go to the
highest vendor tier, and only to vendors holding valid insurance. Reaching
the highest tier requires insurance on file. Commercial work pays a better,
sourced payout than listing work.

Job kinds (job_type, aliases normalised by ``commercial_kind``):
  site_progress_visual  fixed-angle aerial stills + flyover video
                        (``progress_capture`` from Bricks OS maps here)
  site_mapped_capture   orthomosaic / 3D capture (mapping skill required)
  site_monitoring       one visit of a recurring monitoring plan; mapped when
                        add_ons contains "mapped", otherwise visual

Hard gates for every commercial kind (``commercial_gate_failures``):
  * vendor tier == TOP_TIER (Tier A)
  * insurance verified, unexpired on the job date, and the verified liability
    limit >= the job minimum (DEFAULT_MIN_INSURANCE_CAD = $2M unless the job
    sets ``min_insurance_cad``, e.g. 5_000_000 for a CCDC 41 contract)
  * Advanced RPAS unless the job explicitly sets requires_rpas_advanced=False
  * skills: drone (+ mapping for mapped work)

Payout: fixed per visit, tier-independent (only Tier A can hold the job),
never indexed to a client-side plan discount. Kam's standing rule (payout scan
2026-09-27): Bricks always keeps the larger cut, vendor <= 50% of rack, bands
C 35-40% / B 40-45% / A 45-50%. Commercial pays the top of Tier A, ~49% of the
one-off rack (Kam, 2026-10-17 correction of the earlier 63% draft):
  $185 of $375 visual · $735 of $1,499 mapped · +$315 of $650 first setup.
Client rack = median of published Canadian per-visit prices, see
/workspace/ship/commercial/commercial-pricing-payout-20261017.md.

In-house staff (worker_type="employee", Kam: "the goal is to have in house
employees handling these jobs later"): eligible for commercial jobs when the
company insurance policy (recorded once at org level, table company_insurance)
covers employees, instead of a personal COI. Tier bands do not apply to staff.
Advanced RPAS and skills still do. Employees get no per-job payout (salary is
handled outside Dispatch). No HR/payroll here.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional
import time

TOP_TIER = 3
DEFAULT_MIN_INSURANCE_CAD = 2_000_000
CCDC41_UAV_MIN_INSURANCE_CAD = 5_000_000

VISUAL = "site_progress_visual"
MAPPED = "site_mapped_capture"
MONITORING = "site_monitoring"
COMMERCIAL_KINDS = (VISUAL, MAPPED, MONITORING)

_KIND_ALIASES = {
    VISUAL: VISUAL,
    "progress_capture": VISUAL,
    "site_visual": VISUAL,
    "visual_progress": VISUAL,
    "progress_visual": VISUAL,
    MAPPED: MAPPED,
    "mapped_capture": MAPPED,
    "site_mapping": MAPPED,
    "orthomosaic": MAPPED,
    "mapping": MAPPED,
    MONITORING: MONITORING,
    "recurring_monitoring": MONITORING,
    "site_monitoring_visit": MONITORING,
    "monitoring_visit": MONITORING,
}

# Client rack (CAD, pre-tax) for one-off visits; used for margin display only.
COMMERCIAL_RACK_CAD = {VISUAL: 375.0, MAPPED: 1499.0, "mapped_setup": 650.0}
# Top of the Tier A band (45-50%); Bricks keeps the larger share.
COMMERCIAL_VENDOR_SHARE = 0.49
VENDOR_MAX_SHARE = 0.50
# Fixed vendor payout cents (~49% of one-off rack, rounded to the nearest $5).
COMMERCIAL_PAYOUT_CENTS = {VISUAL: 18500, MAPPED: 73500}
MAPPED_SETUP_PAYOUT_CENTS = 31500  # first capture on a site (control + mission build)

EMPLOYEE = "employee"
CONTRACTOR = "contractor"
COMPANY_POLICY_ID = "bricks"
_COMPANY_POLICY: dict = {"row": None, "at": 0.0}
_COMPANY_POLICY_TTL_S = 300.0


def _as_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return dict(getattr(obj, "__dict__", {}) or {})


def _add_on_keys(raw: Any) -> set[str]:
    import json

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = [raw]
    out: set[str] = set()
    for item in raw or []:
        if isinstance(item, dict):
            item = item.get("id") or item.get("key") or item.get("name") or ""
        key = str(item).strip().lower()
        if key:
            out.add(key)
    return out


def commercial_kind(job: Any) -> Optional[str]:
    jt = str(_as_dict(job).get("job_type") or "").strip().lower()
    return _KIND_ALIASES.get(jt)


def is_commercial_job(job: Any) -> bool:
    return commercial_kind(job) is not None


def deliverable_kind(job: Any) -> Optional[str]:
    """VISUAL or MAPPED for what this visit produces (monitoring resolves by add-on)."""
    kind = commercial_kind(job)
    if kind is None:
        return None
    if kind == MONITORING:
        keys = _add_on_keys(_as_dict(job).get("add_ons"))
        return MAPPED if keys & {"mapped", "mapping", "orthomosaic", "site_mapped_capture"} else VISUAL
    return kind


def required_skills(job: Any) -> set[str]:
    kind = deliverable_kind(job)
    if kind is None:
        return set()
    return {"drone", "mapping"} if kind == MAPPED else {"drone"}


def job_min_insurance_cad(job: Any) -> int:
    raw = _as_dict(job).get("min_insurance_cad")
    try:
        val = int(float(raw)) if raw not in (None, "") else 0
    except (TypeError, ValueError):
        val = 0
    return max(DEFAULT_MIN_INSURANCE_CAD, val)


def job_requires_advanced_rpas(job: Any) -> bool:
    return _as_dict(job).get("requires_rpas_advanced") is not False


def _parse_date(raw: Any) -> Optional[date]:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    s = str(raw).strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _job_date(job: Any) -> date:
    j = _as_dict(job)
    for key in ("scheduled_date", "selected_slot_start", "time_window_start"):
        d = _parse_date(j.get(key))
        if d:
            return d
    return date.today()


def worker_type(member: Any) -> str:
    raw = str(_as_dict(member).get("worker_type") or "").strip().lower()
    return EMPLOYEE if raw in (EMPLOYEE, "staff", "in_house", "in-house") else CONTRACTOR


def is_employee(member: Any) -> bool:
    return worker_type(member) == EMPLOYEE


def set_company_policy(row: Optional[dict]) -> None:
    """Prime the org-level policy cache (tests, or after an admin update)."""
    _COMPANY_POLICY["row"] = dict(row) if row else None
    _COMPANY_POLICY["at"] = time.monotonic()


def company_policy() -> Optional[dict]:
    return _COMPANY_POLICY["row"]


async def refresh_company_policy(db=None, *, force: bool = False) -> Optional[dict]:
    """Load the company insurance row (cached 5 min). Missing table/row = None,
    which keeps employees out of commercial work (safe default)."""
    if not force and _COMPANY_POLICY["at"] and time.monotonic() - _COMPANY_POLICY["at"] < _COMPANY_POLICY_TTL_S:
        return _COMPANY_POLICY["row"]
    try:
        if db is None:
            from ..shared.database import get_db
            db = await get_db()
        res = await db.table("company_insurance").select("*").eq("id", COMPANY_POLICY_ID).limit(1).execute()
        data = res.data if isinstance(getattr(res, "data", None), list) else []
        row = data[0] if data and isinstance(data[0], dict) else None
    except Exception:
        row = _COMPANY_POLICY["row"]
    set_company_policy(row)
    return row


def _company_policy_status(on: date, minimum_cad: int) -> dict:
    pol = company_policy()
    if not pol:
        return {"source": "company", "verified": False, "expires_on": None, "liability_cad": None,
                "aviation_covered": False, "minimum_cad": minimum_cad, "valid": False,
                "reason": "company_insurance_missing"}
    st = _personal_status({
        "coi_verified": bool(pol.get("verified_at")) or bool(pol.get("verified")),
        "coi_aviation_covered": pol.get("aviation_covered"),
        "coi_expires_on": pol.get("expires_on"),
        "coi_liability_cad": pol.get("liability_cad"),
    }, on=on, minimum_cad=minimum_cad)
    st["source"] = "company"
    if st["valid"] and not pol.get("covers_employees"):
        st["valid"] = False
        st["reason"] = "company_policy_excludes_employees"
    elif st["reason"]:
        st["reason"] = "company_" + st["reason"]
    return st


def insurance_status(member: Any, *, on: Optional[date] = None, minimum_cad: int = DEFAULT_MIN_INSURANCE_CAD) -> dict:
    """Employees: the company policy. Contractors: their own verified COI."""
    on = on or date.today()
    if is_employee(member):
        return _company_policy_status(on, minimum_cad)
    st = _personal_status(member, on=on, minimum_cad=minimum_cad)
    st["source"] = "personal"
    return st


def _personal_status(member: Any, *, on: Optional[date] = None, minimum_cad: int = DEFAULT_MIN_INSURANCE_CAD) -> dict:
    """Verified, unexpired (on ``on``), and liability limit >= minimum_cad."""
    m = _as_dict(member)
    on = on or date.today()
    expires = _parse_date(m.get("coi_expires_on"))
    try:
        amount = int(float(m.get("coi_liability_cad") or 0))
    except (TypeError, ValueError):
        amount = 0
    out = {
        "verified": bool(m.get("coi_verified")),
        "expires_on": expires.isoformat() if expires else None,
        "liability_cad": amount or None,
        "aviation_covered": bool(m.get("coi_aviation_covered")),
        "minimum_cad": minimum_cad,
        "valid": False,
        "reason": None,
    }
    if not out["verified"]:
        out["reason"] = "insurance_not_verified"
    elif not out["aviation_covered"]:
        out["reason"] = "insurance_no_drone_liability"
    elif expires is None:
        out["reason"] = "insurance_no_expiry"
    elif expires < on:
        out["reason"] = "insurance_expired"
    elif amount < minimum_cad:
        out["reason"] = "insurance_below_minimum"
    else:
        out["valid"] = True
    return out


def has_valid_insurance(member: Any, *, on: Optional[date] = None, minimum_cad: int = DEFAULT_MIN_INSURANCE_CAD) -> bool:
    return insurance_status(member, on=on, minimum_cad=minimum_cad)["valid"]


def can_hold_top_tier(member: Any, *, on: Optional[date] = None) -> bool:
    """Tier A requires verified, unexpired drone/aviation liability >= $2M."""
    return has_valid_insurance(member, on=on, minimum_cad=DEFAULT_MIN_INSURANCE_CAD)


def top_tier_insurance_flag(member: Any, *, on: Optional[date] = None) -> Optional[str]:
    """Reason string when a member sits at Tier A without valid insurance.

    Flag only. Never demotes and never notifies (Kam, 2026-10-17). Tier bands
    are a contractor concept, so employees are never flagged here."""
    m = _as_dict(member)
    if is_employee(m):
        return None
    try:
        tier = int(m.get("tier") or 1)
    except (TypeError, ValueError):
        tier = 1
    if tier < TOP_TIER:
        return None
    st = insurance_status(m, on=on)
    return None if st["valid"] else st["reason"]


def commercial_gate_failures(member: Any, job: Any, *, has_skills: Optional[set] = None, advanced_rpas: Optional[bool] = None) -> list[str]:
    """Empty list = member may hold this commercial job."""
    if not is_commercial_job(job):
        return []
    m = _as_dict(member)
    fails: list[str] = []
    try:
        tier = int(m.get("tier") or 1)
    except (TypeError, ValueError):
        tier = 1
    if tier < TOP_TIER and not is_employee(m):
        fails.append("tier_below_top")
    st = insurance_status(m, on=_job_date(job), minimum_cad=job_min_insurance_cad(job))
    if not st["valid"]:
        fails.append(st["reason"] or "insurance_invalid")
    if job_requires_advanced_rpas(job) and advanced_rpas is False:
        fails.append("rpas_advanced_required")
    if has_skills is not None:
        missing = required_skills(job) - set(has_skills)
        for s in sorted(missing):
            fails.append(f"skill_missing_{s}")
    return fails


def commercial_payout_cents(job: Any) -> Optional[int]:
    """Fixed vendor payout for a commercial visit, or None for non-commercial jobs."""
    kind = deliverable_kind(job)
    if kind is None:
        return None
    cents = COMMERCIAL_PAYOUT_CENTS[kind]
    if kind == MAPPED and "mapped_setup" in _add_on_keys(_as_dict(job).get("add_ons")):
        cents += MAPPED_SETUP_PAYOUT_CENTS
    return cents


def member_commercial_payout_cents(member: Any, job: Any) -> Optional[int]:
    """Per-job payout for this member: None = not commercial, 0 = employee."""
    cents = commercial_payout_cents(job)
    if cents is None:
        return None
    return 0 if is_employee(member) else cents


def payout_table() -> list[dict]:
    rows = []
    for key, label in ((VISUAL, "Site progress — visual visit"), (MAPPED, "Mapped capture (orthomosaic / 3D)")):
        rows.append({
            "kind": key,
            "label": label,
            "client_rack_cad": COMMERCIAL_RACK_CAD[key],
            "vendor_payout_cad": COMMERCIAL_PAYOUT_CENTS[key] / 100,
            "bricks_keeps_cad": COMMERCIAL_RACK_CAD[key] - COMMERCIAL_PAYOUT_CENTS[key] / 100,
        })
    rows.append({
        "kind": "mapped_setup",
        "label": "Mapped first-capture setup (add-on)",
        "client_rack_cad": COMMERCIAL_RACK_CAD["mapped_setup"],
        "vendor_payout_cad": MAPPED_SETUP_PAYOUT_CENTS / 100,
        "bricks_keeps_cad": COMMERCIAL_RACK_CAD["mapped_setup"] - MAPPED_SETUP_PAYOUT_CENTS / 100,
    })
    return rows
