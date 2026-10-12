"""Automatic, evidence-based review of a photographer application.

Replaces the text-only fit guess on apply. Nothing here approves or rejects;
Kam makes the call in Bricks OS. What it does:

  1. Looks at the uploaded sample photos (and photos scraped from a portfolio
     site that actually loads) with a vision model, scored per image against
     RUBRIC -- the same criteria as the deliverable auto-QC (qc_agent._QC_PROMPT)
     plus listing-specific ones (window pulls, HDR blend, room coverage).
     The deliverable OpenCV pre-pass (qc_opencv.analyze_image_cv) runs on every
     image too, so sharpness / exposure / tilt use the same thresholds.
  2. Reads photo metadata (camera, phone, stock/copyright tags), hashes every
     image (sha256 + 64-bit dHash) and checks for duplicates in the set and
     across earlier applicants.
  3. Compares typed gear, city, availability and any RPAS claim to rules.
  4. Builds a transparent 0-100 score from weighted parts, each R/Y/G, and a
     verdict: recommend | needs_review | not_a_fit. Missing samples cap the
     score and the colour is grey "Incomplete", never green.

Every evidence bullet carries a source (verified / self_reported /
couldnt_check). If something was not looked at, it never says it was.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

import httpx

from . import applicant_evidence as ev

logger = logging.getLogger(__name__)

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
# Cheap, capable vision model on the existing OpenRouter key (picked 2026-10-12:
# caught tilt/dark/blown/cast/blur/watermark/non-RE on the eval set where
# gemini-2.5-flash-lite passed dark and blown frames). Override with env.
DEFAULT_VISION_MODEL = "google/gemini-3.1-flash-lite"
VISION_TIMEOUT = 45.0
VISION_CONCURRENCY = 5
VISION_MAX_SIDE = 1280
PORTFOLIO_MAX_IMAGES = 6

# ---------------------------------------------------------------------------
# The Bricks real estate rubric (written; shared by prompt, email and OS view).
# First six mirror the deliverable auto-QC prompt so applicants are judged the
# way their Bricks deliverables will be.
# ---------------------------------------------------------------------------
RUBRIC = (
    ("verticals", "Straight verticals",
     "Walls, door frames and cabinet edges are vertical and parallel to the frame edges; camera level, no keystone lean, no rolled horizon."),
    ("exposure", "Exposure / HDR blend",
     "Interior evenly lit, no crushed shadows or blown highlights; if bracketed, the blend looks natural (no halos, grey ceilings, flat 'HDR' look)."),
    ("windows", "Window pulls",
     "Views through windows are visible and not blown out to white, and the window edges have no glow or halo. Mark n/a when no window is in frame."),
    ("white_balance", "White balance",
     "Whites and ceilings read neutral white; no yellow, orange, blue or green cast; mixed light sources corrected."),
    ("composition", "Composition and room coverage",
     "Shows the room's layout from a corner or doorway at roughly chest height; the main features are in frame; nothing important cut off; sensible lens width (not fisheye-distorted)."),
    ("clutter", "Clutter and presentation",
     "Space presented cleanly: no clutter, toiletries, cords, trash, people, or photographer reflection."),
    ("sharpness", "Sharpness",
     "Crisp and in focus edge to edge, no motion blur or heavy noise."),
)
RUBRIC_KEYS = tuple(k for k, _l, _d in RUBRIC)
RUBRIC_LABEL = {k: l for k, l, _d in RUBRIC}
GRADE_VALUE = {"pass": 1.0, "minor": 0.5, "fail": 0.0}

_RUBRIC_TEXT = "\n".join(f'- "{k}" ({label}): {desc}' for k, label, desc in RUBRIC)
VISION_PROMPT = f"""You are reviewing ONE sample photo submitted by a photographer applying to shoot real estate listings for Bricks (Toronto).
Judge only what you can see, as a strict senior listing-photo editor. "pass" means it already meets a professional MLS listing standard with nothing to fix; any visible issue is at least "minor" (noticeable but deliverable after a quick fix); "fail" means it would be sent back (clearly tilted, dark, colour cast, blurry, blown windows, wrong subject). Use "n/a" only when the criterion cannot apply (e.g. no window in frame). Do not give every criterion "pass" by default; look for problems first.

Criteria:
{_RUBRIC_TEXT}

Also report:
- "is_real_estate": true only if this is a photo of a home or property for a listing (interior, exterior, aerial of a property, twilight exterior). Portraits, weddings, cars, landscapes, products, food, events, AI renders and floor plans are false.
- "subject": one of "interior", "exterior", "aerial", "twilight", "not_real_estate".
- "watermark": true if any visible watermark, logo, or text overlay is on the image; "watermark_text": the text if readable, else "".
- "looks_stock": true if it looks like a stock or marketing image rather than the applicant's own listing shoot (e.g. stock agency watermark, render, magazine layout).
- "note": at most 18 words, plain, the single most important observation.

