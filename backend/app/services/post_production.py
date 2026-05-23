import os
import httpx
import logging
from typing import List, Optional, Dict
from datetime import datetime, timezone
from ..shared.database import get_db

logger = logging.getLogger(__name__)

# --- Config ---
BOXBROWNIE_PUBLIC_KEY = os.environ.get("BOXBROWNIE_PUBLIC_KEY")
BOXBROWNIE_PRIVATE_KEY = os.environ.get("BOXBROWNIE_PRIVATE_KEY")
AUTOENHANCE_API_KEY = os.environ.get("AUTOENHANCE_API_KEY")
FOTELLO_API_KEY = os.environ.get("FOTELLO_API_KEY")

# --- BoxBrownie Helpers ---

def _build_bb_virtual_staging_xml(source_url: str, room_type: str, style_preset: str) -> str:
    room_map = {'Living Room': 'Living Room', 'Bedroom': 'Bedroom 1', 'Kitchen': 'Kitchen'}
    style_map = {'modern': 'Modern', 'coastal': 'Contemporary', 'scandi': 'Minimalist'}
    room_name = room_map.get(room_type, 'Living Room')
    furniture_style = style_map.get(style_preset, 'Modern')
    
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<job>
  <jobTitle>Bricks Virtual Staging - {room_name} - {furniture_style}</jobTitle>
  <virtualStagingType>VIRTUAL_STAGING</virtualStagingType>
  <fileGroup>
    <files><fileURL>{source_url}</fileURL></files>
    <options>
      <option><optionName>ROOM_NAME</optionName><optionValue>{room_name}</optionValue></option>
      <option><optionName>FURNITURE_STYLE</optionName><optionValue>{furniture_style}</optionValue></option>
    </options>
  </fileGroup>
</job>"""

async def trigger_boxbrownie_staging(source_url: str, room_type: str = "Living Room", style_preset: str = "modern") -> Optional[str]:
    if not BOXBROWNIE_PUBLIC_KEY or not BOXBROWNIE_PRIVATE_KEY:
        logger.error("BoxBrownie keys not configured")
        return None
    xml = _build_bb_virtual_staging_xml(source_url, room_type, style_preset)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://www.boxbrownie.com/api/v2/job/createVirtualStaging",
                headers={"Api-Public-Key": BOXBROWNIE_PUBLIC_KEY, "Api-Private-Key": BOXBROWNIE_PRIVATE_KEY},
                data={"xml": xml}
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("jobId") or data.get("job_id")
    except Exception as e:
        logger.exception(f"BoxBrownie staging request failed: {e}")
        return None

# --- Autoenhance Helpers ---

async def trigger_autoenhance_edit(image_url: str) -> Optional[str]:
    if not AUTOENHANCE_API_KEY:
        logger.error("AUTOENHANCE_API_KEY not configured")
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            img_res = await client.get(image_url)
            img_res.raise_for_status()
            resp = await client.post(
                "https://api.autoenhance.ai/v2/image",
                headers={"x-api-key": AUTOENHANCE_API_KEY},
                files={"image": ("image.jpg", img_res.content, "image/jpeg")}
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("image_id")
    except Exception as e:
        logger.exception(f"Autoenhance edit failed: {e}")
        return None

# --- Fotello Helpers ---

async def trigger_fotello_edit(image_url: str) -> Optional[str]:
    """
    Triggers AI-powered real estate photo enhancement via Fotello's API.
    Specifically leverages their premium window pulls, sky replacements,
    and automatic object removals.
    """
    if not FOTELLO_API_KEY:
        logger.error("FOTELLO_API_KEY not configured")
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Download the source image
            img_res = await client.get(image_url)
            img_res.raise_for_status()
            
            # Post to Fotello's endpoint using standard bearer token auth
            resp = await client.post(
                "https://api.fotello.co/v1/images",
                headers={
                    "Authorization": f"Bearer {FOTELLO_API_KEY}",
                    "Accept": "application/json"
                },
                files={"image": ("image.jpg", img_res.content, "image/jpeg")}
            )
            resp.raise_for_status()
            data = resp.json()
            # Fotello returns { "image_id": "img_..." } or { "job_id": "job_..." }
            return data.get("image_id") or data.get("job_id")
    except Exception as e:
        logger.exception(f"Fotello AI edit failed: {e}")
        return None

# --- Pipeline Orchestrator ---

async def process_job_assets(job_id: str):
    """
    Main entry point for post-production processing.
    Triggered when all assets are uploaded.
    """
    db = await get_db()
    res_job = await db.table("jobs").select("*").eq("id", job_id).maybe_single().execute()
    if not res_job.data: return
    
    job = res_job.data
    res_assets = await db.table("uploads").select("*").eq("job_id", job_id).execute()
    assets = res_assets.data
    
    logger.info(f"Starting post-production for job {job_id}")
    
    results = []
    for asset in assets:
        file_url = asset.get("file_url")
        filename = (asset.get("filename") or "").lower()
        if not file_url: continue
        
        # SKIP 3D assets (Virtual Visit / Gaussian Splats)
        if filename.endswith(".splat") or filename.endswith(".ply"):
            logger.info(f"Skipping processing for 3D asset: {filename}")
            continue
            
        # ROUTE based on metadata
        if asset.get("requires_staging"):
            bb_id = await trigger_boxbrownie_staging(file_url)
            if bb_id:
                results.append({"asset_id": asset["id"], "provider": "box_brownie", "provider_job_id": bb_id})
        else:
            if FOTELLO_API_KEY:
                ft_id = await trigger_fotello_edit(file_url)
                if ft_id:
                    results.append({"asset_id": asset["id"], "provider": "fotello", "provider_job_id": ft_id})
            elif AUTOENHANCE_API_KEY:
                ae_id = await trigger_autoenhance_edit(file_url)
                if ae_id:
                    results.append({"asset_id": asset["id"], "provider": "autoenhance", "provider_job_id": ae_id})

    for res in results:
        await db.table("uploads").update({
            "processing_status": "processing",
            "provider": res["provider"],
            "provider_job_id": res["provider_job_id"]
        }).eq("id", res["asset_id"]).execute()

    await db.table("jobs").update({
        "status": "processing",
        "updated_at": datetime.now(timezone.utc).isoformat()
    }).eq("id", job_id).execute()
