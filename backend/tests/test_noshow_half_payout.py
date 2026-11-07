"""
Client no-show payout (Kam, 2026-10-30): once a no-show claim is reviewed and
approved, the vendor is paid 50% of their normal base payout for the job, with HST
on top as usual. A rejected claim pays nothing. Stripe is mocked: no live charges.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.services import payout_service as ps
from app.services.payout_service import noshow_payout_cents, trigger_payout_for_job

JOB_ID = "job-ns-1"
CLAIM_ID = "claim-ns-1"
BASE = 15001  # odd, to pin rounding


def test_noshow_payout_is_half_rounded_half_up():
    assert noshow_payout_cents(15000) == 7500
    assert noshow_payout_cents(15001) == 7501
    assert noshow_payout_cents(1) == 1
    assert noshow_payout_cents(0) == 0
    assert noshow_payout_cents(None) == 0


def _patches(member, existing_payout=None):
    job = {"id": JOB_ID, "vendor_id": "vendor_1", "assignee_id": "member_1",
           "booking_id": "bk_1", "rack_price": 299, "package_id": "essentials"}
    claim_updates = []

    async def upd_claim(db, claim_id, fields):
        claim_updates.append((claim_id, dict(fields)))

    p = [
        patch.object(ps, "_db_select_job", AsyncMock(return_value=job)),
        patch.object(ps, "_db_select_vendor_member", AsyncMock(return_value=member)),
        patch.object(ps, "_db_select_payout_by_job", AsyncMock(return_value=existing_payout)),
        patch.object(ps, "_db_insert_payout", AsyncMock(return_value={"id": "payout_1"})),
        patch.object(ps, "_db_update_payout", AsyncMock()),
        patch.object(ps, "_db_update_job_payout_amount", AsyncMock()),
        patch.object(ps, "_db_update_noshow_claim", side_effect=upd_claim),
        patch.object(ps, "get_fixed_payout_amount", AsyncMock(return_value=BASE)),
        patch("app.services.vendor_payout_rate_card.addon_payout_cents", return_value=0),
        patch("app.services.client_service_payouts.set_payout_cents", return_value=None),
        patch("app.services.commercial_jobs.is_employee", return_value=False),
        patch("app.shared.vendor_identity.is_owner_member", return_value=False),
    ]
    return p, claim_updates


async def _run(member, noshow, existing_payout=None):
    ps_list, claim_updates = _patches(member, existing_payout)
    transfer = AsyncMock(return_value="tr_test_1")
    for x in ps_list:
        x.start()
    try:
        with patch("app.services.stripe_connect_service.handle_payout_to_vendor", transfer):
            result = await trigger_payout_for_job(
                object(), JOB_ID, noshow_claim_id=CLAIM_ID if noshow else None
            )
    finally:
        for x in reversed(ps_list):
            x.stop()
    return result, transfer, claim_updates


@pytest.mark.asyncio
async def test_normal_payout_pays_full_base_and_skips_claims():
    result, transfer, claim_updates = await _run({"tier": 2}, noshow=False)
    assert result["base_amount"] == BASE
    assert transfer.call_args.kwargs["amount"] == BASE
    assert claim_updates == []


@pytest.mark.asyncio
async def test_approved_noshow_pays_half_base_and_writes_claim():
    result, transfer, claim_updates = await _run({"tier": 2}, noshow=True)
    half = noshow_payout_cents(BASE)
    assert result["status"] == "paid"
    assert result["base_amount"] == half
    assert transfer.call_args.kwargs["amount"] == half  # not HST-registered
    assert claim_updates[0] == (CLAIM_ID, {"amount_cents": half})
    last = claim_updates[-1][1]
    assert last["status"] == "paid_out"
    assert last["paid_via_payout_id"] == "payout_1"


@pytest.mark.asyncio
async def test_approved_noshow_adds_hst_on_the_half():
    result, transfer, _ = await _run({"tier": 2, "hst_number": "123456789RT0001"}, noshow=True)
    half = noshow_payout_cents(BASE)
    hst = round(half * 0.13)
    assert result["base_amount"] == half
    assert result["net_amount"] == half + hst
    assert transfer.call_args.kwargs["amount"] == half + hst


@pytest.mark.asyncio
async def test_noshow_payout_is_not_paid_twice():
    existing = {"id": "payout_1", "status": "paid", "amount": 7501,
                "stripe_transfer_id": "tr_old"}
    result, transfer, claim_updates = await _run({"tier": 2}, noshow=True, existing_payout=existing)
    assert result["already_exists"] is True
    assert not transfer.called
    assert claim_updates[-1][1]["status"] == "paid_out"


# ── Admin review triggers the half payout only on approval ───────────────────

from unittest.mock import MagicMock  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from app.routers import admin_ops  # noqa: E402


def _claim_db(claim):
    db = MagicMock()
    q = MagicMock()
    db.table.return_value = q
    q.select.return_value = q
    q.update.return_value = q
    q.eq.return_value = q
    q.maybe_single.return_value = q
    q.execute = AsyncMock(return_value=MagicMock(data=claim))
    return db, q


async def _review(claim, action):
    db, q = _claim_db(claim)
    payout = AsyncMock(return_value={"status": "paid", "payout_id": "payout_1"})
    webhook = AsyncMock()
    with patch.object(admin_ops, "resolve_vendor_actor_id", AsyncMock(return_value="admin_1")), \
         patch.object(admin_ops, "get_vendor_member", AsyncMock(return_value={"role": "admin"})), \
         patch.object(admin_ops, "is_vendor_admin", return_value=True), \
         patch.object(admin_ops, "get_db", AsyncMock(return_value=db)), \
         patch.object(admin_ops, "send_dispatch_status_update_webhook", webhook), \
         patch("app.services.payout_service.trigger_payout_for_job", payout):
        result = await admin_ops.review_noshow_claim(claim["id"], action, None, "Bearer x", None)
    return result, payout, webhook, q


def _claim(status, reviewed_at=None):
    return {"id": CLAIM_ID, "job_id": JOB_ID, "booking_id": "bk_1",
            "status": status, "reviewed_at": reviewed_at}


@pytest.mark.asyncio
async def test_approving_flagged_claim_pays_half_and_fires_deferred_webhook():
    result, payout, webhook, _ = await _review(_claim("pending_review"), "approve")
    payout.assert_awaited_once()
    assert payout.call_args.args[1] == JOB_ID
    assert payout.call_args.kwargs["noshow_claim_id"] == CLAIM_ID
    assert webhook.call_args.kwargs["new_status"] == "agent_no_show"
    assert result["payout"]["status"] == "paid"


@pytest.mark.asyncio
async def test_approving_auto_recorded_claim_pays_without_refiring_webhook():
    result, payout, webhook, _ = await _review(_claim("pending"), "approve")
    payout.assert_awaited_once()
    assert not webhook.called
    assert result["status"] == "pending"


@pytest.mark.asyncio
async def test_rejected_claim_pays_nothing():
    result, payout, webhook, _ = await _review(_claim("pending_review"), "reject")
    assert not payout.called
    assert result["status"] == "rejected"
    assert webhook.call_args.kwargs["new_status"] == "no_show_reversed"


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", [
    _claim("pending", reviewed_at="2026-10-30T12:00:00+00:00"),
    _claim("paid_out", reviewed_at="2026-10-30T12:00:00+00:00"),
    _claim("rejected", reviewed_at="2026-10-30T12:00:00+00:00"),
])
async def test_already_reviewed_claim_cannot_be_approved_again(claim):
    with pytest.raises(HTTPException) as exc:
        await _review(claim, "approve")
    assert exc.value.status_code == 409


# ── Report gate: vendor must have checked in ─────────────────────────────────

from app.routers.vendor_os import _validate_noshow_claim  # noqa: E402


@pytest.mark.asyncio
async def test_noshow_report_requires_check_in():
    job = {"id": JOB_ID, "assignee_id": "member_1", "status": "assigned",
           "scheduled_date": "2026-10-30", "time_window_start": "10:00"}
    out = await _validate_noshow_claim(MagicMock(), job, "member_1")
    assert out["ok"] is False
    assert "Check in" in out["reason"]
