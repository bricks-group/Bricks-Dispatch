"""
In-house sky segmentation + replacement.

Fully self-hosted, free/open-source (Hugging Face `transformers` +
OneFormer + PyTorch, all MIT/BSD-family licensed) -- no third-party API,
no per-image cost. Runs as its own container (see Dockerfile in this
directory) rather than inside Bricks-Dispatch's Vercel function: the
model + torch + transformers stack is ~940MB, far past the 250MB hard
ceiling of Vercel's @vercel/python builder (see
project_bricks_inhouse_photo_enhancement memory for the full sizing
investigation).

Model: shi-labs/oneformer_ade20k_swin_tiny (MIT license, verified against
the official SHI-Labs/OneFormer GitHub LICENSE file, not just the HF tag).
Trained on ADE20K/Scene Parse 150, which includes an explicit "sky" class
(id 2) -- confirmed via model.config.id2label, not assumed.

Sky replacement (this file, not the segmentation model) generates its
replacement sky procedurally (gradient + soft cloud noise) rather than
pulling from a stock-photo asset library, so there's no separate
licensing/sourcing question for the replacement imagery itself.
"""

import io
import logging

import numpy as np
import torch
from PIL import Image, ImageFilter
from transformers import OneFormerForUniversalSegmentation, OneFormerProcessor

logger = logging.getLogger(__name__)

MODEL_ID = "shi-labs/oneformer_ade20k_swin_tiny"
SKY_CLASS_ID = 2  # ADE20K/Scene Parse 150 "sky" -- exact id, not string-matched
                   # (a naive `"sky" in label` substring match also catches
                   # "skyscraper", id 48 -- caught during Phase 2 testing).

_model = None
_processor = None


def _get_model():
    """Lazy singleton -- loaded once per container process, not per request."""
    global _model, _processor
    if _model is None:
        logger.info(f"Loading sky segmentation model: {MODEL_ID}")
        _processor = OneFormerProcessor.from_pretrained(MODEL_ID)
        _model = OneFormerForUniversalSegmentation.from_pretrained(MODEL_ID)
        _model.eval()
    return _model, _processor


def segment_sky_mask(img: Image.Image) -> np.ndarray:
    """Returns a boolean (H, W) mask, True where the model predicts sky."""
    model, processor = _get_model()
    inputs = processor(images=img, task_inputs=["semantic"], return_tensors="pt")
    with torch.no_grad():
        outputs = model(**inputs)
    predicted = processor.post_process_semantic_segmentation(
        outputs, target_sizes=[img.size[::-1]]
    )[0]
    return (predicted.numpy() == SKY_CLASS_ID)


def _generate_procedural_sky(width: int, height: int, seed: int | None = None) -> np.ndarray:
    """Clear-blue gradient with soft cloud texture, generated locally --
    no stock image dependency. RGB uint8 array."""
    rng = np.random.default_rng(seed)

    top_color = np.array([90, 150, 225], dtype=np.float32)
    horizon_color = np.array([200, 220, 235], dtype=np.float32)
    t = np.linspace(0, 1, height, dtype=np.float32).reshape(-1, 1, 1)
    gradient = top_color * (1 - t) + horizon_color * t
    sky = np.broadcast_to(gradient, (height, width, 3)).copy()

    # Soft clouds: blurred noise, lightly screened onto the gradient.
    noise = rng.random((height, width), dtype=np.float32)
    cloud_img = Image.fromarray((noise * 255).astype(np.uint8))
    cloud_img = cloud_img.filter(ImageFilter.GaussianBlur(radius=width / 25))
    clouds = np.asarray(cloud_img, dtype=np.float32) / 255.0
    clouds = np.clip((clouds - 0.5) * 2.5, 0, 1)  # sparsen -- only the brightest blobs read as cloud
    sky = sky + clouds[:, :, None] * (255 - sky) * 0.6

    return np.clip(sky, 0, 255).astype(np.uint8)


def _feather_mask(mask: np.ndarray, radius: float) -> np.ndarray:
    """Boolean mask -> soft-edged float mask in [0, 1] for blending."""
    mask_img = Image.fromarray((mask * 255).astype(np.uint8))
    mask_img = mask_img.filter(ImageFilter.GaussianBlur(radius=radius))
    return np.asarray(mask_img, dtype=np.float32) / 255.0


def _match_ambient_light(sky_rgb: np.ndarray, original_arr: np.ndarray, non_sky_mask: np.ndarray) -> np.ndarray:
    """Scale the generated sky's brightness to roughly match the photo's
    existing ambient light, so a replacement sky doesn't look pasted onto
    a differently-lit scene."""
    if non_sky_mask.sum() == 0:
        return sky_rgb
    scene_luma = float(original_arr[non_sky_mask].mean())
    sky_luma = float(sky_rgb.mean())
    if sky_luma <= 0:
        return sky_rgb
    # Modest correction only -- full luma matching would wash the sky's own
    # blue tone out to match e.g. a warm interior-lit exterior shot.
    factor = np.clip((scene_luma / sky_luma) ** 0.35, 0.7, 1.3)
    return np.clip(sky_rgb.astype(np.float32) * factor, 0, 255).astype(np.uint8)


def replace_sky(image_bytes: bytes, feather_radius: float = 4.0) -> bytes:
    """
    Segments the sky, generates a procedural replacement, composites with
    feathered edges and light ambient-matching. Returns JPEG bytes.

    Not wired into the default enhancement pipeline (photo_enhance_service.py)
    -- whether/when to apply sky replacement (e.g. only on exterior shots)
    is a routing decision, not made here. See Phase 3 in
    project_bricks_inhouse_photo_enhancement memory.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = img.size

    sky_mask = segment_sky_mask(img)
    feathered = _feather_mask(sky_mask, radius=feather_radius)

    original_arr = np.asarray(img, dtype=np.float32)
    replacement_sky = _generate_procedural_sky(width, height)
    replacement_sky = _match_ambient_light(replacement_sky, original_arr, ~sky_mask)

    alpha = feathered[:, :, None]
    composited = original_arr * (1 - alpha) + replacement_sky.astype(np.float32) * alpha
    result = Image.fromarray(np.clip(composited, 0, 255).astype(np.uint8), "RGB")

    buf = io.BytesIO()
    result.save(buf, format="JPEG", quality=90)
    return buf.getvalue()
