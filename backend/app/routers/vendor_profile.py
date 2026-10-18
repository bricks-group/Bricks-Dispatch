"""Photographer profile + payout display for GO and OS dispatch.

GET/PUT  /api/profile
POST     /api/profile/rpas   (multipart file → file URL on the member)
GET      /api/payouts        (Owner: full package, $0 deduction; IC: rate card)
Also mounted under /api/vendor-os/profile for the web desk.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, File, Form, Header, UploadFile
from pydantic import BaseModel

from ..shared.auth import get_vendor_member, resolve_vendor_actor_id
from ..shared.database import get_db
from ..shared.errors import api_error
from ..shared.vendor_identity import (
    VENDOR_SKILLS,
    is_owner_member,
    member_title,
    owner_seed_fields,
    photographer_payout_cents,
    public_profile,
    rack_dollars,
)

router = APIRouter(tags=["vendor-profile"])
os_router = APIRouter(prefix="/api/vendor-os", tags=["vendor-os-profile"])


class ProfileUpdateBody(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    availability: Optional[bool] = None
    skills: Optional[List[str]] = None
    service_areas: Optional[List[str]] = None
    home_city: Optional[str] = None
    max_travel_km: Optional[int] = None
    bio: Optional[str] = None
    rpas_certificate_level: Optional[str] = None
    rpas_certificate_number: Optional[str] = None


async def _actor_member(authorization: Optional[str], x_user_id: Optional[str]) -> dict:
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    member = await get_vendor_member(actor_id)
    return member


def _maybe_single_row(res) -> dict:
    """postgrest maybe_single().execute() is None when there is no row.

    Reading .data on that None is AttributeError. The global handler turns it
    into HTTP 500 with no CORS, so the browser shows net::ERR_FAILED instead
    of the profile fallback (member status / empty row).
    """
    data = getattr(res, "data", None) if res is not None else None
    return data if isinstance(data, dict) else {}


async def _persist_safe(db, member_id: str, patch: dict) -> dict:
    """Write fields that exist; drop unknown columns if PostgREST errors."""
    if not patch:
        res = await db.table("vendor_members").select("*").eq("id", member_id).maybe_single().execute()
        return _maybe_single_row(res)
    patch = {k: v for k, v in patch.items() if v is not None}
    patch["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        res = await db.table("vendor_members").update(patch).eq("id", member_id).execute()
        if res is not None and res.data:
            return res.data[0]
    except Exception:
        optional = {
            "title", "skills", "payout_mode", "rpas_certificate_url", "rpas_certificate_level",
            "rpas_certificate_number", "bio", "avatar_url", "service_areas", "home_city",
        }
        slim = {k: v for k, v in patch.items() if k not in optional}
        if slim:
            res = await db.table("vendor_members").update(slim).eq("id", member_id).execute()
            if res is not None and res.data:
                return res.data[0]
    res = await db.table("vendor_members").select("*").eq("id", member_id).maybe_single().execute()
    return _maybe_single_row(res)


async def _seed_owner(db, member: dict) -> dict:
    patch = owner_seed_fields(member)
    if not patch:
        return member
    # Empty re-read must not drop the authenticated member (KeyError on "id").
    return (await _persist_safe(db, member["id"], patch)) or {**member, **patch}


async def _availability_flag(db, member: dict) -> bool:
    # maybe_single() is None when the member has no vendor_availability row yet
    # (every new GO photographer). None.data was a CORS-less 500 on every
    # GET /api/vendor-os/profile before the member-status fallback could run.
    res = await db.table("vendor_availability").select("available").eq(
        "member_id", member["id"]
    ).maybe_single().execute()
    row = _maybe_single_row(res)
    if "available" in row:
        return bool(row.get("available"))
    return (member.get("status") or "") == "available"


async def build_profile(member: dict) -> dict:
    db = await get_db()
    from ..services.commercial_jobs import refresh_company_policy
    await refresh_company_policy(db)
    member = await _seed_owner(db, member)
    if not member.get("rpas_certificate_url"):
        try:
            docs = await db.table("vendor_documents").select("file_url").eq(
                "member_id", member["id"]
            ).eq("doc_type", "rpas_certificate").order("created_at", desc=True).limit(1).execute()
            if docs.data:
                member = {**member, "rpas_certificate_url": docs.data[0].get("file_url") or ""}
        except Exception:
            pass
    avail = await _availability_flag(db, member)
    portfolio_docs = []
    coi_docs = []
    try:
        docs = await db.table("vendor_documents").select(
            "id, doc_type, file_url, status, created_at"
        ).eq("member_id", member["id"]).execute()
        portfolio_docs = [
            d for d in (docs.data or [])
            if str(d.get("doc_type") or "").startswith("portfolio_")
        ]
    except Exception:
        portfolio_docs = []
    try:
        # Separate read so a column problem here never hides the portfolio.
        res = await db.table("vendor_documents").select(
            "id, status, created_at, expiry_date, validation_result"
        ).eq("member_id", member["id"]).eq("doc_type", "coi").order(
            "created_at", desc=True
        ).limit(5).execute()
        coi_docs = res.data or []
    except Exception:
        coi_docs = []
    extra = {
        "availability": avail,
        "bio": member.get("bio") or member.get("internal_notes") or "",
        "_portfolio_documents": portfolio_docs,
        "insurance_review": latest_coi_review(coi_docs),
    }
    return public_profile(member, extra)


def latest_coi_review(docs: list) -> Optional[dict]:
    """The vendor's most recent certificate-of-insurance upload, for GO.

    Lets the photographer see "In review" / "Not accepted" after a reload
    (the verified state itself comes from insurance_status on the member).
    Only the vendor's own declared values are echoed; no file key, no
    reviewer notes.
    """
    rows = [d for d in (docs or []) if isinstance(d, dict)]
    if not rows:
        return None
    rows.sort(key=lambda d: str(d.get("created_at") or ""), reverse=True)
    d = rows[0]
    vr = d.get("validation_result")
    declared = (vr.get("declared") or {}) if isinstance(vr, dict) else {}
    status = str(d.get("status") or "pending").lower()
    if status not in ("pending", "verified", "rejected", "flagged"):
        status = "pending"
    try:
        limit = int(declared.get("liability_cad")) if declared.get("liability_cad") is not None else None
    except (TypeError, ValueError):
        limit = None
    return {
        "status": "rejected" if status == "flagged" else status,
        "uploaded_at": d.get("created_at"),
        "expires_on": (str(d.get("expiry_date") or declared.get("expires_on") or "")[:10] or None),
        "liability_cad": limit,
        "aviation_covered": bool(declared.get("aviation_covered")),
        "insurer": declared.get("insurer") or None,
    }


async def get_profile_impl(
    authorization: Optional[str],
    x_user_id: Optional[str],
):
    member = await _actor_member(authorization, x_user_id)
    return await build_profile(member)


async def update_profile_impl(
    payload: ProfileUpdateBody,
    authorization: Optional[str],
    x_user_id: Optional[str],
):
    member = await _actor_member(authorization, x_user_id)
    db = await get_db()
    member = await _seed_owner(db, member)
    patch: dict = {}
    if payload.name is not None:
        patch["name"] = payload.name.strip()
    if payload.phone is not None:
        patch["phone"] = payload.phone.strip()
    if payload.home_city is not None:
        patch["home_city"] = payload.home_city.strip()
    if payload.max_travel_km is not None:
        patch["max_travel_km"] = int(payload.max_travel_km)
    if payload.service_areas is not None:
        patch["service_areas"] = [s.strip() for s in payload.service_areas if s and s.strip()]
    if payload.skills is not None:
        patch["skills"] = [s for s in payload.skills if s in VENDOR_SKILLS]
        # "mapping" is admin-granted after a QC'd test mission; a self-serve
        # profile save keeps it but can never add it.
        if "mapping" in (member.get("skills") or []):
            patch["skills"].append("mapping")
        if "drone" in patch["skills"] and not member.get("drone_certified"):
            patch["drone_certified"] = True
    if payload.rpas_certificate_level is not None:
        level = payload.rpas_certificate_level.strip().lower()
        if level not in ("basic", "advanced"):
            api_error(400, "RPAS level must be basic or advanced", "invalid_rpas_level")
        patch["rpas_certificate_level"] = level
        if level == "advanced":
            patch["drone_certified"] = True
    if payload.rpas_certificate_number is not None:
        patch["rpas_certificate_number"] = payload.rpas_certificate_number.strip()
    if payload.bio is not None:
        patch["bio"] = payload.bio
        patch["internal_notes"] = payload.bio
    if payload.availability is not None:
        patch["status"] = "available" if payload.availability else "offline"
        now = datetime.now(timezone.utc).isoformat()
        await db.table("vendor_availability").upsert({
            "vendor_id": member.get("vendor_id", "default_vendor"),
            "member_id": member["id"],
            "available": payload.availability,
            "updated_at": now,
        }).execute()
    # Owner title is locked — ICs cannot self-promote.
    if is_owner_member(member):
        patch["title"] = "Owner"
        patch["role"] = "Owner"
        patch["tier"] = 3
        patch["payout_mode"] = "owner_full"
    updated = (await _persist_safe(db, member["id"], patch)) or {**member, **patch}
    return await build_profile(updated)


async def upload_rpas_impl(
    authorization: Optional[str],
    x_user_id: Optional[str],
    file: UploadFile,
    level: str = "advanced",
    certificate_number: str = "",
):
    member = await _actor_member(authorization, x_user_id)
    raw = await file.read()
    if not raw:
        api_error(400, "Empty file", "empty_file")
    if len(raw) > 12 * 1024 * 1024:
        api_error(400, "File too large (12 MB max)", "file_too_large")
    filename = os.path.basename(file.filename or "rpas-certificate.pdf").lstrip(".") or "rpas-certificate.pdf"
    mime = file.content_type or "application/pdf"
    from ..services.storage import upload_file
    file_key, _, _, _ = await upload_file(raw, "onboarding", member["id"], filename, mime)
    db = await get_db()
    doc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.table("vendor_documents").insert({
        "id": doc_id,
        "vendor_id": member.get("vendor_id", "default_vendor"),
        "member_id": member["id"],
        "doc_type": "rpas_certificate",
        "file_url": file_key,
        "status": "pending",
        "created_at": now,
    }).execute()
    level = (level or "advanced").strip().lower()
    if level not in ("basic", "advanced"):
        level = "advanced"
    rpas_patch = {
        "rpas_certificate_url": file_key,
        "rpas_certificate_level": level,
        "drone_certified": True,
    }
    if (certificate_number or "").strip():
        rpas_patch["rpas_certificate_number"] = certificate_number.strip()
    updated = (await _persist_safe(db, member["id"], rpas_patch)) or {**member, **rpas_patch}
    profile = await build_profile(updated)
    profile["rpas_document_id"] = doc_id
    profile["rpas_certificate_url"] = file_key
    return profile


async def upload_insurance_impl(
    authorization: Optional[str],
    x_user_id: Optional[str],
    file: UploadFile,
    liability_cad: int,
    expires_on: str,
    aviation_covered: bool = False,
    insurer: str = "",
    policy_number: str = "",
):
    """Vendor uploads a certificate of insurance (COI) for commercial work.

    Stored as a pending vendor_documents row with the vendor's declared limit,
    expiry and drone/aviation coverage. Nothing here marks the member verified:
    an admin verifies in OS hiring (or Dispatch admin_ops), which is what the
    commercial job gate and Tier A rule read. A pending upload never removes an
    existing verified COI.
    """
    from datetime import date as _date

    member = await _actor_member(authorization, x_user_id)
    raw = await file.read()
    if not raw:
        api_error(400, "Empty file", "empty_file")
    if len(raw) > 12 * 1024 * 1024:
        api_error(400, "File too large (12 MB max)", "file_too_large")
    try:
        exp = _date.fromisoformat((expires_on or "").strip()[:10])
    except ValueError:
        api_error(400, "expires_on must be YYYY-MM-DD", "invalid_expiry")
    if exp < datetime.now(timezone.utc).date():
        api_error(400, "This certificate has already expired", "insurance_expired")
    try:
        limit = int(liability_cad)
    except (TypeError, ValueError):
        api_error(400, "liability_cad must be a whole number of dollars", "invalid_liability")
    if limit <= 0:
        api_error(400, "liability_cad must be positive", "invalid_liability")
    filename = os.path.basename(file.filename or "certificate-of-insurance.pdf").lstrip(".") or "certificate-of-insurance.pdf"
    mime = file.content_type or "application/pdf"
    from ..services.storage import upload_file
    file_key, _, _, _ = await upload_file(raw, "onboarding", member["id"], filename, mime)
    db = await get_db()
    doc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    declared = {
        "declared": {
            "liability_cad": limit,
            "expires_on": exp.isoformat(),
            "aviation_covered": bool(aviation_covered),
            "insurer": (insurer or "").strip()[:120] or None,
            "policy_number": (policy_number or "").strip()[:80] or None,
        }
    }
    await db.table("vendor_documents").insert({
        "id": doc_id,
        "vendor_id": member.get("vendor_id", "default_vendor"),
        "member_id": member["id"],
        "doc_type": "coi",
        "file_url": file_key,
        "status": "pending",
        "expiry_date": exp.isoformat(),
        "validation_result": declared,
        "created_at": now,
    }).execute()
    patch = {}
    if (policy_number or "").strip():
        patch["insurance_policy_number"] = policy_number.strip()[:80]
    updated = (await _persist_safe(db, member["id"], patch)) if patch else member
    profile = await build_profile(updated or member)
    profile["insurance_document_id"] = doc_id
    profile["insurance_pending_review"] = True
    profile["insurance_review"] = {
        "status": "pending",
        "uploaded_at": now,
        "expires_on": exp.isoformat(),
        "liability_cad": limit,
        "aviation_covered": bool(aviation_covered),
        "insurer": declared["declared"]["insurer"],
    }
    return profile


async def earnings_impl(
    authorization: Optional[str],
    x_user_id: Optional[str],
):
    member = await _actor_member(authorization, x_user_id)
    db = await get_db()
    member = await _seed_owner(db, member)
    owner = is_owner_member(member)
    now = datetime.now(timezone.utc)
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)

    res_jobs = await db.table("jobs").select("*").eq(
        "assignee_id", member["id"]
    ).order("scheduled_date", desc=True).limit(50).execute()
    jobs = res_jobs.data or []

    job_ids = [j["id"] for j in jobs if j.get("id")]
    payout_status_map: dict[str, str] = {}
    if job_ids:
        res_payouts = await db.table("payouts").select("job_id, status").in_("job_id", job_ids).execute()
        for p in (res_payouts.data or []):
            if p.get("job_id"):
                payout_status_map[p["job_id"]] = p.get("status", "pending")

    week_total = 0.0
    paid_total = 0.0
    pending_total = 0.0
    recent = []
    week_job_count = 0

    for j in jobs:
        cents = await photographer_payout_cents(db, j, member)
        payout = round(cents / 100, 2)
        rack = rack_dollars(j)
        deduction = 0.0 if owner else 0.0  # ICs are on a rate card, not a cut of rack
        sched = j.get("scheduled_date", "")
        is_this_week = False
        try:
            sched_dt = datetime.fromisoformat(str(sched).replace("Z", "+00:00"))
            if sched_dt.tzinfo is None:
                sched_dt = sched_dt.replace(tzinfo=timezone.utc)
            is_this_week = week_start <= sched_dt < week_end
        except Exception:
            is_this_week = str(sched)[:10] >= week_start.date().isoformat() and str(sched)[:10] < week_end.date().isoformat()

        actual_payout_status = payout_status_map.get(j.get("id", ""), "pending")
        is_paid = actual_payout_status == "paid"
        if is_this_week:
            week_job_count += 1
            week_total += payout
            if is_paid:
                paid_total += payout
            else:
                pending_total += payout

        if len(recent) < 20:
            row = {
                "id": j.get("id"),
                "address": j.get("address", ""),
                "scheduled_date": sched,
                "status": j.get("status", "assigned"),
                "package_name": j.get("package_name"),
                "payout_amount": payout,
                "payout_status": actual_payout_status,
                "payout_model": "owner_full" if owner else "ic_rate_card",
            }
            if owner:
                row["package_amount"] = rack
                row["deduction"] = 0
            recent.append(row)

    return {
        "week_total": round(week_total, 2),
        "paid_amount": round(paid_total, 2),
        "pending_amount": round(pending_total, 2),
        "payout_day": week_end.strftime("%A"),
        "week_job_count": week_job_count,
        "recent_jobs": recent,
        "title": member_title(member),
        "payout_model": "owner_full" if owner else "ic_rate_card",
        "payout_label": "Full package · $0 deduction" if owner else "Fixed rate card",
        "deduction": 0 if owner else None,
        "show_platform_cut": False,
        "pending_trip_fees": [],
        "pending_trip_fees_total": 0,
    }




async def upload_photo_impl(
    authorization: Optional[str],
    x_user_id: Optional[str],
    file: UploadFile,
):
    member = await _actor_member(authorization, x_user_id)
    raw = await file.read()
    if not raw:
        api_error(400, "Empty file", "empty_file")
    if len(raw) > 8 * 1024 * 1024:
        api_error(400, "File too large (8 MB max)", "file_too_large")
    filename = os.path.basename(file.filename or "profile.jpg").lstrip(".") or "profile.jpg"
    mime = file.content_type or "image/jpeg"
    from ..services.storage import upload_file
    file_key, _, _, _ = await upload_file(raw, "onboarding", member["id"], filename, mime)
    db = await get_db()
    updated = (await _persist_safe(db, member["id"], {"avatar_url": file_key})) or {**member, "avatar_url": file_key}
    profile = await build_profile(updated)
    profile["avatar_url"] = file_key
    return profile


# ── HTTP ──────────────────────────────────────────────────────────────────────

@router.get("/api/profile")
async def get_profile(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    return await get_profile_impl(authorization, x_user_id)


@router.put("/api/profile")
@router.patch("/api/profile")
async def update_profile(
    payload: ProfileUpdateBody,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    return await update_profile_impl(payload, authorization, x_user_id)


@router.post("/api/profile/rpas")
async def upload_rpas(
    file: UploadFile = File(...),
    level: str = Form("advanced"),
    certificate_number: str = Form(""),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    return await upload_rpas_impl(authorization, x_user_id, file, level, certificate_number)


@router.post("/api/profile/insurance")
async def upload_insurance(
    file: UploadFile = File(...),
    liability_cad: int = Form(...),
    expires_on: str = Form(...),
    aviation_covered: bool = Form(False),
    insurer: str = Form(""),
    policy_number: str = Form(""),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    return await upload_insurance_impl(
        authorization, x_user_id, file, liability_cad, expires_on,
        aviation_covered, insurer, policy_number,
    )


@router.get("/api/payouts")
async def get_payouts(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    return await earnings_impl(authorization, x_user_id)


@os_router.get("/profile")
async def os_get_profile(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    return await get_profile_impl(authorization, x_user_id)


@os_router.put("/profile")
@os_router.patch("/profile")
async def os_update_profile(
    payload: ProfileUpdateBody,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    return await update_profile_impl(payload, authorization, x_user_id)


@os_router.post("/profile/rpas")
async def os_upload_rpas(
    file: UploadFile = File(...),
    level: str = Form("advanced"),
    certificate_number: str = Form(""),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    return await upload_rpas_impl(authorization, x_user_id, file, level, certificate_number)

@os_router.post("/profile/insurance")
async def os_upload_insurance(
    file: UploadFile = File(...),
    liability_cad: int = Form(...),
    expires_on: str = Form(...),
    aviation_covered: bool = Form(False),
    insurer: str = Form(""),
    policy_number: str = Form(""),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    return await upload_insurance_impl(
        authorization, x_user_id, file, liability_cad, expires_on,
        aviation_covered, insurer, policy_number,
    )

@router.post("/api/profile/photo")
async def upload_photo(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    return await upload_photo_impl(authorization, x_user_id, file)


@os_router.post("/profile/photo")
async def os_upload_photo(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    return await upload_photo_impl(authorization, x_user_id, file)

