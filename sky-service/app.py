"""
Standalone sky-replacement service. Runs on its own host, not on Vercel,
because even this lightweight ONNX model needs more headroom than
Vercel's serverless function size limit allows. Called over HTTP by
Bricks-Dispatch's backend. See sky-service/README.md for deploy targets
and sky_segmentation.py for why replace_sky() shells out to two
subprocesses instead of running inline.
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
