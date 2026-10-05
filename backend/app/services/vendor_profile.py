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

# Free-text specialty / job_type labels that map onto SKILL_KEYS (www / Companion /
# media_intake portfolio lockstep). Used by assign scoring so specialty=reel still
# boosts legacy job_type=video after vendor_skills drops the phantom "video" label.
_SKILL_KEY_ALIASES = {
    "video": "reel",
    "cinematic": "reel",
    "cinematic_reel": "reel",
    "photography": "photo",
    "photos": "photo",
    "hdr": "photo",
    "virtual": "tour",
    "3d": "tour",
    "polycam": "tour",
}


def normalize_skill_key(raw: Any) -> str:
    """Map specialty/job_type free text onto SKILL_KEYS when an alias exists."""
    key = str(raw or "").strip().lower()
    return _SKILL_KEY_ALIASES.get(key, key)


def specialty_matches_job(member: Any, job: Any) -> bool:
    """Soft specialty boost for assign scoring — video/reel/cinematic lockstep.

    Hard-gate uses job_required_skills ⊆ vendor_skills. Rule-based scoring used
    ``specialty == job_type or job_type in have``; after video→reel skill alias,
    have never contains the phantom "video" label, so specialty=reel (and
    skills=["reel"]) lost the specialty boost on in-flight job_type=video.
    Normalize both sides the same way vendor_skills / job_required_skills do.
    """
    m = _as_dict(member)
    j = _as_dict(job)
    spec = normalize_skill_key(m.get("specialty"))
    jt = normalize_skill_key(j.get("job_type") or "photo")
    if spec and jt and spec == jt:
        return True
    have = vendor_skills(member)
    return bool(jt and jt in have)

VENDOR_TYPES = ("photographer", "stager", "cleaner")
VENDOR_TYPE_ALIASES = {
    "photo": "photographer",
    "photography": "photographer",
    "photographer_ic": "photographer",
    "media": "photographer",
    "staging": "stager",
    "stage": "stager",
    "home_staging": "stager",
    "furniture_staging": "stager",
    "cleaning": "cleaner",
    "clean": "cleaner",
    "turnover": "cleaner",
    "maid": "cleaner",
}
ROLE_FOR_VENDOR_TYPE = {
    "photographer": "Photographer",
    "stager": "Stager",
    "cleaner": "Cleaner",
}
IC_TITLE_FOR_VENDOR_TYPE = {
    "photographer": "Photographer IC",
    "stager": "Stager IC",
    "cleaner": "Cleaner IC",
}
# Media / field-capture job_types stay on the photographer spine.
# Drone is a photographer capability (Advanced RPAS), not a vendor_type.
_PHOTOGRAPHER_JOB_TYPES = {
    "photo", "photography", "photos", "hdr", "drone", "reel", "video",
    "tour", "virtual", "twilight", "floor_plan", "floorplan", "exterior",
    "visit", "virtual_visit", "media", "",
}

GTA_TOKENS = {
    "gta", "greater toronto", "toronto", "mississauga", "brampton", "vaughan",
    "markham", "richmond hill", "oakville", "burlington", "milton", "peel",
    "halton", "york", "durham", "etobicoke", "scarborough", "north york",
    "ajax", "pickering", "whitby", "oshawa", "vaughan", "caledon", "newmarket",
    # Golden Horseshoe / launch footprint (Canada-only Ontario hub)
    "hamilton", "niagara", "niagara falls", "st catharines", "st. catharines",
    "guelph", "cambridge", "kitchener", "waterloo", "london", "barrie",
    "golden horseshoe",
}

# Address/city values that mean "not geocoded yet" — never block auto-assign.
_LOCATION_PLACEHOLDERS = frozenset({
    "", "-", "—", "–", "−", "tbd", "tba", "n/a", "na", "none", "null",
    "unknown", "address tbd", "address tba", "location tbd", "location tba",
})

# Canada province aliases — job.state / service_areas may use either form.
PROVINCE_ALIASES = {
    "ab": {"ab", "alberta"},
    "bc": {"bc", "british columbia"},
    "mb": {"mb", "manitoba"},
    "nb": {"nb", "new brunswick"},
    "nl": {"nl", "newfoundland", "newfoundland and labrador", "labrador"},
    "ns": {"ns", "nova scotia"},
    "nt": {"nt", "northwest territories"},
    "nu": {"nu", "nunavut"},
    "on": {"on", "ontario"},
    "pe": {"pe", "pei", "prince edward island"},
    "qc": {"qc", "quebec", "québec"},
    "sk": {"sk", "saskatchewan"},
    "yt": {"yt", "yukon", "yukon territory"},
}

