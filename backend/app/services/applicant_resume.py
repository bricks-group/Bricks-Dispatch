"""Resume check for photographer applicants (required from 2026-10-31).

Stdlib only: DOCX via zipfile, PDF via a light content-stream read (zlib),
DOC via text runs. It only looks for real estate photo experience; it never
approves or rejects. Unreadable files are "couldn't check", not a penalty.
"""
from __future__ import annotations

import io
import re
import zipfile
import zlib
from typing import Optional

MAX_TEXT = 60_000

RE_TERMS = (
    "real estate", "realtor", "listing", "mls", "brokerage", "property", "properties",
    "interior", "exterior", "architectural", "architecture", "airbnb", "hdr", "twilight",
    "matterport", "floor plan", "virtual tour", "vacation rental", "hospitality", "homes",
)


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


_PDF_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)
_PDF_STR = re.compile(rb"\(((?:\\.|[^\\)])*)\)")


def _pdf_text(data: bytes) -> str:
    out = []
    for m in _PDF_STREAM.finditer(data):
        raw = m.group(1)
        try:
            raw = zlib.decompress(raw)
        except Exception:
            pass
        if b"Tj" not in raw and b"TJ" not in raw:
            continue
        for s in _PDF_STR.findall(raw):
            out.append(s.decode("latin-1", "ignore"))
        out.append("\n")
        if sum(len(x) for x in out) > MAX_TEXT:
            break
    return " ".join(out)


def _doc_text(data: bytes) -> str:
    ascii_runs = re.findall(rb"[\x20-\x7e]{4,}", data)
    utf16 = re.findall(rb"(?:[\x20-\x7e]\x00){4,}", data)
    return " ".join([r.decode("ascii", "ignore") for r in ascii_runs] + [r.decode("utf-16-le", "ignore") for r in utf16])


def extract_text(data: bytes, ext: str) -> str:
    ext = (ext or "").lower().lstrip(".")
    try:
        if ext == "docx" or data[:2] == b"PK":
            text = _docx_text(data)
        elif ext == "pdf" or data[:5] == b"%PDF-":
            text = _pdf_text(data)
        else:
            text = _doc_text(data)
    except Exception:
        return ""
    return " ".join(text.split())[:MAX_TEXT]


def assess(text: str) -> dict:
    """{readable, terms, value}: value 1.0 (shows real estate work), 0.5 (readable, no sign of it)."""
    t = (text or "").lower()
    words = re.findall(r"[a-z]{3,}", t)
    if len(words) < 25:
        return {"readable": False, "terms": [], "value": None}
    terms = [k for k in RE_TERMS if re.search(r"\b" + re.escape(k) + r"\b", t)]
    return {"readable": True, "terms": terms[:6], "value": 1.0 if len(terms) >= 2 else 0.5}


def resume_ext(path: str) -> str:
    p = str(path or "")
    return p.rsplit(".", 1)[-1].lower() if "." in p else ""


def check_resume_bytes(data: Optional[bytes], path: str) -> dict:
    if not data:
        return {"readable": False, "terms": [], "value": None, "error": "missing"}
    return {**assess(extract_text(data, resume_ext(path))), "error": ""}
