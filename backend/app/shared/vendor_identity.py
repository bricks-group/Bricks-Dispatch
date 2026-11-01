"""Vendor identity: Owner vs photographer IC, assignability, payout display.

Kam/Ajani (vendor_members.id starting 0c3fa7c2) is Owner: highest tier,
full package, zero deductions. Everyone else is a photographer IC on the
fixed rate card. Pending members (e.g. Aman) are not assignable.
"""

from __future__ import annotations

from typing import Any, Optional

OWNER_ID_PREFIX = "0c3fa7c2"
OWNER_TITLE = "Owner"
IC_TITLE = "Photographer IC"
IC_TITLES = {
    "photographer": "Photographer IC",
    "stager": "Stager IC",
    "cleaner": "Cleaner IC",
    "inspector": "Inspector IC",
    "measurer": "Measurer IC",
    "handyman": "Repairs IC",
}
VENDOR_SKILLS = ("photo", "drone", "reel", "tour", "twilight")
TIER_LABELS = {1: "C", 2: "B", 3: "A"}


def is_owner_member(member: Optional[dict]) -> bool:
    if not member:
        return False
    mid = str(member.get("id") or "").lower()
    if mid.startswith(OWNER_ID_PREFIX):
        return True
    if (member.get("payout_mode") or "") == "owner_full":
        return True
    title = (member.get("title") or "").strip().lower()
    role = (member.get("role") or "").strip().lower()
    return title == "owner" or role == "owner"


def needs_terms(member: Optional[dict]) -> bool:
    """A contractor who reached GO without accepting the IC agreement and privacy policy.

    Applicants accept on the www application. Members an owner added from the
    team screen (and older rows) never did, so GO asks them once before the app
    opens. Owners are not contractors and are never asked.
    """
    if not member or is_owner_member(member):
        return False
    if not member.get("terms_accepted_at"):
        return True
    # Vendors still onboarding accept the current agreement version (one of the
    # checklist steps). Live vendors on an older version are not re-gated here.
    if not member.get("onboarding_completed_at"):
        from ..routers.onboarding import _IC_AGREEMENT_VERSION
        return str(member.get("ic_agreement_version") or "") != _IC_AGREEMENT_VERSION
    return False


def member_title(member: Optional[dict]) -> str:
    if is_owner_member(member):
        return OWNER_TITLE
    stored = (member or {}).get("title") or (member or {}).get("role") or ""
    if stored.strip().lower() in ("owner", "admin", "manager"):
        return stored
    try:
        from ..services.vendor_profile import member_vendor_type
        vtype = member_vendor_type(member)
    except Exception:
        vtype = "photographer"
    return IC_TITLES.get(vtype, IC_TITLE)


def _vendor_type_of(member: dict) -> str:
    try:
        from ..services.vendor_profile import member_vendor_type
        return member_vendor_type(member)
    except Exception:
        return (member.get("vendor_type") or "photographer").strip().lower() or "photographer"


def is_vendor_paused(member: Optional[dict]) -> bool:
    """Paused vendor (expired credential, probation fail, admin) or paused trade.

    Owner is never paused. Trade pause comes from trade_specs.json `enabled`.
    """
    if not member or is_owner_member(member):
        return False
    if member.get("paused_at"):
        return True
    from ..services.trade_specs import trade_enabled
    return not trade_enabled(member_trade(member))


def member_trade(member: Optional[dict]) -> str:
    try:
        from ..services.vendor_profile import member_vendor_type
        return member_vendor_type(member or {})
    except Exception:
        return ((member or {}).get("vendor_type") or "photographer").strip().lower() or "photographer"


def is_assignable(member: Optional[dict]) -> bool:
    """Hard gate used by auto-assign and manual assign. Pending (Aman) is never
    assignable; neither is a paused vendor or a vendor in a paused trade."""
    if not member:
        return False
    if not member.get("is_active"):
        return False
    if is_vendor_paused(member):
        return False
    return (member.get("onboarding_status") or "") == "approved"


def member_skills(member: Optional[dict]) -> list[str]:
    raw = (member or {}).get("skills")
    if isinstance(raw, list) and raw:
        return [s for s in raw if s in VENDOR_SKILLS]
    if is_owner_member(member):
        return list(VENDOR_SKILLS)
    specialty = ((member or {}).get("specialty") or "").strip()
    if specialty in VENDOR_SKILLS:
        return [specialty]
    equipment = (member or {}).get("equipment_list") or []
    found = [s for s in VENDOR_SKILLS if s in equipment]
    return found or ["photo"]


def owner_seed_fields(member: dict) -> dict:
    """Fields to persist on Kam so hiring/assigner reads Owner + highest tier."""
    if not is_owner_member(member):
        return {}
    patch: dict[str, Any] = {}
    if member.get("title") != OWNER_TITLE:
        patch["title"] = OWNER_TITLE
    if (member.get("role") or "") != OWNER_TITLE:
        patch["role"] = OWNER_TITLE
    if member.get("tier") != 3:
        patch["tier"] = 3
    if member.get("payout_mode") != "owner_full":
        patch["payout_mode"] = "owner_full"
    if member.get("onboarding_status") != "approved":
        patch["onboarding_status"] = "approved"
    if not member.get("is_active"):
        patch["is_active"] = True
    skills = member_skills(member)
    if set(skills) != set(VENDOR_SKILLS):
        patch["skills"] = list(VENDOR_SKILLS)
    if not member.get("drone_certified"):
        patch["drone_certified"] = True
    if (member.get("rpas_certificate_level") or "").lower() != "advanced":
        patch["rpas_certificate_level"] = "advanced"
    if not member.get("service_areas"):
        patch["service_areas"] = ["Mississauga", "GTA", "Toronto", "Peel", "Halton", "York", "Durham"]
    if not member.get("home_city"):
        patch["home_city"] = "Mississauga"
        patch["home_lat"] = 43.5890
        patch["home_lng"] = -79.6441
        patch["max_travel_km"] = 80
    if not member.get("polycam_equipped"):
        patch["polycam_equipped"] = True
    if (member.get("vendor_type") or "") != "photographer":
        patch["vendor_type"] = "photographer"
    return patch


