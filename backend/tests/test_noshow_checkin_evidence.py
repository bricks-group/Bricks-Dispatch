"""
Client no-show evidence (Kam, 2026-10-30).

A report needs: an app check-in near the property, a 20-minute wait counted from
the LATER of check-in and the booked start, a photo uploaded after check-in, and a
logged call attempt. Stripe is never touched: the report path only records a claim
(payout runs later, on admin approval) and the webhook/audit calls are mocked.
"""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app.routers import admin_ops
from app.routers import vendor_os as vos
from app.shared import noshow_policy as np_

ACTOR = "member-ns"
JOB_ID = "11111111-2222-3333-4444-555555555555"
# 1 Yonge St, Toronto
JOB_LAT, JOB_LNG = 43.6426, -79.3755
TZ = ZoneInfo("America/Toronto")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _job(start_offset_min: float, **extra) -> dict:
    """start_offset_min > 0 means the booked start is in the past."""
    start = (datetime.now(timezone.utc) - timedelta(minutes=start_offset_min)).astimezone(TZ)
    job = {
        "id": JOB_ID,
        "assignee_id": ACTOR,
        "vendor_id": "default_vendor",
        "booking_id": None,
        "status": "assigned",
        "province": "ON",
        "scheduled_date": start.strftime("%Y-%m-%d"),
        "time_window_start": start.strftime("%H:%M"),
        "lat": JOB_LAT,
        "lng": JOB_LNG,
    }
    job.update(extra)
    return job


def _ago(minutes: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


# ── In-memory supabase-py stand-in ───────────────────────────────────────────

class _Q:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.op, self.payload, self.filters, self.single = "select", None, [], False
        self.in_filters, self.null_filters = [], []

    def select(self, *a, **k):
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, fields):
        self.op, self.payload = "update", fields
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def in_(self, col, vals):
        self.in_filters.append((col, [str(v) for v in vals]))
        return self

    def is_(self, col, val):
        self.null_filters.append(col)
        return self

    def gte(self, *a):
        return self

    def order(self, *a, **k):
        return self

    def range(self, *a):
        return self

    def maybe_single(self):
        self.single = True
        return self

    def _rows(self):
        return [r for r in self.db.tables.setdefault(self.name, [])
                if all(str(r.get(c)) == str(v) for c, v in self.filters)
                and all(str(r.get(c)) in vs for c, vs in self.in_filters)
                and all(r.get(c) is None for c in self.null_filters)]

    async def execute(self):
        if self.op == "insert":
            self.db.tables.setdefault(self.name, []).append(dict(self.payload))
            return SimpleNamespace(data=[self.payload])
        if self.op == "update":
            for r in self._rows():
                r.update(self.payload)
            self.db.updates.append((self.name, dict(self.payload)))
            return SimpleNamespace(data=[])
        rows = [copy.deepcopy(r) for r in self._rows()]
        if self.single:
            return None if not rows else SimpleNamespace(data=rows[0])
        return SimpleNamespace(data=rows, count=len(rows))


class _Db:
    def __init__(self, job):
        self.tables = {"jobs": [job], "agent_noshow_trip_fees": [], "bookings": []}
        self.updates = []

    def table(self, name):
        return _Q(self, name)


@pytest.fixture
def env(monkeypatch):
    state = {}

    def install(job):
        db = _Db(job)
        state["db"] = db

        async def fake_get_db():
            return db

        monkeypatch.setattr(vos, "get_db", fake_get_db)
        monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=ACTOR))
        monkeypatch.setattr(vos, "get_vendor_member", AsyncMock(return_value={"id": ACTOR, "vendor_id": "default_vendor", "role": "photographer"}))
        monkeypatch.setattr(vos, "write_audit_event", AsyncMock())
        monkeypatch.setattr(vos, "send_dispatch_status_update_webhook", AsyncMock())

        async def fake_status(job_id, payload, *_a):
            for r in db.tables["jobs"]:
                if r["id"] == job_id:
                    r["status"] = payload.status
            return {"status": "updated", "job_id": job_id, "new_status": payload.status}

        monkeypatch.setattr(vos, "update_vendor_job_status", fake_status)
        return db

    return install


def _db_job(db):
    return db.tables["jobs"][0]


def _status_code(exc):
    return exc.value.status_code


def _detail(exc):
    d = exc.value.detail
    return d.get("message") if isinstance(d, dict) else str(d)


# ── Wait rule: later of check-in and booked start ────────────────────────────

