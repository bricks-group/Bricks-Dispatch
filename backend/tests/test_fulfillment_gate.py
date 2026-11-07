"""Fulfillment gate (launch review 2026-11-07).

Delivery to the client and vendor payout only when every purchased component
is present AND the job is editor-approved. Raw video clips never auto-deliver.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import fulfillment_gate as fg


@pytest.fixture(autouse=True)
def _gate_on(monkeypatch):
    monkeypatch.setenv("FULFILLMENT_GATE", "1")


# ── required components ────────────────────────────────────────────────────
@pytest.mark.parametrize("pid,expected", [
    ("photos_only", {"photos"}),
    ("essentials", {"photos", "drone", "floorplan"}),
    ("story", {"photos", "drone", "floorplan", "reel"}),
    ("showcase", {"photos", "drone", "floorplan", "reel", "tour"}),
    ("full", {"photos", "drone", "floorplan", "reel", "tour", "twilight"}),
    ("full-suite", {"photos", "drone", "floorplan", "reel", "tour", "twilight"}),
])
def test_ladder_components(pid, expected):
    assert fg.required_components({"package_id": pid}) == expected


def test_addons_add_components_and_fees_ignored():
    job = {"package_id": "photos_only", "add_ons": [
        {"key": "twilight"}, {"key": "virtual_visit"}, {"key": "travel", "amount_cents": 2500},
    ]}
    assert fg.required_components(job) == {"photos", "twilight", "visit"}


def test_floor_plan_only_requires_floorplan():
    assert fg.required_components({"package_id": "floor_plan_only"}) == {"floorplan"}


def test_unknown_package_requires_any_deliverable():
    assert fg.required_components({"package_id": "pm_turnover_set"}) == {"any"}


# ── evaluate ───────────────────────────────────────────────────────────────
def _urls(*kinds):
    out = []
    for k in kinds:
        if k == "photos":
            out.append({"upload_id": "p", "url": "u"})
        elif k == "reel_final":
            out.append({"upload_id": "r", "url": "u", "kind": "reel", "provider": "editor_final"})
        elif k == "reel_raw":
            out.append({"upload_id": "r", "url": "u", "kind": "reel", "provider": "as_is",
                        "filename": "reel_clip.mp4"})
        elif k == "tour":
            out.append({"session_id": "s", "url": "u", "kind": "splat"})
        else:
            out.append({"upload_id": k, "url": "u", "kind": k})
    return out


def test_complete_and_approved_is_ready():
    job = {"package_id": "story", "editor_approved_at": "2026-11-07T15:00:00Z"}
    v = fg.evaluate(job, _urls("photos", "drone", "floorplan", "reel_final"))
    assert v["ready"] is True and v["missing"] == []


def test_missing_component_blocks_even_when_approved():
    job = {"package_id": "story", "editor_approved_at": "2026-11-07T15:00:00Z"}
    v = fg.evaluate(job, _urls("photos", "drone", "floorplan"))
    assert v["ready"] is False and v["missing"] == ["reel"]


def test_raw_reel_clip_never_counts_as_film():
    job = {"package_id": "story", "editor_approved_at": "2026-11-07T15:00:00Z"}
    v = fg.evaluate(job, _urls("photos", "drone", "floorplan", "reel_raw"))
    assert v["ready"] is False and "reel" in v["missing"]


def test_complete_but_unapproved_blocks():
    v = fg.evaluate({"package_id": "photos_only"}, _urls("photos"))
    assert v["ready"] is False
    assert "awaiting editor approval" in v["reasons"]


def test_visit_present_flag_satisfies_visit():
    job = {"package_id": "photos_only", "add_ons": [{"key": "visit"}], "editor_approved_at": "x"}
    assert fg.evaluate(job, _urls("photos"), visit_present=True)["ready"] is True
    assert fg.evaluate(job, _urls("photos"), visit_present=False)["missing"] == ["visit"]


def test_gate_disabled_by_env(monkeypatch):
    monkeypatch.setenv("FULFILLMENT_GATE", "off")
    assert fg.gate_enabled() is False
    monkeypatch.delenv("FULFILLMENT_GATE")
    assert fg.gate_enabled() is True  # default ON


# ── source clips ───────────────────────────────────────────────────────────
def test_source_clip_detection():
    assert fg.is_source_clip({"filename": "DJI_0001.MP4"})
    assert fg.is_source_clip({"filename": "x", "mime_type": "video/quicktime"})
    assert not fg.is_source_clip({"filename": "final.mp4", "provider": "editor_final"})
    assert not fg.is_source_clip({"filename": "aerial.jpg"})


def test_source_clip_rows_excluded_from_delivery_rows():
    from app.services.media_intake import is_listing_still_delivery_row
    assert is_listing_still_delivery_row({"filename": "reel.mp4", "provider": "source_clip"}) is False
    assert is_listing_still_delivery_row({"filename": "reel.mp4", "provider": "editor_final"}) is True


def test_raw_drone_video_parked_not_delivered(monkeypatch):
    from app.services import post_production as pp
    updates = []

    async def fake_update(_db, uid, update):
        updates.append((uid, update))

    monkeypatch.setattr(pp, "_safe_update_upload", fake_update)
    asset = {"id": "up-d", "file_url": "jobs/j/up-d/DJI_0001.MP4", "filename": "DJI_0001.MP4",
             "mime_type": "video/mp4"}
    asyncio.run(pp.deliver_asset_without_vendor(MagicMock(), "j", asset))
    assert updates == [("up-d", updates[0][1])]
    assert updates[0][1]["provider"] == "source_clip"
    assert updates[0][1]["processing_status"] == "done"


# ── payout gate ────────────────────────────────────────────────────────────
def test_payout_release_rules():
    assert fg.payout_release_allowed({"status": "completed"}) == (False, "not_delivered")
    assert fg.payout_release_allowed({"status": "delivered"}) == (False, "awaiting_editor_approval")
    assert fg.payout_release_allowed({
        "status": "delivered", "editor_approved_at": "x", "fulfillment_gate": {"missing": ["reel"]},
    }) == (False, "incomplete_delivery")
    assert fg.payout_release_allowed({
        "status": "delivered", "editor_approved_at": "x", "fulfillment_gate": {"missing": []},
    }) == (True, "ok")


def _payout_db(job):
    job_chain = MagicMock()
    job_chain.select.return_value.eq.return_value.maybe_single.return_value.execute = AsyncMock(
        return_value=MagicMock(data=job))
    job_chain.update.return_value.eq.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
    payout_chain = MagicMock()
    payout_chain.select.return_value.eq.return_value.maybe_single.return_value.execute = AsyncMock(
        return_value=MagicMock(data=None))
    payout_chain.insert.return_value.execute = AsyncMock(return_value=MagicMock(data=[{"id": "pay_new"}]))
    payout_chain.update.return_value.eq.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
    db = MagicMock()
    db.table = MagicMock(side_effect=lambda n: job_chain if n == "jobs" else payout_chain)
    return db, payout_chain


@pytest.mark.asyncio
async def test_trigger_payout_held_until_editor_approval():
    from app.services.payout_service import trigger_payout_for_job
    db, payout_chain = _payout_db({"id": "j1", "package_price": 599, "vendor_tier": 2,
                                   "status": "completed"})
    with patch("app.services.stripe_connect_service.handle_payout_to_vendor",
               new_callable=AsyncMock) as stripe:
        result = await trigger_payout_for_job(db, "j1")
    assert result == {"skipped": "fulfillment_gate", "reason": "not_delivered", "job_id": "j1"}
    stripe.assert_not_called()
    payout_chain.insert.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_payout_allowed_after_complete_approved_delivery():
    from app.services.payout_service import trigger_payout_for_job
    db, _ = _payout_db({"id": "j2", "vendor_id": "v", "package_price": 599, "vendor_tier": 2,
                        "status": "delivered", "editor_approved_at": "2026-11-07T15:00:00Z",
                        "fulfillment_gate": {"ready": True, "missing": []}})
    with patch("app.services.stripe_connect_service.handle_payout_to_vendor",
               new_callable=AsyncMock, return_value="tr_test"):
        result = await trigger_payout_for_job(db, "j2")
    assert result.get("status") == "paid"


# ── finalize gate ──────────────────────────────────────────────────────────
def _chain(data):
    c = MagicMock()
    for m in ("select", "eq", "maybe_single", "update", "upsert", "limit"):
        getattr(c, m).return_value = c
    c.execute = AsyncMock(return_value=MagicMock(data=data))
    return c


def test_finalize_holds_without_editor_approval(monkeypatch):
    from app.routers import post_production as pp
    still = {"id": "up-1", "filename": "a.jpg", "file_url": "jobs/j/a.jpg",
             "processed_url": "https://cdn/a.jpg", "processing_status": "done",
             "qc_status": "passed", "provider": "inhouse"}
    uploads, md, caps, jobs = _chain([still]), _chain(None), _chain([]), _chain(None)
    db = MagicMock()
    db.table.side_effect = lambda n: {"uploads": uploads, "media_delivery": md,
                                      "capture_sessions": caps, "jobs": jobs}.get(n, MagicMock())
    recorded = []

    async def fake_record(_db, _jid, verdict):
        recorded.append(verdict)

    monkeypatch.setattr(pp, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(pp, "run_qc_for_job", AsyncMock(return_value={}))
    monkeypatch.setattr(pp, "_load_job_row", AsyncMock(return_value={"id": "j", "package_id": "essentials"}))
    monkeypatch.setattr(pp, "_record_fulfillment_gate", fake_record)
    payout = AsyncMock()
    with patch("app.services.qc_hold_notify.notify_owner_qc_hold", AsyncMock()), \
         patch("app.services.payout_service.trigger_payout_for_job", payout):
        asyncio.run(pp._finalize_job_if_complete("j"))
    md.upsert.assert_not_called()
    payout.assert_not_called()
    assert recorded and recorded[0]["ready"] is False
    assert set(recorded[0]["missing"]) == {"drone", "floorplan"}
    assert recorded[0]["editor_approved"] is False


def test_finalize_delivers_when_complete_and_approved(monkeypatch):
    from app.routers import post_production as pp
    still = {"id": "up-1", "filename": "a.jpg", "file_url": "jobs/j/a.jpg",
             "processed_url": "https://cdn/a.jpg", "processing_status": "done",
             "qc_status": "passed", "provider": "inhouse"}
    uploads, md, caps, jobs = _chain([still]), _chain(None), _chain([]), _chain(None)
    db = MagicMock()
    db.table.side_effect = lambda n: {"uploads": uploads, "media_delivery": md,
                                      "capture_sessions": caps, "jobs": jobs}.get(n, MagicMock())
    monkeypatch.setattr(pp, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(pp, "run_qc_for_job", AsyncMock(return_value={}))
    monkeypatch.setattr(pp, "_load_job_row", AsyncMock(return_value={
        "id": "j", "package_id": "photos_only", "editor_approved_at": "2026-11-07T15:00:00Z"}))
    monkeypatch.setattr(pp, "_record_fulfillment_gate", AsyncMock())
    monkeypatch.setattr(pp, "_send_delivery_notification", AsyncMock(return_value="sent"))
    payout = AsyncMock()
    with patch("app.services.qc_hold_notify.notify_owner_qc_hold", AsyncMock()), \
         patch("app.services.payout_service.trigger_payout_for_job", payout), \
         patch("app.services.editor_ledger.record_editor_cost", AsyncMock()):
        asyncio.run(pp._finalize_job_if_complete("j"))
    md.upsert.assert_called_once()
    payout.assert_called_once()
