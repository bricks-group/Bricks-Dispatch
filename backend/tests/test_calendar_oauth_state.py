"""Calendar OAuth state is signed, expiring, and one-use. A bare member id is not accepted."""
from __future__ import annotations

import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from app.services.vendor_calendars import (
    OAuthStateError,
    build_microsoft_authorize_url,
    consume_oauth_state,
    mint_oauth_state,
    read_oauth_state,
)


class _Query:
    def __init__(self, store, op, row=None):
        self.store = store
        self.op = op
        self.row = row
        self.filters = {}

    def select(self, *_a, **_k):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, _n):
        return self

    def insert(self, row):
        self.row = row
        self.op = "insert"
        return self

    async def execute(self):
        if self.op == "insert":
            self.store.append(self.row)
            return SimpleNamespace(data=[self.row])
        nonce = self.filters.get("entity_id")
        found = [r for r in self.store if r.get("entity_id") == nonce]
        return SimpleNamespace(data=found[:1])


class _Db:
    def __init__(self):
        self.rows = []

    def table(self, name):
        assert name == "audit_events"
        return _Query(self.rows, "select")


def test_bare_member_id_is_rejected(monkeypatch):
    monkeypatch.setenv("CALENDAR_OAUTH_STATE_SECRET", "test-state-secret")
    with pytest.raises(OAuthStateError):
        read_oauth_state("f83dbb0f-d3d8-42fa-bdcf-fe301328dce6", "google")


def test_state_is_bound_to_member_and_provider(monkeypatch):
    monkeypatch.setenv("CALENDAR_OAUTH_STATE_SECRET", "test-state-secret")
    state = mint_oauth_state("member-a", "google", now=1_700_000_000)
    assert read_oauth_state(state, "google", now=1_700_000_000)["m"] == "member-a"
    with pytest.raises(OAuthStateError):
        read_oauth_state(state, "microsoft", now=1_700_000_000)
    tampered = state[:-2] + ("A" if state[-1] != "A" else "B")
    with pytest.raises(OAuthStateError):
        read_oauth_state(tampered, "google", now=1_700_000_000)


def test_state_expires(monkeypatch):
    monkeypatch.setenv("CALENDAR_OAUTH_STATE_SECRET", "test-state-secret")
    state = mint_oauth_state("member-a", "google", now=1_000)
    with pytest.raises(OAuthStateError, match="expired"):
        read_oauth_state(state, "google", now=1_000 + 11 * 60)


@pytest.mark.asyncio
async def test_state_is_one_use(monkeypatch):
    monkeypatch.setenv("CALENDAR_OAUTH_STATE_SECRET", "test-state-secret")
    state = mint_oauth_state("member-a", "microsoft", now=1_700_000_000)
    db = _Db()
    assert await consume_oauth_state(state, "microsoft", db, now=1_700_000_000) == "member-a"
    with pytest.raises(OAuthStateError, match="already used"):
        await consume_oauth_state(state, "microsoft", db, now=1_700_000_100)
    from app.services import vendor_calendars as vc
    vc._CONSUMED_OAUTH_STATES.clear()
    with pytest.raises(OAuthStateError, match="already used"):
        await consume_oauth_state(state, "microsoft", db, now=1_700_000_100)


def test_microsoft_authorize_url_does_not_use_bare_member_id(monkeypatch):
    monkeypatch.setenv("CALENDAR_OAUTH_STATE_SECRET", "test-state-secret")
    monkeypatch.setenv("AZURE_AD_CLIENT_ID", "ms-cid")
    monkeypatch.setenv("MICROSOFT_OAUTH_REDIRECT_URI", "https://dispatch-api.brxs.ca/api/vendor-os/calendars/microsoft/callback")
    url = build_microsoft_authorize_url("member-9")
    state = parse_qs(urlparse(url).query)["state"][0]
    assert state != "member-9"
    assert read_oauth_state(state, "microsoft")["m"] == "member-9"
