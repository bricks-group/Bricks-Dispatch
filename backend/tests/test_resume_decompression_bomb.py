"""Applicant resume inflate must stay bounded (decompression-bomb DoS).

These tests fail while review materializes a whole deflate member. They pass
once a compressed resume under the upload cap cannot expand without a limit,
a downloaded object over MAX_RESUME_BYTES is refused, and a review row is
stored before the resume bytes are read (so a killed review is not retried).

A normal short resume must still be readable. The cap is the traced-memory
budget below: an 8 MiB expansion that is fully read peaks far above it, and a
read limited to about 1 MiB (plus one copy) stays under it. Checking
ZipInfo.file_size and then calling ZipFile.read() still fails the lied-size
case, because the uncompressed-size field is attacker-controlled and read()
inflates the deflate stream before it honors that field.
"""
from __future__ import annotations

import io
import os
import struct
import tracemalloc
import zipfile
import zlib

import pytest

os.environ.setdefault("APPLICANT_UPLOAD_SECRET", "test-upload-secret-not-real")

from app.services import applicant_evidence as ev  # noqa: E402
from app.services import applicant_resume as resume  # noqa: E402

# Real expansion of the bomb fixtures. Compressed size stays under the 10 MB
# upload cap; the vulnerable reader allocates the expanded member.
_EXPANDS_TO = 8 * 1024 * 1024
# High-water mark, in bytes traced by tracemalloc, allowed while checking one resume.
_PEAK_BUDGET = 4 * 1024 * 1024
_LIED_FILE_SIZE = 100

_RE_CV = (
    "Sam Photographer\nReal estate photographer, 2019 to now. Shot 400 MLS listings for a Toronto brokerage.\n"
    "Interior and exterior HDR, twilight and drone work for realtors. Lightroom, Photoshop, Matterport.\n"
    "Before that I worked as an assistant on architecture and hospitality shoots across the GTA for three years."
)


def _peak(fn):
    tracemalloc.start()
    try:
        result = fn()
    finally:
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return result, peak


async def _apeak(fn):
    tracemalloc.start()
    try:
        result = await fn()
    finally:
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return result, peak


def _docx(text: str) -> bytes:
    xml = "<w:document><w:body>" + "".join(
        f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in text.split("\n")
    ) + "</w:body></w:document>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def _docx_bomb(*, lie: int | None) -> bytes:
    """DOCX whose word/document.xml deflates from _EXPANDS_TO bytes.

    lie=100 rewrites the local-header and central-directory uncompressed sizes
    so a check of ZipInfo.file_size looks at the attacker's number.
    """
    xml = b"<w:document><w:t>" + (b"A" * _EXPANDS_TO) + b"</w:t></w:document>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
    data = bytearray(buf.getvalue())
    assert len(data) < ev.MAX_RESUME_BYTES
    if lie is not None:
        honest = struct.pack("<I", len(xml))
        assert data.count(honest) == 2
        data = data.replace(honest, struct.pack("<I", lie))
    blob = bytes(data)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        info = z.getinfo("word/document.xml")
        assert info.compress_size < 100_000
        if lie is None:
            assert info.file_size >= _EXPANDS_TO
        else:
            assert info.file_size == lie
    return blob


def _stored_docx_over_cap() -> bytes:
    """Valid DOCX larger than MAX_RESUME_BYTES. Parsing it yields the real resume."""
    pad = b" " * (ev.MAX_RESUME_BYTES + 4096)
    xml = ("<w:document><w:t>" + _RE_CV + "</w:t>").encode() + pad + b"</w:document>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as z:
        z.writestr("word/document.xml", xml)
    blob = buf.getvalue()
    assert len(blob) > ev.MAX_RESUME_BYTES
    return blob


def _pdf_with_stream(plain: bytes) -> bytes:
    comp = zlib.compress(plain)
    assert b"\nendstream" not in comp
    pdf = (
        b"%PDF-1.4\n1 0 obj << /Length "
        + str(len(comp)).encode()
        + b" /Filter /FlateDecode >>\nstream\n"
        + comp
        + b"\nendstream\nendobj\n%%EOF"
    )
    matches = list(resume._PDF_STREAM.finditer(pdf))
    assert len(matches) == 1 and matches[0].group(1) == comp
    return pdf


def _pdf_bomb() -> bytes:
    pdf = _pdf_with_stream(b"A" * _EXPANDS_TO)
    assert len(pdf) < ev.MAX_RESUME_BYTES
    return pdf


