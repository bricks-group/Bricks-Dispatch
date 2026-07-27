from datetime import datetime, timezone
import logging
import os
import traceback
import uuid
from typing import Optional
from fastapi import APIRouter, HTTPException, Form, Request

logger = logging.getLogger(__name__)

from ..services.storage import upload_file
from ..shared.database import get_db
from ..shared.schemas import AvailabilitySettings, VendorApplicationCreate, VendorMember, VendorDocument

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

# Version tracks the text revision so we can re-prompt if terms materially change.
_IC_AGREEMENT_VERSION = "1.1"
_IC_AGREEMENT_TEXT = """
BRICKS INDEPENDENT CONTRACTOR AGREEMENT  v1.1
Effective: June 1, 2026

By submitting this application you ("Contractor") agree to the following terms with Bricks Technologies Inc. ("Bricks"). This characterization of the relationship as independent contractor reflects the parties' genuine intent. Nothing in this agreement waives any statutory rights that may apply under the Employment Standards Act, 2000 (Ontario) or any other applicable legislation. If a court or tribunal of competent jurisdiction determines that this agreement creates an employment or dependent contractor relationship, applicable statutory rights will apply notwithstanding the parties' characterization.

1. INDEPENDENT CONTRACTOR STATUS
   The parties intend that you are an independent contractor. You have sole control over the means, methods, and manner of performing your work. Bricks specifies deliverable outcomes (photo count, resolution, shot list) but not how you achieve them. You set your own availability, use your own equipment, and bear your own business risk. This agreement does not make you an employee, agent, partner, or joint venturer of Bricks.

2. RIGHT TO REFUSE WORK
   You may decline any job offer through the Bricks platform at any time, for any reason, without penalty. Your dispatch priority score is not negatively affected by declines where your decline rate remains below 40% over any rolling 30-day period. Bricks will not treat a reasonable level of job refusal as grounds for suspension or removal from the platform.

3. RIGHT TO ENGAGE OTHERS AND NO EXCLUSIVITY
   You may hire assistants, second shooters, or subcontractors at your own expense. You are not exclusive to Bricks and may provide photography or related services to any other client, platform, or competitor at your discretion. There is no non-solicitation restriction on your independent client relationships.

4. TOOLS AND EQUIPMENT
   You provide your own camera equipment, lenses, drone, editing software, vehicle, and all other tools required to complete your work. Bricks provides no equipment and takes no responsibility for equipment maintenance, repair, or replacement.

5. TAXES AND HST
   All fees stated in this agreement and in your payout statements are exclusive of HST/GST. You are solely responsible for income tax and, if applicable, CPP contributions on your net earnings. If your annual taxable supplies exceed $30,000 CAD over four consecutive calendar quarters (the CRA small supplier threshold), you must register for an HST/GST number with the Canada Revenue Agency and charge HST on amounts payable to you by Bricks. Upon registration, you will notify Bricks of your HST number and Bricks will pay HST on your fees and claim the corresponding input tax credit. Bricks will issue a T4A (Box 048) for your annual earnings at or above the CRA reporting threshold of $500.

6. INSURANCE AND LIABILITY
   You are responsible for determining and obtaining appropriate insurance coverage. Commercial general liability insurance ($2M minimum recommended), drone aviation liability insurance (if drone-capable), and commercial vehicle insurance are your responsibility to obtain and maintain. BRICKS DOES NOT REQUIRE PROOF OF INSURANCE AS A CONDITION OF DISPATCH AND EXPRESSLY DISCLAIMS ALL LIABILITY FOR ANY PERSONAL INJURY, PROPERTY DAMAGE, EQUIPMENT LOSS, DRONE INCIDENTS, OR THIRD-PARTY CLAIMS ARISING FROM YOUR PERFORMANCE OF SERVICES. You agree to indemnify and hold harmless Bricks Technologies Inc., its officers, employees, and agents from and against any claims, losses, or damages arising out of or related to your work, including drone operations.

7. DRONE OPERATIONS
   If you operate a drone, you represent that you hold a valid Transport Canada RPAS certificate at the level required for each operation (Basic for uncontrolled airspace; Advanced for controlled airspace, including proximity to Pearson CYYZ and Billy Bishop CYTZ). All operations must comply with the Aeronautics Act and Transport Canada regulations. You accept sole regulatory and legal responsibility for all drone operations.

8. PLATFORM FEES AND PAYOUTS
   Bricks retains a platform fee from each job value based on your tier (Tier A: 45%; Tier B: 55%; Tier C: 65%). Your payout is the remainder of the job price (excluding HST), paid via Stripe Connect Express on the standard weekly settlement schedule. Bricks may adjust the fee schedule on 30 days' written notice to you.

9. INTELLECTUAL PROPERTY
   You assign to Bricks all copyright and intellectual property rights in the specific photos, videos, and other deliverables produced for each individual booking accepted through this platform. This assignment is limited to those deliverables and does not extend to your pre-existing portfolio, equipment, editing presets, or other works created outside of Bricks bookings. Bricks licenses the assigned deliverables to the booking agent for real estate marketing use of the specific listed property only. You retain the right to display the deliverables in your portfolio with appropriate attribution, unless the booking agent requests confidentiality.

10. CONDUCT AND QUALITY
    You agree to maintain professional conduct at all times and to deliver work meeting Bricks' published quality standards. Bricks may suspend or remove you from the platform for repeated quality failures, serious client complaints, or conduct violations, with reasonable notice except where immediate removal is required to protect clients or the platform.

11. CONFIDENTIALITY
    Client names, addresses, contact information, and booking details are confidential. You may not use client data to solicit those clients outside of Bricks bookings. This clause survives termination of this agreement.

12. NO NON-COMPETE
    Nothing in this agreement restricts you from working in the photography, videography, or related industries or from soliciting or serving clients independently. Bricks will not seek to enforce any non-compete obligation against you.

13. TERMINATION AND NOTICE
    Either party may terminate this agreement by providing written notice equal to two (2) weeks for every year (or partial year) of continuous engagement under this agreement, with a minimum of two (2) weeks and a maximum of twenty-four (24) weeks. Either party may terminate immediately for cause (material breach, fraud, or criminal conduct). Upon notice of termination, outstanding accepted jobs must be completed or transferred with Bricks' assistance. All outstanding payout amounts will be settled within the normal Stripe Connect settlement cycle following termination.

14. DISPUTE RESOLUTION AND GOVERNING LAW
    This agreement is governed by the laws of the Province of Ontario and the federal laws of Canada applicable therein. Either party may bring a complaint or application before the Ontario Ministry of Labour (Employment Standards Branch) or the Canada Revenue Agency at any time — this agreement does not restrict those statutory rights.

    Any civil dispute arising from this agreement that the parties cannot resolve informally within 30 days of written notice shall be submitted to binding arbitration in Toronto, Ontario, administered by ADR Chambers under the Arbitration Act, 1991 (Ontario). For claims by Contractor against Bricks of $50,000 CAD or less, Bricks will pay all arbitration administration fees. For claims above $50,000 CAD, fees shall be split equally. The arbitrator's award may be enforced in any court of competent jurisdiction. Either party retains the right to seek emergency injunctive relief from a court of competent jurisdiction.

15. STATUTORY RIGHTS — NO WAIVER
    Nothing in this agreement limits, restricts, or waives any rights available to you under the Employment Standards Act, 2000 (Ontario), the Human Rights Code (Ontario), or any other applicable legislation. If any provision of this agreement is found to be void or unenforceable under applicable law (including but not limited to ESA s. 5(1)), that provision is severed and the remaining provisions continue in full effect.

16. ENTIRE AGREEMENT
    This agreement constitutes the entire agreement between the parties regarding contractor services and supersedes all prior agreements or understandings on this subject. Amendments require written consent of both parties. Bricks will provide at least 14 days' advance notice before requiring Contractors to accept a revised version of this agreement.
""".strip()


