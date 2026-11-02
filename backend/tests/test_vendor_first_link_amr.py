"""First vendor_members link needs a mailbox-proving sign-in (Sentinel, 2026-11-02).

A password session (or a fair-use minted token) must never create the FIRST
auth_user_id link on an unlinked vendor_members row by email. Only amr
otp / magiclink / email link, or OAuth with a provider-verified email, may.
Password sessions keep working for rows already linked to the same uid.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest

from app.shared import auth as auth_mod
from app.shared import job_scope, vendor_identity
from tests.test_vendor_otp_takeover import ATTACKER_UID, MEMBER, VENDOR_EMAIL, _Db, _patch

UID = ATTACKER_UID


def _tok(amr, **claims):
    body = {"sub": UID, "amr": [{"method": m, "timestamp": 1} for m in amr], **claims}
    return jwt.encode(body, "x", algorithm="HS256")


def _lookup(db, monkeypatch, amr, **kw):
    _patch(monkeypatch, db)
    return asyncio.run(auth_mod.lookup_vendor_member(UID, VENDOR_EMAIL, email_confirmed=True, amr=set(amr), **kw))


@pytest.mark.parametrize("amr", [["password"], [], ["recovery"], ["invite"], ["password", "mfa/totp"]])
def test_password_like_session_cannot_first_link(monkeypatch, amr):
    db = _Db([MEMBER])
    assert _lookup(db, monkeypatch, amr) is None
    assert db.updates == []
    assert db.rows[0]["auth_user_id"] is None


@pytest.mark.parametrize("amr", [["otp"], ["magiclink"], ["email/signup"], ["email-link"]])
def test_mailbox_proving_session_first_links(monkeypatch, amr):
    db = _Db([MEMBER])
    got = _lookup(db, monkeypatch, amr)
    assert got and got["id"] == MEMBER["id"]
    assert db.updates == [{"auth_user_id": UID}]


def test_password_session_works_for_row_already_linked_to_same_uid(monkeypatch):
    db = _Db([{**MEMBER, "auth_user_id": UID}])
    got = _lookup(db, monkeypatch, ["password"])
    assert got and got["id"] == MEMBER["id"]
    assert db.updates == []


def test_password_session_works_when_member_id_is_uid(monkeypatch):
    db = _Db([{**MEMBER, "id": UID}])
    got = _lookup(db, monkeypatch, ["password"])
    assert got and got["id"] == UID


def test_oauth_first_link_needs_provider_verified_email(monkeypatch):
    db = _Db([MEMBER])
    assert _lookup(db, monkeypatch, ["oauth"]) is None  # default: not provider-verified
    assert db.updates == []
    got = _lookup(db, monkeypatch, ["oauth"], first_link_ok=True)
    assert got["auth_user_id"] == UID


def _user(confirmed=True, identities=()):
    return SimpleNamespace(id=UID, email=VENDOR_EMAIL,
                           email_confirmed_at="2026-11-02T15:00:00Z" if confirmed else None,
                           identities=list(identities))


GOOGLE_OK = {"provider": "google", "identity_data": {"email": VENDOR_EMAIL, "email_verified": True}}


def test_oauth_provider_verification_rules():
    f = auth_mod.oauth_email_verified_by_provider
    assert f(_user(identities=[GOOGLE_OK]), VENDOR_EMAIL)
    assert f(_user(identities=[SimpleNamespace(**GOOGLE_OK)]), VENDOR_EMAIL.upper())
    assert not f(_user(confirmed=False, identities=[GOOGLE_OK]), VENDOR_EMAIL)
    assert not f(_user(identities=[{"provider": "google", "identity_data": {"email": VENDOR_EMAIL}}]), VENDOR_EMAIL)
    assert not f(_user(identities=[{"provider": "google", "identity_data": {"email": "x@y.z", "email_verified": True}}]), VENDOR_EMAIL)
    # the email identity's identity_data is not a provider verification
    assert not f(_user(identities=[{"provider": "email", "identity_data": {"email": VENDOR_EMAIL, "email_verified": True}}]), VENDOR_EMAIL)
    assert not f(None, VENDOR_EMAIL)


def test_first_link_allowed_matrix():
    fl = auth_mod.first_link_allowed
    assert fl({"otp"}) and fl({"magiclink"}) and fl({"email/signup"})
    assert not fl({"password"}) and not fl(set()) and not fl(None)
    assert not fl({"oauth"}) and fl({"oauth"}, oauth_email_verified=True)
    assert not fl({"password"}, oauth_email_verified=True)


def test_resolve_password_session_refused_on_unlinked_row(monkeypatch):
    db = _Db([MEMBER], user=_user())
    _patch(monkeypatch, db)
    with pytest.raises(Exception) as e:
        asyncio.run(auth_mod.resolve_vendor_actor_id("Bearer " + _tok(["password"])))
    assert getattr(e.value, "status_code", None) in (401, 403)
    assert db.rows[0]["auth_user_id"] is None


def test_resolve_google_verified_first_links(monkeypatch):
    db = _Db([MEMBER], user=_user(identities=[GOOGLE_OK]))
    _patch(monkeypatch, db)
    assert asyncio.run(auth_mod.resolve_vendor_actor_id("Bearer " + _tok(["oauth"]))) == MEMBER["id"]
    assert db.rows[0]["auth_user_id"] == UID


def test_resolve_google_unverified_refused(monkeypatch):
    db = _Db([MEMBER], user=_user(identities=[{"provider": "google", "identity_data": {"email": VENDOR_EMAIL}}]))
    _patch(monkeypatch, db)
    with pytest.raises(Exception):
        asyncio.run(auth_mod.resolve_vendor_actor_id("Bearer " + _tok(["oauth"])))
    assert db.rows[0]["auth_user_id"] is None


def test_resolve_password_session_linked_row_still_works(monkeypatch):
    db = _Db([{**MEMBER, "auth_user_id": UID}], user=_user())
    _patch(monkeypatch, db)
    assert asyncio.run(auth_mod.resolve_vendor_actor_id("Bearer " + _tok(["password"]))) == MEMBER["id"]


# --- fair-use / local-token fallback ---------------------------------------

def test_user_metadata_email_verified_is_ignored():
    forged = _tok(["password"], user_metadata={"email_verified": True})
    assert auth_mod._jwt_email_verified(forged) is False
    assert auth_mod._jwt_email_verified(_tok(["otp"], email_verified=True)) is True
    assert auth_mod._jwt_email_verified(_tok(["otp"], email_verified="true")) is False
    assert auth_mod._jwt_email_verified("not-a-jwt") is False


def _local_path(monkeypatch, db, token, pg_confirmed=False):
    class _A:
        async def get_user(_s, _t):
            raise Exception("invalid JWT: unable to parse or verify signature")
    db.auth = _A()
    _patch(monkeypatch, db)
    monkeypatch.setattr(auth_mod, "decode_supabase_user_from_jwt", lambda t: (UID, VENDOR_EMAIL))
    monkeypatch.setattr(auth_mod, "_pg_email_confirmed", lambda uid: pg_confirmed)
    return asyncio.run(auth_mod.resolve_vendor_actor_id("Bearer " + token))


def test_local_path_forged_metadata_cannot_first_link(monkeypatch):
    db = _Db([MEMBER])
    with pytest.raises(Exception):
        _local_path(monkeypatch, db, _tok(["password"], user_metadata={"email_verified": True}))
    assert db.rows[0]["auth_user_id"] is None


def test_local_path_password_with_confirmed_email_still_cannot_first_link(monkeypatch):
    db = _Db([MEMBER])
    with pytest.raises(Exception):
        _local_path(monkeypatch, db, _tok(["password"]), pg_confirmed=True)
    assert db.rows[0]["auth_user_id"] is None


def test_local_path_otp_with_server_confirmed_email_links(monkeypatch):
    db = _Db([MEMBER])
    assert _local_path(monkeypatch, db, _tok(["otp"]), pg_confirmed=True) == MEMBER["id"]
    assert db.rows[0]["auth_user_id"] == UID


def test_local_path_otp_unconfirmed_email_no_match(monkeypatch):
    db = _Db([MEMBER])
    with pytest.raises(Exception):
        _local_path(monkeypatch, db, _tok(["otp"], user_metadata={"email_verified": True}), pg_confirmed=False)


def test_pg_quota_fallback_refuses_unlinked_email_pick(monkeypatch):
    from app.shared import pg_fallback as pgf

    class _Boom:
        def table(self, *_a):
            raise RuntimeError("quota")
    _patch(monkeypatch, _Boom())
    monkeypatch.setattr(pgf, "_is_rest_quota_error", lambda e: True)
    monkeypatch.setattr(pgf, "pg_select_vendor_member", lambda a, e: dict(MEMBER))
    run = lambda amr: asyncio.run(auth_mod.lookup_vendor_member(UID, VENDOR_EMAIL, email_confirmed=True, amr=amr))
    assert run({"password"}) is None
    assert run({"otp"})["id"] == MEMBER["id"]
    monkeypatch.setattr(pgf, "pg_select_vendor_member", lambda a, e: {**MEMBER, "auth_user_id": UID})
    assert run({"password"})["id"] == MEMBER["id"]


# --- Kam / Owner by full uid --------------------------------------------------

def test_owner_match_is_full_uid_not_prefix():
    full = job_scope.KAM_MEMBER_ID
    assert full == "0c3fa7c2-d23b-4e44-90ad-ab2d19af2580"
    fake = "0c3fa7c2-0000-4000-8000-000000000000"
    assert vendor_identity.is_owner_member({"id": full})
    assert not vendor_identity.is_owner_member({"id": fake})


def test_prefix_uid_does_not_pull_kam_row(monkeypatch):
    kam = {"id": job_scope.KAM_MEMBER_ID, "email": "kam@example.com", "auth_user_id": None,
           "role": "Owner", "vendor_id": "default_vendor", "is_active": True}
    db = _Db([kam])
    _patch(monkeypatch, db)
    fake = "0c3fa7c2-0000-4000-8000-000000000000"
    got = asyncio.run(auth_mod.lookup_vendor_member(fake, None, email_confirmed=True, amr={"password"}))
    assert got is None
