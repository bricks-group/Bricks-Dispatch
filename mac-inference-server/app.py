"""
Bricks in-house object-removal inference server.

Runs on the user's own Apple Silicon Mac -- NOT part of the Vercel-deployed
backend/ FastAPI app (that's a separate deployable artifact, for different
hardware). See README.md in this directory for install/run/expose steps.

Exposes:
  GET  /health   -- reachability + model-load status check. Dispatch's
                    backend (backend/app/services/object_removal_service.py)
                    hits this before attempting /inpaint, so a sleeping/
                    offline Mac fails over to Gemini quickly instead of
                    hanging on a big multipart upload first.
  POST /inpaint   -- image + mask in (multipart/form-data), filled-in
                    (object-removed) image out (raw JPEG bytes).

Mask convention (matches the underlying LaMa model / simple-lama-inpainting
package): a single-channel image, same pixel dimensions as the source photo
(if not, this server resizes it to match) -- white (255) marks the region to
remove/fill in, black (0) means "leave this pixel alone".

Auth: every request must carry X-Mac-Inference-Key matching the
MAC_INFERENCE_KEY env var, checked with hmac.compare_digest. This server
gets exposed to the public internet via a Cloudflare Tunnel -- it needs
real auth, not just an obscure URL.

Model choice (researched 2026-09-12, see CLAUDE.md's Object Removal
section for the full writeup): LaMa (Large Mask Inpainting, Suvorov et al.
2021) via the `simple-lama-inpainting` pip package -- a small (~27M
param, ~200MB checkpoint) Fourier-convolution network purpose-built for
resolution-robust mask inpainting, not a diffusion model, so it's realistic
to run on an 8GB Apple Silicon Mac. MPS (Apple GPU) acceleration is
attempted by default with an automatic per-op CPU fallback (LaMa's FFC
layers aren't all implemented for Metal yet) -- see PYTORCH_ENABLE_MPS_FALLBACK
below. Images are downscaled to MAC_INFERENCE_MAX_DIMENSION before inference
(default 1536px) and the result upscaled back, to keep peak memory bounded
regardless of the original photo's resolution.
"""
import hmac
import io
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional, Tuple

# Must be set before any MPS op actually runs (not necessarily before torch
# is imported -- PyTorch reads this at op-dispatch time). Setting it this
# early is still the right place: it guarantees the env var exists by the
# time _load_model() below does its own `import torch`, regardless of
# import order elsewhere in the process.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mac_inference_server")

MAC_INFERENCE_KEY_ENV = "MAC_INFERENCE_KEY"

# Long edge a photo is downscaled to before inference. LaMa's memory/time
# cost scales with pixel count, and real estate photos routinely arrive at
# 4000px+ on the long edge -- uncapped, that risks exhausting an 8GB
# machine's RAM on a single request. 1536 is a conservative default that
# still holds plenty of detail for a listing photo; override via env if the
# Mac gets more RAM later (per the ecosystem's stated upgrade plan).
MAX_DIMENSION = int(os.environ.get("MAC_INFERENCE_MAX_DIMENSION", "1536"))

# auto (prefer Apple GPU, fall back to CPU) | cpu | mps
DEVICE_PREF = os.environ.get("MAC_INFERENCE_DEVICE", "auto").lower()

_model = None
_model_load_error: Optional[str] = None
_device_used: Optional[str] = None


