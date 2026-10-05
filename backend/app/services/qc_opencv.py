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


# Residual tilt. photo_enhance.straighten_verticals only auto-corrects up to
# ~1.5 deg, so a frame shot further off level reaches QC still crooked. A true
# camera roll leans verticals the SAME way on both halves of the frame; a
# pitched camera (keystone) leans them opposite ways, which is a perspective
# choice, not a crooked shot. So tilt only counts when both halves agree, and
# the reported tilt is the smaller of the two halves.
_TILT_WINDOW_DEG = 8.0
_TILT_FLAG_DEG = 1.5
_TILT_HOLD_DEG = 4.0


# Underexposed delivery. crushed_blacks only counts pure-zero pixels, so a
# fused still that came out murky overall (e.g. a Mertens merge that "reads
# dark") sailed through. Median luminance on the canonical 1024 frame: on 37
# finished pro listing edits (AutoHDR / BoxBrownie / Photomatix) the darkest
# edited interior sits at ~106; single unedited mid-exposure frames sit at
# ~36-57. Below the flag line goes to Review; below the hold line the still is
# held. Real dusk shots (twilight) are dark on purpose and are exempt.
_DARK_FLAG_MEDIAN = 70
_DARK_HOLD_MEDIAN = 50


def _weighted_median(pairs):
    pairs = sorted(pairs, key=lambda t: t[0])
    total = sum(w for _, w in pairs)
    acc = 0.0
    for val, w in pairs:
        acc += w
        if acc >= total / 2.0:
            return float(val)
    return float(pairs[-1][0])


def measure_roll_tilt(gray) -> Optional[float]:
    """Signed residual roll in degrees, or None when the frame has too little
    vertical structure (or the halves disagree) to judge."""
    import cv2
    import numpy as np

    h, w = gray.shape[:2]
    edges = cv2.Canny(gray, 60, 160, apertureSize=3)
    min_len = max(24, min(h, w) // 8)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 360, threshold=50, minLineLength=min_len, maxLineGap=12)
    if lines is None:
        return None
    left, right = [], []
    for line in lines.reshape(-1, 4):
        x1, y1, x2, y2 = [int(v) for v in line]
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0:
            continue
        ang = float(np.degrees(np.arctan2(dy, dx)))
        dev = ang - 90.0 if ang > 0 else ang + 90.0
        if abs(dev) > _TILT_WINDOW_DEG:
            continue
        length = float(np.hypot(dx, dy))
        bucket = left if (x1 + x2) / 2.0 < w / 2.0 else right
        bucket.append((dev, length))
    if len(left) < 3 or len(right) < 3:
        return None
    lm, rm = _weighted_median(left), _weighted_median(right)
    if lm * rm <= 0:
        return None
    return lm if abs(lm) < abs(rm) else rm


def analyze_image_cv(image_bytes: bytes, is_exterior: bool = False, is_twilight: bool = False) -> dict:
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
    hist_flat = hist.reshape(-1)
    total_pixels = gray.size
    dark_pixel_pct = float(hist_flat[0]) / total_pixels
    bright_pixel_pct = float(hist_flat[255]) / total_pixels

    if dark_pixel_pct > 0.05:
        issues.append("crushed_blacks")
        score -= 15
    if bright_pixel_pct > 0.05:
        issues.append("blown_highlights")
        score -= 15

    median_luma = float(np.median(blur_check_gray))
    if not is_twilight:
        if median_luma < _DARK_HOLD_MEDIAN:
            issues.append("underexposed")
            score -= 35
        elif median_luma < _DARK_FLAG_MEDIAN:
            issues.append("underexposed")
            score -= 15

    roll_tilt = None
    try:
        roll_tilt = measure_roll_tilt(blur_check_gray)
    except Exception as e:  # never let the tilt probe break QC
        logger.warning("qc_opencv: tilt probe skipped (%s)", e)
    if roll_tilt is not None:
        if abs(roll_tilt) >= _TILT_HOLD_DEG:
            issues.append("tilted_verticals")
            score -= 35
        elif abs(roll_tilt) >= _TILT_FLAG_DEG:
            issues.append("tilted_verticals")
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

    result = {
        "score": score,
        "status": status,
        "source": "opencv",
        "issues": issues,
        "metrics": {
            "laplacian_variance": laplacian_var,
            "dark_pixel_pct": round(dark_pixel_pct, 4),
            "bright_pixel_pct": round(bright_pixel_pct, 4),
            "aspect_ratio": round(aspect_ratio, 4),
            "median_luma": round(median_luma, 1),
            "roll_tilt_deg": None if roll_tilt is None else round(roll_tilt, 2),
        },
    }
    # PropTx 5.01(d) people soft-fail — quarantine for GO/MLS, never hard-reject here
    try:
        from .mls_people_detect import merge_people_into_qc
        result = merge_people_into_qc(result, image_bytes)
    except Exception as e:
        logger.warning("qc_opencv: people soft-fail skipped (%s)", e)
    return result


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
