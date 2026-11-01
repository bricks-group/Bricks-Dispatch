"""QC scores the downscaled review variant; its byte size must not cost -15.

Sandbox E2E (Oct 31): every in-house HDR still and the drone came back
"flagged 85 — low_filesize" because the ~290 KB qc variant was size-checked.
"""
import io

import numpy as np
from PIL import Image

from app.services.qc_opencv import analyze_image_cv


def _small_good_jpeg() -> bytes:
    rng = np.random.default_rng(0)
    base = np.tile(np.linspace(60, 200, 1024, dtype=np.float32), (683, 1))
    img = np.clip(base[..., None] + rng.normal(0, 18, (683, 1024, 3)), 0, 255).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="JPEG", quality=82)
    data = buf.getvalue()
    assert len(data) < 500_000
    return data


def test_full_file_still_penalised_for_low_filesize():
    r = analyze_image_cv(_small_good_jpeg())
    assert "low_filesize" in r["issues"]


def test_qc_variant_skips_filesize_check():
    data = _small_good_jpeg()
    full = analyze_image_cv(data)
    var = analyze_image_cv(data, check_filesize=False)
    assert "low_filesize" not in var["issues"]
    assert var["score"] == full["score"] + 15
