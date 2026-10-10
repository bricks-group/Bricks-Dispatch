"""
PropTx Art. 5.01(d) people / face soft-fail for MLS stills.

Detects faces via OpenCV FaceDetectorYN (YuNet ONNX). Soft-fail only:
never hard-blocks the enhance pipeline. GO/MLS publish path quarantines
when persons/faces are present.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_NAME = "face_detection_yunet_2023mar.onnx"
_DEFAULT_SCORE = 0.65
_DEFAULT_NMS = 0.3

# Exact soft-fail field names for Frame / GO consumers
FIELD_PEOPLE_DETECTED = "qc_people_detected"
FIELD_FACE_COUNT = "qc_face_count"
FIELD_MLS_QUARANTINE = "mls_quarantine"
FIELD_SOFT_FAIL = "qc_soft_fail"
FIELD_SOFT_FAIL_REASONS = "qc_soft_fail_reasons"
REASON_PERSONS = "proptx_5_01d_persons"


def _model_path() -> str:
    env = (os.getenv("MLS_FACE_MODEL_PATH") or "").strip()
    if env and os.path.isfile(env):
        return env
    return os.path.join(os.path.dirname(__file__), "models", _MODEL_NAME)


@lru_cache(maxsize=1)
def _yunet_available() -> Tuple[bool, str]:
    path = _model_path()
    if not os.path.isfile(path):
        return False, path
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False, path
    if not hasattr(__import__("cv2"), "FaceDetectorYN_create"):
        return False, path
    return True, path


def _decode_bgr(image_bytes: bytes):
    import cv2

    arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


def detect_faces(
    image_bytes: bytes,
    *,
    score_threshold: float = _DEFAULT_SCORE,
) -> Dict[str, Any]:
    """Return face boxes for an encoded image. Empty / fail-open on errors."""
    ok, path = _yunet_available()
    if not ok:
        return {
            "faces": [],
            "face_count": 0,
            "detector": "yunet_unavailable",
            "model_path": path,
        }
    try:
        import cv2

        img = _decode_bgr(image_bytes)
        if img is None:
            return {"faces": [], "face_count": 0, "detector": "yunet", "error": "unreadable"}
        h, w = img.shape[:2]
        # YuNet prefers modest input; scale long edge ~640 for speed
        long_edge = max(h, w)
        scale = 1.0
        work = img
        if long_edge > 640:
            scale = 640.0 / float(long_edge)
            work = cv2.resize(
                img,
                (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
                interpolation=cv2.INTER_AREA,
            )
        wh, ww = work.shape[:2]
        det = cv2.FaceDetectorYN_create(
            path, "", (ww, wh), float(score_threshold), _DEFAULT_NMS, 5000
        )
        _retval, faces = det.detect(work)
        out: List[Dict[str, Any]] = []
        if faces is not None:
            inv = 1.0 / scale if scale else 1.0
            for row in faces:
                x, y, bw, bh = [float(v) * inv for v in row[:4]]
                score = float(row[-1]) if len(row) > 4 else 0.0
                out.append({
                    "x": int(round(x)),
                    "y": int(round(y)),
                    "w": int(round(bw)),
                    "h": int(round(bh)),
                    "score": round(score, 4),
                })
        return {
            "faces": out,
            "face_count": len(out),
            "detector": "yunet",
            "model_path": path,
        }
    except Exception as e:
        logger.warning("mls_people_detect: YuNet failed (%s) — fail-open", e)
        return {
            "faces": [],
            "face_count": 0,
            "detector": "yunet_error",
            "error": type(e).__name__,
        }


def people_soft_fail_metadata(image_bytes: bytes) -> Dict[str, Any]:
    """
    PropTx 5.01(d) soft-fail metadata for MLS / GO path.

    Soft-fail only: callers must not hard-block enhance. When faces are
    detected, flag quarantine for GO review before MLS publish.
    """
    result = detect_faces(image_bytes)
    face_count = int(result.get("face_count") or 0)
    people = face_count > 0
    reasons: List[str] = [REASON_PERSONS] if people else []
    return {
        FIELD_PEOPLE_DETECTED: people,
        FIELD_FACE_COUNT: face_count,
        FIELD_MLS_QUARANTINE: people,
        FIELD_SOFT_FAIL: people,
        FIELD_SOFT_FAIL_REASONS: reasons,
        "people_detector": result.get("detector"),
        "people_faces": result.get("faces") or [],
    }


def merge_people_into_qc(cv_result: dict, image_bytes: bytes) -> dict:
    """Augment an analyze_image_cv result with people soft-fail (never hard-reject)."""
    meta = people_soft_fail_metadata(image_bytes)
    out = dict(cv_result)
    issues = list(out.get("issues") or [])
    metrics = dict(out.get("metrics") or {})
    metrics[FIELD_FACE_COUNT] = meta[FIELD_FACE_COUNT]
    metrics[FIELD_PEOPLE_DETECTED] = meta[FIELD_PEOPLE_DETECTED]

    if meta[FIELD_PEOPLE_DETECTED]:
        if "persons_detected" not in issues:
            issues.append("persons_detected")
        # Soft-fail: flag for GO / MLS quarantine — do not reject (would strand jobs)
        status = out.get("status") or "passed"
        if status == "passed":
            status = "flagged"
        # Cap score into flagged band if still "passed-looking"
        score = int(out.get("score", 100))
        if score >= 90:
            score = 85
        out["status"] = status
        out["score"] = score

    out["issues"] = issues
    out["metrics"] = metrics
    out[FIELD_PEOPLE_DETECTED] = meta[FIELD_PEOPLE_DETECTED]
    out[FIELD_FACE_COUNT] = meta[FIELD_FACE_COUNT]
    out[FIELD_MLS_QUARANTINE] = meta[FIELD_MLS_QUARANTINE]
    out[FIELD_SOFT_FAIL] = meta[FIELD_SOFT_FAIL]
    out[FIELD_SOFT_FAIL_REASONS] = meta[FIELD_SOFT_FAIL_REASONS]
    return out
