"""QC flags/holds stills left crooked past what straighten_verticals corrects,
without mistaking keystone (pitched camera) lean for a crooked frame."""
from __future__ import annotations

import cv2
import numpy as np

from app.services.qc_opencv import analyze_image_cv, measure_roll_tilt


def _scene(lean_left: float = 0.0, lean_right: float = 0.0, w: int = 1600, h: int = 1200):
    img = np.full((h, w, 3), 200, np.uint8)
    rng = np.random.default_rng(7)
    img = (img + rng.integers(-20, 20, img.shape)).clip(0, 255).astype(np.uint8)
    for i, x in enumerate(range(120, w - 100, 110)):
        lean = lean_left if x < w / 2 else lean_right
        off = int(round(np.tan(np.radians(lean)) * (h - 200)))
        cv2.line(img, (x, 100), (x + off, h - 100), (30, 30, 30), 6)
    return img


def _rotated(deg: float):
    img = _scene()
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
    return cv2.warpAffine(img, m, (w, h), borderValue=(200, 200, 200))


def _jpeg(img) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    return buf.tobytes()


def _gray(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def test_level_frame_has_no_tilt_issue():
    res = analyze_image_cv(_jpeg(_scene()))
    assert "tilted_verticals" not in res["issues"]
    tilt = res["metrics"]["roll_tilt_deg"]
    assert tilt is None or abs(tilt) < 1.0


def test_mild_roll_is_flagged_not_held():
    tilt = measure_roll_tilt(_gray(_rotated(2.5)))
    assert tilt is not None and 1.8 < abs(tilt) < 3.2
    res = analyze_image_cv(_jpeg(_rotated(2.5)))
    assert "tilted_verticals" in res["issues"]


def test_heavy_roll_is_held():
    res = analyze_image_cv(_jpeg(_rotated(6.0)))
    assert "tilted_verticals" in res["issues"]
    assert res["status"] == "rejected"


def test_keystone_lean_is_not_a_crooked_frame():
    img = _scene(lean_left=4.0, lean_right=-4.0)
    assert measure_roll_tilt(_gray(img)) is None
    res = analyze_image_cv(_jpeg(img))
    assert "tilted_verticals" not in res["issues"]


def test_sub_correction_roll_is_ignored():
    res = analyze_image_cv(_jpeg(_rotated(0.8)))
    assert "tilted_verticals" not in res["issues"]
