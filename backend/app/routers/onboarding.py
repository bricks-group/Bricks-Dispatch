from datetime import datetime, timezone
import uuid
from typing import Optional
from fastapi import APIRouter, HTTPException, Form

from ..services.storage import upload_file
from ..shared.database import get_db
from ..shared.schemas import AvailabilitySettings, VendorApplicationCreate, VendorMember, VendorDocument

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

# Version tracks the text revision so we can re-prompt if terms materially change.
_IC_AGREEMENT_VERSION = "1.0"
_IC_AGREEMENT_TEXT = """
BRICKS INDEPENDENT CONTRACTOR AGREEMENT  v1.0
Effective: January 1, 2026

By submitting this application you ("Contractor") agree to the following terms with Bricks Technologies Inc. ("Bricks"). This agreement does not create an employment relationship under the Employment Standards Act, 2000 (Ontario) or any other applicable legislation.

1. INDEPENDENT CONTRACTOR STATUS
   You are an independent contractor, not an employee, dependent contractor, agent, partner, or joint venturer of Bricks. You have sole control over the means, methods, and manner of performing your work. Bricks specifies deliverable outcomes (photo count, resolution, shot list) but not how you achieve them.

2. RIGHT TO REFUSE WORK
   You may decline any job offer through the Bricks platform at any time, for any reason, without penalty or adverse consequence on your account standing. Declining a job does not affect your access to future job offers.

3. RIGHT TO ENGAGE OTHERS AND NO EXCLUSIVITY
   You may hire assistants, second shooters, or subcontractors at your own expense to assist with completing your work. You are not exclusive to Bricks and may provide photography or related services to other clients, platforms, or competitors at your discretion.

4. TOOLS AND EQUIPMENT
   You provide your own camera equipment, lenses, drone, editing software, vehicle, and any other tools required to complete your work. Bricks provides no equipment.

5. TAXES AND HST
   You are solely responsible for remitting all applicable income taxes and, where applicable, both portions of CPP contributions. If your annual gross revenue exceeds $30,000 CAD across four consecutive calendar quarters, you must register for an HST/GST number with the CRA and remit HST collected on platform earnings. Bricks will issue a T4A for annual earnings above the CRA reporting threshold. You are encouraged to operate through a registered business entity.

6. INSURANCE AND LIABILITY
   You are responsible for determining what insurance coverage, if any, is appropriate for your work. Commercial general liability insurance ($2M coverage recommended), drone insurance (if drone-capable), and vehicle insurance for commercial use are your responsibility to obtain and maintain. BRICKS DOES NOT REQUIRE PROOF OF INSURANCE AS A CONDITION OF RECEIVING WORK AND EXPRESSLY DISCLAIMS ALL LIABILITY FOR ANY PERSONAL INJURY, PROPERTY DAMAGE, EQUIPMENT LOSS, DRONE INCIDENTS, OR THIRD-PARTY CLAIMS ARISING FROM YOUR WORK. You agree to indemnify and hold harmless Bricks Technologies Inc., its officers, employees, and agents from and against any and all claims arising out of or related to your performance of services.

7. DRONE OPERATIONS
   If you operate a drone, you represent that you hold all required certifications under Transport Canada regulations (including Advanced RPAS Certificate for controlled airspace) and that all operations comply with applicable law. You accept sole responsibility for any regulatory violations or incidents.

8. PLATFORM FEES AND PAYOUTS
   Bricks retains a platform fee (40–60% of the job value, depending on your tier) as consideration for providing platform infrastructure, client acquisition, payment processing, and delivery. Payouts are issued via Stripe Connect within the standard Stripe settlement period. Bricks may adjust the fee schedule with 30 days' written notice.

9. INTELLECTUAL PROPERTY
   You assign to Bricks all copyright and intellectual property rights in photos, videos, and other deliverables produced under this agreement. Bricks licenses these deliverables to the booking agent for marketing use of the specific listed property only.

10. CONDUCT AND QUALITY
    You agree to maintain professional conduct at all times and deliver work that meets Bricks' published quality standards. Bricks may suspend or remove you from the platform for repeated quality failures, client complaints, or conduct violations, with reasonable notice.

11. CONFIDENTIALITY
    Client names, addresses, contact information, and booking details are confidential. You may not use client data for independent solicitation outside of Bricks bookings. This clause survives termination.

12. NO NON-COMPETE
    Nothing in this agreement restricts you from working in the photography industry or soliciting clients independently. Non-compete clauses are not enforceable against independent contractors in Ontario under applicable law.

13. TERMINATION
    Either party may terminate this agreement on 7 days' written notice, or immediately for cause. Outstanding accepted jobs must be completed or transferred with Bricks' assistance. All outstanding payout amounts will be settled within the normal Stripe Connect settlement cycle.

14. GOVERNING LAW AND DISPUTE RESOLUTION
    This agreement is governed by the laws of the Province of Ontario and the federal laws of Canada applicable therein. Any dispute that cannot be resolved informally shall be submitted to binding arbitration in Toronto, Ontario under the Arbitration Act, 1991 (Ontario). The prevailing party shall be entitled to reasonable legal fees.

15. ENTIRE AGREEMENT
    This agreement constitutes the entire agreement between the parties regarding contractor services. If any provision is found unenforceable, the remaining provisions remain in full effect.
""".strip()


@router.get("/ic-agreement")
async def get_ic_agreement():
    """Return the current Bricks IC agreement text and version number."""
    return {
        "version": _IC_AGREEMENT_VERSION,
        "effective_date": "2026-01-01",
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

    res_existing = await (await get_db()).table("vendor_members").select("*").eq("email", application.email.lower().strip()).maybe_single().execute()
    if res_existing.data:
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
        
    valid_docs = ["coi", "wsib", "hst_registry", "id_verification"]
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
