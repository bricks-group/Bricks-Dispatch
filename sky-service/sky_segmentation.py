"""
Sky replacement, orchestrating two subprocess phases (phase1_segment.py,
phase2_composite.py). Model: U-2-Net sky segmentation, INT8-quantized ONNX
(model/skyseg_int8.onnx), MIT licensed -- traced and verified directly
against the upstream LICENSE file at
https://github.com/xiongzhu666/Sky-Segmentation-and-Post-processing, not
just the Hugging Face tag re-hosting it at JianyuanWang/skyseg. See
model/LICENSE-skyseg-model.txt for the full license text and provenance.

No PyTorch, no transformers: pure onnxruntime + Pillow + numpy. This
replaces an earlier OneFormer/ADE20K-based design (see
project_bricks_inhouse_photo_enhancement memory) that was correct but had
an irreducible ~1.1GB+ activation-memory floor from its transformer
decoder -- too large for any card-free host. This U-2-Net model is a pure
CNN with no such floor.

Why two subprocesses instead of one function call: this model's inference
plus a full-resolution composite together peak around 850MB in a single
process, well over a card-free host's 512MB ceiling. Freeing objects with
del/gc.collect() inside one process does NOT return that memory to the OS
-- peak RSS is a lifetime high-water mark, not a live reading -- so the
only way to keep each phase's memory independent is to actually let a
process exit between them. Segmentation alone peaks ~503MB; compositing
alone (at a capped ~2400px output) peaks ~250MB. Both fit under 512MB
only because they never coexist in the same process.
"""

import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODEL_PATH = os.path.join(_HERE, "model", "skyseg_int8.onnx")
_PHASE1 = os.path.join(_HERE, "phase1_segment.py")
_PHASE2 = os.path.join(_HERE, "phase2_composite.py")

_SUBPROCESS_TIMEOUT_S = 60


def replace_sky(image_bytes: bytes, feather_radius: float = 4.0) -> bytes:
    """
    Segments the sky, generates a procedural replacement, composites with
    feathered edges and ambient-light matching. Returns JPEG bytes.

    Not wired into the default enhancement pipeline (photo_enhance_service.py)
    -- whether/when to apply sky replacement (e.g. only on exterior shots)
    is a routing decision, not made here. See Phase 3 in
    project_bricks_inhouse_photo_enhancement memory.
    """
    with tempfile.TemporaryDirectory() as tmp:
        input_path = os.path.join(tmp, "input.jpg")
        mask_path = os.path.join(tmp, "mask.png")
        output_path = os.path.join(tmp, "output.jpg")

        with open(input_path, "wb") as f:
            f.write(image_bytes)

        subprocess.run(
            [sys.executable, _PHASE1, input_path, mask_path, _MODEL_PATH],
            check=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
        )
        subprocess.run(
            [sys.executable, _PHASE2, input_path, mask_path, output_path, str(feather_radius)],
            check=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
        )

        with open(output_path, "rb") as f:
            return f.read()
