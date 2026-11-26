"""HTML in photographer names and booking fields must not render in notification mail.

These tests fail while services/notifications.py and the no-vendor ops alert paste
those strings into HTML unescaped. They pass once every dynamic value is escaped
before it is placed in the message body that Resend is given.

The Resend HTTP client is replaced with a recorder. The recorder encodes the JSON
body the same way httpx 0.28.1 encode_json does (json.dumps ensure_ascii=False,
separators=(",", ":"), allow_nan=False) and the assertions run on the html field
after that round trip, which does not neutralize tags.
"""

from __future__ import annotations

import asyncio
import json as jsonlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.routers import vendor_os as vos
from app.routers import vendor_profile as vp
from app.services import notifications as notif
from app.services import vendor_selection as vsel
from app.shared.schemas import Job

ACTOR = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
JOB_ID = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
AGENT_EMAIL = "agent.booking@example.com"
PHOTO_EMAIL = "photographer@example.com"

# Markup a signed-in photographer can store as their display name, and markup a
# booking can store on the job. Each payload is a full element, not a fragment
# that a later formatter would drop.
NAME_HTML = (
    '</strong><a href="https://evil.example/pay">Pay your shoot balance here</a>'
)
ADDR_HTML = (
    '</strong><a href="https://evil.example/addr">Open the lockbox instructions</a>'
)
AGENT_HTML = '</p><a href="https://evil.example/login">Sign in to your Bricks account</a>'
# format_shoot_date keeps only the first 10 characters when the value is not a date.
# This 10-character prefix is what reaches the when-line and closes the paragraph.
WHEN_BREAKOUT = "</p><img "
PACKAGE_HTML = '</p><a href="https://evil.example/pkg">Confirm this package</a>'
BOOKING_HTML = '</p><a href="https://evil.example/book">Open the booking</a>'
ARRIVE_HTML = '</strong><img src="https://evil.example/pixel">'
ABBREV_HTML = 'ET"><a href="https://evil.example/zone">ET</a>'

# Obvious placeholder. Not a credential.
_RESEND_PLACEHOLDER = "placeholder-not-a-secret"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self._db = db
        self._table = table
        self._op = "select"
        self._patch = None
        self._row = None
        self._single = False
        self._eqs: list[tuple] = []

    def select(self, *_a, **_k):
        return self

    def insert(self, row):
        self._op = "insert"
        self._row = row
        return self

    def upsert(self, row):
        self._op = "upsert"
        self._row = row
        return self

    def update(self, patch):
        self._op = "update"
        self._patch = dict(patch)
        return self

    def eq(self, key, value):
        self._eqs.append((key, value))
        return self

    def in_(self, *_a, **_k):
        return self

    def is_(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def maybe_single(self):
        self._single = True
        return self

    async def execute(self):
        table = self._db.tables.setdefault(self._table, {})
        if self._op == "update":
            for key, value in self._eqs:
                row = table.get(value) if key == "id" else None
                if isinstance(row, dict):
                    row.update(self._patch or {})
                    return _Result([dict(row)])
            return _Result([])
        if self._op in ("insert", "upsert"):
            row = dict(self._row or {})
            key = row.get("id") or row.get("member_id") or f"row-{len(table)}"
            table[key] = row
            return _Result([dict(row)])
        if self._single:
            for key, value in self._eqs:
                row = table.get(value) if key == "id" else None
                if isinstance(row, dict):
                    return _Result(dict(row))
            return None
        rows = []
        for key, value in self._eqs:
            row = table.get(value) if key == "id" else None
            if isinstance(row, dict):
                rows.append(dict(row))
        return _Result(rows)


class _Db:
    def __init__(self):
        self.tables: dict = {"vendor_members": {}, "jobs": {}}

    def table(self, name):
        return _Query(self, name)


class _Response:
    status_code = 200
    headers: dict = {}

    def raise_for_status(self):
        return None

    def json(self):
        return {"id": "email_test"}


class _RecordingClient:
    """Stand-in for httpx.AsyncClient that keeps the JSON body httpx would encode.

    encode_json in httpx 0.28.1 (httpx/_content.py) is json.dumps(...,
    ensure_ascii=False, separators=(",", ":"), allow_nan=False). That encoding
    leaves < and > in the html string, so a tag in the html field is still a tag
    on the wire.
    """

    posts: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, url, headers=None, json=None, **kwargs):
        body = json if isinstance(json, dict) else {}
        encoded = json_dumps_like_httpx(body)
        decoded = jsonlib.loads(encoded)
        _RecordingClient.posts.append({"url": url, "json": decoded, "headers": headers or {}})
        return _Response()


