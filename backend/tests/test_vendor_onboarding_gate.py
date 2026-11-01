"""Onboarding gate (Kam, 2026-10-31): approval -> onboarding -> live.

No test shoot. Real estate samples and a resume are required to apply.
Rejected applicants may reapply after 90 days.
"""
from __future__ import annotations

import io
import os
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

os.environ.setdefault("APPLICANT_UPLOAD_SECRET", "test-upload-secret-not-real")

from app.services import applicant_evidence as ev  # noqa: E402
from app.services import applicant_resume as resume  # noqa: E402
from app.services import vendor_onboarding as vo  # noqa: E402
from app.services.vendor_profile import member_eligible_for_job, onboarding_complete  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now(timezone.utc).isoformat()


def _vendor(**over):
    m = {
        "id": "m-1", "vendor_id": "default_vendor", "name": "Sam Photographer", "email": "sam@example.com",
        "role": "Photographer", "vendor_type": "photographer", "worker_type": "contractor",
        "onboarding_status": "approved", "is_active": True, "skills": ["photo"], "specialty": "photo",
        "service_areas": ["Toronto"], "home_city": "Toronto",
        "terms_accepted_at": NOW, "ic_agreement_version": vo.current_agreement_version(),
    }
    m.update(over)
    return m


# ── gate ────────────────────────────────────────────────────────────────────
def test_approved_vendor_is_not_auto_assignable_until_onboarding_done():
    job = {"job_type": "photo", "service_type": "photo", "city": "Toronto", "property_city": "Toronto"}
    onboarding = _vendor()
    live = _vendor(onboarding_completed_at=NOW)
    assert not onboarding_complete(onboarding)
    assert member_eligible_for_job(onboarding, job) is False
    assert onboarding_complete(live)
    # Paused live vendor: still blocked.
    assert member_eligible_for_job({**live, "is_active": False}, job) is False


def test_vendor_member_model_carries_onboarding_fields():
    from app.shared.schemas import VendorMember
    m = VendorMember(name="x", email="x@example.com", onboarding_completed_at=NOW, rejected_at=None)
    d = m.dict()
    for k in ("onboarding_completed_at", "first_signed_in_at", "guide_acknowledged_at",
              "availability_confirmed_at", "rejected_at"):
        assert k in d


def test_public_profile_not_assignable_while_onboarding():
    from app.shared.vendor_identity import public_profile
    p = public_profile(_vendor())
    assert p["assignable"] is False and p["onboarding_complete"] is False
    p2 = public_profile(_vendor(onboarding_completed_at=NOW))
    assert p2["onboarding_complete"] is True


def test_needs_terms_regates_old_version_only_while_onboarding():
    from app.shared.vendor_identity import needs_terms
    assert needs_terms(_vendor(ic_agreement_version="1.8")) is True
    assert needs_terms(_vendor(ic_agreement_version="1.8", onboarding_completed_at=NOW)) is False
    assert needs_terms(_vendor()) is False


# ── checklist ───────────────────────────────────────────────────────────────
def test_checklist_steps_and_completion():
    m = _vendor()
    s = vo.checklist(m, None)
    assert [x["key"] for x in s["steps"]] == list(vo.STEP_KEYS)
    assert s["state"] == "onboarding" and not s["all_steps_done"]
    assert "test shoot" not in str(s).lower()
    m.update(first_signed_in_at=NOW, availability_confirmed_at=NOW, guide_acknowledged_at=NOW)
    assert not vo.checklist(m, {"connect_status": "pending"})["all_steps_done"]
    done = vo.checklist(m, {"connect_status": "connected"})
    assert done["all_steps_done"] and done["done"] == 5
    assert not vo.checklist({**m, "ic_agreement_version": "1.8"}, {"connect_status": "connected"})["all_steps_done"]


def test_payouts_not_required_for_employees():
    m = _vendor(worker_type="employee", first_signed_in_at=NOW, availability_confirmed_at=NOW, guide_acknowledged_at=NOW)
    s = vo.checklist(m, None)
    pay = next(x for x in s["steps"] if x["key"] == "payouts")
    assert pay["done"] and not pay["required"] and s["all_steps_done"]


def test_states():
    assert vo.checklist(_vendor(onboarding_status="pending", is_active=False))["state"] == "applicant"
    assert vo.checklist(_vendor(onboarding_status="rejected"))["state"] == "rejected"
    assert vo.checklist(_vendor(is_active=False))["state"] == "paused"
    assert vo.checklist(_vendor(onboarding_completed_at=NOW))["state"] == "live"


