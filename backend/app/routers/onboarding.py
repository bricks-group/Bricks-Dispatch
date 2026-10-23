from datetime import datetime, timezone
import logging
import os
import secrets
import traceback
import uuid
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, HTTPException, Form, Request

logger = logging.getLogger(__name__)

import re

from ..services.storage import upload_file
from ..services.notifications import create_notification
from ..shared.database import get_db, maybe_single_row
from ..shared.applicant_source import ApplicantSourceError, found_us_line, normalize_applicant_source
from ..shared.schemas import AvailabilitySettings, SampleUploadRequest, VendorApplicationCreate, VendorMember, VendorDocument
from ..shared.portfolio import (
    PORTFOLIO_MEDIA_TYPES,
    assemble_portfolio_samples,
    normalize_media_type,
    portfolio_doc_type,
    sanitize_portfolio_samples,
    supplemental_links_from_samples,
)

ADMIN_NOTIFICATION_EMAIL = os.environ.get("ADMIN_NOTIFICATION_EMAIL", "")

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

# Version tracks the text revision so we can re-prompt if terms materially change.
_IC_AGREEMENT_VERSION = "1.8"
_IC_AGREEMENT_EFFECTIVE_DATE = "2026-10-03"
_IC_AGREEMENT_TEXT = """
BRICKS INDEPENDENT CONTRACTOR AGREEMENT  v1.8
Effective: October 3, 2026
Contracting party: Bricks Group ("Bricks")
Governing law: Ontario, Canada

By submitting an application, checking the acceptance box, or accepting a booking through the Bricks platform, you ("Contractor") agree to these terms.

This agreement is for independent-contractor services. It is not employment. Nothing in this agreement waives statutory rights that cannot be waived. If a court or tribunal finds employment or dependent-contractor status, those statutory rights apply to that finding and the remaining terms continue.

1. RELATIONSHIP
You are an independent contractor, not an employee, agent, partner, or joint venturer of Bricks. You control the means, methods, and manner of your work. Bricks may specify deliverable outcomes (shot count, resolution, file formats, turnaround, shoot window) without directing how you achieve them. You set your own availability, use your own equipment, and may work for other clients including competitors. Bricks does not guarantee a minimum volume of work. You have no authority to bind Bricks.

2. RIGHT TO REFUSE
You may decline any job offer for any reason. Declining a job is not a breach. Routing may still consider your availability and history. You are not required to accept a minimum number of jobs.

3. ASSISTANTS AND SUBCONTRACTORS
You may hire assistants or second shooters at your own expense. You remain fully responsible for their work, insurance, taxes, conduct on site, and any claim arising from them. You may not assign this agreement without Bricks' written consent.

4. EQUIPMENT
You provide and maintain your own camera, lenses, drone, lighting, vehicle, editing tools, and all other equipment. Bricks provides no equipment and is not responsible for loss, damage, theft, or repair.

5. TAXES
Fees are exclusive of HST/GST. You are solely responsible for income tax, HST/GST (if you are required to register), and any CPP that applies to you as self-employed. If your taxable supplies exceed the CRA small-supplier threshold, you must register, give Bricks your HST number, and Bricks will pay HST on your fees. Bricks will issue a T4A (Box 048) when required. Bricks does not remit EI or employer CPP for you and does not provide employment benefits.

6. INSURANCE AND WSIB
Insurance and WSIB proof are not required to apply, and are not required before a first dispatched job. Bricks does not require a certificate of insurance, additional-insured status, a renewal certificate, commercial general liability, aviation liability, automobile liability, or a WSIB clearance or exemption. Missing or lapsed coverage is not suspension, refusal, or cancellation of dispatch.

You remain an independent contractor, responsible for your own equipment (section 4) and your own taxes (section 5). Drone and RPAS duties in section 7 are unchanged.

7. DRONE
If you operate a drone you represent that you hold a valid Transport Canada RPAS certificate at the level required for that operation (Advanced for controlled airspace, including proximity to CYYZ and CYTZ). You comply with the Aeronautics Act, Canadian Aviation Regulations, and any NAV CANADA or site restriction. You are solely responsible for the operation, including fines, incidents, and third-party claims. If airspace, weather, or regulation prevents aerial capture, you still complete the ground package and note the miss in the job.

8. PAYOUTS
You are paid a fixed amount per accepted booking, set by Bricks' vendor rate card for the media package, client footprint band, and your vendor tier at the time you accept the job. That amount does not change based on what the client was charged or any client discount. Client media on www.brxs.ca is sold à la carte as Photos only, Essentials, Story, Showcase, and Full suite. Virtual Visit (free-roam Gaussian splat) is always a paid add-on — never packaged into a suite and never treated as included deliverable pay. Path-confined Virtual Tour may be included in Showcase and Full suite or sold as an add-on. Add-ons (drone, cinematic reel, Virtual Tour, twilight, Virtual Visit, and similar) are paid at the published add-on rate card for your tier — fixed dollars, not % of rack. Bricks may change the rate card prospectively on 14 days' notice; changes do not apply to jobs already accepted.

Current media rate card (CAD, exclusive of HST), shown as Tier C / Tier B / Tier A. Footprint bands: Small ≤1,200 sqft; Mid 1,201–2,500; Large 2,501–4,000; XL 4,001+.

Photos only — Small (≤1,200 sqft) $55.15 / $62.60 / $71.50; Mid (1,201–2,500 sqft) $73.65 / $83.60 / $95.50; Large (2,501–4,000 sqft) $92.15 / $104.60 / $119.50; XL (4,001+ sqft) $110.65 / $125.60 / $143.50
Essentials — Small (≤1,200 sqft) $91.75 / $104.15 / $119.05; Mid (1,201–2,500 sqft) $117.65 / $133.55 / $152.65; Large (2,501–4,000 sqft) $143.55 / $162.95 / $186.25; XL (4,001+ sqft) $169.45 / $192.35 / $219.85
Story — Small (≤1,200 sqft) $146.90 / $166.75 / $190.55; Mid (1,201–2,500 sqft) $183.90 / $208.75 / $238.55; Large (2,501–4,000 sqft) $220.90 / $250.75 / $286.55; XL (4,001+ sqft) $261.60 / $296.95 / $339.35
Showcase — Small (≤1,200 sqft) $202.00 / $229.30 / $262.10; Mid (1,201–2,500 sqft) $257.50 / $292.30 / $334.10; Large (2,501–4,000 sqft) $313.00 / $355.30 / $406.10; XL (4,001+ sqft) $372.20 / $422.50 / $482.90
Full suite — Small (≤1,200 sqft) $257.50 / $292.30 / $334.10; Mid (1,201–2,500 sqft) $320.40 / $363.70 / $415.70; Large (2,501–4,000 sqft) $381.45 / $433.00 / $494.90; XL (4,001+ sqft) $446.20 / $506.50 / $578.90

Add-on payouts are fixed published dollar amounts from the add-on rate card for your tier — fixed dollars, not a percentage of rack.

Virtual Visit — Small (≤1,200 sqft) $92.15 / $104.60 / $119.50; Mid (1,201–2,500 sqft) $110.65 / $125.60 / $143.50; Large (2,501–4,000 sqft) $129.15 / $146.60 / $167.50; XL (4,001+ sqft) $147.65 / $167.60 / $191.50
Virtual staging — $12.95 / $14.70 / $16.80 per image (Tier C / Tier B / Tier A). Not banded by footprint.

Tier is assigned by Bricks based on quality, reliability, volume, and compliance. Tier C is the starting tier. Bricks may change your tier on notice. Payouts are via Stripe Connect within 7 business days of client acceptance of deliverables, or within 14 days of shoot completion if the client has not acted. Raise payout disputes in writing to raphael@brxs.ca within 30 days of the payment date.

9. INTELLECTUAL PROPERTY
Upon creation (and in any event upon submission) you assign to Bricks all right, title, and interest, including copyright, in the photos, video, floor plans, 3D captures, and other deliverables produced for each Bricks booking (the "Deliverables"). This assignment does not cover your pre-existing portfolio, presets, or work made outside Bricks bookings.

You waive all moral rights in the Deliverables in favour of Bricks and its licensees, to the extent permitted by the Copyright Act (Canada), including the rights of paternity and integrity.

You keep your RAW / unedited source files. For 90 days after delivery you will provide copies to Bricks on request if needed for a re-edit, dispute, or client delivery.

Bricks licenses Deliverables to the booking client for real estate marketing of that property (MLS, web, social, print, brokerage materials). Bricks may also use Deliverables for Bricks' own portfolio, training, and marketing unless the client has asked in writing that the property be kept confidential.

You warrant that the Deliverables are your original work (except stock or tools Bricks supplied), do not infringe third-party rights, and that you have model releases or equivalent permission for any identifiable person you include. You will not photograph occupants, children, documents, or personal effects except as needed for empty-room listing media, and you will not publish shoot-day content without Bricks' or the client's go-ahead.

10. ACCESS, PROPERTY, AND CONDUCT
You represent Bricks on site. You will arrive in the shoot window, use access devices (lockbox codes, keys, fobs) only for the booking, not copy them, and leave the property secured as you found it. You will not enter areas you do not need. You will not use client or occupant personal information except to perform the job. Report damage, injury, safety issues, or denied access to Bricks immediately. Inaccessible property or a client no-show reported in-app may qualify for the published trip fee then in effect.

11. CONFIDENTIALITY AND PERSONAL INFORMATION
Client names, addresses, contact details, access codes, booking notes, payout rates, and Bricks' non-public processes are confidential. You will not use them to market yourself to that client off-platform, except as section 12 allows. You will handle personal information only as needed to perform the job and in line with PIPEDA. This section survives termination.

12. NON-SOLICIT (CLIENTS INTRODUCED BY BRICKS)
For 12 months after your last Bricks job with a client, you will not solicit that client for listing-media services competing with Bricks, where Bricks introduced you to them. This does not stop you working for clients who reach you on their own, or from working for other platforms. There is no non-compete. You may work anywhere in photography and related fields.

13. QUALITY
Deliverables go through the Bricks app within the booking turnaround (standard 24–48 hour target from shoot end unless the booking says otherwise; times are targets, not guarantees) and must meet the Shoot Standards Guide. Bricks may require a complimentary re-shoot if work does not meet those standards. Repeated failures, fraud, or serious complaints may mean suspension, tier change, or termination.

14. INDEMNITY
You will indemnify, defend, and hold harmless Bricks Group and its officers, directors, contractors, and agents from claims, losses, damages, and reasonable legal fees arising out of: (a) your services or those of your assistants; (b) injury or property damage at a job; (c) drone operations; (d) your breach of this agreement or law; (e) alleged infringement by the Deliverables; (f) your tax, WSIB, or employment-status claims against Bricks to the extent they arise from your holding yourself out as an employee contrary to this agreement. Bricks may control the defence of a third-party claim.

15. LIMITATION OF LIABILITY
Bricks is not liable to you for lost jobs, lost profit, or equipment damage. Bricks' total liability to you for a booking is the payout for that booking. Nothing excludes liability that Ontario or Canadian law does not allow to be excluded (including fraud or bodily injury Bricks caused).

16. TERM AND TERMINATION
This agreement starts when you accept it and continues until terminated. Either party may terminate for any reason on written notice (email is enough). Bricks may suspend or terminate immediately for safety or legal risk, fraud, material quality failure, or material breach. Outstanding accepted jobs should be finished or handed off. Earned payouts for completed, accepted work are paid on the normal cycle. Sections 9–15 and 17 survive.

17. DISPUTES AND LAW
Ontario law and applicable Canadian federal law govern. Courts of Ontario have exclusive jurisdiction. Before filing, the parties will try to resolve a dispute in writing within 30 days. You may still make a complaint to the Ministry of Labour or the CRA. Binding arbitration is not required.

18. CHANGES; ENTIRE AGREEMENT
This agreement, the Shoot Standards Guide, and the booking details for each job are the entire agreement for contractor services. Bricks may issue a revised version on at least 14 days' notice. Continued acceptance of jobs after the effective date is acceptance of the revision. You may stop accepting jobs and terminate if you do not agree. Amendments other than a posted revision require written agreement of both parties.

Questions: booking@brxs.ca
Bricks Group · Ontario, Canada
""".strip()


