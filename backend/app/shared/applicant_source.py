"""'How did you hear about Bricks?' on the vendor application.

Stored as vendor_members.source (a fixed key) and vendor_members.source_detail
(short free text: the "Other" answer, or an optional referrer name). Older
applicants and older clients (GO app, iOS) leave both null.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

# Key -> label. Order matches the www.brxs.ca/photographers select.
SOURCE_LABELS = {
    "instagram": "Instagram",
    "facebook": "Facebook",
    "linkedin": "LinkedIn",
    "tiktok": "TikTok",
    "google": "Google search",
    "indeed": "Indeed",
    "craigslist_kijiji": "Craigslist/Kijiji",
    "referral_photographer": "Referral from a photographer",
    "referral_realtor": "Referral from a realtor/brokerage",
    "event": "Event or meetup",
    "other": "Other",
}
REFERRAL_SOURCES = {"referral_photographer", "referral_realtor"}
SOURCE_DETAIL_MAX = 80


class ApplicantSourceError(ValueError):
    pass


def _clean_detail(raw) -> str:
    return " ".join(str(raw or "").split())[:SOURCE_DETAIL_MAX].strip()


def normalize_applicant_source(source, detail) -> Tuple[Optional[str], Optional[str]]:
    """Return (source, source_detail) to store. Blank source stays null.

    Other needs a short answer. A referral may carry a name. Any other choice
    drops the detail.
    """
    key = str(source or "").strip().lower()
    if not key:
        return None, None
    if key not in SOURCE_LABELS:
        raise ApplicantSourceError("Please choose how you heard about Bricks.")
    text = _clean_detail(detail)
    if key == "other":
        if not text:
            raise ApplicantSourceError("Please tell us where you heard about Bricks.")
        return key, text
    if key in REFERRAL_SOURCES:
        return key, (text or None)
    return key, None


_BANNED = re.compile(r"\S*(jev|poor-fit|yes-probability)\S*", re.IGNORECASE)
_DECIMAL = re.compile(r"\d+\.\d+")


def found_us_line(source, detail=None) -> str:
    """One line for the owner email: 'Found us via: X'. Empty for legacy null."""
    key = str(source or "").strip().lower()
    label = SOURCE_LABELS.get(key)
    if not label:
        return ""
    text = _clean_detail(detail)
    # Keep the notification rules: no Jev name and no score-like number.
    text = " ".join(_DECIMAL.sub("", _BANNED.sub("", text)).split())
    if text and (key == "other" or key in REFERRAL_SOURCES):
        return f"Found us via: {label} ({text})"
    return f"Found us via: {label}"
