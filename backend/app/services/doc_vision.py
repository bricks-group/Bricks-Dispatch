"""Document content check for applicant uploads (Kam, 2026-11-02).

Runs server-side in Dispatch (OpenRouter vision, key in Vercel env
OPENROUTER_API_KEY). For each uploaded credential / ID / proof file it reads
what is actually on the page and checks it point by point against what the
applicant entered:

  COI / E&O / drone / inventory insurance: insured name, coverage amount,
      expiry vs the entered expiry date, right kind of document
  Government ID: name, not expired, is a government photo ID
  OAHI / RHI / training, RPAS, police check: name, issuer, dates
  Proof of work (photos, reports, floor plans): readable, right kind

Each point is "verified", "unverified" (couldn't read it) or "mismatch".
Blank files are caught locally before any model call. The result is stored
on vendor_documents.validation_result.content_check and read by
trade_review. Never raises; soft-fails to "not_checked".
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import logging
import re
from datetime import date, datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

VERSION = "doc-vision-2026-11-02"
VERIFIED, UNVERIFIED, MISMATCH = "verified", "unverified", "mismatch"
# overall
OK, PARTIAL, BLANK, UNREADABLE, BAD, NOT_CHECKED = "verified", "partial", "blank", "unreadable", "mismatch", "not_checked"
PROBLEM = (BLANK, UNREADABLE, BAD)
MAX_BYTES = 15 * 1024 * 1024

INSURANCE = {"coi", "eo", "drone_liability", "inventory_insurance", "wsib"}
CERTS = {"rhi_or_training", "rpas_certificate", "police_check"}
IDENT = {"id_verification"}
PROOF = {"proof_photo", "before_after", "sample_report", "floor_plan", "portfolio"}

KIND_DESC = {
    "coi": "a certificate of insurance for commercial general liability",
    "eo": "a certificate of insurance for errors and omissions (professional liability)",
    "drone_liability": "a certificate of insurance covering drone / UAV / RPAS liability",
    "inventory_insurance": "an insurance certificate or policy covering furniture, inventory or contents",
    "wsib": "a WSIB clearance certificate",
    "rhi_or_training": "an OAHI Registered Home Inspector (RHI) certificate, a home inspector licence, or a home inspection training certificate",
    "rpas_certificate": "a Transport Canada RPAS (drone) pilot certificate, basic or advanced",
    "police_check": "a police record check / criminal background check",
    "id_verification": "a government-issued photo ID (driver's licence, passport, provincial ID or PR card)",
    "proof_photo": "a photo of the applicant's real work (e.g. a cleaned or staged room)",
    "before_after": "before/after photos of real handyman or repair work",
    "sample_report": "a sample home inspection report",
    "floor_plan": "a floor plan or measurement drawing",
    "portfolio": "a real estate or property photo",
}


def _group(kind: str) -> str:
    k = "portfolio" if kind.startswith("portfolio_") else kind
    if k in INSURANCE:
        return "insurance"
    if k in CERTS:
        return "cert"
    if k in IDENT:
        return "id"
    if k in PROOF:
        return "proof"
    return "other"


def _prompt(kind: str, today: date) -> str:
    k = "portfolio" if kind.startswith("portfolio_") else kind
    want = KIND_DESC.get(k, "a document")
    base = (
        f"Today is {today.isoformat()}. You are checking an uploaded file that should be {want}. "
        "Read only what is visibly on the file. Do not guess. Return strict JSON with these keys:\n"
        '  "blank": true if the page is empty, a solid colour, or has no meaningful content,\n'
        '  "readable": true if you can read the text / see the content clearly,\n'
        '  "doc_type_matches": true only if this really is the expected kind of document,\n'
        '  "what_it_is": a few words describing what the file actually is,\n'
        '  "looks_edited_or_sample": true if it looks like a template, specimen, watermark "SAMPLE", or obviously edited,\n'
    )
    g = _group(kind)
    if g == "insurance":
        base += (
            '  "insured_name": the named insured exactly as printed, or null,\n'
            '  "insurer": insurer / broker name or null,\n'
            '  "coverage_amount_cad": the main liability limit (per occurrence) as a number in dollars, or null,\n'
            '  "expiry_date": policy expiry date as YYYY-MM-DD, or null.\n'
        )
    elif g == "cert":
        base += (
            '  "holder_name": the person named on the certificate, or null,\n'
            '  "issuer": the issuing organisation, or null,\n'
            '  "issue_date": YYYY-MM-DD or null,\n'
            '  "expiry_date": YYYY-MM-DD or null,\n'
            '  "has_certificate_number": true if a certificate / registration number is printed (do NOT return the number).\n'
        )
    elif g == "id":
        base += (
            '  "holder_name": the full name on the ID, or null,\n'
            '  "id_kind": e.g. "Ontario driver\'s licence", "passport", or null,\n'
            '  "expiry_date": YYYY-MM-DD or null,\n'
            '  "has_photo": true if a face photo is on the ID.\n'
            "Do NOT return ID numbers, addresses or dates of birth.\n"
        )
    else:
        base += '  "shows_real_work": true if it shows real work of the expected kind (not a stock image, screenshot of text, or unrelated picture).\n'
    return base + "Return JSON only."


# ------------------------------------------------------------------ file prep
def _sniff(data: bytes, filename: str = "") -> str:
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or data[:4] in (b"RIFF", b"GIF8") or data[4:12] in (b"ftypheic", b"ftypmif1"):
        return "image"
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if ext in ("jpg", "jpeg", "png", "webp", "heic", "gif"):
        return "image"
    if ext == "pdf":
        return "pdf"
    return "other"


def image_blank(data: bytes) -> Optional[bool]:
    """True if the image is effectively one flat colour. None if it can't be opened."""
    try:
        from PIL import Image, ImageStat
        im = Image.open(io.BytesIO(data))
        im.draft("L", (512, 512))
        g = im.convert("L")
        g.thumbnail((512, 512))
        st = ImageStat.Stat(g)
        lo, hi = g.getextrema()
        return st.stddev[0] < 4.0 or (hi - lo) < 12
    except Exception:
        return None


