from datetime import datetime, timezone
import uuid
from typing import Optional
from fastapi import APIRouter, HTTPException, Form

from ..services.storage import upload_file
from ..shared.database import db
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

    # 1. Check if email already exists
    existing = await db.vendor_members.find_one({"email": application.email.lower().strip()})
    if existing:
        raise HTTPException(status_code=400, detail="An application with this email already exists.")

    # 2. Create new VendorMember record
    member = VendorMember(
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
    await db.vendor_members.insert_one(member_dict)

    # 3. Initialize default availability settings
    availability = AvailabilitySettings(
        member_id=member.id,
        vendor_id=member.vendor_id,
        available=True
    )
    await db.vendor_availability.insert_one(availability.dict())

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
    member = await db.vendor_members.find_one({"id": member_id}, {"_id": 0})
    if not member:
        raise HTTPException(status_code=404, detail="Application not found.")
        
    # Get document status
    docs = await db.vendor_documents.find({"member_id": member_id}, {"_id": 0}).to_list(10)
    
    return {
        "name": member["name"],
        "status": member["onboarding_status"],
        "is_active": member["is_active"],
        "documents": docs
    }

@router.post("/documents")
async def upload_compliance_document(
    member_id: str = Form(...),
    doc_type: str = Form(...), # coi, wsib, hst_registry
    filename: str = Form(...),
    file_data: str = Form(...), # Base64
    expiry_date: Optional[str] = Form(None)
):
    """
    Upload Ontario compliance documents (COI, WSIB, etc).
    """
    member = await db.vendor_members.find_one({"id": member_id})
    if not member:
        raise HTTPException(status_code=404, detail="Member not found.")
        
    # Validation of doc_type for Ontario context
    valid_docs = ["coi", "wsib", "hst_registry", "id_verification"]
    if doc_type not in valid_docs:
        raise HTTPException(status_code=400, detail=f"Invalid doc_type. Must be one of {valid_docs}")

    # Process upload via existing storage service
    import base64
    try:
        raw_bytes = base64.b64decode(file_data)
    except Exception:
        raw_bytes = file_data.encode()

    # Upload to S3-compatible storage
    file_key, _, _ = await upload_file(
        raw_bytes, "onboarding", member_id, filename, "application/pdf"
    )

    doc = VendorDocument(
        vendor_id=member.get("vendor_id", "default_vendor"),
        member_id=member_id,
        doc_type=doc_type,
        file_url=file_key,
        expiry_date=expiry_date,
        status="pending"
    )
    
    await db.vendor_documents.insert_one(doc.dict())
    
    return {
        "success": True,
        "document_id": doc.id,
        "status": "pending"
    }
