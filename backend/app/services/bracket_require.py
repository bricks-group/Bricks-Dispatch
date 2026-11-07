"""Bracket gate for Mark complete — mirrors web/frontend lib/bracket-require.ts.

Product law: vendors must upload bracketed stills (− / 0 / +) per room/angle
for HDR and twilight before a job can be marked complete. Soft-fail
people/overlays stay soft; missing brackets are a hard reject.

Count alone is not enough — three over-only (or under-only) frames must not
pass. Each stem needs at least one under, one mid, and one over exposure
(from ±EV / under / over filename tags).

Visit/3D (.splat/.ply/.spz) are not listing stills — never counted toward
or against the stills bracket requirement. Staging/drone/reel inferred from
name/mime are excluded. Twilight brackets are required (durable twilight_/
dusk cues when media_type is absent). media_type is not a DB column; infer
when absent.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from .media_intake import infer_media_type, is_visit_file
from .photo_enhance_service import bracket_stem, exposure_bias_from_name

HDR_BRACKET_MIN = 3
BRACKET_HINT = "Upload bracketed exposures for each angle (− / 0 / +)."

# Near-zero band for "mid" plate (matches enhance under/mid/over pick tolerance).
_MID_ABS = 0.5

_IMAGE_EXT = re.compile(
    r"\.(jpe?g|png|tif{1,2}|webp|heic|heif)$",
    re.IGNORECASE,
)

# Client media_type values that participate in the stills bracket gate
# (HDR + twilight). Staging/drone/reel stay excluded.
_HDR_MEDIA = frozenset({"hdr", "photo", "stills", "twilight", ""})


def basename(path_or_name: str) -> str:
    raw = path_or_name or ""
    return raw.replace("\\", "/").rsplit("/", 1)[-1]


def is_image_name(name: str) -> bool:
    """Listing still frame: camera JPEG/HEIC or camera RAW (all RAW formats)."""
    from .raw_decode import is_camera_raw_name

    return bool(_IMAGE_EXT.search(name or "")) or is_camera_raw_name(name)


def group_by_bracket_stem(filenames: Sequence[str]) -> Dict[str, List[str]]:
    buckets: Dict[str, List[str]] = {}
    for raw in filenames:
        name = basename(raw)
        if not name:
            continue
        key = bracket_stem(name) or name
        buckets.setdefault(key, []).append(name)
    return buckets


def bracket_has_under_mid_over(filenames: Sequence[str]) -> bool:
    """True when filenames cover under (<0), mid (~0), and over (>0) exposures."""
    biases: List[float] = []
    for raw in filenames:
        b = exposure_bias_from_name(basename(str(raw)))
        if b is not None:
            biases.append(float(b))
    if not biases:
        return False
    has_under = any(b < -0.05 for b in biases)
    has_mid = any(abs(b) <= _MID_ABS for b in biases)
    has_over = any(b > 0.05 for b in biases)
    return has_under and has_mid and has_over


def _is_hdr_still_row(row: dict) -> bool:
    """True when this upload should be checked as a bracket-gated listing still."""
    filename = row.get("filename") or ""
    if is_visit_file(filename):
        return False
    name = basename(str(filename))
    if not name or not is_image_name(name):
        return False

    raw_mt = str(row.get("media_type") or "").strip().lower()
    if raw_mt:
        return raw_mt in _HDR_MEDIA

    # DB rows omit media_type — infer so drone/reel/staging skip the gate;
    # twilight_/dusk cues participate (same as explicit media_type=twilight).
    inferred = infer_media_type(filename, row.get("mime_type"), None)
    return inferred in ("hdr", "twilight")


def _issue_for_stem(stem: str, files: List[str], minimum: int) -> Dict[str, Any]:
    n = len(files)
    if n < minimum:
        return {
            "stem": stem,
            "count": n,
            "filenames": files,
            "message": (
                f'Cannot complete: still angle "{stem}" has only {n} exposure'
                f'{"s" if n != 1 else ""} (need ≥{minimum}). '
                f"{BRACKET_HINT}"
            ),
        }
    # Count ok but missing − / 0 / + coverage (e.g. three over-only frames).
    return {
        "stem": stem,
        "count": n,
        "filenames": files,
        "message": (
            f'Cannot complete: still angle "{stem}" needs under, mid, and over '
            f"exposures (− / 0 / +), not {n} same-side frames. "
            f"Name files with ±EV (e.g. {stem}_-2.jpg, {stem}_0.jpg, {stem}_+2.jpg)."
        ),
    }


def validate_hdr_uploads_for_complete(
    uploads: Sequence[dict] | None,
    *,
    allow_incomplete: bool = False,
    min_count: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Return an issue dict when any HDR/twilight stem is incomplete; else None.

    Issue shape matches the TS BracketIssue for client parity:
      { stem, count, filenames, message }
    """
    if allow_incomplete:
        return None
    minimum = min_count if min_count is not None else HDR_BRACKET_MIN

    hdr_names: List[str] = []
    for row in uploads or []:
        if not isinstance(row, dict):
            continue
        if not _is_hdr_still_row(row):
            continue
        hdr_names.append(basename(str(row.get("filename") or "")))

    if not hdr_names:
        return None

    groups = group_by_bracket_stem(hdr_names)
    for stem, files in groups.items():
        if len(files) < minimum or not bracket_has_under_mid_over(files):
            return _issue_for_stem(stem, files, minimum)
    return None
