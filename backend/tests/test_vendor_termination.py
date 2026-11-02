"""Vendor termination policy (Kam, 2026-11-02): a no-show or a cancel after accepting
terminates the vendor. Emails, reassignment and once-markers are mocked."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services import ops_autopilot as ops
from app.services import vendor_termination as vt
from app.services.vendor_profile import member_eligible_for_job


class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op, self.payload = db, table, [], "select", None

    def select(self, *_a, **_k):
        return self

    def eq(self, k, v):
        self.filters.append((k, lambda x, v=v: str(x) == str(v)))
        return self

    def in_(self, k, vs):
        self.filters.append((k, lambda x, vs=vs: x in vs))
        return self

    def is_(self, k, v):
        self.filters.append((k, lambda x: x is None))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def update(self, patch):
        self.op, self.payload = "update", patch
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    async def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "insert":
            row = {"id": f"t{len(rows)}", "undone_at": None, **self.payload}
            rows.append(row)
            return _Res([row])
        hit = [r for r in rows if all(f(r.get(k)) for k, f in self.filters)]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
        return _Res([dict(r) for r in hit])


class FakeDB:
    def __init__(self, **tables):
        self.tables = tables

    def table(self, name):
        return _Q(self, name)


NOW = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)  # 10:00 ET
VENDOR = {"id": "m1", "name": "Test Cleaner", "email": "test+v@example.com", "phone": "+1 (416) 555-0100",
          "is_active": True, "onboarding_status": "approved", "vendor_type": "cleaner"}


def _job(**kw):
    j = {"id": "j1", "assignee_id": "m1", "status": "assigned", "scheduled_date": "2026-11-03",
         "selected_slot_start": "2026-11-03T14:00:00+00:00", "address": "1 Test St", "agent_email": "client@example.com"}
    j.update(kw)
    return j


def _db(jobs=None, docs=None):
    return FakeDB(vendor_members=[dict(VENDOR)], jobs=jobs or [_job()], vendor_terminations=[],
                  vendor_documents=docs or [])


@pytest.fixture(autouse=True)
def _mocks(monkeypatch):
    monkeypatch.setenv("OPS_ACTION_SECRET", "test-secret")
    monkeypatch.delenv("NOSHOW_TERMINATE_GRACE_MIN", raising=False)
    seen = set()

    async def once(_db, key, _data=None):
        if key in seen:
            return False
        seen.add(key)
        return True

    sent = []

    async def send(to, subject, title, body):
        sent.append((to, subject, body))
        return True

    reassign = AsyncMock(side_effect=lambda db, jid, reason: {"ok": True, "job_id": jid, "to": "m2"})
    kam = AsyncMock(return_value=True)
    with patch.object(ops, "once", once), patch.object(ops, "reassign_job", reassign), \
            patch.object(ops, "send_kam", kam), patch.object(vt, "_send", send):
        yield {"sent": sent, "reassign": reassign, "kam": kam}


def test_no_show_window():
    j = _job()
    assert not vt.is_no_show(j, NOW - timedelta(minutes=31))      # before start + 30
    assert vt.is_no_show(j, NOW - timedelta(minutes=30))           # exactly start + 30
    assert not vt.is_no_show({**j, "checked_in_at": "2026-11-03T13:58:00Z"}, NOW)
    assert not vt.is_no_show({**j, "uploaded_count": 3}, NOW)
    assert not vt.is_no_show({**j, "status": "cancelled"}, NOW)  # Bricks / client cancelled
    assert not vt.is_no_show({**j, "assignee_id": "default_user"}, NOW)
    assert not vt.is_no_show({k: v for k, v in j.items() if k != "selected_slot_start"}, NOW)  # no booked time
    old = _job(selected_slot_start="2026-11-01T14:00:00+00:00", scheduled_date="2026-11-01")
    assert not vt.is_no_show(old, datetime(2026, 11, 1, 15, tzinfo=timezone.utc))  # before the rule started


def test_grace_is_configurable(monkeypatch):
    monkeypatch.setenv("NOSHOW_TERMINATE_GRACE_MIN", "45")
    assert vt.grace_min() == 45
    assert not vt.is_no_show(_job(), NOW - timedelta(minutes=20))
    assert vt.is_no_show(_job(), NOW - timedelta(minutes=15))


def test_acceptance_rules():
    assert vt.acceptance({"vendor_accepted_at": "x"}, NOW) == "yes"
    assert vt.acceptance({"assigned_at": (NOW - timedelta(minutes=61)).isoformat()}, NOW) == "yes"
    assert vt.acceptance({"assigned_at": (NOW - timedelta(minutes=5)).isoformat()}, NOW) == "no"
    assert vt.acceptance({}, NOW) == "unknown"
    assert vt.is_cancel_after_accept("assigned", "declined")
    assert not vt.is_cancel_after_accept("pending_assignment", "declined")  # declining an offer is fine


def test_terminate_no_show_full_flow(_mocks):
    future = _job(id="j2", selected_slot_start="2026-11-05T14:00:00+00:00", agent_email="c2@example.com")
    done = _job(id="j3", status="delivered")
    db = _db(jobs=[_job(), future, done])
    out = asyncio.run(vt.terminate(db, "m1", "j1", "no_show", now=NOW))
    assert out["terminated"]
    m = db.tables["vendor_members"][0]
    assert m["is_active"] is False and m["onboarding_status"] == "terminated" and m["terminated_at"]
    assert not member_eligible_for_job(m, {"package_id": "cleaning"})
    assert sorted(r["job_id"] for r in out["reassigned"]) == ["j1", "j2"]  # delivered job untouched
    led = db.tables["vendor_terminations"][0]
    assert led["email"] == "test+v@example.com" and led["phone"] == "4165550100" and led["trigger"] == "no_show"
    to = [s[0] for s in _mocks["sent"]]
    assert "client@example.com" in to          # no-show moves the visit -> client hears
    assert "c2@example.com" not in to          # future job reassigned cleanly -> no notice
    assert "test+v@example.com" in to          # neutral vendor notice
    vendor_body = [s[2] for s in _mocks["sent"] if s[0] == "test+v@example.com"][0]
    assert "Kam" not in vendor_body and "still be sent" in vendor_body
    actions = _mocks["kam"].call_args[0][4]
    assert actions[0][0] == "undo_termination"
    again = asyncio.run(vt.terminate(db, "m1", "j1", "no_show", now=NOW))
    assert again.get("skipped") == "already_terminated"


def test_cancel_after_accept_no_client_notice_when_reassigned(_mocks):
    db = _db(jobs=[_job(status="declined", vendor_accepted_at="2026-11-02T20:00:00Z",
                        selected_slot_start="2026-11-04T14:00:00+00:00")])
    out = asyncio.run(vt.on_vendor_drop(db, db.tables["jobs"][0], "m1", "assigned", "declined", now=NOW))
    assert out["terminated"]
    assert "client@example.com" not in [s[0] for s in _mocks["sent"]]


def test_client_notified_when_no_replacement(_mocks):
    _mocks["reassign"].side_effect = lambda db, jid, reason: {"ok": False, "job_id": jid, "to": None}
    db = _db(jobs=[_job(status="declined", vendor_accepted_at="x", selected_slot_start="2026-11-04T14:00:00+00:00")])
    asyncio.run(vt.on_vendor_drop(db, db.tables["jobs"][0], "m1", "assigned", "declined", now=NOW))
    assert "client@example.com" in [s[0] for s in _mocks["sent"]]


def test_drop_inside_accept_window_or_offer_decline_does_not_terminate(_mocks):
    db = _db(jobs=[_job(status="declined", assigned_at=(NOW - timedelta(minutes=5)).isoformat())])
    out = asyncio.run(vt.on_vendor_drop(db, db.tables["jobs"][0], "m1", "assigned", "declined", now=NOW))
    assert out == {"skipped": "acceptance_no"}
    assert asyncio.run(vt.on_vendor_drop(db, db.tables["jobs"][0], "m1", "pending_assignment", "declined", now=NOW)) is None
    assert db.tables["vendor_members"][0]["is_active"] is True


def test_unknown_acceptance_asks_kam(_mocks):
    db = _db(jobs=[_job(status="declined")])
    out = asyncio.run(vt.on_vendor_drop(db, db.tables["jobs"][0], "m1", "assigned", "declined", now=NOW))
    assert out == {"skipped": "acceptance_unknown"}
    assert _mocks["kam"].call_args[0][4][0][0] == "terminate_vendor"
    assert db.tables["vendor_members"][0]["is_active"] is True


def test_owner_never_terminated(_mocks):
    db = _db()
    with patch("app.shared.vendor_identity.is_owner_member", return_value=True):
        out = asyncio.run(vt.terminate(db, "m1", "j1", "no_show", now=NOW))
    assert out.get("skipped") == "owner_or_missing"


def test_reapply_blocked_and_undo(_mocks):
    docs = [{"member_id": "m1", "doc_type": "id_verification",
             "validation_result": {"content_check": {"sha256": "abc", "id_fingerprint": vt.id_fingerprint("Test Cleaner", "A123")}}}]
    db = _db(docs=docs)
    asyncio.run(vt.terminate(db, "m1", "j1", "no_show", now=NOW))
    assert asyncio.run(vt.blocked_identity(db, email="TEST+v@example.com"))
    assert asyncio.run(vt.blocked_identity(db, phone="416-555-0100"))
    assert asyncio.run(vt.blocked_identity(db, id_hashes=[vt.id_fingerprint("cleaner test", "a-123")]))
    assert not asyncio.run(vt.blocked_identity(db, email="other@example.com"))
    res = asyncio.run(ops.run_action(db, {"a": "undo_termination", "p": {"member_id": "m1"}}))
    assert res["ok"]
    m = db.tables["vendor_members"][0]
    assert m["is_active"] is True and m["onboarding_status"] == "approved" and m["terminated_at"] is None
    assert not asyncio.run(vt.blocked_identity(db, email="test+v@example.com"))


def test_sweep_terminates_no_show(_mocks):
    db = _db()
    with patch.object(ops, "_recent_jobs", AsyncMock(return_value=[_job()])):
        out = asyncio.run(ops.sweep(db, NOW, run_reconcile=False, run_payouts=False))
    assert out["terminated"] and out["terminated"][0]["terminated"]
