"""Null maybe_single() on the by-id member lookup must not break GO auth.

Applicants (www /photographers apply) and owner-added members get a random
vendor_members.id. When they later sign in on GO, the auth user id differs
from the member id, so the by-id PostgREST read has no row and
maybe_single().execute() returns None. lookup_vendor_member used to read
.data on that None: AttributeError -> 401 "Invalid session" (Auth API path)
or HTTP 500 with no CORS (Fair-Use JWT path). The email match must win.
"""
from __future__ import annotations

import pytest

import asyncio
from types import SimpleNamespace

from app.shared import auth as auth_mod

AUTH_UID = "11111111-aaaa-4bbb-8ccc-000000000001"
MEMBER = {
    "id": "22222222-aaaa-4bbb-8ccc-000000000002",
    "email": "applicant@example.com",
    "role": "Photographer",
    "vendor_id": "default_vendor",
    "is_active": True,
}


class _Chain:
    def __init__(self, by_id, by_email):
        self._by_id = by_id
        self._by_email = by_email
        self._mode = None

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def ilike(self, *a, **k):
        self._mode = "email"
        return self

    def limit(self, *a, **k):
        return self

    def maybe_single(self):
        self._mode = "single"
        return self

    async def execute(self):
        if self._mode == "single":
            return self._by_id  # postgrest returns None for zero rows
        return SimpleNamespace(data=list(self._by_email))


class _Db:
    def __init__(self, by_id=None, by_email=()):
        self._by_id = by_id
        self._by_email = by_email

    def table(self, name):
        assert name == "vendor_members", name
        return _Chain(self._by_id, self._by_email)


def _patch_db(monkeypatch, db):
    async def fake_get_db():
        return db

    monkeypatch.setattr(auth_mod, "get_db", fake_get_db)


def test_lookup_by_email_when_by_id_maybe_single_is_none(monkeypatch):
    _patch_db(monkeypatch, _Db(by_id=None, by_email=[dict(MEMBER)]))
    out = asyncio.run(auth_mod.lookup_vendor_member(AUTH_UID, "Applicant@Example.com"))
    assert out and out["id"] == MEMBER["id"]


def test_no_member_is_none_not_crash(monkeypatch):
    _patch_db(monkeypatch, _Db(by_id=None, by_email=[]))
    assert asyncio.run(auth_mod.lookup_vendor_member(AUTH_UID, "nobody@example.com")) is None


def _auth_402_local(monkeypatch, db, email_confirmed, amr):
    from supabase_auth.errors import AuthApiError

    from app.shared import fair_use_auth as fua

    class _Auth:
        async def get_user(self, token):
            raise AuthApiError("exceed_storage_size_quota", 402, None)

    db.auth = _Auth()
    _patch_db(monkeypatch, db)
    fua.clear_auth_cooldown()
    claims = {"sub": AUTH_UID, "session_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
              "amr": [{"method": m, "timestamp": 1} for m in amr]}
    monkeypatch.setattr(fua, "verify_and_load_auth_user", lambda tok, *a: {
        "id": AUTH_UID, "email": MEMBER["email"], "email_confirmed": email_confirmed, "claims": claims})
    import jwt
    return jwt.encode(claims, "x", algorithm="HS256")


def test_resolve_actor_fair_use_jwt_maps_to_member_id(monkeypatch):
    """Auth API 402 -> local ES256 verify + live session -> lookup returns the member id, not 500."""
    from app.shared import fair_use_auth as fua

    db = _Db(by_id=None, by_email=[dict(MEMBER)])
    # Row is unlinked, so the first link needs a mailbox-proving sign-in (amr otp)
    # and a confirmed email (auth.users.email_confirmed_at from the same query).
    tok = _auth_402_local(monkeypatch, db, True, ["otp"])
    try:
        actor = asyncio.run(auth_mod.resolve_vendor_actor_id("Bearer " + tok, None))
    finally:
        fua.clear_auth_cooldown()
    assert actor == MEMBER["id"]


def test_resolve_actor_local_jwt_unverified_email_no_match(monkeypatch):
    """Locally verified token whose auth.users email is unconfirmed must not adopt a row by email."""
    from app.shared import fair_use_auth as fua

    db = _Db(by_id=None, by_email=[dict(MEMBER)])
    tok = _auth_402_local(monkeypatch, db, False, ["otp"])
    try:
        with pytest.raises(Exception):
            asyncio.run(auth_mod.resolve_vendor_actor_id("Bearer " + tok, None))
    finally:
        fua.clear_auth_cooldown()
