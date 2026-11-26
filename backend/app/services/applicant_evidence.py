"""Structured applicant evidence: city, equipment, RPAS, availability, samples.

Pure functions (no I/O) so the review, the owner email, and the tests share
one definition. Every check says where its answer came from:

  verified       -- Bricks looked at something real (an uploaded photo, a
                    fetched page, photo metadata, a hash match).
  self_reported  -- the applicant typed it; Bricks only compared it to a rule.
  couldnt_check  -- Bricks tried and could not look (dead link, Instagram,
                    no vision provider, nothing uploaded).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

VERIFIED = "verified"
SELF_REPORTED = "self_reported"
COULDNT_CHECK = "couldnt_check"
SOURCE_LABEL = {
    VERIFIED: "Verified",
    SELF_REPORTED: "Self-reported",
    COULDNT_CHECK: "Couldn't check",
}

# ---------------------------------------------------------------------------
# Samples: 8-15 real estate photos uploaded straight to Bricks storage.
# ---------------------------------------------------------------------------
MIN_SAMPLES = 8
MAX_SAMPLES = 15
MAX_SAMPLE_BYTES = 15 * 1024 * 1024
MIN_SAMPLE_BYTES = 40 * 1024
MAX_CERT_BYTES = 10 * 1024 * 1024
SAMPLE_MIME = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
}
UPLOAD_PREFIX = "bricks-go/applicants"
UPLOAD_SESSION_TTL = 6 * 3600


def sniff_mime(data: bytes) -> Optional[str]:
    """Image type from magic bytes. None when it is not an accepted image."""
    if not data or len(data) < 12:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1"):
        return "image/heic"
    return None


def _session_secret() -> bytes:
    raw = (
        os.environ.get("APPLICANT_UPLOAD_SECRET")
        or os.environ.get("CAPTURE_PUT_SECRET")
        or os.environ.get("SUPABASE_JWT_SECRET")
        or ""
    )
    if not raw:
        raise RuntimeError("upload session secret not configured")
    return raw.encode("utf-8")


def _mac(msg: str) -> str:
    return hmac.new(_session_secret(), msg.encode(), hashlib.sha256).hexdigest()


def mint_upload_session(now: Optional[int] = None, owner: Optional[str] = None) -> str:
    """Opaque session id: <exp>.<key>.<hmac>. Paths live under it.

    owner is the applicant's verified auth user id. Its key, and so its storage
    folder, is the same on every call: an applicant always writes the same
    slots (see sample_path) instead of opening a new folder per request.
    """
    exp = int(now or time.time()) + UPLOAD_SESSION_TTL
    rand = _mac(f"applicant-owner:{owner}")[:16] if owner else secrets.token_hex(8)
    sig = _mac(f"{exp}.{rand}")[:24]
    return f"{exp}.{rand}.{sig}"


def verify_upload_session(session: str, now: Optional[int] = None, grace: int = 3600) -> bool:
    try:
        exp_s, rand, sig = str(session or "").split(".")
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    if not re.fullmatch(r"[0-9a-f]{16}", rand) or not re.fullmatch(r"[0-9a-f]{24}", sig):
        return False
    if exp + grace < int(now or time.time()):
        return False
    try:
        expected = hmac.new(_session_secret(), f"{exp}.{rand}".encode(), hashlib.sha256).hexdigest()[:24]
    except RuntimeError:
        return False
    return hmac.compare_digest(expected, sig)


def session_dir(session: str) -> str:
    """Storage folder for one upload session key (no dots; not tied to the expiry)."""
    _exp, rand, _sig = session.split(".")
    return f"{UPLOAD_PREFIX}/{rand}{_mac('dir.' + rand)[:8]}"


# Resume (required for photographers, 2026-10-31): PDF, DOC or DOCX, 10 MB.
RESUME_MIME = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}
MAX_RESUME_BYTES = 10 * 1024 * 1024
MIN_RESUME_BYTES = 1024


def resume_mime_for(name: str, mime: str) -> Optional[str]:
    """Browsers sometimes send '' or octet-stream for .doc/.docx; trust the extension then."""
    m = (mime or "").lower().strip()
    if m in RESUME_MIME:
        return m
    ext = (name or "").lower().rsplit(".", 1)[-1] if "." in (name or "") else ""
    for k, v in RESUME_MIME.items():
        if v == ext and m in ("", "application/octet-stream", "application/x-msword", "application/zip"):
            return k
    return None


def is_resume_path(path: str) -> bool:
    return "/resume-" in str(path or "")


def sample_path(session: str, index: int, kind: str, mime: str) -> str:
    """Fixed slot per (kind, index, type): minting again overwrites, never adds."""
    ext = RESUME_MIME.get(mime) if kind == "resume" else None
    ext = ext or SAMPLE_MIME.get(mime, "jpg")
    return f"{session_dir(session)}/{kind}-{index:02d}.{ext}"


def path_in_session(path: str, session: str) -> bool:
    p = str(path or "")
    if ".." in p.split("/") or not p:
        return False
    base = session_dir(session) + "/"
    return p.startswith(base) and "/" not in p[len(base):]


# ---------------------------------------------------------------------------
# Location: a city picker. In the GTA is derived here, never asked.
# ---------------------------------------------------------------------------
# Toronto + the four GTA regions (Peel, Halton, York, Durham). Keys are folded.
GTA_CITIES = (
    "Toronto", "Etobicoke", "North York", "Scarborough", "East York", "York",
    "Mississauga", "Brampton", "Caledon",
    "Oakville", "Burlington", "Milton", "Halton Hills",
    "Vaughan", "Markham", "Richmond Hill", "Aurora", "Newmarket", "King",
    "Whitchurch-Stouffville", "East Gwillimbury", "Georgina",
    "Pickering", "Ajax", "Whitby", "Oshawa", "Clarington", "Uxbridge", "Scugog", "Brock",
)
# Neighbourhood / community names people pick or type that sit inside a GTA city.
_GTA_ALIASES = {
    "thornhill", "woodbridge", "concord", "maple", "unionville", "stouffville",
    "king city", "georgetown", "bowmanville", "courtice", "downtown toronto",
    "gta", "greater toronto area", "nobleton", "bolton", "acton",
}
# Nearby but outside the GTA (the city picker lists these so the answer is clean).
NEAR_GTA_CITIES = (
    "Hamilton", "Barrie", "Guelph", "Kitchener", "Waterloo", "Cambridge",
    "St. Catharines", "Niagara Falls", "Orangeville", "Peterborough", "Innisfil",
    "Bradford West Gwillimbury", "Brantford", "London", "Kingston", "Ottawa",
)


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFKD", str(text or ""))
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = re.sub(r"[.,]", " ", folded.lower())
    folded = re.sub(r"\b(on|ont|ontario|canada|ca)\b", " ", folded)
    return " ".join(folded.split())


_GTA_KEYS = {_fold(c) for c in GTA_CITIES} | _GTA_ALIASES


def city_in_gta(city: str) -> Optional[bool]:
    """True/False from a picked city. None when blank."""
    key = _fold(city)
    if not key:
        return None
    if key in _GTA_KEYS:
        return True
    return False


def location_evidence(city: str) -> dict:
    raw = " ".join(str(city or "").split())
    in_gta = city_in_gta(raw)
    if in_gta is None:
        return {"key": "location", "ok": False, "source": COULDNT_CHECK, "text": "No city given."}
    if in_gta:
        return {"key": "location", "ok": True, "source": SELF_REPORTED, "text": f"Lives in {raw} (GTA)."}
    return {"key": "location", "ok": False, "source": SELF_REPORTED, "text": f"Lives in {raw}, outside the GTA."}


# ---------------------------------------------------------------------------
# Equipment minimums (Kam to confirm). Shoot standards elsewhere only say
# "full-frame or equivalent, fast primes, DJI/Autel for aerial" -- these make
# that concrete and checkable against what the applicant typed.
# ---------------------------------------------------------------------------
GEAR_MINIMUMS = (
    "Interchangeable-lens camera, full-frame or APS-C (mirrorless or DSLR) with auto exposure bracketing. Phones, point-and-shoots, action cams and Micro Four Thirds don't meet it.",
    "A wide-angle lens reaching 16mm full-frame equivalent or wider (e.g. 16-35mm on full-frame, 10-18mm on APS-C).",
    "A tripod (every interior is bracketed).",
    "Drone work only: a drone with a 1-inch or 1/1.3-inch-class camera (DJI Mini 3 Pro / Mini 4 Pro / Air / Mavic class or Autel EVO) plus a Transport Canada pilot certificate. Advanced is needed for most of the GTA core (Pearson / Billy Bishop airspace).",
    "Recommended, not required: flash or continuous lighting.",
)

_FF = r"(sony\s*(a|α|ilce-?)\s*7\w*|sony\s*a\s*7\w*|\ba7\s?(r|s|c|iv|v|iii|ii)?\w*|\ba9\w*|\ba1\b|sony\s*a1\b|fx3|fx6|zv-?e1\b|canon\s*(eos\s*)?(r|r3|r5|r6|r8|rp|5d|6d|1d)\w*|\beos\s*(r|r5|r6|r8|rp|5d|6d)\w*|nikon\s*(z\s?[5-9]|zf\b|d[678]\d0|d[345]\b|d850|d750|d780|d810)\w*|\bz\s?[5-9](\s?ii|\s?iii)?\b|\bzf\b|panasonic\s*(lumix\s*)?(s1|s5|s9)\w*|lumix\s*(s1|s5|s9)\w*|leica\s*(sl|q|m\d)\w*|sigma\s*fp\b)"
_APSC = r"(sony\s*(a|α|ilce-?)\s*6\d{3}\w*|\ba6\d{3}\b|zv-?e10|canon\s*(eos\s*)?(r7|r10|r50|r100|\d{2,3}d|m50|m6)\w*|nikon\s*(z\s?50|z\s?fc|z\s?30|d[357]\d{3})\w*|fuji\w*\s*(x-?[a-z]{1,2}\d+\w*)|\bx-?(t|s|h|e|pro)\d+\w*|ricoh\s*gr)"
_MEDIUM = r"(gfx\s?\d+\w*|hasselblad\s*x\d\w*)"
_MFT = r"(\bgh\d\w*|\bg9\w*|\bg85\b|olympus|om-?\d|om\s*system|e-m\d\w*|micro\s*four)"
_PHONE = r"(iphone|pixel\s?\d|galaxy\s*s\d+|samsung\s*galaxy|phone|gopro|insta360|osmo\s*pocket|dji\s*pocket)"


def camera_class(body: str) -> Optional[str]:
    t = str(body or "").lower()
    if not t.strip():
        return None
    if re.search(_PHONE, t):
        return "phone"
    if re.search(_MEDIUM, t):
        return "medium"
    if re.search(_APSC, t):
        return "apsc"
    if re.search(_FF, t):
        return "ff"
    if re.search(_MFT, t):
        return "mft"
    return "unknown"


_CROP = {"ff": 1.0, "medium": 0.79, "apsc": 1.5, "mft": 2.0, "unknown": 1.0}


def widest_focal_mm(lenses: str) -> Optional[float]:
    """Smallest focal length mentioned, e.g. '16-35mm f/4, 50mm' -> 16."""
    t = str(lenses or "").lower().replace("–", "-")
    vals = []
    for a, b in re.findall(r"(\d{1,3}(?:\.\d)?)\s*(?:-\s*(\d{1,3}(?:\.\d)?))?\s*mm", t):
        try:
            vals.append(float(a))
        except ValueError:
            pass
    vals = [v for v in vals if 4 <= v <= 600]
    return min(vals) if vals else None


def _yes(text: str) -> bool:
    t = str(text or "").strip().lower()
    return bool(t) and t not in ("no", "none", "n/a", "na", "-", "nope", "0", "false")


def equipment_text(equipment: dict) -> str:
    eq = equipment or {}
    parts = []
    for key, label in (
        ("camera_body", "Camera"), ("lenses", "Lenses"), ("tripod", "Tripod"),
        ("lighting", "Lighting"), ("drone_model", "Drone"),
    ):
        v = " ".join(str(eq.get(key) or "").split())
        if v:
            parts.append(f"{label}: {v}")
    return "; ".join(parts)


def equipment_list_from_profile(equipment: dict) -> list:
    """Flattened equipment_list for every existing reader."""
    eq = equipment or {}
    out = []
    for key in ("camera_body", "lenses", "tripod", "lighting", "drone_model"):
        v = " ".join(str(eq.get(key) or "").split())
        if v:
            out.append(v)
    return out


def equipment_from_list(items: Iterable[str]) -> dict:
    """Best-effort structured view of an older free-text equipment list."""
    camera = lenses = tripod = lighting = drone = ""
    for raw in items or ():
        s = " ".join(str(raw or "").split())
        low = s.lower()
        if not s:
            continue
        if re.search(r"\b(dji|autel|drone|mavic|avata|mini\s*\d)\b", low) and "mic" not in low and "gimbal" not in low and " rs " not in f" {low} ":
            drone = f"{drone}, {s}" if drone else s
        elif "tripod" in low:
            tripod = f"{tripod}, {s}" if tripod else s
        elif re.search(r"(\blight(s|ing)?\b|flash|strobe|godox|profoto|speedlite|softbox)", low) and "lightroom" not in low:
            lighting = f"{lighting}, {s}" if lighting else s
        elif re.search(r"\d\s*mm", low) and not camera_class(s) in ("ff", "apsc", "medium"):
            lenses = f"{lenses}, {s}" if lenses else s
        elif camera_class(s) in ("ff", "apsc", "medium", "mft", "phone") and not camera:
            camera = s
            if re.search(r"\d\s*mm", low):
                lenses = f"{lenses}, {s}" if lenses else s
    return {"camera_body": camera, "lenses": lenses, "tripod": tripod, "lighting": lighting, "drone_model": drone}


def equipment_evidence(equipment: dict, *, wants_drone: bool = False) -> list:
    """Bullets comparing typed kit to GEAR_MINIMUMS. All self-reported."""
    eq = equipment or {}
    out = []
    cls = camera_class(eq.get("camera_body"))
    body = " ".join(str(eq.get("camera_body") or "").split())
    if cls is None:
        out.append({"key": "camera", "ok": False, "source": SELF_REPORTED, "text": "No camera body listed."})
    elif cls in ("ff", "medium"):
        out.append({"key": "camera", "ok": True, "source": SELF_REPORTED, "text": f"Camera meets the minimum: {body} (full-frame)."})
    elif cls == "apsc":
        out.append({"key": "camera", "ok": True, "source": SELF_REPORTED, "text": f"Camera meets the minimum: {body} (APS-C)."})
    elif cls in ("mft", "phone"):
        kind = "Micro Four Thirds" if cls == "mft" else "phone or compact"
        out.append({"key": "camera", "ok": False, "source": SELF_REPORTED, "text": f"Camera below the minimum: {body} ({kind})."})
    else:
        out.append({"key": "camera", "ok": None, "source": SELF_REPORTED, "text": f"Camera not recognised, check by hand: {body}."})

    widest = widest_focal_mm(eq.get("lenses"))
    if widest is None:
        widest = widest_focal_mm(body)  # kit lens typed with the body
    crop = _CROP.get(cls or "unknown", 1.0)
    if widest is None:
        out.append({"key": "lens", "ok": False, "source": SELF_REPORTED, "text": "No wide-angle lens listed."})
    else:
        equiv = widest * crop
        if equiv <= 17.0:
            out.append({"key": "lens", "ok": True, "source": SELF_REPORTED, "text": f"Wide-angle lens meets the minimum (widest {widest:g}mm)."})
        else:
            out.append({"key": "lens", "ok": False, "source": SELF_REPORTED, "text": f"No lens wide enough for interiors (widest listed is {widest:g}mm)."})

    if _yes(eq.get("tripod")):
        out.append({"key": "tripod", "ok": True, "source": SELF_REPORTED, "text": "Has a tripod."})
    else:
        out.append({"key": "tripod", "ok": False, "source": SELF_REPORTED, "text": "No tripod listed."})

    if _yes(eq.get("lighting")):
        out.append({"key": "lighting", "ok": True, "source": SELF_REPORTED, "text": "Has flash or lighting."})

    drone = " ".join(str(eq.get("drone_model") or "").split())
    if wants_drone and not _yes(drone):
        out.append({"key": "drone", "ok": False, "source": SELF_REPORTED, "text": "Ticked drone work but listed no drone."})
    elif _yes(drone):
        out.append({"key": "drone", "ok": True, "source": SELF_REPORTED, "text": f"Drone: {drone}."})
    return out


def equipment_meets_minimum(bullets: list) -> bool:
    need = {"camera", "lens", "tripod"}
    got = {b["key"] for b in bullets if b.get("ok") is True}
    return need <= got


# ---------------------------------------------------------------------------
# RPAS: Transport Canada numbers can't be verified online.
# ---------------------------------------------------------------------------
_PILOT_CERT_RE = re.compile(r"^PC-?\d{6,12}$")
_DRONE_REG_RE = re.compile(r"^C-?\d{10}$")


def rpas_number_format(number: str) -> str:
    """pilot_cert | drone_registration | unusual | missing"""
    n = re.sub(r"\s+", "", str(number or "").upper())
    if not n:
        return "missing"
    if _PILOT_CERT_RE.match(n):
        return "pilot_cert"
    if _DRONE_REG_RE.match(n):
        return "drone_registration"
    return "unusual"


def rpas_evidence(claimed: bool, level: str, number: str, cert_attached: bool, cert_read: Optional[dict] = None) -> Optional[dict]:
    if not claimed:
        return None
    lvl = str(level or "").strip().lower()
    lvl_word = lvl.capitalize() if lvl in ("basic", "advanced") else "unknown level"
    fmt = rpas_number_format(number)
    article = "an" if lvl_word[:1].lower() in "aeiou" else "a"
    bits = [f"Claims {article} {lvl_word} drone pilot certificate"]
    ok: Optional[bool] = True
    if fmt == "missing":
        bits.append("no certificate number given")
        ok = False
    elif fmt == "drone_registration":
        bits.append(f"number {number} looks like a drone registration (C-…), not a pilot certificate")
        ok = False
    elif fmt == "unusual":
        bits.append(f"number {number} is an unusual format")
        ok = None
    else:
        bits.append(f"number {number}")
    if cert_attached:
        bits.append("cert photo attached")
        if cert_read:
            if cert_read.get("number_matches") is True:
                bits.append("number on the photo matches")
            elif cert_read.get("number_matches") is False:
                bits.append("number on the photo doesn't match")
                ok = False
    else:
        bits.append("no cert photo")
        ok = False if ok else ok
    text = ", ".join(bits) + ". Transport Canada can't be checked online."
    return {"key": "rpas", "ok": ok, "source": SELF_REPORTED, "text": text}


# ---------------------------------------------------------------------------
# Availability (self-reported). Upgrades the vendor_availability row.
# ---------------------------------------------------------------------------
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_LABEL = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri", "sat": "Sat", "sun": "Sun"}
WINDOWS = {
    "early_morning": ("Early morning", "06:00", "09:00"),
    "daytime": ("Daytime", "09:00", "17:00"),
    "evening": ("Evening", "17:00", "21:00"),
}
NOTICE_CHOICES = (2, 12, 24, 48, 72)


class AvailabilityError(ValueError):
    pass


def normalize_availability(raw: Any, *, today: Optional[date] = None) -> dict:
    """Validate the form's availability block. Raises AvailabilityError."""
    if not isinstance(raw, dict):
        raise AvailabilityError("Tell us your availability.")
    days = [d for d in DAYS if d in {str(x).lower()[:3] for x in (raw.get("days_of_week") or [])}]
    weekends = bool(raw.get("weekends"))
    if weekends:
        for d in ("sat", "sun"):
            if d not in days:
                days.append(d)
    else:
        days = [d for d in days if d not in ("sat", "sun")]
    days = [d for d in DAYS if d in days]
    windows = [w for w in WINDOWS if w in {str(x) for x in (raw.get("time_windows") or [])}]
    if not days:
        raise AvailabilityError("Pick the days you can shoot.")
    if not windows:
        raise AvailabilityError("Pick at least one time window.")
    today = today or datetime.now(timezone.utc).date()
    start_raw = str(raw.get("earliest_start_date") or "").strip()[:10]
    try:
        start = date.fromisoformat(start_raw)
    except ValueError:
        raise AvailabilityError("Pick your earliest start date.")
    if start < today - timedelta(days=1) or start > today + timedelta(days=366):
        raise AvailabilityError("Earliest start date must be within the next year.")

    def _int(key, lo, hi, msg):
        try:
            v = int(raw.get(key))
        except (TypeError, ValueError):
            raise AvailabilityError(msg)
        if v < lo or v > hi:
            raise AvailabilityError(msg)
        return v

    per_week = _int("shoots_per_week", 1, 40, "Tell us how many shoots a week you expect to take.")
    per_day = _int("max_shoots_per_day", 1, 8, "Tell us your max shoots per day (1-8).")
    notice = _int("notice_hours", 1, 168, "Tell us how much notice you need.")
    if raw.get("has_car") is None or raw.get("has_licence") is None:
        raise AvailabilityError("Tell us whether you have a car and a driver's licence.")
    starts = [WINDOWS[w][1] for w in windows]
    ends = [WINDOWS[w][2] for w in windows]
    return {
        "days_of_week": days,
        "time_windows": windows,
        "weekends": "sat" in days or "sun" in days,
        "earliest_start_date": start.isoformat(),
        "shoots_per_week": per_week,
        "jobs_per_day": per_day,
        "notice_hours": notice,
        "has_car": bool(raw.get("has_car")),
        "has_licence": bool(raw.get("has_licence")),
        # Existing dispatch gate fields: span of the picked windows.
        "work_start": min(starts),
        "work_end": max(ends),
    }


