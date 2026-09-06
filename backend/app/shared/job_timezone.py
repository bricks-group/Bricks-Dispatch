"""Canada-wide property timezone for vendor/admin when-lines.

Resolution order (unknown → America/Toronto, silently):
1. Explicit IANA on the job (timezone / time_zone / tz) — OS intake / Stripe metadata
2. Province / state / postal FSA → IANA
3. Province token in the street address
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_IANA = "America/Toronto"

# Province / territory → IANA. QC uses Eastern (majority of bookings).
PROVINCE_TO_IANA: dict[str, str] = {
    "bc": "America/Vancouver",
    "british columbia": "America/Vancouver",
    "colombie-britannique": "America/Vancouver",
    "ab": "America/Edmonton",
    "alberta": "America/Edmonton",
    "sk": "America/Regina",
    "saskatchewan": "America/Regina",
    "mb": "America/Winnipeg",
    "manitoba": "America/Winnipeg",
    "on": "America/Toronto",
    "ont": "America/Toronto",
    "ontario": "America/Toronto",
    "qc": "America/Toronto",
    "que": "America/Toronto",
    "quebec": "America/Toronto",
    "québec": "America/Toronto",
    "nb": "America/Moncton",
    "new brunswick": "America/Moncton",
    "nouveau-brunswick": "America/Moncton",
    "ns": "America/Halifax",
    "nova scotia": "America/Halifax",
    "nouvelle-écosse": "America/Halifax",
    "pe": "America/Halifax",
    "pei": "America/Halifax",
    "prince edward island": "America/Halifax",
    "île-du-prince-édouard": "America/Halifax",
    "nl": "America/St_Johns",
    "nf": "America/St_Johns",
    "newfoundland": "America/St_Johns",
    "newfoundland and labrador": "America/St_Johns",
    "terre-neuve": "America/St_Johns",
    "yt": "America/Whitehorse",
    "yukon": "America/Whitehorse",
    "nt": "America/Yellowknife",
    "northwest territories": "America/Yellowknife",
    "territoires du nord-ouest": "America/Yellowknife",
    "nu": "America/Iqaluit",
    "nunavut": "America/Iqaluit",
}

# First letter of Canadian postal FSA.
POSTAL_TO_IANA: dict[str, str] = {
    "A": "America/St_Johns",
    "B": "America/Halifax",
    "C": "America/Halifax",
    "E": "America/Moncton",
    "G": "America/Toronto",
    "H": "America/Toronto",
    "J": "America/Toronto",
    "K": "America/Toronto",
    "L": "America/Toronto",
    "M": "America/Toronto",
    "N": "America/Toronto",
    "P": "America/Toronto",
    "R": "America/Winnipeg",
    "S": "America/Regina",
    "T": "America/Edmonton",
    "V": "America/Vancouver",
    "X": "America/Yellowknife",
    "Y": "America/Whitehorse",
}

# Common Stripe / booker aliases → IANA.
_ALIAS_TO_IANA: dict[str, str] = {
    "et": "America/Toronto",
    "est": "America/Toronto",
    "edt": "America/Toronto",
    "eastern": "America/Toronto",
    "toronto": "America/Toronto",
    "ct": "America/Winnipeg",
    "cst": "America/Winnipeg",
    "cdt": "America/Winnipeg",
    "central": "America/Winnipeg",
    "mt": "America/Edmonton",
    "mst": "America/Edmonton",
    "mdt": "America/Edmonton",
    "mountain": "America/Edmonton",
    "pt": "America/Vancouver",
    "pst": "America/Vancouver",
    "pdt": "America/Vancouver",
    "pacific": "America/Vancouver",
    "vancouver": "America/Vancouver",
    "at": "America/Halifax",
    "ast": "America/Halifax",
    "adt": "America/Halifax",
    "atlantic": "America/Halifax",
    "nt": "America/St_Johns",
    "nst": "America/St_Johns",
    "ndt": "America/St_Johns",
    "newfoundland": "America/St_Johns",
}

_STD_TO_LETTER = {
    "EST": "ET",
    "EDT": "ET",
    "CST": "CT",
    "CDT": "CT",
    "MST": "MT",
    "MDT": "MT",
    "PST": "PT",
    "PDT": "PT",
    "AST": "AT",
    "ADT": "AT",
    "NST": "NT",
    "NDT": "NT",
}

_IANA_TO_PROVINCE = {
    "America/Vancouver": "BC",
    "America/Edmonton": "AB",
    "America/Regina": "SK",
    "America/Winnipeg": "MB",
    "America/Toronto": "ON",
    "America/Moncton": "NB",
    "America/Halifax": "NS",
    "America/St_Johns": "NL",
    "America/Whitehorse": "YT",
    "America/Yellowknife": "NT",
    "America/Iqaluit": "NU",
}


def _norm(raw: Any) -> str:
    return " ".join(str(raw or "").strip().lower().replace("_", " ").split())


def valid_iana(name: str | None) -> Optional[str]:
    s = (name or "").strip()
    if not s:
        return None
    alias = _ALIAS_TO_IANA.get(_norm(s))
    if alias:
        return alias
    try:
        ZoneInfo(s)
        return s
    except (ZoneInfoNotFoundError, KeyError, ValueError):
        return None


def _province_key(raw: Any) -> str:
    return _norm(raw).replace(".", "")


def iana_for_province(raw: Any) -> Optional[str]:
    return PROVINCE_TO_IANA.get(_province_key(raw))


def iana_for_postal(raw: Any) -> Optional[str]:
    s = str(raw or "").strip().upper().replace(" ", "")
    if not s or not s[0].isalpha():
        return None
    return POSTAL_TO_IANA.get(s[0])


def iana_from_address(address: Any) -> Optional[str]:
    text = str(address or "")
    if not text.strip():
        return None
    # Prefer ", ON" / " ON " / trailing province tokens.
    compact = f" {text.replace(',', ' ')} "
    for key, iana in sorted(PROVINCE_TO_IANA.items(), key=lambda kv: -len(kv[0])):
        if f" {key} " in compact.lower() or compact.lower().rstrip().endswith(f" {key}"):
            return iana
    return None


def resolve_job_iana(job: dict | None) -> str:
    """IANA for this job. Never raises. Unknown → America/Toronto."""
    job = job or {}
    for key in ("timezone", "time_zone", "tz"):
        hit = valid_iana(job.get(key))
        if hit:
            return hit
    for key in ("province", "state"):
        hit = iana_for_province(job.get(key))
        if hit:
            return hit
    for key in ("zip_code", "postal_code", "postal"):
        hit = iana_for_postal(job.get(key))
        if hit:
            return hit
    hit = iana_from_address(job.get("address"))
    if hit:
        return hit
    return DEFAULT_IANA


def infer_province_code(job: dict | None) -> str:
    """Two-letter province if we can see one. Empty if unknown."""
    job = job or {}
    for key in ("province", "state"):
        raw = _province_key(job.get(key))
        if raw in PROVINCE_TO_IANA:
            if len(raw) == 2:
                return raw.upper()
            return _IANA_TO_PROVINCE.get(PROVINCE_TO_IANA[raw], "")
    addr_iana = iana_from_address(job.get("address"))
    if addr_iana:
        return _IANA_TO_PROVINCE.get(addr_iana, "")
    postal_iana = iana_for_postal(job.get("zip_code") or job.get("postal_code"))
    if postal_iana:
        return _IANA_TO_PROVINCE.get(postal_iana, "")
    explicit = valid_iana(job.get("timezone") or job.get("time_zone") or job.get("tz"))
    if explicit:
        return _IANA_TO_PROVINCE.get(explicit, "")
    return ""


def zoneinfo_for_job(job: dict | None) -> ZoneInfo:
    try:
        return ZoneInfo(resolve_job_iana(job))
    except (ZoneInfoNotFoundError, KeyError, ValueError):
        return ZoneInfo(DEFAULT_IANA)


def _ref_datetime(job: dict | None, iana: str) -> datetime:
    job = job or {}
    ymd = str(job.get("scheduled_date") or "")[:10]
    hhmm = "12:00"
    for key in ("confirmed_arrive_at", "time_window_start"):
        raw = str(job.get(key) or "")
        if len(raw) >= 5 and raw[2] == ":" and "T" not in raw:
            hhmm = raw[:5]
            break
    try:
        tz = ZoneInfo(iana)
    except (ZoneInfoNotFoundError, KeyError, ValueError):
        tz = ZoneInfo(DEFAULT_IANA)
    if len(ymd) == 10 and ymd[4] == "-":
        try:
            d = date.fromisoformat(ymd)
            hour, minute = int(hhmm[:2]), int(hhmm[3:5])
            return datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz)
        except ValueError:
            pass
    return datetime.now(tz)


def tz_abbrev(job: dict | None = None, iana: str | None = None) -> str:
    """Canadian letter abbrev: PT / MT / CT / ET / AT / NT. Not PST/PDT."""
    zone = valid_iana(iana) or resolve_job_iana(job)
    try:
        std = _ref_datetime(job, zone).strftime("%Z") or ""
    except Exception:
        std = ""
    if std in _STD_TO_LETTER:
        return _STD_TO_LETTER[std]
    cleaned = std.upper().replace(" ", "")
    if cleaned in _STD_TO_LETTER:
        return _STD_TO_LETTER[cleaned]
    # Fallback from zone identity (DST-safe letter, not offset).
    if zone in ("America/Vancouver",):
        return "PT"
    if zone in ("America/Edmonton", "America/Whitehorse", "America/Yellowknife"):
        return "MT"
    if zone in ("America/Winnipeg", "America/Regina"):
        return "CT"
    if zone in ("America/Halifax", "America/Moncton"):
        return "AT"
    if zone in ("America/St_Johns",):
        return "NT"
    return "ET"


def stamp_job_timezone(job: dict | None) -> dict:
    """Fill timezone (and state when blank) for formatters. Does not persist timezone."""
    job = dict(job or {})
    iana = resolve_job_iana(job)
    job["timezone"] = iana
    if not str(job.get("state") or "").strip():
        code = infer_province_code(job)
        if code:
            job["state"] = code
    return job
