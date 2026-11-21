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
# "mapping" (orthomosaic / 3D capture) is admin-granted only, never inferred from
# equipment or specialty text — commercial mapped work requires a QC'd test mission.
SKILL_KEYS = ("photo", "drone", "reel", "tour", "twilight", "mapping")

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
    # www / OS / Companion stamp job_type=floor_plan_only (stripe-webhook +
    # book_checkout + PACKAGES). SKILL_KEYS has photo, not floor_plan* — without
    # this alias hard-gate needed.issubset(have) demands a phantom skill no
    # photo vendor has, so floor-plan-only books never auto-assign.
    "floor_plan": "photo",
    "floorplan": "photo",
    "floor_plan_only": "photo",
    "floorplans": "photo",
    "2d_floor_plan": "photo",
    "virtual": "tour",
    "3d": "tour",
    "polycam": "tour",
}


def normalize_skill_key(raw: Any) -> str:
    """Map specialty/job_type free text onto SKILL_KEYS when an alias exists."""
    key = str(raw or "").strip().lower()
    return _SKILL_KEY_ALIASES.get(key, key)


def specialty_matches_job(member: Any, job: Any) -> bool:
    """Soft specialty boost for assign scoring — video/reel + floor_plan→photo lockstep.

    Hard-gate uses job_required_skills ⊆ vendor_skills. Rule-based scoring used
    ``specialty == job_type or job_type in have``; after video→reel / floor_plan→photo
    skill aliases, have never contains the phantom labels, so specialty=reel|photo
    (and skills=["reel"|"photo"]) lost the specialty boost on in-flight
    job_type=video|floor_plan_only. Normalize both sides the same way
    vendor_skills / job_required_skills do.
    """
    m = _as_dict(member)
    j = _as_dict(job)
    spec = normalize_skill_key(m.get("specialty"))
    jt = normalize_skill_key(j.get("job_type") or "photo")
    if spec and jt and spec == jt:
        return True
    have = vendor_skills(member)
    return bool(jt and jt in have)