def pdf_blank(data: bytes) -> bool:
    """A PDF with no fonts, no images and almost no drawing operators has nothing on it."""
    head = data[: 4 * 1024 * 1024]
    if re.search(rb"/(Font|Image|XObject)\b", head):
        return False
    # uncompressed text / drawing operators
    if re.search(rb"\b(Tj|TJ)\b", head) or len(re.findall(rb"\b(re|l|c)\s", head)) > 40:
        return False
    return True


def to_jpeg(data: bytes, max_side: int = 1600) -> Optional[bytes]:
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((max_side, max_side))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=85)
        return out.getvalue()
    except Exception:
        return None


# ------------------------------------------------------------------ model call
def pdf_to_jpeg(data: bytes, max_pages: int = 2, width: int = 1400) -> Optional[bytes]:
    """Render the first pages of a PDF to one stacked JPEG (server-side, no model). None on failure."""
    try:
        import io
        import pypdfium2 as pdfium
        from PIL import Image
        pdf = pdfium.PdfDocument(data)
        pages = []
        for i in range(min(len(pdf), max_pages)):
            pg = pdf[i]
            w = pg.get_width() or 612
            img = pg.render(scale=max(0.5, min(4.0, width / w))).to_pil().convert("RGB")
            pages.append(img)
        if not pages:
            return None
        W = max(p.width for p in pages)
        H = sum(p.height for p in pages)
        sheet = Image.new("RGB", (W, H), "white")
        y = 0
        for p in pages:
            sheet.paste(p, (0, y))
            y += p.height
        buf = io.BytesIO()
        sheet.save(buf, "JPEG", quality=85)
        return buf.getvalue()
    except Exception as e:
        logger.info("pdf render failed (%s); falling back to file part", type(e).__name__)
        return None


