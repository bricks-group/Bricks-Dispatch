"""Null maybe_single() must not 500 onboarding status/RPAS/compliance/portfolio.

postgrest maybe_single().execute() returns None when there is no row. Reading
.data on that None was AttributeError (CORS-less HTTP 500). Missing member
rows must return clean 404; approval semantics unchanged (status stays pending).
Also covers notifications.store_push_token when the push_tokens lookup is null.
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.routers import onboarding as ob
from app.services import notifications as notif
from app.shared.database import maybe_single_row


MEMBER_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


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

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def maybe_single(self):
        self._single = True
        return self

    def update(self, patch):
        self._db.updates.append((self._name, patch))
        return self

    def insert(self, row):
        self._db.inserts.append((self._name, row))
        return self

    def upsert(self, row, **k):
        self._db.upserts.append((self._name, row))
        return self

    async def execute(self):
        key = (self._name, tuple(sorted(self._filters.items())), self._single)
        if key in self._db.results:
            return self._db.results[key]
        if self._single:
            return None  # the crash case
        return SimpleNamespace(data=[])


class _Db:
    def __init__(self, results=None):
        self.results = results or {}
        self.inserts = []
        self.upserts = []
        self.updates = []

    def table(self, name):
        return _Chain(self, name)


def _fake_request(api_key: str = "test-internal-key"):
    req = MagicMock()
    req.headers = {"X-Internal-Api-Key": api_key}
    return req


def test_maybe_single_row_helper():
    assert maybe_single_row(None) == {}
    assert maybe_single_row(SimpleNamespace(data=None)) == {}
    assert maybe_single_row(SimpleNamespace(data={"id": "1"})) == {"id": "1"}
    assert maybe_single_row(SimpleNamespace(data=["not", "a", "dict"])) == {}


def test_application_status_null_member_is_404(monkeypatch):
    db = _Db()

    async def fake_get_db():
        return db

    monkeypatch.setattr(ob, "get_db", fake_get_db)
    with pytest.raises(HTTPException) as ei:
        _run(ob.get_application_status(MEMBER_ID, token=None))
    assert ei.value.status_code == 404
    assert "Application not found" in str(ei.value.detail)


def test_upload_documents_null_member_is_404(monkeypatch):
    db = _Db()

    async def fake_get_db():
        return db

    monkeypatch.setattr(ob, "get_db", fake_get_db)
    with pytest.raises(HTTPException) as ei:
        _run(ob.upload_compliance_document(
            member_id=MEMBER_ID,
            doc_type="coi",
            filename="coi.pdf",
            file_data="YWJj",  # base64 "abc"
            expiry_date=None,
            token=None,
            application_token=None,
        ))
    assert ei.value.status_code == 404
    assert "Member not found" in str(ei.value.detail)


def test_upload_portfolio_null_member_is_404(monkeypatch):
    db = _Db()

    async def fake_get_db():
        return db

    monkeypatch.setattr(ob, "get_db", fake_get_db)
    with pytest.raises(HTTPException) as ei:
        _run(ob.upload_portfolio_sample(
            member_id=MEMBER_ID,
            media_type="hdr",
            filename="shot.jpg",
            file_data="YWJj",
            link=None,
            token=None,
        ))
    assert ei.value.status_code == 404
    assert "Member not found" in str(ei.value.detail)


def test_validate_rpas_null_member_is_404(monkeypatch):
    db = _Db()

    async def fake_get_db():
        return db

    monkeypatch.setattr(ob, "get_db", fake_get_db)
    monkeypatch.setenv("INTERNAL_API_KEYS", "test-internal-key")
    with pytest.raises(HTTPException) as ei:
        _run(ob.validate_rpas_certificate(MEMBER_ID, _fake_request()))
    assert ei.value.status_code == 404
    assert "Member not found" in str(ei.value.detail)


def test_validate_compliance_null_member_is_404(monkeypatch):
    db = _Db()

    async def fake_get_db():
        return db

    monkeypatch.setattr(ob, "get_db", fake_get_db)
    monkeypatch.setenv("INTERNAL_API_KEYS", "test-internal-key")
    with pytest.raises(HTTPException) as ei:
        _run(ob.validate_compliance(MEMBER_ID, "coi", _fake_request()))
    assert ei.value.status_code == 404
    assert "Member not found" in str(ei.value.detail)


def test_store_push_token_null_existing_inserts(monkeypatch):
    """store_push_token crashed on existing.data when maybe_single returned None."""
    db = _Db()
    inserted = {}

    class _InsertChain(_Chain):
        async def execute(self):
            if self._op == "insert" if hasattr(self, "_op") else False:
                return SimpleNamespace(data=[])
            return await super().execute()

    # Use a custom db that records inserts and returns success
    class _PushDb(_Db):
        def table(self, name):
            return _PushChain(self, name)

    class _PushChain(_Chain):
        def insert(self, row):
            self._db.inserts.append((self._name, row))
            self._insert_row = row
            return self

        async def execute(self):
            if hasattr(self, "_insert_row"):
                return SimpleNamespace(data=[self._insert_row])
            return await super().execute()

    db = _PushDb()

    async def fake_get_db():
        return db

    monkeypatch.setattr(notif, "get_db", fake_get_db)
    out = _run(notif.store_push_token(MEMBER_ID, "ExponentPushToken[test]", "expo"))
    assert out["registered"] is True
    assert out["id"]
    assert db.inserts and db.inserts[0][0] == "push_tokens"
    assert db.inserts[0][1]["token"] == "ExponentPushToken[test]"


def test_notify_assignment_null_member_no_raise(monkeypatch):
    db = _Db()

    async def fake_get_db():
        return db

    monkeypatch.setattr(notif, "get_db", fake_get_db)
    monkeypatch.setattr(notif, "create_notification", AsyncMock(return_value={}))
    # SMS / push helpers may import lazily — stub if present
    import app.notifications as nmod
    monkeypatch.setattr(nmod, "notify_job_assigned_to_vendor", lambda *a, **k: False, raising=False)

    out = _run(notif.notify_assignment({"id": "job-1", "address": "1 Main"}, MEMBER_ID))
    assert isinstance(out, dict)
    assert "sms" in out
