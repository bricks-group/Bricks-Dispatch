"""Shared Web Push core: browser-interoperable encryption, VAPID, host allowlist,
and dead devices are dropped."""
from __future__ import annotations

import asyncio
import json
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.shared import web_push as wp


def _browser():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return priv, wp.b64u(pub), wp.b64u(os.urandom(16))


def test_round_trip():
    priv, p, a = _browser()
    assert wp.decrypt(wp.encrypt(b"hi", p, a), priv, a) == b"hi"


def test_allowlist():
    assert wp.endpoint_allowed("https://web.push.apple.com/x")
    assert not wp.endpoint_allowed("https://push.apple.com.evil.io/x")
    assert not wp.endpoint_allowed("http://fcm.googleapis.com/x")


def test_push_to_email_sends_and_reports_gone_without_vapid_is_noop():
    if not os.environ.get("VAPID_PUBLIC_KEY"):
        assert asyncio.run(wp.push_to_email(lambda: None, "a@b.ca", {"title": "x"}))["skipped"] == "vapid_not_configured"
    keys = wp.generate_vapid_keys()
    cfg = {**keys, "subject": wp.DEFAULT_SUBJECT}
    priv, p, a = _browser()
    subs = [{"endpoint": "https://fcm.googleapis.com/fcm/send/a", "p256dh": p, "auth": a},
            {"endpoint": "https://fcm.googleapis.com/fcm/send/old", "p256dh": p, "auth": a},
            {"endpoint": "https://evil.example/x", "p256dh": p, "auth": a}]
    calls = []

    class C:
        async def post(self, url, content=None, headers=None):
            calls.append((url, content))
            return type("R", (), {"status_code": 410 if url.endswith("old") else 201})()

    out = asyncio.run(wp.push_to_email(lambda: None, "a@b.ca", wp.notification("T", "B", "/x"), cfg=cfg, client=C(), subs=subs))
    assert out == {"sent": 1, "gone": 1, "devices": 3}
    assert [c[0] for c in calls] == [subs[0]["endpoint"], subs[1]["endpoint"]]
    assert json.loads(wp.decrypt(calls[0][1], priv, a))["title"] == "T"


def test_lookup_without_table_returns_no_devices():
    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a):
            raise RuntimeError('relation "push_subscriptions" does not exist')

    conn = type("Conn", (), {"cursor": lambda self: Cur(), "close": lambda self: None})()
    assert wp.subscriptions_for_email(lambda: conn, "a@b.ca") == []
