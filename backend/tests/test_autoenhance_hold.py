"""Kam 2026-11-07: Autoenhance can't edit -> hold stills, alert once, re-run later."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import autoenhance_service as ae
from app.services import post_production as pp


def _run(c):
    return asyncio.run(c)


@pytest.fixture(autouse=True)
def _reset():
    ae.clear_plan_block()
    yield
    ae.clear_plan_block()


def test_plan_block_expires_without_restart(monkeypatch):
    monkeypatch.setattr(ae, "api_key", lambda: "k")
    t = [1000.0]
    monkeypatch.setattr(ae.time, "monotonic", lambda: t[0])
    monkeypatch.setenv("AUTOENHANCE_PLAN_BLOCK_TTL_SEC", "300")
    ae._mark_plan_blocked("No Plan")
    assert ae.is_available() is False and "No Plan" in ae.unavailable_reason()
    t[0] += 301
    assert ae.is_available() is True and ae.unavailable_reason() == ""


def test_kill_switch_and_missing_key_reasons(monkeypatch):
    monkeypatch.setattr(ae, "api_key", lambda: "")
    assert ae.unavailable_reason() == "AUTOENHANCE_API_KEY not set"
    monkeypatch.setenv("PAID_STILLS_PROCESSING_DISABLED", "1")
    assert "PAID_STILLS_PROCESSING_DISABLED" in ae.unavailable_reason()


def test_inhouse_only_when_explicit(monkeypatch):
    monkeypatch.delenv("POSTPROD_STILLS_PROVIDER", raising=False)
    monkeypatch.setenv("PAID_STILLS_PROCESSING_DISABLED", "1")
    assert pp.stills_provider_preference() == "autoenhance"  # kill switch holds, never in-house
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "inhouse")
    assert pp.stills_provider_preference() == "inhouse"


def test_hold_reason_budget_vs_unavailable(monkeypatch):
    assert pp._ae_hold_reason(None, ae) == pp.AE_BUDGET_REASON
    monkeypatch.setattr(ae, "api_key", lambda: "")
    assert pp._ae_hold_reason(60.0, ae) == "AUTOENHANCE_API_KEY not set"


def test_hold_writes_fields_and_alerts_except_budget(monkeypatch):
    ups, alerts = [], []

    async def safe(_db, uid, f):
        ups.append((uid, f))

    async def alert(job_id, reason):
        alerts.append((job_id, reason))
    monkeypatch.setattr(pp, "_safe_update_upload", safe)
    monkeypatch.setattr(pp, "alert_autoenhance_hold", alert)
    _run(pp.hold_stills_for_autoenhance(None, "J", [{"id": "u1"}, {"id": "u2"}], "HTTP 402"))
    assert [u for u, _ in ups] == ["u1", "u2"]
    f = ups[0][1]
    assert f["processing_status"] == "held_autoenhance" and f["provider"] == "autoenhance_held"
    assert "HTTP 402" in f["qc_notes"] and "processed_url" not in f
    assert alerts == [("J", "HTTP 402")]
    _run(pp.hold_stills_for_autoenhance(None, "J2", [{"id": "u3"}], pp.AE_BUDGET_REASON))
    assert len(alerts) == 1  # budget holds re-run quietly on the next sweep


def test_alert_sent_once_per_job(monkeypatch):
    from app.services import editor_review as er, notifications
    events, sent = set(), []

    async def claim(eid, *args):
        if eid in events:
            return False
        events.add(eid)
        return True

    async def notify(**kwargs):
        sent.append(kwargs)
        return True
    monkeypatch.setattr(er, "_claim_alert", claim)
    monkeypatch.setattr(notifications, "send_email_via_resend", notify)
    assert _run(pp.alert_autoenhance_hold("J9", "No Plan")) is True
    assert _run(pp.alert_autoenhance_hold("J9", "No Plan")) is False
    assert len(sent) == 1 and "J9" in sent[0]["subject"]
    assert sent[0]["to"] == "raph@brxs.ca"


def test_held_stills_block_finalize_and_are_not_already_processed():
    src = (Path(pp.__file__).resolve().parents[1] / "routers" / "post_production.py").read_text()
    assert '("processing", "pending", "held_autoenhance")' in src
    assert pp.is_already_processed({"processing_status": "held_autoenhance"}) is False


class _Q:
    def __init__(self, db, t):
        self.db, self.t, self.f, self.p, self.ins = db, t, {}, None, None

    def select(self, *_a):
        return self

    def eq(self, c, v):
        self.f[c] = v
        return self

    def gte(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def in_(self, c, vals):
        self.ins = (c, list(vals))
        return self

    def update(self, p):
        self.p = p
        return self

    async def execute(self):
        rows = [r for r in self.db.rows[self.t] if all(r.get(k) == v for k, v in self.f.items())
                and (self.ins is None or r.get(self.ins[0]) in self.ins[1])]
        if self.p is not None:
            for r in rows:
                r.update(self.p)
        return SimpleNamespace(data=rows)


class DB:
    def __init__(self, **rows):
        self.rows = rows

    def table(self, t):
        return _Q(self, t)


def test_retry_resets_holds_and_reruns(monkeypatch):
    from app.routers import post_production as rp
    db = DB(uploads=[{"id": "u1", "job_id": "J", "processing_status": "held_autoenhance"},
                     {"id": "u2", "job_id": "J", "processing_status": "done"}],
            jobs=[{"id": "J", "status": "processing"}])
    ran = []

    async def proc(jid):
        ran.append(jid)
    monkeypatch.setattr(pp, "process_job_assets", proc)
    monkeypatch.setattr(ae, "is_available", lambda: True)
    out = _run(rp.retry_autoenhance_holds(db))
    assert out == ["J"] and ran == ["J"]
    assert db.rows["uploads"][0]["processing_status"] == "pending"
    assert db.rows["uploads"][1]["processing_status"] == "done"
    assert db.rows["jobs"][0]["status"] == "completed"


def test_retry_does_nothing_while_ae_unavailable(monkeypatch):
    from app.routers import post_production as rp
    db = DB(uploads=[{"id": "u1", "job_id": "J", "processing_status": "held_autoenhance"}], jobs=[])
    monkeypatch.setattr(ae, "is_available", lambda: False)
    assert _run(rp.retry_autoenhance_holds(db)) == []
    assert db.rows["uploads"][0]["processing_status"] == "held_autoenhance"