@pytest.mark.asyncio
async def test_refresh_stamps_once_and_sends_live_email(monkeypatch):
    m = _vendor(first_signed_in_at=NOW, availability_confirmed_at=NOW, guide_acknowledged_at=NOW, worker_type="employee")
    updates = []

    async def fake_update(member_id, fields, *, only_if_null=None):
        updates.append((member_id, fields, only_if_null))
        return [{"id": member_id}]

    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(vo, "_update_member", fake_update)
    monkeypatch.setattr(vo, "send_live_email", sent)
    s = await vo.refresh_onboarding("m-1", member=m)
    assert s["complete"] and s["state"] == "live"
    assert updates and updates[0][2] == "onboarding_completed_at"
    sent.assert_awaited_once()

    # Lost the race (already stamped by another request): no second email.
    sent.reset_mock()

    async def no_rows(member_id, fields, *, only_if_null=None):
        return []

    monkeypatch.setattr(vo, "_update_member", no_rows)
    await vo.refresh_onboarding("m-1", member=m)
    sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_never_completes_paused_or_unapproved(monkeypatch):
    base = dict(first_signed_in_at=NOW, availability_confirmed_at=NOW, guide_acknowledged_at=NOW, worker_type="employee")
    fake = AsyncMock(return_value=[{"id": "m-1"}])
    monkeypatch.setattr(vo, "_update_member", fake)
    monkeypatch.setattr(vo, "send_live_email", AsyncMock())
    await vo.refresh_onboarding("m-1", member=_vendor(is_active=False, **base))
    await vo.refresh_onboarding("m-1", member=_vendor(onboarding_status="pending", **base))
    fake.assert_not_awaited()


# ── emails ──────────────────────────────────────────────────────────────────
def _orange_buttons(html: str) -> int:
    return len(re.findall(r"<a [^>]*background:#D2542B", html))


def test_vendor_emails_say_bricks_only_with_one_orange_button():
    for subject, html, _ in (vo.onboarding_welcome_email(_vendor()), vo.live_email(_vendor())):
        for bad in ("Bricks GO", "Bricks OS", " GO ", "test shoot"):
            assert bad not in subject and bad not in html
        assert _orange_buttons(html) == 1
        assert "help@brxs.ca" in html
        assert "Hi Sam," in html
    subject, html, _ = vo.onboarding_welcome_email(_vendor())
    assert "/onboarding" in html and "contractor agreement" in html
    rs, rh = vo.reject_email(_vendor(), datetime(2026, 10, 31, tzinfo=timezone.utc))
    assert rs == "Update on your Bricks application"
    assert "January 29, 2027" in rh and "brxs.ca/photographers" in rh
    assert "GO" not in rh.replace("GOOD", "")
    assert vo.VENDOR_MAIL_FROM == "Bricks <help@brxs.ca>"


def test_email_escapes_names():
    _, html, _ = vo.live_email(_vendor(name="<script>x</script> Doe"))
    assert "<script>" not in html


def test_admin_ops_approve_uses_onboarding_email_not_go():
    src = (ROOT / "app/routers/admin_ops.py").read_text()
    assert "send_onboarding_welcome" in src and "send_reject_email" in src
    assert "Welcome to Bricks GO" not in src
    assert 'update_data["rejected_at"]' in src


# ── reapply ─────────────────────────────────────────────────────────────────
def test_reapply_cooldown():
    from app.routers.onboarding import _reapply_target
    old = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    assert _reapply_target({"id": "x", "onboarding_status": "rejected", "rejected_at": old})["id"] == "x"
    # Legacy rows without rejected_at fall back to updated_at.
    assert _reapply_target({"id": "y", "onboarding_status": "rejected", "updated_at": old})["id"] == "y"
    with pytest.raises(HTTPException) as exc:
        _reapply_target({"id": "x", "onboarding_status": "rejected", "rejected_at": recent})
    assert "apply again from" in exc.value.detail
    for status in ("pending", "approved"):
        with pytest.raises(HTTPException) as exc:
            _reapply_target({"id": "x", "onboarding_status": status, "rejected_at": old})
        assert "already exists" in exc.value.detail


# ── application requirements ────────────────────────────────────────────────
def _app(**over):
    from tests.test_applicant_review import _application
    return _application(**over)


@pytest.mark.asyncio
async def test_apply_requires_resume_and_real_estate_attestation():
    from app.routers.onboarding import _validate_application
    await _validate_application(_app())
    with pytest.raises(HTTPException) as exc:
        await _validate_application(_app(resume_path=None))
    assert "resume" in exc.value.detail.lower()
    with pytest.raises(HTTPException) as exc:
        await _validate_application(_app(samples_are_real_estate=False))
    assert "real estate" in exc.value.detail.lower()
    with pytest.raises(HTTPException) as exc:
        await _validate_application(_app(resume_path="bricks-go/applicants/someone/resume-98-aaaa.pdf"))
    assert "through this form" in exc.value.detail