async def _call(prompt: str, kind_of_file: str, payload: bytes, filename: str) -> tuple[dict, float]:
    from .applicant_review import call_vision, OPENROUTER_CHAT_URL, vision_model, _parse_json, VISION_TIMEOUT
    if kind_of_file == "image":
        return await call_vision(prompt, payload)
    jpeg = pdf_to_jpeg(payload)
    if jpeg:
        return await call_vision(prompt, jpeg)
    import os
    import httpx
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    body = {
        "model": vision_model(), "temperature": 0.1, "max_tokens": 500,
        "response_format": {"type": "json_object"}, "usage": {"include": True},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "file", "file": {"filename": filename or "document.pdf",
                                      "file_data": "data:application/pdf;base64," + base64.b64encode(payload).decode()}},
        ]}],
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "HTTP-Referer": "https://brxs.ca", "X-Title": "Bricks document check"}
    async with httpx.AsyncClient(timeout=max(VISION_TIMEOUT, 45)) as client:
        resp = await client.post(OPENROUTER_CHAT_URL, json=body, headers=headers)
    if resp.status_code >= 400:
        logger.warning("doc check pdf call http=%s body=%s", resp.status_code, resp.text[:300].replace("\n", " "))
    resp.raise_for_status()
    data = resp.json()
    text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
    try:
        cost = float((data.get("usage") or {}).get("cost") or 0.0)
    except (TypeError, ValueError):
        cost = 0.0
    return _parse_json(text), cost


# ------------------------------------------------------------------ comparisons
_STOP = {"inc", "ltd", "llc", "corp", "co", "the", "and", "of", "services", "service", "limited", "incorporated", "company"}


def _tokens(s: Any) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", str(s or "").lower().replace("&", " ")) if len(t) > 1 and t not in _STOP}


def name_matches(on_doc: Any, candidates: list) -> Optional[bool]:
    """True/False, or None if the document had no name."""
    d = _tokens(on_doc)
    if not d:
        return None
    for c in candidates:
        ct = _tokens(c)
        if not ct:
            continue
        common = d & ct
        if len(common) >= 2 or (common and (len(ct) == 1 or len(d) == 1)):
            return True
    return False


def _day(v: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).lower().replace(",", "").replace("$", "").strip()
    m = re.match(r"([0-9.]+)\s*(m|million|k)?", s)
    if not m:
        return None
    n = float(m.group(1))
    return n * 1_000_000 if m.group(2) in ("m", "million") else n * 1000 if m.group(2) == "k" else n


def _pt(point: str, status: str, detail: str) -> dict:
    return {"point": point, "status": status, "detail": detail}


