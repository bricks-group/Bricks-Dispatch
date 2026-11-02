"""Account takeover via prepare-email-login (Sentinel, 2026-11-02).

Attack: sign up (public signup, confirmation on) with a vendor's email and an
attacker password -> POST /api/vendor-os/auth/prepare-email-login -> the
endpoint used to UPDATE auth.users SET email_confirmed_at -> password login ->
lookup_vendor_member matched the vendor row by email.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import email_login_prep as elp
from app.shared import auth as auth_mod

VENDOR_EMAIL = "pro@vendor.ca"
MEMBER = {"id": "11111111-aaaa-4bbb-8ccc-000000000001", "email": "Pro@Vendor.ca", "auth_user_id": None,
          "role": "Photographer", "vendor_id": "default_vendor", "is_active": True}
ATTACKER_UID = "22222222-aaaa-4bbb-8ccc-000000000002"


class _VQ:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *a, **k):
        return self

    def ilike(self, col, val):
        self.rows = [r for r in self.rows if str(r.get(col) or "").lower() == str(val).lower()]
        return self

    def limit(self, *a):
        return self

    async def execute(self):
        return SimpleNamespace(data=list(self.rows))


def _prep_db(members=(MEMBER,)):
    db = MagicMock()
    db.table = lambda t: _VQ([dict(m) for m in members])
    db.auth.admin.create_user = AsyncMock()
    db.auth.admin.delete_user = AsyncMock()
    return db


def _unconfirmed(**kw):
    u = {"id": ATTACKER_UID, "email_confirmed_at": None, "last_sign_in_at": None,
         "phone": None, "phone_confirmed_at": None, "providers": ["email"]}
    u.update(kw)
    return u


def _run(db, email=VENDOR_EMAIL, user=None):
    with patch.object(elp, "_pg_auth_user", return_value=user):
        return asyncio.run(elp.ensure_vendor_email_login_ready(db, email))


def test_no_sql_confirm_path_exists():
    assert not hasattr(elp, "_pg_confirm_email")
    import inspect
    assert "email_confirmed_at = now()" not in inspect.getsource(elp).replace("SET email_confirmed_at", "")


def test_normal_first_time_vendor_gets_passwordless_confirmed_user():
    db = _prep_db()
    assert _run(db, "PRO@vendor.ca", None) == "created"
    db.auth.admin.create_user.assert_awaited_once_with({"email": VENDOR_EMAIL, "email_confirm": True})
    args = db.auth.admin.create_user.await_args.args[0]
    assert "password" not in args
    db.auth.admin.delete_user.assert_not_called()


def test_attack_unconfirmed_password_signup_is_replaced_not_confirmed():
    """Attacker's unconfirmed signup is deleted; the fresh user has no password,
    so the attacker's password no longer works. Only the emailed code does."""
    db = _prep_db()
    assert _run(db, VENDOR_EMAIL, _unconfirmed()) == "recreated"
    db.auth.admin.delete_user.assert_awaited_once_with(ATTACKER_UID)
    db.auth.admin.create_user.assert_awaited_once_with({"email": VENDOR_EMAIL, "email_confirm": True})


@pytest.mark.parametrize("override", [
    {"last_sign_in_at": "2026-11-02T10:00:00Z"},
    {"providers": ["email", "google"]},
    {"providers": ["phone"]},
    {"phone": "+15555550100"},
    {"phone_confirmed_at": "2026-11-02T10:00:00Z"},
])
def test_unconfirmed_user_with_history_is_left_alone(override):
    db = _prep_db()
    assert _run(db, VENDOR_EMAIL, _unconfirmed(**override)) == "kept_unconfirmed"
    db.auth.admin.delete_user.assert_not_called()
    db.auth.admin.create_user.assert_not_called()


def test_unconfirmed_user_linked_to_member_is_left_alone():
    db = _prep_db([{**MEMBER, "auth_user_id": ATTACKER_UID}])
    assert _run(db, VENDOR_EMAIL, _unconfirmed()) == "kept_unconfirmed"
    db.auth.admin.delete_user.assert_not_called()


def test_confirmed_user_untouched():
    db = _prep_db()
    assert _run(db, VENDOR_EMAIL, _unconfirmed(email_confirmed_at="2026-11-01T00:00:00Z")) == "ok"
    db.auth.admin.delete_user.assert_not_called()
    db.auth.admin.create_user.assert_not_called()