_PROVINCE_LOOKUP: dict[str, str] = {}
for _code, _names in PROVINCE_ALIASES.items():
    for _n in _names:
        _PROVINCE_LOOKUP[_n] = _code


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



def normalize_vendor_type(raw: Any) -> str:
    """Wave A types only. Unknown values fall back to photographer (media path)."""
    key = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not key:
        return "photographer"
    key = VENDOR_TYPE_ALIASES.get(key, key)
    return key if key in VENDOR_TYPES else "photographer"


def member_vendor_type(member: Any) -> str:
    m = _as_dict(member)
    # Owner is always on the photographer spine (media + RPAS capable).
    if is_platform_owner(m):
        return "photographer"
    explicit = m.get("vendor_type")
    if explicit:
        return normalize_vendor_type(explicit)
    role = str(m.get("role") or "").strip().lower()
    title = str(m.get("title") or "").strip().lower()
    for blob in (role, title):
        mapped = VENDOR_TYPE_ALIASES.get(blob, blob)
        if mapped in VENDOR_TYPES:
            return mapped
    return "photographer"


def role_for_vendor_type(vendor_type: Any) -> str:
    return ROLE_FOR_VENDOR_TYPE.get(normalize_vendor_type(vendor_type), "Photographer")


def ic_title_for_vendor_type(vendor_type: Any) -> str:
    return IC_TITLE_FOR_VENDOR_TYPE.get(normalize_vendor_type(vendor_type), "Photographer IC")


def job_required_vendor_type(job: Any) -> str:
    """Media bookings → photographer; future staging/cleaning jobs → those types."""
    j = _as_dict(job)
    for key in ("required_vendor_type", "vendor_type"):
        if j.get(key):
            return normalize_vendor_type(j.get(key))
    jt = str(j.get("job_type") or "photo").strip().lower()
    mapped = VENDOR_TYPE_ALIASES.get(jt, jt)
    if mapped in ("stager", "cleaner"):
        return mapped
    if jt in _PHOTOGRAPHER_JOB_TYPES or mapped == "photographer":
        return "photographer"
    # Unknown job types stay on photographer so media path never breaks.
    return "photographer"

def has_advanced_rpas(member: Any) -> bool:
    m = _as_dict(member)
    if m.get("rpas_advanced") is True:
        return True
    level = str(m.get("rpas_certificate_level") or "").strip().lower()
    return level == "advanced"


def vendor_skills(member: Any) -> set[str]:
    m = _as_dict(member)
    if is_platform_owner(m):
        return set(SKILL_KEYS)
    skills: set[str] = set()
    for item in m.get("skills") or []:
        key = str(item).strip().lower()
        if key in SKILL_KEYS:
            skills.add(key)
        if key in ("photography", "photos"):
            skills.add("photo")
        if key in ("virtual", "3d", "polycam"):
            skills.add("tour")
        # www / Companion cinematic reel — never leave a non-SKILL_KEYS "video"
        # label that hard-gates assign against reel-capable vendors.
        if key in ("video", "cinematic", "cinematic_reel"):
            skills.add("reel")
    spec = str(m.get("specialty") or "").strip().lower()
    if spec in ("photo", "photography", "photos"):
        skills.add("photo")
    elif spec == "virtual":
        skills.add("tour")
    elif spec in ("video", "cinematic", "cinematic_reel", "reel"):
        skills.add("reel")
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
        vt = member_vendor_type(m)
        if vt == "stager":
            skills.add("staging")
        elif vt == "cleaner":
            skills.add("cleaning")
        else:
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
    needed_type = job_required_vendor_type(j)
    # Non-media Wave A jobs: type match is the hard gate; skills are soft labels.
    if needed_type == "stager":
        return {"staging"}
    if needed_type == "cleaner":
        return {"cleaning"}
    jt = str(j.get("job_type") or "photo").lower()
    skills: set[str] = set()
    if jt in ("photo", "photography", ""):
        skills.add("photo")
    elif jt == "virtual":
        skills.add("tour")
    elif jt in ("reel", "video", "cinematic", "cinematic_reel"):
        # SKILL_KEYS has reel, not video. Pre-fa52afd Companion Story/Showcase/Suite
        # stamped job_type=video and hard-gated assign against reel vendors.
        skills.add("reel")
    elif jt in ("stager", "staging", "cleaner", "cleaning"):
        pass
    else:
        skills.add(jt)
    keys = _add_on_keys(j.get("add_ons") or [])
    blob = " ".join(keys) + " " + jt
    if jt == "drone" or "drone" in keys:
        skills.add("drone")
    # Cinematic reel aliases (www / Companion / media_intake portfolio lockstep).
    if (
        "reel" in blob
        or keys & {"reel", "video", "cinematic", "cinematic_reel"}
        or jt in ("reel", "video", "cinematic", "cinematic_reel")
    ):
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


