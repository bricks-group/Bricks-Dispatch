"""
Standalone sky-replacement service. Runs on its own host (Oracle Cloud
Always Free -- see sky-service/README.md), not on Vercel, because the
model + torch + transformers stack is too large for Vercel's serverless
function size limit. Called over HTTP by Bricks-Dispatch's backend.
"""

import logging

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response

from sky_segmentation import replace_sky

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Bricks Sky Segmentation Service")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/replace-sky")
async def replace_sky_endpoint(file: UploadFile = File(...)):
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty file")
    try:
        result_bytes = replace_sky(image_bytes)
    except Exception:
        logger.exception("Sky replacement failed")
        raise HTTPException(status_code=500, detail="Sky replacement failed")
    return Response(content=result_bytes, media_type="image/jpeg")
