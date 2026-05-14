from fastapi import APIRouter

router = APIRouter()


@router.get("/", tags=["jobs"])
async def list_jobs():
    """List all jobs — implemented in dispatch.router"""
    return []


@router.get("/{job_id}", tags=["jobs"])
async def get_job(job_id: str):
    """Get job by ID — implemented in dispatch.router"""
    return {"job_id": job_id}