VENDOR_TYPES = ("photographer", "stager", "cleaner", "inspector", "measurer", "handyman")
# Values vendor_members.vendor_type accepts today (vendor_members_vendor_type_check,
# migration 20260913193000). inspector / measurer / handyman wait on the
# widening migration Kam must approve; until then their trade lives in
# application_profile.trade and the column keeps its 'photographer' default.
# vendor_members_vendor_type_check allows all six trades (checked 2026-11-01).
DB_VENDOR_TYPES = ("photographer", "stager", "cleaner", "inspector", "measurer", "handyman")
# What older rows could hold: inspector / measurer / handyman were stored as
# 'photographer' with the real trade on application_profile.trade or the label.
LEGACY_COLUMN_TYPES = ("photographer", "stager", "cleaner")
# Trades that need a $2M CGL certificate of insurance (Kam, 2026-10-26).
CGL_REQUIRED_TYPES = ("inspector", "handyman")
CGL_MIN_CENTS = 2_000_000 * 100
# Every trade has a set price and a set cut (Kam, t631u/t635u 2026-11-21), so
# no trade is held back from auto-assign any more. Kept as an empty tuple so
# old imports keep working.
QUOTE_ONLY_TYPES: tuple = ()
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
    "inspection": "inspector",
    "inspections": "inspector",
    "home_inspector": "inspector",
    "home_inspection": "inspector",
    "building_inspection": "inspector",
    "measurement": "measurer",
    "measurements": "measurer",
    "measuring": "measurer",
    "measure": "measurer",
    "repair": "handyman",
    "repairs": "handyman",
    "handyperson": "handyman",
    "handy": "handyman",
    # De-staging (pickup / removal of staged furniture) is stager work.
    "destage": "stager",
    "destaging": "stager",
    "de_stage": "stager",
    "de_staging": "stager",
    "staging_pickup": "stager",
    "staging_removal": "stager",
}
# Package names / ids that mark a de-staging job when job_type doesn't say.
_DESTAGE_PACKAGE_WORDS = (
    "destag", "de_stag", "de-stag", "de stag",
    "staging_pickup", "staging pickup", "staging-pickup",
    "staging_removal", "staging removal", "staging-removal",
)
ROLE_FOR_VENDOR_TYPE = {
    "photographer": "Photographer",
    "stager": "Stager",
    "cleaner": "Cleaner",
    "inspector": "Inspector",
    "measurer": "Measurer",
    "handyman": "Repairs",
}
IC_TITLE_FOR_VENDOR_TYPE = {
    "photographer": "Photographer IC",
    "stager": "Stager IC",
    "cleaner": "Cleaner IC",
    "inspector": "Inspector IC",
    "measurer": "Measurer IC",
    "handyman": "Repairs IC",
}
# Media / field-capture job_types stay on the photographer spine.
# Drone is a photographer capability (Advanced RPAS), not a vendor_type.
_PHOTOGRAPHER_JOB_TYPES = {
    "photo", "photography", "photos", "hdr", "drone", "reel", "video",
    "tour", "virtual", "twilight", "floor_plan", "floorplan", "exterior",
    "visit", "virtual_visit", "media", "",
    "listing", "3d_tour", "virtual_tour", "floor_plan_only", "floorplans",
    "2d_floor_plan", "cinematic", "cinematic_reel", "photo_floorplan",
    "photo_video", "photo_drone",
}
# Compound media job_types (photo_floorplan, site_mapped_capture, ...) match on
# any of these tokens. Anything else is unknown and goes to admin review.
_MEDIA_JOB_TOKENS = {
    "photo", "photos", "photography", "hdr", "drone", "aerial", "reel", "video",
    "cinematic", "tour", "virtual", "3d", "2d", "twilight", "floor", "floorplan",
    "floorplans", "exterior", "visit", "media", "listing", "progress", "mapped",
    "mapping", "capture", "monitoring", "orthomosaic",
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



def trade_coi_ok(member: Any, vendor_type: str, today: Any = None) -> bool:
    """Inspector / handyman auto-assign needs a verified, unexpired $2M CGL COI (Kam 2026-10-26)."""
    if vendor_type not in CGL_REQUIRED_TYPES:
        return True
    m = _as_dict(member)
    if not m.get("coi_verified"):
        return False
    try:
        if int(float(m.get("coi_liability_cad") or 0)) * 100 < CGL_MIN_CENTS:
            return False
    except (TypeError, ValueError):
        return False
    exp = str(m.get("coi_expires_on") or "")[:10]
    if not exp:
        return False
    from datetime import date as _date
    d = today or _date.today()
    return exp >= d.isoformat()

def onboarding_complete(member: Any) -> bool:
    """Checklist done (or grandfathered): vendor_members.onboarding_completed_at set."""
    return bool(_as_dict(member).get("onboarding_completed_at"))


def is_aman_pending(member: Any) -> bool:
    """Aman Sammy (and any unapproved applicant) must never be assigned."""
    m = _as_dict(member)
    status = str(m.get("onboarding_status") or "").strip().lower()
    if status != "approved":
        return True
    name = str(m.get("name") or "").lower()
    return all(tok in name for tok in AMAN_NAME_TOKENS)



def _vendor_type_key(raw: Any) -> str:
    key = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    return VENDOR_TYPE_ALIASES.get(key, key)


def strict_vendor_type(raw: Any) -> Optional[str]:
    """Known vendor type or None. Never guesses photographer."""
    key = _vendor_type_key(raw)
    return key if key in VENDOR_TYPES else None


def normalize_vendor_type(raw: Any) -> str:
    """Members / applications: unknown or empty values fall back to photographer (media path)."""
    return strict_vendor_type(raw) or "photographer"


def storage_vendor_type(vendor_type: Any) -> str:
    """Value safe to write to vendor_members.vendor_type before the widening migration."""
    vt = normalize_vendor_type(vendor_type)
    return vt if vt in DB_VENDOR_TYPES else "photographer"


def _profile_dict(m: dict) -> dict:
    prof = m.get("application_profile")
    if isinstance(prof, str):
        try:
            import json
            prof = json.loads(prof)
        except ValueError:
            prof = None
    return prof if isinstance(prof, dict) else {}


def member_trade(member: Any) -> Optional[str]:
    """Trade recorded on the application (application_profile.trade), if any."""
    return strict_vendor_type(_profile_dict(_as_dict(member)).get("trade"))


def _label_vendor_type(blob: str) -> Optional[str]:
    """Role / title label (Stager, Inspector IC, Repairs) -> vendor type."""
    blob = blob.strip().lower()
    if blob.endswith(" ic"):
        blob = blob[:-3]
    return strict_vendor_type(blob)


def member_vendor_type(member: Any) -> str:
    m = _as_dict(member)
    # Owner is always on the photographer spine (media + RPAS capable).
    if is_platform_owner(m):
        return "photographer"
    # Trades the DB column can't hold yet (inspector / measurer / handyman) live
    # on application_profile.trade or the role/title label and win over the
    # column's 'photographer' default.
    trade = member_trade(m)
    if trade and trade not in LEGACY_COLUMN_TYPES:
        return trade
    labels = [_label_vendor_type(str(m.get(k) or "")) for k in ("role", "title")]
    for label in labels:
        if label and label not in LEGACY_COLUMN_TYPES:
            return label
    explicit = m.get("vendor_type")
    if explicit:
        return normalize_vendor_type(explicit)
    for label in labels:
        if label:
            return label
    return trade or "photographer"


def role_for_vendor_type(vendor_type: Any) -> str:
    return ROLE_FOR_VENDOR_TYPE.get(normalize_vendor_type(vendor_type), "Photographer")


def ic_title_for_vendor_type(vendor_type: Any) -> str:
    return IC_TITLE_FOR_VENDOR_TYPE.get(normalize_vendor_type(vendor_type), "Photographer IC")


def _is_media_job_type(jt: str) -> bool:
    if jt in _PHOTOGRAPHER_JOB_TYPES:
        return True
    tokens = [t for t in jt.replace("-", "_").replace(" ", "_").split("_") if t]
    return any(t in _MEDIA_JOB_TOKENS for t in tokens)


def job_required_vendor_type(job: Any) -> Optional[str]:
    """Vendor type a job needs, or None when Bricks can't tell (admin review).

    Media bookings -> photographer; staging / cleaning / inspection /
    measurement / repairs jobs -> those trades. Unknown job types used to fall
    back to photographer, which could send a repair or inspection job to a
    photographer. They now return None and are never auto-assigned.
    """
    j = _as_dict(job)
    for key in ("required_vendor_type", "vendor_type"):
        if j.get(key):
            return strict_vendor_type(j.get(key))
    jt = str(j.get("job_type") or "photo").strip().lower()
    mapped = _vendor_type_key(jt)
    if mapped in VENDOR_TYPES and mapped != "photographer":
        return mapped
    # A de-staging package is stager work even on a job_type left at the
    # photo default.
    package_blob = " ".join(str(j.get(k) or "") for k in ("package_id", "package_name")).lower()
    if any(w in package_blob for w in _DESTAGE_PACKAGE_WORDS):
        return "stager"
    if mapped == "photographer" or _is_media_job_type(jt):
        return "photographer"
    try:
        from .commercial_jobs import is_commercial_job
        if is_commercial_job(j):
            return "photographer"
    except Exception:
        pass
    return None

TRADE_SKILL = {
    "photographer": "photo",
    "stager": "staging",
    "cleaner": "cleaning",
    "inspector": "inspection",
    "measurer": "measurement",
    "handyman": "repairs",
}


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
        # Floor-plan-only books stamp floor_plan* — map onto photo (SKILL_KEYS).
        if key in ("floor_plan", "floorplan", "floor_plan_only", "floorplans", "2d_floor_plan"):
            skills.add("photo")
    spec = str(m.get("specialty") or "").strip().lower()
    if spec in ("photo", "photography", "photos"):
        skills.add("photo")
    elif spec in ("floor_plan", "floorplan", "floor_plan_only", "floorplans", "2d_floor_plan"):
        skills.add("photo")
    elif spec == "virtual":
        skills.add("tour")
    elif spec in ("video", "cinematic", "cinematic_reel", "reel"):
        skills.add("reel")
    elif spec and spec != "mapping":
        skills.add(spec)
    if m.get("drone_certified") or has_advanced_rpas(m):
        skills.add("drone")
    if m.get("polycam_equipped"):
        skills.add("tour")
    for item in m.get("equipment_list") or []:
        t = str(item).lower()
        for skill in SKILL_KEYS:
            if skill != "mapping" and skill in t:
                skills.add(skill)
        if "polycam" in t or "splat" in t or "virtual" in t:
            skills.add("tour")
    if not skills:
        vt = member_vendor_type(m)
        skills.add(TRADE_SKILL.get(vt, "photo"))
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


# Client-service add-ons that need their own capture skill on top of the
# job_type's. inv_acquisition (drone) sells interior stills and a Virtual
# Visit; inv_acquisition_mapped (site_mapped_capture) sells the same, so a
# drone-only vendor must not be auto-assigned work they can't deliver.
_ADD_ON_SKILLS = {
    "interior": "photo",
    "interior_stills": "photo",
    "virtual_visit": "tour",
    "tour": "tour",
    "virtual": "tour",
}


def _add_on_skills(job: Any) -> set[str]:
    keys = _add_on_keys(_as_dict(job).get("add_ons") or [])
    return {_ADD_ON_SKILLS[k] for k in keys if k in _ADD_ON_SKILLS}


def job_required_skills(job: Any) -> set[str]:
    j = _as_dict(job)
    # Commercial (builder/developer) kinds: drone (+ mapping for mapped work).
    # progress_capture used to fall through to skills.add("progress_capture"),
    # a skill no vendor can hold, so OS progress visits never auto-assigned.
    from .commercial_jobs import is_commercial_job, required_skills as _commercial_skills
    if is_commercial_job(j):
        return _commercial_skills(j) | _add_on_skills(j)
    needed_type = job_required_vendor_type(j)
    # Non-media Wave A jobs: type match is the hard gate; skills are soft labels.
    if needed_type in TRADE_SKILL and needed_type != "photographer":
        return {TRADE_SKILL[needed_type]}
    jt = str(j.get("job_type") or "photo").lower()
    skills: set[str] = set()
    if jt in ("photo", "photography", ""):
        skills.add("photo")
    elif jt in ("floor_plan", "floorplan", "floor_plan_only", "floorplans", "2d_floor_plan"):
        # SKILL_KEYS has photo, not floor_plan*. www stripe-webhook / OS
        # book_checkout / Companion PACKAGES stamp job_type=floor_plan_only —
        # hard-gate must not demand a phantom skill photo vendors lack.
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
    skills |= _add_on_skills(j)
    if not skills:
        skills.add("photo")
    return skills


def min_tier_for_job(job: Any) -> int:
    j = _as_dict(job)
    from .commercial_jobs import TOP_TIER as _COMMERCIAL_TIER, is_commercial_job
    if is_commercial_job(j):
        return _COMMERCIAL_TIER
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
    from .commercial_jobs import is_commercial_job, job_requires_advanced_rpas
    if is_commercial_job(j):
        return job_requires_advanced_rpas(j)
    if str(j.get("job_type") or "").lower() == "drone":
        return True
    return "drone" in _add_on_keys(j.get("add_ons") or [])


def member_eligible_for_job(member: Any, job: Any) -> bool:
    """Approved, active, type/location/skill match.

    Listing jobs: insurance is not a gate. COI and WSIB are optional records and
    missing, expired, or unverified proof must not exclude an otherwise approved
    vendor from a listing job.

    Commercial jobs (commercial_jobs.COMMERCIAL_KINDS, incl. OS progress_capture):
    Tier A + verified, unexpired drone/aviation liability >= the job minimum
    (default $2M) + Advanced RPAS unless the job opts out (Kam, 2026-10-17).
    In-house employees (worker_type="employee") use the company policy instead
    of a personal COI and are not tier-banded.
    """
    m = _as_dict(member)
    if not m.get("is_active"):
        return False
    # Terminated for a no-show / cancel after accepting (Kam, 2026-11-02): never auto-assigned.
    if m.get("terminated_at") or str(m.get("onboarding_status") or "") == "terminated":
        return False
    if is_aman_pending(m):
        return False
    # Approved vendors finish the onboarding checklist first (Kam, 2026-10-31).
    if not onboarding_complete(m):
        return False
    # Paused vendor (expired credential, probation) or paused trade (Kam, 2026-11-01).
    from ..shared.vendor_identity import is_vendor_paused
    if is_vendor_paused(m):
        return False
    needed_type = job_required_vendor_type(job)
    from .trade_specs import trade_enabled
    if needed_type is not None and not trade_enabled(needed_type):
        return False
    # Unknown job type: fail closed so ops routes it by hand.
    if needed_type is None:
        return False
    if needed_type in QUOTE_ONLY_TYPES:
        return False
    # Measuring is one service: the measured floor plan. A measurer can take a
    # floor-plan-only job as well as a photographer (Kam t635u, audit decision 5).
    jt = str(_as_dict(job).get("job_type") or "").strip().lower()
    if needed_type == "photographer" and jt == "floor_plan_only" and member_vendor_type(m) == "measurer":
        return location_matches(m, job)
    if member_vendor_type(m) != needed_type:
        return False
    if not location_matches(m, job):
        return False
    # Trades: type + location + approved (onboarding_complete already requires
    # their verified credentials). No photo skill/RPAS gates.
    if needed_type in ("stager", "cleaner", "inspector", "measurer", "handyman"):
        return trade_coi_ok(m, needed_type)
    from .commercial_jobs import is_commercial_job, is_employee
    # Tier bands are a contractor concept; in-house staff on commercial jobs
    # are gated by the company policy + RPAS + skills instead.
    if int(m.get("tier") or 1) < min_tier_for_job(job) and not (is_employee(m) and is_commercial_job(job)):
        return False
    needed = job_required_skills(job)
    have = vendor_skills(m)
    if not needed.issubset(have):
        return False
    if is_drone_job(job) and not has_advanced_rpas(m):
        return False
    if "tour" in needed and not (m.get("polycam_equipped") or "tour" in have):
        return False
    # Commercial: Tier A + verified, unexpired drone liability >= job minimum.
    from .commercial_jobs import commercial_gate_failures
    if commercial_gate_failures(m, job, has_skills=have, advanced_rpas=has_advanced_rpas(m)):
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
    # Canonical cents include every add-on and exclude client GST/HST.
    if j.get("subtotal_cents") is not None:
        return max(0, int(j["subtotal_cents"]))
    rack = j.get("rack_price")
    if rack is None:
        rack = j.get("package_price")
    return rack_to_cents(rack)


def display_title(member: Any) -> str:
    if is_platform_owner(member):
        return "Owner"
    return ic_title_for_vendor_type(member_vendor_type(member))


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
