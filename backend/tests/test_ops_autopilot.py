"""Ops autopilot: one-tap links, sweep decisions, payout retries, daily email gating."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services import ops_autopilot as ops

NOW = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)


class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op, self.payload = db, table, [], "select", None

    def select(self, *a, **k): return self
    def limit(self, *a): return self
    def order(self, *a, **k): return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val); return self

    def gte(self, col, val):
        self.filters.append(lambda r: str(r.get(col) or "") >= str(val)); return self

    def lte(self, col, val):
        self.filters.append(lambda r: str(r.get(col) or "") <= str(val)); return self

    def like(self, col, pat):
        p = pat.rstrip("%"); self.filters.append(lambda r: str(r.get(col) or "").startswith(p)); return self

    def insert(self, row):
        self.op, self.payload = "insert", row; return self

    def update(self, row):
        self.op, self.payload = "update", row; return self

    async def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "insert":
            row = {"created_at": NOW.isoformat(), **self.payload}
            rows.append(row); return _Res([row])
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in hit: r.update(self.payload)
        return _Res(hit)


class FakeDB:
    def __init__(self, **tables):
        self.tables = {k: list(v) for k, v in tables.items()}

    def table(self, name):
        return _Q(self, name)


def run(c):
    return asyncio.get_event_loop().run_until_complete(c) if False else asyncio.run(c)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OPS_ACTION_SECRET", "test-secret")
    monkeypatch.delenv("OPS_AUTOPILOT_EXTERNAL_SENDS", raising=False)
    monkeypatch.setattr(ops, "_iana", lambda job: "America/Toronto")


def test_sign_verify_roundtrip_and_tamper():
    t = ops.sign_action("reassign", {"job_id": "j1"}, now=1000)
    assert ops.verify_action(t, now=1001)["p"] == {"job_id": "j1"}
    assert ops.verify_action(t, now=1000 + ops.ACTION_TTL_S + 1) is None
    body, sig = t.split(".")
    assert ops.verify_action(body + "." + sig[:-2] + "AA", now=1001) is None
    assert ops.verify_action("garbage", now=1001) is None


def test_job_start_utc_prefers_slot_then_local_window():
    assert ops.job_start_utc({"selected_slot_start": "2026-11-03T14:00:00+00:00"}) == datetime(2026, 11, 3, 14, tzinfo=timezone.utc)
    s = ops.job_start_utc({"scheduled_date": "2026-11-03", "time_window_start": "10:00"})
    assert s == datetime(2026, 11, 3, 15, tzinfo=timezone.utc)  # 10:00 EST


def _job(**kw):
    base = {"id": "j1", "address": "1 Test St", "scheduled_date": "2026-11-02", "assignee_id": "m1",
            "updated_at": (NOW - timedelta(hours=1)).isoformat()}
    base.update(kw); return base


def _sweep(db, **kw):
    with patch.object(ops, "send_kam", wraps=ops.send_kam) as sk, \
         patch("app.services.notifications.create_notification", new=AsyncMock()) as cn, \
         patch("app.services.vendor_selection.assign_vendor_for_job", new=AsyncMock(return_value="m2")) as av:
        out = run(ops.sweep(db, NOW, run_reconcile=False, run_payouts=False))
    return out, cn, av


def test_declined_future_job_is_reassigned_once_excluding_decliner():
    db = FakeDB(jobs=[_job(status="declined", selected_slot_start=(NOW + timedelta(days=1)).isoformat())])
    out, cn, av = _sweep(db)
    assert len(out["reassigned"]) == 1 and out["reassigned"][0]["to"] == "m2"
    av.assert_awaited_with("j1", exclude_member_id="m1")
    db.tables["jobs"][0]["status"] = "declined"; db.tables["jobs"][0]["assignee_id"] = "m1"
    out2, _, av2 = _sweep(db)
    assert out2["reassigned"] == [] and not av2.await_count
    assert not cn.await_count


def test_noshow_flags_kam_once_with_reassign_link():
    db = FakeDB(jobs=[_job(status="assigned", selected_slot_start=(NOW - timedelta(minutes=45)).isoformat())])
    out, cn, _ = _sweep(db)
    assert out["noshow_flagged"] == ["j1"]
    html = cn.await_args.kwargs["email_html"]
    assert "/api/ops/act?t=" in html and "Reassign" in html
    out2, cn2, _ = _sweep(db)
    assert out2["noshow_flagged"] == [] and not cn2.await_count


def test_late_upload_escalates_and_vendor_reminder_needs_flag():
    shot = (NOW - timedelta(hours=8)).isoformat()
    db = FakeDB(jobs=[_job(status="in_progress", selected_slot_start=shot, uploaded_count=0, deliverables_count=10)])
    with patch.object(ops, "send_external", new=AsyncMock(return_value=True)) as se:
        out, cn, _ = _sweep(db)
    assert out["upload_reminded"] == [] and not se.await_count and not cn.await_count
    db.tables["jobs"][0]["selected_slot_start"] = (NOW - timedelta(hours=25)).isoformat()
    out, cn, _ = _sweep(db)
    assert out["upload_escalated"] == ["j1"] and cn.await_count == 1


def test_qc_failed_escalates_with_retry_processing():
    db = FakeDB(jobs=[_job(status="qc_failed", selected_slot_start=(NOW - timedelta(hours=30)).isoformat())])
    out, cn, _ = _sweep(db)
    assert out["stuck_escalated"] == ["j1"]
    assert "Retry editing" in cn.await_args.kwargs["email_html"]


def test_payout_retry_spacing_and_escalation():
    job = {"id": "p1", "status": "delivered", "payout_status": "failed", "updated_at": (NOW - timedelta(hours=3)).isoformat()}
    db = FakeDB(jobs=[job], payouts=[{"job_id": "p1", "status": "failed"}])
    tp = AsyncMock(return_value={"status": "failed"})
    with patch("app.services.payout_service.trigger_payout_for_job", new=tp), \
         patch("app.services.notifications.create_notification", new=AsyncMock()) as cn:
        out = {"payout_retried": [], "payout_escalated": []}
        run(ops._payout_retries(db, NOW, out))
        assert len(out["payout_retried"]) == 1
        run(ops._payout_retries(db, NOW, out))  # too soon: spacing
        assert tp.await_count == 1
        for n in (2, 3):
            db.tables["ops_autopilot_events"].append({"key": f"payout_retry:p1:{n}", "created_at": (NOW - timedelta(hours=7)).isoformat()})
        for e in db.tables["ops_autopilot_events"]:
            e["created_at"] = (NOW - timedelta(hours=7)).isoformat()
        run(ops._payout_retries(db, NOW, out))
        assert out["payout_escalated"] == ["p1"] and cn.await_count == 1 and tp.await_count == 1


def test_payout_retry_skips_paid_and_fresh():
    db = FakeDB(jobs=[{"id": "a", "status": "delivered", "payout_status": "paid", "updated_at": (NOW - timedelta(hours=5)).isoformat()},
                      {"id": "b", "status": "delivered", "updated_at": (NOW - timedelta(minutes=30)).isoformat()}])
    tp = AsyncMock()
    with patch("app.services.payout_service.trigger_payout_for_job", new=tp):
        run(ops._payout_retries(db, NOW, {"payout_retried": [], "payout_escalated": []}))
    assert not tp.await_count


def test_daily_quiet_when_healthy_and_emails_when_broken():
    db = FakeDB(ops_autopilot_runs=[{"kind": "sweep", "ok": True, "created_at": (NOW - timedelta(minutes=10)).isoformat()}])
    with patch.object(ops, "check_urls", new=AsyncMock(return_value=[])), \
         patch.object(ops, "pg_cron_failures", return_value=[]), \
         patch("app.services.notifications.create_notification", new=AsyncMock()) as cn:
        out = run(ops.daily(db, NOW, do_backup=False))
        assert out["problems"] == [] and out["emailed"] is False and not cn.await_count
    with patch.object(ops, "check_urls", new=AsyncMock(return_value=["https://x returned 500"])), \
         patch.object(ops, "pg_cron_failures", return_value=[]), \
         patch("app.services.notifications.create_notification", new=AsyncMock()) as cn:
        out = run(ops.daily(db, NOW + timedelta(days=1), do_backup=False))
        assert out["emailed"] is True and cn.await_args.kwargs["email_to"] == ops.kam_email()


def test_daily_flags_stale_sweep():
    db = FakeDB(ops_autopilot_runs=[{"kind": "sweep", "ok": True, "created_at": (NOW - timedelta(hours=3)).isoformat()}])
    with patch.object(ops, "check_urls", new=AsyncMock(return_value=[])), patch.object(ops, "pg_cron_failures", return_value=[]):
        probs = run(ops.health(db, NOW))
    assert any("has not run" in p for p in probs)


def test_run_action_unknown():
    assert run(ops.run_action(FakeDB(), {"a": "nope", "p": {}}))["ok"] is False


def test_probation_held_payout_is_not_retried_or_escalated():
    job = {"id": "p2", "status": "delivered", "payout_status": None, "updated_at": (NOW - timedelta(hours=30)).isoformat()}
    db = FakeDB(jobs=[job], payouts=[{"job_id": "p2", "status": "held"}])
    tp = AsyncMock(return_value={"status": "held"})
    with patch("app.services.payout_service.trigger_payout_for_job", new=tp):
        out = {"payout_retried": [], "payout_escalated": []}
        run(ops._payout_retries(db, NOW, out))
    assert tp.await_count == 0 and out == {"payout_retried": [], "payout_escalated": []}