def json_dumps_like_httpx(payload: dict) -> str:
    return jsonlib.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _install_recorder(monkeypatch):
    _RecordingClient.posts = []
    import sys

    stub = sys.modules.get("httpx")
    if stub is not None:
        monkeypatch.setattr(stub, "AsyncClient", _RecordingClient, raising=False)
    monkeypatch.setattr(notif, "httpx", SimpleNamespace(AsyncClient=_RecordingClient), raising=False)
    monkeypatch.setattr(vsel, "httpx", SimpleNamespace(AsyncClient=_RecordingClient), raising=False)
    monkeypatch.setenv("RESEND_API_KEY", _RESEND_PLACEHOLDER)


def _resend_messages():
    return [p for p in _RecordingClient.posts if "api.resend.com/emails" in str(p.get("url"))]


def _assert_text_escaped(html: str, value: str):
    """value must be text, not markup, in the HTML body."""
    assert value not in html
    assert html_mod_escape(value) in html


def html_mod_escape(value: str) -> str:
    import html as html_mod

    return html_mod.escape(value, quote=True)


def _member(**extra):
    row = {
        "id": ACTOR,
        "name": "Alex Rivera",
        "email": PHOTO_EMAIL,
        "phone": "",
        "role": "Photographer",
        "title": "Photographer IC",
        "vendor_id": "default_vendor",
        "status": "available",
        "is_active": True,
        "onboarding_status": "approved",
        "onboarding_completed_at": "2026-01-01T00:00:00+00:00",
    }
    row.update(extra)
    return row


def _job(**extra):
    row = {
        "id": JOB_ID,
        "assignee_id": ACTOR,
        "vendor_id": "default_vendor",
        "address": ADDR_HTML,
        "agent_name": AGENT_HTML,
        "agent_email": AGENT_EMAIL,
        "scheduled_date": WHEN_BREAKOUT,
        "time_window_start": "12:00",
        "time_window_end": "16:00",
        "preferred_window": "afternoon",
        "status": "assigned",
        "state": "ON",
        "job_type": "photo",
        "booking_id": "booking-1",
    }
    row.update(extra)
    return row


def _bind_db(monkeypatch, db):
    async def _get_db():
        return db

    async def _member_of(actor_id, email=None):
        row = db.tables["vendor_members"].get(actor_id)
        if not row:
            from app.shared.errors import api_error
            api_error(403, "Vendor member not found", "vendor_member_not_found")
        return dict(row)

    for mod in (vp, vos, notif, vsel):
        monkeypatch.setattr(mod, "get_db", _get_db)
    monkeypatch.setattr(vp, "resolve_vendor_actor_id", AsyncMock(return_value=ACTOR))
    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=ACTOR))
    monkeypatch.setattr(vp, "get_vendor_member", _member_of)
    monkeypatch.setattr(vos, "get_vendor_member", _member_of)


