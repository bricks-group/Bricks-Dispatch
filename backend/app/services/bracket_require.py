"""HDR bracket gate for Mark complete — mirrors web/frontend lib/bracket-require.ts.

Product law: vendors must upload bracketed stills (− / 0 / +) per room/angle
before a job can be marked complete. Soft-fail people/overlays stay soft;
missing brackets are a hard reject.

Visit/3D (.splat/.ply/.spz) are not listing stills — never counted toward
or against the stills bracket requirement. Drone/reel inferred from name/mime
are excluded. media_type is not a DB column; infer when absent.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from .media_intake import infer_media_type, is_visit_file
from .photo_enhance_service import bracket_stem

HDR_BRACKET_MIN = 3
BRACKET_HINT = "Upload bracketed exposures for each angle (− / 0 / +)."

_IMAGE_EXT = re.compile(
    r"\.(jpe?g|png|tif{1,2}|webp|heic|dng|cr2|nef|arw|raf|orf)$",
    re.IGNORECASE,
)

# Client media_type values that participate in the HDR stills bracket gate.
_HDR_MEDIA = frozenset({"hdr", "photo", "stills", ""})


def basename(path_or_name: str) -> str:
    raw = path_or_name or ""
    return raw.replace("\\", "/").rsplit("/", 1)[-1]


def is_image_name(name: str) -> bool:
    return bool(_IMAGE_EXT.search(name or ""))


def group_by_bracket_stem(filenames: Sequence[str]) -> Dict[str, List[str]]:
    buckets: Dict[str, List[str]] = {}
    for raw in filenames:
        name = basename(raw)
        if not name:
            continue
        key = bracket_stem(name) or name
        buckets.setdefault(key, []).append(name)
    return buckets


def _is_hdr_still_row(row: dict) -> bool:
    """True when this upload should be checked as an HDR listing still."""
    filename = row.get("filename") or ""
    if is_visit_file(filename):
        return False
    name = basename(str(filename))
    if not name or not is_image_name(name):
        return False

    raw_mt = str(row.get("media_type") or "").strip().lower()
    if raw_mt:
        return raw_mt in _HDR_MEDIA

    # DB rows omit media_type — infer so drone/reel/twilight/staging skip the gate.
    inferred = infer_media_type(filename, row.get("mime_type"), None)
    return inferred == "hdr"


def validate_hdr_uploads_for_complete(
    uploads: Sequence[dict] | None,
    *,
    allow_incomplete: bool = False,
    min_count: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Return an issue dict when any HDR stem is incomplete; else None.

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
        if len(files) < minimum:
            n = len(files)
            return {
                "stem": stem,
                "count": n,
                "filenames": files,
                "message": (
                    f'Cannot complete: HDR angle "{stem}" has only {n} exposure'
                    f'{"s" if n != 1 else ""} (need ≥{minimum}). '
                    f"{BRACKET_HINT}"
                ),
            }
    return None