def _notice_words(h: Optional[int]) -> str:
    if h is None:
        return ""
    if h < 24:
        return f"{h}h notice"
    d = h / 24
    return f"{d:g} day{'s' if d != 1 else ''} notice"


def availability_summary(av: Optional[dict]) -> str:
    """One line for the email and the admin view."""
    if not av or not av.get("days_of_week"):
        return "No availability given."
    days = av.get("days_of_week") or []
    weekdays = [d for d in days if d in DAYS[:5]]
    if len(weekdays) == 5:
        day_txt = "Mon-Fri"
    else:
        day_txt = ", ".join(DAY_LABEL[d] for d in weekdays) if weekdays else "No weekdays"
    if "sat" in days or "sun" in days:
        we = [DAY_LABEL[d] for d in ("sat", "sun") if d in days]
        day_txt += " + " + "/".join(we)
    win = "/".join(WINDOWS[w][0].lower() for w in (av.get("time_windows") or []) if w in WINDOWS)
    parts = [day_txt]
    if win:
        parts.append(win)
    if av.get("shoots_per_week"):
        n = av["shoots_per_week"]
        parts.append(f"about {n} shoot{'s' if n != 1 else ''}/week")
    if av.get("jobs_per_day"):
        parts.append(f"max {av['jobs_per_day']}/day")
    if av.get("notice_hours"):
        parts.append(_notice_words(av.get("notice_hours")))
    if av.get("earliest_start_date"):
        try:
            d = date.fromisoformat(str(av["earliest_start_date"])[:10])
            parts.append(f"starts {d.strftime('%b')} {d.day}")
        except ValueError:
            pass
    car = av.get("has_car")
    lic = av.get("has_licence")
    if car is not None or lic is not None:
        if car and lic:
            parts.append("car + licence")
        elif lic and not car:
            parts.append("licence, no car")
        elif car and not lic:
            parts.append("car, no licence")
        else:
            parts.append("no car or licence")
    return " · ".join(parts)


