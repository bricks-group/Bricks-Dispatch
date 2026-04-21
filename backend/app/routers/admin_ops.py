from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Header, HTTPException

from ..shared.auth import is_vendor_admin, get_vendor_member, resolve_vendor_actor_id
from ..shared.database import db
from ..shared.errors import api_error
from ..shared.pagination import normalize_pagination, paginated_response
from ..shared.schemas import VendorMemberUpdate, VendorLead

router = APIRouter(prefix="/api/admin", tags=["admin-ops"])

@router.post("/leads")
async def create_lead(
    payload: VendorLead,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    Record a new potential vendor lead discovered during acquisition.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
        
    lead_dict = payload.dict()
    await db.vendor_leads.insert_one(lead_dict)
    return lead_dict

@router.get("/leads")
async def list_leads(
    status: Optional[str] = None,
    city: Optional[str] = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    View the acquisition funnel.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
        
    query = {}
    if status: query["status"] = status
    if city: query["city"] = city
    
    leads = await db.vendor_leads.find(query, {"_id": 0}).sort("created_at", -1).to_list(100)
    return leads

@router.get("/applicants")
async def list_applicants(
    status: Optional[str] = "pending",
    page: int = 1,
    page_size: int = 20,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    List vendors in the onboarding pipeline.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions for vetting", "insufficient_permissions")
        
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    query = {"vendor_id": vendor_id, "onboarding_status": status}
    
    total = await db.vendor_members.count_documents(query)
    page, page_size, skip = normalize_pagination(page, page_size)
    
    applicants = await db.vendor_members.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(page_size).to_list(page_size)
    
    return paginated_response(applicants, total, page, page_size)

@router.patch("/applicants/{member_id}")
async def update_applicant_status(
    member_id: str,
    payload: VendorMemberUpdate,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    Update onboarding status or vetting details for an applicant.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
        
    vendor_id = actor_member.get("vendor_id", "default_vendor")
    
    update_data = {k: v for k, v in payload.dict().items() if v is not None}
    if not update_data:
        api_error(400, "No update data provided", "no_update_data")
        
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    # Special logic for final approval
    if update_data.get("onboarding_status") == "approved":
        update_data["is_active"] = True
        update_data["role"] = "Photographer" # Upgrade from 'Applicant'
    
    result = await db.vendor_members.update_one(
        {"id": member_id, "vendor_id": vendor_id},
        {"$set": update_data}
    )
    
    if result.matched_count == 0:
        api_error(404, "Applicant not found", "applicant_not_found")
        
    return await db.vendor_members.find_one({"id": member_id}, {"_id": 0})

@router.get("/applicants/{member_id}/documents")
async def get_applicant_documents(
    member_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    Retrieve compliance documents for a vendor.
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
        
    docs = await db.vendor_documents.find({"member_id": member_id}, {"_id": 0}).to_list(50)
    return docs

@router.put("/applicants/{member_id}/documents/{doc_id}/verify")
async def verify_document(
    member_id: str,
    doc_id: str,
    status: str = "verified", # verified or rejected
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """
    Approve or Reject a compliance document (COI, WSIB, etc).
    """
    actor_id = await resolve_vendor_actor_id(authorization, x_user_id)
    actor_member = await get_vendor_member(actor_id)
    
    if not is_vendor_admin(actor_member):
        api_error(403, "Insufficient permissions", "insufficient_permissions")
        
    result = await db.vendor_documents.update_one(
        {"id": doc_id, "member_id": member_id},
        {"$set": {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    
    if result.matched_count == 0:
        api_error(404, "Document not found", "document_not_found")
        
    return {"id": doc_id, "status": status}