def _pick_device():
    import torch

    if DEVICE_PREF == "cpu":
        return torch.device("cpu")
    if DEVICE_PREF == "mps":
        if torch.backends.mps.is_available():
            return torch.device("mps")
        logger.warning("MAC_INFERENCE_DEVICE=mps requested but MPS is not available on this machine -- using cpu")
        return torch.device("cpu")
    # auto
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def _load_model() -> None:
    """
    Load the LaMa inpainting model once, at process startup. Any failure
    (no internet on first run to fetch the ~200MB checkpoint, missing
    dependency, etc.) is captured rather than raised, so the server still
    starts and /health can report the real problem instead of the process
    just failing to boot with an opaque traceback.
    """
    global _model, _model_load_error, _device_used
    try:
        from simple_lama_inpainting import SimpleLama

        device = _pick_device()
        _device_used = str(device)
        logger.info(f"Loading LaMa inpainting model on device={_device_used} ...")
        _model = SimpleLama(device=device)
        logger.info("LaMa model loaded successfully")
    except Exception as e:  # noqa: BLE001 -- deliberately broad, see docstring
        _model_load_error = f"{type(e).__name__}: {e}"
        logger.error(f"Failed to load LaMa model: {_model_load_error}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_model()
    yield


app = FastAPI(title="Bricks Mac Inference Server", lifespan=lifespan)


def _verify_key(provided: Optional[str]) -> None:
    """Fail-closed shared-secret check, same convention as this ecosystem's
    other internal auth (`_verify_callback_secret` in Dispatch's own
    post_production.py router, `verify_internal_api_key` in shared/auth.py):
    a missing server-side secret is a 500 (misconfiguration), a missing or
    wrong client-supplied key is a 401."""
    expected = os.environ.get(MAC_INFERENCE_KEY_ENV, "")
    if not expected:
        raise HTTPException(status_code=500, detail="MAC_INFERENCE_KEY is not configured on this server")
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Mac-Inference-Key")


@app.get("/health")
async def health():
    return {
        "status": "ok" if _model is not None else "model_unavailable",
        "model_loaded": _model is not None,
        "model_load_error": _model_load_error,
        "device": _device_used,
    }


def _resize_for_inference(img: Image.Image) -> Tuple[Image.Image, Tuple[int, int]]:
    """Downscale to MAX_DIMENSION on the long edge. Returns the (possibly
    unchanged) image plus the ORIGINAL size, so the caller can upscale the
    inpainted result back to match the photo that was actually uploaded."""
    original_size = img.size
    longest = max(img.size)
    if longest <= MAX_DIMENSION:
        return img, original_size
    scale = MAX_DIMENSION / float(longest)
    new_size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
    return img.resize(new_size, Image.LANCZOS), original_size


@app.post("/inpaint")
async def inpaint(
    image: UploadFile = File(...),
    mask: UploadFile = File(...),
    x_mac_inference_key: Optional[str] = Header(default=None, alias="X-Mac-Inference-Key"),
):
    _verify_key(x_mac_inference_key)

    if _model is None:
        raise HTTPException(
            status_code=503,
            detail=f"Inpainting model unavailable: {_model_load_error or 'not loaded'}",
        )

    try:
        image_bytes = await image.read()
        mask_bytes = await mask.read()
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        mask_img = Image.open(io.BytesIO(mask_bytes)).convert("L")
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not read image/mask: {type(e).__name__}: {e}")

    if mask_img.size != img.size:
        # Be forgiving of a caller-supplied mask that isn't pixel-identical
        # to the source photo's dimensions (e.g. drawn against a downscaled
        # preview) -- nearest-neighbor keeps the mask a hard edge, not a
        # blurred one, before LaMa gets it.
        mask_img = mask_img.resize(img.size, Image.NEAREST)

    resized_img, original_size = _resize_for_inference(img)
    resized_mask = mask_img.resize(resized_img.size, Image.NEAREST)

    start = time.monotonic()
    try:
        result = _model(resized_img, resized_mask)
    except Exception as e:
        logger.error(f"Inpainting inference failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Inference failed: {type(e).__name__}: {e}")
    duration_ms = int((time.monotonic() - start) * 1000)

    if result.size != original_size:
        result = result.resize(original_size, Image.LANCZOS)

    buf = io.BytesIO()
    result.convert("RGB").save(buf, format="JPEG", quality=92)
    logger.info(
        f"inpaint outcome=success size={original_size} device={_device_used} duration_ms={duration_ms}"
    )
    return Response(content=buf.getvalue(), media_type="image/jpeg")