def _normalize_province(raw: Any) -> Optional[str]:
    text = str(raw or "").strip().lower()
    if not text:
        return None
    if text in _PROVINCE_LOOKUP:
        return _PROVINCE_LOOKUP[text]
    # "Calgary, AB" / "AB Canada"
    for token in text.replace(",", " ").split():
        if token in _PROVINCE_LOOKUP:
            return _PROVINCE_LOOKUP[token]
    return None


def travel_within_cap(member: Any, job: Any, origin: Optional[tuple[float, float]] = None) -> Optional[bool]:
    """Hard travel gate when coordinates are known.

    Returns True within cap, False over cap, None when coords missing
    (caller should fall through to area/text matching).
    ``origin`` overrides vendor home (same-day previous job for travel chaining).
    """
    m = _as_dict(member)
    j = _as_dict(job)
    try:
        if origin is not None:
            vlat, vlng = float(origin[0]), float(origin[1])
        else:
            vlat, vlng = m.get("home_lat"), m.get("home_lng")
            if vlat is None or vlng is None:
                return None
            vlat, vlng = float(vlat), float(vlng)
        jlat, jlng = j.get("lat"), j.get("lng")
        if jlat is None or jlng is None:
            return None
        cap = int(m.get("max_travel_km") or 50)
        return haversine_km(vlat, vlng, float(jlat), float(jlng)) <= cap
    except (TypeError, ValueError):
        return None


def _is_placeholder_location_text(raw: Any) -> bool:
    text = str(raw or "").strip().lower()
    if text in _LOCATION_PLACEHOLDERS:
        return True
    # Unicode dashes / lone punctuation left after strip
    normalized = (
        text.replace("—", "-").replace("–", "-").replace("−", "-").strip("-").strip()
    )
    return normalized in _LOCATION_PLACEHOLDERS


def _job_location_unknown(job: Any) -> bool:
    """No lat/lng and address/city blank or placeholder (—, TBD, n/a, …).

    Launch intake sometimes stores em-dash address + province only with no
    geocode. That must fail-open so Ontario vendors are not all excluded.
    """
    j = _as_dict(job)
    try:
        lat, lng = j.get("lat"), j.get("lng")
        if lat is not None and lng is not None:
            float(lat)
            float(lng)
            return False
    except (TypeError, ValueError):
        pass
    return _is_placeholder_location_text(j.get("address")) and _is_placeholder_location_text(
        j.get("city")
    )


def _vendor_covers_gta(areas: list[str]) -> bool:
    return any(
        a in GTA_TOKENS or "gta" in a or "toronto" in a or "mississauga" in a for a in areas
    )


def _areas_match_job(areas: list[str], hay: str, job_province: Optional[str]) -> bool:
    if any(a and a in hay for a in areas):
        return True
    # Province-level service areas (Calgary/Alberta vendors without GTA tokens)
    vendor_provinces = set()
    for a in areas:
        code = _normalize_province(a)
        if code:
            vendor_provinces.add(code)
        # City tokens already covered by substring; also match bare city names
        if a and len(a) >= 3 and a in hay:
            return True
    if job_province and job_province in vendor_provinces:
        return True
    vendor_gta = _vendor_covers_gta(areas)
    job_gta = any(tok in hay for tok in GTA_TOKENS)
    if vendor_gta and (job_gta or not hay.strip()):
        return True
    # GTA / Mississauga-home spine covers Ontario launch jobs even when the
    # city is outside classic GTA_TOKENS (e.g. Hamilton) or only state=ON is set.
    # Calgary/AB still rejects: job_province is "ab", not "on".
    if vendor_gta and job_province == "on":
        return True
    return False


