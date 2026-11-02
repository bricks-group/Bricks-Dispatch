"""Rules-based applicant review for non-photographer trades (Kam, 2026-11-01).

Photographers keep the sample-photo rubric in applicant_review.run_review.
Other trades are scored from trade_specs.json `rubric` weights using what
they uploaded (credentials, proof of work, ID) and what they told us
(experience, reviews or references, business or HST). Same result shape as
run_review so the owner email and Bricks OS view render it unchanged.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from . import trade_specs as ts

RUBRIC_VERSION = "trade-rules-2026-11-01"
GREEN, YELLOW, RED, GREY = "green", "yellow", "red", "grey"
RECOMMEND, NEEDS_REVIEW, NOT_A_FIT = "recommend", "needs_review", "not_a_fit"
VERDICT_LABEL = {RECOMMEND: "Recommend", NEEDS_REVIEW: "Needs review", NOT_A_FIT: "Not a fit"}
PART_LABEL = {"proof": "Proof of work", "credentials": "Credentials", "identity": "Identity",
              "experience": "Experience", "completeness": "Completeness"}
INCOMPLETE_SCORE_CAP = 60
RECOMMEND_MIN_SCORE = 75
NOT_A_FIT_MAX_SCORE = 45


def _status(v: float) -> str:
    return GREEN if v >= 0.8 else YELLOW if v >= 0.5 else RED


def _years(creds: dict) -> Optional[float]:
    try:
        return float(str(creds.get("years_experience") or "").split()[0])
    except (ValueError, IndexError):
        return None


def run_trade_review(member: dict, docs: list[dict], today: Optional[date] = None) -> dict:
    vt = (member.get("vendor_type") or (member.get("application_profile") or {}).get("trade") or "").strip().lower()
    spec = ts.spec(vt) or {}
    label = spec.get("label") or vt.capitalize()
    prof = member.get("application_profile") if isinstance(member.get("application_profile"), dict) else {}
    creds = prof.get("credentials") if isinstance(prof.get("credentials"), dict) else {}
    vet = prof.get("vetting") if isinstance(prof.get("vetting"), dict) else {}
    states = ts.credential_states(member, docs or [], today)
    by_group: dict[str, list[dict]] = {}
    for c in states:
        by_group.setdefault(c["group"], []).append(c)

    evidence, flags, missing = [], [], []

    def frac(group: str) -> Optional[float]:
        rows = by_group.get(group) or []
        if not rows:
            return None
        ok = 0.0
        for c in rows:
            if c["state"] in ("verified", "pending"):
                ok += 1
            elif c["state"] == "incomplete":
                ok += min(c["count"] / max(int(c.get("min_count") or 1), 1), 1.0)
        return ok / len(rows)

    for c in states:
        if c["state"] == "expired":
            flags.append({"source": "verified", "text": f"{c['label']} expired on {c['expires_on']}."})
        elif c["state"] in ("missing", "missing_expiry", "incomplete"):
            missing.append(c["label"] + (" (expiry date)" if c["state"] == "missing_expiry" else ""))
        else:
            evidence.append({"source": "verified" if c["state"] == "verified" else "self_reported",
                             "text": f"{c['label']} uploaded" + (f", expires {c['expires_on']}" if c.get("expires_on") else "") + "."})

    values: dict[str, float] = {}
    for g, key in (("proof", "proof"), ("credential", "credentials"), ("identity", "identity")):
        f = frac(g)
        values[key] = 1.0 if f is None else f
    if any(c["state"] == "expired" for c in by_group.get("credential", [])):
        values["credentials"] = min(values["credentials"], 0.25)

    yrs = _years(creds)
    refs = vet.get("references") or []
    exp = 0.0
    if yrs is not None:
        exp += 0.6 if yrs >= 3 else 0.4 if yrs >= 1 else 0.2
        evidence.append({"source": "self_reported", "text": f"{yrs:g} years of experience."})
    if vet.get("reviews_link"):
        exp += 0.4
        evidence.append({"source": "self_reported", "text": "Reviews link: " + vet["reviews_link"]})
    elif len(refs) >= 2:
        exp += 0.4
        evidence.append({"source": "self_reported", "text": f"{len(refs)} references given."})
    elif ts.occupied_homes(vt):
        missing.append("a reviews link or two references")
    values["experience"] = min(exp, 1.0)

    have = [bool(creds.get("business_name") or member.get("hst_number")), bool(member.get("phone")),
            bool(member.get("home_city") or (prof.get("location") or {}).get("city"))]
    values["completeness"] = sum(have) / len(have)
    if not have[0]:
        missing.append("business name or HST number")

    weights = {k: int(v) for k, v in (spec.get("rubric") or {}).items() if k in PART_LABEL}
    total_w = sum(weights.values()) or 1
    parts, score_f = {}, 0.0
    for k, w in weights.items():
        v = values.get(k, 0.0)
        pts = round(v * w * 100 / total_w)
        parts[k] = {"value": round(v, 2), "status": _status(v), "label": PART_LABEL[k], "points": pts,
                    "max_points": round(w * 100 / total_w)}
        score_f += v * w * 100 / total_w
    score = int(round(score_f))

    id_missing = any(c["kind"] == "id_verification" and c["state"] == "missing" for c in states)
    incomplete = bool(missing) and (id_missing or values["credentials"] < 1.0 or values["proof"] < 1.0)
    if incomplete:
        score = min(score, INCOMPLETE_SCORE_CAP)
    reasons = []
    if flags:
        verdict = NOT_A_FIT if values["credentials"] <= 0.25 and score <= NOT_A_FIT_MAX_SCORE else NEEDS_REVIEW
        reasons.append("A required credential has expired.")
    elif score >= RECOMMEND_MIN_SCORE and not incomplete and all(p["status"] != RED for p in parts.values()):
        verdict = RECOMMEND
        reasons.append(f"Credentials, ID and proof of work are in for {label.lower()}.")
    elif score <= NOT_A_FIT_MAX_SCORE and not incomplete:
        verdict = NOT_A_FIT
        reasons.append("Weak on most parts of the rubric.")
    else:
        verdict = NEEDS_REVIEW
        if incomplete:
            reasons.append("Waiting on documents: " + ", ".join(missing[:3]) + ".")
    colour = {RECOMMEND: GREEN, NEEDS_REVIEW: YELLOW, NOT_A_FIT: RED}[verdict]
    if incomplete and verdict != NOT_A_FIT:
        colour = GREY
    return {
        "version": 1, "trade": vt, "trade_label": label, "rubric_version": RUBRIC_VERSION,
        "verdict": verdict, "verdict_label": VERDICT_LABEL[verdict], "verdict_reasons": reasons,
        "resume": None, "colour": colour, "incomplete": incomplete, "needs_samples": False,
        "score": score, "score_cap": INCOMPLETE_SCORE_CAP if incomplete else None,
        "parts": parts, "weights": weights, "criteria": {}, "evidence": evidence, "flags": flags,
        "missing": missing, "images": [], "thumbs": [], "availability_summary": None,
        "model": None, "vision_ran": False, "cost_usd": 0.0,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