def _pdf_resume() -> bytes:
    body = b"BT /F1 12 Tf (" + _RE_CV.replace("\n", " ").encode() + b") Tj ET"
    return _pdf_with_stream(body)


def _assert_bounded(path: str, blob: bytes) -> None:
    _result, peak = _peak(lambda: resume.check_resume_bytes(blob, path))
    assert peak < _PEAK_BUDGET, f"{path} traced peak {peak} bytes (budget {_PEAK_BUDGET})"


def test_normal_docx_and_pdf_resumes_stay_readable():
    docx = resume.check_resume_bytes(_docx(_RE_CV), "bricks-go/applicants/x/resume-01-abcd.docx")
    pdf = resume.check_resume_bytes(_pdf_resume(), "bricks-go/applicants/x/resume-01-abcd.pdf")
    assert docx["readable"] and docx["value"] == 1.0 and "real estate" in docx["terms"]
    assert pdf["readable"] and pdf["value"] == 1.0 and pdf["error"] == ""


def test_docx_inflate_is_bounded():
    _assert_bounded("bricks-go/applicants/x/resume-01-abcd.docx", _docx_bomb(lie=None))


def test_docx_lied_uncompressed_size_is_bounded():
    """file_size in the ZIP header says 100 bytes. The deflate stream does not."""
    _assert_bounded("bricks-go/applicants/x/resume-01-abcd.docx", _docx_bomb(lie=_LIED_FILE_SIZE))


def test_pk_bytes_are_bounded_even_when_the_name_says_pdf():
    """extract_text sends a PK payload down the DOCX reader before it looks at the extension."""
    _assert_bounded("bricks-go/applicants/x/resume-01-abcd.pdf", _docx_bomb(lie=_LIED_FILE_SIZE))


def test_pdf_flate_inflate_is_bounded():
    _assert_bounded("bricks-go/applicants/x/resume-01-abcd.pdf", _pdf_bomb())


def test_resume_over_max_bytes_is_not_a_successful_read():
    """The downloaded object itself is over the upload cap, and it is a real resume.

    Trailing bytes would break a DOCX central directory, so the DOCX fixture is a
    stored archive whose member is genuinely larger than MAX_RESUME_BYTES. The PDF
    fixture is a normal resume with padding after %%EOF, which the stream scanner
    still reads.
    """
    pdf = _pdf_resume() + b"\0" * (ev.MAX_RESUME_BYTES + 1)
    assert len(pdf) > ev.MAX_RESUME_BYTES
    doc = (_RE_CV + "\n").encode() + b" " * (ev.MAX_RESUME_BYTES + 1)
    for path, blob in (
        ("bricks-go/applicants/x/resume-01-abcd.docx", _stored_docx_over_cap()),
        ("bricks-go/applicants/x/resume-01-abcd.pdf", pdf),
        ("bricks-go/applicants/x/resume-01-abcd.doc", doc),
    ):
        got = resume.check_resume_bytes(blob, path)
        assert got["value"] is None, path
        assert got["readable"] is False, path
        assert got["error"], path


@pytest.mark.asyncio
async def test_run_review_does_not_inflate_a_resume():
    from tests.test_applicant_review import _inp, _run, _vision

    async def peak_for(path: str, blob: bytes):
        inp, blobs = _inp(n=0)
        inp.resume_path = path
        blobs[path] = blob

        async def go():
            return await _run(inp, blobs, _vision("pass"))

        return await _apeak(go)

    for path, small, bomb in (
        ("bricks-go/applicants/abc/resume-98-aa.docx", _docx(_RE_CV), _docx_bomb(lie=None)),
        ("bricks-go/applicants/abc/resume-98-aa.docx", _docx(_RE_CV), _docx_bomb(lie=_LIED_FILE_SIZE)),
        ("bricks-go/applicants/abc/resume-98-aa.pdf", _pdf_resume(), _pdf_bomb()),
    ):
        _ok, peak_ok = await peak_for(path, small)
        _bad, peak_bad = await peak_for(path, bomb)
        assert peak_bad < peak_ok + _PEAK_BUDGET, f"{path} control {peak_ok} bomb {peak_bad}"


@pytest.mark.asyncio
async def test_run_review_refuses_resume_larger_than_upload_cap():
    from tests.test_applicant_review import _inp, _run, _vision

    inp, blobs = _inp(n=0)
    path = "bricks-go/applicants/abc/resume-98-aa.docx"
    inp.resume_path = path
    blobs[path] = _stored_docx_over_cap()
    got = await _run(inp, blobs, _vision("pass"))
    assert got["resume"]["value"] is None
    assert got["resume"]["readable"] is False

    inp2, blobs2 = _inp(n=0)
    pdf_path = "bricks-go/applicants/abc/resume-98-aa.pdf"
    inp2.resume_path = pdf_path
    blobs2[pdf_path] = _pdf_resume() + b"\0" * (ev.MAX_RESUME_BYTES + 1)
    got2 = await _run(inp2, blobs2, _vision("pass"))
    assert got2["resume"]["value"] is None
    assert got2["resume"]["readable"] is False


