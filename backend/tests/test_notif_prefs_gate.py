"""Agent emails honour Bricks OS Settings → Notifications (fail open)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services import client_comms as cc
from app.shared import notif_prefs_gate as gate


def _conn(rows, sink=None, boom=None):
    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            if boom:
                raise boom
            if sink is not None:
                sink.append((sql, params))
            self._r = rows

        def fetchone(self):
            return self._r[0] if self._r else None

    return lambda: SimpleNamespace(cursor=lambda: Cur(), close=lambda: None)


def test_gate_reads_field_by_email():
    sink = []
    assert gate.agent_allows("A@B.ca", "delivery_ready_email", connect=_conn([(False,)], sink)) is False
    assert "delivery_ready_email" in sink[0][0] and "lower(email) = lower(%s)" in sink[0][0]
    assert sink[0][1] == ("A@B.ca",)
    assert gate.agent_allows("a@b.ca", "new_booking_email", connect=_conn([(True,)])) is True


def test_gate_fails_open():
    assert gate.agent_allows("a@b.ca", "new_booking_email", connect=_conn([])) is True  # no row
    assert gate.agent_allows("a@b.ca", "new_booking_email", connect=_conn([(None,)])) is True
    assert gate.agent_allows("a@b.ca", "new_booking_email", connect=lambda: None) is True  # no DB
    assert gate.agent_allows("a@b.ca", "new_booking_email",
                             connect=_conn([], boom=RuntimeError('relation "notification_prefs" does not exist'))) is True
    assert gate.agent_allows("", "new_booking_email", connect=_conn([(False,)])) is True


def test_gate_rejects_unknown_field():
    with pytest.raises(ValueError):
        gate.agent_allows("a@b.ca", "email; DROP TABLE users", connect=_conn([]))


def _setup(monkeypatch, allows):
    job = {"id": "j1", "booking_id": "", "address": "1 Main St", "agent_email": "agent@example.com",
           "agent_name": "Alex", "assignee_id": "p1", "package_name": "Essentials", "scheduled_date": "2026-10-20"}
    sent, asked = [], []

    async def load_job(_jid, _cols):
        return dict(job)

    async def photog(*_a, **_k):
        return "Pat"

    async def email(to, subject, html):
        sent.append((to, subject))
        return True

    monkeypatch.setattr(cc, "_load_job", load_job)
    monkeypatch.setattr(cc, "_resolve_photographer_name", photog)
    monkeypatch.setattr(cc, "_email_agent_direct", email)
    monkeypatch.setattr(cc, "_agent_allows", lambda e, f: asked.append((e, f)) or allows.get(f, True))
    from app.shared import web_push
    pushed = []

    async def fake_push(connect, email, msg, **_k):
        pushed.append((email, msg))
        return {"sent": 1, "devices": 1}

    monkeypatch.setattr(web_push, "push_to_email", fake_push)
    _setup.pushed = pushed
    return sent, asked


def test_booking_confirmed_muted(monkeypatch):
    sent, asked = _setup(monkeypatch, {"new_booking_email": False})
    asyncio.run(cc.notify_booking_confirmed("j1"))
    assert sent == [] and asked == [("agent@example.com", "new_booking_push"), ("agent@example.com", "new_booking_email")]
    assert len(_setup.pushed) == 1  # push has its own toggle; muting email doesn't mute push


def test_booking_confirmed_default_sends(monkeypatch):
    sent, _ = _setup(monkeypatch, {})
    asyncio.run(cc.notify_booking_confirmed("j1"))
    assert sent and sent[0][1].startswith("Shoot confirmed")


def test_delivery_muted_and_default(monkeypatch):
    sent, asked = _setup(monkeypatch, {"delivery_ready_email": False})
    assert asyncio.run(cc.notify_capture_delivered("j1", products=["3D tour"])) is False
    assert sent == [] and asked == [("agent@example.com", "delivery_ready_email")]
    sent, _ = _setup(monkeypatch, {})
    assert asyncio.run(cc.notify_capture_delivered("j1", products=["3D tour"])) is True
    assert sent[0][1].startswith("Your 3D tour is ready")


def test_booking_confirmed_pushes_with_desk_link(monkeypatch):
    sent, _ = _setup(monkeypatch, {})
    asyncio.run(cc.notify_booking_confirmed("j1"))
    (email, msg), = _setup.pushed
    assert email == "agent@example.com"
    assert msg["title"] == "Shoot confirmed · 1 Main St" and "Pat" in msg["body"]
    assert msg["url"].endswith("/properties/j1") and msg["tag"] == "booking-j1"
    assert sent  # email still goes


def test_booking_push_muted_keeps_email(monkeypatch):
    sent, _ = _setup(monkeypatch, {"new_booking_push": False})
    asyncio.run(cc.notify_booking_confirmed("j1"))
    assert _setup.pushed == [] and sent


def test_push_failure_never_blocks_email(monkeypatch):
    sent, _ = _setup(monkeypatch, {})
    from app.shared import web_push

    async def boom(*a, **k):
        raise RuntimeError("push service down")

    monkeypatch.setattr(web_push, "push_to_email", boom)
    asyncio.run(cc.notify_booking_confirmed("j1"))
    assert sent and sent[0][1].startswith("Shoot confirmed")
