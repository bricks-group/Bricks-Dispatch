from fastapi import APIRouter

router = APIRouter()

@router.get("/", summary="Dispatch service root")
async def dispatch_root():
    return {"message": "Welcome to the Dispatch Service"}
