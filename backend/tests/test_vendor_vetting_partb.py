"""Part B vetting: expiry auto-pause sweep and 3-job probation (Kam, 2026-11-01)."""
import asyncio
from datetime import date

from app.services import trade_specs as ts
from app.services import vendor_vetting as vv
from app.shared.vendor_identity import is_assignable


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

    def in_(self, k, vals):
        vals = {str(v) for v in vals}
        self.filters.append((k, lambda x: str(x) in vals))
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

    def _match(self, r):
        return all(f(r.get(k)) for k, f in self.filters)

    async def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "insert":
            rows.append(dict(self.payload))
            return _Res([self.payload])
        hit = [r for r in rows if self._match(r)]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
        return _Res([dict(r) for r in hit])


class FakeDB:
    def __init__(self, **tables):
        self.tables = tables

    def table(self, name):
        return _Q(self, name)


def _m(**kw):
    base = {"id": "m1", "is_active": True, "onboarding_status": "approved", "vendor_type": "inspector",
            "role": "Inspector", "title": "Inspector IC", "onboarding_completed_at": "2026-11-01T00:00:00Z",
            "home_city": "Toronto", "tier": 1}
    base.update(kw)
    return base


DOCS_OK = [{"member_id": "m1", "doc_type": "eo", "status": "verified", "expiry_date": "2030-01-01"},
           {"member_id": "m1", "doc_type": "coi", "status": "verified", "expiry_date": "2030-01-01"}]
DOCS_EXPIRED = [{"member_id": "m1", "doc_type": "eo", "status": "verified", "expiry_date": "2026-01-01"},
                {"member_id": "m1", "doc_type": "coi", "status": "verified", "expiry_date": "2030-01-01"}]
TODAY = date(2026, 11, 2)


def test_expiry_decision_pauses_then_resumes_only_its_own_pause():
    action, reason = vv.expiry_decision(_m(), DOCS_EXPIRED, TODAY)
    assert action == "pause" and reason.startswith("Expired: ")
    assert vv.expiry_decision(_m(paused_at="x", paused_reason=reason), DOCS_EXPIRED, TODAY) == (None, None)
    assert vv.expiry_decision(_m(paused_at="x", paused_reason=reason), DOCS_OK, TODAY) == ("resume", None)
    # Probation/admin pauses are never lifted by the sweep.
    assert vv.expiry_decision(_m(paused_at="x", paused_reason="Probation: job failed review"), DOCS_OK, TODAY) == (None, None)
    assert vv.expiry_decision(_m(id="0c3fa7c2-d23b-4e44-90ad-ab2d19af2580"), DOCS_EXPIRED, TODAY) == (None, None)


def test_expiry_sweep_writes_and_dry_run_does_not():
    db = FakeDB(vendor_members=[_m()], vendor_documents=list(DOCS_EXPIRED))
    out = asyncio.run(vv.run_expiry_sweep(db, dry_run=True, today=TODAY))
    assert [p["member_id"] for p in out["paused"]] == ["m1"] and not db.tables["vendor_members"][0].get("paused_at")
    out = asyncio.run(vv.run_expiry_sweep(db, today=TODAY))
    m = db.tables["vendor_members"][0]
    assert m["paused_at"] and m["paused_reason"].startswith("Expired: ") and not is_assignable(m)
    db.tables["vendor_documents"] = list(DOCS_OK)
    out = asyncio.run(vv.run_expiry_sweep(db, today=TODAY))
    assert out["resumed"] == [{"member_id": "m1"}] and db.tables["vendor_members"][0]["paused_at"] is None


def test_probation_records_first_n_jobs_only():
    db = FakeDB(vendor_probation_reviews=[])
    n = ts.probation_jobs()
    for i in range(n + 2):
        asyncio.run(vv.record_probation_job(db, {"id": f"j{i}", "assignee_id": "m1"}))
    asyncio.run(vv.record_probation_job(db, {"id": "j0", "assignee_id": "m1"}))
    rows = db.tables["vendor_probation_reviews"]
    assert [r["job_id"] for r in rows] == [f"j{i}" for i in range(n)]
    assert asyncio.run(vv.record_probation_job(db, {"id": "x", "assignee_id": "default_user"})) is None
    assert asyncio.run(vv.record_probation_job(db, {"id": "x", "assignee_id": "0c3fa7c2-d23b-4e44-90ad-ab2d19af2580"})) is None


def test_probation_fail_or_low_rating_pauses():
    assert vv.probation_pause_reason("approved", 5) is None
    assert vv.probation_pause_reason("approved", ts.bad_rating_max()).startswith("Probation: ")
    assert vv.probation_pause_reason("approved", ts.bad_rating_max() + 1) is None
    assert vv.probation_pause_reason("failed", None) == "Probation: job failed review"
    db = FakeDB(vendor_members=[_m()], vendor_probation_reviews=[{"job_id": "j1", "member_id": "m1", "status": "pending"}])
    out = asyncio.run(vv.review_probation_job(db, "m1", "j1", "approved", client_rating=2, reviewed_by="admin"))
    assert out["paused"] and db.tables["vendor_members"][0]["paused_reason"] == "Probation: client rating 2/5"
    assert db.tables["vendor_probation_reviews"][0]["status"] == "approved"
    assert asyncio.run(vv.review_probation_job(db, "m1", "nope", "approved")) is None
    try:
        asyncio.run(vv.review_probation_job(db, "m1", "j1", "maybe"))
        assert False
    except ValueError:
        pass


def test_probation_pass_does_not_pause():
    db = FakeDB(vendor_members=[_m()], vendor_probation_reviews=[{"job_id": "j1", "member_id": "m1", "status": "pending"}])
    out = asyncio.run(vv.review_probation_job(db, "m1", "j1", "approved", client_rating=5))
    assert out["paused"] is False and not db.tables["vendor_members"][0].get("paused_at")


def test_cron_registered():
    import json, os
    v = json.load(open(os.path.join(os.path.dirname(__file__), "..", "vercel.json")))
    assert any(c["path"] == "/api/onboarding/credential-expiry" for c in v["crons"])


def test_upload_names_never_collide():
    from app.routers.onboarding import _unique_upload_name
    a = _unique_upload_name("id_verification", "scan.pdf")
    b = _unique_upload_name("police_check", "scan.pdf")
    c = _unique_upload_name("police_check", "scan.pdf")
    assert a != b and b != c
    assert a.startswith("id_verification-") and a.endswith("-scan.pdf")
    assert "/" not in _unique_upload_name("coi", "../../etc/x y.pdf")