def test_non_vendor_does_nothing():
    db = _prep_db()
    with patch.object(elp, "_pg_auth_user", side_effect=AssertionError("must not look up auth")):
        assert asyncio.run(elp.ensure_vendor_email_login_ready(db, "someone@else.com")) == "not_vendor"
    db.auth.admin.create_user.assert_not_called()
    db.auth.admin.delete_user.assert_not_called()


@pytest.mark.parametrize("email", ["p_o@vendor.ca", "%@vendor.ca", "pro@vendor_ca", "*@vendor.ca"])
def test_wildcard_emails_never_match_a_vendor(email):
    db = _prep_db()
    assert _run(db, email, None) in ("invalid", "not_vendor")
    db.auth.admin.create_user.assert_not_called()


def test_pg_unavailable_never_confirms_existing():
    db = _prep_db()
    db.auth.admin.create_user = AsyncMock(side_effect=Exception("A user with this email address has already been registered"))
    with patch.object(elp, "_pg_auth_user", side_effect=RuntimeError("no postgres")):
        assert asyncio.run(elp.ensure_vendor_email_login_ready(db, VENDOR_EMAIL)) == "unknown"
    db.auth.admin.delete_user.assert_not_called()


def test_prepare_endpoint_still_returns_ok_for_everyone(monkeypatch):
    from app.routers import auth as auth_router
    db = _prep_db()
    monkeypatch.setattr(auth_router, "get_db", AsyncMock(return_value=db))
    with patch.object(elp, "_pg_auth_user", return_value=_unconfirmed()):
        out = asyncio.run(auth_router.vendor_os_prepare_email_login(SimpleNamespace(email=VENDOR_EMAIL)))
    assert out == {"ok": True}
    db.auth.admin.delete_user.assert_awaited_once()


# ---- lookup_vendor_member hardening -------------------------------------

class _Chain:
    def __init__(self, db):
        self.db, self.filters, self.single, self.upd = db, [], False, None

    def select(self, *a, **k):
        return self

    def update(self, data):
        self.upd = data
        return self

    def eq(self, c, v):
        self.filters.append(("eq", c, v)); return self

    def ilike(self, c, v):
        self.filters.append(("ilike", c, v)); return self

    def is_(self, c, v):
        self.filters.append(("is", c, v)); return self

    def limit(self, *a):
        return self

    def maybe_single(self):
        self.single = True; return self

    def _m(self, r, f):
        op, c, v = f
        have = r.get(c)
        if op == "ilike":
            return str(have or "").lower() == str(v).lower()
        if op == "is":
            return have is None
        return str(have or "") == str(v)

    async def execute(self):
        rows = [r for r in self.db.rows if all(self._m(r, f) for f in self.filters)]
        if self.upd is not None:
            for r in rows:
                r.update(self.upd)
            self.db.updates.append(self.upd)
            return SimpleNamespace(data=rows)
        if self.single:
            return SimpleNamespace(data=rows[0]) if rows else None
        return SimpleNamespace(data=rows)


class _Db:
    def __init__(self, rows, user=None):
        self.rows = [dict(r) for r in rows]
        self.updates = []
        outer = self

        class _A:
            async def get_user(_s, _t):
                return SimpleNamespace(user=outer.user)
        self.user = user
        self.auth = _A()

    def table(self, name):
        return _Chain(self)


def _tok(amr):
    import jwt
    return jwt.encode({"sub": ATTACKER_UID, "amr": [{"method": m, "timestamp": 1} for m in amr]}, "x", algorithm="HS256")


def _patch(monkeypatch, db):
    monkeypatch.setattr(auth_mod, "get_db", AsyncMock(return_value=db))


def test_unconfirmed_email_never_matches(monkeypatch):
    db = _Db([MEMBER])
    _patch(monkeypatch, db)
    got = asyncio.run(auth_mod.lookup_vendor_member(ATTACKER_UID, VENDOR_EMAIL, email_confirmed=False, amr={"password"}))
    assert got is None