@pytest.mark.asyncio
async def test_anonymous_sample_upload_mints_a_resume_under_the_declared_cap(monkeypatch):
    """POST /api/onboarding/sample-uploads has no auth dependency. The declared
    size is the only size it sees; the bytes arrive later through the signed URL.
    """
    from app.routers.onboarding import mint_sample_uploads, router
    from app.shared.schemas import SampleUploadRequest

    route = next(r for r in router.routes if getattr(r, "path", None) == "/api/onboarding/sample-uploads")
    assert route.methods == {"POST"}
    assert route.dependant.dependencies == []

    async def fake_signed(path):
        return {"signed_url": f"https://storage.example/{path}?token=t", "token": "t", "path": path}

    monkeypatch.setattr("app.services.storage.create_signed_upload_url", fake_signed)
    declared = ev.MAX_RESUME_BYTES - 1
    files = [
        {"name": f"sample-{i}.jpg", "type": "image/jpeg", "size": 2_000_000, "kind": "sample"}
        for i in range(ev.MIN_SAMPLES)
    ]
    files.append({
        "name": "cv.docx",
        "type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "size": declared,
        "kind": "resume",
    })
    out = await mint_sample_uploads(SampleUploadRequest(files=files))
    resume_slot = next(u for u in out["uploads"] if u["kind"] == "resume")
    assert resume_slot["path"].endswith(".docx")
    assert ev.path_in_session(resume_slot["path"], out["session"])
    assert "size" not in resume_slot


@pytest.mark.asyncio
async def test_apply_validation_accepts_the_session_resume_without_reading_it():
    from app.routers.onboarding import _validate_application
    from tests.test_applicant_review import _application

    application = _application(n_samples=ev.MIN_SAMPLES)
    await _validate_application(application)
    assert application.resume_path
    assert ev.is_resume_path(application.resume_path)
    assert ev.path_in_session(application.resume_path, application.sample_upload_session)


@pytest.mark.asyncio
async def test_review_row_is_stored_before_the_resume_is_downloaded(monkeypatch):
    """pending_without_review retries any pending photographer with no
    vendor_applicant_reviews row. The row has to exist before resume bytes are
    read, or a review killed during inflate is picked up again.
    """
    from unittest.mock import AsyncMock

    from app.services import applicant_review_runner as runner

    events: list = []
    resume_path = "bricks-go/applicants/abc/resume-01-abcd.docx"

    async def download_bytes(path):
        events.append(("download", path))
        if path == resume_path:
            return _docx(_RE_CV)
        return b""

    async def insert(table, rows):
        events.append(("insert", table))

    monkeypatch.setattr("app.services.storage.download_bytes", download_bytes)
    monkeypatch.setattr(runner, "_load_docs", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "_load_availability", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "_load_hashes", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "_store_thumbs", AsyncMock())
    monkeypatch.setattr(runner, "notify_owner", AsyncMock())
    monkeypatch.setattr(runner, "_insert", insert)

    member = {
        "id": "mem-1",
        "name": "Sam Photographer",
        "email": "sam.photographer.example@example.com",
        "phone": "+14165550199",
        "vendor_type": "photographer",
        "onboarding_status": "pending",
        "home_city": "Toronto",
        "skills": ["photo"],
        "drone_certified": False,
        "equipment_list": ["Sony A7 IV"],
        "portfolio_links": [],
        "application_profile": {
            "equipment": {"camera_body": "Sony A7 IV", "lenses": "16-35mm", "tripod": "Manfrotto"},
            "samples": [],
            "location": {"city": "Toronto"},
            "resume": {"path": resume_path, "name": "cv.docx"},
        },
    }
    result = await runner.review_application("mem-1", notify=False, trigger="apply", member=member)
    assert result is not None, "review_application returned no result, so it never stored a row"
    try:
        resume_at = next(i for i, evn in enumerate(events) if evn == ("download", resume_path))
        stored_at = next(i for i, evn in enumerate(events) if evn == ("insert", "vendor_applicant_reviews"))
    except StopIteration:
        pytest.fail(f"resume download and review insert were not both observed: {events}")
    assert stored_at < resume_at, events