@router.get("/ic-agreement")
async def get_ic_agreement():
    """Return the current Bricks IC agreement text and version number."""
    return {
        "version": _IC_AGREEMENT_VERSION,
        "effective_date": "2026-06-01",
        "text": _IC_AGREEMENT_TEXT,
    }


@router.post("/apply")
async def apply_to_bricks(application: VendorApplicationCreate):
    """
    Low-friction Snappr-style application. 
    Burden of insurance/tax is shifted to the contractor via accepted_terms.
    """
    if not application.accepted_terms:
        raise HTTPException(status_code=400, detail="You must accept the Bricks Contractor Agreement to apply.")

    try:
        return await _apply_to_bricks_inner(application)
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
    member = VendorMember(
        id=str(uuid.uuid4()),
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
        ic_agreement_version=application.ic_agreement_version,
        terms_accepted_at=now_iso,
        internal_notes=f"IC agreement v{application.ic_agreement_version} accepted on {now_iso}",
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
        "member_id": member.id
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


@router.get("/status/{member_id}")
async def get_application_status(member_id: str):
    """
    Check the status of an onboarding application.
    """
    res_member = await (await get_db()).table("vendor_members").select("*").eq("id", member_id).maybe_single().execute()
    member = res_member.data
    if not member:
        raise HTTPException(status_code=404, detail="Application not found.")
        
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
    expiry_date: Optional[str] = Form(None)
):
    """
    Upload Ontario compliance documents (COI, WSIB, etc).
    """
    res_member = await (await get_db()).table("vendor_members").select("*").eq("id", member_id).maybe_single().execute()
    member = res_member.data
    if not member:
        raise HTTPException(status_code=404, detail="Member not found.")
        
    valid_docs = ["coi", "wsib", "hst_registry", "id_verification", "rpas_certificate"]
    if doc_type not in valid_docs:
        raise HTTPException(status_code=400, detail=f"Invalid doc_type. Must be one of {valid_docs}")

    import base64
    try:
        raw_bytes = base64.b64decode(file_data)
    except Exception:
        raw_bytes = file_data.encode()

    file_key, _, _ = await upload_file(
        raw_bytes, "onboarding", member_id, filename, "application/pdf"
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
    
    return {
        "success": True,
        "document_id": doc.id,
        "status": "pending"
    }