@pytest.mark.asyncio
async def test_resume_upload_kind(monkeypatch):
    from app.routers.onboarding import mint_sample_uploads
    from app.shared.schemas import SampleUploadRequest

    async def fake_signed(path):
        return {"signed_url": f"https://storage.example/{path}?token=t", "token": "t", "path": path}

    monkeypatch.setattr("app.services.storage.create_signed_upload_url", fake_signed)
    ok = await mint_sample_uploads(SampleUploadRequest(files=[
        {"name": "cv.docx", "type": "", "size": 80_000, "kind": "resume"},
        {"name": "a.jpg", "type": "image/jpeg", "size": 2_000_000},
    ]))
    r = next(u for u in ok["uploads"] if u["kind"] == "resume")
    assert r["path"].endswith(".docx") and ev.is_resume_path(r["path"])
    assert ok["limits"]["max_resume_mb"] == 10
    for bad in (
        [{"name": "cv.png", "type": "image/png", "size": 80_000, "kind": "resume"}],
        [{"name": "cv.pdf", "type": "application/pdf", "size": 11 * 1024 * 1024, "kind": "resume"}],
        [{"name": "a.pdf", "type": "application/pdf", "size": 9000, "kind": "resume"}] * 2,
    ):
        with pytest.raises(HTTPException):
            await mint_sample_uploads(SampleUploadRequest(files=bad))


# ── resume check ────────────────────────────────────────────────────────────
def _docx(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document><w:body>" + "".join(
            f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in text.split("\n")) + "</w:body></w:document>")
    return buf.getvalue()


RE_CV = ("Sam Photographer\nReal estate photographer, 2019 to now. Shot 400 MLS listings for a Toronto brokerage.\n"
         "Interior and exterior HDR, twilight and drone work for realtors. Lightroom, Photoshop, Matterport.\n"
         "Before that I worked as an assistant on architecture and hospitality shoots across the GTA for three years.")
WEDDING_CV = ("Sam Photographer\nWedding and portrait photographer since 2018. Engagement sessions, family portraits,\n"
              "corporate headshots and events. Second shooter for two studios. Editing in Lightroom and Capture One.\n"
              "Comfortable with clients, timelines and fast turnaround on galleries for couples and companies.")


def test_resume_docx_and_scoring():
    r = resume.check_resume_bytes(_docx(RE_CV), "x/resume-98-aa.docx")
    assert r["readable"] and r["value"] == 1.0 and "real estate" in r["terms"]
    w = resume.check_resume_bytes(_docx(WEDDING_CV), "x/resume-98-aa.docx")
    assert w["readable"] and w["value"] == 0.5
    junk = resume.check_resume_bytes(b"%PDF-1.4 garbage", "x/resume.pdf")
    assert junk["value"] is None


def test_resume_pdf_text():
    import zlib
    body = b"BT /F1 12 Tf (" + RE_CV.replace("\n", " ").encode() + b") Tj ET"
    pdf = b"%PDF-1.4\n1 0 obj << /Length 1 /Filter /FlateDecode >>\nstream\n" + zlib.compress(body) + b"\nendstream\nendobj\n%%EOF"
    r = resume.check_resume_bytes(pdf, "x/resume-98-aa.pdf")
    assert r["readable"] and r["value"] == 1.0


@pytest.mark.asyncio
async def test_review_scores_resume_and_gives_real_estate_reasons():
    from tests.test_applicant_review import _inp, _run, _vision
    from app.services import applicant_review as ar

    inp, blobs = _inp()
    inp.resume_path = "bricks-go/applicants/abc/resume-98-aa.docx"
    blobs[inp.resume_path] = _docx(RE_CV)
    r = await _run(inp, blobs, _vision("pass"))
    assert r["verdict"] == ar.RECOMMEND
    assert r["parts"]["resume"]["status"] == ar.GREEN and r["resume"]["value"] == 1.0

    # Interiors only: needs review, with the reason.
    inp2, blobs2 = _inp()
    r2 = await _run(inp2, blobs2, _vision("pass", subjects=("interior",)))
    assert r2["verdict"] == ar.NEEDS_REVIEW
    assert "No exterior shots in the samples." in r2["verdict_reasons"]
    assert "resume" not in r2["parts"]  # legacy applicant without a resume: no penalty
    assert any(b["key"] == "resume" and b["source"] == ev.COULDNT_CHECK for b in r2["evidence"])

    # Not real estate: not a fit, with the reason.
    inp3, blobs3 = _inp()
    r3 = await _run(inp3, blobs3, _vision("pass", real_estate=False))
    assert r3["verdict"] == ar.NOT_A_FIT
    assert any("aren't real estate" in x for x in r3["verdict_reasons"])


def test_review_email_has_resume_link_and_reasons():
    from app.services import applicant_email as mail
    review = {"verdict": "needs_review", "verdict_label": "Needs review", "colour": "yellow", "score": 70,
              "parts": {}, "criteria": {}, "evidence": [], "flags": [], "missing": [],
              "verdict_reasons": ["No exterior shots in the samples."]}
    kw = dict(member_id="m-1", name="Sam", city="Toronto", specialties=["photo"], found_via="", review=review, thumb_urls=[])
    html = mail.render_html(**kw, resume_url="https://storage.example/resume.pdf?token=t")
    assert "Download resume" in html and "No exterior shots" in html
    assert "No resume on file." in mail.render_html(**kw)
    text = mail.render_text(**kw, resume_url="https://storage.example/resume.pdf?token=t")
    assert "Resume: https://storage.example/resume.pdf" in text and "Why: No exterior" in text