def payout_model(member: Optional[dict]) -> str:
    return "owner_full" if is_owner_member(member) else "ic_rate_card"


def rack_dollars(job: dict) -> float:
    raw = job.get("rack_price")
    if raw is None:
        raw = job.get("package_price") or 0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


async def photographer_payout_cents(db, job: dict, member: Optional[dict]) -> int:
    """Owner: full package, $0 deduction. IC: fixed rate card, never % of rack."""
    from ..services.payout_service import (
        calculate_payout_amount,
        get_fixed_payout_amount,
    )

    if is_owner_member(member):
        return int(round(rack_dollars(job) * 100))

    from ..services.commercial_jobs import is_employee
    from ..services.client_service_payouts import set_payout_cents
    if is_employee(member):
        return 0  # in-house staff: salary handled outside Dispatch
    tier = (member or {}).get("tier") or job.get("vendor_tier") or 1
    fixed = set_payout_cents(job, tier)
    if fixed is not None:
        return int(fixed)

    package_id = job.get("package_id")
    sqft = job.get("sqft")
    amount = await get_fixed_payout_amount(db, package_id, sqft, tier)
    if amount is None:
        amount = calculate_payout_amount(
            job.get("rack_price") if job.get("rack_price") is not None else job.get("package_price"),
            tier,
        )
    return int(amount or 0)


def public_profile(member: dict, extra: Optional[dict] = None) -> dict:
    owner = is_owner_member(member)
    title = member_title(member)
    tier = 3 if owner else (member.get("tier") or 1)
    rpas_url = member.get("rpas_certificate_url") or ""
    rpas_level = member.get("rpas_certificate_level") or ("advanced" if owner else None)
    assignable = is_assignable(member)
    # Approved + active opens the app; jobs need the onboarding checklist too.
    onboarded = owner or bool(member.get("onboarding_completed_at"))
    out = {
        "id": member.get("id"),
        "name": member.get("name") or "",
        "email": member.get("email") or "",
        "phone": member.get("phone") or "",
        "role": title,
        "title": title,
        "vendor_type": "photographer" if owner else _vendor_type_of(member),
        "availability": (member.get("status") or "") == "available",
        "status": member.get("status") or "offline",
        "skills": member_skills(member),
        "service_areas": member.get("service_areas") or [],
        "home_city": member.get("home_city") or "",
        "max_travel_km": member.get("max_travel_km") or 50,
        "tier": tier,
        "tier_label": TIER_LABELS.get(tier, "C"),
        "drone_certified": bool(member.get("drone_certified")),
        "rpas_certificate_url": rpas_url,
        "rpas_certificate_level": rpas_level,
        "rpas_certificate_number": member.get("rpas_certificate_number"),
        "onboarding_status": member.get("onboarding_status") or "pending",
        "is_active": bool(member.get("is_active")),
        "assignable": assignable and onboarded,
        "assignable_reason": (
            None if (assignable and onboarded)
            else "Pending — not assignable" if (member.get("onboarding_status") or "") == "pending"
            else "Finish setup to get jobs" if assignable
            else "Not approved"
        ),
        "onboarding_complete": onboarded,
        "payout_model": payout_model(member),
        "payout_label": (
            "Full package · $0 deduction" if owner else "Fixed rate card"
        ),
        "deduction_cad": 0 if owner else None,
        "show_platform_cut": False,
        "on_time_rate": member.get("on_time_rate") or 0,
        "quality_rating": member.get("quality_rating") or 0,
        "total_assignments": member.get("total_assignments") or 0,
        "active_assignments": member.get("active_assignments") or 0,
        "member_since": member.get("created_at") or "",
        "avatar_url": member.get("avatar_url") or "",
        "total_jobs_completed": member.get("total_assignments") or 0,
        "rating": float(member.get("quality_rating") or 0),
        "ic_agreement_version": member.get("ic_agreement_version"),
        "member_role": member.get("role") or "",
        "terms_accepted_at": member.get("terms_accepted_at"),
        "application_submitted": bool(member.get("terms_accepted_at")),
        "app_access": bool(assignable or owner),
        "portfolio_links": member.get("portfolio_links") or [],
        "portfolio_samples": member.get("portfolio_samples") or [],
    }
    # Commercial gate visibility (flag only; never demotes or notifies).
    from ..services.commercial_jobs import insurance_status, is_employee, top_tier_insurance_flag
    out["worker_type"] = "employee" if is_employee(member) else "contractor"
    if out["worker_type"] == "employee" and not owner:
        out["payout_model"] = "employee_salaried"
        out["payout_label"] = "In-house staff · salaried (no per-job payout)"
    out["insurance"] = insurance_status(member)
    out["top_tier_insurance_missing"] = top_tier_insurance_flag({**member, "tier": tier})
    out["mapping_qualified"] = "mapping" in ((member.get("skills") or []) if isinstance(member.get("skills"), list) else [])
    if extra:
        out.update(extra)
    # Prefer assembled samples when caller passed documents via extra.
    try:
        from .portfolio import assemble_portfolio_samples
        docs = (extra or {}).get("_portfolio_documents")
        out["portfolio_samples"] = assemble_portfolio_samples(member, docs)
        out.pop("_portfolio_documents", None)
    except Exception:
        pass
    return out
