"""PUT availability keeps a non-null id; not-assignable reasons say why."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.routers import vendor_os as vos
from app.routers import vendor_profile as vp
from app.shared.schemas import AvailabilityUpdate
from app.shared.vendor_identity import public_profile


class _Chain:
    def __init__(self, existing, calls):
        self.existing, self.calls, self.op = existing, calls, None

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def maybe_single(self):
        return self

    def upsert(self, row, **kw):
        self.op = "upsert"
        self.calls.append((row, kw))
        return self

    async def execute(self):
        if self.op == "select":
            return SimpleNamespace(data=self.existing)
        return SimpleNamespace(data=[self.calls[-1][0]])


def _run_put(monkeypatch, existing):
    calls = []
    db = MagicMock()
    db.table.side_effect = lambda _n: _Chain(existing, calls)
    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value="m1"))
    monkeypatch.setattr(vos, "get_vendor_member", AsyncMock(return_value={"id": "m1", "vendor_id": "default_vendor"}))
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(vos, "write_audit_event", AsyncMock())
    payload = AvailabilityUpdate(available=True, work_start="06:00", work_end="17:00", jobs_per_day=2, blocked_windows=[])
    try:
        asyncio.run(vos.update_vendor_availability(payload, authorization="Bearer x", x_user_id=None))
    except Exception:
        pass  # later read-back is not under test
    return calls


def test_put_availability_reuses_existing_id(monkeypatch):
    calls = _run_put(monkeypatch, [{"id": "av-existing"}])
    row, kw = calls[0]
    assert row["id"] == "av-existing"
    assert kw.get("on_conflict") == "vendor_id,member_id"


def test_put_availability_new_row_gets_id(monkeypatch):
    calls = _run_put(monkeypatch, [])
    row, _ = calls[0]
    assert row["id"]


def _member(**kw):
    base = {"id": "v1", "role": "Repairs IC", "is_active": True, "onboarding_status": "approved",
            "onboarding_completed_at": "2026-11-03T05:43:52Z"}
    base.update(kw)
    return base


def test_reason_paused_trade_not_not_approved(monkeypatch):
    import app.services.trade_specs as ts
    monkeypatch.setattr(ts, "trade_enabled", lambda _t: False)
    pub = public_profile(_member())
    assert pub["assignable"] is False
    assert pub["assignable_reason"] == "Your trade isn't taking jobs yet"


def test_reason_paused_member():
    pub = public_profile(_member(paused_at="2026-11-01T00:00:00Z"))
    assert pub["assignable_reason"] == "Paused"


def test_reason_rejected_still_not_approved():
    pub = public_profile(_member(onboarding_status="rejected"))
    assert pub["assignable_reason"] == "Not approved"


def test_unique_upload_name_differs_each_time():
    a, b = vp._unique_name("avatar", "IMG_3514.jpeg"), vp._unique_name("avatar", "IMG_3514.jpeg")
    assert a != b and a.endswith("IMG_3514.jpeg") and a.startswith("avatar-")
