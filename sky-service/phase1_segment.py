"""
Phase 1 of the sky-replacement pipeline: segmentation only.

Run as a standalone subprocess (not imported), so its memory is fully
released back to the OS when it exits -- see sky_segmentation.py for why
that matters. Decodes the source image via libjpeg's draft mode, which
skips the full-resolution JPEG decode entirely since the model only ever
needs a fixed 320x320 input regardless of the photo's real resolution.
Writes a small (320x320-scale) grayscale mask; upsampling to the source's
full resolution is deferred to phase 2, which needs the full-res decode
anyway and has much more memory headroom to spare for it.
"""

import sys

import numpy as np
from PIL import Image
import onnxruntime

MODEL_INPUT_SIZE = 320
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def segment(image_path: str, model_path: str) -> Image.Image:
    image = Image.open(image_path)
    image.draft("RGB", (MODEL_INPUT_SIZE, MODEL_INPUT_SIZE))
    image = image.convert("RGB")
    resized = image.resize((MODEL_INPUT_SIZE, MODEL_INPUT_SIZE))

    x = np.asarray(resized, dtype=np.float32)
    x = (x / 255.0 - IMAGENET_MEAN) / IMAGENET_STD
    x = x.transpose(2, 0, 1)[None, ...].astype(np.float32)

    session = onnxruntime.InferenceSession(model_path, providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name
    result = session.run([output_name], {input_name: x})[0]
    result = np.squeeze(result)
    result = (result - result.min()) / (result.max() - result.min())

    return Image.fromarray((result * 255).astype(np.uint8))


if __name__ == "__main__":
    image_path, mask_out_path, model_path = sys.argv[1], sys.argv[2], sys.argv[3]
    mask = segment(image_path, model_path)
    mask.save(mask_out_path)
