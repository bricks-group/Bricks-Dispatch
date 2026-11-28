"""Completed jobs must not be reopened by the assigned photographer.

A photographer session can call three vendor routes that write job status:

- PUT/PATCH /api/vendor-os/jobs/{id}/status  (update_vendor_job_status)
- POST /api/vendor-os/jobs/{id}/watch-action (watch_job_action)
- POST /api/vendor-os/jobs/{id}/check-in     (check_in_job, which calls
  update_vendor_job_status when the job is not already in_progress)

These tests are the spec for closing the reopen loop. They fail while a
vendor can leave completed or processing, while a second completion of the
same job adds another on-time assignment, or while a device completed_at in
the past is treated as the completion clock. They pass once those writes
stop.

The internal dispatch status route is not covered here. It requires
X-Internal-Api-Key (dispatch.update_job_status) and is not a photographer
session.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.integrations.bricks_ai_dispatch import _score_vendor
from app.routers import vendor_os as vos
from app.services.vendor_selection import pick_vendor_rule_based
from app.shared.fair_use_auth import clear_auth_cooldown
from app.shared.schemas import Job, JobStatusUpdate, VendorMember

ACTOR = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
EMAIL = "photographer@example.com"
JOB_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
OTHER_JOB = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
BOOKING = "bk-reopen-1"
ADDRESS = "10 King St"
# Obvious placeholder. The email helper only builds the Resend request when
# a key is present; the HTTP client below never sends it.
_RESEND_PLACEHOLDER = "placeholder-not-a-real-key"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _b64(obj: dict) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _session_token() -> str:
    """Unverified JWT shape. resolve_vendor_actor_id reads amr only after get_user."""
    header = _b64({"alg": "none", "typ": "JWT"})
    body = _b64({"sub": ACTOR, "amr": [{"method": "otp"}]})
    return f"{header}.{body}.sig"


class _Resp:
    status_code = 200

    def raise_for_status(self):
        return None


class _Http:
    """Stands in for the outbound HTTP client. Records the call and does not send it."""

    def __init__(self, *args, **kwargs):
        self.calls = _Http.calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs.get("json") or {}))
        return _Resp()

    async def put(self, url, **kwargs):
        self.calls.append(("PUT", url, kwargs.get("json") or {}))
        return _Resp()

    async def patch(self, url, **kwargs):
        self.calls.append(("PATCH", url, kwargs.get("json") or {}))
        return _Resp()

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs.get("json") or {}))
        return _Resp()


_Http.calls = []


class _Not:
    def __init__(self, query):
        self.query = query

    def in_(self, col, values):
        self.query.not_in.append((col, {str(v) for v in values}))
        return self.query


class _Q:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.op = "select"
        self.payload = None
        self.filters = []
        self.not_in = []
        self.single = False

    def select(self, *args, **kwargs):
        return self

    def update(self, fields):
        self.op = "update"
        self.payload = copy.deepcopy(fields)
        return self

    def insert(self, row):
        self.op = "insert"
        self.payload = copy.deepcopy(row)
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def in_(self, col, values):
        self.filters.append((col, ("in", list(values))))
        return self

    def ilike(self, col, val):
        self.filters.append((col, ("ilike", val)))
        return self

    def is_(self, col, val):
        self.filters.append((col, ("is", val)))
        return self

    def limit(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def range(self, *args, **kwargs):
        return self

    def maybe_single(self):
        self.single = True
        return self

    @property
    def not_(self):
        return _Not(self)

    def _match(self, row) -> bool:
        for col, val in self.filters:
            if isinstance(val, tuple) and val[0] == "in":
                allowed = {str(v) for v in val[1]}
                if str(row.get(col)) not in allowed:
                    return False
                continue
            if isinstance(val, tuple) and val[0] == "ilike":
                if str(row.get(col) or "").lower() != str(val[1] or "").lower():
                    return False
                continue
            if isinstance(val, tuple) and val[0] == "is":
                if str(val[1]).lower() == "null":
                    if row.get(col) not in (None, ""):
                        return False
                elif str(row.get(col)) != str(val[1]):
                    return False
                continue
            if str(row.get(col)) != str(val):
                return False
        for col, blocked in self.not_in:
            if str(row.get(col)) in blocked:
                return False
        return True

    def _rows(self):
        return [r for r in self.db.tables.setdefault(self.name, []) if self._match(r)]

    def _visible(self, row):
        out = copy.deepcopy(row)
        # Reads report a stale legal status. The stored row is what an update
        # must match, so a second lookup of the same row still does not
        # authorize a write against a status the read no longer matches.
        if self.name == "jobs" and self.db.seen_status is not None and self.op == "select":
            out["status"] = self.db.seen_status
        return out

    async def execute(self):
        if self.op == "insert":
            row = copy.deepcopy(self.payload)
            self.db.tables.setdefault(self.name, []).append(row)
            self.db.writes.append((self.name, "insert", copy.deepcopy(row)))
            return _Result([row])
        if self.op == "update":
            matched = self._rows()
            for row in matched:
                row.update(copy.deepcopy(self.payload))
            self.db.writes.append((self.name, "update", copy.deepcopy(self.payload), len(matched)))
            data = [self._visible(r) for r in matched]
            return _Result(data)
        rows = [self._visible(r) for r in self._rows()]
        if self.single:
            if not rows:
                return None
            return _Result(rows[0])
        return _Result(rows)


class _Result:
    def __init__(self, data):
        self.data = data
        self.count = len(data) if isinstance(data, list) else 1


class _User:
    def __init__(self):
        self.id = ACTOR
        self.email = EMAIL
        self.email_confirmed_at = "2026-06-01T00:00:00+00:00"
        self.identities = []


class _Auth:
    async def get_user(self, token):
        # Supabase Auth is the boundary. The rest of resolve_vendor_actor_id
        # runs for real against this user and the in-memory member row.
        assert token
        return _ResultUser(_User())


class _ResultUser:
    def __init__(self, user):
        self.user = user


class _Db:
    def __init__(self, jobs, members):
        self.tables = {
            "jobs": jobs,
            "vendor_members": members,
            "uploads": [],
            "vendor_probation_reviews": [],
        }
        self.writes = []
        self.auth = _Auth()
        self.seen_status = None

    def table(self, name):
        return _Q(self, name)


def _member(**extra) -> dict:
    row = {
        "id": ACTOR,
        "auth_user_id": ACTOR,
        "vendor_id": "default_vendor",
        "name": "Casey Photographer",
        "email": EMAIL,
        "role": "Photographer IC",
        "title": "Photographer IC",
        "vendor_type": "photographer",
        "specialty": "photo",
        "status": "available",
        "is_active": True,
        "onboarding_status": "approved",
        "onboarding_completed_at": "2026-10-01T00:00:00+00:00",
        "tier": 1,
        "quality_rating": 4.0,
        "on_time_rate": 0.5,
        "cancel_rate": 0.0,
        "decline_rate": 0.0,
        "total_assignments": 4,
        "active_assignments": 0,
    }
    row.update(extra)
    return row


def _job(**extra) -> dict:
    row = {
        "id": JOB_ID,
        "assignee_id": ACTOR,
        "vendor_id": "default_vendor",
        "booking_id": BOOKING,
        "agent_email": "client@example.com",
        "agent_name": "Alex Client",
        "address": ADDRESS,
        "status": "in_progress",
        "job_type": "photo",
        "package_name": "Essentials",
        "checklist": [],
        "deliverables_count": 4,
        "uploaded_count": 1,
        "scheduled_date": "2099-06-01",
        "time_window_start": "09:00",
        "time_window_end": "17:00",
        "timezone": "America/Toronto",
    }
    row.update(extra)
    return row


def _other_job() -> dict:
    return _job(id=OTHER_JOB, address="20 Queen St", booking_id="bk-other", status="assigned")


@pytest.fixture
def gate(monkeypatch):
    """Install one in-memory database and a non-sending HTTP client."""
    import httpx

    _Http.calls = []
    clear_auth_cooldown()
    state = {"db": None}

    async def get_db():
        return state["db"]

    def pg_job(job_id):
        db = state["db"]
        for row in db.tables["jobs"]:
            if str(row.get("id")) == str(job_id):
                out = copy.deepcopy(row)
                if db.seen_status is not None:
                    out["status"] = db.seen_status
                return out
        return None

    def pg_member(actor_id="", email=""):
        for row in state["db"].tables["vendor_members"]:
            if actor_id and str(row.get("id")) == str(actor_id):
                return copy.deepcopy(row)
            if email and str(row.get("email") or "").lower() == str(email).lower():
                return copy.deepcopy(row)
        return None

    monkeypatch.setattr("app.shared.database.get_db", get_db)
    monkeypatch.setattr("app.shared.auth.get_db", get_db)
    monkeypatch.setattr(vos, "get_db", get_db)
    monkeypatch.setattr("app.shared.pg_fallback.pg_select_job_by_id", pg_job)
    monkeypatch.setattr("app.shared.pg_fallback.pg_select_vendor_member", pg_member)
    monkeypatch.setattr("app.shared.pg_fallback._pg_connect", lambda: None)
    monkeypatch.setattr("app.shared.pg_fallback._os_internal_post", lambda *a, **k: None)
    monkeypatch.setenv("RESEND_API_KEY", _RESEND_PLACEHOLDER)
    httpx.AsyncClient = _Http

    def install(*jobs, member=None, seen_status=None):
        rows = [copy.deepcopy(j) for j in jobs] or [_job()]
        db = _Db(rows, [_member() if member is None else copy.deepcopy(member)])
        db.seen_status = seen_status
        state["db"] = db
        _Http.calls = []
        return db

    return install


def _auth():
    return {"authorization": f"Bearer {_session_token()}", "x_user_id": None}


def _attempt(coro):
    try:
        return _run(coro), None
    except HTTPException as exc:
        return None, exc


def _job_row(db, job_id=JOB_ID) -> dict:
    for row in db.tables["jobs"]:
        if row["id"] == job_id:
            return row
    raise AssertionError(f"missing job {job_id}")


def _member_row(db) -> dict:
    return db.tables["vendor_members"][0]


def _put(status, **fields):
    return vos.update_vendor_job_status(JOB_ID, JobStatusUpdate(status=status, **fields), **_auth())


def _watch(action):
    return vos.watch_job_action(JOB_ID, vos.WatchActionRequest(action=action), **_auth())


def _check_in():
    return vos.check_in_job(JOB_ID, vos.CheckInRequest(), **_auth())


def _enroute_emails():
    return [
        body for method, url, body in _Http.calls
        if method == "POST" and str(url).startswith("https://api.resend.com/emails")
        and str((body or {}).get("subject") or "").startswith("Photographer en route")
    ]


def _status_webhooks(new_status=None):
    out = []
    for method, url, body in _Http.calls:
        if method == "PUT" and f"/api/dispatch-jobs/{BOOKING}/status" in str(url):
            if new_status is None or (body or {}).get("new_status") == new_status:
                out.append(body)
    return out


def _assert_refused(result, err, db, status):
    observed = {
        "result": result,
        "http_status": getattr(err, "status_code", None),
        "job_status": _job_row(db)["status"],
        "other_job_status": _job_row(db, OTHER_JOB)["status"],
        "total_assignments": _member_row(db)["total_assignments"],
        "on_time_rate": _member_row(db)["on_time_rate"],
        "enroute_emails": len(_enroute_emails()),
        "booking_updates": len(_status_webhooks()),
        "checked_in_at": _job_row(db).get("checked_in_at"),
        "probation_rows": len(db.tables["vendor_probation_reviews"]),
    }
    assert observed == {
        "result": None,
        "http_status": 409,
        "job_status": status,
        "other_job_status": "assigned",
        "total_assignments": 4,
        "on_time_rate": 0.5,
        "enroute_emails": 0,
        "booking_updates": 0,
        "checked_in_at": None,
        "probation_rows": 0,
    }


def test_vendor_status_routes_are_mounted():
    paths = {getattr(route, "path", "") for route in vos.router.routes}
    assert "/api/vendor-os/jobs/{job_id}/status" in paths
    assert "/api/vendor-os/jobs/{job_id}/watch-action" in paths
    assert "/api/vendor-os/jobs/{job_id}/check-in" in paths


def test_assigned_to_in_progress_sends_one_en_route_notice(gate):
    db = gate(_job(status="assigned"), _other_job())
    result, err = _attempt(_put("in_progress"))
    assert err is None and result["new_status"] == "in_progress"
    assert _job_row(db)["status"] == "in_progress"
    assert _job_row(db, OTHER_JOB)["status"] == "assigned"
    assert len(_enroute_emails()) == 1
    assert ADDRESS in _enroute_emails()[0]["subject"]
    hooks = _status_webhooks("in_progress")
    assert len(hooks) == 1
    assert hooks[0]["previous_status"] == "assigned"
    assert hooks[0]["job_id"] == JOB_ID
    assert _member_row(db)["total_assignments"] == 4


def test_first_completion_counts_once_when_the_server_clock_is_before_the_deadline(gate):
    db = gate(_job(status="in_progress"), _other_job())
    recent = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    result, err = _attempt(_put("completed", completed_at=recent))
    assert err is None and result["new_status"] == "completed"
    assert _job_row(db)["status"] == "completed"
    assert _member_row(db)["total_assignments"] == 5
    assert _member_row(db)["on_time_rate"] == 0.6
    assert _enroute_emails() == []
    assert len(_status_webhooks("completed")) == 1


def test_past_completed_at_does_not_count_as_on_time_after_the_deadline(gate):
    """Server time is after the booked end. A device timestamp from that morning must not count as on time."""
    db = gate(
        _job(
            status="in_progress",
            scheduled_date="2020-01-02",
            time_window_start="09:00",
            time_window_end="12:00",
        ),
        _other_job(),
    )
    result, err = _attempt(_put("completed", completed_at="2020-01-02T08:00:00+00:00"))
    assert err is None and result["new_status"] == "completed"
    assert _job_row(db)["status"] == "completed"
    assert _member_row(db)["total_assignments"] == 5
    # 2 on-time out of 4, plus one late completion → 2/5.
    assert _member_row(db)["on_time_rate"] == 0.4


def test_put_cannot_reopen_a_completed_job(gate):
    db = gate(_job(status="completed"), _other_job())
    result, err = _attempt(_put("in_progress"))
    _assert_refused(result, err, db, "completed")


def test_watch_arrived_cannot_reopen_a_completed_job(gate):
    db = gate(_job(status="completed"), _other_job())
    result, err = _attempt(_watch("arrived"))
    _assert_refused(result, err, db, "completed")


def test_watch_on_way_cannot_reopen_a_completed_job(gate):
    db = gate(_job(status="completed"), _other_job())
    result, err = _attempt(_watch("on_way"))
    _assert_refused(result, err, db, "completed")


@pytest.mark.parametrize("action", ["accept", "decline"])
def test_watch_accept_and_decline_cannot_leave_completed(gate, action):
    db = gate(_job(status="completed"), _other_job())
    result, err = _attempt(_watch(action))
    _assert_refused(result, err, db, "completed")


def test_check_in_cannot_reopen_a_completed_job(gate):
    db = gate(_job(status="completed"), _other_job())
    result, err = _attempt(_check_in())
    _assert_refused(result, err, db, "completed")


@pytest.mark.parametrize(
    "call",
    [
        lambda: _put("in_progress"),
        lambda: _put("completed"),
        lambda: _watch("arrived"),
        lambda: _check_in(),
    ],
    ids=["put_in_progress", "put_completed", "watch_arrived", "check_in"],
)
def test_vendor_cannot_move_a_processing_job(gate, call):
    db = gate(_job(status="processing"), _other_job())
    result, err = _attempt(call())
    _assert_refused(result, err, db, "processing")


def test_delivered_stays_delivered(gate):
    db = gate(_job(status="delivered"), _other_job())
    result, err = _attempt(_put("in_progress"))
    _assert_refused(result, err, db, "delivered")
    result, err = _attempt(_watch("arrived"))
    _assert_refused(result, err, db, "delivered")


def test_repeating_completion_does_not_raise_the_on_time_record(gate):
    db = gate(_job(status="in_progress"), _other_job())
    result, err = _attempt(_put("completed"))
    assert err is None and result["new_status"] == "completed"
    assert _member_row(db)["total_assignments"] == 5
    assert _member_row(db)["on_time_rate"] == 0.6

    for _ in range(8):
        _attempt(_watch("arrived"))
        assert _job_row(db)["status"] == "completed"
        again, again_err = _attempt(_put("completed"))
        if again_err is not None:
            assert again_err.status_code == 409
        assert again is None or again.get("new_status") == "completed"
        assert _job_row(db)["status"] == "completed"
        assert _member_row(db)["total_assignments"] == 5
        assert _member_row(db)["on_time_rate"] == 0.6

    assert _enroute_emails() == []
    assert _status_webhooks("in_progress") == []
    assert len(_status_webhooks("completed")) == 1


def test_reopen_loop_cannot_outrank_an_honest_photographer(gate):
    db = gate(_job(status="in_progress"))
    result, err = _attempt(_put("completed"))
    assert err is None and result["new_status"] == "completed"
    for _ in range(8):
        _attempt(_watch("arrived"))
        _attempt(_put("in_progress"))
        _attempt(_check_in())
        _attempt(_put("completed"))

    attacker = VendorMember(**_member_row(db))
    honest = VendorMember(
        id=OTHER,
        name="Honest Photographer",
        email="honest@example.com",
        specialty="photo",
        is_active=True,
        status="available",
        onboarding_status="approved",
        onboarding_completed_at="2026-10-01T00:00:00+00:00",
        tier=1,
        quality_rating=4.0,
        on_time_rate=0.80,
        cancel_rate=0.0,
        decline_rate=0.0,
        total_assignments=10,
        active_assignments=0,
    )
    offer = Job(address="123 Main St", job_type="photo", time_window_start="09:00", time_window_end="12:00")
    chosen = pick_vendor_rule_based(offer, [attacker, honest])
    attacker_score = _score_vendor(attacker, offer, 1)
    honest_score = _score_vendor(honest, offer, 1)
    assert chosen == honest.id, (
        chosen,
        attacker.on_time_rate,
        attacker.total_assignments,
        attacker_score,
        honest_score,
    )
    assert attacker.total_assignments == 5
    assert attacker.on_time_rate == 0.6
    assert honest_score > attacker_score


def test_status_write_must_match_the_stored_status_not_a_stale_read(gate):
    """The row is delivered. Every status read still reports in_progress.

    A check of that read, including a second read, must not complete the
    delivered row or add an on-time assignment. The update has to be limited
    to the status that was checked.
    """
    db = gate(_job(status="delivered"), _other_job(), seen_status="in_progress")
    result, err = _attempt(_put("completed", completed_at="2020-01-02T08:00:00+00:00"))
    assert _job_row(db)["status"] == "delivered"
    assert _job_row(db, OTHER_JOB)["status"] == "assigned"
    assert _member_row(db)["total_assignments"] == 4
    assert _member_row(db)["on_time_rate"] == 0.5
    assert db.tables["vendor_probation_reviews"] == []
    assert _status_webhooks() == []
    assert _enroute_emails() == []
    assert err is not None and err.status_code == 409
    assert result is None


def test_another_photographers_job_is_not_updated(gate):
    db = gate(_job(status="in_progress", assignee_id=OTHER), _other_job())
    result, err = _attempt(_put("completed"))
    assert err is not None and err.status_code in (403, 404)
    assert result is None
    assert _job_row(db)["status"] == "in_progress"
    assert _member_row(db)["total_assignments"] == 4
    assert _Http.calls == []
    assert db.writes == []