def test_confirm_arrive_email_escapes_name_address_and_when_line(monkeypatch):
    """Profile name and job fields are mailed to the booking agent as HTML text.

    The photographer saves a display name through PUT /api/profile. That write
    only strips whitespace. Confirming an arrive time on their assigned job
    then builds the client email from the stored name and the job's address,
    agent name, and schedule.
    """
    db = _Db()
    db.tables["vendor_members"][ACTOR] = _member()
    db.tables["jobs"][JOB_ID] = _job()
    _bind_db(monkeypatch, db)
    _install_recorder(monkeypatch)

    saved = _run(vp.update_profile(
        vp.ProfileUpdateBody(name=f"  {NAME_HTML}  "),
        authorization="Bearer session",
        x_user_id=None,
    ))
    # The name that confirm-arrive will load is whatever the profile write kept.
    stored_name = db.tables["vendor_members"][ACTOR]["name"]
    assert saved["name"] == stored_name

    _run(vos.confirm_job_arrive_time(
        JOB_ID,
        vos.ConfirmArriveRequest(arrive_at="13:30"),
        authorization="Bearer session",
        x_user_id=None,
    ))

    messages = _resend_messages()
    assert len(messages) == 1
    body = messages[0]["json"]
    assert body["to"] == [AGENT_EMAIL]
    assert body["from"] == "BRICKS <notifications@brxs.ca>"
    html = body["html"]
    assert WHEN_BREAKOUT not in html
    assert ADDR_HTML not in html
    assert AGENT_HTML not in html
    assert html_mod_escape(ADDR_HTML) in html
    assert html_mod_escape(AGENT_HTML) in html
    # A profile write that still stores markup must be escaped in the message.
    # A write that rejects or strips the markup must not have kept the tags.
    if "<" in stored_name or ">" in stored_name:
        assert stored_name not in html
        assert html_mod_escape(stored_name) in html
    else:
        assert "<" not in stored_name and ">" not in stored_name


def test_assignment_email_escapes_property_address(monkeypatch):
    """The new-job email to the assigned photographer includes the booking address."""
    db = _Db()
    db.tables["vendor_members"][ACTOR] = _member(name=NAME_HTML)
    job = _job(agent_email="", agent_name="Sam Agent")
    _bind_db(monkeypatch, db)
    _install_recorder(monkeypatch)

    result = _run(notif.notify_assignment(job, ACTOR))
    assert result["email"] is True

    messages = _resend_messages()
    assert len(messages) == 1
    body = messages[0]["json"]
    assert body["to"] == [PHOTO_EMAIL]
    assert "notifications@brxs.ca" in body["from"]
    html = body["html"]
    _assert_text_escaped(html, ADDR_HTML)
    _assert_text_escaped(html, NAME_HTML)
    assert WHEN_BREAKOUT not in html


def test_no_vendor_ops_alert_escapes_job_fields(monkeypatch):
    """The no-vendor Resend body interpolates intake fields into HTML."""
    db = _Db()
    db.tables["jobs"][JOB_ID] = _job()
    _bind_db(monkeypatch, db)
    _install_recorder(monkeypatch)

    job = Job(
        id=JOB_ID,
        address=ADDR_HTML,
        time_window_start="12:00",
        time_window_end="16:00",
        job_type="photo",
        booking_id=BOOKING_HTML,
        package_name=PACKAGE_HTML,
        min_tier_required=1,
        scheduled_date="2026-09-25",
    )
    _run(vsel._escalate_no_vendor(job, attempt_count=3, error_message="none eligible"))

    messages = _resend_messages()
    assert len(messages) == 1
    body = messages[0]["json"]
    assert body["to"] == ["raph@brxs.ca"]
    html = body["html"]
    _assert_text_escaped(html, ADDR_HTML)
    _assert_text_escaped(html, PACKAGE_HTML)
    _assert_text_escaped(html, BOOKING_HTML)


def test_arrive_template_escapes_clock_label():
    """arrive and the zone abbreviation are inserted in the same strong tag as the name."""
    html = notif._arrive_confirm_email_html(
        "Sam Agent",
        "Alex Rivera",
        "10 Front Street",
        "Fri, Sep 25 · 1:30 PM ET",
        ARRIVE_HTML,
        ABBREV_HTML,
    )
    _assert_text_escaped(html, ARRIVE_HTML)
    _assert_text_escaped(html, ABBREV_HTML)


def test_assignment_template_escapes_when_line():
    when = f"{WHEN_BREAKOUT} · 1:30 PM ET"
    html = notif._assignment_email_html("Alex Rivera", "10 Front Street", when, "https://go.brxs.ca")
    assert WHEN_BREAKOUT not in html
    assert html_mod_escape(when) in html
