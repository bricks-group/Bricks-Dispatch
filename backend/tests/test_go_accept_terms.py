"""Team members an owner adds reach GO without the IC agreement or privacy policy.

Applicants accept both on the www application (terms_accepted_at). A member
added from the team screen never did, yet gets jobs once approved. GO now asks
once (pop-ups read to the end) and this endpoint records the acceptance.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.routers import vendor_profile as vp
from app.routers import auth as auth_router
from app.routers.onboarding import _IC_AGREEMENT_VERSION, _IC_AMENDMENT_VERSION
from app.shared import pg_fallback
from app.shared.vendor_identity import needs_terms

ACTOR = "11111111-2222-3333-4444-555555555555"
MEMBER = {"id": ACTOR, "name": "Crew", "role": "Photographer", "vendor_id": "default_vendor",
          "onboarding_status": "approved", "is_active": True, "terms_accepted_at": None,
          "ic_agreement_version": None, "internal_notes": "Added by owner"}


class _Chain:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.patch = None

    def update(self, patch):
        self.patch = patch
        return self

    def insert(self, row):
        self.db.inserts.append((self.name, row))
        return self

    def eq(self, col, val):
        self.db.filters.append((self.name, col, val))
        return self

    async def execute(self):
        if self.patch is not None:
            if self.db.quota:
                raise RuntimeError("402 Payment Required: fair use quota exceeded")
            self.db.updates.append((self.name, self.patch))
            return SimpleNamespace(data=[{**MEMBER, **self.patch}])
        return SimpleNamespace(data=[])


class _Db:
    def __init__(self, quota=False):
        self.quota = quota
        self.updates, self.inserts, self.filters = [], [], []

    def table(self, name):
        return _Chain(self, name)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _patch(monkeypatch, db, member=None):
    async def fake_get_db():
        return db
    monkeypatch.setattr(vp, "get_db", fake_get_db)
    import app.shared.audit as audit
    monkeypatch.setattr(audit, "get_db", fake_get_db)
    monkeypatch.setattr(vp, "resolve_vendor_actor_id", AsyncMock(return_value=ACTOR))
    monkeypatch.setattr(vp, "get_vendor_member", AsyncMock(return_value=dict(member or MEMBER)))


A1 = {"ic_amendment_version": _IC_AMENDMENT_VERSION, "ic_amendment_accepted_at": "2026-11-02T16:00:00+00:00"}


def _body(version=_IC_AGREEMENT_VERSION, read=("ic", "privacy", "ic_a1"), amendment=_IC_AMENDMENT_VERSION):
    return vp.AcceptTermsBody(ic_agreement_version=version, ic_amendment_version=amendment, read=list(read))


def test_needs_terms_only_for_contractors_without_acceptance():
    assert needs_terms(MEMBER) is True
    accepted = {**MEMBER, "terms_accepted_at": "2026-10-01T00:00:00+00:00"}
    # Still onboarding: the current agreement version is required.
    assert needs_terms({**accepted, **A1, "ic_agreement_version": _IC_AGREEMENT_VERSION}) is False
    assert needs_terms({**accepted, **A1, "ic_agreement_version": "0.1"}) is True
    # Live vendors on an older version are not re-gated for v1.9, only for amendment A1.
    live = {**accepted, "onboarding_completed_at": "2026-10-01T00:00:00+00:00"}
    assert needs_terms({**live, **A1}) is False
    assert needs_terms(live) is True
    assert needs_terms({**live, "ic_amendment_version": "IC-A0"}) is True
    assert needs_terms({**MEMBER, "role": "Owner"}) is False
    assert needs_terms({**MEMBER, "payout_mode": "owner_full"}) is False
    assert needs_terms(None) is False


def test_me_carries_needs_terms(monkeypatch):
    monkeypatch.setattr(auth_router, "resolve_vendor_actor_id", AsyncMock(return_value=ACTOR))
    monkeypatch.setattr(auth_router, "get_vendor_member", AsyncMock(return_value=dict(MEMBER)))
    out = _run(auth_router.vendor_os_me("Bearer t"))
    assert out["needs_terms"] is True and out["id"] == ACTOR


def test_accept_records_version_time_and_audit(monkeypatch):
    db = _Db()
    _patch(monkeypatch, db)
    out = _run(vp.accept_terms_impl(_body(), "Bearer t", None))
    assert out["ic_agreement_version"] == _IC_AGREEMENT_VERSION and out["needs_terms"] is False
    (table, patch), = db.updates
    assert table == "vendor_members"
    assert patch["ic_agreement_version"] == _IC_AGREEMENT_VERSION
    assert patch["terms_accepted_at"] == out["terms_accepted_at"]
    assert patch["ic_amendment_version"] == _IC_AMENDMENT_VERSION
    assert patch["ic_amendment_accepted_at"] == out["terms_accepted_at"]
    assert patch["internal_notes"].startswith("Added by owner\n")
    assert "accepted in GO" in patch["internal_notes"]
    assert ("vendor_members", "id", ACTOR) in db.filters
    audits = [r for t, r in db.inserts if t == "audit_events"]
    assert audits and audits[0]["action"] == "terms_accepted"


def test_refuses_unread_documents(monkeypatch):
    db = _Db()
    _patch(monkeypatch, db)
    with pytest.raises(HTTPException) as e:
        _run(vp.accept_terms_impl(_body(read=("ic",)), "Bearer t", None))
    assert e.value.status_code == 422
    assert db.updates == []


def test_refuses_outdated_agreement_version(monkeypatch):
    db = _Db()
    _patch(monkeypatch, db)
    with pytest.raises(HTTPException) as e:
        _run(vp.accept_terms_impl(_body(version="1.8"), "Bearer t", None))
    assert e.value.status_code == 409
    assert db.updates == []


def test_already_accepted_is_a_no_op(monkeypatch):
    db = _Db()
    done = {**MEMBER, "terms_accepted_at": "2026-10-20T00:00:00+00:00", "ic_agreement_version": _IC_AGREEMENT_VERSION, **A1}
    _patch(monkeypatch, db, done)
    out = _run(vp.accept_terms_impl(_body(), "Bearer t", None))
    assert out["terms_accepted_at"] == "2026-10-20T00:00:00+00:00"
    assert db.updates == []


def test_fair_use_quota_falls_back_to_postgres(monkeypatch):
    db = _Db(quota=True)
    _patch(monkeypatch, db)
    seen = {}
    def fake_pg(mid, fields):
        seen["mid"], seen["fields"] = mid, fields
        return True
    monkeypatch.setattr(pg_fallback, "pg_update_vendor_member_fields", fake_pg)
    out = _run(vp.accept_terms_impl(_body(), "Bearer t", None))
    assert seen["mid"] == ACTOR
    assert seen["fields"]["ic_agreement_version"] == _IC_AGREEMENT_VERSION
    assert out["needs_terms"] is False


def test_pg_fallback_whitelists_terms_columns(monkeypatch):
    captured = {}

    class _Cur:
        rowcount = 1
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, vals):
            captured["sql"], captured["vals"] = sql, vals

    class _Conn:
        def cursor(self): return _Cur()
        def close(self): pass

    monkeypatch.setattr(pg_fallback, "_pg_connect", lambda: _Conn())
    ok = pg_fallback.pg_update_vendor_member_fields(ACTOR, {"terms_accepted_at": "t", "ic_agreement_version": "1.9", "role": "Owner"})
    assert ok is True
    assert "terms_accepted_at = %s" in captured["sql"] and "ic_agreement_version = %s" in captured["sql"]
    assert "role" not in captured["sql"]


def test_live_vendor_accepts_only_the_amendment(monkeypatch):
    db = _Db()
    live = {**MEMBER, "terms_accepted_at": "2026-10-01T00:00:00+00:00", "ic_agreement_version": "1.8",
            "onboarding_completed_at": "2026-10-02T00:00:00+00:00"}
    _patch(monkeypatch, db, live)
    with pytest.raises(HTTPException) as e:
        _run(vp.accept_terms_impl(vp.AcceptTermsBody(read=["ic_a1"], ic_amendment_version="IC-A0"), "Bearer t", None))
    assert e.value.status_code == 409
    with pytest.raises(HTTPException) as e:
        _run(vp.accept_terms_impl(vp.AcceptTermsBody(read=[], ic_amendment_version=_IC_AMENDMENT_VERSION), "Bearer t", None))
    assert e.value.status_code == 422
    out = _run(vp.accept_terms_impl(vp.AcceptTermsBody(read=["ic_a1"], ic_amendment_version=_IC_AMENDMENT_VERSION),
                                    "Bearer t", None))
    (table, patch), = db.updates
    assert patch["ic_amendment_version"] == _IC_AMENDMENT_VERSION and "terms_accepted_at" not in patch
    assert out["ic_amendment_accepted_at"] == patch["ic_amendment_accepted_at"]
    assert "IC-A1 v1.0 accepted in GO" in patch["internal_notes"]


def test_amendment_text_endpoint():
    from app.routers import onboarding
    out = _run(onboarding.get_ic_amendment())
    assert out["version"] == "IC-A1 v1.0" and out["binds_without_acceptance"].startswith("2026-11-16")
    assert "IC-A1 v1.0" in out["text"].splitlines()[0]
    assert "help@brxs.ca" in out["text"]
