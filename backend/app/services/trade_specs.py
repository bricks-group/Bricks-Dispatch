"""Trade registry: one source of truth for every vendor trade (Kam, 2026-11-01).

Loaded from trade_specs.json next to this file. www (src/lib/trades/spec.json) and
OS (backend/common/trade_specs.json) carry byte-identical copies; tests compare them.

`enabled: false` pauses a trade: no applications, hidden from clients, and its
vendors are never assigned (vendor_identity.is_assignable / member_eligible_for_job).
Turning a trade back on is a one-line change to the JSON.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Any, Optional

_PATH = os.path.join(os.path.dirname(__file__), "trade_specs.json")


@lru_cache(maxsize=1)
def registry() -> dict:
    with open(_PATH, encoding="utf-8") as f:
        return json.load(f)


def trades() -> dict:
    return registry()["trades"]


def spec(vendor_type: Optional[str]) -> Optional[dict]:
    return trades().get(str(vendor_type or "").strip().lower())


def trade_enabled(vendor_type: Optional[str]) -> bool:
    """Unknown types count as enabled so legacy rows keep their current behaviour."""
    s = spec(vendor_type)
    return True if s is None else bool(s.get("enabled", True))


def paused_trades() -> list[str]:
    return [k for k, v in trades().items() if not v.get("enabled", True)]


def probation_jobs() -> int:
    return int(registry().get("probation_jobs") or 3)


def bad_rating_max() -> int:
    return int(registry().get("bad_rating_max") or 3)


def doc_label(kind: str) -> str:
    return registry().get("doc_labels", {}).get(kind, kind.replace("_", " ").capitalize())


def trade_page_url(vendor_type: Optional[str]) -> str:
    s = spec(vendor_type) or spec("photographer")
    return "https://www.brxs.ca" + (s.get("apply_path") or "/work-with-bricks")


def _profile(member: dict) -> dict:
    ap = member.get("application_profile")
    if isinstance(ap, str):
        try:
            ap = json.loads(ap)
        except Exception:
            ap = {}
    return ap if isinstance(ap, dict) else {}


def _flags(member: dict) -> set[str]:
    """Conditions that switch on conditional credentials (drone, commercial_drone)."""
    ap = _profile(member)
    tc = ap.get("trade_credentials") if isinstance(ap.get("trade_credentials"), dict) else {}
    out: set[str] = {"always"}
    skills = [str(s).lower() for s in (member.get("skills") or [])]
    if member.get("drone_certified") or "drone" in skills or tc.get("drone"):
        out.add("drone")
    if tc.get("commercial_drone") or ap.get("commercial_drone"):
        out.update({"drone", "commercial_drone"})
    return out


def required_credentials(member: dict, vendor_type: Optional[str] = None) -> list[dict]:
    s = spec(vendor_type or member.get("vendor_type")) or {}
    flags = _flags(member)
    return [c for c in s.get("credentials", []) if c.get("required_when", "always") in flags]


def upload_kinds() -> list[str]:
    """Every doc_type the registry can ask for (for /documents validation)."""
    kinds = {"id_verification", "police_check"}
    for t in trades().values():
        kinds.update(c["kind"] for c in t.get("credentials", []))
        kinds.update(p["kind"] for p in t.get("proof", []) if p["kind"] != "portfolio")
    return sorted(kinds)


def expiring_kinds() -> set[str]:
    return {c["kind"] for t in trades().values() for c in t.get("credentials", []) if c.get("expires")}


def occupied_homes(vendor_type: Optional[str]) -> bool:
    return bool((spec(vendor_type) or {}).get("occupied_homes"))


def identity_rules(vendor_type: Optional[str]) -> dict:
    return dict((spec(vendor_type) or {}).get("identity") or {})


def required_docs(member: dict, vendor_type: Optional[str] = None) -> list[dict]:
    """Every upload the trade requires: credentials, proof of work and identity."""
    vt = vendor_type or member.get("vendor_type")
    s = spec(vt) or {}
    out = [{"kind": c["kind"], "label": c["label"], "group": "credential", "expires": bool(c.get("expires")),
            "min_limit_cents": c.get("min_limit_cents"),
            "min_aggregate_cents": c.get("min_aggregate_cents")} for c in required_credentials(member, vt)]
    for p in s.get("proof", []):
        if p.get("required"):
            out.append({"kind": p["kind"], "label": p["label"], "group": "proof", "min_count": int(p.get("min_count") or 1)})
    ident = s.get("identity", {})
    if ident.get("gov_id"):
        out.append({"kind": "id_verification", "label": doc_label("id_verification"), "group": "identity"})
    if ident.get("police_check"):
        out.append({"kind": "police_check", "label": doc_label("police_check"), "group": "identity"})
    return out


def _parse_day(v: Any) -> Optional[date]:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def credential_states(member: dict, docs: list[dict], today: Optional[date] = None) -> list[dict]:
    """verified / pending / missing / expired for each required document.

    docs: vendor_documents rows (doc_type, status, expiry_date, validation_result).
    COI also counts vendor_members.coi_expires_on / coi_verified for legacy rows.
    """
    today = today or datetime.now(timezone.utc).date()
    out = []
    for req in required_docs(member):
        kind = req["kind"]
        rows = [d for d in docs if str(d.get("doc_type") or "") == kind and str(d.get("status") or "") != "rejected"]
        if kind == "proof_photo" or kind == "before_after":
            rows = rows or [d for d in docs if str(d.get("doc_type") or "").startswith("portfolio_")]
        if kind == "portfolio":
            rows = [d for d in docs if str(d.get("doc_type") or "").startswith("portfolio_")]
            if not rows and (member.get("portfolio_samples") or member.get("portfolio_links")):
                rows = [{"status": "pending"}]
        expiry = None
        if req.get("expires"):
            dates = [_parse_day(d.get("expiry_date")) for d in rows]
            dates = [x for x in dates if x]
            if kind == "coi" and _parse_day(member.get("coi_expires_on")):
                dates.append(_parse_day(member.get("coi_expires_on")))
            expiry = max(dates) if dates else None
        count = len(rows)
        if kind == "coi" and not rows and member.get("coi_verified"):
            count = 1
        need = int(req.get("min_count") or 1)
        if count == 0:
            state = "missing"
        elif req.get("expires") and expiry and expiry < today:
            state = "expired"
        elif req.get("expires") and not expiry:
            state = "missing_expiry"
        elif count < need:
            state = "incomplete"
        elif any(str(d.get("status") or "") == "verified" for d in rows) or (kind == "coi" and member.get("coi_verified")):
            state = "verified"
        else:
            state = "pending"
        out.append({**req, "state": state, "count": count, "expires_on": expiry.isoformat() if expiry else None})
    return out


def expired_required(member: dict, docs: list[dict], today: Optional[date] = None) -> list[dict]:
    return [c for c in credential_states(member, docs, today) if c["state"] == "expired"]


def apply_shapes(vendor_type: Optional[str]) -> list[str]:
    """Company and/or sole contractor shapes the trade accepts (Kam 2026-11-21)."""
    s = spec(vendor_type) or {}
    shapes = s.get("apply_shapes") or ["sole", "company"]
    return [str(x) for x in shapes]


def operator_shape(member: dict) -> str:
    """sole | company from the application. Default sole when unset."""
    ap = _profile(member)
    raw = str(ap.get("operator_shape") or ap.get("applicant_shape") or "").strip().lower()
    if raw in ("company", "sole"):
        return raw
    # Heuristic: a business name with crew/team points at company.
    tc = ap.get("trade_credentials") if isinstance(ap.get("trade_credentials"), dict) else {}
    if tc.get("business_name") and (ap.get("team_size") or 0) not in (None, "", 0, 1, "1"):
        return "company"
    return "sole"


def cgl_min_cents(vendor_type: Optional[str]) -> Optional[int]:
    """Required CGL per-occurrence floor in cents, or None when CGL is not required."""
    for c in (spec(vendor_type) or {}).get("credentials", []):
        if c.get("kind") == "coi" and c.get("required_when", "always") == "always":
            return int(c.get("min_limit_cents") or 0) or None
    return None


def eo_mins(vendor_type: Optional[str]) -> Optional[dict]:
    """E&O floors {min_limit_cents, min_aggregate_cents} or None."""
    for c in (spec(vendor_type) or {}).get("credentials", []):
        if c.get("kind") == "eo" and c.get("required_when", "always") == "always":
            return {
                "min_limit_cents": int(c.get("min_limit_cents") or 0),
                "min_aggregate_cents": int(c.get("min_aggregate_cents") or 0) or None,
            }
    return None


def _person_verified_row(d: dict) -> bool:
    """Admin (person) verified — not AI-extracted / pending."""
    return str(d.get("status") or "") == "verified"


def missing_person_verified(
    member: dict,
    docs: list[dict],
    today: Optional[date] = None,
    vendor_type: Optional[str] = None,
) -> list[dict]:
    """Required docs that are not yet person-verified (or are expired / incomplete)."""
    m = dict(member)
    if vendor_type:
        m["vendor_type"] = vendor_type
    out = []
    for c in credential_states(m, docs, today):
        if c["state"] != "verified":
            out.append(c)
    return out


def all_person_verified(
    member: dict,
    docs: list[dict],
    today: Optional[date] = None,
    vendor_type: Optional[str] = None,
) -> bool:
    return not missing_person_verified(member, docs, today, vendor_type)


def credentials_stamp(member: dict) -> bool:
    """True when application_profile records that every required doc was person-verified."""
    ap = _profile(member)
    return bool(ap.get("credentials_person_verified"))


def set_credentials_stamp(profile: dict, ok: bool, at: Optional[str] = None) -> dict:
    """Return a shallow-copied application_profile with the credentials stamp set or cleared."""
    ap = dict(profile or {})
    if ok:
        ap["credentials_person_verified"] = True
        if at:
            ap["credentials_person_verified_at"] = at
    else:
        ap.pop("credentials_person_verified", None)
        ap.pop("credentials_person_verified_at", None)
    return ap
