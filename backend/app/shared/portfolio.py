"""Portfolio sample types aligned with the Bricks www media catalog."""

from __future__ import annotations

from typing import Any, Iterable, Optional

# Match Bricks catalog deliverable kinds shown on www / Hiring.
PORTFOLIO_MEDIA_TYPES = (
    "hdr",
    "drone",
    "floor_plan",
    "tour",
    "twilight",
    "reel",
)

PORTFOLIO_MEDIA_LABELS = {
    "hdr": "HDR photo",
    "drone": "Drone",
    "floor_plan": "Floor plan",
    "tour": "Tour",
    "twilight": "Twilight",
    "reel": "Reel",
}

PORTFOLIO_DOC_PREFIX = "portfolio_"


def normalize_media_type(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    key = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "hdr": "hdr",
        "hdr_photo": "hdr",
        "photo": "hdr",
        "photos": "hdr",
        "drone": "drone",
        "aerial": "drone",
        "floor_plan": "floor_plan",
        "floorplan": "floor_plan",
        "floor_plans": "floor_plan",
        "tour": "tour",
        "virtual_tour": "tour",
        "twilight": "twilight",
        "reel": "reel",
        "cinematic_reel": "reel",
        "video": "reel",
    }
    key = aliases.get(key, key)
    return key if key in PORTFOLIO_MEDIA_TYPES else None


def portfolio_doc_type(media_type: str) -> str:
    return f"{PORTFOLIO_DOC_PREFIX}{media_type}"


def media_type_from_doc_type(doc_type: Optional[str]) -> Optional[str]:
    if not doc_type or not str(doc_type).startswith(PORTFOLIO_DOC_PREFIX):
        return None
    return normalize_media_type(str(doc_type)[len(PORTFOLIO_DOC_PREFIX) :])


def sanitize_portfolio_samples(raw: Optional[Iterable[Any]]) -> list[dict]:
    """Normalize applicant portfolio_samples payload → list of {media_type, url?, link?}."""
    out: list[dict] = []
    if not raw:
        return out
    for item in raw:
        if isinstance(item, str):
            link = item.strip()
            if link:
                out.append({"media_type": "hdr", "link": link})
            continue
        if not isinstance(item, dict):
            continue
        mt = normalize_media_type(item.get("media_type") or item.get("type") or item.get("kind"))
        if not mt:
            continue
        url = (item.get("url") or item.get("file_url") or item.get("file_key") or "").strip() or None
        link = (item.get("link") or item.get("portfolio_link") or "").strip() or None
        thumb = (item.get("thumb_url") or item.get("thumbnail_url") or "").strip() or None
        filename = (item.get("filename") or "").strip() or None
        entry: dict[str, Any] = {"media_type": mt}
        if url:
            entry["url"] = url
        if link:
            entry["link"] = link
        if thumb:
            entry["thumb_url"] = thumb
        if filename:
            entry["filename"] = filename
        if url or link:
            out.append(entry)
    return out


def assemble_portfolio_samples(
    member: Optional[dict],
    documents: Optional[Iterable[dict]] = None,
) -> list[dict]:
    """Merge member.portfolio_samples + portfolio_* documents + supplemental links."""
    by_type: dict[str, dict] = {}

    stored = (member or {}).get("portfolio_samples")
    if isinstance(stored, list):
        for sample in sanitize_portfolio_samples(stored):
            by_type[sample["media_type"]] = {**by_type.get(sample["media_type"], {}), **sample}

    for doc in documents or []:
        mt = media_type_from_doc_type(doc.get("doc_type"))
        if not mt:
            continue
        entry = {
            "media_type": mt,
            "url": doc.get("file_url") or "",
            "document_id": doc.get("id"),
            "status": doc.get("status") or "pending",
            "label": PORTFOLIO_MEDIA_LABELS.get(mt, mt),
        }
        prev = by_type.get(mt) or {}
        by_type[mt] = {**prev, **{k: v for k, v in entry.items() if v}}

    links = (member or {}).get("portfolio_links") or []
    for link in links:
        s = str(link or "").strip()
        if not s:
            continue
        if "hdr" not in by_type:
            by_type["hdr"] = {"media_type": "hdr", "link": s, "label": PORTFOLIO_MEDIA_LABELS["hdr"]}
        elif not by_type["hdr"].get("link"):
            by_type["hdr"]["link"] = s

    ordered: list[dict] = []
    for mt in PORTFOLIO_MEDIA_TYPES:
        if mt in by_type:
            row = dict(by_type[mt])
            row.setdefault("label", PORTFOLIO_MEDIA_LABELS.get(mt, mt))
            ordered.append(row)
    return ordered


def supplemental_links_from_samples(samples: list[dict]) -> list[str]:
    links: list[str] = []
    for s in samples:
        link = (s.get("link") or "").strip()
        if link and link not in links:
            links.append(link)
    return links
