import asyncio
from unittest.mock import AsyncMock, patch

from app.services import amendment_notice as an
from app.services import ops_autopilot as ops

REAL = {"id": "r1", "name": "Filwyn Ma", "email": "filwyn@gmail.com", "role": "Photographer", "is_active": True}


def test_skip_reasons():
    assert an.skip_reason(REAL) is None
    assert an.skip_reason({**REAL, "role": "Owner"}) == "owner"
    for over in ({"id": "test-term-0001"}, {"name": "TEST Payout Vendor"}, {"email": "akashorter+x@gmail.com"},
                 {"email": "go-e2e-1@brxs.ca"}, {"email": "go.ok@brxs.test"}, {"name": "Fair Use Smoke"},
                 {"email": "raph.mercs+bricks-test-vendor@gmail.com"}):
        assert an.skip_reason({**REAL, **over}) == "test or seed account", over
    assert an.skip_reason({**REAL, "is_active": False}).startswith("not an active vendor")
    assert an.skip_reason({**REAL, "role": "Applicant"}).startswith("applicant")
    assert an.skip_reason({**REAL, "terminated_at": "2026-11-02T00:00:00Z"}) == "terminated"


def test_body_has_guide_line_dates_and_help():
    title, body = an.notice_body(REAL)
    assert title == "An update to your contractor agreement"
    assert an.GUIDE_LINE in body and "16 November 2026" in body and "help@brxs.ca" in body
    assert "IC-A1 v1.0" in body and "100 m" in body and "Kam" not in body


class _Q:
    def __init__(self, db, t):
        self.db, self.t, self.f, self.op, self.payload = db, t, {}, "select", None
    def select(self, *_a): return self
    def eq(self, k, v): self.f[k] = v; return self
    def limit(self, *_a): return self
    def update(self, p): self.op, self.payload = "update", p; return self
    def delete(self): self.op = "delete"; return self
    async def execute(self):
        class R: pass
        r = R()
        rows = self.db[self.t]
        hit = [x for x in rows if all(x.get(k) == v for k, v in self.f.items())]
        if self.op == "delete":
            for x in hit: rows.remove(x)
        if self.op == "update":
            for x in hit: x.update(self.payload)
        r.data = hit
        return r


class _Db(dict):
    def table(self, t): return _Q(self, t)


def test_run_is_flag_gated_and_idempotent():
    members = [REAL, {**REAL, "id": "r2"},  # same email twice: one email
               {"id": "t1", "name": "TEST x", "email": "akashorter+t@gmail.com", "is_active": True, "role": "Photographer"},
               {"id": "a1", "name": "Applicant", "email": "a@gmail.com", "is_active": False, "role": "Applicant"}]
    db = _Db(vendor_members=members, ops_autopilot_events=[])

    async def once(_db, key, data=None):
        if any(e["key"] == key for e in db["ops_autopilot_events"]):
            return False
        db["ops_autopilot_events"].append({"key": key, "data": data or {}})
        return True

    send = AsyncMock(return_value="re_123")
    with patch.object(ops, "once", once), patch.object(an, "_send", send), patch.object(an.asyncio, "sleep", AsyncMock()):
        assert asyncio.run(an.run(db)) == {}  # no request marker: nothing sent
        assert send.call_count == 0
        db["ops_autopilot_events"].append({"key": an.K_TEST_REQ, "data": {}})
        out = asyncio.run(an.run(db))
        assert out == {"test": "re_123"} and send.call_args[0][0] == an.TEST_TO
        assert asyncio.run(an.run(db)) == {}  # test copy only once
        db["ops_autopilot_events"].append({"key": an.K_SEND_REQ, "data": {}})
        out = asyncio.run(an.run(db))
        assert [s["member_id"] for s in out["sent"]] == ["r1"] and send.call_count == 2
        assert asyncio.run(an.run(db))["sent"] == []  # re-run: nothing more
        assert send.call_count == 2
        mark = next(e for e in db["ops_autopilot_events"] if e["key"] == "ic_a1_notice:r1")
        assert mark["data"]["resend_id"] == "re_123"


def test_failed_send_is_retried_next_run():
    db = _Db(vendor_members=[REAL], ops_autopilot_events=[{"key": an.K_SEND_REQ, "data": {}}])

    async def once(_db, key, data=None):
        if any(e["key"] == key for e in db["ops_autopilot_events"]):
            return False
        db["ops_autopilot_events"].append({"key": key, "data": data or {}})
        return True

    send = AsyncMock(side_effect=[None, "re_9"])
    with patch.object(ops, "once", once), patch.object(an, "_send", send), patch.object(an.asyncio, "sleep", AsyncMock()):
        assert asyncio.run(an.run(db))["failed"] == 1
        assert asyncio.run(an.run(db))["sent"][0]["resend_id"] == "re_9"