def test_early_checkin_wait_counts_from_booked_start():
    # Checked in 30 min early; booked start is 10 min from now.
    job = _job(-10, checked_in_at=_ago(30))
    r = np_.readiness(job)
    start = np_.booked_start(job)
    assert r["wait_started_at"] == start.isoformat()
    assert r["ready_at"] == (start + timedelta(minutes=20)).isoformat()
    assert not r["waited"] and not r["can_report"]


def test_early_checkin_ready_once_twenty_minutes_past_start():
    # Checked in 60 min ago, booked start 25 min ago → waited.
    job = _job(25, checked_in_at=_ago(60))
    assert np_.readiness(job)["waited"] is True


def test_late_checkin_wait_counts_from_checkin():
    # Booked start 40 min ago but checked in only 10 min ago → still waiting.
    job = _job(40, checked_in_at=_ago(10))
    r = np_.readiness(job)
    assert r["wait_started_at"] == np_.parse_ts(job["checked_in_at"]).isoformat()
    assert not r["waited"]


# ── Check-in near the property ───────────────────────────────────────────────

def test_checkin_too_far_is_rejected_and_nothing_saved(env):
    db = env(_job(0))
    # ~1.1 km north of the property
    with pytest.raises(HTTPException) as e:
        _run(vos.check_in_job(JOB_ID, vos.CheckInRequest(lat=JOB_LAT + 0.01, lng=JOB_LNG, accuracy_m=8), "Bearer t", None))
    assert _status_code(e) == 422
    assert "from the property" in _detail(e)
    assert _db_job(db).get("checked_in_at") is None
    assert _db_job(db)["status"] == "assigned"


def test_checkin_without_location_is_rejected_when_job_has_coords(env):
    db = env(_job(0))
    with pytest.raises(HTTPException) as e:
        _run(vos.check_in_job(JOB_ID, None, "Bearer t", None))
    assert _status_code(e) == 422
    assert "Location is required" in _detail(e)
    assert _db_job(db).get("checked_in_at") is None


def test_checkin_nearby_records_time_gps_and_distance(env):
    db = env(_job(-30))  # 30 min early
    out = _run(vos.check_in_job(JOB_ID, vos.CheckInRequest(lat=JOB_LAT + 0.0006, lng=JOB_LNG, accuracy_m=12), "Bearer t", None))
    row = _db_job(db)
    assert row["status"] == "in_progress"
    assert row["checked_in_at"]
    assert 60 < row["check_in_distance_m"] < 75  # ~67 m, inside the 100 m fence
    assert row["check_in_accuracy_m"] == 12
    assert out["noshow"]["wait_started_at"] == np_.booked_start(row).isoformat()
    first = row["checked_in_at"]
    _run(vos.check_in_job(JOB_ID, vos.CheckInRequest(lat=JOB_LAT, lng=JOB_LNG), "Bearer t", None))
    assert _db_job(db)["checked_in_at"] == first  # first check-in time is kept


def test_checkin_radius_is_one_config_value(env, monkeypatch):
    env(_job(0))
    monkeypatch.setattr(np_, "CHECKIN_RADIUS_M", 2000)
    out = _run(vos.check_in_job(JOB_ID, vos.CheckInRequest(lat=JOB_LAT + 0.01, lng=JOB_LNG), "Bearer t", None))
    assert out["noshow"]["checkin_radius_m"] == 2000


def test_checkin_on_job_without_coords_records_gps_without_distance(env):
    db = env(_job(0, lat=None, lng=None))
    _run(vos.check_in_job(JOB_ID, vos.CheckInRequest(lat=JOB_LAT, lng=JOB_LNG), "Bearer t", None))
    row = _db_job(db)
    assert row["checked_in_at"] and row["check_in_lat"] == JOB_LAT
    assert row["check_in_distance_m"] is None


# ── Call attempt ─────────────────────────────────────────────────────────────

def test_call_attempt_needs_checkin(env):
    env(_job(0))
    with pytest.raises(HTTPException) as e:
        _run(vos.log_call_attempt(JOB_ID, None, "Bearer t", None))
    assert _status_code(e) == 409


def test_call_attempt_logs_dialer_without_twilio(env, monkeypatch):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    db = env(_job(30, status="in_progress", checked_in_at=_ago(30)))
    out = _run(vos.log_call_attempt(JOB_ID, vos.CallAttemptRequest(method="dialer"), "Bearer t", None))
    assert _db_job(db)["noshow_call_method"] == "dialer"
    assert out["has_call"] is True


