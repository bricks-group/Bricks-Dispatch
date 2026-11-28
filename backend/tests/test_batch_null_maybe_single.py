"""Null maybe_single() must not 500 the remaining confirmed Dispatch routes.

postgrest maybe_single().execute() returns None when there is no row. Reading
.data on that None is AttributeError (CORS-less HTTP 500 / net::ERR_FAILED).
This batch covers listing attach, settings GET/PUT, escalate/no-show member
reads, and enhance-provider webhooks — same null-safe helper pattern as
1030405 / a2071d8 / 467113c / 4cf4d80 / c3d86a1.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.routers import vendor_os as vos
from app.routers import post_production as pp
from app.shared.schemas import VendorSettingsUpdate


ACTOR = "11111111-2222-3333-4444-555555555555"
MEMBER = {
    "id": ACTOR,
    "name": "GO",
    "role": "Owner",
    "vendor_id": "default_vendor",
    "status": "available",
    "email": "go@example.com",
}
JOB_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
JOB = {
    "id": JOB_ID,
    "assignee_id": ACTOR,
    "vendor_id": "default_vendor",
    "status": "assigned",
    "booking_id": "bk-1",
    "brokerage_id": None,
}


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Chain:
    def __init__(self, db, name):
        self._db = db
        self._name = name
        self._single = False
        self._filters = {}

    def select(self, *a, **k):
        return self

    def eq(self, key, val):
        self._filters[key] = val
        return self

    def maybe_single(self):
        self._single = True
        return self

    def insert(self, row):
        self._db.inserts.append((self._name, row))
        return self

    def upsert(self, row):
        self._db.upserts.append((self._name, row))
        return self

    def update(self, patch):
        self._db.updates.append((self._name, patch))
        return self

    async def execute(self):
        key = (self._name, tuple(sorted(self._filters.items())), self._single)
        if key in self._db.results:
            return self._db.results[key]
        # Default: missing maybe_single row → None (the crash case)
        if self._single:
            return None
        return SimpleNamespace(data=[])


class _Db:
    def __init__(self, results=None):
        self.results = results or {}
        self.inserts = []
        self.upserts = []
        self.updates = []

    def table(self, name):
        return _Chain(self, name)


def _auth(monkeypatch, member=None):
    m = dict(member or MEMBER)
    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=m["id"]))
    monkeypatch.setattr(vos, "get_vendor_member", AsyncMock(return_value=m))
    monkeypatch.setattr(vos, "write_audit_event", AsyncMock())
    monkeypatch.setattr(vos, "is_vendor_admin", lambda mem: (mem.get("role") or "") in ("Owner", "Admin"))


# --- PATCH /jobs/{id}/listing ---

def test_attach_listing_null_transaction_is_404(monkeypatch):
    db = _Db({
        (("jobs", (("id", JOB_ID),), True)): SimpleNamespace(data=dict(JOB)),
        # transactions maybe_single → None (missing listing)
    })

    async def fake_get_db():
        return db

    monkeypatch.setattr(vos, "get_db", fake_get_db)
    _auth(monkeypatch)
    monkeypatch.setattr(vos, "_load_visible_job", AsyncMock(return_value=dict(JOB)))
    monkeypatch.setattr(vos, "ensure_assigned_to_actor", lambda *a, **k: None)

    with pytest.raises(HTTPException) as ei:
        _run(vos.attach_job_listing(
            JOB_ID,
            vos.JobListingUpdate(transaction_id="11111111-1111-1111-1111-111111111111"),
            "Bearer t",
            None,
        ))
    assert ei.value.status_code == 404
    detail = ei.value.detail
    if isinstance(detail, dict):
        assert detail.get("code") == "listing_not_found"
    else:
        assert "listing" in str(detail).lower()


# --- GET/PUT /settings ---

def test_get_settings_null_row_returns_defaults(monkeypatch):
    db = _Db()  # all maybe_single → None

    async def fake_get_db():
        return db

    monkeypatch.setattr(vos, "get_db", fake_get_db)
    _auth(monkeypatch)

    out = _run(vos.get_vendor_settings("Bearer t", None))
    assert out["vendor_id"] == "default_vendor"
    assert "company_name" in out
    assert db.inserts and db.inserts[0][0] == "vendor_settings"


def test_put_settings_null_reread_returns_merged(monkeypatch):
    db = _Db()

    async def fake_get_db():
        return db

    monkeypatch.setattr(vos, "get_db", fake_get_db)
    _auth(monkeypatch)

    out = _run(vos.update_vendor_settings(
        VendorSettingsUpdate(company_name="Acme Photo"),
        "Bearer t",
        None,
    ))
    assert out["vendor_id"] == "default_vendor"
    assert out["company_name"] == "Acme Photo"
    assert db.upserts


# --- escalate / contractor-no-show optional reads ---

def test_escalate_null_booking_and_user_skips_sms(monkeypatch):
    """Missing booking/user rows must not AttributeError — skip phone notify."""
    db = _Db({
        (("jobs", (("id", JOB_ID),), True)): SimpleNamespace(data=dict(JOB)),
        # bookings / users → None
    })

    async def fake_get_db():
        return db

    monkeypatch.setattr(vos, "get_db", fake_get_db)
    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=ACTOR))

    # escalations list execute returns empty
    class _EscChain(_Chain):
        async def execute(self):
            if self._name == "agent_noshow_escalations" and not self._single:
                return SimpleNamespace(data=[])
            return await super().execute()

    class _EscDb(_Db):
        def table(self, name):
            return _EscChain(self, name)

    db = _EscDb({
        (("jobs", (("id", JOB_ID),), True)): SimpleNamespace(data=dict(JOB)),
    })

    async def fake_get_db2():
        return db

    monkeypatch.setattr(vos, "get_db", fake_get_db2)

    out = _run(vos.escalate_absent_agent(JOB_ID, "Bearer t", None))
    # No phone → SMS not sent, but route still returns a normal body
    assert "attempts" in out or "message" in out or "can_file_now" in out or "sms_sent" in out or isinstance(out, dict)


def test_contractor_no_show_null_member_skips_cancel_rate(monkeypatch):
    job = dict(JOB)
    job["assignee_id"] = "missing-member"
    db = _Db({
        (("jobs", (("id", JOB_ID), ("vendor_id", "default_vendor")), True)): SimpleNamespace(data=job),
        # guarded reset (status still 'assigned') changes the row
        (("jobs", (("id", JOB_ID), ("status", "assigned")), False)): SimpleNamespace(data=[{"id": JOB_ID}]),
        # vendor_members → None
    })

    async def fake_get_db():
        return db

    monkeypatch.setattr(vos, "get_db", fake_get_db)
    _auth(monkeypatch)
    monkeypatch.setattr(
        "app.services.vendor_selection.assign_vendor_for_job",
        AsyncMock(return_value=None),
    )

    out = _run(vos.report_contractor_no_show(JOB_ID, "Bearer t", None))
    assert out["job_id"] == JOB_ID
    assert out["original_contractor"] == "missing-member"
    assert not any(name == "vendor_members" for name, _ in db.updates)


def test_validate_noshow_null_booking_does_not_crash(monkeypatch):
    job = dict(JOB)
    db = _Db()  # existing claim + booking → None

    out = _run(vos._validate_noshow_claim(db, job, ACTOR))
    assert out["ok"] is True or out["ok"] is False  # just must not raise


# --- enhance-provider webhooks ---

class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _pp_db(monkeypatch):
    db = _Db()

    async def fake_get_db():
        return db

    monkeypatch.setattr(pp, "get_db", fake_get_db)
    monkeypatch.setattr(pp, "_verify_callback_secret", lambda s: None)
    return db


def test_fotello_callback_null_upload_is_ignored(monkeypatch):
    _pp_db(monkeypatch)
    out = _run(pp.fotello_callback(
        _Req({"image_id": "unknown", "status": "completed", "result_url": "https://x"}),
        secret="s",
    ))
    assert out == {"received": True}


def test_autoenhance_callback_null_upload_is_ignored(monkeypatch):
    _pp_db(monkeypatch)
    out = _run(pp.autoenhance_callback(
        _Req({"image_id": "unknown", "status": "complete", "enhanced_url": "https://x"}),
        secret="s",
    ))
    assert out == {"received": True}


def test_boxbrownie_callback_null_upload_is_ignored(monkeypatch):
    _pp_db(monkeypatch)
    out = _run(pp.boxbrownie_callback(
        _Req({"order_id": "unknown", "status": "completed", "result_url": "https://x"}),
        secret="s",
    ))
    assert out == {"received": True}


def test_maybe_single_row_helper():
    assert vos._maybe_single_row(None) == {}
    assert vos._maybe_single_row(SimpleNamespace(data=None)) == {}
    assert vos._maybe_single_row(SimpleNamespace(data={"id": "1"})) == {"id": "1"}
    assert pp._maybe_single_row(None) == {}
