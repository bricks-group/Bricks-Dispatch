"""Phone-OTP (SMS) users on GO: member lookup by id / auth_user_id only.

A phone-only Supabase user has no email on the Auth record until they confirm
one, so lookup_vendor_member must:
- match vendor_members.auth_user_id = JWT sub (parity with pg_select_vendor_member);
- never run an email query with an empty email (no email-only adoption);
- skip the auth_user_id query for non-UUID actor ids (internal X-User-Id callers);
- leave the existing id / Kam / confirmed-email behaviour unchanged.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.shared import auth as auth_mod

AUTH_UID = "33333333-aaaa-4bbb-8ccc-000000000003"
LINKED = {
    "id": "44444444-aaaa-4bbb-8ccc-000000000004",
    "auth_user_id": AUTH_UID,
    "email": "pro@example.com",
    "role": "Photographer",
    "vendor_id": "default_vendor",
    "is_active": True,
    "onboarding_status": "approved",
}


class _Chain:
    def __init__(self, db):
        self.db = db
        self.filters = []
        self.single = False

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def ilike(self, col, val):
        self.filters.append(("ilike", col, val))
        return self

    def limit(self, *a, **k):
        return self

    def maybe_single(self):
        self.single = True
        return self

    async def execute(self):
        self.db.calls.append(list(self.filters))
        rows = [r for r in self.db.rows if all(self._match(r, f) for f in self.filters)]
        if self.single:
            return SimpleNamespace(data=rows[0]) if rows else None
        return SimpleNamespace(data=rows)

    @staticmethod
    def _match(row, f):
        op, col, val = f
        have = str(row.get(col) or "")
        if op == "ilike":
            return have.lower() == str(val).lower()
        return have == str(val)


class _Db:
    def __init__(self, rows=(), user=None):
        self.rows = [dict(r) for r in rows]
        self.calls = []
        self._user = user

        class _Auth:
            async def get_user(_self, _token):
                return SimpleNamespace(user=self._user)

        self.auth = _Auth()

    def table(self, name):
        assert name == "vendor_members", name
        return _Chain(self)


def _patch(monkeypatch, db):
    async def fake_get_db():
        return db

    monkeypatch.setattr(auth_mod, "get_db", fake_get_db)


def _email_queries(db):
    return [c for c in db.calls if any(f[1] == "email" for f in c)]


def test_phone_user_resolves_by_auth_user_id(monkeypatch):
    db = _Db(rows=[LINKED])
    _patch(monkeypatch, db)
    out = asyncio.run(auth_mod.lookup_vendor_member(AUTH_UID, ""))
    assert out and out["id"] == LINKED["id"]
    assert _email_queries(db) == []
    assert [("eq", "auth_user_id", AUTH_UID)] in db.calls


def test_phone_user_without_link_is_none_and_never_queries_email(monkeypatch):
    other = dict(LINKED, auth_user_id=None, email="")
    db = _Db(rows=[other])
    _patch(monkeypatch, db)
    assert asyncio.run(auth_mod.lookup_vendor_member(AUTH_UID, None)) is None
    assert _email_queries(db) == []


def test_non_uuid_actor_skips_auth_user_id_query(monkeypatch):
    db = _Db(rows=[])
    _patch(monkeypatch, db)
    assert asyncio.run(auth_mod.lookup_vendor_member("default_user", "")) is None
    assert not any(f[1] == "auth_user_id" for c in db.calls for f in c)


def test_auth_user_id_match_beats_email_row(monkeypatch):
    stray = {"id": "55555555-aaaa-4bbb-8ccc-000000000005", "email": "pro@example.com", "role": "Applicant"}
    db = _Db(rows=[stray, LINKED])
    _patch(monkeypatch, db)
    out = asyncio.run(auth_mod.lookup_vendor_member(AUTH_UID, "pro@example.com"))
    assert out["id"] == LINKED["id"]


def test_auth_user_id_query_error_is_non_fatal(monkeypatch):
    db = _Db(rows=[])

    class _BadChain(_Chain):
        async def execute(self):
            if any(f[1] == "auth_user_id" for f in self.filters):
                raise Exception("column vendor_members.auth_user_id does not exist")
            return await super().execute()

    db.table = lambda name: _BadChain(db)
    _patch(monkeypatch, db)
    assert asyncio.run(auth_mod.lookup_vendor_member(AUTH_UID, "")) is None


def test_resolve_actor_for_phone_session(monkeypatch):
    user = SimpleNamespace(id=AUTH_UID, email="", phone="14165550123")
    db = _Db(rows=[LINKED], user=user)
    _patch(monkeypatch, db)
    actor = asyncio.run(auth_mod.resolve_vendor_actor_id("Bearer tok"))
    assert actor == LINKED["id"]
    assert _email_queries(db) == []


def test_resolve_actor_for_unlinked_phone_session_is_403(monkeypatch):
    user = SimpleNamespace(id=AUTH_UID, email=None, phone="14165550123")
    db = _Db(rows=[], user=user)
    _patch(monkeypatch, db)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(auth_mod.resolve_vendor_actor_id("Bearer tok"))
    assert ei.value.status_code in (401, 403)