def test_noshow_status_shares_client_phone_only_after_checkin(env):
    db = env(_job(0, booking_id="bk-1"))
    db.tables["bookings"].append({"id": "bk-1", "contact_phone": "+14165550100", "guest_phone": None, "user_id": None})
    out = _run(vos.get_noshow_status(JOB_ID, "Bearer t", None))
    assert out["client_phone"] is None
    _db_job(db).update(status="in_progress", checked_in_at=_ago(1))
    out = _run(vos.get_noshow_status(JOB_ID, "Bearer t", None))
    assert out["client_phone"] == "+14165550100"


def test_call_attempt_no_number_is_logged_only_when_no_phone(env):
    db = env(_job(30, status="in_progress", checked_in_at=_ago(30)))
    _run(vos.log_call_attempt(JOB_ID, vos.CallAttemptRequest(method="no_number"), "Bearer t", None))
    assert _db_job(db)["noshow_call_method"] == "no_number"
    _db_job(db)["booking_id"] = "bk-2"
    db.tables["bookings"].append({"id": "bk-2", "contact_phone": None, "guest_phone": "416 555 0101", "user_id": None})
    _run(vos.log_call_attempt(JOB_ID, vos.CallAttemptRequest(method="no_number"), "Bearer t", None))
    assert _db_job(db)["noshow_call_method"] == "dialer"


# ── Report gates ─────────────────────────────────────────────────────────────

def _ready_job(**over):
    base = dict(
        status="in_progress",
        checked_in_at=_ago(35),  # before the booked start (30 min ago)
        check_in_lat=JOB_LAT, check_in_lng=JOB_LNG, check_in_accuracy_m=10, check_in_distance_m=20.0,
        noshow_photo_path=f"{JOB_ID}/p.jpg", noshow_photo_uploaded_at=_ago(25), noshow_photo_taken_at=_ago(25),
        noshow_call_attempted_at=_ago(24), noshow_call_method="dialer",
    )
    base.update(over)
    return _job(30, **base)


def _report():
    return vos.AgentNoShowReport(notes="No answer at door", photographer_lat=JOB_LAT, photographer_lng=JOB_LNG)


def test_report_before_twenty_minutes_is_rejected(env):
    db = env(_ready_job(checked_in_at=_ago(10), noshow_photo_uploaded_at=_ago(9), noshow_call_attempted_at=_ago(8)))
    with pytest.raises(HTTPException) as e:
        _run(vos.report_agent_no_show(JOB_ID, _report(), "Bearer t", None))
    assert _status_code(e) == 422 and "Wait" in e.value.detail
    assert db.tables["agent_noshow_trip_fees"] == []


def test_report_early_checkin_before_start_plus_twenty_is_rejected(env):
    # Checked in 60 min early; booked start 5 min ago → 15 min still to wait.
    job = _ready_job(checked_in_at=_ago(65), noshow_photo_uploaded_at=_ago(60), noshow_call_attempted_at=_ago(60))
    start = (datetime.now(timezone.utc) - timedelta(minutes=5)).astimezone(TZ)
    job.update(scheduled_date=start.strftime("%Y-%m-%d"), time_window_start=start.strftime("%H:%M"))
    db = env(job)
    with pytest.raises(HTTPException) as e:
        _run(vos.report_agent_no_show(JOB_ID, _report(), "Bearer t", None))
    assert "Wait" in e.value.detail
    assert db.tables["agent_noshow_trip_fees"] == []


def test_report_without_photo_is_rejected(env):
    db = env(_ready_job(noshow_photo_path=None, noshow_photo_uploaded_at=None))
    with pytest.raises(HTTPException) as e:
        _run(vos.report_agent_no_show(JOB_ID, _report(), "Bearer t", None))
    assert "photo" in e.value.detail
    assert db.tables["agent_noshow_trip_fees"] == []


def test_photo_from_before_checkin_does_not_count(env):
    env(_ready_job(noshow_photo_uploaded_at=_ago(40)))
    with pytest.raises(HTTPException) as e:
        _run(vos.report_agent_no_show(JOB_ID, _report(), "Bearer t", None))
    assert "photo" in e.value.detail


def test_report_without_call_is_rejected(env):
    db = env(_ready_job(noshow_call_attempted_at=None, noshow_call_method=None))
    with pytest.raises(HTTPException) as e:
        _run(vos.report_agent_no_show(JOB_ID, _report(), "Bearer t", None))
    assert "Call client" in e.value.detail
    assert db.tables["agent_noshow_trip_fees"] == []