def evaluate(kind: str, raw: dict, *, names: list, entered_expiry: Optional[str], min_limit_cents: Optional[int],
             today: date) -> tuple[str, list]:
    """(overall, points) from the model's reading. Pure."""
    pts: list[dict] = []
    if raw.get("blank"):
        return BLANK, [_pt("Content", MISMATCH, "The file is blank.")]
    if raw.get("readable") is False:
        return UNREADABLE, [_pt("Content", UNVERIFIED, "Bricks couldn't read this file.")]
    what = str(raw.get("what_it_is") or "").strip()[:80]
    if raw.get("doc_type_matches") is False:
        pts.append(_pt("Right document", MISMATCH, f"Looks like {what or 'a different document'}."))
    else:
        pts.append(_pt("Right document", VERIFIED, what.capitalize() or "Matches"))
    if raw.get("looks_edited_or_sample"):
        pts.append(_pt("Authentic", MISMATCH, "Looks like a template, sample or edited file."))
    g = _group(kind)
    if g in ("insurance", "cert", "id"):
        nm = raw.get("insured_name") if g == "insurance" else raw.get("holder_name")
        m = name_matches(nm, names)
        label = "Insured name" if g == "insurance" else "Name"
        pts.append(_pt(label, UNVERIFIED if m is None else VERIFIED if m else MISMATCH,
                       "Not visible on the file." if m is None else f"{nm}" + ("" if m else " doesn't match the applicant.")))
    if g == "insurance":
        amt = _num(raw.get("coverage_amount_cad"))
        if min_limit_cents:
            need = min_limit_cents / 100
            pts.append(_pt("Coverage amount",
                           UNVERIFIED if amt is None else VERIFIED if amt >= need else MISMATCH,
                           "Not visible on the file." if amt is None else f"${amt:,.0f} on file" + ("" if amt >= need else f", needs ${need:,.0f}.")))
        elif amt is not None:
            pts.append(_pt("Coverage amount", VERIFIED, f"${amt:,.0f} on file"))
    if g in ("insurance", "cert", "id"):
        doc_exp = _day(raw.get("expiry_date"))
        ent = _day(entered_expiry)
        if doc_exp and doc_exp < today:
            pts.append(_pt("Expiry", MISMATCH, f"Expired {doc_exp.isoformat()}."))
        elif g == "insurance" or (ent and g == "cert"):
            if not doc_exp:
                pts.append(_pt("Expiry", UNVERIFIED, "Expiry date not visible on the file."))
            elif ent and abs((doc_exp - ent).days) > 1:
                pts.append(_pt("Expiry", MISMATCH, f"File says {doc_exp.isoformat()}, applicant entered {ent.isoformat()}."))
            else:
                pts.append(_pt("Expiry", VERIFIED, f"{doc_exp.isoformat()}" + (" matches what was entered" if ent else "")))
        elif doc_exp:
            pts.append(_pt("Expiry", VERIFIED, f"Valid until {doc_exp.isoformat()}"))
    if g == "cert":
        iss = str(raw.get("issuer") or "").strip()
        pts.append(_pt("Issuer", VERIFIED if iss else UNVERIFIED, iss or "Not visible on the file."))
        if kind == "police_check":
            idt = _day(raw.get("issue_date"))
            if not idt:
                pts.append(_pt("Issued", UNVERIFIED, "Issue date not visible."))
            else:
                old = (today - idt).days > 365
                pts.append(_pt("Issued", MISMATCH if old else VERIFIED, idt.isoformat() + (" (older than 12 months)" if old else "")))
        elif kind == "rpas_certificate":
            pts.append(_pt("Certificate number", VERIFIED if raw.get("has_certificate_number") else UNVERIFIED,
                           "Printed on the certificate" if raw.get("has_certificate_number") else "Not visible."))
    if g == "id":
        pts.append(_pt("Photo", VERIFIED if raw.get("has_photo") else UNVERIFIED,
                       str(raw.get("id_kind") or "Photo ID") if raw.get("has_photo") else "No face photo visible."))
    if g in ("proof", "other"):
        pts.append(_pt("Real work", VERIFIED if raw.get("shows_real_work") else MISMATCH if raw.get("shows_real_work") is False else UNVERIFIED,
                       "Shows real work" if raw.get("shows_real_work") else "Doesn't look like real work of this kind." if raw.get("shows_real_work") is False else "Couldn't tell."))
    if any(p["status"] == MISMATCH for p in pts):
        return BAD, pts
    if all(p["status"] == VERIFIED for p in pts):
        return OK, pts
    return PARTIAL, pts


def applicant_names(member: dict) -> list:
    prof = member.get("application_profile") if isinstance(member.get("application_profile"), dict) else {}
    creds = prof.get("credentials") if isinstance(prof.get("credentials"), dict) else {}
    return [n for n in (member.get("name"), creds.get("business_name"), member.get("business_name")) if n]


# ------------------------------------------------------------------ one doc
async def _fetch(file_url: str) -> Optional[bytes]:
    from .storage import get_signed_download_url
    import httpx
    url = await get_signed_download_url(file_url)
    if not url:
        return None
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        r = await c.get(url)
    if r.status_code >= 400:
        return None
    return r.content[:MAX_BYTES + 1]


