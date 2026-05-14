import asyncio
import logging
import os
import sys
from dotenv import load_dotenv

# Ensure the 'backend' directory is in the Python path for internal imports
sys.path.insert(0, os.path.dirname(__file__))

from supabase import create_client, Client
from backend.app.shared.database import get_db
from backend.app.services.vendor_selection import assign_vendor_for_job

# Load environment variables (e.g., SUPABASE_URL, SUPABASE_KEY)
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def retry_pending_assignments():
    """
    Finds jobs with 'pending_assignment' status and attempts to assign a vendor.
    """
    logger.info("Starting retry_pending_assignments script...")

    # Initialize Supabase client
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        logger.error("Supabase URL or Key not found in environment variables.")
        return

    db: Client = await get_db()

    logger.info("Querying for jobs with status 'pending_assignment'...")
    try:
        res = await db.table("jobs").select("id").eq("status", "pending_assignment").execute()
        jobs_to_retry = res.data
        
        if not jobs_to_retry:
            logger.info("No jobs found with 'pending_assignment' status.")
            return

        logger.info(f"Found {len(jobs_to_retry)} jobs to retry assignment for.")

        for job_data in jobs_to_retry:
            job_id = job_data["id"]
            logger.info(f"Attempting to assign vendor for job_id: {job_id}")
            try:
                assigned_vendor_id = await assign_vendor_for_job(job_id)
                if assigned_vendor_id:
                    logger.info(f"Successfully assigned vendor {assigned_vendor_id} to job {job_id}")
                else:
                    logger.warning(f"Failed to assign vendor for job {job_id} (no vendor chosen).")
            except Exception as e:
                logger.error(f"Error assigning vendor for job {job_id}: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"Error querying database for pending assignments: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(retry_pending_assignments())
