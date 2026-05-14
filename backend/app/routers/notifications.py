from fastapi import APIRouter

router = APIRouter()


@router.get("/", tags=["notifications"])
async def list_notifications():
    """List notifications"""
    return []
