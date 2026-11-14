"""Dispatch Fair-Use auth fallback: ES256 pinned key + live session (Sentinel 2026-11-14).

Ported from Bricks-OS tests/test_fair_use_auth.py. Local verification runs
only when Supabase Auth is unavailable (402 / 5xx / network): ES256 against
SUPABASE_JWT_PUBLIC_JWK with the pinned kid, HS256 rejected, session_id
required, and one read-only query proving a live auth.users row and a live
auth.sessions row owned by sub. Then the vendor_members lookup decides.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from jwt.algorithms import ECAlgorithm
from supabase_auth.errors import AuthApiError, AuthRetryableError

from app.shared import auth as auth_mod
from app.shared import fair_use_auth as fua

URL = "https://testref.supabase.co"
ISS = URL + "/auth/v1"
UID = "11111111-2222-4333-8444-555555555555"
SID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
MEMBER_ID = "ffd2492b-00df-44eb-83c5-a02888d26358"
PRIV = ec.generate_private_key(ec.SECP256R1())
KID = "d7a5a0d6-0000-4000-8000-test00000001"
PUB_JWK = {**ECAlgorithm.to_jwk(PRIV.public_key(), as_dict=True), "kid": KID, "alg": "ES256"}
PUB_JWK_JSON = json.dumps(PUB_JWK)
UUID_SECRET = "0f0e0d0c-0b0a-4908-8706-050403020100"


def b64(d):
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def claims_now(**over):
    now = int(time.time())
    c = {"aud": "authenticated", "role": "authenticated", "sub": UID, "email": "vendor@example.com",
         "iss": ISS, "iat": now, "exp": now + 3600, "session_id": SID,
         "amr": [{"method": "otp", "timestamp": now}]}
    c.update(over)
    return {k: v for k, v in c.items() if v is not None}


def tok(**over):
    return jwt.encode(claims_now(**over), PRIV, algorithm="ES256", headers={"kid": KID})


def hs256_raw(header, claims, key_bytes):
    si = f"{b64(header)}.{b64(claims)}"
    sig = base64.urlsafe_b64encode(hmac.new(key_bytes, si.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    return f"{si}.{sig}"


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.description = None
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.db.queries.append(sql)
        if self.db.fail:
            raise RuntimeError("pg down")
        sub, sid = params
        live = sub in self.db.auth_users
        self.description = [("__fua_auth_live",), ("__fua_session_live",), ("id",), ("email",), ("email_confirmed",)]
        self._row = (live, (sid, sub) in self.db.sessions, sub if live else None,
                     "vendor@example.com" if live else None, live and self.db.confirmed)

    def fetchone(self):
        return self._row


class FakeDB:
    def __init__(self):
        self.fail = False
        self.queries = []
        self.connects = 0
        self.auth_users = {UID}
        self.sessions = {(SID, UID)}
        self.confirmed = True

    def connect(self):
        db = self
        db.connects += 1

        class Conn:
            def cursor(self):
                return FakeCursor(db)

            def close(self):
                pass
        return Conn()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_PUBLIC_JWK", PUB_JWK_JSON)
    monkeypatch.delenv("SUPABASE_JWT_KID", raising=False)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", UUID_SECRET)  # present, never used
    monkeypatch.setenv("SUPABASE_URL", URL)
    db = FakeDB()
    from app.shared import pg_fallback as pf
    monkeypatch.setattr(pf, "_pg_connect", db.connect)
    fua.clear_auth_cooldown()
    st = {"auth_exc": AuthApiError("exceed_storage_size_quota", 402, None), "auth_calls": 0, "lookups": []}

    class Auth:
        async def get_user(self, token):
            st["auth_calls"] += 1
            if st["auth_exc"]:
                raise st["auth_exc"]
            return st["auth_ok"]

    class Db:
        auth = Auth()

    async def fake_get_db():
        return Db()

    async def fake_lookup(uid, email=None, **kw):
        st["lookups"].append((uid, email, kw))
        return {"id": MEMBER_ID} if uid == UID else None

    monkeypatch.setattr(auth_mod, "get_db", fake_get_db)
    monkeypatch.setattr(auth_mod, "lookup_vendor_member", fake_lookup)
    yield db, st
    fua.clear_auth_cooldown()


def call(token):
    return asyncio.run(auth_mod.resolve_vendor_actor_id("Bearer " + token))


def rejects(token, code=401):
    with pytest.raises(HTTPException) as ei:
        call(token)
    assert ei.value.status_code == code
    return ei.value


def test_valid_es256_token_resolves_member_when_auth_402(env):
    db, st = env
    assert call(tok()) == MEMBER_ID
    assert db.connects == 1 and len(db.queries) == 1
    assert "auth.users" in db.queries[0] and "auth.sessions" in db.queries[0]
    uid, email, kw = st["lookups"][0]
    assert uid == UID and email == "vendor@example.com"
    assert kw["email_confirmed"] is True and "otp" in kw["amr"]


def test_unconfirmed_email_is_passed_through(env):
    db, st = env
    db.confirmed = False
    assert call(tok()) == MEMBER_ID
    assert st["lookups"][0][2]["email_confirmed"] is False


def test_auth_5xx_and_network_fall_through(env):
    _, st = env
    st["auth_exc"] = AuthApiError("bad gateway", 503, None)
    assert call(tok()) == MEMBER_ID
    st["auth_exc"] = AuthRetryableError("connection reset", 0)
    assert call(tok()) == MEMBER_ID


def test_definitive_auth_errors_never_reach_local_check(env):
    db, st = env
    for exc in (AuthApiError("invalid JWT", 401, "bad_jwt"),
                Exception("invalid JWT: unable to parse or verify signature"),
                RuntimeError("402 exceed_storage_size_quota")):
        st["auth_exc"] = exc
        e = rejects(tok())
        assert e.detail["code"] == "invalid_session_token"
    assert db.connects == 0 and not fua.auth_cooldown_active()


def test_session_id_required(env):
    db, _ = env
    rejects(tok(session_id=None))
    rejects(tok(session_id="nope"))
    assert db.connects == 0


def test_legacy_minted_shape_rejected(env):
    now = int(time.time())
    minted = jwt.encode({"aud": "authenticated", "role": "authenticated", "sub": UID, "email": "v@example.com",
                         "iat": now, "exp": now + 7 * 24 * 3600, "iss": ISS}, UUID_SECRET, algorithm="HS256")
    rejects(minted)


def test_hs256_with_public_jwk_bytes_rejected(env):
    pem = PRIV.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    for key_bytes in (PUB_JWK_JSON.encode(), pem):
        rejects(hs256_raw({"alg": "HS256", "typ": "JWT", "kid": KID}, claims_now(), key_bytes))


def test_hs256_with_uuid_secret_rejected(env):
    rejects(jwt.encode(claims_now(), UUID_SECRET, algorithm="HS256", headers={"kid": KID}))


def test_wrong_or_missing_kid_rejected(env):
    rejects(jwt.encode(claims_now(), PRIV, algorithm="ES256", headers={"kid": "a171018d-0000-4000-8000-000000000000"}))
    rejects(jwt.encode(claims_now(), PRIV, algorithm="ES256"))


def test_alg_none_rejected(env):
    rejects(f"{b64({'alg': 'none', 'typ': 'JWT', 'kid': KID})}.{b64(claims_now())}.")


def test_forged_signature_rejected(env):
    other = ec.generate_private_key(ec.SECP256R1())
    rejects(jwt.encode(claims_now(), other, algorithm="ES256", headers={"kid": KID}))


def test_claims_checked(env):
    rejects(tok(aud="anon"))
    rejects(tok(iss="https://evil.supabase.co/auth/v1"))
    rejects(tok(role="service_role"))
    rejects(tok(exp=int(time.time()) - 120, iat=int(time.time()) - 4000))


def test_missing_or_malformed_jwk_fails_closed(env, monkeypatch):
    db, _ = env
    monkeypatch.delenv("SUPABASE_JWT_PUBLIC_JWK")
    rejects(tok())
    for bad in ("not json", json.dumps({**PUB_JWK, "d": "x"}), json.dumps({**PUB_JWK, "crv": "P-384"})):
        monkeypatch.setenv("SUPABASE_JWT_PUBLIC_JWK", bad)
        rejects(tok())
    assert db.connects == 0


def test_dead_session_or_user_rejected(env):
    db, _ = env
    db.sessions = {(SID, "99999999-2222-4333-8444-555555555555")}
    rejects(tok())
    db.sessions = {(SID, UID)}
    db.auth_users.clear()
    rejects(tok())


def test_pg_down_fails_closed(env):
    db, _ = env
    db.fail = True
    rejects(tok())


def test_member_missing_is_403(env, monkeypatch):
    async def none_lookup(*a, **k):
        return None
    monkeypatch.setattr(auth_mod, "lookup_vendor_member", none_lookup)
    rejects(tok(), code=403)


def test_cooldown_skips_get_user(env, monkeypatch):
    _, st = env
    clock = [1000.0]
    monkeypatch.setattr(fua.time, "monotonic", lambda: clock[0])
    assert call(tok()) == MEMBER_ID and st["auth_calls"] == 1
    clock[0] += 5
    assert call(tok()) == MEMBER_ID and st["auth_calls"] == 1
    clock[0] += fua.AUTH_402_COOLDOWN_S
    assert call(tok()) == MEMBER_ID and st["auth_calls"] == 2


def test_fallback_runs_in_threadpool(env, monkeypatch):
    import threading
    main = threading.get_ident()
    seen = []
    real = fua.verify_and_load_auth_user

    def spy(*a, **k):
        seen.append(threading.get_ident())
        return real(*a, **k)
    monkeypatch.setattr(fua, "verify_and_load_auth_user", spy)
    assert call(tok()) == MEMBER_ID
    assert seen and seen[0] != main


def test_fallback_never_reads_jwt_secret(env, monkeypatch):
    seen = []
    real_get = fua.os.environ.get
    monkeypatch.setattr(fua.os.environ, "get", lambda k, *a: (seen.append(k), real_get(k, *a))[1])
    assert fua.decode_supabase_jwt(tok()) is not None
    assert "SUPABASE_JWT_SECRET" not in seen