def test_report_without_checkin_is_rejected(env):
    env(_ready_job(checked_in_at=None))
    with pytest.raises(HTTPException) as e:
        _run(vos.report_agent_no_show(JOB_ID, _report(), "Bearer t", None))
    assert "Check in" in e.value.detail


def test_full_report_records_claim_with_evidence(env):
    db = env(_ready_job())
    out = _run(vos.report_agent_no_show(JOB_ID, _report(), "Bearer t", None))
    assert out["claim_status"] in ("pending", "pending_review")
    claims = db.tables["agent_noshow_trip_fees"]
    assert len(claims) == 1
    c = claims[0]
    assert c["amount_cents"] == 0
    assert c["photo_path"] == f"{JOB_ID}/p.jpg"
    assert c["call_method"] == "dialer" and c["call_attempted_at"]
    assert c["checked_in_at"] and c["check_in_distance_m"] == 20.0
    assert c["wait_started_at"] == np_.booked_start(_db_job(db)).isoformat()
    assert _db_job(db)["status"] == "agent_no_show"


def test_report_without_gps_is_not_held_when_checkin_was_near(env):
    """The apps send no GPS on the report; the verified check-in distance is enough."""
    db = env(_ready_job())
    out = _run(vos.report_agent_no_show(JOB_ID, vos.AgentNoShowReport(notes=None), "Bearer t", None))
    assert out["claim_status"] == "pending", out
    assert not (db.tables["agent_noshow_trip_fees"][0].get("review_reason") or "")


def test_job_without_coords_report_is_held_for_review(env):
    db = env(_ready_job(lat=None, lng=None, check_in_distance_m=None))
    out = _run(vos.report_agent_no_show(JOB_ID, _report(), "Bearer t", None))
    assert out["claim_status"] == "pending_review"
    assert "distance unknown" in db.tables["agent_noshow_trip_fees"][0]["review_reason"]


# ── Admin reviewer sees the evidence ─────────────────────────────────────────

def test_admin_claim_list_includes_photo_url_and_distance(monkeypatch):
    claim = {"id": "c1", "job_id": JOB_ID, "status": "pending", "photo_path": f"{JOB_ID}/p.jpg",
             "checked_in_at": _ago(30), "check_in_distance_m": 37.4}
    signed = AsyncMock(return_value="https://signed.example/p.jpg")
    monkeypatch.setattr("app.services.storage.signed_url_in_bucket", signed)
    out = _run(admin_ops._with_noshow_evidence(claim))
    assert out["photo_url"] == "https://signed.example/p.jpg"
    assert out["check_in_distance_label"] == "37 m from the property"
    signed.assert_awaited_once_with("noshow-evidence", f"{JOB_ID}/p.jpg", 3600)


def test_admin_queue_lists_unreviewed_clean_and_held_claims(monkeypatch):
    """A clean claim is recorded as pending; Bricks still reviews it, so the queue shows it."""
    db = _Db(_job(0))
    db.tables["agent_noshow_trip_fees"] = [
        {"id": "clean", "status": "pending", "reviewed_at": None, "created_at": "3"},
        {"id": "held", "status": "pending_review", "reviewed_at": None, "created_at": "2"},
        {"id": "approved", "status": "pending", "reviewed_at": "2026-10-30T12:00:00Z", "created_at": "1"},
        {"id": "rejected", "status": "rejected", "reviewed_at": "2026-10-30T12:00:00Z", "created_at": "0"},
    ]

    async def fake_get_db():
        return db

    monkeypatch.setattr(admin_ops, "get_db", fake_get_db)
    monkeypatch.setattr(admin_ops, "resolve_vendor_actor_id", AsyncMock(return_value="admin"))
    monkeypatch.setattr(admin_ops, "get_vendor_member", AsyncMock(return_value={"id": "admin", "role": "admin"}))

    async def no_url(*_a, **_k):
        return None

    import app.services.storage as storage
    monkeypatch.setattr(storage, "signed_url_in_bucket", no_url)

    def ids(**kw):
        out = _run(admin_ops.list_noshow_claims(**kw, authorization=None, x_user_id=None))
        items = out.get("items") or out.get("data") or out.get("results") or []
        return sorted(r["id"] for r in items)

    assert ids() == ["clean", "held"]
    assert ids(status="pending_review") == ["held"]
    assert ids(status="all") == ["approved", "clean", "held", "rejected"]