def limited_availability_reasons(av: Optional[dict], *, today: Optional[date] = None) -> list:
    if not av or not av.get("days_of_week"):
        return []
    today = today or datetime.now(timezone.utc).date()
    reasons = []
    days = av.get("days_of_week") or []
    if len(days) < 3:
        reasons.append("fewer than 3 days a week")
    if (av.get("shoots_per_week") or 0) < 3:
        reasons.append("under 3 shoots a week")
    if "daytime" not in (av.get("time_windows") or []):
        reasons.append("no daytime window")
    if (av.get("notice_hours") or 0) >= 48:
        reasons.append("needs 2+ days notice")
    try:
        start = date.fromisoformat(str(av.get("earliest_start_date"))[:10])
        if start > today + timedelta(days=30):
            reasons.append(f"can't start until {start.strftime('%b')} {start.day}")
    except (TypeError, ValueError):
        pass
    if av.get("has_car") is False or av.get("has_licence") is False:
        reasons.append("no car or licence for GTA travel")
    return reasons


def availability_evidence(av: Optional[dict], *, today: Optional[date] = None) -> dict:
    if not av or not av.get("days_of_week"):
        return {"key": "availability", "ok": None, "source": COULDNT_CHECK, "text": "No availability on file (applied before the form asked)."}
    reasons = limited_availability_reasons(av, today=today)
    if reasons:
        return {"key": "availability", "ok": False, "source": SELF_REPORTED, "limited": True,
                "text": "Limited availability: " + ", ".join(reasons) + "."}
    return {"key": "availability", "ok": True, "source": SELF_REPORTED, "limited": False,
            "text": "Availability: " + availability_summary(av) + "."}
