"""Photographer certificate uploads tell Bricks ops there's one to check.

www /photographers: construction jobs need "verified drone liability insurance
of at least $2M" on "a certificate Bricks verifies". Uploads sat pending with no
signal to anyone, so nobody knew to verify them.
"""
from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import UploadFile

from app.routers import vendor_profile as vp
from app.services import credential_review_notify as crn

ACTOR = "11111111-2222-3333-4444-555555555555"
MEMBER = {"id": ACTOR, "name": "Pilot One", "role": "Photographer", "vendor_id": "default_vendor",
          "status": "available", "email": "pilot@example.com"}


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


class _Chain:
    def __init__(self, db, name):
        self._db, self._name = db, name
        self._insert = None

    def __getattr__(self, _):
        return lambda *a, **k: self

    def insert(self, row):
        self._insert = row
        return self

    async def execute(self):
        if self._insert is not None:
            self._db.inserts.append((self._name, self._insert))
        return SimpleNamespace(data=[])


class _Db:
    def __init__(self):
        self.inserts = []

    def table(self, name):
        return _Chain(self, name)


def _patch(monkeypatch, sender):
    db = _Db()

    async def fake_get_db():
        return db

    async def fake_upload(raw, folder, mid, filename, mime):
        return (f"onboarding/{mid}/{filename}", None, None, None)

    async def fake_build_profile(m):
        return {"id": m["id"]}

    monkeypatch.setattr(vp, "get_db", fake_get_db)
    monkeypatch.setattr(vp, "_actor_member", AsyncMock(return_value=dict(MEMBER)))
    monkeypatch.setattr(vp, "_persist_safe", AsyncMock(return_value=None))
    monkeypatch.setattr(vp, "build_profile", fake_build_profile)
    import app.services.storage as storage
    monkeypatch.setattr(storage, "upload_file", fake_upload)
    import app.services.notifications as notif
    monkeypatch.setattr(notif, "send_email_via_resend", sender)
    return db


def _file(name="coi.pdf"):
    return UploadFile(filename=name, file=io.BytesIO(b"%PDF-1.4 test"))


def test_insurance_email_body_flags_low_limit_and_no_drone_cover():
    subject, body = crn.insurance_email(MEMBER, {"liability_cad": 1000000, "expires_on": "2027-06-01",
                                                 "aviation_covered": False, "insurer": "Acme"})
    assert subject == "Insurance to review: Pilot One"
    assert "Pilot One (pilot@example.com)" in body
    assert "$1M" in body and "2027-06-01" in body and "Insurer: Acme" in body
    assert "below the $2M" in body
    assert "didn't say it covers drone liability" in body
    assert crn.REVIEW_URL in body


def test_insurance_email_clean_at_two_million_with_drone_cover():
    _, body = crn.insurance_email({"email": "x@example.com"}, {"liability_cad": 2500000, "expires_on": "2027-06-01",
                                                                "aviation_covered": True})
    assert "$2.5M" in body
    assert "below the $2M" not in body and "didn't say" not in body
    assert body.startswith("x@example.com sent")


def test_money_formats():
    assert crn._money(2000000) == "$2M"
    assert crn._money(750000) == "$750,000"
    assert crn._money(None) == "not given"


def test_rpas_email_basic_note():
    subject, body = crn.rpas_email(MEMBER, "basic", "PC-123")
    assert subject == "Drone certificate to review: Pilot One"
    assert "Level: Basic" in body and "PC-123" in body and "don't qualify" in body
    _, adv = crn.rpas_email(MEMBER, "advanced")
    assert "Level: Advanced" in adv and "don't qualify" not in adv


def test_insurance_upload_emails_admin_only(monkeypatch):
    sender = AsyncMock(return_value=True)
    db = _patch(monkeypatch, sender)
    out = _run(vp.upload_insurance_impl("Bearer t", None, _file(), 2000000, "2099-01-01", True, "Global", "P-9"))
    assert out["insurance_pending_review"] is True
    assert db.inserts and db.inserts[0][1]["doc_type"] == "coi"
    sender.assert_awaited_once()
    kw = sender.await_args.kwargs
    assert kw["to"] == "admin@brxs.ca"
    assert kw["subject"] == "Insurance to review: Pilot One"
    assert "Policy: P-9" in kw["text"] and "$2M" in kw["text"]
    assert "onboarding/" not in kw["text"] and "onboarding/" not in kw["html"]


def test_rpas_upload_emails_admin(monkeypatch):
    sender = AsyncMock(return_value=True)
    db = _patch(monkeypatch, sender)
    out = _run(vp.upload_rpas_impl("Bearer t", None, _file("rpas.pdf"), "advanced", "PA-1"))
    assert out["rpas_document_id"]
    assert db.inserts[0][1]["doc_type"] == "rpas_certificate"
    kw = sender.await_args.kwargs
    assert kw["to"] == "admin@brxs.ca" and kw["subject"].startswith("Drone certificate to review")


def test_email_failure_never_breaks_upload(monkeypatch):
    sender = AsyncMock(side_effect=RuntimeError("resend down"))
    _patch(monkeypatch, sender)
    out = _run(vp.upload_insurance_impl("Bearer t", None, _file(), 2000000, "2099-01-01", True, "", ""))
    assert out["insurance_pending_review"] is True
    out2 = _run(vp.upload_rpas_impl("Bearer t", None, _file("r.pdf"), "advanced", ""))
    assert out2["rpas_document_id"]


def test_slow_email_times_out(monkeypatch):
    async def slow(**kw):
        await asyncio.sleep(5)
        return True
    monkeypatch.setattr(crn, "SEND_TIMEOUT_S", 0.05)
    _patch(monkeypatch, slow)
    assert _run(crn.notify_insurance_uploaded(MEMBER, {"liability_cad": 2000000})) is False