@router.get("/ic-agreement")
async def get_ic_agreement():
    """Return the current Bricks IC agreement text and version number."""
    return {
        "version": _IC_AGREEMENT_VERSION,
        "effective_date": _IC_AGREEMENT_EFFECTIVE_DATE,
        "text": _IC_AGREEMENT_TEXT,
    }


# Throwaway / disposable email domains we will not accept applications from.
# Deliberately NOT gmail/yahoo/outlook/etc — real photographers use those.
_DISPOSABLE_DOMAINS = {
    "t@t.com", "tt.com",
    "temp-mail.org", "tempmail.com", "temp-mail.com", "tempmailo.com",
    "guerrillamail.com", "guerrillamail.info", "guerrillamailblock.com",
    "mailinator.com", "mailinator.net", "mailinator.org",
    "10minutemail.com", "10minutemail.net", "tempmail.co",
    "trashmail.com", "trashmail.net", "getnada.com", "nada.email",
    "yopmail.com", "yopmail.net", "yopmail.org",
    "throwawaymail.com", "sharklasers.com", "dispostable.com",
    "maildrop.cc", "fakeinbox.com", "mailnesia.com", "spam4.me",
    "mintemail.com", "mohmal.com", "emailondeck.com",
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# At least 2 chars, letters / spaces / apostrophes / hyphens / dots only.
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z'’.\- ]{1,}$")


async def _validate_application(application: "VendorApplicationCreate") -> None:
    """Server-side gate. Rejects junk + bots even if they skip the form entirely.

    Raises HTTPException(400) with a single joined message on the first problems
    found. The honeypot (company_website) is a silent trap: any value => reject.
    """
    errs: list[str] = []

    # Honeypot — bots that autofill every field trip this; humans never see it.
    if getattr(application, "company_website", None):
        raise HTTPException(status_code=400, detail="Submission rejected.")

    name = (application.name or "").strip()
    if len(name) < 2 or not _NAME_RE.match(name):
        errs.append("Please enter your full name.")

    email = (application.email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        errs.append("Please enter a valid email address.")
    else:
        domain = email.split("@")[-1]
        if domain in _DISPOSABLE_DOMAINS:
            errs.append("Please use a permanent email address, not a disposable one.")

    phone = (application.phone or "").strip()
    phone_digits = re.sub(r"\D", "", phone)
    if len(phone_digits) < 7:
        errs.append("Please enter a valid phone number (at least 7 digits).")

    city = (application.city or "").strip()
    if len(city) < 2:
        errs.append("Please enter your home city.")

    from ..services import applicant_evidence as ev
    from ..services.vendor_profile import normalize_vendor_type as _nvt
    _vtype_early = _nvt(getattr(application, "vendor_type", None) or "photographer")
    is_photographer = _vtype_early == "photographer"

    structured = application.equipment if isinstance(application.equipment, dict) else None
    if structured is not None:
        eq = {k: " ".join(str(structured.get(k) or "").split())[:160]
              for k in ("camera_body", "lenses", "drone_model", "lighting", "tripod")}
        application.equipment = eq
        if is_photographer:
            if not eq["camera_body"]:
                errs.append("Tell us your camera body.")
            if not eq["lenses"]:
                errs.append("List your lenses.")
            if not eq["tripod"]:
                errs.append("Tell us your tripod (or type 'none').")
        application.equipment_list = ev.equipment_list_from_profile(eq) or application.equipment_list
    equipment = [e.strip() for e in (application.equipment_list or []) if e and e.strip()]
    if len(equipment) == 0:
        errs.append("Please list your equipment.")
    elif len(" ".join(equipment)) < 8:
        errs.append("Please describe your equipment in a little more detail.")

    # Sample photos: 8-15, uploaded to Bricks storage under a signed session.
    session = (application.sample_upload_session or "").strip()
    paths = []
    for raw in application.sample_paths or []:
        p_ = str(raw or "").strip()
        if p_ and p_ not in paths:
            paths.append(p_)
    cert_path = (application.rpas_cert_path or "").strip() or None
    if paths or cert_path:
        if not ev.verify_upload_session(session):
            errs.append("Your photo upload expired. Please re-add your sample photos.")
        elif any(not ev.path_in_session(x, session) for x in paths + ([cert_path] if cert_path else [])):
            errs.append("Sample photos must be uploaded through this form.")
    application.sample_paths = paths
    application.rpas_cert_path = cert_path
    if is_photographer:
        if len(paths) < ev.MIN_SAMPLES:
            errs.append(
                f"Upload {ev.MIN_SAMPLES}-{ev.MAX_SAMPLES} sample real estate photos "
                "(update the Bricks GO app, or apply at brxs.ca/photographers)."
            )
        elif len(paths) > ev.MAX_SAMPLES:
            errs.append(f"Upload at most {ev.MAX_SAMPLES} sample photos.")

    # Drone: a claim needs the number, the level and a photo of the certificate.
    if is_photographer and application.drone_certified:
        lvl = (application.rpas_certificate_level or "").strip().lower()
        if lvl not in ("basic", "advanced"):
            errs.append("Pick your RPAS certificate level (Basic or Advanced).")
        if not (application.rpas_certificate_number or "").strip():
            errs.append("Enter your RPAS pilot certificate number.")
        if not cert_path:
            errs.append("Add a photo of your RPAS pilot certificate.")
        application.rpas_certificate_level = lvl or None

    # Availability (self-reported). Required for photographers on the new form.
    if application.availability is not None or (is_photographer and structured is not None):
        try:
            application.availability = ev.normalize_availability(application.availability)
        except ev.AvailabilityError as exc:
            errs.append(str(exc))

    samples = sanitize_portfolio_samples(
        [s.dict() if hasattr(s, "dict") else s for s in (application.portfolio_samples or [])]
    )
    links = [str(l).strip() for l in (application.portfolio_links or []) if l and str(l).strip()]
    for link in links:
        if not link.lower().startswith(("http://", "https://")):
            errs.append("Portfolio links must be full URLs (starting with https://).")
            break
    for sample in samples:
        link = (sample.get("link") or "").strip()
        if link and not link.lower().startswith(("http://", "https://")):
            errs.append("Portfolio sample links must be full URLs (starting with https://).")
            break
    if not samples and not links and not paths:
        errs.append("Add at least one portfolio sample (image upload or link) by media type.")

    hst = (application.hst_number or "").strip()
    if not hst and not application.hst_exempt:
        errs.append("HST / GST number is required, or confirm the small-supplier exemption (under $30,000 CAD/year).")

    from ..services.vendor_profile import normalize_vendor_type
    vtype = normalize_vendor_type(getattr(application, "vendor_type", None) or "photographer")
    application.vendor_type = vtype
    # Stager / cleaner may not have a media portfolio yet.
    if vtype in ("stager", "cleaner"):
        errs = [e for e in errs if "portfolio" not in e.lower() and "sample" not in e.lower()]

    try:
        application.source, application.source_detail = normalize_applicant_source(
            getattr(application, "source", None), getattr(application, "source_detail", None)
        )
    except ApplicantSourceError as exc:
        errs.append(str(exc))

    if errs:
        raise HTTPException(status_code=400, detail=" ".join(errs))




def _application_portfolio_links(application: "VendorApplicationCreate") -> list:
    """Links already on the application. Uploaded files are included when the URL is public."""
    links = []
    for raw in application.portfolio_links or []:
        links.append(str(raw or "").strip())
    for sample in application.portfolio_samples or []:
        row = sample.dict() if hasattr(sample, "dict") else (sample or {})
        if not isinstance(row, dict):
            continue
        for key in ("link", "url", "thumb_url"):
            links.append(str(row.get(key) or "").strip())
    return [link for link in links if link]

def _applicant_jev_fields(application: "VendorApplicationCreate") -> dict:
    """Fields already stored on the application. No token, email, or phone."""
    samples = []
    for sample in application.portfolio_samples or []:
        row = sample.dict() if hasattr(sample, "dict") else (sample or {})
        if not isinstance(row, dict):
            continue
        samples.append({
            "media_type": row.get("media_type") or "",
            "link": row.get("link") or "",
        })
    links = [str(link).strip() for link in (application.portfolio_links or []) if link and str(link).strip()]
    return {
        "name": application.name,
        "city": application.city,
        "vendor_type": getattr(application, "vendor_type", None) or "photographer",
        "equipment": [item for item in (application.equipment_list or []) if item],
        "portfolio": {"links": links, "samples": samples},
        "drone": bool(application.drone_certified),
    }


async def _store_applicant_jev_probability(member_id: str, existing_notes: str, probability: float) -> None:
    """Append Jev's yes-probability to internal_notes. Never writes onboarding status."""
    line = f"Jev poor-fit yes-probability {probability:.2f}"
    notes = (existing_notes or "").strip()
    updated = f"{notes}; {line}" if notes else line
    payload = {
        "internal_notes": updated,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await (await get_db()).table("vendor_members").update(payload).eq("id", member_id).execute()
    except Exception as exc:
        from ..shared.pg_fallback import _is_rest_quota_error, pg_update_vendor_member_fields

        if _is_rest_quota_error(exc):
            if not pg_update_vendor_member_fields(member_id, payload):
                logger.warning("applicant jev note not stored member=%s", member_id)
        else:
            logger.warning(
                "applicant jev note not stored member=%s (%s)",
                member_id,
                type(exc).__name__,
            )


@router.post("/apply")
async def apply_to_bricks(application: VendorApplicationCreate, background_tasks: BackgroundTasks):
    """
    Wave A vendor application (photographer | stager | cleaner).
    Requires acceptance of the current IC agreement.
    Records ic_agreement_version as the live server version (currently 1.8).
    photographers.html posts vendor_type=photographer; drone remains an RPAS capability.
    """
    if not application.accepted_terms:
        raise HTTPException(status_code=400, detail="You must accept the Bricks Contractor Agreement to apply.")

    # Gate junk/bot submissions before any DB write or notification.
    await _validate_application(application)

    try:
        result = await _apply_to_bricks_inner(application)
    except HTTPException:
        raise
    except Exception:
        logger.error("apply_to_bricks_failed:\n" + traceback.format_exc())
        raise HTTPException(status_code=500, detail="Could not process application. Please try again.")

    existing_notes = result.pop("_internal_notes", "")
    from ..services.vendor_profile import normalize_vendor_type
    vtype = normalize_vendor_type(getattr(application, "vendor_type", None) or "photographer")
    type_label = {"photographer": "photographer", "stager": "stager", "cleaner": "cleaner"}.get(vtype, "vendor")

    # Evidence-based review (samples through the vision rubric, link checks,
    # hashes, gear / location / availability / RPAS rules), stored on
    # vendor_applicant_reviews, then one short text + one HTML email to Kam.
    # Runs after the response; it never approves or rejects.
    from ..services.applicant_review_runner import review_application
    background_tasks.add_task(review_application, result["member_id"], notify=True, trigger="apply")

    found = found_us_line(getattr(application, "source", None), getattr(application, "source_detail", None))
    # In-app record only. Kam's text and admin@brxs.ca email are the notify above.
    # Do not email ADMIN_NOTIFICATION_EMAIL — that env is unset, and this path
    # must not fall through to a personal mailbox.
    background_tasks.add_task(
        create_notification,
        title=f"New {type_label} application",
        body=(
            f"{application.name} applied ({vtype}).\n"
            f"Email: {application.email}\n"
            f"Phone: {application.phone}\n"
            f"City: {application.city}\n"
            f"Specialties: {', '.join(application.specialties) if application.specialties else 'n/a'}\n"
            f"Drone certified: {application.drone_certified}"
            + (f"\n{found}" if found else "")
        ),
        ntype="vendor_application",
        data={"member_id": result["member_id"], "vendor_type": vtype},
    )
    return result


async def _apply_to_bricks_inner(application: VendorApplicationCreate):
    from ..shared.pg_fallback import (
        _is_rest_quota_error,
        pg_insert_vendor_application,
        pg_select_vendor_member,
        pg_upsert_vendor_availability,
    )

    email_l = application.email.lower().strip()
    existing = None
    try:
        res_existing = await (await get_db()).table("vendor_members").select("*").eq(
            "email", email_l
        ).maybe_single().execute()
        existing = maybe_single_row(res_existing) or None
    except Exception as e:
        if _is_rest_quota_error(e):
            existing = pg_select_vendor_member(email=email_l)
        else:
            raise
    if existing:
        raise HTTPException(status_code=400, detail="An application with this email already exists.")

    now_iso = datetime.now(timezone.utc).isoformat()
    application_token = secrets.token_urlsafe(32)
    samples = sanitize_portfolio_samples(
        [s.dict() if hasattr(s, "dict") else s for s in (application.portfolio_samples or [])]
    )
    links = [str(l).strip() for l in (application.portfolio_links or []) if l and str(l).strip()]
    for extra in supplemental_links_from_samples(samples):
        if extra not in links:
            links.append(extra)

    from ..services.vendor_profile import normalize_vendor_type
    vtype = normalize_vendor_type(getattr(application, "vendor_type", None) or "photographer")
    # Photographer apply may omit specialties for stager/cleaner; keep a sensible default.
    default_specialty = {
        "photographer": "photo",
        "stager": "staging",
        "cleaner": "cleaning",
    }.get(vtype, "photo")
    from ..services import applicant_evidence as ev
    application_profile = None
    if vtype == "photographer" or application.sample_paths or application.equipment:
        application_profile = {
            "version": 1,
            "location": {"city": application.city, "in_gta": ev.city_in_gta(application.city)},
            "specialties": list(application.specialties or []),
            "equipment": application.equipment or ev.equipment_from_list(application.equipment_list or []),
            "equipment_structured": bool(application.equipment),
            "rpas": {
                "claimed": bool(application.drone_certified) if vtype == "photographer" else False,
                "number": application.rpas_certificate_number if vtype == "photographer" else None,
                "level": application.rpas_certificate_level if vtype == "photographer" else None,
                "cert_path": application.rpas_cert_path if vtype == "photographer" else None,
            },
            "samples": [{"path": p} for p in (application.sample_paths or [])],
            "upload_session": application.sample_upload_session or None,
        }
    member = VendorMember(
        id=str(uuid.uuid4()),
        application_token=application_token,
        name=application.name,
        email=email_l,
        phone=application.phone,
        role="Applicant",
        vendor_type=vtype,
        specialty=(application.specialties[0] if application.specialties else default_specialty),
        onboarding_status="pending",
        is_active=False,
        portfolio_links=links,
        portfolio_samples=samples,
        equipment_list=application.equipment_list,
        home_city=application.city,
        home_lat=application.home_lat,
        home_lng=application.home_lng,
        hst_number=application.hst_number,
        drone_certified=application.drone_certified if vtype == "photographer" else False,
        rpas_certificate_number=application.rpas_certificate_number if vtype == "photographer" else None,
        rpas_certificate_level=application.rpas_certificate_level if vtype == "photographer" else None,
        max_travel_km=application.max_travel_km,
        operation_polygon=application.operation_polygon,
        service_areas=application.preferred_regions if application.preferred_regions else [application.city],
        ic_agreement_version=_IC_AGREEMENT_VERSION,
        terms_accepted_at=now_iso,
        source=getattr(application, "source", None),
        source_detail=getattr(application, "source_detail", None),
        application_profile=application_profile,
        rpas_certificate_url=application.rpas_cert_path if vtype == "photographer" else None,
        internal_notes=f"IC agreement v{_IC_AGREEMENT_VERSION} accepted on {now_iso}; vendor_type={vtype}",
    )

    member_dict = member.dict()
    # Coordinate models serialize as dicts; portfolio_samples as list[dict].
    if "portfolio_samples" in member_dict and member_dict["portfolio_samples"] is not None:
        member_dict["portfolio_samples"] = [
            s.dict() if hasattr(s, "dict") else s for s in member_dict["portfolio_samples"]
        ]
    if "operation_polygon" in member_dict and member_dict["operation_polygon"] is not None:
        member_dict["operation_polygon"] = [
            c.dict() if hasattr(c, "dict") else c for c in member_dict["operation_polygon"]
        ]

    db = await get_db()
    inserted_via_pg = False
    try:
        await db.table("vendor_members").insert(member_dict).execute()
    except Exception as e:
        if _is_rest_quota_error(e):
            # Canada Fair Use REST often 402 — same PG fallback as payouts/Connect.
            pg_row = pg_insert_vendor_application(member_dict)
            if not pg_row:
                logger.error("apply_to_bricks Fair-Use PG insert failed for %s", email_l)
                raise HTTPException(
                    status_code=503,
                    detail="Could not process application right now. Please try again shortly.",
                )
            inserted_via_pg = True
        else:
            # Soft-drop newer columns on older DBs (portfolio_samples, vendor_type).
            # application_profile is kept: migration 0017 is live on Canada.
            member_dict.pop("portfolio_samples", None)
            try:
                await db.table("vendor_members").insert(member_dict).execute()
            except Exception as e2:
                if _is_rest_quota_error(e2):
                    pg_row = pg_insert_vendor_application(member_dict)
                    if not pg_row:
                        raise HTTPException(
                            status_code=503,
                            detail="Could not process application right now. Please try again shortly.",
                        )
                    inserted_via_pg = True
                else:
                    member_dict.pop("vendor_type", None)
                    try:
                        await db.table("vendor_members").insert(member_dict).execute()
                    except Exception as e3:
                        if _is_rest_quota_error(e3):
                            pg_row = pg_insert_vendor_application(member_dict)
                            if not pg_row:
                                raise HTTPException(
                                    status_code=503,
                                    detail="Could not process application right now. Please try again shortly.",
                                )
                            inserted_via_pg = True
                        else:
                            raise

    availability = AvailabilitySettings(
        member_id=member.id,
        vendor_id=member.vendor_id,
        available=True
    )
    avail_dict = availability.dict()
    if isinstance(application.availability, dict) and application.availability.get("days_of_week"):
        av = application.availability
        avail_dict.update({
            "work_start": av["work_start"],
            "work_end": av["work_end"],
            "jobs_per_day": av["jobs_per_day"],
            "days_of_week": av["days_of_week"],
            "time_windows": av["time_windows"],
            "weekends": av["weekends"],
            "earliest_start_date": av["earliest_start_date"],
            "shoots_per_week": av["shoots_per_week"],
            "notice_hours": av["notice_hours"],
            "has_car": av["has_car"],
            "has_licence": av["has_licence"],
        })
    try:
        await (await get_db()).table("vendor_availability").insert(avail_dict).execute()
    except Exception as e:
        if _is_rest_quota_error(e) or inserted_via_pg:
            if not pg_upsert_vendor_availability(avail_dict):
                logger.warning(
                    "apply_to_bricks: availability PG upsert failed member=%s (non-fatal)",
                    member.id,
                )
        else:
            # Non-quota insert failure: try PG anyway if REST flaked; else log.
            if not pg_upsert_vendor_availability(avail_dict):
                logger.warning(
                    "apply_to_bricks: availability insert failed member=%s: %s",
                    member.id,
                    type(e).__name__,
                )

    return {
        "success": True,
        "message": "Welcome to Bricks! Our team will review your portfolio. You'll hear from us soon.",
        "member_id": member.id,
        "application_token": application_token,
        "_internal_notes": member.internal_notes,
    }


@router.post("/sample-uploads")
async def mint_sample_uploads(body: SampleUploadRequest):
    """Signed upload URLs for application sample photos and the RPAS cert photo.

    Files go straight from the browser / GO to Bricks storage (brxs-deliverables),
    never through this function (Vercel body cap). Limits: 15 samples + 1
    certificate per session, JPEG/PNG/WebP/HEIC, 15 MB per sample, 10 MB cert.
    The review re-checks real bytes (magic number, size) before using them.
    """
    from ..services import applicant_evidence as ev
    from ..services.storage import create_signed_upload_url

    files = list(body.files or [])
    if not files:
        raise HTTPException(status_code=400, detail="No files.")
    samples = [f for f in files if (f.kind or "sample") == "sample"]
    certs = [f for f in files if f.kind == "rpas_cert"]
    if len(samples) > ev.MAX_SAMPLES or len(certs) > 1 or len(samples) + len(certs) != len(files):
        raise HTTPException(status_code=400, detail=f"Up to {ev.MAX_SAMPLES} sample photos and one certificate photo.")
    for f in files:
        mime = (f.type or "").lower()
        if mime not in ev.SAMPLE_MIME:
            raise HTTPException(status_code=400, detail=f"{f.name or 'A file'} isn't a JPEG, PNG, WebP or HEIC photo.")
        cap = ev.MAX_CERT_BYTES if f.kind == "rpas_cert" else ev.MAX_SAMPLE_BYTES
        if f.size <= 0 or f.size > cap:
            raise HTTPException(status_code=400, detail=f"{f.name or 'A file'} is too large ({cap // (1024 * 1024)} MB max).")
        if f.kind != "rpas_cert" and f.size < ev.MIN_SAMPLE_BYTES:
            raise HTTPException(status_code=400, detail=f"{f.name or 'A photo'} is too small to review. Upload the full-size export.")
    session = (body.session or "").strip()
    if not session or not ev.verify_upload_session(session, grace=0):
        session = ev.mint_upload_session()
    out = []
    for i, f in enumerate(files):
        kind = "cert" if f.kind == "rpas_cert" else "sample"
        path = ev.sample_path(session, i, kind, f.type.lower())
        signed = await create_signed_upload_url(path)
        if not signed or not signed.get("signed_url"):
            raise HTTPException(status_code=503, detail="Photo upload is unavailable right now. Please try again shortly.")
        out.append({"name": f.name, "kind": f.kind, "path": signed.get("path") or path,
                    "signed_url": signed["signed_url"], "token": signed.get("token")})
    return {"session": session, "uploads": out,
            "limits": {"min_samples": ev.MIN_SAMPLES, "max_samples": ev.MAX_SAMPLES,
                       "max_sample_mb": ev.MAX_SAMPLE_BYTES // (1024 * 1024)}}


@router.get("/review-backlog")
async def applicant_review_backlog(request: Request, limit: int = 5):
    """Daily Vercel cron: review pending photographers that have no review yet
    (applied before the evidence review, or the background run failed).

    Never notifies anyone (Kam already got the apply text/email) and never
    contacts the applicant. Auth: Vercel CRON_SECRET bearer or internal key.
    """
    from .vendor_os import _cron_authorized
    from ..services.applicant_review_runner import pending_without_review, review_application

    if not _cron_authorized(request.headers.get("Authorization"), request.headers.get("X-Internal-Key")
                            or request.headers.get("X-Internal-Api-Key")):
        raise HTTPException(status_code=401, detail="unauthorized")
    ids = await pending_without_review(limit=max(1, min(int(limit or 5), 10)))
    done = []
    for mid in ids:
        r = await review_application(mid, notify=False, trigger="backlog")
        done.append({"member_id": mid, "verdict": (r or {}).get("verdict"), "score": (r or {}).get("score")})
    return {"ok": True, "reviewed": done}


@router.post("/review/{member_id}")
async def rerun_applicant_review(member_id: str, request: Request, notify: bool = False, store: bool = True):
    """Re-run the automatic review for one applicant. Internal key only.

    notify=false by default: re-runs never text or email anyone unless asked,
    and the applicant is never contacted from here.
    """
    _api_keys = set(k for k in os.environ.get("INTERNAL_API_KEYS", "").split(",") if k)
    if not _api_keys or request.headers.get("X-Internal-Api-Key", "") not in _api_keys:
        raise HTTPException(status_code=401, detail="X-Internal-Api-Key required")
    from ..services.applicant_review_runner import review_application

    result = await review_application(member_id, notify=notify, trigger="rerun", store=store)
    if result is None:
        raise HTTPException(status_code=404, detail="Applicant not found or review failed")
    return {k: result[k] for k in ("verdict", "colour", "score", "incomplete", "needs_samples", "parts",
                                   "criteria", "evidence", "flags", "missing", "cost_usd", "model")}


@router.post("/validate-rpas/{member_id}")
async def validate_rpas_certificate(member_id: str, request: Request):
    """
    Run Gemini Vision + Transport Canada registry validation on a member's uploaded RPAS cert.
    Updates vendor_members with drone_certified, rpas_certificate_number, rpas_certificate_level.
    Stores full validation result on the vendor_documents record.
    Requires X-Internal-Api-Key header (exempt from middleware; admin/internal callers only).
    """
    _api_keys = set(k for k in os.environ.get("INTERNAL_API_KEYS", "").split(",") if k)
    _caller_key = request.headers.get("X-Internal-Api-Key", "")
    if not _api_keys or _caller_key not in _api_keys:
        raise HTTPException(status_code=401, detail="X-Internal-Api-Key required")

    db = await get_db()

    res_m = await db.table("vendor_members").select("name, drone_certified").eq("id", member_id).maybe_single().execute()
    member_row = maybe_single_row(res_m)
    if not member_row:
        raise HTTPException(status_code=404, detail="Member not found")
    vendor_name = member_row.get("name", "")

    # Find most recent rpas_certificate document for this member
    res_d = await db.table("vendor_documents").select("id, file_url, status").eq(
        "member_id", member_id
    ).eq("doc_type", "rpas_certificate").order("created_at", desc=True).limit(1).execute()
    if not res_d.data:
        raise HTTPException(status_code=404, detail="No RPAS certificate document found for this member")

    doc = res_d.data[0]
    doc_id = doc["id"]

    # Resolve signed download URL for the stored cert image
    from ..services.storage import get_signed_download_url
    image_url = await get_signed_download_url(doc["file_url"])
    if not image_url:
        raise HTTPException(status_code=500, detail="Could not generate signed URL for certificate")

    from ..services.rpas_validator import validate_rpas_certificate as _validate
    validation = await _validate(image_url=image_url, vendor_name=vendor_name)

    # Store validation result on document record
    await db.table("vendor_documents").update({
        "status": "verified" if validation["outcome"] == "verified" else (
            "flagged" if validation["outcome"] == "suspicious" else "pending"
        ),
        "validation_result": validation,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", doc_id).execute()

    # Auto-approve drone cert if outcome is verified or extracted
    if validation["outcome"] in ("verified", "extracted"):
        update_fields = {
            "drone_certified": True,
            "rpas_certificate_number": validation.get("certificate_number"),
            "rpas_certificate_level": validation.get("level"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.table("vendor_members").update(update_fields).eq("id", member_id).execute()

    return {
        "member_id": member_id,
        "doc_id": doc_id,
        "outcome": validation["outcome"],
        "level": validation.get("level"),
        "certificate_number": validation.get("certificate_number"),
        "holder_name": validation.get("holder_name"),
        "issues": validation.get("issues", []),
        "drone_certified": validation["outcome"] in ("verified", "extracted"),
        "tc_registry": validation.get("tc_registry", {}),
    }


def _check_application_token(member: dict, token: Optional[str]) -> None:
    """FIXED 2026-08-16: member_id alone (a UUID) was treated as an implicit
    bearer credential with zero further verification on both of these
    endpoints -- unguessable via brute force, but a leaked member_id (logs,
    a forwarded email, a screenshot) would have let anyone view or add to
    an applicant's compliance documents. application_token is generated
    once at /apply and returned only in that response.

    Rows created before this fix have no token on file (application_token
    IS NULL) -- grandfathered through without one rather than locking out
    an applicant already mid-flow; every new application gets a real token
    and is fully enforced. 404 (not 403) on mismatch so this doesn't become
    an oracle for whether a given member_id exists."""
    stored_token = member.get("application_token")
    if stored_token and stored_token != token:
        raise HTTPException(status_code=404, detail="Application not found.")


@router.get("/status/{member_id}")
async def get_application_status(member_id: str, token: Optional[str] = None):
    """
    Check the status of an onboarding application.
    """
    res_member = await (await get_db()).table("vendor_members").select("*").eq("id", member_id).maybe_single().execute()
    member = maybe_single_row(res_member) or None
    if not member:
        raise HTTPException(status_code=404, detail="Application not found.")
    _check_application_token(member, token)

    res_docs = await (await get_db()).table("vendor_documents").select("*").eq("member_id", member_id).execute()
    docs = res_docs.data

    return {
        "name": member["name"],
        "status": member["onboarding_status"],
        "is_active": member["is_active"],
        "documents": docs,
        "portfolio_samples": assemble_portfolio_samples(member, docs),
        "app_access": bool(member.get("is_active") and member.get("onboarding_status") == "approved"),
        "application_submitted": bool(member.get("terms_accepted_at")),
    }


@router.post("/documents")
async def upload_compliance_document(
    member_id: str = Form(...),
    doc_type: str = Form(...),
    filename: str = Form(...),
    file_data: str = Form(...),
    expiry_date: Optional[str] = Form(None),
    token: Optional[str] = Form(None),
    application_token: Optional[str] = Form(None),
):
    """
    Upload Ontario compliance documents (COI, WSIB, etc) or a resume.
    Client must send member_id + application_token (or token) from /apply.
    """
    res_member = await (await get_db()).table("vendor_members").select("*").eq("id", member_id).maybe_single().execute()
    member = maybe_single_row(res_member) or None
    if not member:
        raise HTTPException(status_code=404, detail="Member not found.")
    _check_application_token(member, token or application_token)

    portfolio_docs = [portfolio_doc_type(t) for t in PORTFOLIO_MEDIA_TYPES]
    valid_docs = ["coi", "wsib", "hst_registry", "id_verification", "rpas_certificate", "resume", *portfolio_docs]
    # Allow portfolio_<type> aliases passed as bare media type via normalize
    mt = normalize_media_type(doc_type)
    if mt and doc_type not in valid_docs:
        doc_type = portfolio_doc_type(mt)
    if doc_type not in valid_docs:
        raise HTTPException(status_code=400, detail=f"Invalid doc_type. Must be one of {valid_docs}")

    import base64
    try:
        raw_bytes = base64.b64decode(file_data)
    except Exception:
        raw_bytes = file_data.encode()

    _ext_mime = {
        "pdf": "application/pdf",
        "doc": "application/msword",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
    }
    mime_type = _ext_mime.get(filename.rsplit(".", 1)[-1].lower(), "application/pdf") if "." in filename else "application/pdf"

    file_key, _, _, _ = await upload_file(
        raw_bytes, "onboarding", member_id, filename, mime_type
    )

    doc = VendorDocument(
        id=str(uuid.uuid4()),
        vendor_id=member.get("vendor_id", "default_vendor"),
        member_id=member_id,
        doc_type=doc_type,
        file_url=file_key,
        expiry_date=expiry_date,
        status="pending"
    )

    await (await get_db()).table("vendor_documents").insert(doc.dict()).execute()

    # Optional. A COI or WSIB upload can be validated and stored on the
    # document row. The outcome does not block apply, approval, assignment,
    # or dispatch, and it does not suspend the member or hold a job.
    if doc_type in ("coi", "wsib"):
        from ..services.compliance_validator import validate_compliance_document

        async def _run_compliance_validation():
            try:
                from ..services.storage import get_signed_download_url
                signed_url = await get_signed_download_url(file_key)
                if not signed_url:
                    return
                validation = await validate_compliance_document(
                    document_url=signed_url, doc_type=doc_type, filename=filename
                )
                status = (
                    "verified" if validation["outcome"] == "verified"
                    else "pending" if validation["outcome"] == "failed"
                    else "flagged"
                )
                update_fields = {
                    "status": status,
                    "validation_result": validation,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                if validation.get("expiry_date"):
                    update_fields["expiry_date"] = validation["expiry_date"]
                await (await get_db()).table("vendor_documents").update(
                    update_fields
                ).eq("id", doc.id).execute()
            except Exception:
                logger.error(
                    f"compliance auto-validation failed for doc {doc.id}:\n"
                    + traceback.format_exc()
                )

        import asyncio
        asyncio.create_task(_run_compliance_validation())

    return {
        "success": True,
        "document_id": doc.id,
        "status": "pending"
    }


@router.post("/validate-compliance/{member_id}")
async def validate_compliance(member_id: str, doc_type: str, request: Request):
    """
    Run Gemini-based validation on a member's most recent COI or WSIB document
    against IC agreement Clause 6 minimums (coverage amount, additional
    insured, clearance/exemption). Requires X-Internal-Api-Key — admin/internal
    callers only (mirrors /validate-rpas/{member_id}).
    """
    if doc_type not in ("coi", "wsib"):
        raise HTTPException(status_code=400, detail="doc_type must be 'coi' or 'wsib'")

    _api_keys = set(k for k in os.environ.get("INTERNAL_API_KEYS", "").split(",") if k)
    _caller_key = request.headers.get("X-Internal-Api-Key", "")
    if not _api_keys or _caller_key not in _api_keys:
        raise HTTPException(status_code=401, detail="X-Internal-Api-Key required")

    db = await get_db()

    res_m = await db.table("vendor_members").select("name").eq("id", member_id).maybe_single().execute()
    if not maybe_single_row(res_m):
        raise HTTPException(status_code=404, detail="Member not found")

    res_d = await db.table("vendor_documents").select("id, file_url, status").eq(
        "member_id", member_id
    ).eq("doc_type", doc_type).order("created_at", desc=True).limit(1).execute()
    if not res_d.data:
        raise HTTPException(status_code=404, detail=f"No {doc_type} document found for this member")

    doc = res_d.data[0]
    doc_id = doc["id"]

    from ..services.storage import get_signed_download_url
    document_url = await get_signed_download_url(doc["file_url"])
    if not document_url:
        raise HTTPException(status_code=500, detail="Could not generate signed URL for document")

    from ..services.compliance_validator import validate_compliance_document
    validation = await validate_compliance_document(document_url=document_url, doc_type=doc_type)

    status = (
        "verified" if validation["outcome"] == "verified"
        else "pending" if validation["outcome"] == "failed"
        else "flagged"
    )
    update_fields = {
        "status": status,
        "validation_result": validation,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if validation.get("expiry_date"):
        update_fields["expiry_date"] = validation["expiry_date"]
    await db.table("vendor_documents").update(update_fields).eq("id", doc_id).execute()

    return {
        "member_id": member_id,
        "doc_id": doc_id,
        "doc_type": doc_type,
        "outcome": validation["outcome"],
        "expiry_date": validation.get("expiry_date"),
        "issues": validation.get("issues", []),
        "status": status,
    }


@router.post("/portfolio")
async def upload_portfolio_sample(
    member_id: str = Form(...),
    media_type: str = Form(...),
    filename: str = Form(...),
    file_data: str = Form(...),
    link: Optional[str] = Form(None),
    token: Optional[str] = Form(None),
):
    """Upload a portfolio sample image for a catalog media type (HDR, drone, …)."""
    mt = normalize_media_type(media_type)
    if not mt:
        raise HTTPException(
            status_code=400,
            detail=f"media_type must be one of {list(PORTFOLIO_MEDIA_TYPES)}",
        )

    res_member = await (await get_db()).table("vendor_members").select("*").eq("id", member_id).maybe_single().execute()
    member = maybe_single_row(res_member) or None
    if not member:
        raise HTTPException(status_code=404, detail="Member not found.")
    _check_application_token(member, token)

    import base64
    try:
        raw_bytes = base64.b64decode(file_data)
    except Exception:
        raw_bytes = file_data.encode()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(raw_bytes) > 12 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (12 MB max)")

    _ext_mime = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "gif": "image/gif",
        "mp4": "video/mp4",
        "mov": "video/quicktime",
    }
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    mime_type = _ext_mime.get(ext, "image/jpeg")

    file_key, _, _, _ = await upload_file(
        raw_bytes, "onboarding", member_id, filename, mime_type
    )

    doc_type = portfolio_doc_type(mt)
    doc = VendorDocument(
        id=str(uuid.uuid4()),
        vendor_id=member.get("vendor_id", "default_vendor"),
        member_id=member_id,
        doc_type=doc_type,
        file_url=file_key,
        status="pending",
    )
    db = await get_db()
    await db.table("vendor_documents").insert(doc.dict()).execute()

    samples = sanitize_portfolio_samples(member.get("portfolio_samples") or [])
    # Replace existing entry for this type
    samples = [s for s in samples if s.get("media_type") != mt]
    entry = {"media_type": mt, "url": file_key}
    if link and str(link).strip():
        entry["link"] = str(link).strip()
    samples.append(entry)

    try:
        await db.table("vendor_members").update({
            "portfolio_samples": samples,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", member_id).execute()
    except Exception:
        logger.warning("Could not persist portfolio_samples column for %s", member_id)

    docs_res = await db.table("vendor_documents").select("*").eq("member_id", member_id).execute()
    return {
        "success": True,
        "document_id": doc.id,
        "media_type": mt,
        "file_url": file_key,
        "portfolio_samples": assemble_portfolio_samples({**member, "portfolio_samples": samples}, docs_res.data),
    }
