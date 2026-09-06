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
from ..shared.database import get_db
from ..shared.schemas import AvailabilitySettings, VendorApplicationCreate, VendorMember, VendorDocument

ADMIN_NOTIFICATION_EMAIL = os.environ.get("ADMIN_NOTIFICATION_EMAIL", "")

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

# Version tracks the text revision so we can re-prompt if terms materially change.
_IC_AGREEMENT_VERSION = "1.5"
_IC_AGREEMENT_EFFECTIVE_DATE = "2026-09-06"
_IC_AGREEMENT_TEXT = """
BRICKS INDEPENDENT CONTRACTOR AGREEMENT  v1.5
Effective: September 6, 2026
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

6. INSURANCE AND WSIB (REQUIRED BEFORE FIRST JOB)
Before your first dispatched booking you must provide, and you must keep current:

(a) Commercial general liability insurance of at least $2,000,000 CAD per occurrence, naming Bricks Group as additional insured, with at least 30 days' notice of cancellation or material change to Bricks.

(b) If you accept drone jobs: unmanned-aircraft / aviation liability of at least $1,000,000 CAD.

(c) Automobile liability on any vehicle used to travel to jobs, at or above Ontario statutory minimums, commercially rated if required by your insurer for this work.

(d) A current WSIB Certificate of Clearance, or a written declaration of the exemption category you rely on under the Workplace Safety and Insurance Act, 1997.

You must send renewal certificates before expiry. Lapsed coverage is automatic suspension from dispatch until cured. Bricks may refuse or cancel a job if proof is missing. Your indemnity in section 14 is in addition to insurance, not limited by it.

7. DRONE
If you operate a drone you represent that you hold a valid Transport Canada RPAS certificate at the level required for that operation (Advanced for controlled airspace, including proximity to CYYZ and CYTZ). You comply with the Aeronautics Act, Canadian Aviation Regulations, and any NAV CANADA or site restriction. You are solely responsible for the operation, including fines, incidents, and third-party claims. If airspace, weather, or regulation prevents aerial capture, you still complete the ground package and note the miss in the job.

8. PAYOUTS
You are paid a fixed amount per accepted booking, set by Bricks' vendor rate card for the media package (or à-la-carte mix), property size band, and your vendor tier at the time you accept the job. That amount does not change based on what the client was charged or any client discount. Client media on www.brxs.ca is sold à la carte as Photos only, Essentials, Story, Showcase, and Full suite. Virtual Visit (free-roam Gaussian splat) is always a paid add-on — never packaged into a suite and never treated as included deliverable pay. Path-confined Virtual Tour may be included in Showcase and Full suite or sold as an add-on. Add-ons (drone, cinematic reel, Virtual Tour, twilight, Virtual Visit, and similar) are paid at the published add-on payout for your tier. Bricks may change the rate card prospectively on 14 days' notice; changes do not apply to jobs already accepted.

Current media rate card (CAD, exclusive of HST), shown as Tier C / Tier B / Tier A. These are the same fixed dollar cells already published on the vendor rate card (no new amounts):

Photos only / Essentials — under 2,000 sqft $104.65 / $134.55 / $164.45; 2,000–3,499 $132.65 / $170.55 / $208.45; 3,500–4,999 $164.15 / $211.05 / $257.95; 5,000+ $174.65 / $224.55 / $274.45
Story / Showcase — under 2,000 $209.65 / $269.55 / $329.45; 2,000–3,499 $262.15 / $337.05 / $411.95; 3,500–4,999 $314.64 / $404.55 / $494.45; 5,000+ $367.15 / $472.05 / $576.95
Full suite — under 2,000 $402.15 / $517.05 / $631.95; 2,000–3,499 $419.65 / $539.55 / $659.45; 3,500–4,999 $524.65 / $674.55 / $824.45; 5,000+ $664.65 / $854.55 / $1,044.45
Legacy Estate (flat, only if booked on that package) — $874.65 / $1,124.55 / $1,374.45

Add-on payouts are fixed published dollar amounts from the add-on rate card for your tier — not a percentage of rack. Virtual Visit payout is the published Virtual Visit add-on cell for your tier (quoted by footprint for the client; never a percent of rack).

Tier is assigned by Bricks based on quality, reliability, volume, and compliance. Tier C is the starting tier. Bricks may change your tier on notice. Payouts are via Stripe Connect within 7 business days of client acceptance of deliverables, or within 14 days of shoot completion if the client has not acted. Raise payout disputes in writing to booking@brxs.ca within 30 days of the payment date.

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
Deliverables go through the Bricks app within the booking turnaround (standard 24 hours from shoot end unless the booking says otherwise) and must meet the Shoot Standards Guide. Bricks may require a complimentary re-shoot if work does not meet those standards. Repeated failures, fraud, or serious complaints may mean suspension, tier change, or termination.

14. INDEMNITY
You will indemnify, defend, and hold harmless Bricks Group and its officers, directors, contractors, and agents from claims, losses, damages, and reasonable legal fees arising out of: (a) your services or those of your assistants; (b) injury or property damage at a job; (c) drone operations; (d) your breach of this agreement or law; (e) alleged infringement by the Deliverables; (f) your tax, WSIB, or employment-status claims against Bricks to the extent they arise from your holding yourself out as an employee contrary to this agreement. Bricks may control the defence of a third-party claim.

15. LIMITATION OF LIABILITY
Bricks is not liable to you for lost jobs, lost profit, or equipment damage. Bricks' total liability to you for a booking is the payout for that booking. Nothing excludes liability that Ontario or Canadian law does not allow to be excluded (including fraud or bodily injury Bricks caused).

16. TERM AND TERMINATION
This agreement starts when you accept it and continues until terminated. Either party may terminate for any reason on written notice (email is enough). Bricks may suspend or terminate immediately if you lack required insurance or WSIB proof, for safety or legal risk, fraud, material quality failure, or material breach. Outstanding accepted jobs should be finished or handed off. Earned payouts for completed, accepted work are paid on the normal cycle. Sections 9–15 and 17 survive.

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

    equipment = [e.strip() for e in (application.equipment_list or []) if e and e.strip()]
    if len(equipment) == 0:
        errs.append("Please list your equipment.")
    elif len(" ".join(equipment)) < 8:
        errs.append("Please describe your equipment in a little more detail.")

    links = [str(l).strip() for l in (application.portfolio_links or []) if l and str(l).strip()]
    if not links:
        errs.append("At least one portfolio link is required.")
    else:
        for link in links:
            if not link.lower().startswith(("http://", "https://")):
                errs.append("Portfolio links must be full URLs (starting with https://).")
                break

    hst = (application.hst_number or "").strip()
    if not hst and not application.hst_exempt:
        errs.append("HST / GST number is required, or confirm the small-supplier exemption (under $30,000 CAD/year).")

    if errs:
        raise HTTPException(status_code=400, detail=" ".join(errs))


@router.post("/apply")
async def apply_to_bricks(application: VendorApplicationCreate, background_tasks: BackgroundTasks):
    """
    Photographer application. Requires acceptance of the current IC agreement.
    Records ic_agreement_version as the live server version (currently 1.5).
    """
    if not application.accepted_terms:
        raise HTTPException(status_code=400, detail="You must accept the Bricks Contractor Agreement to apply.")

    # Gate junk/bot submissions before any DB write or notification.
    await _validate_application(application)

    try:

        result = await _apply_to_bricks_inner(application)
        background_tasks.add_task(
            create_notification,
            title="New photographer application",
            body=(
                f"{application.name} applied via brxs.ca/photographers.\n"
                f"Email: {application.email}\n"
                f"Phone: {application.phone}\n"
                f"City: {application.city}\n"
                f"Specialties: {', '.join(application.specialties) if application.specialties else 'n/a'}\n"
                f"Drone certified: {application.drone_certified}"
            ),
            ntype="vendor_application",
            data={"member_id": result["member_id"]},
            email_to=ADMIN_NOTIFICATION_EMAIL,
            email_subject=f"New photographer application: {application.name}",
        )
        return result
    except HTTPException:
        raise
    except Exception:
        logger.error("apply_to_bricks_failed:\n" + traceback.format_exc())
        raise HTTPException(status_code=500, detail="Could not process application. Please try again.")


async def _apply_to_bricks_inner(application: VendorApplicationCreate):
    res_existing = await (await get_db()).table("vendor_members").select("*").eq("email", application.email.lower().strip()).maybe_single().execute()
    if res_existing and res_existing.data:
        raise HTTPException(status_code=400, detail="An application with this email already exists.")

    now_iso = datetime.now(timezone.utc).isoformat()
    application_token = secrets.token_urlsafe(32)
    member = VendorMember(
        id=str(uuid.uuid4()),
        application_token=application_token,
        name=application.name,
        email=application.email.lower().strip(),
        phone=application.phone,
        role="Applicant",
        specialty=application.specialties[0] if application.specialties else "photo",
        onboarding_status="pending",
        is_active=False,
        portfolio_links=application.portfolio_links,
        equipment_list=application.equipment_list,
        home_city=application.city,
        home_lat=application.home_lat,
        home_lng=application.home_lng,
        hst_number=application.hst_number,
        drone_certified=application.drone_certified,
        rpas_certificate_number=application.rpas_certificate_number,
        rpas_certificate_level=application.rpas_certificate_level,
        max_travel_km=application.max_travel_km,
        operation_polygon=application.operation_polygon,
        service_areas=application.preferred_regions if application.preferred_regions else [application.city],
        ic_agreement_version=_IC_AGREEMENT_VERSION,
        terms_accepted_at=now_iso,
        internal_notes=f"IC agreement v{_IC_AGREEMENT_VERSION} accepted on {now_iso}",
    )

    member_dict = member.dict()
    await (await get_db()).table("vendor_members").insert(member_dict).execute()

    availability = AvailabilitySettings(
        member_id=member.id,
        vendor_id=member.vendor_id,
        available=True
    )
    await (await get_db()).table("vendor_availability").insert(availability.dict()).execute()

    return {
        "success": True,
        "message": "Welcome to Bricks! Our team will review your portfolio. You'll hear from us soon.",
        "member_id": member.id,
        "application_token": application_token,
    }


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
    if not res_m.data:
        raise HTTPException(status_code=404, detail="Member not found")
    vendor_name = res_m.data.get("name", "")

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
    member = res_member.data
    if not member:
        raise HTTPException(status_code=404, detail="Application not found.")
    _check_application_token(member, token)

    res_docs = await (await get_db()).table("vendor_documents").select("*").eq("member_id", member_id).execute()
    docs = res_docs.data

    return {
        "name": member["name"],
        "status": member["onboarding_status"],
        "is_active": member["is_active"],
        "documents": docs
    }


@router.post("/documents")
async def upload_compliance_document(
    member_id: str = Form(...),
    doc_type: str = Form(...),
    filename: str = Form(...),
    file_data: str = Form(...),
    expiry_date: Optional[str] = Form(None),
    token: Optional[str] = Form(None),
):
    """
    Upload Ontario compliance documents (COI, WSIB, etc) or a resume.
    """
    res_member = await (await get_db()).table("vendor_members").select("*").eq("id", member_id).maybe_single().execute()
    member = res_member.data
    if not member:
        raise HTTPException(status_code=404, detail="Member not found.")
    _check_application_token(member, token)

    valid_docs = ["coi", "wsib", "hst_registry", "id_verification", "rpas_certificate", "resume"]
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

    # COI and WSIB are the two IC-agreement Clause 6 compliance documents with
    # hard minimums (coverage amount, additional insured, clearance/exemption) —
    # auto-validate them on upload so applicants get a fast pending/flagged
    # signal instead of waiting on manual admin review to catch a bad upload.
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
    if not res_m.data:
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
