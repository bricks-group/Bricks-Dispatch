"""GO profile shows the photographer's latest certificate-of-insurance upload.

www /photographers: "Upload your certificate in the Bricks app" and "drone
liability of at least $2M on a certificate Bricks verifies". The upload endpoint
existed but GO had no screen and the profile never said a COI was in review, so
after a reload the photographer couldn't tell it was received.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.routers import vendor_profile as vp

ACTOR = "11111111-2222-3333-4444-555555555555"
MEMBER = {
    "id": ACTOR, "name": "Pilot", "role": "Photographer",
    "vendor_id": "default_vendor", "status": "available", "email": "pilot@example.com",
}

COI_ROWS = [
    {"id": "old", "status": "rejected", "created_at": "2026-10-01T10:00:00+00:00",
     "expiry_date": "2027-01-01", "validation_result": {"declared": {"liability_cad": 1000000}}},
    {"id": "new", "status": "pending", "created_at": "2026-10-18T10:00:00+00:00",
     "expiry_date": "2027-10-01", "file_url": "onboarding/secret-key.pdf",
     "validation_result": {"declared": {"liability_cad": 2000000, "aviation_covered": True,
                                        "insurer": "Global Aerospace", "policy_number": "P-1"}}},
]


class _Chain:
    def __init__(self, db, name):
        self._db, self._name = db, name
        self._filters: dict = {}
        self._single = False

    def select(self, cols="*", *a, **k):
        self._db.selects.append((self._name, cols))
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def maybe_single(self):
        self._single = True
        return self

    async def execute(self):
        if self._single:
            return None
        if self._name == "vendor_documents" and self._filters.get("doc_type") == "coi":
            if self._db.coi_error:
                raise RuntimeError("column missing")
            return SimpleNamespace(data=list(self._db.coi))
        if self._name == "vendor_documents":
            return SimpleNamespace(data=[{"id": "p1", "doc_type": "portfolio_photo", "file_url": "x", "status": "pending", "created_at": "2026-10-01"}])
        return SimpleNamespace(data=[])


class _Db:
    def __init__(self, coi=None, coi_error=False):
        self.coi = coi or []
        self.coi_error = coi_error
        self.selects: list = []

    def table(self, name):
        return _Chain(self, name)


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _patch(monkeypatch, db, member=None):
    async def fake_get_db():
        return db
    monkeypatch.setattr(vp, "get_db", fake_get_db)
    monkeypatch.setattr(vp, "resolve_vendor_actor_id", AsyncMock(return_value=ACTOR))
    monkeypatch.setattr(vp, "get_vendor_member", AsyncMock(return_value=dict(member or MEMBER)))


def test_latest_coi_review_picks_newest_and_hides_file_key():
    out = vp.latest_coi_review(COI_ROWS)
    assert out == {
        "status": "pending", "uploaded_at": "2026-10-18T10:00:00+00:00", "expires_on": "2027-10-01",
        "liability_cad": 2000000, "aviation_covered": True, "insurer": "Global Aerospace",
    }
    assert "file_url" not in out and "policy_number" not in out


def test_latest_coi_review_none_and_odd_rows():
    assert vp.latest_coi_review([]) is None
    assert vp.latest_coi_review(None) is None
    odd = vp.latest_coi_review([{"status": "flagged", "created_at": "2026-10-02", "validation_result": "junk"}])
    assert odd["status"] == "rejected" and odd["liability_cad"] is None and odd["expires_on"] is None
    weird = vp.latest_coi_review([{"status": "mystery", "validation_result": {"declared": {"liability_cad": "abc", "expires_on": "2027-05-05T00:00:00"}}}])
    assert weird["status"] == "pending" and weird["liability_cad"] is None and weird["expires_on"] == "2027-05-05"


def test_profile_carries_insurance_review_and_status(monkeypatch):
    _patch(monkeypatch, _Db(coi=COI_ROWS))
    out = _run(vp.get_profile_impl("Bearer t", None))
    assert out["insurance_review"]["status"] == "pending"
    assert out["insurance_review"]["liability_cad"] == 2000000
    assert out["insurance"]["valid"] is False
    assert out["insurance"]["reason"] == "insurance_not_verified"
    assert out["portfolio_samples"] is not None


def test_profile_without_coi_has_null_review(monkeypatch):
    _patch(monkeypatch, _Db())
    out = _run(vp.get_profile_impl("Bearer t", None))
    assert out["insurance_review"] is None


def test_coi_read_failure_never_breaks_profile(monkeypatch):
    _patch(monkeypatch, _Db(coi=COI_ROWS, coi_error=True))
    out = _run(vp.get_profile_impl("Bearer t", None))
    assert out["insurance_review"] is None
    assert out["availability"] is True


def test_verified_member_reports_valid(monkeypatch):
    member = {**MEMBER, "coi_verified": True, "coi_aviation_covered": True,
              "coi_liability_cad": 2000000, "coi_expires_on": "2099-01-01"}
    verified_rows = [{**COI_ROWS[1], "status": "verified"}]
    _patch(monkeypatch, _Db(coi=verified_rows), member)
    out = _run(vp.get_profile_impl("Bearer t", None))
    assert out["insurance"]["valid"] is True
    assert out["insurance_review"]["status"] == "verified"