Reply with JSON only:
{{"is_real_estate": true, "subject": "interior", "criteria": {{{", ".join(f'"{k}": "pass"' for k in RUBRIC_KEYS)}}}, "watermark": false, "watermark_text": "", "looks_stock": false, "note": ""}}"""

CERT_PROMPT = """This image should be a Transport Canada drone (RPAS) pilot certificate.
Reply with JSON only: {"is_tc_pilot_certificate": true, "level": "basic|advanced|complex|unknown", "certificate_number": "", "holder_name": ""}.
Copy the certificate number exactly as printed; "" if none is readable."""

# ---------------------------------------------------------------------------
# Score weights (Kam can tune). Parts that do not apply (no drone claim, no
# portfolio link) drop out and the rest are re-normalised to 100.
# ---------------------------------------------------------------------------
SCORE_WEIGHTS = {
    "samples": 45,      # sample-photo rubric (uploads; portfolio photos if no uploads)
    "gear": 15,         # typed kit vs GEAR_MINIMUMS
    "availability": 15,
    "location": 10,     # in the GTA (from the city picker)
    "portfolio": 5,     # optional links: do they load / could they be reviewed
    "drone": 10,        # only when RPAS is claimed
}
PART_LABEL = {
    "samples": "Sample photos",
    "gear": "Gear",
    "availability": "Availability",
    "location": "Location",
    "portfolio": "Portfolio links",
    "drone": "Drone cert",
}
INCOMPLETE_SCORE_CAP = 60
RECOMMEND_MIN_SCORE = 75
NOT_A_FIT_MAX_SCORE = 45

GREEN, YELLOW, RED, GREY = "green", "yellow", "red", "grey"
STATUS_WORD = {GREEN: "Good", YELLOW: "Check", RED: "Weak", GREY: "Missing"}

RECOMMEND, NEEDS_REVIEW, NOT_A_FIT = "recommend", "needs_review", "not_a_fit"
VERDICT_LABEL = {RECOMMEND: "Recommend", NEEDS_REVIEW: "Needs review", NOT_A_FIT: "Not a fit"}

_STOCK_MARKERS = (
    "shutterstock", "getty", "istock", "adobe stock", "adobestock", "dreamstime",
    "alamy", "depositphotos", "123rf", "pond5", "unsplash", "pexels", "pixabay", "stocksy",
)
_PHONE_MAKES = ("apple", "google", "samsung", "huawei", "xiaomi", "oneplus", "motorola", "oppo")


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------
def dhash_hex(img) -> str:
    """64-bit difference hash of a PIL image, 16 hex chars."""
    from PIL import Image

    g = img.convert("L").resize((9, 8), Image.LANCZOS)
    px = list(g.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            left = px[row * 9 + col]
            right = px[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:016x}"


def hamming_hex(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except (TypeError, ValueError):
        return 64


def read_metadata(data: bytes) -> dict:
    """Camera make/model, software, copyright and stock markers from EXIF/XMP."""
    from PIL import Image

    out = {"make": "", "model": "", "software": "", "copyright": "", "artist": "", "stock_marker": ""}
    try:
        with Image.open(io.BytesIO(data)) as im:
            exif = im.getexif()
            tags = {271: "make", 272: "model", 305: "software", 33432: "copyright", 315: "artist"}
            for tag, key in tags.items():
                v = exif.get(tag)
                if v:
                    out[key] = " ".join(str(v).replace("\x00", "").split())[:80]
    except Exception:
        pass
    blob = (data[:200_000] + data[-50_000:]).lower()
    meta_text = " ".join(str(out[k]) for k in ("copyright", "artist", "software")).lower()
    for marker in _STOCK_MARKERS:
        if marker in meta_text or marker.encode() in blob:
            out["stock_marker"] = marker
            break
    return out


def prepare_image(data: bytes) -> Optional[dict]:
    """Decode once: vision JPEG, thumbnail JPEG, hashes, size, metadata."""
    from PIL import Image
    from .image_io import open_rgb

    try:
        img = open_rgb(data)
    except Exception:
        return None
    w, h = img.size
    vis = img.copy()
    vis.thumbnail((VISION_MAX_SIDE, VISION_MAX_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    vis.save(buf, format="JPEG", quality=84)
    thumb = img.copy()
    thumb.thumbnail((640, 640), Image.LANCZOS)
    tbuf = io.BytesIO()
    thumb.save(tbuf, format="JPEG", quality=78)
    return {
        "width": w,
        "height": h,
        "vision_jpeg": buf.getvalue(),
        "thumb_jpeg": tbuf.getvalue(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "dhash": dhash_hex(img),
        "meta": read_metadata(data),
    }


def opencv_grades(data: bytes) -> dict:
    """Deliverable QC OpenCV pre-pass mapped onto rubric keys (worst wins later)."""
    try:
        from .qc_opencv import analyze_image_cv

        cv = analyze_image_cv(data)
    except Exception:
        return {}
    if cv.get("source") != "opencv":
        return {}
    m = cv.get("metrics") or {}
    issues = set(cv.get("issues") or [])
    grades: dict = {}
    tilt = m.get("roll_tilt_deg")
    if "tilted_verticals" in issues and tilt is not None:
        grades["verticals"] = "fail" if abs(tilt) >= 4.0 else "minor"
    if "soft_focus" in issues:
        grades["sharpness"] = "minor"
    if "underexposed" in issues:
        grades["exposure"] = "fail" if (m.get("median_luma") or 255) < 50 else "minor"
    elif "blown_highlights" in issues or "crushed_blacks" in issues:
        grades["exposure"] = "minor"
    return {"grades": grades, "metrics": m, "issues": sorted(issues - {"low_filesize"})}


def _worse(a: Optional[str], b: Optional[str]) -> Optional[str]:
    order = {"fail": 0, "minor": 1, "pass": 2}
    if a not in order:
        return b if b in order else a
    if b not in order:
        return a
    return a if order[a] <= order[b] else b


# ---------------------------------------------------------------------------
# Vision provider (OpenRouter, existing key)
# ---------------------------------------------------------------------------
def vision_model() -> str:
    return (os.environ.get("APPLICANT_VISION_MODEL") or DEFAULT_VISION_MODEL).strip()


def vision_configured() -> bool:
    return bool((os.environ.get("OPENROUTER_API_KEY") or "").strip())


def _parse_json(text: str) -> dict:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.startswith("json"):
            t = t[4:]
    start, end = t.find("{"), t.rfind("}")
    if start >= 0 and end > start:
        t = t[start:end + 1]
    return json.loads(t)


async def call_vision(prompt: str, jpeg: bytes, *, client: Optional[httpx.AsyncClient] = None) -> tuple:
    """(parsed_json, cost_usd). Raises on failure."""
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    body = {
        "model": vision_model(),
        "temperature": 0.1,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
        "usage": {"include": True},
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}},
            ],
        }],
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://brxs.ca",
        "X-Title": "Bricks applicant review",
    }
    own = client is None
    client = client or httpx.AsyncClient(timeout=VISION_TIMEOUT)
    try:
        resp = await client.post(OPENROUTER_CHAT_URL, json=body, headers=headers)
    finally:
        if own:
            await client.aclose()
    resp.raise_for_status()
    data = resp.json()
    text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
    usage = data.get("usage") or {}
    cost = usage.get("cost")
    try:
        cost = float(cost) if cost is not None else 0.0
    except (TypeError, ValueError):
        cost = 0.0
    return _parse_json(text), cost


def _clean_grades(raw: Any) -> dict:
    out = {}
    raw = raw if isinstance(raw, dict) else {}
    for k in RUBRIC_KEYS:
        v = str(raw.get(k) or "").strip().lower()
        out[k] = v if v in ("pass", "minor", "fail", "n/a") else "n/a"
    return out


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------
@dataclass
class ImageResult:
    ref: str                    # storage path or page image URL
    origin: str                 # upload | portfolio
    thumb_ref: str = ""         # storage path (upload) or URL (portfolio)
    reviewed: bool = False
    is_real_estate: Optional[bool] = None
    subject: str = ""
    grades: dict = field(default_factory=dict)
    watermark: bool = False
    watermark_text: str = ""
    looks_stock: bool = False
    note: str = ""
    meta: dict = field(default_factory=dict)
    sha256: str = ""
    dhash: str = ""
    width: int = 0
    height: int = 0
    cv_issues: list = field(default_factory=list)
    error: str = ""

    def quality(self) -> float:
        if not self.reviewed:
            return -2.0
        if self.is_real_estate is False:
            return -1.0
        vals = [GRADE_VALUE[g] for g in self.grades.values() if g in GRADE_VALUE]
        return sum(vals) / len(vals) if vals else 0.0

    def to_dict(self) -> dict:
        return {
            "ref": self.ref, "origin": self.origin, "thumb_ref": self.thumb_ref,
            "reviewed": self.reviewed, "is_real_estate": self.is_real_estate,
            "subject": self.subject, "grades": self.grades, "watermark": self.watermark,
            "watermark_text": self.watermark_text, "looks_stock": self.looks_stock,
            "note": self.note, "camera": " ".join(x for x in (self.meta.get("make"), self.meta.get("model")) if x),
            "stock_marker": self.meta.get("stock_marker") or "", "sha256": self.sha256,
            "dhash": self.dhash, "width": self.width, "height": self.height,
            "cv_issues": self.cv_issues, "error": self.error,
            "quality": round(self.quality(), 3),
        }


async def _review_one(img: ImageResult, data: bytes, client, sem, costs: list, use_vision: bool) -> None:
    prep = await asyncio.to_thread(prepare_image, data)
    if not prep:
        img.error = "unreadable"
        return
    img.sha256, img.dhash = prep["sha256"], prep["dhash"]
    img.width, img.height, img.meta = prep["width"], prep["height"], prep["meta"]
    img._thumb_jpeg = prep["thumb_jpeg"]  # type: ignore[attr-defined]
    cv = await asyncio.to_thread(opencv_grades, prep["vision_jpeg"])
    img.cv_issues = cv.get("issues", []) if cv else []
    if not use_vision:
        img.error = "vision_unavailable"
        return
    async with sem:
        try:
            parsed, cost = await call_vision(VISION_PROMPT, prep["vision_jpeg"], client=client)
            costs.append(cost)
        except Exception as exc:
            img.error = f"vision_error:{type(exc).__name__}"
            return
    img.reviewed = True
    img.is_real_estate = bool(parsed.get("is_real_estate"))
    img.subject = str(parsed.get("subject") or "")[:20]
    grades = _clean_grades(parsed.get("criteria"))
    for k, g in ((cv or {}).get("grades") or {}).items():
        if grades.get(k) != "n/a":
            grades[k] = _worse(grades.get(k), g)
    img.grades = grades
    img.watermark = bool(parsed.get("watermark"))
    img.watermark_text = str(parsed.get("watermark_text") or "")[:60]
    img.looks_stock = bool(parsed.get("looks_stock"))
    img.note = " ".join(str(parsed.get("note") or "").split())[:160]


def criterion_summary(images: list) -> dict:
    """Per criterion: status + words, over reviewed real estate images."""
    re_imgs = [i for i in images if i.reviewed and i.is_real_estate]
    out = {}
    for key, label, _d in RUBRIC:
        grades = [i.grades.get(key) for i in re_imgs if i.grades.get(key) in GRADE_VALUE]
        if not grades:
            out[key] = {"label": label, "status": GREY, "words": "Not assessed (no frames where it applies)." if re_imgs else "Not reviewed."}
            continue
        n = len(grades)
        p = grades.count("pass") / n
        f = grades.count("fail") / n
        if p >= 0.75 and f <= 0.1:
            status, words = GREEN, "Strong across the set."
        elif f >= 0.34:
            status, words = RED, "Weak in many frames."
        else:
            status, words = YELLOW, "Mixed: some frames fall short."
        out[key] = {"label": label, "status": status, "words": words}
    return out


def _status_from(value: float) -> str:
    return GREEN if value >= 0.75 else YELLOW if value >= 0.5 else RED


@dataclass
class ReviewInput:
    member_id: str
    name: str
    city: str
    vendor_type: str = "photographer"
    specialties: list = field(default_factory=list)
    equipment: dict = field(default_factory=dict)
    equipment_structured: bool = True
    portfolio_links: list = field(default_factory=list)
    sample_paths: list = field(default_factory=list)
    drone_claimed: bool = False
    rpas_level: str = ""
    rpas_number: str = ""
    rpas_cert_path: str = ""
    availability: Optional[dict] = None
    source: Optional[str] = None
    source_detail: Optional[str] = None


async def run_review(
    inp: ReviewInput,
    *,
    fetch_bytes=None,
    other_hashes: Optional[list] = None,
    portfolio_checker=None,
    page_scraper=None,
    use_vision: Optional[bool] = None,
    today: Optional[date] = None,
) -> dict:
    """Run every check that can run. Returns the stored result dict. Never raises."""
    started = time.monotonic()
    use_vision = vision_configured() if use_vision is None else use_vision
    if fetch_bytes is None:
        from .storage import download_bytes as fetch_bytes  # type: ignore
    if portfolio_checker is None:
        from .portfolio_check import check_portfolio as portfolio_checker  # type: ignore
    if page_scraper is None:
        from .portfolio_check import scrape_page_images as page_scraper  # type: ignore
    from .portfolio_check import OK_IMAGE, OK_PAGE, is_instagram, is_video_host

    evidence: list = []
    flags: list = []
    costs: list = []
    sem = asyncio.Semaphore(VISION_CONCURRENCY)
    images: list = []

    # ---- portfolio links: resolve, then scrape the ones that load ----------
    links = []
    for u in inp.portfolio_links or []:
        u = " ".join(str(u or "").split())
        if u.lower().startswith(("http://", "https://")) and "@" not in u and u not in links:
            links.append(u)
    check = None
    if links:
        try:
            check = await portfolio_checker(links)
        except Exception:
            check = None

    async with httpx.AsyncClient(timeout=VISION_TIMEOUT) as client:
        # uploads
        upload_tasks = []
        for path in (inp.sample_paths or [])[: ev.MAX_SAMPLES]:
            img = ImageResult(ref=path, origin="upload")
            images.append(img)

            async def _load_and_review(img=img):
                try:
                    data = await fetch_bytes(img.ref)
                except Exception:
                    data = None
                if not data:
                    img.error = "missing"
                    return
                if len(data) > ev.MAX_SAMPLE_BYTES:
                    img.error = "too_large"
                    return
                if not ev.sniff_mime(data):
                    img.error = "not_an_image"
                    return
                await _review_one(img, data, client, sem, costs, use_vision)

            upload_tasks.append(_load_and_review())

        # portfolio scrape (only pages that opened; never Instagram / video)
        scraped_pages: dict = {}
        opened = [c.url for c in (check.opened if check else []) if c.status in (OK_PAGE, OK_IMAGE)]
        scrape_targets = [u for u in opened if not is_instagram(u) and not is_video_host(u)]

        async def _scrape(u):
            try:
                scraped_pages[u] = await page_scraper(u, max_images=PORTFOLIO_MAX_IMAGES)
            except Exception:
                scraped_pages[u] = []

        await asyncio.gather(*upload_tasks, *(_scrape(u) for u in scrape_targets[:2]))
        port_tasks = []
        budget = PORTFOLIO_MAX_IMAGES
        for u in scrape_targets[:2]:
            for img_url, data in scraped_pages.get(u, [])[:budget]:
                img = ImageResult(ref=img_url, origin="portfolio", thumb_ref=img_url)
                images.append(img)
                port_tasks.append(_review_one(img, data, client, sem, costs, use_vision))
                budget -= 1
        await asyncio.gather(*port_tasks)

        # RPAS certificate photo
        cert_read = None
        cert_attached = bool(inp.rpas_cert_path)
        if inp.drone_claimed and cert_attached and use_vision:
            try:
                cert_bytes = await fetch_bytes(inp.rpas_cert_path)
                prep = await asyncio.to_thread(prepare_image, cert_bytes) if cert_bytes else None
                if prep:
                    parsed, cost = await call_vision(CERT_PROMPT, prep["vision_jpeg"], client=client)
                    costs.append(cost)
                    on_photo = re.sub(r"[\s-]", "", str(parsed.get("certificate_number") or "").upper())
                    typed = re.sub(r"[\s-]", "", str(inp.rpas_number or "").upper())
                    cert_read = {
                        "is_certificate": bool(parsed.get("is_tc_pilot_certificate")),
                        "level": str(parsed.get("level") or "")[:12],
                        "number_on_photo": str(parsed.get("certificate_number") or "")[:30],
                        "number_matches": (on_photo == typed) if (on_photo and typed) else None,
                    }
            except Exception as exc:
                logger.info("applicant review: cert read skipped (%s)", type(exc).__name__)

    uploads = [i for i in images if i.origin == "upload"]
    port_imgs = [i for i in images if i.origin == "portfolio"]
    up_reviewed = [i for i in uploads if i.reviewed]
    port_reviewed = [i for i in port_imgs if i.reviewed]
    is_photographer = (inp.vendor_type or "photographer") == "photographer"
    needs_samples = is_photographer and len(up_reviewed) < ev.MIN_SAMPLES
    missing_uploads = max(0, ev.MIN_SAMPLES - len([i for i in uploads if not i.error or i.reviewed]))

    # ---- duplicates / stock / watermark -----------------------------------
    hashed = [i for i in images if i.dhash]
    near_dupes = 0
    for a in range(len(hashed)):
        for b in range(a + 1, len(hashed)):
            if hashed[a].sha256 == hashed[b].sha256 or hamming_hex(hashed[a].dhash, hashed[b].dhash) <= 4:
                near_dupes += 1
    cross = []
    for i in hashed:
        for row in other_hashes or []:
            if row.get("member_id") == inp.member_id:
                continue
            if row.get("sha256") == i.sha256 or hamming_hex(row.get("dhash") or "", i.dhash) <= 4:
                cross.append((i, row.get("member_id")))
                break
    stock = [i for i in images if (i.meta or {}).get("stock_marker") or i.looks_stock]
    marked = [i for i in images if i.watermark]
    phones = [i for i in images if (i.meta or {}).get("make", "").lower().startswith(_PHONE_MAKES)]
    if cross:
        flags.append({"key": "duplicate_other", "source": ev.VERIFIED,
                      "text": f"{len(cross)} image{'s' if len(cross) != 1 else ''} match photos from another Bricks applicant."})
    if near_dupes:
        flags.append({"key": "duplicate_self", "source": ev.VERIFIED, "text": "Some samples are duplicates of each other."})
    if stock:
        flags.append({"key": "stock", "source": ev.VERIFIED,
                      "text": "Looks like stock or someone else's marketing image: " + ", ".join(
                          sorted({(i.meta or {}).get("stock_marker") or "visual check" for i in stock})) + "."})
    if marked:
        wm = sorted({i.watermark_text for i in marked if i.watermark_text})
        flags.append({"key": "watermark", "source": ev.VERIFIED,
                      "text": "Watermark or logo on some images" + (f" ({', '.join(wm[:2])})" if wm else "") + "."})
    if phones:
        flags.append({"key": "phone", "source": ev.VERIFIED, "text": "Some samples were shot on a phone (photo metadata)."})

    # ---- samples evidence --------------------------------------------------
    rubric_basis = up_reviewed if up_reviewed else port_reviewed
    criteria = criterion_summary(rubric_basis)
    re_share = (sum(1 for i in rubric_basis if i.is_real_estate) / len(rubric_basis)) if rubric_basis else 0.0
    if up_reviewed:
        evidence.append({"key": "samples", "ok": not needs_samples, "source": ev.VERIFIED,
                         "text": f"Reviewed {len(up_reviewed)} uploaded sample photo{'s' if len(up_reviewed) != 1 else ''} against the Bricks rubric."})
        if re_share < 0.8:
            evidence.append({"key": "real_estate", "ok": False, "source": ev.VERIFIED,
                             "text": "Some uploads aren't real estate photos." if re_share >= 0.5 else "Most uploads aren't real estate photos."})
    elif uploads:
        why = "the vision check was unavailable" if any(i.error == "vision_unavailable" or i.error.startswith("vision_error") for i in uploads) else "the files couldn't be read"
        evidence.append({"key": "samples", "ok": None, "source": ev.COULDNT_CHECK,
                         "text": f"{len(uploads)} uploaded photos weren't reviewed: {why}."})
    elif is_photographer:
        evidence.append({"key": "samples", "ok": False, "source": ev.COULDNT_CHECK,
                         "text": f"No sample photos uploaded (needs {ev.MIN_SAMPLES}-{ev.MAX_SAMPLES})."})

    # ---- portfolio evidence ------------------------------------------------
    port_part = None  # (value, status) or None when no links
    if links:
        lines_ok, lines_bad = [], []
        if check is None:
            evidence.append({"key": "portfolio", "ok": None, "source": ev.COULDNT_CHECK, "text": "Portfolio links weren't checked."})
            port_part = (0.5, YELLOW)
        else:
            vals = []
            for c in check.links:
                host = (re.sub(r"^https?://", "", c.url).split("/")[0]) or c.url
                if c.status == "instagram":
                    evidence.append({"key": "portfolio", "ok": None, "source": ev.COULDNT_CHECK,
                                     "text": f"Instagram ({host}) can't be reviewed automatically."})
                    vals.append(0.5)
                elif c.status == "failed":
                    reason = {"dns": "domain doesn't resolve", "timeout": "timed out", "parked": "parked or for-sale page",
                              "empty": "empty page", "blocked": "blocked address"}.get(c.detail, c.detail or "error")
                    if c.detail.startswith("http_"):
                        reason = f"HTTP {c.detail[5:]}"
                    evidence.append({"key": "portfolio", "ok": False, "source": ev.VERIFIED,
                                     "text": f"Portfolio {host} doesn't load ({reason})."})
                    vals.append(0.0)
                elif is_video_host(c.url):
                    evidence.append({"key": "portfolio", "ok": None, "source": ev.COULDNT_CHECK,
                                     "text": f"Video link ({host}) loads; video isn't reviewed."})
                    vals.append(0.5)
                else:
                    got = [i for i in port_reviewed if c.url in scraped_pages and any(i.ref == u for u, _d in scraped_pages.get(c.url, []))]
                    if got:
                        re_n = sum(1 for i in got if i.is_real_estate)
                        evidence.append({"key": "portfolio", "ok": re_n > 0, "source": ev.VERIFIED,
                                         "text": f"Portfolio {host} loads; reviewed {len(got)} photo{'s' if len(got) != 1 else ''} from it"
                                                 + ("" if re_n == len(got) else f", {len(got) - re_n} not real estate") + "."})
                        vals.append(1.0 if re_n else 0.25)
                    else:
                        evidence.append({"key": "portfolio", "ok": None, "source": ev.COULDNT_CHECK,
                                         "text": f"Portfolio {host} loads, but no photos could be pulled from the page (likely script-rendered)."})
                        vals.append(0.5)
            unchecked = [u for u in links if u not in {c.url for c in check.links}]
            for u in unchecked:
                evidence.append({"key": "portfolio", "ok": None, "source": ev.COULDNT_CHECK, "text": f"Not checked: {u}."})
            v = sum(vals) / len(vals) if vals else 0.5
            port_part = (v, _status_from(v) if v not in (0.5,) else YELLOW)
            if vals and min(vals) == 0.0:
                port_part = (v, RED if v < 0.5 else YELLOW)

    # ---- typed facts -------------------------------------------------------
    loc = ev.location_evidence(inp.city)
    evidence.append(loc)
    wants_drone = inp.drone_claimed or "drone" in [str(s).lower() for s in inp.specialties or []]
    gear = ev.equipment_evidence(inp.equipment, wants_drone=wants_drone)
    if not inp.equipment_structured:
        for g in gear:
            g["text"] = g["text"].rstrip(".") + " (from a free-text list)."
    evidence.extend(gear)
    if _equip_drone_without_cert(inp):
        flags.append({"key": "drone_no_cert", "source": ev.SELF_REPORTED,
                      "text": "Lists a drone but claims no pilot certificate: can't fly paid jobs."})
    rp = ev.rpas_evidence(inp.drone_claimed, inp.rpas_level, inp.rpas_number, bool(inp.rpas_cert_path), cert_read)
    if rp:
        evidence.append(rp)
    if cert_read and cert_read.get("is_certificate") is False:
        flags.append({"key": "cert_photo", "source": ev.VERIFIED, "text": "The attached cert photo doesn't look like a Transport Canada pilot certificate."})
    avail = ev.availability_evidence(inp.availability, today=today)
    evidence.append(avail)

    # ---- score parts -------------------------------------------------------
    parts: dict = {}
    if rubric_basis:
        vals = [i.quality() for i in rubric_basis]
        q = sum(max(0.0, v) for v in vals) / len(vals)  # non-real-estate counts as 0
        parts["samples"] = {"value": q, "status": _status_from(q)}
    else:
        parts["samples"] = {"value": 0.0, "status": GREY}
    gear_ok = sum(1 for g in gear if g["key"] in ("camera", "lens", "tripod") and g.get("ok") is True)
    gear_unknown = any(g["key"] == "camera" and g.get("ok") is None for g in gear)
    gv = gear_ok / 3.0
    parts["gear"] = {"value": gv, "status": GREEN if gear_ok == 3 else (YELLOW if gear_ok == 2 or gear_unknown else RED)}
    if avail["source"] == ev.COULDNT_CHECK:
        parts["availability"] = {"value": 0.0, "status": GREY}
    else:
        n = len(ev.limited_availability_reasons(inp.availability, today=today))
        av = 1.0 if n == 0 else 0.5 if n <= 2 else 0.0
        parts["availability"] = {"value": av, "status": GREEN if n == 0 else YELLOW if n <= 2 else RED}
    city_key = ev._fold(inp.city)
    if loc["ok"]:
        parts["location"] = {"value": 1.0, "status": GREEN}
    elif loc["source"] == ev.COULDNT_CHECK:
        parts["location"] = {"value": 0.0, "status": GREY}
    elif any(ev._fold(c) == city_key for c in ev.NEAR_GTA_CITIES) or "toronto" in city_key:
        parts["location"] = {"value": 0.5, "status": YELLOW}
    else:
        parts["location"] = {"value": 0.0, "status": RED}
    if port_part is not None:
        parts["portfolio"] = {"value": port_part[0], "status": port_part[1]}
    if inp.drone_claimed and rp:
        dv = 1.0 if rp.get("ok") is True else 0.5 if rp.get("ok") is None else 0.0
        if rp.get("ok") is False and bool(inp.rpas_cert_path) and ev.rpas_number_format(inp.rpas_number) == "pilot_cert":
            dv = 0.5
        if cert_read and cert_read.get("is_certificate") is False:
            dv = min(dv, 0.5)
        parts["drone"] = {"value": dv, "status": _status_from(dv)}
    total_w = sum(SCORE_WEIGHTS[k] for k in parts)
    raw_score = sum(SCORE_WEIGHTS[k] * parts[k]["value"] for k in parts) / total_w * 100 if total_w else 0
    for k in parts:
        parts[k]["weight"] = SCORE_WEIGHTS[k]
        parts[k]["label"] = PART_LABEL[k]
        parts[k]["points"] = round(SCORE_WEIGHTS[k] * parts[k]["value"] / total_w * 100, 1) if total_w else 0
        parts[k]["max_points"] = round(SCORE_WEIGHTS[k] / total_w * 100, 1) if total_w else 0
    incomplete = needs_samples or parts["samples"]["status"] == GREY
    score = int(round(raw_score))
    if incomplete:
        score = min(score, INCOMPLETE_SCORE_CAP)

    # ---- verdict -----------------------------------------------------------
    hard_flag = bool(cross) or len(stock) >= 2
    not_re = bool(rubric_basis) and re_share < 0.5 and len(rubric_basis) >= 3
    weak_criteria = sum(1 for c in criteria.values() if c["status"] == RED)
    if hard_flag or not_re or (rubric_basis and len(rubric_basis) >= 3 and (weak_criteria >= 3 or score <= NOT_A_FIT_MAX_SCORE)):
        verdict = NOT_A_FIT
    elif (
        not incomplete
        and score >= RECOMMEND_MIN_SCORE
        and re_share >= 0.8
        and weak_criteria == 0
        and not flags
        and ev.equipment_meets_minimum(gear)
        and loc["ok"]
        and not avail.get("limited")
        and parts["availability"]["status"] == GREEN
        and (not inp.drone_claimed or parts.get("drone", {}).get("status") == GREEN)
        and all(p["status"] != RED for p in parts.values())
    ):
        verdict = RECOMMEND
    else:
        verdict = NEEDS_REVIEW
    colour = {RECOMMEND: GREEN, NEEDS_REVIEW: YELLOW, NOT_A_FIT: RED}[verdict]
    if incomplete and verdict != NOT_A_FIT:
        colour = GREY

    # best / worst thumbnails (reviewed images only)
    ranked = sorted([i for i in images if i.reviewed], key=lambda i: i.quality(), reverse=True)
    picks = []
    if ranked:
        picks.append(("Best", ranked[0]))
        if len(ranked) >= 3:
            picks.append(("Best", ranked[1]))
        if len(ranked) >= 2:
            picks.append(("Weakest", ranked[-1]))

    missing = []
    if needs_samples:
        have = len(up_reviewed)
        missing.append(f"{ev.MIN_SAMPLES}-{ev.MAX_SAMPLES} uploaded real estate sample photos" + (f" (has {have})" if have else ""))
    if is_photographer and not inp.equipment_structured:
        missing.append("structured gear (camera body, lenses, tripod, lighting, drone)")
    if avail["source"] == ev.COULDNT_CHECK:
        missing.append("availability (days, time windows, notice, shoots/week, car + licence)")
    if loc["source"] == ev.COULDNT_CHECK or (not loc["ok"] and "/" in (inp.city or "")):
        missing.append("a single home city from the city picker")
    if inp.drone_claimed and not inp.rpas_cert_path:
        missing.append("RPAS certificate photo")
    if inp.drone_claimed and ev.rpas_number_format(inp.rpas_number) in ("missing", "drone_registration"):
        missing.append("pilot certificate number (PC-…), not a drone registration")
    if wants_drone and not inp.drone_claimed and inp.equipment.get("drone_model"):
        missing.append("RPAS certificate number, level and photo (if they want drone jobs)")

    cost = round(sum(costs), 5)
    return {
        "version": 1,
        "verdict": verdict,
        "verdict_label": VERDICT_LABEL[verdict],
        "colour": colour,
        "incomplete": incomplete,
        "needs_samples": needs_samples,
        "score": score,
        "score_cap": INCOMPLETE_SCORE_CAP if incomplete else None,
        "parts": parts,
        "weights": dict(SCORE_WEIGHTS),
        "criteria": criteria,
        "evidence": evidence,
        "flags": flags,
        "missing": missing,
        "images": [i.to_dict() for i in images],
        "thumbs": [{"label": lbl, "ref": i.thumb_ref or i.ref, "origin": i.origin, "note": i.note} for lbl, i in picks],
        "availability_summary": ev.availability_summary(inp.availability),
        "model": vision_model() if use_vision else None,
        "vision_ran": bool(costs) or any(i.reviewed for i in images),
        "cost_usd": cost,
        "reverse_image_search": "not run (no cheap API); EXIF, duplicate hashes and watermark/stock checks only",
        "elapsed_s": round(time.monotonic() - started, 1),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "_thumb_bytes": {i.ref: getattr(i, "_thumb_jpeg", None) for lbl, i in picks if i.origin == "upload"},
    }


def _equip_drone_without_cert(inp: ReviewInput) -> bool:
    return bool(ev._yes((inp.equipment or {}).get("drone_model"))) and not inp.drone_claimed


def review_input_from_member(member: dict, availability: Optional[dict] = None) -> ReviewInput:
    """Build the input from a stored vendor_members row (new or legacy)."""
    prof = member.get("application_profile") or {}
    if isinstance(prof, str):
        try:
            prof = json.loads(prof)
        except ValueError:
            prof = {}
    eq = prof.get("equipment") if isinstance(prof.get("equipment"), dict) else None
    structured = bool(eq)
    if not eq:
        eq = ev.equipment_from_list(member.get("equipment_list") or [])
    rp = prof.get("rpas") if isinstance(prof.get("rpas"), dict) else {}
    samples = [s.get("path") for s in (prof.get("samples") or []) if isinstance(s, dict) and s.get("path")]
    loc = prof.get("location") if isinstance(prof.get("location"), dict) else {}
    av = availability if (availability and availability.get("days_of_week")) else None
    return ReviewInput(
        member_id=str(member.get("id") or ""),
        name=member.get("name") or "",
        city=loc.get("city") or member.get("home_city") or "",
        vendor_type=member.get("vendor_type") or "photographer",
        specialties=list(member.get("skills") or prof.get("specialties") or ([member.get("specialty")] if member.get("specialty") else [])),
        equipment=eq,
        equipment_structured=structured,
        portfolio_links=list(member.get("portfolio_links") or []),
        sample_paths=samples,
        drone_claimed=bool(member.get("drone_certified")),
        rpas_level=rp.get("level") or member.get("rpas_certificate_level") or "",
        rpas_number=rp.get("number") or member.get("rpas_certificate_number") or "",
        rpas_cert_path=rp.get("cert_path") or member.get("rpas_certificate_url") or "",
        availability=av,
        source=member.get("source"),
        source_detail=member.get("source_detail"),
    )
