import logging
from typing import Optional

logger = logging.getLogger(__name__)

EXPECTED_DELIVERABLE_COUNT = {
    "standard": 25,
    "premium": 40,
    "luxury": 55,
    "estate": 70,
}


# Laplacian variance (the blur metric below) is resolution-dependent — the
# same photo scores lower after downsampling, since decimation removes the
# high-frequency content the metric measures. qc_agent.py can hand this
# function either a full-res original or a downscaled QC-review variant
# (see storage.py QC_VARIANT_SIZE), so the check canonicalizes to a fixed
# size first. That keeps the threshold below meaningful regardless of the
# input resolution, instead of drifting with whatever upstream happens to
# pass in.
_CANONICAL_BLUR_CHECK_SIZE = 1024


def analyze_image_cv(image_bytes: bytes, is_exterior: bool = False) -> dict:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return {"score": 100, "status": "passed", "source": "opencv_unavailable", "issues": [], "metrics": {}}

    score = 100
    issues = []

    if len(image_bytes) < 500_000:
        issues.append("low_filesize")
        score -= 15

    arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return {"score": 0, "status": "rejected", "source": "opencv", "issues": ["unreadable_image"], "metrics": {}}

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape

    longest_edge = max(height, width)
    if longest_edge > _CANONICAL_BLUR_CHECK_SIZE:
        scale = _CANONICAL_BLUR_CHECK_SIZE / longest_edge
        blur_check_gray = cv2.resize(
            gray, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA
        )
    else:
        blur_check_gray = gray

    laplacian_var = float(cv2.Laplacian(blur_check_gray, cv2.CV_64F).var())
    if laplacian_var < 100:
        issues.append("soft_focus")
        score -= 25

    hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
    total_pixels = gray.size
    dark_pixel_pct = float(hist[0][0]) / total_pixels
    bright_pixel_pct = float(hist[255][0]) / total_pixels

    if dark_pixel_pct > 0.05:
        issues.append("crushed_blacks")
        score -= 15
    if bright_pixel_pct > 0.05:
        issues.append("blown_highlights")
        score -= 15

    aspect_ratio = width / height if height > 0 else 0.0
    if is_exterior and height > width:
        issues.append("portrait_exterior")
        score -= 10

    score = max(0, min(100, score))

    if score >= 90:
        status = "passed"
    elif score >= 70:
        status = "flagged"
    else:
        status = "rejected"

    return {
        "score": score,
        "status": status,
        "source": "opencv",
        "issues": issues,
        "metrics": {
            "laplacian_variance": laplacian_var,
            "dark_pixel_pct": round(dark_pixel_pct, 4),
            "bright_pixel_pct": round(bright_pixel_pct, 4),
            "aspect_ratio": round(aspect_ratio, 4),
        },
    }


def verify_deliverable_count(package: str, image_count: int) -> dict:
    expected = EXPECTED_DELIVERABLE_COUNT.get(package)
    if expected is None:
        return {"ok": True, "expected": None, "actual": image_count, "issue": None}

    ok = image_count >= expected
    return {
        "ok": ok,
        "expected": expected,
        "actual": image_count,
        "issue": "insufficient_deliverables" if not ok else None,
    }