async def check_document(doc: dict, member: dict, *, min_limit_cents: Optional[int] = None,
                         data: Optional[bytes] = None, today: Optional[date] = None) -> dict:
    """content_check dict for one vendor_documents row. Never raises."""
    from .applicant_review import vision_configured, vision_model
    today = today or datetime.now(timezone.utc).date()
    kind = str(doc.get("doc_type") or "")
    fname = str(doc.get("file_url") or "").rsplit("/", 1)[-1]
    out: dict[str, Any] = {"version": VERSION, "kind": kind, "file_url": doc.get("file_url"),
                           "checked_at": datetime.now(timezone.utc).isoformat(), "model": None, "cost_usd": 0.0}
    try:
        if data is None:
            data = await _fetch(str(doc.get("file_url") or ""))
        if not data:
            return {**out, "overall": UNREADABLE, "points": [_pt("Content", UNVERIFIED, "Bricks couldn't open the file.")]}
        out["sha256"] = hashlib.sha256(data).hexdigest()
        ftype = _sniff(data, fname)
        if len(data) < 200:
            return {**out, "overall": BLANK, "points": [_pt("Content", MISMATCH, "The file is empty.")]}
        if ftype == "image":
            b = image_blank(data)
            if b is None:
                return {**out, "overall": UNREADABLE, "points": [_pt("Content", UNVERIFIED, "Bricks couldn't open this image.")]}
            if b:
                return {**out, "overall": BLANK, "points": [_pt("Content", MISMATCH, "The image is blank.")]}
            payload = to_jpeg(data)
            if not payload:
                return {**out, "overall": UNREADABLE, "points": [_pt("Content", UNVERIFIED, "Bricks couldn't open this image.")]}
        elif ftype == "pdf":
            if pdf_blank(data):
                return {**out, "overall": BLANK, "points": [_pt("Content", MISMATCH, "The PDF has no text or images.")]}
            if len(data) > MAX_BYTES:
                return {**out, "overall": NOT_CHECKED, "points": [_pt("Content", UNVERIFIED, "File too large to check automatically.")]}
            payload = data
            jpeg = pdf_to_jpeg(data)
            if jpeg:
                if image_blank(jpeg):
                    return {**out, "overall": BLANK, "points": [_pt("Content", MISMATCH, "The PDF pages are blank.")]}
                ftype, payload = "image", jpeg
        else:
            return {**out, "overall": NOT_CHECKED, "points": [_pt("Content", UNVERIFIED, "File type can't be checked automatically (use PDF or a photo).")]}
        if not vision_configured():
            return {**out, "overall": NOT_CHECKED, "points": [_pt("Content", UNVERIFIED, "Automatic check not available.")]}
        raw, cost = await _call(_prompt(kind, today), ftype, payload, fname)
        overall, pts = evaluate(kind, raw if isinstance(raw, dict) else {}, names=applicant_names(member),
                                entered_expiry=doc.get("expiry_date"), min_limit_cents=min_limit_cents, today=today)
        keep = {k: raw.get(k) for k in ("what_it_is", "insured_name", "holder_name", "insurer", "issuer",
                                        "coverage_amount_cad", "expiry_date", "issue_date", "id_kind") if isinstance(raw, dict) and raw.get(k) is not None}
        return {**out, "overall": overall, "points": pts, "read": keep, "model": vision_model(), "cost_usd": cost}
    except Exception as e:
        logger.warning("doc check failed kind=%s (%s)", kind, type(e).__name__)
        return {**out, "overall": NOT_CHECKED, "points": [_pt("Content", UNVERIFIED, "Automatic check failed; check by hand.")]}


# ------------------------------------------------------------------ member run
def _fresh(doc: dict) -> bool:
    vr = doc.get("validation_result") if isinstance(doc.get("validation_result"), dict) else {}
    cc = vr.get("content_check") if isinstance(vr.get("content_check"), dict) else None
    return bool(cc and cc.get("version") == VERSION and cc.get("file_url") == doc.get("file_url")
                and cc.get("overall") != NOT_CHECKED)


async def check_member_docs(db, member: dict, docs: list[dict], *, force: bool = False, concurrency: int = 4) -> list[dict]:
    """Check every unchecked doc, store the result on the row, return docs with it merged in."""
    from . import trade_specs as ts
    limits = {r["kind"]: r.get("min_limit_cents") for r in ts.required_docs(member)}
    sem = asyncio.Semaphore(concurrency)

    async def one(d: dict) -> dict:
        if not d.get("file_url") or str(d.get("status") or "") == "rejected" or (not force and _fresh(d)):
            return d
        if str(d.get("doc_type") or "") in ("resume", "other"):
            return d
        async with sem:
            cc = await check_document(d, member, min_limit_cents=limits.get(d.get("doc_type")))
        vr = dict(d.get("validation_result") or {}) if isinstance(d.get("validation_result"), dict) else {}
        vr["content_check"] = cc
        if d.get("id"):
            try:
                await db.table("vendor_documents").update({"validation_result": vr}).eq("id", d["id"]).execute()
            except Exception as e:
                logger.warning("doc check store failed (%s)", type(e).__name__)
        return {**d, "validation_result": vr}

    return list(await asyncio.gather(*(one(d) for d in docs or [])))


def content_check(doc: dict) -> Optional[dict]:
    vr = doc.get("validation_result") if isinstance(doc.get("validation_result"), dict) else {}
    cc = vr.get("content_check")
    return cc if isinstance(cc, dict) else None
