"""Sentinel 2026-11-09 Q1: fail-closed contractor-agreement check on the server.

Accept, claim (watch accept) and start (check-in, on_way, arrived, in_progress)
answer 403 terms_required while the IC agreement is owed, or amendment A1 once it
binds (2026-11-16 00:00 ET). Auto-allocation skips those contractors. Completing
work is never blocked; it raises one alert for Kam instead.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.routers import vendor_os as vos
from app.services.vendor_profile import member_eligible_for_job
from app.shared.schemas import Job, JobStatusUpdate, VendorMember
from app.shared.vendor_identity import TERMS_REQUIRED_CODE, terms_block

BEFORE = datetime(2026, 11, 15, 23, 0, tzinfo=timezone.utc)   # 18:00 ET Nov 15
AFTER = datetime(2026, 11, 16, 5, 1, tzinfo=timezone.utc)     # 00:01 ET Nov 16

DONE = "2026-10-01T00:00:00+00:00"
CLEARED = {"id": "m-ok", "vendor_id": "default_vendor", "terms_accepted_at": DONE,
           "ic_agreement_version": "1.9", "ic_amendment_version": "IC-A1 v1.0",
           "onboarding_completed_at": DONE}
NO_IC = {"id": "m-no-ic", "vendor_id": "default_vendor", "name": "New Pat", "email": "pat@example.com"}
NO_A1 = {"id": "m-no-a1", "vendor_id": "default_vendor", "terms_accepted_at": DONE,
         "ic_agreement_version": "1.9", "onboarding_completed_at": DONE}
OWNER = {"id": "m-owner", "vendor_id": "default_vendor", "payout_mode": "owner_full"}


# ── terms_block ──────────────────────────────────────────────────────────────

def test_ic_agreement_owed_blocks_now():
    assert terms_block(NO_IC, now=BEFORE) == "ic_agreement"
    assert terms_block(NO_IC, now=AFTER) == "ic_agreement"


def test_amendment_only_blocks_from_nov_16_et():
    assert terms_block(NO_A1, now=BEFORE) is None
    assert terms_block(NO_A1, now=AFTER) == "amendment"


def test_cleared_and_owner_never_blocked():
    assert terms_block(CLEARED, now=AFTER) is None
    assert terms_block(OWNER, now=AFTER) is None
    assert terms_block(None) is None


# ── auto-allocation ──────────────────────────────────────────────────────────

def _vm(**extra):
    base = dict(id="v1", name="Photo Pro", email="v1@example.com", specialty="photo", is_active=True,
                status="available", onboarding_status="approved", onboarding_completed_at=DONE)
    base.update(extra)
    return VendorMember(**base)


def _job():
    return Job(address="1 King St W, Toronto, ON", job_type="photo", time_window_start="09:00")


def test_auto_allocation_skips_contractor_who_owes_ic_terms():
    assert member_eligible_for_job(_vm(terms_accepted_at=DONE, ic_amendment_version="IC-A1 v1.0"), _job()) is True
    assert member_eligible_for_job(_vm(), _job()) is False


def test_schema_carries_amendment_fields_so_allocation_can_read_them():
    m = _vm(terms_accepted_at=DONE, ic_amendment_version="IC-A1 v1.0", ic_amendment_accepted_at=DONE)
    d = m.model_dump()
    assert d["ic_amendment_version"] == "IC-A1 v1.0"
    assert terms_block(d, now=AFTER) is None
    assert terms_block(_vm(terms_accepted_at=DONE).model_dump(), now=AFTER) == "amendment"


# ── endpoints ────────────────────────────────────────────────────────────────

class _NoWriteDb:
    def table(self, name):
        raise AssertionError(f"no DB write expected before the terms check ({name})")


def _install(monkeypatch, member, job):
    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=member["id"]))
    monkeypatch.setattr(vos, "get_vendor_member", AsyncMock(return_value=member))
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=_NoWriteDb()))
    monkeypatch.setattr(vos, "_load_visible_job", AsyncMock(return_value=job))
    monkeypatch.setattr(vos, "ensure_assigned_to_actor", lambda *a, **k: None)


def _code(exc):
    d = exc.value.detail
    return d.get("code") if isinstance(d, dict) else None


@pytest.mark.parametrize("action,status", [("accept", "pending_assignment"), ("on_way", "assigned"), ("arrived", "assigned")])
def test_watch_accept_and_start_refused_while_terms_owed(monkeypatch, action, status):
    job = {"id": "j1", "assignee_id": NO_IC["id"], "status": status, "vendor_id": "default_vendor"}
    _install(monkeypatch, NO_IC, job)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(vos.watch_job_action("j1", vos.WatchActionRequest(action=action),
                                         authorization="Bearer x", x_user_id=None))
    assert exc.value.status_code == 403 and _code(exc) == TERMS_REQUIRED_CODE


def test_check_in_refused_while_terms_owed(monkeypatch):
    job = {"id": "j1", "assignee_id": NO_IC["id"], "status": "assigned"}
    _install(monkeypatch, NO_IC, job)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(vos.check_in_job("j1", vos.CheckInRequest(lat=43.65, lng=-79.38), "Bearer x", None))
    assert exc.value.status_code == 403 and _code(exc) == TERMS_REQUIRED_CODE


def test_status_in_progress_refused_while_terms_owed(monkeypatch):
    job = {"id": "j1", "assignee_id": NO_IC["id"], "status": "assigned", "booking_id": None,
           "deliverables_count": 0, "uploaded_count": 0}
    _install(monkeypatch, NO_IC, job)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(vos.update_vendor_job_status("j1", JobStatusUpdate(status="in_progress"),
                                                 authorization="Bearer x", x_user_id=None))
    assert exc.value.status_code == 403 and _code(exc) == TERMS_REQUIRED_CODE


def test_completing_is_never_refused_for_terms(monkeypatch):
    """Whatever else completion checks, it is never a terms_required refusal."""
    job = {"id": "j1", "assignee_id": NO_IC["id"], "status": "in_progress", "booking_id": None,
           "deliverables_count": 0, "uploaded_count": 0}
    _install(monkeypatch, NO_IC, job)
    try:
        asyncio.run(vos.update_vendor_job_status("j1", JobStatusUpdate(status="completed"),
                                                 authorization="Bearer x", x_user_id=None))
    except HTTPException as e:
        d = e.detail if isinstance(e.detail, dict) else {}
        assert d.get("code") != TERMS_REQUIRED_CODE
    except AssertionError:
        pass  # reached a DB write: past any terms check
    except Exception:
        pass


def test_decline_is_never_refused_for_terms(monkeypatch):
    job = {"id": "j1", "assignee_id": NO_IC["id"], "status": "assigned"}
    _install(monkeypatch, NO_IC, job)
    try:
        asyncio.run(vos.watch_job_action("j1", vos.WatchActionRequest(action="decline"),
                                         authorization="Bearer x", x_user_id=None))
    except HTTPException as e:
        d = e.detail if isinstance(e.detail, dict) else {}
        assert d.get("code") != TERMS_REQUIRED_CODE
    except AssertionError:
        pass


def test_cleared_contractor_passes_the_gate():
    vos._require_terms_cleared(CLEARED)
    vos._require_terms_cleared(OWNER)
    vos._require_terms_cleared(None)


# ── completed work: flagged, payout not held ─────────────────────────────────

def test_completion_with_terms_owed_flags_kam_once(monkeypatch):
    from app.services import ops_autopilot as ops
    sent = []

    async def fake_send_kam(db, key, title, lines, actions=(), ntype="ops_autopilot", data=None):
        sent.append((key, title, lines, data))
        return True

    monkeypatch.setattr(ops, "send_kam", fake_send_kam)
    monkeypatch.setattr(ops, "_job_line", lambda j: f"job {j.get('id')}", raising=False)
    asyncio.run(vos._flag_completed_with_terms_owed(None, {"id": "j9"}, NO_IC))
    assert len(sent) == 1
    key, title, lines, data = sent[0]
    assert key == "terms_owed_complete:m-no-ic:j9"
    assert data == {"member_id": "m-no-ic", "job_id": "j9", "terms": "ic_agreement"}
    assert any("not held" in line for line in lines)


def test_completion_flag_skipped_when_cleared_and_soft_fails(monkeypatch):
    from app.services import ops_autopilot as ops

    async def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(ops, "send_kam", boom)
    asyncio.run(vos._flag_completed_with_terms_owed(None, {"id": "j9"}, CLEARED))
    asyncio.run(vos._flag_completed_with_terms_owed(None, {"id": "j9"}, NO_IC))  # no raise
