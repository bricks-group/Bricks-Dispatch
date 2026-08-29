"""Vendor profile helpers: owner payout, skills, location, RPAS, assign gates.

Tier field on vendor_members is an int 1/2/3 (entry / core / elite). Pay for ICs
is the fixed vendor_payout_rates card, NOT a % of rack. The platform owner
(Kam) is role=Owner and is paid the full package/rack amount with no cut.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from ..shared.geospatial import haversine_km

KAM_MEMBER_ID = "0c3fa7c2-d23b-4e44-90ad-ab2d19af2580"
KAM_EMAILS = {"akashorter@icloud.com", "akashorter@gmail.com"}
AMAN_NAME_TOKENS = ("aman", "sammy")
ORG_VENDOR_ID = "default_vendor"

TIER_LABELS = {1: "C", 2: "B", 3: "A"}  # display only; pay is the rate card
TOP_TIER = 3
SKILL_KEYS = ("photo", "drone", "reel", "tour", "twilight")

GTA_TOKENS = {
    "gta", "greater toronto", "toronto", "mississauga", "brampton", "vaughan",
    "markham", "richmond hill", "oakville", "burlington", "milton", "peel",
    "halton", "york", "durham", "etobicoke", "scarborough", "north york",
    "ajax", "pickering", "whitby", "oshawa", "vaughan", "caledon", "newmarket",
}


def _as_dict(member: Any) -> dict:
    if member is None:
        return {}
    if isinstance(member, dict):
        return member
    if hasattr(member, "model_dump"):
        return member.model_dump()
    if hasattr(member, "dict"):
        return member.dict()
    return getattr(member, "__dict__", {}) or {}


def is_platform_owner(member: Any) -> bool:
    m = _as_dict(member)
    role = str(m.get("role") or "").strip().lower()
    if role == "owner":
        return True
    email = str(m.get("email") or "").strip().lower()
    if email in KAM_EMAILS:
        return True
    return str(m.get("id") or "") == KAM_MEMBER_ID


def is_aman_pending(member: Any) -> bool:
    """Aman Sammy (and any unapproved applicant) must never be assigned."""
    m = _as_dict(member)
    status = str(m.get("onboarding_status") or "").strip().lower()
    if status != "approved":
        return True
    name = str(m.get("name") or "").lower()
    return all(tok in name for tok in AMAN_NAME_TOKENS)


def has_advanced_rpas(member: Any) -> bool:
    m = _as_dict(member)
    if m.get("rpas_advanced") is True:
        return True
    level = str(m.get("rpas_certificate_level") or "").strip().lower()
    return level == "advanced"


def vendor_skills(member: Any) -> set[str]:
    m = _as_dict(member)
    skills: set[str] = set()
    for item in m.get("skills") or []:
        key = str(item).strip().lower()
        if key in SKILL_KEYS:
            skills.add(key)
        if key in ("photography", "photos"):
            skills.add("photo")
        if key in ("virtual", "3d", "polycam"):
            skills.add("tour")
    spec = str(m.get("specialty") or "").strip().lower()
    if spec in ("photo", "photography", "photos"):
        skills.add("photo")
    elif spec == "virtual":
        skills.add("tour")
    elif spec:
        skills.add(spec)
    if m.get("drone_certified") or has_advanced_rpas(m):
        skills.add("drone")
    if m.get("polycam_equipped"):
        skills.add("tour")
    for item in m.get("equipment_list") or []:
        t = str(item).lower()
        for skill in SKILL_KEYS:
            if skill in t:
                skills.add(skill)
        if "polycam" in t or "splat" in t or "virtual" in t:
            skills.add("tour")
    if not skills:
        skills.add("photo")
    return skills


def _add_on_keys(add_ons: Iterable) -> set[str]:
    keys: set[str] = set()
    for item in add_ons or []:
        if isinstance(item, dict):
            raw = item.get("key") or item.get("id") or item.get("name") or ""
        else:
            raw = item
        keys.add(str(raw).strip().lower())
    return keys


def job_required_skills(job: Any) -> set[str]:
    j = _as_dict(job)
    jt = str(j.get("job_type") or "photo").lower()
    skills: set[str] = set()
    if jt in ("photo", "photography", ""):
        skills.add("photo")
    elif jt == "virtual":
        skills.add("tour")
    else:
        skills.add(jt)
    keys = _add_on_keys(j.get("add_ons") or [])
    blob = " ".join(keys) + " " + jt
    if jt == "drone" or "drone" in keys:
        skills.add("drone")
    if "reel" in blob:
        skills.add("reel")
    if "tour" in blob or "virtual" in blob:
        skills.add("tour")
    if "twilight" in blob:
        skills.add("twilight")
    if not skills:
        skills.add("photo")
    return skills


def min_tier_for_job(job: Any) -> int:
    j = _as_dict(job)
    explicit = j.get("min_tier_required")
    if explicit:
        try:
            return int(explicit)
        except (TypeError, ValueError):
            pass
    blob = f"{j.get('package_id') or ''} {j.get('package_name') or ''}".lower()
    if "estate" in blob or "luxury" in blob:
        return TOP_TIER
    if "premium" in blob:
        return 2
    return 1


def preferred_tier_for_job(job: Any) -> int:
    j = _as_dict(job)
    explicit = j.get("preferred_tier")
    if explicit:
        try:
            return int(explicit)
        except (TypeError, ValueError):
            pass
    return min_tier_for_job(j)


def location_matches(member: Any, job: Any) -> bool:
    m = _as_dict(member)
    j = _as_dict(job)
    hay = " ".join(
        str(j.get(k) or "") for k in ("city", "address", "state", "zip_code")
    ).lower()
    areas = [str(a).strip().lower() for a in (m.get("service_areas") or []) if a]
    home_city = str(m.get("home_city") or "").strip().lower()
    if home_city:
        areas.append(home_city)
    try:
        vlat, vlng = m.get("home_lat"), m.get("home_lng")
        jlat, jlng = j.get("lat"), j.get("lng")
        if vlat is not None and vlng is not None and jlat is not None and jlng is not None:
            cap = int(m.get("max_travel_km") or 50)
            if haversine_km(float(vlat), float(vlng), float(jlat), float(jlng)) <= cap:
                return True
    except (TypeError, ValueError):
        pass
    if any(a and a in hay for a in areas):
        return True
    vendor_gta = any(a in GTA_TOKENS or "gta" in a or "toronto" in a or "mississauga" in a for a in areas)
    job_gta = any(tok in hay for tok in GTA_TOKENS)
    if vendor_gta and (job_gta or not hay.strip()):
        return True
    # No coverage polygon/cities on the member: don't block assignment.
    if not areas:
        return True
    return False


def is_drone_job(job: Any) -> bool:
    j = _as_dict(job)
    if str(j.get("job_type") or "").lower() == "drone":
        return True
    return "drone" in _add_on_keys(j.get("add_ons") or [])


def member_eligible_for_job(member: Any, job: Any) -> bool:
    m = _as_dict(member)
    if not m.get("is_active"):
        return False
    if is_aman_pending(m):
        return False
    if int(m.get("tier") or 1) < min_tier_for_job(job):
        return False
    if not location_matches(m, job):
        return False
    needed = job_required_skills(job)
    have = vendor_skills(m)
    if not needed.issubset(have):
        return False
    if is_drone_job(job) and not has_advanced_rpas(m):
        return False
    if "tour" in needed and not (m.get("polycam_equipped") or "tour" in have):
        return False
    return True


def rack_to_cents(rack_price: Any) -> int:
    """jobs.rack_price is dollars (299) from Booking; payouts are cents."""
    if rack_price is None:
        return 0
    try:
        value = float(rack_price)
    except (TypeError, ValueError):
        return 0
    if value <= 0:
        return 0
    # Values already stored in cents (e.g. 16445) stay as cents.
    if value >= 1000:
        return int(round(value))
    return int(round(value * 100))


def owner_payout_cents(job: Any) -> int:
    j = _as_dict(job)
    rack = j.get("rack_price")
    if rack is None:
        rack = j.get("package_price")
    return rack_to_cents(rack)


def display_title(member: Any) -> str:
    if is_platform_owner(member):
        return "Owner"
    return str(_as_dict(member).get("role") or "Photographer") or "Photographer"


async def ensure_owner_profile(db) -> dict:
    """Idempotent: Kam is Owner, top tier, GTA, photo+drone+reel, Advanced RPAS flag, Aman pending."""
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    kam = {
        "id": KAM_MEMBER_ID,
        "vendor_id": ORG_VENDOR_ID,
        "name": "Ajani Kamali Akio",
        "email": "akashorter@icloud.com",
        "role": "Owner",
        "specialty": "photo",
        "status": "available",
        "is_active": True,
        "onboarding_status": "approved",
        "tier": TOP_TIER,
        "drone_certified": True,
        "rpas_certificate_level": "advanced",
        "polycam_equipped": True,
        "service_areas": ["Mississauga", "GTA", "Toronto", "Peel", "Halton", "York", "Durham"],
        "home_city": "Mississauga",
        "home_lat": 43.5890,
        "home_lng": -79.6441,
        "max_travel_km": 80,
        "updated_at": now,
    }
    extra = {
        "rpas_advanced": True,
        "skills": ["photo", "drone", "reel", "tour", "twilight"],
    }
    out = {"ok": False}
    try:
        existing = await db.table("vendor_members").select("id").or_(
            f"id.eq.{KAM_MEMBER_ID},email.eq.akashorter@icloud.com,email.eq.akashorter@gmail.com"
        ).limit(1).execute()
        rows = existing.data or []
        payload = {**kam, **extra}
        if rows:
            kid = rows[0]["id"]
            try:
                await db.table("vendor_members").update(payload).eq("id", kid).execute()
            except Exception:
                await db.table("vendor_members").update(kam).eq("id", kid).execute()
            out = {"ok": True, "id": kid, "action": "updated"}
        else:
            payload["created_at"] = now
            try:
                await db.table("vendor_members").insert(payload).execute()
            except Exception:
                kam["created_at"] = now
                await db.table("vendor_members").insert(kam).execute()
            out = {"ok": True, "id": KAM_MEMBER_ID, "action": "inserted"}
        try:
            await db.table("jobs").update({"vendor_id": ORG_VENDOR_ID}).eq("vendor_id", KAM_MEMBER_ID).execute()
        except Exception:
            pass
        try:
            await db.table("vendor_members").update({
                "is_active": False, "onboarding_status": "pending", "updated_at": now,
            }).ilike("name", "%aman%sammy%").execute()
        except Exception:
            pass
    except Exception as e:
        out = {"ok": False, "error": str(e)[:200]}
    return out
