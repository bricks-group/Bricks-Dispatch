from datetime import datetime, timezone
import uuid
from typing import Optional
from fastapi import APIRouter, HTTPException, Form

from ..services.storage import upload_file
from ..shared.database import get_db
from ..shared.schemas import AvailabilitySettings, VendorApplicationCreate, VendorMember, VendorDocument

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


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
        max_travel_km=application.max_travel_km,
        operation_polygon=application.operation_polygon,
        service_areas=application.preferred_regions if application.preferred_regions else [application.city],
        internal_notes=f"Terms accepted on {datetime.now(timezone.utc).isoformat()}"
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