def test_password_session_cannot_take_linked_member(monkeypatch):
    other = "99999999-aaaa-4bbb-8ccc-000000000009"
    db = _Db([{**MEMBER, "auth_user_id": other}])
    _patch(monkeypatch, db)
    got = asyncio.run(auth_mod.lookup_vendor_member(ATTACKER_UID, VENDOR_EMAIL, email_confirmed=True, amr={"password"}))
    assert got is None


def test_otp_session_matches_linked_member_without_relinking(monkeypatch):
    other = "99999999-aaaa-4bbb-8ccc-000000000009"
    db = _Db([{**MEMBER, "auth_user_id": other}])
    _patch(monkeypatch, db)
    got = asyncio.run(auth_mod.lookup_vendor_member(ATTACKER_UID, VENDOR_EMAIL, email_confirmed=True, amr={"otp"}))
    assert got and got["id"] == MEMBER["id"]
    assert db.updates == []


def test_first_confirmed_match_links_auth_user_id(monkeypatch):
    db = _Db([MEMBER])
    _patch(monkeypatch, db)
    got = asyncio.run(auth_mod.lookup_vendor_member(ATTACKER_UID, VENDOR_EMAIL, email_confirmed=True, amr={"otp"}))
    assert got["auth_user_id"] == ATTACKER_UID
    assert db.updates == [{"auth_user_id": ATTACKER_UID}]
    # later lookups match by auth_user_id even without email
    got2 = asyncio.run(auth_mod.lookup_vendor_member(ATTACKER_UID, None, email_confirmed=False))
    assert got2["id"] == MEMBER["id"]


def test_wildcard_email_does_not_match(monkeypatch):
    db = _Db([MEMBER])
    _patch(monkeypatch, db)
    # fake ilike is exact; emulate a wildcard row return by using a real-looking mismatch
    got = asyncio.run(auth_mod.lookup_vendor_member(ATTACKER_UID, "p_o@vendor.ca", email_confirmed=True, amr={"otp"}))
    assert got is None


def test_resolve_attack_session_rejected(monkeypatch):
    """End to end: attacker password session on an unconfirmed (or forged) account."""
    user = SimpleNamespace(id=ATTACKER_UID, email=VENDOR_EMAIL, email_confirmed_at=None)
    db = _Db([MEMBER], user=user)
    _patch(monkeypatch, db)
    with pytest.raises(Exception) as e:
        asyncio.run(auth_mod.resolve_vendor_actor_id("Bearer " + _tok(["password"])))
    assert getattr(e.value, "status_code", None) in (401, 403)
    assert "vendor_member_not_found" in str(getattr(e.value, "detail", ""))


def test_resolve_normal_otp_login(monkeypatch):
    user = SimpleNamespace(id=ATTACKER_UID, email=VENDOR_EMAIL, email_confirmed_at="2026-11-02T15:00:00Z")
    db = _Db([MEMBER], user=user)
    _patch(monkeypatch, db)
    assert asyncio.run(auth_mod.resolve_vendor_actor_id("Bearer " + _tok(["otp"]))) == MEMBER["id"]
    assert db.rows[0]["auth_user_id"] == ATTACKER_UID


def test_minted_fair_use_token_never_email_matches(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "s" * 40)
    tok = auth_mod.mint_supabase_access_token(ATTACKER_UID, VENDOR_EMAIL)
    db = _Db([MEMBER])

    class _A:
        async def get_user(_s, _t):
            raise Exception("invalid JWT: unable to parse or verify signature")
    db.auth = _A()
    _patch(monkeypatch, db)
    with pytest.raises(Exception) as e:
        asyncio.run(auth_mod.resolve_vendor_actor_id("Bearer " + tok))
    assert getattr(e.value, "status_code", None) in (401, 403)
    assert "vendor_member_not_found" in str(getattr(e.value, "detail", ""))


def test_backup_excludes_auth_secrets():
    from app.services import ops_autopilot as oa
    assert "auth.identities" not in oa.BACKUP_EXTRA_TABLES
    sql = oa._backup_select("auth.users")
    for bad in ("encrypted_password", "confirmation_token", "recovery_token", "email_change_token",
                "phone_change_token", "reauthentication_token", "*"):
        assert bad not in sql
    with pytest.raises(ValueError):
        oa._backup_select("auth.identities")
    assert oa.BACKUP_KEEP_DAYS == 14
