"""QC flags murky listing stills and holds very dark ones; real dusk exempt."""
from __future__ import annotations

import cv2
import numpy as np

from app.services.qc_opencv import analyze_image_cv


def _room(level: int, w: int = 1600, h: int = 1200) -> bytes:
    rng = np.random.default_rng(3)
    noisy = np.full((h, w, 3), level, np.int16) + rng.integers(-25, 25, (h, w, 3))
    img = noisy.clip(0, 255).astype(np.uint8)
    for x in range(100, w - 100, 140):  # texture so blur check stays quiet
        cv2.rectangle(img, (x, 200), (x + 60, h - 200), (min(255, level + 40),) * 3, 4)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    return buf.tobytes()


def test_bright_edit_has_no_exposure_issue():
    res = analyze_image_cv(_room(150))
    assert "underexposed" not in res["issues"]
    assert res["metrics"]["median_luma"] > 120


def test_murky_still_is_flagged_not_held():
    res = analyze_image_cv(_room(60))
    assert "underexposed" in res["issues"]
    # underexposed alone costs 15 → Review, not a hold
    assert res["status"] in ("flagged", "passed") or len(res["issues"]) > 1


def test_very_dark_still_is_held():
    res = analyze_image_cv(_room(35))
    assert "underexposed" in res["issues"]
    assert res["status"] == "rejected"


def test_twilight_is_exempt():
    res = analyze_image_cv(_room(35), is_twilight=True)
    assert "underexposed" not in res["issues"]