def location_matches(member: Any, job: Any) -> bool:
    """Coords + travel cap are primary. Over-cap is a hard miss even with empty areas.

    Empty service_areas fail-open ONLY when job/vendor coords are unavailable.
    Unknown/placeholder address (—, TBD) with no geocode fail-open so assign
    is not blocked. Ontario + GTA-coverage vendor is a location match.
    Canada-wide: city / province aliases match without requiring GTA tokens.
    """
    m = _as_dict(member)
    j = _as_dict(job)
    hay = " ".join(
        str(j.get(k) or "") for k in ("city", "address", "state", "zip_code")
    ).lower()
    areas = [str(a).strip().lower() for a in (m.get("service_areas") or []) if a]
    home_city = str(m.get("home_city") or "").strip().lower()
    if home_city:
        areas.append(home_city)
    job_province = _normalize_province(j.get("state")) or _normalize_province(hay)

    cap_hit = travel_within_cap(m, j)
    if cap_hit is True:
        return True
    if cap_hit is False:
        # Hard gate: known over-cap never rescued by empty/missing service_areas.
        return False

    # No usable place string/coords → do not block (ops can re-home later).
    if _job_location_unknown(j):
        return True

    if _areas_match_job(areas, hay, job_province):
        return True

    # No coords and no coverage polygon/cities: don't block assignment.
    if not areas:
        return True
    return False


def is_drone_job(job: Any) -> bool:
    j = _as_dict(job)
    if str(j.get("job_type") or "").lower() == "drone":
        return True
    return "drone" in _add_on_keys(j.get("add_ons") or [])


def member_eligible_for_job(member: Any, job: Any) -> bool:
    """Approved, active, type/location/skill match. Insurance is not a gate.

    COI and WSIB are optional records. Missing, expired, or unverified proof
    must not exclude an otherwise approved vendor from assignment or dispatch.
    coi_expires_on and wsib_expires_on are ignored here.
    """
    m = _as_dict(member)
    if not m.get("is_active"):
        return False
    if is_aman_pending(m):
        return False
    needed_type = job_required_vendor_type(job)
    if member_vendor_type(m) != needed_type:
        return False
    if not location_matches(m, job):
        return False
    # Stager / cleaner: type + location + active/approved. No photo skill/RPAS gates.
    if needed_type in ("stager", "cleaner"):
        return True
    if int(m.get("tier") or 1) < min_tier_for_job(job):
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
    return "Photographer IC"


def _missing_column(err: str, keys: list[str]) -> str | None:
    msg = str(err)
    for k in keys:
        if f"'{k}'" in msg or f'"{k}"' in msg or f" {k} " in msg:
            return k
    return None


async def _write_member(db, payload: dict, member_id: str | None) -> None:
    """Update/insert dropping columns PostgREST says are missing."""
    data = dict(payload)
    last = None
    for _ in range(12):
        try:
            if member_id:
                await db.table("vendor_members").update(data).eq("id", member_id).execute()
            else:
                await db.table("vendor_members").insert(data).execute()
            return
        except Exception as e:
            last = e
            miss = _missing_column(str(e), list(data.keys()))
            if not miss:
                raise
            data.pop(miss, None)
    if last:
        raise last


async def ensure_owner_profile(db) -> dict:
    """Idempotent: Kam is Owner, top tier, GTA, photo+drone+reel, Advanced RPAS flag, Aman pending."""
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    kam = {
        "id": KAM_MEMBER_ID,
        "vendor_id": ORG_VENDOR_ID,
        "name": "Ajani Kamali Akio",
        "email": "akashorter@icloud.com",
        "role": "Owner",
        "title": "Owner",
        "payout_mode": "owner_full",
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
        "rpas_advanced": True,
        "skills": ["photo", "drone", "reel", "tour", "twilight"],
        "updated_at": now,
    }
    out = {"ok": False}
    try:
        existing = await db.table("vendor_members").select("id").or_(
            f"id.eq.{KAM_MEMBER_ID},email.eq.akashorter@icloud.com,email.eq.akashorter@gmail.com"
        ).limit(1).execute()
        rows = existing.data or []
        if rows:
            kid = rows[0]["id"]
            await _write_member(db, kam, kid)
            out = {"ok": True, "id": kid, "action": "updated"}
        else:
            kam["created_at"] = now
            await _write_member(db, kam, None)
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
        out = {"ok": False, "error": str(e)[:240]}
    return out
