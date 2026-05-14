from fastapi import APIRouter

router = APIRouter()


@router.get("/", tags=["vendors"])
async def list_vendors():
    """List all vendors — implemented in vendor_os.router"""
    return []


@router.get("/{vendor_id}", tags=["vendors"])
async def get_vendor(vendor_id: str):
    """Get vendor by ID — implemented in vendor_os.router"""
    return {"vendor_id": vendor_id}
